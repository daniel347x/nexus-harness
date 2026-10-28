"""Private inert wire / real host proof of conversation restoration and fail-closed EOF."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace

import pytest

from claude_headless.collection import collect_session
from claude_headless.host import Host, NativeSession, owner_lock, witness
from claude_headless.restart import settlement_blockers as restart_blockers
from claude_headless.settlement import settlement_advisories, settlement_blockers
from claude_headless.store import private_file
from test_claude_headless_host import teardown, until


class Clock:
    def __init__(self):
        self.value = 100.
    def __call__(self):
        return self.value


async def setup(tmp_path, *, launch_environment=None):
    clock = Clock()
    async def auth():
        return {'subscriptionType': 'max', 'cli_version': '2.1.300 (Claude Code)'}
    def builder(run_dir, **params):
        if launch_environment is not None:
            from claude_headless.launch import build_launch
            spec = build_launch(run_dir, **{**params, 'environ': launch_environment,
                                          'workflowy_url': 'http://127.0.0.1:8766/mcp'})
            # Real environment, snapshots and computed envelope, but never a native CLI/inference.
            spec['argv'] = [sys.executable, str(Path(__file__).parent / 'fixtures/claude_lifecycle_cli.py')]
            return spec
        for name in ('prompt.md', 'mcp.json', 'settings.json'):
            private_file(run_dir / name, b'fixture')
        return {'argv': [sys.executable, str(Path(__file__).parent / 'fixtures/claude_lifecycle_cli.py')],
            'env': dict(os.environ), 'safe': {'collector_envelope': {'eligible': True}}}
    host = Host(tmp_path / 'state', tmp_path / 'h.sock', auth_check=auth,
        launch_builder=builder, monotonic=clock, collection_threshold=10, collection_cadence=180)
    serving = asyncio.create_task(host.serve())
    await until(lambda: host.socket_path.exists())
    row = await host.launch(native_uuid=str(uuid.uuid4()), cwd=str(tmp_path), model='haiku',
        effort='low', system_prompt='fixture', role='Manager')
    session = host.sessions[row['native_uuid']]
    await until(lambda: session.record.get('native_session_state') == 'idle' and
        session.record.get('background_level_known') and not session.record.get('setting_control_pending'))
    session.update(native_state_required=True)
    return host, serving, session, clock


def frame(session, value):
    text = (json.dumps(value) + '\n').encode()
    offset = session.store.append_bytes(session.sid, session.generation, 'stdout', text)
    session.frame(text, offset, len(text))


def test_collected_conversation_reconnects_once_for_ten_requests_and_new_reply(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            clock.value += 11
            assert await collect_session(host, session)
            await until(lambda: session.record.get('owner') == 'gone')
            assert session.record['close_reason'] == 'collected'
            assert session.record['collected_settled_age_seconds'] >= 10
            rows = await asyncio.gather(*(host.reconnect(execution_id=session.sid, generation=1) for _ in range(10)))
            assert {row['generation'] for row in rows} == {2}
            assert len({row['pid'] for row in rows}) == 1
            replacement = host.sessions[session.sid]
            await until(lambda: replacement.record['state'] == 'ready' and
                not replacement.record.get('setting_control_pending') and replacement.record['native_session_state'] == 'idle')
            mid = str(uuid.uuid4())
            receipt = await replacement.send(mid, 'new deliberate Send', 'original')
            await until(lambda: host.store.get('receipts', replacement.sid, mid)['completed'])
            assert host.store.get('receipts', replacement.sid, mid)['acknowledged']
            assert receipt['generation'] == 2
            writes = host.store.path(session.sid, 2, 'stdin').read_text().splitlines()
            assert sum(json.loads(line).get('type') == 'user' for line in writes) == 1
            assert host.store.db.execute("SELECT count(*) FROM events WHERE kind='process_started'").fetchone()[0] == 2
        finally:
            await teardown(host, serving)
    asyncio.run(run())


@pytest.mark.parametrize('unknown_level', [False, True])
def test_real_launch_from_parent_claude_context_reaches_native_proof_collection(tmp_path, unknown_level):
    from test_claude_headless_launch import PARENT_SESSION_ENVIRONMENT
    async def run():
        host, serving, session, clock = await setup(tmp_path, launch_environment={
            **PARENT_SESSION_ENVIRONMENT, 'WORKFLOWY_MCP_BEARER': 'inert-test-bearer'})
        try:
            assert session.record['launch']['collector_envelope']['eligible'] is True
            assert session.record['launch']['removed_env_names'] == sorted(PARENT_SESSION_ENVIRONMENT)
            spawned_names = {entry.split(b'=', 1)[0] for entry in
                Path(f'/proc/{session.process.pid}/environ').read_bytes().split(b'\0') if b'=' in entry}
            assert not ({name.encode() for name in PARENT_SESSION_ENVIRONMENT} & spawned_names)
            if unknown_level:
                session.update(background_level_known=False)
                clock.value += 10000  # old wall/silence age cannot replace a known task baseline
                assert not await collect_session(host, session)
                assert session.record['background_level_known'] is True
                assert session.process.returncode is None
                assert session.settled_since == clock.value
            assert not await collect_session(host, session)  # still need the entire settled span
            clock.value += host.collection_threshold + 1
            assert await collect_session(host, session)
            await until(lambda: session.record.get('owner') == 'gone')
            assert session.record['close_reason'] == 'collected'
            proof = session.record['collection_proof']
            assert proof['settled_age_seconds'] >= host.collection_threshold
            assert proof['response_cursor'] and proof['snapshot_cursor']
        finally:
            await teardown(host, serving)
    asyncio.run(run())


@pytest.mark.parametrize('resume', [False, True], ids=['current-generation', 'inherited-incident'])
def test_open_escalation_does_not_pin_idle_process(tmp_path, resume):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            incident = host.supervisor.incident(session.record, 'unknown', 'escalate',
                detail={'reason': 'an old diagnostic still needs review'}, attention=True)
            incident_before = copy.deepcopy(incident)
            iid = incident['incident_id']
            session.update()
            if resume:
                # Like Manager-Session-208: the incident belongs to an earlier process,
                # but stays pending after a deliberate same-conversation reconnect.
                await host.dispatch({'method': 'close_input', 'params': {
                    'execution_id': session.sid, 'generation': session.generation}})
                await until(lambda: session.record.get('owner') == 'gone')
                await host.reconnect(execution_id=session.sid, generation=session.generation)
                session = host.sessions[session.sid]
                await until(lambda: session.record.get('native_session_state') == 'idle' and
                    session.record.get('background_level_known') and
                    not session.record.get('setting_control_pending'))
                assert session.generation > incident['generation']
            assert session.record['pending_escalations'] == [iid]
            assert settlement_blockers(session.record, require_native=True) == []
            assert settlement_advisories(session.record) == [
                'pending escalation(s) awaiting operator disposition']
            assert session.settled_since == clock.value
            clock.value += host.collection_threshold - 1
            assert not await collect_session(host, session)  # no shorter idle threshold
            assert session.process.returncode is None
            clock.value += 2
            assert await collect_session(host, session)
            await until(lambda: session.record.get('owner') == 'gone')
            stored = host.state(session.sid)
            assert stored['close_reason'] == 'collected'
            assert stored['pending_escalations'] == [iid]  # collection is not a disposition
            assert stored['collection_proof']['response_cursor']
            assert stored['collection_proof']['snapshot_cursor']
            assert host.store.get('incidents', session.sid, iid) == incident_before
            writes = host.store.path(session.sid, session.generation, 'stdin').read_text().splitlines()
            assert not any(json.loads(line).get('type') == 'user' for line in writes)
        finally:
            await teardown(host, serving)
    asyncio.run(run())


OPERATOR_ADVISORY = [{'setting_control_pending': True}, {'settings_mismatch': True},
                     {'protocol_attention': True},
                     {'pending_control_requests': ['unknown-control']}]


@pytest.mark.parametrize('values', [
    {'native_session_state': 'running'},  # human or synthetic/native-only held reply
    {'native_session_state': 'requires_action'},
    {'background_tasks': {'bash': {'task_id': 'bash', 'run_id': 'b'}}, 'background_unsettled': True},
    {'background_tasks': {'agent': {'task_id': 'agent', 'run_id': 'a'}}, 'background_unsettled': True},
    {'pending_native_requests': {'permission': {'subtype': 'can_use_tool'}}},
    {'pending_native_requests': {'dialog': {'subtype': 'request_user_dialog'}}},
    {'setting_control_pending': True}, {'settings_mismatch': True},
    {'native_retry_active': True}, {'pending_recovery': 'scheduled'},
    {'pending_message_uuids': ['unanswered']}, {'protocol_attention': True},
    {'background_level_known': False}, {'native_session_state': 'unknown'},
    {'pending_control_requests': ['unknown-control']}, {'native_queued_turns': 1},
    {'infrastructure_error': 'IOError'},
])
def test_all_work_and_unknown_holds_protect_collector_and_direct_close(tmp_path, values):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            session.update(pending_escalations=['incident'], **values)
            clock.value += 10000
            before = host.store.path(session.sid, 1, 'stdin').read_bytes()
            assert not await collect_session(host, session)
            session.update(**values)  # a baseline resnapshot may have learned the formerly unknown level
            assert settlement_blockers(session.record, automatic=False) == restart_blockers(session.record)
            if values in OPERATOR_ADVISORY:
                # Guard sweep: these protect nothing a deliberate operator close could lose. The
                # collector still holds on them; an explicit close proceeds and reports them.
                assert restart_blockers(session.record) == []
                assert settlement_advisories(session.record)
                closed = await host.dispatch({'method': 'close_input', 'params': {
                    'execution_id': session.sid, 'generation': 1}})
                assert closed['status'] == 'input_close_queued'
                return
            with pytest.raises(RuntimeError, match='unsettled work'):
                await host.dispatch({'method': 'close_input', 'params': {
                    'execution_id': session.sid, 'generation': 1}})
            assert session.process.returncode is None
            after = host.store.path(session.sid, 1, 'stdin').read_bytes()
            if values == {'background_level_known': False}:
                assert b'"subtype": "initialize"' in after[len(before):]  # read-only baseline, no close
            else:
                assert after == before
        finally:
            # Error is test projection only, never a real failed Store.
            session.infrastructure_error = None
            await teardown(host, serving)
    asyncio.run(run())


def test_native_only_turn_resets_age_and_old_task_run_cannot_remove_new(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            frame(session, {'type': 'system', 'subtype': 'background_tasks_changed',
                'tasks': [{'task_id': 'child', 'run_id': 'new-run'}]})
            frame(session, {'type': 'system', 'subtype': 'task_notification', 'task_id': 'child',
                'run_id': 'old-run', 'status': 'completed'})
            assert session.record['background_tasks']['child']['run_id'] == 'new-run'
            frame(session, {'type': 'system', 'subtype': 'task_updated', 'task_id': 'child',
                'run_id': 'old-run', 'patch': {'status': 'completed'}})
            assert session.record['background_unsettled']
            frame(session, {'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': []})
            clock.value += 20
            frame(session, {'type': 'system', 'subtype': 'session_state_changed', 'state': 'running'})
            assert not session.pending and session.settled_since is None
            assert not await collect_session(host, session)
            frame(session, {'type': 'system', 'subtype': 'api_retry', 'error_status': 429})
            frame(session, {'type': 'result', 'subtype': 'success', 'is_error': False,
                'origin': {'kind': 'task-notification'}})
            assert not session.record['native_retry_active']
            frame(session, {'type': 'system', 'subtype': 'session_state_changed', 'state': 'idle'})
            assert session.settled_since == clock.value
            assert not await collect_session(host, session)  # threshold restarted, not old age
        finally:
            await teardown(host, serving)
    asyncio.run(run())


@pytest.fixture
def wait_projection(monkeypatch):
    """Real Session projector/update, inert in-memory record sink; no Host, process or live store."""
    writes = []
    store = SimpleNamespace(put=lambda category, sid, record: writes.append(copy.deepcopy(record)))
    monkeypatch.setattr('claude_headless.host.SqliteRows', lambda store, sid: None)
    record = {'native_uuid': 'u4-synthetic', 'generation': 1, 'state': 'ready', 'owner': 'owned',
              'primary_turn_open': False, 'background_tasks': {}, 'background_level_known': True}
    session = NativeSession(SimpleNamespace(store=store, monotonic=Clock()), record, None, None)
    return session, writes


def background_call(tool_id='tool1', command='bash /x/oc_seat.sh wait cc-abc --max 540', **fields):
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'text', 'text': 'unrelated prose'},
        {'type': 'tool_use', 'id': tool_id, 'name': 'Bash', 'input': {
            'command': command, 'description': 'Wait for the worker', 'run_in_background': True, **fields}}]}}


def background_start(task_id='task1', tool_id='tool1', run_id='run1'):
    return {'type': 'system', 'subtype': 'task_started', 'task_id': task_id, 'tool_use_id': tool_id,
            'run_id': run_id, 'task_type': 'local_bash', 'description': 'Native task description',
            'is_backgrounded': True}


def test_u4_t41_background_tool_before_start_keeps_command_start_time_and_progress(wait_projection, monkeypatch):
    session, writes = wait_projection
    monkeypatch.setattr('claude_headless.host.time.time', lambda: 1700000000.25)
    call = background_call()
    session.project(call, json.dumps(call), 'call')
    assert session.record['background_tasks'] == {}, 'an input alone never creates a task'
    start = background_start()
    session.project(start, json.dumps(start), 'start')
    task = session.record['background_tasks']['task1']
    assert task['tool_input'] == {'name': 'Bash', 'command': call['message']['content'][1]['input']['command'],
                                 'description': 'Wait for the worker'}
    assert task['started_at'] == 1700000000.25
    for subtype in ('task_progress', 'task_updated'):
        event = {'type': 'system', 'subtype': subtype, 'task_id': 'task1', 'run_id': 'run1',
                 'patch': {'status': 'running', 'last_tool_name': 'fixture'}}
        session.project(event, json.dumps(event), subtype)
        assert session.record['background_tasks']['task1']['tool_input'] == task['tool_input']
        assert session.record['background_tasks']['task1']['started_at'] == task['started_at']
    assert writes[-1]['background_tasks'] == session.record['background_tasks']
    event['patch']['status'] = 'completed'
    session.project(event, json.dumps(event), 'end')
    assert session.record['background_tasks'] == {}
    assert session.record['background_unsettled'] is False


def test_u4_t42_start_before_tool_attaches_even_after_a_trimmed_same_run_level(wait_projection, monkeypatch):
    session, writes = wait_projection
    monkeypatch.setattr('claude_headless.host.time.time', lambda: 1700000001.5)
    start = background_start()
    session.project(start, json.dumps(start), 'start')
    level = {'type': 'system', 'subtype': 'background_tasks_changed',
             'tasks': [{'task_id': 'task1', 'run_id': 'run1', 'task_type': 'local_bash'}]}
    session.project(level, json.dumps(level), 'level')
    revision = session.work_revision
    call = background_call()
    session.project(call, json.dumps(call), 'late-call')
    task = session.record['background_tasks']['task1']
    assert task['tool_input']['command'] == call['message']['content'][1]['input']['command']
    assert task['tool_use_id'] == 'tool1' and task['started_at'] == 1700000001.5
    assert task['cursor'] == 'level', 'attaching input is not a new task lifecycle event'
    assert set(session.record['background_tasks']) == {'task1'}
    assert session.record['background_unsettled'] is True
    assert session.record['background_level_known'] is True
    assert session.work_revision == revision + 1, 'only the existing assistant reset_settlement call'
    assert writes[-1]['background_tasks'] == session.record['background_tasks'], 'late attach is persisted'


def test_u4_t43_level_preserves_only_same_run_details_and_exact_level_membership(wait_projection):
    session, _ = wait_projection
    for task_id in ('keep', 'replace', 'gone'):
        call = background_call(task_id)
        start = background_start(task_id, task_id)
        session.project(call, json.dumps(call), 'call-' + task_id)
        session.project(start, json.dumps(start), 'start-' + task_id)
    previous = copy.deepcopy(session.record['background_tasks']['keep'])
    level = {'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': [
        {'task_id': 'keep', 'run_id': 'run1', 'description': 'level description'},
        {'task_id': 'replace', 'run_id': 'run2'}, {'task_id': 'new', 'run_id': 'new-run'}]}
    session.project(level, json.dumps(level), 'level')
    tasks = session.record['background_tasks']
    assert set(tasks) == {'keep', 'replace', 'new'}
    assert tasks['keep'] == {**previous, **level['tasks'][0], 'cursor': 'level'}
    assert tasks['replace'] == {**level['tasks'][1], 'cursor': 'level'}
    assert tasks['new'] == {**level['tasks'][2], 'cursor': 'level'}
    assert session.record['background_level_known'] is True and session.record['background_unsettled'] is True


def test_u4_t44_only_primary_explicit_background_inputs_are_captured_cropped_and_bounded(wait_projection):
    session, _ = wait_projection
    for value in (False, None, 1, 'true'):
        call = background_call('foreground', run_in_background=value)
        session.project(call, json.dumps(call), 'foreground')
    for fields in ({'parent_tool_use_id': 'parent'}, {'isSidechain': True}):
        call = {**background_call('child'), **fields}
        session.project(call, json.dumps(call), 'child')
    assert not getattr(session, 'background_inputs', {}), 'foreground and child calls are not captured'
    call = background_call(command='🌈' * 2000, description='d' * 300)
    session.project(call, json.dumps(call), 'long-call')
    start = background_start()
    session.project(start, json.dumps(start), 'start')
    task = session.record['background_tasks']['task1']
    assert task['tool_input'] == {'name': 'Bash', 'command': '🌈' * 1024, 'description': 'd' * 256}
    assert len(call['message']['content'][1]['input']['command']) == 2000, 'native input is not rewritten'
    for n in range(65):
        call = background_call('extra-' + str(n), command=42, description=None)
        session.project(call, json.dumps(call), 'extra-' + str(n))
    assert len(session.background_inputs) == 64
    assert 'tool1' not in session.background_inputs and 'extra-0' not in session.background_inputs
    assert session.background_inputs['extra-64'] == {'name': 'Bash', 'command': '', 'description': ''}
    assert set(session.record['background_tasks']) == {'task1'}, 'eviction never changes task membership'
    assert session.record['background_tasks']['task1'] == task, 'eviction never discards attached metadata'


def test_orphan_recheck_requires_both_pid_witness_absent_and_lock_free(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            await host.dispatch({'method': 'close_input', 'params': {'execution_id': session.sid, 'generation': 1}})
            await until(lambda: session.record.get('owner') == 'gone')
            assert session.sid not in host.sessions  # released at its journaled exit
            record = host.store.get('sessions', session.sid)
            record.update(state='ownership_unknown', owner='unknown')
            host.store.put('sessions', session.sid, record)
            fd = owner_lock(host.root / session.sid / 'owner.lock')
            host.recheck_orphans()
            assert host.state(session.sid)['state'] == 'ownership_unknown'
            os.close(fd)
            record.update(pid=os.getpid(), process_witness=witness(os.getpid()))
            host.store.put('sessions', session.sid, record)
            host.recheck_orphans()
            assert host.state(session.sid)['state'] == 'ownership_unknown'
            record['process_witness'] = 'gone-witness'
            host.store.put('sessions', session.sid, record)
            clock.value += 61
            await host.supervisor.tick()
            assert host.state(session.sid)['state'] == 'exited'
            assert host.state(session.sid)['orphan_exit_rechecked']
        finally:
            await teardown(host, serving)
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['missing', 'late', 'wrong-pid', 'missing-version', 'malformed-version',
    'running', 'nonempty', 'malformed-level', 'no-fresh-level', 'work-after-query', 'child-level', 'foreign-level'])
def test_boundary_readback_unknown_or_changed_never_authorizes_eof(tmp_path, monkeypatch, mode):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        work = None
        try:
            # Inject the native readback at the projector seam instead of asking the inert
            # fixture, so wrong-PID/lost/snapshot-order cases cannot accidentally be repaired.
            original_control = session.control
            def control(subtype, request=None, request_id=None, **kwargs):
                if not kwargs.get('settlement_readback'):
                    return original_control(subtype, request, request_id, **kwargs)
                session.store.put('controls', session.sid, {'request_id': request_id,
                    'subtype': 'initialize', 'settlement_readback': True,
                    'generation': session.generation, 'status': 'pending'}, request_id)
                async def answer():
                    await asyncio.sleep(0)
                    if mode == 'missing':
                        clock.value += 31
                        return
                    body = {'pid': session.process.pid, 'claude_code_version': '2.1.300', 'session_state': 'idle'}
                    if mode == 'wrong-pid': body['pid'] += 1
                    if mode == 'missing-version': del body['claude_code_version']
                    if mode == 'malformed-version': body['claude_code_version'] = 'unknown'
                    if mode == 'running': body['session_state'] = 'running'
                    frame(session, {'type': 'control_response', 'response': {'subtype': 'success',
                        'request_id': request_id, 'response': body}})
                    if mode == 'late': clock.value += 31
                    if mode == 'no-fresh-level':
                        clock.value += 31
                        return
                    tasks = [] if mode != 'nonempty' else [{'task_id': 'agent', 'run_id': 'run'}]
                    if mode == 'malformed-level': tasks = [{'task_id': 'no-run'}]
                    level = {'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': tasks}
                    if mode == 'child-level': level['parent_tool_use_id'] = 'child-call'
                    if mode == 'foreign-level': level['session_id'] = str(uuid.uuid4())
                    frame(session, level)
                    if mode == 'work-after-query':
                        frame(session, {'type': 'system', 'subtype': 'session_state_changed', 'state': 'running'})
                nonlocal work
                work = asyncio.create_task(answer())
                return {'request_id': request_id}
            monkeypatch.setattr(session, 'control', control)
            clock.value += 11
            assert not await collect_session(host, session)
            assert session.process.returncode is None
            assert session.record['state'] == 'ready'
            assert not session.collection_in_progress
        finally:
            if work: await work
            await teardown(host, serving)
    asyncio.run(run())


def test_native_readback_is_not_a_reinitialize_auth_or_settings_reset(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            prior = (session.record['initialize_request_id'], dict(session.record['controls_effective']))
            clock.value += 11
            assert await collect_session(host, session)
            await until(lambda: session.record['owner'] == 'gone')
            assert (session.record['initialize_request_id'], session.record['controls_effective']) == prior
            assert not session.record['setting_control_pending']
            controls = host.store.records('controls', session.sid)
            assert sum(row['subtype'] == 'get_settings' for row in controls) == 1
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_unqualified_launch_never_collects_even_after_days_and_no_version_is_pinned(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            clock.value += 86400 * 7
            for value in ({'collector_envelope': {}}, {'collector_envelope': {'eligible': False}}, {}):
                session.update(launch=value)
                assert not await collect_session(host, session)
                assert 'launch envelope not collectable' in session.record['collection_refusal']
            assert session.process.returncode is None
            # The launch-time `claude --version` string gates nothing any more.
            session.update(launch={'collector_envelope': {'eligible': True}}, auth={'cli_version': 'unknown'})
            assert await collect_session(host, session)
            await until(lambda: session.record.get('owner') == 'gone')
        finally:
            await teardown(host, serving)
    asyncio.run(run())


@pytest.mark.parametrize('version', ['2.1.299', '2.1.300', '2.2.0', '3.0.0-beta.1'])
def test_any_claude_code_version_is_collected_when_its_own_readback_proves_the_capabilities(
        tmp_path, monkeypatch, version):
    monkeypatch.setenv('FIXTURE_CLAUDE_CODE_VERSION', version)
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            mid = str(uuid.uuid4())
            await session.send(mid, 'one finished turn', 'one finished turn')
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'] and
                        session.record['native_session_state'] == 'idle')
            assert session.record['native_cli_version'] == version
            assert host.state(session.sid)['collection_eligible'] is True
            assert host.state(session.sid)['collection_refusal'] is None
            health = await host.dispatch({'method': 'health'})
            assert health['collection_gate'] == 'native_capabilities' and health['collection_native_proof']
            clock.value += 11
            assert await collect_session(host, session)
            await until(lambda: session.record.get('owner') == 'gone')
            assert session.record['close_reason'] == 'collected'
        finally:
            await teardown(host, serving)
    asyncio.run(run())


@pytest.mark.parametrize('version', ['', 'unknown', '2.1'])
def test_a_readback_without_a_well_formed_version_refuses_that_process_and_says_why(
        tmp_path, monkeypatch, version):
    monkeypatch.setenv('FIXTURE_CLAUDE_CODE_VERSION', version)
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            clock.value += 10000
            for _ in range(2):
                assert not await collect_session(host, session)
            assert session.process.returncode is None and session.record['state'] == 'ready'
            skipped = [e['detail']['reason'] for e in host.store.replay(sid=session.sid, limit=500)['events']
                       if e['kind'] == 'collection_skipped']
            assert skipped and all('initialize readback missing or malformed' in reason for reason in skipped)
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_a_release_without_session_state_pushes_is_refused_once_it_ran_a_turn(tmp_path, monkeypatch):
    monkeypatch.setenv('FIXTURE_NO_STATE_EVENTS', '1')
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            assert host.state(session.sid)['collection_refusal'] is None  # no turn yet: nothing to push
            mid = str(uuid.uuid4())
            await session.send(mid, 'a turn with no state pushes', 'a turn with no state pushes')
            await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
            clock.value += 10000
            for _ in range(3):
                assert not await collect_session(host, session)
            assert 'session_state_changed' in session.record['collection_refusal']
            refused = [e for e in host.store.replay(sid=session.sid, limit=500)['events']
                       if e['kind'] == 'collection_refused']
            assert len(refused) == 1 and 'session-state events missing' in refused[0]['detail']['reason']
            assert session.process.returncode is None
            # Only the automatic collector refuses; a deliberate operator close is unaffected.
            closed = await host.dispatch({'method': 'close_input', 'params': {
                'execution_id': session.sid, 'generation': 1}})
            assert closed['status'] == 'input_close_queued'
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_an_unrecognised_native_system_subtype_refuses_only_the_collector(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            frame(session, {'type': 'system', 'subtype': 'some_future_background_notice'})
            assert settlement_blockers(session.record, require_native=True) == []  # never a hold
            clock.value += 10000
            assert not await collect_session(host, session)
            assert 'some_future_background_notice' in host.state(session.sid)['collection_refusal']
            assert session.process.returncode is None
            closed = await host.dispatch({'method': 'close_input', 'params': {
                'execution_id': session.sid, 'generation': 1}})
            assert closed['status'] == 'input_close_queued'
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_host_resume_gateway_migration_preserves_other_bytes_and_records_change(tmp_path, monkeypatch):
    from claude_headless import launch
    from claude_headless.launch import mcp_url_only_change
    original_builder = launch.build_launch
    gateway = ['172.20.0.1']
    monkeypatch.setattr(launch, 'default_gateway_ip', lambda: gateway[0])
    monkeypatch.setenv('WORKFLOWY_MCP_BEARER', 'dummy-not-a-secret')
    monkeypatch.delenv('WORKFLOWY_MCP_URL', raising=False)
    def private_builder(run_dir, **kwargs):
        spec = original_builder(run_dir, **kwargs)
        spec['argv'] = [sys.executable, str(Path(__file__).parent / 'fixtures/claude_lifecycle_cli.py')]
        return spec  # no remote MCP/API: this is an inert CLI, not a native provider override
    monkeypatch.setattr(launch, 'build_launch', private_builder)
    async def run():
        async def auth(): return {'subscriptionType': 'max', 'cli_version': '2.1.299 (Claude Code)'}
        host = Host(tmp_path / 'state', tmp_path / 'h.sock', auth_check=auth)
        serving = asyncio.create_task(host.serve())
        await until(lambda: host.socket_path.exists())
        try:
            row = await host.launch(native_uuid=str(uuid.uuid4()), cwd=str(tmp_path), model='haiku',
                effort='low', system_prompt='exact saved persona', role='Manager')
            session = host.sessions[row['native_uuid']]
            await until(lambda: session.record.get('native_session_state') == 'idle' and
                not session.record.get('setting_control_pending'))
            prior = Path(row['run_dir'])
            await host.dispatch({'method': 'close_input', 'params': {'execution_id': session.sid, 'generation': 1}})
            await until(lambda: session.record['owner'] == 'gone')
            gateway[0] = '172.22.0.1'
            resumed = await host.reconnect(execution_id=session.sid, generation=1)
            current = Path(resumed['run_dir'])
            assert (prior / 'prompt.md').read_bytes() == (current / 'prompt.md').read_bytes()
            assert (prior / 'settings.json').read_bytes() == (current / 'settings.json').read_bytes()
            proof = mcp_url_only_change((prior / 'mcp.json').read_bytes(), (current / 'mcp.json').read_bytes())
            assert proof['url_only'] and not proof['identical']
            assert proof['current_url'] == 'http://172.22.0.1:8766/mcp'
            assert resumed['launch']['workflowy_url_source'] == 'gateway'
            page = host.store.replay(sid=session.sid, limit=500)
            evidence = next(event for event in page['events'] if event['kind'] == 'workflowy_endpoint_reresolved')
            assert evidence['detail']['all_other_snapshot_bytes'] == 'identical'
            # Readiness belongs to the new generation, not the launch response itself.
            replacement = host.sessions[session.sid]
            await until(lambda: replacement.record['native_session_state'] == 'idle' and
                not replacement.record.get('setting_control_pending'))
        finally:
            await teardown(host, serving)
    asyncio.run(run())


@pytest.mark.parametrize('patch', [{'hooks': {'Stop': [{'command': 'unexpected'}]}},
    {'autoCompactEnabled': True}, {'idleCompaction': True}, {'autoMemoryEnabled': True},
    {'fastMode': True}, {'promptSuggestions': True}, {'progressSummaries': True},
    {'env': {'DISABLE_CRON': '0'}}, {'env': {'CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS': '0'}},
    # A release that stops reporting a signal refuses, never assumes the default.
    {'env': None}, {'env': {}}, {'env': {'DISABLE_CRON': '1'}},
    {'env': {'CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS': '1'}}, {'fastMode': None}])
def test_native_effective_envelope_override_never_collects(tmp_path, patch):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            session.update(native_effective_settings={**session.record['native_effective_settings'], **patch})
            clock.value += 10000
            assert not await collect_session(host, session)
            assert session.process.returncode is None
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_ten_concurrent_send_intents_restore_one_process_without_a_hidden_message_queue(tmp_path):
    async def run():
        host, serving, old, clock = await setup(tmp_path)
        try:
            await host.dispatch({'method': 'close_input', 'params': {'execution_id': old.sid, 'generation': 1}})
            await until(lambda: old.record['owner'] == 'gone')
            async def send_intent():
                row = await host.reconnect(execution_id=old.sid, generation=1)
                session = host.sessions[old.sid]
                await until(lambda: session.record['state'] == 'ready' and
                    not session.record.get('setting_control_pending'))
                return await session.send(str(uuid.uuid4()), 'hold', 'new deliberate Send')
            outcomes = await asyncio.gather(*(send_intent() for _ in range(10)), return_exceptions=True)
            assert sum(isinstance(value, dict) for value in outcomes) == 1
            assert sum(isinstance(value, RuntimeError) and 'busy' in str(value) for value in outcomes) == 9
            row = host.state(old.sid)
            assert row['generation'] == 2 and len(row['pending_message_uuids']) == 1
            assert host.store.db.execute("SELECT count(*) FROM events WHERE kind='process_started'").fetchone()[0] == 2
            stdin = host.store.path(old.sid, 2, 'stdin').read_text().splitlines()
            await until(lambda: host.store.get('receipts', old.sid, row['pending_message_uuids'][0])['written'])
            stdin = host.store.path(old.sid, 2, 'stdin').read_text().splitlines()
            assert sum(json.loads(line).get('type') == 'user' for line in stdin) == 1
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_work_arriving_after_close_authorization_revokes_eof_and_reconnect_reuses_owner(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            await host.dispatch({'method': 'close_input', 'params': {'execution_id': session.sid, 'generation': 1}})
            frame(session, {'type': 'system', 'subtype': 'session_state_changed', 'state': 'running'})
            work = asyncio.create_task(host.reconnect(execution_id=session.sid, generation=1))
            row = await asyncio.wait_for(work, 10)  # private acceptance observation budget
            assert row['generation'] == 1 and row['pid'] == session.process.pid
            assert session.process.returncode is None and row['state'] == 'ready'
            assert row['close_reason'] is None
            kinds = [event['kind'] for event in host.store.replay(sid=session.sid, limit=500)['events']]
            assert 'input_close_revoked' in kinds and 'stdin_closed' not in kinds
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_never_tasked_process_gets_a_baseline_before_aging_and_cannot_close_on_unknown(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            session.update(background_level_known=False)
            clock.value += 20000  # silence while unknown is NOT settled age
            assert not await collect_session(host, session)
            assert session.record['background_level_known'] is True
            assert session.settled_since == clock.value
            assert session.process.returncode is None
            assert not await collect_session(host, session)
            clock.value += 11
            assert await collect_session(host, session)
            await until(lambda: session.record['owner'] == 'gone')
        finally:
            await teardown(host, serving)
    asyncio.run(run())


def test_empty_conversation_genesis_is_never_a_missing_history_fallback(tmp_path):
    async def run():
        host, serving, session, clock = await setup(tmp_path)
        try:
            assert host.empty_conversation_proved(session.sid, session.record)
            frame(session, {'type': 'assistant', 'message': {'model': 'haiku', 'content': []}})
            assert not host.empty_conversation_proved(session.sid, session.record)
            session.record.pop('last_assistant_cursor', None)
            assert not host.empty_conversation_proved(session.sid, session.record)  # immutable log still witnesses work
        finally:
            await teardown(host, serving)
    asyncio.run(run())
