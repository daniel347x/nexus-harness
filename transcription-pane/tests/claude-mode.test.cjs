const test = require('node:test');
const assert = require('node:assert/strict');
const {pane} = require('./pane-fixture.cjs');
const ref = c => c.repeat(32);
function hostTarget() {
  return {schemaVersion:1,backend:'claude',targetRef:ref('T'),storeRef:ref('S'),sessionRef:ref('J'),scopeRef:ref('M'),
    leafRef:ref('L'),fileEpoch:ref('E'),uuid:'11111111-1111-4111-8111-111111111111',leafUuid:null,
    source:'host',scope:'main',title:'Registered Manager',executionId:'cc-11111111-1111-4111-8111-111111111111',
    generation:1,role:'Manager',capabilities:{nativeInput:false,controls:false}};
}
const envelope = t => ({...t,status:'complete',targetInfo:t,events:[],diagnostics:[],branches:[]});

test('same-page Claude embed is lazy and retained; switches do not touch editor, settings or inspection',async()=>{
  const f=pane(), classes=new Set(), attrs=new Map(), loads=[];
  f.ctx.document.body={classList:{toggle(name,on){on?classes.add(name):classes.delete(name);}}};
  Object.assign(f.el('claude-sessions-frame'),{getAttribute:name=>attrs.get(name),setAttribute(name,value){attrs.set(name,value);loads.push(value);}});
  f.box.value='unsaved dictation 😀'; f.el('inspector-input').textContent='held exact record';
  f.storage.set('refine_think_level','high');f.storage.set('pane_target_mode','follow');
  f.api.renderTarget();assert.equal(loads.length,0);assert.equal(f.el('claude-viewer').hidden,true);
  f.api.setBackend('claude');assert.equal(classes.has('claude-mode'),true);assert.deepEqual(loads,['/sessions?embedded=1']);
  assert.equal(f.el('send-btn').disabled,true);
  f.api.setBackend('opencode');assert.equal(classes.has('claude-mode'),false);assert.equal(f.el('claude-viewer').hidden,true);
  f.api.setBackend('claude');assert.equal(loads.length,1);
  assert.equal(f.box.value,'unsaved dictation 😀');assert.equal(f.el('inspector-input').textContent,'held exact record');
  assert.equal(f.storage.get('refine_think_level'),'high');assert.equal(f.storage.get('pane_target_mode'),'follow');
});

test('host read-only target requires exact native execution and generation/capability metadata, never a title',()=>{
  const f=pane(), t=hostTarget();
  assert.equal(f.api.validatedClaudeTarget(envelope(t),t.targetRef).executionId,t.executionId);
  for(const change of [{executionId:'ses_false'},{generation:0},{generation:1.5},{role:'manager'},{capabilities:{nativeInput:true,controls:false}},
    {capabilities:{nativeInput:false,controls:true}},{source:'unknown'}]) {
    const bad={...t,...change};assert.throws(()=>f.api.validatedClaudeTarget(envelope(bad),t.targetRef));
  }
  for(const role of ['Manager','conductor','worker','helper','base']) {
    const actual={...t,role};assert.equal(f.api.validatedClaudeTarget(envelope(actual),t.targetRef).role,role);
  }
});

test('all Claude roles refuse Send and watcher updates cannot retarget; return to OpenCode still sends',async()=>{
  const f=pane(); f.box.value='original'; f.ctx.fetch=async url=>{
    f.requests.push([url]);
    if(url==='/api/active-session')return {ok:true,json:async()=>({sessionId:'ses_open',title:'OpenCode',revision:1,age_s:0})};
    if(url==='/oc/api/session/ses_open')return {ok:true,json:async()=>({data:{title:'OpenCode',agent:'manager'}})};
    if(url==='/oc/api/session/active')return {ok:true,json:async()=>({data:{}})};
    if(url==='/api/send')return {ok:true,json:async()=>({data:{id:'sent'},thinking:'unchanged'})};
    throw new Error('Unexpected '+url);
  };
  f.api.state.backend='claude';
  for(const role of ['Manager','conductor','worker','helper','base','history']){
    f.api.state.claudeTarget={...hostTarget(),role};await f.api.send();assert.equal(f.box.value,'original');
  }
  const t=f.api.state.claudeTarget;await f.api.pollWatcher();assert.equal(f.api.state.claudeTarget,t);
  assert.equal(f.api.state.target,null);assert.equal(f.requests.length,0);
  f.api.state.watcher={sessionId:'ses_open',title:'OpenCode',revision:1,age_s:0}; // retained OpenCode observation from before mode switch
  f.api.setBackend('opencode');for(let i=0;i<30&&!f.api.state.target?.agent;i++)await Promise.resolve();
  await f.api.send();assert.equal(f.box.value,'');assert.equal(f.requests.filter(([url])=>url==='/api/send').length,1);
});

test('host context echoes source/execution/generation and frozen targets reject an away/back generation change',async()=>{
  const f=pane(), t=hostTarget();f.api.state.backend='claude';f.api.state.claudeTarget=t;
  const frozen=f.api.freezeRefineTarget();
  f.api.state.claudeTarget={...t,generation:2};assert.equal(f.api.sameRefineTarget(frozen),false);
  f.api.state.claudeTarget=t;
  const prose='[USER]\ngenuine 😀\n\n---\n\n[ASSISTANT]\nonly prose';
  for(const change of [{},{generation:2},{executionId:'cc-other'},{source:'native'}]){
    f.ctx.fetch=async()=>({ok:true,text:async()=>JSON.stringify({...t,...change,status:'stored',targetRevision:0,
      contextIdentity:ref('C'),text:prose,bytes:Buffer.byteLength(prose),turns:2,users:1,readAt:Date.now()})});
    if(Object.keys(change).length)await assert.rejects(f.api.buildContext(frozen),/identity/);
    else assert.equal((await f.api.buildContext(frozen)).text,prose);
  }
});
