"""U1a host additions for seats: immutable launch labels, first launch time, newest input, health flags (offline)."""
import asyncio
import uuid

import pytest

from claude_headless.client import Client, HostError
from claude_headless.host import Host, validate_labels
from test_claude_headless_host import auth, builder, send, teardown, until

LABELS = {'title': '[ap:PROG]-001 ▸ ⚡ quick worker · [ap:S]', 'kind': 'seat', 'quick': True,
          'manager_session': '183', 'conductor_id': 'cc-x', 'conductor_n': 1, 'ratio': .5, 'seat_base': None}


def test_validate_labels_accepts_scalars_and_refuses_everything_else():
    assert validate_labels(None) is None
    assert validate_labels(LABELS) == LABELS and validate_labels(LABELS) is not LABELS
    for bad in ([], 'title', {'Title': 'x'}, {'a-b': 1}, {'': 1}, {'x' * 65: 1}, {'x': [1]}, {'x': {'a': 1}},
                {'x': float('nan')}, {'x': float('inf')}, {'x': 'y' * 2001}, {f'k{i}': 1 for i in range(33)}):
        with pytest.raises(ValueError):
            validate_labels(bad)


def ready(session):
    return session.record['state'] == 'ready' and not session.record.get('setting_control_pending')


def test_labels_are_immutable_across_resume_and_the_newest_input_is_recorded(tmp_path):
    async def scenario():
        host = Host(tmp_path / 'state', tmp_path / 'host.sock', launch_builder=builder, auth_check=auth)
        serving = asyncio.create_task(host.serve())
        await until(lambda: host.socket_path.exists())
        client = Client(host.socket_path)
        try:
            health = await client.acall('health')
            assert health['seat_interface'] == 1
            assert health['unattended_roles_enabled'] is True          # Dan's ruling D2
            assert health['native_tool_time_context_qualified'] is False  # still the honest native fact
            with pytest.raises(HostError, match='label'):
                await client.acall('launch', native_uuid=str(uuid.uuid4()), cwd=str(tmp_path), model='haiku',
                                   effort='low', system_prompt='fixture', role='worker', labels={'Bad': 1})
            assert host.store.sessions() == []   # refused before anything was registered
            sid = str(uuid.uuid4())
            row = await client.acall('launch', native_uuid=sid, cwd=str(tmp_path), model='haiku', effort='low',
                                     system_prompt='fixture', role='worker', socket_handle='[ap:S]', labels=LABELS)
            assert row['labels'] == LABELS and isinstance(row['first_launched_at'], float)
            session = host.sessions[sid]
            await until(lambda: ready(session))
            assert (await client.acall('state', execution_id=row['execution_id'])).get('last_input_uuid') is None
            mid, _ = await send(client, row, 'hello')
            state = await client.acall('state', execution_id=row['execution_id'])
            assert state['last_input_uuid'] == mid and isinstance(state['last_input_at'], float)
            await until(lambda: host.store.get('receipts', sid, mid)['completed'])
            await client.acall('close_input', execution_id=row['execution_id'], generation=1)
            await until(lambda: session.record.get('owner') == 'gone')
            resumed = await client.acall('resume', execution_id=row['execution_id'])
            assert resumed['generation'] == 2 and resumed['labels'] == LABELS
            assert resumed['first_launched_at'] == row['first_launched_at']
            assert resumed['last_input_uuid'] == mid   # carried until the next input replaces it
            second = host.sessions[sid]
            await until(lambda: ready(second))
            with pytest.raises(HostError, match='live owner'):
                await client.acall('launch', native_uuid=sid, cwd=str(tmp_path), model='haiku', effort='low',
                                   system_prompt='fixture', role='worker', resume=True, labels={'title': 'changed'})
            await client.acall('close_input', execution_id=row['execution_id'], generation=2)
            await until(lambda: second.record.get('owner') == 'gone')
            with pytest.raises(HostError, match='cannot change execution labels'):
                await client.acall('launch', native_uuid=sid, cwd=str(tmp_path), model='haiku', effort='low',
                                   system_prompt='fixture', role='worker', resume=True, labels={'title': 'changed'})
            unlabeled = await client.acall('launch', native_uuid=str(uuid.uuid4()), cwd=str(tmp_path),
                                           model='haiku', effort='low', system_prompt='fixture', role='helper')
            assert unlabeled['labels'] is None
            await until(lambda: ready(host.sessions[unlabeled['native_uuid']]))
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_rate_limit_frames_are_timestamped(tmp_path):
    """The quota guard orders frames by durable cursor; the timestamp is for the human line."""
    from claude_headless.host import NativeSession

    class Store:
        def put(self, *a, **k):
            pass

    class FakeHost:
        store = Store()
        changed = asyncio.Event()
        monotonic = staticmethod(lambda: 0)

        class supervisor:
            @staticmethod
            def incident(*a, **k):
                raise AssertionError('no incident for an allowed frame')

    record = {'native_uuid': str(uuid.uuid4()), 'generation': 1}
    session = NativeSession.__new__(NativeSession)
    session.host, session.store, session.record = FakeHost(), FakeHost.store, record
    session.sid, session.settlement_probe, session.settled_since = record['native_uuid'], None, None
    frame = {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed', 'unifiedWindows': {
        'five_hour': {'utilization': .2, 'resetsAt': 1}}}}
    NativeSession.project(session, frame, '', 'store:7')
    assert record['last_rate_limit_cursor'] == 'store:7' and isinstance(record['last_rate_limit_at'], float)
    assert record['last_rate_limit_info']['unifiedWindows']['five_hour']['utilization'] == .2
