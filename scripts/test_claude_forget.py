"""Exact-sid deletion and tombstones; synthetic stores and inert native transports only."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid

import pytest

from claude_headless.client import Client
from claude_headless.host import Host
from claude_headless.rows import SqliteRows
from claude_headless.store import Store, private_dir
from test_claude_headless_host import auth, builder, until


TABLES = ('events', 'receipts', 'permissions', 'controls', 'incidents', 'recoveries', 'results',
          'rows', 'row_parts', 'row_keys', 'row_diagnostics', 'row_incidents', 'row_sets', 'stream_ends', 'task_inputs')
ROW_TABLES = ('row_sets', 'rows', 'row_parts', 'row_keys', 'row_diagnostics', 'row_incidents')


@contextmanager
def offline_host(tmp_path):
    host = Host(tmp_path / 'state', tmp_path / 'host.sock', builder, auth)
    try:
        yield host
    finally:
        host.store.db.close()
        os.close(host.host_lock)


def ended(host, sid=None, **extra):
    sid = sid or str(uuid.uuid4())
    run = private_dir(host.root / sid / 'g000001')
    (run / 'prompt.md').write_text('fixture')
    record = {'native_uuid': sid, 'execution_id': 'cc-' + sid, 'generation': 1, 'role': 'worker',
              'state': 'exited', 'owner': 'gone', 'first_launched_at': 1, 'last_activity_at': 2,
              'pending_message_uuids': [], 'pending_escalations': [], 'run_dir': str(run),
              'cwd': str(host.root.parent), 'requested_model': 'haiku', 'requested_effort': 'low',
              'prompt_sha256': hashlib.sha256(b'fixture').hexdigest(), **extra}
    host.store.put('sessions', sid, record)
    return record


def seed(store, sid, count=1):
    """At least one row in each table, including the tables that no current fixture event fills."""
    with store.batch():
        for table in TABLES:
            if table == 'events':
                store.event(sid, 1, 'fixture', stream='stdout', offset=0, length=7)
                continue
            if table in ('row_sets', 'stream_ends'):
                # Store.event already seeded its exact stream end and writer watermark.
                continue
            columns = [row['name'] for row in store.db.execute(f'PRAGMA table_info({table})')]
            for n in range(count if table == 'row_diagnostics' else 1):
                body = {'execution_id': 'cc-' + sid, 'recovery_id': 'fixture', 'status': 'completed',
                        'message_uuid': 'fixture', 'completed': True, 'fixture': n}
                values = {'sid': sid, 'body': json.dumps(body), 'detail': '{}', 'generation': 1,
                          'scope': 'main', 'n': n, 'seq': n, 'part': n, 'ordinal': n, 'time': 1,
                          'changed': n, 'body_changed': n, 'background': 0}
                store.db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES '
                                 f'({",".join("?" for _ in columns)})',
                                 [values.get(column, 'fixture') for column in columns])
        backend = SqliteRows(store, sid)
        backend.data.update(status='complete', built_through=store.db.execute(
            'SELECT COALESCE(MAX(seq),0) FROM events WHERE sid=?', (sid,)).fetchone()[0])
        backend.save()


def counts(store):
    tables = [row[0] for row in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if any(col['name'] == 'sid' for col in store.db.execute(f'PRAGMA table_info({row[0]})'))]
    return {table: dict(store.db.execute(f'SELECT sid, count(*) FROM {table} GROUP BY sid')) for table in tables}


async def call(host, method, **params):
    return await host.dispatch({'method': method, 'params': params})


def test_forget_per_sid_tables_files_cache_and_every_list_mode(tmp_path, monkeypatch):
    async def scenario():
        with offline_host(tmp_path) as host:
            record, other = ended(host), ended(host)
            sid, eid = record['native_uuid'], record['execution_id']
            transcript = tmp_path / 'projects' / 'slug' / (sid + '.jsonl')
            transcript.parent.mkdir(parents=True)
            transcript.write_text('the archived conversation')
            subagents = transcript.parent / sid
            subagents.mkdir()
            (subagents / 'agent-x.jsonl').write_text('subagent')
            record['native_transcript_path_expected'] = str(transcript)
            host.store.put('sessions', sid, record)
            seed(host.store, sid, 4200)
            seed(host.store, other['native_uuid'])
            before = counts(host.store)
            host.store.cached_sessions()  # prime L-17's cache before the tombstone write
            threads, real_remove = [], host.remove_forget_files

            def remove(record):
                threads.append(threading.get_ident())
                real_remove(record)
            monkeypatch.setattr(host, 'remove_forget_files', remove)
            result = await call(host, 'forget', execution_ids=[eid])
            assert result['accepted'] == [eid] and result['skipped'] == [] and result['job']
            tombstone = host.store.get('sessions', sid)
            assert tombstone['state'] == 'exited' and tombstone['owner'] == 'gone'
            assert tombstone['archived'] == {'at': tombstone['archived']['at'], 'key': 'claude:' + eid, 'by': 'forget'}
            assert tombstone['forget_pending'] is True
            assert tombstone['last_activity_at'] == 2  # no invented conversation activity to put it in default list
            assert next(r for s, r, _ in host.store.cached_sessions() if s == sid) == tombstone
            assert (await call(host, 'state', execution_id=eid))['archived'] == tombstone['archived']
            for selection in ({}, {'execution_ids': [eid]}, {'all': True}, {'execution_ids': [eid], 'summary': True}):
                rows = await call(host, 'list', **selection)
                assert next(r for r in rows if r['execution_id'] == eid)['archived'] == tombstone['archived']
            await host.forget_task
            after = counts(host.store)
            for table in TABLES:
                assert after[table].get(sid, 0) == 0
            for table in before:
                assert {s: n for s, n in after[table].items() if s != sid} == {
                    s: n for s, n in before[table].items() if s != sid}
            assert host.store.get('sessions', sid)['archived'] == tombstone['archived']
            assert 'forget_pending' not in host.store.get('sessions', sid)
            assert not (host.root / sid).exists() and not transcript.exists() and not subagents.exists()
            assert threads and all(t != threading.get_ident() for t in threads)
            health = await call(host, 'health')
            assert 'forget' in health['implemented']
            assert health['forget'] == {'status': 'complete', 'queued': 0, 'current': None,
                                        'rows_deleted': sum(before[t][sid] for t in TABLES), 'done': 1, 'failed': []}
    asyncio.run(scenario())


@pytest.mark.parametrize('state,owner', [('ready', 'host'), ('ownership_unknown', 'unknown'),
    ('exited_draining', 'draining'), ('exited', 'host')])
def test_forget_not_ended_unknown_and_already_archived(tmp_path, state, owner):
    async def scenario():
        with offline_host(tmp_path) as host:
            live = ended(host, state=state, owner=owner)
            archived = ended(host, archived={'at': 1, 'key': 'claude:cc-fixture', 'by': 'forget'})
            absent = 'cc-' + str(uuid.uuid4())
            before = host.store.db.total_changes
            result = await call(host, 'forget', execution_ids=[live['execution_id'], absent, archived['execution_id']])
            assert result['accepted'] == []
            assert result['skipped'] == [{'id': live['execution_id'], 'reason': 'not-ended'},
                {'id': absent, 'reason': 'unknown'}, {'id': archived['execution_id'], 'reason': 'already-archived'}]
            assert host.store.db.total_changes == before and host.forget_task is None
    asyncio.run(scenario())


@pytest.mark.parametrize('params', [{}, {'execution_ids': 'cc-x'}, {'execution_ids': [1]},
    {'execution_ids': ['not-an-id']}, {'execution_ids': [], 'yes': True},
    {'execution_ids': [], 'empty_sid': True}, {'empty_sid': False}, {'empty_sid': 1}])
def test_forget_validation_is_value_error_before_writes(tmp_path, params):
    async def scenario():
        with offline_host(tmp_path) as host:
            with pytest.raises(ValueError):
                await call(host, 'forget', **params)
            assert host.forget_task is None
    asyncio.run(scenario())


def test_forget_launch_failed_is_ended_and_other_work_is_not_a_gate(tmp_path):
    async def scenario():
        with offline_host(tmp_path) as host:
            record = ended(host, state='launch_failed', pending_recovery='old', pending_message_uuids=['uncertain'])
            result = await call(host, 'forget', execution_ids=[record['execution_id']])
            assert result['accepted'] == [record['execution_id']]
            await host.forget_task
            assert host.state(record['execution_id'])['state'] == 'exited'
    asyncio.run(scenario())


def test_reconstruct_tombstone_does_not_probe_write_or_requeue(tmp_path, monkeypatch):
    with offline_host(tmp_path) as host:
        record = ended(host, archived={'at': 1, 'key': 'fixture', 'by': 'forget'}, forget_pending=True)
        before = json.dumps(record, sort_keys=True)
        statements = []
        host.store.db.set_trace_callback(statements.append)

        def forbidden(*args, **kwargs):
            pytest.fail('a tombstone must be skipped before any ownership/path/row work')
        monkeypatch.setattr('claude_headless.host.witness', forbidden)
        monkeypatch.setattr('claude_headless.host.owner_lock', forbidden)
        monkeypatch.setattr(host, 'index_raw_gaps', forbidden)
        monkeypatch.setattr(host.store, 'put', forbidden)
        host.reconstruct(record)
        assert statements == [] and json.dumps(record, sort_keys=True) == before
        assert host.forget_task is None  # resumption belongs after socket bind, not reconstruction


@pytest.mark.parametrize('method', ['resume', 'reconnect', 'fork', 'launch-resume'])
def test_archived_refusal_text_precedes_role_and_path_checks(tmp_path, method):
    async def scenario():
        with offline_host(tmp_path) as host:
            record = ended(host, archived={'at': 1793059200000, 'key': 'claude:cc-fixture', 'by': 'forget'})
            with pytest.raises(ValueError) as error:
                if method == 'fork':
                    host.fork_base(record['execution_id'], sid=str(uuid.uuid4()), cwd=str(tmp_path), role='worker')
                elif method == 'launch-resume':
                    await host.launch(native_uuid=record['native_uuid'], cwd=str(tmp_path), model='haiku',
                                      effort='low', system_prompt='fixture', resume=True)
                else:
                    await call(host, method, execution_id=record['execution_id'], **({'generation': 1} if method == 'reconnect' else {}))
            assert str(error.value) == 'archived on 2026-10-27T00:00:00+00:00; its conversation is in 📦 History (claude:cc-fixture)'
    asyncio.run(scenario())


def test_store_forget_batches_are_bounded_indexed_equalities_and_committed(tmp_path):
    store = Store(tmp_path / 'state')
    try:
        seed(store, 'target', 4503)
        seed(store, 'other', 2)
        trace, sizes, total = [], [], 0
        store.db.set_trace_callback(trace.append)
        while True:
            deleted, elapsed = store.forget_batch('row_diagnostics', 'target', 999999)
            assert 0 <= deleted <= 2000 and elapsed >= 0
            assert not store.db.in_transaction and store.depth == 0
            sizes.append(deleted)
            total += deleted
            if not deleted:
                break
        assert total == 4503 and len(sizes) >= 4
        assert store.db.execute("SELECT count(*) FROM row_diagnostics WHERE sid='other'").fetchone()[0] == 2
        deletes = [sql for sql in trace if sql.startswith('DELETE')]
        assert deletes and all("WHERE sid='target' LIMIT" in sql for sql in deletes)
        # A redundant outer sid equality picks a full sid-index walk on composite
        # keys: 0.73 s even for ONE row on the real 13.48 M-row diagnostics range.
        # The bounded rowid subquery must feed PRIMARY KEY lookups, not that walk.
        plan = [row[3] for row in store.db.execute('EXPLAIN QUERY PLAN ' + deletes[0])]
        assert 'USING INTEGER PRIMARY KEY (rowid=?)' in plan[0], plan
        assert trace.count('BEGIN ') == trace.count('COMMIT') >= 3
        assert not any('VACUUM' in sql for sql in trace)
    finally:
        store.db.close()


def test_forget_queue_yields_to_controls_and_a_second_call(tmp_path, monkeypatch):
    async def scenario():
        with offline_host(tmp_path) as host:
            a, b = ended(host), ended(host)
            seed(host.store, a['native_uuid'], 7000)
            real, ticks = host.store.forget_batch, []

            def batch(*args, **kwargs):
                result = real(*args, **kwargs)
                ticks.append(result[0])
                assert host.store.depth == 0 and not host.store.db.in_transaction
                return result
            monkeypatch.setattr(host.store, 'forget_batch', batch)
            first = await call(host, 'forget', execution_ids=[a['execution_id']])
            task = host.forget_task
            health = await call(host, 'health')
            assert health['forget']['status'] == 'running' and health['forget']['done'] == 0
            assert (await call(host, 'state', execution_id=a['execution_id']))['archived']
            second = await call(host, 'forget', execution_ids=[b['execution_id']])
            assert second['job'] == first['job'] and host.forget_task is task
            await task
            assert host.forget_done == 2 and len(ticks) > 26
    asyncio.run(scenario())


def test_forget_delete_plans_use_rowid_not_a_whole_composite_sid_range(tmp_path):
    store = Store(tmp_path / 'state')
    try:
        trace = []
        store.db.set_trace_callback(trace.append)
        for table in TABLES:
            store.forget_batch(table, '', 64)
        deletes = [sql for sql in trace if sql.startswith('DELETE')]
        assert len(deletes) == len(TABLES)
        for sql in deletes:
            assert "WHERE sid='' LIMIT" in sql  # every statement names even the empty sid by equality
            plan = [row[3] for row in store.db.execute('EXPLAIN QUERY PLAN ' + sql)]
            assert 'USING INTEGER PRIMARY KEY (rowid=?)' in plan[0], (sql, plan)
    finally:
        store.db.close()


def test_forget_restart_mid_deletion_resumes_after_bind(tmp_path):
    async def scenario():
        with tempfile.TemporaryDirectory(prefix='u32-sock-') as sockets:
            path = Path(sockets) / 'h.sock'
            host = Host(tmp_path / 'state', path, builder, auth)
            record = ended(host)
            seed(host.store, record['native_uuid'], 15000)
            serving = asyncio.create_task(host.serve())
            await until(lambda: path.exists())
            client = Client(path)
            await client.acall('forget', execution_ids=[record['execution_id']])
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
            pending = host.store.get('sessions', record['native_uuid'])
            assert pending['forget_pending'] and host.forget_rows_deleted < 15012
            host.store.db.close()
            os.close(host.host_lock)
            path.unlink()
            restarted = Host(tmp_path / 'state', path, builder, auth)
            assert restarted.forget_task is None
            assert restarted.store.get('sessions', record['native_uuid']) == pending
            serving = asyncio.create_task(restarted.serve())
            try:
                await until(lambda: restarted.forget_task is not None)
                await restarted.forget_task
                state = await client.acall('state', execution_id=record['execution_id'])
                assert state['archived'] == pending['archived'] and not state.get('forget_pending')
                assert all(counts(restarted.store)[t].get(record['native_uuid'], 0) == 0 for t in TABLES)
                assert restarted.forget_health()['done'] == 1
            finally:
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)
                restarted.store.db.close()
                os.close(restarted.host_lock)
    asyncio.run(scenario())


def test_backfill_rechecks_archive_after_its_yield(tmp_path, monkeypatch):
    async def scenario():
        with offline_host(tmp_path) as host:
            record = ended(host)
            seed(host.store, record['native_uuid'])
            backend = SqliteRows(host.store, record['native_uuid'])
            with host.store.batch():
                backend.data['status'] = 'queued'
                backend.save()
            pages = []

            def page(store, sid, backend, **kwargs):
                pages.append(sid)
                return backend or SqliteRows(store, sid)  # queued; a second page would run after the await
            monkeypatch.setattr('claude_headless.host.backfill_rows_page', page)
            backfill = asyncio.create_task(host.backfill_rows())
            await asyncio.sleep(0)
            assert pages == [record['native_uuid']]
            await call(host, 'forget', execution_ids=[record['execution_id']])
            await until(lambda: host.rows_backfill_status == 'complete')
            assert not backfill.done()  # The projection worker now sleeps until the next queued set.
            backfill.cancel()
            await asyncio.gather(backfill, return_exceptions=True)
            await host.forget_task
            assert pages == [record['native_uuid']]
            assert all(counts(host.store)[t].get(record['native_uuid'], 0) == 0 for t in TABLES)
    asyncio.run(scenario())


def test_a_pending_recovery_cannot_recreate_forgotten_rows(tmp_path, monkeypatch):
    async def scenario():
        with offline_host(tmp_path) as host:
            record = ended(host)
            seed(host.store, record['native_uuid'])
            waiting, release = asyncio.Event(), asyncio.Event()
            resume = host.resume_snapshot

            async def paused(eid):
                waiting.set()
                await release.wait()
                return await resume(eid)
            monkeypatch.setattr(host, 'resume_snapshot', paused)
            recovery = {'execution_id': record['execution_id'], 'recovery_id': 'fixture', 'status': 'scheduled'}
            task = asyncio.create_task(host.supervisor.execute(recovery))
            await waiting.wait()
            await call(host, 'forget', execution_ids=[record['execution_id']])
            host.supervisor.consider_recovery({**recovery, 'status': 'scheduled'}, 0)
            release.set()
            await task
            await host.forget_task
            assert host.supervisor.actions == {}
            assert all(counts(host.store)[t].get(record['native_uuid'], 0) == 0 for t in TABLES)
    asyncio.run(scenario())


def test_empty_sid_deletes_exactly_six_tables_and_no_files_or_other_sids(tmp_path, monkeypatch):
    async def scenario():
        with offline_host(tmp_path) as host:
            other = ended(host)
            seed(host.store, '', 4200)
            seed(host.store, other['native_uuid'])
            for table in ('incidents', 'quota_checks', 'observations', 'observer_recoveries'):
                host.store.put(table, '', {'fixture': True}, 'global')
            before = counts(host.store)

            def forbidden(*args, **kwargs):
                pytest.fail('junk may not replay, delete files or replace a session')
            monkeypatch.setattr(host.store, 'replay', forbidden)
            monkeypatch.setattr(host, 'remove_forget_files', forbidden)
            result = await call(host, 'forget', empty_sid=True)
            assert result['accepted'] == [''] and result['skipped'] == [] and result['job']
            await host.forget_task
            after = counts(host.store)
            for table, sids in before.items():
                expected = {**sids}
                if table in ROW_TABLES:
                    expected.pop('', None)
                assert after[table] == expected, table
            assert host.forget_health() == {'status': 'complete', 'queued': 0, 'current': None,
                'rows_deleted': sum(before[t][''] for t in ROW_TABLES), 'done': 1, 'failed': []}
            assert json.loads(host.store.db.execute("SELECT value FROM meta WHERE key='forget_empty_sid'").fetchone()[0])['status'] == 'complete'
    asyncio.run(scenario())


def test_junk_restart_marker_resumes_without_a_tombstone(tmp_path):
    async def scenario():
        with tempfile.TemporaryDirectory(prefix='u32-junk-') as sockets:
            path = Path(sockets) / 'h.sock'
            host = Host(tmp_path / 'state', path, builder, auth)
            seed(host.store, '', 15000)
            before = counts(host.store)
            await call(host, 'forget', empty_sid=True)
            host.forget_task.cancel()
            await asyncio.gather(host.forget_task, return_exceptions=True)
            assert json.loads(host.store.db.execute("SELECT value FROM meta WHERE key='forget_empty_sid'").fetchone()[0])['status'] == 'started'
            host.store.db.close()
            os.close(host.host_lock)
            restarted = Host(tmp_path / 'state', path, builder, auth)
            assert restarted.forget_task is None
            serving = asyncio.create_task(restarted.serve())
            try:
                await until(lambda: restarted.forget_task is not None)
                await restarted.forget_task
                after = counts(restarted.store)
                assert all(after[t].get('', 0) == 0 for t in ROW_TABLES)
                assert all(after[t] == before[t] for t in before if t not in ROW_TABLES)
                assert after['sessions'] == {}
            finally:
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)
                restarted.store.db.close()
                os.close(restarted.host_lock)
    asyncio.run(scenario())


def test_forget_failure_keeps_pending_and_continues_other_jobs(tmp_path, monkeypatch):
    async def scenario():
        with offline_host(tmp_path) as host:
            a, b = ended(host), ended(host)
            real = host.remove_forget_files

            def fail_one(record):
                if record['native_uuid'] == a['native_uuid']:
                    raise OSError('fixture: files unavailable')
                real(record)
            monkeypatch.setattr(host, 'remove_forget_files', fail_one)
            result = await call(host, 'forget', execution_ids=[a['execution_id'], b['execution_id']])
            await host.forget_task
            assert result['accepted'] == [a['execution_id'], b['execution_id']]
            assert host.forget_health()['failed'] == [{'id': a['execution_id'], 'error': 'OSError'}]
            assert host.forget_health()['done'] == 1 and host.forget_health()['status'] == 'failed'
            assert host.store.get('sessions', a['native_uuid'])['forget_pending']
            assert 'forget_pending' not in host.store.get('sessions', b['native_uuid'])
    asyncio.run(scenario())


def test_completed_junk_stays_empty_after_store_reopen(tmp_path):
    async def scenario():
        with offline_host(tmp_path) as host:
            seed(host.store, '')
            await call(host, 'forget', empty_sid=True)
            await host.forget_task
        store = Store(tmp_path / 'state')
        try:
            assert all(counts(store)[t].get('', 0) == 0 for t in ROW_TABLES)
            assert counts(store)['events'] == {'': 1}  # the genuine host-wide event remains
        finally:
            store.db.close()
    asyncio.run(scenario())


def test_an_inflight_resume_rechecks_the_tombstone_after_auth(tmp_path):
    async def scenario():
        with offline_host(tmp_path) as host:
            record = ended(host)
            waiting, release = asyncio.Event(), asyncio.Event()

            async def paused_auth():
                waiting.set()
                await release.wait()
                return await auth()
            host.auth_check = paused_auth
            resume = asyncio.create_task(host.resume_snapshot(record['execution_id']))
            await waiting.wait()
            await call(host, 'forget', execution_ids=[record['execution_id']])
            await host.forget_task
            release.set()
            with pytest.raises(ValueError, match='its conversation is in 📦 History'):
                await resume
            assert host.sessions == {}
            assert all(counts(host.store)[t].get(record['native_uuid'], 0) == 0 for t in TABLES)
    asyncio.run(scenario())


def test_native_fixture_send_does_not_wait_for_forget(tmp_path):
    from test_claude_headless_host import setup, teardown, send

    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            record = ended(host)
            seed(host.store, record['native_uuid'], 50000)
            await client.acall('forget', execution_ids=[record['execution_id']])
            mid, receipt = await send(client, row, 'control lane remains open')
            assert receipt['status'] == 'accepted'
            assert host.forget_health()['status'] == 'running'
            assert host.forget_health()['done'] == 0
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            await host.forget_task
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_exit_bookkeeping_failure_still_releases_owner_so_forget_answers_archived(tmp_path, monkeypatch):
    from test_claude_headless_host import setup, teardown

    async def scenario():
        host, serving, client, row, session = await setup(tmp_path)
        def broken(*args):
            raise RuntimeError('fixture exit bookkeeping failed')
        monkeypatch.setattr(host.supervisor, 'exited', broken)
        try:
            await client.acall('close_input', execution_id=row['execution_id'], generation=1)
            await until(lambda: session.tasks[3].done())
            errors = await asyncio.gather(session.tasks[3], return_exceptions=True)
            assert isinstance(errors[0], RuntimeError) and session.record['owner'] == 'gone'
            result = await client.acall('forget', execution_ids=[row['execution_id']])
            assert result['accepted'] == [row['execution_id']]
            await host.forget_task
            assert (await client.acall('state', execution_id=row['execution_id']))['archived']['by'] == 'forget'
            listed = await client.acall('list', execution_ids=[row['execution_id']])
            assert listed[0]['archived']['by'] == 'forget' and session.sid not in host.sessions
        finally:
            host.sessions.pop(session.sid, None)  # fixture-only cleanup of the deliberately raising ended task
            await teardown(host, serving)

    asyncio.run(scenario())


def test_forget_removes_task_input_pointers_and_coverage_but_keeps_other_sessions(tmp_path):
    async def scenario():
        with offline_host(tmp_path) as host:
            record, other = ended(host), ended(host)
            sid, other_sid = record['native_uuid'], other['native_uuid']
            seed(host.store, sid)
            seed(host.store, other_sid)
            with host.store.batch():
                host.store.mark_task_inputs_complete(sid, 1)
                host.store.mark_task_inputs_complete(other_sid, 1)
            assert host.store.db.execute('SELECT count(*) FROM task_inputs WHERE sid=?', (sid,)).fetchone()[0] == 2
            await call(host, 'forget', execution_ids=[record['execution_id']])
            await host.forget_task
            assert not host.store.db.execute('SELECT 1 FROM task_inputs WHERE sid=?', (sid,)).fetchone()
            assert host.store.db.execute('SELECT count(*) FROM task_inputs WHERE sid=?', (other_sid,)).fetchone()[0] == 2
            assert host.state(record['execution_id'])['archived']['key'] == 'claude:' + record['execution_id']
    asyncio.run(scenario())
