"""Guard sweep (Manager-Session-182 follow-ups): no hold may outlive what it protects.

The 182 lockout: a stale ``protocol_attention`` on a proved-dead record made ``reconnect``
refuse forever while the pane kept offering it. These tests pin the one shared reconnect rule,
the logged release of dead-generation markers, the Manager Send that is no longer refused for
its own escalations or a merely scheduled recovery, and the claims that stay real (a live or
unknown owner, a recovery already in flight). Inert CLI fixture only; no inference.
"""
import asyncio
import json
import uuid

import pytest

from claude_headless.settlement import (DEAD_GENERATION_HOLDS, RECONNECT_POLICY, reconnect_blockers,
                                        settlement_advisories, settlement_blockers)
from claude_headless.collection import collect_session
from test_claude_lifecycle import setup
from test_claude_headless_host import teardown, until


def dead_manager(**fields):
    record = {'role': 'Manager', 'state': 'exited', 'owner': 'gone', 'pid': 4242,
              'process_witness': {'pid': 4242, 'start_ticks': '1', 'boot_id': 'b'},
              'execution_id': 'cc-x', 'native_uuid': 'x', 'generation': 2}
    record.update(fields)
    return record


STALE = {'protocol_attention': True, 'settings_mismatch': True, 'setting_control_pending': True,
         'infrastructure_error': 'OperationalError', 'pending_escalations': ['old-incident'],
         'pending_message_uuids': ['11111111-1111-4111-8111-111111111111']}


def test_one_reconnect_rule_releases_dead_generation_markers_and_names_real_holds():
    assert reconnect_blockers(dead_manager(**STALE), policy=RECONNECT_POLICY) == []
    assert set(DEAD_GENERATION_HOLDS) <= set(STALE) | {'incident_unpersisted'}
    # What stays real, each naming what Dan can do next.
    live = reconnect_blockers(dead_manager(state='ready', owner='host'), policy=2)
    assert live and 'not proved gone' in live[0]
    unknown = reconnect_blockers(dead_manager(state='ownership_unknown', owner='unknown'), policy=2)
    assert 'New Manager' in unknown[0]
    assert 'New Manager' in reconnect_blockers(dead_manager(process_witness=None), policy=2)[0]
    assert 'still running' in reconnect_blockers(dead_manager(), policy=2, witness_alive=True)[0]
    claimed = reconnect_blockers(dead_manager(pending_recovery='r'), policy=2, recovery_status='claimed')
    assert 'recovery' in claimed[0]
    assert reconnect_blockers(dead_manager(pending_recovery='r'), policy=2, recovery_status='scheduled') == []
    assert 'only a registered Manager' in reconnect_blockers(dead_manager(role='worker'), policy=2)[0]


def test_an_older_host_record_gets_that_hosts_rule_so_the_pane_never_offers_what_it_refuses():
    record = dead_manager(protocol_attention=True)            # no reconnect_policy stamp: older host
    reason = reconnect_blockers(record)
    assert reason and 'protocol_attention' in reason[0] and 'New Manager' in reason[0]
    assert reconnect_blockers({**record, 'reconnect_policy': RECONNECT_POLICY}) == []


def test_operator_close_and_restart_hold_only_on_work_a_close_could_lose():
    record = {'protocol_attention': True, 'pending_escalations': ['i'], 'settings_mismatch': True,
              'setting_control_pending': True, 'financial_route_attention': True,
              'pending_control_requests': ['c']}
    assert settlement_blockers(record, require_native=False, automatic=False) == []
    assert len(settlement_blockers(record, require_native=False)) == 5           # review alone never pins a process
    assert len(settlement_advisories(record)) == 6                                # and the operator is told
    for hold in ({'pending_message_uuids': ['m']}, {'background_tasks': {'t': {}}}, {'native_retry_active': True},
                 {'pending_native_requests': {'p': {}}}, {'native_queued_turns': 1}, {'pending_recovery': 'r'}):
        assert settlement_blockers(hold, require_native=False, automatic=False), hold


def test_a_never_tasked_manager_does_not_hold_a_guarded_restart_on_an_unknown_task_level():
    # 2.1.299 reports no task level on a process that never ran a task; a fresh Manager
    # refused every restart until the collector's readback happened to seed it.
    fresh = {'native_state_required': True, 'native_session_state': 'idle', 'state': 'ready',
             'owner': 'host', 'background_level_known': False, 'background_tasks': {},
             'background_unsettled': False, 'background_level_cursor': None, 'last_subagent_cursor': None}
    assert settlement_blockers(fresh, automatic=False) == []
    assert settlement_blockers(fresh) == ['native background task level is unknown']   # the collector still seeds
    assert settlement_advisories({**fresh, 'native_state_required': False}) == []
    for evidence in ({'last_subagent_cursor': 'c:1'}, {'background_level_cursor': 'c:2'},
                     {'protocol_attention': True}):
        assert settlement_blockers({**fresh, **evidence}, automatic=False) == [
            'native background task level is unknown'], evidence


def test_the_182_lockout_reconnects_once_logs_the_release_and_never_replays(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            clock.value += 11
            assert await collect_session(host, session)
            await until(lambda: session.record.get('owner') == 'gone')
            # What 182's dead record carried, plus an input that never got its result.
            mid = str(uuid.uuid4())
            host.store.put('receipts', session.sid, {'execution_id': 'cc-' + session.sid, 'generation': 1,
                'message_uuid': mid, 'original': 'lost?', 'submitted': 'lost?', 'status': 'uncertain',
                'durable': True, 'completed': False, 'results': []}, mid)
            session.update(protocol_attention=True, settings_mismatch=True, pending_escalations=['old-incident'],
                           pending_message_uuids=[mid])
            row = await host.reconnect(execution_id=session.sid, generation=1)
            assert row['generation'] == 2
            replacement = host.sessions[session.sid]
            await until(lambda: replacement.record['state'] == 'ready' and
                not replacement.record.get('setting_control_pending') and replacement.record['native_session_state'] == 'idle')
            assert replacement.record['pending_message_uuids'] == []        # a new process can never answer it
            assert not replacement.record.get('protocol_attention')
            receipt = host.store.get('receipts', session.sid, mid)
            assert receipt['status'] == 'uncertain' and receipt['released_by_reconnect']['from_generation'] == 1
            incidents = [i for i in host.store.incidents(limit=500)['incidents']
                         if i['category'] == 'dead_generation_holds_released']
            assert len(incidents) == 1
            assert incidents[0]['detail']['released'] == {'protocol_attention': True, 'settings_mismatch': True}
            assert incidents[0]['detail']['uncertain_message_uuids'] == [mid]
            assert incidents[0]['detail']['message_replayed'] is False
            new = str(uuid.uuid4())
            sent = await replacement.send(new, 'my next message', 'my next message')
            assert sent['escalations_open_at_send'] == ['old-incident']      # shown, never a refusal
            await until(lambda: host.store.get('receipts', session.sid, new)['completed'])
            writes = host.store.path(session.sid, 2, 'stdin').read_text().splitlines()
            users = [json.loads(line) for line in writes if json.loads(line).get('type') == 'user']
            assert [u['uuid'] for u in users] == [new]                       # the uncertain one never replayed
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_a_scheduled_recovery_yields_to_dans_send_but_an_in_flight_one_does_not(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            rid = 'recovery-fixture'
            host.store.put('recoveries', session.sid, {'recovery_id': rid, 'execution_id': 'cc-' + session.sid,
                'status': 'claimed', 'source_message_uuids': [], 'incident_cursor': None,
                'due_at': 10 ** 12, 'submitted': 'continue'}, rid)
            session.update(pending_recovery=rid)
            with pytest.raises(RuntimeError, match='recovery is sending'):
                await session.send(str(uuid.uuid4()), 'x', 'x')
            recovery = host.store.get('recoveries', session.sid, rid)
            recovery['status'] = 'scheduled'
            host.store.put('recoveries', session.sid, recovery, rid)
            mid = str(uuid.uuid4())
            receipt = await session.send(mid, 'Dan instead', 'Dan instead')
            assert receipt['superseded_recovery'] == rid
            assert host.store.get('recoveries', session.sid, rid)['status'] == 'cancelled'
            assert session.record['pending_recovery'] is None
            assert any(i['category'] == 'recovery_superseded'
                       for i in host.store.incidents(limit=500)['incidents'])
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_reconnect_still_refuses_while_a_recovery_is_reconnecting(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            clock.value += 11
            assert await collect_session(host, session)
            await until(lambda: session.record.get('owner') == 'gone')
            rid = 'recovery-claimed'
            host.store.put('recoveries', session.sid, {'recovery_id': rid, 'execution_id': 'cc-' + session.sid,
                'status': 'claimed', 'source_message_uuids': []}, rid)
            session.update(pending_recovery=rid)
            with pytest.raises(RuntimeError, match='automatic crash recovery'):
                await host.reconnect(execution_id=session.sid, generation=1)
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_host_records_carry_the_reconnect_rule_version(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            assert host.state(session.sid)['reconnect_policy'] == RECONNECT_POLICY
            rows = await host.dispatch({'method': 'list', 'params': {}})
            assert all(row['reconnect_policy'] == RECONNECT_POLICY for row in rows)
        finally:
            await teardown(host, serving)
    asyncio.run(run())
