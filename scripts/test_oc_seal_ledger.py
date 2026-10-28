"""The seal ledger's durable single-write appends and file-ordered read cache."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import oc_seal_ledger as ledger


@pytest.fixture
def ledger_path(tmp_path, monkeypatch):
    path = tmp_path / 'state' / 'program-seals.jsonl'
    config = tmp_path / 'settings.json'
    config.write_text(json.dumps({'seal_ledger_path': str(path)}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(config))
    return path


def test_append_read_and_units(ledger_path):
    with patch.object(ledger.time, 'time', return_value=123.456), \
            patch.object(ledger.os, 'write', wraps=os.write) as write, \
            patch.object(ledger.os, 'fsync', wraps=os.fsync) as fsync:
        ledger.append('5CKYAT', 100001, '[ap:ABC123]', 'oc_seal')
    assert write.call_count == fsync.call_count == 1
    assert ledger_path.read_bytes().endswith(b'\n')
    assert ledger.read() == {'5CKYAT': {'core': '5CKYAT', 'sealed_at': 100001,
                                      'seal': '[ap:ABC123]', 'by': 'oc_seal', 'at': 123456}}


def test_latest_line_wins_in_file_order(ledger_path):
    ledger.append('5CKYAT', 99999, '[ap:ABC123]', 'oc_seal')
    ledger.append('OTHER1', 30000, None, 'backfill')
    ledger.append('5CKYAT', 10000, None, 'backfill')
    assert ledger.read()['5CKYAT']['sealed_at'] == 10000
    assert set(ledger.read()) == {'5CKYAT', 'OTHER1'}


def test_malformed_lines_skipped(ledger_path):
    ledger.append('5CKYAT', 1, None, 'backfill')
    with ledger_path.open('a') as target:
        target.write('{broken\nnull\n[]\n{"core":"5CKYAT"}\n')
    assert ledger.read()['5CKYAT']['sealed_at'] == 1


def test_missing_or_unreadable_is_empty(ledger_path):
    assert ledger.read() == {}
    ledger.append('5CKYAT', 1, None, 'backfill')
    original_open = Path.open

    def unreadable(path, *args, **kwargs):
        if path == ledger_path:
            raise PermissionError('unreadable')
        return original_open(path, *args, **kwargs)

    with patch.object(Path, 'open', autospec=True, side_effect=unreadable):
        assert ledger.read() == {}


def test_cache_uses_mtime_and_size_and_returns_copies(ledger_path):
    ledger.append('5CKYAT', 1, None, 'backfill')
    result = ledger.read()
    result['5CKYAT']['sealed_at'] = 50
    original_open = Path.open

    def cached(path, *args, **kwargs):
        if path == ledger_path:
            raise AssertionError('cache reread')
        return original_open(path, *args, **kwargs)

    with patch.object(Path, 'open', autospec=True, side_effect=cached):
        assert ledger.read()['5CKYAT']['sealed_at'] == 1
    stamp = ledger_path.stat()
    body = ledger_path.read_text().replace('"sealed_at":1', '"sealed_at":2')
    ledger_path.write_text(body)
    os.utime(ledger_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000))
    assert ledger.read()['5CKYAT']['sealed_at'] == 2
    stamp = ledger_path.stat()
    with ledger_path.open('a') as target:
        target.write('\n')
    os.utime(ledger_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with patch.object(Path, 'open', autospec=True, side_effect=original_open) as opened:
        assert ledger.read()['5CKYAT']['sealed_at'] == 2
    assert sum(call.args[0] == ledger_path for call in opened.call_args_list) == 1


def test_path_is_part_of_cache_identity(ledger_path, tmp_path, monkeypatch):
    ledger.append('5CKYAT', 1, None, 'backfill')
    assert ledger.read()
    other_config = tmp_path / 'other-settings.json'
    other_config.write_text(json.dumps({'seal_ledger_path': str(tmp_path / 'absent.jsonl')}))
    monkeypatch.setenv('OC_SESSION_HISTORY_CONFIG', str(other_config))
    assert ledger.read() == {}


def test_append_failure_has_no_retry(ledger_path):
    with patch.object(ledger.os, 'write', side_effect=OSError('disk full')) as write:
        with pytest.raises(OSError, match='disk full'):
            ledger.append('5CKYAT', 1, None, 'backfill')
    assert write.call_count == 1
