"""Host unit 1 (audit [ap:GEHGQK] Group C, M1, and the two-week list of [ap:WPWZ14]): every stuck state has a way out.

One bad record is that session's supervision failure, never the fleet's; the host's own supervision marker clears
itself; a durability failure is cleared once storage works again (the gap journaled); a pending input no process can
answer is released with a disposition (release_input), so an overnight crash whose recovery failed on an expired
login takes a follow-up, a cancel or a resolve in the morning without a forced replacement; a missing session
directory never stops the host; `list` answers two weeks plus everything live or busy. Inert fixture CLI, private
state roots and sockets; never the live host."""
import asyncio
import os
import shutil
import sqlite3
import threading
import time
import uuid

import pytest

import oc_backend
import oc_claude
from claude_headless.host import LIST_WINDOW_DAYS, Host, list_view
from claude_headless.settlement import settlement_blockers
from test_claude_headless_host import auth, builder, until
from test_claude_headless_supervision import cleanup, held, setup


@pytest.fixture(autouse=True)
def isolated_host_socket(tmp_path, monkeypatch):
    """The default socket is the LIVE host's. No test may reach it."""
    monkeypatch.setenv("OC_CLAUDE_HOST_SOCKET", str(tmp_path / "no-host.sock"))


async def finished(host, session, text):
    mid = str(uuid.uuid4())
    await session.send(mid, text, text)
    await until(lambda: (host.store.get('receipts', session.sid, mid) or {}).get('completed'))
    return mid


def incidents(host, category):
    return [x for x in host.store.incidents(limit=500)['incidents'] if x['category'] == category]


# ------------------------------------------------------------------------------------ 1 · scoped supervision

def test_one_bad_record_is_that_sessions_supervision_failure_never_the_fleets(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role='worker')
        try:
            ghost = str(uuid.uuid4())   # a recovery row whose session record does not exist
            host.store.put('recoveries', ghost, {'recovery_id': 'r-ghost', 'execution_id': 'cc-' + ghost,
                                                 'status': 'scheduled', 'due_at': 0}, 'r-ghost')
            await host.supervisor.tick()
            assert not session.infrastructure_error and session.record['state'] == 'ready'
            assert host.supervision_error is None
            health = await host.dispatch({'method': 'health', 'params': {}})
            assert [(e['phase'], e['error']) for e in health['supervision_attention']['cc-' + ghost]] == \
                [('recovery', 'ValueError')]
            await finished(host, session, 'still takes input')
            host.store.put('recoveries', ghost, {'recovery_id': 'r-ghost', 'execution_id': 'cc-' + ghost,
                                                 'status': 'cancelled', 'due_at': 0}, 'r-ghost')
            await host.supervisor.tick()
            assert not host.supervisor.attention     # its next clean pass clears it
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_an_unattributable_tick_failure_is_a_host_marker_that_clears_itself(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role='worker')
        records = host.store.records
        broken = [True]

        def flaky(table, sid=None):
            if table == 'recoveries' and broken[0]:
                raise sqlite3.OperationalError('fixture: the journal is unreadable')
            return records(table, sid)
        host.store.records = flaky
        loop = asyncio.create_task(host.supervisor.run())
        try:
            await until(lambda: host.supervision_error == 'OperationalError')
            assert not session.infrastructure_error and session.record['state'] == 'ready'
            assert 'durability' not in '; '.join(settlement_blockers(session.record, automatic=False))
            broken[0] = False
            await until(lambda: host.supervision_error is None)
            assert host.last_supervision_error['error'] == 'OperationalError'
            assert host.last_supervision_error['cleared_at'] is not None
        finally:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
            host.store.records = records
            await cleanup(host)
    asyncio.run(scenario())


# ------------------------------------------------------------------------------------ 2 · durability failure

def test_a_cleared_durability_failure_resumes_retention_with_the_gap_journaled(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role='worker')
        try:
            session.failed_storage(OSError('fixture: disk full'))
            assert session.record['state'] == 'infrastructure_error'
            assert any('durability' in r for r in settlement_blockers(session.record, automatic=False))
            with pytest.raises(RuntimeError):
                await session.send(str(uuid.uuid4()), 'refused', 'refused')
            # Output arriving while storage fails is drained and counted, never retained.
            session.process.stdin.write(b'{"type":"control_request","request_id":"gap-probe",'
                                        b'"request":{"subtype":"get_settings"}}\n')
            await session.process.stdin.drain()
            await until(lambda: session.lost_bytes['stdout'] > 0)
            row = await host.dispatch({'method': 'clear_infrastructure_error', 'params': {
                'execution_id': session.record['execution_id'], 'disposition': 'freed disk space; checked'}})
            assert row['cleared'] and row['state'] == 'ready' and not row.get('infrastructure_error')
            assert row['retention_gap']['lost_bytes']['stdout'] > 0 and row['protocol_attention']
            assert not any('durability' in r for r in settlement_blockers(session.record, automatic=False))
            assert incidents(host, 'infrastructure_error_cleared')
            await finished(host, session, 'after the gap')   # the stdout parser resumed at a frame boundary
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_a_resync_mid_frame_retains_the_broken_rest_and_parses_on(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role='worker')
        try:
            session.stdout_resync = 'fragment'
            mid = str(uuid.uuid4())
            await session.send(mid, 'a turn after a mid-frame gap', 'x')
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            kinds = [row['kind'] for row in host.store.db.execute('SELECT kind FROM events WHERE sid=?', (session.sid,))]
            assert 'raw_resync_fragment' in kinds
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_a_stored_records_durability_marker_is_cleared_and_journaled(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role='worker')
        try:
            await host.dispatch({'method': 'close_input', 'params': {'execution_id': session.sid, 'generation': 1}})
            await until(lambda: session.record.get('owner') == 'gone')
            assert session.sid not in host.sessions  # released at its journaled exit: a stored record now
            record = host.store.get('sessions', session.sid)
            record.update(infrastructure_error='OSError', incident_unpersisted=True)
            host.store.put('sessions', session.sid, record)
            assert oc_claude.seat_phase({**record, 'last_input_uuid': 'm'})[0] == 'unknown'
            row = host.clear_infrastructure_error(session.sid, 'the old generation lost bytes; reviewed')
            assert row['cleared'] and not row.get('infrastructure_error') and not row.get('incident_unpersisted')
            assert row['state'] == 'exited' and row['retention_gap']['infrastructure_error'] == 'OSError'
            assert not host.clear_infrastructure_error(session.sid, 'again')['cleared']
            with pytest.raises(ValueError):
                host.clear_infrastructure_error(session.sid, ' ')
        finally:
            await cleanup(host)
    asyncio.run(scenario())


# ------------------------------------------------------------------------------------ 3 · release_input

def test_the_release_rule_holds_only_while_a_process_may_still_answer(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role='worker')
        try:
            receipt = {'generation': session.generation}
            session.update(native_session_state='running', primary_turn_open=True)
            assert 'still working' in host.input_hold(session.sid, receipt)
            assert host.input_hold(session.sid, {'generation': session.generation - 1}) is None  # an earlier process's
            assert host.input_hold(session.sid, {**receipt, 'withheld': True}) is None           # never written
            session.update(native_session_state='idle', primary_turn_open=False)
            assert host.input_hold(session.sid, receipt) is None         # idle, turn closed: its result was lost
            session.update(native_queued_turns=1)
            assert host.input_hold(session.sid, receipt)                 # a queued turn may still answer it
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_resolve_recovery_no_longer_refuses_once_the_owner_is_proved_gone(tmp_path):
    async def scenario():
        logged_in = [True]

        async def login():
            if not logged_in[0]:
                raise RuntimeError('Max-only launch refused: not logged in')
            return await auth()
        host = Host(tmp_path / 'state', tmp_path / 'host.sock', builder, login, clock=lambda: 1000.0)
        try:
            sid = str(uuid.uuid4())
            await host.launch(native_uuid=sid, cwd=str(tmp_path), model='haiku', effort='low',
                              system_prompt='fixture', role='worker')
            session = host.sessions[sid]
            await until(lambda: session.record.get('initialized') and not session.record.get('setting_control_pending'))
            logged_in[0] = False
            mid = str(uuid.uuid4())
            await session.send(mid, 'crash', 'overnight task')
            await until(lambda: session.record.get('owner') == 'gone')
            rid = host.store.records('recoveries')[0]['recovery_id']
            await host.supervisor.tick()
            await until(lambda: host.store.get('recoveries', sid, rid)['status'] == 'blocked')
            assert host.state(sid)['pending_message_uuids'] == [mid]
            recovery = host.supervisor.resolve_recovery(sid, rid, 'inspected in the morning')  # refused before
            assert recovery['operator_disposition'] == 'inspected in the morning'
            assert not host.state(sid).get('pending_recovery') and not host.state(sid).get('recovery_attempt_failed')
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_overnight_crash_failed_login_recovery_then_release_and_resume(tmp_path):
    """The case Dan named: an overnight worker crashes, its automatic recovery fails on an expired login, the login
    is fixed in the morning, and the seat takes its next message without a forced replacement."""
    async def scenario():
        logged_in = [True]

        async def login():
            if not logged_in[0]:
                raise RuntimeError('Max-only launch refused: not logged in')
            return await auth()
        host = Host(tmp_path / 'state', tmp_path / 'host.sock', builder, login, clock=lambda: 1000.0)
        try:
            sid = str(uuid.uuid4())
            await host.launch(native_uuid=sid, cwd=str(tmp_path), model='haiku', effort='low',
                              system_prompt='fixture', role='worker')
            session = host.sessions[sid]
            await until(lambda: session.record.get('initialized') and not session.record.get('setting_control_pending'))
            logged_in[0] = False
            mid = str(uuid.uuid4())
            await session.send(mid, 'crash', 'overnight task')
            await until(lambda: session.record.get('owner') == 'gone')
            rid = host.store.records('recoveries')[0]['recovery_id']
            await host.supervisor.tick()
            await until(lambda: host.store.get('recoveries', sid, rid)['status'] == 'blocked')
            stuck = host.state(sid)
            assert stuck['pending_message_uuids'] == [mid] and stuck['recovery_attempt_failed']
            assert stuck['pending_recovery'] == rid
            logged_in[0] = True                                   # Dan fixes the login in the morning
            answer = await host.dispatch({'method': 'release_input', 'params': {'execution_id': 'cc-' + sid,
                'disposition': 'login fixed; the socket shows the task unfinished, the follow-up redoes it'}})
            assert answer['released'] == [mid] and not answer['held'] and answer['message_replayed'] is False
            assert answer['recovery']['recovery_id'] == rid
            failed = [i['incident_id'] for i in incidents(host, 'recovery_failed')]
            assert failed and set(failed) <= set(answer['acknowledged_escalations'])
            record = host.state(sid)
            assert not record['pending_message_uuids'] and not record.get('pending_recovery')
            assert not record.get('recovery_attempt_failed')
            receipt = host.store.get('receipts', sid, mid)
            assert receipt['released_by_operator'] and receipt['status'] == 'uncertain' and not receipt['completed']
            await host.resume_snapshot(sid)
            resumed = host.sessions[sid]
            await until(lambda: resumed.record.get('initialized') and not resumed.record.get('setting_control_pending'))
            assert resumed.generation == 2 and not resumed.pending
            await finished(host, resumed, 'morning follow-up')
            assert incidents(host, 'input_released')
            again = host.supervisor.release_input(sid, 'nothing left')
            assert again['released'] == [] and again['held'] == []
        finally:
            await cleanup(host)
    asyncio.run(scenario())


# ------------------------------------------------------------------------------------ 4 · M1: tolerant startup

def test_a_missing_session_directory_never_stops_the_host(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role='worker')
        sid, root = session.sid, host.root
        mid = await held(session)
        await cleanup(host)                       # the fixture exits with the input unanswered
        shutil.rmtree(root / sid)                 # Dan's "someday" file cleanup
        broken = str(uuid.uuid4())                # and one record too malformed to reconstruct
        reopened = Host(root, tmp_path / 'reopened.sock', builder, auth, clock=lambda: 2000.0)
        reopened.store.put('sessions', broken, {'native_uuid': broken, 'execution_id': 'cc-' + broken,
                                                'generation': None, 'owner': 'host', 'state': 'ready'})
        reopened.store.db.close()
        os.close(reopened.host_lock)
        again = Host(root, tmp_path / 'again.sock', builder, auth, clock=lambda: 2000.0)   # must not raise
        try:
            record = again.state(sid)
            assert record['session_directory_missing'] and record['owner'] == 'gone' and record['state'] == 'exited'
            assert not record.get('pending_recovery')   # nothing can resume it: its scheduled recovery is cancelled
            assert again.state(broken)['reconstruction_error'] == 'TypeError'
            assert again.state(broken)['owner'] == 'gone'
            assert [i for i in incidents(again, 'session_directory_missing') if mid in i['message_uuids']]
            with pytest.raises(ValueError, match='run directory is gone'):
                await again.resume_snapshot(sid)
            answer = again.supervisor.release_input(sid, 'pruned on purpose; nothing to resume')
            assert answer['released'] == [mid] and answer['acknowledged_escalations']
            # The orphan recheck proves an unknown owner gone when its directory has been pruned, too.
            record.update(state='ownership_unknown', owner='unknown', pid=999999,
                          process_witness={'pid': 999999, 'start_ticks': '1', 'boot_id': 'gone'})
            again.store.put('sessions', sid, record)
            again.recheck_orphans()
            assert again.state(sid)['owner'] == 'gone' and not again.supervisor.attention
        finally:
            again.store.db.close()
            os.close(again.host_lock)
    asyncio.run(scenario())


# ------------------------------------------------------------------------------------ 5 · the two-week list

def row(n, *, age_days, **extra):
    now = 1_800_000_000
    return {'execution_id': f'cc-{n:08x}', 'native_uuid': f'{n:08x}', 'state': 'exited', 'owner': 'gone',
            'last_activity_at': now - age_days * 86400, **extra}


def test_the_list_answers_two_weeks_plus_live_or_busy_and_widens_on_request():
    now = 1_800_000_000
    rows = [row(1, age_days=1), row(2, age_days=30), row(3, age_days=30, state='ready', owner='host'),
            row(4, age_days=30, pending_message_uuids=['m']), row(5, age_days=30, pending_recovery='r'),
            {'execution_id': 'cc-00000006', 'native_uuid': '00000006', 'state': 'exited', 'owner': 'gone'},
            row(7, age_days=30, state='ownership_unknown', owner='unknown')]
    ids = lambda got: [r['execution_id'][-1] for r in got]
    assert LIST_WINDOW_DAYS == 14
    assert ids(list_view(rows, {}, now=now)) == ['1', '3', '4', '5', '6', '7']   # the old dead record (2) is left out
    assert ids(list_view(rows, {'all': True}, now=now)) == ['1', '2', '3', '4', '5', '6', '7']
    assert ids(list_view(rows, {'window_days': 60}, now=now)) == ['1', '2', '3', '4', '5', '6', '7']
    assert ids(list_view(rows, {'window_days': 0.5}, now=now)) == ['3', '4', '5', '6', '7']
    assert ids(list_view(rows, {'execution_ids': ['cc-00000002']}, now=now)) == ['2']   # asked by id: any age
    assert ids(list_view(rows, {'window_days': 'soon', 'all': 'yes'}, now=now)) == ids(list_view(rows, {}, now=now))


def test_the_running_host_lists_the_window_and_state_answers_any_age(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path, role='worker')
        try:
            await host.dispatch({'method': 'close_input', 'params': {'execution_id': session.sid, 'generation': 1}})
            await until(lambda: session.record.get('owner') == 'gone')
            eid = session.record['execution_id']
            assert eid in [r['execution_id'] for r in await host.dispatch({'method': 'list', 'params': {}})]
            assert session.sid not in host.sessions  # ended and released: list reads its stored record
            stored = host.store.get('sessions', session.sid)
            stored['first_launched_at'] = stored['last_input_at'] = stored['context_generation_started_at'] = None
            host.store.put('sessions', session.sid, stored)
            clock[0] += (LIST_WINDOW_DAYS + 1) * 86400
            assert eid not in [r['execution_id'] for r in await host.dispatch({'method': 'list', 'params': {}})]
            assert eid in [r['execution_id'] for r in await host.dispatch({'method': 'list', 'params': {'all': True}})]
            assert (await host.dispatch({'method': 'state', 'params': {'execution_id': eid}}))['owner'] == 'gone'
            health = await host.dispatch({'method': 'health', 'params': {}})
            assert health['list_window_days'] == LIST_WINDOW_DAYS
            assert {'release_input', 'clear_infrastructure_error'} <= set(health['implemented'])
        finally:
            await cleanup(host)
    asyncio.run(scenario())


# ------------------------------------------------------------------------------------ 6 · the scripts, end to end

class LoginHost:
    """A real Host on a private socket and state root with a login that can expire, in a thread."""

    def __init__(self, root):
        self.root, self.logged_in = root, True
        self.loop = asyncio.new_event_loop()
        self.started = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        assert self.started.wait(30)
        t0 = time.time()
        while not self.host.socket_path.exists():
            assert time.time() - t0 < 30
            time.sleep(0.01)

    async def login(self):
        if not self.logged_in:
            raise RuntimeError('Max-only launch refused: not logged in')
        return await auth()

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.host = Host(self.root / 'state', self.root / 'h.sock', launch_builder=builder, auth_check=self.login)
        self.serving = self.loop.create_task(self.host.serve())
        self.started.set()
        self.loop.run_forever()

    def stop(self):
        from test_claude_headless_host import teardown
        asyncio.run_coroutine_threadsafe(teardown(self.host, self.serving), self.loop).result(60)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)


def overnight(host, lh, tmp_path, socket, out):
    """A seat that did its first job, then crashed overnight while the login had expired: its one automatic
    recovery is blocked. → its cc- id."""
    res = oc_claude.start(model='claude-haiku-5-5', effort='low', system_prompt='fixture', prompt='first job',
                          role='worker', cwd=str(tmp_path), socket=socket, program='[ap:PROG]',
                          agent='quick-opus55-high', labels={'title': 't', 'kind': 'seat'}, host=host,
                          settings=oc_backend.load_settings(config={}, environ={}), out=out, poll=0.02)
    eid = res['execution_id']
    assert oc_claude.wait(eid, max_s=30, host=host, out=out, poll=0.05) == 0
    lh.logged_in = False
    record = host.call('state', execution_id=eid)
    oc_claude.deliver(host, eid, record['generation'], 'crash', out=out, poll=0.02)
    t0 = time.time()
    while True:
        record = host.call('state', execution_id=eid)
        rec = next((r for r in host.call('recoveries', execution_id=eid) or [] if r.get('status') == 'blocked'), None)
        if rec and record.get('owner') == 'gone':
            break
        assert time.time() - t0 < 30
        time.sleep(0.05)
    assert record['pending_message_uuids'] and record['pending_recovery'] == rec['recovery_id']
    assert oc_claude.wait(eid, max_s=1, host=host, out=out, poll=0.05) == oc_backend.EXIT_ATTENTION
    return eid, rec['recovery_id']


def test_the_morning_after_follow_up_cancel_and_resolve_all_work(tmp_path):
    lh = LoginHost(tmp_path)
    try:
        host = oc_backend.connect(lh.host.socket_path)
        assert oc_claude.host_releases_inputs(host)
        lines = []
        kw = dict(host=host, settings=oc_backend.load_settings(config={}, environ={}), out=lines.append, poll=0.02)

        # follow-up: releases the input its dead process never answered, resumes the same conversation, sends.
        eid, rid = overnight(host, lh, tmp_path, '[ap:SOCK1]', lines.append)
        lh.logged_in = True
        fu = oc_claude.follow_up(eid, 'morning job', **kw)
        assert fu['resumed'] and fu['generation'] == 2
        assert any('released' in line for line in lines)
        # the alert about the failed launch stays open for its own disposition; resolve answers it
        assert oc_claude.wait(eid, max_s=30, host=host, out=lines.append, poll=0.05) == oc_backend.EXIT_ATTENTION
        oc_claude.resolve(eid, note='login fixed', all_escalations=True, host=host, out=lines.append)
        assert oc_claude.wait(eid, max_s=30, host=host, out=lines.append, poll=0.05) == 0
        assert lines[-1] == 'morning job'

        # cancel: the switch to a fallback works on the stuck seat directly.
        eid2, rid2 = overnight(host, lh, tmp_path, '[ap:SOCK2]', lines.append)
        lh.logged_in = True
        res = oc_claude.cancel(eid2, 'switched to the fallback', host=host, out=lines.append)
        assert res['actions'][0].startswith('1 uncertain input(s) released')
        assert any(a.startswith(f'recovery {rid2}') for a in res['actions'])
        assert not host.call('state', execution_id=eid2).get('pending_message_uuids')

        # resolve --recovery: the recovery and the input it covered, in one disposition.
        eid3, rid3 = overnight(host, lh, tmp_path, '[ap:SOCK3]', lines.append)
        lh.logged_in = True
        done = oc_claude.resolve(eid3, note='inspected; redo by hand', recovery=rid3, host=host, out=lines.append)
        assert done[0]['recovery_id'] == rid3
        record = host.call('state', execution_id=eid3)
        assert not record.get('pending_message_uuids') and not record.get('pending_recovery')
        left = oc_claude.resolve(eid3, note='nothing left', release_inputs=True, host=host, out=lines.append)
        assert left[0]['released'] == [] and left[0]['held'] == []
    finally:
        lh.stop()
