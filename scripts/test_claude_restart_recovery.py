"""Reopened lifecycle fault tests: synthetic native state, no sockets/signals/inference.

All actions go through the changed production owners. Crash seams are BaseExceptions so
the coordinator cannot run a restoration catch; ordinary faults exercise that catch too.
"""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_headless import accounts, restart
from claude_headless.observer_recovery import ObserverRecovery
from claude_headless.restart_state import read_operations, journal
from test_claude_accounts import native
from test_claude_headless_restart import Harness, rec, wit, HOST_PID, NEW_PID, CANONICAL, stored_manager_record


class CoordinatorGone(BaseException):
    pass


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    native(home)
    destination = home / '.claude-work'
    native(home, destination, 'work@example.test')
    accounts._write(accounts.registry_path(), {'emails': {
        'personal': 'personal@example.test', 'work': 'work@example.test'}, 'directories': [str(destination)]})
    monkeypatch.setattr(accounts, 'auth_status', lambda directory=None: {
        'loggedIn': True, 'email': 'work@example.test' if directory else 'personal@example.test',
        'authMethod': 'claude.ai', 'subscriptionType': 'max'})
    old = accounts.selected_account()
    accounts._write(accounts.selector_path(), {**{k: old[k] for k in ('label', 'config_dir', 'identity')}, 'epoch': 7})
    h = Harness([], state_root=str(tmp_path / 'host'))
    h.destination, h.home, h.operation_id = destination, home, 'recovery-R'
    return h


def invoke(h, *, recover=False, account='work', control=None, manager_policy=None):
    return restart.guarded_restart(h.client, state_root=h.state_root, close_idle=True,
        operation_id=h.operation_id, account=None if recover else account,
        recover=recover, control=control, manager_policy=manager_policy,
        witness_fn=h.witness, kill_fn=h.kill, starter=h.starter,
        clock=h.clock, sleep=h.sleep, interval=1, timeout=3)


def row(h):
    # A NEW read-only connection every time, never the coordinator's in-memory report.
    return read_operations(h.state_root, h.operation_id)


def pre_stop_record(h, monkeypatch, outcome):
    """Drive a real recorded outcome or the retained no-outcome gate/phase gap; no signals."""
    gate = restart.RestartControl(Path(h.state_root).parent / 'pre-stop-control.json')
    gate.initialize()
    if outcome == 'refused':
        h.rows = [rec(9001, state='ready', owner='host'), rec(9002, state='initializing', owner='host',
            execution_id='cc-22222222-2222-4222-8222-222222222222',
            native_uuid='22222222-2222-4222-8222-222222222222')]
        h.witnesses.update({9001: wit(9001), 9002: wit(9002)})
    elif outcome == 'cancelled':
        gate.cancel()
    if outcome == 'interrupted':
        def gap(self):
            self.control.begin_stopping()
            raise CoordinatorGone('stopping gate won; no phase acknowledgement/outcome')
        with monkeypatch.context() as m:
            m.setattr(restart.RestartLifecycle, 'before_effects', gap)
            with pytest.raises(CoordinatorGone):
                invoke(h, control=gate)
    else:
        with pytest.raises(restart.RestartRefused) as error:
            invoke(h, control=gate)
        assert error.value.report['requested_transition'] == outcome
    assert not row(h)['transition'].get('host_stop_intended') and not h.kills and h.starts == 0
    return gate


def crash_after_stop(h):
    original = h.kill
    def stop(pid):
        original(pid)
        raise CoordinatorGone('stop succeeded; reply/phase write lost')
    h.kill = stop
    try:
        with pytest.raises(CoordinatorGone):
            invoke(h)
    finally:
        h.kill = original


@pytest.mark.parametrize('seam', ['before_commit', 'committed_reply_lost'])
def test_intent_failure_precedes_every_stopping_effect(recovery, monkeypatch, seam):
    h = recovery
    h.rows = [rec(9001, state='ready', owner='host')]
    h.witnesses[9001] = wit(9001)
    if seam == 'before_commit':
        target, name = ObserverRecovery, 'admit_transition'
        def fail(*a, **kw):
            raise OSError('intent storage unavailable')
    else:
        target, name = restart.RestartLifecycle, 'admit'
        original = target.admit
        def fail(self, *a, **kw):
            original(self, *a, **kw)
            raise OSError('intent committed; acknowledgement lost')
    with monkeypatch.context() as m:
        m.setattr(target, name, fail)
        with pytest.raises(restart.RestartRefused):
            invoke(h)
    assert not h.kills and h.starts == 0 and 'close_input' not in h.client.methods
    record = row(h)
    assert record is None if seam == 'before_commit' else record['transition']['request']['account'] == 'work'
    result = invoke(h)
    assert result['operation_id'] == h.operation_id and h.starts == 1 and len(h.kills) == 1


@pytest.mark.parametrize('winner', ['cancel', 'stop_then_crash', 'gate_write_phase_gap'])
def test_cancel_and_stopping_gate_race_preserves_effect_truth(recovery, monkeypatch, winner):
    h = recovery
    h.rows = [rec(9001, state='ready', owner='host')]
    h.witnesses[9001] = wit(9001)
    control = restart.RestartControl(Path(h.state_root).parent / 'control.json')
    control.initialize()
    if winner == 'cancel':
        control.cancel()
        with pytest.raises(restart.RestartRefused) as error:
            invoke(h, control=control)
        assert error.value.report['requested_transition'] == 'cancelled'
        assert h.starts == 0 and not h.kills and 'close_input' not in h.client.methods
    else:
        original = restart.RestartLifecycle.before_effects
        def crash(self):
            original(self)
            raise CoordinatorGone('gate stopping committed, no close issued')
        with monkeypatch.context() as m:
            if winner == 'gate_write_phase_gap':
                update = ObserverRecovery.transition_update
                def lost_phase(self, *a, **kw):
                    if kw.get('fields', {}).get('phase') == 'settling':
                        raise CoordinatorGone('gate stopping won before lifecycle phase commit')
                    return update(self, *a, **kw)
                m.setattr(ObserverRecovery, 'transition_update', lost_phase)
            else:
                m.setattr(restart.RestartLifecycle, 'before_effects', crash)
            with pytest.raises(CoordinatorGone):
                invoke(h, control=control)
        assert control.phase() == 'stopping' and not control.cancel()
        assert row(h)['transition']['phase'] == ('waiting' if winner == 'gate_write_phase_gap' else 'settling')
        assert not h.kills and 'close_input' not in h.client.methods
        original_id = h.operation_id
        h.operation_id = 'not-another-operation'
        with pytest.raises(restart.RestartRefused, match='unfinished physical transition'):
            invoke(h)
        h.operation_id = original_id
        result = invoke(h, recover=True)
        assert result['requested_transition'] == 'succeeded'
        assert h.starts == 1 and len(h.client.params_for('close_input')) == 1


def test_partial_close_or_retirement_lost_reply_reconciles_exact_incarnation(recovery):
    h = recovery
    h.rows = [rec(9001, state='ready', owner='host')]
    h.witnesses[9001] = wit(9001)
    original = h.client.handler
    def lost(method, params):
        value = original(method, params)
        if method == 'close_input':
            raise ConnectionError('EOF admitted before reply lost')
        return value
    h.client.handler = lost
    with pytest.raises(restart.RestartRefused):
        invoke(h)
    h.client.handler = original
    h.rows.append(rec(9002, execution_id='cc-productive', owner='host', state='ready', pending_message_uuids=['work']))
    h.witnesses[9002] = wit(9002)
    with pytest.raises(restart.RestartRefused):
        invoke(h, recover=True)
    effects = row(h)['transition']['effects'].values()
    [close] = [effect for effect in effects if effect['kind'] == 'close_native']
    assert close['target']['generation'] == 2
    assert any(event['status'] == 'uncertain' for event in close['events'])
    assert close['events'][-1]['status'] == 'observed_complete'  # exact later native receipt reconciles the gap
    assert len(h.client.params_for('close_input')) == 1 and not h.kills and h.starts == 0
    assert accounts.selected_account()['epoch'] == 7


@pytest.mark.parametrize('seam', ['before_signal', 'after_signal', 'pid_reused', 'drain_held', 'uuid_lock_held'])
def test_recover_stop_intent_rechecks_old_witness_and_all_native_owners(recovery, seam):
    h = recovery
    original = h.kill
    if seam == 'before_signal':
        h.kill = lambda pid: (_ for _ in ()).throw(CoordinatorGone('before syscall'))
        with pytest.raises(CoordinatorGone):
            invoke(h)
        h.kill = original
    else:
        crash_after_stop(h)
    assert row(h)['transition']['host_stop_intended']
    held = None
    if seam == 'pid_reused':
        h.witnesses[HOST_PID] = wit(HOST_PID, 'reused')
    if seam in ('drain_held', 'uuid_lock_held'):
        session = rec(9001)
        if seam == 'drain_held':
            session.update(state='exited_draining', owner='draining')
        with journal(h.state_root) as owner:
            owner.store.put('sessions', session['native_uuid'], session)
        if seam == 'uuid_lock_held':
            from claude_headless.host import owner_lock
            folder = Path(h.state_root) / session['native_uuid']
            folder.mkdir()
            held = owner_lock(folder / 'owner.lock')
    try:
        if seam in ('drain_held', 'uuid_lock_held'):
            with pytest.raises(restart.RestartError):
                invoke(h, recover=True)
            assert h.starts == 0
        else:
            result = invoke(h, recover=True)
            assert result['service']['state'] == 'ready' and h.starts == 1
            assert h.kills == [HOST_PID]  # successful already-applied signal never repeated; reused PID untouched
    finally:
        if held is not None:
            os.close(held)


def test_plain_restart_recovers_absent_host_under_same_id(recovery, monkeypatch, capsys):
    h = recovery
    original = h.kill
    def gone(pid):
        original(pid)
        raise CoordinatorGone()
    h.kill = gone
    with pytest.raises(CoordinatorGone):
        invoke(h, account=None)
    h.kill = original
    before = accounts.selector_path().read_bytes()
    monkeypatch.setattr(restart, 'open_client', lambda args: h.client)  # read-only failed health is allowed; no effects
    monkeypatch.setattr(restart, '_posix_witness', h.witness)
    assert restart.main(['--state-root', h.state_root, '--status', h.operation_id]) == 0
    assert json.loads(capsys.readouterr().out)['operation_id'] == h.operation_id
    assert restart.main(['--state-root', h.state_root, '--operations']) == 0
    assert json.loads(capsys.readouterr().out)['operations'][0]['operation_id'] == h.operation_id
    result = invoke(h, recover=True)
    again = invoke(h, recover=True)
    assert result['service']['state'] == 'ready' and again['replayed_completed_operation']
    assert accounts.selector_path().read_bytes() == before and h.starts == 1 and h.kills == [HOST_PID]
    assert row(h)['transition']['request']['kind'] == 'restart'


@pytest.mark.parametrize('partial', [False, True])
def test_apply_failure_restores_old_service_without_reapplying_target(recovery, monkeypatch, partial):
    h = recovery
    (h.home / '.claude/settings.json').write_text('{}')
    for path in (h.home / '.claude/settings.json.bak', h.destination / 'settings.json.bak'):
        path.write_text(str(path.parent.name))
    original, applied = accounts.prepare_shared_directory, []
    def prepare(directory, **kw):
        if kw.get('check_only'):
            return original(directory, **kw)
        applied.append('target')
        if partial:
            original(directory, **kw)
        raise OSError('target preparation failed after stop')
    monkeypatch.setattr(accounts, 'prepare_shared_directory', prepare)
    with pytest.raises(restart.RestartError) as failure:
        invoke(h)
    result = failure.value.report
    assert result['service']['state'] == 'ready' and result['service']['account']['label'] == 'personal'
    assert result['requested_transition'] == 'failed' and result['direction'] == 'restore_old'
    assert accounts.selected_account()['epoch'] == 7 and h.starts == 1
    with journal(h.state_root) as owner:
        [notice] = owner.store.records('quota_checks')
        assert notice['status'] == 'scheduled' and notice['due_at'] is not None
    replay = invoke(h, recover=True)
    assert replay['requested_transition'] == 'failed' and applied == ['target'] and h.starts == 1
    assert (h.home / '.claude/settings.json.bak').read_text() == '.claude'
    assert (h.destination / 'settings.json.bak').read_text() == '.claude-work'


@pytest.mark.parametrize('seam', ['writer_before', 'file_fsync', 'replace_before'])
def test_selector_exception_before_replace_restores_old(recovery, monkeypatch, seam):
    h = recovery
    old = accounts.selector_path().read_bytes()
    original = accounts._write
    def write(path, value):
        if Path(path) == accounts.selector_path():
            raise OSError('before selector replacement')
        return original(path, value)
    if seam == 'writer_before':
        monkeypatch.setattr(accounts, '_write', write)
    elif seam == 'file_fsync':
        fsync = accounts.os.fsync
        def failed_sync(fd):
            target = Path(os.readlink('/proc/self/fd/' + str(fd)))
            if target.name.startswith('.claude-account.json.'):
                raise OSError('selector file fsync before replacement')
            return fsync(fd)
        monkeypatch.setattr(accounts.os, 'fsync', failed_sync)
    else:
        replace = accounts.os.replace
        def failed_replace(source, target):
            if Path(target) == accounts.selector_path():
                raise OSError('selector replace failed before effect')
            return replace(source, target)
        monkeypatch.setattr(accounts.os, 'replace', failed_replace)
    with pytest.raises(restart.RestartError) as error:
        invoke(h)
    assert error.value.report['direction'] == 'restore_old'
    assert error.value.report['service']['state'] == 'ready'
    assert accounts.selector_path().read_bytes() == old and h.starts == 1
    assert any(e['kind'] == 'selector_commit' and e['events'][-1]['status'] == 'uncertain'
               for e in row(h)['transition']['effects'].values())


@pytest.mark.parametrize('seam', ['reply', 'parent_sync', 'writer_reply'])
def test_selector_exception_after_replace_continues_destination_once(recovery, monkeypatch, seam):
    h = recovery
    replacements = []
    original = accounts.os.replace
    def replace(source, destination):
        result = original(source, destination)
        if Path(destination) == accounts.selector_path():
            replacements.append('replace')
            if seam == 'reply':
                raise OSError('selector replaced; syscall reply lost')
        return result
    monkeypatch.setattr(accounts.os, 'replace', replace)
    if seam == 'writer_reply':
        writer = accounts._write
        def write_then_lost(path, value):
            result = writer(path, value)
            if Path(path) == accounts.selector_path():
                raise OSError('write fsync replace parent-sync and readback succeeded before reply lost')
            return result
        monkeypatch.setattr(accounts, '_write', write_then_lost)
    sync = accounts._sync_parent
    fail = [True]
    def parent_sync(path):
        if Path(path) == accounts.selector_path() and fail:
            fail.pop()
            raise OSError('parent fsync interrupted after replacement')
        return sync(path)
    if seam == 'parent_sync':
        monkeypatch.setattr(accounts, '_sync_parent', parent_sync)
    result = invoke(h)
    again = invoke(h, recover=True)
    assert result['requested_transition'] == 'succeeded' and result['direction'] == 'continue_destination'
    assert result['service']['account']['label'] == 'work' and again['replayed_completed_operation']
    assert accounts.selector_snapshot()['value']['operation_id'] == h.operation_id
    assert accounts.selected_account()['epoch'] == 8 and replacements == ['replace'] and h.starts == 1
    assert row(h)['transition']['errors']


@pytest.mark.parametrize('change', ['unreadable', 'malformed', 'same_label_new_epoch', 'superseded', 'native_changed', 'missing'])
def test_selector_unreadable_changed_or_superseded_is_not_old(recovery, monkeypatch, change):
    h = recovery
    crash_after_stop(h)
    original = accounts.selector_snapshot
    if change == 'unreadable':
        monkeypatch.setattr(accounts, 'selector_snapshot', lambda: (_ for _ in ()).throw(OSError('selector unreadable')))
    elif change == 'malformed':
        accounts.selector_path().write_text('[]')
    elif change == 'missing':
        accounts.selector_path().unlink()
    elif change == 'native_changed':
        native(h.home, email='someone-else@example.test')
    else:
        value = accounts._read(accounts.selector_path())
        value.update(epoch=9, operation_id='someone-else' if change == 'superseded' else h.operation_id)
        accounts._write(accounts.selector_path(), value)
    with pytest.raises(restart.RestartError):
        invoke(h, recover=True)
    assert h.kills == [HOST_PID] and h.starts == 0
    assert row(h)['transition']['requested_transition'] != 'succeeded'


def test_request_identity_conflict_and_other_unfinished_operation_are_not_new_stops(recovery):
    h = recovery
    crash_after_stop(h)
    with pytest.raises(restart.RestartRefused, match='different restart request'):
        invoke(h, account=None)
    h.operation_id = 'different-R'
    with pytest.raises(restart.RestartRefused, match='unfinished physical transition'):
        invoke(h)
    assert h.kills == [HOST_PID] and h.starts == 0


@pytest.mark.parametrize('seam', ['reconnect_reply', 'ready_write_reply'])
def test_same_uuid_reconnect_lost_reply_observes_generation_without_relaunch(recovery, monkeypatch, seam):
    h = recovery
    h.state_record = stored_manager_record(Path(h.state_root).parent)
    snapshots = Path(h.state_record['run_dir'])
    for name in ('settings.json', 'mcp.json', 'agents.json'):
        (snapshots / name).write_bytes(('stored-' + name).encode())
    before = {p.name: p.read_bytes() for p in snapshots.iterdir()}
    policy = {'policy': 'exact', 'resume': CANONICAL}
    result = invoke(h, account=None, manager_policy=policy)
    dispatch, reads, sleeps = h.client.handler, [], []
    def lost(method, params):
        answer = dispatch(method, params)
        if method == 'reconnect' and seam == 'reconnect_reply':
            h.state_record.update(state='initializing', initialized=False)
            raise ConnectionError('reconnect admitted g3 before reply lost')
        if method == 'state' and h.state_record.get('generation') == 3:
            reads.append(3)
            if len(reads) >= 3:
                h.state_record.update(state='ready', initialized=True)
        return answer
    h.client.handler = lost
    def observe(seconds):
        sleeps.append(row(h)['transition']['manager']['state'])
        assert len(h.client.params_for('reconnect')) == 1
    if seam == 'ready_write_reply':
        original, failed = ObserverRecovery.transition_update, []
        def write(self, operation_id, **kw):
            answer = original(self, operation_id, **kw)
            if kw.get('fields', {}).get('manager', {}).get('state') == 'ready' and not failed:
                failed.append(True)
                raise ConnectionError('ready phase committed before reply lost')
            return answer
        monkeypatch.setattr(ObserverRecovery, 'transition_update', write)
    result = restart.complete_followup(h.client, h.state_root, h.operation_id, sleep=observe, witness_fn=h.witness)
    assert result['manager']['state'] == 'ready' and result['manager']['generation'] == 3
    assert len(h.client.params_for('reconnect')) == 1 and not h.client.params_for('launch')
    assert not {'send', 'steer'} & set(h.client.methods)
    assert before == {p.name: p.read_bytes() for p in snapshots.iterdir()}
    if seam == 'reconnect_reply':
        assert 'starting' in sleeps
    again = restart.complete_followup(h.client, h.state_root, h.operation_id, witness_fn=h.witness)
    assert again['manager']['state'] == 'ready' and len(h.client.params_for('reconnect')) == 1


@pytest.mark.parametrize('problem', ['snapshot_missing', 'readiness_failed', 'fresh_reply_lost'])
def test_manager_failure_is_separate_from_switched_ready_host(recovery, problem):
    h = recovery
    policy = {'policy': 'fresh', 'model': 'claude-sonnet-5-5', 'effort': 'low'} if problem == 'fresh_reply_lost' else {
        'policy': 'exact', 'resume': CANONICAL}
    h.state_record = None if problem == 'fresh_reply_lost' else stored_manager_record(Path(h.state_root).parent)
    if problem == 'snapshot_missing':
        (Path(h.state_record['run_dir']) / 'prompt.md').unlink()
    result = invoke(h, manager_policy=policy)
    selected = accounts.selector_path().read_bytes()
    dispatch = h.client.handler
    def fault(method, params):
        value = dispatch(method, params)
        if method == 'reconnect' and problem == 'readiness_failed':
            h.state_record.update(state='launch_blocked', initialized=False)
        if method == 'launch' and problem == 'fresh_reply_lost':
            raise ConnectionError('fresh UUID admitted before reply lost')
        return value
    h.client.handler = fault
    result = restart.complete_followup(h.client, h.state_root, h.operation_id, witness_fn=h.witness)
    assert result['service']['state'] == 'ready' and result['service']['account']['label'] == 'work'
    assert accounts.selector_path().read_bytes() == selected and h.starts == 1 and h.kills == [HOST_PID]
    if problem == 'fresh_reply_lost':
        assert result['manager']['state'] == 'ready'
        identity = row(h)['transition']['recipe']['fresh_manager_uuid']
        assert [p['native_uuid'] for p in h.client.params_for('launch')] == [identity]
        restart.complete_followup(h.client, h.state_root, h.operation_id, witness_fn=h.witness)
        assert len(h.client.params_for('launch')) == 1
    else:
        assert result['manager']['state'] == 'failed' and result['requested_transition'] == 'succeeded'
        assert result['errors']


@pytest.mark.parametrize('entry', ['engine', 'cli'])
def test_later_journal_failure_does_not_disable_authorized_old_service_restoration(recovery, monkeypatch, capsys, entry):
    h = recovery
    failed = [False]
    update = ObserverRecovery.transition_update
    def storage(self, *a, **kw):
        if failed[0]:
            raise OSError('journal unavailable after shutdown')
        return update(self, *a, **kw)
    monkeypatch.setattr(ObserverRecovery, 'transition_update', storage)
    kill, starter = h.kill, h.starter
    def stopped(pid):
        kill(pid)
        failed[0] = True
    h.kill = stopped
    def child():
        answer = starter()
        record = row(h)['transition']
        accounts._write(Path(h.state_root) / 'host-start-receipt.json', {
            'state_root': h.state_root, 'socket_path': h.client.socket_path, 'host_pid': answer['host_pid'],
            'process_witness': answer['process_witness'], 'account': answer['account'],
            'operation_id': h.operation_id, 'start_token': record['recipe']['start_tokens']['restore_old'],
            'phase': 'bound'})
        return answer
    h.starter = child
    if entry == 'engine':
        with pytest.raises(restart.RestartError) as error:
            invoke(h)
        result = error.value.report
    else:
        monkeypatch.setattr(restart, 'open_client', lambda args: h.client)
        monkeypatch.setattr(restart, '_posix_witness', h.witness)
        monkeypatch.setattr(restart, '_sigterm', h.kill)
        monkeypatch.setattr(restart, 'start_host_fresh', lambda *a, **kw: child())
        monkeypatch.setattr(__import__('claude_manager_headless'), 'run_prepare', lambda args: {'status': 'prepared'})
        assert restart.main(['--account', 'work', '--close-idle', '--operation-id', h.operation_id,
                             '--state-root', h.state_root]) == 1
        result = json.loads(capsys.readouterr().out)
    assert h.starts == 1 and accounts.selected_account()['label'] == 'personal'
    assert result['direction'] == 'restore_old' and result['requested_transition'] == 'failed'
    assert result['service']['state'] == 'ready' and result['service']['durable'] is False
    assert result['finalization_pending'] and any(e.get('durable') is False for e in result['errors'])
    failed[0] = False
    result = invoke(h, recover=True)
    assert result['service']['state'] == 'ready' and result['requested_transition'] == 'failed'
    assert result['service']['evidence']['start_binding'] == 'recorded_attempt'
    assert h.starts == 1 and h.kills == [HOST_PID]


def test_retirement_lost_reply_retains_exact_obligation_before_later_real_work(recovery):
    from test_claude_observer_switch import SwitchHarness, invoke as observer_invoke
    h = SwitchHarness(Path(recovery.state_root))
    dispatch = h.client.handler
    def lost(method, params):
        answer = dispatch(method, params)
        if method == 'retire_observer_owner':
            raise ConnectionError('exact observer owner ended before reply lost')
        return answer
    h.client.handler = lost
    with pytest.raises(restart.RestartRefused):
        observer_invoke(h)
    original = read_operations(h.state_root, 'switch-one')
    assert original['observations'][0]['status'] == 'stopped'
    h.client.handler = dispatch
    h.rows.append(rec(9002, execution_id='cc-real-work', owner='host', state='ready', pending_message_uuids=['genuine']))
    h.witnesses[9002] = wit(9002)
    with pytest.raises(restart.RestartRefused):
        observer_invoke(h, recover=True)
    retained = read_operations(h.state_root, 'switch-one')
    assert retained['pages']['master']['ap_id'] == original['pages']['master']['ap_id']
    assert retained['observations'][0]['status'] == 'stopped'
    assert len([event for event in h.events if event[0] == 'retire']) == 1
    assert h.kills == [] and h.starts == 0


def test_plain_success_after_pre_stop_hold_does_not_invent_a_failure_notice(recovery):
    h = recovery
    h.rows = [rec(9001, state='ready', owner='host', pending_message_uuids=['own-turn'])]
    h.witnesses[9001] = wit(9001)
    with pytest.raises(restart.RestartRefused):
        invoke(h, account=None)
    h.rows[0]['pending_message_uuids'] = []
    result = invoke(h, account=None)
    assert result['service']['state'] == 'ready' and result['notice']['status'] == 'not_requested'
    with journal(h.state_root) as owner:
        [episode] = owner.store.records('quota_checks')
        assert episode['due_at'] is None and episode['status'] == 'pending_switch'
    assert not {'send', 'steer'} & set(h.client.methods)


@pytest.mark.parametrize('outcome', ['refused', 'cancelled'])
def test_finished_pre_stop_outcome_releases_different_request_and_keeps_history(recovery, monkeypatch, outcome):
    from claude_headless import host as host_module
    from claude_headless.restart_state import physical_pending, read_receipt
    h = recovery
    monkeypatch.setattr(host_module, 'witness', h.witness)
    gate = pre_stop_record(h, monkeypatch, outcome)
    original_id, original = h.operation_id, row(h)
    assert not physical_pending(original)
    if outcome == 'refused':
        assert gate.phase() == 'stopping' and original['transition']['physical_active']
        [effect] = original['transition']['effects'].values()
        assert effect['kind'] == 'close_native' and effect['target']['pid'] == 9001
        assert len(h.client.params_for('close_input')) == 1
        h.rows[1].update(state='exited', owner='gone')
        h.witnesses.pop(9002)
    h.operation_id = 'new-after-' + outcome
    result = invoke(h)
    assert result['operation_id'] == h.operation_id and result['requested_transition'] == 'succeeded'
    assert h.starts == 1 and h.kills == [HOST_PID]
    assert read_operations(h.state_root, original_id) == original
    old = read_receipt(h.state_root, original_id)
    assert old['requested_transition'] == outcome and old['service']['state'] != 'ready'
    assert gate.phase() == ('stopping' if outcome == 'refused' else 'cancelled')


def test_finished_partial_refusal_same_id_retry_does_not_repeat_its_close(recovery, monkeypatch):
    h = recovery
    gate = pre_stop_record(h, monkeypatch, 'refused')
    original = row(h)
    h.rows[1].update(state='exited', owner='gone')
    h.witnesses.pop(9002)
    result = invoke(h, control=gate)
    assert result['operation_id'] == h.operation_id and result['requested_transition'] == 'succeeded'
    assert len(h.client.params_for('close_input')) == 1 and h.starts == 1 and h.kills == [HOST_PID]
    assert row(h)['pages'] == original['pages'] and gate.phase() == 'stopping'
    with journal(h.state_root) as owner:
        [episode] = owner.store.records('quota_checks')
        assert episode['message_uuid'] == result['notice']['message_uuid']
    [old_close] = original['transition']['effects'].values()
    current = row(h)['transition']['effects'][old_close['action_id']]
    assert current['events'][:len(old_close['events'])] == old_close['events']


def test_same_id_reattempt_gap_is_not_the_preceding_finished_refusal(recovery, monkeypatch):
    from claude_headless import host as host_module
    from claude_headless.host_start import start_admission
    from claude_headless.restart_state import physical_pending
    h = recovery
    gate = pre_stop_record(h, monkeypatch, 'refused')
    original = row(h)
    h.rows[1].update(state='exited', owner='gone')
    h.witnesses.pop(9002)
    monkeypatch.setattr(host_module, 'witness', h.witness)
    before_effects = restart.RestartLifecycle.before_effects
    def interrupted(lifecycle):
        before_effects(lifecycle)
        raise CoordinatorGone('new same-ID attempt admitted stopping; no new outcome')
    with monkeypatch.context() as m:
        m.setattr(restart.RestartLifecycle, 'before_effects', interrupted)
        with pytest.raises(CoordinatorGone):
            invoke(h, control=gate)
    current = row(h)
    assert current['transition']['requested_transition'] == 'pending' and physical_pending(current)
    assert current['transition']['errors'][:len(original['transition']['errors'])] == original['transition']['errors']
    assert current['pages'] == original['pages'] and gate.phase() == 'stopping'
    with pytest.raises(RuntimeError, match='unfinished physical transition'):
        with start_admission(h.state_root):
            pytest.fail('the reattempt is not the completed refusal')
    operation = h.operation_id
    h.operation_id = 'not-after-reattempt-gap'
    with pytest.raises(restart.RestartRefused, match='unfinished physical transition'):
        invoke(h)
    h.operation_id = operation
    result = invoke(h, recover=True)
    assert result['requested_transition'] == 'succeeded' and h.starts == 1
    assert h.kills == [HOST_PID] and len(h.client.params_for('close_input')) == 1


@pytest.mark.parametrize('outcome', ['refused', 'cancelled', 'interrupted'])
def test_absent_old_host_without_stop_intent_recovers_to_recorded_pre_stop_outcome(recovery, monkeypatch, capsys, outcome):
    from claude_headless import host as host_module
    from claude_headless.restart_state import physical_pending
    h = recovery
    monkeypatch.setattr(host_module, 'witness', h.witness)
    gate = pre_stop_record(h, monkeypatch, outcome)
    original = row(h)
    h.witnesses.pop(HOST_PID)
    h.phase = 'new'  # endpoint refuses health until a fixture starter runs
    assert not physical_pending(row(h))
    monkeypatch.setattr(restart, 'open_client', lambda args: h.client)
    monkeypatch.setattr(restart, '_posix_witness', h.witness)
    monkeypatch.setattr(restart, '_sigterm', lambda pid: pytest.fail('no signal authorized'))
    monkeypatch.setattr(restart, 'start_host_fresh', lambda *a, **kw: pytest.fail('pre-stop recover is not a new start'))
    before_calls = len(h.client.calls)
    for _ in range(2):
        assert restart.main(['--state-root', h.state_root, '--recover', h.operation_id]) == (4 if outcome == 'cancelled' else 3)
        receipt = json.loads(capsys.readouterr().out)
        expected = 'cancelled' if outcome == 'cancelled' else 'refused'
        assert receipt['operation_id'] == h.operation_id and receipt['requested_transition'] == expected
        assert receipt['retry']['safe_pre_stop_retry'] is False
        assert 'claude_host.sh start' in receipt['retry']['reason']
        final = row(h)
        assert final['transition']['requested_transition'] == expected
        assert final['transition']['effects'] == original['transition']['effects']
        assert final['pages'] == original['pages'] and final['observations'] == original['observations']
        assert not physical_pending(final)
    assert len(h.client.calls) == before_calls and not h.kills and h.starts == 0
    assert gate.phase() == ('cancelled' if outcome == 'cancelled' else 'stopping')
    before = row(h)
    assert restart.main(['--state-root', h.state_root, '--status', h.operation_id]) == 0
    assert json.loads(capsys.readouterr().out)['requested_transition'] == expected
    assert restart.main(['--state-root', h.state_root, '--operations']) == 0
    assert [r['operation_id'] for r in json.loads(capsys.readouterr().out)['operations']] == [h.operation_id]
    assert row(h) == before and accounts.selected_account()['epoch'] == 7


@pytest.mark.parametrize('evidence', ['alive', 'unreadable', 'missing_witness'])
def test_unfinished_gate_gap_keeps_ownership_without_exact_absence_proof(recovery, monkeypatch, evidence):
    from claude_headless import host as host_module
    from claude_headless.host_start import start_admission
    from claude_headless.restart_state import physical_pending
    h = recovery
    pre_stop_record(h, monkeypatch, 'interrupted')
    if evidence == 'unreadable':
        def unknown(pid):
            raise OSError('not absence')
        h.witness = unknown
    elif evidence == 'missing_witness':
        with journal(h.state_root) as owner:
            record = owner.read(h.operation_id)
            record['transition']['recipe']['old_host']['process_witness'] = None
            owner._save(record)
    monkeypatch.setattr(host_module, 'witness', h.witness)
    assert physical_pending(row(h))
    with pytest.raises(RuntimeError, match='unfinished physical transition'):
        with start_admission(h.state_root):
            pytest.fail('unfinished live/uncertain gap must not pass admission')
