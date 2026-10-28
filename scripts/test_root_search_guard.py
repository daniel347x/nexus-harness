"""The root-search guard (docs/root-search-guard.md) against the shared vectors, plus its message and doubt rules.

The held OpenCode port (branch feat/root-search-guard-2) must pass the same vectors before it ships."""
import json
import os
from pathlib import Path
import time

import pytest

from claude_headless.root_search import ROOTS_CONFIG, ROOTS_ENV, Env, configured_roots, judge

VECTORS = json.loads((Path(__file__).parent / 'fixtures/root_search_vectors.json').read_text(encoding='utf-8'))
ENV = VECTORS['environment']


def expand(value):
    if isinstance(value, str):
        return value.replace('@REPOS@', ENV['repos_root']).replace('@HOME@', ENV['home'])
    if isinstance(value, dict):
        return {expand(key): expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item) for item in value]
    return value


REPOS = set(expand(ENV['repos']))
FOLDERS = expand(ENV['folders'])
UNREADABLE = set(expand(ENV['unreadable']))


def fixture_listdir(path):
    if path in UNREADABLE:
        raise PermissionError(path)
    return FOLDERS.get(path, [])


def fixture_env(**kwargs):
    return Env(home=ENV['home'], extra_roots=expand(ENV['extra_roots']), is_repo=lambda path: path in REPOS,
               listdir=kwargs.get('listdir', fixture_listdir))


@pytest.mark.parametrize('vector', VECTORS['vectors'], ids=[v['id'] for v in VECTORS['vectors']])
def test_shared_vectors(vector):
    vector = expand(vector)
    refusal = judge(vector['tool'], vector['input'], vector['cwd'], fixture_env(), shell_cwd=vector.get('shell_cwd'),
                    glob_honours_gitignore=vector.get('glob_honours_gitignore', ENV['glob_honours_gitignore']))
    assert ('refuse' if refusal else 'allow') == vector['expect'], vector['why']
    if refusal and 'root' in vector:
        assert refusal.root == vector['root']
    if 'message' in vector:
        assert refusal.message == vector['message']


def test_message_names_the_piece_why_and_the_subfolders_to_use():
    command = expand('cd @REPOS@/opencode-companion && git status --short; grep -rn "ROLE UNKNOWN" --include=*.py . | head')
    refusal = judge('Bash', {'command': command}, None, fixture_env())
    assert refusal.piece == 'grep -rn "ROLE UNKNOWN" --include=*.py .'
    assert refusal.root_kind == 'repository' and refusal.command == 'grep -r'
    text = refusal.message
    assert '`grep -rn "ROLE UNKNOWN" --include=*.py .`' in text
    assert 'root of the repository opencode-companion' in text and 'ignores .gitignore' in text
    listed = text.split('opencode-companion holds: ', 1)[1].split(')', 1)[0].split(', ')
    assert listed == ['agents', 'chrome-extension', 'config', 'docs', 'packages', 'plugins', 'proxy', 'scripts',
                      'transcription-pane']  # tmp, node_modules and .git are never offered
    assert 'Grep tool' in text and 'Nothing in this command ran.' in text


def test_glob_and_grep_tool_messages():
    """Where the session's settings make Glob honour .gitignore (lever D, every fresh launch), Glob may search one
    repository like Grep and is refused only above repositories, and its message says neither that it walks ignored
    folders nor that it runs for an hour. Without them (an older stored profile) it is the old whole-tree walk."""
    env = fixture_env()
    companion = {'pattern': '**/quick-worker/AGENTS.md', 'path': expand('@REPOS@/opencode-companion')}
    assert judge('Glob', companion, None, env, glob_honours_gitignore=True) is None
    glob = judge('Glob', {'pattern': '*/AGENTS.md', 'path': expand('@REPOS@')}, None, env, glob_honours_gitignore=True)
    assert glob.root_kind == 'fixed' and glob.walk == 'ignore-aware'
    assert "Claude Code's 60 s Glob limit" in glob.message and 'ignores .gitignore' not in glob.message
    assert 'hour' not in glob.message and 'Nothing in this command ran' not in glob.message
    walk = judge('Glob', companion, None, env)  # the default: settings unknown, so Glob walks ignored folders
    assert walk.walk == 'whole-tree' and 'whatever the pattern' in walk.message and 'rg --files' in walk.message
    grep = judge('Grep', {'pattern': 'foo', 'path': expand('@REPOS@')}, None, env)
    assert grep.root_kind == 'fixed' and grep.walk == 'ignore-aware'
    assert 'searches every repository' in grep.message and 'one repository' in grep.message


def test_the_grep_tool_is_offered_only_at_a_repository():
    """CF-07: above repositories the Grep tool is refused too, so a refusal there never suggests it."""
    env = fixture_env()
    for command in ('grep -rn foo ~', 'grep -rn foo @REPOS@', 'find @HOME@/projects -name x'):
        refusal = judge('Bash', {'command': expand(command)}, None, env)
        assert refusal.root_kind != 'repository' and 'Grep tool' not in refusal.message, command
    at_repository = judge('Bash', {'command': expand('grep -rn foo @REPOS@/opencode-companion')}, None, env)
    assert 'or use the Grep tool, which honors .gitignore and may search this repository' in at_repository.message


@pytest.mark.parametrize('command', [
    'python3 -c "' + 'x = 1; ' * 40_000 + '"',  # 320 KB, double-quoted
    'echo ' + 'y' * 320_000,                     # unquoted
])
def test_a_long_argument_is_judged_in_linear_time(command):
    """CF-11 (M1): a 300 KB+ argument used to take seconds (quadratic word building) in the host's shared loop."""
    started = time.perf_counter()
    assert judge('Bash', {'command': command}, None, fixture_env(), shell_cwd=expand('@REPOS@/opencode-companion')) is None
    assert time.perf_counter() - started < 0.5


def test_the_setting_reads_the_environment_then_the_config(tmp_path):
    config = tmp_path / 'roots.json'
    config.write_text(json.dumps({'extra_roots': ['/srv/code', 7, '~/src']}))
    assert configured_roots({}, config) == ['/srv/code', '~/src']
    assert configured_roots({ROOTS_ENV: os.pathsep.join(['/a', '', '/b'])}, config) == ['/a', '/b']
    assert configured_roots({ROOTS_ENV: ''}, config) == []  # set, even empty, the variable wins
    assert configured_roots({}, tmp_path / 'missing.json') == []
    config.write_text('{not json')
    assert configured_roots({}, config) == []
    env = Env(home='/home/someone', extra_roots=['/srv/code', '~/src', 'relative', 'D:\\code'],
              is_repo=lambda path: False, listdir=lambda path: [])
    assert env.extra_roots == ['/srv/code', '/home/someone/src', '/mnt/d/code']
    assert {'/srv/code', '/srv', '/home/someone/src', '/mnt/d/code', '/mnt/d', '/mnt', '/'} <= env.fixed
    assert Env(home='/home/someone').extra_roots == []  # empty by default


def test_the_repository_config_is_well_formed():
    roots = configured_roots({}, ROOTS_CONFIG)
    assert roots and all(root.startswith('/') for root in roots)


def test_a_folder_of_repositories_costs_one_listing_and_three_lookups():
    listed, probed = [], []
    def listdir(path):
        listed.append(path)
        return ['e', 'd', 'c', 'b', 'a', '.hidden']
    def is_repo(path):
        probed.append(path)
        return False
    env = Env(home='/home/someone', is_repo=is_repo, listdir=listdir)
    assert judge('Bash', {'command': 'grep -rn foo /data/big'}, None, env) is None
    assert listed == ['/data/big']
    assert probed == ['/data/big', '/data/big/a', '/data/big/b', '/data/big/c']  # itself, then the first three


def test_record_carries_what_the_host_stores():
    refusal = judge('Bash', {'command': expand('grep -rn foo @REPOS@/quick-worker')}, None, fixture_env())
    assert set(refusal.record()) == {'tool', 'piece', 'command', 'walk', 'root', 'root_kind', 'message'}


def test_a_long_piece_is_shortened_in_the_message():
    command = expand('grep -rn ' + 'x' * 400 + ' @REPOS@/opencode-companion')
    refusal = judge('Bash', {'command': command}, None, fixture_env())
    assert refusal and '…`' in refusal.message and len(refusal.message) < 900


def test_listing_failure_still_refuses_without_folders():
    def broken(path):
        raise PermissionError(path)
    refusal = judge('Bash', {'command': expand('cd @REPOS@/opencode-companion && find .')}, None,
                    fixture_env(listdir=broken))
    assert refusal and 'holds:' not in refusal.message


@pytest.mark.parametrize('tool, tool_input', [
    ('Bash', None), ('Bash', {'command': 7}), ('Bash', {}), ('Grep', {'pattern': 'x', 'path': 5}),
    ('Glob', {'pattern': None}), (None, {'command': 'grep -r x /'}),
])
def test_malformed_input_allows(tool, tool_input):
    assert judge(tool, tool_input, None, fixture_env()) is None


def test_an_error_inside_the_check_allows():
    def explode(path):
        raise RuntimeError('probe failed')
    env = Env(home=ENV['home'], extra_roots=expand(ENV['extra_roots']), is_repo=explode)
    assert judge('Bash', {'command': 'cd /mnt/c/tmp && grep -rn foo .'}, None, env) is None
    assert judge('Grep', {'pattern': 'x', 'path': '/mnt/c/tmp'}, None, env) is None


def test_deep_nesting_is_doubt_and_allows():
    command = 'grep -rn foo /'
    for _ in range(10):
        command = 'bash -c ' + json.dumps(command)
    assert judge('Bash', {'command': command}, None, fixture_env()) is None


def test_real_filesystem_probe_finds_a_git_dir_and_a_worktree_file(tmp_path):
    (tmp_path / 'repo/.git').mkdir(parents=True)
    (tmp_path / 'worktree').mkdir()
    (tmp_path / 'worktree/.git').write_text('gitdir: elsewhere\n')
    (tmp_path / 'plain/one').mkdir(parents=True)
    (tmp_path / 'plain/.hidden/.git').mkdir(parents=True)  # hidden subfolders are never probed
    env = Env(home='/nonexistent-home/user')  # a parent of $HOME is a root: keep tmp_path clear of it
    for name in ('repo', 'worktree'):
        refusal = judge('Bash', {'command': f'cd {tmp_path / name} && grep -rn foo .'}, None, env)
        assert refusal and refusal.root_kind == 'repository'
    # tmp_path holds repositories among its first subfolders: a folder of repositories.
    refusal = judge('Bash', {'command': f'cd {tmp_path} && grep -rn foo .'}, None, env)
    assert refusal and refusal.root_kind == 'repositories' and 'Grep tool' not in refusal.message
    assert judge('Grep', {'pattern': 'x', 'path': str(tmp_path)}, None, env).root_kind == 'repositories'
    assert judge('Bash', {'command': f'cd {tmp_path / "plain"} && grep -rn foo .'}, None, env) is None
    assert judge('Bash', {'command': f'grep -rn foo {tmp_path / "missing"}'}, None, env) is None  # OSError: not a root
