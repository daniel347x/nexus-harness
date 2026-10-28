"""Native Claude login folders; secret-free selection, shared stores, no token swapping.

Only an explicit guarded restart commits selection. The default is deliberately
CLAUDE_CONFIG_DIR *unset*, not ~/.claude (native .claude.json moves when set).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

LABELS = ('personal', 'work')
# These contain authentication/account policy, identifiers, or account-bound caches.
# Backup artifacts stay local too: they are snapshots, not active shared stores.
# All other existing children are shared, including future user-authored stores.
PRIVATE = {'.credentials.json', '.claude.json', 'backups', 'cache', 'telemetry',
           'statsig', 'remote-settings.json', 'policy-limits.json',
           'policy-limits.json.stamp.json'}
RELINKABLE = {'.last-cleanup', '.last-update-result.json'}  # disposable installation housekeeping
PROJECT_KEYS = {'allowedTools', 'hasTrustDialogAccepted', 'mcpServers', 'mcpContextUris',
                'enabledMcpjsonServers', 'disabledMcpjsonServers',
                'hasClaudeMdExternalIncludesApproved', 'hasClaudeMdExternalIncludesWarningShown'}
PREFERENCE_KEYS = {'hasCompletedOnboarding', 'lastOnboardingVersion', 'installMethod',
                   'autoUpdates', 'autoUpdatesProtectedForNative', 'theme', 'verbose',
                   'preferredNotifChannel', 'mcpServers'}


def registry_path():
    return Path.home() / '.config/opencode-companion/claude-accounts.json'


def selector_path():
    return Path.home() / '.local/state/opencode-companion/claude-account.json'


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {} if default is None else default


def _write(path, data):
    """Private atomic replacement, with file AND directory durability and exact readback.

    A raised error after replace is deliberately not a claim that the old value remains.
    The transition driver reconciles the persisted selector even when this function raises.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_parent(path)
        if _read(path) != data:
            raise OSError('atomic account record readback differs from the intended value')
    finally:
        temporary.unlink(missing_ok=True)


def _sync_parent(path):
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def selector_snapshot():
    """Exact selector presence/value and independently resolved native folder identity.

    Missing is meaningful only as a recorded old input; malformed/unreadable is an error,
    never an implicit default or old-account recovery decision.
    """
    try:
        value = json.loads(selector_path().read_text(encoding='utf-8'))
        present = True
    except FileNotFoundError:
        value, present = {}, False
    if (not isinstance(value, dict) or type(value.get('epoch', 0)) is not int
            or value.get('epoch', 0) < 0 or not isinstance(value.get('config_dir'), (str, type(None)))):
        raise ValueError('malformed Claude account selector')
    account = _folder_identity(value.get('config_dir'), _read(registry_path()))
    return {'present': present, 'value': value,
            'account': {**{k: account.get(k) for k in ('label', 'config_dir', 'identity')},
                        'epoch': value.get('epoch', 0)}}


def same_account(left, right):
    return all(left.get(k) == right.get(k) for k in ('config_dir', 'identity', 'epoch'))


def selection_truth(old, destination, operation_id, target_epoch):
    """Classify actual persisted/native truth, never the writer's exception position."""
    actual = selector_snapshot()
    if (actual['present'] == old['present'] and actual['value'] == old['value']
            and same_account(actual['account'], old['account'])):
        return 'old', actual
    value = actual['value']
    if (destination is not None and actual['present'] and value.get('operation_id') == operation_id
            and value.get('epoch') == target_epoch
            and all(value.get(k) == destination.get(k) for k in ('label', 'config_dir', 'identity'))
            and same_account(actual['account'], {**destination, 'epoch': target_epoch})):
        return 'destination', actual
    return 'other', actual


def credentials_path(account=None):
    account = selected_account() if account is None else account
    return Path(account.get('config_dir') or (Path.home() / '.claude')) / '.credentials.json'


def account_environment(env, account):
    """Apply the deliberate selection only AFTER launch sanitization."""
    env = dict(env)
    env.pop('CLAUDE_CONFIG_DIR', None)
    if account.get('config_dir'):
        env['CLAUDE_CONFIG_DIR'] = str(account['config_dir'])
    return env


def _folder_identity(config_dir, registry):
    state = Path(config_dir) / '.claude.json' if config_dir else Path.home() / '.claude.json'
    native = _read(state).get('oauthAccount') or {}
    email = str(native.get('emailAddress') or '').casefold()
    key = native.get('accountUuid') or email
    credential = Path(config_dir or Path.home() / '.claude') / '.credentials.json'
    try:
        login_at = credential.stat().st_mtime
    except FileNotFoundError:
        login_at = 0
    # No token or token hash enters records. Unknown native identity stays folder-specific.
    identity = hashlib.sha256(str(key or ('folder:' + str(credential.parent))).encode()).hexdigest()[:24]
    label = next((label for label, expected in registry.get('emails', {}).items()
                  if email and email == expected.casefold()), 'default' if config_dir is None else 'unmapped')
    return {'label': label, 'config_dir': str(config_dir) if config_dir else None,
            'identity': identity, 'email': email or None, 'login_at': login_at}


# @beacon[
#   id=opc@selected_claude_account,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Resolve the secret-free selector against the folder's actual native OAuth identity; unset config means the existing default login and inherited directory overrides never select an account.,
# ]
def selected_account():
    registry, selector = _read(registry_path()), _read(selector_path())
    account = _folder_identity(selector.get('config_dir'), registry)
    return {**account, 'epoch': selector.get('epoch', 0)}


def auth_status(config_dir=None):
    """Native status; return a whitelist only, never raw CLI diagnostics or credentials."""
    from .launch import CLI, sanitize_environment
    env = account_environment(sanitize_environment(dict(os.environ))[0], {'config_dir': config_dir})
    result = subprocess.run([CLI, 'auth', 'status', '--json'], env=env,
                            capture_output=True, text=True, timeout=30)
    try:
        raw = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError('native auth status unavailable; run claude auth status in the terminal') from None
    return {key: raw.get(key) for key in ('loggedIn', 'authMethod', 'apiProvider',
                                        'email', 'accountId', 'subscriptionType')}


# @beacon[
#   id=opc@resolve_claude_account,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Resolve a fixed personal/work label by native auth status in every enrolled folder; the current default folder wins duplicates and no assumed personal/default mapping survives a manual login change.,
# ]
def resolve_account(label):
    if label not in LABELS:
        raise ValueError('account must be personal or work')
    registry = _read(registry_path())
    expected = registry.get('emails', {}).get(label)
    if not expected:
        raise ValueError(f'{label} is not enrolled; run oc.sh claude_accounts enroll {label} --email EMAIL')
    for directory in [None, *registry.get('directories', [])]:
        status = auth_status(directory)
        if (status.get('loggedIn') and str(status.get('email') or '').casefold() == expected.casefold()
                and status.get('authMethod') == 'claude.ai' and status.get('subscriptionType') == 'max'):
            return {**_folder_identity(directory, registry), 'label': label,
                    'epoch': _read(selector_path()).get('epoch', 0), 'email': status['email']}
    raise ValueError(f'no enrolled native Max login currently holds {label}; check claude_accounts status')


# @beacon[
#   id=opc@commit_claude_selection,
#   slice_labels=opc-basic,opc-claude-seats,opc-claude-restart,
#   kind=ast,
#   comment=Sole durable selector commit after proved old-host exit: compare exact previous/native truth, fsync file/replace/parent/readback, and reconcile a persisted same-operation target without replacement or another epoch.,
# ]
def commit_selection(account, *, expected_previous=None, operation_id=None, target_epoch=None):
    """Sole selector writer: compare old truth; same-operation target is sync/readback, not replace.

    Called under the existing transition lock after proved old-host exit. A failed write
    (including after replacement) must still be resolved through ``selection_truth``.
    """
    actual = selector_snapshot()
    selector = actual['value']
    if operation_id and selector.get('operation_id') == operation_id:
        if (selector.get('epoch') != target_epoch or any(
                selector.get(k) != account.get(k) for k in ('label', 'config_dir', 'identity'))
                or not same_account(actual['account'], {**account, 'epoch': target_epoch})):
            raise ValueError('operation stamp exists with a different selector identity')
        # Complete interrupted durability confirmation without another replacement/epoch.
        fd = os.open(selector_path(), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _sync_parent(selector_path())
        if selector_snapshot() != actual:
            raise OSError('selector changed during durability readback')
        return {**account, **selector}
    if expected_previous is not None and actual != expected_previous:
        raise ValueError('selector/native identity changed from the recorded previous selection')
    value = {key: account.get(key) for key in ('label', 'config_dir', 'identity')}
    epoch = int(selector.get('epoch', 0)) + 1
    if target_epoch is not None and target_epoch != epoch:
        raise ValueError('target selector epoch differs from the recorded next epoch')
    value.update(epoch=epoch, selected_at=time.time())
    if operation_id:
        value['operation_id'] = operation_id
    _write(selector_path(), value)
    return {**account, **value}


def private_store_name(name):
    """Account state and local snapshots are not active shared stores."""
    return (name in PRIVATE or name.startswith(('.credentials.', '.claude.json.', 'policy-limits.', 'remote-settings.'))
            or name.endswith('~') or re.search(r'\.(?:bak|backup)(?:[.\-]|$)', name) is not None)


# @beacon[
#   id=opc@claude_account_shared_directory,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Link active non-account stores to canonical ~/.claude; preserve local backup artifacts and account state. check_only validates the same store/preference plan without writes before a restart can close any owner.,
# ]
def prepare_shared_directory(directory, *, home=None, check_only=False):
    home = Path.home() if home is None else Path(home)
    source, directory = home / '.claude', Path(directory)
    if directory.resolve() == source.resolve():
        raise ValueError('the default directory must remain unset, never prepared as an extra login')
    shared, private = [], []
    # Preflight all conflicts before making a link. Never hide or replace an existing history.
    entries = list(source.iterdir()) if source.exists() else []
    for entry in entries:
        name = entry.name
        if private_store_name(name):
            private.append(name)
            continue
        target = directory / name
        if target.is_symlink() and target.resolve() == entry.resolve():
            shared.append(name)
            continue
        if (target.exists() or target.is_symlink()) and not (name in RELINKABLE and not target.is_dir()):
            raise ValueError(f'{target}: existing store is not the canonical link; preserve/migrate it first')
        shared.append(name)
    # Mixed cache: release notes are installation data, model catalog is account-bound.
    changelog = source / 'cache/changelog.md'
    if changelog.exists():
        cache = directory / 'cache'
        if (cache.exists() or cache.is_symlink()) and not cache.is_dir():
            raise ValueError(f'{cache}: cache must be a directory')
        target = cache / 'changelog.md'
        if target.is_dir():
            raise ValueError(f'{target}: changelog must be a file')
    original = _read(home / '.claude.json')
    destination = _read(directory / '.claude.json')
    for key in PREFERENCE_KEYS:
        if key in original:
            destination[key] = original[key]
    projects = destination.setdefault('projects', {})
    for cwd, values in original.get('projects', {}).items():
        own = projects.setdefault(cwd, {})
        for key in PROJECT_KEYS:
            if key in values:
                own[key] = values[key]
            else:
                own.pop(key, None)
    result = {'shared': sorted([*shared, *(['cache/changelog.md'] if changelog.exists() else [])]),
              'private': sorted(private), 'project_preferences': 'mirrored from default'}
    if check_only:
        return result
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in shared:
        target = directory / name
        if name in RELINKABLE and (target.exists() or target.is_symlink()) and not (
                target.is_symlink() and target.resolve() == (source / name).resolve()):
            target.unlink()
        if not target.is_symlink():
            target.symlink_to(source / name, target_is_directory=(source / name).is_dir())
    if changelog.exists():
        cache.mkdir(exist_ok=True)
        target = cache / 'changelog.md'
        if not target.is_symlink() or target.resolve() != changelog.resolve():
            target.unlink(missing_ok=True)  # disposable release-note cache, never user history/config
            target.symlink_to(changelog)
    _write(directory / '.claude.json', destination)
    return result


@contextlib.contextmanager
def transition_lock(*, root=None, shared=False):
    """One POSIX transition; a new launch refuses briefly instead of racing the stop."""
    import fcntl
    root = Path(root) if root is not None else Path.home() / '.local/state/opencode-companion/claude-headless'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root / 'account-transition.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Claude account/host transition in progress; retry after it settles') from None
        yield
    finally:
        os.close(fd)


@contextlib.asynccontextmanager
async def launch_gate(root):
    with transition_lock(root=root, shared=True):
        yield


# @beacon[
#   id=opc@claude_account_enroll,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=One operator native browser enrollment into an extra folder with linked stores; identifies the real default account first, verifies the approved email and records labels without changing the active selector or touching default login.,
# ]
def enroll(label, email):
    if label not in LABELS:
        raise ValueError('account must be personal or work')
    email = email.casefold()
    registry = _read(registry_path())
    default = auth_status()
    default_email = str(default.get('email') or '').casefold()
    if not default.get('loggedIn') or not default_email:
        raise ValueError('identify/sign into the existing default account first')
    if default_email == email:
        registry.setdefault('emails', {})[label] = email
        _write(registry_path(), registry)
        return {'label': label, 'config_dir': None, 'auth': default}
    directory = Path.home() / ('.claude-' + label)
    links = prepare_shared_directory(directory)
    from .launch import CLI, sanitize_environment
    env = account_environment(sanitize_environment(dict(os.environ))[0], {'config_dir': str(directory)})
    # Inherit the operator's terminal for the native link/code flow, never capture a secret.
    code = subprocess.call([CLI, 'auth', 'login', '--claudeai', '--email', email], env=env)
    if code:
        raise ValueError(f'native login exited {code}; selector unchanged')
    actual = auth_status(str(directory))
    if str(actual.get('email') or '').casefold() != email or actual.get('subscriptionType') != 'max':
        raise ValueError('the approved login is not the requested Max email; registry/selector unchanged')
    links = prepare_shared_directory(directory)  # native login keeps its own identity; restore canonical preferences
    registry.setdefault('emails', {})[label] = email
    other = next(item for item in LABELS if item != label)
    registry['emails'][other] = default_email
    registry['directories'] = list(dict.fromkeys([*registry.get('directories', []), str(directory)]))
    _write(registry_path(), registry)
    return {'label': label, 'config_dir': str(directory), 'auth': actual, **links}


def status():
    registry = _read(registry_path())
    rows = []
    for directory in [None, *registry.get('directories', [])]:
        auth = auth_status(directory)
        row = _folder_identity(directory, registry)
        email = str(auth.get('email') or '').casefold()
        row['label'] = next((k for k, v in registry.get('emails', {}).items() if v.casefold() == email), row['label'])
        rows.append({**row, 'auth': auth})
    return {'active': selected_account(), 'accounts': rows}
