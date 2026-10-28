"""Pins retain every key, use the settings path and replace atomically/private."""
import json
import os
from pathlib import Path
import stat
from unittest.mock import patch

import pytest

import oc_pins
from oc_pins import SessionPins, star_new_program


SESSION = 'session:claude:cc-01234567-89ab-cdef-0123-456789abcdef'


def test_settings_path_and_set_unset(tmp_path, monkeypatch):
    path = tmp_path / 'private' / 'pins.json'
    config = tmp_path / 'settings.json'
    config.write_text(json.dumps({'pins_path': str(path)}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    pins = SessionPins(clock=lambda: 123.456)
    assert pins.path == path and pins.all() == {}
    assert pins.set(SESSION) is True
    assert pins.get(SESSION) == {'pinned_at': 123.456}
    assert pins.set(SESSION, False) is False
    assert pins.get(SESSION) is None and pins.all() == {}


@pytest.mark.parametrize('key', [SESSION, SESSION + '/agent-a9bc_123-xyz',
                               'session:opencode:ses_12345678', 'program:5CKYAT'])
def test_contract_key_forms(tmp_path, key):
    pins = SessionPins(tmp_path / 'pins.json')
    pins.set(key)
    assert key in pins.all()


@pytest.mark.parametrize('key', ['', None, '5CKYAT', 'program:bad', 'program:abcdef',
                               'session:claude:cc-NOT-A-UUID', 'session:opencode:ses_',
                               SESSION.upper(), SESSION + '/agent-'])
def test_bad_key_raises_before_writing(tmp_path, key):
    pins = SessionPins(tmp_path / 'pins.json')
    with pytest.raises(ValueError):
        pins.set(key)
    assert not pins.path.exists()


def test_atomic_owner_only_replace(tmp_path):
    path = tmp_path / 'private' / 'pins.json'
    path.parent.mkdir(mode=0o755)
    pins = SessionPins(path)
    replace = os.replace
    observations = []

    def observed_replace(source, target):
        observations.append(json.loads(Path(source).read_text()))
        assert Path(source).parent == path.parent
        assert stat.S_IMODE(Path(source).stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        replace(source, target)

    with patch('oc_pins.os.replace', side_effect=observed_replace), \
            patch('oc_pins.os.fsync', wraps=os.fsync) as fsync:
        pins.set('program:5CKYAT')
    assert fsync.call_count == 1 and observations[0]['schemaVersion'] == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.iterdir()) == [path]


def test_missing_malformed_and_unreadable_are_empty(tmp_path):
    path = tmp_path / 'pins.json'
    pins = SessionPins(path)
    assert pins.all() == {}
    path.write_text('not json')
    assert pins.all() == {}
    path.write_text(json.dumps({'pins': {SESSION: {'pinned_at': 10}}}))
    with patch.object(Path, 'read_text', side_effect=PermissionError('unreadable')):
        assert pins.all() == {}


def test_cache_by_mtime_size_and_return_copies(tmp_path):
    path = tmp_path / 'pins.json'
    pins = SessionPins(path, clock=lambda: 1)
    pins.set(SESSION)
    data = pins.all()
    data[SESSION]['pinned_at'] = 99
    with patch.object(Path, 'read_text', side_effect=AssertionError('cache reread')):
        assert pins.get(SESSION) == {'pinned_at': 1}
    stamp = path.stat()
    path.write_text(path.read_text().replace('"pinned_at": 1', '"pinned_at": 2'))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000))
    assert pins.get(SESSION) == {'pinned_at': 2}
    stamp = path.stat()
    path.write_text(path.read_text().replace('"pinned_at": 2', '"pinned_at": 300'))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert pins.get(SESSION) == {'pinned_at': 300}


def test_unknown_schema_and_no_cap_or_eviction(tmp_path):
    path = tmp_path / 'pins.json'
    existing = {f'program:{n:06d}': {'pinned_at': n} for n in range(5000)}
    path.write_text(json.dumps({'schemaVersion': 99, 'pins': existing}))
    pins = SessionPins(path, clock=lambda: 1)
    assert pins.all() == existing
    pins.set(SESSION)
    assert len(pins.all()) == 5001
    assert pins.get('program:000000') == {'pinned_at': 0}


def test_birth_star_uses_one_store_and_retains_existing_timestamp(tmp_path, monkeypatch, capsys):
    pins = SessionPins(tmp_path / 'pins.json', clock=lambda: 17)
    monkeypatch.setattr(oc_pins, 'SessionPins', lambda: pins)
    pins.set(SESSION)
    star_new_program('[ap:5CKYAT]')
    assert capsys.readouterr().out == '★ starred [ap:5CKYAT] (Favorite programs)\n'
    before = pins.path.read_bytes()
    pins.clock = lambda: 99
    star_new_program('[ap:5CKYAT]')
    assert capsys.readouterr().out == '★ already starred [ap:5CKYAT] (Favorite programs)\n'
    assert pins.path.read_bytes() == before
    assert pins.all() == {SESSION: {'pinned_at': 17}, 'program:5CKYAT': {'pinned_at': 17}}


def test_birth_dry_run_never_opens_store(monkeypatch, capsys):
    def forbidden():
        raise AssertionError('dry run opened the store')
    monkeypatch.setattr(oc_pins, 'SessionPins', forbidden)
    star_new_program('[ap:‹ROOT›]', dry_run=True)
    assert capsys.readouterr().out == '★ would star [ap:‹ROOT›] (Favorite programs)\n'


@pytest.mark.parametrize('failure', ['construct', 'write'])
def test_birth_store_failure_is_one_warning_and_never_raises(tmp_path, monkeypatch, capsys, failure):
    def broken(*args, **kwargs):
        raise PermissionError('not writable\nmore detail')
    if failure == 'construct':
        monkeypatch.setattr(oc_pins, 'SessionPins', broken)
    else:
        pins = SessionPins(tmp_path / 'pins.json')
        monkeypatch.setattr(oc_pins, 'SessionPins', lambda: pins)
        monkeypatch.setattr(pins, 'set', broken)
    star_new_program('[ap:5CKYAT]')
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 1 and out.count('⚠️') == 1
    assert 'program [ap:5CKYAT] was not starred' in out and 'PermissionError: not writable more detail' in out


@pytest.mark.parametrize('program', ['[ap:bad]', 'session:opencode:ses_abc', 'program:5CKYAT', 'no AP-ID'])
def test_birth_helper_never_creates_other_pin_kinds(tmp_path, monkeypatch, capsys, program):
    pins = SessionPins(tmp_path / 'pins.json')
    monkeypatch.setattr(oc_pins, 'SessionPins', lambda: pins)
    star_new_program(program)
    assert 'was not starred' in capsys.readouterr().out
    assert not pins.path.exists()
