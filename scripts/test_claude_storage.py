"""Host unit 2 (audit [ap:GEHGQK] Group B): one store transaction per native read with the raw bytes fsynced first,
result copies in their own rows, ended sessions released from memory, and every live process checked each collection
pass. Real Store/NativeSession code; a fake process only where the test must choose the read boundaries."""
import asyncio
import json
import os
import sqlite3
import uuid

import pytest

import oc_claude
from claude_headless.host import Host, NativeSession
from claude_headless.store import Store, private_dir
from claude_headless.rows import SqliteRows, RowProjector
from test_claude_headless_host import auth, builder, until
from test_claude_headless_supervision import cleanup, setup


class _Stdin:
    def write(self, data):
        self.data = getattr(self, 'data', b'') + data

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class _Process:
    """A process whose stdout delivers exactly the reads a test feeds it."""
    def __init__(self):
        self.stdout, self.stderr = asyncio.StreamReader(limit=2 ** 30), asyncio.StreamReader()
        self.stdin, self.returncode, self.pid = _Stdin(), None, os.getpid()


def _line(event):
    return (json.dumps(event) + '\n').encode()


async def _fed_session(tmp_path):
    host = Host(tmp_path / 'state', tmp_path / 'host.sock', builder, auth)
    sid = str(uuid.uuid4())
    record = {'execution_id': 'cc-' + sid, 'native_uuid': sid, 'generation': 1, 'role': 'worker', 'state': 'ready',
              'owner': 'host', 'pending_message_uuids': [], 'pending_control_requests': [],
              'pending_native_requests': {}, 'pending_escalations': [], 'launch': {'argv': ['claude'] * 50}}
    private_dir(host.store.root / sid / 'g000001')
    host.store.put('sessions', sid, record)
    process = _Process()
    session = NativeSession(host, record, process, -1)
    host.sessions[sid] = session
    task = asyncio.create_task(session.stdout())
    await asyncio.sleep(0)
    return host, session, process, task


async def _read(process, data):
    """Deliver one stdout read and let the drain take it whole."""
    process.stdout.feed_data(data)
    while process.stdout._buffer:  # noqa: SLF001 - the fixture waits until the reader took this read
        await asyncio.sleep(0)
    await asyncio.sleep(0)


def _stream_frames(n):
    return [_line({'type': 'stream_event', 'uuid': str(uuid.uuid4()), 'parent_tool_use_id': None,
                   'event': {'type': 'content_block_delta', 'index': 0,
                             'delta': {'type': 'text_delta', 'text': f'word {i} '}}}) for i in range(n)]


def test_one_read_is_one_transaction_after_its_raw_bytes_are_fsynced(tmp_path, monkeypatch):
    async def scenario():
        host, session, process, task = await _fed_session(tmp_path)
        trace, order = [], []
        host.store.db.set_trace_callback(trace.append)
        real_append = host.store.append_bytes

        def append(*args):
            offset = real_append(*args)
            order.append(('raw fsynced', len(trace)))
            return offset
        monkeypatch.setattr(host.store, 'append_bytes', append)
        try:
            frames = _stream_frames(12)
            await _read(process, b''.join(frames))
            commits = [i for i, sql in enumerate(trace) if sql == 'COMMIT']
            record_writes = [sql for sql in trace if sql.startswith('INSERT OR REPLACE INTO sessions')]
            assert len(commits) == 1 and len(record_writes) == 1  # twelve frames, one transaction, one record write
            assert order == [('raw fsynced', order[0][1])] and order[0][1] < commits[0]  # bytes before the index
            rows = host.store.db.execute("SELECT offset, length FROM events WHERE kind='native_frame' ORDER BY seq")
            assert [tuple(r) for r in rows] == [(sum(map(len, frames[:i])), len(f)) for i, f in enumerate(frames)]
            assert host.store.get('sessions', session.sid)['last_native_cursor'] == session.record['last_native_cursor']
            # A frame split across two reads is indexed by the read that completes it, once.
            trace.clear()
            tail = _stream_frames(1)[0]
            await _read(process, tail[:10])
            await _read(process, tail[10:])
            assert trace.count('COMMIT') == 2
            assert host.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='native_frame'").fetchone()[0] == 13
        finally:
            process.stdout.feed_eof()
            await task
            host.store.db.close()
    asyncio.run(scenario())


def test_a_failure_inside_a_read_rolls_it_back_whole_and_no_cursor_is_ever_reused(tmp_path, monkeypatch):
    async def scenario():
        host, session, process, task = await _fed_session(tmp_path)
        try:
            await _read(process, b''.join(_stream_frames(2)))
            committed = host.store.db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
            real_event, handed = host.store.event, []

            def event(sid, generation, kind, *args, **kwargs):
                if kind == 'native_frame' and len(handed) == 2:
                    raise sqlite3.OperationalError('disk I/O error (fixture)')
                cursor = real_event(sid, generation, kind, *args, **kwargs)
                if kind == 'native_frame':
                    handed.append(cursor)
                return cursor
            monkeypatch.setattr(host.store, 'event', event)
            frames = _stream_frames(4)
            await _read(process, b''.join(frames))
            monkeypatch.setattr(host.store, 'event', real_event)
            # Nothing of that read is indexed (only the failure's own incident is journaled), and the session
            # declares the failure.
            after = host.store.db.execute('SELECT seq, kind FROM events WHERE seq>?', (committed,)).fetchall()
            assert [row['kind'] for row in after] == ['incident']
            assert session.infrastructure_error == 'OperationalError' and session.stdout_broken
            stdout = host.store.path(session.sid, 1, 'stdout').read_bytes()
            assert stdout.endswith(b''.join(frames))  # the raw bytes were retained (fsynced first)
            # Cursors handed out inside the rolled-back read never name another event later.
            assert len(handed) == 2 and all(int(c.split(':')[1]) > committed for c in handed)
            assert after[0]['seq'] > max(int(c.split(':')[1]) for c in handed)
            gap = session.clear_infrastructure_error('storage works again (fixture)')
            later = host.store.event(session.sid, 1, 'fixture_after_gap')
            assert int(later.split(':')[1]) > max(int(c.split(':')[1]) for c in handed)
            assert gap['raw_gaps'] == [{'stream': 'stdout', 'offset': len(stdout) - len(b''.join(frames)),
                                        'length': len(b''.join(frames))}]
            await _read(process, _stream_frames(1)[0])  # parses on at the next frame boundary
            assert host.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='native_frame'").fetchone()[0] == 3
        finally:
            process.stdout.feed_eof()
            await task
            host.store.db.close()
    asyncio.run(scenario())


def test_after_a_rolled_back_read_memory_matches_the_store_again(tmp_path, monkeypatch):
    """Nothing a rolled-back read set outlives it: no escalation key without its incident, no input marked answered
    whose receipt says otherwise (it stays pending for release_input, as the gap contract says)."""
    async def scenario():
        host, session, process, task = await _fed_session(tmp_path)
        try:
            mid = str(uuid.uuid4())
            host.store.put('receipts', session.sid, {'message_uuid': mid, 'submitted': 'x', 'original': 'x',
                                                    'status': 'acknowledged', 'completed': False, 'results': []}, mid)
            session.pending.add(mid)
            session.update(pending_message_uuids=[mid])
            real_event = host.store.event

            def event(sid, generation, kind, *args, **kwargs):
                if kind == 'unknown_system_subtype':
                    raise sqlite3.OperationalError('disk I/O error (fixture)')
                return real_event(sid, generation, kind, *args, **kwargs)
            monkeypatch.setattr(host.store, 'event', event)
            await _read(process, b''.join([
                _line({'type': 'result', 'uuid': 'r', 'subtype': 'success', 'is_error': False,
                       'user_message_uuid': mid, 'result': 'answered'}),
                _line({'type': 'system', 'subtype': 'init', 'apiKeySource': 'ANTHROPIC_API_KEY'}),  # an alert
                _line({'type': 'system', 'subtype': 'fixture_unknown'})]))                            # fails here
            monkeypatch.setattr(host.store, 'event', real_event)
            assert session.infrastructure_error == 'OperationalError'
            assert mid in session.pending and session.record['pending_message_uuids'] == [mid]
            assert not session.record.get('financial_route_attention') and not session.record.get('last_result_cursor')
            for key in session.record['pending_escalations']:  # only the failure's own, committed incident
                assert host.store.get('incidents', session.sid, key)['category'] == 'infrastructure_error'
            assert not host.store.get('receipts', session.sid, mid)['completed']
        finally:
            process.stdout.feed_eof()
            await task
            host.store.db.close()
    asyncio.run(scenario())


def test_batch_reads_see_their_own_deferred_record_and_nest(tmp_path):
    store = Store(tmp_path / 'state')
    record = {'native_uuid': 's', 'n': 1}
    store.put('sessions', 's', record)
    with store.batch():
        record['n'] = 2
        store.put('sessions', 's', record)
        with store.batch():
            record['n'] = 3
            store.put('sessions', 's', record)
            assert store.get('sessions', 's')['n'] == 3 and store.sessions()[0]['n'] == 3
        # Written once, at the outermost commit: the row itself still holds the value before the batch.
        assert json.loads(store.db.execute("SELECT record FROM sessions WHERE sid='s'").fetchone()[0])['n'] == 1
    assert json.loads(store.db.execute("SELECT record FROM sessions WHERE sid='s'").fetchone()[0])['n'] == 3
    with pytest.raises(RuntimeError):
        with store.batch():
            store.put('sessions', 's', {'native_uuid': 's', 'n': 4})
            store.event('s', 1, 'fixture')
            raise RuntimeError('fixture')
    assert store.get('sessions', 's')['n'] == 3 and not store.db.execute("SELECT 1 FROM events").fetchone()
    store.db.close()


def test_result_copies_are_rows_of_their_own_and_duplicates_are_still_caught(tmp_path):
    async def scenario():
        host, session, process, task = await _fed_session(tmp_path)
        try:
            wake = _line({'type': 'result', 'uuid': 'wake-1', 'subtype': 'success', 'is_error': False,
                          'origin': {'kind': 'task-notification'}, 'result': 'the worker is done.'})
            await _read(process, wake)
            await _read(process, wake)  # the same native result again
            kinds = [r[0] for r in host.store.db.execute('SELECT kind FROM events ORDER BY seq')]
            assert kinds.count('result_duplicate') == 1
            assert 'synthetic_results' not in session.record and 'result_identities' not in session.record
            assert 'synthetic_results' not in host.store.get('sessions', session.sid)
            state = await host.dispatch({'method': 'state', 'params': {'execution_id': session.record['execution_id']}})
            assert state['last_synthetic_result']['result'] == 'the worker is done.'
            assert state['last_synthetic_result']['category'] == 'turn_receipt'  # classified before it was written
            assert oc_claude.wake_result(state)['result'] == 'the worker is done.'
            listed = await host.dispatch({'method': 'list', 'params': {}})
            assert 'last_synthetic_result' not in listed[0]  # `state` answers it; list rows stay lean
        finally:
            process.stdout.feed_eof()
            await task
            host.store.db.close()
    asyncio.run(scenario())


def test_old_records_are_migrated_once_on_open(tmp_path, capfd):
    root = tmp_path / 'state'
    store = Store(root)
    sid = str(uuid.uuid4())
    store.event(sid, 1, 'fixture')
    legacy = {'native_uuid': sid, 'execution_id': 'cc-' + sid, 'generation': 1, 'last_result_cursor': f'{store.identity}:9',
              'result_identities': ['a' * 64, 'b' * 64],
              'synthetic_results': [{'cursor': f'{store.identity}:5', 'result': 'older'},
                                    {'cursor': f'{store.identity}:9', 'result': 'newest wake-up'}]}
    store.put('sessions', sid, legacy)
    store.put('sessions', 'unreadable', {'native_uuid': 'unreadable'})
    store.db.execute("UPDATE sessions SET record='{not json' || '\"result_identities\"' WHERE sid='unreadable'")
    store.db.execute('DROP INDEX events_by_session')
    store.db.commit()
    store.db.close()
    capfd.readouterr()
    reopened = Store(root)
    log = capfd.readouterr().err
    assert 'one-time migration' in log and '"result_copies_moved_from_records": 1' in log
    record = reopened.get('sessions', sid)
    assert 'result_identities' not in record and 'synthetic_results' not in record
    assert reopened.result_seen(sid, 'a' * 64) and reopened.result_seen(sid, 'b' * 64)
    assert reopened.last_background_result(sid)['result'] == 'newest wake-up'
    assert oc_claude.wake_result({**record, 'last_synthetic_result': reopened.last_background_result(sid)})
    assert json.loads(reopened.db.execute("SELECT value FROM meta WHERE key='migration_unit2'").fetchone()[0])
    plan = ' '.join(str(tuple(r)) for r in reopened.db.execute(
        'EXPLAIN QUERY PLAN SELECT COALESCE(MAX(offset+length),0) FROM events WHERE sid=? AND generation=? '
        'AND stream=?', (sid, 1, 'stdout')))
    assert 'events_by_session' in plan  # startup gap checks no longer walk every session's events
    reopened.db.close()
    capfd.readouterr()
    Store(root).db.close()
    assert 'one-time migration' not in capfd.readouterr().err  # a second open finds nothing to do


def test_an_ended_session_is_released_and_everything_reads_its_stored_record(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role='worker')
        try:
            eid, sid = session.record['execution_id'], session.sid
            mid = str(uuid.uuid4())
            await session.send(mid, 'hello', 'hello')
            await until(lambda: host.store.get('receipts', sid, mid)['completed'])
            await host.dispatch({'method': 'close_input', 'params': {'execution_id': eid, 'generation': 1}})
            await until(lambda: session.record.get('owner') == 'gone')
            assert sid not in host.sessions
            state = await host.dispatch({'method': 'state', 'params': {'execution_id': eid}})
            assert state['owner'] == 'gone' and state['state'] == 'exited' and state['generation'] == 1
            assert eid in [r['execution_id'] for r in await host.dispatch({'method': 'list', 'params': {}})]
            page = await host.dispatch({'method': 'replay', 'params': {'execution_id': eid, 'limit': 500}})
            frame = next(e for e in page['events'] if e['kind'] == 'native_frame')
            assert (await host.dispatch({'method': 'raw', 'params': {'cursor': frame['cursor']}}))['total'] > 0
            assert (await host.dispatch({'method': 'receipt', 'params': {'execution_id': eid,
                                                                         'message_uuid': mid}}))['completed']
            released = await host.dispatch({'method': 'release_input', 'params': {'execution_id': eid,
                                                                                  'disposition': 'fixture'}})
            assert released['released'] == [] and released['completed'] == []  # nothing unfinished to release
            # An interrupt receipt of an ended session is settled from its stored records.
            rid = str(uuid.uuid4())
            host.store.put('controls', sid, {'request_id': rid, 'operation_class': 'interrupt',
                                             'pending_message_uuids': [mid], 'settlement': {'settled': False}}, rid)
            receipt = await host.dispatch({'method': 'control_receipt', 'params': {'execution_id': eid,
                                                                                   'request_id': rid}})
            assert receipt['settlement']['settled'] is True
            # A follow-up on the stopped seat resumes the same conversation from the stored record.
            row = await host.dispatch({'method': 'resume', 'params': {'execution_id': eid}})
            assert row['generation'] == 2 and host.sessions[sid].generation == 2
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_every_live_process_is_checked_each_collection_pass(tmp_path, monkeypatch):
    async def scenario():
        now = [0.0]
        host = Host(tmp_path / 'state', tmp_path / 'host.sock', builder, auth, monotonic=lambda: now[0],
                    collection_cadence=180)
        checked, gate = [], asyncio.Event()

        async def collect(host_, session):
            checked.append(session.sid)
            if session.sid == 'slow':
                await gate.wait()  # a readback still being observed
            return False
        import claude_headless.collection as collection
        monkeypatch.setattr(collection, 'collect_session', collect)

        class Live:
            def __init__(self, sid, returncode=None):
                self.sid, self.process = sid, type('P', (), {'returncode': returncode})()
        for sid in ('a', 'b', 'c', 'slow'):
            host.sessions[sid] = Live(sid)
        host.sessions['ended'] = Live('ended', returncode=0)
        try:
            await host.lifecycle_tick()
            assert checked == []  # not before the first cadence
            now[0] += 180
            await host.lifecycle_tick()
            await asyncio.sleep(0)
            assert sorted(checked) == ['a', 'b', 'c', 'slow']  # every live process in one pass
            now[0] += 180
            await host.lifecycle_tick()
            await asyncio.sleep(0)
            assert sorted(checked) == ['a', 'a', 'b', 'b', 'c', 'c', 'slow']  # one still being checked is skipped
            gate.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert 'slow' not in host.collection_tasks
        finally:
            host.store.db.close()
    asyncio.run(scenario())


def _assistant_frames():
    return [_line({'type': 'stream_event', 'event': event}) for event in [
        {'type': 'message_start', 'message': {'id': 'message'}},
        {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
        {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'hello'}}]]


def _row_tables(store):
    return {table: [tuple(r) for r in store.db.execute(f'SELECT * FROM {table} ORDER BY 1,2')]
            for table in ('rows', 'row_parts', 'row_keys', 'row_diagnostics', 'row_incidents', 'row_sets')}


def test_rows_and_events_share_the_one_read_commit_after_fsync(tmp_path, monkeypatch):
    async def scenario():
        host, session, process, task = await _fed_session(tmp_path)
        trace, order = [], []
        host.store.db.set_trace_callback(trace.append)
        append = host.store.append_bytes
        def fsynced(*args):
            result = append(*args)
            order.append(len(trace))
            return result
        monkeypatch.setattr(host.store, 'append_bytes', fsynced)
        try:
            await _read(process, b''.join(_assistant_frames()))
            assert trace.count('COMMIT') == 1
            writes = [n for n, sql in enumerate(trace) if sql.startswith(('INSERT INTO events',
                'INSERT OR REPLACE INTO rows ', 'INSERT OR IGNORE INTO row_parts', 'INSERT OR REPLACE INTO row_sets'))]
            assert len(writes) >= 7 and min(writes) >= order[0] and max(writes) < trace.index('COMMIT')
            row = SqliteRows.decode(host.store.db.execute('SELECT body FROM rows').fetchone()[0], host.store, session.sid)
            assert row['seq'] == 0 and row['prose_bytes'] == 5 and len(row['text_parts']) == 1
            assert session.rows.built_through == session.record['last_native_cursor']
            assert not session.infrastructure_error
        finally:
            process.stdout.feed_eof()
            await task
            host.store.db.close()
    asyncio.run(scenario())


def test_row_projection_exception_is_a_diagnostic_not_a_rolled_back_read(tmp_path, monkeypatch):
    async def scenario():
        host, session, process, task = await _fed_session(tmp_path)
        real = RowProjector.project
        def broken(self, event, frame):
            if event['kind'] == 'native_frame':
                raise ValueError('fixture projection')
            return real(self, event, frame)
        monkeypatch.setattr(RowProjector, 'project', broken)
        try:
            await _read(process, _assistant_frames()[0])
            assert not session.infrastructure_error
            assert host.store.db.execute("SELECT COUNT(*) FROM events WHERE kind='native_frame'").fetchone()[0] == 1
            item = json.loads(host.store.db.execute('SELECT body FROM row_diagnostics').fetchone()[0])
            assert item['status'] == 'unsupported:fixture projection'
            assert session.rows.built_through == session.record['last_native_cursor']
        finally:
            process.stdout.feed_eof()
            await task
            host.store.db.close()
    asyncio.run(scenario())


def test_rollback_restores_all_row_tables_and_dense_positions(tmp_path):
    async def scenario():
        host, session, process, task = await _fed_session(tmp_path)
        try:
            await _read(process, b''.join(_assistant_frames()))
            before = _row_tables(host.store)
            frame = _line({'type': 'assistant', 'uuid': 'next',
                           'message': {'role': 'assistant', 'content': 'next'}})
            offset = host.store.append_bytes(session.sid, 1, 'stdout', frame)
            with pytest.raises(RuntimeError, match='fixture'):
                with host.store.batch():
                    session.frame(frame, offset, len(frame))
                    host.supervisor.incident(session.record, 'fixture')
                    assert host.store.db.execute('SELECT COUNT(*) FROM rows').fetchone()[0] == 2
                    raise RuntimeError('fixture')
            session.restore_committed()
            assert _row_tables(host.store) == before
            assert session.rows.cache_tag == SqliteRows.tag(session.rows.state())
            with host.store.batch():
                session.frame(frame, offset, len(frame))
            assert [r[0] for r in host.store.db.execute('SELECT seq FROM rows ORDER BY seq')] == [0, 1]
        finally:
            process.stdout.feed_eof()
            await task
            host.store.db.close()
    asyncio.run(scenario())


def test_agent_window_cross_scope_paging_exclusive_edges_tail_and_single_scope(tmp_path):
    from test_claude_rows_backfill import host_at, append, assistant, close
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    eid = 'cc-' + sid
    try:
        for n in range(605):
            append(host, sid, assistant(n, scope='A' if n % 2 else 'B'))
        append(host, sid, assistant('main'))
        expected = list(range(1, 604))
        values, low = [], 0
        while True:
            page = host.rows(eid, agent_window=[low, 604], limit=500)
            assert page['next_from_seq'] is None
            values.extend(page['rows'])
            assert all({'scope', 'seq', 'ordinal'} <= r.keys() for r in page['rows'])
            if not page['has_more']:
                break
            low = page['next_after_ordinal']
        assert [r['ordinal'] for r in values] == expected
        assert len({(r['scope'], r['seq']) for r in values}) == len(expected)
        tail = host.rows(eid, agent_window=[603, None])
        assert [r['ordinal'] for r in tail['rows']] == [604]
        single = host.rows(eid, scope='agent:A', agent_window=[-1, None], limit=500)
        assert all(r['scope'] == 'agent:A' and r['ordinal'] % 2 for r in single['rows'])
        with pytest.raises(ValueError, match='never main'):
            host.rows(eid, scope='main', agent_window=[-1, None])
        plan = ' '.join(str(tuple(r)) for r in host.store.db.execute(
            "EXPLAIN QUERY PLAN SELECT body FROM rows WHERE sid=? AND scope LIKE 'agent:%' AND ordinal>? AND ordinal<? ORDER BY ordinal LIMIT 501",
            (sid, -1, 604)))
        assert 'rows_by_ordinal' in plan
    finally:
        close(host)


def test_read_shapes_changes_tie_group_incident_pages_and_stored_session(tmp_path):
    from test_claude_rows_backfill import host_at, append, assistant, close, view
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    eid = 'cc-' + sid
    try:
        for n in range(3):
            append(host, sid, assistant(n, call='shared'))
        append(host, sid, {'type': 'user', 'uuid': 'result-carrier', 'message': {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'shared', 'content': 'result'}]}})
        changed = host.store.db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
        rows, point = [], [changed, -1]
        while True:
            page = host.rows_changed(eid, since_changed=point[0], after_seq=point[1], limit=2)
            rows.extend(page['rows'])
            if not page['has_more']:
                break
            point = page['next']
        assert [r['seq'] for r in rows] == [0, 1, 2, 3]
        for n in range(105):
            append(host, sid, kind='incident', detail={'incident_id': str(n), 'category': 'fixture'})
        for _ in range(3):
            append(host, sid, kind='protocol_attention', detail={})
        newest = host.store.db.execute('SELECT MAX(seq) FROM events WHERE sid=?', (sid,)).fetchone()[0]
        before = view(host.store, sid)
        trace = []
        host.store.db.set_trace_callback(trace.append)
        page = host.rows(eid, from_seq=1, limit=2)
        assert [r['seq'] for r in page['rows']] == [1, 2] and page['next_from_seq'] == 3
        assert page['incident_count'] == 105 and len(page['incidents']) == 100
        assert len(host.rows(eid, incident_offset=100)['incidents']) == 5
        assert [r['seq'] for r in host.rows(eid, limit=2)['rows']] == [2, 3]
        assert host.rows(eid, by_origin=page['rows'][0]['origin'])['rows'] == [page['rows'][0]]
        diag = host.row_diagnostics(eid, limit=2)
        assert len(diag['diagnostics']) == 2 and diag['has_more'] and diag['next_offset'] == 2
        assert len(host.row_diagnostics(eid, offset=2)['diagnostics']) == 1
        for method in ('rows', 'rows_changed', 'row_witnesses', 'row_diagnostics'):
            response = asyncio.run(host.dispatch({'method': method, 'params': {'execution_id': eid}}))
            assert response['as_of'] == newest and response['built_through'] == newest
            assert response['epoch'].startswith('rows-') and response['status'] == 'complete'
        assert host.row_witnesses(eid)['witnesses']
        assert not any(sql.startswith(('INSERT', 'UPDATE', 'DELETE', 'COMMIT')) for sql in trace)
        assert view(host.store, sid) == before
        assert sid not in host.sessions and host.rows(eid, from_seq=1, limit=2) == page
    finally:
        close(host)


def test_stream_ends_follow_offsets_in_the_event_transaction_and_after_restart(tmp_path):
    root = tmp_path / 'state'
    store = Store(root)
    try:
        trace = []
        store.db.set_trace_callback(trace.append)
        with store.batch():
            store.event('s', 1, 'fixture', stream='stdout', offset=0, length=12)
            store.event('s', 1, 'fixture', stream='stdout', offset=2, length=3)  # never decrease an end
            store.event('s', 1, 'fixture', stream='stderr', offset=0, length=7)
            store.event('s', 2, 'fixture', stream='stdout', offset=0, length=4)
            store.event('other', 1, 'fixture', stream='stdout', offset=0, length=9)
            store.event('s', 1, 'fixture_without_bytes')
        assert trace.count('COMMIT') == 1
        expected = [('other', 1, 'stdout', 9), ('s', 1, 'stderr', 7),
                    ('s', 1, 'stdout', 12), ('s', 2, 'stdout', 4)]
        ends = lambda: [tuple(row) for row in store.db.execute('SELECT * FROM stream_ends ORDER BY 1,2,3')]
        assert ends() == expected
        before = store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        with pytest.raises(RuntimeError, match='rollback'):
            with store.batch():
                store.event('s', 1, 'fixture', stream='stdout', offset=12, length=30)
                store.event('new', 1, 'fixture', stream='stdin', offset=0, length=99)
                raise RuntimeError('rollback')
        assert ends() == expected
        assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == before
        # An ordinary later append also exercises the seq_floor path after rollback.
        store.event('s', 1, 'fixture_after_rollback')
        store.db.close()
        store = Store(root)
        assert ends() == expected
        actual = [tuple(row) for row in store.db.execute(
            'SELECT sid,generation,stream,MAX(offset+length) FROM events '
            'WHERE offset IS NOT NULL AND length IS NOT NULL GROUP BY sid,generation,stream ORDER BY 1,2,3')]
        assert ends() == actual
    finally:
        store.db.close()


def test_stream_ends_invalidate_only_when_an_older_writer_appended_unindexed_events(tmp_path):
    root = tmp_path / 'state'
    store = Store(root)
    store.event('s', 1, 'fixture', stream='stdout', offset=0, length=12)
    store.db.close()
    store = Store(root)
    assert store.db.execute('SELECT end FROM stream_ends').fetchone()[0] == 12
    store.db.close()
    # A pre-U4.1 Store knows nothing about the new table or its event watermark.
    with sqlite3.connect(root / 'host.sqlite3') as old:
        old.execute('INSERT INTO events(sid,generation,kind,detail,stream,offset,length) VALUES (?,?,?,?,?,?,?)',
                    ('s', 1, 'old_writer', '{}', 'stdout', 12, 8))
    store = Store(root)
    try:
        assert not store.db.execute('SELECT 1 FROM stream_ends').fetchone()  # next gap check seeds exact ends
        assert store.db.execute('SELECT MAX(offset+length) FROM events WHERE sid=?', ('s',)).fetchone()[0] == 20
        assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 2  # evidence was not invalidated
        store.event('s', 1, 'fixture', stream='stdout', offset=20, length=3)
        assert store.db.execute('SELECT end FROM stream_ends').fetchone()[0] == 23
    finally:
        store.db.close()


def test_every_startup_event_read_uses_one_sid_index_range_or_the_event_primary_key(tmp_path, monkeypatch):
    root = tmp_path / 'state'
    sid = str(uuid.uuid4())
    store = Store(root)
    private_dir(root / sid / 'g000001')
    store.put('sessions', sid, {'native_uuid': sid, 'execution_id': 'cc-' + sid, 'generation': 1})
    store.append_bytes(sid, 1, 'stdout', b'abc')
    store.event(sid, 1, 'fixture', stream='stdout', offset=0, length=3)
    store.db.execute('DROP TABLE IF EXISTS stream_ends')  # first U4.1 start over an older store
    store.db.commit()
    store.db.close()
    connect = sqlite3.connect
    trace = []
    def traced(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(trace.append)
        return db
    monkeypatch.setattr(sqlite3, 'connect', traced)
    for start in (1, 2):
        trace.clear()
        host = Host(root, tmp_path / 'host.sock', builder, auth)
        host.store.db.set_trace_callback(None)
        try:
            reads = [sql for sql in trace if sql.lstrip().upper().startswith('SELECT') and 'FROM events' in sql]
            assert reads
            for sql in reads:
                plan = ' '.join(row[3] for row in host.store.db.execute('EXPLAIN QUERY PLAN ' + sql))
                assert 'SCAN events' not in plan and 'GROUP BY' not in sql, (sql, plan)
                if 'WHERE sid=' in sql:
                    assert 'events_by_session' in plan or 'INTEGER PRIMARY KEY' in plan, (sql, plan)
                else:
                    assert 'MAX(seq)' in sql and 'SEARCH events' in plan, (sql, plan)  # rowid MAX is a PK seek
            stream_reads = [sql for sql in trace if sql.startswith('SELECT end FROM stream_ends')]
            assert len(stream_reads) == 3
            for sql in stream_reads:
                plan = ' '.join(row[3] for row in host.store.db.execute('EXPLAIN QUERY PLAN ' + sql))
                assert 'sqlite_autoindex_stream_ends_1 (sid=? AND generation=? AND stream=?)' in plan, plan
            aggregates = [sql for sql in reads if 'MAX(offset+length)' in sql]
            assert len(aggregates) == (3 if start == 1 else 0)
        finally:
            host.store.db.close()
            os.close(host.host_lock)


def test_task_input_indexes_share_the_native_read_commit(tmp_path, monkeypatch):
    from claude_headless import observations
    async def scenario():
        host, session, process, task = await _fed_session(tmp_path)
        monkeypatch.setattr(observations, 'task_processes', lambda *_: ([], None))
        try:
            # _fed_session bypasses Host.launch; model the launch's initial coverage here.
            with host.store.batch():
                host.store.mark_task_inputs_complete(session.sid, 1)
            trace = []
            host.store.db.set_trace_callback(trace.append)
            frames = [
                {'type': 'assistant', 'message': {'content': [
                    {'type': 'tool_use', 'id': 'shared-input', 'name': 'Bash', 'input': {'command': 'sleep 60'}}]}},
                {'type': 'assistant', 'parent_tool_use_id': 'agent', 'isSidechain': True, 'message': {'content': [
                    {'type': 'tool_use', 'id': 'shared-input', 'name': 'Bash', 'input': {'command': 'sleep 60'}}]}},
                {'type': 'system', 'subtype': 'task_started', 'task_id': 'task-one', 'run_id': 'run-one',
                 'tool_use_id': 'shared-input', 'task_type': 'local_bash'},
            ]
            await _read(process, b''.join(_line(frame) for frame in frames))
            assert not session.infrastructure_error
            assert trace.count('COMMIT') == 1
            writes = [n for n, sql in enumerate(trace) if sql.startswith('INSERT OR IGNORE INTO task_inputs')]
            assert writes and max(writes) < trace.index('COMMIT')
            native = host.store.db.execute("SELECT seq FROM events WHERE kind='native_frame' ORDER BY seq").fetchall()
            rows = host.store.db.execute("SELECT seq FROM task_inputs WHERE kind='tool' AND lookup_key='shared-input' ORDER BY seq").fetchall()
            assert [r[0] for r in rows] == [native[0][0], native[1][0]]  # Agent/sidechain evidence is not skipped
            lifecycle = host.store.task_input_rows(session.sid, 1, 'task', ('task-one', 'run-one'))
            assert [r['seq'] for r in lifecycle] == [native[2][0]]
            assert host.store.task_inputs_complete(session.sid, 1)
            assert not any(sql.startswith('SELECT ') and ' FROM events WHERE sid=' in sql and 'generation=' in sql
                           for sql in trace)
        finally:
            process.stdout.feed_eof()
            await task
            host.store.db.close()
    asyncio.run(scenario())


def test_task_input_index_and_coverage_roll_back_with_frame_batch(tmp_path):
    async def scenario():
        host, session, process, task = await _fed_session(tmp_path)
        try:
            with host.store.batch():
                host.store.mark_task_inputs_complete(session.sid, 1)
            before = [tuple(r) for r in host.store.db.execute('SELECT * FROM task_inputs')]
            frame = _line({'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'rolled-back-input', 'name': 'Bash', 'input': {'command': 'sleep 60'}}]}})
            offset = host.store.append_bytes(session.sid, 1, 'stdout', frame)
            with pytest.raises(RuntimeError, match='fixture rollback'):
                with host.store.batch():
                    session.frame(frame, offset, len(frame))
                    assert host.store.db.execute("SELECT 1 FROM task_inputs WHERE kind='tool'").fetchone()
                    assert host.store.task_inputs_complete(session.sid, 1)
                    raise RuntimeError('fixture rollback')
            session.restore_committed()
            assert [tuple(r) for r in host.store.db.execute('SELECT * FROM task_inputs')] == before
            assert not host.store.db.execute("SELECT 1 FROM events WHERE kind='native_frame'").fetchone()
        finally:
            process.stdout.feed_eof()
            await task
            host.store.db.close()
    asyncio.run(scenario())


def test_launched_generations_start_with_complete_task_input_coverage(tmp_path):
    from claude_headless.observations import full_task_input
    async def scenario():
        host, session, clock = await setup(tmp_path, role='worker')
        try:
            assert host.store.task_inputs_complete(session.sid, session.generation)
            trace = []
            host.store.db.set_trace_callback(trace.append)
            assert full_task_input(host, session, {'tool_use_id': 'absent'}) == (
                None, 'missing or ambiguous full scoped tool input')
            assert full_task_input(host, session, {'task_id': 'absent', 'run_id': 'absent'}) == (
                None, 'task has no unique exact task/run to tool-use identity')
            assert not any(sql.startswith('SELECT ') and ' FROM events WHERE sid=' in sql and 'generation=' in sql
                           for sql in trace)
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_task_input_lookup_plan_uses_exact_key_and_event_rowid(tmp_path):
    store = Store(tmp_path / 'state')
    try:
        for kind, key in [('tool', 'tool-one'), ('task', '["task-one", "run-one"]')]:
            plan = [r[3] for r in store.db.execute('EXPLAIN QUERY PLAN '
                'SELECT e.* FROM task_inputs AS i JOIN events AS e ON e.seq=i.seq '
                'WHERE i.sid=? AND i.generation=? AND i.kind=? AND i.lookup_key=? ORDER BY i.seq',
                ('sid', 1, kind, key))]
            assert any('SEARCH i USING COVERING INDEX' in p and 'sid=? AND generation=? AND kind=? AND lookup_key=?' in p
                       for p in plan), plan
            assert any('SEARCH e USING INTEGER PRIMARY KEY (rowid=?)' in p for p in plan), plan
            assert not any('SCAN ' in p for p in plan), plan
    finally:
        store.db.close()


def test_task_input_index_does_not_reject_json_escaped_tool_ids(tmp_path):
    import inspect
    from types import SimpleNamespace
    from claude_headless.observations import full_task_input
    store = Store(tmp_path / 'state')
    try:
        sid, tool = str(uuid.uuid4()), 'tool-\ud800'
        private_dir(store.root / sid / 'g000001')
        event = {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': tool, 'name': 'Bash', 'input': {'command': 'sleep 60'}}]}}
        raw = _line(event)  # JSON-valid escaped surrogate, not malformed UTF-8
        offset = store.append_bytes(sid, 1, 'stdout', raw)
        # The base accepts this frame; the index must not introduce a new failure at ingestion.
        parsed = {'_task_frame': event} if '_task_frame' in inspect.signature(store.event).parameters else {}
        cursor = store.event(sid, 1, 'native_frame', {'type': 'assistant'}, stream='stdout', offset=offset,
                             length=len(raw), **parsed)
        source, reason = full_task_input(SimpleNamespace(store=store), SimpleNamespace(sid=sid, generation=1),
                                        {'tool_use_id': tool})
        assert reason is None and source['tool_use_id'] == tool and source['cursor'] == cursor
    finally:
        store.db.close()
