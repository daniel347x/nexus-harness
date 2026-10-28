const test = require('node:test');
const assert = require('node:assert/strict');
const {pane} = require('./pane-fixture.cjs');
const ref = c=>c.repeat(32);
const uuid='11111111-1111-4111-8111-111111111111';
const target=()=>({schemaVersion:1,backend:'claude',targetRef:ref('T'),storeRef:ref('S'),sessionRef:ref('J'),scopeRef:ref('M'),
  leafRef:ref('L'),fileEpoch:ref('E'),uuid,leafUuid:null,source:'host',scope:'main',title:'Registered Manager',
  executionId:'cc-'+uuid,generation:1,role:'Manager',capabilities:{nativeInput:true,controls:true}});
function setup() {
  const f=pane(), t=target();f.api.state.backend='claude';f.api.state.claudeTarget=t;
  const live={state:'ready',owner:'live',controls_effective:{model:'claude-sonnet-5-5',effort:'high'},pending_message_uuids:[]};
  const receipts=new Map(), controls=new Map(), calls=[];
  const env=body=>({schemaVersion:1,backend:'claude',targetRef:t.targetRef,executionId:t.executionId,generation:t.generation,uuid:t.uuid,...body});
  const response=(data,status=200)=>({ok:status<400,status,text:async()=>JSON.stringify(data)});
  const server={live,receipts,controls,calls,env,response,beforeState:null,beforePost:null,afterPost:null,losePost:false,
    sendStatus:200,controlStatus:200,loseControl:false,initialAck:false};
  f.ctx.fetch=async(url,options)=>{
    const body=options?.body&&JSON.parse(options.body);calls.push({url,body});
    if(url.startsWith('/api/claude/state?')){if(server.beforeState)await server.beforeState();return response(env({liveState:live}));}
    if(url==='/api/claude/send'){
      if(server.beforePost)await server.beforePost(body);
      if(server.sendStatus!==200)return response({error:'busy: idle-only'},server.sendStatus);
      const receipt={execution_id:t.executionId,generation:t.generation,message_uuid:body.messageUuid,original:body.original,submitted:body.submitted,
        durable:true,acknowledged:server.initialAck,started:false,completed:false,status:'accepted'};
      receipts.set(body.messageUuid,receipt);if(server.afterPost)await server.afterPost(receipt);
      if(server.losePost)throw new Error('lost POST response');
      return response(env({receipt}));
    }
    if(url.startsWith('/api/claude/receipt?'))return response(env({receipt:receipts.get(new URLSearchParams(url.split('?')[1]).get('messageUuid'))||null}));
    if(url==='/api/claude/control'){
      if(server.controlStatus!==200)return response({error:'busy: idle-only'},server.controlStatus);
      const r={request_id:body.requestId,generation:t.generation,state:'pending',applied:false};controls.set(body.requestId,r);
      if(server.loseControl)throw new Error('lost control response');return response(env({controlReceipt:r}));
    }
    if(url.startsWith('/api/claude/control-receipt?'))return response(env({controlReceipt:controls.get(new URLSearchParams(url.split('?')[1]).get('requestId'))||null}));
    throw new Error('unexpected '+url);
  };
  return Object.assign(f,{t,server});
}
const posts=f=>f.server.calls.filter(c=>c.url==='/api/claude/send');
const controlPosts=f=>f.server.calls.filter(c=>c.url==='/api/claude/control');
const ack=f=>{f.server.receipts.get(f.api.state.claudeSend.messageUuid).acknowledged=true;};
test('native model/thinking badge uses verified effective readback, never requested controls as applied',async()=>{
  const f=setup();f.server.live.setting_control_state='applied';await f.api.pollClaudeManager();
  assert.equal(f.el('claude-effective').textContent,'claude-sonnet-5-5 · thinking high · applied');
  f.el('claude-model').value='REQUESTED-OPUS';f.el('claude-effort').value='max';f.api.renderClaudeManager();assert.doesNotMatch(f.el('claude-effective').textContent,/REQUESTED|max/);
  for(const patch of [{setting_control_state:'pending'},{setting_control_state:undefined},{setting_control_state:'applied',setting_control_pending:true},{setting_control_state:'applied',setting_control_pending:false,settings_mismatch:true}]){
    Object.assign(f.server.live,patch);await f.api.pollClaudeManager();assert.match(f.el('claude-effective').textContent,/thinking high · pending$/);assert.doesNotMatch(f.el('claude-effective').textContent,/REQUESTED|max|applied/);
  }
  f.api.state.claudeManager={targetRef:f.t.targetRef,error:'offline'};f.api.renderClaudeManager();assert.equal(f.el('claude-effective').textContent,'Model unknown · thinking unknown · pending');
  f.api.state.backend='opencode';f.api.renderClaudeManager();assert.equal(f.el('claude-manager-panel').hidden,true);
});

test('registered Manager capability is accepted; workers cannot borrow writable flags',()=>{
  const f=setup(),t=f.t,env={...t,status:'complete',targetInfo:t,events:[],diagnostics:[],branches:[]};
  assert.equal(f.api.validatedClaudeTarget(env,t.targetRef).capabilities.nativeInput,true);
  for(const role of ['worker','helper','conductor','base'])assert.throws(()=>f.api.validatedClaudeTarget({...env,targetInfo:{...t,role}},t.targetRef));
});
test('F2: idle controls follow changed verified model/effort for the same target, adding an absent model',async()=>{
  const f=setup(); f.server.live.setting_control_state='applied'; await f.api.pollClaudeManager();
  const choicesFor=f.api.state.claudeChoicesFor;
  f.server.live.controls_effective={model:'new-native-model',effort:'max'};
  await f.api.pollClaudeManager();
  assert.equal(f.api.state.claudeChoicesFor,choicesFor);
  assert.equal(f.el('claude-model').value,'new-native-model'); assert.equal(f.el('claude-effort').value,'max');
  assert.ok(f.el('claude-model').options.some(o=>o.value==='new-native-model'));
  assert.match(f.el('claude-effective').textContent,/new-native-model · thinking max · applied/);
  assert.equal(controlPosts(f).length,0); assert.equal(posts(f).length,0);
});
test('F2: local unapplied choices survive repeated unchanged and changed verified polls',async()=>{
  const f=setup(); f.server.live.setting_control_state='applied'; await f.api.pollClaudeManager();
  f.el('claude-model').value='my-unapplied-model'; f.el('claude-effort').value='low';
  for (let i=0;i<2;i++) await f.api.pollClaudeManager();
  f.server.live.controls_effective={model:'external-applied-model',effort:'max'};
  for (let i=0;i<2;i++) await f.api.pollClaudeManager();
  assert.equal(f.el('claude-model').value,'my-unapplied-model'); assert.equal(f.el('claude-effort').value,'low');
  assert.match(f.el('claude-effective').textContent,/external-applied-model · thinking max · applied/);
  assert.equal(controlPosts(f).length,0);
});
test('F2: in-flight/unsettled control preserves choices; settled readback follows without a false dirty edit',async()=>{
  const f=setup(); f.server.live.setting_control_state='applied'; await f.api.pollClaudeManager();
  f.api.state.claudeControlSending=true;
  f.server.live.controls_effective={model:'external-model',effort:'low'}; await f.api.pollClaudeManager();
  assert.equal(f.el('claude-model').value,'claude-sonnet-5-5');
  f.api.state.claudeControlSending=false;
  await f.api.controlClaude('apply_effort','max');
  f.server.live.controls_effective={model:'external-model',effort:'max'}; await f.api.pollClaudeManager();
  assert.equal(f.el('claude-model').value,'claude-sonnet-5-5');
  Object.assign(f.server.controls.get(f.api.state.claudeControl.requestId),{state:'applied',applied:true});
  await f.api.pollClaudeManager();
  assert.equal(f.el('claude-model').value,'external-model'); assert.equal(f.el('claude-effort').value,'max');
});
test('F3: host pending-message reason does not claim another device’s message is this browser’s',()=>{
  const f=setup(); f.server.live.pending_message_uuids=['other-device-message'];
  assert.equal(f.api.claudeBusy(f.server.live),'the Manager is still answering a previous message');
  assert.equal(f.api.claudeSteerBlock(f.server.live),'');
});
test('F2: applied local model choice settles while a native effort change follows; target changes discard old choices',async()=>{
  const f=setup(); f.server.live.setting_control_state='applied'; await f.api.pollClaudeManager();
  f.el('claude-model').value='my-applied-model'; await f.api.controlClaude('set_model','my-applied-model');
  Object.assign(f.server.controls.get(f.api.state.claudeControl.requestId),{state:'applied',applied:true});
  f.server.live.controls_effective={model:'my-applied-model',effort:'low'}; await f.api.pollClaudeManager();
  assert.equal(f.el('claude-model').value,'my-applied-model'); assert.equal(f.el('claude-effort').value,'low');
  f.el('claude-model').value='unapplied'; f.api.state.claudeTarget={...f.t,generation:2};
  f.api.renderClaudeManager();
  assert.equal(f.el('claude-model').value,'my-applied-model'); assert.equal(f.el('claude-effort').value,'low');
});
test('long Unicode/multiline full original and exact submission are journaled before one POST; acceptance never clears',async()=>{
  const f=setup(),full=' \n'+('🧭 café 漢字\n\nlong paragraph\t😀\r\n'.repeat(4500))+'THE-TAIL\n ';
  f.box.value=full;
  f.server.beforePost=body=>{
    const index=JSON.parse(f.storage.get(f.api.K.claudeSend));assert.equal(index.schemaVersion,2);
    const stored=JSON.parse(f.storage.get(f.api.K.claudeSend+':'+f.t.executionId+'/'+f.t.generation));assert.equal(stored.original,full);assert.equal(stored.submitted,full.trim());
    assert.equal(stored.messageUuid,body.messageUuid);assert.equal(f.api.readOriginals()[0].text,full);
  };
  await f.api.send();assert.equal(posts(f).length,1);assert.equal(posts(f)[0].body.original,full);assert.equal(posts(f)[0].body.submitted,full.trim());
  assert.equal(f.box.value,full);assert.match(f.el('claude-send-receipt').textContent,/accepted yes.*acknowledged no/);
  ack(f);await f.api.pollClaudeManager();assert.equal(f.box.value,'');assert.equal(f.storage.get(f.api.K.lastSent),full.trim());
  assert.equal(f.api.readOriginals()[0].text,full);assert.equal(posts(f).length,1);
});
test('lost Send POST response reconciles same ID; repeated hotkeys never duplicate',async()=>{
  const f=setup();f.box.value='full original 😀';f.server.losePost=true;
  await f.api.send();const id=f.api.state.claudeSend.messageUuid;assert.equal(f.box.value,'full original 😀');
  await f.api.send();assert.equal(posts(f).length,1);assert.equal(f.api.state.claudeSend.messageUuid,id);
  ack(f);await f.api.pollClaudeManager();assert.equal(f.box.value,'');assert.equal(posts(f).length,1);
});
test('round 5: an uncertain Send status line gives way to the receipt that confirms it, and says the draft was sent',async()=>{
  const f=setup();f.box.value='dictated';f.server.losePost=true;await f.api.send();
  assert.match(f.el('status').textContent,/Manager Send uncertain/);
  await f.api.pollClaudeManager();
  assert.match(f.el('status').textContent,/accepted by the host/);assert.doesNotMatch(f.el('status').textContent,/uncertain/);
  ack(f);await f.api.pollClaudeManager();
  assert.match(f.el('status').textContent,/Manager Send delivered.*sent draft was cleared/);assert.equal(f.box.value,'');assert.equal(posts(f).length,1);
  // Dan edited the box after the uncertain Send: the receipt still says the earlier text WAS sent.
  const g=setup();g.box.value='first';g.server.losePost=true;await g.api.send();g.box.value='first plus more';g.api.draftChanged();
  ack(g);await g.api.pollClaudeManager();
  assert.match(g.el('status').textContent,/retained draft WAS sent/);assert.equal(g.box.value,'first plus more');
  // A newer line is never overwritten by an older Send's evidence.
  const h=setup();h.box.value='third';h.server.losePost=true;await h.api.send();h.api.status('something newer','info');
  ack(h);await h.api.pollClaudeManager();assert.equal(h.el('status').textContent,'something newer');
});
test('receipt null after lost response remains uncertain, never becomes permission to resend',async()=>{
  const f=setup();f.box.value='keep';f.server.losePost=true;await f.api.send();f.server.receipts.clear();
  for(let i=0;i<3;i++)await f.api.send();assert.equal(posts(f).length,1);assert.equal(f.box.value,'keep');
  assert.match(f.el('claude-send-receipt').textContent,/uncertain yes/);
});
for(const race of ['edit','edit-away-back','target-away-back','backend','generation','page'])test('ACK preserves draft on '+race+' race',async()=>{
  const f=setup();f.box.value='original';await f.api.send();
  if(race==='edit'){f.box.value='new dictation';f.api.draftChanged();}
  if(race==='edit-away-back'){f.box.value='other';f.api.draftChanged();f.box.value='original';f.api.draftChanged();}
  if(race==='target-away-back'){f.api.invalidateTarget();f.api.invalidateTarget();}
  if(race==='backend')f.api.state.backend='opencode';
  if(race==='generation')f.api.state.claudeTarget={...f.t,generation:2};
  if(race==='page')f.api.state.pageEpoch='other page';
  const before=f.box.value;ack(f);await f.api.pollClaudeManager();assert.equal(f.box.value,before);assert.equal(posts(f).length,1);
  assert.equal(f.storage.get(f.api.K.lastSent),'original');
});
test('receipt exact original/submission/UUID/generation checks reject forged ACK',async()=>{
  for(const patch of [{original:'wrong'},{submitted:'wrong'},{message_uuid:'wrong'},{generation:2},{execution_id:'cc-wrong'},{durable:false}]){
    const f=setup();f.box.value='original';await f.api.send();ack(f);Object.assign(f.server.receipts.get(f.api.state.claudeSend.messageUuid),patch);
    await f.api.pollClaudeManager();assert.equal(f.box.value,'original');assert.equal(f.api.state.claudeSend.uncertain,true);
  }
});
test('browser reload recovers pending UUID and ACK, but never clears the new page draft',async()=>{
  const f=setup();f.box.value='original';f.server.losePost=true;await f.api.send();ack(f);
  const next=setup();for(const [k,v]of f.storage)next.storage.set(k,v);next.box.value='original';
  next.server.receipts.set(f.api.state.claudeSend.messageUuid,f.server.receipts.get(f.api.state.claudeSend.messageUuid));
  next.api.initializeClaudeJournal();await next.api.pollClaudeManager();assert.equal(next.box.value,'original');assert.equal(posts(next).length,0);
  assert.equal(next.storage.get(next.api.K.lastSent),'original');
});
test('storage quota/corrupt originals/oversize journal refuse before native Send',async()=>{
  for(const kind of ['quota','corrupt','oversize']){
    const f=setup();f.box.value=kind==='oversize'?'😀'.repeat(600000):'original';
    if(kind==='quota')f.failWrites();if(kind==='corrupt')f.storage.set(f.api.K.recovery,'corrupt');
    await f.api.send();assert.equal(posts(f).length,0);assert.ok(f.box.value);assert.equal(f.api.state.sending,false);
  }
});
test('busy Manager: Send is one host steer, queued then delivered at its next tool step',async()=>{
  const f=setup();f.box.value='steer draft';f.server.live.pending_message_uuids=['busy'];f.server.live.native_session_state='running';
  await f.api.send();assert.equal(posts(f).length,1);assert.equal(posts(f)[0].body.steer,true);
  const op=f.api.state.claudeSend;assert.equal(op.steer,true);assert.equal(f.box.value,'steer draft');   // not queued yet
  assert.match(f.el('status').textContent,/Queued for the busy Manager: it lands at its next tool step/);
  f.server.receipts.get(op.messageUuid).queued=true;await f.api.pollClaudeManager();
  assert.equal(f.box.value,'');assert.match(f.el('status').textContent,/Queued for the Manager: it lands at its next tool step/);
  assert.match(f.el('claude-send-receipt').textContent,/^Steer .* queued yes · delivered no/);
  f.box.value='second thought';await f.api.send();assert.equal(posts(f).length,1);   // one steer in flight at a time
  assert.match(f.el('status').textContent,/previous message is queued for the Manager, not delivered yet/);assert.equal(f.box.value,'second thought');
  Object.assign(f.server.receipts.get(op.messageUuid),{acknowledged:true,delivered:true,status:'delivered'});await f.api.pollClaudeManager();
  assert.match(f.el('status').textContent,/Delivered to the busy Manager at its next tool step/);
  assert.match(f.el('claude-send-receipt').textContent,/queued yes · delivered yes/);
  Object.assign(f.server.live,{pending_message_uuids:[],native_session_state:'idle'});f.server.receipts.get(op.messageUuid).completed=true;await f.api.pollClaudeManager();
  await f.api.send();assert.equal(posts(f).length,2);assert.equal('steer' in posts(f)[1].body,false);   // idle: an ordinary Send
});
test('a queued steer an interrupt cancelled is settled: the next Send goes, nothing is resent',async()=>{
  const f=setup();f.box.value='steer draft';Object.assign(f.server.live,{pending_message_uuids:['busy'],native_session_state:'running'});
  await f.api.send();const op=f.api.state.claudeSend;
  Object.assign(f.server.receipts.get(op.messageUuid),{queued:true});await f.api.pollClaudeManager();
  Object.assign(f.server.receipts.get(op.messageUuid),{status:'cancelled',cancelled:true});await f.api.pollClaudeManager();
  assert.match(op.note,/cancelled before delivery/);assert.equal(f.api.pendingConversationSend(f.api.state.claudeTarget),null);
  f.box.value='after the interrupt';await f.api.send();assert.equal(posts(f).length,2);
  assert.equal(posts(f).filter(c=>c.body.messageUuid===op.messageUuid).length,1);
});
test('audit #3: a scheduled recovery no longer refuses Send: busy, it goes as one steer and the host decides; Interrupt stays a separate control',async()=>{
  const f=setup();f.box.value='waiting draft';f.server.live.pending_message_uuids=['busy'];f.server.live.native_retry_active=true;f.server.live.pending_recovery='rec-1';
  assert.equal(f.api.claudeSteerBlock(f.server.live),'');
  assert.match(f.api.claudeRecoveryNote(f.server.live),/your Send replaces its “continue”/);
  assert.doesNotMatch(f.api.claudeRecoveryNote(f.server.live),/crash recovery is pending/);
  await f.api.send();assert.equal(posts(f).length,1);assert.equal(posts(f)[0].body.steer,true);
  await f.api.controlClaude('set_model','claude-opus-5-5');assert.equal(controlPosts(f).length,0);   // still busy with its own turn
  await f.api.controlClaude('interrupt');assert.equal(controlPosts(f).length,1);assert.equal(controlPosts(f)[0].body.operation,'interrupt');
  assert.equal(posts(f).length,1);
});
test('target/draft mutation during state check refuses before POST',async()=>{
  for(const change of [f=>f.api.invalidateTarget(),f=>{f.api.draftChanged();f.box.value='new';},f=>f.api.state.backend='opencode']){
    const f=setup();f.box.value='original';f.server.beforeState=()=>change(f);await f.api.send();assert.equal(posts(f).length,0);
  }
});
test('server busy rejection does not strand Send button; subsequent fresh explicit Send can proceed',async()=>{
  const f=setup();f.box.value='original';f.server.sendStatus=409;await f.api.send();assert.equal(f.api.state.claudeSend.refused,true);
  assert.equal(f.el('send-btn').disabled,false);assert.equal(f.box.value,'original');
  f.server.sendStatus=200;await f.api.send();assert.equal(posts(f).length,2);assert.notEqual(posts(f)[0].body.messageUuid,posts(f)[1].body.messageUuid);
});
test('native applied readback and caller control UUID survive lost response; no Refine settings/default mutation',async()=>{
  const f=setup();f.box.value='draft';f.storage.set('refine_think_level','medium');f.server.loseControl=true;
  await f.api.controlClaude('apply_effort','high');const op=f.api.state.claudeControl;
  assert.equal(JSON.parse(f.storage.get(f.api.K.claudeControl+':'+f.t.executionId+'/'+f.t.generation)).requestId,op.requestId);assert.equal(controlPosts(f).length,1);
  await f.api.controlClaude('apply_effort','high');assert.equal(controlPosts(f).length,1);
  const r=f.server.controls.get(op.requestId);Object.assign(r,{state:'applied',applied:true,effective:{model:'claude-sonnet-5-5',effort:'high'}});
  await f.api.pollClaudeManager();assert.equal(op.settled,true);assert.match(f.el('claude-control-receipt').textContent,/applied verified/);
  assert.equal(f.storage.get('refine_think_level'),'medium');assert.equal(f.box.value,'draft');
});
test('workers/history cannot send or control even from direct functions; native identifier never reaches OpenCode',async()=>{
  for(const change of [{role:'worker'},{source:'native'},{capabilities:{nativeInput:false,controls:false}}]){
    const f=setup();f.api.state.claudeTarget={...f.t,...change};f.box.value='original';
    await f.api.send();await f.api.controlClaude('interrupt');assert.equal(f.server.calls.length,0);assert.equal(f.box.value,'original');
  }
});
test('malformed persisted operation blocks future Send and preserves recovery rather than clearing storage',async()=>{
  const f=setup();f.box.value='original';f.storage.set(f.api.K.claudeSend,'{"schemaVersion":1}');
  f.api.initializeClaudeJournal();await f.api.send();assert.equal(posts(f).length,0);assert.ok(f.api.state.claudeJournalError);
  assert.equal(f.storage.get(f.api.K.claudeSend),'{"schemaVersion":1}');
});
test('setter acknowledgement alone and uncertain control never enable another semantic control',async()=>{
  const f=setup();await f.api.controlClaude('set_model','claude-opus-5-5');const op=f.api.state.claudeControl,r=f.server.controls.get(op.requestId);
  for(const state of ['replied','uncertain']){
    Object.assign(r,{state,applied:false});await f.api.pollClaudeManager();assert.equal(op.settled,false);
    await f.api.controlClaude('apply_effort','low');assert.equal(controlPosts(f).length,1);
  }
});
test('interrupt reply and task settlement remain separate, later settlement updates without repeating interrupt',async()=>{
  const f=setup();f.server.live.pending_message_uuids=['busy'];await f.api.controlClaude('interrupt');
  const op=f.api.state.claudeControl,r=f.server.controls.get(op.requestId);Object.assign(r,{state:'replied',settlement:{settled:false}});
  await f.api.pollClaudeManager();assert.equal(op.settled,true);assert.match(f.el('claude-control-receipt').textContent,/task settlement not proved/);
  r.settlement.settled=true;await f.api.pollClaudeManager();assert.match(f.el('claude-control-receipt').textContent,/task settlement proved/);
  assert.equal(controlPosts(f).length,1);assert.equal(posts(f).length,0);
});
