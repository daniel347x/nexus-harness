"""Offline restart/report integration; native cancellation is qualified separately."""
import uuid
from types import SimpleNamespace

import pytest

from claude_headless import accounts, restart
from claude_headless.observer_recovery import ObserverRecovery
from claude_headless.observations import ObservationLedger
from claude_headless.store import Store
from test_claude_headless_restart import Harness, rec, HOST_PID
from test_claude_accounts import native
from test_claude_observer_recovery import observation


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    native(home)
    native(home, home / '.claude-work', 'work@example.test')
    accounts._write(accounts.registry_path(), {'emails': {'personal': 'personal@example.test', 'work': 'work@example.test'}})
    monkeypatch.setattr(accounts, 'resolve_account', lambda label: {
        **accounts._folder_identity(home / '.claude-work', accounts._read(accounts.registry_path())), 'label': label})


class SwitchHarness(Harness):
    def __init__(self, root, *, outcome='ended', blockers=None):
        super().__init__([rec(8001, owner='host', state='ready', background_unsettled=True,
            background_tasks={'task': {'run_id': 'run', 'task_type': 'local_bash', 'description': 'quota timer'}})], state_root=str(root))
        self.implemented = ['prepare_observer_switch', 'observer_recoveries']
        self.outcome, self.blockers = outcome, blockers or []
        self.operation = None

    def dispatch(self, method, params):
        if method == 'health':
            return {**super().dispatch(method, params), 'implemented': self.implemented}
        if method == 'observer_recoveries':
            if not __import__('pathlib').Path(self.state_root, 'host.sqlite3').exists():
                return None
            store = Store(self.state_root)
            record = ObserverRecovery(store).read(params['operation_id'])
            store.db.close()
            return record
        if method == 'observer_switch_inventory':
            return {'blockers': self.blockers}
        if method == 'prepare_observer_switch':
            self.operation = params['operation_id']
            store = Store(self.state_root)
            rows = [] if self.rows[0]['owner'] == 'gone' else [observation(status='armed')]
            ObserverRecovery(store).stage(self.operation, rows, requested_account=params['requested_account'])
            store.db.close()
            return {'outcome': 'prepared', 'candidates': [] if not rows else [
                {'execution_id': self.rows[0]['execution_id'], 'generation': 2}]}
        if method == 'retire_observer_owner':
            self.events.append(('retire', params))
            if self.outcome == 'ended':
                self.rows[0].update(owner='gone', state='exited', background_tasks={}, background_unsettled=False)
                self.witnesses.pop(8001)
                store = Store(self.state_root)
                stopped = observation(status='stopped')
                stopped['retirement'] = {'operation_id': self.operation, 'phase': 'owner_exited'}
                store.put('observations', '', stopped, 'one')
                store.db.close()
            return {'outcome': 'retiring'}
        if method == 'observer_retirement':
            return {'outcome': self.outcome}
        if method == 'finish_observer_switch':
            return {}
        return super().dispatch(method, params)


class ObserverAwareHarness(Harness):
    """Real ledger inventory/prepare over an inert open-turn Manager, with owned fake witnesses."""

    def __init__(self, root, *, hold_at='inventory', mixed=False):
        record = rec(9001, owner='host', state='ready', native_state_required=True,
            native_session_state='running', primary_turn_open=True, background_level_known=True,
            pending_message_uuids=['manager-own-turn'], native_retry_active=mixed and hold_at == 'inventory')
        super().__init__([record], state_root=str(root))
        self.implemented = ['prepare_observer_switch', 'observer_recoveries']
        self.hold_at, self.mixed, self.prepare_calls = hold_at, mixed, 0
        self.store = Store(root)
        session = SimpleNamespace(record=record, generation=record['generation'],
                                  process=SimpleNamespace(pid=record['pid']))
        self.ledger = ObservationLedger(SimpleNamespace(store=self.store,
            sessions={record['native_uuid']: session}, observer_switch=None,
            observer_recovery=ObserverRecovery(self.store)))
        if hold_at == 'prepare':
            self.finish_turn()

    def finish_turn(self):
        self.rows[0].update(pending_message_uuids=[], primary_turn_open=False, native_session_state='idle')

    def dispatch(self, method, params):
        if method == 'observer_switch_inventory':
            return self.ledger.switch_inventory()
        if method == 'prepare_observer_switch':
            self.prepare_calls += 1
            if self.hold_at == 'prepare' and self.prepare_calls == 1:
                self.rows[0].update(pending_message_uuids=['manager-own-turn'], primary_turn_open=True,
                                    native_session_state='running', native_retry_active=self.mixed)
            return self.ledger.prepare_switch(**params)
        if method == 'finish_observer_switch':
            return self.ledger.finish_switch(**params)
        return super().dispatch(method, params)

    def close(self):
        self.store.db.close()


def invoke(h, *, control=None, timeout=5, recover=False):
    return restart.guarded_restart(h.client, account=None if recover else 'work', recover=recover,
        close_idle=True, state_root=h.state_root,
        control=control, operation_id='switch-one', timeout=timeout, witness_fn=h.witness,
        kill_fn=h.kill, starter=h.starter, clock=h.clock, sleep=h.sleep, interval=1)


def test_observer_ends_before_host_stop_and_one_notice_queues_only_after_verified_switch(isolated, tmp_path):
    h = SwitchHarness(tmp_path / 'host')
    report = invoke(h)
    assert report['phases']['observer_retirements'] == [{'outcome': 'ended'}]
    assert h.kills == [HOST_PID] and h.starts == 1
    assert next(i for i, row in enumerate(h.events) if row[0] == 'retire') < next(i for i, row in enumerate(h.events) if row[0] == 'kill')
    store = Store(h.state_root)
    recovery = ObserverRecovery(store).read('switch-one')
    assert recovery['facts']['outcome'] == 'complete'
    [notice] = store.records('quota_checks')
    assert notice['kind'] == 'observer_recovery' and notice['status'] == 'scheduled'
    expected = str(uuid.uuid5(uuid.NAMESPACE_URL, 'observer-switch:switch-one'))
    assert notice['message_uuid'] == expected and notice['recipient'] is None
    assert 'send' not in h.client.methods and 'steer' not in h.client.methods
    store.db.close()


@pytest.mark.parametrize('outcome', ['uncertain', 'retiring'])
def test_partial_stop_or_observer_timeout_keeps_real_record_and_does_not_notify_old_account(isolated, tmp_path, outcome):
    h = SwitchHarness(tmp_path / 'host', outcome=outcome)
    with pytest.raises(restart.RestartRefused) as error:
        invoke(h)
    assert error.value.report['observer_recovery']['operation_id'] == 'switch-one'
    assert h.kills == [] and h.starts == 0
    store = Store(h.state_root)
    assert ObserverRecovery(store).read('switch-one')['facts']['outcome'] == 'refused'
    [notice] = store.records('quota_checks')
    assert notice['due_at'] is None and notice['status'] == 'pending_switch'
    original = notice['message_uuid']
    restart.finish_observer_recovery(error.value.report, outcome='cancelled')
    assert store.records('quota_checks')[0]['message_uuid'] == original
    assert store.records('quota_checks')[0]['due_at'] is None
    store.db.close()


def test_real_work_reports_exact_owner_task_reason_before_any_effect(isolated, tmp_path):
    h = SwitchHarness(tmp_path / 'host', blockers=[{'execution_id': 'cc-real', 'generation': 2,
        'title': 'test worker', 'reasons': ['registered background tasks'], 'observer_reason': 'mixed effects',
        'tasks': [{'task_id': 'writing', 'run_id': 'run-real', 'kind': 'unclassified', 'reason': 'sleep before write'}]}])
    with pytest.raises(restart.RestartRefused, match='writing run run-real.*sleep before write'):
        invoke(h)
    assert h.kills == [] and h.starts == 0
    store = Store(h.state_root)
    assert not ObserverRecovery(store).read('switch-one')['transition']['effects']
    store.db.close()


@pytest.mark.parametrize('hold_at', ['inventory', 'prepare'])
@pytest.mark.parametrize('mixed', [False, True])
def test_observer_aware_real_settlement_hold_keeps_its_retry_type(isolated, tmp_path, monkeypatch, hold_at, mixed):
    from claude_headless import host as host_module
    from claude_headless.restart_state import read_receipt, read_operations
    from oc_claude_restart import retryable
    h = ObserverAwareHarness(tmp_path / 'host', hold_at=hold_at, mixed=mixed)
    monkeypatch.setattr(host_module, 'witness', h.witness)
    gate = restart.RestartControl(tmp_path / 'gate.json')
    gate.initialize()
    try:
        with pytest.raises(restart.RestartRefused) as error:
            invoke(h, control=gate)
        receipt = read_receipt(h.state_root, 'switch-one')
        assert receipt['schema'] == 'claude-restart/1' and receipt['operation_id'] == 'switch-one'
        assert error.value.report['retry'] == receipt['retry']
        assert receipt['requested_transition'] == 'refused'
        assert receipt['retry']['safe_pre_stop_retry'] is (not mixed)
        assert retryable(receipt) is (not mixed)
        assert 'accepted input awaiting a result' in receipt['retry']['reason']
        if mixed:
            assert 'native retry active' in receipt['retry']['reason']
        assert gate.phase() == ('stopping' if hold_at == 'prepare' else 'waiting')
        effects = read_operations(h.state_root, 'switch-one')['transition']['effects'].values()
        assert [e['kind'] for e in effects] == (['prepare_observers'] if hold_at == 'prepare' else [])
        assert not h.kills and h.starts == 0 and 'close_input' not in h.client.methods
        assert 'retire_observer_owner' not in h.client.methods
    finally:
        h.close()


@pytest.mark.parametrize('outcome,reasons', [
    ('held', []), ('held', ['native ownership uncertainty']),
    ('failed', ['native turn or required action in progress'])])
def test_observer_prepare_ambiguous_or_permanent_hold_is_not_retryable(isolated, tmp_path, outcome, reasons):
    from oc_claude_restart import retryable
    h = SwitchHarness(tmp_path / 'host')
    dispatch = h.client.handler
    def held(method, params):
        if method == 'prepare_observer_switch':
            return {'outcome': outcome, 'blockers': [{'execution_id': 'cc-real', 'generation': 2,
                'reasons': reasons, 'observer_reason': 'exact ownership/preparation unresolved'}]}
        return dispatch(method, params)
    h.client.handler = held
    with pytest.raises(restart.RestartRefused) as error:
        invoke(h)
    assert not retryable(error.value.report)
    assert not h.kills and h.starts == 0 and 'retire_observer_owner' not in h.client.methods


def test_cancelled_operation_never_starts_observer_retirement(isolated, tmp_path):
    gate = restart.RestartControl(tmp_path / 'gate.json')
    gate.initialize()
    gate.cancel()
    h = SwitchHarness(tmp_path / 'host')
    with pytest.raises(restart.RestartRefused, match='cancelled'):
        invoke(h, control=gate)
    assert not any(row[0] == 'retire' for row in h.events) and not h.kills


def test_destination_start_failure_keeps_index_and_pending_notice_not_false_success(isolated, tmp_path):
    h = SwitchHarness(tmp_path / 'host')
    starter = h.starter
    h.starter = lambda: (_ for _ in ()).throw(RuntimeError('replacement failed'))
    with pytest.raises(restart.RestartError):
        invoke(h)
    store = Store(h.state_root)
    record = ObserverRecovery(store).read('switch-one')
    assert record['facts']['outcome'] == 'failed'
    assert record['facts']['phases']['host_exit_observed'] is True
    assert store.records('quota_checks')[0]['due_at'] is None
    identity = store.records('quota_checks')[0]['message_uuid']
    store.db.close()
    h.starter = starter
    result = invoke(h, recover=True)
    assert result['service']['state'] == 'ready' and result['service']['account']['label'] == 'work'
    store = Store(h.state_root)
    [notice] = store.records('quota_checks')
    assert notice['message_uuid'] == identity and notice['status'] == 'scheduled'
    assert notice['due_at'] is not None and h.starts == 1
    store.db.close()


def test_restored_service_schedules_failed_operation_notice(isolated, tmp_path, monkeypatch):
    h = SwitchHarness(tmp_path / 'host')
    original = accounts.commit_selection
    monkeypatch.setattr(accounts, 'commit_selection', lambda *a, **kw: (_ for _ in ()).throw(OSError('before replace')))
    with pytest.raises(restart.RestartError) as failure:
        invoke(h)
    report = failure.value.report
    assert report['requested_transition'] == 'failed' and report['direction'] == 'restore_old'
    assert report['service']['state'] == 'ready' and report['service']['account']['label'] == 'personal'
    store = Store(h.state_root)
    [notice] = store.records('quota_checks')
    assert notice['status'] == 'scheduled' and notice['due_at'] is not None
    text = ObserverRecovery(store).notice_text('switch-one')
    assert text.startswith('AUTOMATED ACCOUNT SWITCH FAILED — SERVICE RESTORED — not a new message from Dan.')
    assert 'personal (epoch 0)' in text and 'before replace' in text
    assert 'stopped' in text and 'not confirmed delivery' in text
    assert '/home/' not in text and 'sleep 7190; date -u' not in text
    identity = notice['message_uuid']
    store.db.close()
    monkeypatch.setattr(accounts, 'commit_selection', original)
    assert invoke(h, recover=True)['requested_transition'] == 'failed'
    store = Store(h.state_root)
    assert store.records('quota_checks')[0]['message_uuid'] == identity
    store.db.close()
    assert h.starts == 1 and h.kills == [HOST_PID]


def test_same_completed_operation_replays_receipt_not_account_transition(isolated, tmp_path):
    h = SwitchHarness(tmp_path / 'host')
    first = invoke(h)
    second = invoke(h)
    assert second['replayed_completed_operation']
    assert second['phases'] == first['phases']
    assert h.kills == [HOST_PID] and h.starts == 1
