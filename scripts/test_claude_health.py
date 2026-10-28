"""U4.5 committed health counters and quiet load facts; synthetic stores only."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import uuid

import pytest

from claude_headless.rows import SqliteRows, backfill_rows_page
from claude_headless.store import JUNK_TABLES
from test_claude_rows_backfill import close, host_at


def recount(host):
    states = {sid: json.loads(body) for sid, body in host.store.db.execute(
        "SELECT sid, body FROM row_sets WHERE sid!=''")}
    pending = {sid: host.store.db.execute('SELECT COUNT(*) FROM events WHERE sid=? AND seq>?',
        (sid, state['built_through'])).fetchone()[0] for sid, state in states.items()}
    behind = [sid for sid, state in states.items() if state['status'] in ('queued', 'running')]
    return {
        'remaining_sessions': sum(state['status'] != 'complete' for state in states.values()),
        'built_through': {'cc-' + sid: state['built_through'] for sid, state in states.items()},
        'unreadable': sorted('cc-' + sid for sid, state in states.items() if state['status'] == 'unreadable'),
        'behind_sets': len(behind), 'behind_events': sum(pending[sid] for sid in behind),
        'entries': {sid: (state['status'], state['built_through'], pending[sid]) for sid, state in states.items()}}


def assert_counters(host):
    expected, health = recount(host), host.rows_health()
    counters = host.store.row_counters()
    assert set(health) == {'status', 'error', 'remaining_sessions', 'built_through', 'unreadable'}
    assert health['remaining_sessions'] == counters.remaining_sessions == expected['remaining_sessions']
    assert health['built_through'] == counters.built_through == expected['built_through']
    assert sorted(health['unreadable']) == sorted(counters.unreadable) == expected['unreadable']
    assert counters.behind_sets == expected['behind_sets']
    assert counters.behind_events == expected['behind_events']
    assert {sid: value[:3] for sid, value in counters.states.items()} == expected['entries']
    return expected


def queued(host, sid, count):
    with host.store.batch():
        backend = SqliteRows(host.store, sid)
        backend.data['status'] = 'queued'
        backend.save()
        for _ in range(count):
            host.store.event(sid, 1, 'ignored')
    return backend


def test_rows_counters_follow_appends_backfill_ranges_rollback_and_restart(tmp_path):
    host = host_at(tmp_path)
    sid, other, aborted = (str(uuid.uuid4()) for _ in range(3))
    try:
        assert_counters(host)
        host.store.event(sid, 1, 'ignored')
        baseline = assert_counters(host)
        with host.store.batch():
            backend = SqliteRows(host.store, sid)
            backend.data['status'] = 'queued'
            backend.save()
            for _ in range(503):
                host.store.event(other, 1, 'ignored')  # Global sequence gaps are not this sid's debt.
                host.store.event(sid, 1, 'ignored')
            assert host.rows_health()['built_through'] == baseline['built_through']
            assert host.store.row_counters().behind_sets == 0  # No pre-commit publication.
        assert assert_counters(host)['behind_events'] == 503
        trace = []
        host.store.db.set_trace_callback(trace.append)
        backfill_rows_page(host.store, sid)
        host.store.db.set_trace_callback(None)
        after_page = assert_counters(host)
        assert after_page['behind_events'] == 253
        counts = [sql for sql in trace if sql.startswith('SELECT COUNT(*) FROM events')]
        assert counts and all('AND seq<=' in sql for sql in counts)  # Only the consumed 250-event range.
        plan = [row[3] for row in host.store.db.execute('EXPLAIN QUERY PLAN ' + counts[0])]
        assert any('events_by_session' in row and 'rowid>?' in row and 'rowid<?' in row for row in plan)
        host.store.event(sid, 1, 'raw-while-queued')
        assert assert_counters(host)['behind_events'] == 254
        before = copy.deepcopy(host.rows_health())
        counters = host.store.row_counters()
        with pytest.raises(RuntimeError, match='rolled back'):
            with host.store.batch():
                host.store.event(sid, 1, 'rolled-back-append')
                queued(host, aborted, 3)
                SqliteRows(host.store, sid).mark_unreadable(ValueError('rolled-back mark'))
                assert host.rows_health() == before
                raise RuntimeError('rolled back')
        assert host.store.row_counters() is counters and host.rows_health() == before
        assert_counters(host)
        close(host)
        host = host_at(tmp_path)
        assert_counters(host)
        while host.store.row_counters().behind_sets:
            backfill_rows_page(host.store, sid)
            assert_counters(host)
        assert host.rows_health()['remaining_sessions'] == 0
    finally:
        close(host)


def test_rows_counters_unreadable_keeps_health_but_not_debt_after_savepoint_failure(tmp_path, monkeypatch):
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    try:
        queued(host, sid, 9)
        before = assert_counters(host)
        advance, calls = SqliteRows.advance, []
        def fail_after_saved_prefix(backend, event):
            calls.append(event['cursor'])
            if len(calls) == 3:
                raise OSError('fixture projection failure')
            return advance(backend, event)
        with monkeypatch.context() as patch:
            patch.setattr(SqliteRows, 'advance', fail_after_saved_prefix)
            backfill_rows_page(host.store, sid)
        expected = assert_counters(host)
        assert len(calls) == 3 and expected['built_through'] == before['built_through']
        assert expected['unreadable'] == ['cc-' + sid] and expected['remaining_sessions'] == 1
        assert expected['behind_sets'] == expected['behind_events'] == 0
        assert expected['entries'][sid][2] == 9  # Failed prefix rolled back, not falsely consumed.
        host.store.event(sid, 1, 'raw-still-commits')
        assert assert_counters(host)['entries'][sid][2] == 10
        backfill_rows_page(host.store, sid)  # Unreadable is not automatically retried.
        assert_counters(host)
        close(host)
        host = host_at(tmp_path)
        assert assert_counters(host)['unreadable'] == ['cc-' + sid]
    finally:
        close(host)


def test_rows_counters_follow_forget_event_slices_row_set_and_empty_sid_junk(tmp_path):
    host = host_at(tmp_path)
    sid, other = (str(uuid.uuid4()) for _ in range(2))
    try:
        queued(host, sid, 170)
        host.store.event(other, 1, 'unaffected')
        with host.store.batch():
            SqliteRows(host.store, '').save()  # Retained host-wide junk must never enter conversation health.
        host.store.event('', 0, 'host-wide')
        assert assert_counters(host)['behind_events'] == 170
        for expected in (106, 42, 0):
            host.store.forget_batch('events', sid, 64)
            assert assert_counters(host)['behind_events'] == expected
        assert host.store.row_counters().behind_sets == 1  # The queued set still exists until its deletion.
        host.store.forget_batch('row_sets', sid)
        assert 'cc-' + sid not in assert_counters(host)['built_through']
        assert host.store.row_counters().behind_sets == 0
        before = assert_counters(host)
        for table in JUNK_TABLES:
            host.store.forget_batch(table, '')
            assert assert_counters(host) == before
        # Exercise the complete background forget owner, not only its Store deletion primitive.
        from test_claude_forget import ended
        record = ended(host)
        queued(host, record['native_uuid'], 5)
        async def forget():
            await host.forget({'execution_ids': [record['execution_id']]})
            await host.forget_task
        asyncio.run(forget())
        assert 'cc-' + record['native_uuid'] not in assert_counters(host)['built_through']
    finally:
        close(host)


def test_rows_counters_requeue_malformed_replacement_and_backward_watermark(tmp_path):
    host = host_at(tmp_path)
    sid = str(uuid.uuid4())
    try:
        for _ in range(5):
            host.store.event(sid, 1, 'ignored')
        assert_counters(host)
        backend = SqliteRows(host.store, sid)
        with host.store.batch():
            backend.data.update(status='queued', built_through=0)
            backend.save()
        assert assert_counters(host)['behind_events'] == 5
        with host.store.batch():
            host.store.db.execute("UPDATE row_sets SET body='not JSON' WHERE sid=?", (sid,))
            SqliteRows.requeue(host.store, sid)
        assert assert_counters(host)['unreadable'] == ['cc-' + sid]
        assert host.store.row_counters().behind_events == 0
    finally:
        close(host)


def test_health_preserves_rows_shape_without_per_call_row_set_reads(tmp_path):
    host = host_at(tmp_path)
    try:
        with host.store.batch():
            for _ in range(30):
                SqliteRows(host.store, str(uuid.uuid4())).save()
        assert_counters(host)
        trace = []
        host.store.db.set_trace_callback(trace.append)
        async def calls():
            for _ in range(20):
                reply = await host.dispatch({'method': 'health', 'params': {}})
                assert len(reply['rows_backfill']['built_through']) == 30
                reply['rows_backfill']['built_through'].clear()  # Returned maps are not the cache's objects.
        asyncio.run(calls())
        host.store.db.set_trace_callback(None)
        assert not any('FROM row_sets' in sql for sql in trace)
        assert not any('COUNT(' in sql for sql in trace)
        assert len(host.rows_health()['built_through']) == 30
    finally:
        close(host)


def test_health_load_is_additive_and_uses_existing_bound_receipt_instant(tmp_path):
    from claude_headless.client import Client
    from claude_headless.host import Host
    from test_claude_headless_host import auth, builder, until
    async def scenario():
        with tempfile.TemporaryDirectory(prefix='lag-') as sockets:
            host = Host(tmp_path / 'state', Path(sockets) / 'h.sock', builder, auth)
            serving = None
            try:
                before = await host.dispatch({'method': 'health'})
                assert before['load']['startup']['bound_at'] is None
                sid, bad = str(uuid.uuid4()), str(uuid.uuid4())
                queued(host, sid, 7)
                queued(host, bad, 4)
                with host.store.batch():
                    SqliteRows(host.store, bad).mark_unreadable(ValueError('fixture'))
                load = (await host.dispatch({'method': 'health'}))['load']
                assert set(load) == {'loop_lag_ms_max_60s', 'rows_behind_sets', 'rows_behind_events',
                                     'startup', 'wal_bytes', 'free_bytes'}
                expected = recount(host)
                assert load['rows_behind_sets'] == expected['behind_sets'] == 1
                assert load['rows_behind_events'] == expected['behind_events'] == 7
                assert before['load']['loop_lag_ms_max_60s'] == 0
                serving = asyncio.create_task(host.serve())
                await until(lambda: host.start_receipt.get('phase') == 'bound')
                reply = await Client(host.socket_path).acall('health')
                receipt = json.loads((host.root / 'host-start-receipt.json').read_text())
                assert reply['load']['startup']['bound_at'] == receipt['observed_at']
                assert receipt['phase'] == 'bound'
                assert all(type(reply['load']['startup'][key]) in (int, float) and
                    reply['load']['startup'][key] >= 0 for key in ('store_open_ms', 'migrate_ms', 'reconstruct_ms'))
                assert type(reply['load']['wal_bytes']) is int and reply['load']['wal_bytes'] >= 0
                assert type(reply['load']['free_bytes']) is int and reply['load']['free_bytes'] > 0
                bound = reply['load']['startup']['bound_at']
                host.publish_start_receipt('ready')
                assert host.load_health()['startup']['bound_at'] == bound
                reply['load']['startup'].clear()
                assert host.load_health()['startup']['bound_at'] == bound
            finally:
                if serving is not None:
                    serving.cancel()
                    await asyncio.gather(serving, return_exceptions=True)
                close(host)
    asyncio.run(scenario())


def test_startup_names_first_stream_end_seed_and_ordinary_second_start(tmp_path):
    from test_claude_forget import ended
    host = host_at(tmp_path)
    try:
        record = ended(host)
        host.store.event(record['native_uuid'], 1, 'ignored')
        close(host)
        host = host_at(tmp_path)
        first = host.load_health()['startup']
        assert first['stream_ends_seeded'] == 3  # stdout/stderr/stdin, each in the reconstruct transaction.
        assert first['bound_at'] is None
        close(host)
        host = host_at(tmp_path)
        second = host.load_health()['startup']
        assert second['stream_ends_seeded'] == 0
        assert all(second[key] >= 0 for key in ('store_open_ms', 'migrate_ms', 'reconstruct_ms'))
        assert_counters(host)
    finally:
        close(host)


def test_loop_lag_ticker_reports_wake_lateness_and_expires_old_maximum(tmp_path, monkeypatch):
    import claude_headless.host as module
    host = host_at(tmp_path)
    now, wakes = [0.0], []
    try:
        assert module.LOOP_LAG_TICK_SECONDS == 1 and module.LOOP_LAG_WINDOW_SECONDS == 60
        host.monotonic = lambda: now[0]
        async def sleep(delay):
            wakes.append(delay)
            assert delay == 1
            if len(wakes) == 4:
                raise asyncio.CancelledError()
            now[0] += delay + (2.25 if len(wakes) == 1 else .125)
        monkeypatch.setattr(module.asyncio, 'sleep', sleep)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(host.measure_loop_lag())
        assert host.load_health()['loop_lag_ms_max_60s'] == 2250
        now[0] = 64
        assert host.load_health()['loop_lag_ms_max_60s'] == 125
        now[0] = 66
        assert host.load_health()['loop_lag_ms_max_60s'] == 0
        # Load never changes a control/list's semantics, even with a large observed stall.
        assert asyncio.run(host.dispatch({'method': 'list', 'params': {'all': True}})) == []
    finally:
        close(host)


def test_serve_owns_and_cancels_the_single_lag_ticker_at_shutdown(tmp_path):
    from claude_headless.host import Host
    from test_claude_headless_host import auth, builder, until
    async def scenario():
        with tempfile.TemporaryDirectory(prefix='lag-') as sockets:
            host = Host(tmp_path / 'state', Path(sockets) / 'h.sock', builder, auth)
            serving = asyncio.create_task(host.serve())
            try:
                await until(lambda: host.loop_lag_task is not None)
                await asyncio.sleep(0)
                assert not host.loop_lag_task.done()
                assert host.loop_lag_samples == host.loop_lag_samples.__class__()
            finally:
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)
                assert host.loop_lag_task.cancelled()
                assert host.supervisor_task.done() and host.rows_backfill_task.done()
                close(host)
    asyncio.run(scenario())
