"""Native manager-quick policy and child-frame isolation; no native inference."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest
from claude_headless import manager, quick_agent
from test_claude_headless_launch import build, pair
from test_claude_headless_supervision import setup, cleanup, frame, held


def test_one_source_verbatim_and_inherited_tools_model():
    definitions, evidence = quick_agent.manager_quick_definition()
    definition = definitions['manager-quick']
    raw = quick_agent.SOURCE.read_bytes()
    assert definition['prompt'] == quick_agent.PREAMBLE + raw.decode('utf-8')
    assert definition['model'] == 'inherit'
    assert 'tools' not in definition  # inherited MCP, not a built-ins-only allow-list
    assert definition['disallowedTools'] == ['Agent', 'SendMessage']
    assert evidence['source_sha256'] == hashlib.sha256(raw).hexdigest()
    for text in ('## Result', 'absolute path', '--helper', 'cannot start subagents',
                 'do NOT call it', 'never pass', 'No OpenCode'):
        assert text.lower() in definition['prompt'].lower(), text


def test_generated_each_launch_and_resume_local_snapshot(tmp_path, monkeypatch):
    source = tmp_path / 'AGENTS.md'
    source.write_text('first doctrine\n')
    monkeypatch.setattr(quick_agent, 'SOURCE', source)
    old, new = tmp_path / 'old', tmp_path / 'new'
    old.mkdir(); new.mkdir()
    first = build(old, role='Manager')
    assert json.loads(Path(pair(first['argv'], '--agents')).read_text())['manager-quick']['prompt'].endswith('first doctrine\n')
    assert pair(first['argv'], '--agents') == str(old / 'agents.json')
    assert first['safe']['argv'] == first['argv']
    assert '--forward-subagent-text' in first['argv']
    assert first['safe']['agents_sha256'] == hashlib.sha256((old/'agents.json').read_bytes()).hexdigest()
    assert 'first doctrine' not in json.dumps(first['safe'])
    if os.name == 'posix':
        assert (old/'agents.json').stat().st_mode & 0o777 == 0o600
    (old/'launch.json').write_text(json.dumps(first['safe']))
    source.write_text('new doctrine\n')
    second = build(new, role='Manager', resume=True, snapshot_source=str(old))
    assert json.loads(Path(pair(second['argv'], '--agents')).read_text())['manager-quick']['prompt'].endswith('new doctrine\n')
    for name in ('prompt.md', 'mcp.json', 'settings.json'):
        assert (new/name).read_bytes() == (old/name).read_bytes()
    assert second['safe']['agents_sha256'] != first['safe']['agents_sha256']
    assert '--agents' not in build(tmp_path, role='worker')['argv']


def test_inherited_child_model_force_is_removed(tmp_path):
    result = build(tmp_path, role='Manager', environ={
        'WORKFLOWY_MCP_BEARER':'inert', 'CLAUDE_CODE_SUBAGENT_MODEL':'sonnet',
        'CLAUDE_CODE_SUBAGENT_MODEL_FORCE':'opus'})
    for name in ('CLAUDE_CODE_SUBAGENT_MODEL', 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE'):
        assert name not in result['env']
        assert name in result['safe']['removed_env_names']


def test_missing_or_empty_doctrine_refuses_restart_preflight(tmp_path, monkeypatch):
    source = tmp_path/'AGENTS.md'
    monkeypatch.setattr(quick_agent, 'SOURCE', source)
    with pytest.raises(FileNotFoundError):
        manager.compose_manager_prompt()
    source.write_text(' \n')
    with pytest.raises(ValueError, match='doctrine is empty'):
        manager.compose_manager_prompt()


@pytest.mark.parametrize('scope', [{'parent_tool_use_id':'agent-call'}, {'isSidechain':True}])
def test_child_frames_do_not_become_parent_receipt_model_or_context(tmp_path, scope):
    async def scenario():
        host, session, _ = await setup(tmp_path)
        try:
            mid = await held(session)
            previous = dict(session.record)
            revision = session.context_revision
            frame(session, {'type':'assistant', **scope, 'uuid':'child-message', 'message':{
                'model':'claude-sonnet-5-5', 'stop_reason':'end_turn',
                'usage':{'input_tokens':99999}, 'content':[{'type':'text','text':'child only'}]}})
            frame(session, {'type':'result', **scope, 'uuid':'child-result', 'is_error':True,
                'errors':['429 fixture'], 'user_message_uuid':mid,
                'modelUsage':{'claude-sonnet-5-5':{'contextWindow':999}}})
            frame(session, {'type':'user', **scope, 'uuid':mid, 'isReplay':True,
                'message':{'content':'incorrect child echo'}})
            assert session.context_revision == revision
            assert mid in session.pending
            assert not host.store.get('receipts',session.sid,mid).get('echo_mismatch')
            for key in ('last_completed_model_call', 'last_assistant_model', 'context_windows',
                        'settings_mismatch', 'pending_recovery', 'pending_escalations'):
                assert session.record.get(key) == previous.get(key), key
            assert not host.store.records('recoveries')
            assert session.record['last_subagent_cursor']
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_child_permission_still_allowed_and_raw_retained(tmp_path):
    async def scenario():
        host, session, _ = await setup(tmp_path)
        try:
            frame(session, {'type':'control_request','parent_tool_use_id':'agent-call',
                'request_id':'child-permission', 'request':{'subtype':'can_use_tool',
                'tool_name':'mcp__workflowy__node', 'input':{'verb':'read','params':{'node_handle':'[ap:JOB]'}}}})
            receipt = host.store.permission(session.sid,session.generation,'child-permission')
            assert receipt['behavior'] == 'allow'
            assert 'child-permission' in host.store.path(session.sid,1,'stdout').read_text()
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_child_tool_id_never_contaminates_parent_snapshot(tmp_path):
    async def scenario():
        host, session, _ = await setup(tmp_path)
        try:
            for scope, value in [({},'parent'), ({'parent_tool_use_id':'agent-call'},'child')]:
                frame(session, {'type':'assistant',**scope,'uuid':value, 'message':{
                    'content':[{'type':'tool_use','id':'same-tool','name':'Read','input':{'file_path':value}}]}})
                frame(session, {'type':'user',**scope,'uuid':value+'-result', 'message':{
                    'content':[{'type':'tool_result','tool_use_id':'same-tool','content':value}]}})
            snapshot=host.tool_snapshot(session.record['execution_id'],'same-tool',1)
            assert json.loads(snapshot['input']['input_raw'])['file_path']=='parent'
            assert len(snapshot['outputs'])==1
            assert json.loads(snapshot['outputs'][0]['block_raw'])['content']=='parent'
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_agent_background_allows_send_but_blocks_destructive_close(tmp_path):
    async def scenario():
        host, session, _ = await setup(tmp_path)
        try:
            mid = await held(session)
            frame(session, {'type':'system','subtype':'task_started', 'task_id':'agent-id', 'run_id':'agent-run',
                'task_type':'local_agent','is_backgrounded':True, 'tool_use_id':'agent-call',
                'description':'Quick socket test'})
            frame(session, {'type':'result','uuid':'main-result','subtype':'success',
                'is_error':False,'user_message_uuid':mid})
            frame(session, {'type':'system','subtype':'task_progress','task_id':'agent-id', 'run_id':'agent-run',
                'last_tool_name':'Read', 'description':'Reading socket'})
            assert session.record['background_tasks']['agent-id']['last_tool_name']=='Read'
            with pytest.raises(RuntimeError, match='background tasks'):
                await host.dispatch({'method':'close_input','params':{
                    'execution_id':session.record['execution_id'],'generation':1}})
            sent = await session.send(str(uuid.uuid4()),'next message','next message')
            assert sent['durable'] and session.record['background_unsettled']
            frame(session, {'type':'system','subtype':'task_notification','task_id':'agent-id','run_id':'agent-run','status':'completed'})
            assert not session.record['background_unsettled']
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_running_background_agent_never_blocks_send_only_the_own_turn_does(tmp_path):
    """Observed 2.1.299 order: native session_state stays running after the primary
    result while a background agent works; only primary init..result is the own turn."""
    from claude_headless.host import own_turn_busy
    async def scenario():
        host, session, _ = await setup(tmp_path)
        try:
            state = lambda value: frame(session, {'type':'system','subtype':'session_state_changed','state':value})
            mid = await held(session)
            session.update(native_state_required=True)
            frame(session, {'type':'system','subtype':'init'})
            state('running')
            frame(session, {'type':'system','subtype':'task_started','task_id':'agent-id','run_id':'agent-run',
                'task_type':'local_agent','is_backgrounded':True,'tool_use_id':'agent-call','description':'Bake-off'})
            frame(session, {'type':'result','uuid':'main-result','subtype':'success','is_error':False,'user_message_uuid':mid})
            # Child permission prompts flip native state, still not the Manager's turn.
            state('requires_action')
            frame(session, {'type':'control_request','request_id':'child-perm','request':{'subtype':'can_use_tool',
                'tool_name':'Bash','input':{'command':'true'},'tool_use_id':'child-bash','agent_id':'agent-id'}})
            assert session.record['pending_native_requests']['child-perm']['agent_id'] == 'agent-id'
            state('running')
            assert session.record['primary_turn_open'] is False and own_turn_busy(session.record) is None
            sent = await session.send(str(uuid.uuid4()),'while the child runs','while the child runs')
            assert sent['durable'] and session.record['background_unsettled']
            frame(session, {'type':'result','uuid':'second','subtype':'success','is_error':False,'user_message_uuid':sent['message_uuid']})
            # The child's completion wakes the Manager: an autonomous own turn blocks again.
            frame(session, {'type':'system','subtype':'init'})
            with pytest.raises(RuntimeError, match='working on its own turn'):
                await session.send(str(uuid.uuid4()),'during own turn','during own turn')
            frame(session, {'type':'result','uuid':'third','subtype':'success','is_error':False,
                'origin':{'kind':'task-notification'}})
            assert own_turn_busy(session.record) is None
            state('unknown')
            assert 'not known' in own_turn_busy(session.record)
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_pinned_cli_session_state_probe_second_send_while_child_holds(tmp_path):
    """Isolated loopback probe of the pinned CLI with production's session-state events:
    native state stayed 'running' after the primary result while the child held, the
    old gate refused there, the own-turn gate does not; and the effort variant reached
    the child's requests (output_config.effort max vs the parent's low)."""
    from claude_headless.host import own_turn_busy
    fixture = json.loads((Path(__file__).parent/'fixtures/claude-agent-session-state-2.1.299.json').read_text())
    split = fixture['second_send_before_index']
    async def scenario():
        host, session, _ = await setup(tmp_path)
        try:
            # The probe ran a synthetic provider; align its model echo with this host's
            # applied setting so the model witness stays out of the question.
            model = (session.record.get('controls_effective') or {}).get('model')
            for event in fixture['events']:
                if event.get('subtype') == 'init':
                    event['model'] = model
                if event.get('type') == 'assistant' and not event.get('parent_tool_use_id'):
                    event['message']['model'] = model
            for event in fixture['events'][:split]:
                frame(session, event)
            assert session.record['native_session_state'] == 'running'
            assert session.record['primary_turn_open'] is False and session.record['background_unsettled']
            assert own_turn_busy(session.record) is None
            sent = await session.send(str(uuid.uuid4()), 'second while child holds', 'second while child holds')
            assert sent['durable']
            for event in fixture['events'][split:]:
                frame(session, event)
            assert session.record['native_session_state'] == 'idle' and not session.record['background_unsettled']
        finally:
            await cleanup(host)
    asyncio.run(scenario())
    assert 'output_config.effort max' in fixture['provenance']['note']


def test_effort_variants_share_the_one_doctrine():
    definitions, evidence = quick_agent.manager_quick_definition()
    assert set(definitions) == {'manager-quick', 'manager-quick-medium', 'manager-quick-high',
                                'manager-quick-xhigh', 'manager-quick-max'}
    assert 'effort' not in definitions['manager-quick']
    for level in ('medium', 'high', 'xhigh', 'max'):
        variant = definitions['manager-quick-' + level]
        assert variant['effort'] == level and variant['prompt'] == definitions['manager-quick']['prompt']
        assert variant['model'] == 'inherit' and variant['disallowedTools'] == ['Agent', 'SendMessage'] and 'tools' not in variant
        assert level in variant['description']
    assert evidence['effort_variants'][-1] == 'manager-quick-max'
    assert 'manager-quick-xhigh' in manager.compose_manager_prompt()['text']


def test_conservative_gate_before_any_turn_closed():
    from claude_headless.host import own_turn_busy
    assert own_turn_busy({'native_session_state':'running'}) == 'the Manager is working on its own turn'
    assert own_turn_busy({'native_session_state':'requires_action','primary_turn_open':True})
    assert own_turn_busy({'native_session_state':'idle','native_state_required':True}) is None


def test_observed_pinned_cli_agent_lifecycle_fixture(tmp_path):
    fixture = json.loads((Path(__file__).parent/'fixtures/claude-agent-2.1.299.json').read_text())
    proof = fixture['provenance']
    assert proof['exit'] == 0 and not proof['forced']
    assert proof['second_parent_send_seconds'] < proof['second_parent_result_seconds'] < proof['child_notification_seconds']
    async def scenario():
        host, session, _ = await setup(tmp_path)
        try:
            previous_model = session.record.get('last_assistant_model')
            for event in fixture['events'][:5]:
                frame(session,event)
            task = session.record['background_tasks']['ab4e17c94c8afd826']
            assert task['last_tool_name'] == 'mcp__research__ping'
            assert session.record.get('last_assistant_model') == previous_model
            assert not session.record.get('settings_mismatch')
            assert not host.tool_snapshot(session.record['execution_id'],'toolu_inert_child_ping',1)['available']
            for event in fixture['events'][5:]:
                frame(session,event)
            assert not session.record['background_unsettled']
        finally:
            await cleanup(host)
    asyncio.run(scenario())
