const test=require('node:test'), assert=require('node:assert/strict');
const {pane}=require('./pane-fixture.cjs');
const A='ses_managerAAAA',B='ses_managerBBBB',W='ses_workerCCCCC';
const model={providerID:'openai',id:'gpt-fixture',variant:'high'};
const tick=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r))};
function setup({storage,lose=false,missing=false,defer=false}={}) {
  const f=pane({storage,response:{content:[{type:'text',text:'Refined fixture'}],stop_reason:'end_turn'}}), provider=f.ctx.fetch;
  const calls=[],records=new Map(),states=new Map([A,B].map((sid,i)=>[sid,{schemaVersion:1,backend:'opencode',sessionId:sid,
    target:{backend:'opencode',sessionId:sid,witness:(i?'b':'a').repeat(64)},title:'Manager '+sid,agent:'manager',model:{...model},busy:true,
    capabilities:{send:true,control:true,context:true},context:{usedTokens:1234,window:872000,basis:'input + cache read + cache write + output + reasoning',measuredAt:1000}}]));
  const server={calls,records,states,lose,missing,defer,release:null}; let serial=0;
  const response=(data,status=200)=>{const raw=JSON.stringify(data);return {ok:status<400,status,json:async()=>JSON.parse(raw),text:async()=>raw}};
  f.ctx.fetch=async(url,opts={})=>{
    if(url.startsWith('https://'))return provider(url,opts);
    const u=new URL(url,'http://fixture'),path=u.pathname,q=u.searchParams,body=opts.body&&JSON.parse(opts.body);
    calls.push({url,path,body});
    if(path==='/api/opencode/manager')return response(states.get(q.get('id')) || {error:'not a Manager'},states.has(q.get('id'))?200:409);
    if(path==='/api/opencode/manager-models'||path==='/api/opencode/launch-defaults')return response({schemaVersion:1,backend:'opencode',agents:['manager','manager-gpt'],models:[{...model,variants:{low:{},high:{},max:{}}}],mcp:[{name:'workflowy',status:'connected'}]});
    if(path==='/api/opencode/context'){
      const target=states.get(q.get('id')).target,text='[USER]\nWhole genuine prose 😀\n\n---\n\n[ASSISTANT]\nAnswer';
      return response({schemaVersion:1,backend:'opencode',target,text,bytes:new TextEncoder().encode(text).length,historyMode:q.get('historyMode'),users:1,
        historyBoundaries:[{id:'msg_one',end:28},{id:'msg_two',end:text.length}]});
    }
    if(path==='/api/opencode/intent'){
      const nativeId=(body.kind==='launch'?'ses_':'msg_')+(++serial).toString(16).padStart(12,'0')+'ABCDEFGHIJKLMN';
      const record={schemaVersion:1,backend:'opencode',operationId:body.operationId,kind:body.kind,intent:body,nativeId,state:'prepared',acknowledged:false};records.set(body.operationId,record);return response(record);
    }
    if(['/api/opencode/send','/api/opencode/control','/api/opencode/launch-manager'].includes(path)){
      const record=records.get(body.operationId);
      if(record.kind==='send'){record.state='pending';record.acknowledged=true;if(server.defer)await new Promise(r=>server.release=r);}
      else if(record.kind==='control'){
        record.state=record.intent.operation==='interrupt'?'accepted':'applied';record.acknowledged=record.state==='applied';
        if(record.intent.model)states.get(record.intent.target.sessionId).model=record.intent.model;
      }else{
        const sid=record.nativeId,state={...states.get(A),target:{backend:'opencode',sessionId:sid,witness:'c'.repeat(64)},sessionId:sid,title:record.intent.name,model:record.intent.model};
        states.set(sid,state);Object.assign(record,{state:'created',acknowledged:true,target:state.target,sent:false});
      }
      if(server.lose)throw Error('reply lost after native admission');return response(record);
    }
    if(path==='/api/opencode/receipt')return server.missing?response({error:'receipt read unavailable'},502):response(records.get(q.get('operationId')));
    throw Error('unexpected '+url);
  };
  const draft=value=>{f.box.value=value;f.api.draftChanged();f.api.saveDraft()};
  const select=async(sid=A)=>{await f.api.acceptOpenCodeManager(sid);await tick()};
  return {f,server,draft,select};
}
test('desktop OpenCode Manager Send works without watcher while busy, with full exact original and unchanged clear',async()=>{
  const {f,server,draft,select}=setup();await select();const original=' \n'+('Long 😀 Ω\n'.repeat(50000))+'TAIL  ';draft(original);
  await f.api.send();const posts=server.calls.filter(c=>c.path==='/api/opencode/send');assert.equal(posts.length,1);
  const r=server.records.get(posts[0].body.operationId);assert.equal(r.intent.original,original);assert.equal(r.intent.submitted,original.trim());assert.equal(r.intent.delivery,'steer');
  assert.equal(f.box.value,'');assert.ok(!server.calls.some(c=>c.path.startsWith('/oc')||c.path==='/api/send'));
});
test('delayed A receipt neither clears nor holds B; away/back target revisions retain identical draft',async()=>{
  const {f,server,draft,select}=setup({defer:true});await select();draft('keep same text');const sending=f.api.send();await tick();
  assert.equal(typeof server.release,'function');await select(B);draft('B draft');server.release();await sending;assert.equal(f.box.value,'B draft');
  assert.equal(f.api.openCodeManagerApi().pending(f.api.state.ocManagerTarget),null);
  server.defer=false;await f.api.send();assert.equal(server.calls.filter(c=>c.path==='/api/opencode/send').length,2);
});
test('edited-away-and-back text cannot clear on delayed native ACK',async()=>{
  const {f,server,draft,select}=setup({defer:true});await select();draft('same');const p=f.api.send();await tick();draft('other');draft('same');server.release();await p;assert.equal(f.box.value,'same');
});
test('lost reply and reload only query the saved operation; desktop reload never clears its draft',async()=>{
  const {f,server,draft,select}=setup({lose:true,missing:true});await select();draft('reload draft');await f.api.send();
  assert.equal(f.box.value,'reload draft');assert.ok(f.api.openCodeManagerApi().pending(f.api.state.ocManagerTarget));
  server.missing=false;f.api.state.pageEpoch='different page';await f.api.pollOpenCodeManager();await f.api.pollOpenCodeManager();
  assert.equal(server.calls.filter(c=>c.path==='/api/opencode/send').length,1);assert.equal(f.box.value,'reload draft');
});
test('OpenCode model/thinking applies while busy and Interrupt is one intent, with draft untouched',async()=>{
  const {f,server,draft,select}=setup();await select();draft('control draft');f.el('opencode-variant').value='max';
  await f.api.controlOpenCodeManager('set_model');await f.api.controlOpenCodeManager('interrupt');
  const controls=server.calls.filter(c=>c.path==='/api/opencode/control');assert.equal(controls.length,2);
  assert.equal(server.records.get(controls[0].body.operationId).intent.model.variant,'max');assert.equal(f.box.value,'control draft');
});
test('guarded Refine context on desktop honors whole/budgeted/no-context without /oc',async()=>{
  const {f,server,draft,select}=setup();await select();draft('rough dictation');
  const whole=await f.api.buildContext(f.api.freezeRefineTarget());assert.match(whole.text,/Whole genuine prose/);
  f.storage.set('refine_history_mode','budgeted');await f.api.buildContext(f.api.freezeRefineTarget());
  assert.match(server.calls.filter(c=>c.path==='/api/opencode/context').at(-1).url,/historyMode=budgeted.*budgetBytes=51200/);
  f.storage.set('pane_claude_no_context_v1','true');const before=server.calls.filter(c=>c.path==='/api/opencode/context').length;
  await f.api.refine();assert.equal(f.box.value,'Refined fixture');assert.equal(server.calls.filter(c=>c.path==='/api/opencode/context').length,before);
});
test('worker cannot become Manager target and a saved full sid reopens without any list window',async()=>{
  const {f,server,select}=setup();await select();await assert.rejects(()=>f.api.acceptOpenCodeManager(W),/not a Manager/);
  assert.equal(f.api.state.ocManagerTarget.sessionId,A);
  const prior=JSON.parse(f.storage.get('pane_manager_target_v1'));f.api.state.ocManagerTarget=null;
  await f.api.acceptOpenCodeManager(prior.target.sessionId,f.api.state.targetRevision,()=>true,true,prior.target);
  assert.equal(f.api.state.ocManagerTarget.sessionId,A);assert.equal(server.calls.filter(c=>c.path.includes('programs')).length,0);
});
