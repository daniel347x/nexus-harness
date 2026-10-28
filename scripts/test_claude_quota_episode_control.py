"""Exact alarm control: isolated SQLite/coordinator races and inert fixture CLI pipes, no real inference."""
import asyncio
from types import SimpleNamespace
import uuid

import pytest

from claude_headless.quota_wake import QuotaWake, record_account_switch_notice
from claude_headless.store import Store
from test_claude_headless_host import until
from test_claude_headless_supervision import cleanup, setup
from test_claude_quota_halt import input_lines


class AlarmHost:
    """Only coordinator/storage behavior is simulated; this is not native CLI qualification."""
    def __init__(self, root):
        self.store = Store(root)
        self.changed = asyncio.Event()
        self.sessions = {}
        self.reconnects = []
        self.sends = []
        self.manager = {'native_uuid': str(uuid.uuid4()), 'generation': 1, 'role': 'Manager',
                        'initialized': True, 'owner': 'host'}
        self.manager['execution_id'] = 'cc-' + self.manager['native_uuid']
        self.session = SimpleNamespace(record=self.manager, generation=1, send=self.send)
        self.sessions[self.manager['native_uuid']] = self.session
        self.store.put('sessions', self.manager['native_uuid'], self.manager)

    async def reconnect(self, **kwargs):
        self.reconnects.append(kwargs)
        return self.manager

    def state(self, execution_id):
        assert execution_id == self.manager['execution_id']
        return self.manager

    async def send(self, mid, submitted, original, **kwargs):
        self.sends.append(mid)
        receipt = {'message_uuid': mid, 'generation': 1, 'status': 'accepted',
                   'submitted': submitted, 'original': original, 'written': False, 'acknowledged': False}
        self.store.put('receipts', self.manager['native_uuid'], receipt, mid)
        return receipt


def alarm(host, clock):
    wake = QuotaWake(host, lambda: clock[0])
    episode = wake.arm(rate_limit_type='five_hour', resets_at=500, program='[ap:ONE]')
    return wake, episode


@pytest.mark.parametrize('action', ['defer', 'cancel'])
def test_exact_control_is_durable_idempotent_and_same_episode_halts_never_rearm(tmp_path, action):
    host, clock = AlarmHost(tmp_path / 'state'), [1000.0]
    try:
        wake, episode = alarm(host, clock)
        other = wake.arm(rate_limit_type='five_hour', resets_at=2000, program='[ap:OTHER]')
        result = wake.control_episode(episode['episode_id'], action, 'Dan via isolated operator')
        row = result['record']
        status = 'deferred_by_operator' if action == 'defer' else 'cancelled_by_operator'
        assert result['status'] == row['status'] == status and result['due_at'] is None
        assert result['receipt'] is None and result['message_replayed'] is False
        assert result['display']['send_admitted'] is False
        assert row['original_due_at'] == 800 and row['message_uuid'] == episode['message_uuid']
        assert row['affected'] == episode['affected']
        control = row['operator_control']
        assert control['authority'] == 'Dan via isolated operator' and control['original_due_at'] == 800
        event = host.store.replay()['events'][-1]
        assert event['kind'] == 'quota_episode_control' and event['cursor'] == control['event_cursor']
        assert event['detail']['message_uuid'] == episode['message_uuid']
        again = wake.control_episode(episode['episode_id'], action, control['authority'])
        assert again == result and len(host.store.replay()['events']) == 1
        same = wake.arm(rate_limit_type='five_hour', resets_at=500, program='[ap:LATE]')
        assert same['status'] == status and same['due_at'] is None
        assert {r['program'] for r in same['affected']} == {'[ap:ONE]', '[ap:LATE]'}
        record = {'quota_halt': {'rate_limit_type': 'five_hour', 'resets_at': 500}}
        assert wake.for_record(record)['operator_control'] == control
        assert host.store.get('quota_checks', '', other['episode_id']) == other
    finally:
        host.store.db.close()
    host = AlarmHost(tmp_path / 'state')
    try:
        wake = QuotaWake(host, lambda: clock[0])
        async def after_restart():
            wake.tick(clock[0])
            assert wake.tasks == {}
            await wake.advance(episode['episode_id'])
        asyncio.run(after_restart())
        persisted = host.store.get('quota_checks', '', episode['episode_id'])
        assert persisted['status'] == status and persisted['due_at'] is None
        assert persisted['message_uuid'] == episode['message_uuid']
        assert host.reconnects == host.sends == []
    finally:
        host.store.db.close()


def test_cancel_after_defer_preserves_original_due_and_both_operator_events(tmp_path):
    host = AlarmHost(tmp_path / 'state')
    try:
        wake, episode = alarm(host, [1000.0])
        first = wake.control_episode(episode['episode_id'], 'defer', 'Dan, first instruction')
        final = wake.control_episode(episode['episode_id'], 'cancel', 'Dan, later instruction')
        assert final['record']['original_due_at'] == first['record']['original_due_at'] == 800
        assert final['record']['operator_defer'] == first['record']['operator_control']
        assert len(final['record']['operator_controls']) == len(host.store.replay()['events']) == 2
        assert final['record']['operator_cancel']['authority'] == 'Dan, later instruction'
    finally:
        host.store.db.close()


@pytest.mark.parametrize('episode,action,authority', [
    ('missing', 'defer', 'Dan'), ('500', 'resume', 'Dan'), ('500', 'cancel', ''), (500, 'defer', 'Dan')])
def test_invalid_or_missing_exact_episode_does_not_create_or_mutate_a_record(tmp_path, episode, action, authority):
    host = AlarmHost(tmp_path / 'state')
    try:
        wake, before = alarm(host, [1000.0])
        with pytest.raises(ValueError):
            wake.control_episode(episode, action, authority)
        assert host.store.records('quota_checks') == [before]
        assert host.store.replay()['events'] == []
    finally:
        host.store.db.close()


def test_legacy_operator_deferral_is_not_overwritten_by_stale_delivery_saves(tmp_path):
    host = AlarmHost(tmp_path / 'state')
    try:
        wake, stale = alarm(host, [1000.0])
        legacy = {**stale, 'status': 'deferred_by_operator', 'due_at': None,
                  'operator_defer': {'authority': 'legacy instruction', 'original_due_at': 800}}
        legacy.pop('original_due_at')
        host.store.put('quota_checks', '', legacy, legacy['episode_id'])
        saved = wake.save(stale, status='sending', due_at=900, submitted='previously prepared text')
        assert saved['status'] == 'deferred_by_operator' and saved['due_at'] is None
        assert saved['delivery_status'] == 'sending' and saved['operator_defer'] == legacy['operator_defer']
        controlled = wake.control_episode(stale['episode_id'], 'cancel', 'new instruction')
        assert controlled['record']['original_due_at'] == 800
    finally:
        host.store.db.close()


@pytest.mark.parametrize('reconnect_outcome', ['ready', 'settings_pending', 'failure'])
def test_control_wins_while_reconnect_is_awaited_without_cancelling_reconnect(tmp_path, reconnect_outcome):
    async def scenario():
        host, clock = AlarmHost(tmp_path / 'state'), [1000.0]
        try:
            wake, episode = alarm(host, clock)
            entered, release = asyncio.Event(), asyncio.Event()
            async def reconnect(**kwargs):
                entered.set()
                await release.wait()
                if reconnect_outcome == 'failure':
                    raise ConnectionError('isolated reconnect failed')
                return {**host.manager, 'setting_control_pending': reconnect_outcome == 'settings_pending'}
            host.reconnect = reconnect
            advance = asyncio.create_task(wake.advance(episode['episode_id']))
            await asyncio.wait_for(entered.wait(), 5)
            controlled = wake.control_episode(episode['episode_id'], 'defer', 'Dan during reconnect')
            assert controlled['record']['status'] == 'deferred_by_operator'
            assert not advance.done()
            release.set()
            await asyncio.wait_for(advance, 5)
            row = host.store.get('quota_checks', '', episode['episode_id'])
            assert row['status'] == 'deferred_by_operator' and row['due_at'] is None
            assert row['message_uuid'] == episode['message_uuid'] and host.sends == []
            wake.tick(clock[0] + 86400)
            assert wake.tasks == {} and host.store.receipts(host.manager['native_uuid']) == []
        finally:
            host.store.db.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('action', ['defer', 'cancel'])
def test_control_revokes_a_notice_waiting_for_native_send_admission_not_other_inputs(tmp_path, action):
    async def scenario():
        host, manager, clock = await setup(tmp_path, role='Manager')
        try:
            wake = host.supervisor.quota_wake
            episode = wake.arm(rate_limit_type='five_hour', resets_at=clock[0] - 300, program='[ap:ONE]')
            reconnect = host.reconnect
            async def reconnect_then_hold_admission(**kwargs):
                record = await reconnect(**kwargs)
                await manager.command_lock.acquire()
                return record
            host.reconnect = reconnect_then_hold_admission
            advance = asyncio.create_task(wake.advance(episode['episode_id']))
            await until(lambda: wake.admissions.get(episode['episode_id']) and manager.command_lock._waiters)
            # A genuine independent input queued behind the same lock is not the alarm's task.
            ordinary_mid = str(uuid.uuid4())
            ordinary = asyncio.create_task(manager.send(ordinary_mid, 'ordinary fixture input', 'ORIGINAL'))
            await until(lambda: len(manager.command_lock._waiters) == 2)
            result = wake.control_episode(episode['episode_id'], action, 'Dan at Send lock')
            assert result['record']['admission_cancel_requested'] is True and result['receipt'] is None
            manager.command_lock.release()
            await asyncio.wait_for(asyncio.gather(advance, ordinary), 5)
            await until(lambda: host.store.get('receipts', manager.sid, ordinary_mid)['completed'])
            row = host.store.get('quota_checks', '', episode['episode_id'])
            assert row['status'] == result['status'] and row['due_at'] is None
            assert row['delivery_status'] == 'not_admitted' and row['admission_cancel_confirmed'] is True
            assert host.store.get('receipts', manager.sid, episode['message_uuid']) is None
            assert [line['uuid'] for line in input_lines(host, manager.sid)] == [ordinary_mid]
            assert not wake.admissions and not manager.command_lock.locked()
        finally:
            if manager.command_lock.locked():
                manager.command_lock.release()
            await cleanup(host)
    asyncio.run(scenario())


@pytest.mark.parametrize('outcome', ['acknowledged', 'uncertain', 'lost_reply'])
def test_already_admitted_send_is_retained_not_cancelled_and_late_receipt_stays_visible(tmp_path, outcome):
    async def scenario():
        host = AlarmHost(tmp_path / 'state')
        try:
            wake, episode = alarm(host, [1000.0])
            admitted, release = asyncio.Event(), asyncio.Event()
            async def send(*args, **kwargs):
                receipt = await host.send(*args, **kwargs)
                admitted.set()
                await release.wait()
                receipt.update(written=True, acknowledged=outcome == 'acknowledged',
                               status='uncertain' if outcome != 'acknowledged' else 'acknowledged')
                host.store.put('receipts', host.manager['native_uuid'], receipt, receipt['message_uuid'])
                if outcome == 'lost_reply':
                    raise ConnectionError('isolated lost response after admission')
            host.session.send = send
            advance = asyncio.create_task(wake.advance(episode['episode_id']))
            await asyncio.wait_for(admitted.wait(), 5)
            result = wake.control_episode(episode['episode_id'], 'cancel', 'Dan after Send admission')
            assert result['display']['send_admitted'] is True and result['receipt']['status'] == 'accepted'
            assert result['record']['delivery_status'] == 'sending'
            assert not result['record'].get('admission_cancel_requested') and not advance.done()
            release.set()
            await asyncio.wait_for(advance, 5)
            row = host.store.get('quota_checks', '', episode['episode_id'])
            assert row['status'] == 'cancelled_by_operator' and row['due_at'] is None
            assert row['delivery_status'] == ('sent' if outcome == 'acknowledged' else 'delivery_problem')
            if outcome != 'acknowledged':
                assert 'never resent' in row['failure']
            assert row['submitted'] == result['receipt']['submitted']
            receipt = host.store.get('receipts', host.manager['native_uuid'], episode['message_uuid'])
            host.store.put('receipts', host.manager['native_uuid'], {**receipt, 'acknowledged': True}, episode['message_uuid'])
            wake.tick(2000)
            final = host.store.get('quota_checks', '', episode['episode_id'])
            assert final['status'] == 'cancelled_by_operator' and final['delivery_status'] == 'sent'
            assert final['due_at'] is None and host.sends == [episode['message_uuid']]
            assert not wake.tasks and not wake.admissions
        finally:
            host.store.db.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('late_change', ['halt', 'manager'])
def test_scheduled_admission_rechecks_manager_and_latest_affected_evidence(tmp_path, late_change):
    async def scenario():
        host = AlarmHost(tmp_path / 'state')
        try:
            wake, episode = alarm(host, [1000.0])
            advance = asyncio.create_task(wake.advance(episode['episode_id']))
            await asyncio.sleep(0)  # preparation ran; the newly scheduled admission has not run yet
            assert wake.admissions.get(episode['episode_id']) and host.sends == []
            if late_change == 'halt':
                wake.arm(rate_limit_type='five_hour', resets_at=500, program='[ap:LATE]')
            else:
                sid = str(uuid.uuid4())
                host.manager = {**host.manager, 'native_uuid': sid, 'execution_id': 'cc-' + sid, 'last_input_at': 1001}
                host.sessions[sid] = SimpleNamespace(record=host.manager, generation=1, send=host.send)
                host.store.put('sessions', sid, host.manager)
            await asyncio.wait_for(advance, 5)
            if late_change == 'manager':
                assert host.sends == []  # never send to the stale Manager just because preparation finished
                await wake.advance(episode['episode_id'])
            row = host.store.get('quota_checks', '', episode['episode_id'])
            assert row['recipient'] == host.manager['execution_id']
            receipt = host.store.get('receipts', host.manager['native_uuid'], episode['message_uuid'])
            assert host.sends == [episode['message_uuid']] and row['submitted'] == receipt['submitted']
            if late_change == 'halt':
                assert '[ap:ONE]' in receipt['submitted'] and '[ap:LATE]' in receipt['submitted']
        finally:
            host.store.db.close()
    asyncio.run(scenario())


def test_controlled_switch_notice_can_finalize_without_being_rearmed(tmp_path):
    host = AlarmHost(tmp_path / 'state')
    try:
        wake = QuotaWake(host, lambda: 1000)
        staged = record_account_switch_notice(host.store, notice_id='isolated-switch',
            recipient=host.manager['execution_id'], requested_account='fixture', clock=lambda: 1000)
        wake.control_episode(staged['episode_id'], 'defer', 'Dan before finalization')
        final = record_account_switch_notice(host.store, notice_id='isolated-switch', outcome='complete', clock=lambda: 1001)
        assert final['status'] == 'deferred_by_operator' and final['due_at'] is None
        assert final['outcome'] == 'complete' and final['message_uuid'] == staged['message_uuid']
    finally:
        host.store.db.close()
