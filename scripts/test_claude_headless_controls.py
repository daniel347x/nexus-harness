"""Offline U04 correlated-control tests: inert fake session/store, no API, no process.

The fakes emulate exactly the NativeSession surface ``controls.py`` uses (``control``, ``update``,
``event``, ``command_lock``, ``process``, ``pending``, ``store``, ``host.changed``); a scripted
reply stands in for the host's ``NativeSession.project`` writing a native control response.
Bounded ``wait_for``/``sleep`` in these tests are fixture budgets, never product policy.
"""
import asyncio
import json

import pytest

from claude_headless import controls


class FakeStore:
    """Inert in-memory stand-in for Store: deep-copied JSON rows, no sqlite and no disk."""

    def __init__(self):
        self.tables = {'controls': {}, 'receipts': {}, 'sessions': {}}

    def get(self, table, sid, key=None):
        row = self.tables[table].get((sid, key))
        return json.loads(json.dumps(row)) if row is not None else None

    def put(self, table, sid, body, key=None):
        self.tables[table][(sid, key)] = json.loads(json.dumps(body))

    def control_rows(self, sid):
        return [row for (row_sid, _key), row in self.tables['controls'].items() if row_sid == sid]


class FakeHost:
    def __init__(self):
        self.changed = asyncio.Event()
        self.supervisor = FakeSupervisor()


class FakeSupervisor:
    """Records the exact escalation calls the real Supervisor.incident would receive."""

    def __init__(self):
        self.incidents = []

    def incident(self, record, category, handler='retain', source_cursor=None, mids=None,
                 detail=None, attention=False, key=None):
        body = {'category': category, 'handler': handler, 'detail': detail or {},
                'attention': attention, 'key': key, 'record': record}
        self.incidents.append(body)
        return body


class FakeProcess:
    def __init__(self, alive=True):
        self.returncode = None if alive else 1


class FakeSession:
    """Inert stand-in for NativeSession's control surface; replies are scripted, never native."""

    def __init__(self, *, role='Manager', generation=1, state='ready', alive=True, effective=None,
                 sid='fake-native-uuid', requested_model='haiku', requested_effort='low'):
        self.sid = sid
        self.generation = generation
        self.record = {'execution_id': 'cc-' + sid, 'native_uuid': sid, 'generation': generation,
                       'role': role, 'state': state, 'requested_model': requested_model,
                       'requested_effort': requested_effort}
        if effective is not None:
            self.record['controls_effective'] = dict(effective)
        self.store = FakeStore()
        self.store.put('sessions', sid, self.record)
        self.host = FakeHost()
        self.process = FakeProcess(alive)
        self.command_lock = asyncio.Lock()
        self.pending = set()
        self.events = []
        self.requests = []
        self.replies = {}
        self._counter = 0

    def update(self, **values):
        self.record.update(values)
        self.store.put('sessions', self.sid, self.record)

    def event(self, kind, detail=None, **raw):
        self.events.append({'kind': kind, 'detail': detail or {}})
        if self.host.changed is not None:
            self.host.changed.set()
        return f'cursor:{len(self.events)}'

    def script(self, subtype, *results):
        """Queue replies for one native subtype (a dict, or a callable of (rid, request))."""
        for result in results:
            self.replies.setdefault(subtype, []).append(result)

    def control(self, subtype, request=None):
        self._counter += 1
        rid = f'rid-{self._counter}'
        self.store.put('controls', self.sid,
                       {'request_id': rid, 'subtype': subtype, 'generation': self.generation,
                        'status': 'pending'}, rid)
        self.requests.append({'rid': rid, 'subtype': subtype,
                              'request': json.loads(json.dumps(request or {})),
                              'events_before': len(self.events),
                              'record_at_issue': {key: self.record.get(key) for key in
                                                  ('interrupt_authorized', 'setting_control_pending')}})
        if self.host.changed is not None:
            self.host.changed.set()
        queue = self.replies.get(subtype)
        result = queue.pop(0) if queue else None
        if callable(result):
            result = result(rid, dict(request or {}))
        if result is not None:
            self.apply_reply(rid, **result)
        return {'request_id': rid, 'subtype': subtype, 'generation': self.generation,
                'status': 'pending'}

    def apply_reply(self, rid, *, status='success', response=None):
        record = self.store.get('controls', self.sid, rid)
        assert record is not None, f'no control {rid} to reply to'
        record.update(status=status,
                      response=response if response is not None else {'subtype': status, 'request_id': rid})
        self.store.put('controls', self.sid, record, rid)
        if self.host.changed is not None:
            self.host.changed.set()


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(controls, 'POLL_SECONDS', 0.01)


def reply(rid, **body):
    return {'status': 'success', 'response': {'subtype': 'success', 'request_id': rid, **body}}


def readback(model=None, effort=None, **extra):
    applied = {}
    if model is not None:
        applied['model'] = model
    if effort is not None:
        applied['effort'] = effort
    body = {**extra}
    if applied:
        body['applied'] = applied
    return body


async def wait_for_requests(session, count):
    for _ in range(2000):
        if len(session.requests) >= count:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f'{count} control request(s) were not issued')


async def deliver(session, builders):
    """Deliver a reply to every request as it is issued (cancelled by the test afterwards)."""
    delivered = set()
    while True:
        for request in list(session.requests):
            if request['rid'] in delivered:
                continue
            delivered.add(request['rid'])
            builder = builders.get(request['subtype'])
            if builder is not None:
                await asyncio.sleep(0.02)  # lands after dispatch has polled at least once
                session.apply_reply(request['rid'], **builder(request['rid'], request['request']))
        await asyncio.sleep(0.005)


def test_set_model_alias_verified_applied_and_receipt():
    async def scenario():
        session = FakeSession()
        session.script('set_model', lambda rid, request: reply(rid))
        session.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-sonnet-5-5', effort='low')))
        result = await controls.dispatch_control(session, 'set_model',
                                                 {'model': 'sonnet', 'authority': 'manager-pane'})
        assert result['state'] == 'applied' and result['applied'] is True
        assert result['blocks_send'] is False and result['requested'] == {'model': 'sonnet'}
        assert result['operation'] == 'set_model' and result['native_subtype'] == 'set_model'
        assert result['operation_class'] == 'setting' and result['authority'] == 'manager-pane'
        assert [row['subtype'] for row in session.requests] == ['set_model', 'get_settings']
        assert session.requests[0]['request'] == {'model': 'sonnet'}
        # the authority is durable before the control is queued
        assert session.requests[0]['events_before'] >= 1
        assert session.events[0]['kind'] == 'control_authority'
        assert session.events[0]['detail']['authority'] == 'manager-pane'
        # the durable receipt is reachable by request_id and the gate shows the verified value
        assert controls.receipt(session, result['request_id'])['state'] == 'applied'
        gate = controls.settings_gate(session)
        assert gate['blocked'] is False and gate['effective']['model'] == 'claude-sonnet-5-5'
        assert session.record['setting_control_pending'] is False
        # a verified setter records actual choices and rewrites requested choices for same-UUID resume
        assert session.record['actual_model'] == 'claude-sonnet-5-5'
        assert session.record['actual_effort'] == 'low'
        assert session.record['requested_model'] == 'claude-sonnet-5-5'
        assert session.record['requested_effort'] == 'low'
        assert session.record['settings_mismatch'] is False
    asyncio.run(scenario())


def test_set_model_explicit_native_name_with_delayed_poll():
    async def scenario():
        session = FakeSession(requested_effort='medium')
        session.host.changed = None  # pure poll path: no wake event at all
        delivering = asyncio.create_task(deliver(session, {
            'set_model': lambda rid, request: reply(rid),
            'get_settings': lambda rid, request: reply(
                rid, response=readback(model='claude-haiku-4-5', effort='medium'))}))
        try:
            result = await controls.dispatch_control(session, 'set_model',
                                                     {'model': 'claude-haiku-4-5'})
        finally:
            delivering.cancel()
            await asyncio.gather(delivering, return_exceptions=True)
        assert result['state'] == 'applied' and result['applied'] is True
        assert controls.settings_gate(session)['effective']['effort'] == 'medium'
    asyncio.run(scenario())


def test_set_model_mismatch_disclosed_and_blocks_send():
    async def scenario():
        session = FakeSession()
        session.script('set_model', lambda rid, request: reply(rid))
        session.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-haiku-4-5', effort='low')))
        result = await controls.dispatch_control(session, 'set_model', {'model': 'sonnet'})
        assert result['state'] == 'mismatch' and result['applied'] is False
        assert result['blocks_send'] is True and 'sonnet' in result['disclosure']
        assert 'claude-haiku-4-5' in result['disclosure']
        gate = controls.settings_gate(session)
        assert gate['blocked'] is True and gate['reason'] == 'effective_choice_differs'
        assert gate['state'] == 'mismatch' and gate['request_id'] == result['request_id']
        assert session.record['setting_control_pending'] is True
        assert any(event['kind'] == 'control_mismatch' for event in session.events)
    asyncio.run(scenario())


def test_native_error_reply_refused_blocks_send():
    async def scenario():
        session = FakeSession()
        session.script('apply_flag_settings', lambda rid, request: {
            'status': 'error',
            'response': {'subtype': 'error', 'request_id': rid, 'error': 'effort level not entitled'}})
        result = await controls.dispatch_control(session, 'apply_effort', {'effort': 'max'})
        assert result['state'] == 'refused' and result['applied'] is False
        assert result['blocks_send'] is True and 'not entitled' in result['disclosure']
        assert [row['subtype'] for row in session.requests] == ['apply_flag_settings']
        gate = controls.settings_gate(session)
        assert gate['blocked'] is True and gate['reason'] == 'native_refused'
    asyncio.run(scenario())


def test_unusable_readback_shape_opens_send_with_a_loud_warning():
    """Finding #4 (Manager-Session-185): a readback this code cannot read (a Claude Code update that renames or nests
    a field) is unverifiable, not different: Send opens with a durable warning instead of closing for good."""
    async def scenario():
        session = FakeSession()
        session.script('set_model', lambda rid, request: reply(rid))
        session.script('get_settings', lambda rid, request: reply(rid))  # no usable applied.model/effort
        result = await controls.dispatch_control(session, 'set_model', {'model': 'sonnet'})
        assert result['state'] == 'applied_unverified' and result['blocks_send'] is False
        assert result['applied'] is False and 'settings unverified' in result['disclosure']
        gate = controls.settings_gate(session)
        assert gate['blocked'] is False
        assert session.record['settings_unverified'] is True and session.record['setting_control_pending'] is False
        assert 'Send is open' in session.record['settings_unverified_disclosure']
        assert any(event['kind'] == 'control_unverified_open' for event in session.events)
        warned = [i for i in session.host.supervisor.incidents if i['category'] == 'settings_unverified']
        assert len(warned) == 1 and warned[0]['handler'] == 'warn' and warned[0]['attention'] is False
        assert not [i for i in session.host.supervisor.incidents if i['attention']]   # nothing escalates
        # an effective-only reply is still never applied (no laundering), and still not a difference
        only_effective = FakeSession(sid='fake-effective-only')
        only_effective.script('set_model', lambda rid, request: reply(rid))
        only_effective.script('get_settings', lambda rid, request: reply(
            rid, response={'effective': {'model': 'claude-haiku-4-5', 'effortLevel': 'low'}}))
        result = await controls.dispatch_control(only_effective, 'set_model', {'model': 'sonnet'})
        assert result['state'] == 'applied_unverified' and result['applied'] is False
        assert controls.settings_gate(only_effective)['blocked'] is False
        assert 'actual_model' not in only_effective.record
        # a reshaped effort (an object, a new spelling) with the model verified: open with the warning
        reshaped = FakeSession(sid='fake-reshaped', requested_effort='high')
        reshaped.script('set_model', lambda rid, request: reply(rid))
        reshaped.script('get_settings', lambda rid, request: reply(
            rid, response={'applied': {'model': 'claude-sonnet-5-5', 'effort': {'level': 'high'}}}))
        result = await controls.dispatch_control(reshaped, 'set_model', {'model': 'sonnet'})
        assert result['state'] == 'applied_unverified' and result['unverified'] == ['effort']
        # ... but a readback that NAMES a different model still blocks, even with the effort unreadable
        different = FakeSession(sid='fake-different')
        different.script('set_model', lambda rid, request: reply(rid))
        different.script('get_settings', lambda rid, request: reply(
            rid, response={'applied': {'model': 'claude-haiku-4-5'}}))
        result = await controls.dispatch_control(different, 'set_model', {'model': 'sonnet'})
        assert result['state'] == 'mismatch' and result['blocks_send'] is True
    asyncio.run(scenario())


def test_failed_or_unknown_get_settings_opens_a_launch_gate_with_a_warning_and_a_setter_status_is_verified():
    async def scenario():
        launched = FakeSession(sid='fake-launch-error')
        launched.record.update(requested_model='sonnet', requested_effort='medium', setting_control_pending=True)
        launched.script('get_settings', lambda rid, request: {
            'status': 'error', 'response': {'subtype': 'error', 'request_id': rid, 'error': 'unknown subtype'}})
        result = await controls.read_settings(launched, authority='initialize')
        assert result['blocks_send'] is False and result['disposition']['disposition'] == 'applied_unverified'
        assert launched.record['settings_unverified'] is True and 'unknown subtype' in \
            launched.record['settings_unverified_disclosure']
        odd = FakeSession(sid='fake-launch-odd')
        odd.record.update(requested_model='sonnet', requested_effort='medium', setting_control_pending=True)
        odd.script('get_settings', lambda rid, request: {'status': 'accepted_v2', 'response': {}})
        assert (await controls.read_settings(odd, authority='initialize'))['blocks_send'] is False
        # a setter reply with a status this code does not know is not a failure: the readback decides
        setter = FakeSession(sid='fake-setter-status')
        setter.script('set_model', lambda rid, request: {'status': 'ok', 'response': {}})
        setter.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-sonnet-5-5', effort='low')))
        result = await controls.dispatch_control(setter, 'set_model', {'model': 'sonnet'})
        assert result['state'] == 'applied' and result['blocks_send'] is False
        # a native error reply to the setter is the native saying no: still blocked
        refused = FakeSession(sid='fake-setter-refused')
        refused.script('set_model', lambda rid, request: {
            'status': 'error', 'response': {'subtype': 'error', 'request_id': rid, 'error': 'not entitled'}})
        assert (await controls.dispatch_control(refused, 'set_model', {'model': 'opus'}))['blocks_send'] is True
    asyncio.run(scenario())


def test_model_change_cannot_silently_alter_effort():
    async def scenario():
        session = FakeSession(effective={'model': 'claude-haiku-4-5', 'effort': 'low'})
        session.script('set_model', lambda rid, request: reply(rid))
        session.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-sonnet-5-5', effort='high')))
        result = await controls.dispatch_control(session, 'set_model', {'model': 'sonnet'})
        assert result['state'] == 'mismatch' and result['blocks_send'] is True
        assert session.record['settings_mismatch'] is True
        gate = controls.settings_gate(session)
        assert gate['blocked'] is True and gate['reason'] == 'effective_choice_differs'
        assert controls.receipt(session, result['request_id'])['mismatch'] is True
        incidents = session.host.supervisor.incidents
        assert [incident['category'] for incident in incidents] == ['control_mismatch']
        assert incidents[0]['handler'] == 'escalate' and incidents[0]['attention'] is True

        # and an effort operation may not silently alter the model
        other = FakeSession(sid='fake-effort', effective={'model': 'claude-haiku-4-5', 'effort': 'low'})
        other.script('apply_flag_settings', lambda rid, request: reply(rid))
        other.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-opus-4-6', effort='high')))
        result = await controls.dispatch_control(other, 'apply_effort', {'effort': 'high'})
        assert result['state'] == 'mismatch' and other.record['settings_mismatch'] is True
    asyncio.run(scenario())


def test_second_setter_refuses_in_flight_and_corrects_after_terminal_reply():
    async def scenario():
        for state in ('pending', 'replied'):
            session = FakeSession(sid=f'fake-{state}')
            session.record.update(setting_control_pending=True, setting_control_state=state,
                                  setting_control_request_id='rid-old',
                                  setting_control_block_reason='awaiting_native_reply')
            session.store.put('controls', session.sid, {
                'request_id': 'rid-old', 'operation_class': 'setting', 'requested': {'model': 'sonnet'},
                'state': state, 'blocks_send': True, 'dispatched_at': 1.0, 'dispatched_generation': 1}, 'rid-old')
            with pytest.raises(RuntimeError, match='without a native terminal reply'):
                await controls.dispatch_control(session, 'set_model', {'model': 'opus'})
            assert session.requests == []

        # Guard sweep: an uncertain outcome, or a setter from an ended generation (its reply can
        # never arrive), is superseded by a new explicit setter whose own readback decides.
        for sid, state, generation in (('fake-uncertain', 'uncertain', 1), ('fake-dead-generation', 'pending', 1)):
            session = FakeSession(sid=sid, generation=2 if sid == 'fake-dead-generation' else 1)
            session.record.update(setting_control_pending=True, setting_control_state=state,
                                  setting_control_request_id='rid-old',
                                  setting_control_block_reason='uncertain_outcome')
            session.store.put('controls', session.sid, {
                'request_id': 'rid-old', 'operation_class': 'setting', 'requested': {'model': 'sonnet'},
                'state': state, 'blocks_send': True, 'dispatched_at': 1.0, 'dispatched_generation': generation}, 'rid-old')
            session.script('set_model', lambda rid, request: reply(rid))
            session.script('get_settings', lambda rid, request: reply(
                rid, response=readback(model='claude-opus-4-6', effort='low')))
            result = await controls.dispatch_control(session, 'set_model', {'model': 'opus'})
            assert result['state'] == 'applied', sid
            assert controls.receipt(session, 'rid-old')['state'] == 'superseded'
            assert controls.settings_gate(session)['blocked'] is False

        session = FakeSession(sid='fake-correction')
        session.record.update(setting_control_pending=True, setting_control_state='mismatch',
                              setting_control_request_id='rid-old',
                              setting_control_block_reason='effective_choice_differs')
        session.store.put('controls', session.sid, {
            'request_id': 'rid-old', 'operation_class': 'setting', 'requested': {'model': 'sonnet'},
            'state': 'mismatch', 'blocks_send': True, 'dispatched_at': 1.0}, 'rid-old')
        session.script('set_model', lambda rid, request: reply(rid))
        session.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-opus-4-6', effort='low')))
        result = await controls.dispatch_control(session, 'set_model', {'model': 'opus'})
        assert result['state'] == 'applied'
        assert controls.receipt(session, 'rid-old')['state'] == 'superseded'
        assert controls.settings_gate(session)['blocked'] is False
        assert session.record['requested_model'] == 'claude-opus-4-6'
        assert session.record['settings_mismatch'] is False
    asyncio.run(scenario())


def test_setter_waits_for_retry_and_own_turn_but_not_background_or_recovery():
    async def scenario():
        for values in ({'native_retry_active': True},
                       {'native_state_required': True, 'native_session_state': 'running'},
                       {'native_state_required': True, 'native_session_state': 'running', 'primary_turn_open': True}):
            session = FakeSession(sid='fake-' + '-'.join(sorted(values)))
            session.record.update(values)
            with pytest.raises(RuntimeError, match='wait for it'):
                await controls.dispatch_control(session, 'set_model', {'model': 'sonnet'})
            assert session.requests == []
        # Guard sweep: background agents/tasks keep the choices they started with, and a
        # scheduled recovery re-checks the settings gate before sending: neither blocks a
        # change between the Manager's own turns any more.
        for values in ({'background_unsettled': True, 'background_tasks': {'t': {'task_id': 't'}}},
                       {'pending_recovery': 'recovery-1'},
                       {'native_state_required': True, 'native_session_state': 'running', 'primary_turn_open': False}):
            session = FakeSession(sid='fake-ok-' + '-'.join(sorted(values)), effective={'model': 'claude-haiku-4-5', 'effort': 'low'})
            session.record.update(values)
            session.script('set_model', lambda rid, request: reply(rid))
            session.script('get_settings', lambda rid, request: reply(
                rid, response=readback(model='claude-sonnet-4-6', effort='low')))
            result = await controls.dispatch_control(session, 'set_model', {'model': 'sonnet'})
            assert result['state'] == 'applied', values
    asyncio.run(scenario())


def test_effort_alias_flag_settings_and_restrictions():
    async def scenario():
        session = FakeSession()
        session.script('apply_flag_settings', lambda rid, request: reply(rid),
                       lambda rid, request: reply(rid))
        session.script('get_settings',
                       lambda rid, request: reply(rid, response=readback(model='claude-haiku-4-5',
                                                                         effort='high')),
                       lambda rid, request: reply(rid, response=readback(model='claude-haiku-4-5',
                                                                         effort='medium')))
        first = await controls.dispatch_control(session, 'apply_effort', {'effort': 'high'})
        assert first['state'] == 'applied' and first['native_subtype'] == 'apply_flag_settings'
        assert session.requests[0]['request'] == {'settings': {'effortLevel': 'high'}}
        second = await controls.dispatch_control(session, 'apply_flag_settings',
                                                 {'settings': {'effortLevel': 'medium'}})
        assert second['state'] == 'applied'
        assert controls.settings_gate(session)['effective']['effort'] == 'medium'
        issued = list(session.requests)
        for bad in ({}, {'effort': 'high', 'effort_level': 'low'}, {'effort': 'turbo'}):
            with pytest.raises(ValueError):
                await controls.dispatch_control(session, 'apply_effort', bad)
        for bad in ({'settings': {'effortLevel': 'high', 'model': 'sonnet'}},
                    {'settings': {}}, {'settings': {'effortLevel': 'nope'}}, {}):
            with pytest.raises(ValueError):
                await controls.dispatch_control(session, 'apply_flag_settings', bad)
        with pytest.raises(ValueError):
            await controls.dispatch_control(session, 'set_model', {'model': 'bad name!'})
        assert session.requests == issued  # refused parameters never reach the native process
    asyncio.run(scenario())


def test_setters_manager_only_and_idle_only():
    async def scenario():
        worker = FakeSession(role='worker')
        with pytest.raises(RuntimeError, match='Manager-only'):
            await controls.dispatch_control(worker, 'set_model', {'model': 'sonnet'})
        worker.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-haiku-4-5', effort='low')))
        read = await controls.read_settings(worker)  # read-only is allowed for any role
        assert read['state'] == 'applied' and read['model'] == 'claude-haiku-4-5'

        busy = FakeSession()
        busy.pending = {'mid-1'}
        busy.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-haiku-4-5', effort='low')))
        with pytest.raises(RuntimeError, match='idle-only'):
            await controls.dispatch_control(busy, 'set_model', {'model': 'sonnet'})
        assert (await controls.read_settings(busy))['state'] == 'applied'

        cold = FakeSession(state='initializing')
        with pytest.raises(RuntimeError, match='not initialized'):
            await controls.dispatch_control(cold, 'set_model', {'model': 'sonnet'})

        dead = FakeSession(alive=False)
        with pytest.raises(RuntimeError, match='has exited'):
            await controls.dispatch_control(dead, 'get_settings', {})
    asyncio.run(scenario())


def test_seat_switch_authority_opens_setters_for_seats_only():
    async def scenario():
        worker = FakeSession(role='worker')
        with pytest.raises(RuntimeError, match='Manager-only'):
            await controls.dispatch_control(worker, 'set_model', {'model': 'claude-opus-5-5',
                                                                  'authority': 'operator'})
        base = FakeSession(role='base')
        with pytest.raises(RuntimeError, match='Manager-only'):
            await controls.dispatch_control(base, 'set_model', {'model': 'claude-opus-5-5',
                                                                'authority': 'seat-switch:test'})
        assert worker.requests == [] and base.requests == []
        worker.script('set_model', lambda rid, request: reply(rid))
        worker.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-opus-5-5', effort='low')))
        result = await controls.dispatch_control(worker, 'set_model', {'model': 'claude-opus-5-5',
                                                                       'authority': 'seat-switch:test'})
        assert result['state'] == 'applied'
        assert worker.record['requested_model'] == 'claude-opus-5-5'
        assert not controls.settings_gate(worker)['blocked']
    asyncio.run(scenario())


def test_interrupt_and_readback_allowed_busy_and_do_not_take_the_lock():
    async def scenario():
        session = FakeSession(role='conductor')
        session.pending = {'mid-1'}
        session.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-haiku-4-5', effort='low')))
        session.script('interrupt', lambda rid, request: reply(
            rid, response={'still_queued': [], 'cancelled': []}))
        await session.command_lock.acquire()
        try:
            read = await asyncio.wait_for(controls.read_settings(session), 2)
            assert read['state'] == 'applied'
            result = await asyncio.wait_for(
                controls.dispatch_control(session, 'interrupt', {'authority': 'operator'}), 2)
            assert result['state'] == 'replied'
        finally:
            session.command_lock.release()
        assert session.requests[-1]['request'] == {'cancel_queued': True}
    asyncio.run(scenario())


def test_interrupt_requires_authority_and_receipt_is_not_settlement():
    async def scenario():
        session = FakeSession(role='worker')  # interrupt is not a setter; no Manager-only rule
        session.pending = {'mid-1'}
        session.store.put('receipts', session.sid,
                          {'message_uuid': 'mid-1', 'status': 'started'}, 'mid-1')
        with pytest.raises(ValueError, match='authority'):
            await controls.dispatch_control(session, 'interrupt', {})
        assert session.requests == []
        session.script('interrupt', lambda rid, request: reply(
            rid, response={'still_queued': [], 'cancelled': []}))
        result = await controls.dispatch_control(session, 'interrupt',
                                                 {'authority': 'operator', 'reason': 'stop the turn'})
        assert result['state'] == 'replied' and result['blocks_send'] is False
        assert result['pending_message_uuids'] == ['mid-1'] and result['reason'] == 'stop the turn'
        assert session.events[0]['kind'] == 'control_authority'
        settlement = controls.interrupt_settlement(session, result['request_id'])
        assert settlement['settled'] is False and settlement['awaiting_message_uuids'] == ['mid-1']
        assert 'not settlement' in settlement['claim']
        assert settlement['cancelled'] == [] and settlement['still_queued'] == []
        session.store.put('receipts', session.sid,
                          {'message_uuid': 'mid-1', 'status': 'completed', 'completed': True}, 'mid-1')
        session.pending.discard('mid-1')
        assert controls.interrupt_settlement(session, result['request_id'])['settled'] is True

        idle = FakeSession()
        idle.script('interrupt', lambda rid, request: reply(rid, response={}))
        empty = await controls.dispatch_control(idle, 'interrupt', {'authority': 'operator'})
        assert controls.interrupt_settlement(idle, empty['request_id'])['settled'] is False
    asyncio.run(scenario())


def test_interrupt_cancelled_caller_recorded_uncertain_without_gating():
    async def scenario():
        session = FakeSession()
        task = asyncio.create_task(controls.dispatch_control(session, 'interrupt',
                                                             {'authority': 'operator'}))
        await wait_for_requests(session, 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        record = controls.receipt(session, session.requests[0]['rid'])
        assert record['state'] == 'uncertain' and record['caller_lost'] is True
        assert record['blocks_send'] is False
        assert controls.settings_gate(session)['blocked'] is False
        assert any(event['kind'] == 'control_caller_lost' for event in session.events)
    asyncio.run(scenario())


def test_cancelled_setter_keeps_gate_blocked():
    async def scenario():
        session = FakeSession()
        task = asyncio.create_task(controls.dispatch_control(session, 'set_model', {'model': 'sonnet'}))
        await wait_for_requests(session, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        record = controls.receipt(session, session.requests[0]['rid'])
        assert record['state'] == 'uncertain' and record['caller_lost'] is True
        assert record['blocks_send'] is True
        gate = controls.settings_gate(session)
        assert gate['blocked'] is True and gate['reason'] == 'uncertain_outcome'
        assert session.record['setting_control_pending'] is True
        assert 'may still apply' in gate['disclosure']
    asyncio.run(scenario())


def test_missing_reply_stays_pending_forever_no_deadline():
    async def scenario():
        session = FakeSession()
        task = asyncio.create_task(controls.dispatch_control(session, 'apply_effort',
                                                             {'effort': 'high'}))
        await wait_for_requests(session, 1)
        await asyncio.sleep(0.25)  # many poll cadences; still no deadline on the operation
        assert not task.done()
        record = controls.receipt(session, session.requests[0]['rid'])
        assert record['status'] == 'pending' and record['state'] == 'pending'
        assert controls.settings_gate(session)['blocked'] is True
        assert session.record['setting_control_pending'] is True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_restart_safety_restores_and_settles_durable_operation():
    async def scenario():
        session = FakeSession()  # a rebuilt record: no setting_control_* fields at all
        session.store.put('controls', session.sid, {
            'request_id': 'old-op', 'operation': 'set_model', 'operation_class': 'setting',
            'requested': {'model': 'sonnet'}, 'state': 'replied', 'blocks_send': True,
            'dispatched_at': 1.0, 'dispatched_generation': 1,
            'disclosure': 'awaiting readback'}, 'old-op')
        gate = controls.settings_gate(session)
        assert gate['blocked'] is True and gate['request_id'] == 'old-op'
        assert gate['reason'] == 'awaiting_readback'
        assert session.record['setting_control_pending'] is True
        matched = controls.resolve_readback(session,
                                            {'model': 'claude-sonnet-5-5', 'effort': 'low'},
                                            request_id='init-readback', source='initialize')
        assert matched['disposition'] == 'applied'
        assert controls.settings_gate(session)['blocked'] is False
        assert controls.receipt(session, 'old-op')['state'] == 'applied'

        replied = FakeSession(sid='fake-2')
        replied.store.put('controls', replied.sid, {
            'request_id': 'old-op-2', 'operation_class': 'setting', 'requested': {'model': 'sonnet'},
            'state': 'replied', 'blocks_send': True, 'dispatched_at': 1.0}, 'old-op-2')
        mismatch = controls.resolve_readback(replied, {'model': 'claude-haiku-4-5'},
                                             request_id='rb-2', source='initialize')
        assert mismatch['disposition'] == 'mismatch'
        gate = controls.settings_gate(replied)
        assert gate['blocked'] is True and gate['reason'] == 'effective_choice_differs'
        assert 'claude-haiku-4-5' in gate['disclosure']

        pending = FakeSession(sid='fake-3')
        pending.store.put('controls', pending.sid, {
            'request_id': 'old-op-3', 'operation_class': 'setting', 'requested': {'model': 'sonnet'},
            'state': 'pending', 'blocks_send': True, 'dispatched_at': 1.0}, 'old-op-3')
        uncertain = controls.resolve_readback(pending, {'model': 'claude-haiku-4-5'},
                                              request_id='rb-3', source='initialize')
        assert uncertain['disposition'] == 'uncertain'
        assert controls.settings_gate(pending)['blocked'] is True

        healed = FakeSession(sid='fake-4')  # crash between the durable row and the record
        healed.record.update(setting_control_pending=True, setting_control_state='applied',
                             setting_control_request_id='done-op', setting_control_block_reason=None)
        healed.store.put('controls', healed.sid, {'request_id': 'done-op', 'operation_class': 'setting',
                                                  'state': 'applied', 'dispatched_at': 2.0}, 'done-op')
        assert controls.settings_gate(healed)['blocked'] is False
    asyncio.run(scenario())


def test_readback_settles_waiter_then_cancellation_does_not_reopen():
    async def scenario():
        session = FakeSession()
        task = asyncio.create_task(controls.dispatch_control(session, 'set_model', {'model': 'sonnet'}))
        await wait_for_requests(session, 1)
        session.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-sonnet-5-5', effort='low')))
        read = await controls.read_settings(session)
        assert read['disposition']['disposition'] == 'applied'
        assert read['gate']['blocked'] is False
        task.cancel()  # the original caller goes away after the gate was verified open
        await asyncio.gather(task, return_exceptions=True)
        assert controls.settings_gate(session)['blocked'] is False
        assert controls.receipt(session, session.requests[0]['rid'])['state'] == 'applied'
    asyncio.run(scenario())


def test_reconcile_setting_state_uses_native_readback_only():
    async def scenario():
        session = FakeSession()
        session.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-sonnet-5-5', effort='low')))
        assert (await controls.reconcile_setting_state(session))['blocked'] is False
        assert session.requests == []  # nothing pending: no native call at all

        blocked = FakeSession(sid='fake-blocked')
        blocked.store.put('controls', blocked.sid, {
            'request_id': 'old-op', 'operation_class': 'setting', 'requested': {'model': 'sonnet'},
            'state': 'uncertain', 'blocks_send': True, 'dispatched_at': 1.0}, 'old-op')
        blocked.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-sonnet-5-5', effort='low')))
        result = await controls.reconcile_setting_state(blocked)
        assert result['blocked'] is False and result['disposition']['disposition'] == 'applied'

        dead = FakeSession(sid='fake-dead', alive=False)
        dead.store.put('controls', dead.sid, {
            'request_id': 'old-op-d', 'operation_class': 'setting', 'requested': {'model': 'sonnet'},
            'state': 'uncertain', 'blocks_send': True, 'dispatched_at': 1.0}, 'old-op-d')
        result = await controls.reconcile_setting_state(dead)
        assert result['blocked'] is True and 'not attempted' in result['reconcile']
    asyncio.run(scenario())


def test_launch_baseline_gate_verified_by_initial_readback():
    async def scenario():
        session = FakeSession()  # fresh launch: the lead closes the gate before any readback
        session.record.update(requested_model='sonnet', requested_effort='medium',
                              setting_control_pending=True)
        gate = controls.settings_gate(session)
        assert gate['blocked'] is True and gate['reason'] == 'awaiting_initial_readback'
        assert 'unverified since launch' in gate['disclosure']
        session.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-sonnet-5-5', effort='medium')))
        result = await controls.read_settings(session, authority='initialize')
        assert result['disposition']['disposition'] == 'applied_baseline'
        assert result['gate']['blocked'] is False
        assert session.record['controls_effective']['model'] == 'claude-sonnet-5-5'
        assert session.record['actual_model'] == 'claude-sonnet-5-5'
        assert session.record['actual_effort'] == 'medium'
        assert session.record['settings_mismatch'] is False
        assert any(event['kind'] == 'control_baseline_verified' for event in session.events)

        remapped = FakeSession(sid='fake-remap')
        remapped.record.update(requested_model='opus', requested_effort='high',
                               setting_control_pending=True)
        remapped.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-haiku-4-5', effort='high')))
        result = await controls.read_settings(remapped, authority='initialize')
        assert result['disposition']['disposition'] == 'mismatch'
        gate = controls.settings_gate(remapped)
        assert gate['blocked'] is True and gate['reason'] == 'effective_choice_differs'
        assert 'opus' in gate['disclosure'] and 'claude-haiku-4-5' in gate['disclosure']
        assert remapped.record['settings_mismatch'] is True
        assert [incident['category'] for incident in remapped.host.supervisor.incidents] == ['control_mismatch']

        unknown = FakeSession(sid='fake-unknown')  # no baseline effort anywhere: unverifiable, opens with a warning
        unknown.record.pop('requested_effort')
        unknown.script('set_model', lambda rid, request: reply(rid))
        unknown.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-sonnet-5-5', effort='low')))
        result = await controls.dispatch_control(unknown, 'set_model', {'model': 'sonnet'})
        assert result['state'] == 'applied_unverified' and result['blocks_send'] is False
        assert 'effort' in result['disclosure'] and unknown.record['settings_unverified'] is True

        dated = FakeSession(sid='fake-dated')  # a release-dated id of the requested model is that model
        dated.record.update(requested_model='claude-opus-5-5', requested_effort='high', setting_control_pending=True)
        dated.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-opus-5-5-20261001', effort='high')))
        result = await controls.read_settings(dated, authority='initialize')
        assert result['disposition']['disposition'] == 'applied_baseline' and result['gate']['blocked'] is False

        paid = FakeSession(sid='fake-1m')  # the paid 1M-context route is a different choice: blocked
        paid.record.update(requested_model='claude-opus-5-5', requested_effort='high', setting_control_pending=True)
        paid.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-opus-5-5[1m]', effort='high')))
        result = await controls.read_settings(paid, authority='initialize')
        assert result['disposition']['disposition'] == 'mismatch' and result['gate']['blocked'] is True
    asyncio.run(scenario())


def test_no_op_same_value_verified_no_op():
    async def scenario():
        session = FakeSession(effective={'model': 'claude-sonnet-5-5', 'effort': 'low'})
        session.script('set_model', lambda rid, request: reply(rid))
        session.script('get_settings', lambda rid, request: reply(
            rid, response=readback(model='claude-sonnet-5-5', effort='low')))
        result = await controls.dispatch_control(session, 'set_model', {'model': 'sonnet'})
        assert result['state'] == 'no_op_applied' and result['no_op'] is True
        assert result['applied'] is True and result['blocks_send'] is False
        assert controls.settings_gate(session)['blocked'] is False
    asyncio.run(scenario())


def test_interrupt_authorized_before_queue_and_settlement_requires_background_clear():
    async def scenario():
        session = FakeSession()
        session.pending = {'mid-1'}
        session.store.put('receipts', session.sid,
                          {'message_uuid': 'mid-1', 'status': 'started'}, 'mid-1')
        session.script('interrupt', lambda rid, request: reply(
            rid, response={'still_queued': [], 'cancelled': []}))
        result = await controls.dispatch_control(session, 'interrupt', {'authority': 'operator'})
        # the classifier must see the authorization before the native could answer
        assert session.requests[0]['record_at_issue']['interrupt_authorized'] == 'operator'
        assert session.record['interrupt_authorized'] == result['request_id']
        session.store.put('receipts', session.sid,
                          {'message_uuid': 'mid-1', 'status': 'completed', 'completed': True}, 'mid-1')
        session.pending.discard('mid-1')
        session.record['background_unsettled'] = True
        settlement = controls.interrupt_settlement(session, result['request_id'])
        assert settlement['settled'] is False and settlement['background_unsettled'] is True
        session.record['background_unsettled'] = False
        session.record['background_tasks'] = {'t1': {'status': 'running'}}
        settlement = controls.interrupt_settlement(session, result['request_id'])
        assert settlement['settled'] is False and settlement['background_task_ids'] == ['t1']
        session.record['background_tasks'] = {}
        assert controls.interrupt_settlement(session, result['request_id'])['settled'] is True
    asyncio.run(scenario())


def test_escalation_incident_and_inert_guard():
    async def scenario():
        session = FakeSession()
        session.script('set_model', lambda rid, request: {
            'status': 'error',
            'response': {'subtype': 'error', 'request_id': rid, 'error': 'model not entitled'}})
        result = await controls.dispatch_control(session, 'set_model', {'model': 'opus'})
        assert result['state'] == 'refused'
        incidents = session.host.supervisor.incidents
        assert len(incidents) == 1 and incidents[0]['category'] == 'control_refused'
        assert incidents[0]['handler'] == 'escalate' and incidents[0]['attention'] is True
        assert incidents[0]['key'].endswith(result['request_id'])
        inert = FakeSession(sid='fake-inert')
        inert.host.supervisor = None  # a session without a supervisor degrades to a no-op
        inert.script('set_model', lambda rid, request: {
            'status': 'error',
            'response': {'subtype': 'error', 'request_id': rid, 'error': 'model not entitled'}})
        assert (await controls.dispatch_control(inert, 'set_model', {'model': 'opus'}))['state'] == 'refused'
    asyncio.run(scenario())


def test_param_and_method_hygiene():
    async def scenario():
        session = FakeSession()
        with pytest.raises(ValueError, match='unknown control method'):
            await controls.dispatch_control(session, 'launch', {})
        with pytest.raises(ValueError, match='params must be object'):
            await controls.dispatch_control(session, 'get_settings', None)
        with pytest.raises(ValueError, match='unexpected control params'):
            await controls.dispatch_control(session, 'get_settings', {'verbose': True})
        with pytest.raises(ValueError, match='wrong generation'):
            await controls.dispatch_control(session, 'get_settings', {'generation': 2})
        with pytest.raises(ValueError, match='model must be a plain model name'):
            await controls.dispatch_control(session, 'set_model', {})
        with pytest.raises(ValueError, match='boolean'):
            await controls.dispatch_control(session, 'interrupt',
                                            {'authority': 'op', 'cancel_queued': 'yes'})
        with pytest.raises(ValueError, match='unexpected control params'):
            await controls.dispatch_control(session, 'interrupt', {'authority': 'op', 'x': 1})
        assert session.requests == []
    asyncio.run(scenario())


def test_parse_readback_shapes_and_matching():
    both = controls.parse_readback({'status': 'success', 'response': {'response': {
        'applied': {'model': 'claude-haiku-4-5', 'effort': 'low'},
        'effective': {'effortLevel': 'low'}}}})
    assert both['model'] == 'claude-haiku-4-5' and both['effort'] == 'low' and both['complete'] is True
    # incomplete shapes are readbacks with the missing value None: unverifiable, never applied, never "different"
    partial = controls.parse_readback({'status': 'success', 'response': {'response': {
        'applied': {'effort': 'high'}, 'effective': {'effortLevel': 'high'}}}})
    assert partial['model'] is None and partial['effort'] == 'high' and partial['complete'] is False
    effective_only = controls.parse_readback({'status': 'success', 'response': {'response': {
        'effective': {'model': 'claude-sonnet-5-5', 'effortLevel': 'low'}}}})
    assert effective_only['model'] is None and effective_only['effort'] is None and not effective_only['complete']
    assert controls.parse_readback({'status': 'pending'}) is None
    assert controls.parse_readback({'status': 'error', 'response': {'subtype': 'error'}}) is None
    assert controls.parse_readback({'status': 'success', 'response': {'response': {}}})['complete'] is False
    assert controls.parse_readback({'status': 'success', 'response': 'v3'})['complete'] is False
    assert controls.compare_model('claude-opus-5-5', 'claude-opus-5-5-20261001') == 'match'
    assert controls.compare_model('claude-opus-5-5', 'claude-opus-5-5[1m]') == 'differs'
    assert controls.compare_model('claude-opus-5-5', 'opus') == 'unknown'
    assert controls.compare_model('claude-opus-5-5', 'sonnet') == 'differs'
    assert controls.compare_model('claude-opus-5-5', None) == 'unknown'
    assert controls.compare_effort('high', 'HIGH') == 'match'
    assert controls.compare_effort('high', 'low') == 'differs'
    assert controls.compare_effort('high', 'x-high') == 'unknown'
    assert controls.compare_effort('high', {'level': 'high'}) == 'unknown'
    assert controls.model_matches('sonnet', 'claude-sonnet-5-5')
    assert controls.model_matches('opus', 'claude-opus-4-6')
    assert controls.model_matches('fable', 'claude-fable-5-1')               # Claude Code's `fable` alias on Max
    assert controls.model_matches('claude-fable-5-1', 'claude-fable-5-1')
    assert not controls.model_matches('claude-fable-5-1', 'claude-fable-5')
    assert controls.model_matches('claude-sonnet-5-5', 'claude-sonnet-5-5')
    assert not controls.model_matches('son', 'claude-sonnet-5-5')
    assert not controls.model_matches('sonnet', 'claude-haiku-5-5')
    assert not controls.model_matches('claude-haiku-4-5', 'claude-haiku-4-5-remap')
    assert not controls.model_matches('sonnet', 'sonnetx-5')
    assert controls.effort_matches('High', 'high')
    assert not controls.effort_matches('high', 'xhigh')
