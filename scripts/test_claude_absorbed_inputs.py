"""U11: synthetic stores/wire only. No live host, store, transcript, clock-based settlement or inference."""
import asyncio
import copy
import hashlib
import json
import os
from types import SimpleNamespace
import uuid

import pytest

import oc_claude
from claude_headless.host import Host, NativeSession
from claude_headless.restart import settlement_blockers as restart_blockers
from claude_headless.settlement import settlement_blockers
from claude_headless.store import private_dir
from test_claude_headless_host import setup, teardown, until


ORIGIN = {'kind': 'task-notification', 'producer': 'session-task', 'task_id': 'wake'}


def events(host, kind):
    return [e for e in host.store.replay(limit=500)['events'] if e['kind'] == kind]


def frame(session, value):
    data = (json.dumps(value, ensure_ascii=False) + '\n').encode()
    with session.store.batch():
        offset = session.store.append_bytes(session.sid, session.generation, 'stdout', data)
        session.frame(data, offset, len(data))
    return session.record['last_native_cursor']


def result(mid=None, origin=ORIGIN, **extra):
    return {'type': 'result', 'uuid': str(uuid.uuid4()), 'subtype': 'success', 'is_error': False,
            'result': 'finished', 'terminal_reason': 'completed', 'result_index': 1,
            'user_message_uuids': [mid] if mid else [], 'origin': origin, 'queued_turn_count': 0, **extra}


def echo(session, mid):
    return frame(session, {'type': 'user', 'uuid': mid, 'isReplay': True,
                          'message': {'content': session.store.get('receipts', session.sid, mid)['submitted']}})


def accept(session):
    """Invent an exact already-written receipt, not a real Send or a copy of a real record."""
    mid = str(uuid.uuid4())
    submitted, original = 'refined 雪\r\n', 'ORIGINAL\x00\r\n雪'
    raw = (json.dumps({'type': 'user', 'uuid': mid, 'message': {'content': submitted}}) + '\n').encode()
    with session.store.batch():
        offset = session.store.append_bytes(session.sid, session.generation, 'stdin', raw)
        intent = session.event('stdin_intent', {'message_uuid': mid}, stream='stdin', offset=offset, length=len(raw))
        receipt = {'message_uuid': mid, 'execution_id': session.record['execution_id'],
                   'generation': session.generation, 'submitted': submitted, 'original': original,
                   'submitted_sha256': hashlib.sha256(submitted.encode()).hexdigest(),
                   'original_sha256': hashlib.sha256(original.encode()).hexdigest(),
                   'intent_cursor': intent, 'written': True, 'durable': True, 'acknowledged': False,
                   'started': False, 'completed': False, 'status': 'accepted', 'results': []}
        session.store.put('receipts', session.sid, receipt, mid)
        session.pending.add(mid)
        session.update(pending_message_uuids=sorted(session.pending), last_input_uuid=mid)
    return mid


@pytest.fixture
def projection(tmp_path):
    # Real Store/Host methods, no subprocess, serve(), reconstruction of real records or live socket.
    host = Host(tmp_path / 'synthetic', tmp_path / 'unused.sock')
    sid = str(uuid.uuid4())
    private_dir(host.root / sid / 'g000001')
    record = {'native_uuid': sid, 'execution_id': 'cc-' + sid, 'generation': 1, 'pid': 999999999,
              'role': 'Manager', 'owner': 'host', 'state': 'ready', 'initialized': True,
              'native_session_state': 'idle', 'primary_turn_open': False, 'native_queued_turns': 0,
              'native_state_required': True, 'background_level_known': True, 'pending_message_uuids': []}
    session = NativeSession(host, record, SimpleNamespace(returncode=None), None)
    host.sessions[sid] = session
    session.update()
    try:
        yield host, session
    finally:
        host.store.db.close()
        os.close(host.host_lock)


def clear(record):
    assert record['pending_message_uuids'] == []
    assert restart_blockers(record) == []
    assert settlement_blockers(record, automatic=False) == []
    assert oc_claude.busy_reasons(record) == []


@pytest.mark.parametrize('steer', [False, True], ids=['Send', 'steer'])
def test_u11_task_notification_wire_names_and_settles_absorbed_input(tmp_path, steer):
    async def run():
        host, serving, client, row, session = await setup(tmp_path)
        try:
            seed = str(uuid.uuid4())
            text = 'wake-turn-steer' if steer else 'wake-turn'
            await session.send(seed, text, text)
            await until(lambda: host.store.get('receipts', session.sid, seed)['completed'] and
                        session.record.get('native_session_state') == ('running' if steer else 'idle'))
            mid = str(uuid.uuid4())
            text = 'steer-absorbed' if steer else 'absorbed-send'
            accepted = await session.send(mid, text, 'original\r\n雪', steer=steer)
            assert accepted['status'] == 'accepted'
            await until(lambda: host.store.get('receipts', session.sid, mid)['acknowledged'])
            assert mid in session.pending
            assert any('accepted input' in r for r in restart_blockers(session.record))
            assert any('accepted input' in r for r in settlement_blockers(session.record, automatic=False))
            assert 'accepted input awaiting its result' in oc_claude.busy_reasons(session.record)
            # Test fixture only: finish the held tool step without a second user message.
            session.control('get_settings')
            await until(lambda: session.record.get('native_session_state') == 'idle')
            receipt = host.store.get('receipts', session.sid, mid)
            assert receipt['completed'], 'a named task-notification result must complete the absorbed input'
            assert receipt['status'] == 'completed' and receipt['original'] == 'original\r\n雪'
            assert receipt['results'][0]['origin'] == ORIGIN
            assert receipt['results'][0]['user_message_uuids'] == [mid]
            assert not steer or (receipt['queued'] and receipt['delivered'])
            clear(session.record)
            settled = [e for e in events(host, 'input_settled') if e['detail']['message_uuid'] == mid]
            assert len(settled) == 1
            assert settled[0]['detail']['result_cursor'] == receipt['results'][0]['cursor']
            before = host.store.path(session.sid, 1, 'stdin').read_bytes()
            assert await session.send(mid, text, 'original\r\n雪', steer=steer) == receipt
            assert host.store.path(session.sid, 1, 'stdin').read_bytes() == before
            assert sum(json.loads(line).get('uuid') == mid for line in before.splitlines()) == 1
        finally:
            await teardown(host, serving)
    asyncio.run(run())


@pytest.mark.parametrize('origin', [None, 'user', {'kind': 'user-message'}, ORIGIN, {'kind': 'future-origin'}])
def test_u11_named_primary_results_journal_completion_without_changing_user_receipts(projection, origin):
    host, session = projection
    mid = accept(session)
    echo(session, mid)
    old = host.store.get('receipts', session.sid, mid)
    value = result(mid, origin)
    cursor = frame(session, value)
    receipt = host.store.get('receipts', session.sid, mid)
    assert receipt['completed'] and mid not in session.pending
    assert len(events(host, 'input_settled')) == 1
    assert receipt['settlement_evidence']['result_cursor'] == cursor
    for key in ('original', 'submitted', 'original_sha256', 'submitted_sha256', 'acknowledgement_cursor', 'written'):
        assert receipt[key] == old[key]
    frame(session, value)  # exact duplicate result: no second receipt/result/settlement
    assert len(events(host, 'input_settled')) == 1 and len(events(host, 'result_duplicate')) == 1
    assert host.store.get('receipts', session.sid, mid) == receipt
    clear(session.record)


@pytest.mark.parametrize('delivery', ['steer-echo', 'started'])
def test_u11_idle_closed_turn_reports_unnamed_delivered_input_stale_not_completed(projection, delivery):
    host, session = projection
    mid = accept(session)
    if delivery == 'steer-echo':
        receipt = host.store.get('receipts', session.sid, mid)
        receipt['kind'] = 'steer'
        host.store.put('receipts', session.sid, receipt, mid)
        delivery_cursor = echo(session, mid)
    else:
        delivery_cursor = frame(session, {'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'started'})
    # The idle push can arrive on either side of the result. Nothing settles before the result.
    frame(session, {'type': 'system', 'subtype': 'session_state_changed', 'state': 'running'})
    cursor = frame(session, result())
    assert mid in session.pending
    frame(session, {'type': 'system', 'subtype': 'session_state_changed', 'state': 'idle'})
    receipt = host.store.get('receipts', session.sid, mid)
    assert receipt['status'] == 'stale' and not receipt['completed']
    assert receipt['released_as_stale'] and receipt['effects'] == 'uncertain' and receipt['results'] == []
    assert receipt['settlement_evidence']['delivery_cursor'] == delivery_cursor
    assert receipt['settlement_evidence']['result_cursor'] == cursor
    assert len(events(host, 'input_stale')) == 1
    assert oc_claude.seat_phase(session.record, receipt=receipt)[0] == 'attention'  # honest, never busy/done
    clear(session.record)


def test_u11_not_yet_started_send_and_queued_steer_stay_pending_despite_idle_and_old_result(projection):
    host, session = projection
    frame(session, result())  # a PREVIOUS turn's result, not this new input's
    mid = accept(session)
    frame(session, {'type': 'system', 'subtype': 'session_state_changed', 'state': 'idle'})
    assert mid in session.pending and not events(host, 'input_stale')
    frame(session, result())  # even a later unrelated result is insufficient without delivery/start
    assert mid in session.pending and not events(host, 'input_stale')
    frame(session, {'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'queued'})
    frame(session, {'type': 'system', 'subtype': 'session_state_changed', 'state': 'idle'})
    assert mid in session.pending and not events(host, 'input_stale')
    assert 'accepted input awaiting its result' in oc_claude.busy_reasons(session.record)
    echo(session, mid)  # an ordinary Send can be acknowledged before its native turn starts
    frame(session, result(queued_turn_count=None))  # a wake result without any explicit queue level
    assert mid in session.pending and not events(host, 'input_stale')
    # A child result naming it proves nothing either.
    frame(session, result(mid, parent_tool_use_id='child'))
    assert mid in session.pending
    # Once the actual native turn names it, the existing user-origin path is unchanged, now journaled.
    frame(session, result(mid, origin=None))
    clear(session.record)
    assert len(events(host, 'input_settled')) == 1


@pytest.mark.parametrize('hold', [
    {'native_queued_turns': 1}, {'pending_native_requests': {'p': {}}},
    {'native_session_state': 'running'}, {'native_session_state': 'requires_action'},
    {'primary_turn_open': True}, {'native_session_state': 'unknown'},
])
def test_u11_stale_requires_idle_closed_unqueued_request_free_turn(projection, hold):
    host, session = projection
    mid = accept(session)
    echo(session, mid)
    frame(session, {'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'started'})
    session.update(native_session_state='running')
    frame(session, result())
    session.update(native_session_state='idle', **{k: v for k, v in hold.items() if k != 'native_session_state'})
    if 'native_session_state' in hold:
        session.update(**hold)
    # Reconstruct applies the same rule, without calling a new method that cannot exist on the old base.
    host.sessions.pop(session.sid)
    host.reconstruct(copy.deepcopy(session.record))
    assert host.store.get('sessions', session.sid)['pending_message_uuids'] == [mid]
    assert not events(host, 'input_stale')
    stored = host.store.get('sessions', session.sid)
    stored.update(native_session_state='idle', primary_turn_open=False, native_queued_turns=0,
                  pending_native_requests={}, native_wake_pending=False)
    host.reconstruct(stored)
    assert host.store.get('sessions', session.sid)['pending_message_uuids'] == []
    assert host.store.get('receipts', session.sid, mid)['status'] == 'stale'


@pytest.mark.parametrize('named', [True, False], ids=['named-background-completes', 'unnamed-stale-reported'])
def test_u11_reconstruct_repairs_old_receipts_without_replay_or_duplicate_events(projection, named):
    host, session = projection
    mid = accept(session)
    echo(session, mid)
    frame(session, {'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'started'})
    value = result(mid if named else None)
    # Invent the old host's persisted state: raw/index/result evidence, but no settlement.
    raw = (json.dumps(value) + '\n').encode()
    with host.store.batch():
        offset = host.store.append_bytes(session.sid, 1, 'stdout', raw)
        cursor = session.event('native_frame', {'type': 'result', 'uuid': value['uuid']},
                               stream='stdout', offset=offset, length=len(raw), _frame=raw)
        receipt = host.store.get('receipts', session.sid, mid)
        if named:
            receipt['results'] = [{'identity': [value['uuid'], 1, ORIGIN, 1], **value, 'cursor': cursor}]
            host.store.put('receipts', session.sid, receipt, mid)
        session.update(primary_turn_open=False, primary_turn_cursor=cursor, last_result_cursor=cursor)
    stdin = host.store.path(session.sid, 1, 'stdin').read_bytes()
    host.sessions.pop(session.sid)
    host.reconstruct(copy.deepcopy(session.record))
    repaired = host.store.get('receipts', session.sid, mid)
    assert repaired['completed'] if named else repaired['status'] == 'stale'
    assert host.store.get('sessions', session.sid)['pending_message_uuids'] == []
    assert not host.store.records('recoveries')
    kind = 'input_settled' if named else 'input_stale'
    assert len(events(host, kind)) == 1 and events(host, kind)[0]['detail']['result_cursor'] == cursor
    host.reconstruct(host.store.get('sessions', session.sid))
    assert len(events(host, kind)) == 1
    assert host.store.get('receipts', session.sid, mid) == repaired
    assert host.store.path(session.sid, 1, 'stdin').read_bytes() == stdin


@pytest.mark.parametrize('error', [False, True])
def test_u11_background_supervision_and_recovery_classification_are_unchanged(projection, error):
    host, session = projection
    mid = accept(session)
    action_mid = accept(session)
    receipt = host.store.get('receipts', session.sid, action_mid)
    receipt['recovery_id'] = 'existing-recovery'
    host.store.put('receipts', session.sid, receipt, action_mid)
    recovery = {'recovery_id': 'existing-recovery', 'status': 'accepted', 'action_uuid': action_mid,
                'incident_cursor': receipt['intent_cursor'], 'source_message_uuids': [mid]}
    host.store.put('recoveries', session.sid, recovery, 'existing-recovery')
    session.update(native_retry_active=True, interrupt_authorized={'authority': 'fixture'},
                   pending_recovery='existing-recovery')
    frame(session, result(mid, user_message_uuids=[mid, action_mid], is_error=error,
                          result='API Error: 529 INERT' if error else 'done'))
    assert host.store.get('receipts', session.sid, mid)['completed']
    assert not host.store.get('receipts', session.sid, action_mid)['completed']
    assert action_mid in session.pending  # an internal recovery action is not an absorbed human input
    assert host.store.records('recoveries') == [recovery]  # no background recovery_result or new continue
    assert session.record['pending_recovery'] == 'existing-recovery'
    assert session.record['interrupt_authorized'] == {'authority': 'fixture'}
    assert session.record['native_retry_active'] is error  # existing background success/error rule
    incident = [i for i in host.store.incidents(limit=500)['incidents'] if i['category'] == 'capacity_quota']
    assert len(incident) == int(error)
    if error:
        assert incident[0]['handler'] == 'wait_continue_once' and incident[0]['escalation']['status'] == 'pending'
    assert host.store.last_background_result(session.sid)['terminal_error'] is error
    # The same background receipt must not make startup pronounce the recovery completed either.
    host.sessions.pop(session.sid)
    host.reconstruct(copy.deepcopy(session.record))
    assert host.store.get('recoveries', session.sid, 'existing-recovery')['status'] == 'blocked'
    assert not host.store.get('receipts', session.sid, action_mid)['completed']


def test_u11_settlement_receipt_event_and_pending_rollback_with_native_read(projection, monkeypatch):
    host, session = projection
    mid = accept(session)
    old_receipt = host.store.get('receipts', session.sid, mid)
    put = host.store.put

    def fail_after_settlement(table, sid, body, key=None):
        if table == 'receipts' and body.get('settlement_cursor'):
            raise OSError('synthetic receipt storage failure after settlement event')
        return put(table, sid, body, key)

    with monkeypatch.context() as patch:
        patch.setattr(host.store, 'put', fail_after_settlement)
        with pytest.raises(OSError, match='synthetic receipt storage failure'):
            frame(session, result(mid))
    session.restore_committed()  # the stdout drain's ordinary after-rollback recovery, not a new repair path
    assert host.store.get('receipts', session.sid, mid) == old_receipt
    assert mid in session.pending and not events(host, 'input_settled')
    assert not events(host, 'native_frame')
    assert not host.store.db.execute('SELECT 1 FROM results WHERE sid=?', (session.sid,)).fetchall()
    # Retained bytes are still exact raw; next indexed cursor does not reuse any rolled-back cursor.
    assert b'task-notification' in host.store.path(session.sid, 1, 'stdout').read_bytes()
    frame(session, result(mid))
    assert host.store.get('receipts', session.sid, mid)['completed']
    assert len(events(host, 'input_settled')) == 1


@pytest.mark.parametrize('origin', [None, ORIGIN], ids=['user-origin-compatibility', 'absorbed-background'])
def test_u11_late_named_result_completes_even_an_already_released_receipt(projection, origin):
    host, session = projection
    mid = accept(session)
    receipt = host.store.get('receipts', session.sid, mid)
    receipt.update(released_by_operator=True, status='uncertain')
    host.store.put('receipts', session.sid, receipt, mid)
    session.pending.clear()
    session.update(pending_message_uuids=[])
    cursor = frame(session, result(mid, origin))
    receipt = host.store.get('receipts', session.sid, mid)
    assert receipt['completed'] and receipt['released_by_operator']  # keep the earlier disposition evidence
    assert len(events(host, 'input_settled')) == 1
    assert receipt['settlement_evidence']['result_cursor'] == cursor


def test_u11_old_generation_delivery_never_proves_a_new_inputs_turn_ended(projection):
    host, session = projection
    mid = accept(session)
    echo(session, mid)
    receipt = host.store.get('receipts', session.sid, mid)
    receipt['generation'] = 2  # the echo and later result still belong to generation 1
    host.store.put('receipts', session.sid, receipt, mid)
    frame(session, result())
    assert mid in session.pending and not events(host, 'input_stale')
    host.sessions.pop(session.sid)
    host.reconstruct(copy.deepcopy(session.record))
    assert host.store.get('sessions', session.sid)['pending_message_uuids'] == [mid]
    assert not events(host, 'input_stale')
    # The generation-1 receipt can be settled by generation-1 explicit evidence, not by an idle guess.
    receipt['generation'] = 1
    host.store.put('receipts', session.sid, receipt, mid)
    host.sessions[session.sid] = session
    frame(session, result(mid, user_message_uuids=None, user_message_uuid=mid))
    clear(session.record)
    assert len(events(host, 'input_settled')) == 1


@pytest.mark.parametrize('origin', [None, ORIGIN])
def test_u11_explicitly_named_old_input_can_complete_in_a_later_generation(projection, origin):
    host, session = projection
    mid = accept(session)
    session.generation = 2
    private_dir(host.root / session.sid / 'g000002')
    session.update(generation=2)
    cursor = frame(session, result(mid, origin))
    receipt = host.store.get('receipts', session.sid, mid)
    assert receipt['completed'] and receipt['generation'] == 1
    assert len(events(host, 'input_settled')) == 1
    assert receipt['settlement_evidence']['result_cursor'] == cursor
    clear(session.record)


def test_u11_due_wake_turn_prevents_unnamed_stale_release(projection):
    host, session = projection
    mid = accept(session)
    frame(session, {'type': 'command_lifecycle', 'command_uuid': mid, 'state': 'started'})
    session.update(native_session_state='running')
    frame(session, result())
    session.update(native_wake_pending=True)
    frame(session, {'type': 'system', 'subtype': 'session_state_changed', 'state': 'idle'})
    assert mid in session.pending and not events(host, 'input_stale')
    frame(session, result(mid))
    assert host.store.get('receipts', session.sid, mid)['completed']
    assert len(events(host, 'input_settled')) == 1
    clear(session.record)
