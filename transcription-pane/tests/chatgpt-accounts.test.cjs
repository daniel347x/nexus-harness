// Offline VM/DOM checks of the real GPT controller. Every IO and timer is injected.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/chatgpt-accounts.js'), 'utf8');
const STORE = 'pane_chatgpt_account_switch_v1';
const A = 'openai:fixture+A', B = 'openai:fixture/B?two';
const LABEL_A = 'Family GPT · 雪', LABEL_B = 'Studio <not markup> & research';
const NOW = 1792900000000;
const tick = () => new Promise(resolve => setImmediate(resolve));
const copy = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };

function element(attrs = {}) {
  return {attrs:{...attrs},textContent:'',title:'',className:'',children:[],listeners:{},disabled:false,hidden:false,open:false,
    setAttribute(k,v) { this.attrs[k]=String(v); }, getAttribute(k) { return this.attrs[k] ?? null; },
    addEventListener(k,fn) { this.listeners[k]=fn; }, append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children=nodes; },
    get text() { return this.textContent+this.children.map(n=>n.text).join('\n'); }};
}
function panel() {
  const parts = Object.fromEntries(['current','accounts','viewing','switch','details','status','enrollment','plan','quotas','quota-status','refresh','banked'].map(k=>[k,element()]));
  const root = {...element(),querySelector:selector=>parts[selector.slice(10,-1)] || null};
  return {root,...parts,button:id=>parts.accounts.children.find(n=>n.getAttribute('data-gpt-account')===id)};
}
function status({active=A,known=true,operation=null,available=true,rows,error=null} = {}) {
  return {schemaVersion:1,backend:'chatgpt-accounts',available,error,mapping:{configured:true},operation,
    active:{known,credential_id:known?active:null,credential_label:active===A?LABEL_A:LABEL_B,detail:known?'Native public readback':'Native selection unknown'},
    accounts:rows || [{id:A,label:LABEL_A,method:'chatgpt-token-sharing',active:active===A,quota:{state:'mapped',label:'fixture-a',authPresent:true}},
      {id:B,label:LABEL_B,method:'chatgpt-token-sharing',active:active===B,quota:{state:'unmapped',label:null,authPresent:false}}]};
}
function receipt(phase='pending', credentialId=B, id='gpt-operation-1') {
  return {id,credentialId,label:credentialId===A?LABEL_A:LABEL_B,phase,detail:'Fixture native selection '+phase,
    attempted:phase!=='pending',startedAt:1,updatedAt:2,finishedAt:['pending','attempting'].includes(phase)?null:3,activeAtFinish:credentialId};
}
function quota(id=A, overrides={}) {
  return {schemaVersion:1,backend:'chatgpt-usage',credentialId:id,quotaState:'mapped',now:NOW/1000,observedAt:NOW/1000-70,
    ageSeconds:70,refreshing:false,retryAt:null,error:null,chatgpt:{ok:true,account:{label:id===A?'fixture-a':'fixture-b',opencode_credential_id:id,
      active:id===A,binding:'Operator mapping only; not independent identity verification.'},plan:id===A?'Fixture Pro':'Fixture Plus',
      windows:[{label:'weekly',percent:id===A?39:72,resets_at:NOW/1000+7200}],models:{Astra:{available:true,available_at:null}},
      reset_credits:[{expires_at:NOW/1000+3600,granted_at:NOW/1000-10,title:'Fixture grant'}]},...overrides};
}
function boot({body=status(),storage=new Map(),route,quotaRoute}={}) {
  const ui=panel(), requests=[],events=[],timers=new Map(); let clock=NOW,seq=0;
  const document={readyState:'complete',hidden:false,querySelectorAll:selector=>selector==='[data-chatgpt-accounts]'?[ui.root]:[],
    createElement:()=>element(),addEventListener(){},dispatchEvent:e=>events.push(e)};
  ui.root.ownerDocument=document;
  class Clock extends Date { static now() { return clock; } }
  const context={window:{crypto:{randomUUID:()=> 'gpt-operation-1'}},document,Date:Clock,
    sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)},
    CustomEvent:function(type,init) { this.type=type;this.detail=init.detail; },
    setTimeout:(fn,ms)=>{const id=++seq;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id),
    fetch:async(url,options={})=>{
      const request={url,method:options.method||'GET',body:options.body?JSON.parse(options.body):null};requests.push(request);
      const u=new URL(url,'http://offline.invalid'); let data;
      if(u.pathname==='/api/chatgpt/usage') data=quotaRoute?await quotaRoute(u.searchParams.get('credentialId'),request):quota(u.searchParams.get('credentialId'));
      else data=route?await route(request):typeof body==='function'?body():body;
      if(data instanceof Error)throw data;
      const answer=data?.httpStatus?data:{httpStatus:200,data};
      return {ok:answer.httpStatus>=200&&answer.httpStatus<300,status:answer.httpStatus,json:async()=>copy(answer.data)};
    }};
  vm.createContext(context);vm.runInContext(source,context);
  const owner=context.window.OCChatGPTAccounts;
  return {ui,owner,api:owner.get(ui.root),requests,events,storage,timers,
    advance:seconds=>clock+=seconds*1000,fire:async(maxMs=5000)=>{const entry=[...timers].find(([,t])=>t.ms<=maxMs);if(entry){timers.delete(entry[0]);await entry[1].fn();}await tick();}};
}
const posts = page => page.requests.filter(r=>r.method==='POST');

test('auto mount is idempotent and arbitrary native IDs and labels are text, not fixed personal/work roles',async()=>{
  const p=boot();await tick();
  assert.equal(p.owner.mount(p.ui.root),p.api);assert.equal(p.ui.accounts.children.length,2);
  assert.equal(p.ui.button(A).textContent,LABEL_A+' · active');assert.equal(p.ui.button(B).textContent,LABEL_B);
  assert.equal(p.ui.current.textContent,LABEL_A);assert.equal(p.ui.button(A).getAttribute('data-active'),'true');
  assert.equal(p.ui.button(A).getAttribute('aria-pressed'),'true');assert.equal(p.ui.switch.disabled,true);
  assert.equal(posts(p).length,0);
});

test('view buttons only read the chosen quota; viewed/active states and optional operator binding stay separate',async()=>{
  const p=boot();await tick();p.ui.button(B).listeners.click();await tick();
  assert.equal(posts(p).length,0);assert.equal(p.api.snapshot().shown,B);assert.equal(p.api.snapshot().status.active.credential_id,A);
  assert.equal(p.ui.button(B).getAttribute('aria-pressed'),'true');assert.equal(p.ui.button(B).getAttribute('data-active'),'false');
  assert.match(p.ui.viewing.textContent,/NOT active/);assert.match(p.ui.quotas.text,/72% used/);
  assert.match(p.ui.quotas.text,/Operator mapping only; not independent identity verification/);
  assert.match(p.ui.banked.text,/Banked resets · Studio/);assert.equal(p.ui.plan.textContent,'Fixture Plus');assert.equal(p.ui.switch.disabled,false);
  assert.ok(p.requests.some(r=>r.url==='/api/chatgpt/usage?credentialId='+encodeURIComponent(B)));
  assert.ok(p.requests.every(r=>!r.url.includes('refresh=1')));
  p.ui.refresh.listeners.click();await tick();assert.ok(p.requests.some(r=>r.url.endsWith('&refresh=1')));
});

for(const quotaState of ['unmapped','invalid','unverified'])test(quotaState+' quota never disables a valid native Switch or claims zero banked resets',async()=>{
  const p=boot({quotaRoute:id=>quota(id,{quotaState,ageSeconds:null,chatgpt:{ok:false,error:'Optional quota '+quotaState,account:{opencode_credential_id:id}}})});
  await tick();p.api.viewAccount(B);await tick();
  assert.match(p.ui.quotas.text,/Quota unknown/);assert.match(p.ui.banked.text,/unknown · not a verified zero/);
  assert.equal(p.ui.switch.disabled,false);assert.equal(posts(p).length,0);
});

test('stale/unknown quota and exhausted percentages disclose history but never gate the switch',async()=>{
  const q=quota(B,{ageSeconds:900,error:'Last provider read failed',retryAt:NOW/1000+600});
  q.chatgpt={...q.chatgpt,ok:false,stale:true,windows:[{label:'weekly',percent:100,resets_at:NOW/1000+3600}]};
  const p=boot({quotaRoute:id=>id===B?q:quota(id)});await tick();p.api.viewAccount(B);await tick();
  assert.equal(p.ui.quotas.getAttribute('data-stale'),'true');assert.match(p.ui.quotas.text,/STALE \/ historical/);
  assert.match(p.ui.quotas.text,/100% used/);assert.match(p.ui.quotas.text,/historical availability/);
  assert.match(p.ui.banked.text,/as of the historical reading/);assert.match(p.ui['quota-status'].textContent,/next read in 10m/);
  assert.equal(p.ui.switch.disabled,false);
});

test('passed resets remove old percentages and model availability, and expired banked grants disappear',async()=>{
  const q=quota();q.chatgpt.windows[0].resets_at=q.now-1;q.chatgpt.models.Astra.available_at=q.now-1;q.chatgpt.reset_credits[0].expires_at=q.now-1;
  const p=boot({quotaRoute:()=>q});await tick();
  assert.doesNotMatch(p.ui.quotas.text,/39% used/);assert.match(p.ui.quotas.text,/\? used/);
  assert.match(p.ui.quotas.text,/recorded reset passed/);assert.match(p.ui.quotas.text,/availability unknown after recorded reset/);
  assert.match(p.ui.banked.text,/none banked/);assert.doesNotMatch(p.ui.banked.text,/Expires/);
});

test('countdown is repainted against elapsed browser time, never a fabricated post-reset zero',async()=>{
  const p=boot();await tick();assert.match(p.ui.quotas.text,/39% used/);
  p.advance(7201);p.api.paintQuota();assert.match(p.ui.quotas.text,/current quota unknown/);assert.doesNotMatch(p.ui.quotas.text,/39% used|0% used/);
});

for(const mismatch of ['credentialId','bound account','backend'])test('foreign quota '+mismatch+' is rejected rather than attributed to the viewed account',async()=>{
  const p=boot({quotaRoute:id=>{const q=quota(id);if(id===B){if(mismatch==='credentialId')q.credentialId=A;else if(mismatch==='bound account')q.chatgpt.account.opencode_credential_id=A;else q.backend='usage';}return q;}});
  await tick();p.api.viewAccount(B);await tick();assert.match(p.ui.quotas.text,/Foreign GPT quota reply rejected/);
  assert.doesNotMatch(p.ui.quotas.text,/72% used|39% used/);assert.equal(p.ui.plan.textContent,'');assert.equal(p.ui.switch.disabled,false);
});

test('a late other-account quota cannot replace the current view, plan or banked resets',async()=>{
  const late=deferred();const p=boot({quotaRoute:id=>id===B?late.promise:quota(id)});await tick();p.api.viewAccount(B);p.api.viewAccount(A);
  late.resolve(quota(B));await tick();assert.equal(p.api.snapshot().quota.credentialId,A);assert.equal(p.ui.plan.textContent,'Fixture Pro');
  assert.match(p.ui.quotas.text,/39% used/);assert.doesNotMatch(p.ui.quotas.text,/72% used/);assert.match(p.ui.banked.text,/Family GPT/);
});

test('late old quota for the same ID cannot overwrite a newer explicit provider read',async()=>{
  const late=deferred();let reads=0;const p=boot({quotaRoute:id=>++reads===1?late.promise:quota(id)});await tick();
  await p.api.readQuota(A,true);const old=quota(A);old.chatgpt.plan='OLD';old.chatgpt.windows[0].percent=99;late.resolve(old);await tick();
  assert.equal(p.ui.plan.textContent,'Fixture Pro');assert.doesNotMatch(p.ui.quotas.text,/99% used/);
});

test('explicit Switch posts once with the immutable ID and expected active, while both views remain usable',async()=>{
  let op=null;const p=boot({route:r=>{if(r.method==='POST'){op=receipt();assert.deepEqual(JSON.parse(p.storage.get(STORE)),{id:'gpt-operation-1',credentialId:B,expectedActiveCredentialId:A});}return status({operation:op});}});
  await tick();p.api.viewAccount(B);await tick();await Promise.all([p.api.switchAccount(),p.api.switchAccount()]);await tick();
  assert.equal(posts(p).length,1);assert.deepEqual(posts(p)[0].body,{schemaVersion:1,backend:'chatgpt',credentialId:B,operationId:'gpt-operation-1',expectedActiveCredentialId:A});
  assert.equal(p.ui.root.getAttribute('data-busy'),'true');assert.equal(p.ui.button(A).disabled,false);assert.equal(p.ui.button(B).disabled,false);
  p.api.viewAccount(A);await tick();assert.equal(JSON.parse(p.storage.get(STORE)).credentialId,B);assert.equal(p.ui.button(A).getAttribute('data-active'),'true');
  assert.ok(p.requests.some(r=>r.url==='/api/chatgpt/account-switch?operationId=gpt-operation-1'));
  assert.equal(p.events[0].type,'oc-chatgpt-account-switch');assert.equal(p.events[0].detail.provider,'openai');
});

test('an unknown native active login remains unknown and an explicit switch carries expectedActiveCredentialId null',async()=>{
  const p=boot({route:r=>status({known:false,operation:r.method==='POST'?receipt('uncertain'):null})});await tick();p.api.viewAccount(B);await tick();
  assert.equal(p.ui.current.textContent,'Active unknown');assert.ok(p.ui.accounts.children.every(n=>n.getAttribute('data-active')==='false'));
  assert.equal(p.ui.switch.disabled,false);await p.api.switchAccount();await tick();assert.equal(posts(p)[0].body.expectedActiveCredentialId,null);
});

test('lost reply and page reload query exactly the saved operation and never replay a POST',async()=>{
  let op=null;const storage=new Map();const route=r=>{if(r.method==='POST'){op=receipt();return new Error('Fixture lost reply');}return status({operation:op});};
  const first=boot({storage,route});await tick();first.api.viewAccount(B);await first.api.switchAccount();await tick();
  assert.equal(posts(first).length,1);const intent=copy(JSON.parse(storage.get(STORE)));
  const reloaded=boot({storage,route});await tick();assert.equal(posts(reloaded).length,0);assert.equal(reloaded.requests[0].url,'/api/chatgpt/account-switch?operationId='+intent.id);
  assert.deepEqual(JSON.parse(storage.get(STORE)),intent);assert.equal(reloaded.ui.switch.disabled,true);
  op=receipt('confirmed');await reloaded.api.refresh();await tick();assert.equal(storage.has(STORE),false);
  assert.equal(posts(reloaded).length,0);assert.equal(reloaded.ui.button(A).getAttribute('data-active'),'true');
  assert.match(reloaded.ui.status.textContent,/historical receipt/);
});

test('a historical confirmed receipt never overrides a fresh external active selection or unknown readback',async()=>{
  let active=B,known=true;const p=boot({body:()=>status({active,known,operation:receipt('confirmed')})});await tick();
  assert.equal(p.ui.button(B).getAttribute('data-active'),'true');active=A;await p.api.refresh();await tick();
  assert.equal(p.api.snapshot().shown,B);assert.equal(p.ui.button(B).getAttribute('aria-pressed'),'true');assert.equal(p.ui.button(B).getAttribute('data-active'),'false');
  assert.equal(p.ui.button(A).getAttribute('data-active'),'true');assert.match(p.ui.status.textContent,/NOT currently confirmed active/);assert.equal(p.ui.switch.disabled,false);
  known=false;await p.api.refresh();await tick();assert.equal(p.ui.current.textContent,'Active unknown');assert.ok(p.ui.accounts.children.every(n=>n.getAttribute('data-active')==='false'));
});

test('failed fresh status read removes the old active ring without replaying a pending selection',async()=>{
  let fail=false;const p=boot({route:()=>fail?new Error('Fixture status disconnected'):status()});await tick();fail=true;await p.api.refresh();await tick();
  assert.equal(p.ui.current.textContent,'Active unknown');assert.ok(p.ui.accounts.children.every(n=>n.getAttribute('data-active')==='false'));
  assert.match(p.ui.status.textContent,/disconnected/);assert.equal(posts(p).length,0);
});

test('a forgotten receipt after reload reports uncertainty and performs no hidden retry',async()=>{
  const storage=new Map([[STORE,JSON.stringify({id:'retained-id',credentialId:B,expectedActiveCredentialId:A})]]);
  const p=boot({storage});await tick();assert.equal(p.requests[0].url,'/api/chatgpt/account-switch?operationId=retained-id');
  assert.match(p.ui.status.textContent,/No retained receipt.*Nothing was replayed/);assert.equal(posts(p).length,0);assert.equal(storage.has(STORE),false);
});

test('single-login enrollment help is informational and starts no enrollment or native action',async()=>{
  const all=status();const p=boot({body:{...all,accounts:all.accounts.slice(0,1)}});await tick();
  assert.equal(p.ui.enrollment.hidden,false);assert.equal(p.ui.accounts.children.length,1);assert.equal(p.ui.switch.disabled,true);
  assert.equal(posts(p).length,0);assert.ok(p.requests.every(r=>r.url.startsWith('/api/chatgpt/')));
});

test('an unresolved POST cannot strand receipt recovery, and its late reply cannot restore an old active badge',async()=>{
  const late=deferred();let active=A,op=null;
  const p=boot({route:r=>{
    if(r.method==='POST'){op=receipt('attempting');return late.promise;}
    return status({active,operation:op});
  }});
  await tick();p.api.viewAccount(B);await tick();const posting=p.api.switchAccount();await tick();
  assert.equal(posts(p).length,1);assert.equal(p.api.snapshot().submitting,true);
  active=B;op=receipt('confirmed');await p.api.refresh();await tick();
  assert.equal(p.api.snapshot().intent,null);assert.equal(p.api.snapshot().submitting,false);
  p.api.viewAccount(A);await tick();assert.equal(p.ui.switch.disabled,false,'a terminal receipt releases the UI while POST is still unanswered');
  active=A;await p.api.refresh();await tick();
  late.resolve(status({active:B,operation:receipt('confirmed')}));await posting;await tick();
  assert.equal(posts(p).length,1);assert.equal(p.ui.current.textContent,LABEL_A);
  assert.equal(p.ui.button(B).getAttribute('data-active'),'false');
});
