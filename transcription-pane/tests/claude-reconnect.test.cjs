// Batch B, pane side: Send to a collected/ended registered Manager reconnects the
// conversation first, then delivers ONE new journaled Send to the rebounded generation.
// Real app.js in the offline VM; inert transport; no process, socket, port or inference.
const test = require('node:test');
const assert = require('node:assert/strict');
const {pane} = require('./pane-fixture.cjs');
const ref = c => c.repeat(32);
const uuid = '11111111-1111-4111-8111-111111111111';
const EX = 'cc-' + uuid;
const OTHER = '22222222-2222-4222-8222-222222222222';

function sleepingTarget(extra = {}) {
  return {schemaVersion:1,backend:'claude',targetRef:ref('T'),storeRef:ref('S'),sessionRef:ref('J'),scopeRef:ref('M'),
    leafRef:ref('L'),fileEpoch:ref('E'),uuid,leafUuid:null,scope:'main',source:'host',title:'Manager-Session-181',
    cwd:'/private/workspace',executionId:EX,generation:1,role:'Manager',lifecycle:'sleeping',
    capabilities:{nativeInput:false,controls:false,reconnect:true},
    successor:{title:'Manager-Session-182',executionId:'cc-'+OTHER},...extra};
}
const liveSnapshot = extra => ({...sleepingTarget(),capabilities:{nativeInput:true,controls:true,reconnect:true},lifecycle:'live',...extra});
const response = (data,status=200)=>({ok:status<400,status,text:async()=>JSON.stringify(data)});
const settle = async f => { for (let i=0;i<12;i++) await new Promise(resolve=>setImmediate(resolve)); };

function setup({target=sleepingTarget(),manualTimers=true,readyFromStart=false,generation=2}={}) {
  const f = pane({manualTimers});
  f.api.state.backend = 'claude';
  f.api.state.claudeTarget = target;
  const NEW = ref('N'), SESSION = ref('n'), SCOPE = ref('m'), LEAF = ref('l'), EPOCH = ref('e'), STORE = ref('s');
  const server = {target,newRef:NEW,calls:[],reconnectPosts:[],sendPosts:[],receiptQueries:[],ready:readyFromStart,
    reconnectError:null,generation,receipt:null,earlierReceipt:null,busy:false,liveExtra:null,beforeState:null,
    beforeReconnect:null};
  const record = (ready=server.ready) => ({schemaVersion:1,backend:'claude',targetRef:NEW,executionId:EX,uuid,role:'Manager',
    state:ready?'ready':'initializing',lifecycle:ready?'live':'pending',
    capabilities:ready?{history:true,live:true,context:true,inspect:true,nativeInput:true,controls:true,subagents:true,reconnect:true}
      :{history:true,live:true,context:true,inspect:true,nativeInput:false,controls:false,subagents:true,reconnect:false},
    liveState:ready?{state:'ready',owner:'host',initialized:true,pending_message_uuids:server.busy?['busy']:[],controls_effective:{model:'claude-sonnet-5-5',effort:'high'},...(server.liveExtra||{})}
      :{state:'initializing',owner:'host',initialized:false,...(server.liveExtra||{})}});
  f.ctx.fetch = async (url,options)=>{
    const body=options?.body&&JSON.parse(options.body);
    server.calls.push({url,body});
    // Every response echoes the REQUESTED identity, exactly as the pane's validation demands.
    const requestedRef=r=>r===NEW?NEW:(r===target.targetRef?target.targetRef:null);
    const generationFor=r=>r===NEW?server.generation:(r===target.targetRef?target.generation:null);
    const liveTarget=target.capabilities?.nativeInput===true&&!['sleeping','ended'].includes(target.lifecycle);
    const readyFor=r=>r===NEW?server.ready:liveTarget;
    if (url==='/api/claude/reconnect') {
      if (server.beforeReconnect) await server.beforeReconnect();
      server.reconnectPosts.push(body);
      if (server.reconnectError) return response({error:server.reconnectError.message},server.reconnectError.status||409);
      const r=record(server.ready);
      return response({schemaVersion:1,backend:'claude',targetRef:NEW,executionId:EX,uuid,role:'Manager',
        previousGeneration:body.generation,generation:server.generation,state:r.state,lifecycle:r.lifecycle,
        capabilities:r.capabilities,liveState:r.liveState});
    }
    if (url.startsWith('/api/claude/state?')) {
      const q=new URLSearchParams(url.split('?')[1]),ref=requestedRef(q.get('targetRef'));
      if (!ref) throw new Error('state for an unknown target reference');
      if (server.beforeState) await server.beforeState(url);
      const r=record(readyFor(ref));
      return response({schemaVersion:1,backend:'claude',targetRef:ref,executionId:EX,uuid,generation:generationFor(ref),
        role:'Manager',lifecycle:r.lifecycle,capabilities:r.capabilities,liveState:r.liveState});
    }
    if (/^\/api\/claude\/(events|target)\?/.test(url)) {
      const q=new URLSearchParams(url.split('?')[1]),ref=requestedRef(q.get('targetRef'));
      if (!ref) throw new Error('events for an unknown target reference');
      const r=record(true);
      const info={schemaVersion:1,backend:'claude',targetRef:ref,storeRef:STORE,sessionRef:SESSION,scopeRef:SCOPE,leafRef:LEAF,
        fileEpoch:EPOCH,scope:'main',uuid,leafUuid:null,title:'Manager-Session-181',cwd:'/private/workspace',source:'host',
        role:'Manager',executionId:EX,generation:generationFor(ref),state:'ready',lifecycle:'live',
        capabilities:{...r.capabilities,nativeInput:true,controls:true},successor:null,contextIdentity:null};
      return response({schemaVersion:1,backend:'claude',targetRef:ref,storeRef:STORE,sessionRef:SESSION,scopeRef:SCOPE,
        leafRef:LEAF,fileEpoch:EPOCH,status:'complete',events:[],branches:[],diagnostics:[],uuid,executionId:EX,
        generation:generationFor(ref),targetInfo:info});
    }
    if (url==='/api/claude/send') {
      server.sendPosts.push(body);
      if (generationFor(body.targetRef)!==body.generation) return response({error:'generation is not current'},409);
      if (server.sendError) return response({error:server.sendError},409);   // the host's own refusal
      const r={execution_id:EX,generation:body.generation,message_uuid:body.messageUuid,original:body.original,
        submitted:body.submitted,durable:true,acknowledged:false,started:false,completed:false,status:'accepted'};
      server.receipt=r;
      return response({schemaVersion:1,backend:'claude',targetRef:body.targetRef,executionId:EX,uuid,generation:body.generation,receipt:r});
    }
    if (url.startsWith('/api/claude/receipt?')) {
      const q=new URLSearchParams(url.split('?')[1]),mid=q.get('messageUuid');
      server.receiptQueries.push(mid);
      const receipt=(server.receipt&&server.receipt.message_uuid===mid)?server.receipt
        :(server.earlierReceipt&&server.earlierReceipt.message_uuid===mid)?server.earlierReceipt:null;
      return response({schemaVersion:1,backend:'claude',targetRef:q.get('targetRef'),executionId:EX,uuid,
        generation:Number(q.get('generation')),receipt});
    }
    throw new Error('unexpected '+url);
  };
  return Object.assign(f,{server,t:target});
}
const sends = f => f.server.sendPosts;
const reconnects = f => f.server.reconnectPosts;
const ack = f => { f.server.receipt.acknowledged=true; };

test('Dan: Send to a sleeping Manager reconnects it, waits for readiness, and delivers one new journaled Send',{timeout:20000},async()=>{
  const f=setup();f.box.value='wake up 🌙';
  const sending=f.api.send();
  await settle(f);
  assert.match(f.el('status').textContent,/Reconnecting to Manager-Session-181 …/);
  assert.equal(reconnects(f).length,1);
  assert.deepEqual(Object.keys(reconnects(f)[0]).sort(),['backend','executionId','generation','schemaVersion','targetRef']);
  assert.equal(reconnects(f)[0].generation,1);                    // exact expected old generation
  assert.equal(reconnects(f)[0].targetRef,f.t.targetRef);
  assert.equal(sends(f).length,0);                                // reconnect alone never sends
  assert.equal(f.api.state.sending,true);
  f.server.ready=true;await f.fireTimers();await settle(f);await sending;
  assert.equal(sends(f).length,1);
  assert.equal(sends(f)[0].generation,2);                         // the rebounded generation
  assert.equal(sends(f)[0].targetRef,f.server.newRef);
  assert.equal(sends(f)[0].submitted,'wake up 🌙');
  assert.equal(f.api.state.claudeTarget.generation,2);
  assert.equal(f.api.state.claudeTarget.lifecycle,'live');
  assert.equal(f.box.value,'wake up 🌙');                         // draft kept until the exact ACK
  const key=f.api.K.claudeSend+':'+EX+'/2';
  assert.equal(JSON.parse(f.storage.get(key)).original,'wake up 🌙');
  assert.equal(f.storage.get(f.api.K.claudeSend+':'+EX+'/1')??null,null);// no journal rode the old generation
  assert.match(f.el('claude-send-receipt').textContent,/accepted yes/);
  ack(f);await f.api.pollClaudeManager();
  assert.equal(f.box.value,'');
  assert.equal(f.storage.get(f.api.K.lastSent),'wake up 🌙');
  assert.equal(sends(f).length,1);                                // never a second delivery
});

test('Dan: a refused reconnect sends nothing, keeps the draft and allocates no new journal',{timeout:20000},async()=>{
  const f=setup();f.box.value='keep me';f.server.reconnectError={status:409,message:'ownership is not proved settled; reconnect withheld'};
  await f.api.send();
  assert.equal(reconnects(f).length,1);assert.equal(sends(f).length,0);
  assert.equal(f.box.value,'keep me');
  assert.match(f.el('status').textContent,/ownership is not proved settled/);
  assert.equal(f.storage.get(f.api.K.claudeSend)??null,null);
  assert.equal(f.api.state.claudeSend,null);
});

test('a draft edit during the reconnect wait aborts the Send; nothing is delivered',{timeout:20000},async()=>{
  const f=setup();f.box.value='original';
  const sending=f.api.send();await settle(f);
  f.box.value='edited while reconnecting';f.api.draftChanged();
  f.server.ready=true;await f.fireTimers();await settle(f);await sending;
  assert.equal(sends(f).length,0);
  assert.equal(f.box.value,'edited while reconnecting');
  assert.match(f.el('status').textContent,/target\/draft changed; check it and Send again/);
  assert.equal(f.storage.get(f.api.K.claudeSend+':'+EX+'/2')??null,null);
});

test('an edit away and back during the reconnect wait still cancels the Send',{timeout:20000},async()=>{
  const f=setup();f.box.value='original';
  const sending=f.api.send();await settle(f);
  f.box.value='other';f.api.draftChanged();f.box.value='original';f.api.draftChanged();   // same text, new revision
  f.server.ready=true;await f.fireTimers();await settle(f);await sending;
  assert.equal(sends(f).length,0);
  assert.equal(f.box.value,'original');
  assert.match(f.el('status').textContent,/target\/draft changed; check it and Send again/);
});

test('a retarget while the reconnect reply is still in flight cancels the Send',{timeout:20000},async()=>{
  const f=setup();f.box.value='original';
  let release=null;
  f.server.beforeReconnect=()=>new Promise(resolve=>{release=resolve;});
  const sending=f.api.send();await settle(f);
  assert.ok(release,'the reconnect reply is held');
  f.api.invalidateTarget();                                        // a deliberate retarget wins
  release();await settle(f);await sending;
  assert.equal(sends(f).length,0);
  assert.match(f.el('status').textContent,/target\/draft changed; check it and Send again/);
});

test('a retarget during the readiness wait cancels the Send and leaves the new authority alone',{timeout:20000},async()=>{
  const f=setup();f.box.value='must not go to the old conversation';
  const sending=f.api.send();await settle(f);
  const other={...liveSnapshot(),targetRef:ref('O'),sessionRef:ref('o'),scopeRef:ref('p'),title:'Other Manager',executionId:'cc-'+OTHER,uuid:OTHER,generation:1};
  f.api.state.claudeTarget=other;f.api.invalidateTarget();f.api.renderTarget();
  f.server.ready=true;await f.fireTimers();await settle(f);await sending;
  assert.equal(sends(f).length,0);
  assert.equal(f.api.state.claudeTarget.targetRef,other.targetRef);
  assert.equal(f.box.value,'must not go to the old conversation');
  assert.match(f.el('status').textContent,/target\/draft changed; check it and Send again/);
});

test('a retarget away and back during the readiness wait still cancels (revision witness)',{timeout:20000},async()=>{
  const f=setup();f.box.value='original';
  const sending=f.api.send();await settle(f);
  const same={...f.api.state.claudeTarget};f.api.invalidateTarget();f.api.state.claudeTarget=same;   // away/back
  f.server.ready=true;await f.fireTimers();await settle(f);await sending;
  assert.equal(sends(f).length,0);
  assert.match(f.el('status').textContent,/target\/draft changed; check it and Send again/);
});

test('a retarget after the deliberate rebind but before the reply cancels the final Send',{timeout:20000},async()=>{
  const f=setup();f.box.value='original';
  let release=null;
  // Hold only the post-rebind state read: the rebound target is in place when it is issued.
  f.server.beforeState=url=>url.includes('targetRef='+f.server.newRef)&&f.api.state.claudeTarget?.generation===2&&!release
    ?new Promise(resolve=>{release=resolve;}):null;
  const sending=f.api.send();await settle(f);                        // reconnect POST + the initializing readiness read
  f.server.ready=true;await f.fireTimers();await settle(f);          // readiness observed and the rebind done
  assert.ok(release,'the post-rebind state read is held');
  assert.equal(f.api.state.claudeTarget.generation,2);
  f.api.invalidateTarget();                                          // a later deliberate retarget wins
  release();await settle(f);await sending;
  assert.equal(sends(f).length,0);
  assert.match(f.el('status').textContent,/target\/draft changed after reconnect; message not sent/);
});

test('the Manager\'s own turn (native-origin, a permission, unknown state, a queued turn, a native retry) takes Send as one host steer',{timeout:20000},async()=>{
  for(const liveExtra of [{native_session_state:'running'},{native_session_state:'running',primary_turn_open:true},
                          {native_session_state:'running',native_retry_active:true},
                          {native_session_state:'requires_action'},{native_session_state:'unknown',native_state_required:true},
                          {native_queued_turns:1},{pending_native_requests:{req:'permission'}}]){
    const f=setup({target:liveSnapshot(),readyFromStart:true,generation:1});
    f.server.liveExtra=liveExtra;f.box.value='steer draft';
    await f.api.send();
    assert.equal(sends(f).length,1,JSON.stringify(liveExtra));
    assert.equal(sends(f)[0].steer,true);
    assert.equal(f.box.value,'steer draft');                          // cleared only once Claude Code holds it
    assert.match(f.el('status').textContent,/Queued for the busy Manager: it lands at its next tool step/);
  }
});

test('audit #3: a scheduled recovery never refuses Send; the host replaces it, or says it is sending right now',{timeout:20000},async()=>{
  // Idle with a recovery scheduled (a capacity wait can be an hour): one ordinary Send goes; the host cancels the continue.
  // Busy with one: the Send is a steer, as for any busy turn. Neither says "crash".
  for(const [liveExtra,steer] of [[{pending_recovery:'rec-1'},undefined],
                                  [{native_session_state:'running',native_retry_active:true,pending_recovery:'rec-1'},true]]){
    const f=setup({target:liveSnapshot(),readyFromStart:true,generation:1});
    f.server.liveExtra=liveExtra;f.box.value='my message';
    await f.api.send();
    assert.equal(sends(f).length,1,JSON.stringify(liveExtra));
    assert.equal(sends(f)[0].steer,steer);
    assert.doesNotMatch(f.el('status').textContent,/crash|Not sent/);
  }
  // A recovery already sending its continue: the host's own refusal is shown, the draft kept, nothing queued.
  const f=setup({target:liveSnapshot(),readyFromStart:true,generation:1});
  f.server.liveExtra={pending_recovery:'rec-1'};f.box.value='held draft';
  f.server.sendError='busy: an automatic recovery is sending its continue right now; wait a moment';
  await f.api.send();
  assert.equal(sends(f).length,1);
  assert.equal(f.box.value,'held draft');
  assert.equal(f.api.state.claudeSend.refused,true);
  assert.match(f.el('status').textContent,/not submitted: busy: an automatic recovery is sending its continue right now; wait a moment · draft retained/);
  f.server.sendError=null;await f.api.send();                       // a refused Send never holds the next one
  assert.equal(sends(f).length,2);
});

test('Dan: a background subagent or task never blocks Send once the Manager\'s own turn closed',{timeout:20000},async()=>{
  // Observed 2.1.299: native state stays running (and flips to requires_action for the
  // child's auto-answered permissions) after the primary result while the child works.
  for(const liveExtra of [{native_session_state:'running',primary_turn_open:false,background_unsettled:true,
                             background_tasks:{a1:{task_type:'local_agent',description:'Bake-off'}}},
                          {native_session_state:'requires_action',primary_turn_open:false,native_state_required:true,
                             pending_native_requests:{child:{subtype:'can_use_tool',agent_id:'a1'}}}]){
    const f=setup({target:liveSnapshot(),readyFromStart:true,generation:1});
    f.server.liveExtra=liveExtra;f.box.value='talk while it runs';
    await f.api.send();
    assert.equal(sends(f).length,1,JSON.stringify(liveExtra));
    assert.equal(sends(f)[0].submitted,'talk while it runs');
  }
});

test('a reconnect that never becomes writable reports it and sends nothing',{timeout:20000},async()=>{
  const f=setup();f.box.value='patient draft';
  const sending=f.api.send();await settle(f);
  assert.equal(reconnects(f).length,1);assert.equal(sends(f).length,0);
  // The record stays initializing: the wait is a bounded observation, never a queue.
  for(let i=0;i<3;i++){await f.fireTimers();await settle(f);}
  assert.equal(sends(f).length,0);assert.equal(f.box.value,'patient draft');
  assert.equal(f.api.state.sending,true);
  sending.catch(()=>{});                                          // the test ends the wait; the pane keeps observing
});

test('an earlier Send on the same conversation is reconciled before any new UUID; the reconnect stands',{timeout:20000},async()=>{
  const f=setup();
  const old={schemaVersion:1,target:liveSnapshot(),messageUuid:'00000000-0000-4000-8000-000000000001',
    original:'uncertain earlier message',submitted:'uncertain earlier message',frozen:{},acknowledged:false,uncertain:true};
  f.storage.set(f.api.K.claudeSend,JSON.stringify(old));
  f.api.initializeClaudeJournal();
  assert.equal(f.api.state.claudeJournalError,null);
  const earlier=f.api.state.claudeSends.get(EX+'/1');assert.ok(earlier);
  f.api.renderTarget();
  assert.equal(f.el('send-btn').disabled,false);                  // reconnect-only stays available
  f.box.value='new deliberate message';
  const first=f.api.send();await settle(f);f.server.ready=true;await f.fireTimers();await settle(f);await first;
  assert.equal(reconnects(f).length,1);                           // the conversation was reconnected
  assert.equal(sends(f).length,0);                                // but no new message rode it
  assert.ok(f.server.receiptQueries.includes(earlier.messageUuid));
  assert.equal(f.api.state.claudeTarget.generation,2);            // the authority was rebound anyway
  assert.equal(f.box.value,'new deliberate message');
  assert.match(f.el('status').textContent,/the earlier Send .*is not reconciled yet/);
  assert.equal(f.api.state.claudeSends.get(EX+'/1'),earlier);      // the old journal survives intact
  assert.match(f.el('claude-send-receipt').textContent,/Old Manager.*query only; never resend/);
  // Its exact durable receipt resolves (as the resumed replay acknowledges it): one new Send.
  f.server.earlierReceipt={execution_id:EX,generation:1,message_uuid:earlier.messageUuid,
    original:'uncertain earlier message',submitted:'uncertain earlier message',
    durable:true,acknowledged:true,started:false,completed:false,status:'acknowledged'};
  const second=f.api.send();await settle(f);await second;
  assert.equal(sends(f).length,1);
  assert.equal(sends(f)[0].generation,2);
  assert.notEqual(sends(f)[0].messageUuid,earlier.messageUuid);   // never a resend of the earlier operation
  assert.equal(f.api.state.claudeSends.get(EX+'/1'),earlier);
  ack(f);await f.api.pollClaudeManager();assert.equal(sends(f).length,1);
});

test('a live target with an unresolved Send on the same conversation refuses and only queries',{timeout:20000},async()=>{
  const f=setup({target:liveSnapshot(),readyFromStart:true,generation:1});
  const old={schemaVersion:1,target:liveSnapshot(),messageUuid:'00000000-0000-4000-8000-000000000002',
    original:'unresolved live message',submitted:'unresolved live message',frozen:{},acknowledged:false,uncertain:true};
  f.storage.set(f.api.K.claudeSend,JSON.stringify(old));
  f.api.initializeClaudeJournal();
  assert.ok(f.api.state.claudeSends.get(EX+'/1'));
  f.box.value='must not repeat blindly';
  await f.api.send();
  assert.equal(sends(f).length,0);
  assert.equal(reconnects(f).length,0);                           // a live target needs no reconnect
  assert.ok(f.server.receiptQueries.includes(old.messageUuid));   // it queried the same receipt only
  assert.equal(f.box.value,'must not repeat blindly');
  // It says why and names the way through (audit #6) instead of a silent no-op.
  assert.match(f.el('status').textContent,/Not sent — your previous message 00000000 to this conversation has no confirmed delivery yet .*I checked — release it.*never resent/);
  assert.equal(f.el('claude-send-release').hidden,false);
  // Dan's deliberate disposition: recorded on the journal entry, never a resend; the next Send is a new message.
  f.api.releaseClaudeSend();
  const saved=JSON.parse(f.storage.get(f.api.K.claudeSend+':'+EX+'/1'));
  assert.equal(saved.released.by,'operator');
  assert.equal(saved.original,'unresolved live message');           // the original stays recoverable
  assert.equal(f.el('claude-send-release').hidden,true);
  await f.api.send();
  assert.equal(sends(f).length,1);
  assert.notEqual(sends(f)[0].messageUuid,old.messageUuid);
  assert.equal(sends(f)[0].original,'must not repeat blindly');
});

test('audit #6: a fresh unconfirmed Send is not offered for release yet; an unreadable journal is exported, recorded and cleared',{timeout:20000},async()=>{
  const f=setup({target:liveSnapshot(),readyFromStart:true,generation:1});
  const fresh={schemaVersion:1,target:liveSnapshot(),messageUuid:'00000000-0000-4000-8000-000000000003',at:Date.now(),
    original:'just sent',submitted:'just sent',frozen:{},acknowledged:false,uncertain:false,receipt:{durable:true}};
  f.storage.set(f.api.K.claudeSend,JSON.stringify(fresh));
  f.api.initializeClaudeJournal();
  assert.equal(f.api.stuckClaudeSend(),null);
  f.box.value='second';await f.api.send();
  assert.equal(sends(f).length,0);
  assert.match(f.el('status').textContent,/still awaiting its delivery confirmation/);

  const g=setup({target:liveSnapshot(),readyFromStart:true,generation:1});
  g.storage.set(g.api.K.claudeSend,'{"schemaVersion":2,"slots":["not a slot"]}');
  g.api.initializeClaudeJournal();
  assert.ok(g.api.state.claudeJournalError);
  g.api.renderTarget();
  assert.equal(g.el('claude-journal-export').hidden,false);
  assert.equal(g.el('send-btn').disabled,true);
  g.api.exportClearClaudeJournal();
  assert.equal(g.api.state.claudeJournalError,null);
  assert.equal(g.storage.get(g.api.K.claudeSend),undefined);
  const disposition=JSON.parse(g.storage.get(g.api.K.claudeJournalDisposition));
  assert.ok(disposition.reason && disposition.keys.includes(g.api.K.claudeSend));
  const kept=JSON.parse(g.storage.get(disposition.quarantined));
  assert.equal(kept.entries[g.api.K.claudeSend],'{"schemaVersion":2,"slots":["not a slot"]}');   // the original stays recoverable
  g.box.value='now it goes';await g.api.send();
  assert.equal(sends(g).length,1);
});

test('ownership-unknown, worker and unregistered history targets gain no reconnect Send',{timeout:20000},async()=>{
  const cases=[
    sleepingTarget({lifecycle:'pending',state:'ownership_unknown',capabilities:{nativeInput:false,controls:false,reconnect:false}}),
    sleepingTarget({role:'worker',capabilities:{nativeInput:false,controls:false,reconnect:false}}),
    sleepingTarget({source:'windows-history',role:'history',executionId:null,generation:null,capabilities:{nativeInput:false,controls:false}}),
    sleepingTarget({capabilities:{nativeInput:false,controls:false,reconnect:false}}),
  ];
  for(const target of cases){
    const f=setup({target});f.box.value='hands off';
    await f.api.send();
    assert.equal(f.server.calls.length,0,JSON.stringify(target.role||target.lifecycle));
    assert.equal(f.box.value,'hands off');
    assert.equal(f.api.reconnectableManager(target),false);
  }
});

test('a sleeping target enables Send with the sleeping wording; a live one keeps the ordinary path',{timeout:20000},async()=>{
  const f=setup();
  f.api.renderTarget();
  assert.equal(f.el('send-btn').disabled,false);
  assert.match(f.el('backend-status').textContent,/This Manager is sleeping \(reconnects on Send\).*The live Manager is Manager-Session-182\./);
  assert.equal(f.api.reconnectableManager(f.api.state.claudeTarget),true);
  assert.equal(f.api.sleepingManagerText(f.api.state.claudeTarget),'This Manager is sleeping (reconnects on Send). The live Manager is Manager-Session-182.');
  const g=setup({target:liveSnapshot()});
  g.api.renderTarget();
  assert.equal(g.el('send-btn').disabled,false);
  assert.doesNotMatch(g.el('backend-status').textContent,/sleeping/);
  assert.equal(g.api.reconnectableManager(g.api.state.claudeTarget),false);
  assert.equal(g.api.endedManagerText(g.api.state.claudeTarget),'This Manager has ended. The live Manager is Manager-Session-182. Send to it reconnects the conversation.');
});

test('a busy reconnected Manager takes the message as one host steer to the new generation',{timeout:20000},async()=>{
  const f=setup();f.box.value='busy draft';
  const sending=f.api.send();await settle(f);f.server.ready=true;f.server.busy=true;
  await f.fireTimers();await settle(f);await sending;
  assert.equal(sends(f).length,1);
  assert.equal(sends(f)[0].steer,true);
  assert.equal(sends(f)[0].generation,2);
  assert.equal(f.box.value,'busy draft');
  assert.equal(f.api.state.claudeTarget.generation,2);
});

test('a reconnect that finds the same live generation reuses it and sends to it directly',{timeout:20000},async()=>{
  const f=setup({readyFromStart:true,generation:1});f.box.value='same generation';
  const sending=f.api.send();await settle(f);await sending;
  assert.equal(reconnects(f).length,1);                           // the host reuses; no second launch exists here
  assert.equal(sends(f).length,1);
  assert.equal(sends(f)[0].generation,1);
  assert.equal(sends(f)[0].targetRef,f.server.newRef);
  assert.equal(f.api.state.claudeTarget.lifecycle,'live');
});

test('guard sweep: a refused reconnect keeps the host reason on screen with the way out, and the target is not treated as broken',{timeout:20000},async()=>{
  const reason='the running Claude host still uses its old reconnect rule and refuses while protocol_attention is recorded on the ended process. Start a fresh conversation with ＋ New Manager; this one stays in the list.';
  const f=setup();f.box.value='keep me';f.server.reconnectError={status:409,message:reason};
  await f.api.send();
  assert.equal(sends(f).length,0);assert.equal(f.box.value,'keep me');
  assert.match(f.el('status').textContent,/Not sent — Claude would not reconnect Manager-Session-181: the running Claude host still uses its old reconnect rule/);
  assert.match(f.el('status').textContent,/New Manager/);
  assert.equal(f.api.state.claudeTargetError,null);                 // not a broken target: no flash-and-replace
  assert.ok(!f.server.calls.some(c=>c.url.startsWith('/api/claude/sessions')));
  f.api.renderTarget();
  assert.match(f.el('backend-status').textContent,/could not be reconnected: the running Claude host/);
});

test('guard sweep: an ended Manager the host will not reconnect says why instead of promising a reconnect',()=>{
  const f=setup({target:sleepingTarget({lifecycle:'ended',capabilities:{nativeInput:false,controls:false,reconnect:false},
    reconnectBlocked:'its previous Claude process is not proved gone yet (owner \'unknown\', state \'ownership_unknown\'); wait a moment and Send again.'})});
  const text=f.api.endedManagerText(f.api.state.claudeTarget);
  assert.match(text,/Send cannot reconnect it: its previous Claude process is not proved gone yet/);
  assert.doesNotMatch(text,/Send to it reconnects/);
});
