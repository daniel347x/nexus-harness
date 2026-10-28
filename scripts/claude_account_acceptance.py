#!/usr/bin/env python3
"""Folder-parity acceptance for Dan's Claude Max account switch.

Proves the thing Dan worries about: a second native login folder (``CLAUDE_CONFIG_DIR``) must not
change what an agent reads (project instructions, settings profile, MCP and agents file) or where
its conversation lives (one shared ``~/.claude/projects``). The login folder decides the login and
its account state; the working folder decides everything else.

Two modes:

- **synthetic (default)** — a scratch ``HOME`` and a scratch second login folder, prepared by the
  same :func:`accounts.prepare_shared_directory` the enrollment uses. No real login, host, session
  or CLI process is touched. This is the mode to run during a build.
- **``--live --label work``** — after Dan's real second account is enrolled: read-only checks of
  the real second folder's links and mirrored project state, plus the same launch-record parity in
  scratch run directories. It reads the native auth status to resolve the label but starts no
  inference turn, no host and no session, and writes nothing to the real configuration folders.

Output is a PASS/FAIL row per working folder and role; exit status 1 for any failure, 2 for a
precondition problem. ``--json`` prints the same receipt as one JSON object.

Examples::

    python3 scripts/claude_account_acceptance.py
    python3 scripts/claude_account_acceptance.py --live --label work
    python3 scripts/claude_account_acceptance.py --live --label work --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from claude_headless import accounts, launch

WORKING_FOLDERS = (
    "/home/daniel347x",
    "/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker",
    "/mnt/e/__daniel347x/__Dan_Root/__repos/manager-sessions",
)
"""Dan's three active folders: home, quick sessions, Manager sessions."""

TOKEN = "account-acceptance-bearer-value"
ENDPOINT = "http://172.18.176.1:8766/mcp"
PROMPT = "You are an account-folder acceptance seat. Never leave the scratch directory.\n"
SESSION_ID = "6f0c6f4e-3f59-4b4d-8f0a-2f2e2a0e1b37"
MODEL = "claude-haiku-5-5"
EFFORT = "low"
INHERITED_CONFIG_DIR = "/inherited/login-folder-that-must-not-pass-through"
INHERITED_ENVIRON = {
    "PATH": "/usr/bin",
    "WORKFLOWY_MCP_BEARER": TOKEN,
    "CLAUDE_CONFIG_DIR": INHERITED_CONFIG_DIR,
}
SHARED_DIGEST_KEYS = ("prompt_sha256", "settings_sha256", "mcp_sha256", "profile_sha256")
SYNTHETIC_SHARED_DIRS = ("projects", "file-history", "todos", "plans", "skills", "plugins")
SYNTHETIC_SHARED_FILES = ("settings.json", "history.jsonl")
SYNTHETIC_PRIVATE = ("backups", "cache", "telemetry", "statsig")


def _launch_account(label, config_dir, identity):
    return {"label": label, "config_dir": str(config_dir) if config_dir else None,
            "identity": identity, "epoch": 0}


def _build(run_dir, cwd, account, role):
    run_dir.mkdir(parents=True, exist_ok=True)
    return launch.build_launch(
        run_dir, native_uuid=SESSION_ID, cwd=cwd, model=MODEL, effort=EFFORT,
        system_prompt=PROMPT, environ=dict(INHERITED_ENVIRON), workflowy_url=ENDPOINT,
        role=role, account=dict(account))


def _normalized_argv(argv, run_dir):
    return [argument.replace(str(run_dir), "<run>") for argument in argv]


def launch_parity_failures(root, cwd, role, second_dir):
    """Empty when the default-folder and second-folder launches are the same launch."""
    failures: list[str] = []
    first_dir, second_dir_run = root / "default" / role, root / "second" / role
    first = _build(first_dir, cwd, _launch_account('default', None, 'default-identity'), role)
    second = _build(second_dir_run, cwd, _launch_account('second', second_dir, 'second-identity'), role)

    if first['safe']['cwd'] != second['safe']['cwd'] or first['safe']['cwd'] != cwd:
        failures.append('cwd differs between accounts')
    for key in SHARED_DIGEST_KEYS:
        if first['safe'].get(key) != second['safe'].get(key):
            failures.append(f'{key} differs')
    if role == 'Manager':
        if not first['safe'].get('agents_sha256'):
            failures.append('Manager launch carries no agents.json snapshot')
        elif first['safe']['agents_sha256'] != second['safe']['agents_sha256']:
            failures.append('agents_sha256 differs')
    elif 'agents_sha256' in first['safe'] or 'agents_sha256' in second['safe']:
        failures.append('a non-Manager launch unexpectedly carries agents.json')

    for name in ('prompt.md', 'settings.json', 'mcp.json'):
        if (first_dir / name).read_bytes() != (second_dir_run / name).read_bytes():
            failures.append(f'{name} bytes differ')
    if role == 'Manager' and (first_dir / 'agents.json').read_bytes() != (
            second_dir_run / 'agents.json').read_bytes():
        failures.append('agents.json bytes differ')
    if first['profile'] != second['profile']:
        failures.append('applied settings profile differs')
    if _normalized_argv(first['argv'], first_dir) != _normalized_argv(second['argv'], second_dir_run):
        failures.append('argv differs beyond the private run-dir paths')

    if 'CLAUDE_CONFIG_DIR' in first['env']:
        failures.append('the default launch unexpectedly sets CLAUDE_CONFIG_DIR')
    if second['env'].get('CLAUDE_CONFIG_DIR') != str(second_dir):
        failures.append('the second launch does not carry the selected login folder')
    if INHERITED_CONFIG_DIR in first['env'].values() or INHERITED_CONFIG_DIR in second['env'].values():
        failures.append('an inherited CLAUDE_CONFIG_DIR passed through sanitization')
    if {name: value for name, value in first['env'].items() if name != 'CLAUDE_CONFIG_DIR'} != {
            name: value for name, value in second['env'].items() if name != 'CLAUDE_CONFIG_DIR'}:
        failures.append('the child environment differs beyond CLAUDE_CONFIG_DIR')
    if first['safe'].get('account', {}).get('label') != 'default' or second['safe'].get(
            'account', {}).get('label') != 'second':
        failures.append('the launch record does not name the selected account')
    for name, result in (('default', first), ('second', second)):
        if result['safe']['collector_envelope']['eligible'] is not True:
            failures.append(f'the {name} launch is not collector-eligible')
    return failures


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _is_private(name):
    """Use the mapper's account-state/local-backup classification in acceptance too."""
    return accounts.private_store_name(name)


def shared_folder_failures(home, directory):
    """Empty when every non-account store in ~/.claude is linked and no account state is."""
    failures: list[str] = []
    canonical, directory = Path(home) / '.claude', Path(directory)
    if not canonical.is_dir():
        return [f'no canonical login folder: {canonical}']
    if not directory.is_dir():
        return [f'the second login folder does not exist: {directory}']
    for entry in sorted(canonical.iterdir()):
        target = directory / entry.name
        if _is_private(entry.name):
            if target.is_symlink():
                failures.append(f'{entry.name}: account-owned entry is linked to the default login')
            continue
        if not target.is_symlink():
            failures.append(f'{entry.name}: shared store is not linked (state or history would split)')
        elif target.resolve() != entry.resolve():
            failures.append(f'{entry.name}: link points somewhere other than the default folder')
    if not (directory / 'projects').is_symlink():
        failures.append('projects: conversation history is not shared with the default login')
    elif (directory / 'projects').resolve() != (canonical / 'projects').resolve():
        failures.append('projects: conversation history resolves outside ~/.claude/projects')
    # The mixed cache: release notes are installation data (shared), the rest of the cache is not.
    changelog = canonical / 'cache' / 'changelog.md'
    if changelog.exists():
        linked_changelog = directory / 'cache' / 'changelog.md'
        if not linked_changelog.is_symlink() or linked_changelog.resolve() != changelog.resolve():
            failures.append('cache/changelog.md: release notes are not shared with the default login')

    original = _read_json(Path(home) / '.claude.json') or {}
    mirrored = _read_json(directory / '.claude.json')
    if mirrored is None:
        failures.append('the second folder has no .claude.json project state (was it enrolled?)')
        return failures
    for key in accounts.PREFERENCE_KEYS:
        if key in original and mirrored.get(key) != original[key]:
            failures.append(f'preference {key} is not mirrored from the default folder')
    for cwd, values in original.get('projects', {}).items():
        for key in accounts.PROJECT_KEYS:
            if key in values and mirrored.get('projects', {}).get(cwd, {}).get(key) != values[key]:
                failures.append(f'project preference {key} for {cwd} is not mirrored')
    return failures


def _synthetic_home(root):
    home = Path(root)
    canonical = home / '.claude'
    for name in SYNTHETIC_SHARED_DIRS:
        (canonical / name).mkdir(parents=True)
    for name in SYNTHETIC_SHARED_FILES:
        (canonical / name).write_text('{}\n')
    for name in SYNTHETIC_PRIVATE:
        (canonical / name).mkdir()
    (canonical / 'cache' / 'changelog.md').write_text('release notes are installation data\n')
    (canonical / '.credentials.json').write_text('{"synthetic": true}\n')
    (home / '.claude.json').write_text(json.dumps({
        'hasCompletedOnboarding': True,
        'theme': 'dark',
        'oauthAccount': {'emailAddress': 'synthetic@example.invalid'},
        'projects': {WORKING_FOLDERS[0]: {'allowedTools': ['Bash'],
                                          'hasTrustDialogAccepted': True}},
    }))
    return home


def _rows(root, second_dir):
    rows = []
    for cwd in WORKING_FOLDERS:
        for role in ('worker', 'Manager'):
            if not Path(cwd).is_dir():
                rows.append({'cwd': cwd, 'role': role, 'status': 'SKIP',
                             'detail': 'working folder not present on this machine'})
                continue
            problems = launch_parity_failures(root / 'launches', cwd, role, second_dir)
            rows.append({'cwd': cwd, 'role': role,
                         'status': 'PASS' if not problems else 'FAIL',
                         'detail': problems or
                         'same cwd, prompt/settings/MCP/agents bytes and argv; only CLAUDE_CONFIG_DIR differs'})
    return rows


def run_synthetic():
    with tempfile.TemporaryDirectory(prefix='claude-account-acceptance-') as temp:
        root = Path(temp)
        home = _synthetic_home(root / 'home')
        second_dir = root / '.claude-second'
        prepared = accounts.prepare_shared_directory(second_dir, home=home)
        return {
            'mode': 'synthetic',
            'shared': prepared['shared'],
            'private': prepared['private'],
            'folder_failures': shared_folder_failures(home, second_dir),
            'rows': _rows(root, second_dir),
        }


def run_live(label):
    home = Path.home()
    try:
        second = accounts.resolve_account(label)
    except (ValueError, OSError, RuntimeError) as error:
        return None, f'cannot resolve the enrolled {label} account: {error}'
    config_dir = second.get('config_dir')
    if not config_dir:
        return None, f'the {label} account is the default login; there is no second folder to compare'
    with tempfile.TemporaryDirectory(prefix='claude-account-acceptance-') as temp:
        return {
            'mode': 'live',
            'label': label,
            'config_dir': config_dir,
            # Secret-free account summary: the label, the folder and the folder identity hash only.
            'identity': second.get('identity'),
            'folder_failures': shared_folder_failures(home, config_dir),
            'rows': _rows(Path(temp), Path(config_dir)),
        }, None


def _render(receipt):
    lines = [f"account-folder acceptance — {receipt['mode']}"]
    if receipt['mode'] == 'live':
        lines.append(f"label {receipt['label']} · folder {receipt['config_dir']} · "
                     f"identity {receipt['identity']}")
    else:
        lines.append(f"shared by link: {', '.join(receipt['shared'])}")
        lines.append(f"kept private: {', '.join(receipt['private'])}")
    for problem in receipt['folder_failures']:
        lines.append(f"  FOLDER FAIL: {problem}")
    for row in receipt['rows']:
        detail = row['detail'] if isinstance(row['detail'], str) else '; '.join(row['detail'])
        lines.append(f"  {row['status']:4}  {row['role']:6}  {row['cwd']}  — {detail}")
    failed = _failed(receipt)
    lines.append(f"overall {'FAIL' if failed else 'PASS'}")
    if receipt['mode'] == 'live':
        lines.append('remaining live acceptance: switch A→B→A with the same Manager UUID and one '
                     'history, quota labelled with its account, a refused restart leaving the account '
                     'unchanged, desktop and phone buttons')
    return '\n'.join(lines)


def _failed(receipt):
    return bool(receipt['folder_failures']) or any(row['status'] == 'FAIL' for row in receipt['rows'])


# @beacon[
#   id=opc@claude_account_acceptance_main,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Run folder-parity acceptance; synthetic by default, --live reads actual native auth status and checks the real second folder without inference turns, host actions or configuration writes.,
# ]
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--live', action='store_true',
                        help='check the real enrolled second folder read-only (native auth status only; '
                             'no inference turn, no host)')
    parser.add_argument('--label', choices=accounts.LABELS,
                        help='the enrolled label to check in --live mode')
    parser.add_argument('--json', action='store_true', help='print the receipt as one JSON object')
    args = parser.parse_args(argv)

    if args.live:
        if not args.label:
            parser.error('--live needs --label')
        receipt, error = run_live(args.label)
        if error:
            print(error)
            return 2
    else:
        receipt = run_synthetic()

    print(json.dumps(receipt, indent=2) if args.json else _render(receipt))
    return 1 if _failed(receipt) else 0


if __name__ == '__main__':
    raise SystemExit(main())
