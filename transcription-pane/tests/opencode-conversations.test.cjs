// U2 · OpenCode conversations in the one viewer: production sessions.js in a Node DOM double, U1's frozen
// schema-1 page (opencode-conversation-events.json) and an offline U1 route double. No live pane or OpenCode.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(process.env.HISTORY_TEST_SOURCE || path.join(__dirname,'../static/sessions.js'),'utf8');
const core=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-preview-core.js'),'utf8');
const shared=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-record-text.js'),'utf8');
const reading=fs.readFileSync(path.join(__dirname,'../static/tool-reading.js'),'utf8');
const PAGE=JSON.parse(fs.readFileSync(path.join(__dirname,'opencode-conversation-events.json'),'utf8'));
const SID=PAGE.sessionRef,EPOCH=PAGE.fileEpoch,CHILD='ses_child000001';
const clone=v=>JSON.parse(JSON.stringify(v));
const env=(fields={})=>({schemaVersion:1,backend:'claude',...fields});
// Claude-only routes no OpenCode selection may ever reach (independent inventory reads are allowed and counted apart).
const FORBIDDEN=/^\/api\/claude\/(events|subagents|rename|state|send|control|reconnect|resolve-incident|inspect)/;

// N real-data-shaped rows: U1's own row shapes cycled with fresh identities and dense positions.
function rows(n,from=0) {
  return Array.from({length:n},(_,k)=>{const i=from+k,t=clone(PAGE.events[i%PAGE.events.length]);
    return {...t,recordRef:'r'+i,uuid:'r'+i,seq:i,calls:t.calls.map(c=>({...c,callRef:'c'+i+'_'+c.block,id:c.id+'_'+i,messageId:'m'+i}))};});
}
// The U1 events/inspect routes, offline: newest ≤100 rows without a cursor, fromSeq/forward cursors, deltas with
// replacement rows, an index-local epoch (stale → 409) and a cursor that requires the epoch.
function ocServer(sessions) {
  const srv={sessions,reads:[],inspects:[],fail:null,ack:null};
  const reply=(code,status,error)=>new Response(JSON.stringify({schemaVersion:1,backend:'opencode',status,error}),{status:code});
  srv.handle=(url,body)=>{
    const u=new URL(url,'http://pane'),q=Object.fromEntries(u.searchParams);
    if(u.pathname==='/api/opencode/inspect'){srv.inspects.push(body);return srv.ack?srv.ack(body):undefined;}
    if(u.pathname!=='/api/opencode/events')return undefined;
    srv.reads.push(q);
    if(srv.fail){const f=srv.fail;if(f.once)srv.fail=null;return reply(f.code,f.status,f.error);}
    const s=sessions[q.id];if(!s)return reply(404,'missing','OpenCode session missing');
    if(q.cursor && (!q.fileEpoch || q.fromSeq!==undefined))return reply(400,'schema','cursor requires epoch and excludes fromSeq');
    if(q.fileEpoch && q.fileEpoch!==s.epoch)return reply(409,'stale-epoch','OpenCode index discarded/rebuilt; resync the same session by position');
    const all=s.rows,n=all.length;let events,change='snapshot',updates=[],start;
    if(q.cursor?.startsWith('d_')){start=Number(q.cursor.slice(2));events=all.slice(start,start+100);change='append';updates=s.updates.splice(0);}
    else if(q.cursor?.startsWith('f_'))start=Number(q.cursor.slice(2));
    else if(q.fromSeq!==undefined)start=Number(q.fromSeq);
    else start=Math.max(0,n-100);
    events ??= all.slice(start,start+100);
    const end=start+events.length,hasMore=change==='snapshot' && end<n;
    return {schemaVersion:1,backend:'opencode',source:'opencode',sessionRef:q.id,scopeRef:'main',fileEpoch:s.epoch,events:clone(events),eventUpdates:clone(updates),
      hasMore,nextCursor:hasMore?'f_'+end:null,deltaCursor:'d_'+(change==='append'?Math.max(end,n):n),rowsAhead:end<n,status:'complete',change,busy:s.busy,
      sessionInfo:{id:q.id,title:s.title ?? q.id,agent:s.agent ?? null,model:s.model ?? null,directory:s.directory ?? null,parentId:s.parentId ?? null},
      leafRef:null,targetRef:null,indexLimit:null,branches:[],traces:[],summaryUpdates:[],subagentActivity:[],diagnostics:[],...(s.extra || {})};
  };
  return srv;
}
const fixturePage=()=>({rows:clone(PAGE.events),epoch:EPOCH,busy:true,updates:[],title:PAGE.sessionInfo.title,agent:'quick-worker',model:'recorded-session-model',directory:'/fixture/workspace',parentId:'ses_parent00001'});
const ocRow=(id,extra={})=>({id,program:null,kind:'seat',quick:true,title:'⚡ quick worker · '+id,busy:true,model:'openrouter/deepseek/deepseek-v4.1-flash',cost:0.01,agent:'quick-worker',directory:'/fixture/workspace',createdAt:1000,...extra});

function fixture({claude,oc,ocRows=[],phone=false,embedded=true,glance,activity}={}) {
  const elements=new Map(),requests=[],messages=[],timers=new Map();let time=100000,timerId=0;
  class Node {
    constructor(tag='div'){this.tag=tag;this.children=[];this.parentNode=null;this.dataset={};this.attributes={};this.scrollTop=0;this.clientHeight=100;this.hidden=false;this.value='';this.content='';this.style={setProperty:(k,v)=>this.attributes[k]=v,removeProperty:k=>delete this.attributes[k]};
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
  const parent={postMessage:m=>messages.push(m)},origin='http://127.0.0.1:9911';
  const doc={hidden:false,getElementById:el,createElement:t=>new Node(t),createDocumentFragment:()=>new Node('fragment'),body:phone?{dataset:{view:'programs'}}:undefined,
    defaultView:embedded?{frameElement:{id:'claude-sessions-frame'},parent,location:{origin},addEventListener(){}}:null};
  const ctx={TextEncoder,TextDecoder,AbortController,URLSearchParams,URL,Response,Date:class extends Date{static now(){return time;}},
    setTimeout:(f,ms)=>{if(ms===0){queueMicrotask(f);return 0;}timers.set(++timerId,f);return timerId;},clearTimeout:i=>timers.delete(i),setInterval(){},
    BroadcastChannel:class{postMessage(m){messages.push(m);}},fetch:async(url,options)=>{
      const body=options?.body?JSON.parse(options.body):null;requests.push([url,options?.method || 'GET',body]);
      let data;
      if(url.split('?')[0]==='/api/pins')return new Response(JSON.stringify({schemaVersion:1,pins:{}}));
      if(url.split('?')[0]==='/api/opencode/programs')data={schemaVersion:1,backend:'opencode',status:'complete',sessions:ocRows};
      else if(url==='/api/opencode/activity')data=await activity?.() ?? {schemaVersion:1,backend:'opencode',status:'complete',observedAt:time/1000,active:ocRows.filter(r=>r.busy).map(r=>r.id),sessions:[]};
      else if(url.startsWith('/api/opencode/glance?'))data=glance?.(url) ?? {schemaVersion:1,backend:'opencode',status:'complete',id:new URL(url,'http://p').searchParams.get('id'),messages:[]};
      else if(url.startsWith('/api/opencode/'))data=await oc?.handle(url,body);
      else data=await claude?.(url,body);
      if(data===undefined){if(url==='/api/tool-selection')data=box;else if(url.startsWith('/api/claude/sessions'))data=env({sessions:[],hasMore:false,status:'complete'});
        else if(url.startsWith('/api/opencode/turns'))data={schemaVersion:1,backend:'opencode',status:'unavailable',id:'x',error:'offline'};
        else throw new Error('unexpected offline request '+url);}
      return data instanceof Response?data:new Response(JSON.stringify(data));}};
  vm.runInNewContext(shared+'\n'+core+'\n'+reading+'\n'+source.replace("if(typeof document!=='undefined'", "if(false && typeof document!=='undefined'"),ctx);
  const api=ctx.opcClaudeHistory.create(doc);
  const flush=async(n=3)=>{for(let i=0;i<n;i++)await new Promise(r=>setImmediate(r));};
  return {api,ctx,el,requests,messages,timers,parent,origin,box:value=>box=value,advance:(ms=3001)=>time+=ms,now:()=>time,flush,
    fireTimers:async()=>{for(const [k,f] of [...timers]){timers.delete(k);f();}await flush();}};
}
const find=(node,cls)=>{const out=[];const walk=n=>{if(n.classList?.contains(cls))out.push(n);for(const c of n.children||[])walk(c);};walk(node);return out;};
const resident=f=>Array.from(f.api.state.order,k=>f.api.state.rows.get(k)); // a test-realm array (the factory runs in its own vm realm)
const roleText=row=>row.node.children[0].children[0].textContent;
const ocReads=f=>f.requests.filter(([u])=>u.startsWith('/api/opencode/events')).map(([u])=>Object.fromEntries(new URL(u,'http://p').searchParams));
const claudeForbidden=(f,from=0)=>f.requests.slice(from).filter(([u])=>FORBIDDEN.test(u)).map(([u])=>u);
async function openListed(f) {
  await f.api.ocList();const item=find(f.el('sessions-list'),'oc-item')[0];
  item.children[0].onclick();await f.flush();return item;
}

// ---------------------------------------------------------------- 1 · sidebar row → the same viewer; chip → glance
test('desktop OpenCode row click opens its whole conversation in the same viewer at its newest rows; the 💬 chip alone is the glance',async()=>{
  const srv=ocServer({[SID]:fixturePage()}),f=fixture({oc:srv,ocRows:[ocRow(SID,{title:'[ap:QQQQQQ]-001 ▸ ⚡ quick worker · handoff'})]});
  await openListed(f);
  const s=f.api.state.session;
  assert.equal(s.backend,'opencode');assert.equal(s.source,'opencode');assert.equal(s.id,SID);assert.equal(s.sessionRef,SID);
  assert.deepEqual(clone(s.scopes),[{label:'main',scopeRef:'main'}]);assert.deepEqual(clone(s.capabilities),{history:false,nativeInput:false,controls:false,subagents:false,reconnect:false});
  for(const key of ['targetRef','executionId','generation','uuid','parentUuid','agentId','lifecycle','liveState','hostTwin','successor'])assert.equal(s[key],undefined,key+' is never fabricated');
  assert.deepEqual(ocReads(f),[{id:SID}],'one tail read with only the OpenCode id: no scope, leaf or target key');
  assert.equal(f.requests.filter(([u])=>u.startsWith('/api/opencode/glance')).length,0,'a row click is not a glance');
  assert.equal(resident(f).length,26);assert.deepEqual(resident(f).map(r=>r.seq),[...Array(26).keys()]);
  assert.equal(f.api.state.follow,true);assert.equal(f.api.state.epoch,EPOCH);
  const item=find(f.el('sessions-list'),'oc-item')[0];
  assert.equal(item.classList.contains('selected'),true);assert.equal(item.children[0].getAttribute('aria-pressed'),'true');
  assert.match(item.children[0].title,/Click to read its whole conversation here \(view only\); 💬 opens a quick glance under this row\./);
  // Header: OpenCode, the recorded title, the ses_ id with Copy, Open in CodeNomad for its folder.
  const head=f.el('oc-header');assert.equal(head.hidden,false);
  assert.match(head.textContent,/^OpenCode/);assert.match(head.textContent,/OpenCode handoff fixture/);assert.match(head.textContent,new RegExp(SID));
  assert.equal(find(head,'oc-header-copy').length,1);assert.equal(find(head,'focus-open')[0].disabled,false);
  assert.match(f.el('ribbon-session').title,/OpenCode session ses_fixture0001 · view only/);
  assert.equal(f.el('history-activity').textContent,'read 0 s ago · view only');assert.equal(f.el('history-activity').dataset.state,'busy');
  // The chip toggles the glance without browsing, targeting or reading the conversation again.
  const before={session:f.api.state.session,generation:f.api.state.generation,reads:ocReads(f).length,messages:f.messages.length};
  const chip=()=>find(find(f.el('sessions-list'),'oc-item')[0],'oc-glance-chip')[0];
  chip().onkeydown({key:'Enter',preventDefault(){},stopPropagation(){}});await f.flush();
  assert.equal(chip().getAttribute('aria-expanded'),'true');assert.equal(find(f.el('sessions-list'),'oc-glance').length,1);
  assert.match(find(f.el('sessions-list'),'oc-glance')[0].textContent,/whole conversation: click the row/);
  chip().onclick({stopPropagation(){}});await f.flush();assert.equal(find(f.el('sessions-list'),'oc-glance').length,0);
  assert.equal(f.api.state.session,before.session);assert.equal(f.api.state.generation,before.generation);assert.equal(ocReads(f).length,before.reads);assert.equal(f.messages.length,before.messages);
  assert.deepEqual(claudeForbidden(f),[]);
});

test('a bare unlisted child ses_ opens; sessionInfo fills its header; Copy and Open in CodeNomad send the exact focus request; a missing folder is visibly unavailable',async()=>{
  const child={...fixturePage(),rows:rows(3),title:'child title',directory:'/child/dir',parentId:SID};
  const srv=ocServer({[CHILD]:child,ses_nofolder01:{...fixturePage(),rows:rows(2),directory:null,title:null}}),posts=[];
  const f=fixture({oc:srv,claude:(url,body)=>{if(url==='/api/focus-request' && body){posts.push(body);return {schemaVersion:1,request:{nonce:body.nonce,state:'pending',missingTab:null,noPage:false,outcome:null}};}
    if(url==='/api/focus-request')return {schemaVersion:1,request:{nonce:posts.at(-1).nonce,state:'done',outcome:{code:'done'}}};}});
  await f.api.openOpenCode(CHILD);await f.flush();
  const s=f.api.state.session;assert.equal(s.id,CHILD);assert.equal(s.title,'child title');assert.equal(s.directory,'/child/dir');assert.equal(s.parentId,SID);
  assert.equal(resident(f).length,3);assert.match(f.el('oc-header').textContent,/child title.*ses_child000001/s);
  // Its identity is its ses_ id, decided before any Claude role logic: two OpenCode sessions never collapse into one identity.
  assert.equal(f.api.identityOf(s),'opencode:'+CHILD);assert.equal(f.api.isSelected(f.api.openCodeSession('ses_nofolder01')),false);
  assert.equal(f.api.isSelected({sessionRef:CHILD,uuid:'u',source:'windows-history',scopes:[]}),false,'a Claude row never matches an OpenCode selection');assert.match(f.el('oc-header').textContent,/started by ses_fixture0001/);
  const before={session:f.api.state.session,generation:f.api.state.generation};
  find(f.el('oc-header'),'focus-open')[0].onclick({stopPropagation(){}});await f.flush();
  assert.deepEqual({...posts[0],nonce:undefined},{sessionId:CHILD,directory:'/child/dir',title:'child title',parentId:SID,nonce:undefined});
  assert.match(find(f.el('oc-header'),'focus-status')[0].textContent,/waiting for CodeNomad/);
  await f.api.focusPoll();assert.match(find(f.el('oc-header'),'focus-status')[0].textContent,/✓ shown in CodeNomad/);
  assert.equal(f.api.state.session,before.session);assert.equal(f.api.state.generation,before.generation);assert.equal(f.messages.filter(m=>m.type==='target').length,0);
  await f.api.openOpenCode('ses_nofolder01');await f.flush();
  assert.equal(find(f.el('oc-header'),'focus-open')[0].disabled,true);assert.match(f.el('oc-header').textContent,/folder not recorded · Open in CodeNomad unavailable/);
  assert.equal(f.api.state.session.title,'ses_nofolder01','an unknown title is the bare id');assert.equal(resident(f).length,2,'a missing folder never refuses the conversation');
  await f.api.openOpenCode('not-a-session');assert.equal(f.api.state.session.id,'ses_nofolder01','an invalid id opens nothing');
});

// ---------------------------------------------------------------- 2 · backend gate, address, First, long traversal
test('the expected-backend gate: an OpenCode page claiming another backend is refused visibly; a Claude page still refuses OpenCode',async()=>{
  const srv=ocServer({[SID]:fixturePage()});const handle=srv.handle;srv.handle=(u,b)=>{const d=handle(u,b);return d instanceof Response?d:{...d,backend:'claude'};};
  const f=fixture({oc:srv});await f.api.openOpenCode(SID);await f.flush();
  assert.equal(resident(f).length,0);assert.equal(f.api.state.blocked,true);assert.match(f.el('history-status').textContent,/wrong history schema\/backend/);
  const claude={sessionRef:'c1',uuid:'u1',title:'Claude',source:'windows-history',scopes:[{scopeRef:'s1',label:'main'}]};
  const g=fixture({claude:url=>url.startsWith('/api/claude/events')?{schemaVersion:1,backend:'opencode',sessionRef:'c1',scopeRef:'s1',fileEpoch:'e',events:[],status:'complete',hasMore:false}:undefined});
  await g.api.browse(claude);await g.flush();assert.match(g.el('history-status').textContent,/wrong history schema\/backend/);
});

test('address carries only OpenCode keys: tail {id}, delta {id,fileEpoch,cursor}, position {id,fileEpoch,fromSeq}; ⤒ First sends fromSeq 0, never a null tail read',async()=>{
  const srv=ocServer({[SID]:{...fixturePage(),rows:rows(300)}}),f=fixture({oc:srv});
  await f.api.openOpenCode(SID);await f.flush();
  assert.deepEqual(resident(f).map(r=>r.seq),Array.from({length:100},(_,i)=>200+i),'the newest 100 rows');
  f.advance();await f.api.poll();await f.flush();
  assert.deepEqual(Object.keys(ocReads(f).at(-1)).sort(),['cursor','fileEpoch','id']);
  await f.api.loadOlder();await f.flush();
  const position=ocReads(f).find(q=>q.fromSeq!==undefined);assert.deepEqual(Object.keys(position).sort(),['fileEpoch','fromSeq','id']);assert.equal(position.fromSeq,'100');
  const before=ocReads(f).length;await f.api.toFirst();await f.flush();
  assert.deepEqual(ocReads(f).slice(before)[0],{id:SID,fromSeq:'0'},'First reads position 0 (a fresh read: no stale epoch)');
  assert.equal(resident(f)[0].seq,0);assert.equal(f.api.atStart(),true);assert.equal(f.api.state.follow,false,'First holds still at the top');
  assert.equal(roleText(resident(f)[0]),'You','it lands on the first prompt');
  for(const q of ocReads(f))for(const key of Object.keys(q))assert.ok(['id','fileEpoch','cursor','fromSeq'].includes(key),'no Claude key '+key);
  assert.ok(!ocReads(f).some(q=>q.cursor && q.fromSeq),'cursor and fromSeq never together');
  assert.deepEqual(claudeForbidden(f),[]);
});

test('a 737-row (430-message shape) session: tail, First, then ↓ through 100-row pages inside the 200-row window reaches every row once, no hole, no duplicate',async()=>{
  const N=737,srv=ocServer({[SID]:{...fixturePage(),rows:rows(N),busy:false}}),f=fixture({oc:srv});
  await f.api.openOpenCode(SID);await f.flush();assert.equal(resident(f).at(-1).seq,N-1);
  await f.api.toFirst();await f.flush();
  const seen=new Set(resident(f).map(r=>r.key));
  const contiguous=()=>{const s=resident(f).map(r=>r.seq);return s.every((v,i)=>i===0 || v===s[i-1]+1);};
  for(let i=0;i<40 && f.api.state.next;i++) {
    f.el('history-viewport').scrollTop=(f.api.state.order.length-1)*100; // Dan reads down to the last row on screen
    await f.api.loadNewer();await f.flush();
    assert.ok(contiguous(),'one contiguous resident run');assert.ok(f.api.state.order.length<=200,'the 200-row window holds');
    assert.equal(new Set(f.api.state.order).size,f.api.state.order.length,'no duplicate mounts');
    for(const r of resident(f))seen.add(r.key);
  }
  assert.equal(seen.size,N);assert.deepEqual([...seen].map(k=>Number(k.slice(1))).sort((a,b)=>a-b),[...Array(N).keys()]);
  assert.equal(resident(f).at(-1).seq,N-1);
  // ↑ from the end reads back through positions too (the window evicted the start).
  const top=resident(f)[0].seq;await f.api.loadOlder();await f.flush();assert.ok(resident(f)[0].seq<top,'older rows above remain reachable');assert.ok(contiguous());
});

test('an empty session has a delta cursor and grows by the poll; following stays pinned',async()=>{
  const s={...fixturePage(),rows:[],busy:true},srv=ocServer({[SID]:s}),f=fixture({oc:srv});
  await f.api.openOpenCode(SID);await f.flush();assert.equal(resident(f).length,0);assert.equal(f.api.state.delta,'d_0');
  s.rows.push(...rows(3));f.advance();await f.api.poll();await f.flush();
  assert.equal(resident(f).length,3);assert.equal(ocReads(f).at(-1).cursor,'d_0');
  assert.equal(f.el('history-viewport').scrollTop,f.el('history-viewport').scrollHeight,'Following pins the newest pixel');
});

// ---------------------------------------------------------------- 3 · live following and running calls
test('busy ≈1 s / idle and unknown 3 s, never the native-subagent 600 ms; a call is yellow only while busy, pending and after the last receipt',async()=>{
  const page=fixturePage(),srv=ocServer({[SID]:page}),f=fixture({oc:srv});
  await f.api.openOpenCode(SID);await f.flush();
  assert.equal(f.api.pollDelay(),1000);
  const running=()=>resident(f).flatMap(r=>r.calls).filter(c=>c.node.classList.contains('running')).map(c=>c.id);
  assert.deepEqual(running(),['prt_running'],'the pending call after the last receipt; the stored subagent call is not running');
  assert.match(find(resident(f)[25].node,'tool-title')[0].textContent,/shell · running now/);
  page.busy=false;f.advance();await f.api.poll();await f.flush();
  assert.equal(f.api.pollDelay(),3000);assert.deepEqual(running(),[],'idle never marks a call running');
  page.busy=null;f.advance();await f.api.poll();await f.flush();
  assert.equal(f.api.pollDelay(),3000);assert.deepEqual(running(),[],'unknown busy manufactures nothing');assert.equal(f.el('history-activity').dataset.state,'unknown');assert.doesNotMatch(f.el('history-activity').textContent,/activity unknown/);
  // A completed call stays completed even while busy.
  page.busy=true;const done=clone(page.rows[25]);done.calls[0].status='stored';done.calls[0].outputBytes=0;page.updates.push(done);
  f.advance();await f.api.poll();await f.flush();assert.deepEqual(running(),[]);assert.match(find(resident(f)[25].node,'tool-title')[0].textContent,/shell · stored/);
  assert.equal(find(resident(f)[25].node,'tool-size')[1].textContent,'Out stored: '+f.ctx.opcToolText.size(0),'a recorded zero stays zero');
  assert.notEqual(f.ctx.opcToolText.size(0),f.ctx.opcToolText.size(null),'and is not the unknown mark');
});

test('streaming → running → stored on the same call, partial → final text and loading → readable reasoning replace in place: same nodes, keys and paused pixel',async()=>{
  const page=fixturePage(),srv=ocServer({[SID]:page}),f=fixture({oc:srv});
  page.rows[25].calls[0].status='not-final-fragment';
  await f.api.openOpenCode(SID);await f.flush();
  const call=resident(f)[25].calls[0],callNode=call.node,think=resident(f)[23],thinkNode=think.node,text=resident(f)[24],textNode=text.node;
  assert.match(callNode.children[0].textContent,/streaming \(not-final fragment\)/);
  assert.equal(think.node.classList.contains('event-quiet'),true);assert.equal(roleText(think),'Thinking · loading');
  f.api.setFollow(false,{byScroll:true});f.el('history-viewport').scrollTop=700;
  const r25=clone(page.rows[25]);r25.calls[0].status='pending-or-missing';page.updates.push(r25);f.advance();await f.api.poll();await f.flush();
  assert.equal(resident(f)[25].calls[0].node,callNode);assert.match(callNode.children[0].textContent,/running now/);
  const final=clone(page.rows[25]);final.calls[0].status='stored';final.calls[0].outputBytes=2048;final.partial=false;
  const reasoning={...clone(page.rows[23]),partial:false,originKind:null,thinking:[{blockIndex:0,availability:'stored',streaming:false,text:'Now readable.'}]};
  const prose={...clone(page.rows[24]),partial:false,text:'stored current block, **final**'};
  page.updates.push(final,reasoning,prose);f.advance();await f.api.poll();await f.flush();
  const rowsNow=resident(f);
  assert.equal(rowsNow[25].calls[0].node,callNode);assert.match(callNode.children[0].textContent,/shell · stored/);assert.match(find(callNode,'tool-size')[1].textContent,/Out stored: 2\.0 KiB/);
  assert.equal(rowsNow[23].node,thinkNode,'the quiet marker is replaced in place under the same identity');assert.equal(thinkNode.classList.contains('event-quiet'),false);
  assert.equal(roleText(rowsNow[23]),'OpenCode · thinking');assert.match(thinkNode.textContent,/Thinking.*Now readable\./s);
  assert.equal(rowsNow[24].node,textNode);assert.match(textNode.textContent,/final/);
  assert.equal(f.el('history-viewport').scrollTop,700,'paused: the reading position never moves');assert.equal(f.api.state.order.length,26,'no duplicate rows');
});

// ---------------------------------------------------------------- 4 · every mapping kind labelled
test('U1\'s frozen 26-row page: every row is present and labelled; quiet markers instead of blank cards; own model/variant; receipts with reasoning tokens; unknown is — and zero is 0; never "Claude"',async()=>{
  const srv=ocServer({[SID]:fixturePage()}),f=fixture({oc:srv});await f.api.openOpenCode(SID);await f.flush();
  const r=resident(f),label=i=>roleText(r[i]),cls=(i,c)=>r[i].node.classList.contains(c);
  assert.deepEqual(r.map(x=>x.seq),[...Array(26).keys()],'every stored message/part has its row, in order');
  assert.equal(label(0),'You');assert.ok(cls(0,'turn-user'));
  assert.equal(label(1),'OpenCode · thinking');assert.match(find(r[1].node,'thinking-name')[0].textContent,/^Thinking$/,'stored reasoning, not a "summary"');
  assert.equal(label(2),'Thinking · encrypted; no readable text');assert.ok(cls(2,'event-quiet'));assert.doesNotMatch(r[2].node.textContent,/encrypted-content|reasoningEncrypted/);
  assert.equal(label(3),'Thinking · empty');assert.ok(cls(3,'event-quiet'));
  for(const i of [2,3])assert.equal(r[i].model.textContent,'(block-model · xhigh)','a quiet marker keeps its model and date');
  assert.match(r[2].meta.textContent,/2026|time/);
  assert.equal(label(4),'OpenCode','mid-round prose (a tool call follows)');assert.ok(cls(4,'turn-mid'));
  assert.equal(label(5),'OpenCode · tools');assert.equal(r[5].model.textContent,'(block-model · xhigh)');
  assert.match(r[5].model.title,/Recorded on this OpenCode block: block-model · variant xhigh · agent quick-worker/);
  assert.equal(label(6),'OpenCode · unknown part · future-part');assert.match(r[6].prose.textContent,/Unknown parts stay readable/);
  assert.equal(label(7),'OpenCode · tool · not inspectable (no unique part id)');assert.equal(find(r[7].node,'tool-bar').length,0,'no fake Inspect');assert.match(r[7].prose.textContent,/missing-id/);
  assert.ok(cls(8,'event-flagged'),'an empty user message after a tool stays visible (toolResults 0)');assert.ok(!cls(8,'event-silent'));assert.match(r[8].flag.textContent,/user message · no text recorded/);
  assert.equal(label(9),'OpenCode · synthetic message');assert.equal(label(10),'OpenCode · system message');assert.equal(label(11),'OpenCode · skill');
  assert.match(r[9].prose.textContent,/Full synthetic text/);
  assert.equal(label(12),'OpenCode · shell');assert.ok(cls(12,'turn-tools') && cls(12,'oc-shell') && cls(12,'has-tools'));assert.equal(r[12].calls.length,0);assert.equal(find(r[12].node,'tool-bar').length,0,'a shell MESSAGE never gets a fake Inspect');
  assert.match(r[12].prose.textContent,/command: printf hello[^]*all stored output[^]*"truncated": false/);
  for(const [i,type] of [[13,'compaction'],[14,'agent-switched'],[15,'model-switched'],[16,'location-switched'],[21,'assistant state']]) {
    assert.ok(cls(i,'event-flagged'));assert.match(r[i].flag.children[0].textContent,new RegExp('OpenCode · '+type));assert.doesNotMatch(r[i].flag.textContent,/native task/);
  }
  assert.match(r[13].flag.textContent,/Recorded summary/,'the flag disclosure holds the full detail');assert.match(r[15].flag.textContent,/new-model/);
  assert.equal(label(17),'OpenCode · answer');assert.ok(cls(17,'turn-final'));assert.equal(r[17].model.textContent,'(final-model · medium)');
  assert.equal(r[18].result.textContent,'End of turn · succeeded · list-price estimate $0.7500 (not billed spend) · tokens (last step): input 99, output 8, reasoning 7, cache read 6, cache write 5');
  assert.equal(r[22].result.textContent,'End of turn · interrupted · list-price estimate $0.0000 (not billed spend) · tokens (last step): input —, output —, reasoning —, cache read —, cache write —');
  assert.doesNotMatch(r[18].node.textContent,/Final answer/,'no duplicate final answer in the receipt');
  assert.equal(label(19),'OpenCode · unknown message type · future-message');assert.match(r[19].prose.textContent,/Unknown messages stay readable/);
  assert.equal(label(20),'Step started · no content recorded');assert.ok(cls(20,'event-quiet'),'a started empty step is a quiet marker, not a blank answer card');assert.ok(!cls(20,'turn-final'));
  assert.equal(label(23),'Thinking · loading');assert.equal(label(24),'OpenCode');assert.equal(r[24].model.textContent,'(live-model · high)');
  assert.equal(label(25),'OpenCode · tools');
  for(const row of r)assert.doesNotMatch(row.node.children[0].textContent,/Claude/,'no Claude label on an OpenCode row');
  assert.ok(r.every(x=>!x.node.hidden && !x.node.classList.contains('event-silent')),'no row is hidden or folded away');
  assert.doesNotMatch(f.el('history-source').textContent+f.el('history-capabilities').textContent+f.el('history-activity').textContent,/Claude ·|Unknown host role|history \/ context only/);
  assert.match(f.el('history-capabilities').textContent,/View only · an OpenCode conversation is never a Send, Refine or control target here/);
  // Sizes are U1's raw counts through the shared size helper: no second definition.
  const sizes=find(r[5].node,'tool-size').map(n=>n.textContent);
  assert.deepEqual(sizes,['In stored: '+f.ctx.opcToolText.size(62),'Out stored: '+f.ctx.opcToolText.size(56)]);
  assert.equal(find(r[25].node,'tool-size')[1].textContent,'Out stored: '+f.ctx.opcToolText.size(null),'unknown output stays unknown');
  // Dan: each call's total time on its collapsed row, left of its sizes, as its opened view says it (callDuration);
  // a call still running shows none.
  const time=find(r[5].node,'tool-time')[0];
  assert.equal(time.textContent,'2.0s');assert.equal(time.hidden,false);assert.equal(time.parentNode.children.indexOf(time),0,'left of the sizes');
  assert.equal(time.textContent,f.ctx.opcToolReading.callTimeText(1792390003000,1792390005000).split(' · took ')[1],'the opened view\'s own value');
  assert.equal(find(r[25].node,'tool-time')[0].textContent,'');assert.equal(find(r[25].node,'tool-time')[0].hidden,true);
});

test('limits stay visible: an over-limit row keeps its position and unavailable-body line; a 413 page is a visible refusal, not an empty view',async()=>{
  const page=fixturePage();page.rows[4]={...page.rows[4],status:'response-limit',text:null,bodyBytes:9000000,limitBytes:8388608,thinking:[],calls:[]};
  const f=fixture({oc:ocServer({[SID]:page})});await f.api.openOpenCode(SID);await f.flush();
  const r=resident(f)[4];assert.equal(roleText(r),'OpenCode · assistant · too large to show here');assert.match(r.prose.textContent,/Stored body unavailable · response-limit · 9000000 bytes/);
  const srv=ocServer({[SID]:fixturePage()});srv.fail={code:413,status:'session-limit',error:'OpenCode session exceeds 64 MiB; not cropped'};
  const g=fixture({oc:srv});await g.api.openOpenCode(SID);await g.flush();
  assert.equal(resident(g).length,0);assert.equal(g.api.state.blocked,true);assert.match(g.el('history-status').textContent,/History unavailable: OpenCode session exceeds 64 MiB; not cropped/);
  assert.match(g.el('history-activity').textContent,/last read failed: OpenCode session exceeds 64 MiB/);
});

// ---------------------------------------------------------------- 5 · Inspect and child navigation
function inspector(f,srv,{mutate=null}={}) {
  srv.ack=body=>{
    // The server derives the actual part id from its own records (the request never carries it).
    const part=Object.values(srv.sessions).flatMap(x=>x.rows).flatMap(r=>r.calls).find(c=>c.callRef===body.callRef);
    const selection={backend:'opencode',source:'opencode',origin:'pane',instanceId:'opencode-local',sessionId:body.id,messageId:body.messageId,partId:part?.id ?? null,partIndex:body.partIndex,
      callRef:body.callRef,fileEpoch:body.fileEpoch,selectorId:body.selectorId,sequence:body.sequence,observedAt:body.observedAt,association:{callId:null}};
    const data={schemaVersion:1,backend:'opencode',ok:true,epoch:'mail',revision:2+body.sequence,selection};
    if(mutate)mutate(data);f.box({schemaVersion:1,epoch:'mail',revision:2+body.sequence,selection:clone(selection)});return data;
  };
}
test('Inspect posts exactly the frozen OpenCode identity; the ACK must name that click and the shared mailbox must agree before the highlight',async()=>{
  const srv=ocServer({[SID]:fixturePage()}),f=fixture({oc:srv});inspector(f,srv);await f.api.openOpenCode(SID);await f.flush();
  const call=resident(f)[5].calls[0];call.node.onclick();await f.flush();
  assert.deepEqual(Object.keys(srv.inspects[0]).sort(),['backend','callRef','fileEpoch','id','messageId','observedAt','partIndex','schemaVersion','selectorId','sequence']);
  assert.deepEqual({...srv.inspects[0],selectorId:undefined,observedAt:undefined},{schemaVersion:1,backend:'opencode',id:SID,messageId:'msg_work',partIndex:4,
    callRef:PAGE.events[5].calls[0].callRef,fileEpoch:EPOCH,selectorId:undefined,sequence:1,observedAt:undefined});
  assert.equal(call.node.classList.contains('accepted'),true);assert.match(f.el('inspect-status').textContent,/Exact call accepted in the existing inspector; target unchanged/);
  assert.equal(f.requests.filter(([u])=>u==='/api/claude/inspect').length,0);assert.equal(f.messages.length,0,'Inspect never targets');
  // A CodeNomad or Claude click replaces it in the one mailbox: the highlight clears.
  f.box({schemaVersion:1,epoch:'mail',revision:99,selection:{backend:'codenomad',callId:'call_x'}});await f.api.mailbox();
  assert.equal(call.node.classList.contains('accepted'),false);assert.match(f.el('inspect-status').textContent,/Held call closed or replaced/);
  // A tool row's child link is separate: it opens the child and never inspects the parent call.
  const link=find(resident(f)[5].node,'oc-child-link')[0];assert.ok(link);assert.equal(link.parentNode,resident(f)[5].node);assert.notEqual(link.parentNode,call.node);
  assert.equal(find(call.node,'oc-child-link').length,0,'not nested inside the Inspect button');
  srv.sessions[CHILD]={...fixturePage(),rows:rows(2),title:'the child'};const posts=srv.inspects.length;
  link.onclick({stopPropagation(){}});await f.flush();
  assert.equal(f.api.state.session.id,CHILD);assert.equal(srv.inspects.length,posts,'the link never inspects');assert.equal(f.messages.length,0);
});

test('a wrong ACK in any identity field is refused; the latest deliberate click wins; a late ACK after browsing never highlights',async()=>{
  const fields=[['backend',d=>d.backend='claude'],['selection backend',d=>d.selection.backend='claude'],['session',d=>d.selection.sessionId='ses_other000001'],
    ['message',d=>d.selection.messageId='msg_other'],['part',d=>d.selection.partId='prt_other'],['part index',d=>d.selection.partIndex=5],['call',d=>d.selection.callRef='occall_other'],
    ['epoch',d=>d.selection.fileEpoch='oc_other'],['selector',d=>d.selection.selectorId='someone-else'],['sequence',d=>d.selection.sequence=77],['ok',d=>d.ok=false]];
  for(const [name,mutate] of fields) {
    const srv=ocServer({[SID]:fixturePage()}),f=fixture({oc:srv});inspector(f,srv,{mutate});await f.api.openOpenCode(SID);await f.flush();
    const call=resident(f)[5].calls[0];call.node.onclick();await f.flush();
    assert.equal(call.node.classList.contains('accepted'),false,name);assert.equal(call.node.classList.contains('failed'),true,name);
    assert.match(f.el('inspect-status').textContent,/Inspect not accepted: Inspect ACK identity changed · Click deliberately to retry\./,name);
    assert.equal(srv.inspects.length,1,name+': no automatic retry');
  }
  // Latest wins: two clicks, only the second is accepted.
  // Five calls on screen (rows 5, 25, 31, 51, 57).
  const srv=ocServer({[SID]:{...fixturePage(),rows:rows(60)}}),f=fixture({oc:srv});inspector(f,srv);await f.api.openOpenCode(SID);await f.flush();
  const calls=resident(f).flatMap(r=>r.calls);calls[0].node.onclick();calls[1].node.onclick();await f.flush();
  assert.equal(calls[1].node.classList.contains('accepted'),true);assert.equal(calls[0].node.classList.contains('accepted'),false);
  // A late ACK after a browse cannot accept anything.
  let release;srv.ack=body=>new Promise(r=>release=()=>r({schemaVersion:1,backend:'opencode',ok:true,epoch:'mail',revision:50,
    selection:{backend:'opencode',source:'opencode',sessionId:body.id,messageId:body.messageId,partId:calls[2].id,partIndex:body.partIndex,callRef:body.callRef,fileEpoch:body.fileEpoch,selectorId:body.selectorId,sequence:body.sequence}}));
  calls[2].node.onclick();await f.flush();srv.sessions.ses_another00001={...fixturePage(),rows:rows(2)};
  await f.api.openOpenCode('ses_another00001');release();await f.flush();
  assert.equal(f.api.state.accepted,null);assert.ok(resident(f).flatMap(r=>r.calls).every(c=>!c.node.classList.contains('accepted')));
});

// ---------------------------------------------------------------- 6 · restart / eviction / changed index → the SAME ses_
test('a pane restart (stale epoch) resyncs the SAME ses_ by position with no Claude read — even when the Claude list is down — and keeps rows, nodes and the pause',async()=>{
  const s={...fixturePage(),rows:rows(150),busy:false},srv=ocServer({[CHILD]:s});
  const f=fixture({oc:srv,claude:url=>{if(url.startsWith('/api/claude/'))return new Response(JSON.stringify({error:'down'}),{status:500});}});
  await f.api.openOpenCode(CHILD);await f.flush();const nodes=resident(f).map(r=>r.node),lo=resident(f)[0].seq;
  f.api.setFollow(false,{byScroll:true});f.el('history-viewport').scrollTop=500;
  s.epoch='oc_22222222222222222222222222222222';s.rows.push(...rows(1,150));const from=f.requests.length;
  f.advance();await f.api.poll();await f.flush();
  assert.ok(f.api.state.resync,'a 409 stale-epoch starts a resync, not a failure');
  for(let i=0;i<5 && f.api.state.resync;i++){f.advance(20000);await f.api.poll();await f.flush();}
  assert.equal(f.api.state.resync,null);assert.equal(f.api.state.session.id,CHILD);assert.equal(f.api.state.epoch,s.epoch);
  const reread=ocReads(f).find(q=>q.fromSeq!==undefined);assert.deepEqual(reread,{id:CHILD,fromSeq:String(lo)},'re-read by the oldest resident position, the old epoch dropped');
  assert.deepEqual(resident(f).slice(0,nodes.length).map(r=>r.node),nodes,'unchanged rows keep their nodes');
  assert.equal(f.api.state.follow,false);assert.equal(f.el('history-viewport').scrollTop,500);
  assert.deepEqual(claudeForbidden(f,from),[]);
  // Only the independent inventory refresh touched a Claude route, and every such read failed (500): recovery never depended on it.
  assert.ok(f.requests.slice(from).filter(([u])=>u.startsWith('/api/claude/')).every(([u])=>u.startsWith('/api/claude/sessions')));
  assert.match(f.el('history-status').textContent,/Resynced after a pane-server restart/);
});

test('Inspect on unchanged resident OpenCode tools after resync posts the current epoch and accepts the same button, including changed-index and deferred rows',async()=>{
  for(const scenario of ['stale-epoch','changed-index','deferred']) {
    const s=fixturePage(),srv=ocServer({[SID]:s}),f=fixture({oc:srv});let selection=null;
    // Reuse the fixture's DOM/transport with a selectable document; its audit-probe header stays untouched.
    const Node=f.el('history-events').constructor;
    f.api=f.ctx.opcClaudeHistory.create({getElementById:f.el,createElement:t=>new Node(t),createDocumentFragment:()=>new Node('fragment'),getSelection:()=>selection,
      defaultView:{frameElement:{id:'claude-sessions-frame'},parent:f.parent,location:{origin:f.origin},addEventListener(){}}});
    s.rows[5].text='Tool context before update';
    s.rows[5].thinking=[{blockIndex:0,availability:'stored',streaming:false,text:'Held reasoning'}];
    inspector(f,srv);await f.api.openOpenCode(SID);await f.flush();
    const row=resident(f)[5],button=row.calls[0].node,prose=row.prose,thinking=row.thinking[0].node;
    thinking.open=true;f.api.setFollow(false,{byScroll:true});f.el('history-viewport').scrollTop=400;
    button.onclick();await f.flush();assert.equal(button.classList.contains('accepted'),true);
    if(scenario==='deferred') {
      selection={isCollapsed:false,rangeCount:1,anchorNode:prose,focusNode:prose,getRangeAt:()=>({intersectsNode:n=>n===prose})};
      const changed=clone(s.rows[5]);changed.text='Tool context after update';changed.calls[0].outputBytes++;
      s.rows[5]=changed;s.updates.push(changed);f.advance();await f.api.poll();await f.flush();
      assert.ok(row.deferred,'selected replacement waits to repaint');assert.equal(prose.textContent,'Tool context before update');
    }
    s.epoch='oc_resynced_'+scenario.replaceAll('-','_');
    if(scenario==='changed-index')srv.fail={once:true,code:409,status:'changed-index',error:'index rebuilt; resident rows still match'};
    const from=ocReads(f).length;
    f.advance();await f.api.poll();await f.flush();
    for(let i=0;i<5 && f.api.state.resync;i++){f.advance(20000);await f.api.poll();await f.flush();}
    assert.equal(f.api.state.resync,null,scenario);assert.equal(f.api.state.epoch,s.epoch,scenario);
    assert.equal(row.calls[0].node,button);assert.equal(row.prose,prose);assert.equal(row.thinking[0].node,thinking);assert.equal(thinking.open,true);
    assert.equal(f.api.state.follow,false);assert.equal(f.el('history-viewport').scrollTop,400);
    assert.deepEqual(ocReads(f).slice(from),[{id:SID,fileEpoch:EPOCH,cursor:'d_26'},{id:SID,fromSeq:'0'}],'only the existing poll and positional resync read');
    assert.match(f.el('history-status').textContent,/Resynced after a pane-server restart/);
    const heldAfterResync=button.classList.contains('accepted');
    for(const call of resident(f).flatMap(r=>r.calls)) {
      const posts=srv.inspects.length;call.node.onclick();await f.flush();
      assert.equal(srv.inspects[posts]?.fileEpoch,s.epoch,'resynced Inspect must POST the current fileEpoch');
      assert.equal(srv.inspects.length,posts+1,'one deliberate click, one POST');
      assert.equal(call.node.classList.contains('accepted'),true,'the same button receives the accepted highlight');
      assert.equal(call.node.getAttribute('aria-pressed'),'true');assert.equal(f.api.state.accepted.key,call.key);
      assert.equal(JSON.parse(call.key)[2],s.epoch,'the click/highlight key uses the same epoch');
    }
    assert.equal(heldAfterResync,false,'an old-epoch accepted highlight is not transferred to a new witness');
    if(scenario==='deferred') {
      assert.equal(row.deferred.epoch,s.epoch,'the deferred repaint cannot restore the discarded epoch');
      assert.equal(prose.textContent,'Tool context before update','resync did not repaint selected text');
      assert.equal(selection.anchorNode,prose);selection=null;f.api.flushRepaints();
      assert.equal(row.deferred,null);assert.equal(row.prose,prose);assert.equal(row.thinking[0].node,thinking);assert.equal(thinking.open,true);
      assert.equal(prose.textContent,'Tool context after update');assert.equal(row.calls[0].node,button);
      const posts=srv.inspects.length;button.onclick();await f.flush();
      assert.equal(srv.inspects[posts]?.fileEpoch,s.epoch,'flushed Inspect must POST the current fileEpoch');
      assert.equal(button.classList.contains('accepted'),true);
    }
  }
});

test('a changed index reloads the same conversation\'s tail with a neutral notice; a vanished session stays selected and unavailable, never a Manager',async()=>{
  const s={...fixturePage(),rows:rows(40)},srv=ocServer({[SID]:s});
  const manager={sessionRef:'mgr',uuid:'u-mgr',title:'Manager-Session-1',source:'host',role:'Manager',state:'ready',lifecycle:'live',executionId:'cc-mgr',generation:1,
    capabilities:{history:true,nativeInput:true,controls:true,subagents:true,reconnect:true},scopes:[{scopeRef:'scope-mgr',label:'main'}]};
  const f=fixture({oc:srv,claude:url=>url.startsWith('/api/claude/sessions')?env({sessions:[manager],hasMore:false,status:'complete'}):undefined});
  await f.api.list();await f.api.openOpenCode(SID);await f.flush();
  s.epoch='oc_33333333333333333333333333333333';s.rows[0]={...s.rows[0],kind:'assistant',recordRef:'x0',uuid:'x0'};const from=f.requests.length;
  f.advance();await f.api.poll();await f.flush();for(let i=0;i<5 && f.api.state.resync;i++){f.advance(20000);await f.api.poll();await f.flush();}
  assert.equal(f.api.state.session.id,SID,'the same OpenCode conversation, never the newest Manager');assert.equal(f.api.state.session.backend,'opencode');
  assert.match(f.el('sessions-notice').textContent,/Reloaded .* at its newest turn: the rows on screen could not be matched .*nothing was lost/);
  assert.deepEqual(claudeForbidden(f,from),[]);
  delete srv.sessions[SID];f.advance();await f.api.poll();await f.flush();
  assert.equal(f.api.state.session.id,SID);assert.equal(f.api.state.blocked,true);assert.match(f.el('history-status').textContent,/OpenCode session missing/);
  srv.sessions[SID]=s;const reads=ocReads(f).length;await f.api.reconnect();await f.flush();
  assert.equal(f.api.state.session.id,SID,'Reconnect re-reads the same ses_');assert.ok(ocReads(f).length>reads);assert.equal(f.api.state.blocked,false);assert.equal(resident(f).length,40);assert.notEqual(f.api.state.session.sessionRef,'mgr');assert.deepEqual(claudeForbidden(f,from),[]);
});

// ---------------------------------------------------------------- 7 · the structural Claude-path matrix
test('Claude-path matrix: from a live targeted Manager with gauge, host evidence, children and a pending target action, an OpenCode selection triggers no Claude path; back to Claude still works',async()=>{
  const manager={sessionRef:'mgr',uuid:'u-mgr',title:'Manager-Session-1',sessionLabel:'Manager-Session-1',cwd:'/work',source:'host',role:'Manager',state:'ready',lifecycle:'live',executionId:'cc-mgr',generation:1,
    targetRef:'target-mgr',capabilities:{history:true,nativeInput:true,controls:true,subagents:true,reconnect:true},scopes:[{scopeRef:'scope-mgr',label:'main'}]};
  const claude=url=>{
    if(url.startsWith('/api/claude/sessions'))return env({sessions:[manager],hasMore:false,status:'complete'});
    if(url.startsWith('/api/claude/subagents'))return env({sessions:[{sessionRef:'sub1',parentUuid:'u-mgr',role:'subagent',agentId:'a1',title:'child',scopes:[{scopeRef:'ss',label:'main'}]}],hasMore:false,status:'complete'});
    if(url.startsWith('/api/claude/events'))return env({sessionRef:'mgr',scopeRef:'scope-mgr',fileEpoch:'e1',leafRef:'leaf',targetRef:'target-mgr',status:'complete',change:'snapshot',hasMore:false,deltaCursor:'delta',
      events:[{recordRef:'c1',uuid:'cu1',kind:'assistant',status:'stored',text:'Claude prose',calls:[]}],branches:[],traces:[],diagnostics:[],
      contextReadout:{state:'measured',occupancy_tokens:300000,window_tokens:1000000,measured_at:1792000000},liveState:{state:'ready',activity:{state:'tool',tool:'Bash',lastEventAt:99}},
      incidents:[{category:'stall',incident_id:'i1'}]});
  };
  const page=fixturePage();page.extra={incidents:[{category:'should-be-ignored'}],liveState:{state:'exited'},contextReadout:{state:'measured',occupancy_tokens:5}};
  const srv=ocServer({[SID]:page}),f=fixture({claude,oc:srv,ocRows:[ocRow(SID)]});
  await f.api.list();await f.api.browse(f.api.state.inventory[0]);await f.flush();
  assert.equal(f.api.state.target,'target-mgr');assert.ok(f.api.state.targetAction,'an outstanding auto-target action');assert.equal(f.messages.filter(m=>m.type==='hello').length>0,true);
  assert.equal(f.el('context-gauge').dataset.known,'true');assert.ok(f.api.state.incidents.length);
  f.advance();await f.api.poll();await f.flush();assert.ok(f.requests.some(([u])=>u.startsWith('/api/claude/subagents')),'children were being polled');
  const from=f.requests.length,posted=f.messages.length;
  // Switch to OpenCode, then drive every Claude-only path.
  await openListed(f);await f.flush();
  await f.fireTimers();                                   // the delayed Claude target callback
  f.api.useTarget('target-mgr');f.api.useTarget();await f.fireTimers();     // explicit target invocations
  f.parent.postMessage=m=>f.messages.push(m);
  await f.api.children();await f.api.children('cursor');  // manual child refresh / more
  for(let i=0;i<4;i++){f.advance(11000);await f.api.poll();await f.flush();}
  f.api.controls();
  assert.equal(f.el('history-target').disabled,true);
  assert.deepEqual(f.messages.slice(posted).filter(m=>m.type==='target' || m.type==='hello'),[],'no target or hello message while OpenCode is selected');
  assert.match(f.el('target-status').textContent,/Not a Send\/Refine target: an OpenCode conversation is view-only here/);
  assert.equal(f.api.state.target,null);assert.equal(f.api.state.targetAction,null);assert.equal(f.api.state.childParent,null);assert.deepEqual([...f.api.state.children],[]);
  assert.equal(f.api.state.liveState,null,'no inherited or injected host state');assert.deepEqual([...f.api.state.incidents],[]);assert.equal(f.api.state.contextReadout,null);
  assert.equal(f.el('context-gauge').dataset.known,'false');assert.match(f.el('context-gauge').title,/unknown · OpenCode/);
  assert.match(f.el('history-live').textContent,/no host evidence/);assert.equal(find(find(f.el('sessions-list'),'oc-item')[0],'session-rename').length,0,'no rename pencil');
  // A resync and a Reconnect stay on OpenCode routes.
  page.epoch='oc_44444444444444444444444444444444';f.advance();await f.api.poll();await f.flush();for(let i=0;i<5 && f.api.state.resync;i++){f.advance(20000);await f.api.poll();await f.flush();}
  await f.api.reconnect();await f.flush();
  assert.equal(f.api.state.session.backend,'opencode');assert.equal(f.api.state.session.id,SID,'no session substitution');
  assert.deepEqual(claudeForbidden(f,from),[],'no Claude events/subagents/rename/state/send/control/reconnect/incident/inspect route');
  const inventoryReads=f.requests.slice(from).filter(([u])=>u.startsWith('/api/claude/sessions')).length;
  assert.ok(inventoryReads>=1,'the independent inventory refresh continues (identified separately: '+inventoryReads+' reads)');
  // A host-launch notification only refreshes the inventory.
  // Back to Claude: its normal path still works.
  const back=f.requests.length;await f.api.browse(f.api.state.inventory[0]);await f.flush();
  assert.ok(f.requests.slice(back).some(([u])=>u.startsWith('/api/claude/events?sessionRef=mgr')));assert.equal(f.api.state.target,'target-mgr');
  assert.ok(f.messages.slice(posted).some(m=>m.type==='hello'),'the Claude auto-target path runs again');assert.equal(f.el('context-gauge').dataset.known,'true');
  assert.equal(f.el('oc-header').hidden,true);assert.equal(resident(f)[0].node.children[0].children[0].textContent,'Claude · answer');
});

// ---------------------------------------------------------------- 9 · live elapsed call timers
// Dan: while tools execute he must see them and how long, not just a spinner and the last completed
// call. The timer ticks once a second in place, freezes the stored total on completion, and stops
// claiming running when the evidence is idle, unknown or stale.
const timerCall=(id,time,{status='pending-or-missing',resultTime=null,name='shell'}={})=>({id,block:0,callRef:'call_'+id,name,status,time,resultTime,
  previewData:{schemaVersion:1,status:'available',value:{kind:'string',characters:1,jsonCharacters:3,head:'x',tail:'x'}},inputBytes:1,outputBytes:null});
const timerRow=(seq,calls,extra={})=>({recordRef:'tr'+seq,uuid:'tr'+seq,seq,kind:'assistant',status:'stored',text:'',calls,...extra});
const timerPage=(rows,busy=true)=>({rows,epoch:EPOCH,busy,updates:[],title:'timer fixture',agent:'quick-worker',model:null,directory:'/fixture',parentId:null});
const timerNode=(row,id)=>row.calls.find(c=>c.id===id)?.node.children[2].children[0];
const timerRunning=rows=>rows.flatMap(r=>r.calls).filter(c=>c.node.classList.contains('running')).map(c=>c.id);

test('concurrent calls after the last receipt tick once a second in place, freeze their stored total on completion; idle, unknown and a stale busy fact stop claiming running',async()=>{
  const page=timerPage([
    timerRow(0,[timerCall('a',90)]),                    // before the last receipt: never running
    {...timerRow(1,[]),kind:'result'},                  // end of the previous turn (the receipt barrier)
    timerRow(2,[timerCall('b',95),timerCall('c',99)]),  // both pending after it
  ]);
  const f=fixture({oc:ocServer({[SID]:page})});
  await f.api.openOpenCode(SID);await f.flush();        // the fake clock opens at 100000 ms = 100 s
  const rows=resident(f),elapsed=start=>f.ctx.opcToolReading.elapsedText(start*1000,f.now());
  assert.deepEqual(timerRunning(rows),['b','c'],'both concurrent calls after the last receipt are yellow; the one before it is not');
  assert.equal(timerNode(rows[2],'b').textContent,elapsed(95)+' so far');
  assert.equal(timerNode(rows[2],'c').textContent,elapsed(99)+' so far');
  assert.equal(timerNode(rows[2],'b').dataset.running,'true');
  // The clock tick repaints the same time cell only: no row rebuild, no scroll move, no read.
  const b= timerNode(rows[2],'b'),row=rows[2].node,scroll=f.el('history-viewport').scrollTop,reads=f.requests.length;
  f.advance(3000);f.api.tickCallTimers();
  assert.equal(timerNode(rows[2],'b'),b,'the same time cell ticks');
  assert.equal(b.textContent,elapsed(95)+' so far');assert.equal(timerNode(rows[2],'c').textContent,elapsed(99)+' so far');
  assert.equal(rows[2].node,row,'no body rebuild');assert.equal(f.el('history-viewport').scrollTop,scroll,'ticking never moves the reading position');
  assert.equal(f.requests.length,reads,'ticking never makes a read');
  assert.match(b.title,/^Running for /);
  // Completion: the stored call → its frozen total from the stored timestamps; the other keeps running.
  const done=clone(page.rows[2]);done.calls[0]={...done.calls[0],status:'stored',resultTime:99,outputBytes:0};page.updates.push(done);
  f.advance();await f.api.poll();await f.flush();
  assert.deepEqual(timerRunning(rows),['c'],'a completed call is no longer running');
  assert.equal(timerNode(rows[2],'b').textContent,'4.0s');assert.equal(timerNode(rows[2],'b').hidden,false);
  assert.equal(timerNode(rows[2],'b').dataset.running,'false');
  f.advance(30000);f.api.tickCallTimers();
  assert.equal(timerNode(rows[2],'b').textContent,'4.0s','a finished call never keeps ticking');
  assert.equal(timerNode(rows[2],'c').textContent,'','the clock itself stops a stale running claim without waiting for classify');
  // Idle: no running claim, and a call with no stored result shows no invented duration.
  page.busy=false;f.advance();await f.api.poll();await f.flush();
  assert.deepEqual(timerRunning(rows),[]);assert.equal(timerNode(rows[2],'c').textContent,'');assert.equal(timerNode(rows[2],'c').hidden,true);
  // Unknown busy is unknown, never busy.
  page.busy=null;f.advance();await f.api.poll();await f.flush();
  assert.deepEqual(timerRunning(rows),[]);assert.equal(f.el('history-activity').dataset.state,'unknown');assert.doesNotMatch(f.el('history-activity').textContent,/activity unknown/);
  // A busy fact older than its read window is stale evidence.
  page.busy=true;f.advance();await f.api.poll();await f.flush();assert.deepEqual(timerRunning(rows),['c']);
  f.advance(16000);f.api.classify();assert.deepEqual(timerRunning(rows),[],'a stale busy fact stops the clock');
  // A failed observation never keeps a call yellow.
  page.busy=true;f.advance();await f.api.poll();await f.flush();assert.deepEqual(timerRunning(rows),['c']);
  f.api.state.observationError='fixture read failed';f.api.classify();
  assert.deepEqual(timerRunning(rows),[],'a failed read is unknown, not running');
});

test('a running call with a missing or unusable start stays yellow but never invents elapsed time; a stored total with no stored start stays blank',async()=>{
  const page=timerPage([timerRow(0,[timerCall('nostart',null),timerCall('future',200)])]);
  const f=fixture({oc:ocServer({[SID]:page})});await f.api.openOpenCode(SID);await f.flush(); // now 100 s: before the 200 s start
  const rows=resident(f);
  assert.deepEqual(timerRunning(rows),['nostart','future'],'status and busy still make both yellow');
  for(const [id,title] of [['nostart',/start time is not recorded/],['future',/elapsed time is unavailable/]]) {
    const node=timerNode(rows[0],id);
    assert.equal(node.textContent,'');assert.equal(node.hidden,true,'no number without a usable start');assert.match(node.title,title);
  }
  // A stored total cannot be computed from a start the records do not carry.
  const done=clone(page.rows[0]);done.calls[0]={...done.calls[0],status:'stored',resultTime:250};page.updates.push(done);
  f.advance();await f.api.poll();await f.flush();
  const stored=timerNode(rows[0],'nostart');assert.equal(stored.textContent,'');assert.equal(stored.hidden,true);assert.equal(stored.title,'');
});

test('current-tool strip lists every open parallel call even outside the resident window and survives a last-sibling completion',async()=>{
  const earlier=timerCall('earlier',90),last=timerCall('last',99),page=timerPage([timerRow(5,[last])]);
  page.extra={runningCalls:[earlier,last]};
  const f=fixture({oc:ocServer({[SID]:page})});await f.api.openOpenCode(SID);await f.flush();
  const strip=f.el('running-tools');assert.equal(strip.hidden,false);assert.equal(find(strip,'running-tool').length,2);
  assert.match(strip.textContent,/10s so far/);assert.equal(resident(f).length,1,'current summary does not inject history rows');
  const first=find(strip,'running-tool')[0];f.advance(2000);f.api.tickCallTimers();assert.equal(find(strip,'running-tool')[0],first);assert.match(first.textContent,/12s so far/);
  page.extra.runningCalls=[earlier];page.updates=[timerRow(5,[{...last,status:'stored',resultTime:101}])];
  f.advance();await f.api.poll();await f.flush();assert.equal(find(strip,'running-tool').length,1);assert.equal(find(strip,'running-tool')[0],first);
  page.busy=false;page.extra.runningCalls=[];f.advance();await f.api.poll();await f.flush();assert.equal(strip.hidden,true);
});

// Independent OpenCode activity and foreground helper badges (desktop and phone Programs).
test('current activity remains visible while the conversation history read is unavailable',async()=>{
  const c=timerCall('independent',95),f=fixture({activity:()=>({schemaVersion:1,backend:'opencode',status:'complete',observedAt:f.now()/1000,
    active:[SID],sessions:[{id:SID,toolStatus:'complete',runningCalls:[c]}]})});
  f.api.state.session=f.api.openCodeSession(SID);f.api.state.observationError='history index is still loading';
  await f.api.pollOpenCodeActivity();f.api.tickCallTimers();f.api.activity();
  assert.equal(f.el('running-tools').hidden,false);assert.match(f.el('running-tools').textContent,/5s so far/);
  assert.equal(f.el('history-activity').dataset.state,'busy');assert.match(f.el('history-activity').textContent,/^read 0 s ago/);assert.equal(ocReads(f).length,0);
});
test('fast activity beats stale inventory, names every blocking helper and links without targeting; completed waits disappear',async()=>{
  const lead='ses_waitlead001',a='ses_waithelperA',b='ses_waithelperB';
  const ocRows=[ocRow(lead,{busy:false,title:'lead'}),ocRow(a,{kind:'helper',parentSeat:lead,title:'helper A',socket:'[ap:HELPAA]'}),ocRow(b,{kind:'helper',parentSeat:lead,title:'helper B'})];
  let calls=[{name:'shell',status:'pending-or-missing',time:95,command:`set +e; bash '/work/oc_seat.sh' wait '${a}' --max 540`},
    {name:'shell',status:'pending-or-missing',time:96,command:`env -u TOKEN timeout 600 bash /work/oc.sh seat wait "${b}" --max 540`},
    {name:'shell',status:'pending-or-missing',time:96,command:`printf '%s' 'bash /work/oc_seat.sh wait ses_notawait1'; # oc.sh seat wait ses_comment1`}],active=[lead,a,b];
  const f=fixture({ocRows,oc:ocServer({[a]:fixturePage()}),activity:()=>({schemaVersion:1,backend:'opencode',status:'complete',observedAt:f.now()/1000,active,sessions:[{id:lead,runningCalls:calls,toolStatus:'complete'}]})});
  await f.api.ocList();await f.api.pollOpenCodeActivity();
  const leadRow=()=>find(f.el('sessions-list'),'oc-item').find(n=>n.children[0].title.includes(lead));
  assert.equal(leadRow().classList.contains('active'),true,'fast active map wins over cached busy=false');
  const flag=find(leadRow(),'waiting-flag')[0];assert.equal(flag.textContent,'🔔 Waiting on helper');
  assert.match(flag.title,/helper A/);assert.match(flag.title,/helper B/);assert.match(flag.title,/socket \[ap:HELPAA\]/);
  assert.match(flag.title,/blocking foreground wait/);assert.doesNotMatch(flag.title,/not blocking/);
  assert.doesNotMatch(flag.title,/ses_notawait1|ses_comment1/,'quoted examples/comments are not executing waits');
  const links=find(leadRow(),'wait-target');assert.equal(links.length,2);links[0].onclick();await f.flush();
  assert.equal(f.api.state.session.id,a);assert.equal(f.api.state.target,null);
  calls=[];f.advance(2000);await f.api.pollOpenCodeActivity();
  assert.equal(find(leadRow(),'waiting-flag').length,0,'wait badge gone as soon as calls settle, even if model continues');
  assert.equal(leadRow().classList.contains('active'),true);
  active=[];f.advance(2000);await f.api.pollOpenCodeActivity();
  assert.equal(leadRow().classList.contains('active'),false);assert.match(leadRow().textContent,/idle/);
});

test('activity failure and stale cache say unknown, never idle or a timeless spinner; newer conversation evidence can still win',async()=>{
  const sid='ses_statuslead1';let response,fail=false;
  const f=fixture({ocRows:[ocRow(sid)],activity:()=>{if(fail)throw new Error('backend down');return response;}});
  response={schemaVersion:1,backend:'opencode',status:'complete',observedAt:f.now()/1000,active:[sid],sessions:[]};
  await f.api.ocList();await f.api.pollOpenCodeActivity();
  assert.equal(f.api.ocEvidence({id:sid}).busy,true);
  f.advance(16000);f.api.inventory();assert.equal(f.api.ocEvidence({id:sid}).busy,null);
  let row=find(f.el('sessions-list'),'oc-item')[0];assert.match(row.textContent,/activity unknown/);assert.doesNotMatch(row.textContent,/ · idle/);
  response.observedAt=f.now()/1000;response.stale=true;response.error='refresh failed';await f.api.pollOpenCodeActivity();
  assert.equal(f.api.ocEvidence({id:sid}).busy,null,'server stale flag beats a recently received response');
  delete response.stale;delete response.error;await f.api.pollOpenCodeActivity();assert.equal(f.api.ocEvidence({id:sid}).busy,true);
  fail=true;await f.api.pollOpenCodeActivity();assert.equal(f.api.ocEvidence({id:sid}).busy,null);
  f.api.state.session={backend:'opencode',id:sid};f.api.state.ocObservedAt=f.now()+1;f.api.state.ocBusyFact=false;
  assert.equal(f.api.ocEvidence({id:sid}).busy,false,'newer independent conversation read wins');
});

test('Refresh requests fast activity immediately and repeats a click during its in-flight read; phone helper links open read-only Chat',async()=>{
  const lead='ses_phonewait01',helper='ses_phonehelp01';let release,requests=0;
  const response=()=>({schemaVersion:1,backend:'opencode',status:'complete',observedAt:f.now()/1000,active:[lead],sessions:[{id:lead,toolStatus:'complete',runningCalls:[{command:'bash /a/oc_seat.sh wait '+helper}]}]});
  const f=fixture({phone:true,ocRows:[ocRow(lead),ocRow(helper,{kind:'helper',parentSeat:lead,title:'named helper'})],activity:()=>{requests++;return requests===1?new Promise(resolve=>release=()=>resolve(response())):response();}});
  await f.api.ocList();const first=f.api.pollOpenCodeActivity();await f.flush();
  await f.api.pollOpenCodeActivity(true);assert.equal(requests,1);release();await first;await f.flush();assert.equal(requests,2);
  const row=find(f.el('sessions-list'),'oc-item').find(n=>n.children[0].title.includes(lead)),link=find(row,'wait-target')[0];
  assert.match(link.textContent,/named helper/);link.onclick();
  assert.equal(f.messages.at(-1).type,'open-session');assert.equal(f.messages.at(-1).session.id,helper);
  assert.equal(f.api.state.session,null);assert.equal(f.requests.some(([u])=>u.includes('/inspect') || u.includes('/send')),false);
});

// ---------------------------------------------------------------- 8 · the phone stays as it was
test('phone sidebar-only page: a row tap is still the glance, a tree row toggles its tree with the 💬 chip, and nothing opens a conversation, focus or target',async()=>{
  const lead=ocRow('ses_phonelead01',{program:'PPPPPP',kind:'conductor',conductorN:1,title:'[ap:PPPPPP]-001 ▸ Conductor'}),seat=ocRow('ses_phoneseat01',{program:'PPPPPP',kind:'seat',conductorN:1,parentSeat:null});
  const f=fixture({phone:true,ocRows:[lead,seat],oc:ocServer({ses_phoneseat01:fixturePage()})});
  await f.api.ocList();f.api.state.lineageOpen.set('p:PPPPPP',true);f.api.inventory();
  const items=()=>find(f.el('sessions-list'),'oc-item'),byId=id=>items().find(n=>n.children[0].title.includes(id));
  const leaf=byId('ses_phoneseat01');assert.equal(find(leaf,'oc-glance-chip').length,1,'a leaf keeps the same larger glance action as a tree row');
  leaf.children[0].onclick();await f.flush();
  assert.ok(f.requests.some(([u])=>u==='/api/opencode/glance?id=ses_phoneseat01'));assert.equal(byId('ses_phoneseat01').children[0].getAttribute('aria-expanded'),'true');
  const tree=byId('ses_phonelead01');assert.equal(find(tree,'oc-glance-chip').length,1);
  assert.match(byId('ses_phoneseat01').children[0].title,/Tap for a glance under this row; its Read conversation › opens the whole conversation in Chat\./);
  assert.equal(find(find(f.el('sessions-list'),'oc-glance')[0],'glance-read')[0].textContent,'Read conversation ›','the phone page reads it in its own Chat');
  assert.equal(find(f.el('sessions-list'),'focus-open').length,0);
  await f.api.openOpenCode('ses_phoneseat01');await f.flush();
  assert.equal(f.api.state.session,null);assert.equal(ocReads(f).length,0,'no conversation read on the phone');
  assert.equal(f.requests.filter(([u])=>u.startsWith('/api/opencode/inspect') || u==='/api/focus-request').length,0);assert.equal(f.messages.length,0);
});
