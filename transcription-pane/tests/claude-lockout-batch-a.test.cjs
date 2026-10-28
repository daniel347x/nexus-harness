const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const {pane}=require('./pane-fixture.cjs');
const ref=c=>c.repeat(32),a='11111111-1111-4111-8111-111111111111',b='22222222-2222-4222-8222-222222222222';
const target=(uuid=a,c='T',generation=1)=>({schemaVersion:1,backend:'claude',targetRef:ref(c),storeRef:ref('S'),sessionRef:ref(c.toLowerCase()),scopeRef:ref('M'),
  leafRef:ref('L'),fileEpoch:ref('E'),uuid,leafUuid:null,scope:'main',source:'host',title:uuid===a?'Old Manager':'New Manager',cwd:'/private/workspace',lifecycle:'live',
  executionId:'cc-'+uuid,generation,role:'Manager',capabilities:{nativeInput:true,controls:true}});
const row=t=>({...t,state:'ready',scopes:[{label:'main',scopeRef:t.scopeRef}]});
const env=(t,fields={})=>({...t,status:'complete',events:[],branches:[],diagnostics:[],targetInfo:t,...fields});
const response=(data,status=200)=>({ok:status<400,status,text:async()=>JSON.stringify(data)});
function expose(f) {vm.runInContext('this.extra={main,loadSessions,recoverClaudeTarget,selectClaudeJournals};',f.ctx);return f.ctx.extra;}
test('Dan: pane reconstruction reselects the exact live Manager automatically after handle expiry, with no native intent',async()=>{
  const f=pane(),old=target(),fresh={...old,targetRef:ref('N'),sessionRef:ref('n')};f.storage.set(f.api.K.backend,'claude');f.storage.set(f.api.K.claudeTarget,JSON.stringify(old));f.box.value='dictation retained';const calls=[];
  f.ctx.fetch=async url=>{calls.push(url);if(url.includes('targetRef='+old.targetRef))return response({error:'host handle expired; reselect'},409);
    if(url==='/api/claude/sessions')return response({schemaVersion:1,backend:'claude',sessions:[row(fresh)],hasMore:false});return response(env(fresh));};
  await f.api.initializeTargetState();assert.equal(f.api.state.claudeTarget.executionId,old.executionId);assert.equal(f.api.state.claudeTarget.targetRef,fresh.targetRef);
  assert.equal(f.api.state.claudeTargetError,null);assert.equal(f.el('send-btn').disabled,false);assert.equal(f.box.value,'dictation retained');assert.match(f.el('status').textContent,/Ready: sending/);assert.notEqual(f.el('status').className,'status err');
  assert.ok(calls.every(url=>!url.includes('/send')&&!url.includes('/control')&&!url.startsWith('/oc')));
});
test('Dan: transient target failure retries automatically and initializing selection upgrades when ready',async()=>{
  for(const initializing of [false,true]){
    const f=pane(),t=target();f.api.state.backend='claude';f.api.state.claudeTargetHint=t;
    f.api.state.claudeTarget=initializing?{...t,lifecycle:'connecting',capabilities:{nativeInput:false,controls:false}}:t;let ready=false,reads=0;
    if(!initializing)f.api.state.claudeTargetError='temporary 409';
    f.ctx.fetch=async url=>{reads++;if(url.startsWith('/api/claude/sessions'))return response({schemaVersion:1,backend:'claude',sessions:[{...row(t),state:ready?'ready':'initializing',capabilities:ready?t.capabilities:{nativeInput:false,controls:false}}],hasMore:false});
      if(url.startsWith('/api/claude/state?'))return response({...t,liveState:{state:'ready'}});return response(env(t));};
    await f.api.pollClaudeManager();assert.equal(f.el('send-btn').disabled,true);ready=true;await f.api.pollClaudeManager();assert.equal(f.api.state.claudeTargetError,null);
    assert.equal(f.el('send-btn').disabled,false);assert.ok(reads>=4);assert.match(f.el('status').textContent,/Ready: sending/);
  }
});
test('automatic row selection retries a transient validation failure neutrally, never sending to the previous Manager while connecting',async()=>{
  const f=pane(),old=target(),fresh=target(b,'N');vm.runInContext('this.receive=handleTargetMessage;',f.ctx);f.api.state.backend='claude';f.api.state.claudeTarget=old;f.api.state.editorId='editor';f.box.value='new dictation';
  const acknowledgements=[],child={postMessage:m=>acknowledgements.push(m)};f.ctx.location={origin:'http://fixture.invalid'};f.el('claude-sessions-frame').contentWindow=child;let fail=true;const calls=[];
  f.ctx.fetch=async url=>{calls.push(url);if(/^\/api\/claude\/(events|target)\?/.test(url))return fail?response({error:'temporary initialization boundary'},409):response(env(fresh));
    if(url.startsWith('/api/claude/state?'))return response({...fresh,liveState:{state:'ready'}});throw new Error('no old-target intent: '+url);};
  await f.ctx.receive({source:child,origin:f.ctx.location.origin,data:{schemaVersion:1,type:'target',clientId:'history',editorId:'editor',actionId:'selection',actionRevision:1,targetRevision:0,targetRef:fresh.targetRef,automatic:true}});
  assert.equal(acknowledgements.at(-1).pending,true);assert.equal(f.el('status').className,'status info');assert.equal(f.el('send-btn').disabled,true);
  const count=calls.length;await f.api.send();assert.equal(calls.length,count);assert.equal(f.box.value,'new dictation');fail=false;await f.api.pollClaudeManager();
  assert.equal(f.api.state.claudeTarget.executionId,fresh.executionId);assert.equal(f.el('send-btn').disabled,false);assert.equal(acknowledgements.at(-1).applied,true);assert.equal(f.box.value,'new dictation');
  assert.ok(calls.every(url=>!url.includes('/send')&&!url.includes('/control')));
});
test('Dan: old pending Send and control remain visible/query-only but new Manager Send and Interrupt are independent',async()=>{
  const f=pane(),extra=expose(f),old=target(),fresh=target(b,'N');f.api.state.backend='claude';f.api.state.claudeTarget=old;f.box.value='old original';const posts=[],queries=[];
  f.ctx.fetch=async(url,options)=>{const body=options?.body&&JSON.parse(options.body),t=body?.executionId===old.executionId||url.includes(encodeURIComponent(old.executionId))?old:f.api.state.claudeTarget;
    if(url.startsWith('/api/claude/state?'))return response({...t,liveState:{state:'ready',pending_message_uuids:[]}});
    if(url==='/api/claude/send'){posts.push(body);return response({...t,receipt:{execution_id:t.executionId,generation:t.generation,message_uuid:body.messageUuid,original:body.original,submitted:body.submitted,durable:true,acknowledged:false}});}
    if(url==='/api/claude/control'){posts.push(body);return response({...t,controlReceipt:{request_id:body.requestId,generation:t.generation,state:'pending'}});}
    if(url.includes('receipt?')){queries.push(url);return response({...t,receipt:null,controlReceipt:null});}throw new Error(url);};
  await f.api.send();await f.api.controlClaude('set_model','claude-sonnet-5-5');const oldSend=f.api.state.claudeSend,oldControl=f.api.state.claudeControl;
  f.api.state.claudeTarget=fresh;extra.selectClaudeJournals(fresh);f.api.invalidateTarget();f.api.renderTarget();f.box.value='new deliberate intent';
  assert.equal(f.el('send-btn').disabled,false);assert.equal(f.el('claude-interrupt-now').disabled,false);
  await f.api.send();await f.api.controlClaude('interrupt');await f.api.pollClaudeManager();
  assert.equal(posts.filter(p=>p.executionId===fresh.executionId&&p.messageUuid).length,1);assert.equal(posts.filter(p=>p.executionId===fresh.executionId&&p.operation==='interrupt').length,1);
  assert.equal(f.api.state.claudeSends.get(old.executionId+'/1'),oldSend);assert.equal(f.api.state.claudeControls.get(old.executionId+'/1'),oldControl);
  assert.match(f.el('claude-send-receipt').textContent,/Old Manager.*query only; never resend/);assert.match(f.el('claude-control-receipt').textContent,/Old Manager.*query only; never repeat/);
  assert.ok(queries.some(q=>q.includes(oldSend.messageUuid)));assert.equal(f.box.value,'new deliberate intent');
  const reload=pane({storage:f.storage});reload.api.state.backend='claude';reload.api.initializeClaudeJournal();reload.api.renderTarget();assert.equal(reload.api.state.claudeJournalError,null);assert.equal(reload.api.state.claudeSends.size,2);assert.equal(reload.api.state.claudeControls.size,2);
  assert.equal(reload.el('send-btn').disabled,true);assert.equal(reload.el('claude-manager-panel').hidden,false); // receipts survive without a selected mutation target
});
test('Send ACK for 600K text stores receipt by reference and cannot expand into a second original',async()=>{
  const f=pane(),t=target();f.api.state.backend='claude';f.api.state.claudeTarget=t;f.box.value='x'.repeat(600000);let before=0;
  f.ctx.fetch=async(url,options)=>{if(url.includes('/state?'))return response({...t,liveState:{pending_message_uuids:[]}});
    const body=JSON.parse(options.body),key=f.api.K.claudeSend+':'+t.executionId+'/1';before=f.storage.get(key).length;
    return response({...t,receipt:{execution_id:t.executionId,generation:1,message_uuid:body.messageUuid,original:body.original,submitted:body.submitted,durable:true,acknowledged:true,completed:true,results:['irrelevant diagnostic'.repeat(50000)]}});};
  await f.api.send();const saved=f.storage.get(f.api.K.claudeSend+':'+t.executionId+'/1'),op=JSON.parse(saved);
  assert.equal(f.box.value,'');assert.equal(op.acknowledged,true);assert.equal(op.receipt.receiptRef,op.messageUuid);assert.equal('original' in op.receipt,false);assert.equal('submitted' in op.receipt,false);
  assert.ok(saved.length<before+2000);assert.ok(saved.length*2<4*1024*1024);assert.equal(f.api.readOriginals()[0].text.length,600000);
});
test('explicit no-context Refine never validates a stale existing target',async()=>{
  const f=pane({response:{content:[{type:'text',text:'refined'}],stop_reason:'end_turn'}});f.api.state.backend='claude';f.api.state.claudeTarget=target();f.storage.set(f.api.K.nativeNoContext,'true');f.box.value='original';
  await f.api.refine();assert.equal(f.box.value,'refined');assert.ok(f.requests.every(([url])=>url.startsWith('https://')));
});
test('ended Manager supplies fully witnessed archived Refine context but Send stays refused with its named replacement',async()=>{
  const f=pane({response:{content:[{type:'text',text:'cleaned with old context'}],stop_reason:'end_turn'}}),t={...target(),lifecycle:'ended',archived:true,capabilities:{nativeInput:false,controls:false},successor:{title:'New Manager',executionId:'cc-'+b}};
  f.api.state.backend='claude';f.api.state.claudeTarget=t;f.box.value='original';const provider=f.ctx.fetch,calls=[];
  f.ctx.fetch=async(url,options)=>{calls.push(url);if(url.startsWith('https://'))return provider(url,options);
    if(url.startsWith('/api/claude/context?'))return response({...t,status:'stored',contextIdentity:ref('C'),targetRevision:0,text:'[ASSISTANT]\narchived prose',bytes:26,turns:1,users:0,readAt:100});
    if(/^\/api\/claude\/(events|target)\?/.test(url))return response(env({...t,contextIdentity:ref('C')}));throw new Error('must not contact native writer: '+url);};
  await f.api.send();assert.equal(calls.length,0);assert.equal(f.box.value,'original');assert.match(f.el('status').textContent,/This Manager has ended.*live Manager is New Manager.*Send to it reconnects the conversation/);
  await f.api.refine();assert.equal(f.box.value,'cleaned with old context');assert.ok(calls.some(u=>u.startsWith('/api/claude/context?')));assert.ok(calls.every(u=>!u.includes('/send')&&!u.includes('/control')));
});
test('Claude startup never awaits OpenCode and never overwrites a red journal recovery status',async()=>{
  const f=pane(),extra=expose(f);f.storage.set(f.api.K.backend,'claude');f.storage.set(f.api.K.claudeSend,'{"schemaVersion":1}');const calls=[],intervals=[];
  f.ctx.fetch=url=>{calls.push(url);return new Promise(()=>{});};f.ctx.setInterval=fn=>{intervals.push(fn);return intervals.length;};
  await extra.main();assert.deepEqual(calls,[]);assert.ok(intervals.length>=3);assert.match(f.el('status').textContent,/native recovery blocked/);assert.equal(f.el('status').className,'status err');
  await extra.loadSessions();await f.api.pollWatcher();assert.deepEqual(calls,[]);
});
test('switching a cold OpenCode startup to Claude cannot strand native polling behind an unrelated hung service',async()=>{
  const f=pane(),extra=expose(f),t=target(),calls=[],timers=[];
  f.ctx.setInterval=fn=>{timers.push(fn);return timers.length;};f.ctx.fetch=url=>{calls.push(url);
    if(url.startsWith('/api/claude/state?'))return Promise.resolve(response({...t,liveState:{state:'ready'}}));return new Promise(()=>{});};
  await extra.main();assert.ok(timers.length>=3);assert.ok(calls.some(u=>u.startsWith('/oc')));
  f.api.setBackend('claude');f.api.state.claudeTarget=t;await f.api.pollClaudeManager();assert.equal(f.api.state.claudeManager.liveState.state,'ready');assert.equal(f.el('send-btn').disabled,false);
});
