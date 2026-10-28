"""Offline effect/identity/receipt tests; native semantics require the separate real-CLI proof."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from claude_headless import observations as obs
from claude_headless.controls import dispatch_control
from claude_headless.host import witness
from claude_headless.store import Store, private_dir


@pytest.mark.parametrize('command,kind', [
    ('sleep 7190; date -u', 'timer'),
    ('sleep 1h30m', 'timer'),
    ('timeout 60 bash /repo/oc_seat.sh wait ses_worker', None),
    (f'bash {obs.REPO}/scripts/oc_seat.sh wait ses_worker --max 3000', 'session_doorbell'),
    (f'bash {obs.REPO}/scripts/oc.sh seat wait cc-{uuid.uuid4()} --max 3000', 'session_doorbell'),
    (f'bash {obs.REPO}/scripts/oc.sh claude wait cc-{uuid.uuid4()} --max 540', 'session_doorbell'),
    ('oc_seat.py wait --socket "[ap:SOCKET]" --max 540 --keep-others', 'socket_doorbell'),
    ('python3 -u oc_timer.py 30m --label "quota retry"', 'timer'),
    ('bash -lc "sleep 60; date -u"', 'timer'),
    ('while true; do oc.sh seat info ses_worker; sleep 5; done', 'read_only_poll'),
    ('while true; do oc.sh usage --account work --json; sleep 60; done', 'read_only_poll'),
    ('cd /a/repo && oc_seat.sh wait ses_worker --max 540', 'session_doorbell'),
    ('sleep 1; printf "woke up"', 'timer'),
    ('sleep 1; echo done', 'timer'),
])
def test_current_and_legacy_full_effect_grammar(command, kind):
    result = obs.recognize(command)
    assert result['detachable'] is (kind is not None), result
    if kind:
        assert result['kind'] == kind
        assert result['obligations']


@pytest.mark.parametrize('command', [
    'sleep 60; touch result', 'sleep 60 && pytest tests', 'sleep 60; git commit -am done',
    'sleep 60; echo written > output', 'timeout 60 pytest test_x.py',
    'sleep 60; ' + ' ' * 1200 + 'python3 write.py',
    'echo "sleep 7190"', 'echo "oc_seat.sh wait ses_worker"',
    'oc.sh seat cancel ses_worker', 'oc.sh seat resume ses_worker', 'oc.sh usage --buy-credits',
    'eval "sleep 60"', 'sleep $(touch output)', 'sleep 60 &', 'sleep 1 | tee output',
    'while true; do sleep 5; touch output; done', 'bash -lc "sleep 1; touch output"',
    'env PATH=/evil sleep 60', '/evil/sleep 60', '/evil/oc_seat.sh wait ses_worker',
    'oc_seat.py wait ses_worker --unknown', 'sleep -1', 'sleep inf',
])
def test_real_work_and_unknown_effects_are_not_disposable(command):
    assert obs.recognize(command)['detachable'] is False


def fixture(tmp_path, monkeypatch, *, command='sleep 7190; date -u', role='conductor', parent=None, program='[ap:PROG]'):
    import os
    store = Store(tmp_path / 'state')
    sid = str(uuid.uuid4())
    private_dir(store.root / sid)
    private_dir(store.root / sid / 'g000001')
    task = {'task_id': 'task-one', 'run_id': 'run-one', 'tool_use_id': 'tool-one',
            'task_type': 'local_bash', 'started_at': 100, 'description': 'Not classification authority',
            'tool_input': {'command': command[:1024]}}
    record = {'execution_id': 'cc-' + sid, 'native_uuid': sid, 'generation': 1, 'owner': 'host',
              'state': 'ready', 'primary_turn_open': False, 'native_session_state': 'idle',
              'pid': os.getpid(), 'process_witness': witness(os.getpid()), 'role': role,
              'parent': parent, 'program': program, 'background_tasks': {'task-one': task},
              'background_level_known': True}
    store.put('sessions', sid, record)
    data = json.dumps({'type': 'assistant', 'uuid': 'assistant-one', 'message': {'content': [
        {'type': 'tool_use', 'id': 'tool-one', 'name': 'Bash', 'input': {'command': command,
            'run_in_background': True}}]}}).encode() + b'\n'
    offset = store.append_bytes(sid, 1, 'stdout', data)
    store.event(sid, 1, 'native_frame', stream='stdout', offset=offset, length=len(data))
    host = SimpleNamespace(store=store, sessions={})
    session = SimpleNamespace(host=host, store=store, sid=sid, generation=1, record=record,
                              process=SimpleNamespace(pid=os.getpid(), returncode=None), command_lock=asyncio.Lock(),
                              control_tasks=set(), infrastructure_error=None, stdout_at_boundary=True)
    host.sessions[sid] = session
    def update(**values):
        record.update(values)
        store.put('sessions', sid, record)
    session.update = update
    host.session = lambda execution_id, generation: session if (execution_id == record['execution_id'] and generation == 1) else None
    ledger = host.observations = obs.ObservationLedger(host)
    proof = {'pid': 123456789, 'start_ticks': 'fixture', 'boot_id': 'fixture'}
    monkeypatch.setattr(obs, 'task_processes', lambda *args: ([{'witness': proof, 'argv': ['sleep', '7190']}], None))
    import claude_headless.host as host_module
    original = host_module.witness
    state = {'present': True, 'controls': []}
    monkeypatch.setattr(host_module, 'witness', lambda pid: proof if pid == proof['pid'] and state['present']
                        else None if pid == proof['pid'] or (pid == record['pid'] and record.get('owner') == 'gone') else original(pid))
    def control(subtype, request, request_id):
        state['controls'].append((subtype, request, request_id))
        row = ledger.get(obs.identity('claude', record['execution_id'], 1, 'task-one', 'run-one'))
        assert row['status'] == 'stop_intended'  # durable before native write
        receipt = {'request_id': request_id, 'status': 'pending', 'subtype': subtype, 'generation': 1}
        store.put('controls', sid, receipt, request_id)
        return receipt
    session.control = control
    return host, session, ledger, task, state


def emit_terminal(store, ledger, session, status, *, run='run-one', level=True):
    cursor = store.event(session.sid, 1, 'fixture_terminal')
    ledger.task_event(session, {'task_id': 'task-one', 'run_id': run,
        'subtype': 'task_notification', 'status': status}, cursor)
    if run == 'run-one':
        session.record['background_tasks'] = {}
    if level:
        session.record['background_level_cursor'] = store.event(session.sid, 1, 'fixture_level')
    store.put('sessions', session.sid, session.record)
    return cursor


def test_full_raw_input_not_cropped_summary_and_agent_never_disposable(tmp_path, monkeypatch):
    host, session, ledger, task, state = fixture(tmp_path, monkeypatch,
        command='sleep 60; ' + ' ' * 1600 + 'touch written')
    assert task['tool_input']['command'].startswith('sleep')
    row = ledger.inventory()[0]
    assert not row['detachable'] and 'touch' in row['reason']
    task['task_type'] = 'local_agent'
    task['run_id'] = 'different-run'
    row = ledger.inventory()[0]
    assert not row['detachable'] and 'local_agent' in row['reason']
    assert state['controls'] == []
    host.store.db.close()


def test_exact_stop_retains_intent_and_replay_never_resends_empty_success_not_proof(tmp_path, monkeypatch):
    async def scenario():
        host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
        row = ledger.inventory()[0]
        stopped = await dispatch_control(session, 'stop_task', {'observation_id': row['observation_id'], 'authority': 'fixture explicit switch'})
        assert stopped['status'] == 'stop_uncertain'
        assert state['controls'][0][:2] == ('stop_task', {'task_id': 'task-one'})
        receipt = host.store.get('controls', session.sid, stopped['stop_request_id'])
        receipt['status'] = 'success'
        host.store.put('controls', session.sid, receipt, stopped['stop_request_id'])
        again = await ledger.stop(row['observation_id'], authority='lost reply retry')
        assert again['status'] == 'stop_uncertain' and len(state['controls']) == 1
        emit_terminal(host.store, ledger, session, 'stopped', run='stale-run')
        assert ledger.records()[0]['status'] == 'stop_uncertain'
        emit_terminal(host.store, ledger, session, 'stopped', level=False)
        assert ledger.records()[0]['status'] == 'stop_uncertain'  # no later registry level
        session.record['background_level_cursor'] = host.store.event(session.sid, 1, 'fresh_level')
        assert ledger.records()[0]['status'] == 'stop_uncertain'  # original process still lives
        state['present'] = False
        assert ledger.records()[0]['status'] == 'stopped'
        assert 'not accepted worker completion' in ledger.records()[0]['watched_work']
        # Reload the independent ledger with no live native writer. No new control is issued.
        host.store.put('sessions', session.sid, session.record)
        host.sessions.clear()
        assert (await obs.ObservationLedger(host).stop(row['observation_id'], authority='restart readback'))['status'] == 'stopped'
        assert len(state['controls']) == 1
        host.store.db.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('terminal', ['completed', 'failed'])
def test_completion_or_failure_race_keeps_actual_receipt_not_rearmed(tmp_path, monkeypatch, terminal):
    host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
    row = ledger.inventory()[0]
    emit_terminal(host.store, ledger, session, terminal)
    state['present'] = False
    row = ledger.records()[0]
    assert row['status'] == 'raced_completion'
    assert row['outcome']['task'] == [terminal]
    assert state['controls'] == []
    host.store.db.close()


def test_stale_task_cannot_cancel_reused_id_and_ownership_keeps_immediate_parent(tmp_path, monkeypatch):
    async def scenario():
        host, session, ledger, task, state = fixture(tmp_path, monkeypatch, role='helper', parent='ses_worker')
        row = ledger.inventory()[0]
        assert row['ownership_chain'][0]['parent'] == 'ses_worker'
        assert row['ownership_chain'][1]['backend'] == 'opencode'
        assert row['ownership_problem']
        task['run_id'] = 'new-run'
        result = await ledger.stop(row['observation_id'], authority='fixture explicit switch')
        assert result['status'] == 'identity_changed' and state['controls'] == []
        host.store.db.close()
    asyncio.run(scenario())


def test_unknown_input_can_be_resolved_on_later_inventory_without_duplicate_identity(tmp_path, monkeypatch):
    host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
    task.pop('tool_use_id')
    row = ledger.inventory()[0]
    assert not row['detachable']
    task['tool_use_id'] = 'tool-one'
    resolved = ledger.inventory()[0]
    assert resolved['observation_id'] == row['observation_id']
    assert resolved['detachable'] and len(host.store.records('observations')) == 1
    host.store.db.close()


@pytest.mark.parametrize('field,value', [
    ('pending_message_uuids', ['input']), ('pending_recovery', 'recovery'), ('native_retry_active', True),
    ('pending_native_requests', {'permission': True}), ('native_queued_turns', 1),
    ('infrastructure_error', 'disk'), ('protocol_attention', True), ('stdout_eof', True),
    ('pending_control_requests', ['unknown-control']), ('primary_turn_open', True),
    ('background_level_known', False),
])
def test_retirement_never_signals_real_work_or_unknown_receipts(tmp_path, monkeypatch, field, value):
    async def scenario():
        host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
        session.record[field] = value
        signals = []
        monkeypatch.setattr(obs.os, 'kill', lambda *args: signals.append(args))
        answer = await ledger.retire(operation_id='switch-one', execution_id=session.record['execution_id'],
            generation=1, authority='fixture explicit switch')
        assert answer['outcome'] == 'held'
        assert signals == [] and state['controls'] == []
        host.store.db.close()
    asyncio.run(scenario())


def test_retirement_freezes_exact_owner_and_retains_lost_reply_and_watched_job(tmp_path, monkeypatch):
    """Injected kernel edges are unit evidence only, not the separate native lifecycle proof."""
    import signal
    async def scenario():
        host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
        events = []
        def kill(pid, signum):
            assert pid == session.process.pid  # never a group or watched target
            rows = host.store.records('observations')
            assert rows and all(row.get('retirement') for row in rows)
            events.append(signum)
            if signum == signal.SIGCONT:
                session.record.update(owner='gone', state='exited')
                session.process.returncode = 143
                state['present'] = False
                host.store.put('sessions', session.sid, session.record)
        monkeypatch.setattr(obs.os, 'kill', kill)
        monkeypatch.setattr(obs, '_process_state', lambda *args: 'T')
        monkeypatch.setattr(obs, '_stdout_drained', lambda *args: True)
        monkeypatch.setattr(obs, 'process_tree', lambda *args: [])
        owner = session.record['execution_id']
        await ledger.retire(operation_id='switch-one', execution_id=owner, generation=1, authority='switch')
        await asyncio.gather(*list(session.control_tasks))
        answer = await ledger.retire(operation_id='switch-one', execution_id=owner, generation=1, authority='lost reply')
        assert answer['outcome'] == 'ended'
        assert answer['observations'][0]['status'] == 'detached'
        assert answer['observations'][0]['outcome']['native_terminal'].startswith('unknown')
        assert events == [signal.SIGSTOP, signal.SIGTERM, signal.SIGCONT]
        assert state['controls'] == []  # retirement is NOT falsely called native stop_task
        host.store.db.close()
    asyncio.run(scenario())


def test_freeze_revalidation_thaws_without_killing_an_unattributed_child(tmp_path, monkeypatch):
    import signal
    async def scenario():
        host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
        signals = []
        monkeypatch.setattr(obs.os, 'kill', lambda pid, sig: signals.append(sig))
        monkeypatch.setattr(obs, '_process_state', lambda *args: 'T')
        monkeypatch.setattr(obs, '_stdout_drained', lambda *args: True)
        monkeypatch.setattr(obs, 'process_tree', lambda *args: [{'witness': {'pid': 999},
                                                              'parent_pid': session.process.pid, 'argv': ['pytest']}])
        await ledger.retire(operation_id='switch-one', execution_id=session.record['execution_id'], generation=1, authority='switch')
        await asyncio.gather(*list(session.control_tasks), return_exceptions=True)
        assert signals == [signal.SIGSTOP, signal.SIGCONT]
        assert state['present'] and session.record['owner'] == 'host'
        assert session.record['observation_transition'] is None
        row = ledger.records()[0]
        assert row['retirement']['phase'] == 'not_stopped'
        assert 'unattributed native child' in row['retirement']['error']
        host.store.db.close()
    asyncio.run(scenario())


def test_naturally_ended_observer_keeps_typed_obligation_and_pending_native_wake(tmp_path, monkeypatch):
    host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
    # No prior switch inventory: task completion itself must retain the full obligation.
    cursor = emit_terminal(host.store, ledger, session, 'completed')
    session.record.update(native_wake_pending=True, native_wake_cursor=cursor)
    inventory = ledger.switch_inventory()
    assert not inventory['blockers'] and len(inventory['candidates']) == 1
    assert inventory['observations'][0]['kind'] == 'timer'
    assert inventory['observations'][0]['terminal_evidence'][0]['status'] == 'completed'
    row = inventory['observations'][0]
    row['retirement'] = {'operation_id': 'switch-one', 'signal_authorized': True}
    ledger.save(row, 'fixture_retirement')
    state['present'] = False
    assert ledger.retirement('switch-one', session.record['execution_id'], 1)['outcome'] != 'ended'
    # A queued observer wake's init is still attributable before any new assistant/tool work.
    session.record.update(primary_turn_open=True, native_session_state='running')
    assert not ledger.switch_inventory()['blockers']
    session.record['last_assistant_cursor'] = host.store.event(session.sid, 1, 'new_model_or_tool_work')
    assert ledger.switch_inventory()['blockers']  # substantive new native work is not disposable
    assert state['controls'] == []
    host.store.db.close()


def test_terminal_refreshes_early_incomplete_level_from_exact_raw_task_started(tmp_path, monkeypatch):
    host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
    task['tool_use_id'] = None  # early native registry before task_started correlation
    first = ledger.inventory()[0]
    assert not first['detachable'] and first['source'] is None
    raw = json.dumps({'type': 'system', 'subtype': 'task_started', 'task_id': 'task-one',
                      'run_id': 'run-one', 'tool_use_id': 'tool-one', 'task_type': 'local_bash'}).encode() + b'\n'
    offset = host.store.append_bytes(session.sid, 1, 'stdout', raw)
    host.store.event(session.sid, 1, 'native_frame', stream='stdout', offset=offset, length=len(raw))
    cursor = emit_terminal(host.store, ledger, session, 'completed')
    session.record.update(native_wake_pending=True, native_wake_cursor=cursor)
    row = ledger.get(first['observation_id'])
    assert row['detachable'] and row['source']['input']['command'] == 'sleep 7190; date -u'
    assert row['owner']['tool_use_id'] == 'tool-one'
    assert not ledger.switch_inventory()['blockers']
    assert ledger.switch_inventory()['candidates']
    host.store.db.close()


def test_quota_sleep_not_active_credential_reader_is_detachable():
    assert obs.active_observer_effect([{'argv': ['sleep', '120']}]) is None
    assert 'credentials' in obs.active_observer_effect([{'argv': ['python3', str(obs.REPO / 'scripts/oc_usage.py'), '--json']}])


def test_identical_timers_retain_shared_process_scope_for_whole_owner_retirement(monkeypatch):
    rows = [{'witness': {'pid': pid}, 'parent_pid': 10,
             'argv': ['/bin/bash', '-c', "eval 'sleep 120; date -u'"]} for pid in (11, 12)]
    rows += [{'witness': {'pid': 13}, 'parent_pid': 11, 'argv': ['sleep', '120']},
             {'witness': {'pid': 14}, 'parent_pid': 12, 'argv': ['sleep', '120']}]
    monkeypatch.setattr(obs, 'process_tree', lambda *_: rows)
    processes, reason = obs.task_processes(SimpleNamespace(process=SimpleNamespace(pid=10)), 'sleep 120; date -u')
    assert reason is None and len(processes) == 4
    assert sum(bool(p.get('shell_root')) for p in processes) == 2


def test_failed_shutdown_signal_always_releases_exact_freeze(tmp_path, monkeypatch):
    import signal
    async def scenario():
        host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
        signals = []
        def kill(pid, sig):
            signals.append(sig)
            if sig == signal.SIGTERM:
                raise OSError('injected signal failure')
        monkeypatch.setattr(obs.os, 'kill', kill)
        monkeypatch.setattr(obs, '_process_state', lambda *args: 'T')
        monkeypatch.setattr(obs, '_stdout_drained', lambda *args: True)
        monkeypatch.setattr(obs, 'process_tree', lambda *args: [])
        await ledger.retire(operation_id='switch-one', execution_id=session.record['execution_id'], generation=1, authority='switch')
        await asyncio.gather(*list(session.control_tasks), return_exceptions=True)
        assert signals == [signal.SIGSTOP, signal.SIGTERM, signal.SIGCONT]
        assert state['present'] and session.record['state'] == 'ready'
        assert session.record['observation_transition'] is None
        assert ledger.records()[0]['retirement']['phase'] == 'not_stopped'
        host.store.db.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('same_witness', [True, False])
def test_host_recovery_finishes_only_exact_frozen_observer_intent(tmp_path, monkeypatch, same_witness):
    import signal
    host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
    row = ledger.inventory()[0]
    row['retirement'] = {'operation_id': 'switch-one', 'phase': 'frozen',
                         'owner_process': row['owner_process'], 'last_assistant_cursor': None}
    ledger.save(row, 'fixture_frozen_intent')
    host.sessions.clear()
    signals = []
    monkeypatch.setattr(obs.os, 'kill', lambda pid, sig: signals.append(sig))
    monkeypatch.setattr(obs, '_process_state', lambda *args: 'T' if same_witness else None)
    monkeypatch.setattr(obs, 'process_tree', lambda *args: [])
    ledger.recover_frozen_retirements()
    assert signals == ([signal.SIGTERM, signal.SIGCONT] if same_witness else [])
    if same_witness:
        assert ledger.get(row['observation_id'])['retirement']['native_terminal_gap']
    host.store.db.close()


def test_writer_revalidates_exact_task_run_before_native_stop_bytes(tmp_path, monkeypatch):
    from claude_headless.host import NativeSession
    async def scenario():
        host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
        task['run_id'] = 'replacement-run'
        session.record['pending_control_requests'] = ['stop-request']
        host.store.put('controls', session.sid, {'request_id': 'stop-request', 'subtype': 'stop_task', 'status': 'pending',
            'qualified_owner': {'session': session.record['execution_id'], 'generation': 1, 'task_id': 'task-one',
                                'run_id': 'run-one', 'tool_use_id': 'tool-one'}}, 'stop-request')
        session.event = lambda kind, detail: host.store.event(session.sid, 1, kind, detail)
        session.writes = asyncio.Queue()
        session.writes.put_nowait((b'NEVER WRITE', 'cursor', 'control', {'request_id': 'stop-request'}))
        session.writes.put_nowait(None)
        session.process.stdin = SimpleNamespace(write=lambda *_: pytest.fail('replacement task must not be stopped'))
        await NativeSession.writer(session)
        assert host.store.get('controls', session.sid, 'stop-request')['status'] == 'withheld'
        assert session.record['pending_control_requests'] == []
        host.store.db.close()
    asyncio.run(scenario())


def test_watched_target_completion_failure_race_is_evidence_not_worker_acceptance(tmp_path, monkeypatch):
    target_sid, mid = str(uuid.uuid4()), str(uuid.uuid4())
    host, session, ledger, task, state = fixture(tmp_path, monkeypatch,
        command='oc_seat.py wait cc-' + target_sid + ' --max 540')
    target = {'native_uuid': target_sid, 'execution_id': 'cc-' + target_sid, 'generation': 3,
              'last_input_uuid': mid, 'state': 'ready', 'owner': 'host', 'native_session_state': 'running'}
    host.store.put('sessions', target_sid, target)
    host.store.put('receipts', target_sid, {'message_uuid': mid, 'status': 'started', 'completed': False}, mid)
    row = ledger.inventory()[0]
    assert row['watched_target_snapshots'][0]['before']['native_receipt']['completed'] is False
    target.update(native_session_state='idle', last_result_cursor=host.store.event(target_sid, 3, 'fixture_result'))
    host.store.put('sessions', target_sid, target)
    host.store.put('receipts', target_sid, {'message_uuid': mid, 'status': 'completed', 'completed': True,
        'results': [{'subtype': 'error', 'is_error': True, 'terminal_reason': 'usage_limit', 'cursor': target['last_result_cursor']}]}, mid)
    row = ledger.reconcile(row)
    race = row['raced_target_events'][0]
    assert race['native_receipt']['completed'] and race['native_results'][0]['is_error']
    assert race['assignment_completion'].startswith('not inferred')
    assert target['native_session_state'] == 'idle' and state['controls'] == []
    foreign = obs.watched_snapshot(host, {'backend': 'opencode', 'session': 'ses_worker'})
    assert foreign['availability'] == 'unknown' and foreign['execution_action'].startswith('none')
    host.store.db.close()


def _input_frame(*, tool='tool-one', command='sleep 60', name='Bash', scope=None, sidechain=False, blocks=None):
    return {'type': 'assistant', 'parent_tool_use_id': scope, 'isSidechain': sidechain,
            'message': {'content': blocks if blocks is not None else [
                {'type': 'tool_use', 'id': tool, 'name': name, 'input': {'command': command}}]}}


def _task_frame(*, tool='tool-one', task='task-one', run='run-one', subtype='task_started'):
    return {'type': 'system', 'subtype': subtype, 'task_id': task, 'run_id': run, 'tool_use_id': tool}


def _retain_input_frame(store, sid, event, *, generation=1, detail=None, kind='native_frame'):
    private_dir(store.root / sid / f'g{generation:06d}')
    raw = (json.dumps(event) + '\n').encode()
    offset = store.append_bytes(sid, generation, 'stdout', raw)
    return store.event(sid, generation, kind, detail, stream='stdout', offset=offset, length=len(raw))


def _input_store(tmp_path):
    store = Store(tmp_path / 'state')
    session = SimpleNamespace(sid=str(uuid.uuid4()), generation=1)
    return SimpleNamespace(store=store), session


def _input_cases():
    direct, lifecycle = {'tool_use_id': 'tool-one'}, {'task_id': 'task-one', 'run_id': 'run-one'}
    ambiguous = 'missing or ambiguous full scoped tool input'
    no_identity = 'task has no unique exact task/run to tool-use identity'
    not_bash = 'task input is not native Bash (Agent work is not an observer)'
    return [
        ('repeated-identical-frames-first-cursor', [_input_frame(), _input_frame()], direct, None),
        ('sidechain-duplicate-ambiguous', [_input_frame(), _input_frame(sidechain=True)], direct, ambiguous),
        ('agent-scope-duplicate-ambiguous', [_input_frame(), _input_frame(scope='agent-one')], direct, ambiguous),
        ('child-bash-retained', [_input_frame(scope='agent-one', sidechain=True)], direct, None),
        ('different-full-inputs-ambiguous', [_input_frame(), _input_frame(command='touch work')], direct, ambiguous),
        ('missing-tool', [], direct, ambiguous),
        ('agent-not-bash', [_input_frame(name='Agent')], direct, not_bash),
        ('non-object-bash-input', [_input_frame(blocks=[
            {'type': 'tool_use', 'id': 'tool-one', 'name': 'Bash', 'input': ['sleep', '60']}])], direct, not_bash),
        ('two-blocks-in-one-frame', [_input_frame(blocks=[
            {'type': 'tool_use', 'id': 'tool-one', 'name': 'Bash', 'input': {'command': command}}
            for command in ('sleep 60', 'touch work')])], direct, ambiguous),
        ('unique-task-run', [_input_frame(), _task_frame(), _task_frame(run='another-run', tool='another-tool')],
            lifecycle, None),
        ('two-task-run-candidates', [_input_frame(), _task_frame(), _task_frame(tool='another-tool')],
            lifecycle, no_identity),
        ('no-task-run-candidate', [_input_frame(), _task_frame(run='another-run')], lifecycle, no_identity),
        ('null-metadata-system-subtype', [_input_frame(), _task_frame(subtype='legacy_other')], lifecycle, None),
        ('missing-task-and-run', [_input_frame(), _task_frame(task=None, run=None)], {}, None),
        ('json-numeric-equality', [_input_frame(), _task_frame(task=True, run=1.0)],
            {'task_id': 1, 'run_id': True}, None),
    ]


@pytest.mark.parametrize('_name,frames,task,reason', _input_cases(), ids=[c[0] for c in _input_cases()])
def test_task_input_index_equals_original_scan(tmp_path, monkeypatch, _name, frames, task, reason):
    host, session = _input_store(tmp_path)
    try:
        cursors = [_retain_input_frame(host.store, session.sid, frame) for frame in frames]
        # This oracle is the original scan still owned by full_task_input, not a second proof implementation.
        # On the base there is no task_input_rows method; adding this inert override is harmless there too.
        with monkeypatch.context() as scan:
            scan.setattr(host.store, 'task_input_rows', lambda *args: None, raising=False)
            scanned = obs.full_task_input(host, session, task)
        indexed = obs.full_task_input(host, session, task)
        assert indexed == scanned
        assert indexed[1] == reason
        if reason is None:
            assert indexed[0]['cursor'] == cursors[0]
            assert indexed[0]['input'] == {'command': 'sleep 60'}
    finally:
        host.store.db.close()


def test_task_input_metadata_generation_and_frame_kind_match_original_scan(tmp_path, monkeypatch):
    host, session = _input_store(tmp_path)
    try:
        expected = _retain_input_frame(host.store, session.sid, _input_frame(), detail={'type': 'assistant'})
        _retain_input_frame(host.store, session.sid, _input_frame(command='touch wrong-generation'), generation=2)
        _retain_input_frame(host.store, session.sid, _input_frame(command='touch wrong-kind'), kind='unknown_frame')
        _retain_input_frame(host.store, session.sid, _input_frame(command='touch wrong-metadata'),
                            detail={'type': 'system', 'subtype': 'init'})
        _retain_input_frame(host.store, session.sid, _task_frame(subtype='other'),
                            detail={'type': 'system', 'subtype': 'other'})
        with monkeypatch.context() as scan:
            scan.setattr(host.store, 'task_input_rows', lambda *args: None, raising=False)
            scanned = obs.full_task_input(host, session, {'tool_use_id': 'tool-one'})
            lifecycle_scanned = obs.full_task_input(host, session, {'task_id': 'task-one', 'run_id': 'run-one'})
        assert obs.full_task_input(host, session, {'tool_use_id': 'tool-one'}) == scanned
        assert scanned[0]['cursor'] == expected
        assert obs.full_task_input(host, session, {'task_id': 'task-one', 'run_id': 'run-one'}) == lifecycle_scanned
        assert lifecycle_scanned == (None, 'task has no unique exact task/run to tool-use identity')
    finally:
        host.store.db.close()


def _generation_scans(trace):
    return [sql for sql in trace if sql.startswith('SELECT ') and ' FROM events WHERE sid=' in sql
            and 'generation=' in sql]


def test_pre_index_generation_backfills_once_then_only_exact_key_lookups(tmp_path):
    host, session = _input_store(tmp_path)
    store = host.store
    try:
        first = _retain_input_frame(store, session.sid, _input_frame())
        _retain_input_frame(store, session.sid, _input_frame())
        _retain_input_frame(store, session.sid, _task_frame())
        for n in range(40):
            _retain_input_frame(store, session.sid, _input_frame(tool=f'unrelated-{n}'))
        trace = []
        store.db.set_trace_callback(trace.append)
        direct = obs.full_task_input(host, session, {'tool_use_id': 'tool-one'})
        assert direct[0]['cursor'] == first
        assert len(_generation_scans(trace)) == 1
        trace.clear()
        assert obs.full_task_input(host, session, {'tool_use_id': 'tool-one'}) == direct
        assert obs.full_task_input(host, session, {'task_id': 'task-one', 'run_id': 'run-one'}) == direct
        assert obs.full_task_input(host, session, {'tool_use_id': 'absent'}) == (
            None, 'missing or ambiguous full scoped tool input')
        assert obs.full_task_input(host, session, {'task_id': 'absent', 'run_id': 'absent'}) == (
            None, 'task has no unique exact task/run to tool-use identity')
        assert not _generation_scans(trace), trace
        assert store.db.execute("SELECT count(*) FROM task_inputs WHERE kind='tool' AND lookup_key='tool-one'").fetchone()[0] == 2
    finally:
        store.db.close()


def test_older_writer_invalidates_coverage_and_partial_index_is_backfilled(tmp_path):
    host, session = _input_store(tmp_path)
    store = host.store
    try:
        _retain_input_frame(store, session.sid, _input_frame())
        _retain_input_frame(store, session.sid, _task_frame())
        direct = obs.full_task_input(host, session, {'tool_use_id': 'tool-one'})
        assert store.task_inputs_complete(session.sid, 1)
        # This is today's Store.event call shape: no parsed-frame indexing. Its database trigger must
        # invalidate coverage even when that writer does not know the new table exists.
        _retain_input_frame(store, session.sid, _task_frame(tool='another-tool'))
        assert not store.task_inputs_complete(session.sid, 1)
        # Leave partial rows as well: a missing key without coverage is not proof of absence.
        store.db.execute("DELETE FROM task_inputs WHERE kind='tool'")
        store.db.commit()
        trace = []
        store.db.set_trace_callback(trace.append)
        assert obs.full_task_input(host, session, {'tool_use_id': 'tool-one'}) == direct
        assert len(_generation_scans(trace)) == 1
        trace.clear()
        assert obs.full_task_input(host, session, {'task_id': 'task-one', 'run_id': 'run-one'}) == (
            None, 'task has no unique exact task/run to tool-use identity')
        assert not _generation_scans(trace)
    finally:
        store.db.close()


def test_missing_index_still_answers_by_original_scan(tmp_path):
    host, session = _input_store(tmp_path)
    try:
        _retain_input_frame(host.store, session.sid, _input_frame())
        _retain_input_frame(host.store, session.sid, _task_frame())
        expected = obs.full_task_input(host, session, {'tool_use_id': 'tool-one'})
        host.store.db.execute('DROP TABLE IF EXISTS task_inputs')
        assert obs.full_task_input(host, session, {'tool_use_id': 'tool-one'}) == expected
        assert obs.full_task_input(host, session, {'task_id': 'task-one', 'run_id': 'run-one'}) == expected
    finally:
        host.store.db.close()


def test_failed_backfill_keeps_outer_native_batch_and_original_answer(tmp_path, monkeypatch):
    import sqlite3
    host, session = _input_store(tmp_path)
    try:
        cursor = _retain_input_frame(host.store, session.sid, _input_frame())
        def unavailable(*args):
            raise sqlite3.OperationalError('index unavailable (fixture)')
        monkeypatch.setattr(host.store, 'index_task_frame', unavailable, raising=False)
        with host.store.batch():
            sibling = host.store.event(session.sid, 1, 'same_read_evidence')
            source, reason = obs.full_task_input(host, session, {'tool_use_id': 'tool-one'})
            assert reason is None and source['cursor'] == cursor
            assert host.store.db.execute("SELECT 1 FROM events WHERE kind='same_read_evidence'").fetchone()
        assert host.store.db.execute('SELECT 1 FROM events WHERE seq=?', (int(sibling.rsplit(':', 1)[1]),)).fetchone()
    finally:
        host.store.db.close()


@pytest.mark.parametrize('detail', [None, "'5'", "{type:'assistant',}",
                                  "{type:'system',subtype:'task_started'}"])
def test_task_input_backfill_uses_sqlite_metadata_without_reinterpreting_it(tmp_path, monkeypatch, detail):
    host, session = _input_store(tmp_path)
    try:
        cursor = _retain_input_frame(host.store, session.sid, _input_frame())
        host.store.db.execute('UPDATE events SET detail=? WHERE seq=?', (detail, int(cursor.rsplit(':', 1)[1])))
        host.store.db.commit()
        with monkeypatch.context() as scan:
            scan.setattr(host.store, 'task_input_rows', lambda *args: None, raising=False)
            expected = obs.full_task_input(host, session, {'tool_use_id': 'tool-one'})
        assert obs.full_task_input(host, session, {'tool_use_id': 'tool-one'}) == expected
        trace = []
        host.store.db.set_trace_callback(trace.append)
        assert obs.full_task_input(host, session, {'tool_use_id': 'tool-one'}) == expected
        assert not _generation_scans(trace), trace
        assert host.store.task_inputs_complete(session.sid, 1)
    finally:
        host.store.db.close()


def test_task_input_backfill_does_not_commit_an_otherwise_empty_outer_batch(tmp_path):
    host, session = _input_store(tmp_path)
    try:
        _retain_input_frame(host.store, session.sid, _input_frame())
        with pytest.raises(RuntimeError, match='outer batch rollback'):
            with host.store.batch():
                source, reason = obs.full_task_input(host, session, {'tool_use_id': 'tool-one'})
                assert reason is None and source['input']['command'] == 'sleep 60'
                assert host.store.db.in_transaction
                raise RuntimeError('outer batch rollback')
        assert not host.store.db.execute('SELECT 1 FROM task_inputs').fetchone()
    finally:
        host.store.db.close()


@pytest.mark.parametrize('switch', [False, True], ids=['inventory', 'switch-inventory'])
def test_observation_inventories_use_read_only_cached_execution_id_map(tmp_path, monkeypatch, switch):
    host, session, ledger, task, state = fixture(tmp_path, monkeypatch)
    try:
        parent_sid = str(uuid.uuid4())
        parent = {'native_uuid': parent_sid, 'execution_id': 'cc-' + parent_sid, 'generation': 1,
                  'role': 'Manager', 'state': 'exited', 'owner': 'gone'}
        host.store.put('sessions', parent_sid, parent)
        session.record['parent'] = parent['execution_id']
        # The live overlay must still win over the stored parent, by execution_id rather than native UUID.
        host.sessions[parent_sid] = SimpleNamespace(record={**parent, 'role': 'conductor'}, sid=parent_sid, generation=1)
        before = json.dumps(host.store.cached_sessions(), sort_keys=True)
        monkeypatch.setattr(host.store, 'sessions', lambda: pytest.fail('inventory decoded every session record'))
        answer = ledger.switch_inventory()['observations'] if switch else ledger.inventory()
        row = next(r for r in answer if r['owner']['session'] == session.record['execution_id'])
        assert row['ownership_chain'][1]['session'] == parent['execution_id']
        assert row['ownership_chain'][1]['role'] == 'conductor'
        assert json.dumps(host.store.cached_sessions(), sort_keys=True) == before
    finally:
        host.store.db.close()
