const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const {pane, streamResponse, messageEvents} = require('./pane-fixture.cjs');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = app.indexOf('async function resolveTarget() {');
const end = app.indexOf('\nfunction renderTarget()', start);
assert.ok(start >= 0 && end > start);

test('late followed-session GET cannot overwrite a newer manual target', async () => {
  let mode = 'follow', release;
  const state = { target: null, watcher: { sessionId: 'ses_followed', title: 'followed', age_s: 1 } };
  const ctx = {
    state, ls: { get: () => mode }, K: { targetMode: 'mode' },
    oc: () => new Promise(resolve => { release = resolve; }),
    renderTarget() {}, refreshContext() {}, $: () => ({}),
    invalidateTarget() { state.targetRevision = (state.targetRevision || 0) + 1; },
  };
  vm.createContext(ctx); vm.runInContext(app.slice(start, end), ctx);
  const pending = ctx.resolveTarget();
  mode = 'ses_manual'; state.target = { id: 'ses_manual', title: 'manual' };
  release({ data: { title: 'old followed title', agent: 'manager' } }); await pending;
  assert.equal(state.target.id, 'ses_manual'); assert.equal(state.target.title, 'manual');
});
test('late session GET cannot overwrite a target replaced during the request', async () => {
  let release;
  const state = { target: null, watcher: { sessionId: 'ses_first', age_s: 1 } };
  const ctx = {
    state, ls: { get: () => 'follow' }, K: { targetMode: 'mode' },
    oc: () => new Promise(resolve => { release = resolve; }),
    renderTarget() {}, refreshContext() {}, $: () => ({}),
    invalidateTarget() { state.targetRevision = (state.targetRevision || 0) + 1; },
  };
  vm.createContext(ctx); vm.runInContext(app.slice(start, end), ctx);
  const pending = ctx.resolveTarget(); state.target = { id: 'ses_second', title: 'newer' };
  release({ data: { title: 'older' } }); await pending;
  assert.equal(state.target.id, 'ses_second'); assert.equal(state.target.title, 'newer');
});

const ref = char => char.repeat(32);
function nativeTarget(targetRef = ref('A'), uuid = '11111111-1111-4111-8111-111111111111') {
  const identity = {storeRef:ref('S'),sessionRef:ref('J'),scopeRef:ref('M'),leafRef:ref('L'),fileEpoch:ref('E')};
  return {schemaVersion:1,backend:'claude',status:'complete',targetRef,...identity,events:[{text:'not retained'}],
    targetInfo:{schemaVersion:1,backend:'claude',targetRef,...identity,uuid,leafUuid:uuid,scope:'main',title:'Native history',source:'windows-history',bindingRef:'never-persist-this'}};
}
function targets(options = {}) {
  const f = pane(options), posted = [], channels = [];
  f.ctx.BroadcastChannel = class {
    constructor(name) { this.name = name; channels.push(this); }
    postMessage(m) { posted.push(JSON.parse(JSON.stringify(m))); }
  };
  vm.runInContext('this.targetAPI = {invalidateTarget,setBackend,resolveTarget,pollWatcher,renderTarget,refreshContext,acceptClaudeTarget,handleTargetMessage,initializeTargetState,validatedClaudeTarget};', f.ctx);
  f.api = {...f.api,...f.ctx.targetAPI};
  const origin='http://127.0.0.1:9911', child={postMessage:m=>posted.push(JSON.parse(JSON.stringify(m)))};
  f.ctx.location={origin};f.el('claude-sessions-frame').contentWindow=child;
  const receive=f.api.handleTargetMessage;
  // Retained target-race tests now arrive through the authorized embedded frame.
  f.api.handleTargetMessage=event=>receive(event.data.type==='target' && !event.source ? {...event,source:child,origin} : event);
  f.ctx.fetch = async url => ({ok:true,text:async()=>JSON.stringify(nativeTarget(new URL(url,'http://fixture').searchParams.get('targetRef')))});
  f.api.state.editorId = 'editor-fixture';
  f.api.state.targetChannel = {postMessage:m=>posted.push(JSON.parse(JSON.stringify(m)))};
  const message = (actionRevision = 1, targetRef = ref('A'), targetRevision = f.api.state.targetRevision) =>
    ({schemaVersion:1,type:'target',clientId:'history',editorId:f.api.state.editorId,actionId:'action-'+actionRevision,actionRevision,targetRevision,targetRef});
  return {...f, posted, channels, message, receive, child, origin};
}
test('BroadcastChannel targets, standalone windows and wrong origins never retarget an editor',async()=>{
  const f=targets();
  for(const event of [{data:f.message()},{data:f.message(),source:{},origin:f.origin},
    {data:f.message(),source:f.child,origin:'http://other'}])await f.receive(event);
  assert.equal(f.api.state.claudeTarget,null);assert.equal(f.posted.length,0);assert.equal(f.api.state.targetRevision,0);
  await f.receive({data:f.message(),source:f.child,origin:f.origin});assert.equal(f.api.state.targetRevision,1);
});
const response = data => ({ok:true,text:async()=>JSON.stringify(data)});
test('explicit validated native target has full server identity, ACK, versioned keys and no saved binding or event bodies', async () => {
  const f = targets(); f.box.value = 'draft remains'; f.api.state.ctx = {text:'old'};
  await f.api.handleTargetMessage({data:f.message()});
  assert.equal(f.posted.at(-1).applied,true); assert.equal(f.api.state.backend,'claude');
  assert.equal(f.api.state.targetRevision,1); assert.equal(f.api.state.ctx,null);
  assert.equal('bindingRef' in f.api.state,false); assert.equal(f.api.state.claudeTarget.bindingRef,null);
  const saved = JSON.parse(f.storage.get('pane_claude_target_v1'));
  assert.equal(saved.uuid,nativeTarget().targetInfo.uuid); assert.equal(saved.fileEpoch,ref('E'));
  assert.equal(saved.events,undefined); assert.equal(saved.text,undefined); assert.equal(saved.bindingRef,null);
  assert.equal(f.storage.get('pane_backend_v1'),'claude'); assert.equal(f.box.value,'draft remains');
  assert.equal(f.el('send-btn').disabled,true); assert.equal(f.el('refine-btn').disabled,false);
  // Round 9a: the badge shows the name; the UUID and source are on hover.
  assert.match(f.el('target-pill').textContent,/Claude.*Native history.*read-only/);
  assert.doesNotMatch(f.el('target-pill').textContent,/11111111/);
  assert.match(f.el('target-pill').title,/11111111[\s\S]*windows-history/);
  assert.match(f.el('cache-info').textContent,/—/);
});
test('target channel presence is explicit, schema bounded, duplicate actions rejected and absent editor never applies a target', async () => {
  const f = targets();
  await f.api.handleTargetMessage({data:{schemaVersion:1,type:'hello',clientId:'history',requestId:'hello-1'}});
  assert.equal(f.posted[0].type,'presence'); assert.equal(f.posted[0].editorId,'editor-fixture');
  await f.api.handleTargetMessage({data:{...f.message(),editorId:'absent-editor'}});
  assert.equal(f.api.state.targetRevision,0); assert.equal(f.posted.length,1);
  await f.api.handleTargetMessage({data:{...f.message(),body:'forbidden'}});
  assert.equal(f.posted.length,1);
  await f.api.handleTargetMessage({data:f.message()});
  await f.api.handleTargetMessage({data:f.message()});
  assert.equal(f.posted.at(-1).applied,false); assert.match(f.posted.at(-1).reason,/old action/);
});
test('two real target validations complete out-of-order: only newer explicit action ACKs applied', async () => {
  const f = targets(), pending = [];
  f.ctx.fetch = url => new Promise(resolve=>pending.push({url,resolve}));
  const old = f.api.handleTargetMessage({data:f.message(1,ref('A'))});
  const newer = f.api.handleTargetMessage({data:f.message(2,ref('B'))});
  pending[1].resolve(response(nativeTarget(ref('B')))); await newer;
  pending[0].resolve(response(nativeTarget(ref('A')))); await old;
  assert.equal(f.api.state.claudeTarget.targetRef,ref('B'));
  assert.deepEqual(f.posted.map(m=>m.applied),[true,false]);
  assert.equal(f.api.state.targetRevision,1);
});
test('earlier validation completes before newer request: newer request requires fresh pane presence/revision', async () => {
  const f = targets();
  await f.api.handleTargetMessage({data:f.message(1,ref('A'),0)});
  await f.api.handleTargetMessage({data:f.message(2,ref('B'),0)});
  assert.equal(f.posted.at(-1).applied,false); assert.equal(f.api.state.claudeTarget.targetRef,ref('A'));
  await f.api.handleTargetMessage({data:f.message(3,ref('B'),1)});
  assert.equal(f.posted.at(-1).applied,true); assert.equal(f.api.state.claudeTarget.targetRef,ref('B'));
});
test('native validation versus pane explicit backend A→B→A is rejected even with same final backend', async () => {
  const f = targets(); let release;
  f.ctx.fetch = () => new Promise(resolve=>{release=resolve;});
  const p = f.api.handleTargetMessage({data:f.message()});
  f.api.setBackend('claude'); f.api.setBackend('opencode');
  const revision = f.api.state.targetRevision;
  release(response(nativeTarget())); await p;
  assert.equal(f.api.state.backend,'opencode'); assert.equal(f.api.state.targetRevision,revision);
  assert.equal(f.api.state.claudeTarget,null); assert.equal(f.posted.at(-1).applied,false);
});
test('target storage failure and HTTP/source discriminator mismatch do not claim application', async () => {
  for (const mode of ['storage','http','backend','epoch','scope','oversize']) {
    const f = targets(), data = nativeTarget();
    if (mode === 'storage') f.failWrites();
    if (mode === 'backend') data.backend = 'opencode';
    if (mode === 'epoch') data.targetInfo.fileEpoch = ref('X');
    if (mode === 'scope') data.targetInfo.scopeRef = ref('X');
    f.ctx.fetch = async () => mode === 'http' ? {ok:false,status:409,text:async()=>JSON.stringify({error:'stale epoch'})} :
      mode === 'oversize' ? {ok:true,text:async()=> 'x'.repeat(8*1024*1024+1)} : response(data);
    await f.api.handleTargetMessage({data:f.message()});
    assert.equal(f.posted.at(-1).applied,false,mode); assert.equal(f.api.state.backend,'opencode',mode);
    assert.equal(f.api.state.claudeTarget,null,mode); assert.equal(f.api.state.targetRevision,0,mode);
  }
});
test('reload revalidates saved opaque target and discards saved binding; stale saved target stays visibly blocked', async () => {
  for (const stale of [false,true]) {
    const storage = new Map([['pane_backend_v1','claude'],['pane_claude_target_v1',JSON.stringify({...nativeTarget().targetInfo,bindingRef:'old-binding',nativeArm:true})]]);
    const f = targets({storage}); let reads = 0;
    f.ctx.fetch = async () => { reads++; return stale ? {ok:false,status:409,text:async()=>JSON.stringify({error:'expired'})} : response(nativeTarget()); };
    await f.api.initializeTargetState();
    assert.equal(reads,1); assert.equal('bindingRef' in f.api.state,false); assert.equal('nativeArm' in f.api.state,false);
    assert.equal(f.channels[0].name,'pane-target-v1'); assert.equal(f.api.state.backend,'claude');
    assert.equal(f.api.state.claudeTarget === null,stale);
    assert.match(f.el('target-pill').textContent,stale ? /blocked.*expired/ : /read-only/);
  }
});
test('CodeNomad heartbeat/context read completing inside Claude mode cannot update native target or cache', async () => {
  const f = targets(); await f.api.handleTargetMessage({data:f.message()});
  const before = JSON.stringify(f.api.state.claudeTarget), revision = f.api.state.targetRevision;
  f.ctx.fetch = async url => {assert.equal(url,'/api/active-session'); return {json:async()=>({sessionId:'ses_other',title:'other',model:{id:'changed'}})};};
  await f.api.pollWatcher();
  assert.equal(f.api.state.watcher,null); // Claude polling is independent of OpenCode services.
  await f.api.resolveTarget(); // explicit native refresh is now a U3 context operation, not a watcher effect
  assert.equal(JSON.stringify(f.api.state.claudeTarget),before); assert.equal(f.api.state.targetRevision,revision);
  assert.equal(f.api.state.target,null); assert.equal(f.api.state.ctx,null); assert.match(f.el('cache-info').textContent,/—/);
  let finish;
  f.api.setBackend('opencode');
  f.api.state.target = {id:'ses_context'};
  f.ctx.buildContext = () => new Promise(resolve=>{finish=resolve;});
  const context = f.api.refreshContext(); f.api.setBackend('claude');
  finish({sid:'ses_context',text:'old',bytes:3,turns:1,users:1,at:new Date()}); await context;
  assert.equal(f.api.state.ctx,null); assert.match(f.el('cache-info').textContent,/—/);
});
test('unselected native required-context Refine and unqualified Send have no provider/OpenCode traffic or recovery side effects', async () => {
  const f = targets(); f.api.state.backend = 'claude'; f.box.value = 'full retained';
  f.ctx.fetch = () => {throw new Error('must not route native UUID to OpenCode/provider');};
  await f.api.refine(); await f.api.send();
  assert.equal(f.box.value,'full retained'); assert.equal(f.storage.has('pane_originals_v1'),false);
  assert.equal(f.requests.length,0); assert.match(f.el('status').textContent,/unavailable/);
});
test('target change during actual pending OpenCode provider response invalidates application and preserves original', async () => {
  const f = pane({responseHook:()=>new Promise(resolve=>{release=resolve;})}); let release;
  vm.runInContext('this.targetAPI={invalidateTarget,setBackend};', f.ctx);
  f.box.value = 'original full';
  const pending = f.api.refine();
  for(let i=0;i<10&&!release;i++) await Promise.resolve();
  assert.ok(release);
  f.ctx.targetAPI.setBackend('claude'); f.ctx.targetAPI.setBackend('opencode');
  // Deliberately ignore AbortSignal: revision guard must still stop stale output.
  release(streamResponse(messageEvents({content:[{type:'text',text:'late replacement'}],stop_reason:'end_turn'})));
  await pending;
  assert.equal(f.box.value,'original full'); assert.equal(f.api.readOriginals()[0].text,'original full');
  assert.equal(f.copies.includes('late replacement'),false);
});
test('all four inlined Manager and manager-gpt decisions survive source integration', () => {
  assert.equal(app.split('\n').filter(line=>line.includes("['manager', 'manager-gpt'].includes(")).length,4);
  const f = targets(); f.api.state.target = {id:'ses_manager_gpt',title:'GPT Manager',agent:'manager-gpt'};
  f.api.renderTarget(); assert.match(f.el('target-pill').textContent,/🛰️/); assert.doesNotMatch(f.el('target-pill').textContent,/⚠/);
});
test('no OpenCode session: a watcher that reports but recognises none says CodeNomad may have changed, never "silent"', () => {
  const f = targets(), pill = () => f.el('target-pill').textContent; f.api.state.target = null;
  f.api.state.watcher = {sessionId:null,title:null,thinking:null,age_s:2};
  f.api.renderTarget(); assert.match(pill(),/sees none it recognises.*CodeNomad may have changed/); assert.doesNotMatch(pill(),/silent/);
  for (const watcher of [null, {sessionId:null,age_s:120}, {sessionId:null,age_s:null}]) {
    f.api.state.watcher = watcher; f.api.renderTarget(); assert.match(pill(),/CodeNomad watcher silent/);
  }
});
test('manager-gpt actual Send never asks the not-Manager confirmation', async () => {
  const f = pane(); f.api.state.target={id:'ses_gpt',title:'GPT Manager',agent:'manager-gpt'};
  f.box.value='literal whole draft'; f.ctx.confirm=()=>{throw new Error('unexpected Manager confirmation');};
  await f.api.send();
  assert.equal(f.requests.some(([url])=>url==='/api/send'),true); assert.equal(f.box.value,'');
});
test('late OpenCode metadata for A cannot overwrite a new explicit A after A→B→A', async () => {
  const f = targets(), pending=[];
  f.ctx.oc = () => new Promise(resolve=>pending.push(resolve));
  f.ctx.refreshContext=()=>{};
  f.storage.set('pane_target_mode','ses_A');
  const old=f.api.resolveTarget();
  f.storage.set('pane_target_mode','ses_B'); f.api.invalidateTarget(); f.api.state.target=null;
  const b=f.api.resolveTarget();
  f.storage.set('pane_target_mode','ses_A'); f.api.invalidateTarget(); f.api.state.target=null;
  const a=f.api.resolveTarget();
  pending[2]({data:{title:'new A',agent:'manager'}}); await a;
  pending[0]({data:{title:'old A',agent:'wrong'}}); await old;
  pending[1]({data:{title:'late B'}}); await b;
  assert.equal(f.api.state.target.title,'new A'); assert.equal(f.api.state.target.agent,'manager');
});
