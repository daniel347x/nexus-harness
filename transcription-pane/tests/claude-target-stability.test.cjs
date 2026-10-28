const test=require('node:test'),assert=require('node:assert/strict');
const {pane}=require('./pane-fixture.cjs');
const ref=c=>c.repeat(32),uuid='11111111-1111-4111-8111-111111111111';
const target=()=>({schemaVersion:1,backend:'claude',targetRef:ref('T'),storeRef:ref('S'),sessionRef:ref('J'),scopeRef:ref('M'),
  leafRef:ref('L'),fileEpoch:ref('E'),uuid,leafUuid:null,source:'host',scope:'main',title:'Manager',executionId:'cc-'+uuid,
  generation:1,role:'Manager',capabilities:{nativeInput:true,controls:true}});
const envelope=t=>({...t,status:'complete',events:[],branches:[],diagnostics:[],targetInfo:t});
const response=(data,status=200)=>({ok:status<400,status,text:async()=>JSON.stringify(data)});
const row=t=>({...t,state:'ready',scopes:[{label:'main',scopeRef:t.scopeRef}]});

test('host selection/reload binds session identity despite leaf replacement and transcript growth',async()=>{
  const f=pane(),old=target(),next={...old,leafRef:ref('F'),leafUuid:'22222222-2222-4222-8222-222222222222'};
  f.storage.set(f.api.K.backend,'claude');f.storage.set(f.api.K.claudeTarget,JSON.stringify(old));
  f.ctx.fetch=async()=>response(envelope(next));await f.api.initializeTargetState();
  assert.equal(f.api.state.claudeTarget.targetRef,old.targetRef);assert.equal(f.api.state.claudeTarget.leafRef,next.leafRef);
  assert.equal(f.api.state.claudeTargetError,null);assert.equal(f.el('send-btn').disabled,false);
  assert.equal(f.api.hostSessionMatches(old,next),true);
});
test('specific metadata failure names the field, rather than a generic native identity error',()=>{
  const f=pane(),t=target();
  for(const [field,change]of [['target reference',{targetRef:ref('X')}],['host generation',{generation:0}],['host role',{role:'manager'}],['host execution ID',{executionId:'cc-other'}]]){
    const data=envelope({...t,...change});assert.throws(()=>f.api.validatedClaudeTarget(data,t.targetRef),new RegExp(field));
  }
  assert.throws(()=>f.api.validatedClaudeTarget({...envelope(t),status:'ambiguous'},t.targetRef),/history status ambiguous/);
  const broken=envelope(t);broken.targetInfo={...t,fileEpoch:'wrong'};
  assert.throws(()=>f.api.validatedClaudeTarget(broken,t.targetRef),/fileEpoch response mismatch/);
});
test('indexing host fallback never invalidates Send authority; native targets stay strict',()=>{
  const f=pane(),t=target();
  for(const status of ['indexing']){
    assert.equal(f.api.validatedClaudeTarget({...envelope(t),status},t.targetRef).executionId,t.executionId);
    assert.throws(()=>f.api.validatedClaudeTarget({...envelope({...t,source:'windows-history',leafUuid:uuid}),status},t.targetRef),/history status/);
  }
});
test('host Refine reads fresh leaf context then pins that path while transcript growth leaves Send alone',async()=>{
  const f=pane(),t=target(),context={...t,leafRef:ref('F'),leafUuid:'22222222-2222-4222-8222-222222222222',contextIdentity:ref('C'),
    targetRevision:0,text:'[ASSISTANT]\nfinal prose',bytes:23,turns:1,users:0,readAt:10,status:'stored'};
  context.bytes=Buffer.byteLength(context.text);f.api.state.backend='claude';f.api.state.claudeTarget=t;const frozen=f.api.freezeRefineTarget();
  let changed=false;const requests=[];
  f.ctx.fetch=async url=>{requests.push(url);
    if(url.startsWith('/api/claude/context?'))return response(context);
    const q=new URLSearchParams(url.split('?')[1]);
    assert.equal(q.get('leafRef'),context.leafRef);assert.equal(q.get('fileEpoch'),context.fileEpoch);
    return response(envelope({...t,...(changed?{leafRef:ref('X')}:{leafRef:context.leafRef}),leafUuid:context.leafUuid,contextIdentity:context.contextIdentity}));
  };
  const ctx=await f.api.buildContext(frozen);await f.api.revalidateRefineTarget(frozen,undefined,ctx);
  assert.equal(f.api.state.claudeTarget,t);assert.equal(f.api.isClaudeManager(),true);
  changed=true;await assert.rejects(f.api.revalidateRefineTarget(frozen,undefined,ctx),/source\/path\/epoch changed/);
  assert.ok(requests.every(u=>!u.includes('/send')));
});
test('a live turn moving on never blocks host Refine: the pinned read decides, the boundary row may take its record UUID',async()=>{
  const f=pane(),t=target(),context={...t,leafRef:ref('F'),leafUuid:null,contextIdentity:ref('C'),
    targetRevision:0,text:'[USER]\nquestion',bytes:0,turns:1,users:1,readAt:10,status:'stored'};
  context.bytes=Buffer.byteLength(context.text);f.api.state.backend='claude';f.api.state.claudeTarget=t;const frozen=f.api.freezeRefineTarget();
  let pinned={};
  f.ctx.fetch=async url=>url.startsWith('/api/claude/context?')?response(context):
    response(envelope({...t,leafRef:context.leafRef,leafUuid:context.leafUuid,contextIdentity:context.contextIdentity,...pinned}));
  const ctx=await f.api.buildContext(frozen);
  // The reply in flight at the read finished meanwhile: the frozen path's last row now has a record UUID.
  pinned={leafUuid:'33333333-3333-4333-8333-333333333333'};
  await f.api.revalidateRefineTarget(frozen,undefined,ctx);
  // A different frozen read (or epoch) is still a refusal.
  for(const change of [{contextIdentity:ref('D')},{leafRef:ref('X')}]){
    pinned=change;await assert.rejects(f.api.revalidateRefineTarget(frozen,undefined,ctx),/source\/path\/epoch changed/);
  }
});
test('stale generation stays blocked and one click reselects only the same ready execution, preserving draft/receipts',async()=>{
  const f=pane(),old=target(),fresh={...old,targetRef:ref('N'),sessionRef:ref('K'),generation:2,leafRef:ref('F')};
  f.api.state.backend='claude';f.api.state.claudeTargetHint=old;f.box.value='unsaved dictation 😀';
  f.api.state.claudeSend={target:old,uncertain:true,messageUuid:'retained-operation'};
  const calls=[];f.ctx.fetch=async url=>{calls.push(url);
    if(url==='/api/claude/sessions')return response({schemaVersion:1,backend:'claude',sessions:[{...row(fresh),executionId:'cc-other',uuid:'other'}],hasMore:true,nextCursor:ref('C')});
    if(url.startsWith('/api/claude/sessions?'))return response({schemaVersion:1,backend:'claude',sessions:[row(fresh)],hasMore:false});
    if(url.startsWith('/api/claude/target?sessionRef='))return response(envelope(fresh)); // authority, never a history page
    if(url==='/api/claude/target?targetRef='+fresh.targetRef)return response(envelope(fresh));
    throw new Error('unexpected '+url);
  };
  await f.api.blockClaudeTarget(new Error('host generation changed from 1 to 2'));
  assert.match(f.el('status').textContent,/generation changed from 1 to 2/);assert.equal(f.api.state.claudeTarget,null);
  assert.equal(f.el('claude-reselect').hidden,false);assert.equal(f.el('send-btn').disabled,true);
  await f.api.reselectCurrentManager();
  assert.equal(f.api.state.claudeTarget.executionId,old.executionId);assert.equal(f.api.state.claudeTarget.generation,2);
  const oldReceipt=f.api.state.claudeSends.get(old.executionId+'/1');
  assert.equal(f.box.value,'unsaved dictation 😀');assert.equal(oldReceipt.messageUuid,'retained-operation');
  assert.equal(oldReceipt.uncertain,true);assert.equal(f.api.state.claudeSend,null);assert.equal(f.el('claude-reselect').hidden,true);
  assert.ok(calls.every(url=>!url.includes('/send')&&!url.includes('/control')));
});
test('recovery never offers an exited worker, history row, or unrelated newest Manager',async()=>{
  for(const patch of [{state:'exited'},{role:'worker'},{source:'native'},{executionId:'cc-other'}, {capabilities:{nativeInput:false,controls:false}}]){
    const f=pane(),t=target();f.api.state.backend='claude';f.api.state.claudeTargetHint=t;
    f.ctx.fetch=async()=>response({schemaVersion:1,backend:'claude',sessions:[{...row(t),...patch}],hasMore:false});
    await f.api.blockClaudeTarget(new Error('selected session exited'));
    assert.equal(f.api.state.claudeReselect,null);assert.equal(f.el('claude-reselect').hidden,true);
    assert.match(f.el('status').textContent,/selected session exited/);
  }
});
test('late recovery inventory/reselection cannot override a newer deliberate target or backend change',async()=>{
  const f=pane(),t=target();f.api.state.backend='claude';f.api.state.claudeTargetHint=t;
  let release;f.ctx.fetch=()=>new Promise(r=>release=r);
  const pending=f.api.blockClaudeTarget(new Error('old target invalid'));f.api.invalidateTarget();f.api.state.backend='opencode';
  release(response({schemaVersion:1,backend:'claude',sessions:[row(t)],hasMore:false}));await pending;
  assert.equal(f.api.state.claudeReselect,null);assert.equal(f.api.state.backend,'opencode');
});
test('startup never silently accepts changed generation or capabilities under a saved ref',async()=>{
  for(const patch of [{generation:2},{capabilities:{nativeInput:false,controls:false}}]){
    const f=pane(),t=target();f.storage.set(f.api.K.backend,'claude');f.storage.set(f.api.K.claudeTarget,JSON.stringify(t));
    f.ctx.fetch=async url=>url.startsWith('/api/claude/target?')?response(envelope({...t,...patch})):
      response({schemaVersion:1,backend:'claude',sessions:[],hasMore:false});
    await f.api.initializeTargetState();assert.equal(f.api.state.claudeTarget,null);
    assert.match(f.el('status').textContent,/generation\/role\/capabilities changed/);assert.equal(f.el('send-btn').disabled,true);
  }
});
// U1.1 · Selection and Send authority come from the host's registry state (GET /api/claude/target),
// never from a page of history; Refine's frozen pin is still minted and checked by its own history reads.
const authority=t=>({schemaVersion:1,backend:'claude',targetRef:t.targetRef,sessionRef:t.sessionRef,scopeRef:t.scopeRef,
  status:'authority',source:'host',role:t.role,executionId:t.executionId,generation:t.generation,
  targetInfo:{...t,leafRef:null,fileEpoch:null,leafUuid:null,contextIdentity:null,status:'authority'}});
test('U1.1: selecting a Manager reads its registry authority only, never a history page, and Send is ready at once',async()=>{
  const f=pane(),t=target(),requests=[];f.api.state.backend='claude';
  f.ctx.fetch=async url=>{requests.push(url);
    if(url==='/api/claude/target?targetRef='+t.targetRef)return response(authority(t));
    throw new Error('a selection must not read history: '+url);};
  const accepted=await f.api.acceptClaudeTarget(t.targetRef,f.api.state.targetRevision,()=>true);
  assert.deepEqual(requests,['/api/claude/target?targetRef='+t.targetRef]);
  assert.equal(accepted.executionId,t.executionId);assert.equal(accepted.generation,1);assert.equal(f.api.isClaudeManager(),true);
  for(const k of ['leafRef','fileEpoch','leafUuid','contextIdentity'])assert.equal(accepted[k],null,k);
  assert.equal(f.el('send-btn').disabled,false);assert.equal(f.el('refine-btn').disabled,false);
});
test('U1.1: the authority shape is host-only and never carries history fields; a native target keeps its history read',async()=>{
  const f=pane(),t=target();
  assert.equal(f.api.validatedClaudeTarget(authority(t),t.targetRef).leafRef,null);
  for(const k of ['leafRef','fileEpoch','leafUuid','contextIdentity']){const data=authority(t);data.targetInfo[k]=ref('Z');
    assert.throws(()=>f.api.validatedClaudeTarget(data,t.targetRef),/Claude target validation failed/,k);}
  const native=authority({...t,source:'native'});assert.throws(()=>f.api.validatedClaudeTarget(native,t.targetRef),/history status authority/);
  // A handle the facade does not own (native history) is answered through its history read, as before.
  const requests=[];f.api.state.backend='claude';
  f.ctx.fetch=async url=>{requests.push(url);
    if(url.startsWith('/api/claude/target?'))return response({schemaVersion:1,backend:'claude',status:'history-target',error:'not a host session handle'},409);
    return response(envelope(t));};
  assert.equal((await f.api.acceptClaudeTarget(t.targetRef,f.api.state.targetRevision,()=>true)).leafRef,t.leafRef);
  assert.deepEqual(requests,['/api/claude/target?targetRef='+t.targetRef,'/api/claude/events?targetRef='+t.targetRef]);
});
test('U1.1: Refine from an authority-only target still freezes and re-checks its context through the pinned history read',async()=>{
  const f=pane(),t={...target(),leafRef:null,fileEpoch:null,leafUuid:null,contextIdentity:null};
  const context={...t,leafRef:ref('F'),fileEpoch:ref('E'),leafUuid:null,contextIdentity:ref('C'),
    targetRevision:0,text:'[USER]\nquestion',bytes:0,turns:1,users:1,readAt:10,status:'stored'};
  context.bytes=Buffer.byteLength(context.text);f.api.state.backend='claude';f.api.state.claudeTarget=t;const frozen=f.api.freezeRefineTarget();
  const pins=[];let moved=false;
  f.ctx.fetch=async url=>{if(url.startsWith('/api/claude/context?'))return response(context);
    assert.ok(url.startsWith('/api/claude/events?'),url);const q=new URLSearchParams(url.split('?')[1]);pins.push([q.get('leafRef'),q.get('fileEpoch')]);
    return response(envelope({...t,leafRef:context.leafRef,fileEpoch:context.fileEpoch,contextIdentity:moved?ref('D'):context.contextIdentity}));};
  const ctx=await f.api.buildContext(frozen);await f.api.revalidateRefineTarget(frozen,undefined,ctx);
  assert.deepEqual(pins,[[context.leafRef,context.fileEpoch]]);
  moved=true;await assert.rejects(f.api.revalidateRefineTarget(frozen,undefined,ctx),/source\/path\/epoch changed/);
});
