#!/usr/bin/env python3
"""Restart OpenCode's background service with this repo's configuration, and report when it is ready.

Run through restart_oc_server.sh from PowerShell or WSL. The service is OpenCode's own (`opencode service`,
127.0.0.1:4096, password in ~/.config/opencode/service.json); this script installs the repo's files into
~/.config/opencode and has the service restart itself:

  config/opencode.json → opencode.json (the previous copy kept as opencode.json.bak-prev-sync)
  agents/AGENTS.md     → ~/AGENTS.md, NOT into ~/.config/opencode (any copy left there is removed). The worker
                         instructions. OpenCode reads it as a PROJECT instruction file for every session located
                         in the home folder (every conductor, seat, helper and worker), and never for a session
                         outside home, such as the Manager's in manager-sessions on /mnt/e (Manager-Session-149,
                         [ap:1UAPGC]). The Manager's own prompt is the body of agents/agent/manager.md.
  plugins/*.ts         → plugins/ (discovered by OpenCode itself; copy-only, never deletes an installed file)
  agents/agent/*.md    → agents/ (the cast; copy-only), except a file whose front matter says `backend: claude-code`:
                         it runs only in Claude Code on Max, so it is never installed (an installed copy is
                         removed) and the config check does not expect it (decision D6, [ap:0OP1FK])

A Workflowy MCP daemon the config dials but cannot reach is a loud warning, not a refusal: the restart goes ahead (the
agents stay reachable) and ends "READY WITHOUT WORKFLOWY MCP" (Dan, Manager-Session-185).

Nothing is installed, and the running service is not touched, unless:
  · OpenCode accepts the candidate config: a throwaway server on PROBE_PORT, with its own config, data and state
    homes and its own password, loads it with no configuration diagnostic and no failed plugin, and lists every repo
    agent. (OpenCode does not reject a bad config: it logs a diagnostic and DROPS the bad value, or the whole file if
    the JSON is malformed or fails final validation; an agent file it cannot use is left out without a word; an
    unknown top-level key is ignored silently, which no check here can see.) And
  · no session is running on the live service.
Then: the service settings (port, hostname, UV_PROJECT_ENVIRONMENT) are checked, the payload proxy is restarted when a
provider routes through it, `opencode service restart` runs, and the script waits for a new server process and a
connected Workflowy MCP.

  --check   the config check alone: installs nothing, restarts nothing.
"""
from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

HOME = Path.home()
REPO = Path(__file__).resolve().parents[1]
BINARY = HOME / '.opencode/bin/opencode'
CONFIG_HOME = HOME / '.config/opencode'
HOSTNAME, PORT = '127.0.0.1', 4096
SERVICE_ENV = {'UV_PROJECT_ENVIRONMENT': '.venv-wsl'}   # keeps WSL uv out of Windows-built .venv dirs ([ap:RQZ6AT])
PROBE_PORT = 4098                                       # the throwaway config-check server; never the live port
# What the throwaway server logs about a config it could not fully use (anything else it logs, e.g. an MCP server
# that cannot connect from the probe's environment, is not a property of the config and does not fail the check).
PROBE_FAILURES = ('message="configuration normalization diagnostic"', 'message="failed to load plugin"')

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_client import oc_get, oc_info, oc_status  # noqa: E402  — the live service, with its password


def http_get(base: str, path: str, password: str, timeout: float = 5):
    """GET one /api route of a server with Basic auth → its unwrapped data. An HTTP error carries OpenCode's own
    error body (Manager-Session-089: a bare 'HTTP Error 400' hid the broken file and field)."""
    auth = base64.b64encode(f'opencode:{password}'.encode()).decode()
    req = urllib.request.Request(base + path, headers={'Authorization': f'Basic {auth}', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace').strip()[:2000]
        raise RuntimeError(f'{path} -> HTTP {e.code}: {body or e.reason}') from e
    return data['data'] if isinstance(data, dict) and 'data' in data else data


def repo_config() -> dict:
    """The repo's config/opencode.json IS the config (Manager-Session-037): edit it there, commit, restart."""
    source = REPO / 'config/opencode.json'
    try:
        return json.loads(source.read_text(encoding='utf-8'))
    except FileNotFoundError as e:
        raise RuntimeError(f'{source} is missing. Nothing was installed or restarted.') from e
    except ValueError as e:
        raise RuntimeError(f'config/opencode.json is not valid JSON ({e}); fix it in the repo. Nothing was installed or restarted.') from e


def workflowy_gate(cfg: dict) -> tuple[str, int, str, bool] | None:
    """OpenCode is a Streamable-HTTP client of the one Workflowy MCP daemon on Windows. An unreachable daemon is a loud
    warning, never a refusal (Dan, Manager-Session-185: without Workflowy the agents can still be talked to); the
    post-restart check reports the MCP state. → (host, port, label, reachable), or None when the config has no remote
    Workflowy server."""
    wf = cfg.get('mcp', {}).get('servers', {}).get('workflowy', {})
    if wf.get('type') != 'remote':
        print('No remote Workflowy MCP server in the config; no daemon reachability check.', flush=True)
        return None
    u = urlparse(wf.get('url', ''))
    host, port = u.hostname or '127.0.0.1', int(u.port or (443 if u.scheme == 'https' else 80))
    label = f'Workflowy MCP daemon {wf.get("url")}'
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError as e:
        print(f'⚠️  WARNING: {label} is not reachable ({e}). Restarting anyway: the agents stay reachable, but '
              'Workflowy MCP will not connect until the daemon runs on Windows (workflowy-mcp --transport http, or any '
              'stdio shim host such as the TypingMind connector); then restart OpenCode again.', flush=True)
        return host, port, label, False
    print(f'Reachable: {label}', flush=True)
    return host, port, label, True


# @beacon[
#   id=oc-server@claude_code_only,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=D6: an agent file whose front matter says backend: claude-code runs only in Claude Code on Max; the installer and the config check skip it (and remove an installed copy), so OpenCode can never bill the Anthropic API for it.,
# ]
def claude_code_only(path: Path) -> bool:
    """True for an agent file whose front matter says `backend: claude-code` (decision D6, [ap:0OP1FK]): it runs only
    in Claude Code on Dan's Max subscription (oc_backend routes it there) and never exists inside OpenCode, so a stray
    `subagent` call by its name fails loudly instead of billing the Anthropic API."""
    text = path.read_text(encoding='utf-8')
    if not text.startswith('---'):
        return False
    m = re.search(r'^backend:\s*(\S+)\s*$', text.split('\n---', 1)[0], re.M)
    return bool(m) and m.group(1).strip('\'"').lower() in ('claude-code', 'claude')


def opencode_agent_files() -> list[Path]:
    """The repo's agent files OpenCode loads: every agents/agent/*.md except the Claude-Code-only ones."""
    return [p for p in sorted((REPO / 'agents/agent').glob('*.md')) if not claude_code_only(p)]


def claude_only_agent_files() -> list[Path]:
    return [p for p in sorted((REPO / 'agents/agent').glob('*.md')) if claude_code_only(p)]


def repo_agents() -> list[str]:
    return [p.stem for p in opencode_agent_files()]


def probe_failures(log_text: str, staged: Path) -> list[str]:
    """The throwaway server's PROBE_FAILURES lines, deduplicated and readable: timestamps, run and span ids dropped,
    the staged config directory shown as ~/.config/opencode. At most ten."""
    seen: list[str] = []
    for line in log_text.splitlines():
        if any(f in line for f in PROBE_FAILURES):
            msg = re.sub(r'^timestamp=\S+ level=\S+ run=\S+ ', '', line)
            msg = re.sub(r' (http\.span|role|ref)=\S+', '', msg).replace(str(staged), '~/.config/opencode')
            if msg not in seen:
                seen.append(msg)
    return seen[:10]


# @beacon[
#   id=oc-server@probe_candidate_config,
#   role=probe_candidate_config,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Before the live service is touched: stage the config this restart WILL install (installed files overlaid with the repo's) in temp homes and have a throwaway OpenCode on PROBE_PORT load it; a configuration diagnostic, a plugin that fails to load, or a repo agent missing from /api/agent refuses the restart and leaves the working service running.,
# ]
def probe_candidate_config() -> str | None:
    """None if OpenCode accepts the config this restart is about to install, else what went wrong.

    The staged tree mirrors the install below (copy-only overlay): the installed opencode.json, agents/ and plugins/,
    then the repo's config/opencode.json, plugins/*.ts and agents/agent/*.md on top. (agents/AGENTS.md is not
    config: it installs to ~/AGENTS.md, a plain instruction file OpenCode does not validate.)
    A foreground `opencode serve` with its own config, data and state homes and its own password never reads or writes
    the service's settings, registration or database. It must answer /api/agent with every repo agent, and its warnings
    (--print-logs) must hold no configuration diagnostic and no failed plugin. ~5 s.
    """
    root = Path(tempfile.mkdtemp(prefix='oc-config-probe-'))
    cfg = root / 'config' / 'opencode'
    cfg.mkdir(parents=True)
    try:
        if (CONFIG_HOME / 'opencode.json').is_file():
            shutil.copy2(CONFIG_HOME / 'opencode.json', cfg / 'opencode.json')
        for sub in ('agents', 'plugins'):
            if (CONFIG_HOME / sub).is_dir():
                shutil.copytree(CONFIG_HOME / sub, cfg / sub)
        overlay = [(REPO / 'config/opencode.json', cfg / 'opencode.json')]
        overlay += [(p, cfg / 'plugins' / p.name) for p in sorted((REPO / 'plugins').glob('*.ts'))]
        overlay += [(p, cfg / 'agents' / p.name) for p in opencode_agent_files()]
        for p in claude_only_agent_files():              # never staged, as never installed (D6)
            (cfg / 'agents' / p.name).unlink(missing_ok=True)
        for s, d in overlay:
            if s.is_file():
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_bytes(s.read_bytes())
        if subprocess.run(['ss', '-H', '-ltn', f'sport = :{PROBE_PORT}'], capture_output=True, text=True, timeout=5).stdout.strip():
            return f'the config-check port {PROBE_PORT} is busy; free it and rerun'
        used, limit = inotify_instances()
        if limit and limit - used < 8:
            # A server that cannot get file watchers answers /api/info but hangs listing its agents (2026-10-20).
            return (f'only {limit - used} of {limit} inotify instances are free ({used} in use, mostly idle `claude -p` '
                    'processes); a new OpenCode server hangs without them. Raise the limit '
                    '(`sudo sysctl fs.inotify.max_user_instances=1024`, persist it in /etc/sysctl.d) or close idle '
                    'Claude processes, then rerun')
        password = secrets.token_urlsafe(24)
        env = dict(os.environ, XDG_CONFIG_HOME=str(root / 'config'), XDG_DATA_HOME=str(root / 'data'),
                   XDG_STATE_HOME=str(root / 'state'), OPENCODE_PASSWORD=password)
        env.pop('OPENCODE_SERVER_PASSWORD', None)
        with (root / 'probe.log').open('wb') as out:
            proc = subprocess.Popen([str(BINARY), 'serve', '--hostname', '127.0.0.1', '--port', str(PROBE_PORT),
                                     '--print-logs', '--log-level', 'warn'],
                                    cwd=str(root), env=env, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        base = f'http://127.0.0.1:{PROBE_PORT}'
        try:
            deadline = time.monotonic() + 40
            while True:
                if proc.poll() is not None:
                    log = (root / 'probe.log').read_text(errors='replace')[-1500:]
                    return f'the config-check server exited {proc.returncode}: {log}'
                try:
                    if http_get(base, '/api/info', password, timeout=2).get('version'):
                        break
                except (OSError, ValueError, RuntimeError):
                    pass
                if time.monotonic() >= deadline:
                    return 'the config-check server never answered /api/info (40 s)'
                time.sleep(.25)
            agents: list = []
            slow = 0
            for _ in range(4):                      # a location boots lazily: the first answer can be empty or slow
                try:
                    agents = http_get(base, '/api/agent', password, timeout=120) or []
                except RuntimeError as e:
                    return str(e).replace(str(cfg), '~/.config/opencode')
                except OSError:                     # a slow boot is not a bad config: wait again
                    slow += 1
                    print(f'  config-check server slow to list its agents (try {slow}); waiting again ...', flush=True)
                    continue
                if agents:
                    break
                time.sleep(2)
            if not agents and slow:
                return 'the config-check server never listed its agents (4 tries, up to 120 s each)'
            loaded = {a.get('id') for a in agents if isinstance(a, dict)}
            problems = [f'agent file(s) not loaded by OpenCode: {", ".join(m)} (check their frontmatter and model line)'
                        for m in [[a for a in repo_agents() if a not in loaded]] if m]
            time.sleep(1.5)                         # let the boot-time warnings reach the log
            problems += probe_failures((root / 'probe.log').read_text(errors='replace'), cfg)
            return '\n  '.join(problems) if problems else None
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                os.killpg(proc.pid, signal.SIGKILL)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def inotify_instances() -> tuple[int, int]:
    """→ (inotify instances this user's processes hold, the per-user limit); (0, 0) when unreadable."""
    try:
        limit = int(Path('/proc/sys/fs/inotify/max_user_instances').read_text().strip())
    except (OSError, ValueError):
        return 0, 0
    used, uid = 0, os.getuid()
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != uid:
                continue
            for fd in (proc / 'fd').iterdir():
                try:
                    if os.readlink(fd) == 'anon_inode:inotify':
                        used += 1
                except OSError:
                    pass
        except OSError:
            pass
    return used, limit


def service_pid() -> int | None:
    """The live service's server pid, or None when it does not answer."""
    try:
        return int(oc_info().get('pid') or 0) or None
    except Exception:  # noqa: BLE001
        return None


def copy_if_changed(src: Path, dst: Path) -> bool:
    data = src.read_bytes()
    if dst.is_file() and dst.read_bytes() == data:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return True


# @beacon[
#   id=oc-server@install_files,
#   role=install_files,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Install the repo's config, plugins and agent files into ~/.config/opencode (copy-only; the previous opencode.json kept as .bak-prev-sync; Claude-Code-only agent files never installed, D6), and the worker instructions agents/AGENTS.md into ~/AGENTS.md (removing any global copy, so it never reaches the Manager in manager-sessions). Runs only after the config check and the idle check.,
# ]
def install_files() -> None:
    """The repo's files into ~/.config/opencode, and the worker instructions into ~/AGENTS.md. Copy-only: never
    deletes an installed file, except a global ~/.config/opencode/AGENTS.md, which would reach every session, and an
    installed copy of a Claude-Code-only agent file (claude_code_only), which OpenCode must never run."""
    dest = CONFIG_HOME / 'opencode.json'
    data = (REPO / 'config/opencode.json').read_bytes()
    if dest.is_file() and dest.read_bytes() == data:
        print('Installed opencode.json: already current.', flush=True)
    else:
        if dest.is_file():
            (CONFIG_HOME / 'opencode.json.bak-prev-sync').write_bytes(dest.read_bytes())
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        print('Installed opencode.json (previous copy kept as opencode.json.bak-prev-sync).', flush=True)
    prompt = REPO / 'agents/AGENTS.md'
    if prompt.is_file():
        print(f"Installed ~/AGENTS.md (the worker instructions): {'updated' if copy_if_changed(prompt, HOME / 'AGENTS.md') else 'already current'}.", flush=True)
    stale = CONFIG_HOME / 'AGENTS.md'
    if stale.is_file():
        stale.unlink()
        print('Removed the global ~/.config/opencode/AGENTS.md (it would reach every session, the Manager\'s included).', flush=True)
    for label, files, sub in (('plugins', sorted(REPO.glob('plugins/*.ts')), 'plugins'),
                              ('agents', opencode_agent_files(), 'agents')):
        changed = [p.name for p in files if copy_if_changed(p, CONFIG_HOME / sub / p.name)]
        print(f"Installed {label}: {', '.join(changed) if changed else 'already current'}.", flush=True)
    claude_only = claude_only_agent_files()
    if claude_only:
        removed = []
        for p in claude_only:
            installed = CONFIG_HOME / 'agents' / p.name
            if installed.is_file():
                installed.unlink()
                removed.append(p.name)
        print(f"Not installed (Claude Code only, `backend: claude-code`): {', '.join(p.name for p in claude_only)}"
              + (f"; removed the installed copies of {', '.join(removed)}" if removed else "") + ".", flush=True)


def ensure_service_settings() -> None:
    """Port, hostname and environment of the service, as this machine expects them; set what differs."""
    out = subprocess.run([str(BINARY), 'service', 'get'], capture_output=True, text=True, timeout=30)
    try:
        current = json.loads(out.stdout or '{}')
    except ValueError:
        current = {}
    wanted = [('hostname', HOSTNAME), ('port', PORT)] + [('env', (k, v)) for k, v in SERVICE_ENV.items()]
    for key, value in wanted:
        if key == 'env':
            name, val = value
            if (current.get('env') or {}).get(name) == val:
                continue
            args = ['env', name, val]
        else:
            if current.get(key) == value:
                continue
            args = [key, str(value)]
        r = subprocess.run([str(BINARY), 'service', 'set', *args], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f'opencode service set {" ".join(args)} failed: {(r.stderr or r.stdout).strip()[:300]}')
        print(f'Service setting: {" ".join(args)}', flush=True)


def restart_proxy(cfg: dict) -> None:
    """The payload proxy ([ap:NN279Q]) must be up before the first model call of any provider routed through it.
    Restarted, not just started, so it serves its current code; no session is running at this point."""
    bases = [((p or {}).get('settings') or {}).get('baseURL', '') for p in (cfg.get('providers') or {}).values()]
    proxied = [b for b in bases if '127.0.0.1' in b or 'localhost' in b]
    if not proxied:
        return
    proxy_sh = REPO / 'proxy/oc_payload_proxy.sh'
    r = subprocess.run(['bash', str(proxy_sh), 'restart'], capture_output=True, text=True)
    last = (r.stdout or r.stderr).strip().splitlines()
    print(f"Payload proxy ({len(proxied)} provider(s) routed): {last[-1] if last else 'no output'}", flush=True)
    if r.returncode != 0:
        raise RuntimeError(f'payload proxy failed to start; every proxied provider would fail. See {proxy_sh}')


# @beacon[
#   id=auto-beacon@main-o7pk,
#   role=main,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def main() -> int:
    check_only = '--check' in sys.argv[1:]
    lock = open(f'/tmp/opencode-restart-{os.getuid()}.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if not BINARY.is_file():
        raise RuntimeError(f'OpenCode binary missing: {BINARY}')
    cfg = repo_config()
    # manager-quick.md is generated from quick-worker/AGENTS.md (its one source) before the config check reads the agents.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import build_manager_quick_agent
    if build_manager_quick_agent.build():
        print('Regenerated agents/agent/manager-quick.md from quick-worker/AGENTS.md (commit it).', flush=True)
    # manager-gpt.md (the GPT Manager) is generated from manager.md plus the GPT overlay in agents/manager-gpt/.
    import build_manager_gpt_agent
    if build_manager_gpt_agent.build():
        print('Regenerated agents/agent/manager-gpt.md from manager.md + agents/manager-gpt/ (commit it).', flush=True)
    gate = None if check_only else workflowy_gate(cfg)
    problem = probe_candidate_config()
    if problem:
        raise RuntimeError('OpenCode would REJECT the config this restart is about to install, so nothing was '
                           f'installed and the running service is untouched. Fix it in the repo and rerun.\n  {problem}')
    print(f'Config check: the candidate config loads cleanly with all {len(repo_agents())} repo agents '
          '(throwaway server, own homes).', flush=True)
    if check_only:
        return 0

    old_pid = service_pid()
    if old_pid:
        ok, active = oc_status()
        if not ok:
            raise RuntimeError('Cannot establish whether a session is running (/api/session/active); refusing restart')
        if active:
            raise RuntimeError(f'{len(active)} session(s) running: {", ".join(sorted(active))}; finish or interrupt '
                               'them before restarting. Nothing was installed or restarted.')
    install_files()
    ensure_service_settings()
    restart_proxy(cfg)
    verb = 'restart' if old_pid else 'start'
    r = subprocess.run([str(BINARY), 'service', verb], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f'opencode service {verb} failed: {(r.stderr or r.stdout).strip()[:500]}')
    print(f'opencode service {verb}: {(r.stdout or r.stderr).strip()[:200] or "ok"}', flush=True)

    deadline = time.monotonic() + 60
    while True:
        pid = service_pid()
        if pid and pid != old_pid:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('the service did not come back with a new server process within 60 s; '
                               'inspect `opencode service status`')
        time.sleep(.5)
    info = oc_info()
    print(f'OpenCode service: UP (pid {pid}, version {info.get("version")}).', flush=True)
    print('Checking MCP servers (a fresh uv install may take time) ...', flush=True)
    deadline = time.monotonic() + 120
    states: dict = {}
    while time.monotonic() < deadline:
        try:
            states = {s.get('name'): (s.get('status') or {}).get('status') for s in (oc_get('/api/mcp', timeout=60) or [])}
        except Exception:  # noqa: BLE001
            states = {}
        if states.get('workflowy') == 'connected':
            break
        time.sleep(2)
    for name, st in states.items():
        print(f'  MCP {name}: {st}', flush=True)
    if states.get('workflowy') != 'connected':
        if gate and not gate[3]:
            # The daemon was down before the restart: the restart itself succeeded, and says plainly what is missing.
            print(f'\nREADY WITHOUT WORKFLOWY MCP: OpenCode is up (pid {pid}) and its agents can be reached, but '
                  f'Workflowy MCP is {states.get("workflowy") or "absent"} because {gate[2]} was not reachable. Start '
                  'the daemon on Windows, then restart OpenCode again.', flush=True)
            return 0
        raise RuntimeError('OpenCode is up, but Workflowy MCP is NOT connected. Inspect /api/mcp; do not infer '
                           'daemon readiness from HTTP 200.')
    if gate:
        try:
            with socket.create_connection(gate[:2], timeout=3):
                pass
            print(f'Windows destination TCP: reachable ({gate[2]}).', flush=True)
        except OSError as e:
            print(f'⚠️  Windows destination TCP: not reachable from here ({gate[2]}: {e}), though OpenCode reports '
                  'Workflowy MCP connected.', flush=True)
    print('MCP connected is separate from browser-tab registration; verify tabs with system.broker.status when needed.', flush=True)
    print('\nREADY. Attach from PowerShell (plain `opencode` attaches to the service):')
    print('wsl bash -lc "opencode"')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BlockingIOError:
        print('RESTART FAILED / NOT READY: another restart is running (lock held)', file=sys.stderr, flush=True)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        print(f'RESTART FAILED / NOT READY: {e}', file=sys.stderr, flush=True)
        sys.exit(1)
