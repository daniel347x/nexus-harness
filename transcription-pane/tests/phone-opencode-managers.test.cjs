// Phone OpenCode Managers: the shared server-journal client and the phone's draft-clear evidence rules, offline.
// Real static/opencode-manager.js and phone static/m.js in a Node VM; a fake pane route family only (no live pane,
// OpenCode, inference, credentials or browser). The UI scenarios are in phone-opencode-managers.html.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {createHash, webcrypto}=require('node:crypto');

const SHARED=fs.readFileSync(path.join(__dirname,'../static/opencode-manager.js'),'utf8');
const PHONE=fs.readFileSync(path.join(__dirname,'../static/m.js'),'utf8');
const ORDER=fs.readFileSync(path.join(__dirname,'../static/manager-order.js'),'utf8');
const SID_A='ses_'+'a'.repeat(12)+'A'.repeat(14);
const SID_B='ses_'+'b'.repeat(12)+'B'.repeat(14);
const WITNESS_A='a'.repeat(64), WITNESS_B='b'.repeat(64), WITNESS_C='c'.repeat(64);
const ALNUM='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';

class Storage {
  constructor(){ this.map=new Map(); }
  get length(){ return this.map.size; }
  key(i){ return [...this.map.keys()][i] ?? null; }
  getItem(k){ return this.map.has(k) ? this.map.get(k) : null; }
  setItem(k,v){ this.map.set(k,String(v)); }
  removeItem(k){ this.map.delete(k); }
}

const reply=(status,data)=>({ok:status < 400,status,json:async()=>data});
const envelope=values=>({schemaVersion:1,backend:'opencode',...values});
const hash=parts=>createHash('sha256').update(JSON.stringify(parts)).digest('hex');

function fakePane(){
  const ops=new Map(), sessions=new Map(), messages=new Map();
  let seq=0;
  const calls={intent:0,send:0,receipt:0,create:0,model:0,interrupt:0,context:0,manager:0};
  const control={lossSend:false,lossLaunch:false,conflictNext:false,busy:false};
  const id=kind=>{ seq++; const time=String(seq).padStart(12,'0'); const tail=String(seq).padStart(14,'A').split('').map((c,i)=>ALNUM[(seq*7+i)%ALNUM.length]).join('');
    return (kind==='launch' ? 'ses_' : 'msg_')+time+tail; };
  const addSession=(sid,title='Manager',extra={})=>{
    const s={id:sid,title,agent:extra.agent || 'manager',model:extra.model || {providerID:'openrouter',id:'deepseek/flash',variant:'medium'},
      location:{directory:'/w/manager-sessions'},time:{created:1700000000000+seq},...extra};
    sessions.set(sid,s); return s;
  };
  const targetFor=sid=>{ const s=sessions.get(sid); return {backend:'opencode',sessionId:sid,witness:hash([sid,s.time.created,s.location.directory])}; };
  const box=sid=>{ if(!messages.has(sid)) messages.set(sid,{inbox:[],history:[]}); return messages.get(sid); };
  const prompt=(sid,item)=>{ const b=box(sid); (control.busy ? b.inbox : b.history).push(item); };
  const findItem=(sid,mid)=>{ const b=box(sid), pending=b.inbox.find(i=>i.id===mid), delivered=b.history.find(i=>i.id===mid);
    return pending ? ['pending',pending] : delivered ? ['delivered',delivered] : null; };
  const matches=(found,rec)=>{ const item=found[1], intent=rec.intent;
    return item.id===rec.nativeId && item.sessionID===intent.target.sessionId && item.type==='user' &&
      (item.payload?.text ?? item.text)===intent.submitted; };
  const enrich=rec=>{
    if(!rec) return null;
    if(rec.kind==='send'){
      if(rec.state==='prepared') return rec;
      const found=findItem(rec.intent.target.sessionId,rec.nativeId);
      if(!found){ if(!rec.acknowledged) rec.state='uncertain'; return rec; }
      if(!matches(found,rec)){ rec.state='conflict'; rec.acknowledged=false; rec.detail='Native id has a different body/target/type'; return rec; }
      rec.state=found[0]; rec.acknowledged=true; rec.detail='Exact native '+found[0]+' evidence'; return rec;
    }
    if(rec.kind==='launch'){
      const s=sessions.get(rec.nativeId); if(!s) return rec;
      if(s.agent===rec.intent.agent && JSON.stringify(s.model)===JSON.stringify(rec.intent.model) &&
         s.location.directory==='/w/manager-sessions' && s.title===rec.intent.name){
        rec.state='created'; rec.acknowledged=true; rec.target=targetFor(s.id);
      } else rec.state='conflict';
      return rec;
    }
    if(rec.intent.operation==='set_model'){
      const s=sessions.get(rec.intent.target.sessionId); if(!s) return rec;
      rec.readback=s.model; rec.acknowledged=JSON.stringify(s.model)===JSON.stringify(rec.intent.model);
      rec.state=rec.acknowledged ? 'applied' : 'uncertain'; return rec;
    }
    return rec;
  };
  const handle=async(url,options={})=>{
    const u=new URL(String(url),'http://pane.test'), route=u.pathname.replace('/api/opencode','');
    const q=Object.fromEntries(u.searchParams), body=options.body ? JSON.parse(options.body) : null;
    const prior=body?.operationId ? ops.get(body.operationId) : null;
    if(route==='/intent' && options.method==='POST'){
      calls.intent++;
      if(prior){ if(JSON.stringify(prior.intent)!==JSON.stringify(body)) return reply(409,envelope({error:'conflict'})); return reply(200,envelope(prior)); }
      if(!['send','launch','control'].includes(body.kind)) return reply(400,envelope({error:'kind'}));
      if(body.kind!=='launch' && !(body.target && /^[a-f0-9]{64}$/.test(body.target.witness || ''))) return reply(400,envelope({error:'target'}));
      const rec={operationId:body.operationId,intent:body,kind:body.kind,state:'prepared',
        nativeId:body.kind==='control' ? null : id(body.kind),acceptedAt:1,acknowledged:false};
      ops.set(body.operationId,rec); return reply(200,envelope(rec));
    }
    if(route==='/send' && options.method==='POST'){
      if(!prior || prior.kind!=='send') return reply(404,envelope({error:'missing'}));
      if(prior.state!=='prepared') return reply(200,envelope(enrich(prior)));
      calls.send++; prior.state='accepted';
      const text=control.conflictNext ? 'A different body' : prior.intent.submitted;
      control.conflictNext=false;
      prompt(prior.intent.target.sessionId,{id:prior.nativeId,sessionID:prior.intent.target.sessionId,type:'user',
        delivery:prior.intent.delivery,text,payload:{text}});
      if(control.lossSend){ control.lossSend=false; return reply(503,envelope({error:'lost reply'})); }
      return reply(200,envelope(enrich(prior)));
    }
    if(route==='/launch-manager' && options.method==='POST'){
      if(!prior || prior.kind!=='launch') return reply(404,envelope({error:'missing'}));
      if(prior.state!=='prepared') return reply(200,envelope(enrich(prior)));
      calls.create++;
      addSession(prior.nativeId,prior.intent.name,{agent:prior.intent.agent,model:prior.intent.model});
      if(control.lossLaunch){ control.lossLaunch=false; prior.state='uncertain'; return reply(503,envelope({error:'lost reply'})); }
      return reply(200,envelope(enrich(prior)));
    }
    if(route==='/control' && options.method==='POST'){
      if(!prior || prior.kind!=='control') return reply(404,envelope({error:'missing'}));
      if(prior.state!=='prepared') return reply(200,envelope(enrich(prior)));
      const s=sessions.get(prior.intent.target.sessionId); if(!s) return reply(409,envelope({error:'not-manager'}));
      if(prior.intent.operation==='set_model'){ calls.model++; s.model=prior.intent.model; }
      else { calls.interrupt++; prior.state='accepted'; prior.interrupted=true; prior.repliedAt=2; }
      return reply(200,envelope(enrich(prior)));
    }
    if(route==='/receipt'){
      calls.receipt++;
      const rec=ops.get(q.operationId);
      return rec ? reply(200,envelope(enrich(rec))) : reply(404,envelope({error:'missing'}));
    }
    if(route==='/manager'){
      calls.manager++;
      const s=sessions.get(q.id); if(!s) return reply(409,envelope({error:'not-manager'}));
      return reply(200,envelope({target:targetFor(s.id),sessionId:s.id,title:s.title,agent:s.agent,model:s.model,
        directory:s.location.directory,createdAt:s.time.created,busy:false,observedAt:1700000001,
        capabilities:{send:true,control:true,context:true},
        context:{status:'measured',basis:'input + cache read + cache write + output + reasoning',usedTokens:1234,window:872000,measuredAt:1699999999}}));
    }
    if(route==='/manager-models'){
      const s=sessions.get(q.id); if(!s) return reply(409,envelope({error:'not-manager'}));
      return reply(200,envelope({target:targetFor(s.id),models:[
        {providerID:'openrouter',id:'deepseek/flash',name:'Flash',variants:{low:{},medium:{},max:{}},limit:{context:872000},enabled:true}]}));
    }
    if(route==='/launch-defaults') return reply(200,envelope({models:[
      {providerID:'openrouter',id:'deepseek/flash',name:'Flash',variants:{low:{},medium:{},max:{}},limit:{context:872000},enabled:true}],
      agents:['manager','manager-gpt'],agent:'manager',mcp:[{name:'workflowy',status:'connected'}],sent:false}));
    if(route==='/context'){
      calls.context++;
      const s=sessions.get(q.id); if(!s) return reply(409,envelope({error:'not-manager'}));
      const text='[USER] one\n\n---\n\n[ASSISTANT] two', mode=q.historyMode || 'whole';
      return reply(200,envelope({target:targetFor(s.id),text,bytes:Buffer.byteLength(text,'utf8'),historyMode:mode,users:1,
        historyBoundaries:[{id:'m1',end:9},{id:'m2',end:text.length}],contextHash:'x'}));
    }
    if(route==='/programs') return reply(200,envelope({status:'complete',sessions:[],observedAt:1700000001}));
    return reply(404,envelope({error:'unknown '+route}));
  };
  addSession(SID_A,'Manager A');
  return {handle,calls,control,ops,sessions,messages,addSession,targetFor};
}

function clientFor(server){
  const storage=new Storage(), requests=[];
  const fetch=async(url,opts={})=>{ requests.push([String(url),opts.method || 'GET']); return server.handle(url,opts); };
  const ctx={console,URL,URLSearchParams,TextEncoder,TextDecoder,AbortController,DOMException,crypto:webcrypto,
    localStorage:storage,fetch,setTimeout,clearTimeout,setInterval:()=>0,clearInterval(){}};
  vm.createContext(ctx);
  vm.runInContext(SHARED,ctx);
  return {client:ctx.opcOpenCodeManager.create({prefix:'pane_m_v1:manager',storage,fetch}),storage,requests};
}

const witness=server=>server.targetFor(SID_A).witness;
const sendFields=(server,text='Hello exactly.',target=server.targetFor(SID_A))=>({target,original:text,submitted:text.trim(),delivery:'steer'});
const localWitness=(sid,extra={})=>({page:'page-1',selectionRevision:1,draftRevision:1,selected:sid,backend:'opencode',sessionId:sid,...extra});

test('the shared client saves the full intent before one POST; a lost reply only polls that operation', async()=>{
  const server=fakePane(), {client,storage}=clientFor(server);
  const original='Dictated original with snow 雪 and no crop ✓';
  const record=await client.begin('send',sendFields(server,original),localWitness(SID_A));
  assert.equal(server.calls.intent,1);
  assert.equal(server.calls.send,0);
  const saved=JSON.parse(storage.getItem('pane_m_v1:manager:opencode:send:'+SID_A+':'+record.operationId));
  assert.equal(saved.intent.original,original);
  assert.equal(saved.intent.submitted,original.trim());
  assert.match(record.nativeId,/^msg_[a-f0-9]{12}[A-Za-z0-9]{14}$/);
  server.control.lossSend=true;
  const lost=await client.execute(record);
  assert.equal(server.calls.send,1);          // one upstream POST only
  assert.ok(server.calls.receipt>0);          // the loss was queried on the same operation
  assert.equal(lost.intent.original,original);
  assert.equal(lost.receipt.state,'delivered');
  assert.equal(client.acknowledged(lost),true);
});

test('a first admission with a different body is a visible conflict, never acknowledged', async()=>{
  const server=fakePane(), {client}=clientFor(server);
  server.control.conflictNext=true;
  const record=await client.begin('send',sendFields(server,'Mine'),localWitness(SID_A));
  const out=await client.execute(record);
  assert.equal(out.receipt.state,'conflict');
  assert.equal(client.acknowledged(out),false);
  assert.match(out.receipt.detail,/different/i);
});

test('a busy Manager keeps the native steer pending; release retains the original and clears the hold', async()=>{
  const server=fakePane(), {client,storage}=clientFor(server);
  server.control.busy=true;
  const record=await client.begin('send',sendFields(server,'Steering now'),localWitness(SID_A));
  const out=await client.execute(record);
  assert.equal(out.receipt.state,'pending');
  assert.equal(out.receipt.acknowledged,true); // the exact inbox item is durable evidence
  assert.equal(client.acknowledged(out),true);
  const released=client.release(out);
  assert.ok(released.releasedAt);
  assert.equal(client.pending(server.targetFor(SID_A)),null);
  assert.equal(JSON.parse(storage.getItem('pane_m_v1:manager:opencode:send:'+SID_A+':'+record.operationId)).intent.original,'Steering now');
});

test('journal slots are target-qualified: A can neither hold nor satisfy B, and a lost launch keeps one session id', async()=>{
  const server=fakePane(), {client}=clientFor(server);
  server.addSession(SID_B,'Manager B');
  const targetA=server.targetFor(SID_A), targetB=server.targetFor(SID_B);
  const a=await client.begin('send',sendFields(server,'A text',targetA),localWitness(SID_A));
  const b=await client.begin('send',sendFields(server,'B text',targetB),localWitness(SID_B,{selectionRevision:2,draftRevision:2}));
  assert.equal(client.pending(targetA).operationId,a.operationId);
  assert.equal(client.pending(targetB).operationId,b.operationId);
  assert.equal(client.entries('send',targetA).length,1);
  assert.equal(client.entries('send',targetA)[0].operationId,a.operationId);

  server.control.lossLaunch=true;
  const fields={name:'Manager-Session-501',agent:'manager',model:{providerID:'openrouter',id:'deepseek/flash',variant:'medium'}};
  const intent=await client.begin('launch',fields,{page:'page-1',selectionRevision:1,draftRevision:0,selected:null});
  const created=await client.execute(intent);
  assert.equal(server.calls.create,1);
  assert.equal(created.receipt.state,'created');
  assert.equal(client.acknowledged(created),true);
  assert.equal(created.nativeId,intent.nativeId);
  await client.execute(intent); // a repeated execute reads the receipt; it never creates again
  assert.equal(server.calls.create,1);
});

test('model and interrupt setters have one intent each and their receipts never repeat the native request', async()=>{
  const server=fakePane(), {client}=clientFor(server);
  const model=await client.begin('control',{target:server.targetFor(SID_A),operation:'set_model',model:{providerID:'openrouter',id:'deepseek/flash',variant:'max'}},
    localWitness(SID_A));
  const applied=await client.execute(model);
  assert.equal(applied.receipt.state,'applied');
  assert.equal(applied.receipt.acknowledged,true);
  assert.deepEqual(applied.receipt.readback,{providerID:'openrouter',id:'deepseek/flash',variant:'max'});
  const before=server.calls.model;
  await client.execute(model);
  assert.equal(server.calls.model,before);
  const stop=await client.begin('control',{target:server.targetFor(SID_A),operation:'interrupt',model:null},localWitness(SID_A));
  const stopped=await client.execute(stop);
  assert.equal(stopped.receipt.interrupted,true);
  const interrupts=server.calls.interrupt;
  await client.execute(stop);
  assert.equal(server.calls.interrupt,interrupts);
});

test('guarded Refine context carries whole or explicit budgeted history and exact target identity', async()=>{
  const server=fakePane(), {client}=clientFor(server);
  const whole=await client.context(server.targetFor(SID_A),'whole',51200);
  assert.equal(whole.historyMode,'whole');
  assert.ok(whole.text.includes('[USER]'));
  assert.equal(whole.bytes,new TextEncoder().encode(whole.text).length);
  assert.equal(whole.turns,2);
  const budgeted=await client.context(server.targetFor(SID_A),'budgeted',51200);
  assert.equal(budgeted.historyMode,'budgeted');
  await assert.rejects(()=>client.context({...server.targetFor(SID_A),witness:WITNESS_C},'whole',51200));
});

// ---- the phone's own rules, loaded from the real m.js with a null DOM and its start() never run.
function phoneRules(server){
  const storage=new Storage(), requests=[];
  const fetch=async(url,opts={})=>{ requests.push([String(url),opts.method || 'GET']); return server.handle(url,opts); };
  const ctx={console,URL,URLSearchParams,TextEncoder,TextDecoder,AbortController,DOMException,crypto:webcrypto,
    localStorage:storage,fetch,setTimeout,clearTimeout,setInterval:()=>0,clearInterval(){}};
  ctx.document={getElementById:()=>null,addEventListener(){},createElement:()=>({style:{},dataset:{},classList:{toggle(){},add(){},remove(){}},append(){},replaceChildren(){},setAttribute(){},addEventListener(){}})};
  ctx.navigator={clipboard:{writeText:async()=>{}}};
  ctx.location={href:'http://pane.test/m',search:'',origin:'http://pane.test'};
  ctx.performance={now:()=>Date.now()};
  vm.createContext(ctx);
  vm.runInContext('globalThis.window=globalThis; window.matchMedia=()=>({matches:false,addEventListener(){},addListener(){}});',ctx);
  vm.runInContext(SHARED,ctx);
  vm.runInContext(ORDER,ctx);
  vm.runInContext(PHONE.replace(/\nphoneManager\.start\(\);\s*$/,'\n')+'\nglobalThis.__phone=phoneManager;',ctx);
  return {ctx,api:ctx.__phone,storage,requests};
}

test('the phone keys a target by backend + native id and never forges Claude execution/generation fields', ()=>{
  const server=fakePane(), {api}=phoneRules(server);
  assert.equal(api.rules.backendOf({source:'host',role:'Manager',executionId:'cc-x'}),'claude');
  assert.equal(api.rules.backendOf({backend:'opencode',sessionId:SID_A}),'opencode');
  assert.equal(api.rules.mid({executionId:'cc-x'}),'cc-x');
  assert.equal(api.rules.mid({backend:'opencode',sessionId:SID_A}),SID_A);
  const target=api.rules.ocTarget({backend:'opencode',sessionId:SID_A,witness:WITNESS_A});
  assert.equal(target.backend,'opencode');
  assert.equal(target.sessionId,SID_A);
  assert.equal(target.witness,WITNESS_A);
  assert.equal(api.rules.ocTarget({executionId:'cc-x'}),null);
  assert.equal(api.rules.createdMs({backend:'opencode',createdAt:1700000000}),1700000000000);
  assert.equal(api.rules.createdMs({backend:'opencode',createdAt:1700000000000}),1700000000000);
  assert.equal(api.rules.createdMs({backend:'claude',createdAt:'2023-11-14T22:13:20Z'}),1700000000000);
});

// The chip strip sorts with the desktop's Manager Sessions comparator (manager-order.js): by Manager-Session number,
// highest first, each primary followed by its sub-sessions (the newest primary is always the leftmost chip), Claude and
// OpenCode together; a live chip never moves ahead.
test('the Manager chips: by Manager-Session number, each primary before its sub-sessions, both backends, liveness ignored', ()=>{
  const server=fakePane(), {api}=phoneRules(server);
  const U=n=>String(n).padStart(8,'0')+'-0000-4000-8000-000000000000';
  const claude=(n,name,{live=false,createdAt})=>({backend:'claude',source:'host',role:'Manager',uuid:U(n),executionId:'cc-'+U(n),generation:1,
    targetRef:'target-'+n,sessionLabel:name,title:name || U(n),createdAt,lifecycle:live?'live':'sleeping',
    capabilities:live?{nativeInput:true,controls:true,reconnect:true}:{nativeInput:false,controls:false,reconnect:true}});
  const oc=(tail,title,createdAt)=>({backend:'opencode',id:'ses_mgr'+tail.padEnd(12,'0'),sessionId:'ses_mgr'+tail.padEnd(12,'0'),kind:'manager',manager:true,title,createdAt});
  const rows=[claude(1,'Manager-Session-216',{live:true,createdAt:1792900000}),oc('217e','Manager-Session-217-e',1793019043),
    claude(2,'Manager-Session-218',{live:true,createdAt:1793021400}),claude(3,'Manager-Session-217-a',{createdAt:1793011800}),
    oc('216a','Manager-Session-216-a',1792972587),claude(4,'Manager-Session-217',{live:true,createdAt:1792985400}),
    claude(5,'Manager-Session-218-a',{live:true,createdAt:1793021700}),oc('216b','Manager-Session-216-b',1792977368),
    claude(6,null,{live:true,createdAt:1792009516}),oc('157a','Manager Session 157-a Setup',1791585648)];
  const sorted=[...rows].sort(api.rules.managerOrder).map(r=>(r.backend==='opencode'?'oc ':'cc ')+(r.sessionLabel || r.title));
  assert.deepEqual(sorted,['cc Manager-Session-218','cc Manager-Session-218-a','cc Manager-Session-217',
    'cc Manager-Session-217-a','oc Manager-Session-217-e','cc Manager-Session-216','oc Manager-Session-216-a','oc Manager-Session-216-b',
    'oc Manager Session 157-a Setup','cc '+U(6)]);
  // ↳ New sub-session reads the same names: the newest primary 218 already has 218-a, so 218-b.
  const ctxOrder=api.rules.managerNames && rows.map(api.rules.managerNames);
  assert.equal(phoneRules(server).ctx.opcManagerOrder.nextSub(ctxOrder).name,'Manager-Session-218-b');
});

test('an exact OpenCode ACK clears only the unchanged same target and the exact persisted original', ()=>{
  const server=fakePane(), {api}=phoneRules(server);
  const target={backend:'opencode',sessionId:SID_A,witness:WITNESS_A};
  const intent={schemaVersion:1,backend:'opencode',operationId:'op-1',kind:'send',target,
    original:'Exact text 雪',submitted:'Exact text 雪',delivery:'steer'};
  const nativeId='msg_'+'a'.repeat(12)+'B'.repeat(14);
  const record={operationId:'op-1',kind:'send',phase:'observed',intent,nativeId,page:'p1',selectionRevision:1,draftRevision:1,
    selectionEpoch:1,selected:SID_A,
    receipt:{schemaVersion:1,backend:'opencode',operationId:'op-1',kind:'send',intent,nativeId,state:'delivered',acknowledged:true}};
  const boot={text:'Exact text 雪',owner:SID_A,draftRevision:1,selectionRevision:1,selectionEpoch:1};
  const snap=extra=>({currentTarget:target,selected:SID_A,page:'p1',selectionRevision:1,draftRevision:1,selectionEpoch:1,
    draftValue:'Exact text 雪',saved:{target:SID_A,text:'Exact text 雪',draftRevision:1},boot,...extra});
  assert.equal(api.rules.ocDraftClearable(record,snap()),true);
  assert.equal(api.rules.ocDraftClearable(record,snap({draftRevision:2,draftValue:'Edited'})),false);
  assert.equal(api.rules.ocDraftClearable(record,snap({selected:SID_B,currentTarget:{backend:'opencode',sessionId:SID_B,witness:WITNESS_B}})),false);
  assert.equal(api.rules.ocDraftClearable(record,snap({currentTarget:{backend:'opencode',sessionId:SID_A,witness:WITNESS_B}})),false);
  assert.equal(api.rules.ocDraftClearable(record,snap({saved:{target:SID_B,text:'Exact text 雪',draftRevision:1}})),false);
  // Reload: only the exact boot-restored draft, unchanged selection and unchanged since boot may clear.
  assert.equal(api.rules.ocDraftClearable(record,snap({page:'p2'})),true);
  assert.equal(api.rules.ocDraftClearable(record,snap({page:'p2',draftValue:'Edited'})),false);
  assert.equal(api.rules.ocDraftClearable(record,snap({page:'p2',selectionEpoch:2})),false);
  assert.equal(api.rules.ocDraftClearable(record,snap({page:'p2',selectionRevision:2})),false);
  assert.equal(api.rules.ocDraftClearable(record,snap({page:'p2',draftRevision:2,draftValue:'Exact text 雪'})),false);
  assert.equal(api.rules.ocDraftClearable(record,snap({page:'p2',saved:{target:SID_A,text:'Exact text 雪',draftRevision:2}})),false);
  assert.equal(api.rules.ocDraftClearable(record,snap({page:'p2',saved:{target:SID_A,text:'Different'}})),false);
  assert.equal(api.rules.ocDraftClearable({...record,receipt:{...record.receipt,state:'conflict',acknowledged:false}},snap()),false);
  assert.equal(api.rules.ocDraftClearable({...record,receipt:{...record.receipt,state:'pending'}},snap()),true);
  assert.equal(api.rules.ocDraftClearable({...record,releasedAt:1},snap()),false);
  const bound={...record,savedDraftToken:'draft-write-A',selectionMarker:'selection-A'};
  const boundSnap=snap({selectionMarker:'selection-A',storedSelectionMarker:'selection-A',saved:{...snap().saved,writer:'p1',revisionToken:'draft-write-A'}});
  assert.equal(api.rules.ocDraftClearable(bound,boundSnap),true);
  assert.equal(api.rules.ocDraftClearable(bound,{...boundSnap,storedSelectionMarker:'other-tab-away-and-back'}),false);
  assert.equal(api.rules.ocDraftClearable(bound,{...boundSnap,saved:{...boundSnap.saved,revisionToken:'other-tab-edit-away-and-back'}}),false);
  assert.equal(api.rules.ocDraftClearable(bound,{...boundSnap,saved:{...boundSnap.saved,writer:'another-active-page'}}),false);
});

test('control evidence distinguishes applied, pending and a one-shot interrupt; no-context is an explicit choice', ()=>{
  const server=fakePane(), {api,storage}=phoneRules(server);
  assert.equal(api.rules.ocControlSettled({intent:{operation:'set_model'},receipt:{state:'applied'}}),true);
  assert.equal(api.rules.ocControlSettled({intent:{operation:'set_model'},receipt:{state:'accepted'}}),false);
  assert.equal(api.rules.ocControlSettled({intent:{operation:'interrupt'},receipt:{state:'accepted',interrupted:false}}),true);
  assert.equal(api.rules.ocControlSettled({intent:{operation:'interrupt'},receipt:{state:'uncertain'}}),false);
  assert.match(api.rules.ocControlNote({intent:{operation:'set_model',model:{providerID:'p',id:'m'}},
    receipt:{state:'applied',readback:{providerID:'p',id:'m',variant:'high'}}}),/applied and verified.*future calls/);
  storage.setItem('pane_m_v1:no_context','true');
  assert.equal(api.rules.noContext(),true);
  storage.setItem('pane_m_v1:no_context','false');
  assert.equal(api.rules.noContext(),false);
});
