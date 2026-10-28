"""Activation proof on synthetic copies only; no Host import or construction."""
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

import claude_rows_compare as proof
from claude_headless.rows import SqliteRows, RowsSource, RowProjector, project_through
from claude_headless.store import Store, private_dir


SID = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'


def append(store, sid, uuid='answer'):
    raw = (json.dumps({'type': 'assistant', 'uuid': uuid, 'session_id': sid,
        'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'private fixture prose'}]}}) + '\n').encode()
    private_dir(store.path(sid, 1, 'stdout').parent)
    offset = store.append_bytes(sid, 1, 'stdout', raw)
    with store.batch():
        store.event(sid, 1, 'native_frame', {'type': 'assistant'}, 'stdout', offset, len(raw))
        backend = SqliteRows(store, sid)
        assert project_through(RowsSource(store), sid, RowProjector(backend))


@pytest.fixture
def copy_store(tmp_path):
    store = Store(tmp_path / 'copy')
    for sid in (SID, OTHER):
        store.put('sessions', sid, {'native_uuid': sid, 'execution_id': 'cc-' + sid,
            'generation': 1, 'owner': 'gone', 'state': 'exited', 'role': 'Manager',
            'cwd': str(tmp_path), 'native_input_open': False})
        append(store, sid)
    store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
    yield store
    store.db.close()


@pytest.fixture
def args(copy_store):
    with tempfile.TemporaryDirectory(prefix='rows-check-test-', dir='/tmp/opencode') as out:
        yield SimpleNamespace(store=str(copy_store.root), raw_root=str(copy_store.root),
                              out_dir=out, sessions='all')


def forbid_startup(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('activation read must not construct/migrate Store')
    for name in ('__init__', 'migrate', 'migrate_rows'):
        monkeypatch.setattr(Store, name, forbidden)


def test_read_only_single_connection_transaction_and_identical_database(args, copy_store, monkeypatch, capsys):
    path = copy_store.root / 'host.sqlite3'
    before = proof.file_sha256(path)
    sql, opens, closed = [], [], []
    connect = sqlite3.connect

    class TraceConnection(sqlite3.Connection):
        def close(self):
            closed.append((self.total_changes, self.in_transaction))
            super().close()

    def tracked(database, **kw):
        opens.append((database, kw.get('uri')))
        db = connect(database, **kw, factory=TraceConnection)
        db.set_trace_callback(sql.append)
        return db

    monkeypatch.setattr(proof.sqlite3, 'connect', tracked)
    forbid_startup(monkeypatch)
    assert proof.live_read_only(args) == 0
    assert len(opens) == 1 and opens[0][0].endswith('?mode=ro') and opens[0][1] is True
    assert sql[0] == 'BEGIN' and sql[-1] == 'ROLLBACK'
    assert sql.count('BEGIN') == sql.count('ROLLBACK') == 1
    assert all(s.startswith(('SELECT', 'BEGIN', 'ROLLBACK')) for s in sql)
    assert closed == [(0, False)]
    assert proof.file_sha256(path) == before
    report = json.loads((Path(args.out_dir) / 'snapshot-report.json').read_text())
    assert report['compared_sessions'] == 2 and report['excluded'] == []
    assert report['replay_sha256'] == report['rows_sha256']
    assert 'mismatches=0' in capsys.readouterr().out


def test_both_dumps_keep_one_snapshot_while_writer_commits_between_them(args, copy_store, monkeypatch):
    original = proof.write_dump
    calls = []

    def interleaved(reader, live, store, sids, source, out, unreadable=None):
        calls.append((id(store.db), store.db.in_transaction, source))
        result = original(reader, live, store, sids, source, out, unreadable)
        if source == 'replay':
            append(copy_store, SID, 'later')  # independent synthetic WAL writer; raw only appends
        return result

    monkeypatch.setattr(proof, 'write_dump', interleaved)
    assert proof.live_read_only(args) == 0
    assert calls[0][0] == calls[1][0] and all(c[1] for c in calls)
    assert [c[2] for c in calls] == ['replay', 'rows']
    for source in ('replay', 'rows'):
        records = [json.loads(line) for line in (Path(args.out_dir) / (source + '.jsonl')).read_text().splitlines()]
        assert len(next(r for r in records if r['sid'] == SID)['rows']) == 1
    assert copy_store.db.execute('SELECT COUNT(*) FROM rows WHERE sid=?', (SID,)).fetchone()[0] == 2


@pytest.mark.parametrize('condition,reason', [('queued','rows-queued'), ('running','rows-running'),
    ('absent','rows-absent'), ('behind','rows-behind'), ('missing','raw-unreadable:FileNotFoundError'),
    ('short','raw-unreadable:short-stdout')])
def test_snapshot_names_and_excludes_each_unavailable_session(args, copy_store, condition, reason, capsys):
    if condition == 'absent':
        copy_store.db.execute('DELETE FROM row_sets WHERE sid=?', (OTHER,))
    elif condition in ('queued', 'running', 'behind'):
        state = SqliteRows(copy_store, OTHER).state()
        if condition == 'behind':
            state['built_through'] = 0
        else:
            state['status'] = condition
        copy_store.db.execute('UPDATE row_sets SET body=? WHERE sid=?', (json.dumps(state), OTHER))
    elif condition == 'missing':
        copy_store.path(OTHER, 1, 'stdout').unlink()
    else:
        copy_store.path(OTHER, 1, 'stdout').write_bytes(b'')
    copy_store.db.commit()
    assert proof.live_read_only(args) == (3 if condition in ('absent', 'behind') else 0)
    report = json.loads((Path(args.out_dir) / 'snapshot-report.json').read_text())
    assert report['requested_sessions'] == 2 and report['compared_sessions'] == 1
    assert report['excluded'] == [{'sid': OTHER, 'reason': reason}]
    assert f'EXCLUDED {OTHER}: {reason}' in capsys.readouterr().out
    for source in ('replay', 'rows'):
        assert {json.loads(line)['sid'] for line in (Path(args.out_dir) / (source + '.jsonl')).read_text().splitlines()} == {SID}


def test_snapshot_mismatch_fails_without_publishing_private_values(args, copy_store, capsys):
    copy_store.db.execute("UPDATE rows SET body=json_set(body,'$.uuid','PRIVATE-DIFFERENT') WHERE sid=?", (SID,))
    copy_store.db.commit()
    assert proof.live_read_only(args) == 1
    output = capsys.readouterr().out
    assert 'MISMATCH' in output and 'mismatches=1' in output
    assert 'PRIVATE-DIFFERENT' not in output and 'private fixture prose' not in output


def test_unreadable_stored_structure_is_named_and_omitted_from_both_dumps(args, copy_store, capsys):
    copy_store.db.execute("UPDATE rows SET body=json_set(body,'$.stream_types',NULL) WHERE sid=?", (OTHER,))
    copy_store.db.commit()
    assert proof.live_read_only(args) == 3
    report = json.loads((Path(args.out_dir) / 'snapshot-report.json').read_text())
    assert report['compared_sessions'] == 1
    assert report['excluded'] == [{'sid': OTHER, 'reason': 'rows-unreadable:AttributeError'}]
    assert report['replay_sha256'] == report['rows_sha256']
    for source in ('replay', 'rows'):
        assert {json.loads(line)['sid'] for line in (Path(args.out_dir) / (source + '.jsonl')).read_text().splitlines()} == {SID}
    output = capsys.readouterr()
    assert f'EXCLUDED {OTHER}: rows-unreadable' in output.out
    assert 'private fixture prose' not in output.out + output.err


def test_u6_cf09_snapshot_exclusions_fail_only_unnamed_health_reasons(tmp_path, monkeypatch, capsys):
    cases = [('replay-render', 'replay-unreadable:OSError', 3),
             ('rows-render', 'rows-unreadable:AttributeError', 3),
             ('state', 'rows-state-unreadable', 3), ('behind', 'rows-behind', 3),
             ('absent', 'rows-absent', 3), ('mismatch-and-absent', 'rows-absent', 1),
             ('queued', 'rows-queued', 0), ('running', 'rows-running', 0),
             ('raw', 'raw-unreadable:FileNotFoundError', 0),
             ('record', 'session-record-unavailable', 0)]
    for fault, reason, expected in cases:
        store = Store(tmp_path / fault)
        try:
            for sid in (SID, OTHER):
                store.put('sessions', sid, {'native_uuid': sid, 'execution_id': 'cc-' + sid,
                    'generation': 1, 'owner': 'gone', 'state': 'exited', 'role': 'Manager',
                    'cwd': str(tmp_path), 'native_input_open': False})
                append(store, sid)
            if fault == 'rows-render':
                store.db.execute("UPDATE rows SET body=json_set(body,'$.stream_types',NULL) WHERE sid=?", (OTHER,))
            elif fault == 'state':
                store.db.execute("UPDATE row_sets SET body='{}' WHERE sid=?", (OTHER,))
            elif fault in ('absent', 'mismatch-and-absent'):
                store.db.execute('DELETE FROM row_sets WHERE sid=?', (OTHER,))
                if fault == 'mismatch-and-absent':
                    store.db.execute("UPDATE rows SET body=json_set(body,'$.uuid','private-mismatch') WHERE sid=?", (SID,))
            elif fault in ('behind', 'queued', 'running'):
                state = SqliteRows(store, OTHER).state()
                state.update({'built_through': 0} if fault == 'behind' else {'status': fault})
                store.db.execute('UPDATE row_sets SET body=? WHERE sid=?', (json.dumps(state), OTHER))
            elif fault == 'raw':
                store.path(OTHER, 1, 'stdout').unlink()
            elif fault == 'record':
                store.db.execute('DELETE FROM sessions WHERE sid=?', (OTHER,))
            store.db.commit()
            real_dump = proof.session_dump
            def render(facade, execution, generation):
                if fault == 'replay-render' and execution == 'cc-' + OTHER and type(facade).__name__ != 'StoredFacade':
                    raise OSError('private synthetic render failure')
                return real_dump(facade, execution, generation)
            with tempfile.TemporaryDirectory(prefix='u6-exclusions-', dir='/tmp/opencode') as out, \
                    monkeypatch.context() as inject:
                forbid_startup(inject)
                inject.setattr(proof, 'session_dump', render)
                result = proof.main(['--live-read-only', '--store', str(store.root),
                    '--raw-root', str(store.root), '--sessions', 'all', '--out-dir', out])
                report = json.loads((Path(out) / 'snapshot-report.json').read_text())
                output = capsys.readouterr()
                assert result == report['exit'] == expected, (fault, output.out, output.err)
                assert report['excluded'] == [{'sid': OTHER, 'reason': reason}]
                failed = [line for line in output.out.splitlines() if line.startswith('FAILED: exclusions health does not name: ')]
                assert len(failed) == (1 if expected else 0), (fault, failed)
                if failed:
                    assert OTHER in failed[0] and reason in failed[0]
                assert 'private synthetic render failure' not in output.out + output.err
                assert 'private-mismatch' not in output.out + output.err
        finally:
            store.db.close()


def test_cli_live_root_only_on_explicit_snapshot_path(args, monkeypatch, capsys):
    # Reclassify our synthetic copy as the live root; no real live path is opened.
    monkeypatch.setenv('XDG_STATE_HOME', str(Path(args.store).parent))
    fake_live = Path(args.store).parent / 'opencode-companion/claude-headless'
    fake_live.parent.mkdir()
    fake_live.symlink_to(Path(args.store), target_is_directory=True)
    assert proof.main(['dump', '--store', str(fake_live), '--raw-root', args.raw_root,
                       '--source', 'replay', '--out', str(Path(args.out_dir) / 'refused.jsonl')]) == 1
    assert 'never the live state root' in capsys.readouterr().err
    assert proof.main(['--live-read-only', '--store', str(fake_live), '--raw-root', args.raw_root,
                       '--sessions', 'all', '--out-dir', args.out_dir]) == 0
    with pytest.raises(SystemExit):
        proof.main(['--live-read-only', '--raw-root', args.raw_root, 'dump', '--store', args.store,
                    '--raw-root', args.raw_root, '--source', 'rows', '--out', str(Path(args.out_dir) / 'bad.jsonl')])


def test_snapshot_outputs_cannot_point_at_state_root(args):
    args.out_dir = args.store
    with pytest.raises(ValueError, match='private scratch'):
        proof.live_read_only(args)
