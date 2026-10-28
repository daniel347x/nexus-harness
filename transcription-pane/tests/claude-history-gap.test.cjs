// Round 9b · Dan's silent history gap (Manager-Session-184, ~17:50): after the "handle expired"
// recovery the view stopped at 16:18 with Following lit, and new turns appended after a 1 h 29 min hole.
// Root cause, reproduced here against a facade-like server: (1) an expired handle froze the view while
// Following stayed lit; (2) while paused (or a cold index still draining) new pages kept arriving and the
// 200-row trim evicted everything EXCEPT the reading anchor, leaving it as an island above the live tail.
// The fix keeps one contiguous run of rows (server positions, end-only trimming) and never ends silently.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(process.env.HISTORY_TEST_SOURCE || path.join(__dirname,'../static/sessions.js'),'utf8');
const shared=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-record-text.js'),'utf8');
const core=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-preview-core.js'),'utf8');
const env=x=>({schemaVersion:1,backend:'claude',...x});

// The facade's paging contract: positions in one growing row list; a cold index drains `drain` rows per
// request (status indexing, continuation at the indexed end); a pane-server restart re-issues every handle.
function server({drain=100000,positions=true}={}) {
  const s={rows:[],gen:1,indexed:0,cold:false,drain,listed:true,shift:0};
  s.add=n=>{for(let i=0;i<n;i++)s.rows.push({n:s.rows.length,time:1792090000+60*s.rows.length});};
  s.restart=()=>{s.gen++;s.indexed=0;s.cold=true;};
  // A reply streaming into the newest row: no UUID until it is final (like a host partial row).
  s.stream=text=>{const r=s.rows.at(-1);r.partial=true;r.text=text;};
  s.finish=text=>{const r=s.rows.at(-1);r.partial=false;r.text=text;};
  const refs=()=>({sessionRef:'sess'+s.gen,scopeRef:'scope'+s.gen});
  s.row=()=>({...refs(),uuid:'e524faa7-d4db-4719-b7de-133d7481daf9',executionId:'cc-e524faa7-d4db-4719-b7de-133d7481daf9',generation:1,role:'Manager',displayRole:'manager',
    source:'host',title:'Manager-Session-184',sessionLabel:'Manager-Session-184',cwd:'/r/manager-sessions',state:'ready',lifecycle:'live',createdAt:1792086607,
    capabilities:{nativeInput:true,controls:true,subagents:true},scopes:[{scopeRef:refs().scopeRef,label:'main'}]});
  const stale={__status:409,body:env({status:'stale',error:'host handle expired; reselect'})};
  // s.shift > 0 renumbers every row after a restart (new code building rows differently): the
  // positions on screen then name different messages.
  const ev=(r,i)=>({recordRef:'g'+s.gen+'r'+(i+s.shift),uuid:r.partial?null:'u'+r.n,scope:'main',kind:r.n%7===0?'user':'assistant',status:r.partial?'partial':'stored',
    partial:!!r.partial,text:r.text ?? 'row '+r.n,calls:[],time:r.time,...(positions?{seq:i+s.shift}:{})});
  s.reads=[];
  s.handle=url=>{
    const q=new URLSearchParams(url.split('?')[1] || '');
    if(url.startsWith('/api/claude/sessions'))return env({sessions:s.listed?[s.row()]:[],status:'complete',hasMore:false});
    if(url.startsWith('/api/claude/subagents'))return q.get('sessionRef')===refs().sessionRef?env({sessions:[],hasMore:false,status:'complete'}):stale;
    if(url==='/api/tool-selection')return env({epoch:'mail',revision:1,selection:null});
    if(!url.startsWith('/api/claude/events'))throw new Error('No live network: '+url);
    if(q.get('sessionRef')!==refs().sessionRef)return stale;
    s.indexed=Math.min(s.rows.length,s.indexed+(s.cold?s.drain:1e9));if(s.indexed===s.rows.length)s.cold=false;
    const indexing=s.indexed<s.rows.length,cursor=q.get('cursor'),from=q.get('fromSeq');let start=0;
    if(cursor){const [,g,at]=cursor.split('_');if(Number(g)!==s.gen)return stale;start=Number(at);}
    if(from!==null){if(cursor || q.get('leafRef'))throw new Error('fromSeq mixed with a cursor/leaf');start=Math.min(Math.max(0,Number(from)-s.shift),s.indexed);}
    s.reads.push(from!==null?'from:'+from:cursor?'cursor':'first');
    const end=Math.min(s.indexed,start+100),more=end<s.indexed || indexing,next=mode=>'c_'+s.gen+'_'+end+'_'+mode+'_'+s.indexed;
    return env({...refs(),fileEpoch:'epoch'+s.gen,leafRef:'leaf'+s.gen,targetRef:'target'+s.gen,events:s.rows.slice(start,end).map((r,i)=>ev(r,start+i)),
      hasMore:more,nextCursor:more?next('forward'):null,deltaCursor:next('delta'),indexingContinuation:indexing && end===s.indexed?next('forward'):null,
      status:indexing?'indexing':'complete',indexLimit:null,change:'snapshot',branches:[],source:'host',liveState:{state:'ready'}});
  };
  return s;
}
function fixture(srv) {
  const nodes=new Map();let clock=1792100000000;
  class Node {
    constructor(tag='div'){this.tag=tag;this.children=[];this.parentNode=null;this.content='';this.attributes={};this.dataset={};this.value='';this.scrollTop=0;this.clientHeight=100;this.listeners={};
      this.style={setProperty:(k,v)=>this.attributes[k]=v};const cls=new Set();this.classList={add:x=>cls.add(x),remove:x=>cls.delete(x),toggle:(x,on)=>{if(on===undefined)on=!cls.has(x);on?cls.add(x):cls.delete(x);},contains:x=>cls.has(x)};}
    set textContent(v){this.content=v;this.replaceChildren();}get textContent(){return this.content+this.children.map(x=>x.textContent).join('');}
    append(...items){for(const x of items){if(x.tag==='fragment'){this.append(...[...x.children]);continue;}x.remove();x.parentNode=this;this.children.push(x);}}
    prepend(x){x.remove();x.parentNode=this;this.children.unshift(x);}
    insertBefore(x,next){x.remove();x.parentNode=this;this.children.splice(next?this.children.indexOf(next):this.children.length,0,x);}
    replaceChildren(...items){for(const x of this.children)x.parentNode=null;this.children=[];this.append(...items);}
    remove(){if(this.parentNode){const p=this.parentNode;p.children.splice(p.children.indexOf(this),1);this.parentNode=null;}}
    setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}addEventListener(n,f){this.listeners[n]=f;}
    get scrollHeight(){return this.id==='history-viewport'?el('history-events').children.length*100:100;}
    getBoundingClientRect(){const top=this.parentNode?.id==='history-events'?this.parentNode.children.indexOf(this)*100-el('history-viewport').scrollTop:0;return {top,bottom:top+100};}
    get offsetParent(){return 1;}
  }
  const el=id=>{if(!nodes.has(id)){const node=new Node();node.id=id;nodes.set(id,node);}return nodes.get(id);};
  const doc={hidden:false,activeElement:null,getElementById:el,createElement:t=>new Node(t),createDocumentFragment:()=>new Node('fragment'),getSelection:()=>null,addEventListener(){}};
  const context={Date:class extends Date{static now(){return clock;}},TextEncoder,TextDecoder,AbortController,URLSearchParams,Response,setTimeout:(f,ms)=>ms===0?queueMicrotask(f):0,clearTimeout(){},setInterval(){},
    BroadcastChannel:class{postMessage(){}},fetch:async url=>{
      if(url.startsWith('/api/opencode/'))return new Response(JSON.stringify({schemaVersion:1,backend:'opencode',status:'unavailable',error:'offline',sessions:[]}));
      const d=await srv.handle(url);return d.__status?new Response(JSON.stringify(d.body),{status:d.__status}):new Response(JSON.stringify(d));}};
  vm.runInNewContext(shared+'\n'+core+'\n'+source,context);
  const api=context.opcClaudeHistory.create(doc);
  const tick=async(n=1)=>{for(let i=0;i<n;i++){clock+=700;await api.poll();await new Promise(r=>setImmediate(r));}};
  return {api,el,doc,tick,now:()=>clock};
}
// The resident rows' positions, in screen order.
const resident=f=>f.api.state.order.map(k=>Number(String(k).replace(/^g\d+r/,'')));
function assertContiguous(f,label) {
  const r=resident(f);
  for(let i=1;i<r.length;i++)assert.equal(r[i],r[i-1]+1,label+': rows '+r[i-1]+' → '+r[i]+' with nothing between (a silent gap)');
  return r;
}
const pressed=f=>f.el('history-follow').getAttribute('aria-pressed')==='true';

test('FMPZO7 (a) · a pane-server restart mid-reply while Following: the cut-off reply completes in place, nothing lost, doubled or marked stale',async()=>{
  const srv=server({drain:40});srv.add(250);srv.stream('The Manager is writing a long reply and has got as far as');
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(20);
  assert.equal(resident(f).at(-1),249);assert.ok(pressed(f));
  const streaming=f.api.state.rows.get(f.api.state.order.at(-1));
  assert.match(streaming.node.textContent,/has got as far as$/);
  // The pane server restarts (every handle re-issued) while the reply finishes and 30 more turns arrive.
  srv.restart();srv.finish('The Manager is writing a long reply and has got as far as the end of it.');srv.add(30);
  for(let i=0;i<30 && f.api.state.order.length && resident(f).at(-1)<279;i++){await f.tick();
    assert.equal(f.api.state.blocked,false,'void handles never stop the view');assert.equal(f.api.state.resyncWarn,null,'no orange line while the resync works');
    assert.notEqual(f.el('history-edge').dataset.kind,'stale');assert.notEqual(f.el('history-follow-label').textContent,'Not updating');}
  assert.equal(f.api.state.session.sessionRef,'sess2','fresh handles, acquired by the page itself');
  assert.ok(srv.reads.some(r=>r.startsWith('from:')),'the window was read again by position');
  const rows=assertContiguous(f,'after the resync');assert.equal(rows.at(-1),279);
  assert.equal(new Set(rows).size,rows.length,'no row doubled');
  // The very node that was cut off mid-sentence now carries the whole reply, under its fresh handle.
  assert.equal(f.api.state.rows.get(f.api.state.order[rows.indexOf(249)]),streaming,'the same row continues in place');
  assert.match(streaming.node.textContent,/the end of it\.$/);
  assert.ok(pressed(f),'Following stays lit');assert.equal(f.el('history-edge').hidden,true);
  srv.add(3);await f.tick(2);assert.equal(assertContiguous(f,'live tail').at(-1),282);
});

test('FMPZO7 · Reconnect stays a deliberate full reload with fresh handles, landing at the newest turn',async()=>{
  const srv=server();srv.add(150);
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(5);
  srv.restart();srv.add(10);
  f.el('inspect-status').textContent='Inspect not accepted: host handle expired; reselect · Click deliberately to retry.';
  await f.api.reconnect();assert.equal(f.api.state.session.sessionRef,'sess2');assert.equal(f.api.state.blocked,false);
  assert.match(f.el('inspect-status').textContent,/^Inspect selects one held call/);assert.match(f.el('sessions-notice').textContent,/Reconnected: Manager-Session-184/);
  await f.tick(10);assert.equal(assertContiguous(f,'after Reconnect').at(-1),159);assert.ok(pressed(f));
});

test('FMPZO7 (b) · a restart while Dan reads row 20: the reading position and the rows on screen stay exactly as they were',async()=>{
  const srv=server();srv.add(200);
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(5);
  f.api.setFollow(false,{byScroll:true});const viewport=f.el('history-viewport');viewport.scrollTop=2000;
  const before=resident(f),nodes=f.api.state.order.map(k=>f.api.state.rows.get(k).node);
  srv.restart();await f.tick(6);
  assert.equal(f.api.state.resync,null,'resynced');assert.equal(f.api.state.session.sessionRef,'sess2');
  assert.deepEqual(resident(f),before);assert.equal(viewport.scrollTop,2000,'the reading position never moved');
  assert.deepEqual(f.api.state.order.map(k=>f.api.state.rows.get(k).node),nodes,'the same nodes, re-keyed in place');
  assert.equal(f.api.state.follow,false);assert.equal(f.el('history-edge').hidden,true);
  // Earlier pages still load after the resync (by position: the old checkpoints are gone).
  srv.add(0);await f.api.loadOlder();assert.equal(resident(f)[0],0);
});

test('FMPZO7 (c) · rows renumbered by new code after the restart: the check fails and the full reload runs with a neutral notice, never a gap',async()=>{
  const srv=server();srv.add(150);
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(5);
  srv.restart();srv.shift=1;await f.tick(6);
  assert.match(f.el('sessions-notice').textContent,/Reloaded Manager-Session-184 after the pane server restarted \(row \d+ is a different message now\)/);
  assert.equal(f.el('sessions-notice').dataset.error,'false','a neutral notice, not a warning');
  assert.equal(f.api.state.blocked,false);assert.equal(f.api.state.resyncWarn,null);
  await f.tick(5);const rows=assertContiguous(f,'after the fallback reload');assert.equal(rows.at(-1),150);assert.ok(pressed(f));
});

test('FMPZO7 (d) · the conversation is gone after the restart: the orange line is the only warning, it keeps retrying and clears by itself',async()=>{
  const srv=server();srv.add(120);
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(5);
  srv.restart();srv.listed=false;
  for(let i=0;i<20 && !f.api.state.resyncWarn;i++)await f.tick();
  assert.match(f.api.state.resyncWarn,/Manager-Session-184 is no longer listed/);
  assert.equal(f.el('history-edge').dataset.kind,'stale');assert.match(f.el('history-edge').textContent,/Not updating since .*no longer listed · still retrying by itself/);
  assert.equal(pressed(f),false);assert.equal(f.el('history-follow-label').textContent,'Not updating');
  assert.equal(f.el('history-edge').children.some(c=>c.textContent==='⟳ Reconnect'),true);
  srv.listed=true;srv.add(5);
  for(let i=0;i<40 && f.api.state.resync;i++)await f.tick();
  assert.equal(f.api.state.resync,null);assert.equal(f.api.state.resyncWarn,null);assert.equal(f.el('history-edge').dataset.kind==='stale',false);
  await f.tick(5);assert.equal(assertContiguous(f,'after the conversation came back').at(-1),124);assert.ok(pressed(f));
});

test('T5.4 · FMPZO7 (e) · a cold index after the restart: a quiet reconnecting wait, then the view continues',async()=>{
  const srv=server({drain:25});srv.add(300);
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(40);
  assert.equal(resident(f).at(-1),299);
  // What the line under the newest message says while each read of the cold index is in flight.
  const shown=[],handle=srv.handle;srv.handle=url=>{if(url.includes('fromSeq='))shown.push([f.el('history-edge').dataset.kind,f.el('history-follow-label').textContent,f.el('history-edge').textContent]);return handle(url);};
  srv.restart();await f.tick(1);
  for(let i=0;i<60 && f.api.state.resync;i++){await f.tick();assert.equal(f.api.state.resyncWarn,null);assert.equal(f.api.state.blocked,false);}
  assert.ok(shown.length>1 && shown.every(([kind,label,line])=>kind==='loading' && label==='Following · reconnecting' && line==='Reconnecting … the rows on screen stay.'),'the wait reads as reconnecting, never as newer or a failure: '+JSON.stringify(shown));
  assert.equal(f.api.state.resync,null);assert.equal(assertContiguous(f,'after the cold index').at(-1),299);
  assert.ok(srv.reads.filter(r=>r.startsWith('from:')).length>1,'it read again while the index was still cold');
});

test('the island: paused while more than a window of new turns arrives, the reading position stays and newer turns wait below a visible line',async()=>{
  const srv=server();srv.add(120);
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(5);
  assert.deepEqual([resident(f)[0],resident(f).at(-1)],[0,119]);
  // Dan scrolls up and reads row 20 (it sits at the top of the viewport) while a busy session adds 400
  // turns, a few at a time (each a small delta page): the old trim kept row 20 as an island over the tail.
  f.api.setFollow(false,{byScroll:true});f.el('history-viewport').scrollTop=2000;
  for(let i=0;i<40;i++){srv.add(10);await f.tick(1);}
  const rows=assertContiguous(f,'paused under a flood');
  assert.ok(rows.includes(20),'the row being read stays resident');
  assert.equal(rows.length,200);assert.notEqual(rows.at(-1),519);
  assert.equal(f.el('history-edge').dataset.kind,'behind');assert.match(f.el('history-edge').textContent,/Newer messages are not loaded below this point/);
  assert.match(f.el('history-follow-label').textContent,/Paused.*newer below/);
  // Scrolling to the bottom does not relight Following short of the head; "Jump to latest" gets there, contiguous.
  const viewport=f.el('history-viewport');f.api.state.scrollIntent=f.now();viewport.scrollTop=viewport.scrollHeight;viewport.listeners.scroll();await f.tick(1);
  assert.equal(f.api.state.follow,false);assertContiguous(f,'loading on at the bottom');
  const jump=f.el('history-edge').children.find(b=>b.textContent==='Jump to latest');jump.onclick();await f.tick(10);
  const head=assertContiguous(f,'jumped to latest');assert.equal(head.at(-1),519);assert.ok(pressed(f));
});

test('earlier pages read while the window holds the head stay contiguous and keep the live cursors',async()=>{
  const srv=server();srv.add(330);
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(10);
  const before=f.api.state.delta;
  f.api.setFollow(false,{byScroll:true});
  for(let i=0;i<3;i++)await f.api.loadOlder();
  const rows=assertContiguous(f,'after loading earlier pages');assert.equal(rows.length,200);
  assert.ok(f.api.state.next,'the evicted newest rows are reachable again (behind)');
  assert.notEqual(f.api.state.delta,null);assert.equal(typeof before,'string');
  f.api.setFollow(true);await f.tick(10);assert.equal(assertContiguous(f,'back to the head').at(-1),329);
});

test('an older server without positions keeps working (plain append); after a restart it falls back to the full reload',async()=>{
  const srv=server({positions:false});srv.add(150);
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(5);
  assert.equal(f.api.state.rows.size,150);srv.restart();await f.tick(4);
  assert.match(f.el('sessions-notice').textContent,/Reloaded Manager-Session-184 after the pane server restarted \(the rows on screen carry no positions\)/);
  assert.equal(f.api.state.session.sessionRef,'sess2');assert.equal(f.api.state.blocked,false);
});

// Random sequences of what Dan does (scroll, pause, follow, ↑, reload, row click, Reconnect, tab hidden)
// against restarts, cold drains and growth: the window is always one contiguous run, Following is never
// lit on a stopped view, and a view short of the head always says so.
function rng(seed){let s=seed>>>0;return ()=>{s=(s*1664525+1013904223)>>>0;return s/2**32;};}
test('property: random reading against restarts, cold drains and growth never opens a silent gap',async()=>{
  for(let seed=1;seed<=24;seed++) {
    const rand=rng(seed),srv=server({drain:20+Math.floor(rand()*80)});srv.add(150+Math.floor(rand()*300));
    const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(20);
    const viewport=f.el('history-viewport'),trace=[];
    const check=final=>{
      const r=assertContiguous(f,'seed '+seed+' after '+trace.join(','));
      assert.ok(!(pressed(f) && f.api.state.blocked),'seed '+seed+': Following lit on a stopped view');
      assert.equal(f.api.state.blocked,false,'seed '+seed+': a listed conversation never stops after a restart');
      assert.equal(f.api.state.resyncWarn,null,'seed '+seed+': no orange line while the conversation is listed');
      if(final && !f.api.state.blocked && f.el('history-edge').hidden!==false && r.length)assert.equal(r.at(-1),srv.rows.length-1,'seed '+seed+': a silent end short of the head');
    };
    for(let step=0;step<30;step++) {
      const a=Math.floor(rand()*11);trace.push(a);
      if(a===0)srv.add(1+Math.floor(rand()*200));
      else if(a===1)srv.restart();
      else if(a===2){await f.api.list();const p=f.api.browse(f.api.state.inventory[0]);if(rand()<0.5)await p;}
      else if(a===3)f.el('history-reload').onclick();
      else if(a===4){f.api.state.scrollIntent=f.now();viewport.scrollTop=Math.floor(rand()*150);viewport.listeners.scroll();}
      else if(a===5){f.api.state.scrollIntent=f.now();viewport.scrollTop=viewport.scrollHeight;viewport.listeners.scroll();}
      else if(a===6)f.el('history-follow').onclick();
      else if(a===7)f.el('nav-user-prev').onclick();
      else if(a===8)f.doc.hidden=!f.doc.hidden;
      else if(a===9)await f.api.reconnect();
      for(let i=0,n=Math.floor(rand()*6);i<n;i++){await f.tick();check(false);}
      check(false);
    }
    f.doc.hidden=false;await f.tick(60);check(true);
  }
});

test('retired host bounds need no action: fallback continuations reach the newest row',async()=>{
  const srv=server({drain:40});srv.add(120);srv.restart();
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(4);
  assert.equal(resident(f).at(-1),119);
  const edge=f.el('history-edge');
  assert.equal(edge.hidden,true);assert.equal(edge.children.some(c=>c.tag==='button'),false);
  assert.equal(f.api.state.indexLimit,null);
  srv.add(10);
  await f.tick(6);
  assert.equal(resident(f).at(-1),129);assertContiguous(f,'unbounded fallback');
  assert.equal(edge.hidden,true);assert.ok(pressed(f));
});

// U5 uses real held transports, not a timer or a fabricated loading flag. The ordinary server above
// intentionally omits rowsAhead so the old-server compatibility/island assertions stay unchanged.
function u5Server(count,{tail=false}={}) {
  const srv=server();srv.add(count);const handle=srv.handle;srv.pages=[];
  srv.handle=url=>{
    if(!url.startsWith('/api/claude/events'))return handle(url);
    const q=new URLSearchParams(url.split('?')[1]);
    if(tail && !q.has('cursor') && !q.has('fromSeq'))url+='&fromSeq='+Math.max(0,srv.rows.length-100);
    const data=handle(url);
    if(!data.__status){data.rowsAhead=data.hasMore;srv.pages.push(data);}
    return data;
  };
  return srv;
}
function deferred() {let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
const settle=()=>new Promise(r=>setImmediate(r));
const loadingLine=f=>[f.el('history-edge').hidden,f.el('history-edge').dataset.kind,f.el('history-edge').textContent,f.el('history-follow-label').textContent];

test('T5.1 · metadata-only tail and held empty continuation stay quiet while Following and still page through',async()=>{
  const srv=u5Server(2),handle=srv.handle,held=deferred();let reads=0;
  srv.handle=url=>{
    if(!url.startsWith('/api/claude/events'))return handle(url);
    reads++;const data=handle(url.replace('cursor=metadata','cursor=c_1_2_forward_2'));
    if(reads===1)return {...data,hasMore:true,nextCursor:'metadata',rowsAhead:false};
    return held.promise.then(()=>({...data,events:[],hasMore:false,nextCursor:null,rowsAhead:false}));
  };
  const f=fixture(srv);await f.api.browse(srv.row());
  const quiet=()=>{assert.equal(f.el('history-edge').hidden,true);assert.equal(f.el('history-follow-label').textContent,'Following');assert.doesNotMatch(f.el('history-status').textContent,/Later pages available/);};
  quiet();assert.equal(f.api.state.next,'metadata');
  const loading=f.api.latest();await settle();quiet();assert.equal(reads,2,'incident continuation is not skipped');
  held.resolve();await loading;quiet();assert.equal(f.api.state.next,null);assert.equal(f.api.state.rowsAhead,false);
  assert.equal(resident(f).at(-1),1);assert.equal(reads,2);
});

test('T5.2 · real rows ahead still say Loading newer messages while Following',async()=>{
  const srv=u5Server(150),handle=srv.handle,held=deferred();let wait=false;
  srv.handle=url=>wait && url.startsWith('/api/claude/events')?held.promise.then(()=>handle(url)):handle(url);
  const f=fixture(srv);await f.api.browse(srv.row());
  assert.deepEqual(loadingLine(f),[false,'loading','Loading newer messages …','Following · loading newer']);
  assert.match(f.el('history-status').textContent,/Later pages available/);
  wait=true;const loading=f.api.page(f.api.state.next,'forward');await settle();
  assert.deepEqual(loadingLine(f),[false,'loading','Loading newer messages …','Following · loading newer']);
  held.resolve();await loading;assert.equal(f.el('history-edge').hidden,true);assert.equal(f.el('history-follow-label').textContent,'Following');
});

test('T5.3 · a held older read says older above, not newer, and clears after the page lands without eviction',async()=>{
  const srv=u5Server(150,{tail:true}),handle=srv.handle,held=deferred();let wait=false;
  srv.handle=url=>wait && url.startsWith('/api/claude/events')?held.promise.then(()=>handle(url)):handle(url);
  const f=fixture(srv);await f.api.browse(srv.row());assert.equal(resident(f)[0],50);assert.equal(f.api.state.rowsAhead,false);
  wait=true;const loading=f.api.loadOlder();await settle();
  assert.equal(f.api.state.loadingMode,'older');
  assert.deepEqual(loadingLine(f),[false,'loading','Loading older messages above …','Following · loading older']);
  held.resolve();await loading;
  assert.equal(resident(f)[0],0);assert.equal(resident(f).at(-1),149);assert.equal(f.api.state.loadingMode,null);
  assert.equal(f.api.state.rowsAhead,false,'ordinary older pages preserve the live forward witness');
  assert.equal(f.el('history-edge').hidden,true);assert.equal(f.el('history-follow-label').textContent,'Following');
});

test('T5.5 · first-page waits invite Send only for a writable Manager; indexing progress reuses sourceText in MB',async()=>{
  for(const writable of [true,false]) {
    const srv=u5Server(1),handle=srv.handle,held=deferred();
    srv.handle=url=>url.startsWith('/api/claude/events')?held.promise.then(()=>handle(url)):handle(url);
    const row=writable?srv.row():{...srv.row(),source:'native-transcript',role:'history',displayRole:'history',capabilities:{}};
    const f=fixture(srv),loading=f.api.browse(row);await settle();
    assert.deepEqual(loadingLine(f),[false,'loading',writable?'Still loading. Feel free to send messages while the load completes.':'Still loading …','Following · still loading']);
    assert.equal(f.api.state.loadingMode,'initial');held.resolve();await loading;
    assert.equal(f.el('history-edge').hidden,true);assert.equal(f.api.state.loadingMode,null);
  }
  const srv=u5Server(2),handle=srv.handle,held=deferred();let reads=0;
  srv.handle=url=>{
    if(!url.startsWith('/api/claude/events'))return handle(url);
    const data=handle(url);reads++;
    return reads===1?{...data,status:'indexing',hasMore:true,nextCursor:'c_1_2_forward_2',indexingContinuation:'c_1_2_forward_2',rowsAhead:true,
      sourceProgress:{kind:'host-raw',indexedBytes:1200000,sourceBytes:3600000}}:held.promise.then(()=>data);
  };
  const f=fixture(srv);await f.api.browse(srv.row());
  const expected='Still loading. Feel free to send messages while the load completes. · 1.2 MB / 3.6 MB indexed (raw stream)';
  assert.deepEqual(loadingLine(f),[false,'loading',expected,'Following · still loading']);
  const loading=f.api.page(f.api.state.next,'forward');await settle();
  assert.deepEqual(loadingLine(f),[false,'loading',expected,'Following · still loading']);
  held.resolve();await loading;assert.equal(f.el('history-edge').hidden,true);
});

test('T5.6 · native first-page timeouts are calm with the unchanged actual backoff; host cap, refusals and later failures stay unchanged',async()=>{
  const srv=u5Server(1),handle=srv.handle;let fail=true;
  srv.handle=url=>{if(fail && url.startsWith('/api/claude/events'))throw new Error('observer timeout');return handle(url);};
  const f=fixture(srv),history={...srv.row(),source:'native-transcript',role:'history',displayRole:'history',capabilities:{}};
  await f.api.browse(history);
  for(const seconds of [3,6,9,12,15,15]) {
    if(seconds!==3)await f.api.page(null,'initial');
    assert.equal(f.el('history-status').dataset.error,'false');assert.equal(f.api.state.blocked,false);
    assert.equal(f.el('history-status').textContent,`Loading history … 0 events · Retry in ${seconds} s: observer timeout`);
    assert.equal(f.api.state.nextPoll-f.now(),seconds*1000);assert.equal(f.api.state.loadingMode,null);
    assert.equal(f.el('history-edge').textContent,'Still loading …');
  }
  await f.api.browse(srv.row());await f.api.page(null,'initial');
  assert.equal(f.api.state.nextPoll-f.now(),3000,'host first-page retries retain the existing 3 s cap');
  assert.match(f.el('history-status').textContent,/Retry in 3 s/);assert.equal(f.el('history-status').dataset.error,'false');
  fail=false;await f.api.page(null,'initial');fail=true;await f.api.page(f.api.state.delta,'delta');
  assert.equal(f.el('history-status').dataset.error,'true','after a first page, a failure is still a problem');
  srv.handle=url=>url.startsWith('/api/claude/events')?{__status:403,body:env({status:'refused',error:'access refused'})}:handle(url);
  await f.api.browse(history);assert.equal(f.api.state.blocked,true);assert.equal(f.el('history-status').dataset.error,'true','refusals are never softened');
});

test('U5 ruling SUDXWG · dropped delta keeps known newer resident rows visible despite rowsAhead false on the page',async()=>{
  const srv=u5Server(120),f=fixture(srv);await f.api.browse(srv.row());await f.tick(5);
  assert.equal(f.api.state.rowsAhead,false);f.api.setFollow(false,{byScroll:true});f.el('history-viewport').scrollTop=2000;
  for(let i=0;i<40;i++){srv.add(10);await f.tick();}
  assert.equal(srv.pages.at(-1).rowsAhead,false);assert.equal(f.api.state.dropped,true);assert.ok(f.api.state.next);
  assert.equal(f.api.state.rowsAhead,true,'known rows beyond the resident end override the page-boundary flag');
  const rows=assertContiguous(f,'explicit rowsAhead under a delta flood');assert.ok(rows.includes(20));assert.equal(rows.length,200);assert.notEqual(rows.at(-1),519);
  assert.equal(f.el('history-edge').dataset.kind,'behind');assert.match(f.el('history-follow-label').textContent,/Paused.*newer below/);
  assert.ok(f.el('history-edge').children.some(n=>n.textContent==='Jump to latest'));
  f.api.setFollow(true);await f.tick(10);assert.equal(resident(f).at(-1),519);assert.equal(f.api.state.rowsAhead,false);assert.equal(f.el('history-edge').hidden,true);
});

test('U5 ruling SUDXWG · ordinary older preserves rowsAhead false; older eviction marks the resident window behind',async()=>{
  const tail=u5Server(330,{tail:true}),ordinary=fixture(tail);await ordinary.api.browse(tail.row());
  assert.deepEqual([resident(ordinary)[0],resident(ordinary).at(-1)],[230,329]);assert.equal(ordinary.api.state.rowsAhead,false);
  ordinary.api.setFollow(false,{byScroll:true});await ordinary.api.loadOlder();
  assert.equal(resident(ordinary).length,200);assert.equal(resident(ordinary).at(-1),329);assert.equal(tail.pages.at(-1).rowsAhead,true);
  assert.equal(ordinary.api.state.rowsAhead,false,'non-evicting older response cannot replace the forward witness');assert.equal(ordinary.el('history-edge').hidden,true);
  // Use the existing earlier-pages fixture's real forward checkpoint for the eviction case.
  const srv=u5Server(330),f=fixture(srv);await f.api.browse(srv.row());await f.api.latest();assert.equal(f.api.state.rowsAhead,false);
  f.api.setFollow(false,{byScroll:true});await f.api.loadOlder();
  assert.equal(f.api.state.dropped,true);assert.equal(f.api.state.rowsAhead,true);assert.ok(f.api.state.next);
  assert.equal(f.el('history-edge').dataset.kind,'behind');assert.match(f.el('history-follow-label').textContent,/Paused.*newer below/);
  assert.ok(resident(f).at(-1)<329);assertContiguous(f,'older eviction');
});

test('U5 · missing rowsAhead stays compatible; a switched generation owns both loading fields',async()=>{
  const srv=server();srv.add(150);const f=fixture(srv);await f.api.browse(srv.row());
  assert.equal(f.api.state.rowsAhead,null);assert.equal(f.el('history-edge').textContent,'Loading newer messages …');
  const held=deferred(),handle=srv.handle;let wait=true;
  srv.handle=url=>wait && url.startsWith('/api/claude/events')?held.promise.then(()=>handle(url)):handle(url);
  const old=f.api.page(f.api.state.next,'older');await settle();assert.equal(f.api.state.loadingMode,'older');
  wait=false;const fresh=f.api.browse({...srv.row(),source:'native-transcript'});await fresh;
  assert.equal(f.api.state.rowsAhead,null);assert.equal(f.api.state.loadingMode,null);
  held.resolve();await old;assert.equal(f.api.state.loadingMode,null);assert.equal(f.api.state.rowsAhead,null);
});

// U1.1 · Monday (2026-10-26): with every host call ~0.26 s the server's 12 s read deadline expired on each
// read. The rows already drawn must stay, with a quiet note that clears itself; the view never says
// "Not updating" just because the host is slow, and the new rows follow when it answers again.
test('U1.1 · a slow or failed history read never blanks the rows on screen: a quiet "Catching up" shows and clears by itself',async()=>{
  const srv=server();srv.add(120);
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(5);
  const before=resident(f),handle=srv.handle;assert.equal(before.at(-1),119);
  const deadline={__status:502,body:env({status:'read-failure',error:'pane host read deadline; native execution untouched'})};
  srv.handle=url=>url.startsWith('/api/claude/events')?deadline:handle(url);
  await f.tick(20);
  assert.deepEqual(resident(f),before,'every row on screen stays');
  assert.equal(f.api.state.blocked,false);assert.equal(f.el('history-edge').dataset.kind,'loading');
  assert.match(f.el('history-edge').textContent,/^Catching up … the rows on screen stay; newer messages follow by themselves\. Feel free to send messages meanwhile\./);
  assert.notEqual(f.el('history-follow-label').textContent,'Not updating');
  srv.handle=handle;srv.add(2);await f.tick(30);
  assert.equal(assertContiguous(f,'caught up').at(-1),121);
  assert.equal(f.api.state.catchingUp,null);assert.equal(f.el('history-edge').hidden,true);
});
test('U1.1 · a stale epoch from the server\'s replay retry, then a slow host, resyncs quietly in place: never "Not updating"',async()=>{
  const srv=server();srv.add(120);
  const f=fixture(srv);await f.api.list();await f.api.browse(srv.row());await f.tick(5);
  const before=resident(f),handle=srv.handle;let first=true;
  srv.handle=url=>{if(!url.startsWith('/api/claude/events'))return handle(url);
    if(first){first=false;return {__status:409,body:env({status:'stale-epoch',error:'host index evicted; reselect'})};}
    return {__status:502,body:env({status:'read-failure',error:'host unavailable; retained history is independent'})};};
  await f.tick(80); // well past the 30 s resync warning
  assert.deepEqual(resident(f),before,'every row on screen stays');
  assert.equal(f.api.state.resyncWarn,null,'a slow host is catching up, not a refused view');
  assert.notEqual(f.el('history-edge').dataset.kind,'stale');assert.notEqual(f.el('history-follow-label').textContent,'Not updating');
  srv.handle=handle;srv.add(1);await f.tick(40);
  assert.equal(assertContiguous(f,'resynced').at(-1),120);assert.equal(f.api.state.resync,null);assert.equal(f.api.state.catchingUp,null);
});
