"""Durable row ownership, resumable pages, read contracts and failure envelopes (offline only)."""
import asyncio
import copy
import json
import os
import uuid

import pytest

from claude_headless.host import Host
from claude_headless.lexical import ReaderError
from claude_headless.rows import (MemoryRows, SqliteRows, RowsSource, RowProjector,
                                  project_through, backfill_rows_page)
from claude_headless.store import Store, private_dir
from test_claude_headless_host import auth, builder
from test_claude_rows import memory_index


def host_at(path):
    return Host(path / 'state', path / 'host.sock', builder, auth)


def close(host):
    host.store.db.close()
    os.close(host.host_lock)


async def backfill_to_idle(host):
    """Exercise a long-lived worker through one drain, then cancel it as serve's shutdown does."""
    task = asyncio.create_task(host.backfill_rows())
    try:
        await asyncio.sleep(0)
        async def idle():
            while host.rows_backfill_status not in ('complete', 'failed'):
                await asyncio.sleep(0)
        await asyncio.wait_for(idle(), 10)  # fixture deadline only, never host policy
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def append(host, sid, value=None, *, kind='native_frame', detail=None, generation=1, frames=True):
    store = host.store
    with store.batch():
        raw = (json.dumps(value, ensure_ascii=False) + '\n').encode() if value is not None else None
        offset = None
        if raw is not None:
            private_dir(store.path(sid, generation, 'stdout').parent)
            offset = store.append_bytes(sid, generation, 'stdout', raw)
        return store.event(sid, generation, kind,
            detail if detail is not None else {'type': value['type']},
            'stdout' if raw is not None else None, offset, len(raw) if raw is not None else None,
            arrival_time=1800000000, _frame=raw if frames else None)


def assistant(n, *, scope=None, call=None):
    content = [{'type': 'text', 'text': f'answer {n}'}]
    if call:
        content.append({'type': 'tool_use', 'id': call, 'name': 'Read', 'input': {}})
    return {'type': 'assistant', 'uuid': str(n), 'parent_tool_use_id': scope,
            'message': {'role': 'assistant', 'content': content}}


def queue(store, sid, *, empty=False):
    with store.batch():
        if empty:
            for table in ('rows', 'row_parts', 'row_keys', 'row_diagnostics', 'row_incidents', 'row_sets'):
                store.db.execute(f'DELETE FROM {table} WHERE sid=?', (sid,))
        backend = SqliteRows(store, sid)
        backend.data['status'] = 'queued'
        backend.save()
    return backend


def view(store, sid):
    backend = SqliteRows(store, sid)
    return {
        'rows': [SqliteRows.decode(r[0], store, sid) for r in store.db.execute('SELECT body FROM rows WHERE sid=? ORDER BY ordinal', (sid,))],
        'parts': [tuple(r) for r in store.db.execute('SELECT origin,part,body FROM row_parts WHERE sid=? ORDER BY origin,part', (sid,))],
        'keys': [tuple(r) for r in store.db.execute('SELECT kind,k,body FROM row_keys WHERE sid=? ORDER BY kind,k', (sid,))],
        'diagnostics': [json.loads(r[0]) for r in store.db.execute("SELECT body FROM row_diagnostics WHERE sid=? ORDER BY json_extract(body,'$.revision')", (sid,))],
        'incidents': [json.loads(r[0]) for r in store.db.execute('SELECT body FROM row_incidents WHERE sid=? ORDER BY n', (sid,))],
        'watermark': backend.built_through, 'counts': backend.data['counts'],
        'maps': [backend.agent_tasks, backend.child_tasks, backend.agent_info]}


def drain(store, sid):
    backend = SqliteRows(store, sid)
    while backend.data['status'] != 'complete':
        backend = backfill_rows_page(store, sid, backend)
    return backend


def test_u6_cf08_failed_second_projection_keeps_raw_and_reloads_at_savepoint(tmp_path, monkeypatch):
    from types import SimpleNamespace
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    backend = SqliteRows(host.store, sid)
    try:
        with host.store.batch():
            backend.save()
        host.sessions[sid] = SimpleNamespace(rows=backend)  # retained cache, no native process
        advance = backend.advance
        observations, reload_calls = [], []
        reload = backend.reload
        def tracked_reload():
            reload_calls.append((host.store.depth, host.store.db.in_transaction))
            return reload()
        monkeypatch.setattr(backend, 'reload', tracked_reload)
        def fail_before_save(event):
            observations.append((host.store.depth, backend.data['incidents'], backend.state()['incidents']))
            raise OSError('synthetic second advance before save')
        with host.store.batch():
            # The projector only writes its row tables, not this pending session record.
            host.store.put('sessions', sid, {'fixture': 'pending caller record'})
            first = host.store.event(sid, 1, 'incident', {'incident_id': 'first', 'category': 'fixture'})
            monkeypatch.setattr(backend, 'advance', fail_before_save)
            host.store.event(sid, 1, 'incident', {'incident_id': 'second', 'category': 'fixture'})
            host.store.event(sid, 1, 'incident', {'incident_id': 'third', 'category': 'fixture'})
        assert observations == [(2, 2, 1)]  # first append was saved, but not committed
        assert not host.store.depth and not host.store.db.in_transaction
        assert backend.state()['status'] == 'unreadable'
        assert backend.state()['reason'] == 'OSError: synthetic second advance before save'
        assert type(backend.state()['at']) in (int, float)
        assert backend.built_through == first
        assert host.store.db.execute('SELECT COUNT(*) FROM events WHERE sid=?', (sid,)).fetchone()[0] == 3
        assert host.store.db.execute('SELECT COUNT(*) FROM row_incidents WHERE sid=?', (sid,)).fetchone()[0] == 1
        assert host.store.get('sessions', sid) == {'fixture': 'pending caller record'}
        # Deliberate contract change: reload AFTER savepoint rollback, still inside the append batch.
        assert reload_calls == [(2, True)]
        backend.sync()
        assert reload_calls == [(2, True)]
        assert backend.data == SqliteRows(host.store, sid).data
        monkeypatch.setattr(backend, 'advance', advance)
        host.store.event(sid, 1, 'incident', {'incident_id': 'after', 'category': 'fixture'})
        assert backend.data['incidents'] == backend.state()['incidents'] == 1
        assert [r[0] for r in host.store.db.execute('SELECT n FROM row_incidents WHERE sid=?', (sid,))] == [0]
        assert host.rows('cc-' + sid)['incidents'][0]['incident_id'] == 'first'
        assert host.row_state('cc-' + sid)[1]['status'] == 'unreadable'
        assert host.rows_changed('cc-' + sid)['status'] == 'unreadable'
        # Fail before a first save too: cache_tag has not changed, but the savepoint reload must undo Python state.
        other = str(uuid.uuid4())
        other_backend = SqliteRows(host.store, other)
        with host.store.batch():
            other_backend.save()
        host.sessions[other] = SimpleNamespace(rows=other_backend)
        reload_calls.clear()
        monkeypatch.setattr(other_backend, 'advance', lambda event: (_ for _ in ()).throw(OSError('first save')))
        host.store.event(other, 1, 'incident', {'incident_id': 'first-save-fails', 'category': 'fixture'})
        assert other_backend.data['status'] == 'unreadable' and other_backend.data['incidents'] == 0
        assert other_backend.data == other_backend.state()
        good = str(uuid.uuid4())
        host.store.event(good, 1, 'incident', {'incident_id': 'unaffected', 'category': 'fixture'})
        assert SqliteRows(host.store, good).data['status'] == 'complete'
        assert host.rows('cc-' + good)['incidents'][0]['incident_id'] == 'unaffected'
    finally:
        close(host)


def test_u6_cf18_backfill_task_failures_name_failed_health_without_retry(tmp_path, monkeypatch):
    import claude_headless.host as module
    for fault in ('first-select', 'constructor', 'recovery-batch'):
        host = host_at(tmp_path / fault)
        sid = str(uuid.uuid4())
        try:
            assert host.rows_health().get('error') is None
            append(host, sid, assistant('synthetic'))
            queue(host.store, sid, empty=True)
            before = view(host.store, sid)
            message = fault + ': ' + 'x' * 240
            raised, db = [], host.store.db
            def fail():
                raised.append(fault)
                raise OSError(message)
            with monkeypatch.context() as inject:
                if fault == 'first-select':
                    class FirstSelectFailure:
                        def execute(self, sql, *args):
                            if sql.startswith('SELECT sid, body FROM row_sets'):
                                fail()
                            return db.execute(sql, *args)
                        def __getattr__(self, name):
                            return getattr(db, name)
                    inject.setattr(host.store, 'db', FirstSelectFailure())
                elif fault == 'constructor':
                    inject.setattr('claude_headless.rows.SqliteRows', lambda *a, **kw: fail())
                else:
                    def page_failure(*args, **kwargs):
                        raise OSError('synthetic page error before recovery')
                    inject.setattr(RowProjector, 'project', page_failure)
                    save = SqliteRows.save
                    inject.setattr(SqliteRows, 'save', lambda backend: fail()
                                   if backend.data['status'] == 'unreadable' else save(backend))
                asyncio.run(backfill_to_idle(host))
            health = host.rows_health()
            assert raised == [fault]  # task ends; no retries or timeout
            assert health['status'] == 'failed'
            assert health['error'] == 'OSError: ' + message[:200]
            assert health['remaining_sessions'] == 1 and health['unreadable'] == []
            assert view(host.store, sid) == before and not host.store.db.in_transaction
            # A separately invoked clean walk clears the prior error; this is not an automatic retry.
            asyncio.run(backfill_to_idle(host))
            assert host.rows_health()['status'] == 'complete'
            assert host.rows_health()['error'] is None
        finally:
            close(host)


def test_backfill_rederives_writer_sessions_and_interruption_resumes_exactly(tmp_path):
    host = host_at(tmp_path)
    try:
        sids = [str(uuid.uuid4()) for _ in range(3)]
        for n in range(540):
            for sid in sids:
                append(host, sid, assistant(n))
        expected = {sid: view(host.store, sid) for sid in sids}
        for sid in sids:
            queue(host.store, sid, empty=True)
            backend = backfill_rows_page(host.store, sid)
            assert backend.data['status'] == 'running' and 1 <= sum(backend.data['counts'].values()) <= 250
            # Throw away the driver, exactly the durable interruption boundary.
            backend = drain(host.store, sid)
            assert backend.data['status'] == 'complete'
            assert view(host.store, sid) == expected[sid]
            before = view(host.store, sid)
            backfill_rows_page(host.store, sid)  # a complete set is never projected
            assert view(host.store, sid) == before
    finally:
        close(host)


def test_rows_serves_exact_stored_agent_registry_in_every_selector(tmp_path):
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    try:
        append(host, sid, assistant('main'))
        append(host, sid, assistant('child', scope='agent-call'))
        backend = SqliteRows(host.store, sid)
        info = {'index': 4, 'description': 'exact label', 'state': 'running', 'future': {'kept': True}}
        with host.store.batch():
            backend.agent_info[(2, 'agent-call')] = info
            backend.save()
        expected = [{'generation': 2, 'call': 'agent-call', **info}]
        origin = host.rows('cc-' + sid)['rows'][0]['origin']
        for selector in ({}, {'from_seq': 0}, {'by_origin': origin}, {'agent_window': [-1, None]}):
            assert host.rows('cc-' + sid, **selector)['agent_info'] == expected
        assert host.rows('cc-' + str(uuid.uuid4()))['agent_info'] == []
    finally:
        close(host)


def test_stream_parts_append_once_without_growing_row_body_and_survive_reload(tmp_path):
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    def stream(value):
        return {'type': 'stream_event', 'event': value}
    try:
        append(host, sid, stream({'type': 'message_start', 'message': {'id': 'long'}}))
        append(host, sid, stream({'type': 'content_block_start', 'index': 0,
                                 'content_block': {'type': 'text', 'text': ''}}))
        for n in range(120):
            append(host, sid, stream({'type': 'content_block_delta', 'index': 0,
                                     'delta': {'type': 'text_delta', 'text': 'word '}}))
            if n == 0:
                size = host.store.db.execute('SELECT length(body) FROM rows').fetchone()[0]
        body = host.store.db.execute('SELECT body FROM rows').fetchone()[0]
        assert 'text_parts' not in json.loads(body)
        assert len(body) < size + 100  # counts grow, witness lists do not get rewritten
        assert host.store.db.execute('SELECT COUNT(*) FROM row_parts').fetchone()[0] == 120
        row = host.rows('cc-' + sid)['rows'][0]
        assert len(row['text_parts']) == 120 and row['prose_bytes'] == 600
        part_rows = [tuple(r) for r in host.store.db.execute('SELECT * FROM row_parts ORDER BY part')]
        append(host, sid, {'type': 'assistant', 'uuid': 'final',
            'message': {'id': 'long', 'role': 'assistant', 'content': [{'type': 'text', 'text': 'word ' * 120}]}})
        assert host.rows('cc-' + sid)['rows'][0]['text_parts'] == []
        assert [tuple(r) for r in host.store.db.execute('SELECT * FROM row_parts ORDER BY part')] == part_rows
    finally:
        close(host)


def test_rows_comparison_renders_stored_structure_without_replaying_projector(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'transcription-pane'))
    import claude_live as live
    import claude_reader as reader
    from claude_rows_compare import stored_facade, session_dump, StoreClient
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    eid = 'cc-' + sid
    try:
        host.store.put('sessions', sid, {'native_uuid': sid, 'execution_id': eid, 'generation': 1,
                                       'owner': 'gone', 'state': 'exited', 'role': 'worker', 'cwd': str(tmp_path)})
        append(host, sid, assistant('main'))
        append(host, sid, assistant('child', scope='child'))
        history = reader.ClaudeReader([])
        history.host_turn_ledger = lambda *a, **k: None
        expected = session_dump(live.ClaudeFacade(history, StoreClient(host.store)), eid, 1)
        def forbidden(*args, **kwargs):
            raise AssertionError('stored source must not call replay/projector')
        monkeypatch.setattr(RowProjector, 'project', forbidden)
        monkeypatch.setattr(host.store, 'replay', forbidden)
        actual = session_dump(stored_facade(live, history, host.store), eid, 1)
        assert actual == expected
        queue(host.store, sid)
        with pytest.raises(ValueError, match='building'):
            session_dump(stored_facade(live, history, host.store), eid, 1)
    finally:
        close(host)


def test_three_way_policy_matches_rows_diagnostics_incidents_and_source_limit(tmp_path, monkeypatch):
    import claude_headless.rows as module
    monkeypatch.setattr(module, 'SOURCE_LIMIT', 700)
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    try:
        append(host, sid, assistant('first'))
        append(host, sid, kind='malformed_frame', detail={})
        append(host, sid, {'type': 'user', 'uuid': 'large', 'message': {'role': 'user', 'content': 'x' * 900}})
        append(host, sid, kind='incident', detail={'incident_id': 'fixture', 'category': 'silence'})
        append(host, sid, kind='protocol_attention', detail={})
        append(host, sid, kind='raw_reconciliation_gap', detail={})
        append(host, sid, {'type': 'stream_event', 'event': {'type': 'message_start', 'message': {'id': 'open'}}})
        append(host, sid, {'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': 0,
                                                           'content_block': {'type': 'future'}}})
        append(host, sid, assistant('child', scope='child'))
        expected = view(host.store, sid)
        idx = memory_index()
        idx.execution = 'cc-' + sid
        assert project_through(RowsSource(host.store), sid, RowProjector(MemoryRows(idx)))
        memory_rows = copy.deepcopy(idx.rows)
        for row in memory_rows:
            row.pop('_summary_size', None)
            row['seq'] = sum(r['scope'] == row['scope'] and r['ordinal'] < row['ordinal'] for r in idx.rows)
        assert json.loads(json.dumps(memory_rows)) == json.loads(json.dumps(expected['rows']))
        assert idx.diagnostics == expected['diagnostics'] and idx.incidents == expected['incidents']
        assert 'source-limit' in [d['status'] for d in idx.diagnostics]
        queue(host.store, sid, empty=True)
        drain(host.store, sid)
        assert view(host.store, sid) == expected
    finally:
        close(host)


def test_backfill_yields_without_a_batch_and_streaming_queued_session_is_exactly_once(tmp_path, monkeypatch):
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    try:
        for n in range(530):
            append(host, sid, assistant(n))
        queue(host.store, sid, empty=True)
        real_sleep, observations = asyncio.sleep, []
        async def yielded(delay):
            assert host.store.depth == 0 and not host.store.db.in_transaction  # catches a batch across await
            state = SqliteRows(host.store, sid).data
            observations.append((state['status'], state['built_through']))
            if len(observations) == 1:
                before = view(host.store, sid)
                append(host, sid, assistant('while-queued'))
                assert view(host.store, sid) == before  # live writer does not project running/queued
                assert (await host.dispatch({'method': 'list', 'params': {}})) == []
            await real_sleep(0)
        monkeypatch.setattr(asyncio, 'sleep', yielded)
        asyncio.run(backfill_to_idle(host))
        assert len(observations) >= 3
        health = asyncio.run(host.dispatch({'method': 'health', 'params': {}}))
        assert health['row_schema'] == 1 and health['rows_backfill']['status'] == 'complete'
        assert health['rows_backfill']['remaining_sessions'] == 0
        actual = view(host.store, sid)
        assert len(actual['rows']) == 531 and [r['seq'] for r in actual['rows']] == list(range(531))
        assert actual['watermark'] == host.store.cursor(host.store.db.execute('SELECT MAX(seq) FROM events WHERE sid=?', (sid,)).fetchone()[0])
    finally:
        close(host)


def test_raw_read_failure_is_persisted_unreadable_and_walk_continues(tmp_path, monkeypatch):
    host = host_at(tmp_path)
    good, bad = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        append(host, good, assistant('good'))
        append(host, bad, assistant('bad'))  # newest first => bad session before good
        for sid in (bad, good):
            queue(host.store, sid, empty=True)
        host.store.path(bad, 1, 'stdout').unlink()  # scratch fixture only
        assert view(host.store, bad)['rows'] == [] and view(host.store, bad)['diagnostics'] == []
        with pytest.raises(ReaderError) as error:
            idx = memory_index()
            idx.execution = 'cc-' + bad
            project_through(RowsSource(host.store), bad, RowProjector(MemoryRows(idx)))
        assert error.value.code == 502 and error.value.status == 'read-failure'
        assert not idx.rows and not idx.diagnostics and idx.cursor is None
        asyncio.run(backfill_to_idle(host))
        failed = SqliteRows(host.store, bad).data
        assert failed['status'] == 'unreadable' and failed['reason'].startswith('ReaderError: ')
        assert type(failed['at']) in (int, float)
        assert view(host.store, bad)['rows'] == []
        assert SqliteRows(host.store, good).data['status'] == 'complete'
        assert len(view(host.store, good)['rows']) == 1
        assert host.rows_health()['unreadable'] == ['cc-' + bad]
        assert host.rows_health()['remaining_sessions'] == 1
        close(host)
        host = host_at(tmp_path)  # Startup/requeue must preserve the very same persisted failure.
        assert SqliteRows(host.store, bad).data == failed
        def forbidden(*a, **kw):
            raise AssertionError('unreadable must never be retried')
        monkeypatch.setattr('claude_headless.host.backfill_rows_page', forbidden)
        asyncio.run(backfill_to_idle(host))
        assert host.rows_health()['status'] == 'complete'
        assert host.rows_health()['unreadable'] == ['cc-' + bad]
        append(host, bad, assistant('raw-still-commits'))
        assert SqliteRows(host.store, bad).data == failed
        assert host.store.db.execute('SELECT COUNT(*) FROM events WHERE sid=?', (bad,)).fetchone()[0] == 2
    finally:
        close(host)


def test_old_writer_growth_is_requeued_on_open_with_epoch_and_positions_preserved(tmp_path, capfd):
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    try:
        append(host, sid, assistant('before'))
        epoch = SqliteRows(host.store, sid).data['epoch']
        host.store.rows_writer = None  # the older host does not know about rows
        append(host, sid, assistant('old-writer'))
        root = host.store.root
        close(host)
        capfd.readouterr()
        store = Store(root)
        try:
            assert SqliteRows(store, sid).data['status'] == 'queued'
            assert SqliteRows(store, sid).data['epoch'] == epoch
            drain(store, sid)
            assert [r['seq'] for r in view(store, sid)['rows']] == [0, 1]
            assert SqliteRows(store, sid).data['epoch'] == epoch
            resumed = view(store, sid)
            queue(store, sid, empty=True)
            drain(store, sid)
            assert view(store, sid) == resumed  # continuation equals an uninterrupted full derivation
            assert store.db.execute("SELECT value FROM meta WHERE key='migration_rows'").fetchone()
        finally:
            store.db.close()
        capfd.readouterr()
        store = Store(root)
        assert SqliteRows(store, sid).data['status'] == 'complete'
        assert capfd.readouterr().err == ''
        store.db.close()
    except BaseException:
        # The normal close occurred before reopen; do not double-close its connection/lock.
        raise


def test_backfill_serves_list_state_and_send_between_pages(tmp_path, monkeypatch):
    from test_claude_storage import _fed_session
    async def scenario():
        host, session, process, stdout = await _fed_session(tmp_path)
        try:
            for n in range(530):
                append(host, session.sid, assistant(n))
            queue(host.store, session.sid, empty=True)
            real_sleep, sent = asyncio.sleep, []
            async def yielded(delay):
                assert not host.store.depth and not host.store.db.in_transaction
                if not sent:
                    assert (await host.dispatch({'method': 'state', 'params': {'execution_id': session.record['execution_id']}}))['state'] == 'ready'
                    assert len(await host.dispatch({'method': 'list', 'params': {}})) == 1
                    receipt = await host.dispatch({'method': 'send', 'params': {
                        'execution_id': session.record['execution_id'], 'generation': 1,
                        'mid': str(uuid.uuid4()), 'submitted': 'during backfill', 'original': 'during backfill'}})
                    assert receipt['status'] == 'accepted' and receipt['durable']
                    sent.append(receipt)
                await real_sleep(0)
            monkeypatch.setattr(asyncio, 'sleep', yielded)
            await backfill_to_idle(host)
            assert len(sent) == 1 and SqliteRows(host.store, session.sid).data['status'] == 'complete'
            assert len(view(host.store, session.sid)['rows']) == 530
        finally:
            process.stdout.feed_eof()
            await stdout
            close(host)
    asyncio.run(scenario())


def test_migration_only_seeds_existing_logs_and_second_open_is_write_free(tmp_path, capfd):
    root = tmp_path / 'old-store'
    store = Store(root)
    sids = [str(uuid.uuid4()) for _ in range(2)]
    for sid in sids:
        store.put('sessions', sid, {'native_uuid': sid})  # S2: known logs belong to sessions or row_sets
        for _ in range(3):
            store.event(sid, 1, 'ignored')
    for table in ('rows', 'row_parts', 'row_keys', 'row_diagnostics', 'row_incidents', 'row_sets'):
        store.db.execute(f'DROP TABLE {table}')
    store.db.execute("DELETE FROM meta WHERE key IN ('row_schema','migration_rows')")
    store.db.commit()
    store.db.close()
    capfd.readouterr()
    store = Store(root)
    assert 'rows migration' in capfd.readouterr().err
    assert store.db.execute('SELECT COUNT(*) FROM rows').fetchone()[0] == 0
    assert all(SqliteRows(store, sid).data['status'] == 'queued' for sid in sids)
    record = store.db.execute("SELECT value FROM meta WHERE key='migration_rows'").fetchone()[0]
    assert json.loads(record)['queued_sessions'] == 2
    store.db.close()
    store = Store(root)
    assert capfd.readouterr().err == ''
    assert store.db.execute("SELECT value FROM meta WHERE key='migration_rows'").fetchone()[0] == record
    store.db.close()


def test_backfill_chooses_live_sessions_before_newer_ended_logs(tmp_path, monkeypatch):
    import claude_headless.host as module
    host = host_at(tmp_path)
    live, ended = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        append(host, live, assistant('older-live'))
        append(host, ended, assistant('newer-ended'))
        for sid in (live, ended):
            queue(host.store, sid, empty=True)
        host.sessions[live] = object()  # ordering only; no NativeSession/process is started
        actual, real = [], module.backfill_rows_page
        def page(store, sid, backend=None, **kwargs):
            actual.append(sid)
            return real(store, sid, backend, **kwargs)
        monkeypatch.setattr(module, 'backfill_rows_page', page)
        asyncio.run(backfill_to_idle(host))
        assert actual == [live, ended]
    finally:
        close(host)


def test_backfill_round_robins_three_sets_one_page_each_live_first(tmp_path, monkeypatch):
    import claude_headless.host as module
    host = host_at(tmp_path)
    sids = [str(uuid.uuid4()) for _ in range(3)]
    try:
        host.store.rows_writer = None
        with host.store.batch():
            for sid in sids:
                for _ in range(503):
                    host.store.event(sid, 1, 'ignored')
                queue(host.store, sid)
        host.store.rows_writer = host.append_rows
        host.sessions[sids[1]] = object()  # Live first even though its row set was not inserted first.
        host.monotonic = lambda: 0  # Count-bound proof independent of machine speed.
        pages, sql, real = [], [], module.backfill_rows_page
        def page(store, sid, backend=None, **kwargs):
            before = (backend or SqliteRows(store, sid)).data['revision']
            result = real(store, sid, backend, **kwargs)
            pages.append((sid, result.data['revision'] - before))
            if len(pages) == 1:
                store.event(sid, 1, 'raw-while-running')  # Already owned: no new all-set inventory on each round.
            return result
        monkeypatch.setattr(module, 'backfill_rows_page', page)
        host.store.db.set_trace_callback(sql.append)
        asyncio.run(backfill_to_idle(host))
        host.store.db.set_trace_callback(None)
        expected_round = [sids[1], sids[0], sids[2]]
        assert [sid for sid, _ in pages] == expected_round * 3
        assert [count for _, count in pages] == [250] * 6 + [4, 3, 3]
        inventories = [s for s in sql if s.startswith('SELECT sid, body FROM row_sets')]
        assert len(inventories) == 1  # Not a metadata scan or event join for every page/round.
        assert not any('JOIN events' in s or 'json_extract' in s for s in sql)
        assert all(SqliteRows(host.store, sid).data['status'] == 'complete' for sid in sids)
    finally:
        close(host)


def test_backfill_wakes_for_sets_queued_after_start_and_after_a_completed_round(tmp_path):
    from test_claude_headless_host import until
    async def scenario():
        host = host_at(tmp_path)
        task = asyncio.create_task(host.backfill_rows())
        try:
            await until(lambda: host.rows_backfill_status == 'complete')
            assert not task.done()
            for _ in range(2):
                sid = str(uuid.uuid4())
                host.store.rows_writer = None
                with host.store.batch():
                    SqliteRows(host.store, sid).save()  # A newly stale complete set while the worker sleeps.
                    for _ in range(251):
                        host.store.event(sid, 1, 'ignored')
                host.store.rows_writer = host.append_rows
                cursor = host.store.event(sid, 1, 'append-wakes-projection')
                assert SqliteRows(host.store, sid).data['status'] == 'queued'
                await until(lambda: host.rows_backfill_status == 'complete' and
                            SqliteRows(host.store, sid).built_through == cursor)
                assert SqliteRows(host.store, sid).data['revision'] == 252
                assert not task.done() and not host.rows_backfill_wake.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            close(host)
    asyncio.run(scenario())


def test_backfill_elapsed_budget_stops_each_page_at_an_event_boundary(tmp_path, monkeypatch):
    import claude_headless.host as module
    host = host_at(tmp_path)
    sid, clock = str(uuid.uuid4()), [0.0]
    try:
        host.store.rows_writer = None
        with host.store.batch():
            for _ in range(7):
                host.store.event(sid, 1, 'ignored')
            queue(host.store, sid)
        host.store.rows_writer = host.append_rows
        host.monotonic = lambda: clock[0]
        project, real, pages = RowProjector.project, module.backfill_rows_page, []
        def charged(self, *args):
            project(self, *args)
            clock[0] += (module.ROW_SLICE_MS - module.ROW_COMMIT_HEADROOM_MS) / 1000 * .55
        def page(store, sid, backend=None, **kwargs):
            result = real(store, sid, backend, **kwargs)
            pages.append(result.data['revision'])
            return result
        monkeypatch.setattr(RowProjector, 'project', charged)
        monkeypatch.setattr(module, 'backfill_rows_page', page)
        asyncio.run(backfill_to_idle(host))
        assert pages == [2, 4, 6, 7]  # Check before each event, not only before the 250-event page.
        assert SqliteRows(host.store, sid).data['status'] == 'complete'
        assert SqliteRows(host.store, sid).data['built_through'] == 7
    finally:
        close(host)


@pytest.mark.parametrize('exit_kind', ['cancel', 'task-failure'])
def test_backfill_freezes_retained_heap_but_new_cycles_collect_and_exit_unfreezes(tmp_path, monkeypatch, exit_kind):
    import gc
    import weakref
    from test_claude_headless_host import until
    async def scenario():
        host = host_at(tmp_path)
        calls, enabled = [], gc.isenabled()
        freeze, unfreeze = gc.freeze, gc.unfreeze
        def frozen():
            calls.append(('freeze', host.store.depth, host.store.db.in_transaction))
            freeze()
        def thawed():
            calls.append(('unfreeze', host.store.depth, host.store.db.in_transaction))
            unfreeze()
        monkeypatch.setattr(gc, 'freeze', frozen)
        monkeypatch.setattr(gc, 'unfreeze', thawed)
        if exit_kind == 'task-failure':
            db = host.store.db
            class FailedInventory:
                def execute(self, sql, *args):
                    if sql.startswith('SELECT') and 'row_sets' in sql:
                        raise OSError('inventory storage failure')
                    return db.execute(sql, *args)
                def __getattr__(self, name):
                    return getattr(db, name)
            monkeypatch.setattr(host.store, 'db', FailedInventory())
        task = asyncio.create_task(host.backfill_rows())
        try:
            await until(lambda: host.rows_backfill_status in ('complete', 'failed'))
            if exit_kind == 'cancel':
                assert calls == [('freeze', 0, False)] and gc.get_freeze_count() > 0
                class Cycle:
                    pass
                cycle = Cycle()
                cycle.link = cycle
                witness = weakref.ref(cycle)
                del cycle
                gc.collect()  # New cycles still collect while the pre-existing retained graph is frozen.
                assert witness() is None and gc.isenabled() == enabled
            else:
                assert host.rows_backfill_status == 'failed'
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            close(host)
        assert calls == [('freeze', 0, False), ('unfreeze', 0, False)]
        assert gc.get_freeze_count() == 0 and gc.isenabled() == enabled
    asyncio.run(scenario())


def test_backfill_failed_page_rolls_back_its_prefix_and_persists_unreadable_in_one_batch(tmp_path, monkeypatch):
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    try:
        host.store.rows_writer = None
        with host.store.batch():
            for n in range(3):
                host.store.event(sid, 1, 'incident', {'incident_id': str(n), 'category': 'fixture'})
            queue(host.store, sid)
        host.store.rows_writer = host.append_rows
        project, projected, sql = RowProjector.project, [], []
        def failed(self, event, frame):
            projected.append(event['cursor'])
            if len(projected) == 2:
                raise OSError('background projection failure')
            return project(self, event, frame)
        monkeypatch.setattr(RowProjector, 'project', failed)
        host.store.db.set_trace_callback(sql.append)
        asyncio.run(backfill_to_idle(host))
        host.store.db.set_trace_callback(None)
        state = SqliteRows(host.store, sid).data
        assert state['status'] == 'unreadable' and state['reason'] == 'OSError: background projection failure'
        assert state['revision'] == state['built_through'] == state['incidents'] == 0
        assert view(host.store, sid)['incidents'] == []
        assert host.store.db.execute('SELECT COUNT(*) FROM events WHERE sid=?', (sid,)).fetchone()[0] == 3
        assert len(projected) == 2 and host.rows_health()['unreadable'] == ['cc-' + sid]
        assert sql.count('BEGIN ') == sql.count('COMMIT') == 1
        assert sql.count('SAVEPOINT rows_page') == sql.count('ROLLBACK TO rows_page') == 1
        assert sql.count('RELEASE rows_page') == 1 and 'ROLLBACK' not in sql
    finally:
        close(host)


def test_backfill_does_not_recreate_a_set_deleted_between_pages(tmp_path, monkeypatch):
    import claude_headless.host as module
    from test_claude_headless_host import until
    async def scenario():
        host = host_at(tmp_path)
        sid = str(uuid.uuid4())
        host.store.rows_writer = None
        with host.store.batch():
            for _ in range(530):
                host.store.event(sid, 1, 'ignored')
            queue(host.store, sid)
        first_page, real = asyncio.Event(), module.backfill_rows_page
        def page(*args, **kwargs):
            result = real(*args, **kwargs)
            first_page.set()
            return result
        monkeypatch.setattr(module, 'backfill_rows_page', page)
        task = asyncio.create_task(host.backfill_rows())
        try:
            await first_page.wait()
            assert not host.store.depth and not host.store.db.in_transaction
            with host.store.batch():
                host.store.db.execute('DELETE FROM row_sets WHERE sid=?', (sid,))
            await until(lambda: host.rows_backfill_status == 'complete')
            assert SqliteRows(host.store, sid).state() is None
            assert host.store.db.execute('SELECT COUNT(*) FROM events WHERE sid=?', (sid,)).fetchone()[0] == 530
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            close(host)
    asyncio.run(scenario())


@pytest.mark.parametrize('residency', ['live', 'evicted', 'ended'])
def test_every_append_path_advances_to_newest_for_all_residencies(tmp_path, residency):
    from test_claude_storage import _fed_session, _read, _line
    async def scenario():
        host, session, process, stdout = await _fed_session(tmp_path)
        stderr = asyncio.create_task(session.stderr())
        writer = asyncio.create_task(session.writer())
        session.lock_fd = os.open('/dev/null', os.O_RDONLY)
        session.tasks = [writer, stdout, stderr]
        sid = session.sid
        if residency != 'live':
            del host.sessions[sid]
            session.record.update(owner='gone', state='exited')
        def current():
            newest = host.store.db.execute('SELECT MAX(seq) FROM events WHERE sid=?', (sid,)).fetchone()[0]
            assert SqliteRows(host.store, sid).data['built_through'] == newest
        try:
            await _read(process, _line(assistant('stdout')))
            current()
            process.stderr.feed_data(b'fixture stderr')
            while process.stderr._buffer:
                await asyncio.sleep(0)
            current()
            session.enqueue(b'{}\n', 'control', {'request_id': 'fixture'})
            while session.writes.qsize():
                await asyncio.sleep(0)
            current()  # stdin intent and the writer's stdin_written evidence
            host.supervisor.incident(session.record, 'fixture')
            current()
            host.store.append_bytes(sid, 1, 'stderr', b'unindexed retained bytes')
            assert host.index_raw_gaps(session.record)
            current()
            # Every control/collection/input-close/launch/reconnect append is one of these two funnels.
            for kind in ('control_authority', 'control_applied', 'collection_readback_requested',
                         'input_close_authorized', 'launch_intent', 'conversation_reconnected'):
                (session.event(kind) if kind.startswith(('control', 'collection', 'input'))
                 else host.store.event(sid, 1, kind))
                current()
            process.stdout.feed_eof()
            process.stderr.feed_eof()
            await asyncio.gather(stdout, stderr)
            current()
            live_rows = host.rows('cc-' + sid)['rows']
            process.returncode = 0
            await session.exited()  # actual process_exit + process_drains_settled journaling
            current()
            assert host.rows('cc-' + sid)['rows'] == live_rows
            before = host.rows('cc-' + sid)
            host.sessions.pop(sid, None)
            assert host.rows('cc-' + sid) == before
        finally:
            process.stdout.feed_eof()
            process.stderr.feed_eof()
            session.writes.put_nowait(None)
            await asyncio.gather(stdout, stderr, writer)
            close(host)
    asyncio.run(scenario())


def test_host_wide_event_gets_no_row_set_and_a_stale_one_is_never_walked(tmp_path, monkeypatch):
    """A host-wide event (sid '') has no conversation rows. A row set for '' would replay every
    session's events (replay treats an empty sid as "all"): the 2026-10-26 restart spent ~50 minutes
    of host CPU walking 14M events for one quota-check deferral, starving every pane read."""
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    try:
        append(host, sid, assistant('one'))
        append(host, '', kind='quota_check_deferred_by_operator', detail={'episode_id': '1'}, generation=0)
        assert host.store.db.execute("SELECT COUNT(*) FROM row_sets WHERE sid=''").fetchone()[0] == 0
        # A stale '' row set left by an older host is skipped by the backfill and not reported as remaining.
        queue(host.store, '')
        walked = []
        real_page = backfill_rows_page
        monkeypatch.setattr('claude_headless.host.backfill_rows_page',
                            lambda store, s, backend=None, **kw: walked.append(s) or real_page(store, s, backend, **kw))
        asyncio.run(backfill_to_idle(host))
        assert '' not in walked
        assert host.rows_health()['status'] == 'complete'
        assert host.rows_health()['remaining_sessions'] == 0
        assert 'cc-' not in host.rows_health()['built_through']
        assert len(view(host.store, sid)['rows']) == 1
    finally:
        close(host)
