const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/sessions.js'),'utf8');
const shared=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-record-text.js'),'utf8');
const core=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-preview-core.js'),'utf8');
const env=x=>({schemaVersion:1,backend:'claude',...x});
const host={sessionRef:'host',uuid:'native-uuid',executionId:'cc-native-uuid',generation:2,role:'manager',source:'host',title:'Manager',cwd:'fixture',state:{owner:'owned',retry:{active:true}},capabilities:{nativeInput:false},scopes:[{scopeRef:'main',label:'main'}]};
const descriptor=value=>({schemaVersion:1,status:'available',value:{kind:'string',characters:Array.from(value).length,jsonCharacters:Array.from(JSON.stringify(value)).length,head:Array.from(value).slice(0,128).join(''),tail:Array.from(value).slice(-128).join('')}});
const event=(n,text='prose '+n)=>({recordRef:'r'+n,uuid:'uuid'+n,kind:'assistant',status:'stored',text,calls:[]});
const tool=(n,status='streaming',value='partial 🌈')=>({...event(n),status,calls:[{id:'tool'+n,block:0,callRef:'call'+n,name:'mcp__workflowy__node',status,previewData:descriptor(value),inputBytes:Buffer.byteLength(value),outputBytes:null}]});
const page=(events,extra={})=>env({sessionRef:'host',scopeRef:'main',fileEpoch:'epoch',leafRef:'leaf',targetRef:'target',status:'complete',change:'snapshot',events,hasMore:false,deltaCursor:'delta',branches:[],...extra});
function fixture(transport,{opencode,telemetry,storage,pins,sidebarOnly=false,family}={}) {
  const nodes=new Map(),requests=[],messages=[],listeners=new Map(),ocRequests=[],teleRequests=[],pinRequests=[],familyRequests=[];let selection=null,clock=null,familyPending=false;
  // loadManagerFamily's unwindowed Manager summary read: its own lane, never counted as a conversation
  // page or a windowed inventory read. Its paired OpenCode programs read keeps the test's own rows.
  const familyClaude=url=>url.startsWith('/api/claude/sessions') && /[?&]role=Manager(?:&|$)/.test(url);
  const familyAnswer=async(url,opts)=>{familyRequests.push([url,opts]);const data=await family?.(url,opts);return new Response(JSON.stringify(data ?? env({sessions:[],status:'complete',hasMore:false})));};
  class Node {
    constructor(tag='div'){this.tag=tag;this.children=[];this.parentNode=null;this.content='';this.attributes={};this.dataset={};this.value='';this.scrollTop=0;this.clientHeight=100;this.style={setProperty:(k,v)=>this.attributes[k]=v};const cls=new Set();this.classList={add:x=>cls.add(x),toggle:(x,on)=>on?cls.add(x):cls.delete(x),contains:x=>cls.has(x)};}
    set textContent(v){this.content=v;this.replaceChildren();}get textContent(){return this.content+this.children.map(x=>x.textContent).join('');}
    append(...items){for(const x of items){if(x.tag==='fragment'){this.append(...[...x.children]);continue;}x.remove();x.parentNode=this;this.children.push(x);}}
    prepend(x){x.remove();x.parentNode=this;this.children.unshift(x);}
    insertBefore(x,next){x.remove();x.parentNode=this;this.children.splice(next?this.children.indexOf(next):this.children.length,0,x);}
    replaceChildren(...items){for(const x of this.children)x.parentNode=null;this.children=[];this.append(...items);}
    remove(){if(this.parentNode){const p=this.parentNode;p.children.splice(p.children.indexOf(this),1);this.parentNode=null;}}
    setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}
    get scrollHeight(){return this.id==='history-viewport' ? el('history-events').children.length*100 : 100;}
    getBoundingClientRect(){const top=this.parentNode?.id==='history-events'?this.parentNode.children.indexOf(this)*100-el('history-viewport').scrollTop:0;return {top,bottom:top+100};}
  }
  const el=id=>{if(!nodes.has(id)){const node=new Node();node.id=id;nodes.set(id,node);}return nodes.get(id);};
  const doc={hidden:false,activeElement:null,body:{dataset:{view:sidebarOnly?'programs':''}},getElementById:el,createElement:t=>new Node(t),createDocumentFragment:()=>new Node('fragment'),getSelection:()=>selection,addEventListener:(n,f)=>listeners.set(n,f)};
  if(storage)doc.defaultView={localStorage:storage};
  let mail=env({epoch:'mail',revision:1,selection:null});
  const context={Date:class extends Date{static now(){return clock ?? Date.now();}},TextEncoder,TextDecoder,AbortController,URLSearchParams,Response,setTimeout:(f,ms)=>ms===0?queueMicrotask(f):0,clearTimeout(){},setInterval(){},BroadcastChannel:class{postMessage(m){messages.push(m);}},fetch:async(url,opts)=>{
    // OpenCode's program rows are their own channel (never counted with the Claude reads): offline unless a test answers.
    if(url==='/api/pins'){pinRequests.push([url,opts]);const d=await pins?.(url,opts) ?? {schemaVersion:1,pins:{}};return d instanceof Response?d:new Response(JSON.stringify(d));}
    // The list cadence's unwindowed Manager summary read (loadManagerFamily) is its own lane: never a
    // conversation page nor a windowed inventory read, so count-based tests keep counting what they mean.
    // Its Claude half is answered here (a test's `family` rows, else an empty complete Manager inventory);
    // the paired OpenCode programs read keeps the test's own rows.
    if(familyClaude(url)){familyPending=true;return familyAnswer(url,opts);}
    if(familyPending && url==='/api/opencode/programs'){familyPending=false;familyRequests.push([url,opts]);const d=await opencode?.(url,url);return new Response(JSON.stringify(d ?? {schemaVersion:1,backend:'opencode',status:'complete',observedAt:(clock ?? Date.now())/1000,sessions:[]}));}
    if(url.startsWith('/api/opencode/')){ocRequests.push(url);const legacy=url.startsWith('/api/opencode/programs?')?'/api/opencode/programs':url;let d=await opencode?.(legacy,url);if(d instanceof Response)return d;if(legacy==='/api/opencode/programs' && d?.status==='complete')d={observedAt:(clock ?? Date.now())/1000,...d};return new Response(JSON.stringify(d ?? {schemaVersion:1,backend:'opencode',status:'unavailable',error:'offline fixture',sessions:[]}));}
    // Telemetry reads are their own channel too (never counted with the Claude reads): offline unless a test answers.
    if(url.startsWith('/api/telemetry')){teleRequests.push(url);const d=await telemetry?.(url);return new Response(JSON.stringify(d ?? {schemaVersion:1,backend:'telemetry',status:'unavailable',error:'offline fixture',sessions:{}}));}
    requests.push([url,opts]);let data=await transport?.(url,opts);
    if(data===undefined){if(url.startsWith('/api/claude/sessions'))data=env({sessions:[host],status:'complete',hasMore:false});else if(url.startsWith('/api/claude/turns'))data=env({uuid:new URLSearchParams(url.split('?')[1]).get('uuid'),turnFacts:null});else if(url.startsWith('/api/claude/events'))data=page([tool(1)]);else if(url==='/api/claude/inspect'){mail=env({epoch:'mail',revision:mail.revision+1,selection:JSON.parse(opts.body)});data=mail;}else if(url==='/api/tool-selection')data=mail;else throw new Error('No live network: '+url);}
    return data instanceof Response?data:new Response(JSON.stringify(data));
  }};
  vm.runInNewContext(shared+'\n'+core+'\n'+source,context);
  const api=context.opcClaudeHistory.create(doc);
  return {api,doc,el,requests,ocRequests,teleRequests,pinRequests,familyRequests,messages,setNow:n=>{clock=n;},select:node=>{selection=node?{isCollapsed:false,rangeCount:1,anchorNode:node,focusNode:node,getRangeAt:()=>({intersectsNode:n=>{for(let at=node;at;at=at.parentNode)if(at===n)return true;return false;}})}:null;listeners.get('selectionchange')?.();},flush:()=>new Promise(r=>setImmediate(r))};
}
test('Programs opens host summaries only; native history is one deliberate read and stays through later host refreshes',async()=>{
  const native={...host,source:'linux-history',sessionRef:'native-old',executionId:null,uuid:'native-old',title:'An unmanaged transcript'};
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:new URLSearchParams(url.split('?')[1]).get('native')==='0'?[host]:[host,native],status:'complete',hasMore:false}):undefined);
  await f.api.list();assert.equal(f.api.state.inventory.length,1);assert.match(f.requests[0][0],/details=0&native=0/);
  await f.api.list(true);assert.equal(f.api.state.inventory.length,2);assert.equal(f.api.state.nativeHistoryLoaded,true);
  await f.api.list();assert.equal(f.api.state.inventory.length,2,'a fast host refresh keeps deliberately loaded native history');
  assert.equal(f.requests.filter(([url])=>url.startsWith('/api/claude/sessions') && !url.includes('native=0')).length,1);
});
test('Programs Reconnect refreshes native handles only for the stored conversation actually selected',async()=>{
  const native={...host,source:'linux-history',sessionRef:'native-old',executionId:null,uuid:'native-uuid',title:'Stored conversation'};
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:[{...native,sessionRef:'native-fresh'}],status:'complete',hasMore:false}):url.startsWith('/api/claude/events')?page([],{sessionRef:'native-fresh'}):undefined);
  f.api.state.session=native;await f.api.reconnect();
  assert.equal(f.api.state.session.sessionRef,'native-fresh');
  assert.ok(f.requests.some(([url])=>url==='/api/claude/sessions?details=0&view=week'),'selected stored conversation re-acquires its native row instead of reusing expired cached handles');
});
test('Programs cached visible Claude facts reach program aggregates before the tree is rebuilt',()=>{
  const f=fixture(),row=cond(1,'FACTS1',1);f.api.state.inventory=[row];f.api.state.inventoryAt=Date.now();
  f.api.state.claudeTurns.set(row.uuid,{generation:row.generation,data:{status:'complete',firstAt:100,lastAt:150}});f.api.inventory();
  assert.equal(f.api.state.tree.programs[0].facts.duration,50);
});
test('Programs optional details have two slots, newest active work leads, collapsed/offscreen work is not read, and activity bypasses them',async()=>{
  const f=fixture(undefined,{opencode:url=>url==='/api/opencode/activity'?{schemaVersion:1,backend:'opencode',status:'complete',observedAt:100,active:[],sessions:[]}:undefined});
  f.setNow(100000);const started=[],release=[];
  const node={isConnected:true,getBoundingClientRect:()=>({top:10,bottom:20})};
  for(const [key,active,recent] of [['history',false,999],['older-active',true,10],['newer-active',true,20]])
    f.api.state.detailWanted.set(key,{nodes:[node],active,recent,read:()=>{started.push(key);return new Promise(resolve=>release.push(resolve));}});
  f.api.state.detailWanted.set('offscreen',{nodes:[{...node,getBoundingClientRect:()=>({top:2000,bottom:2100})}],active:true,recent:1000,read:()=>{throw Error('offscreen read');}});
  f.api.detailFlush();await f.flush();assert.deepEqual(started,['newer-active','older-active']);assert.equal(f.api.state.detailRunning.size,2);
  await f.api.pollOpenCodeActivity();assert.equal(f.ocRequests.filter(url=>url==='/api/opencode/activity').length,1,'fresh activity never waits on optional reads');
  f.api.state.detailWanted=new Map(); // a collapse/filter redraw drops work that is no longer drawn
  for(const finish of release)finish();await f.flush();await f.flush();assert.deepEqual(started,['newer-active','older-active']);
});
test('Programs never promotes an un-timestamped or stale cached OpenCode busy fact to current running',()=>{
  const f=fixture();f.setNow(100000);const row={id:'ses_loading0001',busy:true};
  f.api.state.oc={rows:[row],status:'complete',stale:false};assert.equal(f.api.ocEvidence(row).busy,null);
  f.api.state.oc.observedAt=80000;assert.equal(f.api.ocEvidence(row).busy,null);
  f.api.state.oc.observedAt=99000;assert.equal(f.api.ocEvidence(row).busy,true);
});
// Every session row in the sidebar, across its groups.
const sidebarItems=f=>{const found=[];const walk=n=>{if(n.className==='session-item')found.push(n);for(const c of n.children||[])walk(c);};walk(f.el('sessions-pinned'));walk(f.el('sessions-list'));return found;};
// Heading labels/counts are independent of the new section controls (also works on archived bases).
const headingText=heading=>heading.children.filter(n=>!['section-toggle','section-active'].includes(n.className)).map(n=>n.textContent).join('');
const headingLabel=heading=>heading.children.find(n=>!n.className)?.textContent;
test('the whole list is grouped (true Managers first, then Other) with a real-role badge; filters cover the whole list and browsing never grants input',async()=>{
  const rows=[host,{...host,sessionRef:'conductor',uuid:'c-uuid',role:'Conductor'},{...host,sessionRef:'worker',uuid:'w-uuid',role:'worker'},{...host,sessionRef:'history',uuid:'h-uuid',source:'linux-history',role:'manager'},{...host,sessionRef:'other',uuid:'o-uuid',role:'mystery'}];
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:rows,status:'complete',hasMore:false}):undefined);
  await f.api.list();assert.match(f.el('sessions-list').textContent,/Manager Sessions · 1.*Manager.*Other · 4.*Conductor.*Worker.*History.*Unknown host role/s);
  assert.equal(f.el('sessions-list').children.find(g=>g.dataset.group==='managers').children.length,2); // heading + the one Manager
  for(const [role,count] of [['manager',1],['conductor',1],['worker',1],['history',1],['unknown',1],['all',5]]){f.el('sessions-role').value=role;f.el('sessions-role').onchange();assert.equal(sidebarItems(f).length,count);}
  const reads=f.requests.length;f.el('sessions-role').value='history';f.el('sessions-role').onchange();assert.equal(f.requests.length,reads);assert.match(f.el('sessions-filter-status').textContent,/1 shown · 1 match the filter · 5 in the whole inventory/);
  await f.api.browse(host);assert.match(f.el('history-capabilities').textContent,/Connecting/);assert.match(f.el('history-capabilities').textContent,/readiness is checked automatically/);assert.match(f.el('history-source').textContent,/cc-native-uuid.*generation 2/);
  assert.equal(f.messages.length,0);assert.equal(f.api.state.accepted,null);
});
test('native children appear live under their parent; child clicks/polls/Inspect never send, target or mix the main view',async()=>{
  const parent={...host,capabilities:{nativeInput:true,subagents:true}},child={...host,source:'native',role:'subagent',parentUuid:host.uuid,
    sessionRef:'child',title:'Manager-Session-181 ▸ manager-quick · fixture task',agentId:'a1',capabilities:{nativeInput:false,subagents:true},scopes:[{label:'agent:a1',scopeRef:'child-scope'}]};
  let ready=false,childReads=0;
  const f=fixture(url=>{
    if(url.startsWith('/api/claude/subagents'))return env({sessions:ready?[child]:[],hasMore:false,status:'complete'});
    if(url.startsWith('/api/claude/events?sessionRef=child'))return page([{...tool(9,'pending-or-missing'),scope:'agent:a1',text:++childReads===1?'child working':'child done',thinking:[{blockIndex:1,text:'child thinking',availability:'stored',streaming:false}]}],{sessionRef:'child',scopeRef:'child-scope',targetRef:null});
  });
  await f.api.browse(parent);await f.api.poll();assert.equal(f.api.state.children.length,0);
  ready=true;f.api.state.childNextPoll=0;await f.api.poll();assert.match(f.el('subagents-list').textContent,/fixture task.*manager-quick/);
  const label=f.el('subagents-list').children[0].children;assert.equal(label[0].textContent,'fixture task');assert.equal(label[0].className,'child-title');assert.equal(label[1].textContent,'manager-quick');assert.equal(label[1].className,'child-sub'); // Dan: what it is doing leads, the agent name sits dim below
  assert.match(f.el('subagents-list').children[0].title,/Manager-Session-181 ▸ manager-quick/);
  assert.equal(f.api.state.session,parent);assert.equal(f.api.state.target,'target');
  await f.el('subagents-list').children[0].onclick();await f.flush();
  assert.equal(f.api.state.session.sessionRef,'child');assert.equal(f.api.state.target,null);assert.equal(f.el('history-target').disabled,true);
  assert.equal(f.api.pollDelay(),600);assert.match(f.el('history-events').textContent,/child working.*child thinking|child thinking.*child working/s);
  f.api.state.nextPoll=0;await f.api.poll();assert.match(f.el('history-events').textContent,/child done/);
  assert.equal(f.messages.length,0);assert.ok(f.requests.every(([url])=>!url.includes('/send')&&!url.includes('/control')&&!url.includes('/context')));
  f.doc.hidden=true;const reads=f.requests.length;await f.api.poll();assert.equal(f.requests.length,reads);
});
test('a native expander requires known children: the watch discovers silently, an empty answer draws no footer, and More/Refresh page on the row',async()=>{
  const parent={...host,sessionRef:'exp-parent',uuid:U(210),executionId:cc(210),sessionLabel:'Manager-Session-210',role:'Manager',
    capabilities:{nativeInput:true,subagents:true},liveState:{}};
  const childOf=(id,uuid,agentId)=>({...host,source:'native',role:'subagent',parentUuid:uuid,agentId,sessionRef:id,
    title:'▸ ⚡ quick helper · '+id,scopes:[{label:'agent:'+agentId,scopeRef:id+'-scope'}]});
  const childA=childOf('exp-a',parent.uuid,'a1'),childB=childOf('exp-b',parent.uuid,'a2');
  let stage='empty',failNext=false;
  const f=fixture(url=>{
    if(url.startsWith('/api/claude/sessions'))return env({sessions:[parent],status:'complete',hasMore:false});
    if(url.startsWith('/api/claude/subagents')){
      const q=new URLSearchParams(url.split('?')[1]),ref=q.get('sessionRef');
      if(ref==='peek-row')return env({sessions:[childOf('peek-child',U(220),'p1')],hasMore:false,status:'complete'});
      if(failNext){failNext=false;throw new Error('discovery offline');}
      if(stage==='empty')return env({sessions:[],hasMore:false,status:'complete'});
      return q.get('cursor')?env({sessions:[childB],hasMore:false,status:'complete'})
        :env({sessions:[childA],hasMore:true,nextCursor:'kids-2',status:'complete'});
    }
    if(url.startsWith('/api/claude/events'))return page([event(1)],{sessionRef:new URLSearchParams(url.split('?')[1]).get('sessionRef')});
  });
  await f.api.browse(parent);f.api.state.inventory=[parent];f.api.inventory();
  const rowOf=name=>sidebarItems(f).find(n=>find(n,'name-text')[0]?.textContent===name);
  // The selected parent is watched: its successful empty answer is silent, draws no expander, no footer
  // and no '(no subagents)' line.
  await f.api.poll();
  assert.equal(f.requests.filter(([url])=>url.startsWith('/api/claude/subagents')).length,1);
  assert.equal(f.api.state.childLoaded,true);assert.equal(f.el('subagents-status').textContent,'');assert.equal(f.el('subagents-block').hidden,true);
  assert.equal(find(rowOf('Manager-Session-210'),'session-toggle').length,0);
  assert.doesNotMatch(f.el('sessions-list').textContent+f.el('sessions-pinned').textContent,/no subagents/);
  // Real children draw one expander and the live child list; success leaves the status line empty.
  stage='page';f.api.state.childNextPoll=0;await f.api.poll();
  assert.equal(find(rowOf('Manager-Session-210'),'session-toggle')[0].textContent,'▾');
  assert.equal(find(rowOf('Manager-Session-210'),'session-toggle')[0].title,'Hide native subagents (1)');
  assert.match(f.el('subagents-list').textContent,/exp-a/);assert.equal(f.el('subagents-status').textContent,'');
  assert.equal(f.el('subagents-more').hidden,false);assert.equal(f.el('subagents-refresh').hidden,true);
  const controlBox=f.el('subagents-more').parentNode;
  assert.equal(controlBox.className,'session-child-controls','paging controls ride the row metadata');
  assert.equal(controlBox.parentNode.className,'session-meta');assert.equal(controlBox.parentNode.parentNode.className,'session-open');
  assert.equal(f.el('subagents-block').hidden,false);
  // More is real paging: the next cursor page replaces the shown page, then More disappears and
  // Refresh (the way back to the first page) becomes live.
  f.el('subagents-more').onclick({stopPropagation(){}});await f.flush();
  assert.match(f.el('subagents-list').textContent,/exp-b/);assert.doesNotMatch(f.el('subagents-list').textContent,/exp-a/);
  assert.equal(f.el('subagents-status').textContent,'');assert.equal(f.api.state.childNext,null);
  assert.equal(f.el('subagents-more').hidden,true);assert.equal(f.el('subagents-refresh').hidden,false);
  // A failed deliberate refresh keeps the known page, the real warning and the retry control.
  failNext=true;f.el('subagents-refresh').onclick({stopPropagation(){}});await f.flush();await f.flush();
  assert.match(f.el('subagents-status').textContent,/Subagent discovery unavailable: discovery offline · last page retained/);
  assert.equal(f.el('subagents-status').dataset.error,'true');
  assert.equal(f.el('subagents-block').hidden,false);assert.match(f.el('subagents-list').textContent,/exp-b/);
  assert.equal(f.el('subagents-refresh').hidden,false);
  // A visible non-watched capable row is discovered once through the optional queue, then draws its expander.
  const peekable={...host,sessionRef:'peek-row',uuid:U(220),executionId:cc(220),role:'worker',displayRole:'quick-worker',
    capabilities:{nativeInput:false,subagents:true},title:'peek row',cwd:'/r/quick-worker'};
  f.api.state.inventory=[parent,peekable];f.api.inventory();
  assert.equal(find(rowOf('peek row'),'session-toggle').length,0,'a capability flag alone never draws an expander');
  assert.ok(f.api.state.detailWanted.has('children:'+f.api.identityOf(peekable)));
  f.api.detailFlush();await f.flush();await f.flush();
  assert.equal(f.requests.filter(([url])=>url.startsWith('/api/claude/subagents')&&url.includes('sessionRef=peek-row')).length,1);
  assert.equal(find(rowOf('peek row'),'session-toggle').length,1);assert.equal(find(rowOf('peek row'),'session-toggle')[0].textContent,'▸');
  f.api.inventory();f.api.detailFlush();await f.flush();
  assert.equal(f.requests.filter(([url])=>url.startsWith('/api/claude/subagents')&&url.includes('sessionRef=peek-row')).length,1,'a cached row is never re-read');
  assert.equal(f.messages.length,0);
});
test('forty dense bars consume only event summaries; long Unicode preview tails use the shared core',async()=>{
  const value='🌈'.repeat(100001)+'TAIL\nnext';const f=fixture(()=>page(Array.from({length:40},(_,i)=>tool(i,'streaming',value))));await f.api.browse(host);
  assert.equal(f.api.state.rows.size,40);assert.equal(f.requests.length,1);assert.match(f.api.state.rows.get('r0').calls[0].node.children[1].textContent,/TAIL\\nnext/);assert.doesNotMatch(f.requests.map(x=>x[0]).join(' '),/tool-record|inspect|artifact/);
});
test('partial→final updates the mounted article/prose/button in place with focus/scroll and exact click identity',async()=>{
  const f=fixture();await f.api.browse(host);f.api.reconcile([event(2),event(3)],'forward','epoch');const row=f.api.state.rows.get('r1'),prose=row.prose,button=row.calls[0].node;
  f.api.setFollow(false,{byScroll:true});f.doc.activeElement=button;f.el('history-viewport').scrollTop=25;const before=f.el('history-viewport').scrollTop;
  const final={...tool(1,'stored','Final 😀 input'),text:'Final prose'};final.calls[0].outputBytes=262144;f.api.reconcile([final],'delta','epoch');
  assert.equal(f.api.state.rows.get('r1'),row);assert.equal(row.prose,prose);assert.equal(row.calls[0].node,button);assert.equal(f.doc.activeElement,button);assert.equal(f.el('history-viewport').scrollTop,before);assert.equal(prose.textContent,'Final prose');assert.match(button.textContent,/Final.*256.0 KiB/s);
  button.onclick();await f.flush();await f.flush();const post=f.requests.find(([u])=>u==='/api/claude/inspect');assert.equal(JSON.parse(post[1].body).callRef,'call1');assert.equal(f.api.state.accepted.callRef,'call1');assert.equal(f.api.state.order.filter(k=>k==='r1').length,1);
});
test('active prose and preview text selections defer repaint through multiple updates; latest applies on release',async()=>{
  for(const kind of ['prose','preview']){
    const f=fixture();await f.api.browse(host);const row=f.api.state.rows.get('r1'),button=row.calls[0].node,node=kind==='prose'?row.prose:button.children[1],before=node.textContent;
    f.select(node);f.api.reconcile([{...tool(1,'streaming','intermediate'),text:'intermediate prose'}],'delta','epoch');f.api.reconcile([{...tool(1,'stored','final input'),text:'final prose'}],'delta','epoch');
    assert.equal(node.textContent,before);assert.ok(row.deferred);assert.equal(row.calls[0].node,button);assert.equal(f.api.state.rows.size,1);
    f.select(null);assert.equal(row.deferred,null);assert.equal(row.prose.textContent,'final prose');assert.match(button.textContent,/final input/);assert.equal(row.calls[0].node,button);assert.equal(f.requests.length,1);
  }
});
test('summary-only and full-event summaryUpdates reconcile selected tools, input/status/counts without body reads',async()=>{
  let delta=false;const f=fixture(()=>delta?page([],{summaryUpdates:[{recordRef:'r1',block:0,id:'tool1',status:'streaming',inputBytes:90000,outputBytes:null,previewData:descriptor('new preview')}, {...tool(2,'stored','full event')}] }):page([tool(1),tool(2)]));
  await f.api.browse(host);const row=f.api.state.rows.get('r1'),button=row.calls[0].node;f.select(button.children[1]);delta=true;await f.api.page('delta','delta');assert.doesNotMatch(button.textContent,/new preview/);assert.match(f.api.state.rows.get('r2').node.textContent,/full event/);
  f.select(null);assert.match(button.textContent,/new preview/);assert.match(button.textContent,/87.9 KiB/);assert.equal(f.api.state.rows.get('r1'),row);assert.equal(f.requests.length,2);
});
test('every server call status, including not-final-fragment, survives summary updates; future cosmetic metadata is unknown',async()=>{
  for(const status of ['not-final-fragment','streaming','stored','error','pending-or-missing','ambiguous','future-status']) {
    let delta=false;const f=fixture(()=>delta?page([event(2)],{summaryUpdates:[{recordRef:'r1',block:0,id:'tool1',status,outputBytes:status==='future-status'?'unknown':null,inputBytes:'cosmetic'}],incidents:'future',traces:{},diagnostics:'future',branches:{}}):page([tool(1)],{liveState:{state:'ready',activity:{state:'tool',tool:'fixture'}}}));
    await f.api.browse(host);const row=f.api.state.rows.get('r1');delta=true;await f.api.page('delta','delta');
    assert.equal(f.api.state.blocked,false);assert.equal(f.api.state.failures,0);assert.equal(f.el('history-status').dataset.error,'false');assert.equal(f.api.state.rows.size,2);
    assert.equal(f.api.state.rows.get('r1'),row);
    assert.match(row.calls[0].node.textContent,new RegExp(status==='not-final-fragment'?'streaming.*not-final fragment':status==='future-status'?'unknown':status==='pending-or-missing'?'running now':status));
    // A result-less call in the live Manager's open turn is the one running now: yellow.
    assert.equal(row.calls[0].node.classList.contains('running'),['pending-or-missing','not-final-fragment','streaming'].includes(status));
  }
});
test('same uuid+scope is mounted once even when a page or resident update re-keys its recordRef',async()=>{
  let delta=false;const f=fixture(()=>delta?page([{...event(1,'re-keyed'),recordRef:'replacement'}],{eventUpdates:[{...event(1,'final'),recordRef:'replacement'}]}):page([event(1),event(2),event(3)]));
  await f.api.browse(host);const row=f.api.state.rows.get('r1');f.api.setFollow(false,{byScroll:true});f.el('history-viewport').scrollTop=25;const scroll=f.el('history-viewport').scrollTop;
  delta=true;await f.api.page('delta','delta');assert.equal(f.api.state.rows.size,3);assert.equal(f.el('history-events').children.length,3);assert.equal(f.api.state.rows.get('r1'),row);assert.equal(row.prose.textContent,'final');assert.equal(f.el('history-viewport').scrollTop,scroll);
  f.api.reconcile([{...event(1),recordRef:'other-scope',scope:'worker'}],'forward','epoch');assert.equal(f.api.state.rows.size,4); // no text-only/uuid-only dedup
  assert.throws(()=>f.api.reconcile([{...event(2),recordRef:'r1'}],'delta','epoch'),/conflicting event identity/);
});
test('resumed task IDs stay generation-scoped so a newest page retains every row after the resume',async()=>{
  const task=(n,generation)=>({...event(n,'task generation '+generation),seq:n,uuid:'task:doorbell',kind:'task',scope:'main',generation});
  const rows=[{...event(0),seq:0},task(1,1),{...event(2),seq:2},task(3,2),{...event(4,'Manager recovered and programs resumed'),seq:4}];
  const f=fixture(()=>page(rows));await f.api.browse(host);
  assert.equal(f.api.state.rows.size,5);
  assert.deepEqual(Array.from(f.api.state.order,k=>f.api.state.rows.get(k).seq),[0,1,2,3,4]);
  assert.equal(f.api.state.dropped,false);
  assert.match(f.el('history-events').textContent,/Manager recovered and programs resumed/);
});
test('resumed task IDs append across pages and still deduplicate re-keyed updates within one generation',async()=>{
  const task=(n,generation,text)=>({...event(n,text),seq:n,uuid:'task:doorbell',kind:'task',scope:'main',generation});
  const f=fixture(()=>page([task(0,1,'old doorbell'),{...event(1),seq:1}]));await f.api.browse(host);
  f.api.reconcile([task(2,2,'new notification'),{...event(3,'new reply'),seq:3}],'forward','epoch');
  assert.equal(f.api.state.rows.size,4);
  assert.deepEqual(Array.from(f.api.state.order,k=>f.api.state.rows.get(k).seq),[0,1,2,3]);
  assert.match(f.api.state.rows.get('r0').node.textContent,/old doorbell/);
  const row=f.api.state.rows.get('r2');
  f.api.reconcile([{...task(2,2,'updated notification'),recordRef:'new-task-handle'}],'delta','epoch');
  assert.equal(f.api.state.rows.size,4);
  assert.equal(f.api.state.rows.get('r2'),row);
  assert.match(row.node.textContent,/updated notification/);
  assert.equal(f.api.state.dropped,false);
});
test('replayed prose UUIDs still deduplicate across process generations',async()=>{
  const f=fixture(()=>page([{...event(0,'original prose'),seq:0,generation:1}]));await f.api.browse(host);
  const row=f.api.state.rows.get('r0');
  f.api.reconcile([{...event(0,'replayed prose'),seq:0,generation:2,recordRef:'resumed-prose-handle'}],'delta','epoch');
  assert.equal(f.api.state.rows.size,1);
  assert.equal(f.api.state.rows.get('r0'),row);
  assert.match(row.prose.textContent,/replayed prose/);
});
test('a host final native uuid replaces its synthetic streaming uuid under the same record witness',async()=>{
  const f=fixture(()=>page([{...event(1,'partial'),uuid:'synthetic',partial:true}]));await f.api.browse(host);const row=f.api.state.rows.get('r1');
  f.api.reconcile([{...event(1,'final'),uuid:'native',partial:false}],'summary','epoch');
  f.api.reconcile([{...event(1,'final'),uuid:'native',recordRef:'rekeyed'}],'summary','epoch');
  assert.equal(f.api.state.rows.size,1);assert.equal(f.api.state.rows.get('r1'),row);assert.equal(row.prose.textContent,'final');assert.equal(f.api.state.identities.size,1);
});
test('malformed call identity refuses before deferring a selected event, but cosmetic metadata keeps its prose',async()=>{
  let delta=false;const f=fixture(()=>delta?page([{...event(1,'retained prose'),status:'future-status',thinking:'future'},{...tool(2,'future-status'),calls:[{...tool(2).calls[0],outputBytes:'future'}]}]):page([tool(1)]));
  await f.api.browse(host);const row=f.api.state.rows.get('r1');f.select(row.prose);const signature=row.signature,used=f.api.state.used;
  assert.throws(()=>f.api.reconcile([{...tool(1),calls:[{...tool(1).calls[0],callRef:'bad/ref'}]}],'delta','epoch'),/call witness identity schema/);
  assert.equal(row.signature,signature);assert.equal(f.api.state.used,used);assert.equal(row.deferred,null);
  delta=true;await f.api.page('delta','delta');f.select(null);assert.equal(row.prose.textContent,'retained prose');assert.equal(f.api.state.blocked,false);assert.equal(f.api.state.rows.size,2);assert.equal(f.el('history-status').dataset.error,'false');
});
test('transient indexing failures stay neutral and capped at three seconds, then resume with the same cursor',async()=>{
  let fail=false,stage=0;const f=fixture(url=>{
    if(!url.startsWith('/api/claude/events'))return;
    if(fail)throw new Error('offline');
    return stage++===0?page([event(1)],{status:'indexing',hasMore:true,nextCursor:'scan',indexingContinuation:'scan'}):page([event(2)]);
  });f.setNow(100000);await f.api.browse(host);fail=true;
  for(let n=0;n<6;n++){const now=100000+n*3000;f.setNow(now);await f.api.poll();assert.equal(f.api.state.nextPoll,now+3000);assert.equal(f.el('history-status').dataset.error,'false');assert.match(f.el('history-status').textContent,/Loading history.*Retry in 3 s/);}
  fail=false;f.setNow(f.api.state.nextPoll);await f.api.poll();assert.equal(f.api.state.failures,0);assert.equal(f.api.state.rows.size,2);assert.equal(f.api.state.indexingContinuation,null);
});
test('selected and focused rows survive bounded eviction while held inspector identity remains independent',async()=>{
  const f=fixture();await f.api.browse(host);const row=f.api.state.rows.get('r1');f.select(row.prose);f.doc.activeElement=row.calls[0].node;f.api.state.accepted={key:row.calls[0].key,callRef:'call1',epoch:'mail',revision:1};
  f.api.reconcile(Array.from({length:240},(_,i)=>event(10+i)),'delta','epoch');assert.equal(f.api.state.rows.get('r1'),row);assert.equal(f.api.state.rows.size,200);assert.equal(f.api.state.accepted.callRef,'call1');
  f.select(null);f.doc.activeElement=null;f.el('history-viewport').scrollTop=500;f.api.reconcile(Array.from({length:210},(_,i)=>event(300+i)),'delta','epoch');assert.equal(f.api.state.rows.has('r1'),false);assert.equal(f.api.state.accepted.callRef,'call1');assert.equal(f.requests.length,1);
});
test('retry/silence/unknown/background/escalation/settings are disclosed as host evidence, never guessed idle',async()=>{
  const liveState={owner:'unknown',nativeRetry:{active:true,attempt:3},silence:{duration:601},backgroundTasks:{task:'running'},settings:{status:'unverified'},escalation:{state:'pending'}};
  const f=fixture(()=>page([tool(1)],{liveState,incidents:[{kind:'silence',status:'observed'},{kind:'unknown_error',escalation:'Dan'}],targetInfo:{executionId:'cc-exact',generation:3,capabilities:{nativeInput:false}}}));await f.api.browse(host);
  for(const word of ['unknown','nativeRetry','silence','backgroundTasks','unverified','pending','unknown_error'])assert.ok(f.el('history-live').textContent.includes(word));assert.match(f.el('history-source').textContent,/cc-exact.*generation 3/);assert.doesNotMatch(f.el('history-live').textContent,/"busy"|"idle"/);
  await f.api.browse({...host,source:'linux-history'});assert.equal(f.el('history-live').textContent,'Stored history · no host live-state evidence.');
});
test('empty delta pages retain bounded incident evidence; repeats deduplicate and a source reset clears it',async()=>{
  let incidents=[{execution_id:'cc-uuid',generation:1,incident_id:'first',category:'unknown'}];
  const f=fixture(()=>page([tool(1)],{incidents}));await f.api.browse(host);
  incidents=[];await f.api.page('delta','delta');assert.equal(f.api.state.incidents.length,1);assert.match(f.el('history-observation').textContent,/unknown.*task acceptance: unjudged/);
  incidents=[{execution_id:'cc-uuid',generation:1,incident_id:'first',category:'unknown'}];await f.api.page('delta','delta');assert.equal(f.api.state.incidents.length,1);
  for(let pageNo=0;pageNo<2;pageNo++){
    incidents=Array.from({length:100},(_,n)=>({execution_id:'cc-uuid',generation:1,incident_id:'i'+(pageNo*100+n),category:'permission'}));
    await f.api.page('delta','delta');
  }
  assert.equal(f.api.state.incidents.length,100);assert.equal(f.api.state.incidentOverflow,true);assert.match(f.el('history-live').textContent,/older observed entries evicted/);
  incidents=[];await f.api.browse({...host,sessionRef:'other'});assert.equal(f.api.state.incidents.length,0);
});
test('replayed records are deduplicated; an identity gap keeps the view and resyncs it by position instead of splicing',async()=>{
  let changed=false;const f=fixture(()=>page([tool(1)],changed?{fileEpoch:'replacement',change:'gap',diagnostics:[{status:'gap'}]}:{}));await f.api.browse(host);const row=f.api.state.rows.get('r1');await f.api.page('delta','delta');assert.equal(f.api.state.rows.get('r1'),row);assert.equal(f.api.state.rows.size,1);
  changed=true;await f.api.page('delta','delta');assert.equal(f.api.state.blocked,false);assert.ok(f.api.state.resync,'an identity gap starts a checked resync, never a splice');
  assert.equal(f.api.state.rows.get('r1'),row);assert.equal(f.api.state.rows.size,1);assert.match(f.el('history-status').textContent,/Reconnecting: the server no longer knows this view’s handles/);
});
test('backend eventUpdates completes a mounted partial event without duplicate rows or off-viewport replacements',async()=>{
  let delta=false;const f=fixture(()=>delta?page([],{eventUpdates:[{...tool(1,'stored','complete input'),text:'complete text'},tool(99)]}):page([tool(1)]));await f.api.browse(host);const row=f.api.state.rows.get('r1');delta=true;await f.api.page('delta','delta');
  assert.equal(f.api.state.rows.get('r1'),row);assert.match(row.node.textContent,/complete text.*complete input/s);assert.equal(f.api.state.rows.has('r99'),false);assert.equal(f.api.state.rows.size,1);assert.equal(f.requests.length,2);
});
test('replacement-body envelope breaks refuse without advancing the cursor; they are not cosmetic status fields',async()=>{
  for(const eventUpdates of ['malformed',[null],[{recordRef:'bad/ref'}]]) {
    let delta=false;const f=fixture(()=>delta?page([],{eventUpdates,deltaCursor:'lost-update'}):page([event(1)]));
    await f.api.browse(host);const row=f.api.state.rows.get('r1'),cursor=f.api.state.delta;delta=true;await f.api.page('delta','delta');
    assert.equal(f.api.state.blocked,true);assert.equal(f.api.state.delta,cursor);assert.equal(f.api.state.rows.get('r1'),row);assert.match(f.el('history-status').textContent,/invalid live event update schema/);
  }
});
test('cold host tail scans continue automatically and forward pages replace the same last prose row in full',async()=>{
  const reply='Sanitized full reply line.\n'.repeat(70)+'LAST-REPLY-TAIL',prefix=reply.slice(0,31);
  let stage=0;const f=fixture(url=>url.startsWith('/api/claude/events')?[
    page([{...event(1,prefix),partial:true}],{status:'indexing',hasMore:true,nextCursor:'scan1',indexingContinuation:'scan1'}),
    page([],{eventUpdates:[{...event(1,reply.slice(0,900)),partial:true}],status:'indexing',hasMore:true,nextCursor:'scan2',indexingContinuation:'scan2'}),
    page([{...event(2,reply),kind:'result',receipt:{subtype:'success'}}],{eventUpdates:[{...event(1,reply),partial:false}]})
  ][stage++]:undefined);
  await f.api.browse(host);const row=f.api.state.rows.get('r1'),prose=row.prose;
  assert.equal(prose.textContent,prefix);assert.equal(f.api.state.next,'scan1');
  assert.match(f.el('history-status').textContent,/Loading history … 1 events/);assert.equal(f.el('history-status').dataset.error,'false');
  // No clock advance / 600 ms interval per page: one poll drains both continuations.
  await f.api.poll();assert.equal(prose.textContent,reply);
  assert.equal(f.api.state.rows.get('r1'),row);assert.equal(row.prose,prose);assert.equal(f.api.state.rows.size,2);
  assert.equal(f.api.state.indexingContinuation,null);assert.equal(f.api.state.next,null);
  assert.deepEqual(f.requests.map(([u])=>new URLSearchParams(u.split('?')[1]).get('cursor')),[null,'scan1','scan2']);
  assert.equal(f.el('history-events').textContent.split('LAST-REPLY-TAIL').length-1,1);assert.equal(f.messages.length,0);
});
test('a live delta replay backlog continues after hasMore instead of stranding the last streamed prefix',async()=>{
  const reply='Line one\nLine two\nLine three\n'+('Later complete paragraph 🌈\n'.repeat(60))+'FINAL-TAIL';
  let stage=0;const f=fixture(url=>url.startsWith('/api/claude/events')?[
    page([{...event(1,'Line one'),partial:true}]),
    page([],{eventUpdates:[{...event(1,reply.slice(0,30)),partial:true}],status:'indexing',hasMore:true,nextCursor:'live-scan',indexingContinuation:'live-scan'}),
    page([{...event(2,reply),kind:'result'}],{eventUpdates:[{...event(1,reply),partial:false}]})
  ][stage++]:undefined);
  await f.api.browse(host);const row=f.api.state.rows.get('r1');
  f.select(row.prose);f.setNow(f.api.state.nextPoll);await f.api.poll();assert.equal(row.prose.textContent,'Line one');assert.ok(row.deferred);
  f.select(null);assert.equal(row.prose.textContent,reply);assert.equal(row.deferred,null);assert.equal(f.api.state.order.filter(k=>k==='r1').length,1);
  assert.match(f.requests[1][0],/cursor=delta/);assert.match(f.requests[2][0],/cursor=live-scan/);
});
test('scan observation respects hidden/no-overlap/backoff and, with follow paused, never auto traverses ordinary later history',async()=>{
  let fail=false,hold=false,release,stage=0;const f=fixture(async url=>{
    if(!url.startsWith('/api/claude/events'))return;
    if(fail)throw new Error('offline replay');if(hold)await new Promise(r=>release=r);
    return stage++===0?page([event(1)],{status:'indexing',hasMore:true,nextCursor:'scan',indexingContinuation:'scan'}):page([event(2)],{hasMore:true,nextCursor:'older-history'});
  });
  await f.api.browse(host);f.api.setFollow(false,{byScroll:true});f.setNow(f.api.state.nextPoll);f.doc.hidden=true;await f.api.poll();assert.equal(f.requests.length,1);f.doc.hidden=false;
  hold=true;const pending=f.api.poll();await f.flush();await f.api.poll();assert.equal(f.requests.length,2);release();await pending;hold=false;
  f.setNow(f.api.state.nextPoll);await f.api.poll();assert.equal(f.requests.length,2); // ordinary history remains explicit
  f.api.state.indexingContinuation='scan';fail=true;await f.api.poll();assert.equal(f.requests.length,3);
  await f.api.poll();assert.equal(f.requests.length,3);assert.equal(f.api.state.indexingContinuation,'scan');assert.equal(f.api.state.failures,1);
  f.api.state.indexingContinuation=null;await f.api.browse({...host,source:'linux-history'});assert.equal(f.api.state.indexingContinuation,null);
});
test('foreign, malformed or non-indexing automatic continuation is visibly refused',async()=>{
  for(const [session,extra] of [[host,{indexingContinuation:'bad/ref'}],[host,{indexingContinuation:'other'}],
    [host,{status:'complete'}],[host,{hasMore:false}],[{...host,source:'linux-history'},{}]]) {
    const f=fixture(()=>page([event(1)],{status:'indexing',hasMore:true,nextCursor:'scan',indexingContinuation:'scan',...extra}));
    await f.api.browse(session);assert.equal(f.api.state.blocked,true);assert.match(f.el('history-status').textContent,/invalid indexing continuation schema/);
  }
});
test('an oversized deferred repaint refuses before changing the selected mounted snapshot or view accounting',async()=>{
  const f=fixture(()=>page([event(1,'a'.repeat(5*1024*1024))]));await f.api.browse(host);const row=f.api.state.rows.get('r1'),used=f.api.state.used;f.select(row.prose);
  assert.throws(()=>f.api.reconcile([event(1,'b'.repeat(5*1024*1024))],'delta','epoch'),/view limit while text\/focus is held/);assert.equal(row.prose.textContent[0],'a');assert.equal(row.deferred,null);assert.equal(f.api.state.used,used);
});
test('a replacement that removes a focused tool defers removal until focus leaves, even at an unavailable-body boundary',async()=>{
  const f=fixture();await f.api.browse(host);const row=f.api.state.rows.get('r1'),button=row.calls[0].node;f.doc.activeElement=button;
  f.api.reconcile([{...event(1),text:null,status:'response-limit'}],'delta','epoch');f.api.flushRepaints();assert.equal(row.calls[0].node,button);assert.ok(row.deferred);assert.equal(button.parentNode,row.node);
  f.doc.activeElement=null;f.api.flushRepaints();assert.equal(row.calls.length,0);assert.equal(button.parentNode,null);assert.match(row.node.textContent,/Stored body unavailable.*response-limit/);
});
test('Jump latest reloads durable pages if selected/focused rows forced tail eviction',async()=>{
  const f=fixture();await f.api.browse(host);f.api.state.tailEvicted=true;f.api.reconcile([event(2),event(3)],'forward','epoch');await f.api.latest();assert.equal(f.requests.length,2);assert.equal(f.api.state.tailEvicted,false);assert.equal(f.api.state.rows.has('r3'),false);assert.equal(f.el('history-viewport').scrollTop,f.el('history-viewport').scrollHeight);
});
test('a newly projected earlier tool block inserts ahead of existing tools without moving the focused tool',async()=>{
  const initial=tool(1);initial.calls[0].block=1;const f=fixture(()=>page([initial]));await f.api.browse(host);const row=f.api.state.rows.get('r1'),button=row.calls[0].node;f.doc.activeElement=button;
  const final={...initial,calls:[{...tool(2).calls[0],block:0},initial.calls[0]]};f.api.reconcile([final],'delta','epoch');assert.equal(row.node.children[2],row.calls[0].node);assert.equal(row.node.children[3],button);assert.equal(f.doc.activeElement,button);
});
test('markup defaults compact tools-only with full messages/3-or-5 lines, roles, evidence and no mutation controls',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/sessions.html'),'utf8');assert.match(html,/id="history-tools">/);assert.doesNotMatch(html,/id="history-tools" checked/);assert.match(html,/value="3".*value="5"/);assert.match(html,/id="sessions-role"/);assert.match(html,/id="history-live"/);assert.doesNotMatch(html.replace(/<section id="claude-new-manager-row"[\s\S]*?<\/section>/,''),/id=".*(?:send|interrupt|model|effort).*"|textarea/);
  assert.match(html,/id="claude-new-manager-row"[^>]*hidden/); // New Manager: shown only by the integrated editor, never in standalone /sessions
});
const thinking=(text,streaming=true,availability='stored',blockIndex=0)=>({blockIndex,text,streaming,availability});
test('keyed streaming thinking is safe separate text, expanded live/collapsed history, and final replaces without duplicates',async()=>{
  const initial={...event(1,'Partial reply'),partial:true,thinking:[thinking('<img src=x onerror=bad()> 🌈')]};
  const f=fixture(()=>page([initial]));await f.api.browse(host);const row=f.api.state.rows.get('r1'),block=row.thinking[0];
  assert.equal(block.node.open,true);assert.equal(block.body.textContent,initial.thinking[0].text);assert.equal(row.prose.textContent,'Partial reply');assert.equal(row.node.children[1],block.node);assert.equal(row.node.children[2],row.prose);
  block.node.open=false;const final={...initial,text:'Final reply',partial:false,thinking:[thinking('Final summary',false)]};f.api.reconcile([final],'delta','epoch');
  assert.equal(row.thinking[0],block);assert.equal(block.node.open,false);assert.equal(block.body.textContent,'Final summary');assert.equal(row.prose.textContent,'Final reply');assert.equal(f.api.state.order.length,1);assert.equal(row.thinking.length,1);
  f.api.reconcile([{...event(2),thinking:[thinking('Stored history',false)]}],'forward','epoch');assert.equal(f.api.state.rows.get('r2').thinking[0].node.open,false);
  assert.equal(f.requests.length,1);assert.equal(f.messages.length,0);
});
test('thinking selection and focused disclosure removal defer repaint; accounting includes held/new summary bodies',async()=>{
  const original={...event(1),thinking:[thinking('old summary')]},f=fixture(()=>page([original]));await f.api.browse(host);
  const row=f.api.state.rows.get('r1'),block=row.thinking[0],before=f.api.state.used;f.select(block.body);
  const final={...original,thinking:[thinking('new summary 🌈',false)]};f.api.reconcile([final],'delta','epoch');assert.equal(block.body.textContent,'old summary');assert.ok(row.deferred);assert.ok(f.api.state.used>before);
  f.select(null);assert.equal(block.body.textContent,'new summary 🌈');assert.equal(row.thinking[0],block);assert.equal(f.api.state.used,2*Buffer.byteLength(JSON.stringify(final)));
  f.doc.activeElement=block.summary;f.api.reconcile([event(1)],'delta','epoch');f.api.flushRepaints();assert.equal(block.node.parentNode,row.node);assert.ok(row.deferred);
  f.doc.activeElement=null;f.api.flushRepaints();assert.equal(block.node.parentNode,null);assert.equal(row.thinking.length,0);
});
test('omitted/redacted/body-limit/unknown summaries never render opaque text; identity schemas still refuse',async()=>{
  const f=fixture(()=>page([event(1)]));await f.api.browse(host);
  for(const [i,availability]of ['omitted','redacted','body-limit'].entries()){
    f.api.reconcile([{...event(i+2),thinking:[thinking('OPAQUE_SIGNATURE_OR_REDACTED_DATA',false,availability)]}],'forward','epoch');const row=f.api.state.rows.get('r'+(i+2));assert.doesNotMatch(row.node.textContent,/OPAQUE/);assert.match(row.thinking[0].summary.textContent,new RegExp(availability));
  }
  f.api.reconcile([{...event(1),thinking:[thinking('OPAQUE',true,'invented')]}],'delta','epoch');
  const row=f.api.state.rows.get('r1'),signature=row.signature,used=f.api.state.used;
  assert.match(row.thinking[0].body.textContent,/availability unknown/);assert.doesNotMatch(row.node.textContent,/OPAQUE/);
  assert.throws(()=>f.api.reconcile([{...event(1),thinking:[thinking('bad',true,'stored',-1)]}],'delta','epoch'),/invalid thinking/);assert.equal(row.signature,signature);assert.equal(f.api.state.used,used);
});
test('large thinking bodies obey window budget and cannot silently crop selected text',async()=>{
  const f=fixture(()=>page([{...event(1),thinking:[thinking('a'.repeat(5*1024*1024))]}]));await f.api.browse(host);const row=f.api.state.rows.get('r1'),used=f.api.state.used;f.select(row.thinking[0].body);
  assert.throws(()=>f.api.reconcile([{...event(1),thinking:[thinking('b'.repeat(5*1024*1024))]}],'delta','epoch'),/view limit/);assert.equal(row.thinking[0].body.textContent[0],'a');assert.equal(f.api.state.used,used);
});
test('activity is an age, not a state: silence never infers idle and no activity word or background count reaches the ribbon',async()=>{
  const liveState={state:'ready',owner:'owned',activity:{state:'thinking',thinkingTokens:1234,tool:null,attempt:null,lastEventAt:100},silence_observation:{duration:601}};
  const f=fixture(()=>page([event(1)],{liveState}));await f.api.browse(host);
  for(const [state,extra] of [['idle'],['thinking'],['writing'],['tool',{tool:'workflowy'}],['retrying',{attempt:3}],['permission'],['unknown']]){
    Object.assign(f.api.state.liveState.activity,{state,...extra});f.api.activity(105000);
    assert.equal(f.el('history-activity').textContent,'last event 5 s ago','state '+state+' is never a ribbon word');
    assert.doesNotMatch(f.el('history-activity').textContent,/Idle|Thinking|Writing|Running tool|Retrying|Waiting for permission|Activity unknown/);
  }
  f.api.activity(112000);assert.match(f.el('history-activity').textContent,/^last event 12 s ago$/);assert.equal(f.requests.length,1);assert.equal(f.messages.length,0);
  f.api.activity(99950);assert.match(f.el('history-activity').textContent,/last event 0 s ago/);
  f.api.activity(94000);assert.match(f.el('history-activity').textContent,/last event time unknown/);
  // A real observation failure still says so; it never turns silence into a guessed idle.
  f.api.state.observationError='lost connection';f.api.state.liveState.activity.state='idle';f.api.activity(112000);
  assert.match(f.el('history-activity').textContent,/^Offline \/ observation unavailable/);assert.doesNotMatch(f.el('history-activity').textContent,/^Idle/);
  f.api.state.observationError=null;f.api.state.liveState.activity.lastEventAt=null;f.api.activity(112000);assert.match(f.el('history-activity').textContent,/last event time unknown/);
  // Open background work keeps its count on the row, never on the ribbon.
  f.api.state.liveState.background_tasks={a:{task_type:'local_bash'}};f.api.activity(112000);
  assert.doesNotMatch(f.el('history-activity').textContent,/in background/);
  await f.api.browse({...host,source:'linux-history'});assert.match(f.el('history-activity').textContent,/^Stored history/);assert.doesNotMatch(f.el('history-activity').textContent,/unknown|Idle/);
});
test('the conversation ribbon names the viewed session beside the pie and hides with no session, for either backend',async()=>{
  const liveState={state:'ready',owner:'owned',settings_unverified:true,activity:{state:'thinking',thinkingTokens:1234,tool:null,attempt:null,lastEventAt:100}};
  const row={...host,sessionLabel:'Manager-Session-262',title:'Manager-Session-262',liveState};
  const f=fixture(()=>page([event(1)],{liveState}));f.setNow(105000);await f.api.browse(row);
  const name=f.el('history-session-name'),banner=f.el('history-activity');
  assert.equal(name.textContent,'Manager-Session-262');assert.equal(name.title,'Manager-Session-262');assert.equal(name.hidden,false);
  assert.doesNotMatch(banner.textContent,/Manager-Session-262/,'the name is its own element, not part of the activity line');
  assert.match(banner.textContent,/^last event 5 s ago · ⚠ settings unverified$/,'only the genuine warning rides the age');
  // A chosen name (the shared rename store) reaches the ribbon on the next render without a read.
  const before=f.requests.length;row.sessionLabel='Renamed by Dan';f.api.activity(106000);
  assert.equal(name.textContent,'Renamed by Dan');assert.equal(name.title,'Renamed by Dan');assert.equal(f.requests.length,before);
  // An archived conversation keeps its name and its honest read-only state.
  f.api.state.session={...row,storeRef:'history',key:'claude:archived'};f.api.activity(112000);
  assert.equal(name.textContent,'Renamed by Dan');assert.match(banner.textContent,/^📦 Archived conversation · read-only$/);
  // No session: no invented name.
  f.api.state.session=null;f.api.activity(112000);assert.equal(name.hidden,true);assert.equal(name.textContent,'');assert.equal(banner.textContent,'Choose a session');
  // OpenCode's own readback is an age, never a busy/idle word; a failed read names itself.
  const oc={backend:'opencode',id:'ses_ribbon0001',title:'OpenCode ribbon row',sessionRef:'ses_ribbon0001'};
  f.api.state.session=oc;f.api.state.ocObservedAt=100000;f.api.state.ocBusyFact=null;f.api.activity(112000);
  assert.equal(name.textContent,'OpenCode ribbon row');assert.match(banner.textContent,/^read 12 s ago · view only$/);
  assert.doesNotMatch(banner.textContent,/busy|idle/i);
  f.api.state.observationError='read refused';f.api.activity(112000);
  assert.match(banner.textContent,/^last read failed: read refused · last good read 12 s ago · view only$/);
});
test('native result is a small keyed end-of-turn marker, never repeated assistant prose or untrusted receipt bodies',async()=>{
  const reply='U06B-STREAM-OK unique final reply',result={...event(2,reply),kind:'result',receipt:{subtype:'success',is_error:false,total_cost_usd:0.0123,usage:{input_tokens:100,output_tokens:40,cache_read_input_tokens:0,cache_creation_input_tokens:20},permission_denials:[{tool_name:'secret detail'}],result:reply,errors:['<img src=x> private body']}};
  const f=fixture(()=>page([event(1,reply),result]));await f.api.browse(host);const row=f.api.state.rows.get('r2'),marker=row.result;
  assert.equal(row.prose.parentNode,null);assert.equal(row.prose.textContent,'');assert.equal(f.el('history-events').textContent.split(reply).length-1,1);
  assert.match(marker.textContent,/End of turn · success.*list-price estimate \$0\.0123 \(not billed spend\).*input 100, output 40, cache read 0, cache write 20.*permission denials 1/);
  assert.doesNotMatch(marker.textContent,/secret detail|private body|<img|U06B/);
  f.api.reconcile([{...result,receipt:{...result.receipt,subtype:'error_during_execution',is_error:true}}],'delta','epoch');assert.equal(row.result,marker);assert.match(marker.textContent,/error_during_execution/);assert.equal(row.prose.parentNode,null);assert.equal(f.api.state.order.length,2);
  f.api.reconcile([{...event(3,reply),kind:'result',receipt:{total_cost_usd:null,usage:{input_tokens:null,output_tokens:-1},permission_denials_count:2}}],'forward','epoch');assert.equal(f.api.state.rows.get('r3').result.textContent,'End of turn · outcome unknown · permission denials 2');
  f.api.reconcile([{...result,receipt:{subtype:'success',is_error:true,origin:{kind:'task-notification'}}}],'delta','epoch');
  assert.equal(marker.textContent,'Background result · success · error receipt');assert.equal(row.prose.parentNode,null);
});
test('host observes at 600ms while history retains 3s; hidden/no-overlap/backoff gates remain authoritative',async()=>{
  let offline=false,hold=false,release;const f=fixture(async url=>{
    if(url.startsWith('/api/claude/events')){if(offline)throw new Error('fixture offline');if(hold)await new Promise(r=>release=r);return page([event(1)]);}
  });f.setNow(100000);await f.api.browse(host);assert.equal(f.api.pollDelay(),600);assert.equal(f.api.state.nextPoll,100600);
  f.setNow(100599);await f.api.poll();assert.equal(f.requests.length,1);f.setNow(100600);await f.api.poll();assert.equal(f.requests.length,2);
  f.setNow(101200);f.doc.hidden=true;await f.api.poll();assert.equal(f.requests.length,2);f.doc.hidden=false;
  f.doc.defaultView={frameElement:{closest:()=>({hidden:true})}};await f.api.poll();assert.equal(f.requests.length,2);f.doc.defaultView=null;
  hold=true;const pending=f.api.poll();await f.flush();await f.api.poll();assert.equal(f.requests.length,3);release();await pending;hold=false;
  f.setNow(101800);offline=true;await f.api.poll();assert.equal(f.requests.length,4);assert.equal(f.api.state.nextPoll,104800);f.setNow(102400);await f.api.poll();assert.equal(f.requests.length,4);
  offline=false;f.setNow(104800);await f.api.poll();assert.equal(f.api.state.failures,0);assert.equal(f.api.state.nextPoll,105400);
  f.setNow(200000);await f.api.browse({...host,source:'linux-history'});assert.equal(f.api.pollDelay(),3000);assert.equal(f.api.state.nextPoll,203000);f.setNow(202999);await f.api.poll();const before=f.requests.length;f.setNow(203000);await f.api.poll();assert.equal(f.requests.length,before+1);
});
test('a measured context readout rides the live activity line with its as-of time and the raw source progress explains a slow index',async()=>{
  const readout={state:'measured',occupancy_tokens:294246,window_tokens:1000000,fraction_used:0.294246,
    measured_at:1792005183.797,as_of:'2026-10-14T19:13:03Z',label:'measured: as of the previous model call (2026-10-14T19:13:03Z, 12s old)'};
  const progress={indexedBytes:2516582,indexedRecords:120,sourceBytes:5029000,status:'indexing',kind:'host-raw'};
  const liveState={state:'ready',owner:'owned',activity:{state:'idle',thinkingTokens:null,tool:null,attempt:null,lastEventAt:100}};
  const f=fixture(()=>page([event(1)],{liveState,contextReadout:readout,sourceProgress:progress}));await f.api.browse(host);
  assert.match(f.el('history-activity').textContent,/^last event \d+ s ago · context 294K \/ 1M \(29%\) · as of \d{2}:\d{2}$/);
  assert.equal(f.el('history-activity').dataset.context,'measured');
  assert.equal(f.el('history-activity').title,readout.label);
  assert.match(f.el('history-window').textContent,/viewer memory \d+ bytes \(resident rows, not context\)/);
  assert.match(f.el('history-window').textContent,/viewer source 2\.5 MB \/ 5\.0 MB indexed \(raw stream\)/);
  assert.doesNotMatch(f.el('history-window').textContent,/stored view bytes/);
});
test('an unknown live context readout says why, and a session without one invents nothing',async()=>{
  const f=fixture(()=>page([event(1)],{contextReadout:{state:'unavailable',reason:'no native transcript found for this session yet'}}));
  await f.api.browse(host);
  assert.match(f.el('history-activity').textContent,/ · context unknown: no native transcript found for this session yet$/);
  assert.equal(f.el('history-activity').dataset.context,'unavailable');
  const g=fixture();await g.api.browse(host);
  assert.equal(g.el('history-activity').dataset.context,'none');assert.doesNotMatch(g.el('history-activity').textContent,/context/);
});
test('the next live page updates the context figure after a completed turn without its own request',async()=>{
  let second=null;
  const f=fixture(()=>second?page([event(2)],{contextReadout:second})
    :page([event(1)],{contextReadout:{state:'measured',occupancy_tokens:294246,window_tokens:1000000,measured_at:1792005183.797}}));
  await f.api.browse(host);assert.equal(f.requests.length,1);assert.match(f.el('history-activity').textContent,/context 294K/);
  second={state:'measured',occupancy_tokens:298875,window_tokens:1000000,measured_at:1792005364.917};
  await f.api.page('delta','delta');assert.match(f.el('history-activity').textContent,/context 299K/);
  assert.equal(f.requests.length,2); // the figure rides the existing page, never a request of its own
});

test('Dan: follow on lands a reload on the newest turn, pins arrivals, and pauses without moving the reading position',async()=>{
  let delta=false;const f=fixture(url=>{
    if(!url.startsWith('/api/claude/events'))return;
    const cursor=new URLSearchParams(url.split('?')[1]).get('cursor');
    if(delta)return page([event(9,'newest')],{change:'append'});
    return cursor==='p2'?page([event(3)]):cursor==='p1'?page([event(2)],{hasMore:true,nextCursor:'p2'}):page([event(1)],{hasMore:true,nextCursor:'p1'});
  });
  await f.api.browse(host);assert.equal(f.api.state.follow,true);assert.equal(f.api.state.nextPoll,0);
  await f.api.poll();assert.equal(f.api.state.next,null);assert.ok(f.api.state.rows.has('r3'));
  const viewport=f.el('history-viewport');assert.equal(viewport.scrollTop,viewport.scrollHeight);
  delta=true;f.setNow(f.api.state.nextPoll);await f.api.poll();assert.ok(f.api.state.rows.has('r9'));assert.equal(viewport.scrollTop,viewport.scrollHeight);
  f.api.setFollow(false,{byScroll:true});viewport.scrollTop=40;f.api.reconcile([event(10,'later')],'delta','epoch');
  assert.equal(viewport.scrollTop,40);assert.equal(f.api.state.newCount,1);assert.match(f.el('history-follow-label').textContent,/Paused · 1 new/);
  f.api.setFollow(true);await f.flush();assert.equal(f.api.state.newCount,0);assert.equal(f.el('history-follow').attributes['aria-pressed'],'true');
});
test('Dan: turns are told apart and tool-result carrier records fold away',async()=>{
  const user={...event(1,'Dan asks'),kind:'user'},mid={...event(2,'Let me look')},calls=tool(3,'stored'),carrier={...event(4,''),kind:'user',toolResults:1},
    meta={...event(5,''),kind:'user',toolResults:0,isMeta:true},answer={...event(6,'Here is the answer')},hostCarrier={...event(7,''),kind:'user'};
  const f=fixture(()=>page([user,mid,calls,carrier,meta,answer]));await f.api.browse(host);
  const cls=n=>{const row=f.api.state.rows.get('r'+n).node;return ['turn-user','turn-mid','turn-tools','turn-final','event-silent','event-flagged'].filter(c=>row.classList.contains(c));};
  assert.deepEqual(cls(1),['turn-user']);assert.deepEqual(cls(2),['turn-mid']);assert.deepEqual(cls(3),['turn-tools']);
  assert.deepEqual(cls(4),['event-silent']);assert.deepEqual(cls(5),['event-flagged']);assert.deepEqual(cls(6),['turn-final']);
  assert.match(f.api.state.rows.get('r5').flag.textContent,/meta message/);
  f.api.reconcile([tool(8,'stored'),hostCarrier].map((e,i)=>({...e,recordRef:'h'+i,uuid:'host-'+i})),'forward','epoch'); // host rows carry no toolResults
  assert.equal(f.api.state.rows.get('h1').node.classList.contains('event-silent'),true);
  assert.equal(f.api.state.rows.get('r6').node.classList.contains('turn-mid'),true); // a later tool call reopens the round
});
test('guard sweep: a stale live-state snapshot never freezes the sidebar spinner on an idle Manager',async()=>{
  // 182's false yellow spinner: the selected view stopped refreshing (paused on an older page)
  // while its last snapshot said 'permission'; the host meanwhile had the Manager idle.
  const live={...host,capabilities:{nativeInput:true,controls:true},lifecycle:'live',state:'ready',primary_turn_open:false,pending_message_uuids:[]};
  const f=fixture(url=>{
    if(url.startsWith('/api/claude/sessions'))return env({sessions:[live],status:'complete',hasMore:false});
    if(url.startsWith('/api/claude/events'))return page([tool(1)],{liveState:{state:'ready',primary_turn_open:false,pending_message_uuids:[],activity:{state:'permission'}}});
  });
  f.setNow(1_000_000);await f.api.list();await f.api.browse(live);await f.flush();
  const item=()=>sidebarItems(f)[0];
  assert.equal(item().classList.contains('active'),false);         // streamed activity never lights it (183)
  f.setNow(1_000_000+20_000);await f.api.list();                    // the view went quiet; inventory refreshed
  assert.equal(item().classList.contains('active'),false);         // host truth: idle, no spinner
});
test("183: the Manager's spinner is its own turn only, the same selected or not, never a background subagent",async()=>{
  // Real host data (cc-50a5f467, g2): a notification-started turn (primary system/init, no human
  // message) ended with a task-notification result while a background subagent kept running.
  // primary_turn_open went false, but the selected view's streamed activity stayed 'writing', so
  // the selected row spun and the listed row did not.
  const bg={a1:{task_id:'a1',task_type:'local_agent',subagent_type:'manager-quick-xhigh'}};
  let turn=false;
  const hostState=()=>({state:'ready',native_session_state:'running',primary_turn_open:turn,pending_message_uuids:[],background_tasks:bg});
  // Separate host executions, as in the real inventory: activity keys use execution identity.
  const manager=()=>({...host,sessionRef:'m183',uuid:'uuid-183',executionId:'cc-uuid-183',sessionLabel:'Manager-Session-183',capabilities:{nativeInput:true,controls:true},lifecycle:'live',state:'ready',createdAt:2,liveState:hostState()});
  const other={...host,sessionRef:'m182',uuid:'uuid-182',executionId:'cc-uuid-182',sessionLabel:'Manager-Session-182',capabilities:{nativeInput:true,controls:true},lifecycle:'live',state:'ready',createdAt:1,
    liveState:{state:'ready',primary_turn_open:false,pending_message_uuids:[]}};
  const f=fixture(url=>{
    if(url.startsWith('/api/claude/sessions'))return env({sessions:[manager(),other],status:'complete',hasMore:false});
    if(url.startsWith('/api/claude/events'))return page([event(1)],{sessionRef:new URLSearchParams(url.split('?')[1]).get('sessionRef'),
      liveState:{...hostState(),activity:{state:turn?'thinking':'writing'}}});
  });
  const spinning=label=>sidebarItems(f).find(n=>n.textContent.includes(label)).classList.contains('active');
  f.setNow(1_000_000);await f.api.list();
  await f.api.browse(manager());await f.flush();
  assert.equal(spinning('Manager-Session-183'),false,'selected: a stale streamed activity and a running subagent are not its turn');
  await f.api.browse(other);await f.flush();await f.api.list();
  assert.equal(spinning('Manager-Session-183'),false,'unselected agrees');
  turn=true;f.setNow(1_000_000+11_000);await f.api.list();          // a notification-started turn opens
  assert.equal(spinning('Manager-Session-183'),true,'unselected: its own turn, however it started');
  await f.api.browse(manager());await f.flush();
  assert.equal(spinning('Manager-Session-183'),true,'selected agrees');
  turn=false;f.setNow(1_000_000+22_000);await f.api.page('delta','delta');await f.flush();
  assert.equal(spinning('Manager-Session-183'),false,'its turn closed: the newer polled record clears it while the subagent still runs');
});
test('a pane-server restart (every handle re-issued) never lists the watched parent\'s subagents twice',async()=>{
  const parent=ref=>({...host,sessionRef:ref,uuid:'uuid-183',capabilities:{nativeInput:true,controls:true,subagents:true},lifecycle:'live',state:'ready'});
  const child=ref=>({...host,source:'native',role:'subagent',parentUuid:'uuid-183',sessionRef:ref,uuid:'uuid-183',title:'Manager-Session-183 ▸ manager-quick-xhigh · fixture task',
    agentId:'a1',capabilities:{nativeInput:false,subagents:true},scopes:[{label:'agent:a1',scopeRef:'child-scope'}]});
  let restarted=false;
  const f=fixture(url=>{
    if(url.startsWith('/api/claude/sessions'))return env({sessions:restarted?[parent('new-parent'),child('new-child')]:[parent('old-parent'),child('old-child')],status:'complete',hasMore:false});
    if(url.startsWith('/api/claude/subagents')){
      const asked=new URLSearchParams(url.split('?')[1]).get('sessionRef');
      if(restarted && asked==='old-parent')throw new Error('host handle expired; reselect');
      return env({sessions:[child(restarted?'new-child':'old-child')],hasMore:false,status:'complete'});}
    if(url.startsWith('/api/claude/events'))return page([event(1)]);
  });
  await f.api.list();await f.api.browse(parent('old-parent'));await f.api.children();
  // Rows nested under the parent in the list, plus the watched-children block's list while it shows.
  const count=()=>(f.el('sessions-list').textContent.match(/fixture task/g) || []).length+
    (f.el('subagents-block').hidden ? 0 : (f.el('subagents-list').textContent.match(/fixture task/g) || []).length);
  assert.equal(count(),1);
  restarted=true;await f.api.list();await f.api.children();
  assert.match(f.el('subagents-status').textContent,/handle expired/);
  assert.equal(count(),1,'still once after the restart');
  // Dan opens the parent's subagent toggle (re-issued handle): the children must not also stay
  // in a detached "subagents of the selected session" block.
  const toggle=()=>{const found=[];const walk=n=>{if(n.className==='session-toggle')found.push(n);for(const c of n.children||[])walk(c);};walk(f.el('sessions-pinned'));walk(f.el('sessions-list'));return found[0];};
  const seen=[];
  for(const _ of [1,2]){toggle().onclick();await f.flush();await f.flush();seen.push(count());}
  assert.ok(seen.every(n=>n<=1),'never twice (nested AND in a detached block): '+seen);
  assert.ok(seen.includes(1),'shown once when open: '+seen);
});

// ---------------------------------------------------------------- round 6: one-page groups, real roles, rename
const U=n=>String(n).padStart(8,'0')+'-0000-4000-8000-000000000000';
test('real roles: a worker or Conductor named after a Manager Session never joins the Manager group; a host twin transcript sits under its host row',async()=>{
  const manager={...host,sessionRef:'m',uuid:U(1),role:'Manager',displayRole:'manager',roleSource:'host',sessionLabel:'Manager-Session-184',cwd:'/r/manager-sessions',createdAt:50};
  const quick={...host,sessionRef:'q',uuid:U(2),role:'worker',displayRole:'quick-worker',roleSource:'host',managerSession:'183',title:'[ap:X] ▸ ⚡ quick worker · task',cwd:'/r/quick-worker',createdAt:40};
  const conductor={...host,sessionRef:'c',uuid:U(3),role:'conductor',displayRole:'conductor',roleSource:'host',title:'[ap:X]-001 · program',cwd:'/home/dan',createdAt:30};
  const twin={...host,sessionRef:'t',uuid:U(1),source:'native',role:'history',displayRole:'manager',roleSource:'host-twin',hostTwin:true,title:'Native history boundary · x',cwd:'/r/manager-sessions',createdAt:50};
  const older={...host,sessionRef:'o',uuid:U(4),source:'native',role:'history',displayRole:'manager',roleSource:'folder',sessionLabel:'Manager-Session-170',cwd:'/r/manager-sessions',createdAt:10};
  const probe={...host,sessionRef:'p',uuid:U(5),source:'native',role:'history',title:'Exact reply request',cwd:'/r/manager-sessions',createdAt:20};
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:[probe,twin,quick,older,conductor,manager],status:'complete',hasMore:false}):undefined);
  await f.api.list();
  const groups=f.el('sessions-list').children.map(g=>headingText(g.children[0]));
  assert.deepEqual(groups,['Favorite programs · 0','Manager Sessions · 2','Quick workers and helpers · quick-worker · 1','manager-sessions folder · not Managers · 1','Other · 1']);
  const managers=f.el('sessions-list').children.find(g=>g.dataset.group==='managers').textContent;assert.match(managers,/Manager-Session-184.*its native transcript.*Manager-Session-170/s);assert.doesNotMatch(managers,/quick worker|Native history boundary/);
  // Round 7: the role word leads line 1 (no second-line badge); the role's source stays on hover.
  const words=[];const walk=n=>{if(n.className==='role-word')words.push([n.textContent,n.dataset.role,n.parentNode.parentNode.title]);for(const c of n.children||[])walk(c);};walk(f.el('sessions-list'));
  assert.deepEqual(words.map(b=>b[0]),['Manager:','Manager:','Quick worker:','History:','Conductor:']);
  assert.match(words[2][2],/host registry/);assert.match(words[1][2],/working folder/);
  assert.match(sidebarItems(f)[2].textContent,/^Quick worker:task/); // the script prefix "[ap:X] ▸ ⚡ quick worker ·" is gone
  assert.match(f.el('sessions-list').children.find(g=>g.dataset.group==='folder:quick-worker').textContent,/for Manager-Session-183/);
  const link=[];const find=n=>{if(n.className==='session-twin')link.push(n);for(const c of n.children||[])find(c);};find(f.el('sessions-list'));
  assert.equal(link.length,1);link[0].onclick({stopPropagation(){}});await f.flush();assert.equal(f.api.state.session.sessionRef,'t');
  f.el('sessions-role').value='manager';f.el('sessions-role').onchange();assert.equal(sidebarItems(f).length,2); // the twin stays under its host row
  f.el('sessions-role').value='quick-worker';f.el('sessions-role').onchange();assert.equal(sidebarItems(f).length,1);
});
test('rename from the sidebar: the pencil turns the name into an input; Enter writes the shared name store, Escape changes nothing, empty restores the automatic name',async()=>{
  const row={...host,sessionRef:'b',uuid:U(9),role:'Manager',displayRole:'manager',title:U(9),cwd:'/r/manager-sessions'};
  const posts=[];
  const f=fixture((url,opts)=>{if(url==='/api/claude/rename'){const body=JSON.parse(opts.body);posts.push(body);return env({uuid:body.uuid,name:body.name.trim()||null});}
    if(url.startsWith('/api/claude/sessions'))return env({sessions:[row],status:'complete',hasMore:false});});
  await f.api.list();assert.match(f.el('sessions-pinned').textContent+f.el('sessions-list').textContent,/Session 00000009/);
  const findClass=cls=>{const found=[];const walk=n=>{if(n.className===cls)found.push(n);for(const c of n.children||[])walk(c);};for(const root of [f.el('sessions-pinned'),f.el('sessions-list')])walk(root);return found;};
  const key=(k)=>({key:k,preventDefault(){},stopPropagation(){}});
  findClass('session-rename')[0].onclick({stopPropagation(){}});
  let input=findClass('session-rename-input')[0];assert.ok(input);assert.equal(input.value,'');assert.equal(findClass('session-open')[0].hidden,true);
  f.api.inventory();assert.ok(findClass('session-rename-input')[0],'a refresh never wipes a rename in progress');
  input.value='Manager-Session-185';input.onkeydown(key('Enter'));await f.flush();await f.flush();
  assert.deepEqual(posts,[{schemaVersion:1,backend:'claude',uuid:U(9),name:'Manager-Session-185'}]);
  assert.match(f.el('sessions-pinned').textContent+f.el('sessions-list').textContent,/Manager-Session-185/);assert.equal(findClass('session-rename-input').length,0);assert.match(f.el('sessions-notice').textContent,/Renamed: Manager-Session-185/);
  findClass('session-rename')[0].onclick({stopPropagation(){}});input=findClass('session-rename-input')[0];assert.equal(input.value,'Manager-Session-185');
  input.value='something else';input.onkeydown(key('Escape'));await f.flush();assert.equal(posts.length,1);assert.match(f.el('sessions-pinned').textContent+f.el('sessions-list').textContent,/Manager-Session-185/);
  findClass('session-rename')[0].onclick({stopPropagation(){}});input=findClass('session-rename-input')[0];input.value='  ';input.onkeydown(key('Enter'));await f.flush();await f.flush();
  assert.equal(posts[1].name,'');assert.match(f.el('sessions-pinned').textContent+f.el('sessions-list').textContent,/Session 00000009/);assert.match(f.el('sessions-notice').textContent,/automatic name/);
  f.api.state.session=row;f.api.activity();assert.equal(f.el('history-session-name').textContent,'Session 00000009','the chosen name store reaches the conversation ribbon');
  assert.equal(f.messages.length,0);
});

// ---------------------------------------------------------------- round 7: programs nested, role first, OpenCode rows
const cc=n=>'cc-'+String(n).padStart(8,'0')+'-0000-4000-8000-000000000000';
const seat=(n,lineage,over={})=>({...host,sessionRef:'h'+n,uuid:U(100+n),executionId:cc(n),role:'worker',displayRole:'quick-worker',roleSource:'host',
  state:'exited',lifecycle:'ended',cwd:'/r/quick-worker',createdAt:1000+n,title:lineage.title || 'seat '+n,lineage,...over});
const cond=(n,core,number,{lineage:extra={},...over}={})=>seat(n,{program:core,kind:'conductor',conductorN:number,quickProgram:true,managerSession:'184',
  title:`[ap:${core}]-${String(number).padStart(3,'0')} · ⚡ Pane round 7: programs`,...extra},{role:'conductor',displayRole:'conductor',cwd:'/home/dan',...over});
const ocRow=(id,over)=>({id:'ses_'+id.padEnd(10,'x'),program:'UHZREM',kind:'helper',title:'[ap:UHZREM]-001 ▸ ⚡ quick helper · [ap:WB93JF]',busy:false,outcome:'succeeded',
  model:'openrouter/deepseek/deepseek-v4.1-flash#max',cost:0.0012,createdAt:1500,...over});
const ocEnv=sessions=>({schemaVersion:1,backend:'opencode',status:'complete',sessions});
const find=(root,cls)=>{const out=[];const walk=n=>{if(String(n.className).split(' ').includes(cls))out.push(n);for(const c of n.children||[])walk(c);};walk(root);return out;};
const sessionsOf=(...rows)=>url=>url.startsWith('/api/claude/sessions')?env({sessions:rows,status:'complete',hasMore:false}):undefined;

test('every row keeps its controls in one .session-actions band and every row has a glance, including phone leaves',async()=>{
  const manager={...host,sessionRef:'band-manager',uuid:U(230),executionId:cc(230),role:'Manager',displayRole:'manager',sessionLabel:'Manager-Session-230',
    cwd:'/r/manager-sessions',capabilities:{subagents:true},liveState:{}};
  const leaf={...host,sessionRef:'band-leaf',uuid:U(231),executionId:cc(231),role:'worker',displayRole:'worker',title:'band leaf',cwd:'/r/x'};
  for(const sidebarOnly of [false,true]){
    const oc=ocRow('bandoc',{program:null,kind:'seat',quick:true,title:'OC band row'});
    const f=fixture(sessionsOf(manager,leaf),{sidebarOnly,opencode:()=>ocEnv([oc])});
    await f.api.list();await f.api.ocList();
    const rows=[...find(f.el('sessions-pinned'),'session-item'),...find(f.el('sessions-list'),'session-item')];
    assert.equal(rows.length,3,sidebarOnly?'phone: manager pin, worker leaf and the OC row':'desktop: manager pin, worker leaf and the OC row');
    const direct=row=>row.children.filter(n=>['session-copy','session-rename','session-glance-chip','oc-glance-chip'].includes(n.className));
    for(const row of rows){
      const actions=row.children.find(n=>n.className==='session-actions');
      assert.ok(actions,'every row owns one action band');
      assert.equal(direct(row).length,0,'no control is a direct child icon any more');
      assert.equal(find(actions,'session-copy').length,1);
      const glance=find(actions,'session-glance-chip').length+find(actions,'oc-glance-chip').length;
      assert.equal(glance,1,'every row has its own glance chip');
      assert.equal(find(actions,'session-rename').length,(sidebarOnly||String(row.className).split(' ').includes('oc-item'))?0:1);
    }
    // A phone leaf (nothing under it) now has the chip too, where the whole row used to be the glance tap.
    const leafRow=rows.find(row=>find(row,'name-text')[0].textContent==='band leaf');
    assert.equal(find(leafRow,'session-glance-chip').length,1);
    assert.equal(find(leafRow,'oc-glance-chip').length,0);
    // The OpenCode row's band carries copy and its own glance, never a Claude pencil.
    const ocRowItem=rows.find(row=>String(row.className).split(' ').includes('oc-item'));
    assert.equal(find(ocRowItem,'session-copy').length,1);assert.equal(find(ocRowItem,'oc-glance-chip').length,1);assert.equal(find(ocRowItem,'session-rename').length,0);
  }
});
test('U2.2 setView owns week/month/all queries, stays page-local, and never filters server rows in the browser',async()=>{
  const writes=[],storage={getItem:()=>null,setItem:(key,value)=>writes.push([key,value])};
  const old={...host,sessionRef:'ancient',createdAt:1},f=fixture(sessionsOf(old),{storage,opencode:()=>ocEnv([])});
  assert.equal(f.api.setView(),'week');await f.api.list();
  assert.match(f.requests[0][0],/view=week/);assert.equal(f.api.state.inventory[0].sessionRef,'ancient');
  for(const selected of ['month','all']){
    await f.api.setView(selected);assert.equal(f.el('sessions-view').value,selected);
    assert.ok(f.requests.some(([url])=>url.includes('view='+selected)));
    assert.ok(f.ocRequests.some(url=>url.includes('view='+selected)));
  }
  assert.equal(writes.length,0,'the selector is not a stored preference');
  assert.equal(fixture(undefined,{storage}).api.setView(),'week','a new page resets the view');
  for(const filename of ['sessions.html','m-programs.html']){
    const markup=fs.readFileSync(path.join(__dirname,'../static',filename),'utf8');
    assert.match(markup,/id="sessions-view"[^>]*>.*value="week">Last 7 days.*value="month">30 days.*value="all">Everything live/s);
  }
});

test('U2.2 a view switch supersedes both source reads without changing the Send target',async()=>{
  let releaseClaude,releaseOpenCode;
  const f=fixture(url=>{
    if(!url.startsWith('/api/claude/sessions'))return;
    const selected=new URLSearchParams(url.split('?')[1]).get('view');
    const data=env({sessions:[{...host,sessionRef:selected}],hasMore:false,status:'complete'});
    return selected==='month'?new Promise(resolve=>releaseClaude=()=>resolve(data)):data;
  },{opencode:(_route,url)=>url.includes('view=month')?new Promise(resolve=>releaseOpenCode=()=>resolve(ocEnv([ocRow('oldview',{})]))):ocEnv([ocRow('newview',{})])});
  f.api.state.target='existing-send-target';const old=f.api.setView('month');
  await f.flush();await f.api.setView('all');releaseClaude();releaseOpenCode();await old;
  assert.equal(f.api.state.inventory[0].sessionRef,'all');assert.match(f.api.state.oc.rows[0].id,/newview/);
  assert.equal(f.api.state.target,'existing-send-target');
});

test('U2.2 old pane view=400 and pins=404 retry legacy lists and draw no stars, never an empty sidebar',async()=>{
  const unavailable=new Response(JSON.stringify({error:'unknown parameter'}),{status:400});
  const f=fixture(url=>url.startsWith('/api/claude/sessions')&&url.includes('view=')?unavailable.clone():undefined,
    {opencode:(_route,url)=>url.includes('view=')?unavailable.clone():ocEnv([ocRow('legacy',{})]),
     pins:()=>new Response(JSON.stringify({error:'missing'}),{status:404})});
  await f.api.setView('week');await f.flush();
  assert.equal(f.api.state.inventory.length,1);assert.equal(f.api.state.oc.rows.length,1);
  assert.equal(f.api.state.starsAvailable,false);assert.equal(f.api.starButton('program:ABC123'),null);
  assert.ok(f.requests.some(([url])=>!url.includes('view=')));
  const at=f.requests.length,ocAt=f.ocRequests.length;await f.api.setView('month');
  assert.ok(f.requests.slice(at).every(([url])=>!url.includes('view=')));
  assert.ok(f.ocRequests.slice(ocAt).every(url=>!url.includes('view=')));
});

test('U2.2 stars cover program, Claude, native subagent and OpenCode rows on desktop and phone',async()=>{
  for(const sidebarOnly of [false,true]){
    let pins={};const c=cond(1,'STARS1',1,{executionId:'cc-'+U(101),capabilities:{subagents:true}}),oc=ocRow('starstask',{program:'STARS1'});
    const child={...host,source:'native',role:'subagent',parentUuid:c.uuid,agentId:'a1',sessionRef:'star-child',title:'child task'};
    const f=fixture(sessionsOf(c,child),{sidebarOnly,opencode:()=>ocEnv([oc]),pins:(_url,options)=>{
      if(options.method==='POST'){const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['key','pinned','schemaVersion']);
        if(body.pinned)pins[body.key]={pinned_at:123};else delete pins[body.key];}
      return {schemaVersion:1,pins:{...pins}};
    }});
    await f.api.list();await f.api.ocList();await f.flush();
    f.api.state.lineageOpen.set('p:STARS1',true);f.api.state.expanded.add(c.sessionRef);f.api.inventory();
    const keys=new Set(find(f.el('sessions-list'),'session-star').map(node=>node.dataset.star));
    const sessionKey='session:claude:cc-'+c.uuid,childKey=sessionKey+'/agent-a1';
    for(const key of ['program:STARS1',sessionKey,childKey,'session:opencode:'+oc.id])assert.ok(keys.has(key),key+' has its own star');
    await f.api.toggleStar(childKey);assert.ok(pins[childKey]);
    assert.equal(f.api.starButton(childKey).getAttribute('aria-pressed'),'true');
    await f.api.toggleStar(childKey);assert.equal(pins[childKey],undefined);
    assert.equal(f.api.state.target,null,'stars never retarget Send');
  }
});

test('U2.2 overlapping star toggles preserve the last intent in the one server store',async()=>{
  let pins={};const actions=[],f=fixture(undefined,{pins:(_url,options)=>{
    if(options.method==='POST'){const body=JSON.parse(options.body);actions.push(body.pinned);if(body.pinned)pins[body.key]={pinned_at:123};else delete pins[body.key];}
    return {schemaVersion:1,pins:{...pins}};
  }});
  await f.api.loadStars();await Promise.all([f.api.toggleStar('program:STARS1'),f.api.toggleStar('program:STARS1')]);await f.flush();
  assert.deepEqual(actions,[true,false]);assert.equal(pins['program:STARS1'],undefined);assert.equal(f.api.state.stars['program:STARS1'],undefined);
});

for(const sidebarOnly of [false,true])test(`Favorite programs placement, order, stubs and unstarring on ${sidebarOnly?'phone':'desktop'}`,async()=>{
  const row=(n,core,created,quick=false,live=false,busy=false)=>cond(n,core,1,{createdAt:created,lineage:{quickProgram:quick},
    ...(live?{state:'ready',lifecycle:'live',liveState:{primary_turn_open:busy,pending_message_uuids:[]}}:{})});
  const rows=[row(801,'FAVNEW',4000),row(802,'FAVOLD',1000,false,true),row(803,'FAVRUN',500,false,true,true),
    row(804,'PLAIN1',6000),row(805,'PLAIN2',100,false,true),row(806,'FAVQN1',9000,true),row(807,'FAVQO1',8000,true),row(808,'FAVQRN',10000,true,true,true)];
  let pins=Object.fromEntries(['FAVNEW','FAVOLD','FAVRUN','FAVQN1','FAVQO1','FAVQRN'].map(core=>['program:'+core,{pinned_at:1}]));
  pins['session:claude:cc-'+rows[3].uuid]={pinned_at:1};
  const f=fixture(sessionsOf(...rows),{sidebarOnly,opencode:()=>ocEnv([]),pins:(_url,options)=>{
    if(options?.method==='POST'){const body=JSON.parse(options.body);if(body.pinned)pins[body.key]={pinned_at:2};else delete pins[body.key];}
    return {schemaVersion:1,pins:{...pins}};
  }});
  await f.api.list();await f.api.ocList();await f.api.loadStars();await f.flush();
  const group=kind=>f.el('sessions-list').children.find(g=>g.dataset.group==='programs:'+kind);
  const keys=kind=>group(kind).children.filter(n=>n.className==='program' || n.className==='session-stub').map(n=>n.className==='program'?n.children[0].dataset.anchor:n.dataset.anchor.replace('stub:programs:favorite:','').replace('stub:',''));
  assert.deepEqual(f.el('sessions-list').children.slice(0,2).map(g=>g.dataset.group),['programs:running','programs:favorite']);
  assert.equal(headingLabel(group('favorite').children[0]),'Favorite programs');assert.match(group('favorite').children[0].title,/full programs first.*quick programs/i);
  assert.deepEqual(keys('favorite'),['p:FAVRUN','p:FAVQRN','p:FAVNEW','p:FAVOLD','p:FAVQN1','p:FAVQO1'],'running stubs first, then full before quick in home order');
  assert.deepEqual(keys('full'),['p:PLAIN2','p:PLAIN1','p:FAVNEW','p:FAVOLD','p:FAVRUN'],'starred live/running home stubs keep chronology, unstarred live keeps its boost');
  assert.deepEqual(keys('quick'),['p:FAVQRN','p:FAVQN1','p:FAVQO1']);
  assert.deepEqual(find(group('favorite'),'program').map(p=>p.children[0].dataset.anchor),['p:FAVNEW','p:FAVOLD','p:FAVQN1','p:FAVQO1']);
  assert.deepEqual(find(group('running'),'program').map(p=>p.children[0].dataset.anchor),['p:FAVQRN','p:FAVRUN']);
  assert.ok(find(group('favorite'),'session-stub').every(n=>n.textContent.endsWith(' · shown at top')));
  assert.equal(find(group('full'),'session-stub').filter(n=>n.textContent.endsWith(' · in Favorite programs')).length,2);
  const homeStub=find(group('full'),'session-stub').find(n=>n.dataset.anchor==='stub:p:FAVRUN');
  assert.notEqual(homeStub.dataset.anchor,find(group('favorite'),'session-stub')[0].dataset.anchor,'two stubs never share a reading anchor');
  assert.ok(find(group('favorite'),'program').every(p=>find(p.children[0],'session-star').every(n=>n.dataset.star.startsWith('program:'))),'favorite heads use only existing program stars');
  assert.equal(f.api.state.placed.get('p:PLAIN1'),'home','a member session star never favorites its program');
  await f.api.toggleStar('program:FAVRUN');await f.flush();
  assert.ok(!keys('favorite').includes('p:FAVRUN'));assert.equal(f.api.state.placed.get('p:FAVRUN'),'running');
  assert.deepEqual(keys('full').slice(0,2),['p:FAVRUN','p:PLAIN2'],'unstar restores old live-first home stub behavior');
  await f.api.toggleStar('program:FAVNEW');await f.flush();
  assert.ok(!keys('favorite').includes('p:FAVNEW'));assert.equal(f.api.state.placed.get('p:FAVNEW'),'home');
  assert.ok(find(group('full'),'program').some(p=>p.children[0].dataset.anchor==='p:FAVNEW'));
  assert.deepEqual(f.pinRequests.filter(([,o])=>o?.method==='POST').map(([,o])=>JSON.parse(o.body).key),['program:FAVRUN','program:FAVNEW']);
  f.el('sessions-role').value='conductor';f.el('sessions-role').onchange();assert.equal(group('favorite'),undefined,'role views retain their existing flattened placement');
});

for(const sidebarOnly of [false,true])test(`Favorite programs unpaged, section persistence and fold controls on ${sidebarOnly?'phone':'desktop'}`,async()=>{
  const rows=Array.from({length:16},(_,i)=>cond(820+i,'FAV'+String(i).padStart(3,'0'),1));
  const saved=new Map(),storage={getItem:key=>saved.get(key) ?? null,setItem:(key,value)=>saved.set(key,value)};
  const f=fixture(sessionsOf(...rows),{sidebarOnly,storage,opencode:()=>ocEnv([]),pins:()=>({schemaVersion:1,pins:Object.fromEntries(rows.map(r=>['program:'+r.lineage.program,{pinned_at:1}]))})});
  await f.api.list();await f.api.ocList();await f.api.loadStars();
  const favorite=()=>f.el('sessions-list').children.find(g=>g.dataset.group==='programs:favorite');
  assert.equal(find(favorite(),'program').length,16);assert.equal(find(favorite(),'group-foot').length,0,'no Show more hides any favorite');
  const home=f.el('sessions-list').children.find(g=>g.dataset.group==='programs:quick');
  assert.equal(find(home,'session-stub').length,12,'home positional stubs retain normal paging');
  favorite().children[0].onclick();assert.equal(favorite().children.length,1);
  const reloaded=fixture(sessionsOf(...rows),{sidebarOnly,storage});
  assert.ok(reloaded.api.state.sections.collapsed.has('programs:favorite'),'existing section store survives a cold page');
  favorite().children[0].onclick();f.api.setAllPrograms(true);
  if(sidebarOnly)f.api.cyclePhoneTree();else {assert.equal(f.el('sessions-collapse').dataset.next,'collapse');f.el('sessions-collapse').onclick();}
  assert.equal(favorite().children.length,1,'global collapse folds favorites too');
  if(sidebarOnly)f.api.cyclePhoneTree();else f.el('sessions-collapse').onclick();
  assert.equal(favorite().children.length,1,'fold-to-active leaves quiet favorites folded');
  favorite().children[0].onclick();
  const active=()=>find(favorite(),'section-active')[0];active().onclick({stopPropagation(){}});
  assert.equal(find(favorite(),'program').length,0,'active-only initially hides quiet favorites');
  const activated=f.api.state.inventory.find(r=>r.uuid===rows[0].uuid);activated.state='ready';activated.lifecycle='live';activated.liveState={primary_turn_open:true};f.api.inventory();
  assert.equal(find(favorite(),'session-stub').length,1,'an active favorite is admitted as its Running now stub');
  activated.liveState.primary_turn_open=false;f.api.inventory();
  assert.equal(find(favorite(),'program').length,1,'active-only keeps an admitted favorite after it settles');
  active().onclick({stopPropagation(){}});assert.equal(find(favorite(),'program').length,16);
});

test('Favorite programs empty section is available on cold live view; History stays in archive order',()=>{
  const f=fixture();f.api.inventory();assert.ok(f.el('sessions-list').children.some(g=>g.dataset.group==='programs:favorite'));
  f.api.state.archive={};f.api.state.inventory=[cond(850,'FAVARC',1)];f.api.state.stars={'program:FAVARC':{pinned_at:1}};f.api.inventory();
  assert.ok(!f.el('sessions-list').children.some(g=>g.dataset.group==='programs:favorite'));
  assert.equal(f.api.state.placed.get('p:FAVARC'),'home');
});

test('Favorite programs preserves OpenCode running hover holds and restores placement after an unsaved unstar',async()=>{
  const row=ocRow('favoc',{program:'FAVOC1',kind:'conductor',quickProgram:false,conductorN:1,busy:true,title:'[ap:FAVOC1]-001 · OpenCode favorite'});
  const f=fixture(sessionsOf(),{opencode:()=>ocEnv([row]),pins:(_url,options)=>options?.method==='POST'?
    new Response(JSON.stringify({error:'fixture write failed'}),{status:500}):{schemaVersion:1,pins:{'program:FAVOC1':{pinned_at:1}}}});
  await f.api.list();await f.api.ocList();await f.api.loadStars();
  const favorite=()=>f.el('sessions-list').children.find(g=>g.dataset.group==='programs:favorite');
  assert.equal(f.api.state.placed.get('p:FAVOC1'),'running');assert.equal(find(favorite(),'session-stub').length,1);
  f.api.setSidebarHover(true);f.api.state.oc.rows[0].busy=false;f.api.inventory();
  assert.equal(f.api.state.placed.get('p:FAVOC1'),'running','settled work stays under the pointer like any other program');
  f.api.setSidebarHover(false);assert.equal(f.api.state.placed.get('p:FAVOC1'),'favorite');
  assert.equal(find(favorite(),'program').length,1);
  await f.api.toggleStar('program:FAVOC1');
  assert.ok(Object.hasOwn(f.api.state.stars,'program:FAVOC1'));assert.equal(f.api.state.placed.get('p:FAVOC1'),'favorite');
  assert.equal(find(favorite(),'program').length,1);assert.match(f.el('sessions-status').textContent,/Star was not saved/);
});

test('U2.2 inventory reaches beyond the former 1024 server-page cap',async()=>{
  let pages=0;const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:++pages>1024?[host]:[],hasMore:pages<=1024,nextCursor:pages<=1024?'page-'+pages:null,status:'complete'}):undefined);
  await f.api.list();assert.equal(pages,1025);assert.equal(f.api.state.inventory.length,1);assert.doesNotMatch(f.el('sessions-status').textContent,/unavailable|longer than/);
});

test('U2.2 unpaged OpenCode inventory accepts more than 8 MiB without losing whole units',async()=>{
  const rows=Array.from({length:6501},(_,n)=>ocRow(String(n),{id:'ses_'+String(n).padStart(12,'0'),program:'LARGE1',
    title:'t'.repeat(300),model:'m'.repeat(200),directory:'/'+('d'.repeat(1023))}));
  const data=ocEnv(rows);assert.ok(Buffer.byteLength(JSON.stringify(data))>8*1024*1024);
  const f=fixture(undefined,{opencode:()=>data});await f.api.setView('all');
  assert.equal(f.api.state.oc.status,'complete');assert.equal(f.api.state.oc.rows.length,6501);
});

test('U12.10 roots: the top-painted rectangle follows p.active, collapsed/expanded, busy/waiting and both backends, never mere liveness',async()=>{
  const css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  // DOM activity already existed: the regression is its painter being covered by the head.
  assert.match(css,/\.program\.active-program::after \{[^}]*z-index:2;[^}]*pointer-events:none;/,'the root rectangle must paint above the sticky head');
  const busy=cond(701,'U12BUS',1,{state:'ready',lifecycle:'live',liveState:{primary_turn_open:true,pending_message_uuids:[]}}),
    waiting=cond(702,'U12WAI',1,{state:'ready',lifecycle:'live',liveState:{primary_turn_open:false,pending_message_uuids:[],background_tasks:{work:{task_type:'local_bash'}}}}),
    idle=cond(703,'U12IDL',1,{state:'ready',lifecycle:'live',liveState:{primary_turn_open:false,pending_message_uuids:[]}}),
    oc=ocRow('u12active',{program:'U12OCC',kind:'conductor',conductorN:1,busy:true});
  const f=fixture(sessionsOf(busy,waiting,idle),{opencode:()=>ocEnv([oc])});await f.api.list();await f.api.ocList();
  const root=core=>find(f.el('sessions-list'),'program').find(p=>p.children[0].title.includes('[ap:'+core+']'));
  assert.equal(f.api.state.tree.programs.length,4);
  for(const open of [false,true]) {
    for(const core of ['U12BUS','U12WAI','U12IDL','U12OCC'])f.api.state.lineageOpen.set('p:'+core,open);
    f.api.inventory();
    for(const p of f.api.state.tree.programs) {
      const node=root(p.core);assert.ok(node);
      assert.equal(node.classList.contains('active-program'),p.active,'the existing p.active alone selects the painter');
      assert.equal(p.active,f.api.state.activity.get(p.key).any);
      assert.equal(node.children[0].getAttribute('aria-expanded'),String(open));
      assert.equal(node.classList.contains('active-program'),p.core!=='U12IDL','live but idle is not active');
      assert.equal(headingLabel(node.parentNode.children[0]),p.active?'Running now':'Quick programs');
    }
  }
  assert.match(source,/box\.classList\.toggle\('active-program',p\.active\)/);
  assert.equal(find(f.el('sessions-list'),'session-stub').length,3,'lifted roots still leave unmarked home stubs, not duplicate programs');
  f.api.state.oc.stale=true;for(const row of f.api.state.inventory)row.liveState={primary_turn_open:false,pending_message_uuids:[],background_tasks:{}};f.api.inventory();
  assert.ok(find(f.el('sessions-list'),'program').every(p=>!p.classList.contains('active-program')),'quiet home roots have no running rectangle');
  assert.ok(sidebarItems(f).every(row=>!row.classList.contains('active')));
});
test('U12.13 sections: only top-level h3s receive the new heading style; whole-header clicks, active-only and collapse-all keep their meanings',async()=>{
  const css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  assert.match(css,/#sessions-list > \.session-group > h3 \{[^}]*font-size:var\(--section-heading-fs\)/);
  const running=cond(711,'U12HDR',1,{liveState:{primary_turn_open:true},state:'ready',lifecycle:'live'}),quiet=cond(712,'U12HDQ',1),
    manager={...host,sessionRef:'heading-manager',liveState:{primary_turn_open:false}};
  const f=fixture(sessionsOf(running,quiet,manager));await f.api.list();
  const groups=()=>f.el('sessions-list').children,headers=()=>groups().map(g=>g.children[0]),allH3=[];
  const walk=n=>{if(n.tag==='h3')allH3.push(n);for(const child of n.children || [])walk(child);};walk(f.el('sessions-list'));
  assert.deepEqual(allH3,headers(),'every h3 is one direct section header, never inside a program');
  const home=()=>groups().find(g=>headingLabel(g.children[0])==='Quick programs'),head=()=>home().children[0],count=headingText(head());
  head().onclick();assert.equal(home().children.length,1);assert.equal(headingText(head()),count,'label and count do not change');
  head().onclick();assert.ok(home().children.length>1);
  const open=head().children.find(c=>c.className==='section-toggle'),event={stopPropagation(){},preventDefault(){}};
  open.onclick(event);assert.equal(home().children.length,1);head().onclick();
  head().children.find(c=>c.className==='section-active').onclick(event);
  assert.equal(head().children.find(c=>c.className==='section-toggle').getAttribute('aria-expanded'),'true','active-only never collapses the section');
  f.el('sessions-collapse').onclick();assert.ok(groups().filter(g=>headingLabel(g.children[0])!=='Running now').every(g=>g.children.length===1));
  assert.equal(groups().find(g=>headingLabel(g.children[0])==='Running now').children[0].children[0].getAttribute('aria-expanded'),'true');
});

test('lineageTree: program → Conductors by number (handoff -001/-002) → seats by number → helpers by their lead; an unlisted parent puts the row one level up with a note',async()=>{
  const f=fixture();
  const c1=cond(1,'UF5GX3',1),c2=cond(2,'UF5GX3',2,{lineage:{entryAction:'resume'}});
  const pre=seat(3,{program:'UF5GX3',kind:'seat',quick:true,title:'[ap:UF5GX3] ▸ ⚡ quick worker · the job'},{createdAt:990}); // oc_quick's worker, before its Conductor
  const s2=seat(4,{program:'UF5GX3',kind:'seat',quick:true,conductorN:2,conductorId:c2.executionId,title:'[ap:UF5GX3]-002 ▸ ⚡ quick worker · second'});
  const helper=seat(5,{program:'UF5GX3',kind:'helper',quick:true,conductorN:2,parentSeat:s2.executionId,title:'[ap:UF5GX3]-002 ▸ ⚡ quick helper · [ap:HHHHHH]'},{role:'helper',displayRole:'helper'});
  const orphan=seat(6,{program:'UF5GX3',kind:'helper',quick:true,conductorN:1,parentSeat:'ses_gonegonegone',title:'h'},{role:'helper',displayRole:'helper'});
  const lost=seat(7,{program:'UF5GX3',kind:'seat',quick:true,conductorN:3,conductorId:cc(99),title:'[ap:UF5GX3]-003 ▸ ⚡ quick worker · lost'});
  const tree=f.api.lineageTree([c1,c2,pre,s2,helper,orphan,lost]),p=tree.programs[0],byId=tree.byId;
  assert.equal(tree.programs.length,1);assert.deepEqual(Array.from(p.top,x=>x.id),[c1.executionId,c2.executionId,lost.executionId]);
  assert.equal(byId.get(pre.executionId).parent.id,c1.executionId);          // no number, started before -001: row 001's
  assert.equal(byId.get(s2.executionId).parent.id,c2.executionId);
  assert.equal(byId.get(helper.executionId).parent.id,s2.executionId);        // the helper → lead edge, by id
  const o=byId.get(orphan.executionId);assert.equal(o.parent.id,c1.executionId);assert.match(o.note,/started by ses_gonegonego.*not listed/);
  const l=byId.get(lost.executionId);assert.equal(l.parent,null);assert.match(l.note,/started by Conductor -003 \(cc-00000099\), not listed/);
  assert.equal(p.title,'Pane round 7: programs');assert.equal(p.kind,'quick');assert.equal(p.managerSession,'184');assert.equal(p.count,7);
  assert.deepEqual(Array.from(tree.ancestors.get('s:'+helper.executionId)),['p:UF5GX3','s:'+c2.executionId,'s:'+s2.executionId]);
});
test('full stored Conductor titles name either backend immediately, retain custom names and never enrich clipped labels',async()=>{
  const full='PROGRAM · Millions of events: keep the Claude host and the Manager App fast as the stores grow — every program readable 雪';
  const c=cond(1,'UF5GX3',1,{title:'[ap:UF5GX3]-001 · '+full,lineage:{title:'[ap:UF5GX3]-001 · '+full}});
  const f=fixture(sessionsOf(c));
  await f.api.list();
  assert.equal(f.api.state.tree.programs[0].title,full);
  assert.equal(c.title,'[ap:UF5GX3]-001 · '+full,'stored/display session title is never rewritten');
  f.api.state.lineageOpen.set('p:UF5GX3',true);f.api.inventory();
  const program=find(f.el('sessions-list'),'program')[0];
  assert.deepEqual(find(program,'name-text').map(n=>n.textContent),[full,'[ap:UF5GX3]-001'],'the same Conductor label is not repeated below its program');
  const reads=f.requests.length;f.api.inventory();f.api.inventory();assert.equal(f.requests.length,reads,'a full-name render reads nothing');
  const custom=cond(2,'UF5GX3',2,{title:'[ap:UF5GX3]-002 · Deliberately chosen name',lineage:{title:'[ap:UF5GX3]-002 · Deliberately chosen name'}});
  assert.equal(f.api.lineageTree([c,custom]).programs[0].title,'Deliberately chosen name','the newest Conductor label wins exactly as stored');
  const short=full.slice(0,60);c.title=c.lineage.title='[ap:UF5GX3]-001 · '+short;
  await f.api.list();assert.equal(f.api.state.tree.programs[0].title,short,'a stored clipped title stays clipped, never extended by a name cache');
  const older=cond(3,'UF5GX3',1,{title:'[ap:UF5GX3]-001 · '+short,lineage:{title:'[ap:UF5GX3]-001 · '+short}}),newer=cond(4,'UF5GX3',2,{title:'[ap:UF5GX3]-002 · '+full,lineage:{title:'[ap:UF5GX3]-002 · '+full}});
  f.api.state.inventory=[older,newer];f.api.inventory();f.api.setAllPrograms(true); // expose the automatic quiet-Conductor fold
  assert.deepEqual(find(find(f.el('sessions-list'),'program')[0],'name-text').map(n=>n.textContent),[full,'[ap:UF5GX3]-001 · '+short,'[ap:UF5GX3]-002'],'a different prefix-only older label is not mistaken for the full stored label');
  const oc=ocRow('fulltitle',{program:'AAAAAA',kind:'conductor',title:'[ap:AAAAAA]-001 · '+full,conductorN:1});
  const g=fixture(undefined,{opencode:()=>ocEnv([oc])});await g.api.ocList();
  assert.equal(g.api.state.tree.programs.find(p=>p.core==='AAAAAA').title,full,'OpenCode-only programs use the stored Conductor title without enrichment');
});
test('a mixed program reads as one tree: a Claude Conductor and lead seat with OpenCode helpers under the lead, an OpenCode worker under the Conductor; non-program rows keep their groups',async()=>{
  const c=cond(1,'UHZREM',1),lead=seat(2,{program:'UHZREM',kind:'seat',fresh:true,seatRole:'lead',conductorN:1,conductorId:cc(1),title:'[ap:UHZREM]-001 ▸ seat · [ap:VRF26N]'},{displayRole:'lead-seat',cwd:'/home/dan'});
  const manager={...host,sessionRef:'m',uuid:U(1),role:'Manager',displayRole:'manager',sessionLabel:'Manager-Session-184',cwd:'/r/manager-sessions',createdAt:50};
  const lone={...seat(9,{}),lineage:undefined,title:'▸ ⚡ quick worker · Throwaway'};
  const helperOc=ocRow('help1',{parentSeat:lead.executionId,conductorN:1,conductorId:c.executionId}),workerOc=ocRow('work1',{kind:'seat',seatRole:'worker',fresh:true,quick:undefined,conductorN:1,title:'[ap:UHZREM]-001 ▸ worker · recheck (d)',busy:true});
  const f=fixture(sessionsOf(manager,c,lead,lone),{opencode:url=>url==='/api/opencode/programs'?ocEnv([helperOc,workerOc]):undefined});
  await f.api.list();await f.api.ocList();
  const groups=f.el('sessions-list').children.map(g=>headingText(g.children[0]));
  // Its OpenCode worker is busy: the whole program is active, so it sits in Running now, outlined (round 9b).
  assert.deepEqual(groups,['Running now · 1 program','Favorite programs · 0','Manager Sessions · 1','Quick programs · 1 program','Quick workers and helpers · quick-worker · 1']);
  const program=find(f.el('sessions-list'),'program')[0],head=program.children[0];
  assert.ok(program.classList.contains('active-program'),'an active program carries the outline');
  assert.equal(find(head,'name-text')[0].textContent,'Pane round 7: programs');assert.equal(find(head,'program-fold')[0].textContent,'⊟');
  assert.equal(find(find(head,'program-meta')[0],'meta-status')[0].textContent,'[ap:UHZREM] · from Manager-Session-184 · 4 sessions');
  assert.ok(program.classList.contains('live'),'a busy OpenCode member keeps the program live (open)');
  const words=find(program,'role-word').map(n=>n.textContent);assert.deepEqual(words,['Conductor:','Lead seat:','Helper:','Worker:']);
  const names=find(program,'name-text').map(n=>n.textContent);assert.deepEqual(names.slice(1),['[ap:UHZREM]-001','[ap:VRF26N]','[ap:WB93JF]','recheck (d)']);
  const chips=find(program,'backend-chip').map(n=>n.textContent);assert.deepEqual(chips,['Claude Code','Claude Code','OpenCode','OpenCode']);
  // The lead's tree-node holds the OpenCode helper; the Conductor's holds the lead and the OpenCode worker.
  const conductorNode=program.children[1].children[0],leadNode=conductorNode.children.at(-1).children[0];
  assert.match(leadNode.textContent,/Lead seat:\[ap:VRF26N\].*Helper:\[ap:WB93JF\]/s);
  assert.match(f.el('sessions-list').children.find(g=>g.dataset.group==='folder:quick-worker').textContent,/Quick worker:Throwaway/);
  assert.equal(sidebarItems(f).length,4); // Manager, Conductor, lead, the lone quick worker: each Claude row once
});
test('expanders: a program closes once it has ended, a click persists the choice, selecting a session opens its ancestors',async()=>{
  const c=cond(1,'AAAAAA',1),s=seat(2,{program:'AAAAAA',kind:'seat',quick:true,conductorN:1,title:'[ap:AAAAAA]-001 ▸ ⚡ quick worker · job'});
  const f=fixture(sessionsOf(c,s));await f.api.list();
  let program=find(f.el('sessions-list'),'program')[0];
  assert.equal(program.children.length,1,'ended program: closed by default');assert.equal(sidebarItems(f).length,0);
  program.children[0].onclick({stopPropagation(){}});program=find(f.el('sessions-list'),'program')[0];
  assert.equal(sidebarItems(f).length,2);assert.equal(f.api.state.lineageOpen.get('p:AAAAAA'),true);
  const toggle=find(program,'tree-toggle')[1];toggle.onclick({stopPropagation(){}});assert.equal(sidebarItems(f).length,1);  // the Conductor folds its seat
  f.api.state.lineageOpen.set('p:AAAAAA',false);f.api.inventory();assert.equal(sidebarItems(f).length,0);
  await f.api.browse(s);assert.equal(f.api.state.lineageOpen.get('p:AAAAAA'),true);assert.equal(f.api.state.lineageOpen.get('s:'+c.executionId),true);
  assert.equal(sidebarItems(f).filter(n=>n.classList.contains('selected')).length,1,'the selected seat is on screen');
});
test('the role filter flattens programs into one list with a breadcrumb under each match',async()=>{
  const c=cond(1,'BBBBBB',1),lead=seat(2,{program:'BBBBBB',kind:'seat',fresh:true,seatRole:'lead',conductorN:1,title:'[ap:BBBBBB]-001 ▸ seat · [ap:LEAD01]'},{displayRole:'lead-seat'});
  const f=fixture(sessionsOf(c,lead),{opencode:()=>ocEnv([ocRow('hh',{program:'BBBBBB',parentSeat:lead.executionId,conductorN:1,title:'[ap:BBBBBB]-001 ▸ ⚡ quick helper · [ap:HELP01]'})])});
  await f.api.list();await f.api.ocList();
  f.el('sessions-role').value='helper';f.el('sessions-role').onchange();
  const crumbs=find(f.el('sessions-list'),'session-crumb').map(n=>n.textContent);
  assert.deepEqual(crumbs,['[ap:BBBBBB] ▸ -001 ▸ Lead seat [ap:LEAD01] ▸']);
  f.el('sessions-role').value='lead-seat';f.el('sessions-role').onchange();
  assert.equal(sidebarItems(f).length,1);assert.match(find(f.el('sessions-list'),'session-crumb')[0].textContent,/^\[ap:BBBBBB\] ▸ -001 ▸$/);
});
// U2: on the desktop the row itself opens the OpenCode conversation; its 💬 chip alone is the glance.
test('an OpenCode row\'s 💬 chip opens a glance under it and never touches the conversation view; its id has a copy button',async()=>{
  const c=cond(1,'CCCCCC',1),oc=ocRow('glance1',{program:'CCCCCC',kind:'seat',quick:true,conductorN:1,busy:true,title:'[ap:CCCCCC]-001 ▸ ⚡ quick worker · ask'});
  const glance={schemaVersion:1,backend:'opencode',status:'complete',id:oc.id,title:oc.title,busy:true,outcome:null,model:oc.model,agent:'quick-ds41-flash',cost:0.01,
    messages:[{role:'user',time:1,chars:5,text:'hello'},{role:'assistant',time:2,chars:4000,text:'x'.repeat(1200)+'…'}]};
  const f=fixture(sessionsOf(c),{opencode:url=>url.startsWith('/api/opencode/glance?')?glance:ocEnv([oc])});
  await f.api.list();await f.api.browse(c);await f.flush();const before={session:f.api.state.session,generation:f.api.state.generation,scope:f.api.state.scope,events:f.el('history-events').children.length};
  await f.api.ocList();
  const chip=()=>find(find(f.el('sessions-list'),'oc-item')[0],'oc-glance-chip')[0];
  assert.equal(chip().getAttribute('aria-expanded'),'false');
  chip().onclick({stopPropagation(){}});await f.flush();await f.flush();assert.equal(chip().getAttribute('aria-expanded'),'true');
  assert.deepEqual(f.ocRequests.filter(u=>u.startsWith('/api/opencode/glance')),['/api/opencode/glance?id='+oc.id]);
  const box=find(f.el('sessions-list'),'oc-glance')[0];
  assert.match(box.textContent,/busy · quick-ds41-flash · deepseek-v4.1-flash#max · \$0.010.*prompt.*hello.*said.*x{20}/s);assert.match(box.textContent,new RegExp(oc.id));
  assert.equal(find(box,'glance-copy').length,1);
  assert.equal(f.api.state.session,before.session);assert.equal(f.api.state.generation,before.generation);assert.equal(f.api.state.scope,before.scope);
  assert.equal(f.el('history-events').children.length,before.events);assert.equal(f.messages.length,0);
  chip().onclick({stopPropagation(){}});await f.flush();assert.equal(find(f.el('sessions-list'),'oc-glance').length,0);
  assert.equal(f.api.state.session,before.session,'closing the glance does not browse either');
});
test('Claude rows never wait on OpenCode: a hung OpenCode read leaves the list complete; a failed one says "OpenCode unavailable" and keeps the last rows, marked',async()=>{
  const c=cond(1,'DDDDDD',1);let answer=new Promise(()=>{});
  const f=fixture(sessionsOf(c),{opencode:()=>answer});
  void f.api.ocList();await f.api.list();
  assert.equal(f.api.state.inventory.length,1);assert.equal(find(f.el('sessions-list'),'program').length,1);
  assert.equal(f.api.state.oc.status,'loading');
  const g=fixture(sessionsOf(c),{opencode:()=>answer});
  answer=ocEnv([ocRow('kept',{program:'DDDDDD',kind:'seat',quick:true,conductorN:1,busy:true})]);await g.api.list();await g.api.ocList();
  answer={schemaVersion:1,backend:'opencode',status:'unavailable',error:'OpenCode unreachable: refused',sessions:[]};await g.api.ocList();
  assert.match(g.el('sessions-list').textContent,/OpenCode unavailable: OpenCode unreachable: refused · OpenCode rows show their last known state/);
  g.api.state.lineageOpen.set('p:DDDDDD',true);g.api.inventory();
  assert.match(find(g.el('sessions-list'),'oc-item')[0].textContent,/OpenCode unavailable \(last known\)/);
  assert.equal(find(g.el('sessions-list'),'oc-item')[0].classList.contains('active'),false,'no busy spinner from a stale row');
});
test('role word first on line 1 and the script prefixes stripped; a name chosen with rename wins as typed',async()=>{
  const f=fixture();
  assert.equal(f.api.shortTitle('[ap:O220H4]-001 ▸ ⚡ quick helper · [ap:4VW7V3]'),'[ap:4VW7V3]');
  assert.equal(f.api.shortTitle('[ap:O220H4] ▸ ⚡ quick worker · Pane round 7'),'Pane round 7');
  assert.equal(f.api.shortTitle('[ap:UHZREM]-001 ▸ seat · [ap:VRF26N]'),'[ap:VRF26N]');
  assert.equal(f.api.shortTitle('[ap:UF5GX3]-002 · QUICK PROGRAM · TEST'),'QUICK PROGRAM · TEST');
  assert.equal(f.api.shortTitle('▸ ⚡ quick worker · Throwaway'),'Throwaway');
  // A Manager's fork (Claude --fork-self or the OpenCode fork route) keeps a 🍴 mark; nothing else gains one.
  assert.equal(f.api.shortTitle('Manager-Session-219 ▸ ⚡ quick fork worker · [ap:JOB123] · fix X'),'🍴 [ap:JOB123] · fix X');
  assert.equal(f.api.shortTitle('[ap:O220H4]-001 ▸ ⚡ quick fork worker · [ap:4VW7V3] · leg 1'),'🍴 [ap:4VW7V3] · leg 1');
  assert.equal(f.api.shortTitle('[ap:O220H4]-001 ▸ fork · [ap:4VW7V3]'),'fork · [ap:4VW7V3]');
  assert.equal(f.api.shortTitle(''),'');
  const s=seat(1,{program:'EEEEEE',kind:'seat',quick:true,conductorN:1,title:'[ap:EEEEEE]-001 ▸ ⚡ quick worker · job'},{sessionLabel:'My chosen name',nameSource:'chosen',lifecycle:'live',state:'ready'});
  const g=fixture(sessionsOf(s));await g.api.list();
  assert.match(find(g.el('sessions-list'),'session-item')[0].textContent,/^Quick worker:My chosen name/);
});
test('socket-derived titles from Python survive shortTitle and name both Claude and OpenCode sidebar rows',async()=>{
  // Exercise the actual one-time derivation, not hand-written title tails; no Workflowy or backend calls.
  const cases=[
    ['⚡ QUICK · IMPLEMENTER · U8 · seat session names · quick-sol61-fast-xhigh #prog-result','quick-sol61-fast-xhigh','implementer · U8 · seat session names'],
    ['⚡ QUICK · IMPLEMENTER · U7 · sidebar add-ons · quick-sol61-fast-xhigh','quick-sol61-fast-xhigh','implementer · U7 · sidebar add-ons'],
    ['✍️ Prompt · for the U8 implementer (seat session names) · recon C · Pane Usability Round #prog-recon','sol61-xhigh','implementer · U8 seat session names · recon C · Pane Usability Round'],
    ['✍️ Prompt · for findings auditor + adjudicator · general audit 1 · resident B · Pane Usability Round #prog-recon','sol61-xhigh','findings auditor + adjudicator · general audit 1 · resident B · Pane Usability Round'],
    ['🔧 Helper · U4 pre-fix evidence · for ses_eb7a5ff80ffeUV1n3NH9LANpDm','quick-ds41-flash','helper · U4 pre-fix evidence'],
    ['🔧 Helper · title consumers sweep (boom/CLI/pane) · for cc-ca4dc56e-6e73-48d1-b521-268593ed642b','quick-ds41-flash','helper · title consumers sweep boom/CLI/pane'],
    ['📁 Audit 1 · general · wave @62a475b · split #wfy-ns--local','quick-sol61-fast-xhigh','Audit 1 · general · wave 62a475b · split'],
    ['⚡ QUICK · FINDINGS_AUDITOR · 🔎 Findings · wave @62a475b · quick-sol61-fast-xhigh','quick-sol61-fast-xhigh','findings auditor · Findings · wave 62a475b'],
    ['⚖️ DISPATCHED · RESULT · stage 0 · contract [ap:W8HN1F] · opus55','opus55','stage 0'],
  ];
  const code=`import json,sys
sys.path.insert(0,sys.argv[1])
import oc_seat
titles=[]
for name,agent,_ in json.load(sys.stdin):
    oc_seat.socket_name=lambda socket: "[ap:SOCKET] "+name
    titles.append("[ap:PROG01]-008 ▸ ⚡ quick worker · "+oc_seat.socket_title("[ap:SOCKET]",None,agent=agent))
print(json.dumps(titles,ensure_ascii=False))`;
  const titles=JSON.parse(require('node:child_process').execFileSync('python3',['-c',code,path.resolve(__dirname,'../../scripts')],
    {input:JSON.stringify(cases),encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}}));
  const f=fixture();
  titles.forEach((title,i)=>assert.equal(f.api.shortTitle(title),'[ap:SOCKET] · '+cases[i][2]));
  const c=cond(1,'PROG01',8),s=seat(2,{program:'PROG01',kind:'seat',quick:true,conductorN:8,title:titles[0]},
    {lifecycle:'live',state:'ready'});
  const g=fixture(sessionsOf(c,s),{opencode:()=>ocEnv([ocRow('u8title',{program:'PROG01',kind:'seat',quick:true,
    conductorN:8,busy:true,title:titles[7]})])});
  await g.api.list();await g.api.ocList();
  assert.ok(find(g.el('sessions-list'),'session-item').some(n=>n.textContent.startsWith('Quick worker:[ap:SOCKET] · implementer · U8 · seat session names')));
  assert.match(find(g.el('sessions-list'),'oc-item')[0].textContent,/Quick worker:\[ap:SOCKET\] · findings auditor · Findings · wave 62a475b/);
});

for(const sidebarOnly of [false,true])test(`stored socket-run suffixes survive both backends in the ${sidebarOnly?'phone Programs':'desktop'} sidebar`,async()=>{
  const tail='[ap:SOCKET] · IMPLEMENTER · task · #8',title='[ap:PROG01]-008 ▸ ⚡ quick worker · '+tail;
  const c=cond(1,'PROG01',8),s=seat(2,{program:'PROG01',kind:'seat',quick:true,conductorN:8,title},
    {lifecycle:'live',state:'ready'});
  const f=fixture(sessionsOf(c,s),{sidebarOnly,opencode:()=>ocEnv([ocRow('numbered',{
    program:'PROG01',kind:'seat',quick:true,conductorN:8,busy:true,title:title.replace('#8','#9')})])});
  assert.equal(f.api.shortTitle(title),tail);
  await f.api.list();await f.api.ocList();
  const names=()=>find(f.el('sessions-list'),'name-text').map(n=>n.textContent);
  assert.ok(names().includes(tail));assert.ok(names().includes(tail.replace('#8','#9')));
  await f.api.list();await f.api.ocList();
  assert.ok(names().includes(tail));assert.ok(names().includes(tail.replace('#8','#9')));
});

// ---------------------------------------------------------------- round 8: Open in CodeNomad, live programs first
test('Open in CodeNomad on the glance: posts the session, its folder and title, then follows the request (missing tab → which folder to open → done) without touching the conversation view',async()=>{
  const c=cond(1,'FFFFFF',1),oc=ocRow('focus1',{program:'FFFFFF',kind:'seat',quick:true,conductorN:1,busy:true,directory:'/r/quick-worker',title:'[ap:FFFFFF]-001 ▸ ⚡ quick worker · focus'});
  const glance={schemaVersion:1,backend:'opencode',status:'complete',id:oc.id,title:oc.title,busy:true,directory:'/r/quick-worker',parentId:null,messages:[]};
  const posts=[];let served={state:'pending',missingTab:null};
  const transport=(url,opts)=>{
    if(url==='/api/focus-request' && opts?.method==='POST'){const body=JSON.parse(opts.body);posts.push(body);return {schemaVersion:1,request:{nonce:body.nonce,state:'pending',missingTab:null,noPage:false,outcome:null}};}
    if(url==='/api/focus-request')return {schemaVersion:1,request:{nonce:posts.at(-1).nonce,...served}};
    return sessionsOf(c)(url);
  };
  const f=fixture(transport,{opencode:url=>url.startsWith('/api/opencode/glance?')?glance:ocEnv([oc])});
  await f.api.list();await f.api.browse(c);await f.flush();const before={session:f.api.state.session,generation:f.api.state.generation,events:f.el('history-events').children.length};
  await f.api.ocList();find(f.el('sessions-list'),'oc-glance-chip')[0].onclick({stopPropagation(){}});await f.flush();await f.flush();
  const button=()=>find(f.el('sessions-list'),'focus-open')[0],status=()=>find(f.el('sessions-list'),'focus-status')[0]?.textContent || '';
  assert.equal(button().textContent,'Open in CodeNomad');assert.equal(button().disabled,false);
  button().onclick({stopPropagation(){}});await f.flush();await f.flush();
  assert.deepEqual({...posts[0],nonce:undefined},{sessionId:oc.id,directory:'/r/quick-worker',title:oc.title,parentId:null,nonce:undefined});
  assert.match(status(),/waiting for CodeNomad/);assert.equal(button().disabled,true,'one request at a time');
  served={state:'pending',missingTab:{folders:['/home/dan']},noPage:false};await f.api.focusPoll();
  assert.match(status(),/Open the CodeNomad tab for \/r\/quick-worker: this finishes by itself once it is open/);
  served={state:'claimed'};await f.api.focusPoll();assert.match(status(),/CodeNomad is switching to it/);
  served={state:'done',outcome:{code:'done',message:'shown in CodeNomad'}};await f.api.focusPoll();
  assert.match(status(),/✓ shown in CodeNomad/);assert.equal(button().disabled,false);
  const polls=f.requests.filter(([u])=>u==='/api/focus-request').length;await f.api.focusPoll();
  assert.equal(f.requests.filter(([u])=>u==='/api/focus-request').length,polls,'a finished request is not polled again');
  served={state:'failed',outcome:{code:'failed',message:'Clicked the session row in CodeNomad, but the session did not open'}};
  button().onclick({stopPropagation(){}});await f.flush();await f.flush();await f.api.focusPoll();
  assert.match(status(),/✗ Clicked the session row/);
  assert.equal(f.api.state.session,before.session);assert.equal(f.api.state.generation,before.generation);
  assert.equal(f.el('history-events').children.length,before.events);assert.equal(f.messages.length,0);
});
test('Open in CodeNomad is disabled when OpenCode gave no folder, and a refused request says so',async()=>{
  const c=cond(1,'GGGGGG',1),oc=ocRow('focus2',{program:'GGGGGG',kind:'seat',quick:true,conductorN:1,busy:true});
  const f=fixture(url=>url==='/api/focus-request'?(()=>{throw new Error('pane server refused');})():sessionsOf(c)(url),
    {opencode:url=>url.startsWith('/api/opencode/glance?')?{schemaVersion:1,backend:'opencode',status:'complete',id:oc.id,messages:[]}:ocEnv([oc])});
  await f.api.list();await f.api.ocList();find(f.el('sessions-list'),'oc-glance-chip')[0].onclick({stopPropagation(){}});await f.flush();await f.flush();
  assert.equal(find(f.el('sessions-list'),'focus-open')[0].disabled,true);
  await f.api.openInCodeNomad(oc.id,{directory:'/r/x'});
  assert.match(find(f.el('sessions-list'),'focus-status')[0].textContent,/✗ the pane server refused/);
});
test('home program sections stay Full then Quick; within a group, running programs still lead',async()=>{
  const live={lifecycle:'live',state:'ready'};
  const fullOld=cond(1,'HHHHHH',1,{lineage:{quickProgram:false}}),quickLive=cond(2,'IIIIII',1,live);
  const f=fixture(sessionsOf(fullOld,quickLive));await f.api.list();
  const heads=()=>f.el('sessions-list').children.map(g=>headingLabel(g.children[0]));
  assert.deepEqual(heads(),['Favorite programs','Full programs','Quick programs'],'home kinds keep their structural order even with a live Quick program');
  const g=fixture(sessionsOf(fullOld,cond(2,'IIIIII',1)));await g.api.list();
  assert.deepEqual(g.el('sessions-list').children.map(x=>headingLabel(x.children[0])),['Favorite programs','Full programs','Quick programs']);
  const fullLive=cond(3,'JJJJJJ',1,{lineage:{quickProgram:false},...live,createdAt:900}),quickNewer=cond(4,'KKKKKK',1,{...live,createdAt:2000});
  const h=fixture(sessionsOf(fullLive,quickNewer,fullOld));await h.api.list();
  assert.deepEqual(h.el('sessions-list').children.map(x=>headingLabel(x.children[0])),['Favorite programs','Full programs','Quick programs'],'both live: home kinds still keep their structural order');
  const fullPrograms=find(h.el('sessions-list').children.find(g=>g.dataset.group==='programs:full'),'program-head').map(n=>n.textContent);
  assert.match(fullPrograms[0],/JJJJJJ/,'within a group, the running program leads');
});

// ---------------------------------------------------------------- round 9b: activity at a glance, Reconnect
test('one definition of active: a turn open or waiting (turn closed, a background task open) puts the whole program in Running now, outlined; Managers get the waiting flag too',async()=>{
  const live={lifecycle:'live',state:'ready'},waiting={liveState:{primary_turn_open:false,background_tasks:{bnm0n7xml:{task_type:'local_bash'}}}};
  const manager={...host,sessionRef:'m',uuid:U(1),executionId:'cc-'+U(1),role:'Manager',displayRole:'manager',sessionLabel:'Manager-Session-184',cwd:'/r/manager-sessions',createdAt:50,...live,...waiting};
  const idleC=cond(1,'AAAAAA',1,live);                                              // alive, idle: not active
  const waitingC=cond(2,'BBBBBB',1,{...live,...waiting});                            // a Conductor waiting on its worker
  const busyC=cond(3,'CCCCCC',1,live),busySeat=seat(4,{program:'CCCCCC',kind:'seat',quick:true,conductorN:1,title:'[ap:CCCCCC]-001 ▸ ⚡ quick worker · busy'},
    {...live,liveState:{primary_turn_open:true}});                                   // only its worker is busy: the whole program is active
  const f=fixture(sessionsOf(manager,idleC,waitingC,busyC,busySeat));await f.api.list();
  const heads=f.el('sessions-list').children.map(g=>headingText(g.children[0]));
  assert.deepEqual(heads,['Running now · 2 programs','Favorite programs · 0','Manager Sessions · 1','Quick programs · 3 programs']);
  // The one Manager is the remembered newest fallback, so it is pinned rather than lifted, and its
  // own waiting flag travels with it: the pin outranks Running now.
  assert.equal(f.el('sessions-pinned').hidden,false);assert.equal(f.el('sessions-pinned').children.length,1);
  const running=f.el('sessions-list').children[0],programs=find(running,'program');
  assert.ok(programs.every(p=>p.classList.contains('active-program')),'every active program is outlined');
  assert.match(running.textContent,/BBBBBB/);assert.match(running.textContent,/CCCCCC/);
  assert.ok(!find(f.el('sessions-list').children.find(g=>g.dataset.group==='programs:quick'),'program')[0].classList.contains('active-program'),'an idle program has no outline');
  const flags=[...find(f.el('sessions-pinned'),'waiting-flag'),...find(f.el('sessions-list'),'waiting-flag')];
  assert.equal(flags.length,2,'the waiting Manager and the waiting Conductor; the busy worker spins instead');
  assert.match(flags[0].title,/Waiting \(waiting, not blocked\): its turn is closed; it is woken when one of its 1 background task completes\./);
  const busyRow=sidebarItems(f).find(n=>/busy/.test(n.textContent));assert.ok(busyRow.classList.contains('active'));assert.equal(find(busyRow,'waiting-flag').length,0);
  // Collapse all: every program and home section closes, Running now and outlines stay.
  f.el('sessions-collapse').onclick();
  assert.equal(find(f.el('sessions-list'),'tree-children').length,0);assert.equal(find(f.el('sessions-list'),'program').filter(p=>p.classList.contains('active-program')).length,2);
  f.el('sessions-expand').onclick();
  assert.equal(find(f.el('sessions-list'),'program').filter(p=>p.children.length>1).length,2,'only Running now is open; home sections remain collapsed');
  assert.equal(f.api.state.lineageOpen.get('p:AAAAAA'),true,'expand still opens the idle program behind its collapsed home section');
  // The role filter flattens: rows of an active program keep the outline.
  f.el('sessions-role').value='quick-worker';f.el('sessions-role').onchange();
  assert.ok(sidebarItems(f).every(n=>n.classList.contains('in-active-program')));
});
test('OpenCode waiting is best effort: an idle parent with a busy session under it is flagged, with a tooltip saying why',async()=>{
  const c=cond(1,'UHZREM',1,{lifecycle:'live',state:'ready'});
  const lead=ocRow('lead1',{kind:'seat',seatRole:'lead',conductorN:1,busy:false,title:'[ap:UHZREM]-001 ▸ seat · lead'});
  const child=ocRow('child1',{kind:'subagent',parentId:lead.id,busy:true,title:'explore'});
  const f=fixture(sessionsOf(c),{opencode:url=>url==='/api/opencode/programs'?ocEnv([lead,child]):undefined});
  await f.api.list();await f.api.ocList();
  const heads=f.el('sessions-list').children.map(g=>headingLabel(g.children[0]));assert.equal(heads[0],'Running now');
  const flag=find(f.el('sessions-list'),'waiting-flag');assert.equal(flag.length,1);
  assert.match(flag[0].title,/best effort.*\/api\/session\/active.*best guess/s);
});
// ---------------------------------------------------------------- U4: recorded-command waits, native title only
const u4Task=(command,over={})=>({task_type:'local_bash',description:'native task description',
  tool_input:{name:'Bash',command,description:'tool input description'},...over});
const u4Waiting=(tasks,over={})=>cond(401,'CORE04',1,{lifecycle:'live',state:'ready',
  liveState:{primary_turn_open:false,background_tasks:tasks},...over});
test('T4.6: a recorded doorbell names the listed target, role, program and socket without a hover read',async()=>{
  const target=seat(402,{program:'CORE04',kind:'seat',seatRole:'lead',conductorN:1,title:'Build the interface'},
    {executionId:'cc-abc',sessionLabel:'Build the interface',displayRole:'lead-seat',socket:'[ap:SOCK04]'});
  const c=u4Waiting({wait:u4Task('bash /x/scripts/oc_seat.sh wait cc-abc --max 540 2>&1 | tail -30')});
  const f=fixture(sessionsOf(c,target));await f.api.list();
  const flag=()=>find(f.el('sessions-list'),'waiting-flag')[0];
  assert.equal(flag().textContent,'🔔 Doorbell');
  assert.equal(flag().title,'Waiting (waiting, not blocked): its turn is closed; it is woken when one of its 1 background task completes.\n🔔 waits on Build the interface · Lead seat · [ap:CORE04] · socket [ap:SOCK04] — not blocking: wakes when that session stops');
  assert.match(sidebarItems(f).find(r=>find(r,'waiting-flag').length).textContent,/◌ 1 in background/);
  const before=f.requests.length;
  f.api.state.inventory.find(r=>r.executionId===c.executionId).liveState.background_tasks.second=u4Task('bash /x/scripts/oc.sh seat wait cc-abc --max 540');f.api.inventory();
  assert.equal(flag().textContent,'🔔 Doorbell','all waits share the same recogniser');
  assert.equal(flag().title.split('\n').length,3);assert.match(flag().title,/2 background tasks completes/);
  assert.equal(f.requests.length,before,'rendering/hover needs no extra fetch');
});
test('T4.7: a recorded map refresh names only the folder prefix and its observed start time',async()=>{
  const started=1700000000.5,task=u4Task('bash /x/oc_map_refresh.sh "25a49a99-1dbc-4f66-9cd3-d8a9f60ec976" --mode folder',{started_at:started});
  const c=u4Waiting({map:task}),f=fixture(sessionsOf(c));f.setNow(started*1000);await f.api.list();
  const flag=()=>find(f.el('sessions-list'),'waiting-flag')[0];
  assert.equal(flag().textContent,'🗺️ Map refresh');
  assert.equal(flag().title.split('\n')[1],'🗺️ map refresh of folder 25a49a99 · since '+new Date(started*1000).toTimeString().slice(0,8));
  delete f.api.state.inventory[0].liveState.background_tasks.map.started_at;f.api.inventory();assert.match(flag().title,/since not recorded$/);
});
test('T4.8: mixed recorded doorbell and map refresh tasks keep waiting and list both lines',async()=>{
  const c=u4Waiting({bell:u4Task('oc_seat.sh wait cc-absent'),map:u4Task("oc_map_refresh.sh '25a49a99' --mode folder")});
  const f=fixture(sessionsOf(c));await f.api.list();const flag=find(f.el('sessions-list'),'waiting-flag')[0];
  assert.equal(flag.textContent,'waiting');assert.equal(flag.title.split('\n').length,3);
  assert.match(flag.title,/2 background tasks completes/);
  assert.equal(flag.title.split('\n')[1],'🔔 waits on not listed · not listed · program not recorded · socket not recorded — not blocking: wakes when that session stops');
  assert.equal(flag.title.split('\n')[2],'🗺️ map refresh of folder 25a49a99 · since not recorded');
});
test('T4.9: unrecorded commands stay generic even when the description names a script; other inputs use the shared preview',async()=>{
  const task={task_type:'local_bash',description:'oc_seat.sh wait cc-abc: legacy waiter'},c=u4Waiting({legacy:task});
  const f=fixture(sessionsOf(c));await f.api.list();const flag=()=>find(f.el('sessions-list'),'waiting-flag')[0];
  assert.equal(flag().textContent,'waiting');
  assert.equal(flag().title.split('\n')[1],'local_bash: oc_seat.sh wait cc-abc: legacy waiter (command not recorded)');
  f.api.state.inventory[0].liveState.background_tasks={other:u4Task('printf "hello"',{tool_input:{name:'Bash',command:'printf "hello"',description:'oc_map_refresh.sh 25a49a99'}}),
    agent:{task_type:'local_agent',subagent_type:'manager-quick',description:'check the interface'}};
  f.api.inventory();assert.equal(flag().textContent,'waiting','description never feeds a recogniser');
  assert.equal(flag().title.split('\n')[1],JSON.stringify({command:'printf "hello"',description:'oc_map_refresh.sh 25a49a99'}));
  assert.equal(flag().title.split('\n')[2],'manager-quick: check the interface');
});
test('T4.10: the same mixed tree gives Claude its recorded Doorbell while OpenCode keeps only best-effort waiting',async()=>{
  const c=u4Waiting({bell:u4Task('oc_seat.sh wait ses_target123')}),lead=ocRow('target123',{id:'ses_target123',program:'CORE04',kind:'seat',seatRole:'lead',conductorN:1,
    title:'[ap:CORE04]-001 ▸ seat · OC target',busy:false,background_tasks:{fake:u4Task('oc_map_refresh.sh 25a49a99')}});
  const child=ocRow('u4child',{program:'CORE04',kind:'subagent',parentId:lead.id,busy:true});
  const f=fixture(sessionsOf(c),{opencode:()=>ocEnv([lead,child])});await f.api.list();await f.api.ocList();
  const claude=find(sidebarItems(f)[0],'waiting-flag')[0];
  assert.equal(claude.textContent,'🔔 Doorbell');assert.match(claude.title,/waits on OC target · Lead seat · \[ap:CORE04\] · socket not recorded/);
  const oc=find(f.el('sessions-list'),'oc-item').flatMap(r=>find(r,'waiting-flag'));
  assert.equal(oc.length,1);assert.equal(oc[0].textContent,'waiting');
  assert.equal(oc[0].title,'Waiting (best effort): idle, while a session started under it is busy. OpenCode reports only busy or idle (/api/session/active): a parent can read idle while its background subagents run, so this "waiting" is a best guess from a busy session started under it.');
  assert.doesNotMatch(oc[0].title,/Doorbell|Map refresh|oc_map_refresh\.sh|25a49a99/);
});

test('U4 waiting details use liveOf on every render, while the shared activity entry alone decides whether to flag',async()=>{
  const c=u4Waiting({bell:u4Task('oc_seat.sh wait cc-absent')}),f=fixture(sessionsOf(c));await f.api.list();
  const row=f.api.state.inventory[0];f.api.state.session=row;
  f.api.state.liveState={primary_turn_open:false,background_tasks:{map:u4Task('oc_map_refresh.sh 25a49a99')}};
  f.api.state.liveStateAt=f.api.state.inventoryAt+1;const before=f.requests.length;f.api.inventory();
  const flag=()=>find(f.el('sessions-pinned'),'waiting-flag')[0];
  // This is a Conductor, not a pinnable Manager: it remains in its program's Running now tree.
  assert.equal(find(f.el('sessions-list'),'waiting-flag')[0].textContent,'🗺️ Map refresh');
  assert.equal(flag(),undefined);assert.match(sidebarItems(f)[0].textContent,/◌ 1 in background/);
  f.api.state.liveState.primary_turn_open=true;f.api.inventory();
  assert.equal(find(f.el('sessions-list'),'waiting-flag').length,0);assert.ok(sidebarItems(f)[0].classList.contains('active'));
  f.api.state.liveStateAt=f.api.state.inventoryAt-1;f.api.inventory();
  assert.equal(find(f.el('sessions-list'),'waiting-flag')[0].textContent,'🔔 Doorbell','a stale poll never leaves a cached command label');
  assert.equal(f.requests.length,before);
});

test('Reconnect: with nothing selected it opens the newest live Manager; with a selection it finds the same conversation again by identity after a restart',async()=>{
  let gen=1;
  const mk=(n,over)=>({...host,sessionRef:'s'+n+'g'+gen,uuid:U(n),executionId:'cc-'+U(n),role:'Manager',displayRole:'manager',sessionLabel:'Manager-Session-'+(180+n),
    cwd:'/r/manager-sessions',scopes:[{label:'main',scopeRef:'main'+n+'g'+gen}],...over});
  const rows=()=>[mk(1,{createdAt:100,lifecycle:'ended',state:'exited'}),mk(4,{createdAt:400,lifecycle:'live',state:'ready',capabilities:{nativeInput:true,controls:true}}),mk(5,{createdAt:500,lifecycle:'ended',state:'exited'})];
  const reads=[];
  const f=fixture(url=>{
    if(url.startsWith('/api/claude/sessions'))return env({sessions:rows(),status:'complete',hasMore:false});
    if(url.startsWith('/api/claude/events')){const q=new URLSearchParams(url.split('?')[1]);reads.push(q.get('sessionRef'));
      if(!q.get('sessionRef').endsWith('g'+gen))return new Response(JSON.stringify(env({status:'stale',error:'host handle expired; reselect'})),{status:409});
      return page([event(1)],{sessionRef:q.get('sessionRef'),scopeRef:q.get('scopeRef')});}
  });
  await f.api.reconnect();
  assert.equal(f.api.state.session.sessionLabel,'Manager-Session-184','nothing selected: the newest live Manager, not the newest ended one');
  // A real 409 starts the existing in-place resync (the fixture used to JSON-encode Response as {},
  // accidentally testing a schema failure). Manual Reconnect still finds the same new handle.
  gen=2;await f.api.page('delta','delta');assert.equal(f.api.state.blocked,false);assert.ok(f.api.state.resync);
  await f.api.reconnect();
  assert.equal(f.api.state.session.sessionRef,'s4g2');assert.equal(f.api.state.blocked,false);assert.equal(f.api.state.follow,true);
  assert.equal(f.el('history-follow').getAttribute('aria-pressed'),'true');assert.equal(reads.at(-1),'s4g2');
  assert.ok(sidebarItems(f).find(n=>/Manager-Session-184/.test(n.textContent)).classList.contains('selected'));
});
// ------------------------------------------------------------- model and thinking-level labels
const said=(n,model,effort,extra={})=>({...event(n),model,effort,time:1792000000+n,...extra});
const modelLabel=(f,n)=>f.api.state.rows.get('r'+n).model;
test('Dan: each Claude block names the model and thinking level recorded on it, a mid-conversation switch included; You blocks and unrecorded facts show nothing',async()=>{
  const you={recordRef:'r1',uuid:'uuid1',kind:'user',status:'stored',text:'hello',calls:[],time:1792000001};
  const f=fixture(()=>page([you,{...tool(2,'stored'),model:'claude-opus-5-5',effort:'high'},said(3,'claude-opus-5-5','high'),
    said(4,'claude-fable-5-1','medium'),said(5,'claude-opus-5-5',null),said(6,null,null),said(7,'claude-3-5-sonnet-20241022','low'),said(8,'limits-local-fixture','low')]));
  await f.api.browse(host);
  assert.equal(modelLabel(f,1).textContent,'');                                   // You: nothing
  assert.equal(modelLabel(f,2).textContent,'(Opus 5.5 · high)');                   // Claude · tools
  assert.equal(modelLabel(f,4).textContent,'(Fable 5.1 · medium)');                // after a mid-conversation switch: its own model
  assert.equal(modelLabel(f,5).textContent,'(Opus 5.5)');                          // no level recorded on it: the model alone
  assert.equal(modelLabel(f,6).textContent,'');                                    // no model recorded: nothing at all
  assert.equal(modelLabel(f,7).textContent,'(Sonnet 3.5 · low)');
  assert.equal(modelLabel(f,8).textContent,'(limits-local-fixture · low)');        // an unknown id is shown exactly as recorded
  const row=f.api.state.rows.get('r3'),label=row.label.children;
  assert.match(row.role.textContent,/^Claude/);assert.equal(label[0],row.role);assert.equal(label[1],row.model);assert.equal(row.model.className,'event-model'); // right after the role
  assert.match(row.model.title,/Recorded on this message: claude-opus-5-5 · thinking high/);
  assert.match(modelLabel(f,5).title,/no thinking level recorded for it/);
  // A level that lands later (its transcript record trailed the stream) updates the same block in place.
  f.api.reconcile([said(5,'claude-opus-5-5','xhigh')],'summary','epoch');assert.equal(modelLabel(f,5).textContent,'(Opus 5.5 · xhigh)');
  const css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  assert.match(css,/\.event-model \{ font-size:var\(--fs-tiny\); font-weight:650;[^}]*opacity:var\(--model-dim\)/); // the role's own type, dimmer
  assert.match(css,/\.turn-final \.event-role,\.turn-final \.event-model \{ color:var\(--final-label\)/);
});
test('Dan: the sidebar\'s second line ends with the models and thinking levels a session\'s own turns recorded, never a guess and never a third line',async()=>{
  const facts=(models,levels,{complete=true,levelsComplete=true}={})=>({status:complete?'complete':'scanning',records:5,models,levels,modelsComplete:complete,levelsComplete:complete&&levelsComplete});
  const rows=[{...host,sessionRef:'m1',uuid:'u1',executionId:'cc-u1',displayRole:'manager',sessionLabel:'Manager-Session-185',createdAt:9,turnFacts:facts(['claude-opus-5-5'],['high'])},
    {...host,sessionRef:'m2',uuid:'u2',executionId:'cc-u2',displayRole:'manager',sessionLabel:'Manager-Session-184',createdAt:8,turnFacts:facts(['claude-opus-5-5','claude-fable-5-1'],['high','medium'])},
    {...host,sessionRef:'m3',uuid:'u3',executionId:'cc-u3',displayRole:'manager',sessionLabel:'Manager-Session-183',createdAt:7,turnFacts:facts(['claude-opus-5-5'],['high'],{levelsComplete:false})},
    {...host,sessionRef:'m4',uuid:'u4',executionId:'cc-u4',displayRole:'manager',sessionLabel:'Manager-Session-182',createdAt:6,turnFacts:facts(['claude-opus-5-5'],['high'],{complete:false})},
    {...host,sessionRef:'m5',uuid:'u5',executionId:'cc-u5',displayRole:'manager',sessionLabel:'Manager-Session-181',createdAt:5,turnFacts:facts(['claude-opus-5-5','claude-fable-5-1','claude-sonnet-5-5','claude-haiku-5-5'],['low','high'])},
    {...host,sessionRef:'m6',uuid:'u6',executionId:'cc-u6',displayRole:'manager',sessionLabel:'Manager-Session-180',createdAt:4}];
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:rows,status:'complete',hasMore:false}):undefined);
  await f.api.list();
  const meta=label=>{const item=sidebarItems(f).find(n=>n.textContent.includes(label)),open=item.children[0];
    return {lines:open.children.filter(c=>c.className==='session-meta'),text:open.children.filter(c=>c.className==='session-meta').at(-1).textContent};};
  assert.match(meta('Manager-Session-185').text,/ \(Opus 5\.5 · high\)$/);
  assert.equal(meta('Manager-Session-185').lines.length,meta('Manager-Session-180').lines.length); // the same status line, no new line
  assert.match(meta('Manager-Session-184').text,/ \(Opus 5\.5, Fable 5\.1 · medium, high\)$/); // models in first-use order, levels low → high
  assert.match(meta('Manager-Session-183').text,/ \(Opus 5\.5\)$/);                           // a turn without a recorded level: no level list
  assert.doesNotMatch(meta('Manager-Session-182').text,/Opus|\(/);                             // not read to its end yet: nothing
  assert.match(meta('Manager-Session-181').text,/ \(multiple models · low, high\)$/);
  assert.doesNotMatch(meta('Manager-Session-180').text,/Opus|\(/);                             // no facts at all: nothing
  const used=sidebarItems(f).find(n=>n.textContent.includes('Manager-Session-184')).children[0].children.filter(c=>c.className==='session-meta').at(-1).children.find(c=>c.className==='session-models');
  assert.match(used.title,/Recorded by its own turns: claude-opus-5-5, claude-fable-5-1 · thinking medium, high/);
});
test('Dan: a native subagent\'s dim line names its agent type with the model and level its turns recorded (manager-quick · Opus 5.5 · high)',async()=>{
  const parent={...host,capabilities:{nativeInput:true,subagents:true}};
  const facts=(models,levels)=>({status:'complete',records:3,models,levels,modelsComplete:true,levelsComplete:true});
  const child=(id,type,turnFacts)=>({...host,source:'native',role:'subagent',parentUuid:host.uuid,sessionRef:'child-'+id,agentId:id,agentType:type,description:'task '+id,
    title:'Manager-Session-185 ▸ '+type+' · task '+id,capabilities:{nativeInput:false,subagents:true},scopes:[{label:'agent:'+id,scopeRef:'scope-'+id}],...(turnFacts?{turnFacts}:{})});
  const kids=[child('a','manager-quick-high',facts(['claude-opus-5-5'],['high'])),child('b','manager-quick-high',facts(['claude-fable-5-1'],['xhigh'])),
    child('c','manager-quick-xhigh'),child('d','manager-quick',facts(['claude-opus-5-5'],['medium']))];
  const f=fixture(url=>url.startsWith('/api/claude/subagents')?env({sessions:kids,hasMore:false,status:'complete'}):undefined);
  await f.api.browse(parent);f.api.state.childNextPoll=0;await f.api.poll();
  const sub=i=>f.el('subagents-list').children[i].children[1].textContent;
  assert.equal(sub(0),'manager-quick · Opus 5.5 · high');          // the name's level suffix is exactly the one level recorded
  assert.equal(sub(1),'manager-quick-high · Fable 5.1 · xhigh');   // the recorded level differs from the name: both stay visible
  assert.equal(sub(2),'manager-quick-xhigh');                      // nothing recorded yet: the agent type alone
  assert.equal(sub(3),'manager-quick · Opus 5.5 · medium');
  assert.match(f.el('subagents-list').children[0].title,/Recorded by its own turns: claude-opus-5-5 · thinking high/);
});
const at=iso=>Date.parse(iso)/1000;
test('Dan: hovering a Claude row shows its first and last turn in Eastern time; an end not recorded or not read yet says unknown',async()=>{
  const facts=(firstAt,lastAt,status='complete')=>({status,records:4,models:['claude-opus-5-5'],levels:['high'],modelsComplete:status==='complete',levelsComplete:status==='complete',
    ...(firstAt?{firstAt}:{}),...(lastAt?{lastAt}:{})});
  const rows=[{...host,sessionRef:'m1',uuid:'u1',executionId:'cc-u1',displayRole:'manager',sessionLabel:'Manager-Session-185',createdAt:9,turnFacts:facts(at('2026-10-15T23:02:00Z'),at('2026-10-16T01:48:30Z'))},
    {...host,sessionRef:'m2',uuid:'u2',executionId:'cc-u2',displayRole:'manager',sessionLabel:'Manager-Session-184',createdAt:8,turnFacts:facts(at('2026-10-15T20:00:00Z'),null,'scanning')},
    {...host,sessionRef:'m3',uuid:'u3',executionId:'cc-u3',displayRole:'manager',sessionLabel:'Manager-Session-183',createdAt:7},
    {...host,sessionRef:'m4',uuid:'u4',executionId:'cc-u4',displayRole:'manager',sessionLabel:'Manager-Session-182',createdAt:6,turnFacts:facts(at('2025-12-31T23:30:00Z'),at('2026-01-01T05:10:00Z'))}];
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:rows,status:'complete',hasMore:false}):undefined);
  f.setNow(Date.parse('2026-10-16T02:00:00Z'));await f.api.list();
   const hover=label=>sidebarItems(f).find(n=>n.textContent.includes(label)).children[0].title.split('\n').slice(0,2).join('\n');
   assert.equal(hover('Manager-Session-185'),'First: Thu Oct 15, 7:02 PM ET\nLast: Thu Oct 15, 9:48 PM ET');            // EDT, the evening across UTC midnight
   assert.equal(hover('Manager-Session-184'),'First: Thu Oct 15, 4:00 PM ET\nlast turn: unknown (transcript still being read)');
   assert.equal(hover('Manager-Session-183'),'first turn: unknown\nlast turn: unknown');                                // no native transcript: no guess
   assert.equal(hover('Manager-Session-182'),'First: Wed Dec 31, 2025, 6:30 PM ET\nLast: Thu Jan 1, 12:10 AM ET');      // EST, a past year named
  // A native subagent's row carries the same line.
  const parent={...host,capabilities:{nativeInput:true,subagents:true}};
  const kid={...host,source:'native',role:'subagent',parentUuid:host.uuid,sessionRef:'child-a',agentId:'a',agentType:'manager-quick',description:'task a',
    title:'Manager-Session-185 ▸ manager-quick · task a',capabilities:{nativeInput:false,subagents:true},scopes:[{label:'agent:a',scopeRef:'scope-a'}],turnFacts:facts(at('2026-07-04T23:02:00Z'),at('2026-07-04T23:09:00Z'))};
  const g=fixture(url=>url.startsWith('/api/claude/subagents')?env({sessions:[kid],hasMore:false,status:'complete'}):undefined);
  g.setNow(Date.parse('2026-10-16T02:00:00Z'));await g.api.browse(parent);g.api.state.childNextPoll=0;await g.api.poll();
   assert.match(g.el('subagents-list').children[0].title,/^First: Sat Jul 4, 7:02 PM ET\nLast: Sat Jul 4, 7:09 PM ET\n/);
});
test('Dan: every Claude row shows its first and latest turn right of its models, YYYY-MM-DD HH:MM ET; an end not read yet is —, nothing known shows nothing',async()=>{
  const facts=(firstAt,lastAt,status='complete')=>({status,records:4,models:['claude-opus-5-5'],levels:['high'],modelsComplete:status==='complete',levelsComplete:status==='complete',
    ...(firstAt?{firstAt}:{}),...(lastAt?{lastAt}:{})});
  const rows=[{...host,sessionRef:'m1',uuid:'u1',executionId:'cc-u1',displayRole:'manager',sessionLabel:'Manager-Session-185',createdAt:9,turnFacts:facts(at('2026-10-09T01:14:00Z'),at('2026-10-19T03:52:40Z'))},
    {...host,sessionRef:'m2',uuid:'u2',executionId:'cc-u2',displayRole:'manager',sessionLabel:'Manager-Session-184',createdAt:8,turnFacts:facts(at('2026-10-15T20:00:00Z'),null,'scanning')},
    {...host,sessionRef:'m3',uuid:'u3',executionId:'cc-u3',displayRole:'manager',sessionLabel:'Manager-Session-183',createdAt:7},
    {...host,sessionRef:'m4',uuid:'u4',executionId:'cc-u4',displayRole:'manager',sessionLabel:'Manager-Session-182',createdAt:6,turnFacts:facts(at('2026-01-01T04:05:00Z'),at('2026-01-01T05:10:00Z'))}];
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:rows,status:'complete',hasMore:false}):undefined);
  f.setNow(Date.parse('2026-10-19T04:00:00Z'));await f.api.list();
  const meta=label=>find(sidebarItems(f).find(n=>n.textContent.includes(label)),'session-meta').at(-1);
  const parts=label=>meta(label).children.map(n=>n.className);
   assert.deepEqual(parts('Manager-Session-185'),['meta-status','session-models',undefined,'session-times'],'status, models, then the times: each its own wrap unit');
   assert.equal(find(meta('Manager-Session-185'),'session-models')[0].textContent,' (Opus 5.5 · high)');
   assert.equal(find(meta('Manager-Session-185'),'session-times')[0].textContent,'(first 2026-10-08 21:14 ET · last 2026-10-18 23:52 ET)');   // EDT, the evening across UTC midnight
   assert.ok(meta('Manager-Session-185').classList.contains('meta-times'));
   assert.match(find(meta('Manager-Session-185'),'session-times')[0].title,/^First: Thu Oct 8, 9:14 PM ET\nLast: Sun Oct 18, 11:52 PM ET\n/);
   assert.equal(find(meta('Manager-Session-184'),'session-times')[0].textContent,'(first 2026-10-15 16:00 ET · last —)');                       // still being read
   assert.equal(find(meta('Manager-Session-183'),'session-times').length,0);assert.ok(!meta('Manager-Session-183').classList.contains('meta-times'));
   assert.equal(find(meta('Manager-Session-182'),'session-times')[0].textContent,'(first 2025-12-31 23:05 ET · last 2026-01-01 00:10 ET)');   // EST, 24-hour, across the year
  // A native subagent's row carries the same parenthetical on its own line under its agent and model.
  const parent={...host,capabilities:{nativeInput:true,subagents:true}};
  const kid={...host,source:'native',role:'subagent',parentUuid:host.uuid,sessionRef:'child-a',agentId:'a',agentType:'manager-quick',description:'task a',
    title:'Manager-Session-185 ▸ manager-quick · task a',capabilities:{nativeInput:false,subagents:true},scopes:[{label:'agent:a',scopeRef:'scope-a'}],turnFacts:facts(at('2026-07-04T23:02:00Z'),at('2026-07-04T23:09:00Z'))};
  const g=fixture(url=>url.startsWith('/api/claude/subagents')?env({sessions:[kid],hasMore:false,status:'complete'}):undefined);
  g.setNow(Date.parse('2026-10-16T02:00:00Z'));await g.api.browse(parent);g.api.state.childNextPoll=0;await g.api.poll();
   const child=g.el('subagents-list').children[0];
   assert.equal(child.children[1].textContent,'manager-quick · Opus 5.5 · high');
   assert.equal(child.children[2].textContent,'(first 2026-07-04 19:02 ET · last 2026-07-04 19:09 ET)');
});
test('Dan: a program row shows its sessions\' earliest first and latest last turn on its meta line, like a session row; an end not read yet is —, nothing known shows nothing, and it fills in on the refresh',async()=>{
  const facts=(firstAt,lastAt,status='complete')=>({status,records:4,models:['claude-opus-5-5'],levels:['high'],modelsComplete:status==='complete',levelsComplete:status==='complete',
    ...(firstAt?{firstAt}:{}),...(lastAt?{lastAt}:{})});
  const c=cond(1,'TIMES1',1,{turnFacts:facts(at('2026-10-09T01:14:00Z'),at('2026-10-12T15:00:00Z'))});
  let s=seat(2,{program:'TIMES1',kind:'seat',quick:true,conductorN:1,conductorId:cc(1),title:'[ap:TIMES1]-001 ▸ ⚡ quick worker · job'},{turnFacts:facts(at('2026-10-10T12:00:00Z'),null,'scanning')});
  const bare=cond(3,'TIMES2',1,{turnFacts:undefined,createdAt:undefined});
  let rows=[c,s,bare];
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:rows,status:'complete',hasMore:false}):undefined);
  f.setNow(Date.parse('2026-10-19T04:00:00Z'));await f.api.list();
  const meta=core=>find(f.el('sessions-list'),'program-meta').find(n=>n.textContent.includes('[ap:'+core+']'));
   assert.deepEqual(meta('TIMES1').children.map(n=>n.className),['meta-status',undefined,'session-times'],'the facts, then the times: each its own wrap unit');
   assert.ok(meta('TIMES1').classList.contains('meta-times'));
   assert.equal(find(meta('TIMES1'),'session-times')[0].textContent,'(first 2026-10-08 21:14 ET · last —)','a member still being read leaves the end unknown');
   assert.match(find(meta('TIMES1'),'session-times')[0].title,/^First: Thu Oct 8, 9:14 PM ET\nlast turn: unknown \(1 transcript still being read\)\nThe earliest first turn and the latest turn across its listed sessions/);
   assert.equal(find(meta('TIMES2'),'session-times').length,0,'nothing known: no placeholder');assert.ok(!meta('TIMES2').classList.contains('meta-times'));
  // The transcript is read: the next refresh fills the end in, the latest across the program.
  s={...s,turnFacts:facts(at('2026-10-10T12:00:00Z'),at('2026-10-19T03:52:40Z'))};rows=[c,s,bare];await f.api.list();
   assert.equal(find(meta('TIMES1'),'session-times')[0].textContent,'(first 2026-10-08 21:14 ET · last 2026-10-18 23:52 ET)');
});
test('Eastern time is DST-correct on both boundaries of 2026',async()=>{
  const rows=[['2026-03-08T06:59:00Z','2026-03-08T07:00:00Z','Sun Mar 8, 1:59 AM ET','Sun Mar 8, 3:00 AM ET'],     // spring forward: 1:59 EST, then 3:00 EDT
    ['2026-11-01T05:30:00Z','2026-11-01T06:30:00Z','Sun Nov 1, 1:30 AM ET','Sun Nov 1, 1:30 AM ET']]                  // fall back: 1:30 EDT, then 1:30 EST again
    .map(([a,b],i)=>({...host,sessionRef:'d'+i,uuid:'d'+i,executionId:'cc-d'+i,displayRole:'manager',sessionLabel:'Manager-Session-'+(170+i),createdAt:i,
      turnFacts:{status:'complete',records:2,models:[],levels:[],modelsComplete:false,levelsComplete:false,firstAt:at(a),lastAt:at(b)}}));
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:rows,status:'complete',hasMore:false}):undefined);
  f.setNow(Date.parse('2026-12-01T00:00:00Z'));await f.api.list();
   const hover=label=>sidebarItems(f).find(n=>n.textContent.includes(label)).children[0].title.split('\n').slice(0,2).join('\n');
   assert.equal(hover('Manager-Session-170'),'First: Sun Mar 8, 1:59 AM ET\nLast: Sun Mar 8, 3:00 AM ET');
   assert.equal(hover('Manager-Session-171'),'First: Sun Nov 1, 1:30 AM ET\nLast: Sun Nov 1, 1:30 AM ET');
});
test('Dan: an OpenCode row reads its turn times once when first drawn, cached while its stamps stand, for its times line and hover; OpenCode down says unknown',async()=>{
  const c=cond(1,'TTTTTT',1),oc=ocRow('turns1',{program:'TTTTTT',kind:'seat',quick:true,conductorN:1,updatedAt:100,idleAt:200,title:'[ap:TTTTTT]-001 ▸ ⚡ quick worker · ask'});
  let answer={schemaVersion:1,backend:'opencode',status:'complete',id:oc.id,firstAt:at('2026-10-15T23:02:00Z'),lastAt:at('2026-10-16T01:48:00Z')};
  const f=fixture(sessionsOf(c),{opencode:url=>url.startsWith('/api/opencode/turns?')?answer:ocEnv([oc])});
  f.setNow(Date.parse('2026-10-16T02:00:00Z'));f.api.state.lineageOpen.set('p:TTTTTT',true);await f.api.list();await f.api.ocList();
  const button=()=>find(f.el('sessions-list'),'oc-item')[0].children[0];
   assert.match(button().title,/^first turn: unknown \(turn times still being read\)\nlast turn: unknown \(turn times still being read\)\n/);
   assert.equal(find(button(),'session-times').length,0,'no times line before they are read');
    assert.deepEqual(f.ocRequests.filter(u=>u.includes('/turns')),[],'optional details are queued after the row is painted');
  await f.flush();await f.flush();
   assert.match(button().title,/^First: Thu Oct 15, 7:02 PM ET\nLast: Thu Oct 15, 9:48 PM ET\n/);
   assert.equal(find(button(),'session-times')[0].textContent,'(first 2026-10-15 19:02 ET · last 2026-10-15 21:48 ET)');
  button().onpointerenter();await f.flush();await f.flush();
  assert.deepEqual(f.ocRequests.filter(u=>u.includes('/turns')),['/api/opencode/turns?id='+oc.id]);
   f.api.inventory();assert.match(button().title,/^First: Thu Oct 15, 7:02 PM ET\nLast: Thu Oct 15, 9:48 PM ET\n/,'a rebuilt row keeps both cached times as its first two lines');
  button().onpointerenter();await f.flush();assert.equal(f.ocRequests.filter(u=>u.includes('/turns')).length,1,'cached while its stamps stand');
  // OpenCode down: the hover says unknown, never a stale or guessed time.
  answer={schemaVersion:1,backend:'opencode',status:'unavailable',id:oc.id,error:'OpenCode unreachable'};
  f.api.state.oc.rows[0]={...oc,idleAt:300};f.api.inventory();button().onfocus();await f.flush();await f.flush();
   assert.match(button().title,/^first turn: unknown \(OpenCode did not answer\)\nlast turn: unknown \(OpenCode did not answer\)\n/);
});

// ---------------------------------------------------------------- telemetry (oc.sh telemetry) on every tree row
const teleSeg=(full,short=full,over={})=>({key:'k#0',segment:0,kind:'fresh',label:'fresh',full,short,line:'fresh · '+full,flags:[],notes:[],live:false,...over});
const teleEntry=(sid,segments,over={})=>({sid,backend:sid.startsWith('cc-')?'claude':'opencode',role:'IMPLEMENTER',roleSource:'title',phases:true,unit:'U01',lastT:1,segments,...over});
const teleAnswer=sessions=>({schemaVersion:1,backend:'telemetry',status:'complete',program:'[ap:TELEM1]',asOf:1,sessions});
const IMPL_FULL='start-up 50.3 KT · edit@57.2 KT · report@427 KT · end 481 KT · 0/0/96/14 min · ⚑2',IMPL_SHORT='50.3 (0) → 57.2 (0) → 427 (96) → 481 KT (14 min) ⚑2';
function teleProgram(answer,opts={}) {
  const facts={status:'complete',records:4,models:['claude-opus-5-5'],levels:['high'],modelsComplete:true,levelsComplete:true,firstAt:at('2026-10-15T23:00:00Z'),lastAt:at('2026-10-16T01:00:00Z')};
  const c=cond(1,'TELEM1',1,{turnFacts:facts});
  let s=seat(2,{program:'TELEM1',kind:'seat',quick:true,conductorN:1,conductorId:cc(1),title:'[ap:TELEM1]-001 ▸ ⚡ quick worker · U01'},{turnFacts:facts});
  const oc=ocRow('tele1',{program:'TELEM1',kind:'seat',quick:true,conductorN:1,updatedAt:100,idleAt:200,title:'[ap:TELEM1]-001 ▸ ⚡ quick worker · U02'});
  const rows=()=>[c,s,...(opts.extra || [])];
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:rows(),status:'complete',hasMore:false}):undefined,
    {telemetry:url=>answer(url),opencode:url=>url.startsWith('/api/opencode/turns?')?{schemaVersion:1,backend:'opencode',status:'complete',id:oc.id,firstAt:at('2026-10-15T23:02:00Z'),lastAt:at('2026-10-16T01:48:00Z')}:ocEnv([oc]),sidebarOnly:opts.sidebarOnly});
  f.setNow(Date.parse('2026-10-16T02:00:00Z'));f.api.state.lineageOpen.set('p:TELEM1',true);
  return {f,c,oc,setSeat:x=>{s={...s,...x};},row:label=>sidebarItems(f).find(n=>n.textContent.includes(label))};
}
const runsOf=tele=>find(tele,'tele-run').map(n=>n.textContent);
test('Dan: every program-tree row shows its telemetry on its own line under the times line, in the same gray: an implementer\'s chain with every bracket, another role\'s context and wall clock; one read per program, again only when its rows move',async()=>{
  const sessions={[cc(1)]:teleEntry(cc(1),[teleSeg('context 35.6 → 522 KT · 126 min')],{role:'CONDUCTOR',roleSource:'listed',phases:false,unit:null}),
    [cc(2)]:teleEntry(cc(2),[teleSeg(IMPL_FULL,IMPL_SHORT,{flags:['the first source read rests on shell parsing at #4 (shell read x.py)','2 report write(s) before the last file edit']})])};
  const p=teleProgram(()=>teleAnswer(sessions)),f=p.f;
  await f.api.list();await f.api.ocList();await f.flush();await f.flush();await f.flush();
   assert.deepEqual(f.teleRequests,['/api/telemetry?program=TELEM1'],'one read for the whole program');
  const meta=label=>find(p.row(label),'session-meta').at(-1);
   assert.deepEqual(meta('U01').children.map(n=>n.className),['backend-chip','meta-status','session-models',undefined,'session-times'],'the times line stays as it was: status, models, times');
  const tele=find(p.row('U01'),'session-tele')[0],open=meta('U01').parentNode;
   assert.equal(tele.parentNode,open,'inside the row\'s anchored element');assert.equal(open.children.indexOf(tele),open.children.indexOf(meta('U01'))+1,'directly under the times line');
   assert.deepEqual(runsOf(tele),['▶️ '+IMPL_SHORT],'a session run once: one line, every bracket, opened by the first run\'s mark');
   assert.deepEqual(find(tele,'tele-unit').map(n=>n.textContent),['50.3 (0) → ','57.2 (0) → ','427 (96) → ','481 KT (14 min) ⚑2'],'it wraps between moments, never inside one');
   assert.deepEqual(find(tele,'tele-run')[0].children.map(n=>n.className),['tele-mark','tele-body'],'the mark in its own cell, before the moments');
   assert.deepEqual(['tele-mark','tele-cell','tele-arrow','tele-flag'].map(c=>find(tele,c).map(n=>n.textContent)),
     [['▶️ '],['50.3 (0)','57.2 (0)','427 (96)','481 KT (14 min)'],[' → ',' → ',' → '],[' ⚑2']],'one cell per column: the mark, each moment, each arrow, the ⚑');
   assert.equal(tele.attributes['--tele-cols'],'9','the grid\'s columns: mark, four moments, three arrows, ⚑ (then the last, "so far")');
   assert.equal(find(tele,'tele-tail').length,0,'not live: no "so far"');
   assert.equal(tele.dataset.fit,undefined);assert.equal(find(tele,'tele-full').length+find(tele,'tele-short').length+find(tele,'tele-cut').length,0,'no width variants');
   assert.match(tele.title,/^Telemetry · oc\.sh telemetry --sid cc-00000002.* · context in KT \(kilo-tokens\)\nU01 · implementer · start-up = context at the first source read/);
   assert.match(tele.title,/\nfresh · start-up 50\.3 KT.*\n  ⚑ the first source read rests on shell parsing at #4 \(shell read x\.py\)\n  ⚑ 2 report write\(s\) before the last file edit$/);
  const conductor=find(find(f.el('sessions-list'),'session-item').find(n=>find(n,'session-tele').length && !n.textContent.includes('U01')),'session-tele')[0];
   assert.deepEqual(runsOf(conductor),['▶️ context 35.6 → 522 KT · 126 min']);
   assert.deepEqual(find(conductor,'tele-cell').map(n=>n.textContent),['context 35.6','522 KT · 126 min']);assert.equal(conductor.attributes['--tele-cols'],'4');
   assert.match(conductor.title,/\nConductor: phases are not classified for this role yet; context at its first and last response and the wall clock only\n/);
  // Not in its answer: no telemetry, never a placeholder.
   assert.equal(find(find(f.el('sessions-list'),'oc-item')[0],'session-tele').length,0);
  // Redrawn with nothing moved: no read; a row's own stamps move: one more read (not sooner than 5 s after the last).
  f.api.inventory();await f.flush();assert.equal(f.teleRequests.length,1);
  p.setSeat({turnFacts:{...p.c.turnFacts,lastAt:at('2026-10-16T01:59:00Z')}});await f.api.list();await f.flush();
   assert.equal(f.teleRequests.length,1,'within 5 s of the last read');
  f.setNow(Date.parse('2026-10-16T02:00:06Z'));f.api.inventory();await f.flush();await f.flush();
   assert.equal(f.teleRequests.length,2,'its stamps moved: read again');
  f.setNow(Date.parse('2026-10-16T02:00:30Z'));f.api.inventory();await f.flush();assert.equal(f.teleRequests.length,2,'nothing moved, nothing active: no read');
});
test('Dan: one telemetry line per run, oldest first: a session resumed twice shows three, the first marked ▶️ and each resume ↪️ in the same cell, with its own moments (never "carried"), "so far" ending the live run\'s line alone; other roles the same, their context per run',async()=>{
  const fu=(n,full,short,over={})=>teleSeg(full,short,{key:'k#'+n,segment:n,kind:'follow_up',label:'+'+n+' follow-up',line:'+'+n+' follow-up · '+full,...over});
  const sessions={[cc(2)]:teleEntry(cc(2),[teleSeg('start-up 157 KT · edit@186 KT · report@264 KT · end 274 KT · 1/1/17/3 min · ⚑0','157 (1) → 186 (1) → 264 (17) → 274 KT (3 min) ⚑0'),
      fu(1,'carried 274 KT · src@281 KT · edit@281 KT · report@405 KT · end 425 KT · 5/0/59/5 min · ⚑1','281 (5) → 281 (0) → 405 (59) → 425 KT (5 min) ⚑1',{flags:['a flag on the second run']}),
      fu(2,'carried 425 KT · src@431 KT · edit@— · report@— · end 443 KT · 6/2/—/— min · ⚑0','431 (6) → — → — → 443 KT (2 min) ⚑0',{live:true})]),
    [cc(1)]:teleEntry(cc(1),[teleSeg('context 35.6 → 212 KT · 48 min'),fu(1,'context 212 → 240 KT · 9 min','context 212 → 240 KT · 9 min')],{role:'CONDUCTOR',roleSource:'listed',phases:false,unit:null})};
  const p=teleProgram(()=>teleAnswer(sessions)),f=p.f;
  await f.api.list();await f.flush();await f.flush();await f.flush();
  const tele=find(p.row('U01'),'session-tele')[0];
   assert.deepEqual(runsOf(tele),['▶️ 157 (1) → 186 (1) → 264 (17) → 274 KT (3 min) ⚑0','↪️ 281 (5) → 281 (0) → 405 (59) → 425 KT (5 min) ⚑1','↪️ 431 (6) → — → — → 443 KT (2 min) ⚑0 so far']);
   assert.deepEqual(find(tele,'tele-run').map(n=>find(n,'tele-cell').length+'/'+find(n,'tele-arrow').length+'/'+find(n,'tele-flag').length),['4/3/1','4/3/1','4/3/1'],'every run the same cells, so its columns line up');
   assert.deepEqual(find(tele,'tele-tail').map(n=>[n.textContent,n.parentNode.parentNode.parentNode.dataset.run]),[[' so far','2']],'"so far" after the ⚑, on the live run alone');
   assert.deepEqual(find(tele,'tele-run').map(n=>n.dataset.run),['0','1','2'],'stable keys per run');
   assert.ok(!tele.textContent.includes('carried'),'no carried on the row');
   assert.match(tele.title,/a follow-up's carried = the context it already held when it arrived, src@ = at its first source read/);
   assert.match(tele.title,/\nfresh · start-up 157 KT.*\n\+1 follow-up · carried 274 KT.*\n  ⚑ a flag on the second run\n\+2 follow-up · carried 425 KT/,'the long forms stay in the detail');
  const conductor=find(find(f.el('sessions-list'),'session-item').find(n=>find(n,'session-tele').length && !n.textContent.includes('U01')),'session-tele')[0];
   assert.deepEqual(runsOf(conductor),['▶️ context 35.6 → 212 KT · 48 min','↪️ context 212 → 240 KT · 9 min']);
});
test('Dan: a live row ends its line "so far"; an OpenCode row keeps its times on its second line and its telemetry under it; computing asks again in 5 s, a failure in 30 s and shows nothing new',async()=>{
  let mode='computing';const ocId='ses_tele1xxxxx';
  const live={[ocId]:teleEntry(ocId,[teleSeg('start-up 61.2 KT · edit@— · report@— · end 120 KT · 1/2/—/— min · ⚑0','61.2 (1) → — → — → 120 KT (2 min) ⚑0',{live:true})])};
  const p=teleProgram(()=>mode==='computing'?{schemaVersion:1,backend:'telemetry',status:'computing',sessions:{},computing:['program']}:mode==='fail'?{schemaVersion:1,backend:'telemetry',status:'unavailable',error:'boom',sessions:{}}:teleAnswer(live)),f=p.f;
  await f.api.list();await f.api.ocList();await f.flush();await f.flush();
   assert.equal(find(f.el('sessions-list'),'session-tele').length,0,'computing: nothing yet');
  f.setNow(Date.parse('2026-10-16T02:00:04Z'));f.api.inventory();await f.flush();assert.equal(f.teleRequests.length,1);
  mode='done';f.setNow(Date.parse('2026-10-16T02:00:06Z'));f.api.inventory();await f.flush();await f.flush();await f.flush();
   assert.equal(f.teleRequests.length,2,'computing: asked again after 5 s');
  const oc=find(f.el('sessions-list'),'oc-item')[0],meta=find(oc,'session-meta')[0],tele=find(oc,'session-tele')[0];
   assert.deepEqual(meta.children.map(n=>n.className).slice(1),['meta-status',undefined,'session-times'],'the times line stays as it was');
   assert.equal(tele.parentNode,meta.parentNode);assert.equal(meta.parentNode.children.indexOf(tele),meta.parentNode.children.indexOf(meta)+1,'the telemetry directly under it');
   assert.deepEqual(runsOf(tele),['▶️ 61.2 (1) → — → — → 120 KT (2 min) ⚑0 so far']);
  mode='fail';p.setSeat({turnFacts:{...p.c.turnFacts,lastAt:at('2026-10-16T01:59:00Z')}});f.setNow(Date.parse('2026-10-16T02:00:12Z'));await f.api.list();await f.flush();await f.flush();
   assert.equal(f.teleRequests.length,3);assert.equal(find(find(f.el('sessions-list'),'oc-item')[0],'session-tele').length,1,'a failure keeps the last numbers');
  f.setNow(Date.parse('2026-10-16T02:00:30Z'));f.api.inventory();await f.flush();assert.equal(f.teleRequests.length,3,'no retry within 30 s of a failure');
  f.setNow(Date.parse('2026-10-16T02:00:43Z'));f.api.inventory();await f.flush();assert.equal(f.teleRequests.length,4,'retried after 30 s');
});
test('Dan, phone Programs: one telemetry line per run under the times, and a tap on it opens its whole account under the row, a second tap closes it',async()=>{
  const sessions={[cc(2)]:teleEntry(cc(2),[teleSeg(IMPL_FULL,IMPL_SHORT,{notes:['no source read before the first edit (source recon phase empty)']}),
    teleSeg('carried 481 KT · src@490 KT · edit@— · report@— · end 500 KT · 1/—/—/— min · ⚑0','490 (1) → — → — → 500 KT (0 min) ⚑0',{key:'k#1',segment:1,kind:'follow_up',label:'+1 follow-up',line:'+1 follow-up · carried 481 KT · src@490 KT · edit@— · report@— · end 500 KT · 1/—/—/— min · ⚑0'})])};
  const p=teleProgram(()=>teleAnswer(sessions),{sidebarOnly:true}),f=p.f;
  await f.api.list();await f.flush();await f.flush();await f.flush();
  const tele=()=>find(p.row('U01'),'session-tele')[0];
   assert.deepEqual(runsOf(tele()),['▶️ '+IMPL_SHORT,'↪️ 490 (1) → — → — → 500 KT (0 min) ⚑0'],'both runs, oldest first');
   assert.equal(tele().attributes.role,'button');assert.equal(tele().attributes['aria-expanded'],'false');
  tele().onclick({stopPropagation(){}});
  const detail=find(p.row('U01'),'tele-detail')[0];
   assert.ok(detail,'opened under the row');
   assert.match(detail.textContent,/\nfresh · start-up 50\.3 KT · edit@57\.2 KT · report@427 KT · end 481 KT · 0\/0\/96\/14 min · ⚑2\n  · no source read before the first edit \(source recon phase empty\)\n\+1 follow-up · carried 481 KT/);
   assert.equal(tele().attributes['aria-expanded'],'true');
  tele().onclick({stopPropagation(){}});assert.equal(find(p.row('U01'),'tele-detail').length,0,'a second tap closes it');
});

// ---------------------------------------------------------------- U1: one per-render activity model, hidden descendants
const u9Facts=(firstAt,lastAt,status='complete')=>({status,records:2,firstAt,lastAt,models:['claude-opus-5-5'],levels:['high'],modelsComplete:status==='complete',levelsComplete:status==='complete'});
test('U9.B1: every count edge uses the same listed count as program-meta and one to six logarithmic blocks',async()=>{
  for(const [count,bucket] of [[1,1],[2,1],[3,1],[4,2],[7,2],[8,3],[15,3],[16,4],[31,4],[32,5],[63,5],[64,6],[128,6]]) {
    const rows=Array.from({length:count},(_,i)=>seat(300+i,{program:'U9CNTT',kind:'seat',quick:true},{turnFacts:u9Facts(1000,1120)}));
    const f=fixture(url=>{if(!url.startsWith('/api/claude/sessions'))return;const offset=url.includes('cursor=')?64:0;return env({sessions:rows.slice(offset,offset+64),status:'complete',hasMore:count>64 && offset===0,nextCursor:count>64 && offset===0?'count-page-2':null});});f.setNow(2000000);await f.api.list();
    const head=find(f.el('sessions-list'),'program-head')[0],meter=find(head,'program-meter-count')[0];
    assert.equal(meter?.textContent,String(count),'visible count at '+count);
    assert.equal(meter.dataset.bucket,String(bucket),'log edge at '+count);assert.equal(find(meter,'program-count-block').length,bucket);
    assert.match(find(find(head,'program-meta')[0],'meta-status')[0].textContent,new RegExp(' · '+count+' session'+(count===1?'':'s')+' · Conductor not listed$'));
    assert.match(meter.title,new RegExp('^'+count+' listed sessions'));
  }
});
test('U9.B2: duration and short labels survive the continuous scale without counting idle age',()=>{
  const f=fixture();f.setNow(2000000000000);
  for(const [seconds,label] of [[0,'<1m'],[59,'<1m'],[60,'1m'],[120,'2m'],[599,'9m'],[600,'10m'],[2400,'40m'],[3599,'59m'],[3600,'1h'],[10799,'3h'],[10800,'3h'],[28799,'8h'],[28800,'8h'],[33480,'9.3h'],[86399,'24h'],[86400,'1d'],[138240,'1.6d'],[172800,'2d']]) {
    const p=f.api.lineageTree([cond(300,'U9DURA',1,{turnFacts:u9Facts(1000000,1000000+seconds)})]).programs[0];
    assert.ok(p.facts,'one aggregate exists at '+seconds);assert.equal(p.facts.duration,seconds);assert.ok(p.facts.durationFraction>0 && p.facts.durationFraction<=1,'known duration has a visible bounded fill at '+seconds);assert.equal(p.facts.durationLabel,label,'label at '+seconds);
  }
});
test('U9.B3: age comes from the first turn in Eastern calendar days, with midnight, week and DST edges',()=>{
  const f=fixture();f.setNow(Date.parse('2026-10-17T16:00:00Z'));
  for(const [days,label,bucket] of [[0,'today','today'],[1,'1d','yesterday'],[2,'2d','recent'],[6,'6d','recent'],[7,'1w','old'],[13,'1w','old'],[14,'2w','old'],[21,'3w','old']]) {
    const start=at('2026-10-17T12:00:00Z')-days*86400,p=f.api.lineageTree([cond(300,'U9AGEE',1,{turnFacts:u9Facts(start,at('2026-10-17T15:00:00Z'))})]).programs[0];
    assert.ok(p.facts,'age aggregate');assert.equal(p.facts.ageDays,days);assert.equal(p.facts.ageLabel,label);assert.equal(p.facts.ageBucket,bucket);
  }
  f.setNow(Date.parse('2026-11-02T04:30:00Z')); // Nov 1 23:30 EST: 24.5 hours after midnight, still today.
  let p=f.api.lineageTree([cond(300,'U9AGEE',1,{turnFacts:u9Facts(at('2026-11-01T04:00:00Z'),at('2026-11-02T04:00:00Z'))})]).programs[0];
  assert.equal(p.facts.ageDays,0);assert.equal(p.facts.ageLabel,'today');
  f.setNow(Date.parse('2026-03-09T04:05:00Z')); // Mar 9 00:05 EDT, ten minutes after yesterday's first turn.
  p=f.api.lineageTree([cond(300,'U9AGEE',1,{turnFacts:u9Facts(at('2026-03-09T03:55:00Z'),at('2026-03-09T04:00:00Z'))})]).programs[0];
  assert.equal(p.facts.ageDays,1);assert.equal(p.facts.ageLabel,'1d');
});
test('U9.A1: Claude hover starts with two turn lines and retains every old non-time detail from line three',async()=>{
  const row={...host,turnFacts:u9Facts(at('2026-10-15T23:02:00Z'),at('2026-10-16T01:48:00Z'))};
  const f=fixture(sessionsOf(row));f.setNow(Date.parse('2026-10-17T16:00:00Z'));await f.api.list();
  assert.deepEqual(sidebarItems(f)[0].children[0].title.split('\n'),['First: Thu Oct 15, 7:02 PM ET','Last: Thu Oct 15, 9:48 PM ET','Manager','UUID native-uuid','fixture','Manager · role from the host registry']);
});
test('U9.A2: native-subagent hover puts two turn lines before its full title, recorded-model text and native warning',async()=>{
  for(const facts of [u9Facts(at('2026-10-15T23:02:00Z'),at('2026-10-16T01:48:00Z')),undefined]) {
    const parent={...host,capabilities:{nativeInput:true,subagents:true}},kid={...host,source:'native',role:'subagent',parentUuid:host.uuid,sessionRef:'child-u9',agentId:'a',agentType:'manager-quick',description:'task',title:'Manager ▸ manager-quick · task',turnFacts:facts,scopes:[{label:'agent:a',scopeRef:'child-scope'}]};
    const f=fixture(url=>url.startsWith('/api/claude/subagents')?env({sessions:[kid],hasMore:false,status:'complete'}):undefined);
    f.setNow(Date.parse('2026-10-17T16:00:00Z'));await f.api.browse(parent);f.api.state.childNextPoll=0;await f.api.poll();
    const warning=' · native subagent · view-only; transcript growth is not task acceptance';
    assert.deepEqual(f.el('subagents-list').children[0].title.split('\n'),facts?['First: Thu Oct 15, 7:02 PM ET','Last: Thu Oct 15, 9:48 PM ET',kid.title,'Recorded by its own turns: claude-opus-5-5 · thinking high'+warning]:['first turn: unknown','last turn: unknown',kid.title+warning]);
  }
});
test('U9.A3: lazy OpenCode hover has two leading lines before, after and across an inventory rebuild; all old details remain',async()=>{
  const c=cond(300,'U9HOOC',1),oc=ocRow('u9hover',{program:'U9HOOC',kind:'seat',conductorN:1,agent:'quick-worker',updatedAt:2000,idleAt:2100});
  const f=fixture(sessionsOf(c),{opencode:url=>url.includes('/turns?')?{schemaVersion:1,backend:'opencode',status:'complete',id:oc.id,firstAt:at('2026-10-15T23:02:00Z'),lastAt:at('2026-10-16T01:48:00Z')}:ocEnv([oc])});
  f.setNow(Date.parse('2026-10-17T16:00:00Z'));f.api.state.lineageOpen.set('p:U9HOOC',true);await f.api.list();await f.api.ocList();
  const button=()=>find(f.el('sessions-list'),'oc-item')[0].children[0],tail=[oc.title,oc.id,'OpenCode · quick-worker · '+oc.model,'Click to read its whole conversation here (view only); 💬 opens a quick glance under this row.'];
  assert.deepEqual(button().title.split('\n'),['first turn: unknown (turn times still being read)','last turn: unknown (turn times still being read)',...tail]);
   assert.equal(f.ocRequests.filter(u=>u.includes('/turns')).length,0);await f.flush();await f.flush();button().onfocus();await f.flush();await f.flush();
  const expected=['First: Thu Oct 15, 7:02 PM ET','Last: Thu Oct 15, 9:48 PM ET',...tail];assert.deepEqual(button().title.split('\n'),expected);
  f.api.inventory();assert.deepEqual(button().title.split('\n'),expected);assert.equal(f.ocRequests.filter(u=>u.includes('/turns')).length,1);
});
test('U9.A4: program root adds two turn lines and keeps its entire old hover unchanged below, open or closed',async()=>{
  const c=cond(300,'U9HOVR',1,{turnFacts:u9Facts(at('2026-10-15T23:02:00Z'),at('2026-10-16T01:48:00Z'))}),f=fixture(sessionsOf(c));
  f.setNow(Date.parse('2026-10-17T16:00:00Z'));await f.api.list();const head=()=>find(f.el('sessions-list'),'program-head')[0];
  const times=['First: Thu Oct 15, 7:02 PM ET','Last: Thu Oct 15, 9:48 PM ET'],old=['Pane round 7: programs','[ap:U9HOVR] · quick program · ended · launched from Manager-Session-184'];
  assert.deepEqual(head().title.split('\n'),[...times,...old,'Click to open it']);head().onclick({stopPropagation(){}});
  assert.deepEqual(head().title.split('\n'),[...times,...old,'Click to close it']);
});
test('U9.aggregate: the first complete inventories combine both backends once, min start / max end, and disclose stamp estimates without extra reads',async()=>{
  const first=at('2026-10-15T23:02:00Z'),last=at('2026-10-16T01:48:00Z');
  const c=cond(300,'U9MIXD',1,{createdAt:first-100,turnFacts:u9Facts(first,last-100)}),oc=ocRow('u9mixed',{program:'U9MIXD',createdAt:first+10,updatedAt:last-20,idleAt:last});
  const f=fixture(sessionsOf(c),{opencode:()=>ocEnv([oc])});f.setNow(Date.parse('2026-10-17T16:00:00Z'));await f.api.list();await f.api.ocList();
  let p=f.api.state.tree.programs[0];assert.ok(p.facts,'aggregate on the first complete list');assert.equal(p.count,2);assert.equal(p.facts.firstAt,first);assert.equal(p.facts.lastAt,last);assert.equal(p.facts.duration,last-first);assert.equal(p.facts.ageLabel,'2d');
  const head=find(f.el('sessions-list'),'program-head')[0],tooltip=find(head,'program-meter-duration')[0].title;
  assert.deepEqual(head.title.split('\n').slice(0,2),['First: Thu Oct 15, 7:02 PM ET','Last: Thu Oct 15, 9:48 PM ET']);
  assert.match(tooltip,/Claude Code: own first\/latest turn ledger/);assert.match(tooltip,/OpenCode: session creation to max\(updated, idle\) stamps — estimates/);assert.match(tooltip,/never the seal or the current time/);
  const reads=[f.requests.length,f.ocRequests.length],oldFacts=p.facts;f.api.state.oc.rows[0]={...oc,createdAt:first-200,updatedAt:last+3600};f.api.inventory();p=f.api.state.tree.programs[0];
  assert.notEqual(p.facts,oldFacts,'not a carried aggregate');assert.equal(p.facts.firstAt,first-200);assert.equal(p.facts.lastAt,last+3600);assert.equal(p.facts.duration,last+3600-(first-200));
  assert.deepEqual([f.requests.length,f.ocRequests.length],reads);assert.ok(f.ocRequests.every(u=>u.startsWith('/api/opencode/programs?view=')));assert.ok(f.requests.every(([u])=>u.startsWith('/api/claude/sessions')));
  f.api.state.oc.stale=true;f.api.inventory();assert.match(find(f.el('sessions-list'),'program-meter-duration')[0].title,/OpenCode unavailable: its stamps are last known/);
});
test('U9.unknown: a scanning Claude member never creates a falsely short duration, even beside a complete member; later inventory resolves it',async()=>{
  const first=at('2026-10-15T23:02:00Z'),c=cond(300,'U9SCAN',1,{createdAt:first,turnFacts:u9Facts(first,first+120)});
  const scanning=seat(301,{program:'U9SCAN',kind:'seat',conductorN:1},{createdAt:first+10,turnFacts:u9Facts(first+10,null,'scanning'),liveState:{last_activity_at:first+20}});
  const f=fixture(sessionsOf(c,scanning));f.setNow(Date.parse('2026-10-17T16:00:00Z'));await f.api.list();
  let p=f.api.state.tree.programs[0];assert.ok(p.facts);assert.equal(p.facts.firstAt,first);assert.equal(p.facts.lastAt,null);assert.equal(p.facts.duration,null);assert.equal(p.facts.durationFraction,0);
  const head=()=>find(f.el('sessions-list'),'program-head')[0];assert.equal(find(head(),'program-meter-duration')[0].textContent,'—');assert.match(head().title,/^First: .*\nlast turn: unknown \(1 transcript still being read\)\n/);assert.match(find(head(),'program-meter-duration')[0].title,/still being read/);
  scanning.turnFacts=u9Facts(first+10,first+172800);f.api.state.inventory=[c,scanning];const reads=f.requests.length;f.api.inventory();p=f.api.state.tree.programs[0];
  assert.equal(p.facts.duration,172800);assert.equal(find(head(),'program-meter-duration')[0].textContent,'2d');assert.equal(f.requests.length,reads);
});
test('U9.unknown edges: missing, invalid and inverted member stamps stay unknown; start-only fallback is explicitly estimated',()=>{
  const f=fixture();f.setNow(2000000000000);const c=cond(300,'U9UNKN',1,{createdAt:1000,turnFacts:u9Facts(1000,1120)});
  for(const over of [{createdAt:null,turnFacts:undefined},{createdAt:'bad',turnFacts:u9Facts(NaN,Infinity)},{createdAt:1200,turnFacts:u9Facts(1200,1100)},{createdAt:1200,turnFacts:u9Facts(1200,1210,'scanning')}]) {
    const p=f.api.lineageTree([c,seat(301,{program:'U9UNKN',kind:'seat'},over)]).programs[0];assert.ok(p.facts);assert.equal(p.facts.duration,null);assert.equal(p.facts.lastAt,null);assert.equal(p.facts.durationLabel,'—');
    if(over.createdAt===null || over.createdAt==='bad'){assert.equal(p.facts.firstAt,null);assert.equal(p.facts.ageLabel,'—');assert.equal(p.facts.ageBucket,'unknown');}
  }
  let p=f.api.lineageTree([cond(300,'U9UNKN',1,{createdAt:1000,turnFacts:u9Facts(null,1120)})]).programs[0];assert.equal(p.facts.duration,120);assert.match(p.facts.basis,/creation stamp used for 1 missing first turn \(estimate\)/);
  p=f.api.lineageTree([], [ocRow('unknown',{program:'U9UNKN',createdAt:1000})]).programs[0];assert.equal(p.facts.duration,null);assert.equal(p.facts.lastAt,null);
  p=f.api.lineageTree([cond(300,'U9UNKN',1,{createdAt:2000000100,turnFacts:u9Facts(2000000100,2000000200)})]).programs[0];assert.equal(p.facts.ageDays,null);assert.equal(p.facts.ageLabel,'—');
});
test('U9.placement: Running now and reopened home roots keep aligned cells after collapse-all; stubs, live dot space and folds stay independent',async()=>{
  const start=at('2026-10-17T12:00:00Z'),quick=cond(300,'U9QUCK',1,{createdAt:start,turnFacts:u9Facts(start,start+120),lifecycle:'live',state:'ready',liveState:{primary_turn_open:true}});
  const full=cond(301,'U9FULL',1,{lineage:{quickProgram:false},createdAt:start-3*86400,turnFacts:u9Facts(start-3*86400,start-86400)});
  const f=fixture(sessionsOf(quick,full));f.setNow(Date.parse('2026-10-17T16:00:00Z'));await f.api.list();f.el('sessions-collapse').onclick();
  const group=label=>f.el('sessions-list').children.find(n=>n.children[0]?.tag==='h3' && headingLabel(n.children[0])===label);
  assert.equal(find(group('Running now'),'program')[0].children[0].getAttribute('aria-expanded'),'false');
  for(const label of ['Full programs','Quick programs'])group(label).children[0].onclick({stopPropagation(){}});
  const running=()=>find(group('Running now'),'program-head')[0],home=()=>find(group('Full programs'),'program-head')[0];
  for(const head of [running(),home()]){assert.equal(head.getAttribute('aria-expanded'),'false');assert.ok(find(head,'program-meters')[0],'root has its three cells');assert.equal(find(head,'program-meters')[0].children.length,3);assert.equal(find(head,'program-meter-count')[0].textContent,'1');assert.equal(find(head,'program-fold').length,1);}
  assert.equal(find(running(),'program-meter-duration')[0].textContent,'2m');assert.equal(find(home(),'program-meter-duration')[0].textContent,'2d');
  assert.equal(find(running(),'program-meter-age')[0].textContent,'today');assert.equal(find(home(),'program-meter-age')[0].textContent,'3d');
  assert.equal(find(running(),'program-meter-tail').length,0,'live dot supplies its original 13px');assert.equal(find(home(),'program-meter-tail').length,1,'quiet root reserves the same trailing space');
  const stub=find(group('Quick programs'),'session-stub')[0];assert.ok(stub);assert.equal(stub.onclick,undefined);assert.equal(find(stub,'program-meters').length,0);assert.equal(stub.textContent,'↑ Pane round 7: programs · shown at top');
  find(running(),'program-fold')[0].onclick({stopPropagation(){}});assert.equal(running().getAttribute('aria-expanded'),'true');assert.equal(sidebarItems(f).length,1);assert.equal(find(running(),'program-meter-duration')[0].textContent,'2m');
  running().onclick({stopPropagation(){}});assert.equal(running().getAttribute('aria-expanded'),'false');assert.equal(find(running(),'rollup-badge')[0].textContent,'1 busy');
  f.api.setSidebarHover(true);quick.liveState={primary_turn_open:false};f.api.state.inventory=[quick,full];f.api.inventory();assert.ok(group('Running now'));assert.equal(find(running(),'program-meter-duration')[0].textContent,'2m');
  f.api.setSidebarHover(false);assert.equal(group('Running now'),undefined);assert.equal(find(group('Quick programs'),'program-head').length,1);assert.equal(find(group('Quick programs'),'session-stub').length,0);
});

test('U10.fade monotonic: first-turn age fades linearly to the meta-colour floor at the window and never beyond it',async()=>{
  const now=at('2026-10-17T16:00:00Z');let previous=-1;
  for(const [days,expected] of [[0,0],[1,1/14],[3,3/14],[7,.5],[13,13/14],[14,1],[28,1],[100,1]]) {
    const start=now-days*86400,row=cond(400,'U10AGE',1,{createdAt:start,turnFacts:u9Facts(start,start+60)});
    const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:[row],status:'complete',hasMore:false,listWindowDays:14}):undefined);
    f.setNow(now*1000);await f.api.list();const facts=f.api.state.tree.programs[0].facts;
    assert.equal(facts.nameFade,expected,'fade at '+days+' days');assert.ok(facts.nameFade>=previous);previous=facts.nameFade;
    const name=find(f.el('sessions-list'),'program-title')[0].children[0];
    assert.equal(name.classList.contains('program-name-age'),true);assert.equal(name.getAttribute('style'),'--program-name-fade:'+expected*100+'%');
    assert.equal(find(f.el('sessions-list'),'program-meter-age')[0].textContent,facts.ageLabel,'the existing age label stays');
  }
});
test('U10.fade setting: two host windows change the same first-complete inventory and an absent or invalid setting restores today colour',async()=>{
  const now=at('2026-10-17T16:00:00Z'),start=now-7*86400,row=cond(400,'U10SET',1,{createdAt:start,turnFacts:u9Facts(start,start+60)});let window=14;
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:[row],status:'complete',hasMore:false,...(window===undefined?{}:{listWindowDays:window})}):undefined);f.setNow(now*1000);
  for(const [setting,expected] of [[14,.5],[28,.25],[7,1],[undefined,null],[null,null],[0,null],[-1,null],['14',null],[true,null]]) {
    window=setting;await f.api.list();assert.equal(f.api.state.tree.programs[0].facts.nameFade,expected,'setting '+setting);
    const name=find(f.el('sessions-list'),'program-title')[0].children[0];
    assert.equal(name.classList.contains('program-name-age'),expected!==null);
    assert.equal(name.getAttribute('style'),expected===null?undefined:'--program-name-fade:'+expected*100+'%');
  }
  assert.ok(f.requests.every(([url])=>url.startsWith('/api/claude/sessions?')),'no per-program or separate browser setting read');
});
test('U10.fade first turn: a rare follow-up changes latest and duration but never brightens the program name',async()=>{
  const now=at('2026-10-17T16:00:00Z'),start=now-10*86400,row=cond(400,'U10OLD',1,{createdAt:start,turnFacts:u9Facts(start,start+300)});
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:[row],status:'complete',hasMore:false,listWindowDays:14}):undefined);f.setNow(now*1000);await f.api.list();
  const first=f.api.state.tree.programs[0].facts;assert.equal(first.nameFade,10/14);
  row.turnFacts=u9Facts(start,now);await f.api.list();const next=f.api.state.tree.programs[0].facts;
  assert.notEqual(first,next,'fresh aggregate, not a cached fade');assert.equal(next.firstAt,start);assert.equal(next.lastAt,now);assert.equal(next.nameFade,first.nameFade);assert.notEqual(next.duration,first.duration);
});
test('U10.fade unknown: any unknown or future first turn leaves exactly the inherited name colour, even with a window',async()=>{
  const now=at('2026-10-17T16:00:00Z');
  for(const [createdAt,facts] of [[null,undefined],['bad',u9Facts(null,now)],[now+86400,u9Facts(now+86400,now+86460)]]) {
    const rows=[cond(400,'U10UNK',1,{createdAt:now-86400,turnFacts:u9Facts(now-86400,now)}),seat(401,{program:'U10UNK',kind:'seat',conductorN:1},{createdAt,turnFacts:facts})];
    // The future-start case is isolated: a known older member would honestly supply the minimum.
    if(typeof createdAt==='number')rows.shift();
    const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:rows,status:'complete',hasMore:false,listWindowDays:14}):undefined);f.setNow(now*1000);await f.api.list();
    assert.equal(f.api.state.tree.programs[0].facts.nameFade,null);const name=find(f.el('sessions-list'),'program-title')[0].children[0];
    assert.equal(name.classList.contains('program-name-age'),false);assert.equal(name.getAttribute('style'),undefined);assert.equal(find(f.el('sessions-list'),'program-meter-age')[0].textContent,'—');
  }
});
test('U10.fade scope: only Running-now and home root names fade on both backends, never member rows or stubs',async()=>{
  const now=at('2026-10-17T16:00:00Z'),start=now-7*86400,quick=cond(400,'U10RUN',1,{createdAt:start,turnFacts:u9Facts(start,start+300),lifecycle:'live',state:'ready',liveState:{primary_turn_open:true}});
  const oc=ocRow('u10home',{program:'U10HOM',kind:'conductor',conductorN:1,quickProgram:false,createdAt:start,updatedAt:start+14.1*3600,idleAt:start+14.1*3600});
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:[quick,host],status:'complete',hasMore:false,listWindowDays:14}):undefined,{opencode:()=>({...ocEnv([oc]),windowDays:45})});f.setNow(now*1000);await f.api.list();await f.api.ocList();
  assert.equal(f.api.state.tree.programs.find(p=>p.core==='U10HOM').facts.nameFade,.5,'OpenCode roots share the host setting, not their separate 45-day filter');
  for(const title of find(f.el('sessions-list'),'program-title'))assert.equal(title.children[0].getAttribute('style'),'--program-name-fade:50%');
  assert.equal(find(f.el('sessions-list'),'program-title').length,2);
  // The host Manager row is this page's remembered fallback pin, so its home stub joins the running program's.
  assert.equal(find(f.el('sessions-list'),'session-stub').length,2);
  assert.ok(find(f.el('sessions-list'),'session-stub').some(n=>/↑ Manager · pinned at top/.test(n.textContent)));
  for(const root of [...sidebarItems(f),...find(f.el('sessions-list'),'oc-item'),...find(f.el('sessions-list'),'session-stub')]) {
    const walk=n=>{assert.equal(n.classList.contains('program-name-age'),false);assert.equal(n.getAttribute('style'),undefined);for(const child of n.children)walk(child);};walk(root);
  }
  const reads=[f.requests.length,f.ocRequests.length];f.api.inventory();assert.deepEqual([f.requests.length,f.ocRequests.length],reads,'ordinary rebuild never reads settings');
});
test('U10.fade inventory: first-page setting survives paging; refresh swaps rows and span together and a failed refresh retains both',async()=>{
  const now=at('2026-10-17T16:00:00Z'),start=now-7*86400,row=cond(400,'U10PAG',1,{createdAt:start,turnFacts:u9Facts(start,start+60)});let window=14,fail=false;
  const f=fixture(url=>{if(!url.startsWith('/api/claude/sessions'))return;if(url.includes('cursor=')){if(fail)throw new Error('offline page');return env({sessions:[host],status:'complete',hasMore:false});}return env({sessions:[row],status:'discovering',hasMore:true,nextCursor:'next',listWindowDays:window});});f.setNow(now*1000);await f.api.list();
  assert.equal(f.api.state.tree.programs[0].facts.nameFade,.5,'cold complete inventory uses first-page setting');
  window=28;fail=true;await f.api.list();assert.equal(f.api.state.tree.programs[0].facts.nameFade,.5,'failed refresh cannot mix new span into old inventory');
  fail=false;await f.api.list();assert.equal(f.api.state.tree.programs[0].facts.nameFade,.25);
});
test('U10.bar: 5 minutes is visible, 14.1 hours and 2.5 days have clear gaps, four days caps, labels and tooltip stay honest',async()=>{
  const lengths=[];
  for(const [seconds,label,px] of [[0,'<1m',2],[300,'5m',2],[50760,'14.1h',6.898369372921191],[216000,'2.5d',14.230249470757707],[345600,'4d',18],[864000,'10d',18]]) {
    const row=cond(400,'U10BAR',1,{createdAt:1000000,turnFacts:u9Facts(1000000,1000000+seconds)}),f=fixture(sessionsOf(row));f.setNow(2000000000000);await f.api.list();
    const facts=f.api.state.tree.programs[0].facts;assert.equal(typeof facts.durationFraction,'number','continuous fraction exists');assert.ok(Math.abs(facts.durationFraction*18-px)<.001,'length at '+seconds);lengths.push(facts.durationFraction*18);
    const meter=find(f.el('sessions-list'),'program-meter-duration')[0],bar=find(meter,'program-duration-bar')[0];
    assert.equal(bar.getAttribute('style'),'--program-duration-fraction:'+facts.durationFraction);assert.equal(bar.dataset.bucket,undefined);assert.equal(meter.textContent,label);
    assert.match(meter.title,/Bar: square-root scale, capped at 4 days; known durations fill at least 2 of 18 px\./);assert.doesNotMatch(meter.title,/<10m \/ <1h/);
  }
  assert.ok(lengths[1]>0 && lengths[1]<3);assert.ok(lengths[2]-lengths[1]>4);assert.ok(lengths[3]-lengths[2]>7,'long runs differ by over seven pixels on the unchanged track');
  const row=cond(400,'U10BAR',1,{createdAt:1000000,turnFacts:u9Facts(1000000,null,'scanning')}),f=fixture(sessionsOf(row));await f.api.list();
  assert.equal(f.api.state.tree.programs[0].facts.durationFraction,0);assert.equal(find(f.el('sessions-list'),'program-duration-bar')[0].getAttribute('style'),'--program-duration-fraction:0');assert.equal(find(f.el('sessions-list'),'program-duration-label')[0].textContent,'—');
});

test('T1.1: a busy helper rolls up through its lead and Conductor only when their tree is hidden',async()=>{
  const live={lifecycle:'live',state:'ready',liveState:{primary_turn_open:false,background_tasks:{}}};
  const c=cond(101,'U1TEST',1,live),lead=seat(102,{program:'U1TEST',kind:'seat',seatRole:'lead',conductorN:1,title:'lead'},live);
  const helper=seat(103,{program:'U1TEST',kind:'helper',parentSeat:lead.executionId,title:'busy helper'},
    {...live,displayRole:'helper',liveState:{primary_turn_open:true}});
  const f=fixture(sessionsOf(c,lead,helper));await f.api.list();
  const program=()=>find(f.el('sessions-list'),'program')[0],head=()=>program().children[0];
  assert.equal(find(head(),'rollup-badge').length,0,'expanded program: nothing hidden there');
  assert.equal(sidebarItems(f).find(n=>n.textContent.includes('busy helper')).classList.contains('active'),true);
  head().onclick({stopPropagation(){}});
  assert.equal(find(head(),'rollup-badge')[0]?.textContent,'1 busy');
  assert.equal(find(head(),'rollup-badge')[0]?.dataset.state,'busy');
  assert.equal(sidebarItems(f).length,0);
  head().onclick({stopPropagation(){}});
  const conductorNode=program().children[1].children[0];
  conductorNode.children[0].onclick({stopPropagation(){}});
  assert.equal(find(head(),'rollup-badge').length,0);
  const row=sidebarItems(f)[0],badge=find(row,'rollup-badge')[0];
  assert.equal(badge?.textContent,'1 busy');assert.equal(badge.parentNode.className,'session-name');
  assert.equal(row.classList.contains('active'),false,'a descendant never spins its parent');
  assert.equal(sidebarItems(f).length,1);
});
test('T1.2: a collapsed node counts busy and waiting descendants separately, with waiting winning and self excluded',async()=>{
  const live={lifecycle:'live',state:'ready'},c=cond(111,'U1BOTH',1,{...live,liveState:{primary_turn_open:true}});
  const busy=seat(112,{program:'U1BOTH',kind:'seat',conductorN:1,title:'busy'}, {...live,liveState:{primary_turn_open:true}});
  const waiting=seat(113,{program:'U1BOTH',kind:'seat',conductorN:1,title:'waiting'},
    {...live,liveState:{primary_turn_open:false,background_tasks:{bash:{task_type:'local_bash'}}}});
  const f=fixture(sessionsOf(c,busy,waiting));await f.api.list();
  f.api.state.lineageOpen.set('s:'+c.executionId,false);f.api.inventory();
  const row=sidebarItems(f)[0],badge=find(row,'rollup-badge')[0];
  assert.equal(badge?.textContent,'1 busy · 1 waiting');assert.equal(badge.dataset.state,'waiting');
  assert.equal(badge.title,'Under this row: 1 busy · 1 waiting. Busy = the yellow spinner (a turn is open). Waiting = its turn is closed while a background task runs (not blocked).');
  assert.equal(row.classList.contains('active'),true,'its own spinner is independent of the hidden counts');
});
test('T1.3: OpenCode waiting uses the same rollup as its flag; stale OpenCode rows count quiet',async()=>{
  const c=cond(121,'U1OPEN',1,{lifecycle:'live',state:'ready'});
  const lead=ocRow('u1lead',{program:'U1OPEN',kind:'seat',seatRole:'lead',conductorN:1,title:'OC lead'});
  const child=ocRow('u1child',{program:'U1OPEN',kind:'subagent',parentId:lead.id,busy:true,title:'OC child'});
  const f=fixture(sessionsOf(c),{opencode:()=>ocEnv([lead,child])});await f.api.list();await f.api.ocList();
  const flag=find(f.el('sessions-list'),'waiting-flag')[0];
  assert.equal(flag.title,'Waiting (best effort): idle, while a session started under it is busy. OpenCode reports only busy or idle (/api/session/active): a parent can read idle while its background subagents run, so this "waiting" is a best guess from a busy session started under it.');
  assert.equal(find(f.el('sessions-list'),'oc-item')[1].classList.contains('active'),true);
  f.api.state.lineageOpen.set('s:'+c.executionId,false);f.api.inventory();
  const badge=find(sidebarItems(f)[0],'rollup-badge')[0];
  assert.equal(badge?.textContent,'1 busy · 1 waiting');assert.equal(badge.dataset.state,'waiting');
  assert.match(badge.title,/ OpenCode waiting is a best guess\.$/);
  f.api.state.oc.stale=true;f.api.inventory();
  assert.equal(find(f.el('sessions-list'),'rollup-badge').length,0);
  f.api.state.lineageOpen.set('s:'+c.executionId,true);f.api.inventory();
  assert.equal(find(f.el('sessions-list'),'waiting-flag').length,0);
  assert.ok(find(f.el('sessions-list'),'oc-item').every(n=>!n.classList.contains('active')));
  assert.equal(headingLabel(f.el('sessions-list').children.find(g=>g.dataset.group==='programs:quick').children[0]),'Quick programs');
});
test('T1.4: a non-watched Manager counts and spins its own native subagents from its listed host record',async()=>{
  const manager={...host,sessionRef:'u1-manager',uuid:U(131),executionId:cc(131),sessionLabel:'Manager-Session-131',
    lifecycle:'live',state:'ready',capabilities:{subagents:true},liveState:{primary_turn_open:false,background_tasks:{a1:{task_type:'local_agent'}}}};
  const other={...host,sessionRef:'u1-other',uuid:U(132),executionId:cc(132),sessionLabel:'Manager-Session-132',lifecycle:'live',state:'ready'};
  const child={...host,source:'native',role:'subagent',sessionRef:'u1-child',parentUuid:manager.uuid,agentId:'a1',
    title:'Manager-Session-131 ▸ manager-quick-high · native job',scopes:[{scopeRef:'u1-child-scope',label:'agent:a1'}]};
  const f=fixture(url=>url.startsWith('/api/claude/subagents')?env({sessions:[child],status:'complete',hasMore:false}):sessionsOf(manager,other)(url));
  await f.api.list();await f.api.browse(other);
  const row=()=>sidebarItems(f).find(n=>n.textContent.includes('Manager-Session-131'));
  assert.equal(find(row(),'rollup-badge')[0]?.textContent,'1 busy');
  assert.equal(row().classList.contains('active'),false);
  find(row(),'session-toggle')[0].onclick({stopPropagation(){}});await f.flush();await f.flush();
  const button=find(row(),'child-list')[0].children[0];
  assert.equal(button.classList.contains('active'),true);assert.match(button.title,/ · running now$/);
  assert.equal(find(row(),'rollup-badge').length,0,'expanded native list is no longer hidden');
  assert.equal(f.api.state.session.sessionRef,other.sessionRef);assert.equal(f.api.state.childParent.sessionRef,other.sessionRef);
});
test('T1.5: a newer listed host record wins over an older selected poll for both waiting and background count',async()=>{
  const manager={...host,sessionRef:'u1-newest',uuid:U(141),executionId:cc(141),sessionLabel:'Manager-Session-141',
    lifecycle:'live',state:'ready',liveState:{primary_turn_open:false,background_tasks:{bash:{task_type:'local_bash'}}}};
  const f=fixture(url=>url.startsWith('/api/claude/events')?page([event(1)],{sessionRef:manager.sessionRef,
    liveState:{primary_turn_open:false,background_tasks:{}}}):sessionsOf(manager)(url));
  f.setNow(1000);await f.api.list();await f.api.browse(manager);
  f.setNow(2000);await f.api.list();
  assert.ok(f.api.state.liveStateAt<f.api.state.inventoryAt);
  const row=sidebarItems(f)[0];
  assert.equal(find(row,'waiting-flag').length,1);assert.match(row.textContent,/◌ 1 in background/);
  assert.equal(find(row,'waiting-flag')[0].title,'Waiting (waiting, not blocked): its turn is closed; it is woken when one of its 1 background task completes.\nlocal_bash: description not recorded (command not recorded)');
  assert.equal(row.classList.contains('active'),false);
});
test('U1 map entries have the stable keys and shape, count native agents once, and rebuild without touching conversation events',async()=>{
  const live={lifecycle:'live',state:'ready'},c=cond(151,'U1SHAP',1,live);
  const lead=seat(152,{program:'U1SHAP',kind:'seat',conductorN:1,title:'native lead'},
    {...live,capabilities:{subagents:true},liveState:{primary_turn_open:false,background_tasks:{a1:{task_type:'local_agent'},a2:{},bash:{task_type:'local_bash'}}}});
  const helper=seat(153,{program:'U1SHAP',kind:'helper',parentSeat:lead.executionId,title:'tree helper'}, {...live,liveState:{primary_turn_open:true}});
  const manager={...host,sessionRef:'u1-root',uuid:U(154),executionId:cc(154),sessionLabel:'Manager-Session-154',...live,
    liveState:{primary_turn_open:false,background_tasks:{r1:{task_type:'local_agent'}}}};
  const history={...host,sessionRef:'u1-history',uuid:U(155),source:'native',role:'history',primary_turn_open:true};
  const child=id=>({...host,sessionRef:'u1-agent-'+id,source:'native',role:'subagent',parentUuid:lead.uuid,agentId:id,title:'agent '+id});
  const f=fixture(sessionsOf(c,lead,helper,manager,history,child('a1'),child('quiet')));await f.api.list();
  const plain=value=>JSON.parse(JSON.stringify(value)),entry=(own,busy=0,waiting=0,agents=0)=>({own,kids:{busy,waiting},agents,any:own!=='quiet'||busy+waiting+agents>0});
  const map=f.api.state.activity,key='a:'+f.api.identityOf(lead)+':';
  assert.deepEqual(plain(map.get('p:U1SHAP')),entry('quiet',3,1));
  assert.deepEqual(plain(map.get('s:'+c.executionId)),entry('quiet',3,1));
  assert.deepEqual(plain(map.get('s:'+lead.executionId)),entry('waiting',1,0,2));
  assert.deepEqual(plain(map.get('s:'+helper.executionId)),entry('busy'));
  assert.deepEqual(plain(map.get('r:'+f.api.identityOf(manager))),entry('waiting',0,0,1));
  assert.deepEqual(plain(map.get('r:'+f.api.identityOf(history))),entry('quiet'),'no host record: quiet despite a native row field');
  assert.deepEqual(plain(map.get(key+'a1')),entry('busy'));assert.deepEqual(plain(map.get(key+'a2')),entry('busy'));
  assert.deepEqual(plain(map.get(key+'quiet')),entry('quiet'));assert.equal(map.has(key+'bash'),false);
  const leadRow=()=>sidebarItems(f).find(n=>find(n,'name-text')[0]?.textContent==='native lead');
  assert.equal(find(leadRow(),'rollup-badge')[0].textContent,'2 busy','expanded tree still badges a hidden native list');
  f.api.state.lineageOpen.set('s:'+lead.executionId,false);f.api.inventory();
  assert.equal(find(leadRow(),'rollup-badge')[0].textContent,'3 busy','combines disjoint tree and native hidden parts, never self');
  for(const row of f.api.state.inventory)row.liveState={primary_turn_open:false,background_tasks:{}};
  const conversation=f.api.state.subagentActivity,marker={key:'conversation-only',agent:'a1',ordinal:1};conversation.set(marker.key,marker);
  f.api.inventory();
  assert.notEqual(f.api.state.activity,map);assert.equal(map.get('p:U1SHAP').any,true,'the prior render is not mutated');
  assert.deepEqual(plain(f.api.state.activity.get('p:U1SHAP')),entry('quiet'));
  assert.equal(find(f.el('sessions-list'),'rollup-badge').length,0);
  assert.equal(f.api.state.subagentActivity,conversation);assert.equal(conversation.get(marker.key),marker);
});
test('U1 watched-parent evidence is timestamped, separate from the selected child, and never leaks to another Manager',async()=>{
  const parent={...host,sessionRef:'u1-parent',uuid:U(161),executionId:cc(161),sessionLabel:'Manager-Session-161',
    lifecycle:'live',state:'ready',capabilities:{subagents:true},liveState:{primary_turn_open:false,background_tasks:{}}};
  const child={...host,source:'native',role:'subagent',sessionRef:'u1-watched-child',parentUuid:parent.uuid,agentId:'a1',title:'native child'};
  const other={...parent,sessionRef:'u1-next',uuid:U(162),executionId:cc(162),sessionLabel:'Manager-Session-162'};
  const f=fixture(sessionsOf(parent,other));f.setNow(4000);await f.api.list();f.api.state.hostActivity.clear();
  Object.assign(f.api.state,{session:child,childParent:parent,children:[child],inventoryAt:2000,
    parentLiveAt:3000,parentLive:{primary_turn_open:false,background_tasks:{a1:{task_type:'local_agent'}}},
    liveStateAt:4000,liveState:{primary_turn_open:true,background_tasks:{different:{task_type:'local_agent'}}}});
  f.api.inventory();
  const parentRow=()=>sidebarItems(f).find(n=>find(n,'name-text')[0]?.textContent==='Manager-Session-161');
  assert.equal(find(parentRow(),'waiting-flag').length,1);assert.match(parentRow().textContent,/◌ 1 in background/);
  assert.equal(f.el('subagents-list').children[0].classList.contains('active'),true);
  assert.equal(f.api.state.activity.get('a:'+f.api.identityOf(parent)+':different'),undefined,'child poll cannot become parent evidence');
  f.api.state.parentLiveAt=1000;f.api.inventory();
  assert.equal(find(parentRow(),'waiting-flag').length,0);assert.equal(f.el('subagents-list').children[0].classList.contains('active'),false);
  f.api.state.parentLiveAt=5000;f.setNow(5000);await f.api.browse(other);
  assert.equal(f.api.state.parentLive,null);assert.equal(f.api.state.parentLiveAt,0);
  assert.equal(sidebarItems(f).find(n=>find(n,'name-text')[0]?.textContent==='Manager-Session-162').classList.contains('active'),false);
});
test('U1 an equal-size replacement native-child page rebuilds entries before rendering its new children',async()=>{
  const parent={...host,sessionRef:'u1-page-parent',uuid:U(171),executionId:cc(171),capabilities:{subagents:true}};
  const child=id=>({...host,source:'native',role:'subagent',sessionRef:'u1-page-'+id,parentUuid:parent.uuid,agentId:id,
    title:'native '+id,scopes:[{scopeRef:'scope-'+id,label:'agent:'+id}]});
  let rows=[child('first')];
  const f=fixture(url=>url.startsWith('/api/claude/subagents')?env({sessions:rows,status:'complete',hasMore:false}):undefined);
  await f.api.browse(parent);await f.api.children();
  const key='a:'+f.api.identityOf(parent)+':';
  assert.equal(f.api.state.activity.get(key+'first')?.own,'quiet');
  const before=f.api.state.activity;rows=[child('second')];await f.api.children();
  assert.equal(f.api.state.activity.get(key+'second')?.own,'quiet','every rendered child has its own current entry, even when quiet');
  assert.notEqual(f.api.state.activity,before);assert.equal(f.api.state.activity.has(key+'first'),false);
  assert.match(f.el('subagents-list').textContent,/native second/);
});

// ---------------------------------------------------------------- U2: one placement, pinned Manager, lifted roots and home stubs
const sidebarGroup=(f,label)=>f.el('sessions-list').children.find(g=>headingLabel(g.children[0])===label);
test('finished Claude rows settle on the host cadence while a ten-minute native inventory refresh is still pending, desktop and phone',async()=>{
  for(const sidebarOnly of [false,true]) {
    const storeRef='host-store',newStoreRef='new-host-store',busy={primary_turn_open:true,pending_message_uuids:[],background_tasks:{}},idle={...busy,primary_turn_open:false};
    const quick=seat(900,{}, {storeRef,lineage:undefined,title:'finished quick',state:'ready',lifecycle:'pending',liveState:busy}),
      conductor=cond(901,'STATUS',1,{storeRef,state:'ready',lifecycle:'pending',liveState:busy}),
      manager={...host,storeRef,sessionRef:'settled-manager',title:'finished Manager',state:'ready',lifecycle:'live',liveState:busy},
      running=seat(902,{}, {storeRef,lineage:undefined,title:'still running',state:'ready',lifecycle:'pending',liveState:busy}),
      pending=seat(906,{}, {storeRef,lineage:undefined,title:'Send unanswered',state:'ready',lifecycle:'pending',liveState:{...idle,pending_message_uuids:['input']}}),
      waiting=seat(903,{}, {storeRef,lineage:undefined,title:'still waiting',state:'ready',lifecycle:'pending',liveState:{...idle,background_tasks:{bell:{task_type:'local_bash'}}}});
    const rows=[quick,conductor,manager,running,pending,waiting];let slow=false,release,hostReads=0;
    const f=fixture(url=>{
      if(!url.startsWith('/api/claude/sessions'))return;
      const q=new URLSearchParams(url.split('?')[1]);
      if(q.get('storeRef')===newStoreRef){hostReads++;
        // Exercise the complete host-only pagination; no native history is read here.
        return q.get('cursor')==='host-page-2'?env({sessions:[running,pending,waiting],hasMore:false,status:'complete'}):
          env({sessions:[{...quick,liveState:idle},{...conductor,liveState:idle},{...manager,liveState:idle}],hasMore:true,nextCursor:'host-page-2',status:'discovering'});
      }
      if(q.get('cursor')==='native-tail')return new Promise(resolve=>release=()=>resolve(env({sessions:[],hasMore:false,status:'complete'})));
      return env({sessions:slow?rows.map(row=>({...row,storeRef:newStoreRef})):rows,hasMore:slow,nextCursor:slow?'native-tail':null,status:slow?'discovering':'complete'});
    },{sidebarOnly});
    f.setNow(100000);await f.api.list();assert.match(sidebarGroup(f,'Running now').textContent,/finished quick/);
    slow=true;f.setNow(101000);const refresh=f.api.list();await f.flush();assert.equal(f.api.state.inventoryBusy,true);
    f.setNow(111000);await f.api.poll();await f.flush();await f.flush();
    const now=sidebarGroup(f,'Running now');assert.doesNotMatch(now.textContent,/finished quick|finished Manager|STATUS/);
    assert.equal(hostReads,2);assert.equal(f.api.state.inventoryBusy,true,'activity must not wait for the history scan');
    assert.match(now.textContent,/still running/);assert.match(now.textContent,/Send unanswered/);assert.match(now.textContent,/still waiting/);
    assert.equal(find(now,'session-item').filter(n=>n.classList.contains('active')).length,2);
    assert.equal(find(now,'waiting-flag').length,1);
    assert.equal(f.api.state.inventory.length,6,'activity updates never remove sessions from the inventory');
    assert.equal(f.requests.some(([url,opts])=>opts?.method==='POST' || !/^\/api\/claude\/(sessions|turns)/.test(url)),false);
    // The old host snapshot arrives after ten minutes of native paging: it must not relight a finished turn.
    f.setNow(701000);release();await refresh;
    assert.equal(sidebarGroup(f,'Running now'),undefined,'a ten-minute-old observation is unknown, never relit by the old scan');
    assert.match(f.el('sessions-list').textContent,/activity unknown/);
  }
});
test('host activity labels failed or missing observations unknown without claiming idle, pauses hidden, and never overlaps',async()=>{
  const storeRef='host-store',quick=seat(904,{}, {storeRef,lineage:undefined,state:'ready',lifecycle:'pending',liveState:{primary_turn_open:false}});
  let mode='busy',release,reads=0;
  const f=fixture(url=>{
    const q=new URLSearchParams(url.split('?')[1]);
    if(q.get('storeRef')!==storeRef)return env({sessions:[quick],hasMore:false,status:'complete'});
    reads++;if(mode==='fail')throw new Error('offline');
    if(mode==='hold')return new Promise(resolve=>release=()=>resolve(env({sessions:[{...quick,liveState:{primary_turn_open:true}}],hasMore:false})));
    return env({sessions:mode==='missing'?[]:[{...quick,liveState:{primary_turn_open:true}}],hasMore:false,status:'complete'});
  },{sidebarOnly:true});
  f.setNow(100000);await f.api.list();f.api.state.inventoryNextPoll=Infinity;
  await f.api.poll();await f.flush();assert.ok(sidebarGroup(f,'Running now'));
  for(const next of ['fail','missing']){mode=next;f.setNow(f.api.state.hostActivityNextPoll);await f.api.poll();await f.flush();assert.equal(sidebarGroup(f,'Running now'),undefined,next+' is not current running evidence');assert.match(f.el('sessions-list').textContent,/activity unknown/);}
  mode='hold';f.setNow(f.api.state.hostActivityNextPoll);f.doc.hidden=true;const before=reads;
  await f.api.poll();assert.equal(reads,before);f.doc.hidden=false;
  await f.api.poll();await f.flush();await f.api.poll();assert.equal(reads,before+1);release();await f.flush();
  assert.ok(sidebarGroup(f,'Running now'));
});
test('host activity uses the same generation and does not override a newer listed or selected observation',async()=>{
  const quick=seat(905,{}, {lineage:undefined,state:'ready',lifecycle:'pending',liveState:{primary_turn_open:true}}),f=fixture(sessionsOf(quick));
  f.setNow(100000);await f.api.list();
  const observed=(at,generation=quick.generation)=>f.api.state.hostActivity.set(quick.executionId,{at,row:{...quick,generation,liveState:{primary_turn_open:false}}});
  observed(99999);f.api.inventory();assert.ok(sidebarGroup(f,'Running now'),'newer listed turn wins');
  observed(100001,quick.generation+1);f.api.inventory();assert.ok(sidebarGroup(f,'Running now'),'another generation cannot settle this turn');
  observed(100001);f.api.inventory();assert.equal(sidebarGroup(f,'Running now'),undefined);
  Object.assign(f.api.state,{session:quick,liveState:{primary_turn_open:true},liveStateAt:100002});f.api.inventory();
  assert.ok(sidebarGroup(f,'Running now'),'a newer selected-session observation still wins');
});
const assertStub=(group,text)=>{
  const stubs=find(group,'session-stub');assert.equal(stubs.length,1);const stub=stubs[0];
  assert.equal(stub.tag,'div');assert.equal(stub.textContent,text);assert.equal(stub.children.length,0);
  for(const key of ['onclick','onkeydown','onkeyup'])assert.equal(stub[key],undefined);
  for(const key of ['tabindex','aria-hidden','role'])assert.equal(stub.attributes[key],undefined);
  return stub;
};
test('T2.1: the selected live Manager is pinned once with its watched children and an inert home stub; a Conductor keeps the remembered pin',async()=>{
  const manager={...host,sessionRef:'u2-manager',uuid:U(201),executionId:cc(201),sessionLabel:'Manager-Session-201',
    lifecycle:'live',state:'ready',capabilities:{subagents:true},liveState:{primary_turn_open:true}},c=cond(202,'U2PINS',1);
  const child={...host,source:'native',role:'subagent',sessionRef:'u2-native',parentUuid:manager.uuid,agentId:'a1',title:'native task',scopes:[{scopeRef:'child-scope',label:'agent:a1'}]};
  const f=fixture(url=>url.startsWith('/api/claude/subagents')?env({sessions:[child],status:'complete',hasMore:false}):
    url.startsWith('/api/claude/events')?page([event(1)],{sessionRef:new URLSearchParams(url.split('?')[1]).get('sessionRef')}):sessionsOf(manager,c)(url));
  await f.api.list();await f.api.browse(manager);await f.api.children();
  assert.equal(f.api.state.blocked,false);
  assert.equal(f.el('sessions-pinned').hidden,false);assert.equal(find(f.el('sessions-pinned'),'session-item').length,1);
  assert.equal(find(f.el('sessions-list'),'session-item').filter(n=>n.textContent.includes('Manager-Session-201')).length,0);
  assert.equal(sidebarItems(f).filter(n=>n.classList.contains('selected')).length,1);
  assert.equal(f.el('subagents-block').parentNode,f.el('sessions-pinned').children[0]);assert.equal(f.el('subagents-block').hidden,false);
  assert.equal(f.el('subagents-list').children.length,1);assert.match(f.el('sessions-filter-status').textContent,/1 shown · 2 listed/);
  assertStub(sidebarGroup(f,'Manager Sessions'),'↑ Manager-Session-201 · pinned at top');
  const refreshed={...manager,sessionRef:'u2-manager-reissued'};f.api.state.inventory=[refreshed,c];f.api.inventory();
  assert.equal(find(f.el('sessions-pinned'),'session-item').length,1,'pin follows execution identity after a pane-server restart');
  f.el('sessions-role').value='manager';f.el('sessions-role').onchange();
  assert.equal(f.el('sessions-pinned').hidden,true);assert.equal(f.el('sessions-pinned').children.length,0);
  assert.equal(find(f.el('sessions-list'),'session-stub').length,0);assert.equal(sidebarItems(f).length,1);
  f.el('sessions-role').value='all';f.el('sessions-role').onchange();
  assert.equal(f.el('sessions-pinned').hidden,false);
  await f.api.browse(c);
  assert.equal(f.api.state.blocked,false);
  // A Conductor selection never replaces the remembered Manager family: the whole pin stays, only the
  // browsed Conductor is selected, and the Manager's own home row keeps its pinned stub.
  assert.equal(f.el('sessions-pinned').hidden,false);assert.equal(find(f.el('sessions-pinned'),'session-item').length,1);
  assert.match(f.el('sessions-pinned').textContent,/Manager-Session-201/);
  assert.equal(sidebarItems(f).filter(n=>n.classList.contains('selected')).length,1);
  assertStub(sidebarGroup(f,'Manager Sessions'),'↑ Manager-Session-201 · pinned at top');
  assert.equal(sidebarGroup(f,'Running now'),undefined,'a pinned Manager is not also lifted into Running now');
});
test('T2.2: a non-selected Manager waiting on one background task lifts into Running now and keeps its home stub',async()=>{
  const manager={...host,sessionRef:'u2-wait',uuid:U(211),executionId:cc(211),sessionLabel:'Manager-Session-211',
    lifecycle:'live',state:'ready',liveState:{primary_turn_open:false,background_tasks:{bash:{task_type:'local_bash'}}}};
  // An explicit Manager choice elsewhere keeps this waiting Manager non-selected; the no-choice fallback
  // would instead pin the only Manager and lift nothing.
  const chosen={...host,sessionRef:'u2-chosen',uuid:U(209),executionId:cc(209),sessionLabel:'Manager-Session-209',createdAt:1,lifecycle:'sleeping',state:'collected'};
  const f=fixture(sessionsOf(manager,chosen));await f.api.list();
  f.api.state.topManager=chosen;f.api.inventory();
  const running=sidebarGroup(f,'Running now');assert.ok(running,'waiting root Manager is in Running now');
  assert.equal(headingText(running.children[0]),'Running now · 1 session');
  assert.equal(running.children[0].title,'Everything active now: programs, Manager Sessions and other sessions with something busy (the yellow spinner) or waiting, in them or under them. Each keeps a dim stub at its usual place.');
  assert.equal(find(running,'waiting-flag').length,1);assert.equal(sidebarItems(f).length,2);
  assert.equal(f.el('sessions-pinned').hidden,false);assert.match(f.el('sessions-pinned').textContent,/Manager-Session-209/);
  const stubs=find(sidebarGroup(f,'Manager Sessions'),'session-stub').map(n=>n.textContent);
  assert.ok(stubs.includes('↑ Manager-Session-211 · shown at top'),'the running Manager keeps its home stub');
  assert.ok(stubs.includes('↑ Manager-Session-209 · pinned at top'),'the chosen pin keeps its home stub');
});
test('T2.3: an active host quick worker without program lineage lifts from its folder into Running now',async()=>{
  const quick=seat(221,{}, {lineage:undefined,title:'unowned quick job',lifecycle:'live',state:'ready',liveState:{primary_turn_open:true}});
  const f=fixture(sessionsOf(quick));await f.api.list();
  const running=sidebarGroup(f,'Running now');assert.ok(running,'active standalone quick workers must be lifted');
  assert.equal(headingText(running.children[0]),'Running now · 1 session');
  assert.equal(find(running,'session-item')[0].classList.contains('active'),true);assert.equal(sidebarItems(f).length,1);
  assertStub(sidebarGroup(f,'Quick workers and helpers · quick-worker'),'↑ unowned quick job · shown at top');
});
test('T2.4: lifted Full and Quick programs leave one stub each and their home counts do not change with activity',async()=>{
  const live={lifecycle:'live',state:'ready'},full=cond(231,'U2FULL',1,{...live,lineage:{quickProgram:false}}),other=cond(232,'U2HOME',1,{...live,lineage:{quickProgram:false}}),quick=cond(233,'U2QUIC',1,live);
  const f=fixture(sessionsOf(full,other,quick));await f.api.list();
  const counts=()=>['Full programs','Quick programs'].map(label=>headingText(sidebarGroup(f,label).children[0])),before=counts();
  f.api.state.inventory.find(r=>r.executionId===full.executionId).liveState={primary_turn_open:true};
  f.api.state.inventory.find(r=>r.executionId===quick.executionId).liveState={primary_turn_open:true};f.api.inventory();
  const running=sidebarGroup(f,'Running now');assert.equal(find(running,'program').length,2);
  assert.ok(sidebarGroup(f,'Quick programs'),'a fully lifted home group must remain, with its program stub and count');
  assert.equal(headingText(running.children[0]),'Running now · 2 programs');assert.deepEqual(counts(),before);
  assertStub(sidebarGroup(f,'Full programs'),'↑ Pane round 7: programs · shown at top');
  assertStub(sidebarGroup(f,'Quick programs'),'↑ Pane round 7: programs · shown at top');
  assert.equal(find(f.el('sessions-list'),'program').length,3,'each program tree is drawn once, not at both placements');
  assert.equal(sidebarItems(f).length,3,'each open program session is drawn once');
});
test('T2.5: the pointer hold keeps a now-quiet quick worker and its stub until leave, without hiding new activity',async()=>{
  const quick=seat(241,{}, {lineage:undefined,title:'held quick',lifecycle:'live',state:'ready',liveState:{primary_turn_open:true}}),late=seat(242,{}, {lineage:undefined,displayRole:'worker',title:'new activity',cwd:'/r/other',lifecycle:'live',state:'ready'});
  const f=fixture(sessionsOf(quick,late));await f.api.list();const initial=f.api.state.placed,key='r:'+f.api.identityOf(quick);
  f.api.setSidebarHover(true);f.api.state.inventory.find(r=>r.executionId===quick.executionId).liveState={primary_turn_open:false};f.api.inventory();
  assert.equal(f.api.state.activity.get(key).any,false);assert.equal(f.api.state.placed.get(key),'running');assert.notEqual(f.api.state.placed,initial);
  assert.match(sidebarGroup(f,'Running now').textContent,/held quick/);assertStub(sidebarGroup(f,'Quick workers and helpers · quick-worker'),'↑ held quick · shown at top');
  f.api.state.inventory.find(r=>r.executionId===late.executionId).liveState={primary_turn_open:true};f.api.inventory();
  assert.equal(find(sidebarGroup(f,'Running now'),'session-item').length,2,'new activity can enter while old work is held');
  f.api.setSidebarHover(false);
  assert.equal(f.api.state.sidebarHover,false);assert.equal(f.api.state.placed.get(key),'home');
  assert.doesNotMatch(sidebarGroup(f,'Running now').textContent,/held quick/);
  assert.equal(find(sidebarGroup(f,'Quick workers and helpers · quick-worker'),'session-stub').length,0);
  assert.equal(find(sidebarGroup(f,'Quick workers and helpers · quick-worker'),'session-item').length,1);assert.equal(sidebarItems(f).length,2);
  f.api.state.inventory=[];f.api.inventory();assert.equal(f.api.state.placed.size,0,'complete renders replace, never merge, prior placements');
});
test('T2.6: the first and only complete cold-start list already lifts an active quick worker',async()=>{
  const quick=seat(251,{}, {lineage:undefined,title:'cold quick',lifecycle:'live',state:'ready',liveState:{primary_turn_open:true}});
  const f=fixture(sessionsOf(quick));await f.api.list();
  assert.equal(f.requests.filter(([url])=>url.startsWith('/api/claude/sessions')).length,1);
  // The list cadence's unwindowed Manager summary read pair lives in its own lane, never in the inventory count.
  assert.equal(f.familyRequests.length,2);assert.ok(f.familyRequests.some(([url])=>/role=Manager/.test(url)));
  assert.equal(f.api.state.listPartial,false);assert.equal(headingText(f.el('sessions-list').children[0].children[0]),'Running now · 1 session');
  assert.equal(sidebarItems(f).length,1);assert.equal(f.api.state.placed.get('r:'+f.api.identityOf(quick)),'running');
  assertStub(sidebarGroup(f,'Quick workers and helpers · quick-worker'),'↑ cold quick · shown at top');
});
test('U2 partial first scans cannot seed the hold, and filtered grouping ignores pinning, stubs and the hold',async()=>{
  const quick=seat(261,{}, {lineage:undefined,title:'partial quick',lifecycle:'live',state:'ready',liveState:{primary_turn_open:true}});
  const f=fixture();const initial=f.api.state.placed;f.api.setSidebarHover(true);
  f.api.state.inventory=[quick];f.api.state.inventoryAt=Date.now();f.api.state.listPartial=true;f.api.inventory();
  assert.equal(f.api.state.placed,initial);assert.equal(initial.size,0);assert.ok(sidebarGroup(f,'Running now'));
  quick.liveState={primary_turn_open:false};f.api.state.listPartial=false;f.api.inventory();
  assert.equal(sidebarGroup(f,'Running now'),undefined,'a transient partial-list lift cannot hold into the first complete render');
  quick.liveState={primary_turn_open:true};f.api.inventory();quick.liveState={primary_turn_open:false};
  f.el('sessions-role').value='quick-worker';f.el('sessions-role').onchange();
  assert.equal(sidebarGroup(f,'Running now'),undefined);assert.equal(find(f.el('sessions-list'),'session-stub').length,0);
  assert.equal(find(sidebarGroup(f,'Quick workers and helpers · quick-worker'),'session-item').length,1);assert.equal(f.el('sessions-pinned').hidden,true);
});
test('U2 Running now orders Managers, paged programs, then unpaged other roots, with home paging and counts intact',async()=>{
  const live={lifecycle:'live',state:'ready',liveState:{primary_turn_open:true}};
  const managers=[2,1].map(n=>({...host,sessionRef:'u2-order-m'+n,uuid:U(270+n),executionId:cc(270+n),sessionLabel:'Manager-Session-'+n,createdAt:n,...live}));
  // A quiet chosen Manager elsewhere keeps the two active ones non-selected, so both stay in Running now
  // (the no-choice fallback would pin the highest of them instead).
  const chosen={...host,sessionRef:'u2-order-chosen',uuid:U(274),executionId:cc(274),sessionLabel:'Manager-Session-3',createdAt:3,lifecycle:'sleeping',state:'collected'};
  const programs=Array.from({length:13},(_,i)=>cond(280+i,'P'+String(i).padStart(5,'0'),1,{...live,createdAt:100+i}));
  const roots=Array.from({length:31},(_,i)=>seat(300+i,{}, {...live,lineage:undefined,title:'root '+i,createdAt:200+i}));
  const f=fixture(sessionsOf(...roots,...programs,...managers,chosen));f.api.state.topManager=chosen;await f.api.list();
  assert.equal(f.el('sessions-pinned').hidden,false);assert.match(f.el('sessions-pinned').textContent,/Manager-Session-3/);
  const running=sidebarGroup(f,'Running now');assert.equal(headingText(running.children[0]),'Running now · 13 programs · 33 sessions');
  const entries=running.children.filter(n=>['session-item','program'].includes(n.className));
  assert.deepEqual(entries.slice(0,2).map(n=>find(n,'name-text')[0].textContent),['Manager-Session-2','Manager-Session-1']);
  assert.equal(entries[2].className,'program');assert.match(entries[2].textContent,/P00012/);
  assert.equal(entries[13].className,'program');assert.match(entries[14].textContent,/root 30/);assert.match(entries.at(-1).textContent,/root 0/);
  assert.equal(find(running,'program').length,12);assert.equal(find(running,'session-item').length,45,'12 program members plus all 33 root rows');
  const home=sidebarGroup(f,'Quick workers and helpers · quick-worker');assert.equal(headingText(home.children[0]),'Quick workers and helpers · quick-worker · 31');
  assert.equal(find(home,'session-stub').length,30);assert.equal(find(sidebarGroup(f,'Quick programs'),'session-stub').length,12);
  find(home,'group-foot')[0].children[0].onclick();assert.equal(find(sidebarGroup(f,'Quick workers and helpers · quick-worker'),'session-stub').length,31);
  f.api.state.session=programs[0];f.api.inventory();
  assert.equal(find(sidebarGroup(f,'Running now'),'program').length,13,'selectedIn keeps a selected program visible beyond PROGRAM_PAGE');
  assert.equal(find(sidebarGroup(f,'Quick programs'),'session-stub').length,13,'its selected home stub also stays visible beyond the page');
  find(sidebarGroup(f,'Running now'),'group-foot')[0].children[0].onclick();
  assert.equal(find(sidebarGroup(f,'Running now'),'program').length,13);assert.equal(sidebarItems(f).length,47,'the pinned chosen Manager rides beside the 46 running rows');
});
test('U2 the detached watched block stays in the scroll container; a worker selection keeps the remembered pin and its watched children',()=>{
  const manager={...host,sessionRef:'u2-detached',uuid:U(341),executionId:cc(341),sessionLabel:'Manager-Session-341',capabilities:{subagents:true}};
  const child={...host,source:'native',role:'subagent',sessionRef:'u2-detached-child',parentUuid:manager.uuid,agentId:'a1',title:'watched child',scopes:[{scopeRef:'child-scope',label:'agent:a1'}]};
  const quick=seat(342,{}, {lineage:undefined,title:'selected worker'}),f=fixture();
  f.el('sessions-scroll').append(f.el('sessions-info'),f.el('sessions-list'));
  f.api.state.childParent=manager;f.api.state.session=manager;f.api.state.children=[child];f.api.state.childLoaded=true;f.api.inventory();
  assert.equal(f.el('subagents-block').parentNode,f.el('sessions-scroll'));assert.equal(f.el('subagents-block').hidden,false);
  f.api.setSidebarHover(true);f.api.state.inventory=[manager,quick];f.api.inventory();
  assert.equal(f.el('subagents-block').parentNode,f.el('sessions-pinned').children[0]);assert.equal(f.el('sessions-pinned').hidden,false);
  f.api.state.session=quick;f.api.inventory();
  // Browsing a worker keeps the chosen Manager family pinned; only the worker is selected.
  assert.equal(f.el('sessions-pinned').hidden,false);assert.match(f.el('sessions-pinned').textContent,/Manager-Session-341/);
  assert.equal(sidebarItems(f).filter(n=>n.classList.contains('selected')).length,1);
  assert.equal(sidebarGroup(f,'Running now'),undefined,'a previous pin is not held or made running by hover');
  assertStub(sidebarGroup(f,'Manager Sessions'),'↑ Manager-Session-341 · pinned at top');
  assert.equal(sidebarItems(f).length,2);
  assert.equal(f.el('subagents-block').hidden,false,'watched children remain inside their normal expanded row');
});

// ---------------------------------------------------------------- U3: routed minimal trees, quiet-Conductor folds, section choices
const quietLive={lifecycle:'live',state:'ready',liveState:{primary_turn_open:false,background_tasks:{}}};
const memoryStorage=(initial={})=>{const data=new Map(Object.entries(initial)),writes=[];return {data,writes,getItem:key=>data.get(key) ?? null,setItem:(key,value)=>{writes.push([key,value]);data.set(key,value);}};};
const ownState=(f,row,busy=false,waiting=false)=>{f.api.state.inventory.find(r=>r.executionId===row.executionId).liveState={primary_turn_open:busy,background_tasks:waiting?{bash:{task_type:'local_bash'}}:{}};};
const programOf=(f,core)=>find(f.el('sessions-list'),'program').find(p=>find(p,'program-meta')[0].textContent.includes('[ap:'+core+']'));
const programFold=(f,core)=>find(programOf(f,core),'program-fold')[0];
const sectionControl=(f,label,cls)=>find(sidebarGroup(f,label).children[0],cls)[0];
const treeOf=(f,row)=>find(f.el('sessions-list'),'tree-node').find(n=>n.children.some(child=>child.className==='session-item' && find(child,'session-open')[0].title.includes('UUID '+row.uuid)));
const threeBranches=(core,start=400)=>{
  const rows=[],conductors=[],leads=[],helpers=[];
  for(let n=1;n<=3;n++){
    const c=cond(start+n*3,core,n,quietLive),lead=seat(start+n*3+1,{program:core,kind:'seat',seatRole:'lead',conductorN:n,title:'lead '+n},{...quietLive,displayRole:'lead-seat'});
    const helper=seat(start+n*3+2,{program:core,kind:'helper',parentSeat:lead.executionId,title:'helper '+n},{...quietLive,displayRole:'helper'});
    conductors.push(c);leads.push(lead);helpers.push(helper);rows.push(c,lead,helper);
  }
  return {rows,conductors,leads,helpers};
};
test('desktop ⊟ is a two-state toggle read from what is open: first click closes the tops (inner state kept), second opens only busy or waiting work',async()=>{
  const core='U3TOGL',quiet='U3QUIE',a=threeBranches(core),b=threeBranches(quiet,700);a.helpers[1].liveState={primary_turn_open:true};
  const f=fixture(sessionsOf(...a.rows,...b.rows));await f.api.list();
  const button=f.el('sessions-collapse'),open=key=>f.api.state.lineageOpen.get(key),head=c=>find(programOf(f,c),'program-head')[0];
  f.api.state.lineageOpen.set('s:'+a.leads[2].executionId,true);f.api.inventory();
  assert.equal(button.dataset.next,'collapse');assert.match(button.title,/^Close every program and freestanding tree/);
  assert.equal(head(core).getAttribute('aria-expanded'),'true');assert.equal(head(quiet).getAttribute('aria-expanded'),'true','a quiet live program opens by default');
  // First click: only the program rows close; what is inside them keeps its state; home sections fold.
  button.onclick();
  assert.equal(open('p:'+core),false);assert.equal(open('p:'+quiet),false);assert.equal(open('s:'+a.leads[2].executionId),true,'inner open state is kept');
  assert.equal(sidebarItems(f).length,0);assert.equal(sidebarGroup(f,'Quick programs').children.length,1);assert.ok(sidebarGroup(f,'Running now').children.length>1,'Running now stays open');
  assert.equal(button.dataset.next,'running');assert.match(button.title,/^Open only what is busy or waiting/);assert.equal(button.getAttribute('aria-label'),'Show only running work');
  // Second click: the minimal active subtree of the running program, nothing quiet, only the sections that draw it.
  button.onclick();
  assert.equal(open('p:'+core),true);assert.equal(open('p:'+quiet),false);assert.equal(open('s:'+a.conductors[1].executionId),true);
  assert.equal(open('s:'+a.leads[1].executionId),true);assert.equal(open('s:'+a.leads[2].executionId),false,'a quiet branch folds closed');
  assert.deepEqual(sidebarItems(f).map(n=>find(n,'session-open')[0].title.match(/UUID (\S+)/)[1]),[a.conductors[0],a.conductors[1],a.leads[1],a.helpers[1],a.conductors[2]].map(r=>r.uuid));
  assert.equal(sidebarGroup(f,'Quick programs').children.length,1);assert.equal(button.dataset.next,'collapse');
  // The state is re-derived from what is drawn open, not remembered: hand-closing the only open row arms the second state,
  // hand-opening one arms the first again, and the phone's cycle flag is never touched.
  head(core).onclick();assert.equal(button.dataset.next,'running');head(core).onclick();assert.equal(button.dataset.next,'collapse');
  button.onclick();assert.equal(open('p:'+core),false);assert.equal(open('s:'+a.leads[1].executionId),true);assert.equal(f.api.state.treeCycle,undefined);
});
test('T3.1: the program toggle folds to exactly the busy helper path and opens every tree node in one stored write',async()=>{
  const core='U3PATH',{rows,conductors,leads,helpers}=threeBranches(core),storage=memoryStorage();
  helpers[1].liveState={primary_turn_open:true};const f=fixture(sessionsOf(...rows),{storage});await f.api.list();
  assert.equal(programFold(f,core)?.textContent,'⊟');assert.equal(programFold(f,core).title,'Fold to what is active: everything busy or waiting stays visible');
  const before=storage.writes.length;programFold(f,core).onclick({stopPropagation(){}});
  assert.equal(storage.writes.length,before+1);assert.equal(storage.writes.at(-1)[0],'pane_lineage_open');
  const opens=f.api.state.lineageOpen;assert.equal(opens.get('p:'+core),true);
  conductors.forEach((c,i)=>assert.equal(opens.get('s:'+c.executionId),i===1));leads.forEach((lead,i)=>assert.equal(opens.get('s:'+lead.executionId),i===1));
  assert.equal(sidebarItems(f).length,5);assert.ok(sidebarItems(f).find(n=>n.classList.contains('active')).textContent.includes('helper 2'));
  assert.equal(programFold(f,core).textContent,'⊞');assert.equal(programFold(f,core).title,'Open everything in this program');
  let stopped=false;programFold(f,core).onkeydown({key:'Enter',preventDefault(){},stopPropagation(){stopped=true;}});assert.equal(stopped,true,'keyboard activation cannot bubble into the program head');
  assert.equal(storage.writes.length,before+2);assert.ok([...opens.values()].every(Boolean));assert.equal(sidebarItems(f).length,9);
});
test('T3.2: quiet numbered Conductor runs fold on the first complete render, split around activity, and open with a label-less ribbon',async()=>{
  const core='U3RUNS',cs=Array.from({length:11},(_,i)=>cond(430+i,core,i+1,quietLive));cs[8].liveState={primary_turn_open:true};
  const f=fixture(sessionsOf(...cs));await f.api.list();
  assert.equal(f.requests.filter(([url])=>url.startsWith('/api/claude/sessions')).length,1);
  assert.equal(f.familyRequests.length,2,'the list cadence reads both unwindowed Manager summaries, in their own lane');
  assert.deepEqual(find(programOf(f,core),'conductor-fold').map(n=>n.textContent),['▸ Conductors -001 – -008','▸ Conductors -010 – -011']);
  assert.equal(sidebarItems(f).length,1);assert.match(sidebarItems(f)[0].textContent,/Conductor:\[ap:U3RUNS\]-009/);
  find(programOf(f,core),'conductor-fold')[0].onclick();
  assert.equal(find(programOf(f,core),'conductor-fold')[0].textContent,'▾');assert.equal(sidebarItems(f).length,9);
  assert.ok(cs.slice(0,9).every(c=>f.api.state.unfolded.has('s:'+c.executionId)));assert.ok(!f.api.state.unfolded.has('s:'+cs[9].executionId));
  ownState(f,cs[7],true);ownState(f,cs[8],false);ownState(f,cs[9],false,true);f.api.inventory();
  assert.ok(treeOf(f,cs[8]),'one quiet Conductor between two active ones remains a row');assert.ok(treeOf(f,cs[7]));assert.ok(treeOf(f,cs[9]));
  assert.equal(find(programOf(f,core),'conductor-fold').length,1);
  assert.equal(f.api.state.activity.get('s:'+cs[9].executionId).own,'waiting','waiting splits runs just like busy');
});
test('T3.3: program minimal closes folds; program and global expand open them; global collapse preserves child and fold choices',async()=>{
  const core='U3ALLS',cs=[1,2,3].map(n=>cond(450+n,core,n,quietLive));cs[2].liveState={primary_turn_open:true};
  const f=fixture(sessionsOf(...cs));await f.api.list();const key='fold:s:'+cs[0].executionId;
  assert.ok(find(programOf(f,core),'conductor-fold')[0]);find(programOf(f,core),'conductor-fold')[0].onclick();
  assert.equal(f.api.state.lineageOpen.get(key),true);programFold(f,core).onclick();
  assert.equal(f.api.state.lineageOpen.get(key),false);assert.equal(find(programOf(f,core),'conductor-fold')[0].textContent,'▸ Conductors -001 – -002');
  assert.equal(programFold(f,core).textContent,'⊞');programFold(f,core).onclick();
  assert.equal(f.api.state.lineageOpen.get(key),true);assert.equal(sidebarItems(f).length,3);
  f.el('sessions-collapse').onclick();assert.equal(f.api.state.lineageOpen.get('p:'+core),false);assert.equal(f.api.state.lineageOpen.get(key),true);assert.equal(sidebarItems(f).length,0);
  f.api.state.lineageOpen.set(key,false);f.el('sessions-expand').onclick();
  assert.equal(f.api.state.lineageOpen.get(key),true);assert.equal(find(programOf(f,core),'conductor-fold')[0].textContent,'▾');assert.equal(sidebarItems(f).length,3);
});
test('T3.4: a quiet Conductor containing the selected session or its selected native child never folds',async()=>{
  const core='U3SELE',{rows,conductors,leads}=threeBranches(core,470),f=fixture(sessionsOf(...rows));
  f.api.state.lineageOpen.set('p:'+core,true);await f.api.list();assert.equal(find(programOf(f,core),'conductor-fold').length,1);
  f.api.state.session=leads[1];f.api.inventory();
  assert.equal(find(programOf(f,core),'conductor-fold').length,0);assert.ok(treeOf(f,conductors[1]));assert.equal(sidebarItems(f).filter(n=>n.classList.contains('selected')).length,1);
  f.api.state.session={...host,source:'native',role:'subagent',sessionRef:'u3-native-selection',parentUuid:leads[1].uuid,agentId:'a1'};f.api.state.childParent=leads[1];f.api.inventory();
  assert.ok(treeOf(f,conductors[1]));assert.equal(find(programOf(f,core),'conductor-fold').length,0);
});
test('T3.5: active only admits active programs and stubs, keeps them after quiet, seeds newly active minimal paths, and restores saved expanders',async()=>{
  const a=threeBranches('U3ACTA',500),b=threeBranches('U3ACTB',520),storage=memoryStorage(),rows=[...a.rows,...b.rows];
  a.helpers[1].liveState={primary_turn_open:true};const f=fixture(sessionsOf(...rows),{storage});
  for(const row of rows)row.lineage.quickProgram=false;
  f.api.state.lineageOpen.set('p:U3ACTB',true);f.api.state.lineageOpen.set('s:'+b.conductors[0].executionId,true);
  await f.api.list();const saved=JSON.stringify([...f.api.state.lineageOpen]);
  sectionControl(f,'Full programs','section-active').onclick();assert.equal(sectionControl(f,'Full programs','section-active').getAttribute('aria-pressed'),'true');
  assert.equal(headingText(sidebarGroup(f,'Full programs').children[0]),'Full programs · 2 programs');
  assert.equal(find(sidebarGroup(f,'Full programs'),'program').length,0);assertStub(sidebarGroup(f,'Full programs'),'↑ Pane round 7: programs · shown at top');
  let overlay=f.api.state.activeOnly.get('programs:full');assert.deepEqual([...overlay.shown],['p:U3ACTA']);
  a.rows.forEach(row=>ownState(f,row));f.api.inventory();assert.ok(programOf(f,'U3ACTA'),'once shown, a now-quiet entry stays');assert.equal(programOf(f,'U3ACTB'),undefined);
  assert.equal(programFold(f,'U3ACTA').textContent,'⊟','its original minimal busy path survives quiet');
  programFold(f,'U3ACTA').onclick();assert.equal(overlay.open.get('p:U3ACTA'),false);assert.equal(JSON.stringify([...f.api.state.lineageOpen]),saved);
  ownState(f,b.helpers[1],true);f.api.inventory();
  assert.deepEqual([...overlay.shown],['p:U3ACTA','p:U3ACTB']);assert.equal(overlay.open.get('p:U3ACTB'),true);
  b.conductors.forEach((c,i)=>assert.equal(overlay.open.get('s:'+c.executionId),i===1));b.leads.forEach((lead,i)=>assert.equal(overlay.open.get('s:'+lead.executionId),i===1));
  ownState(f,b.helpers[1]);f.api.inventory();assert.equal(find(programOf(f,'U3ACTB'),'conductor-fold').length,1,'quiet runs fold even inside a retained entry');
  find(programOf(f,'U3ACTB'),'conductor-fold')[0].onclick();assert.ok(treeOf(f,b.helpers[1]),'opening its quiet run preserves the seeded minimal path');
  treeOf(f,b.conductors[0]).children[0].onclick();assert.equal(overlay.open.get('s:'+b.conductors[0].executionId),true);assert.equal(JSON.stringify([...f.api.state.lineageOpen]),saved);
  sectionControl(f,'Full programs','section-active').onclick();assert.equal(f.api.state.activeOnly.has('programs:full'),false);assert.equal(JSON.stringify([...f.api.state.lineageOpen]),saved);
  assert.equal(programOf(f,'U3ACTB').children[0].getAttribute('aria-expanded'),'true');
  assert.equal(f.api.state.lineageOpen.get('s:'+b.conductors[0].executionId),true,'Dan\'s saved expander is exactly restored');
  sectionControl(f,'Full programs','section-active').onclick();assert.equal(find(sidebarGroup(f,'Full programs'),'program').length,0);assert.equal(f.api.state.activeOnly.get('programs:full').shown.size,0,'on again starts fresh');
  assert.ok(storage.writes.every(([key])=>key!=='pane_lineage_open'),'overlay clicks never write the saved store');
});
test('T3.6: a Conductor shown before going quiet stays a row under the pointer and folds on leave',async()=>{
  const core='U3HOLD',cs=[1,2].map(n=>cond(550+n,core,n,quietLive));cs[0].liveState={primary_turn_open:true};
  const f=fixture(sessionsOf(...cs));await f.api.list();assert.ok(treeOf(f,cs[0]));assert.ok(treeOf(f,cs[1]));
  f.api.setSidebarHover(true);ownState(f,cs[0]);f.api.inventory();
  assert.equal(f.api.state.activity.get('p:'+core).any,false);assert.equal(find(programOf(f,core),'conductor-fold').length,0);assert.equal(sidebarItems(f).length,2);
  f.api.setSidebarHover(false);assert.equal(find(programOf(f,core),'conductor-fold')[0]?.textContent,'▸ Conductors -001 – -002');assert.equal(sidebarItems(f).length,0);
});
test('T3.7: a section chevron keeps only its heading, persists the choice, and restores its entries when reopened',async()=>{
  const c=cond(561,'U3SECT',1,{...quietLive,lineage:{quickProgram:false}}),storage=memoryStorage(),f=fixture(sessionsOf(c),{storage});await f.api.list();
  const toggle=()=>sectionControl(f,'Full programs','section-toggle');assert.equal(toggle()?.textContent,'▾');assert.equal(toggle().title,'Collapse this section');
  toggle().onclick();assert.equal(sidebarGroup(f,'Full programs').children.length,1);assert.equal(toggle().textContent,'▸');assert.equal(toggle().title,'Show this section');
  assert.deepEqual(JSON.parse(storage.data.get('pane_sidebar_sections')),{collapsed:['programs:full'],activeOnly:[]});
  const cold=fixture(sessionsOf(c),{storage});await cold.api.list();assert.equal(sidebarGroup(cold,'Full programs').children.length,1,'first complete render honours the saved collapse');
  toggle().onkeydown({key:' ',preventDefault(){}});assert.equal(find(sidebarGroup(f,'Full programs'),'program').length,1);
});
test('U3 cold active-only preferences are page-only overlays; malformed/unavailable storage is harmless and partial renders never seed the fold hold',async()=>{
  const core='U3COLD',cs=[1,2,3].map(n=>cond(570+n,core,n,quietLive)),storage=memoryStorage({'pane_sidebar_sections':JSON.stringify({collapsed:[],activeOnly:['programs:quick']}),'pane_lineage_open':JSON.stringify([['p:'+core,true]])});
  const f=fixture(sessionsOf(...cs),{storage});await f.api.list();assert.equal(find(sidebarGroup(f,'Quick programs'),'program').length,0);assert.equal(sectionControl(f,'Quick programs','section-active').getAttribute('aria-pressed'),'true');
  ownState(f,cs[1],true);f.api.inventory();assert.ok(f.api.state.activeOnly.get('programs:quick').shown.has('p:'+core));ownState(f,cs[1]);f.api.inventory();
  assert.equal(find(programOf(f,core),'conductor-fold').length,1);assert.equal(f.api.state.activeOnly.get('programs:quick').open.get('p:'+core),true);
  assert.equal(JSON.parse(storage.data.get('pane_lineage_open'))[0][1],true);assert.equal(storage.writes.length,0,'loading and seeding do not write preferences');
  const cold=fixture(sessionsOf(...cs),{storage});await cold.api.list();assert.equal(find(sidebarGroup(cold,'Quick programs'),'program').length,0,'shown/open overlay never persists');
  for(const broken of [memoryStorage({'pane_sidebar_sections':'{broken'}),{getItem(){throw new Error('denied');},setItem(){throw new Error('denied');}}]){
    const g=fixture(sessionsOf(...cs),{storage:broken});await g.api.list();assert.equal(g.api.state.sections.collapsed.size,0);sectionControl(g,'Quick programs','section-toggle').onclick();assert.equal(sidebarGroup(g,'Quick programs').children.length,1);
  }
  const partial=fixture(),initial=partial.api.state.unfolded;partial.api.state.inventory=cs;partial.api.state.listPartial=true;partial.api.state.lineageOpen.set('p:'+core,true);partial.api.setSidebarHover(true);
  cs[0].liveState={primary_turn_open:true};partial.api.inventory();assert.equal(partial.api.state.unfolded,initial);assert.equal(initial.size,0);
  cs[0].liveState={primary_turn_open:false};partial.api.state.listPartial=false;partial.api.inventory();assert.equal(find(programOf(partial,core),'conductor-fold').length,1);assert.equal(partial.api.state.unfolded.size,0);
});
test('U3 reveal and global controls route active-only trees through the overlay, without opening native subagent lists',async()=>{
  const core='U3ROUT',{rows,leads,helpers}=threeBranches(core,590);helpers[1].liveState={primary_turn_open:true};
  const storage=memoryStorage(),f=fixture(url=>url.startsWith('/api/claude/events')?page([event(1)],{sessionRef:new URLSearchParams(url.split('?')[1]).get('sessionRef')}):sessionsOf(...rows)(url),{storage});
  await f.api.list();sectionControl(f,'Quick programs','section-active').onclick();ownState(f,helpers[1]);f.api.inventory();
  programFold(f,core).onclick();const saved=JSON.stringify([...f.api.state.lineageOpen]),writes=storage.writes.length;
  await f.api.browse(leads[1]);assert.equal(f.api.state.blocked,false);assert.equal(JSON.stringify([...f.api.state.lineageOpen]),saved);assert.equal(storage.writes.length,writes);
  assert.equal(f.api.state.activeOnly.get('programs:quick').open.get('p:'+core),true);assert.ok(sidebarItems(f).some(n=>n.classList.contains('selected')));
  f.el('sessions-expand').onclick();f.el('sessions-collapse').onclick();assert.equal(JSON.stringify([...f.api.state.lineageOpen]),saved);assert.deepEqual(storage.writes.slice(writes).map(([key])=>key),['pane_sidebar_sections'],'only the new section collapse persists; overlay expanders never write the saved store');
  assert.equal(f.api.state.activeOnly.get('programs:quick').open.get('p:'+core),false);assert.equal(f.api.state.expanded.size,0,'no automatic native-list opens');
});
test('U3 active-only works on flattened Claude/OC members and root stubs without auto-opening native lists; unnumbered Conductors never fold',async()=>{
  const core='U3FLAT',c=cond(611,core,1,quietLive),lead=seat(612,{program:core,kind:'seat',seatRole:'lead',conductorN:1,title:'flat lead'},{...quietLive,displayRole:'lead-seat'}),other=seat(613,{program:core,kind:'seat',seatRole:'lead',conductorN:1,title:'quiet lead'},{...quietLive,displayRole:'lead-seat'});
  const child=ocRow('u3flat',{program:core,kind:'helper',parentSeat:lead.executionId,busy:true,title:'OC active child'}),f=fixture(sessionsOf(c,lead,other),{opencode:()=>ocEnv([child])});await f.api.list();await f.api.ocList();
  f.el('sessions-role').value='lead-seat';f.el('sessions-role').onchange();assert.equal(sidebarItems(f).length,2);
  assert.equal(find(sidebarGroup(f,'Running now'),'section-active').length,0,'no active-only toggle on Running now even when flattened');
  f.api.state.oc.stale=true;f.api.inventory();sectionControl(f,'Quick programs','section-active').onclick();assert.equal(sidebarItems(f).length,0);
  ownState(f,other,true);f.api.inventory();assert.equal(sidebarItems(f).length,2,'filtered program members retain U2 Running-now grouping');
  assert.match(find(sidebarItems(f)[0],'session-crumb')[0].textContent,/\[ap:U3FLAT\] ▸ -001 ▸/);
  ownState(f,other);f.api.inventory();assert.equal(sidebarItems(f).length,0,'home active-only sees no active members after the program returns');
  const flat=seat(619,{}, {...quietLive,lineage:undefined,title:'flat root'}),flatQuiet=seat(620,{}, {...quietLive,lineage:undefined,title:'quiet flat root'}),flatView=fixture(sessionsOf(flat,flatQuiet));await flatView.api.list();
  flatView.el('sessions-role').value='quick-worker';flatView.el('sessions-role').onchange();sectionControl(flatView,'Quick workers and helpers · quick-worker','section-active').onclick();
  assert.equal(sidebarItems(flatView).length,0);ownState(flatView,flat,true);flatView.api.inventory();assert.equal(sidebarItems(flatView).length,1);ownState(flatView,flat);flatView.api.inventory();assert.equal(sidebarItems(flatView).length,1,'once shown also holds in the filtered view');
  const root=seat(614,{}, {...quietLive,lineage:undefined,title:'native root',capabilities:{subagents:true},liveState:{primary_turn_open:false,background_tasks:{a1:{task_type:'local_agent'}}}}),g=fixture(sessionsOf(root));await g.api.list();
  sectionControl(g,'Quick workers and helpers · quick-worker','section-active').onclick();assert.equal(find(sidebarGroup(g,'Quick workers and helpers · quick-worker'),'session-stub').length,1);ownState(g,root);g.api.inventory();
  // A capability alone never draws an empty native expander: with no child ever read, the row has none.
  assert.equal(find(sidebarItems(g)[0],'session-toggle').length,0,'no known children, no expander');assert.equal(g.api.state.expanded.size,0);
  const numbered=[1,2].map(n=>cond(615+n,'U3UNKN',n,quietLive)),unknown=cond(618,'U3UNKN',null,quietLive),h=fixture(sessionsOf(...numbered,unknown));await h.api.list();
  assert.equal(find(programOf(h,'U3UNKN'),'conductor-fold').length,1);assert.ok(treeOf(h,unknown));
});

// ---------------------------------------------------------------- U7: reachable folds, structural home sections, compact all, whole headers
// Capture the bubbling path before handlers replace the DOM, as a browser does.
const clickNode=node=>{const path=[];for(let n=node;n;n=n.parentNode)path.push(n);let stopped=false;
  const event={target:node,stopPropagation(){stopped=true;}};for(const n of path){n.onclick?.(event);if(stopped)break;}};
test('U7.1: an open quiet-Conductor ribbon survives hovered rebuilds and expands/collapses immediately under the pointer',async()=>{
  for(const overlay of [false,true]){
    const core='U7FOLD',cs=[1,2,3,4].map(n=>cond(700+n,core,n,quietLive)),storage=memoryStorage(),f=fixture(sessionsOf(...cs),{storage});
    await f.api.list();
    if(overlay){sectionControl(f,'Quick programs','section-active').onclick();ownState(f,cs[0],true);f.api.inventory();ownState(f,cs[0]);f.api.inventory();}
    f.api.setSidebarHover(true);
    const ribbons=()=>find(programOf(f,core),'conductor-fold');assert.equal(ribbons()[0]?.textContent,'▸ Conductors -001 – -004');
    ribbons()[0].onclick();assert.equal(ribbons()[0]?.textContent,'▾');assert.equal(sidebarItems(f).length,4);
    for(let i=0;i<3;i++){f.el('sessions-scroll').scrollTop=i*50;f.api.inventory();assert.equal(ribbons()[0]?.textContent,'▾','background rebuild must not dissolve the open ribbon');assert.equal(sidebarItems(f).length,4);}
    const saved=JSON.stringify([...f.api.state.lineageOpen]);ribbons()[0].onclick();
    assert.equal(ribbons()[0]?.textContent,'▸ Conductors -001 – -004');assert.equal(sidebarItems(f).length,0,'click closes now, not on pointer leave');
    f.api.inventory();assert.equal(ribbons()[0]?.textContent,'▸ Conductors -001 – -004');
    ribbons()[0].onkeydown({key:'Enter',preventDefault(){},stopPropagation(){}});assert.equal(ribbons()[0]?.textContent,'▾');assert.equal(sidebarItems(f).length,4);
    if(overlay)assert.equal(JSON.stringify([...f.api.state.lineageOpen]),saved,'the fix still routes fold choices into the overlay');
    f.api.setSidebarHover(false);assert.equal(ribbons()[0]?.textContent,'▾');
  }
});
test('U7.1 hold: activity splitting an open ribbon never hides already shown Conductors under the pointer',async()=>{
  const core='U7SPLT',cs=[1,2,3,4,5].map(n=>cond(710+n,core,n,quietLive)),f=fixture(sessionsOf(...cs));await f.api.list();
  find(programOf(f,core),'conductor-fold')[0].onclick();f.api.setSidebarHover(true);f.api.inventory();
  assert.equal(find(programOf(f,core),'conductor-fold')[0]?.textContent,'▾');
  ownState(f,cs[1],true);f.api.inventory();assert.ok(cs.every(c=>treeOf(f,c)),'a new fold key must not hide the old ribbon\'s shown members');
  ownState(f,cs[1]);f.api.inventory();assert.ok(cs.every(c=>treeOf(f,c)));assert.equal(find(programOf(f,core),'conductor-fold')[0]?.textContent,'▾');
  // The reverse boundary change: a shown predecessor goes quiet before an open
  // ribbon. It must not swallow that ribbon into a different, closed fold key.
  const g=fixture(sessionsOf({...cs[0],liveState:{primary_turn_open:true}},...cs.slice(1)));await g.api.list();find(programOf(g,core),'conductor-fold')[0].onclick();
  g.api.setSidebarHover(true);ownState(g,cs[0]);g.api.inventory();
  assert.ok(cs.every(c=>treeOf(g,c)));assert.equal(find(programOf(g,core),'conductor-fold')[0]?.textContent,'▾');
  find(programOf(g,core),'conductor-fold')[0].onclick();assert.ok(treeOf(g,cs[0]),'the ribbon click releases only its own members, not the held predecessor');assert.equal(sidebarItems(g).length,1);
});
test('U7.2: only structural home sections remain in fixed order, with arbitrary folders pooled into one paged Other',async()=>{
  const full=cond(721,'U7FULL',1,{...quietLive,lineage:{quickProgram:false}}),quick=cond(722,'U7QUIC',1,quietLive);
  const manager={...host,sessionRef:'u7-manager',uuid:U(723),executionId:cc(723),displayRole:'manager',cwd:'/random/manager-sessions',createdAt:1};
  // Plain workers outside any program: no role-based home, so their working folder places them.
  const root=(n,cwd,over={})=>seat(n,{}, {...quietLive,lineage:undefined,displayRole:'worker',cwd,title:'arbitrary '+n,...over});
  const worker=root(724,'C:\\anywhere\\quick-worker'),old=root(725,'C:\\anywhere\\manager-sessions',{title:'early real conversation',displayRole:'history'});
  const others=Array.from({length:32},(_,i)=>root(730+i,i===0?'/home/dan':i===1?'unknown':'/work/new-folder-'+i,{createdAt:100+i}));
  const active=root(770,'/mixed/brand-new',{liveState:{primary_turn_open:true}}),f=fixture(sessionsOf(...others,active,quick,old,worker,manager,full));await f.api.list();
  assert.deepEqual(f.el('sessions-list').children.map(g=>headingLabel(g.children[0])),['Running now','Favorite programs','Manager Sessions','Full programs','Quick programs','Quick workers and helpers · quick-worker','manager-sessions folder · not Managers','Other']);
  const other=()=>sidebarGroup(f,'Other');assert.equal(headingText(other().children[0]),'Other · 33');assert.equal(find(other(),'session-group').length,1,'no per-folder subsections');
  assert.equal(find(other(),'session-item').length+find(other(),'session-stub').length,30);
  find(other(),'group-foot')[0].children[0].onclick();assert.equal(find(other(),'session-item').length+find(other(),'session-stub').length,33);
  assert.equal(find(other(),'session-stub').length,1);assert.ok(find(other(),'session-open').some(n=>n.title.includes('/work/new-folder-31')),'row hovers retain exact folders');
  assert.equal(find(sidebarGroup(f,'manager-sessions folder · not Managers'),'session-item').length,1);
  f.el('sessions-role').value='worker';f.el('sessions-role').onchange();assert.ok(sidebarGroup(f,'Other'));assert.equal(sidebarItems(f).length,34,'role filtering still covers every standalone worker');
});
test('U7.3: collapse-all persists every home section closed, leaves Running now and the pin visible, and expand-all keeps its program-only meaning',async()=>{
  const c=cond(780,'U7COMP',1,{...quietLive,liveState:{primary_turn_open:true},lineage:{quickProgram:false}}),q=cond(781,'U7CQUT',1,quietLive);
  const manager={...host,sessionRef:'u7-pin',uuid:U(782),executionId:cc(782),displayRole:'manager',liveState:{primary_turn_open:true}};
  const worker=seat(783,{}, {...quietLive,lineage:undefined}),old={...worker,sessionRef:'u7-old',uuid:U(784),executionId:cc(784),displayRole:'worker',cwd:'/x/manager-sessions'},other={...worker,sessionRef:'u7-other',uuid:U(785),executionId:cc(785),displayRole:'worker',cwd:'/anything/new'};
  const rows=[c,q,manager,worker,old,other],storage=memoryStorage(),f=fixture(sessionsOf(...rows),{storage});await f.api.list();f.api.state.session=manager;f.api.inventory();
  sectionControl(f,'Full programs','section-active').onclick();sectionControl(f,'Running now','section-toggle').onclick();
  assert.equal(sidebarGroup(f,'Running now').children.length,1);f.el('sessions-collapse').onclick();
  const homes=()=>f.el('sessions-list').children.filter(g=>headingLabel(g.children[0])!=='Running now');assert.equal(homes().length,7);
  assert.ok(homes().every(g=>g.children.length===1),'every home section is now just its heading');
  assert.equal(sidebarGroup(f,'Running now').children.length,2,'collapse-all reopens Running now even if it was manually closed');
  assert.equal(programOf(f,'U7COMP').children.length,1,'the old program collapse is preserved');assert.equal(find(f.el('sessions-pinned'),'session-item').length,1);
  const saved=JSON.parse(storage.data.get('pane_sidebar_sections'));assert.deepEqual([...saved.collapsed].sort(),[...f.api.state.sections.collapsed].sort());assert.ok(!saved.collapsed.includes('programs:running'));assert.deepEqual(saved.activeOnly,['programs:full']);
  f.el('sessions-expand').onclick();assert.ok(homes().every(g=>g.children.length===1));assert.equal(programOf(f,'U7COMP').children.length,2);
  assert.equal(JSON.stringify(JSON.parse(storage.data.get('pane_sidebar_sections'))),JSON.stringify(saved),'expand-all does not reopen sections or change active-only');
  const cold=fixture(sessionsOf(...rows),{storage});await cold.api.list();assert.ok(cold.el('sessions-list').children.filter(g=>headingLabel(g.children[0])!=='Running now').every(g=>g.children.length===1));assert.ok(sidebarGroup(cold,'Running now').children.length>1);
  clickNode(sidebarGroup(f,'Full programs').children[0].children.find(n=>!n.className));assert.equal(find(sidebarGroup(f,'Full programs'),'session-stub').length,1,'a header reopens its existing home stub; active-only semantics unchanged');
});
test('U7.4: header text and empty header space toggle once; active-only stays independent and chevron Enter/Space still work',async()=>{
  const c=cond(790,'U7HEAD',1,{...quietLive,lineage:{quickProgram:false}}),storage=memoryStorage(),f=fixture(sessionsOf(c),{storage});await f.api.list();
  const heading=()=>sidebarGroup(f,'Full programs').children[0],label=()=>heading().children.find(n=>!n.className),collapsed=()=>f.api.state.sections.collapsed.has('programs:full');
  clickNode(label());assert.equal(collapsed(),true,'a click on the text bubbles to the h3');assert.equal(sidebarGroup(f,'Full programs').children.length,1);
  clickNode(heading());assert.equal(collapsed(),false,'the rest of the header works too');
  clickNode(sectionControl(f,'Full programs','section-active'));assert.equal(collapsed(),false);assert.equal(f.api.state.sections.activeOnly.has('programs:full'),true);
  clickNode(label());assert.equal(collapsed(),true);clickNode(sectionControl(f,'Full programs','section-active'));assert.equal(collapsed(),true);assert.equal(f.api.state.sections.activeOnly.has('programs:full'),false);
  for(const key of ['Enter',' ']){let stopped=false;sectionControl(f,'Full programs','section-toggle').onkeydown({key,preventDefault(){},stopPropagation(){stopped=true;}});assert.equal(stopped,true);}
  assert.equal(collapsed(),true);clickNode(sectionControl(f,'Full programs','section-toggle'));assert.equal(collapsed(),false,'a chevron click must not toggle twice through its parent');
});

// ---------------------------------------------------------------- freestanding quick workers: one forest across backends
// A Manager's quick workers and helpers outside any program: a Claude row's lineage has no program, an OpenCode row none.
const fsOc=(id,over)=>ocRow(id,{program:undefined,quick:true,managerSession:'193',directory:'/r/quick-worker',...over});
const forestRows=()=>{
  const lead=seat(901,{kind:'seat',quick:true,managerSession:'193',title:'▸ ⚡ quick worker · Claude lead'},{managerSession:'193',socket:'[ap:SOCKCC]',createdAt:5000});
  const helper=seat(902,{kind:'helper',quick:true,parentSeat:lead.executionId,managerSession:'193',title:'▸ ⚡ quick helper · Claude sweep'},
    {role:'helper',displayRole:'helper',managerSession:'193',createdAt:5100});
  const ocHelper=fsOc('fshelp1',{kind:'helper',parentSeat:lead.executionId,title:'▸ ⚡ quick helper · OpenCode sweep',createdAt:5200});
  const ocWorker=fsOc('fswork1',{kind:'seat',title:'▸ ⚡ quick worker · Research: Sol vs Astra',socket:'[ap:SOCKOC]',createdAt:6000});
  const ocHelper2=fsOc('fshelp2',{kind:'helper',parentSeat:ocWorker.id,title:'▸ ⚡ quick helper · OpenCode check',createdAt:6100});
  const orphan=fsOc('fsorph1',{kind:'helper',parentSeat:'ses_gonegonegone1',title:'▸ ⚡ quick helper · legacy',createdAt:4000});
  const history={...host,sessionRef:'fs-history',uuid:U(903),source:'native',role:'history',displayRole:'quick-worker',roleSource:'folder',
    title:'old quick transcript',cwd:'/r/quick-worker',createdAt:5500};
  return {lead,helper,ocHelper,ocWorker,ocHelper2,orphan,history,claude:[lead,helper,history],oc:[ocHelper,ocWorker,ocHelper2,orphan]};
};
const forestFixture=(r,{rows=r.claude,transport=null,opencode=null}={})=>fixture(transport || sessionsOf(...rows),
  {opencode:opencode || (url=>url==='/api/opencode/programs'?ocEnv(r.oc):undefined)});
const QUICK_LABEL='Quick workers and helpers · quick-worker';
test('freestanding quick workers of both backends form one forest in Quick workers and helpers: helpers under their lead across backends, an unlisted lead never hides a helper, history stays a flat row by creation time',async()=>{
  const r=forestRows(),f=forestFixture(r);await f.api.list();await f.api.ocList();
  const tree=f.api.state.tree,node=row=>tree.byId.get(row.executionId || row.id);
  assert.equal(tree.programs.length,0);
  assert.deepEqual(new Set(tree.forest.top.map(it=>it.id)),new Set([r.lead.executionId,r.ocWorker.id,r.orphan.id]));
  assert.equal(node(r.helper).parent,node(r.lead));assert.equal(node(r.ocHelper).parent,node(r.lead),'an OpenCode helper sits under its Claude lead');
  assert.equal(node(r.ocHelper2).parent,node(r.ocWorker));
  assert.equal(node(r.orphan).parent,null);assert.equal(node(r.orphan).note,'started by ses_gonegonegone…, not listed');
  assert.deepEqual(Array.from(tree.ancestors.get('s:'+r.ocHelper2.id)),['s:'+r.ocWorker.id],'no program row above a freestanding node');
  assert.deepEqual(f.el('sessions-list').children.map(g=>headingText(g.children[0])),['Favorite programs · 0',QUICK_LABEL+' · 7']);
  const section=sidebarGroup(f,QUICK_LABEL);
  // Newest first: the OpenCode worker's tree, the flat transcript, the Claude lead's tree, the orphan helper.
  assert.deepEqual(section.children.slice(1).map(n=>[n.className,find(n,'name-text')[0].textContent]),
    [['tree-node','[ap:SOCKOC] · Research: Sol vs Astra'],['session-item','old quick transcript'],['tree-node','[ap:SOCKCC] · Claude lead'],['tree-node','legacy']]);
  assert.deepEqual(find(section,'role-word').map(n=>n.textContent),['Quick worker:','Helper:','Quick worker:','Quick worker:','Helper:','Helper:','Helper:']);
  assert.deepEqual(find(section,'backend-chip').map(n=>n.textContent),['OpenCode','OpenCode','Claude Code','Claude Code','OpenCode','OpenCode'],'tree nodes name their backend; the flat transcript row does not');
  const row=name=>find(section,'session-item').find(n=>find(n,'name-text')[0]?.textContent===name);
  assert.match(row('[ap:SOCKCC] · Claude lead').textContent,/host · ended · history \/ context only · for Manager-Session-193/,'no program row above it names the Manager Session');
  // The row's own line (its 💬 glance chip sits beside it since U2).
  assert.match(row('[ap:SOCKOC] · Research: Sol vs Astra').children[0].textContent,/OpenCode idle · succeeded · deepseek-v4.1-flash#max · \$0.001 · for Manager-Session-193$/);
  assert.equal(find(row('legacy'),'session-note')[0].textContent,'started by ses_gonegonegone…, not listed');
  assert.equal(sidebarItems(f).length,3,'each Claude row once: the lead, its helper, the transcript');
  assert.equal(f.el('sessions-filter-status').textContent,'7 shown · 7 listed · 3 in the whole inventory · OpenCode: 4 sessions');
});
test('a busy freestanding helper lifts its lead\'s whole tree into Running now with a home stub; an idle OpenCode lead over a busy helper waits (best effort)',async()=>{
  const r=forestRows();r.ocHelper.busy=true;r.ocHelper2.busy=true;
  const f=forestFixture(r);await f.api.list();await f.api.ocList();
  const running=sidebarGroup(f,'Running now');assert.ok(running);
  assert.equal(headingText(running.children[0]),'Running now · 5 sessions');
  assert.deepEqual(running.children.slice(1).map(n=>find(n,'name-text')[0].textContent),['[ap:SOCKOC] · Research: Sol vs Astra','[ap:SOCKCC] · Claude lead']);
  const home=sidebarGroup(f,QUICK_LABEL);
  assert.deepEqual(find(home,'session-stub').map(n=>n.textContent),['↑ Research: Sol vs Astra · shown at top','↑ Claude lead · shown at top']);
  assert.equal(headingText(home.children[0]),QUICK_LABEL+' · 7','home counts do not change with activity');
  assert.equal(f.api.state.activity.get('s:'+r.lead.executionId).kids.busy,1);assert.equal(f.api.state.placed.get('s:'+r.lead.executionId),'running');
  const waiting=find(running,'waiting-flag');assert.equal(waiting.length,1);assert.match(waiting[0].title,/best effort/);
  assert.ok(find(running,'oc-item').filter(n=>n.classList.contains('active')).length===2,'both busy OpenCode helpers spin');
  r.ocHelper.busy=false;r.ocHelper2.busy=false;await f.api.ocList();
  assert.equal(sidebarGroup(f,'Running now'),undefined);assert.equal(find(sidebarGroup(f,QUICK_LABEL),'session-stub').length,0);
});
test('the role filter flattens freestanding nodes in their own section under a Quick workers breadcrumb, keeping the Manager Session',async()=>{
  const r=forestRows(),f=forestFixture(r);await f.api.list();await f.api.ocList();
  f.el('sessions-role').value='helper';f.el('sessions-role').onchange();
  assert.deepEqual(f.el('sessions-list').children.map(g=>headingText(g.children[0])),[QUICK_LABEL+' · 4']);
  assert.deepEqual(find(f.el('sessions-list'),'session-crumb').map(n=>n.textContent),['Quick workers ▸ Quick worker Research: Sol vs Astra ▸',
    'Quick workers ▸ Quick worker Claude lead ▸','Quick workers ▸ Quick worker Claude lead ▸','Quick workers ▸']);
  assert.match(sidebarItems(f).find(n=>/Claude sweep/.test(n.textContent)).textContent,/for Manager-Session-193/);
  f.el('sessions-role').value='quick-worker';f.el('sessions-role').onchange();
  assert.deepEqual(find(f.el('sessions-list'),'name-text').map(n=>n.textContent),['[ap:SOCKOC] · Research: Sol vs Astra','old quick transcript','[ap:SOCKCC] · Claude lead']);
  assert.equal(sidebarGroup(f,'Running now'),undefined);
});
test('a freestanding OpenCode quick worker\'s 💬 chip opens its glance with Open in CodeNomad for its folder; the conversation view never changes',async()=>{
  const r=forestRows(),glance={schemaVersion:1,backend:'opencode',status:'complete',id:r.ocWorker.id,title:r.ocWorker.title,busy:false,directory:'/r/quick-worker',parentId:null,messages:[]};
  const posts=[],transport=(url,opts)=>{
    if(url==='/api/focus-request' && opts?.method==='POST'){const body=JSON.parse(opts.body);posts.push(body);return {schemaVersion:1,request:{nonce:body.nonce,state:'pending',missingTab:null,noPage:false,outcome:null}};}
    return sessionsOf(...r.claude)(url);};
  const f=forestFixture(r,{transport,opencode:url=>url.startsWith('/api/opencode/glance?')?glance:ocEnv(r.oc)});
  await f.api.list();await f.api.ocList();const before={session:f.api.state.session,generation:f.api.state.generation};
  find(find(f.el('sessions-list'),'oc-item').find(n=>find(n,'name-text')[0].textContent==='[ap:SOCKOC] · Research: Sol vs Astra'),'oc-glance-chip')[0].onclick({stopPropagation(){}});await f.flush();await f.flush();
  assert.deepEqual(f.ocRequests.filter(u=>u.startsWith('/api/opencode/glance')),['/api/opencode/glance?id='+r.ocWorker.id]);
  const button=find(f.el('sessions-list'),'focus-open')[0];assert.equal(button.disabled,false);
  button.onclick({stopPropagation(){}});await f.flush();await f.flush();
  assert.deepEqual({...posts[0],nonce:undefined},{sessionId:r.ocWorker.id,directory:'/r/quick-worker',title:r.ocWorker.title,parentId:null,nonce:undefined});
  assert.equal(f.api.state.session,before.session);assert.equal(f.api.state.generation,before.generation);assert.equal(f.messages.length,0);
});
test('a Manager\'s doorbell on a freestanding quick worker names it, its role, "no program" and its socket, on either backend',async()=>{
  const r=forestRows(),manager={...host,sessionRef:'fs-bell',uuid:U(904),executionId:cc(904),role:'Manager',displayRole:'manager',sessionLabel:'Manager-Session-193',
    cwd:'/r/manager-sessions',lifecycle:'live',state:'ready',liveState:{primary_turn_open:false,background_tasks:{bell:u4Task('bash /x/scripts/oc_seat.sh wait '+r.ocWorker.id+' --max 540')}}};
  const f=forestFixture(r,{rows:[manager,...r.claude]});await f.api.list();await f.api.ocList();
  const bell=()=>[...find(f.el('sessions-pinned'),'waiting-flag'),...find(f.el('sessions-list'),'waiting-flag')].find(n=>n.textContent==='🔔 Doorbell');
  assert.equal(f.el('sessions-pinned').hidden,false,'the only numbered Manager is the remembered fallback pin');
  assert.equal(bell().title.split('\n')[1],'🔔 waits on Research: Sol vs Astra · Quick worker · no program · socket [ap:SOCKOC] — not blocking: wakes when that session stops');
  f.api.state.inventory.find(x=>x.executionId===manager.executionId).liveState.background_tasks={bell:u4Task('oc.sh seat wait '+r.lead.executionId)};f.api.inventory();
  assert.equal(bell().title.split('\n')[1],'🔔 waits on Claude lead · Quick worker · no program · socket [ap:SOCKCC] — not blocking: wakes when that session stops');
});
test('selecting a freestanding Claude helper opens its lead; collapse-all closes the quick-worker section, expand-all opens its tree nodes only',async()=>{
  const r=forestRows(),f=forestFixture(r);await f.api.list();await f.api.ocList();
  f.api.state.lineageOpen.set('s:'+r.lead.executionId,false);f.api.inventory();
  assert.equal(sidebarItems(f).some(n=>/Claude sweep/.test(n.textContent)),false,'a closed lead hides its helpers');
  await f.api.browse(r.helper);
  assert.equal(f.api.state.lineageOpen.get('s:'+r.lead.executionId),true);
  assert.equal(sidebarItems(f).filter(n=>n.classList.contains('selected') && /Claude sweep/.test(n.textContent)).length,1);
  f.el('sessions-collapse').onclick();assert.equal(sidebarGroup(f,QUICK_LABEL).children.length,1,'collapse-all closes the section');
  f.api.state.lineageOpen.set('s:'+r.ocWorker.id,false);f.el('sessions-expand').onclick();
  assert.equal(f.api.state.lineageOpen.get('s:'+r.ocWorker.id),true,'expand-all opens freestanding tree nodes');
  assert.equal(sidebarGroup(f,QUICK_LABEL).children.length,1,'and never reopens a section');
});
test('home sections follow the role, the working folder only without one: a quick fork in the Manager\'s folder and a Claude quick worker elsewhere sit in Quick workers, a plain worker in Other; OpenCode down says so there',async()=>{
  const fork=fsOc('fsfork1',{kind:'seat',directory:'/r/manager-sessions',title:'Manager-Session-193 ▸ ⚡ quick fork worker · fork job',createdAt:7000});
  const away=seat(905,{kind:'seat',quick:true,title:'▸ ⚡ quick worker · elsewhere'},{cwd:'/home/dan',createdAt:7100});
  const plain=seat(906,{}, {lineage:undefined,displayRole:'worker',cwd:'/home/dan',title:'plain worker',createdAt:7200});
  let answer=ocEnv([fork]);const f=fixture(sessionsOf(away,plain),{opencode:()=>answer});await f.api.list();await f.api.ocList();
  assert.equal(f.api.shortTitle(fork.title),'🍴 fork job');
  assert.deepEqual(f.el('sessions-list').children.map(g=>headingText(g.children[0])),['Favorite programs · 0',QUICK_LABEL+' · 2','Other · 1']);
  assert.deepEqual(find(sidebarGroup(f,QUICK_LABEL),'name-text').map(n=>n.textContent),['elsewhere','🍴 fork job']);
  answer={schemaVersion:1,backend:'opencode',status:'unavailable',error:'OpenCode unreachable: refused',sessions:[]};await f.api.ocList();
  const quick=sidebarGroup(f,QUICK_LABEL);
  assert.match(quick.textContent,/OpenCode unavailable: OpenCode unreachable: refused · OpenCode rows show their last known state/);
  assert.match(find(quick,'oc-item')[0].textContent,/OpenCode unavailable \(last known\)/);
  assert.doesNotMatch(sidebarGroup(f,'Other').textContent,/OpenCode unavailable/);
});
test('a forklift sits in its own Forklifts section right after Quick workers, its program placed exactly as without it; a quick fork worker keeps 🍴; sub-session prefixes go',async()=>{
  const f0=fixture();
  assert.equal(f0.api.shortTitle('Manager-Session-1 ▸ 🚜 forklift · X'),'🚜 X');
  assert.equal(f0.api.shortTitle('Manager-Session-084-b ▸ 🚜 forklift · X'),'🚜 X');
  assert.equal(f0.api.shortTitle('Manager-Session-084-b ▸ ⚡ quick fork worker · Y'),'🍴 Y');
  const forklift=seat(910,{kind:'seat',quick:true,title:'Manager-Session-084-b ▸ 🚜 forklift · launch X'},{createdAt:8000});
  const fork=seat(911,{kind:'seat',quick:true,title:'Manager-Session-219 ▸ ⚡ quick fork worker · fix Y'},{createdAt:8100});
  const conductor=cond(912,'PROGFK',1);   // its host labels carry launched_by = the forklift; the pane never links them
  const f=fixture(sessionsOf(forklift,fork,conductor));await f.api.list();
  const headings=f.el('sessions-list').children.map(g=>headingLabel(g.children[0]));
  assert.equal(headings.indexOf('Forklifts'),headings.indexOf(QUICK_LABEL)+1,headings.join(' | '));
  assert.equal(sidebarGroup(f,'Forklifts').children[0].title,'Forks of a Manager that prepare and launch one program (🚜). The program itself is listed under Quick or Full programs, exactly as without a forklift.');
  assert.deepEqual(find(sidebarGroup(f,'Forklifts'),'name-text').map(n=>n.textContent),['🚜 launch X']);
  assert.deepEqual(find(sidebarGroup(f,QUICK_LABEL),'name-text').map(n=>n.textContent),['🍴 fix Y']);
  const tree=f.api.state.tree;
  assert.equal(tree.byId.get(forklift.executionId).children.length,0,'no edge from a forklift to its program');
  assert.equal(tree.programs.length,1);assert.deepEqual(Array.from(tree.programs[0].top,x=>x.id),[conductor.executionId]);
  f.el('sessions-role').value='quick-worker';f.el('sessions-role').onchange();   // flattened: each row under its breadcrumb
  assert.deepEqual(find(f.el('sessions-list'),'session-crumb').map(n=>n.textContent).sort(),['Forklifts ▸','Quick workers ▸']);
});
test('active-only Quick workers hides quiet entries and keeps a once-active freestanding tree until it is turned off',async()=>{
  const r=forestRows();r.ocHelper.busy=true;
  const f=forestFixture(r);await f.api.list();await f.api.ocList();
  sectionControl(f,QUICK_LABEL,'section-active').onclick({stopPropagation(){}});
  const home=()=>sidebarGroup(f,QUICK_LABEL),names=()=>find(home(),'name-text').map(n=>n.textContent);
  assert.deepEqual(find(home(),'session-stub').map(n=>n.textContent),['↑ Claude lead · shown at top'],'the active tree is in Running now');
  assert.deepEqual(names(),[],'quiet entries are hidden');
  r.ocHelper.busy=false;await f.api.ocList();
  assert.deepEqual(names(),['[ap:SOCKCC] · Claude lead','Claude sweep','OpenCode sweep'],'once shown it stays, opened as first shown');
  sectionControl(f,QUICK_LABEL,'section-active').onclick({stopPropagation(){}});
  assert.equal(names().length,7,'turning it off restores the whole section');
});
test('Dan: a GPT (OpenCode) session shows a phase line under each run (wait · think · write · tools, other only when non-trivial), one per run, from the moments\' column',async()=>{
  const LEGACY_GPT_LEGEND='GPT · each run\'s second line = minutes and shares of wall time (oc.sh telemetry --phases over that run alone): wait = for the first output · think = thinking · write = writing tool calls · tools = own tool execution · helpers = blocking seat waits + idle gaps tied to a helper-wait completion · idle = unmatched gaps · other = inside-step text, tails and unknown time (idle/other shown from 5%). Active shares below exclude helpers and idle; concurrent own work is never helper idle.';
  const fu=(n,full,short,over={})=>teleSeg(full,short,{key:'k#'+n,segment:n,kind:'follow_up',label:'+'+n+' follow-up',line:'+'+n+' follow-up · '+full,...over});
  const stream=(line,full)=>({line,full,cli:'oc.sh telemetry --sid ses_x --phases'});
  const GPT1='wall · wait 2.4m 7% · think 1.6m 5% · write 2.5m 8% · tools 1.4m 4% · helpers 24m 75%',GPT2='wall · wait 15m 10% · think 23m 15% · write 13m 9% · tools 95m 65% · helpers 0.1m 0% · idle 9.0m 6%';
  const p0=teleProgram(()=>null),ocId=p0.oc.id;
  const sessions={[cc(2)]:teleEntry(cc(2),[teleSeg(IMPL_FULL,IMPL_SHORT)],{streamPhases:false}),
    [ocId]:teleEntry(ocId,[teleSeg('start-up 97.0 KT · edit@— · report@200 KT · end 222 KT · 1/12/—/19 min · ⚑0','97.0 (1) → — (12) → 200 → 222 KT (19 min) ⚑0',{stream:stream(GPT1,'phases · wall 31.5 min · first run in full')}),
      fu(1,'carried 222 KT · src@243 KT · edit@254 KT · report@577 KT · end 617 KT · 1/1/135/11 min · ⚑1','243 (1) → 254 (1) → 577 (135) → 617 KT (11 min) ⚑1',{stream:stream(GPT2,'phases · wall 147.7 min · second run in full')})],{unit:'U02',streamPhases:true})};
  const p=teleProgram(()=>teleAnswer(sessions)),f=p.f;
  await f.api.list();await f.api.ocList();await f.flush();await f.flush();await f.flush();
  const gpt=find(find(f.el('sessions-list'),'oc-item')[0],'session-tele')[0];
   assert.deepEqual(runsOf(gpt).length,2,'two runs, two chain lines');
   assert.deepEqual(gpt.children.map(n=>n.className),['tele-run','tele-phases','tele-run tele-resumed','tele-phases'],'each run\'s phase line directly under its own line');
   assert.deepEqual(find(gpt,'tele-phases').map(n=>[n.textContent,n.dataset.run]),[[GPT1,'0'],[GPT2,'1']]);
    assert.deepEqual(find(find(gpt,'tele-phases')[1],'tele-phase').map(n=>n.textContent),['wall','wait 15m 10%','think 23m 15%','write 13m 9%','tools 95m 65%','helpers 0.1m 0%','idle 9.0m 6%'],'one unbreakable part per phase, including helpers and unmatched idle');
   assert.equal(gpt.attributes['--tele-cols'],'9','the phase lines add no grid columns');
    assert.equal(gpt.title.split('\n').find(l=>l.startsWith('GPT ·')),LEGACY_GPT_LEGEND,'the GPT legend is byte-identical to the one before Claude had a line');
    assert.match(gpt.title,/Active shares below exclude helpers and idle/);
   assert.match(gpt.title,/\n.*1\/12\/—\/19 min · ⚑0\n  phases · wall 31\.5 min · first run in full\n.*\n  phases · wall 147\.7 min · second run in full/);
  const noStream=find(p.row('U01'),'session-tele')[0];
   assert.equal(find(noStream,'tele-phases').length,0,'a run with no stream record (this Claude entry declares none): no phase line');
   assert.doesNotMatch(noStream.title,/GPT ·|Claude ·/);
});
// A Claude run's second line comes from its host's retained stream records: the same per-run layout and splitter, the
// backend's own honest labels (never GPT's), its own legend, and every buffered/missing/native clock named in the long form.
test('Dan: a Claude session shows its own phase line under each run from the host stream, with its own legend and the buffered/native clocks in the full tooltip',async()=>{
  const line='wall · wait 0.2m 1% · think 3.2m 18% · write 0.4m 2% · tools 12.0m 66% · helpers 1.2m 7%';
  const full='phases · wall 18.2 min · wait for first output 0.3 min (1.6% wall) · thinking 3.2 min (17.6% wall) · writing tool calls 0.4 min (2.2% wall) · tool execution 12.0 min (65.9% wall) · helpers: blocking 1.2 min, matched background idle 0.0 min · active 16.7 min: wait 1.8%, think 19.2%, write 2.4%, tools 76.6% · 5/6 steps complete · native transcript clock for 1 result (no host span) · buffered writing burst at step 4 (the wait may hide it) · = oc.sh telemetry --sid '+cc(2)+' --phases';
  const sessions={[cc(2)]:teleEntry(cc(2),[teleSeg(IMPL_FULL,IMPL_SHORT,{stream:{line,full,cli:'oc.sh telemetry --sid '+cc(2)+' --phases'}})],{streamPhases:true})};
  const caveats=/\n  phases · wall 18\.2 min · .*native transcript clock for 1 result \(no host span\) · buffered writing burst at step 4 \(the wait may hide it\)/;
  const p=teleProgram(()=>teleAnswer(sessions)),f=p.f;
  await f.api.list();await f.flush();await f.flush();await f.flush();
  const tele=find(p.row('U01'),'session-tele')[0],phases=find(tele,'tele-phases')[0];
   assert.deepEqual(tele.children.map(n=>n.className),['tele-run','tele-phases'],'the phase line sits directly under its own run, through the same renderer as GPT');
   assert.equal(phases.textContent,line);
   assert.deepEqual(find(phases,'tele-phase').map(n=>n.textContent),['wall','wait 0.2m 1%','think 3.2m 18%','write 0.4m 2%','tools 12.0m 66%','helpers 1.2m 7%'],'the backend\'s Claude labels arrive unchanged; the UI adds none of its own');
   assert.equal(tele.attributes['--tele-cols'],'9','the grid keeps its columns: mark, four moments, three arrows, ⚑');
   assert.match(tele.title,/\nClaude · each run's second line = minutes and shares of wall time from the host's retained stream records/);
   assert.match(tele.title,/think = the native thinking window \(a thinking block's open to close, narration excluded: a span, not pure compute\)/);
   assert.match(tele.title,/write = observed tool-block start to input stop \(a buffered burst can hide earlier generation\)/);
   assert.match(tele.title,/tools = tool dispatch to result/);
   assert.doesNotMatch(tele.title,/GPT ·/,'a Claude session never gets the GPT legend');
   assert.match(tele.title,caveats,'the native-transcript and buffered clocks are named in the long form, not guessed');
  // Phone: the same long form is the tap view's text, so the caveats are never hover-only.
  const q=teleProgram(()=>teleAnswer(sessions),{sidebarOnly:true}),g=q.f;
  await g.api.list();await g.flush();await g.flush();await g.flush();
  find(q.row('U01'),'session-tele')[0].onclick({stopPropagation(){}});
  const detail=find(q.row('U01'),'tele-detail')[0];
   assert.match(detail.textContent,caveats);
   assert.match(detail.textContent,/\nClaude · each run's second line =/);
});
test('Dan: DeepSeek helper rows retain separate blocking tests/refresh and concurrent background figures on desktop and phone',async()=>{
  const line='wall · tools 40m 50% (tests 12m; refresh 8m; other 20m) · tests in background 30m concurrent · refreshes in background 14m concurrent';
  const full='blocking tests 12m · blocking map refreshes 8m · tests in background 30m concurrent · refreshes in background 14m concurrent';
  for(const sidebarOnly of [false,true]) {
    const base=teleProgram(()=>null),sid=base.oc.id;
    const sessions={[sid]:teleEntry(sid,[teleSeg('context 20 → 40 KT · 80 min','context 20 → 40 KT · 80 min',{stream:{line,full}})],{backend:'opencode',role:'HELPER',phases:false,streamPhases:true})};
    const p=teleProgram(()=>teleAnswer(sessions),{sidebarOnly}),f=p.f;
    await f.api.list();await f.api.ocList();await f.flush();await f.flush();await f.flush();
    const tele=find(find(f.el('sessions-list'),'oc-item')[0],'session-tele')[0];
    assert.equal(find(tele,'tele-phases')[0].textContent,line);
    assert.match(tele.title,/OpenCode helper ·/);
    assert.match(tele.title,/Tests in background and refreshes in background are separate concurrent clocks, not blocking cost/);
    assert.match(tele.title,/blocking tests 12m · blocking map refreshes 8m/);
  }
});
test('Dan: a transcript-only Claude run says its phases are unavailable in place of the line, and hover/tap explains why instead of inventing starts',async()=>{
  const line='phases unavailable · no host stream recording';
  const full='phases unavailable · this run has no host stream recording (its transcript is the only record): waiting for the first output, thinking, writing and tool time are not measured, not guessed.';
  const sessions={[cc(2)]:teleEntry(cc(2),[teleSeg(IMPL_FULL,IMPL_SHORT,{stream:{line,full,cli:'oc.sh telemetry --sid '+cc(2)+' --phases',available:false}})],{streamPhases:true})};
  const p=teleProgram(()=>teleAnswer(sessions)),f=p.f;
  await f.api.list();await f.flush();await f.flush();await f.flush();
  const tele=find(p.row('U01'),'session-tele')[0],phases=find(tele,'tele-phases')[0];
   assert.equal(phases.textContent,line);
   assert.deepEqual(find(phases,'tele-phase').map(n=>n.textContent),['phases unavailable','no host stream recording']);
   assert.equal(phases.dataset.available,'false','the line marks an absence, never a measurement');
   assert.match(tele.title,/\n  phases unavailable · this run has no host stream recording \(its transcript is the only record\): waiting for the first output, thinking, writing and tool time are not measured, not guessed\./);
   assert.match(tele.title,/\nClaude · each run's second line =/);
  const q=teleProgram(()=>teleAnswer(sessions),{sidebarOnly:true}),g=q.f;
  await g.api.list();await g.flush();await g.flush();await g.flush();
  find(q.row('U01'),'session-tele')[0].onclick({stopPropagation(){}});
   assert.match(find(q.row('U01'),'tele-detail')[0].textContent,/\n  phases unavailable · this run has no host stream recording/);
});
test('Claude resume generations draw separate phase lines and preserve the tests/other tool chip and both CLI commands',async()=>{
  const runs=[1,2].map(n=>({generation:n,line:'g'+n+' · wall · wait 0.2m 2% · think window 2.0m 20% · write stream 1.0m 10% · tools elapsed 6.0m 60% (tests 5.0m; other 1.0m)',full:'Claude g'+n+' · native_buffered_tool_input · mixed_test_work · tools split: tests 5.0 min, other tools 1.0 min · = oc.sh telemetry --sid '+cc(2)+' --phases --since generation-'+n,cli:'oc.sh telemetry --sid '+cc(2)+' --phases --since generation-'+n}));
  const sessions={[cc(2)]:teleEntry(cc(2),[teleSeg(IMPL_FULL,IMPL_SHORT,{stream:{runs,line:runs[0].line,full:'outer summary is not drawn twice'}})],{streamPhases:true})};
  const p=teleProgram(()=>teleAnswer(sessions)),f=p.f;
  await f.api.list();await f.flush();await f.flush();await f.flush();
  const tele=find(p.row('U01'),'session-tele')[0],lines=find(tele,'tele-phases');
  assert.deepEqual(lines.map(n=>n.textContent),runs.map(r=>r.line));
  assert.deepEqual(lines.map(n=>n.dataset.generation),['1','2']);
  assert.deepEqual(lines.map(n=>find(n,'tele-phase').at(-1).textContent),['tools elapsed 6.0m 60% (tests 5.0m; other 1.0m)','tools elapsed 6.0m 60% (tests 5.0m; other 1.0m)']);
  assert.match(tele.title,/generation-1/);assert.match(tele.title,/generation-2/);
  assert.match(tele.title,/Mixed test\/work shells stay other tools and are flagged/);
  assert.doesNotMatch(tele.title,/outer summary is not drawn twice/);
});
