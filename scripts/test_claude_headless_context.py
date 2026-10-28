"""Native context fixtures; bounded waits here are not host execution deadlines."""
import asyncio
import json
from pathlib import Path
import sys
import uuid

import pytest

from claude_headless.context import normalize, validate_target, previous_call_report, latest_transcript_call
from claude_headless.host import Host
from claude_headless.client import Client, HostError
from test_claude_headless_host import auth, builder, teardown, until

BODY = {'totalTokens': 600000, 'maxTokens': 1000000, 'rawMaxTokens': 1000000,
        'model': 'claude-haiku-5-5', 'isAutoCompactEnabled': False, 'autocompactSource': 'auto',
        'apiUsage': {'input_tokens': 2, 'cache_creation_input_tokens': 3,
                     'cache_read_input_tokens': 599995, 'output_tokens': 20},
        'categories': [{'kind': 'deferred', 'tokens': 99999999}]}


def row(role='worker', model='claude-haiku-5-5', target=None):
    return {'execution_id': 'cc-'+str(uuid.uuid4()), 'generation': 1,
        'role': role, 'controls_effective': {'model': model}, 'context_target': target,
        'native_api_usage': dict(BODY['apiUsage']), 'native_api_usage_complete': True,
        'native_api_usage_generation': 1, 'native_api_usage_cursor': 'fixture-api-stop'}


def measured_row(role='worker', model='claude-haiku-5-5', target=None):
    record = row(role, model, target)
    record['last_completed_model_call'] = {'usage': dict(BODY['apiUsage']),
        'generation': 1, 'model': model, 'measured_at': '2026-10-14T19:11:00Z'}
    return record


@pytest.mark.parametrize('role,expected,crossed', [('Manager', .60, True), ('conductor', .60, True),
    ('worker', .65, False), ('helper', .65, False), ('base', .65, False)])
def test_shared_profile_role_targets_no_catalog_or_cumulative(role, expected, crossed):
    result = normalize(measured_row(role), BODY, {})
    assert result['ok'] and result['target']['fraction'] == expected
    assert result['target']['crossed'] is crossed
    assert result['occupancy_tokens'] == 600000  # ONE measured input, output deliberately excluded
    assert result['window_tokens'] == 1000000 and result['output_reserve_tokens'] == 0
    assert result['profile_fallback']  # Haiku/Sonnet honestly use the shared fallback


@pytest.mark.parametrize('target', [.4, 1, .01])
def test_explicit_launch_target_beats_both_roles(target):
    for role in ('Manager', 'worker'):
        result = normalize(measured_row(role, target=target), BODY, {})
        assert result['target']['fraction'] == target
        assert result['target']['source'] == 'launch context_target'


@pytest.mark.parametrize('target', [0, -1, 1.01, True, '0.4', float('nan'), float('inf')])
def test_invalid_launch_target_refuses(target):
    with pytest.raises(ValueError):
        validate_target(target)


def test_actual_opus_profile_and_absolute_target_normalization(monkeypatch):
    import oc_common
    body = {**BODY, 'model': 'claude-opus-5-5'}
    result = normalize(measured_row(model='claude-opus-5-5'), body, {})
    assert result['profile']['name'] == 'Claude Opus 5.5' and not result['profile_fallback']
    # Change authority in memory, not a second target table: native normalization follows it.
    monkeypatch.setattr(oc_common, 'context_profile', lambda _: {'name': 'authority', 'land_by_tokens': 321000})
    assert normalize(measured_row(), BODY, {})['target']['tokens'] == 321000


@pytest.mark.parametrize('patch', [{'totalTokens': None}, {'totalTokens': -1}, {'totalTokens': True},
    {'rawMaxTokens': None}, {'rawMaxTokens': 0}, {'maxTokens': 1000001},
    {'model': 'claude-opus-5-5'}, {'isAutoCompactEnabled': True}])
def test_native_diagnostic_or_settings_drift_never_hides_previous_input(patch):
    result = normalize(measured_row(), {**BODY, **patch}, {})
    assert result['ok'] and result['occupancy_tokens'] == 600000
    assert not result['fresh'] and 'previous model call' in result['label']


def test_native_raw_window_not_compact_buffer_or_profile_window():
    result = normalize(measured_row(), {**BODY, 'rawMaxTokens': 1100000, 'maxTokens': 1000000}, {})
    assert result['window_tokens'] == 1100000


def test_actual_native_fixture_single_context_count_not_turn_usage_sum():
    evidence = json.loads((Path(__file__).parent/'fixtures/claude_context_2_1_299.json').read_text())
    record = measured_row()
    record['last_completed_model_call']['usage'] = evidence['after_tool_turn']['apiUsage']
    before = normalize(record, evidence['before_first_turn'], {})
    after = normalize(record, evidence['after_tool_turn'], {})
    assert before['ok'] and before['occupancy_tokens'] == 19559
    assert after['occupancy_tokens'] == 19559
    assert after['window_tokens'] == evidence['native_model_usage_window_witness']['contextWindow']
    assert sum(evidence['turn_usage_not_occupancy'].values()) != after['occupancy_tokens']
    assert 'ONE model call' in after['count_basis']


@pytest.mark.parametrize('patch', [{'apiUsage': None}, {'apiUsage': {}}, {'totalTokens': 600100}])
def test_native_estimates_or_tail_are_never_used_as_measured_occupancy(patch):
    result = normalize(measured_row(), {**BODY, **patch}, {})
    assert result['ok'] and result['occupancy_tokens'] == 600000


@pytest.mark.parametrize('phase', ['provisional', 'multiple_iterations', 'window_override'])
def test_current_native_publication_does_not_invalidate_previous_call(phase):
    record, body = measured_row(), dict(BODY)
    if phase == 'provisional':
        record['native_api_usage_complete'] = False
    elif phase == 'multiple_iterations':
        record['native_api_usage']['iterations'] = [{'type':'message'}, {'type':'message'}]
    else:
        body['autocompactSource'] = 'env'
    result = normalize(record, body, {})
    assert result['ok'] and result['occupancy_tokens'] == 600000 and not result['fresh']
    if phase == 'window_override':
        assert result['window_tokens'] is None  # input still visible without a qualified window


async def setup_context(tmp_path, *, role='worker', target=None, body=None, permission=False):
    specs = []
    def build(run_dir, **kwargs):
        spec = builder(run_dir, **kwargs)
        spec['env']['FIXTURE_CONTEXT_BODY'] = json.dumps(BODY if body is None else body)
        if permission:
            spec['env']['FIXTURE_CONTEXT_PERMISSION'] = '1'
        specs.append(spec)
        return spec
    host = Host(tmp_path/'state', tmp_path/'host.sock', launch_builder=build, auth_check=auth)
    serving = asyncio.create_task(host.serve())
    await until(lambda: host.socket_path.exists())
    client = Client(host.socket_path)
    sid = str(uuid.uuid4())
    record = await client.acall('launch', native_uuid=sid, cwd=str(tmp_path), model='haiku',
        effort='low', system_prompt='fixture', role=role, context_target=target)
    session = host.sessions[sid]
    await until(lambda: session.record['state'] == 'ready' and not session.record['setting_control_pending'])
    mid = str(uuid.uuid4())
    await session.send(mid, 'warm', 'warm')
    await until(lambda: host.store.get('receipts', session.sid, mid)['completed'])
    return host, serving, client, session, specs


def test_context_binding_two_independent_sessions_and_no_arbitrary_args(tmp_path):
    async def scenario():
        host, serving, client, first, specs = await setup_context(tmp_path)
        try:
            token = specs[0]['env']['CC_CONTEXT_BINDING']
            second = await host.launch(native_uuid=str(uuid.uuid4()), cwd=str(tmp_path), model='haiku',
                effort='low', system_prompt='fixture', role='Manager', context_target=.4)
            await until(lambda: not second['setting_control_pending'])
            other = host.sessions[second['native_uuid']]
            warm = str(uuid.uuid4())
            await other.send(warm, 'warm', 'warm')
            await until(lambda: host.store.get('receipts', other.sid, warm)['completed'])
            own = await client.acall('context_status', execution_id=first.record['execution_id'], binding=token)
            assert own['ok'] and own['target']['fraction'] == .65
            with pytest.raises(HostError, match='binding invalid'):
                await client.acall('context_status', execution_id=other.record['execution_id'], binding=token)
            with pytest.raises(HostError, match='unexpected context'):
                await client.acall('context_status', execution_id=first.record['execution_id'], binding=token, model='opus')
            result = await client.context(other.record['execution_id'], 1)
            assert result['target']['fraction'] == .4 and result['execution_id'] != own['execution_id']
            raw = host.store.path(first.sid, 1, 'stdin').read_text()
            assert token not in raw and token not in json.dumps(first.record)
            controls = [json.loads(line) for line in raw.splitlines() if 'get_context_usage' in line]
            assert controls[0]['request'] == {'subtype': 'get_context_usage', 'detail': 'summary'}
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_resume_rotates_binding_preserves_target_and_refuses_old_generation(tmp_path):
    async def scenario():
        host, serving, client, session, specs = await setup_context(tmp_path, target=.43)
        try:
            old_token = specs[0]['env']['CC_CONTEXT_BINDING']
            identity = session.record['execution_id']
            assert (await client.context(identity, 1))['ok']
            await host.dispatch({'method': 'close_input', 'params': {
                'execution_id': session.record['execution_id'], 'generation': session.generation}})
            await asyncio.gather(*session.tasks)
            assert (await client.context(identity, 1))['occupancy_tokens'] == 600000
            await host.resume_snapshot(identity)
            current = host.sessions[session.sid]
            await until(lambda: not current.record['setting_control_pending'])
            assert current.generation == 2 and current.record['context_target'] == .43
            with pytest.raises(HostError, match='binding invalid'):
                await client.acall('context_status', execution_id=identity, binding=old_token)
            assert (await client.context(identity, 1))['state'] == 'stale'
            resumed = await client.acall('context_status', execution_id=identity, binding=specs[-1]['env']['CC_CONTEXT_BINDING'])
            assert not resumed['ok'] and resumed['occupancy_tokens'] is None
            warm = str(uuid.uuid4())
            await current.send(warm, 'warm', 'warm')
            await until(lambda: host.store.get('receipts', current.sid, warm)['completed'])
            result = await client.acall('context_status', execution_id=identity, binding=specs[-1]['env']['CC_CONTEXT_BINDING'])
            assert result['ok'] and result['generation'] == 2 and result['target']['fraction'] == .43
            assert result['provenance']['generation'] == 2
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_in_flight_semantic_change_or_aged_reply_keeps_as_of_number(tmp_path, monkeypatch):
    async def scenario():
        from claude_headless import context
        host, serving, client, session, _ = await setup_context(tmp_path)
        real = context.await_control
        async def changed(*args, **kwargs):
            reply = await real(*args, **kwargs)
            session.project({'type': 'stream_event', 'event': {'type': 'content_block_delta',
                'delta': {'type': 'text_delta', 'text': 'in-flight change'}}}, '{}', 'fixture-change')
            return reply
        try:
            monkeypatch.setattr(context, 'await_control', changed)
            result = await client.context(session.record['execution_id'], 1)
            assert result['state'] == 'measured' and result['occupancy_tokens'] == 600000
            warm = str(uuid.uuid4())
            await session.send(warm, 'warm again', 'warm again')
            await until(lambda: host.store.get('receipts', session.sid, warm)['completed'])
            async def aged(*args, **kwargs):
                reply = await real(*args, **kwargs)
                reply['response_at'] -= 31
                return reply
            monkeypatch.setattr(context, 'await_control', aged)
            result = await client.context(session.record['execution_id'], 1)
            assert result['state'] == 'measured' and not result['fresh']
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_missing_native_fields_no_fallback_and_dead_host_record_not_fresh(tmp_path):
    async def scenario():
        host, serving, client, session, _ = await setup_context(tmp_path, body={})
        try:
            result = await client.context(session.record['execution_id'], 1)
            assert result['state'] == 'unknown' and result['occupancy_tokens'] is None
            await host.dispatch({'method': 'close_input', 'params': {
                'execution_id': session.record['execution_id'], 'generation': session.generation}})
            await asyncio.gather(*session.tasks)
            assert session.sid not in host.sessions  # an ended session is released; its stored record answers
            result = await client.context(session.record['execution_id'], 1)
            assert result['state'] == 'unknown' and result['window_tokens'] is None
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_strict_launch_lists_context_beside_workflowy(tmp_path, monkeypatch):
    from claude_headless import launch
    monkeypatch.setattr(launch, 'BROKER_TOKEN_PATH', tmp_path/'none')
    spec = launch.build_launch(tmp_path, native_uuid=str(uuid.uuid4()), cwd=str(tmp_path),
        model='haiku', effort='low', system_prompt='fixture',
        workflowy_url='http://127.0.0.1:8766/mcp', environ={'WORKFLOWY_MCP_BEARER': 'test'})
    config = json.loads((tmp_path/'mcp.json').read_text())
    assert set(config['mcpServers']) == {'workflowy', 'context'}
    adapter = config['mcpServers']['context']
    assert adapter['type'] == 'stdio' and adapter['command'] == sys.executable
    assert adapter['args'] == [str(Path(__file__).parent/'claude_context_mcp.py')]
    assert adapter['env']['CC_CONTEXT_BINDING'] == '${CC_CONTEXT_BINDING}'
    assert adapter['timeout'] == 3600000 and '--strict-mcp-config' in spec['argv']


def test_real_stdio_mcp_callback_waits_while_host_drains_permission_and_control(tmp_path):
    async def scenario():
        host, serving, client, session, specs = await setup_context(tmp_path, permission=True)
        adapter = None
        try:
            # Simulate a busy native tool turn: context control is legal without command_lock.
            mid = str(uuid.uuid4())
            await session.send(mid, 'hold', 'hold')
            await until(lambda: host.store.get('receipts', session.sid, mid).get('started'))
            adapter = await asyncio.create_subprocess_exec(sys.executable,
                str(Path(__file__).parent/'claude_context_mcp.py'), env=specs[0]['env'],
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async def rpc(identity, method, params):
                adapter.stdin.write((json.dumps({'jsonrpc':'2.0', 'id':identity, 'method':method, 'params':params})+'\n').encode())
                await adapter.stdin.drain()
                return json.loads(await asyncio.wait_for(adapter.stdout.readline(), 5))
            hello = await rpc(1, 'initialize', {'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'fixture','version':'1'}})
            assert 'result' in hello
            listing = await rpc(2, 'tools/list', {})
            schema = listing['result']['tools'][0]['inputSchema']
            assert schema['properties'] == {} and schema['additionalProperties'] is False
            result = await rpc(3, 'tools/call', {'name':'context_status','arguments':{}})
            report = json.loads(result['result']['content'][0]['text'])
            assert report['ok'] and not report['fresh'] and report['execution_id'] == session.record['execution_id']
            assert host.store.permission(session.sid, 1, 'context-drain-permission')['behavior'] == 'allow'
            assert session.pending == {mid}  # callback neither finishes nor interrupts the turn
            before = host.store.path(session.sid, 1, 'stdin').read_bytes()
            bad = await rpc(4, 'tools/call', {'name':'context_status','arguments':{'session':'other'}})
            assert 'error' in bad or bad.get('result',{}).get('isError')
            assert host.store.path(session.sid, 1, 'stdin').read_bytes() == before
            await client.acall('interrupt', execution_id=session.record['execution_id'], generation=1,
                authority='explicit inert fixture cleanup', cancel_queued=True)
            await until(lambda: not session.pending)
        finally:
            if adapter:
                adapter.stdin.close()
                await asyncio.wait_for(adapter.wait(), 5)
            await teardown(host, serving)
    asyncio.run(scenario())


def test_measured_previous_call_live_order_of_magnitude_timestamp_age_and_no_output():
    usage = {'input_tokens': 2, 'cache_read_input_tokens': 280292,
             'cache_creation_input_tokens': 2318, 'output_tokens': 99999}
    result = previous_call_report(measured_row('Manager'), usage, '2026-10-14T19:11:00Z',
        1000000, now=1792005120)
    assert result['occupancy_tokens'] == 282612
    assert result['fraction_used'] == .282612
    assert result['target']['tokens'] == 600000
    assert result['age_seconds'] == 60
    assert result['label'] == 'measured: as of the previous model call (2026-10-14T19:11:00Z, 60s old)'
    # Dan's ruling D2 (overnight build plan [ap:0OP1FK]): seats land on this same reading.
    assert result['unattended_roles_enabled'] is True and not result['fresh']


def test_as_of_age_never_expires_the_number_and_unknown_is_only_absent_call():
    record = measured_row()
    result = previous_call_report(record, BODY['apiUsage'], 1, 1000000, now=10**9)
    assert result['occupancy_tokens'] == 600000 and result['age_seconds'] == 10**9-1
    assert previous_call_report(record, None, 1)['reason'] == 'no completed model call yet in this generation'
    assert normalize({**record, 'generation': 2}, BODY, {})['occupancy_tokens'] is None


def test_transcript_reverse_read_ignores_partial_foreign_sidechain_and_old_generation(tmp_path):
    identity = str(uuid.uuid4())
    def event(stamp='2026-10-14T19:11:00Z', **patch):
        return {'type':'assistant', 'sessionId':identity, 'timestamp':stamp,
            'message':{'id':'message', 'model':'fixture-model', 'stop_reason':'tool_use', 'usage':BODY['apiUsage']}, **patch}
    path = tmp_path/'transcript.jsonl'
    rows = [event(), {'type':'user','pad':'x'*200000}, event(sessionId=str(uuid.uuid4())),
            event(isSidechain=True), event(message={'usage':BODY['apiUsage'], 'stop_reason':None})]
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows)+json.dumps(event('2026-10-14T19:12:00Z')))
    call = latest_transcript_call(path, identity)
    assert call['usage'] == BODY['apiUsage'] and call['measured_at'] == '2026-10-14T19:11:00Z'
    assert latest_transcript_call(path, identity, '2026-10-14T19:11:01Z') is None
    assert latest_transcript_call(path, identity, max_bytes=65536) is None
    assert latest_transcript_call(tmp_path/'missing', identity) is None
    path.write_text(json.dumps(event()))
    assert latest_transcript_call(path, identity) is None
    path.write_text(json.dumps(event())+'\n')
    assert latest_transcript_call(path, identity)['usage'] == BODY['apiUsage']


def test_host_snapshot_survives_new_call_and_uses_start_delta_merged_usage(tmp_path):
    async def scenario():
        host, serving, client, session, _ = await setup_context(tmp_path)
        try:
            identity = session.record['execution_id']
            prior = await client.context(identity, 1)
            session.project({'type':'stream_event','event':{'type':'message_start','message':{
                'id':'next','model':'claude-haiku-5-5','usage':{'input_tokens':2,
                    'cache_read_input_tokens':280292,'cache_creation_input_tokens':2318}}}}, '{}', 'start')
            assert (await client.context(identity, 1))['occupancy_tokens'] == prior['occupancy_tokens']
            session.project({'type':'stream_event','parent_tool_use_id':'child', 'event':{
                'type':'message_start', 'message':{'usage':{'input_tokens':99}}}}, '{}','child')
            session.project({'type':'stream_event','event':{'type':'message_delta',
                'usage':{'output_tokens':9999}}}, '{}','delta')
            assert (await client.context(identity, 1))['occupancy_tokens'] == 600000
            session.project({'type':'stream_event','event':{'type':'message_stop'}}, '{}','stop')
            final = await client.context(identity, 1)
            assert final['occupancy_tokens'] == 282612
            assert final['provenance']['assistant_usage_cursor'] == 'stop'
            session.update(setting_control_pending=True, settings_mismatch=True)
            assert (await client.context(identity, 1))['occupancy_tokens'] == 282612
            session.update(setting_control_pending=False, settings_mismatch=False)
        finally:
            await teardown(host, serving)
    asyncio.run(scenario())


def test_transcript_reader_uses_callers_contained_open_handle_not_a_second_path_open():
    import io
    identity = str(uuid.uuid4())
    event = {'type': 'assistant', 'sessionId': identity, 'timestamp': '2026-10-14T19:11:00Z',
        'message': {'stop_reason': 'end_turn', 'usage': BODY['apiUsage']}}
    opened = []
    def contained_open():
        opened.append(True)
        return io.BytesIO((json.dumps(event)+'\n').encode())
    call = latest_transcript_call('/path/that/must/not/be/opened', identity, opener=contained_open)
    assert call['usage'] == BODY['apiUsage'] and opened == [True]


def test_round5_subagent_transcript_reads_only_that_agents_own_sidechain_calls(tmp_path):
    parent = '50a5f467-19b1-4971-9b0a-afa8ac8819e4'
    def row(agent, sidechain, tokens, at):
        return {'type': 'assistant', 'sessionId': parent, 'isSidechain': sidechain, 'agentId': agent, 'timestamp': at,
                'message': {'model': 'm', 'id': 'x', 'stop_reason': 'tool_use',
                            'usage': {'input_tokens': tokens, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}}
    path = tmp_path / 'agent-aa.jsonl'
    path.write_text(''.join(json.dumps(r) + '\n' for r in [
        row('aa', True, 111, '2026-10-15T07:00:00Z'), row('bb', True, 222, '2026-10-15T07:01:00Z'),
        row(None, False, 333, '2026-10-15T07:02:00Z')]))
    assert latest_transcript_call(path, parent, agent_id='aa')['usage']['input_tokens'] == 111
    assert latest_transcript_call(path, parent)['usage']['input_tokens'] == 333  # default: main records only
    assert latest_transcript_call(path, parent, agent_id='cc') is None
