"""Offline native account storage/transition contract; never a real login/restart."""
import asyncio
import json
from pathlib import Path
import pytest
from claude_headless import accounts, restart
from test_claude_headless_restart import Harness, rec


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return tmp_path


def native(home, directory=None, email='personal@example.test'):
    root = Path(directory) if directory else home / '.claude'
    root.mkdir(parents=True, exist_ok=True)
    (root / '.credentials.json').write_text('{"synthetic": true}')
    state = root / '.claude.json' if directory else home / '.claude.json'
    state.write_text(json.dumps({'oauthAccount': {'emailAddress': email, 'accountUuid': email},
                                'projects': {'/work': {'allowedTools': ['Read'], 'hasTrustDialogAccepted': True}},
                                'cachedExperimentData': {'private': 'not copied'}}))


def test_shared_stores_and_private_account_state(isolated_home):
    home = isolated_home
    native(home)
    source = home / '.claude'
    for name in ('projects', 'skills', 'file-history', 'plugins', 'todos', 'plans', 'agent-memory', 'new-user-store'):
        (source / name).mkdir()
    for name in ('settings.json', 'history.jsonl', 'remote-settings.json', 'policy-limits.json'):
        (source / name).write_text('{}')
    second = home / '.claude-work'
    native(home, second, 'work@example.test')
    result = accounts.prepare_shared_directory(second)
    for name in result['shared']:
        assert (second / name).is_symlink()
        assert (second / name).resolve() == (source / name).resolve()
    assert not (second / '.credentials.json').is_symlink()
    assert not (second / 'remote-settings.json').exists()
    state = json.loads((second / '.claude.json').read_text())
    assert state['oauthAccount']['emailAddress'] == 'work@example.test'
    assert state['projects']['/work']['allowedTools'] == ['Read']
    assert 'cachedExperimentData' in state  # its own, never copied from the other account
    assert accounts.prepare_shared_directory(second) == result


def test_conflicting_history_never_hidden(isolated_home):
    home = isolated_home
    native(home)
    (home / '.claude/projects').mkdir()
    (home / '.claude-work/projects').mkdir(parents=True)
    with pytest.raises(ValueError, match='preserve/migrate'):
        accounts.prepare_shared_directory(home / '.claude-work')
    assert not (home / '.claude-work/projects').is_symlink()


@pytest.mark.parametrize('name', ['settings.json.bak-222', 'settings.json.bak',
                                  'settings.json.backup.20261027', 'settings.json~'])
def test_backup_artifacts_stay_private_and_do_not_block_switch(isolated_home, name):
    home = isolated_home
    native(home)
    second = home / '.claude-work'
    native(home, second, 'work@example.test')
    source_backup, own_backup = home / '.claude' / name, second / name
    source_backup.write_bytes(b'older personal settings')
    own_backup.write_bytes(b'newer work settings')
    result = accounts.prepare_shared_directory(second)
    assert name in result['private'] and name not in result['shared']
    assert not own_backup.is_symlink()
    assert source_backup.read_bytes() == b'older personal settings'
    assert own_backup.read_bytes() == b'newer work settings'
    assert accounts.prepare_shared_directory(second) == result
    from claude_account_acceptance import shared_folder_failures
    (home / '.claude/projects').mkdir()
    accounts.prepare_shared_directory(second)
    assert shared_folder_failures(home, second) == []


def test_shared_directory_check_is_read_only_even_with_missing_destination(isolated_home):
    home = isolated_home
    native(home)
    (home / '.claude/settings.json').write_text('{}')
    second = home / '.claude-work'
    plan = accounts.prepare_shared_directory(second, check_only=True)
    assert not second.exists()
    assert 'settings.json' in plan['shared']
    assert accounts.prepare_shared_directory(second) == plan


@pytest.mark.parametrize('conflict', ['projects', 'cache/changelog.md', '.claude.json'])
def test_shared_store_failure_is_found_before_closing_or_signalling_host(isolated_home, monkeypatch, conflict):
    home = isolated_home
    native(home)
    second = home / '.claude-work'
    native(home, second, 'work@example.test')
    (home / '.claude/projects').mkdir()
    if conflict == 'projects':
        (second / 'projects').mkdir()
    elif conflict == 'cache/changelog.md':
        (home / '.claude/cache').mkdir()
        (home / '.claude/cache/changelog.md').write_text('notes')
        (second / conflict).mkdir(parents=True)
    else:
        (second / conflict).write_text('malformed json')
    before = (second / '.claude.json').read_bytes()
    monkeypatch.setattr(accounts, 'resolve_account', lambda label: {**accounts._folder_identity(second, {}), 'label': label})
    h = Harness([rec(10, owner='host', state='ready')])
    with pytest.raises(restart.RestartRefused):
        do_restart(h, home / 'state', account='work', close_idle=True)
    assert not h.kills and not h.starts
    assert 'close_input' not in h.client.methods
    assert not (second / 'projects').is_symlink()
    assert (second / '.claude.json').read_bytes() == before


def test_switch_with_distinct_backups_preserves_them_and_starts_replacement(isolated_home, monkeypatch):
    home = isolated_home
    native(home)
    second = home / '.claude-work'
    native(home, second, 'work@example.test')
    accounts._write(accounts.registry_path(), {'emails': {'personal': 'personal@example.test', 'work': 'work@example.test'}})
    for folder, value in ((home / '.claude', 'personal'), (second, 'work')):
        (folder / 'settings.json.bak-222').write_text(value)
    (home / '.claude/settings.json').write_text('{}')
    monkeypatch.setattr(accounts, 'resolve_account', lambda label: {**accounts._folder_identity(second, {}), 'label': label})
    h = Harness([])
    report = do_restart(h, home / 'state', account='work')
    assert h.kills and h.starts == 1 and report['account']['label'] == 'work'
    assert (second / 'settings.json').is_symlink()
    assert (home / '.claude/settings.json.bak-222').read_text() == 'personal'
    assert (second / 'settings.json.bak-222').read_text() == 'work'


def test_default_is_unset_and_mapping_tracks_actual_login(isolated_home, monkeypatch):
    home = isolated_home
    native(home)
    accounts._write(accounts.registry_path(), {'emails': {'personal': 'personal@example.test', 'work': 'work@example.test'}})
    assert accounts.selected_account()['label'] == 'personal'
    assert 'CLAUDE_CONFIG_DIR' not in accounts.account_environment({'CLAUDE_CONFIG_DIR': '/evil'}, accounts.selected_account())
    native(home, email='work@example.test')
    assert accounts.selected_account()['label'] == 'work'
    monkeypatch.setattr(accounts, 'auth_status', lambda directory=None: {
        'email': 'work@example.test', 'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'max'})
    assert accounts.resolve_account('work')['config_dir'] is None
    with pytest.raises(ValueError, match='no enrolled'):
        accounts.resolve_account('personal')


def test_transition_excludes_launch_and_competing_transition(tmp_path):
    with accounts.transition_lock(root=tmp_path):
        with pytest.raises(RuntimeError, match='transition in progress'):
            with accounts.transition_lock(root=tmp_path, shared=True):
                pytest.fail('must not enter')
        with pytest.raises(RuntimeError):
            with accounts.transition_lock(root=tmp_path):
                pytest.fail('must not enter')


def do_restart(harness, tmp_path, **kwargs):
    harness.state_root = str(tmp_path)
    return restart.guarded_restart(harness.client, state_root=tmp_path,
        witness_fn=harness.witness, clock=harness.clock, sleep=harness.sleep,
        kill_fn=harness.kill, starter=harness.starter, **kwargs)


def test_refused_switch_never_changes_selector(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, 'resolve_account', lambda label: {'label': label, 'config_dir': None})
    accounts._write(accounts.selector_path(), {'label': 'personal', 'config_dir': None, 'epoch': 7})
    before = accounts.selector_path().read_bytes()
    h = Harness([rec(10, owner='host', state='ready', pending_message_uuids=['busy'])])
    with pytest.raises(restart.RestartRefused):
        do_restart(h, tmp_path / 'state', account='work', close_idle=True)
    assert accounts.selector_path().read_bytes() == before
    assert h.kills == []


def test_switch_commits_between_exit_and_start(tmp_path, monkeypatch):
    account = {**accounts.selected_account(), 'label': 'work'}
    monkeypatch.setattr(accounts, 'resolve_account', lambda label: dict(account))
    h = Harness([])
    old_starter = h.starter
    def starter():
        assert h.kills
        assert accounts.selected_account()['epoch'] == 1
        assert accounts._read(accounts.selector_path())['label'] == 'work'
        return old_starter()
    h.starter = starter
    report = do_restart(h, tmp_path / 'state', account='work')
    assert report['account']['epoch'] == 1
    assert report['phases']['host_exit_observed']


def test_enrollment_default_matches_does_not_login_or_move_selector(monkeypatch):
    monkeypatch.setattr(accounts, 'auth_status', lambda directory=None: {'email': 'me@example.test', 'loggedIn': True})
    monkeypatch.setattr(accounts.subprocess, 'call', lambda *a, **k: pytest.fail('no default login'))
    assert accounts.enroll('personal', 'me@example.test')['config_dir'] is None
    assert not accounts.selector_path().exists()


def test_preflight_uses_deliberate_account_after_sanitization(monkeypatch):
    from claude_headless import host
    seen = []
    class Process:
        returncode = 0
        def __init__(self, args):
            self.args = args
        async def communicate(self):
            if '--version' in self.args:
                return b'Claude Code synthetic', b''
            return json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
                               'subscriptionType': 'max'}).encode(), b''
    async def spawn(*args, **kwargs):
        seen.append(kwargs['env'])
        return Process(args)
    monkeypatch.setattr(host.asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', '/inherited-wrong')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-must-strip')
    account = {'label': 'work', 'config_dir': '/synthetic/work', 'identity': 'work', 'epoch': 2}
    result = asyncio.run(host.subscription_preflight(account))
    assert result['subscriptionType'] == 'max'
    assert all(env['CLAUDE_CONFIG_DIR'] == '/synthetic/work' for env in seen)
    assert all('ANTHROPIC_API_KEY' not in env for env in seen)


def test_native_atomic_housekeeping_replacement_does_not_prevent_switch(isolated_home):
    home = isolated_home
    native(home)
    source, second = home / '.claude', home / '.claude-work'
    (source / '.last-cleanup').write_text('canonical')
    (source / 'cache').mkdir()
    (source / 'cache/changelog.md').write_text('canonical notes')
    accounts.prepare_shared_directory(second)
    for name in ('.last-cleanup', 'cache/changelog.md'):
        (second / name).unlink()
        (second / name).write_text('native atomic replacement')
    accounts.prepare_shared_directory(second)
    assert (second / '.last-cleanup').is_symlink()
    assert (second / 'cache/changelog.md').is_symlink()
    assert (source / '.last-cleanup').read_text() == 'canonical'


def test_enroll_other_account_identifies_default_without_touching_it(isolated_home, monkeypatch):
    home = isolated_home
    native(home, email='work@example.test')
    (home / '.claude/projects').mkdir()
    before = {path: path.read_bytes() for path in (home / '.claude/.credentials.json', home / '.claude.json')}
    def auth(directory=None):
        return {'email': 'personal@example.test' if directory else 'work@example.test',
                'loggedIn': True, 'authMethod': 'claude.ai', 'subscriptionType': 'max'}
    def login(argv, *, env):
        assert env['CLAUDE_CONFIG_DIR'] == str(home / '.claude-personal')
        assert argv[-1] == 'personal@example.test'
        native(home, env['CLAUDE_CONFIG_DIR'], 'personal@example.test')
        return 0
    monkeypatch.setattr(accounts, 'auth_status', auth)
    monkeypatch.setattr(accounts.subprocess, 'call', login)
    result = accounts.enroll('personal', 'personal@example.test')
    assert result['label'] == 'personal'
    assert accounts._read(accounts.registry_path())['emails'] == {
        'personal': 'personal@example.test', 'work': 'work@example.test'}
    assert accounts.resolve_account('work')['config_dir'] is None
    assert accounts.resolve_account('personal')['config_dir'] == str(home / '.claude-personal')
    assert accounts.selected_account()['label'] == 'work'
    assert not accounts.selector_path().exists()
    assert all(path.read_bytes() == data for path, data in before.items())
