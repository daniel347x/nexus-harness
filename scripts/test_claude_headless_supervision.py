"""Inert error envelopes, real local pipes/locks; fixture budgets are not product policy."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest
from claude_headless.host import Host
from claude_headless.supervision import classify_result, retry_due, DEFAULT_RETRY_DELAY
from test_claude_headless_host import auth, builder, until, teardown


@pytest.mark.parametrize('event,category,handler', [
    ({'subtype':'success','is_error':False,'permission_denials':[{'tool':'Write'}]}, 'turn_receipt','retain'),
    ({'subtype':'success','is_error':True,'result':'Not logged in · Please run /login'}, 'auth','alert_no_retry'),
    ({'is_error':True,'result':'API Error: Request rejected (429) · INERT MOCK 429'}, 'capacity_quota','wait_continue_once'),
    ({'is_error':True,'result':'API Error: 529 INERT MOCK 529'}, 'capacity_quota','wait_continue_once'),
    ({'is_error':True,'result':"You've hit your session limit · resets 8am"}, 'capacity_quota','wait_continue_once'),
    ({'is_error':True,'result':'Usage credits required for 1M context'}, 'financial_entitlement','alert_no_retry'),
    ({'is_error':True,'result':'Credit balance too low'}, 'financial_entitlement','alert_no_retry'),
    ({'is_error':True,'result':'API Error: 500 INERT MOCK 500'}, 'native_api','escalate'),
    ({'is_error':True,'result':'Request timed out'}, 'native_api','escalate'),
    ({'is_error':True,'result':'API Error: The response stream was malformed. The response above may be incomplete.'}, 'native_api','escalate'),
    ({'subtype':'error_max_turns','errors':['Reached maximum number of turns (1)']}, 'turn_limit','continuation_needed'),
    ({'subtype':'error_max_budget_usd','errors':['budget exceeded']}, 'budget','configuration_needed'),
    ({'is_error':True,'result':'Prompt is too long'}, 'context','continuation_needed'),
    ({'is_error':True,'result':"error: unknown option '--not-a-valid-headless-flag'"}, 'invalid_config','configuration_needed'),
    ({'is_error':True,'result':'fixture unknown failure'}, 'unknown','escalate'),
])
def test_observed_registry_arms(event, category, handler):
    value = classify_result(event)
    assert value['category'] == category and value['handler'] == handler
    assert value['task_acceptance'] == 'unjudged'


def test_retry_metadata_and_conservative_delay():
    assert retry_due({}, 100) == (100+DEFAULT_RETRY_DELAY, 'conservative_one_hour')
    assert retry_due({'retry_after_ms':2000}, 100) == (102,'retry_after_ms')
    assert retry_due({'rate_limit_info':{'resetsAt':200}}, 100) == (200,'resetsAt')
    assert retry_due({'reset_at':'2026-10-14T00:00:00Z'}, 1)[0] > 1
    assert retry_due({'retry_after':-5},100) == (3700,'conservative_one_hour')
    assert retry_due({'retry_after_ms': float('nan')},100) == (3700,'conservative_one_hour')


async def setup(tmp_path, role='conductor', parent=None, launch_builder=builder):
    clock = [1000.0]
    host = Host(tmp_path/'state', tmp_path/'host.sock', launch_builder, auth, clock=lambda:clock[0])
    row = await host.launch(native_uuid=str(uuid.uuid4()), cwd=str(tmp_path), model='haiku', effort='low',
                            system_prompt='fixture', role=role, parent=parent, owning_conductor='[ap:CONDUCTOR]')
    session = host.sessions[row['native_uuid']]
    await until(lambda: session.record.get('initialized') and not session.record.get('setting_control_pending'))
    return host, session, clock


async def cleanup(host):
    dummy = asyncio.create_task(asyncio.sleep(999))
    for task in host.supervisor.actions.values():
        await asyncio.wait_for(task, 5)
    await teardown(host, dummy)


def frame(session, event):
    data = (json.dumps(event)+'\n').encode()
    offset = session.store.append_bytes(session.sid, session.generation, 'stdout', data)
    session.frame(data, offset, len(data))


async def held(session):
    mid = str(uuid.uuid4())
    await session.send(mid, 'hold', 'ORIGINAL PRIVATE INPUT')
    await until(lambda: session.store.get('receipts', session.sid, mid)['acknowledged'])
    return mid


def test_capacity_only_after_terminal_and_one_journaled_continue(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path)
        try:
            mid = await held(session)
            frame(session, {'type':'system','subtype':'api_retry','attempt':40,'max_retries':0,
                            'retry_delay_ms':2000,'error_status':429,'error':'fixture'})
            before = session.store.path(session.sid,1,'stdin').read_bytes()
            clock[0] += 86400
            await host.supervisor.tick()
            assert session.store.path(session.sid,1,'stdin').read_bytes() == before
            assert not host.store.records('recoveries')
            event = {'type':'result','uuid':'capacity-result','subtype':'success','is_error':True,
                'result':'API Error: 529 INERT MOCK','user_message_uuid':mid,'retry_after_seconds':2,'result_index':0}
            frame(session,event)
            action = host.store.records('recoveries')[0]
            assert action['status']=='scheduled' and action['due_at']==clock[0]+2
            frame(session,event)
            assert len(host.store.records('recoveries'))==1
            await host.supervisor.tick()
            assert session.store.path(session.sid,1,'stdin').read_bytes()==before
            # A later native retry blocks even a due terminal-error recovery.
            frame(session, {'type':'system','subtype':'api_retry','error_status':529})
            clock[0] += 3
            await host.supervisor.tick()
            assert session.store.path(session.sid,1,'stdin').read_bytes()==before
            session.update(native_retry_active=False)  # explicit native completion evidence fixture
            await host.supervisor.tick()
            await until(lambda: host.store.get('recoveries',session.sid,action['recovery_id'])['status']=='completed')
            for _ in range(3):
                await host.supervisor.tick()
            lines = [json.loads(x) for x in session.store.path(session.sid,1,'stdin').read_bytes().splitlines()]
            users = [x for x in lines if x['type']=='user']
            assert len(users)==2 and users[1]['uuid']==action['action_uuid']
            assert users[1]['message']['content'].startswith('continue\n')
            assert 'ORIGINAL PRIVATE INPUT' not in users[1]['message']['content']
            receipt = host.store.get('receipts',session.sid,action['action_uuid'])
            assert receipt['recovery_id']==action['recovery_id'] and receipt['results'][0]['task_acceptance']=='unjudged'
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_a_manager_is_never_continued_automatically(tmp_path):
    # Dan's ruling (Manager-Session-208): the terminal capacity error that schedules a `continue` for a Conductor
    # schedules nothing for a Manager (a withheld escalation for Dan instead), and a recovery scheduled for a Manager
    # before the ruling is cancelled, never sent.
    async def scenario():
        host, session, clock = await setup(tmp_path, role='Manager')
        try:
            mid = await held(session)
            before = session.store.path(session.sid,1,'stdin').read_bytes()
            frame(session, {'type':'result','uuid':'capacity-result','subtype':'success','is_error':True,
                'result':'API Error: 529 INERT MOCK','user_message_uuid':mid,'retry_after_seconds':2,'result_index':0})
            assert not host.store.records('recoveries')
            withheld = [i for i in host.store.records('incidents', session.sid)
                        if i.get('category') == 'recovery_withheld']
            assert withheld and 'never continued automatically' in json.dumps(withheld[-1])
            # A recovery scheduled before the ruling (old code), due hours ahead at a reset, is cancelled at once
            # (not when it falls due), never sent.
            rid = 'pre-ruling'
            host.store.put('recoveries', session.sid, {'recovery_id': rid, 'execution_id': 'cc-' + session.sid,
                'generation': 1, 'reason': 'terminal_error', 'source_message_uuids': [mid], 'due_at': clock[0] + 9000,
                'action_uuid': str(uuid.uuid4()), 'submitted': 'continue', 'status': 'scheduled'}, rid)
            session.update(pending_recovery=rid)
            clock[0] += 5
            for _ in range(3):
                await host.supervisor.tick()
            assert host.store.get('recoveries', session.sid, rid)['status'] == 'cancelled'
            assert session.record.get('pending_recovery') is None
            assert session.store.path(session.sid,1,'stdin').read_bytes() == before
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_long_silence_has_zero_input_signal_close_or_relaunch(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path)
        try:
            await held(session)
            before = session.store.path(session.sid,1,'stdin').read_bytes()
            witness = session.record['process_witness']
            session.process.kill = lambda: pytest.fail('silence killed')
            session.process.terminate = lambda: pytest.fail('silence terminated')
            session.process.send_signal = lambda *_: pytest.fail('silence signalled')
            close = session.process.stdin.close
            session.process.stdin.close = lambda: pytest.fail('silence closed stdin')
            clock[0] += 7*86400
            for _ in range(5):
                await host.supervisor.tick()
            assert session.store.path(session.sid,1,'stdin').read_bytes()==before
            assert session.process.returncode is None and session.record['generation']==1
            assert session.record['process_witness']==witness and len(host.sessions)==1
            assert not host.store.records('recoveries')
            feed = host.store.incidents(limit=500)['incidents']
            silence = [x for x in feed if x['category']=='long_silence']
            assert len(silence)==1 and silence[0]['detail']['execution_action']=='none'
            host.supervisor.activity(session)
            assert any(x['category']=='silence_recovered' for x in host.store.incidents(limit=500)['incidents'])
            session.process.stdin.close = close
        finally:
            await cleanup(host)
    asyncio.run(scenario())


@pytest.mark.parametrize('role,target', [('Manager','Dan'),('worker','owning_conductor')])
def test_unknown_escalation_and_operator_disposition(tmp_path,role,target):
    async def scenario():
        host, session, clock = await setup(tmp_path,role=role,parent='ses_owning_parent')
        try:
            mid = await held(session)
            frame(session, {'type':'result','uuid':'unknown','subtype':'success','is_error':True,
                'result':'Unknown fixture error','user_message_uuid':mid})
            incident = next(x for x in host.store.incidents(limit=500)['incidents'] if x['category']=='unknown')
            assert incident['escalation']['target']==target
            assert incident['escalation']['owning_conductor']=='[ap:CONDUCTOR]'
            assert incident['escalation']['status']=='pending'
            assert not host.store.records('recoveries')
            # A Manager's escalation is addressed to Dan, a seat's to its owning Conductor: either way the
            # sender is the addressee, so the Send proceeds and the receipt records which escalations were open
            # (finding #8; the Manager since the guard sweep). The escalation stays listed until resolved.
            receipt = await session.send(str(uuid.uuid4()), 'x','x')
            assert receipt['escalations_open_at_send'] == [incident['incident_id']]
            assert session.record['pending_escalations'] == [incident['incident_id']]
            if role == 'Manager':
                return
            host.supervisor.resolve_incident(session.sid, incident['incident_id'], 'Operator inspected fixture; next turn authorized')
            assert not session.record['pending_escalations']
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_denial_success_and_expected_negative_tool_error_are_not_task_verdicts(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path)
        try:
            mid=await held(session)
            frame(session, {'type':'system','subtype':'permission_denied','tool_name':'Write'})
            frame(session, {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'expected-negative',
                'is_error':True,'content':'Exit code 7'}]}})
            frame(session, {'type':'result','uuid':'success-denial','subtype':'success','is_error':False,
                'permission_denials':[{'tool_name':'Write'}],'user_message_uuid':mid,'terminal_reason':'completed'})
            receipt=host.store.get('receipts',session.sid,mid)
            assert receipt['completed'] and receipt['denial_cursors'] and receipt['tool_errors']
            assert receipt['results'][0]['category']=='turn_receipt' and receipt['results'][0]['task_acceptance']=='unjudged'
            assert not session.record.get('pending_escalations') and not host.store.records('recoveries')
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_background_result_settles_only_named_input_not_background_tasks(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path)
        try:
            mid=await held(session)
            frame(session,{'type':'system','subtype':'task_started','task_id':'background','run_id':'run-one','is_backgrounded':True})
            frame(session,{'type':'system','subtype':'api_retry','error_status':429})
            frame(session,{'type':'result','uuid':'synthetic','subtype':'success','is_error':False,
                'origin':{'kind':'task-notification'},'user_message_uuid':mid,'result_index':1})
            assert mid not in session.pending and host.store.get('receipts',session.sid,mid)['completed']
            assert session.record['background_unsettled']  # receipt completion never settles the task registry
            assert not session.record['native_retry_active']  # any successful native result clears stale retry
            frame(session,{'type':'result','uuid':'main-result','subtype':'success','is_error':False,'user_message_uuid':mid})
            frame(session,{'type':'system','subtype':'task_notification','task_id':'background','run_id':'run-one','status':'completed'})
            assert not session.record['background_unsettled']
            # Result copies are rows of their own, written once, never part of the session record.
            assert 'synthetic_results' not in session.record and 'result_identities' not in session.record
            assert host.store.db.execute('SELECT COUNT(*) FROM results WHERE sid=? AND background=1',
                                         (session.sid,)).fetchone()[0]==1
            state=await host.dispatch({'method':'state','params':{'execution_id':session.record['execution_id']}})
            assert state['last_synthetic_result']['result_index']==1 and state['last_synthetic_result']['cursor']
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_exit0_crash_resume_same_uuid_preserves_uncertain_effects_without_original_replay(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path)
        try:
            mid=str(uuid.uuid4())
            await session.send(mid,'crash','EXACT ORIGINAL - NEVER REPLAY')
            await until(lambda: session.record.get('owner')=='gone')
            original=host.store.get('receipts',session.sid,mid)
            assert original['status']=='uncertain' and not original['completed']
            action=host.store.records('recoveries')[0]
            await host.supervisor.tick()
            await until(lambda: host.store.get('recoveries',session.sid,action['recovery_id'])['status']=='completed')
            resumed=host.sessions[session.sid]
            assert resumed.generation==2 and resumed.sid==session.sid
            preserved=host.store.get('receipts',session.sid,mid)
            assert preserved['status']=='uncertain' and preserved['effects']=='uncertain'
            assert preserved['superseded_by_recovery']==action['action_uuid']
            stdin=[json.loads(x) for x in host.store.path(session.sid,2,'stdin').read_bytes().splitlines()]
            assert [x['uuid'] for x in stdin if x['type']=='user']==[action['action_uuid']]
            assert all(x.get('message',{}).get('content')!='crash' for x in stdin)
            assert (Path(session.record['run_dir'])/'prompt.md').read_bytes()==(Path(resumed.record['run_dir'])/'prompt.md').read_bytes()
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_auth_error_never_respawns_or_automatically_continues(tmp_path):
    async def scenario():
        host, session, clock = await setup(tmp_path)
        try:
            mid=await held(session)
            frame(session,{'type':'result','uuid':'auth','subtype':'success','is_error':True,
                'result':'Not logged in · Please run /login','user_message_uuid':mid})
            before=host.store.path(session.sid,1,'stdin').read_bytes()
            clock[0]+=30*86400
            for _ in range(4): await host.supervisor.tick()
            assert not host.store.records('recoveries') and len(host.sessions)==1
            assert host.store.path(session.sid,1,'stdin').read_bytes()==before
            assert any(x['category']=='auth' and x['escalation']['target']=='Dan' for x in host.store.incidents(limit=500)['incidents'])
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_explicit_interrupt_receipt_requires_native_result(tmp_path):
    async def scenario():
        host,session,clock=await setup(tmp_path)
        try:
            mid=await held(session)
            result=await host.dispatch({'method':'interrupt','params':{'execution_id':session.record['execution_id'],
                'generation':1,'authority':'explicit fixture operator'}})
            await until(lambda: host.store.get('receipts',session.sid,mid)['completed'])
            receipt=host.store.get('receipts',session.sid,mid)
            assert receipt['results'][0]['category']=='interrupted'
            assert not host.store.records('recoveries')
            assert any(x['subtype']=='interrupt' for x in host.store.records('controls',session.sid))
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_eof_and_partial_output_while_pid_alive_never_authorizes_resume(tmp_path):
    async def scenario():
        host,session,clock=await setup(tmp_path)
        try:
            mid=str(uuid.uuid4())
            await session.send(mid,'partial_eof_live','partial fixture original')
            await until(lambda:session.record.get('stdout_eof'))
            assert session.process.returncode is None
            before=host.store.path(session.sid,1,'stdin').read_bytes()
            clock[0]+=86400
            await host.supervisor.tick()
            assert session.generation==1 and not host.store.records('recoveries')
            assert host.store.path(session.sid,1,'stdin').read_bytes()==before
            assert host.store.get('receipts',session.sid,mid)['completed'] is False
            assert any(x['category']=='stdout_eof_unsettled' for x in host.store.incidents(limit=500)['incidents'])
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_failed_recovery_blocks_not_another_retry_or_respawn(tmp_path):
    async def scenario():
        def failed_builder(run_dir,**kwargs):
            result=builder(run_dir,**kwargs)
            result['env']['FIXTURE_RECOVERY_ERROR']='1'
            return result
        host,session,clock=await setup(tmp_path,launch_builder=failed_builder)
        try:
            mid=await held(session)
            frame(session,{'type':'result','uuid':'capacity','is_error':True,'result':'API Error: 429 fixture',
                'user_message_uuid':mid,'retry_after_seconds':0})
            await host.supervisor.tick()
            await until(lambda:host.store.records('recoveries')[0]['status']=='blocked')
            before=host.store.path(session.sid,1,'stdin').read_bytes()
            clock[0]+=30*86400
            for _ in range(4): await host.supervisor.tick()
            assert len(host.store.records('recoveries'))==1
            assert host.store.path(session.sid,1,'stdin').read_bytes()==before
            assert session.record['recovery_attempt_failed'] and session.record['pending_escalations']
            assert any(x['category']=='recovery_failed' for x in host.store.incidents(limit=500)['incidents'])
            action=host.store.records('recoveries')[0]
            for incident_id in list(session.record['pending_escalations']):
                host.supervisor.resolve_incident(session.sid,incident_id,'Operator inspected fixture failure')
            host.supervisor.resolve_recovery(session.sid,action['recovery_id'],'Recovery cancelled after inspection; no replay')
            assert not session.record.get('pending_recovery')
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_restart_claimed_action_never_resends(tmp_path):
    async def scenario():
        host,session,clock=await setup(tmp_path)
        sid,root=session.sid,host.root
        mid=await held(session)
        frame(session,{'type':'result','uuid':'capacity','is_error':True,'result':'API Error: 429 fixture',
            'user_message_uuid':mid,'retry_after_seconds':0})
        action=host.store.records('recoveries')[0]
        action['status']='claimed'
        host.store.put('recoveries',sid,action,action['recovery_id'])
        await cleanup(host)
        reopened=Host(root,tmp_path/'reopened.sock',builder,auth,clock=lambda:clock[0]+86400)
        try:
            await reopened.supervisor.tick()
            assert not reopened.sessions
            assert reopened.store.get('recoveries',sid,action['recovery_id'])['status']=='blocked'
            assert reopened.state(sid)['pending_escalations']
            assert reopened.store.get('receipts',sid,action['action_uuid']) is None
        finally:
            reopened.store.db.close(); os.close(reopened.host_lock)
    asyncio.run(scenario())


def test_prestart_invalid_flag_no_respawn_storm(tmp_path):
    async def scenario():
        clock=[1000]
        def broken(run_dir,**kwargs):
            result=builder(run_dir,**kwargs)
            result['env']['FIXTURE_STARTUP_FAILURE']='1'
            return result
        host=Host(tmp_path/'startup',tmp_path/'startup.sock',broken,auth,clock=lambda:clock[0])
        try:
            row=await host.launch(native_uuid=str(uuid.uuid4()),cwd=str(tmp_path),model='haiku',effort='low',system_prompt='fixture')
            session=host.sessions[row['native_uuid']]
            await until(lambda:session.record.get('owner')=='gone')
            clock[0]+=86400
            for _ in range(3): await host.supervisor.tick()
            # No respawn: the ended session was released and nothing launched another generation.
            assert not host.sessions and host.state(session.sid)['generation']==1 and not host.store.records('recoveries')
            incident=next(x for x in host.store.incidents(limit=500)['incidents'] if x['category']=='startup_failure')
            assert incident['handler']=='alert_no_retry' and incident['source_cursor']
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_unexpected_execution_model_is_disclosed_and_blocks_send(tmp_path):
    async def scenario():
        host,session,clock=await setup(tmp_path)
        try:
            frame(session,{'type':'assistant','message':{'model':'claude-sonnet-5-5','content':[]}})
            assert session.record['settings_mismatch'] and session.record['setting_control_pending']
            with pytest.raises(RuntimeError,match='settings'):
                await session.send(str(uuid.uuid4()),'not sent','not sent')
            incident=next(x for x in host.store.incidents(limit=500)['incidents'] if x['category']=='execution_model_mismatch')
            assert incident['detail']['expected_applied_model']=='claude-haiku-5-5'
            assert incident['detail']['execution_model']=='claude-sonnet-5-5'
            await host.dispatch({'method':'get_settings','params':{'execution_id':session.record['execution_id'],'generation':1}})
            assert not session.record['settings_mismatch']
            assert session.record['pending_escalations']  # readback does not erase the incident
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_explicit_cancel_of_scheduled_recovery_has_no_input_effect(tmp_path):
    async def scenario():
        host,session,clock=await setup(tmp_path)
        try:
            mid=await held(session)
            frame(session,{'type':'rate_limit_event','rate_limit_info':{'status':'rejected','resetsAt':clock[0]+3600}})
            frame(session,{'type':'result','uuid':'quota','is_error':True,'result':'API Error: 429 fixture',
                'user_message_uuid':mid})
            action=host.store.records('recoveries')[0]
            assert action['due_at']==clock[0]+3600 and action['delay_basis']=='resetsAt'
            before=host.store.path(session.sid,1,'stdin').read_bytes()
            host.supervisor.resolve_recovery(session.sid,action['recovery_id'],'Operator inspected and cancelled automatic continuation')
            clock[0]+=86400
            await host.supervisor.tick()
            assert host.store.get('recoveries',session.sid,action['recovery_id'])['status']=='cancelled'
            assert host.store.path(session.sid,1,'stdin').read_bytes()==before and not session.record.get('pending_recovery')
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_resume_refuses_tampered_stored_configuration_before_spawn(tmp_path):
    async def scenario():
        host,session,clock=await setup(tmp_path)
        try:
            await host.dispatch({'method':'close_input','params':{'execution_id':session.record['execution_id'],'generation':1}})
            await until(lambda:session.record.get('owner')=='gone')
            record=host.state(session.sid)
            record['launch']['settings_sha256']=hashlib.sha256((Path(record['run_dir'])/'settings.json').read_bytes()).hexdigest()
            record['launch']['mcp_sha256']=hashlib.sha256((Path(record['run_dir'])/'mcp.json').read_bytes()).hexdigest()
            host.store.put('sessions',session.sid,record)
            (Path(record['run_dir'])/'mcp.json').write_bytes(b'changed fixture config')
            host.launch_builder=None  # exercise production snapshot-hash admission, no CLI spawn
            with pytest.raises(ValueError,match='configuration hash differs'):
                await host.resume_snapshot(session.sid)
            assert session.sid not in host.sessions and host.state(session.sid)['generation']==1
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_background_task_alone_never_blocks_the_next_send(tmp_path):
    """A Manager's background waiter (its doorbell) must not lock Dan out of the conversation."""
    async def scenario():
        host, session, clock = await setup(tmp_path)
        try:
            mid=await held(session)
            frame(session,{'type':'system','subtype':'task_started','task_id':'waiter','is_backgrounded':True})
            frame(session,{'type':'result','uuid':'main-result','subtype':'success','is_error':False,'user_message_uuid':mid})
            assert session.record['background_unsettled'] and not session.pending
            receipt=await session.send(str(uuid.uuid4()),'next message','next message')
            assert receipt['submitted']=='next message'
            assert session.record['background_unsettled']  # still tracked and shown, just not blocking
        finally:
            await cleanup(host)
    asyncio.run(scenario())
