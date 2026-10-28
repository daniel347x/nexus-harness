#!/usr/bin/env python3
"""Offline check that every cast agent file's frontmatter parses, with a plain-text description.

    python scripts/test_agent_files.py -v

Manager-Session-089: ten new agent files whose descriptions opened with `[THINKING LEVEL ...]` were read by YAML
as lists; OpenCode refused its whole config and every API call returned 400. The restart script now asks a
throwaway OpenCode whether it accepts the candidate config before stopping anything (probe_candidate_config in
opencode_server.py), which is the authoritative check. This test is the cheap early warning at commit time: it
uses strict YAML (PyYAML), stricter than OpenCode's own parser, so a file it passes is safe for OpenCode too.
"""
from __future__ import annotations

import unittest
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1] / 'agents' / 'agent'
WORKER_DENIED_TOOLS = ('subagent', 'execute', 'question', 'skill')

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def frontmatter(text: str) -> str:
    if not text.startswith('---'):
        raise ValueError('no frontmatter: the file must start with ---')
    body = text[3:]
    end = body.find('\n---')
    if end < 0:
        raise ValueError('frontmatter is not closed by a --- line')
    return body[:end]


@unittest.skipIf(yaml is None, 'PyYAML not installed')
class AgentFiles(unittest.TestCase):
    def test_every_agent_file_parses_with_string_fields(self):
        files = sorted(AGENT_DIR.glob('*.md'))
        self.assertTrue(files, f'no agent files found in {AGENT_DIR}')
        for path in files:
            with self.subTest(agent=path.name):
                data = yaml.safe_load(frontmatter(path.read_text(encoding='utf-8')))
                self.assertIsInstance(data, dict, 'frontmatter is not a mapping')
                for key in ('description', 'model', 'variant', 'mode'):
                    if key in data:
                        self.assertIsInstance(data[key], str,
                                              f'{key} must be plain text; quote it if it starts with [ or {{ or contains ": "')

    def test_every_worker_agent_file_denies_the_tools_workers_never_use(self):
        """Workers, lead seats and helpers deny native dispatch, code mode, questions and skills, not browsing.
        A new worker agent file copies the same four-tool block; editing and web tools remain available.
        Only `mode: subagent` files are worker agents; a `mode: primary` agent (the Manager, manager.md) is a
        conversation the human holds and keeps every tool."""
        workers = 0
        for path in sorted(AGENT_DIR.glob('*.md')):
            data = yaml.safe_load(frontmatter(path.read_text(encoding='utf-8')))
            if data.get('mode') != 'subagent':
                continue
            workers += 1
            with self.subTest(agent=path.name):
                denied = {r.get('action') for r in data.get('permissions') or []
                          if r.get('effect') == 'deny' and r.get('resource') == '*'}
                self.assertLessEqual(set(WORKER_DENIED_TOOLS), denied,
                                     f'add the permissions block denying {", ".join(WORKER_DENIED_TOOLS)}')
        self.assertTrue(workers, 'no mode: subagent agent files found')

    def test_manager_quick_generator_keeps_the_committed_tool_policy(self):
        """The restart regenerates this agent: its source must not restore retired web denials."""
        from build_manager_quick_agent import HEADER
        generated = yaml.safe_load(frontmatter(HEADER))
        committed = yaml.safe_load(frontmatter((AGENT_DIR / 'manager-quick.md').read_text(encoding='utf-8')))
        self.assertEqual(generated['permissions'], committed['permissions'])
        self.assertEqual({r['action'] for r in generated['permissions']}, set(WORKER_DENIED_TOOLS))

    def test_every_agent_file_names_its_mode(self):
        """The tool-denial rule keys on `mode`, so a file without one would slip past it silently."""
        for path in sorted(AGENT_DIR.glob('*.md')):
            with self.subTest(agent=path.name):
                data = yaml.safe_load(frontmatter(path.read_text(encoding='utf-8')))
                self.assertIn(data.get('mode'), ('subagent', 'primary'), 'set mode: subagent (a worker) or primary')

    def test_a_backend_line_names_a_known_backend_and_claude_code_means_an_anthropic_model(self):
        """`backend: claude-code` marks a file that runs only in Claude Code on Max (oc_backend routes it there and the
        OpenCode installer skips it, D6); `backend: opencode` pins OpenCode. Anything else is a typo."""
        for path in sorted(AGENT_DIR.glob('*.md')):
            data = yaml.safe_load(frontmatter(path.read_text(encoding='utf-8')))
            if 'backend' not in data:
                continue
            with self.subTest(agent=path.name):
                self.assertIn(data['backend'], ('claude-code', 'opencode'))
                if data['backend'] == 'claude-code':
                    self.assertTrue(str(data.get('model', '')).startswith('anthropic/'), 'claude-code needs anthropic/*')

    def test_the_conductor_agent_denies_subagent_and_oc_launch_uses_it(self):
        """Never a subagent inside a program (Dan's rule): a Conductor starts every worker with oc_seat.sh, so
        conductor.md denies `subagent` (removable: one permission line); oc_launch sets the agent by name."""
        path = AGENT_DIR / 'conductor.md'
        data = yaml.safe_load(frontmatter(path.read_text(encoding='utf-8')))
        self.assertEqual(data.get('mode'), 'primary')
        denied = {r.get('action') for r in data.get('permissions') or [] if r.get('effect') == 'deny'}
        self.assertIn('subagent', denied)
        launch = (AGENT_DIR.parents[1] / 'scripts' / 'oc_launch.py').read_text(encoding='utf-8')
        self.assertIn('CONDUCTOR_AGENT = "conductor"', launch)


if __name__ == '__main__':
    unittest.main()
