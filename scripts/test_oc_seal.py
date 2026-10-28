"""Seal-ledger integration: only after capture, never a reason to undo a seal."""
import argparse
import asyncio
import json
import subprocess
import time
from types import SimpleNamespace

import pytest

import oc_seal


@pytest.fixture
def seal_env(tmp_path, monkeypatch):
    path = tmp_path / 'state' / 'seals.jsonl'
    config = tmp_path / 'settings.json'
    config.write_text(json.dumps({'seal_ledger_path': str(path), 'archive_path': str(tmp_path / 'history/archive.sqlite3'),
                                  'export_dir': str(tmp_path / 'exports'), 'pins_path': str(tmp_path / 'pins.json')}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    calls = []
    state = {'seal': None, 'clear': True, 'capture_ok': True, 'pointer_ok': True}
    launches = []

    def popen(argv, **kwargs):
        launches.append((argv, kwargs))
        if state.get('start_error'):
            raise OSError(state['start_error'])
        return SimpleNamespace(wait=lambda *a, **kw: (_ for _ in ()).throw(AssertionError('seal waited for archive')))

    async def root(program):
        return 'A program', 'seal: null\n', {'seal': state['seal']}, []

    async def capture(program, note):
        calls.append('capture')
        assert not path.exists()
        return state['capture_ok'], '[ap:SEALED]', 'capture detail'

    async def pointer(program, note, handle):
        calls.append('pointer')
        if not state['pointer_ok']:
            raise SystemExit('pointer failed')
        return 'pointer set'

    monkeypatch.setattr(oc_seal, 'load_config', lambda: {})
    monkeypatch.setattr(oc_seal, 'session_number', lambda args: '(Manager-Session 222)')
    monkeypatch.setattr(oc_seal, 'read_root', root)
    monkeypatch.setattr(oc_seal, 'quiescence', lambda *args: (state['clear'], [], []))
    monkeypatch.setattr(oc_seal, 'capture_seal', capture)
    monkeypatch.setattr(oc_seal, 'point_pointer', pointer)
    # Inject the module seam too, so the same behavioral test can run at the pre-trigger base.
    monkeypatch.setattr(oc_seal, 'subprocess', SimpleNamespace(Popen=popen, DEVNULL=subprocess.DEVNULL,
                                                            STDOUT=subprocess.STDOUT), raising=False)
    args = argparse.Namespace(program='[ap:5CKYAT]', session=None, account=None, authority='Dan: seal it',
                              outcome='completed', evidence=[], dry_run=False, board=None)
    return SimpleNamespace(path=path, calls=calls, state=state, args=args, launches=launches)


def test_seal_appends_after_capture_before_pointer(seal_env, monkeypatch):
    env = seal_env

    async def pointer(*args):
        env.calls.append('pointer')
        assert env.path.exists(), 'successful capture must append the seal ledger before the root pointer'
        assert json.loads(env.path.read_text())['seal'] == '[ap:SEALED]'
        return 'pointer set'

    monkeypatch.setattr(oc_seal, 'point_pointer', pointer)
    monkeypatch.setattr(time, 'time', lambda: 123.456)
    assert asyncio.run(oc_seal.run(env.args)) == 0
    assert env.calls == ['capture', 'pointer']
    assert [json.loads(line) for line in env.path.read_text().splitlines()] == [
        {'core': '5CKYAT', 'sealed_at': 123456, 'seal': '[ap:SEALED]', 'by': 'oc_seal', 'at': 123456}]


def test_dry_run_writes_no_ledger(seal_env):
    env = seal_env
    env.args.dry_run = True
    assert asyncio.run(oc_seal.run(env.args)) == 0
    assert not env.path.exists() and env.calls == []


def test_quiescence_refusal_writes_no_ledger(seal_env):
    env = seal_env
    env.state['clear'] = False
    assert asyncio.run(oc_seal.run(env.args)) == 1
    assert not env.path.exists() and env.calls == []


def test_already_sealed_refusal_writes_no_ledger(seal_env):
    env = seal_env
    env.state['seal'] = '[ap:OLDSEA]'
    with pytest.raises(SystemExit, match='already carries seal'):
        asyncio.run(oc_seal.run(env.args))
    assert not env.path.exists() and env.calls == []


def test_failed_capture_writes_no_ledger(seal_env):
    env = seal_env
    env.state['capture_ok'] = False
    assert asyncio.run(oc_seal.run(env.args)) == 1
    assert not env.path.exists() and env.calls == ['capture']


def test_append_failure_is_one_printed_line_and_seal_still_completes(seal_env, monkeypatch, capsys):
    env = seal_env
    attempts = []

    def failed_append(*args):
        attempts.append(args)
        raise OSError('disk full\nwrite unavailable')

    monkeypatch.setattr(oc_seal, 'oc_seal_ledger', SimpleNamespace(append=failed_append), raising=False)
    assert asyncio.run(oc_seal.run(env.args)) == 0
    output = capsys.readouterr().out
    assert len(attempts) == 1 and env.calls == ['capture', 'pointer']
    assert [line for line in output.splitlines() if 'seal ledger append failed' in line] == [
        '⚠️ seal ledger append failed: disk full write unavailable; the seal stands.']
    assert 'SEALED · [ap:5CKYAT]' in output and not env.path.exists()


def test_pointer_failure_keeps_the_already_appended_line(seal_env):
    env = seal_env
    env.state['pointer_ok'] = False
    assert asyncio.run(oc_seal.run(env.args)) == 1
    assert env.calls == ['capture', 'pointer']
    assert json.loads(env.path.read_text())['core'] == '5CKYAT'


def test_seal_starts_detached_low_priority_archive_after_ledger_before_pointer(seal_env, monkeypatch):
    env = seal_env

    async def pointer(*args):
        assert env.path.exists() and len(env.launches) == 1
        env.calls.append('pointer')
        return 'pointer set'

    monkeypatch.setattr(oc_seal, 'point_pointer', pointer)
    assert asyncio.run(oc_seal.run(env.args)) == 0
    argv, kwargs = env.launches[0]
    assert argv == ['nice', '-n', '19', 'bash', str(oc_seal.Path(oc_seal.__file__).with_name('oc.sh')),
                    'history', 'archive', '--program', '5CKYAT']
    assert kwargs['start_new_session'] is True and kwargs['close_fds'] is True
    assert kwargs['stdin'] == oc_seal.subprocess.DEVNULL and kwargs['stderr'] == oc_seal.subprocess.STDOUT
    assert env.calls == ['capture', 'pointer']


def test_archive_start_failure_is_one_line_and_seal_return_code_unchanged(seal_env, capsys):
    env = seal_env
    env.state['start_error'] = 'cannot spawn\nchild unavailable'
    assert asyncio.run(oc_seal.run(env.args)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if 'history archive not started' in line] == [
        '⚠️ history archive not started: cannot spawn child unavailable; the next sweep takes it']
    assert env.path.exists() and env.calls == ['capture', 'pointer'] and len(env.launches) == 1
