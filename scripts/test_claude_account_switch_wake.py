"""Manager-requested account-switch wakes: synthetic stores/CLI only, never a live login or host."""
import asyncio
import json
from pathlib import Path
import uuid

import pytest

from claude_headless import accounts, restart
from claude_headless.quota_wake import record_account_switch_notice
from claude_headless.store import Store
from test_claude_headless_restart import CANONICAL, HOST_PID, Harness, install, rec, stored_manager_record
from test_claude_headless_supervision import cleanup, setup
from test_claude_quota_halt import input_lines, launch_fixture, notice_sent, quota_tick
from test_claude_accounts import native
from claude_headless.restart_state import read_operations


ACCOUNT = {'label': 'work', 'identity': 'work-id', 'epoch': 2, 'config_dir': None}
QUOTA = {'ok': True, 'windows': [{'label': 'weekly', 'percent': 12, 'resets_at': '2026-11-01'},
                                {'label': 'five-hour', 'percent': 3, 'resets_at': '2026-10-25T12:00Z'}]}
ARGS = ['--account', 'work', '--close-idle', '--resume', CANONICAL, '--notify-manager']


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    import claude_manager_headless as launcher
    import oc_usage
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    native(home)
    native(home, home / '.claude-work', 'work@example.test')
    accounts._write(accounts.registry_path(), {'emails': {'personal': 'personal@example.test', 'work': 'work@example.test'}})
    monkeypatch.setattr(accounts, 'resolve_account', lambda label: {
        **accounts._folder_identity(home / '.claude-work', accounts._read(accounts.registry_path())), 'label': label})
    monkeypatch.setattr(oc_usage, 'claude_usage', lambda **kw: QUOTA)
    monkeypatch.setattr(launcher, 'run_prepare', lambda args: {'status': 'prepared'})


def read_notice(root):
    store = Store(root)
    try:
        [notice] = store.records('quota_checks')
        return notice
    finally:
        store.db.close()


def test_notify_option_requires_account_and_exact_resume_before_host_contact(monkeypatch):
    monkeypatch.setattr(restart, 'open_client', lambda args: pytest.fail('no host contact'))
    for args in (['--notify-manager'], ['--account', 'work', '--notify-manager'],
                 ['--resume', CANONICAL, '--notify-manager'],
                 ['--account', 'work', '--resume', 'short', '--notify-manager']):
        with pytest.raises(SystemExit) as error:
            restart.main(args)
        assert error.value.code == 2


def test_restart_stages_before_signal_finalizes_after_resume(isolated, tmp_path, monkeypatch, capsys):
    root = tmp_path / 'state'
    h = Harness([rec(9001)], state_root=str(root), state_record=stored_manager_record(tmp_path))
    install(monkeypatch, h)
    kill = h.kill
    def staged_before_signal(pid):
        episode = read_notice(root)
        assert episode['status'] == 'pending_switch' and episode['due_at'] is None
        kill(pid)
    monkeypatch.setattr(restart, '_sigterm', staged_before_signal)
    assert restart.main(ARGS) == 0
    report = json.loads(capsys.readouterr().out)
    episode = read_notice(root)
    assert report['followup']['sent'] is False
    assert episode['status'] == 'scheduled'
    lifecycle = read_operations(root, report['operation_id'])['transition']
    assert lifecycle['requested_transition'] == 'succeeded' and lifecycle['service']['account']['label'] == 'work'
    assert lifecycle['request']['manager']['resume'] == CANONICAL
    assert lifecycle['old_host_exit_observed'] and lifecycle['service']['state'] == 'ready'
    assert report['notice']['message_uuid'] == episode['message_uuid'] and episode['recipient'] is None
    assert 'send' not in h.client.methods and 'steer' not in h.client.methods


@pytest.mark.parametrize('failure', ['start', 'followup', 'commit'])
def test_post_stop_failure_is_durable_even_with_no_host(isolated, tmp_path, monkeypatch, capsys, failure):
    root = tmp_path / 'state'
    h = Harness([rec(9001)], state_root=str(root), state_record=stored_manager_record(tmp_path))
    install(monkeypatch, h)
    def broken(*args, **kwargs):
        raise RuntimeError('synthetic ' + failure + ' failure')
    if failure == 'start':
        monkeypatch.setattr(restart, 'start_host_fresh', broken)
    elif failure == 'commit':
        monkeypatch.setattr(accounts, 'commit_selection', broken)
    else:
        monkeypatch.setattr(restart, 'run_followup', broken)
    assert restart.main(ARGS) == 1
    report = json.loads(capsys.readouterr().out)
    episode = read_notice(root)
    assert h.kills == [HOST_PID]
    assert any('synthetic ' + failure + ' failure' in e['message'] for e in report['errors'])
    assert (episode['due_at'] is None) == (failure == 'start')
    assert report['notice']['status'] == ('pending_switch' if failure == 'start' else 'scheduled')
    assert (report['service']['state'] == 'ready') == (failure != 'start')


def test_pre_stop_refusal_queues_nothing(isolated, tmp_path, monkeypatch, capsys):
    root = tmp_path / 'state'
    h = Harness([rec(9001, state='ready', owner='host', pending_message_uuids=['work'])], state_root=str(root))
    install(monkeypatch, h)
    assert restart.main(ARGS) == 3
    assert h.kills == []
    assert read_notice(root)['due_at'] is None  # retained before-stop recipe is not a scheduled wake


def test_non_manager_notify_target_refuses_without_signal(isolated, tmp_path, monkeypatch, capsys):
    other = str(uuid.uuid4())
    h = Harness([rec(9001, role='worker'),
                 rec(9002, execution_id='cc-' + other, native_uuid=other, state='ready', owner='host')],
                state_root=str(tmp_path / 'state'))
    install(monkeypatch, h)
    assert restart.main(ARGS) == 3
    assert not h.kills and 'close_input' not in h.client.methods and 'settle_for_restart' not in h.client.methods


def test_lost_resume_reply_is_reconciled_without_second_launch(isolated, tmp_path, monkeypatch, capsys):
    root = tmp_path / 'state'
    h = Harness([rec(9001)], state_root=str(root), state_record=stored_manager_record(tmp_path))
    install(monkeypatch, h)
    calls = []
    def lost(*args, **kwargs):
        calls.append('launch')
        h.dispatch('reconnect', {'execution_id': 'cc-' + CANONICAL, 'generation': 2})
        raise RuntimeError('IPC response lost after accepted launch')
    monkeypatch.setattr(restart, 'run_followup', lost)
    assert restart.main(ARGS) == 0
    report = json.loads(capsys.readouterr().out)
    assert calls == ['launch'] and report['followup']['status'] == 'ready'
    assert any('IPC response lost' in e['message'] for e in report['errors'])
    assert read_notice(root)['status'] == 'scheduled'


@pytest.mark.parametrize('outcome', ['complete', 'failed'])
def test_switch_notice_reconnects_same_uuid_after_host_restart_once(tmp_path, outcome):
    async def scenario():
        from claude_headless.host import Host
        from test_claude_headless_host import auth, builder
        host, manager, clock = await setup(tmp_path, role='Manager')
        root, sid, eid = host.root, manager.sid, manager.record['execution_id']
        store = host.store
        key = str(uuid.uuid4())
        pending = record_account_switch_notice(store, notice_id=key, recipient=eid,
                                               requested_account='work', clock=lambda: clock[0])
        await quota_tick(host)
        assert input_lines(host, sid) == []  # replacement startup cannot race an unfinished outcome
        await cleanup(host)
        # Restart finalizes in the same durable store while NO host exists.
        store = Store(root)
        final = record_account_switch_notice(store, notice_id=key, outcome=outcome, account=ACCOUNT, quota=QUOTA,
            failure='synthetic start failure' if outcome == 'failed' else None,
            report={'phases': {'closed_idle': ['cc-worker (generation 1, role worker)'],
                              'stopped_on_process_tree_proof': ['cc-helper'],
                              'host_exit_observed': True, 'host_started': {'pid': 123} if outcome == 'complete' else None}},
            clock=lambda: clock[0])
        assert final['message_uuid'] == pending['message_uuid']
        store.db.close()
        host = Host(root, tmp_path / 'restored.sock', builder, auth, clock=lambda: clock[0])
        try:
            # A newer unrelated Manager must not steal the explicitly resumed Manager's switch result.
            newer = await launch_fixture(host, tmp_path)
            newer.update(last_input_at=clock[0] + 100)
            [sent] = await notice_sent(host)
            assert sent['recipient'] == eid and host.state(sid)['generation'] == 2
            [notice] = input_lines(host, sid, 2)
            text = notice['message']['content']
            assert text.startswith('AUTOMATED ACCOUNT SWITCH ' + outcome.upper())
            assert 'Active account: work' in text and 'weekly 12%' in text and 'five-hour 3%' in text
            assert 'cc-worker' in text and 'cc-helper' in text
            assert '[ap:XLM018]' in text and '[ap:4Y02WI]' in text
            assert ('synthetic start failure' in text) == (outcome == 'failed')
            assert input_lines(host, newer.sid) == []
            for _ in range(3):
                await quota_tick(host)
            assert len(input_lines(host, sid, 2)) == 1
        finally:
            await cleanup(host)
        host = Host(root, tmp_path / 'again.sock', builder, auth, clock=lambda: clock[0] + 60)
        try:
            await quota_tick(host)
            assert not host.sessions and len(input_lines(host, sid, 2)) == 1
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_failed_quota_read_does_not_lose_notice(isolated, tmp_path, monkeypatch):
    import oc_usage
    root = tmp_path / 'state'
    report = {'manager_notice': {'notice_id': 'one', 'state_root': str(root),
                                'recipient': 'cc-' + CANONICAL, 'requested_account': 'work'}}
    restart.switch_notice(report, outcome='pending')
    monkeypatch.setattr(oc_usage, 'claude_usage', lambda **kw: (_ for _ in ()).throw(ConnectionError()))
    restart.switch_notice(report, outcome='complete')
    episode = read_notice(root)
    assert episode['outcome'] == 'complete' and episode['quota']['ok'] is False
    assert 'ConnectionError' in episode['quota']['error']


def test_uncertain_switch_notice_is_never_replayed_or_retargeted(tmp_path):
    async def scenario():
        host, manager, clock = await setup(tmp_path, role='Manager')
        try:
            key = str(uuid.uuid4())
            record_account_switch_notice(host.store, notice_id=key, recipient=manager.record['execution_id'],
                                         requested_account='work', clock=lambda: clock[0])
            episode = record_account_switch_notice(host.store, notice_id=key, outcome='complete',
                                                   account=ACCOUNT, quota=QUOTA, clock=lambda: clock[0])
            host.store.put('receipts', manager.sid, {'message_uuid': episode['message_uuid'],
                'generation': 1, 'submitted': 'uncertain original switch notice', 'status': 'uncertain',
                'written': True, 'acknowledged': False}, episode['message_uuid'])
            newer = await launch_fixture(host, tmp_path)
            newer.update(last_input_at=clock[0] + 100)
            for _ in range(3):
                await quota_tick(host)
            [problem] = host.store.records('quota_checks')
            assert problem['status'] == 'delivery_problem' and 'never resent' in problem['failure']
            assert problem['recipient'] == manager.record['execution_id']
            assert input_lines(host, manager.sid) == [] and input_lines(host, newer.sid) == []
            # A duplicate finalizer cannot change an admitted success into a new failure/input.
            repeated = record_account_switch_notice(host.store, notice_id=key, outcome='failed', failure='late retry')
            assert repeated['outcome'] == 'complete' and repeated['message_uuid'] == episode['message_uuid']
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_queued_restart_preserves_notify_flag_and_retries_only_pre_stop_refusals(monkeypatch):
    import subprocess
    import oc_claude_restart as queue
    commands, popups = [], []
    codes = iter([3, 0])
    args, operation_id = queue.bind_operation(ARGS)
    def run(command, **kwargs):
        commands.append(command)
        code = next(codes)
        receipt = {'schema': 'claude-restart/1', 'operation_id': operation_id,
            'admitted': True, 'request': {'kind': 'account_switch', 'account': 'work', 'close_idle': True,
                'namespace': {}, 'manager': {'policy': 'exact'}, 'notify': True},
            'requested_transition': 'refused' if code == 3 else 'succeeded', 'publication': {}, 'errors': [],
            'phase': 'waiting' if code == 3 else 'service_ready', 'effects': [],
            'retry': {'classification': 'settlement', 'safe_pre_stop_retry': code == 3},
            'service': {'state': 'unchecked' if code == 3 else 'ready'},
            'manager': {'state': 'pending'}, 'notice': {'status': 'scheduled'}}
        return subprocess.CompletedProcess(command, code, stdout=json.dumps(receipt), stderr='')
    monkeypatch.setattr(queue.subprocess, 'run', run)
    monkeypatch.setattr(queue.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(queue.time, 'monotonic', lambda counter=iter(range(0, 100000, 100)): next(counter))
    monkeypatch.setattr(queue, 'popup', lambda *args: popups.append(args))
    assert queue.worker('/synthetic/claude_restart.sh', args, timeout=3600, interval=15) == 0
    assert len(commands) == 2 and all(command[2:-2] == ARGS for command in commands)
    assert commands[0][-2] == '--operation-id'
    assert commands[0][-1] == commands[1][-1]  # one durable recovery identity across retry attempts
    assert popups and popups[0][0] == 'Claude host restart: DONE'


def lifecycle_alarm(host, monkeypatch):
    """Real canonical admission + ready finalizer; injected account/OS facts only."""
    from claude_headless import host as host_module
    from claude_headless.observer_recovery import ObserverRecovery
    from claude_headless.quota_wake import QuotaWake
    from claude_headless.restart_state import stage_episode, finalize_service
    from test_claude_headless_restart import wit
    root, operation, key = host.store.root, 'recovery-notice-R', 'observer-switch:recovery-notice-R'
    selected = {'label': 'personal', 'config_dir': None, 'identity': 'old-id', 'epoch': 7}
    host.manager.update(state='ready', pid=9001, process_witness=wit(9001), setting_control_pending=False)
    host.store.put('sessions', host.manager['native_uuid'], host.manager)
    monkeypatch.setattr(host_module, 'witness', lambda pid: wit(pid) if pid in (5151, 9001) else None)
    request = {'kind': 'account_switch', 'account': 'work', 'close_idle': True,
        'namespace': {'state_root': str(root), 'socket_path': str(root / 'fixture.sock')},
        'manager': {'policy': 'exact', 'resume': host.manager['native_uuid']}, 'notify': True}
    recipe = {'episode_id': key, 'old_host': {'host_pid': 4242, 'process_witness': wit(4242)},
        'old_selector': {'present': True, 'value': {}, 'account': selected}}
    owner = ObserverRecovery(host.store)
    owner.admit_transition(operation, request, recipe)
    stage_episode(host.store, operation, key)
    owner.transition_update(operation, fields={'direction': 'restore_old', 'host_stop_intended': True,
        'old_host_exit_observed': True, 'start_account': selected, 'start_token': 'attempt',
        'candidate': {'host_pid': 5151, 'process_witness': wit(5151), 'account': selected, 'start_token': 'attempt'}},
        error={'phase': 'applying_target', 'type': 'OSError', 'message': 'work preparation failed'})
    health = {'host_pid': 5151, 'process_witness': wit(5151), 'state_root': str(root),
              'socket_path': str(root / 'fixture.sock')}
    def finalizer():
        return finalize_service(host.store, operation, health, {'account': selected}, host_module.witness,
                                source='injected ordinary endpoint health')
    finalizer()
    wake = QuotaWake(host, lambda: 2000000000)
    return wake, key, finalizer


@pytest.mark.parametrize('receipt_state', ['acknowledged', 'uncertain'])
def test_recovery_notice_lost_send_reply_never_replays_or_retargets(tmp_path, monkeypatch, receipt_state):
    from types import SimpleNamespace
    from test_claude_quota_episode_control import AlarmHost
    from claude_headless.quota_wake import QuotaWake
    from claude_headless.observer_recovery import ObserverRecovery
    async def scenario():
        host = AlarmHost(tmp_path / 'state')
        wake, key, finalize = lifecycle_alarm(host, monkeypatch)
        recipient = host.manager['execution_id']
        async def lost(*a, **kw):
            receipt = await host.send(*a, **kw)
            receipt.update(status=receipt_state, acknowledged=receipt_state == 'acknowledged', written=True)
            host.store.put('receipts', host.manager['native_uuid'], receipt, receipt['message_uuid'])
            raise ConnectionError('Send admitted before response lost')
        host.session.send = lost
        await wake.advance(key)
        episode = host.store.get('quota_checks', '', key)
        submitted = episode['submission']
        assert episode['status'] == ('sent' if receipt_state == 'acknowledged' else 'delivery_problem')
        assert submitted['recipient'] == recipient and 'SERVICE RESTORED' in submitted['submitted']
        ObserverRecovery(host.store).transition_update('recovery-notice-R',
            error={'phase': 'later_status', 'type': 'Observation', 'message': 'later facts must not rewrite admitted text'})
        finalize()
        assert wake.message(host.store.get('quota_checks', '', key)) == submitted['submitted']
        assert len(host.sends) == 1
        host.store.db.close()
        # New connection/coordinator and a different current Manager; exact old receipt wins first.
        restored = AlarmHost(tmp_path / 'state')
        try:
            newer = restored.manager['execution_id']
            assert newer != recipient
            again = QuotaWake(restored, lambda: 2000000100)
            await again.advance(key)
            final = restored.store.get('quota_checks', '', key)
            assert final['recipient'] == recipient and final['submission'] == submitted
            assert final['message_uuid'] == episode['message_uuid']
            assert not restored.reconnects and not restored.sends
        finally:
            restored.store.db.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['missing', 'changed', 'slow', 'defer_before', 'cancel_before', 'defer_after', 'cancel_after'])
def test_current_manager_unavailable_and_episode_controls_survive_recovery(tmp_path, monkeypatch, mode):
    from types import SimpleNamespace
    from test_claude_quota_episode_control import AlarmHost
    from claude_headless.observer_recovery import ObserverRecovery
    async def scenario():
        host = AlarmHost(tmp_path / 'state')
        try:
            wake, key, finalize = lifecycle_alarm(host, monkeypatch)
            separate = wake.arm(rate_limit_type='five_hour', resets_at=2000000300, program='[ap:SEPARATE]')
            separate_before = host.store.get('quota_checks', '', separate['episode_id'])
            if mode == 'missing':
                host.manager['role'] = 'worker'
                host.store.put('sessions', host.manager['native_uuid'], host.manager)
                await wake.advance(key)
                assert not host.sends and not host.reconnects
                assert ObserverRecovery(host.store).read('recovery-notice-R')['transition']['manager']['state'] == 'pending'
            elif mode == 'slow':
                host.manager['initialized'] = False
                await wake.advance(key)
                assert not host.sends and len(host.reconnects) == 1
                assert ObserverRecovery(host.store).read('recovery-notice-R')['transition']['manager']['state'] == 'starting'
            elif mode == 'changed':
                original = host.reconnect
                async def change(**kw):
                    old = await original(**kw)
                    sid = str(uuid.uuid4())
                    host.manager = {**old, 'native_uuid': sid, 'execution_id': 'cc-' + sid, 'last_input_at': 1001}
                    host.sessions[sid] = SimpleNamespace(record=host.manager, generation=1, send=host.send)
                    host.store.put('sessions', sid, host.manager)
                    return old
                host.reconnect = change
                await wake.advance(key)
                assert host.sends == []  # current Manager changed during reconnect; no historical race
                host.reconnect = original
                await wake.advance(key)
                assert host.store.get('quota_checks', '', key)['recipient'] == host.manager['execution_id']
                assert len(host.sends) == 1
            else:
                action, seam = mode.split('_')
                if seam == 'before':
                    reconnect = host.reconnect
                    async def controlled(**kw):
                        result = await reconnect(**kw)
                        wake.control_episode(key, action, 'Dan exact lifecycle control')
                        return result
                    host.reconnect = controlled
                else:
                    async def admitted(*a, **kw):
                        receipt = await host.send(*a, **kw)
                        wake.control_episode(key, action, 'Dan after exact admission')
                        receipt.update(acknowledged=True, status='acknowledged')
                        host.store.put('receipts', host.manager['native_uuid'], receipt, receipt['message_uuid'])
                        return receipt
                    host.session.send = admitted
                await wake.advance(key)
                finalize()
                episode = host.store.get('quota_checks', '', key)
                assert episode['status'] == ('deferred_by_operator' if action == 'defer' else 'cancelled_by_operator')
                assert episode['due_at'] is None and len(host.sends) == (1 if seam == 'after' else 0)
                if seam == 'after':
                    assert episode['delivery_status'] == 'sent'
            assert host.store.get('quota_checks', '', separate['episode_id']) == separate_before
            assert not host.store.records('recoveries')  # no generic continue or leaf/Conductor resume
        finally:
            host.store.db.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('cause,exit_code,retry_count', [
    ('own_turn', 3, 2), ('settlement', 3, 2), ('transition_lock', 3, 2),
    ('permanent', 3, 1), ('unknown', 3, 1), ('malformed', 3, 1), ('incomplete', 3, 1),
    ('post_stop', 3, 1), ('lost_post_stop', 1, 1), ('restored_old', 1, 1), ('real_observer', 3, 2)])
def test_wrapper_retries_only_typed_pre_stop_transients(isolated, tmp_path, monkeypatch, cause, exit_code, retry_count):
    import contextlib
    import io
    import subprocess
    import oc_claude_restart as queue
    commands, popups = [], []
    args, operation = queue.bind_operation(['--close-idle', '--operation-id=retained-R'])
    h = None
    if cause == 'real_observer':
        from test_claude_observer_switch import ObserverAwareHarness
        from claude_headless import host as host_module
        h = ObserverAwareHarness(tmp_path / 'host')
        install(monkeypatch, h)
        monkeypatch.setattr(host_module, 'witness', h.witness)
        args, operation = queue.bind_operation([*ARGS, '--operation-id=retained-R'])
    def run(command, **kw):
        commands.append(command)
        if h is not None:
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = restart.main(command[2:])
            receipt = queue.restart_envelope(stdout.getvalue(), operation)
            assert receipt is not None
            if len(commands) == 1:
                assert code == 3 and queue.retryable(receipt)
                assert 'prepare_observer_switch' in h.implemented
                h.finish_turn()
            return subprocess.CompletedProcess(command, code, stdout=stdout.getvalue(), stderr=stderr.getvalue())
        code = exit_code if len(commands) == 1 else 0
        receipt = {'schema': 'claude-restart/1', 'operation_id': operation,
            'admitted': True, 'request': {'kind': 'restart', 'account': None, 'close_idle': True,
                'namespace': {}, 'manager': {'policy': 'none'}, 'notify': False},
            'publication': {}, 'errors': [], 'requested_transition': 'refused' if code == 3 else 'succeeded',
            'phase': 'host_stop_intended' if cause == 'post_stop' else 'waiting',
            'retry': {'classification': 'settlement' if cause == 'post_stop' else cause, 'safe_pre_stop_retry': True}, 'effects': [],
            'service': {'state': 'ready' if code == 0 or cause == 'restored_old' else 'unchecked',
                        'account': {'label': 'personal'}}, 'manager': {'state': 'pending'},
            'notice': {'status': 'scheduled'}, 'direction': 'restore_old' if cause == 'restored_old' else 'normal'}
        if cause == 'incomplete':
            receipt.pop('request')
            receipt['retry']['classification'] = 'settlement'
        return subprocess.CompletedProcess(command, code, stdout=(
            'not JSON' if cause == 'malformed' else '' if cause == 'lost_post_stop' else json.dumps(receipt)), stderr='')
    monkeypatch.setattr(queue.subprocess, 'run', run)
    monkeypatch.setattr(queue.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(queue.time, 'monotonic', lambda counter=iter(range(0, 100000, 100)): next(counter))
    monkeypatch.setattr(queue, 'popup', lambda *a: popups.append(a))
    try:
        assert queue.worker('/inert/restart.sh', args, 3600, 15) == (0 if retry_count == 2 else exit_code)
    finally:
        if h is not None:
            h.close()
    assert len(commands) == retry_count
    assert all(queue.bind_operation(command[2:])[1] == operation for command in commands)
    if cause == 'restored_old':
        assert 'Switch failed; personal service restored' in popups[-1][1]
    assert all('Manager was resumed' not in text for _, text in popups)


@pytest.mark.parametrize('args', [[], ['--account=work'], ['--operation-id=explicit-public'],
                                  ['--_operation-id', 'explicit-hidden']])
def test_queue_parent_allocates_every_id_before_detach(tmp_path, monkeypatch, capsys, args):
    import sys
    from types import SimpleNamespace
    import oc_claude_restart as queue
    seen = []
    monkeypatch.setattr(queue, 'LOG', tmp_path / 'queue.log')
    monkeypatch.setattr(queue.subprocess, 'Popen', lambda command, **kw: seen.append(command) or SimpleNamespace(pid=123))
    monkeypatch.setattr(sys, 'argv', ['oc_claude_restart.py', *args])
    assert queue.main() == 0
    acknowledgement = json.loads(capsys.readouterr().out.splitlines()[0])
    assert acknowledgement['status'] == 'QUEUED' and acknowledgement['schema'] == 'claude-restart-queue/1'
    assert queue.bind_operation(seen[0])[1] == acknowledgement['operation_id']


@pytest.mark.parametrize('cancel_wait', [False, True])
def test_real_inert_worker_envelope_reaches_existing_pane_projection(tmp_path, monkeypatch, cancel_wait):
    import contextlib
    import importlib.util
    import io
    import subprocess
    import sys
    from types import SimpleNamespace
    import oc_claude_restart as queue
    pane = Path(__file__).resolve().parent.parent / 'transcription-pane'
    monkeypatch.syspath_prepend(str(pane))
    spec = importlib.util.spec_from_file_location('crr_pane_account_projection', pane / 'claude_accounts.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    native(home)
    native(home, home / '.claude-work', 'work@example.test')
    accounts._write(accounts.registry_path(), {'emails': {'personal': 'personal@example.test', 'work': 'work@example.test'}})
    monkeypatch.setattr(accounts, 'resolve_account', lambda label: {
        **accounts._folder_identity(home / '.claude-work', accounts._read(accounts.registry_path())), 'label': label})
    from test_claude_observer_switch import ObserverAwareHarness
    from claude_headless import host as host_module
    from claude_headless.restart_state import read_receipt
    h = ObserverAwareHarness(tmp_path / 'host')
    monkeypatch.setattr(host_module, 'witness', h.witness)
    install(monkeypatch, h)
    monkeypatch.setattr(__import__('claude_manager_headless'), 'run_prepare', lambda args: {'status': 'prepared'})
    calls, spawns, holds = [], [], []
    worker_alive = [True]
    monkeypatch.setattr(module, '_proc_start', lambda pid: 'inert-worker-start')
    def engine(command, **kw):
        calls.append(command)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = restart.main(command[2:])
        if code == 3:
            receipt = queue.restart_envelope(stdout.getvalue(), 'browser-recovery-R')
            assert queue.retryable(receipt)
            before, selection = read_operations(h.state_root), accounts.selector_snapshot()
            waiting = control.operation()['operation']
            assert waiting['id'] == receipt['operation_id'] and waiting['phase'] == 'waiting'
            assert waiting['canCancel'] and 'accepted input awaiting a result' in waiting['detail']
            assert waiting['finishedAt'] is None and waiting['service']['state'] != 'ready'
            holds.append(waiting)
            with pytest.raises(module.AccountError) as busy:
                control.switch('work', 'second-browser-R')
            assert (busy.value.code, busy.value.status) == (409, 'busy') and len(spawns) == 1
            assert control.status(force=True)['operation']['phase'] == 'waiting'
            worker_alive[0] = False
            assert control.operation()['operation']['phase'] == 'refused'
            worker_alive[0] = True
            assert control.operation()['operation']['phase'] == 'waiting'
            assert read_operations(h.state_root) == before and accounts.selector_snapshot() == selection
            assert h.starts == 0 and not h.kills
            if cancel_wait:
                assert control.cancel('browser-recovery-R')['operation']['phase'] == 'cancelled'
                assert not control.operation()['operation']['canCancel']
            else:
                h.finish_turn()
        return subprocess.CompletedProcess(command, code, stdout=stdout.getvalue(), stderr=stderr.getvalue())
    monkeypatch.setattr(queue.subprocess, 'run', engine)
    monkeypatch.setattr(queue.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(queue.time, 'monotonic', lambda counter=iter(range(0, 100000, 100)): next(counter))
    monkeypatch.setattr(queue, 'popup', lambda *a: None)
    account_reader = lambda: {'active': accounts.selected_account(), 'accounts': [
        {'label': 'personal', 'enrolled': True}, {'label': 'work', 'enrolled': True}]}
    def spawn(record):
        spawns.append(record)
        return SimpleNamespace(pid=7654321, poll=lambda: None)
    control = module.AccountSwitch(state_path=tmp_path / 'pane.json', log_path=tmp_path / 'pane.log',
        run_status=account_reader, spawn=spawn, state_root=h.state_root,
        alive=lambda pid, start: worker_alive[0] and pid == 7654321 and start == 'inert-worker-start',
        read_operation=lambda operation: read_receipt(h.state_root, operation, public=True))
    control.switch('work', 'browser-recovery-R')
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log):
            assert queue.worker('/inert/restart.sh', ['--account=work', '--close-idle',
                '--operation-id=browser-recovery-R'], 3600, 15,
                control=restart.RestartControl(control._read_record()['controlPath'])) == (4 if cancel_wait else 0)
        control.log_path.write_text(log.getvalue())
        projected = control.operation()['operation']
        assert len(holds) == 1 and projected['receipt']['operation_id'] == 'browser-recovery-R'
        assert len(spawns) == 1 and '/tmp/' not in json.dumps(projected)
        if cancel_wait:
            assert projected['phase'] == 'cancelled' and len(calls) == 1
            assert h.starts == 0 and not h.kills and 'close_input' not in h.client.methods
        else:
            assert projected['phase'] == 'active' and projected['service']['state'] == 'ready'
            assert projected['manager']['state'] == 'pending' and len(calls) == 2
            assert h.starts == 1 and h.kills == [HOST_PID]
    finally:
        h.close()
