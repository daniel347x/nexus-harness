"""The false doorbell ([ap:AUW2ED]): a background task that ends while a session's own turn is closed wakes it, and
the host's `native_wake_pending` marker keeps that gap from reading as done. No native inference: a real Host with
the inert fixture CLI, fed the frame order observed live on Claude Code (Conductor cc-f89e1832, 2026-10-16)."""
import asyncio

import oc_claude
from claude_headless.settlement import settlement_blockers
from claude_headless.supervision import clear_stale_native_markers
from test_claude_headless_supervision import setup, cleanup, frame, held

RUN = 'run-1'


def _state(session, value):
    frame(session, {'type': 'system', 'subtype': 'session_state_changed', 'state': value})


def _phase(session, mid):
    receipt = session.store.get('receipts', session.sid, mid)
    return oc_claude.seat_phase(dict(session.record, last_input_uuid=mid), receipt=receipt)[0]


def _first_turn_with_background_wait(session, mid):
    """The Conductor's turn: it starts `oc_seat.sh wait <worker>` in the background, then ends."""
    session.update(native_state_required=True)
    frame(session, {'type': 'system', 'subtype': 'init'})
    _state(session, 'running')
    frame(session, {'type': 'system', 'subtype': 'background_tasks_changed',
                    'tasks': [{'task_id': 'wait', 'run_id': RUN, 'task_type': 'local_bash'}]})
    frame(session, {'type': 'system', 'subtype': 'task_started', 'task_id': 'wait', 'run_id': RUN,
                    'is_backgrounded': True, 'description': 'Wait in background for the worker'})
    frame(session, {'type': 'result', 'uuid': 'r1', 'subtype': 'success', 'is_error': False,
                    'user_message_uuid': mid, 'result': 'I set a background wait on the worker.'})
    _state(session, 'idle')


def test_the_gap_before_a_wake_up_turn_never_reads_done(tmp_path):
    async def scenario():
        host, session, _ = await setup(tmp_path, role='conductor')
        try:
            mid = await held(session)
            _first_turn_with_background_wait(session, mid)
            assert _phase(session, mid) == 'running'                   # 1 background task
            # Observed order when the background wait ends while idle; every step must stay "running".
            frame(session, {'type': 'system', 'subtype': 'task_updated', 'task_id': 'wait', 'run_id': RUN,
                            'patch': {'status': 'completed', 'end_time': 1}})
            assert session.record['native_wake_pending'] is True
            assert _phase(session, mid) == 'running'
            frame(session, {'type': 'system', 'subtype': 'task_notification', 'task_id': 'wait', 'run_id': RUN,
                            'status': 'completed'})
            frame(session, {'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': []})
            # The 70 ms window that rang the doorbell early: level empty, native idle, receipt completed.
            assert session.record['background_tasks'] == {} and session.record['native_session_state'] == 'idle'
            phase, reasons = oc_claude.seat_phase(dict(session.record, last_input_uuid=mid),
                                                  receipt=session.store.get('receipts', session.sid, mid))
            assert phase == 'running' and reasons == ['a background task ended; its wake-up turn is due']
            assert 'a background task ended; its wake-up turn is due' in settlement_blockers(session.record)
            _state(session, 'running')
            frame(session, {'type': 'system', 'subtype': 'init'})
            assert session.record['native_wake_pending'] is False
            assert _phase(session, mid) == 'running'                   # the wake turn itself
            frame(session, {'type': 'result', 'uuid': 'r2', 'subtype': 'success', 'is_error': False,
                            'origin': {'kind': 'task-notification'}, 'result': 'done.'})
            _state(session, 'idle')
            assert _phase(session, mid) == 'done'
            # The wake-up's result is a row of its own; the host's `state` answer carries the newest one.
            state = await host.dispatch({'method': 'state', 'params': {'execution_id': session.record['execution_id']}})
            assert 'synthetic_results' not in state
            assert oc_claude.wake_result(state)['result'] == 'done.'
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_only_a_notifying_end_with_the_turn_closed_sets_it(tmp_path):
    async def scenario():
        host, session, _ = await setup(tmp_path, role='conductor')
        try:
            mid = await held(session)
            _first_turn_with_background_wait(session, mid)
            # A deliberate stop queues no notification: no wake-up is due.
            frame(session, {'type': 'system', 'subtype': 'task_updated', 'task_id': 'wait', 'run_id': RUN,
                            'patch': {'status': 'killed'}})
            frame(session, {'type': 'system', 'subtype': 'task_notification', 'task_id': 'wait', 'run_id': RUN,
                            'status': 'stopped'})
            assert not session.record.get('native_wake_pending')
            # A child's (subagent's) task ending is not the session's own wake-up.
            frame(session, {'type': 'system', 'subtype': 'task_notification', 'task_id': 'child', 'run_id': 'c',
                            'status': 'completed', 'parent_tool_use_id': 'agent-call'})
            assert not session.record.get('native_wake_pending')
            assert _phase(session, mid) == 'done'
            # Inside an open turn the notification is consumed by that turn (or reported as a queued turn).
            frame(session, {'type': 'system', 'subtype': 'init'})
            _state(session, 'running')
            frame(session, {'type': 'system', 'subtype': 'task_notification', 'task_id': 'other', 'run_id': 'o',
                            'status': 'completed'})
            assert not session.record.get('native_wake_pending')
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_a_dead_process_drops_the_marker_and_an_operator_close_is_never_held_by_it():
    record = {'native_wake_pending': True, 'background_tasks': {}, 'background_unsettled': False}
    assert clear_stale_native_markers(record) == {'native_wake_pending': True}
    assert record['native_wake_pending'] is False
    held_record = {'native_wake_pending': True}
    assert 'a background task ended; its wake-up turn is due' in settlement_blockers(held_record, require_native=False)
    assert 'a background task ended; its wake-up turn is due' not in settlement_blockers(
        held_record, require_native=False, automatic=False)
