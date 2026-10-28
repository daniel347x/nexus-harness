// OpenCode Manager targeting seam: the production sessions.js in a Node DOM double, an offline
// schema-1 OpenCode Manager route and the real app.js editor (pane-fixture) for the cross-frame seam.
// No live pane, OpenCode, CodeNomad, process, Send or control is ever touched.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(process.env.HISTORY_TEST_SOURCE || path.join(__dirname,'../static/sessions.js'),'utf8');
const core=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-preview-core.js'),'utf8');
const shared=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-record-text.js'),'utf8');
const reading=fs.readFileSync(path.join(__dirname,'../static/tool-reading.js'),'utf8');
const order=fs.readFileSync(path.join(__dirname,'../static/manager-order.js'),'utf8');
const SID='ses_manager000001',WORKER='ses_worker000001';
const WITNESS='a'.repeat(64);
const CLAUDE_UUID='11111111-1111-4111-8111-111111111111',CLAUDE_TARGET='T'.repeat(32);
const clone=v=>JSON.parse(JSON.stringify(v));
const env=(fields={})=>({schemaVersion:1,backend:'claude',...fields});

// The real GET /api/opencode/manager answer: a root Manager with its server creation/directory witness,
// model readback and its own newest measured call. Non-Managers answer 409 and stay view-only.
function managerState(id,{title='Manager-Session-216',witness=WITNESS,context,model,busy=false}={}) {
  return {schemaVersion:1,backend:'opencode',sessionId:id,
    target:{backend:'opencode',sessionId:id,witness},
    title,agent:'manager',model:model || {providerID:'openrouter',id:'deepseek/deepseek-v4.1',variant:'xhigh'},
    directory:'/fixture/manager-sessions',createdAt:1792000000000,busy,observedAt:1792000600,
    capabilities:{send:true,control:true,context:true},
    context:context || {status:'measured',usedTokens:120000,window:1000000,measuredAt:1792000000000,basis:'input + cache read + cache write + output + reasoning'}};
}
const openCodePage=(id,{title=id,busy=false,sessionInfo=true}={})=>({schemaVersion:1,backend:'opencode',source:'opencode',sessionRef:id,scopeRef:'main',
  fileEpoch:'oc_'+'1'.repeat(32),events:[],eventUpdates:[],hasMore:false,nextCursor:null,deltaCursor:'d_0',rowsAhead:false,status:'complete',change:'snapshot',busy,
  ...(sessionInfo?{sessionInfo:{id,title,agent:'manager',model:null,directory:'/fixture/manager-sessions',parentId:null}}:{}),
  leafRef:null,targetRef:null,indexLimit:null,branches:[],traces:[],summaryUpdates:[],subagentActivity:[],diagnostics:[]});
const claudePage=(row)=>({schemaVersion:1,backend:'claude',sessionRef:row.sessionRef,scopeRef:'scope',fileEpoch:'epoch',leafRef:'leaf',targetRef:CLAUDE_TARGET,
  events:[],eventUpdates:[],hasMore:false,nextCursor:null,deltaCursor:'d',rowsAhead:false,status:'complete',change:'snapshot',branches:[],traces:[],diagnostics:[],
  summaryUpdates:[],subagentActivity:[],indexLimit:null,contextReadout:null,targetInfo:row,liveState:{state:'running'}});
const managerRow=(id,extra={})=>({id,kind:'manager',role:'Manager',backend:'opencode',manager:true,program:null,
  title:'Manager-Session-216',agent:'manager',model:'openrouter/deepseek/deepseek-v4.1',directory:'/fixture/manager-sessions',createdAt:1792000000000,busy:false,...extra});
const workerRow=(id,extra={})=>({id,kind:'seat',quick:true,program:null,title:'⚡ quick worker · '+id,agent:'quick-worker',
  model:'openrouter/deepseek/deepseek-v4.1-flash',directory:'/fixture/manager-sessions',createdAt:1792000000000,busy:false,...extra});

function fixture({programs=[],managers={},claude=[],allPrograms=programs,allClaude=claude,embedded=true,sidebarOnly=false}={}) {
  const elements=new Map(),requests=[],messages=[],bcMessages=[],timers=new Map(),listeners=[];let time=1792000600000,timerId=0;
  class Node {
    constructor(tag='div'){this.tag=tag;this.children=[];this.parentNode=null;this.dataset={};this.attributes={};this.scrollTop=0;this.clientHeight=100;this.hidden=false;this.value='';this.content='';this.offsetTop=0;this.offsetHeight=0;
      this.style={setProperty:(k,v)=>this.attributes[k]=v,removeProperty:k=>delete this.attributes[k]};
      const classes=new Set();this.classes=classes;this.classList={add:k=>classes.add(k),contains:k=>classes.has(k),toggle:(k,v)=>{if(v===undefined)v=!classes.has(k);v?classes.add(k):classes.delete(k);}};}
    set className(v){this.classes.clear();for(const k of String(v).split(/\s+/).filter(Boolean))this.classes.add(k);}get className(){return [...this.classes].join(' ');}
    set textContent(v){this.content=v;this.replaceChildren();}get textContent(){return this.content+this.children.map(x=>x.textContent).join('');}
    append(...nodes){for(const n of nodes){if(n.tag==='fragment'){this.append(...[...n.children]);continue;}n.remove();n.parentNode=this;this.children.push(n);}}
    prepend(node){node.remove();node.parentNode=this;this.children.unshift(node);}
    replaceChildren(...nodes){for(const n of this.children)n.parentNode=null;this.children=[];this.append(...nodes);}
    replaceWith(node){const p=this.parentNode,i=p.children.indexOf(this);node.remove();p.children[i]=node;node.parentNode=p;this.parentNode=null;}
    remove(){if(this.parentNode){const i=this.parentNode.children.indexOf(this);this.parentNode.children.splice(i,1);this.parentNode=null;}}
    setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k]??null;}
    get scrollHeight(){return this.id==='history-viewport'?(elements.get('history-events')?.children.length||0)*100:100;}
    getBoundingClientRect(){let top=0;if(this.parentNode?.id==='history-events')top=this.parentNode.children.indexOf(this)*100-el('history-viewport').scrollTop;return {top,bottom:top+100,height:100};}
  }
  const el=name=>{if(!elements.has(name)){const n=new Node();n.id=name;elements.set(name,n);}return elements.get(name);};
  el('oc-header').hidden=true;
  let box=env({epoch:'mail',revision:1,selection:null});
  const parent={postMessage:data=>messages.push(data)},origin='http://127.0.0.1:9911';
  const view=embedded?{frameElement:{id:'claude-sessions-frame'},parent,location:{origin},
    addEventListener:(type,fn)=>{if(type==='message')listeners.push(fn);}}:null;
  const doc={hidden:false,getElementById:el,createElement:t=>new Node(t),createDocumentFragment:()=>new Node('fragment'),
    addEventListener(){},defaultView:view,body:sidebarOnly?{dataset:{view:'programs'}}:null};
  const resp=(data,status=200)=>new Response(JSON.stringify(data),{status});
  const ctx={TextEncoder,TextDecoder,AbortController,URLSearchParams,URL,Response,Date:class extends Date{static now(){return time;}},
    setTimeout:(f,ms)=>{if(ms===0){queueMicrotask(f);return 0;}timers.set(++timerId,f);return timerId;},clearTimeout:i=>timers.delete(i),setInterval(){},
    BroadcastChannel:class{postMessage(m){bcMessages.push(m);}},fetch:async(url,options)=>{
      requests.push({url,method:options?.method||'GET'});
      if(!url.startsWith('/'))throw new Error('unexpected offline request '+url);
      const u=new URL(url,'http://pane'),q=Object.fromEntries(u.searchParams);
      if(u.pathname==='/api/opencode/programs')return resp({schemaVersion:1,backend:'opencode',status:'complete',sessions:q.view?programs:allPrograms,observedAt:time/1000});
      if(u.pathname==='/api/opencode/activity')return resp({schemaVersion:1,backend:'opencode',status:'complete',observedAt:time/1000,active:programs.filter(r=>r.busy).map(r=>r.id),sessions:[]});
      if(u.pathname==='/api/opencode/turns')return resp({schemaVersion:1,backend:'opencode',status:'unavailable',id:q.id,error:'offline'});
      if(u.pathname==='/api/opencode/manager'){
        const record=managers[q.id];
        if(!record || !record.reply || record.revoked)return resp({schemaVersion:1,backend:'opencode',status:'not-manager',error:'This session is not an owned root OpenCode Manager; it stays view-only'},409);
        return resp(record.reply());
      }
      if(u.pathname==='/api/opencode/events'){
        const record=managers[q.id];
        if(!record?.page)return resp({schemaVersion:1,backend:'opencode',status:'missing',error:'OpenCode session missing'},404);
        return resp(record.page());
      }
      if(u.pathname==='/api/claude/sessions')return resp(env({sessions:q.role==='Manager'?allClaude:claude,status:'complete',hasMore:false}));
      if(u.pathname==='/api/claude/events')return resp(claudePage(claude[0]));
      if(u.pathname==='/api/tool-selection')return resp(box);
      throw new Error('unexpected offline request '+url);
    }};
  vm.runInNewContext(shared+'\n'+core+'\n'+reading+'\n'+order+'\n'+source.replace("if(typeof document!=='undefined'", "if(false && typeof document!=='undefined'"),ctx);
  const api=ctx.opcClaudeHistory.create(doc);
  const flush=async(n=4)=>{for(let i=0;i<n;i++)await new Promise(r=>setImmediate(r));};
  return {api,ctx,el,requests,messages,bcMessages,timers,parent,origin,direct:data=>({data,source:parent,origin}),
    emit(data,{source=parent}={}){for(const fn of listeners)fn({data,source,origin});},
    flush,now:()=>time,box:value=>box=value,
    fire(id){const f=timers.get(id);assert.ok(f,'timer '+id+' is live');return f();}};
}
const find=(node,cls)=>{const out=[];const walk=n=>{if(n.classList?.contains(cls))out.push(n);for(const c of n.children||[])walk(c);};walk(node);return out;};
const helloOf=f=>f.messages.filter(m=>m.type==='hello').at(-1);
const applyPresence=(f,hello,editorId='editor-1',revision=7)=>f.api.targetMessage(f.direct({schemaVersion:1,type:'presence',clientId:hello.clientId,requestId:hello.requestId,editorId,targetRevision:revision}));

// ---------------------------------------------------------------- 1 · root Manager vs worker
test('a listed root OpenCode Manager GET-validates by its full sid and auto-targets the parent editor; a worker stays view only',async()=>{
  const f=fixture({programs:[managerRow(SID),workerRow(WORKER)],managers:{
    [SID]:{page:()=>openCodePage(SID,{title:'Manager-Session-216'}), reply:()=>managerState(SID)}}});
  await f.api.ocList();
  // No Manager was chosen yet: the newest numbered Manager is the remembered fallback pin.
  assert.equal(f.el('sessions-pinned').hidden,false);
  const items=[...find(f.el('sessions-pinned'),'oc-item'),...find(f.el('sessions-list'),'oc-item')];
  const managerItem=items.find(item=>/Manager:/.test(item.textContent));
  assert.ok(managerItem,'the root Manager row is listed as a Manager');
  assert.match(managerItem.children[0].title,/selecting a validated Manager targets Send\/Refine/);
  managerItem.children[0].onclick();await f.flush();
  const session=f.api.state.session;
  assert.equal(session.id,SID);assert.equal(session.managerClaim,'row');
  assert.equal(session.manager.target.sessionId,SID);assert.equal(session.manager.target.witness,WITNESS);
  assert.deepEqual({...session.capabilities},{history:false,nativeInput:false,controls:false,subagents:false,reconnect:false,send:true,control:true,context:true});
  assert.equal(f.el('history-target').disabled,false,'a validated Manager is selectable for Send');
  assert.equal(f.el('context-gauge').dataset.known,'true');
  assert.match(f.el('context-gauge').title,/120,000 tokens of 1,000,000 \(12\.0%\).*as of.*input \+ cache read \+ cache write \+ output \+ reasoning/);
  assert.match(f.el('history-activity').textContent,/^read \d+ s ago · .*target validated$/);
  assert.ok(f.requests.some(r=>r.url==='/api/opencode/manager?id='+SID),'the full sid is validated over GET');
  const hello=helloOf(f);assert.ok(hello,'the viewer discovers its actual parent editor');
  applyPresence(f,hello);
  const action=f.api.state.targetAction;assert.ok(action,'the validated Manager auto-targets');
  f.fire(action.timer);
  const target=f.messages.filter(m=>m.type==='target').at(-1);assert.equal(target.backend,'opencode');assert.equal(target.sessionId,SID);
  assert.equal(target.automatic,true);assert.equal(target.targetRef,undefined);
  assert.deepEqual(Object.keys(target).sort(),['actionId','actionRevision','automatic','backend','clientId','editorId','schemaVersion','sessionId','targetRevision','type'].sort());
  const sent=f.api.state.targetAction;
  f.api.targetMessage(f.direct({schemaVersion:1,type:'ack',clientId:hello.clientId,editorId:sent.editorId,actionId:sent.id,actionRevision:sent.sequence,applied:true,reason:'Ready: sending directly to OpenCode Manager Manager-Session-216'}));
  assert.match(f.el('target-status').textContent,/Ready: sending directly to OpenCode Manager/);

  // The ordinary worker is not validated, is not targetable and posts nothing.
  const before={targets:f.messages.filter(m=>m.type==='target').length,hello:helloOf(f).requestId};
  const workerItem=find(f.el('sessions-list'),'oc-item').find(item=>/quick worker/i.test(item.textContent));
  assert.ok(workerItem);
  workerItem.children[0].onclick();await f.flush();
  assert.equal(f.api.state.session.id,WORKER);assert.equal(f.api.state.session.manager,null);
  assert.equal(f.api.state.targetAction,null);
  assert.match(f.el('history-capabilities').textContent,/an OpenCode conversation is never a Send, Refine or control target/);
  assert.equal(f.el('history-target').disabled,true);
  assert.equal(f.messages.filter(m=>m.type==='target').length,before.targets,'a worker selection never replaces the editor target');
  assert.ok(f.requests.filter(r=>r.url==='/api/opencode/manager?id='+WORKER).length===0,'a worker row is never validated as a Manager');
  f.api.useTarget();
  assert.match(f.el('target-status').textContent,/Not a Send\/Refine target: an OpenCode conversation is view-only here/);
});

// ---------------------------------------------------------------- 2 · bare sid, empty Manager, context
test('OpenCode Managers share the Manager Sessions home, pin when selected, and keep their subagents out of Quick workers',async()=>{
  const child={id:'ses_managerCHILD',kind:'subagent',parentId:SID,program:null,title:'Manager child',agent:'explore',createdAt:1792000000010,busy:false};
  const f=fixture({programs:[managerRow(SID),child,workerRow(WORKER)],managers:{[SID]:{page:()=>openCodePage(SID),reply:()=>managerState(SID)}}});
  await f.api.ocList();
  const groups=find(f.el('sessions-list'),'session-group'),managers=groups.find(g=>g.dataset.group==='managers'),quick=groups.find(g=>g.dataset.group==='folder:quick-worker');
  assert.ok(managers);
  // The highest numbered Manager is this page's fallback pin: it sits at the top with its listed subagent,
  // and its home group keeps only the inert stub.
  assert.match(f.el('sessions-pinned').textContent,/Manager-Session-216/);assert.match(f.el('sessions-pinned').textContent,/Manager child/);
  assert.match(managers.textContent,/↑ Manager-Session-216 · pinned at top/);
  assert.ok(quick);assert.doesNotMatch(quick.textContent,/Manager-Session-216|Manager child/);
  await f.api.openOpenCode(SID);await f.flush();
  assert.match(f.el('sessions-pinned').textContent,/Manager-Session-216/);
  f.api.setAllPrograms(false);assert.equal(f.api.state.sections.collapsed.has('managers'),true);
});

// The Manager Sessions order Dan reads (manager-order.js): every Manager by its Manager-Session number, highest first,
// each primary followed by its sub-sessions, Claude Code and OpenCode together, live (green dot) or not; a name without
// a number at the bottom.
test('Manager Sessions lists by Manager-Session number, each primary before its sub-sessions, both backends mixed, liveness ignored',async()=>{
  const U=n=>String(n).padStart(8,'0')+'-0000-4000-8000-000000000000';
  const claudeManager=(n,name,{open=false,createdAt,title=name}={})=>({sessionRef:'ref-'+n,uuid:U(n),executionId:'cc-'+U(n),generation:1,
    source:'host',role:'manager',displayRole:'manager',sessionLabel:name,title,nameSource:'chosen',cwd:'/fixture/manager-sessions',
    targetRef:'target-'+n,scopes:[{scopeRef:'main-'+n,label:'main'}],createdAt,lifecycle:open?'live':'sleeping',state:open?'ready':'collected',
    capabilities:open?{nativeInput:true,controls:true,reconnect:true}:{nativeInput:false,controls:false,reconnect:true},liveState:{}});
  const ocManager=(tail,title,createdAt)=>managerRow('ses_mgr'+tail.padEnd(12,'0'),{title,createdAt});
  const claude=[
    claudeManager(1,'Manager-Session-216',{open:true,createdAt:1792900000}),
    claudeManager(2,'Manager-Session-218',{open:true,createdAt:1793021400}),
    claudeManager(3,'Manager-Session-217-a',{createdAt:1793011800}),
    claudeManager(4,'Manager-Session-217',{open:true,createdAt:1792985400}),
    claudeManager(5,'Manager-Session-218-a',{open:true,createdAt:1793021700}),
    claudeManager(6,'Manager-Session-217-d',{open:true,createdAt:1793018700}),
    claudeManager(7,null,{createdAt:1792009516,title:U(7)}),
  ];
  const programs=[
    ocManager('217e','Manager-Session-217-e',1793019043),
    ocManager('217d','Manager-Session-217-d',1793018978),
    ocManager('216b','Manager-Session-216-b',1792977368),
    ocManager('216a','Manager-Session-216-a',1792972587),
    ocManager('168','Manager-Session-168 Anchor Dock instructions',1791727421),
  ];
  const f=fixture({programs,claude});
  await f.api.list();await f.api.ocList();
  const managers=find(f.el('sessions-list'),'session-group').find(g=>g.dataset.group==='managers');assert.ok(managers);
  const describe=item=>[find(item,'name-text')[0].textContent,item.classList.contains('oc-item')?'oc':'cc',item.classList.contains('live')];
  // With no choice made, the newest numbered family is the reload fallback pin; the home group keeps stubs.
  const pinned=find(f.el('sessions-pinned'),'session-item').map(describe);
  assert.deepEqual(pinned,[['Manager-Session-218','cc',true],['Manager-Session-218-a','cc',true]]);
  assert.equal(find(managers,'session-stub').map(n=>n.textContent).join('|'),'↑ Manager-Session-218 · pinned at top|↑ Manager-Session-218-a · pinned at top');
  const names=find(managers,'session-item').map(describe);
  assert.deepEqual(names,[
    ['Manager-Session-217','cc',true],
    ['Manager-Session-217-a','cc',false],['Manager-Session-217-d','oc',false],['Manager-Session-217-d','cc',true],['Manager-Session-217-e','oc',false],
    ['Manager-Session-216','cc',true],['Manager-Session-216-a','oc',false],['Manager-Session-216-b','oc',false],
    ['Manager-Session-168 Anchor Dock instructions','oc',false],['Session 00000007','cc',false]]);
  assert.match(managers.children[0].title,/by Manager-Session number, highest first, each primary followed by its sub-sessions .*never moves a row/);
  // The same names, in that order, are what the editor's ↳ New sub-session reads (opcSidebar.managerNames).
  assert.deepEqual(JSON.parse(JSON.stringify(f.api.state.managerNames.map(n=>n.find(x=>typeof x==='string' && /Manager-Session/.test(x)) || null))),
    [...pinned,...names].map(([name])=>/Manager-Session/.test(name)?name:null));
});

test('clicking any Manager sub-session pins the complete cross-backend family, primary then letters, including closed siblings outside the view window',async()=>{
  const cc=(n,title)=>({sessionRef:'family-'+n,uuid:CLAUDE_UUID.replace(/^1/,String(n)),executionId:'cc-'+CLAUDE_UUID.replace(/^1/,String(n)),generation:1,
    source:'host',role:'manager',displayRole:'manager',sessionLabel:title,title,cwd:'/fixture/manager-sessions',
    scopes:[{scopeRef:'scope',label:'main'}],createdAt:100+n,lifecycle:'sleeping',state:'collected',capabilities:{reconnect:true},liveState:{}});
  const primary=cc(2,'Manager-Session-226'),b=cc(3,'Manager-Session-226-b'),other=cc(4,'Manager-Session-227'),old=cc(5,'Manager-Session-205');
  const a=managerRow(SID,{title:'Manager-Session-226-a',createdAt:200}),c=managerRow('ses_family226c',{title:'Manager-Session-226-c',createdAt:300});
  const impostor=workerRow(WORKER,{title:'Manager-Session-226-d mentioned by a worker'});
  const f=fixture({programs:[a,impostor],allPrograms:[a,c,impostor],claude:[primary,other],allClaude:[primary,b,other,old],managers:{
    [SID]:{page:()=>openCodePage(SID,{title:a.title}),reply:()=>managerState(SID,{title:a.title})},
    [c.id]:{page:()=>openCodePage(c.id,{title:c.title}),reply:()=>managerState(c.id,{title:c.title})}}});
  await f.api.list();await f.api.ocList();
  const names=()=>find(f.el('sessions-pinned'),'session-item').map(item=>find(item,'name-text')[0].textContent);
  const expected=['Manager-Session-226','Manager-Session-226-a','Manager-Session-226-b','Manager-Session-226-c'];
  const clickA=find(f.el('sessions-list'),'oc-item').find(item=>item.textContent.includes(a.title));
  clickA.children[0].onclick();await f.flush();
  assert.deepEqual(names(),expected,'a click on -a brings primary, closed -b and unwindowed -c along');
  assert.equal(find(f.el('sessions-pinned'),'selected').length,1,'only the clicked conversation is selected, never the whole family');
  assert.ok(f.requests.some(r=>r.url.includes('role=Manager')),'closed Claude siblings use the existing unwindowed Manager summary read');
  assert.ok(f.requests.some(r=>r.url==='/api/opencode/programs'),'closed OpenCode siblings use the cached unwindowed inventory');
  assert.doesNotMatch(f.el('sessions-pinned').textContent,/227|mentioned by a worker/,'family names never confer Manager membership');
  const clickC=find(f.el('sessions-pinned'),'oc-item').find(item=>item.textContent.includes(c.title));
  clickC.children[0].onclick();await f.flush();assert.deepEqual(names(),expected,'clicking a different sub-session never changes family order');
  const reads=f.requests.length;f.api.inventory();f.api.inventory();assert.equal(f.requests.length,reads,'rendering makes no family reads');
  f.api.state.session=primary;f.api.inventory();assert.deepEqual(names(),expected,'a primary click uses exactly the same family');
  // A worker or a child never takes the remembered Manager family away, even through the real browse path.
  await f.api.openOpenCode(WORKER);assert.deepEqual(names(),expected,'browsing a helper keeps the chosen family');
  await f.api.openOpenCode('ses_familychild');assert.deepEqual(names(),expected,'browsing a child keeps the chosen family');
  const browseReads=f.requests.length;f.api.inventory();assert.equal(f.requests.length,browseReads,'the browse reads were the conversation, not the family');
  // No session at all (a reload, a closed editor): the remembered 226 family still outranks the newest 227.
  f.api.state.session=null;f.api.inventory();assert.deepEqual(names(),expected,'an explicit choice is retained with no selection');
  f.api.state.session=other;f.api.inventory();assert.deepEqual(names(),['Manager-Session-227'],'another numbered family replaces it');
  f.api.state.session=null;f.api.inventory();assert.deepEqual(names(),['Manager-Session-227'],'the newer choice is the one retained');
  // A deliberate swap to an older, closed family outside the window comes from the same unwindowed summaries.
  f.api.state.session=old;f.api.inventory();assert.deepEqual(names(),['Manager-Session-205'],'an old closed numbered family replaces it too');
  assert.equal(f.requests.filter(r=>r.method==='POST').length,0,'family placement never sends or changes saved choices');
});

test('phone Programs uses the same family placement from the picker summaries, with no conversation, extra inventory read or target change',async()=>{
  const primary={source:'host',role:'manager',displayRole:'manager',sessionRef:'family-primary',uuid:CLAUDE_UUID,executionId:'cc-'+CLAUDE_UUID,
    sessionLabel:'Manager-Session-226',title:'Manager-Session-226',scopes:[{scopeRef:'scope',label:'main'}],createdAt:100,lifecycle:'sleeping',capabilities:{},liveState:{}};
  const a=managerRow(SID,{title:'Manager-Session-226-a',createdAt:200}),b={...primary,sessionRef:'family-b',uuid:CLAUDE_UUID.replace(/^1/,'2'),executionId:'cc-'+CLAUDE_UUID.replace(/^1/,'2'),sessionLabel:'Manager-Session-226-b',title:'Manager-Session-226-b'};
  const f=fixture({sidebarOnly:true,claude:[],programs:[a]});await f.api.list();await f.api.ocList();
  const before=f.requests.length;
  f.api.selectManagerFamily(a,[b,a,primary]);
  assert.deepEqual(find(f.el('sessions-pinned'),'session-item').map(item=>find(item,'name-text')[0].textContent),
    ['Manager-Session-226','Manager-Session-226-a','Manager-Session-226-b']);
  assert.equal(f.api.state.session,null,'the sidebar does not become a second viewer/Send target');
  assert.equal(f.requests.length,before,'the phone already read all Manager summaries');
  f.api.selectManagerFamily(primary,[b,a,primary]);
  assert.deepEqual(find(f.el('sessions-pinned'),'session-item').map(item=>find(item,'name-text')[0].textContent),
    ['Manager-Session-226','Manager-Session-226-a','Manager-Session-226-b']);
});

test('a bare full sid reopens and validates without any inventory; an empty Manager is usable and its measured context is its own',async()=>{
  const f=fixture({programs:[],managers:{
    [SID]:{page:()=>openCodePage(SID,{title:'Fresh empty Manager'}), reply:()=>managerState(SID,{title:'Fresh empty Manager'})}}});
  f.api.state.lastContext={identity:'host:some-claude',occupancy_tokens:999999,window_tokens:1000000,measured_at:1792000000};
  await f.api.openOpenCode(SID);await f.flush();
  assert.equal(f.api.state.session.id,SID);assert.equal(f.api.state.rows.size,0,'the new Manager has no rows yet');
  assert.equal(f.api.state.blocked,false);
  assert.equal(f.api.state.session.manager.target.sessionId,SID);
  assert.match(f.el('context-gauge').title,/120,000 tokens/);assert.doesNotMatch(f.el('context-gauge').title,/999,999|some-claude/,'no Claude figure is carried into an OpenCode Manager');
  assert.ok(f.api.state.targetAction,'an empty/new Manager still auto-targets');
  // The editor's launch notice names the same full sid: it reopens and revalidates the same Manager.
  const before=f.requests.filter(r=>r.url==='/api/opencode/manager?id='+SID).length;
  f.api.state.session=null;f.api.state.targetAction=null;
  f.emit({schemaVersion:1,type:'browse-opencode',sessionId:SID});await f.flush();
  assert.equal(f.api.state.session.id,SID);
  assert.equal(f.requests.filter(r=>r.url==='/api/opencode/manager?id='+SID).length,before+1);
  assert.ok(f.api.state.targetAction,'browse-opencode auto-targets the validated Manager');
});

// ---------------------------------------------------------------- 3 · unknown context never gates Send
test('an unknown measured context stays unknown, keeps its own honest readback and never disables Send',async()=>{
  const unknown={status:'unknown',usedTokens:null,window:null,measuredAt:null,basis:'input + cache read + cache write + output + reasoning'};
  const f=fixture({programs:[managerRow(SID)],managers:{
    [SID]:{page:()=>openCodePage(SID), reply:()=>managerState(SID,{context:unknown})}}});
  await f.api.openOpenCode(SID);await f.flush();
  assert.equal(f.el('context-gauge').dataset.known,'false');
  assert.match(f.el('context-gauge').title,/Context: unknown · OpenCode Manager reported no measured call yet/);
  assert.equal(f.el('history-target').disabled,false,'unknown context does not revoke Send');
  assert.equal(f.api.state.session.capabilities.send,true);
  assert.equal(f.api.state.session.manager.context.status,'unknown');
});

// ---------------------------------------------------------------- 4 · a revoked Manager
test('a Manager that stops validating loses Send authority on its own read, without a stale target or a Claude fallback',async()=>{
  const record={page:()=>openCodePage(SID), reply:()=>managerState(SID)};
  const f=fixture({programs:[managerRow(SID)],managers:{[SID]:record}});
  await f.api.openOpenCode(SID);await f.flush();
  assert.equal(f.el('history-target').disabled,false);
  assert.ok(f.api.state.session.manager);
  record.revoked=true;
  f.api.state.ocManagerNextPoll=0;await f.api.poll();await f.flush();
  assert.equal(f.api.state.session.manager.target.sessionId,SID,'the facts remain visible');
  assert.equal(f.api.state.session.target,null);
  assert.equal(f.api.state.session.capabilities.send,false);
  assert.equal(f.el('history-target').disabled,true,'a revoked Manager is not a Send target');
  assert.equal(f.api.state.session.managerIdentityChanged,true);
  assert.match(f.el('history-capabilities').textContent,/Manager validation failed|identity changed/);
  assert.equal(f.messages.filter(m=>m.type==='target').length,0,'revocation never sends or falls back to Claude');
});

// ---------------------------------------------------------------- 5 · same-origin parent protocol
test('targeting is posted only to the actual same-origin parent editor; BroadcastChannel discovers duplicate tabs but never receives it',async()=>{
  const f=fixture({programs:[managerRow(SID)],managers:{
    [SID]:{page:()=>openCodePage(SID), reply:()=>managerState(SID)}}});
  await f.api.openOpenCode(SID);await f.flush();
  // A duplicate pane tab answers over BroadcastChannel only: discovery, never authority.
  const hello=helloOf(f);
  f.api.targetMessage({data:{schemaVersion:1,type:'presence',clientId:hello.clientId,requestId:hello.requestId,editorId:'other-tab',targetRevision:2}});
  const first=f.api.state.targetAction;f.fire(first.timer);
  assert.equal(f.messages.filter(m=>m.type==='target').length,0,'no parent presence, no applied target');
  assert.match(f.el('target-status').textContent,/no parent editor answered/);
  assert.ok(f.bcMessages.some(m=>m.type==='hello'),'the duplicate-tab discovery hello is still sent');
  // A deliberate manual action addresses the real parent editor only; a later tab answer never receives it.
  f.api.useTarget();const manual=f.api.state.targetAction;const manualHello=helloOf(f);
  f.api.targetMessage({data:{schemaVersion:1,type:'presence',clientId:manualHello.clientId,requestId:manualHello.requestId,editorId:'other-tab',targetRevision:3}});
  applyPresence(f,manualHello,'parent-editor',9);
  f.fire(manual.timer);
  const target=f.messages.filter(m=>m.type==='target').at(-1);
  assert.equal(target.editorId,'parent-editor');assert.equal(target.targetRevision,9);assert.equal(target.automatic,undefined);
  assert.deepEqual(Object.keys(target).sort(),['actionId','actionRevision','backend','clientId','editorId','schemaVersion','sessionId','targetRevision','type'].sort());
  assert.equal(target.targetRef,undefined);assert.equal(target.uuid,undefined);assert.equal(target.executionId,undefined,'no Claude field is fabricated');
  // Standalone /sessions is read-only: it opens the Manager but never targets anything.
  const standalone=fixture({embedded:false,programs:[managerRow(SID)],managers:{[SID]:{page:()=>openCodePage(SID),reply:()=>managerState(SID)}}});
  await standalone.api.openOpenCode(SID);await standalone.flush();
  assert.equal(standalone.api.state.targetAction,null);
  assert.equal(standalone.messages.length,0);
  standalone.api.useTarget();
  assert.match(standalone.el('target-status').textContent,/standalone sessions is read-only/);
  assert.equal(standalone.messages.length,0);
});

// ---------------------------------------------------------------- 5 · late native action
test('a delayed native Claude target action is suppressed when a validated OpenCode Manager is selected',async()=>{
  const claudeRow={sessionRef:'mgr',uuid:CLAUDE_UUID,title:'Native Manager',source:'host',role:'Manager',state:'ready',lifecycle:'live',executionId:'cc-'+CLAUDE_UUID,generation:1,
    targetRef:CLAUDE_TARGET,capabilities:{nativeInput:true,controls:true,subagents:false},scopes:[{scopeRef:'scope',label:'main'}]};
  const f=fixture({programs:[managerRow(SID)],claude:[claudeRow],managers:{[SID]:{page:()=>openCodePage(SID),reply:()=>managerState(SID)}}});
  await f.api.browse(claudeRow);await f.flush();
  const native=f.api.state.targetAction;assert.ok(native,'the live Claude Manager auto-targets first');
  const lateTimer=f.timers.get(native.timer);assert.ok(lateTimer);
  await f.api.openOpenCode(SID);await f.flush();
  assert.equal(f.api.state.targetAction?.backend,'opencode');
  lateTimer();                                          // the old 1.5 s callback fires after the switch
  assert.equal(f.messages.filter(m=>m.type==='target'&&m.targetRef===CLAUDE_TARGET).length,0,'a late native action never retargets the editor under an OpenCode Manager');
  const hello=helloOf(f);applyPresence(f,hello);const oc=f.api.state.targetAction;f.fire(oc.timer);
  const posted=f.messages.filter(m=>m.type==='target').at(-1);
  assert.equal(posted.backend,'opencode');assert.equal(posted.sessionId,SID);assert.equal(posted.targetRef,undefined);
});

// ---------------------------------------------------------------- 6 · real app.js editor
test('the real editor adopts the validated OpenCode Manager over the same frame protocol, GET only, keeping its draft; a worker selection leaves it in place',async()=>{
  const {pane}=require('./pane-fixture.cjs'),editor=pane();
  const managerConverter=fs.readFileSync(path.join(__dirname,'../static/opencode-manager.js'),'utf8');
  vm.runInContext(managerConverter,editor.ctx);
  editor.ctx.location={origin:'http://127.0.0.1:9911'};
  const editorRequests=[];
  editor.ctx.fetch=async(url,options)=>{
    editorRequests.push({url,method:options?.method||'GET'});
    if(url.startsWith('/api/opencode/manager?'))return {ok:true,json:async()=>managerState(SID,{title:'Manager-Session-216'})};
    if(url.startsWith('/api/opencode/manager-models'))throw new Error('offline model catalog');
    throw new Error('unexpected offline editor request '+url);
  };
  editor.api.state.editorId='editor';
  editor.box.value='draft for Manager-Session-216, never lost';
  const f=fixture({programs:[managerRow(SID),workerRow(WORKER)],managers:{
    [SID]:{page:()=>{throw new Error('history unavailable for this test');},reply:()=>managerState(SID)},
    [WORKER]:{page:()=>openCodePage(WORKER,{title:'Quick worker'}),reply:null}}});
  // The editor is the actual same-origin parent frame; replies come back through the frame.
  const work=[],child={postMessage:data=>f.api.targetMessage(f.direct(data))};
  editor.el('claude-sessions-frame').contentWindow=child;
  f.parent.postMessage=data=>work.push(editor.api.handleTargetMessage({data,source:child,origin:f.origin}));
  await f.api.openOpenCode(SID);await f.flush();await Promise.all(work);
  assert.ok(f.api.state.targetAction,'the viewer queued the validated Manager target');
  assert.ok(f.api.state.targetAction.editorId,'the real editor answered the hello with its presence');
  f.fire(f.api.state.targetAction.timer);await f.flush();await Promise.all(work);
  assert.equal(editor.api.state.ocManagerTarget.sessionId,SID);
  assert.equal(editor.api.state.ocManager.title,'Manager-Session-216');
  assert.equal(editor.el('send-btn').disabled,false,'the editor shows the validated Manager Send even when the viewer history read fails');
  assert.match(editor.el('status').textContent,/Ready: sending directly to OpenCode Manager/);
  assert.equal(editor.box.value,'draft for Manager-Session-216, never lost');
  assert.ok(editorRequests.length>0);assert.ok(editorRequests.every(r=>r.method==='GET'),'selection only GETs, never launches or sends');
  // A worker selection in the viewer posts no target and leaves the editor's OpenCode Manager untouched.
  const targets=f.messages.filter(m=>m.type==='target').length,revision=editor.api.state.targetRevision;
  await f.api.openOpenCode(WORKER);await f.flush();
  assert.equal(f.messages.filter(m=>m.type==='target').length,targets);
  assert.equal(editor.api.state.ocManagerTarget.sessionId,SID);
  assert.equal(editor.api.state.targetRevision,revision);
});

test('selecting another Manager revokes the previous one at once: a failed or timed-out selection leaves no Send target in either runtime',async()=>{
  const {pane}=require('./pane-fixture.cjs'),editor=pane();
  const origin='http://127.0.0.1:9911';editor.ctx.location={origin};
  const requests=[];let ocValid=true;
  editor.ctx.fetch=async(url,options)=>{
    requests.push({url,method:options?.method||'GET'});
    if(url.startsWith('/api/opencode/manager?')){if(!ocValid)throw new Error('offline OpenCode read');return {ok:true,json:async()=>managerState(SID)};}
    if(url.startsWith('/api/opencode/manager-models'))throw new Error('offline model catalog');
    if(url.startsWith('/api/claude/events?'))throw new Error('pane host read deadline');
    throw new Error('unexpected offline editor request '+url);
  };
  editor.api.state.editorId='editor';
  const child={postMessage:()=>{}};editor.el('claude-sessions-frame').contentWindow=child;
  const action=(n,fields)=>editor.api.handleTargetMessage({data:{schemaVersion:1,type:'target',clientId:'viewer',editorId:'editor',
    actionId:'a'+n,actionRevision:n,targetRevision:editor.api.state.targetRevision,...fields},source:child,origin});
  // OpenCode Manager 180 is the Send target; Dan clicks a Claude Manager whose validation fails (slow host).
  await action(1,{backend:'opencode',sessionId:SID,automatic:true});
  assert.equal(editor.api.state.ocManagerTarget.sessionId,SID);
  await action(2,{targetRef:CLAUDE_TARGET,automatic:true});
  assert.equal(editor.api.state.ocManagerTarget,null,'the previous OpenCode Manager is no longer the Send target');
  assert.equal(editor.api.state.claudeTarget,null);
  editor.box.value='must not reach the previous Manager';
  await editor.api.send();
  assert.ok(requests.every(r=>r.method==='GET'),'Send went nowhere: no POST to the previous Manager');
  // The reverse: a Claude Manager is the target and its recovery hint must not survive a failed OpenCode selection.
  editor.api.state.claudePendingSelection=null;
  editor.api.state.claudeTarget=editor.api.state.claudeTargetHint={targetRef:CLAUDE_TARGET,source:'host',role:'Manager',executionId:'cc-'+CLAUDE_UUID,uuid:CLAUDE_UUID,generation:1};
  ocValid=false;
  await action(3,{backend:'opencode',sessionId:'ses_manager000002'});
  assert.equal(editor.api.state.claudeTarget,null,'the previous Claude Manager is no longer the Send target');
  assert.equal(editor.api.state.claudeTargetHint,null,'and no poll can quietly reselect it');
  assert.equal(editor.api.state.ocManagerTarget,null);
  // Reselecting the same Manager is not a switch: its authority stays while it revalidates.
  ocValid=true;await action(4,{backend:'opencode',sessionId:SID});
  assert.equal(editor.api.state.ocManagerTarget.sessionId,SID);
  ocValid=false;await action(5,{backend:'opencode',sessionId:SID});
  assert.equal(editor.api.state.ocManagerTarget?.sessionId,SID,'a failed revalidation of the same Manager is not a switch');
});

// ---------------------------------------------------------------- 7 · unwindowed Manager summaries: reload fallback
const claudeManagerRow=(n,name,{lifecycle='sleeping',createdAt=1792000000}={})=>({sessionRef:'reload-'+n,uuid:n.toString(16).padStart(8,'0')+'-0000-4000-8000-000000000000',
  executionId:'cc-'+n.toString(16).padStart(8,'0')+'-0000-4000-8000-000000000000',generation:1,
  source:'host',role:'manager',displayRole:'manager',sessionLabel:name,title:name,nameSource:'chosen',cwd:'/fixture/manager-sessions',
  targetRef:'target-'+n,scopes:[{scopeRef:'main-'+n,label:'main'}],createdAt,lifecycle,state:lifecycle==='live'?'ready':'collected',capabilities:{},liveState:{}});
test('the list cadence reads both unwindowed Manager summaries once and the fallback pins the highest, even an old closed one outside the window',async()=>{
  // The windowed pages (list /m style) hold only what is recent; the full Manager summaries hold the
  // closed older rows too. No Manager is chosen: the highest number wins, whatever its age or backend.
  const rows={
    windowed:claudeManagerRow(1010,'Manager-Session-216'),
    outside:claudeManagerRow(1011,'Manager-Session-221',{createdAt:1790000000}),
    ocWindowed:managerRow('ses_reload218x',{title:'Manager-Session-218',createdAt:1791000000}),
    ocOutside:managerRow('ses_reload220x',{title:'Manager-Session-220',createdAt:1789000000})};
  const f=fixture({claude:[rows.windowed],allClaude:[rows.windowed,rows.outside],programs:[rows.ocWindowed],allPrograms:[rows.ocWindowed,rows.ocOutside]});
  await f.api.list();await f.api.ocList();await f.flush();
  assert.equal(f.requests.filter(r=>r.url.includes('role=Manager')).length,1,'one full Manager summary read on the list cadence');
  assert.equal(f.requests.filter(r=>r.url==='/api/opencode/programs').length,1,'the full OpenCode program inventory is read on the same cadence');
  assert.ok(f.requests.filter(r=>r.url.includes('view=')).every(r=>!r.url.includes('role=Manager')),'the windowed inventory never carries the unwindowed role=Manager read');
  assert.deepEqual(Array.from(f.api.state.inventory,r=>r.sessionLabel),['Manager-Session-216'],'the windowed page still holds only the recent Manager');
  assert.deepEqual(Array.from(f.api.state.oc.rows,r=>r.title),['Manager-Session-218'],'the windowed OpenCode page still holds only its recent Manager');
  assert.deepEqual(Array.from(f.api.state.managerFamilyRows.claude,r=>r.sessionLabel),['Manager-Session-216','Manager-Session-221']);
  assert.deepEqual(Array.from(f.api.state.managerFamilyRows.opencode,r=>r.title),['Manager-Session-218','Manager-Session-220']);
  const pinned=()=>find(f.el('sessions-pinned'),'session-item').map(item=>find(item,'name-text')[0].textContent);
  assert.deepEqual(pinned(),['Manager-Session-221'],'the highest numbered Manager, closed and outside the window, is the reload fallback pin');
  assert.match(f.el('sessions-list').textContent,/↑ Manager-Session-221 · pinned at top/);
  const windowed=f.requests.length;await f.api.list();
  assert.equal(f.requests.filter(r=>r.url.includes('role=Manager')).length,1,'a second list inside the cadence never re-reads the full summaries');
  assert.equal(f.requests.filter(r=>r.url==='/api/opencode/programs').length,1,'nor the full OpenCode inventory');
  assert.ok(f.requests.length>windowed,'the ordinary windowed inventory still refreshes');
  assert.deepEqual(pinned(),['Manager-Session-221']);
});
test('phone Programs retains the last chosen Manager family and falls back to the newest number from the full picker rows',async()=>{
  const recent=claudeManagerRow(1012,'Manager-Session-216'),higher=claudeManagerRow(1013,'Manager-Session-219',{createdAt:1790000000});
  const worker=workerRow('ses_phonepeek',{title:'⚡ quick worker · phone'});
  const f=fixture({sidebarOnly:true,claude:[recent],allClaude:[recent,higher],programs:[],allPrograms:[]});
  await f.api.list();
  const before=f.requests.length;
  const pinned=()=>find(f.el('sessions-pinned'),'session-item').map(item=>find(item,'name-text')[0].textContent);
  // No picker choice yet: the full rows' highest number is the fallback pin, even outside the window.
  f.api.selectManagerFamily(null,[recent,higher]);
  assert.deepEqual(pinned(),['Manager-Session-219']);
  assert.equal(f.api.state.managerFamilyRows.claude.length,2,'the phone keeps every full picker row, not only the selected family');
  assert.equal(f.api.state.session,null,'Programs never becomes a second viewer or Send target');
  // A chosen Manager sticks even when the picker answers null or a non-Manager row.
  f.api.selectManagerFamily(recent,[recent,higher]);
  assert.deepEqual(pinned(),['Manager-Session-216']);
  f.api.selectManagerFamily(null,[recent,higher]);
  assert.deepEqual(pinned(),['Manager-Session-216'],'null keeps the last chosen Manager');
  f.api.selectManagerFamily(worker,[recent,higher]);
  assert.deepEqual(pinned(),['Manager-Session-216'],'a non-Manager row never replaces the choice');
  f.api.selectManagerFamily(higher,[recent,higher]);
  assert.deepEqual(pinned(),['Manager-Session-219']);
  assert.equal(f.requests.length,before,'the phone reuses its already-read picker rows: no extra reads');
});
