#!/usr/bin/env python3
"""The restart script's own logic, with no server, no service and no network: what it installs (copy-only, the
previous opencode.json kept), which probe log lines fail the config check, and which providers need the proxy.
Linux only (the script locks with fcntl). Run: python3 -m pytest scripts/test_opencode_server.py -q
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import opencode_server as s  # noqa: E402


class InstallTests(unittest.TestCase):
    def test_installs_copy_only_keeps_the_previous_config_and_puts_agents_in_agents(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, home, user = Path(temp, 'repo'), Path(temp, 'home'), Path(temp, 'user')
            user.mkdir()
            for d in ('config', 'agents/agent', 'plugins'):
                (repo / d).mkdir(parents=True)
            (repo / 'config/opencode.json').write_text('{"new": true}')
            (repo / 'agents/AGENTS.md').write_text('prompt')
            (repo / 'agents/agent/a1.md').write_text('---\nmodel: p/m#high\n---\n')
            (repo / 'plugins/p1.ts').write_text('export default {}')
            (home / 'plugins').mkdir(parents=True)
            (home / 'opencode.json').write_text('{"old": true}')
            (home / 'plugins/kept.ts').write_text('installed by hand')
            (home / 'AGENTS.md').write_text('old global prompt')
            with patch.object(s, 'REPO', repo), patch.object(s, 'CONFIG_HOME', home), patch.object(s, 'HOME', user):
                s.install_files()
                s.install_files()                                   # a second run changes nothing
            self.assertEqual((home / 'opencode.json').read_text(), '{"new": true}')
            self.assertEqual((home / 'opencode.json.bak-prev-sync').read_text(), '{"old": true}')
            self.assertEqual((user / 'AGENTS.md').read_text(), 'prompt')       # the home folder, not the config
            self.assertFalse((home / 'AGENTS.md').exists())                     # the global copy is removed
            self.assertTrue((home / 'agents/a1.md').is_file())
            self.assertFalse((home / 'agent').exists())
            self.assertEqual((home / 'plugins/kept.ts').read_text(), 'installed by hand')   # never deleted
            self.assertTrue((home / 'plugins/p1.ts').is_file())

    def test_claude_code_only_agent_files_are_never_installed_and_an_installed_copy_is_removed(self):
        """D6: an agent file marked `backend: claude-code` exists only in Claude Code, so OpenCode can never bill the
        Anthropic API for it; the config check does not expect it either."""
        with tempfile.TemporaryDirectory() as temp:
            repo, home, user = Path(temp, 'repo'), Path(temp, 'home'), Path(temp, 'user')
            user.mkdir()
            for d in ('config', 'agents/agent', 'plugins'):
                (repo / d).mkdir(parents=True)
            (repo / 'config/opencode.json').write_text('{}')
            (repo / 'agents/agent/a1.md').write_text('---\nmodel: p/m#high\n---\n')
            (repo / 'agents/agent/c1.md').write_text('---\nmodel: anthropic/m#high\nbackend: claude-code\n---\nbody\n')
            (repo / 'agents/agent/c2.md').write_text("---\nmodel: anthropic/m#max\nbackend: 'claude-code'\n---\n")
            (repo / 'agents/agent/n1.md').write_text('---\nmodel: anthropic/m#high\n---\nbackend: claude-code\n')
            (home / 'agents').mkdir(parents=True)
            (home / 'agents/c2.md').write_text('installed before it was marked')
            with patch.object(s, 'REPO', repo), patch.object(s, 'CONFIG_HOME', home), patch.object(s, 'HOME', user):
                self.assertEqual(s.repo_agents(), ['a1', 'n1'])          # a body line is not front matter
                self.assertEqual([p.name for p in s.claude_only_agent_files()], ['c1.md', 'c2.md'])
                s.install_files()
            self.assertEqual(sorted(p.name for p in (home / 'agents').iterdir()), ['a1.md', 'n1.md'])


class ProbeLogTests(unittest.TestCase):
    def test_config_diagnostics_and_failed_plugins_fail_the_check_but_mcp_trouble_does_not(self):
        staged = Path('/tmp/oc-config-probe-x/config/opencode')
        log = '\n'.join([
            f'timestamp=1 level=WARN run=r message="configuration normalization diagnostic" source={staged}/opencode.json '
            'path=$.permissions kind=invalid action="skipped malformed recognized value" http.span=38 role=server',
            f'timestamp=2 level=WARN run=r message="configuration normalization diagnostic" source={staged}/opencode.json '
            'path=$.permissions kind=invalid action="skipped malformed recognized value" http.span=403 role=server',
            f'timestamp=3 level=WARN run=r message="failed to load plugin" target={staged}/plugins/broken.ts ref=err_1 '
            'cause="Cause([Die(AggregateError)])" http.span=68 role=server',
            'timestamp=4 level=WARN run=r message="mcp connect failed" server=ssh-a100 status.status=failed',
            'timestamp=5 level=INFO run=r message="server listening"',
        ])
        self.assertEqual(s.probe_failures(log, staged), [
            'message="configuration normalization diagnostic" source=~/.config/opencode/opencode.json '
            'path=$.permissions kind=invalid action="skipped malformed recognized value"',
            'message="failed to load plugin" target=~/.config/opencode/plugins/broken.ts cause="Cause([Die(AggregateError)])"',
        ])
        self.assertEqual(s.probe_failures('timestamp=1 level=WARN run=r message="mcp transport error"', staged), [])


class ProxyTests(unittest.TestCase):
    def test_the_proxy_restarts_only_when_a_provider_routes_through_it(self):
        routed = {'providers': {'anthropic': {'settings': {'baseURL': 'http://127.0.0.1:4097/v1'}},
                                'openai': {'settings': {}}}}
        direct = {'providers': {'openai': {'settings': {}}}}
        with patch.object(s.subprocess, 'run') as run:
            run.return_value.returncode, run.return_value.stdout, run.return_value.stderr = 0, 'started', ''
            s.restart_proxy(direct)
            run.assert_not_called()
            s.restart_proxy(routed)
            self.assertEqual(run.call_args.args[0][-1], 'restart')


if __name__ == '__main__':
    unittest.main()
