// U4.4 · the facade's damaged-rows witness (a top-level Boolean `rowsUnreadable` on an events page: true only for
// the execution whose host rows answered unreadable). The viewer keeps Showing what the raw log still yields, adds
// ONE quiet line of its own beside the ordinary edge/status line ("Catching up" keeps its wording), never a warning,
// never a blanked row or a disabled control, clears it on the same conversation's next readable page (or a switch),
// and keeps it through a slow or failed read of that same conversation. Desktop (sessions.js) and phone (m.js).
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(process.env.HISTORY_TEST_SOURCE || path.join(__dirname,'../static/sessions.js'),'utf8');
const shared=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-record-text.js'),'utf8');
const core=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-preview-core.js'),'utf8');
const PHONE=fs.readFileSync(path.join(__dirname,'../static/m.js'),'utf8');
const DAMAGED='stored rows for this conversation are damaged; showing what the raw log allows';
const env=x=>({schemaVersion:1,backend:'claude',...x});

test('U4.4 damaged note is neutral text on its own line, not a new warning banner',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  assert.match(css,/#history-edge\[data-kind=notice\]\s*\{[^}]*border:0;[^}]*color:var\(--text-dim\)/);
  assert.match(css,/#history-edge \.edge-damaged\s*\{[^}]*flex-basis:100%;[^}]*color:var\(--text-dim\);[^}]*font-weight:400;/);
});

// ---- desktop: the real sessions.js in a Node VM over a facade-like events route --------------------------------
// `damaged` is the page's top-level witness: null = an old host that omits the field, false/true = the Boolean.
function server() {
  const s={rows:[],damaged:null,fail:false,reads:0};
  s.add=n=>{for(let i=0;i<n;i++)s.rows.push({n:s.rows.length,time:1792090000+60*s.rows.length});};
  const UUID='e524faa7-d4db-4719-b7de-133d7481daf9';
  s.row=(over={})=>({sessionRef:'sess1',scopeRef:'scope1',uuid:UUID,executionId:'cc-'+UUID,generation:1,role:'Manager',displayRole:'manager',
    source:'host',title:'Manager-Session-184',sessionLabel:'Manager-Session-184',cwd:'/r/manager-sessions',state:'ready',lifecycle:'live',createdAt:1792086607,
    capabilities:{nativeInput:true,controls:true,subagents:true},scopes:[{scopeRef:'scope1',label:'main'}],...over});
  const event=(r,seq,sessionRef)=>({recordRef:sessionRef+'r'+seq,uuid:sessionRef+'u'+r.n,scope:'main',kind:r.n%7===0?'user':'assistant',
    status:'stored',partial:false,text:'row '+r.n,calls:[],time:r.time,seq});
  s.handle=url=>{
    const q=new URLSearchParams(url.split('?')[1] || '');
    if(url.startsWith('/api/claude/sessions'))return env({sessions:[s.row()],status:'complete',hasMore:false});
    if(url==='/api/tool-selection')return env({epoch:'mail',revision:1,selection:null});
    if(!url.startsWith('/api/claude/events'))throw new Error('No live network: '+url);
    s.reads++;
    if(s.fail)return {__status:502,body:env({status:'read-failure',error:'pane host read deadline; native execution untouched'})};
    const sessionRef=q.get('sessionRef') || 'sess1', scopeRef=q.get('scopeRef') || 'scope1';
    const cursor=q.get('cursor'), start=cursor?Number(cursor.slice(2)):0;
    const end=Math.min(s.rows.length,start+100),more=end<s.rows.length;
    const body=env({sessionRef,scopeRef,fileEpoch:'epoch-'+sessionRef,leafRef:'leaf-'+sessionRef,targetRef:'target-'+sessionRef,
      events:s.rows.slice(start,end).map((r,i)=>event(r,start+i,sessionRef)),hasMore:more,nextCursor:more?'c_'+end:null,deltaCursor:'d_'+end,
      indexingContinuation:null,status:'complete',indexLimit:null,change:'snapshot',branches:[],source:'host',rowsAhead:false,liveState:{state:'ready'}});
    if(s.damaged!==null)body.rowsUnreadable=s.damaged;
    return body;
  };
  return s;
}
function desktopFixture(srv) {
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
  const context={Date:class extends Date{static now(){return clock;}},TextEncoder,TextDecoder,AbortController,URLSearchParams,Response,
    setTimeout:(f,ms)=>ms===0?queueMicrotask(f):0,clearTimeout(){},setInterval(){},
    BroadcastChannel:class{postMessage(){}},fetch:async url=>{
      if(url.startsWith('/api/opencode/'))return new Response(JSON.stringify({schemaVersion:1,backend:'opencode',status:'unavailable',error:'offline',sessions:[]}));
      const d=await srv.handle(url);return d.__status?new Response(JSON.stringify(d.body),{status:d.__status}):new Response(JSON.stringify(d));}};
  vm.runInNewContext(shared+'\n'+core+'\n'+source,context);
  const api=context.opcClaudeHistory.create(doc);
  return {api,el};
}
const edgeLines=f=>f.el('history-edge').children.filter(c=>c.textContent===DAMAGED);
const seqs=f=>f.api.state.order.map(k=>Number(String(k).replace(/^[A-Za-z0-9]+r/,'')));

test('U4.4 desktop · a damaged page keeps every row and adds exactly one quiet line, never a warning or an action',async()=>{
  const srv=server();srv.add(150);srv.damaged=true;
  const f=desktopFixture(srv);await f.api.list();await f.api.browse(srv.row());
  const edge=f.el('history-edge');
  assert.equal(f.api.state.rowsUnreadable,true);
  assert.equal(edge.hidden,false);
  assert.equal(edge.dataset.kind,'notice','neutral text, never a warning/loading kind');
  assert.equal(edge.children.some(c=>c.tag==='button'),false,'no Reconnect or other action appears');
  assert.equal(edgeLines(f).length,1);
  assert.equal(edgeLines(f)[0].className,'edge-text edge-damaged');
  assert.equal(edge.textContent,DAMAGED,'the note is the whole line while the view is otherwise quiet');
  assert.equal(f.api.state.order.length,100,'the rows the raw log allows stay on screen');
  assert.equal(f.el('history-follow-label').textContent,'Following');
  assert.equal(f.api.state.blocked,false);
  assert.equal(f.el('history-status').dataset.error,'false','the note is quiet: no status line is marked an error');
});

test('U4.4 desktop · repeated damaged pages still show one line and never blank a row',async()=>{
  const srv=server();srv.add(150);srv.damaged=true;
  const f=desktopFixture(srv);await f.api.browse(srv.row());
  const before=seqs(f);
  srv.add(20);await f.api.page(f.api.state.next,'forward');
  await f.api.page(f.api.state.delta,'delta');
  await f.api.page(null,'initial');
  const edge=f.el('history-edge');
  assert.equal(edgeLines(f).length,1,'one line, however many damaged pages arrive');
  assert.equal(edge.textContent,DAMAGED);
  assert.equal((edge.textContent.match(new RegExp(DAMAGED.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'g')) || []).length,1);
  const after=seqs(f);
  for(const seq of before)assert.ok(after.includes(seq),'row '+seq+' read before is still resident');
  assert.ok(after.length >= before.length);
});

test('U4.4 desktop · an ordinary page or an old host without the field shows no damaged line',async()=>{
  for(const [label,damaged] of [['an old host (the field absent)',null],['an ordinary readable page',false]]) {
    const srv=server();srv.add(5);srv.damaged=damaged;
    const f=desktopFixture(srv);await f.api.browse(srv.row());
    const edge=f.el('history-edge');
    assert.notEqual(f.api.state.rowsUnreadable,true,label);
    assert.equal(edgeLines(f).length,0,label);
    assert.doesNotMatch(edge.textContent,new RegExp(DAMAGED.slice(0,40)),label);
    assert.equal(edge.hidden,true,label);
    assert.equal(f.api.state.order.length,5,label);
  }
});

test('U4.4 desktop · the note coexists with a slow read\'s "Catching up", survives that failed read and clears on the repaired page',async()=>{
  const srv=server();srv.add(150);srv.damaged=true;
  const f=desktopFixture(srv);await f.api.browse(srv.row());
  const edge=f.el('history-edge');
  assert.equal(edgeLines(f).length,1);
  const before=seqs(f);
  // The host goes slow: the read answers 502 while the same conversation stays on screen.
  srv.fail=true;
  await f.api.page(f.api.state.next,'forward');
  assert.deepEqual(seqs(f),before,'every row on screen stays');
  assert.equal(f.api.state.rowsUnreadable,true,'a failed read never clears the witness');
  assert.equal(f.api.state.catchingUp!==null,true);
  assert.match(edge.textContent,/^Catching up … the rows on screen stay; newer messages follow by themselves\./,'the existing quiet line keeps its exact wording');
  assert.equal(edgeLines(f).length,1,'and the damaged line is its own, beside it');
  assert.notEqual(edge.dataset.kind,'stale');
  assert.equal(f.el('history-follow-label').textContent,'Following');
  // The host answers again, readable: both the catching-up note and the damaged line clear; new rows follow.
  srv.fail=false;srv.damaged=false;srv.add(2);
  await f.api.page(f.api.state.next,'forward');
  assert.equal(f.api.state.rowsUnreadable,false);
  assert.equal(f.api.state.catchingUp,null);
  assert.equal(edgeLines(f).length,0);
  assert.equal(edge.hidden,true);
  for(const seq of before)assert.ok(seqs(f).includes(seq),'row '+seq+' retained across the repair');
  assert.ok(seqs(f).includes(151),'the repaired page brings the new rows');
});

test('U4.4 desktop · a conversation switch drops the note at once, and another damaged conversation brings its own',async()=>{
  const srv=server();srv.add(20);srv.damaged=true;
  const f=desktopFixture(srv);await f.api.browse(srv.row());
  const edge=f.el('history-edge');assert.equal(edgeLines(f).length,1);
  srv.damaged=false;
  const other=srv.row({sessionRef:'sessB',scopes:[{scopeRef:'scopeB',label:'main'}]});
  await f.api.browse(other);
  assert.equal(f.api.state.rowsUnreadable,false);
  assert.equal(edgeLines(f).length,0,'no damaged line after the switch');
  assert.equal(edge.hidden,true);
  assert.ok(f.api.state.order.length>0,'its own rows show');
  assert.equal(f.api.state.session.sessionRef,'sessB');
  // Back to a damaged conversation: its own read brings the line back.
  srv.damaged=true;
  await f.api.browse(srv.row());
  assert.equal(edgeLines(f).length,1);
});

// ---- phone: the real m.js in a Node VM, a minimal DOM whose elements are plain objects -------------------------
function phoneNode(tag,id) {
  const node={tagName:tag,id:id || '',children:[],parentNode:null,dataset:{},attributes:{},style:{setProperty(){},removeProperty(){}},
    hidden:false,textContent:'',innerHTML:'',value:'',checked:false,disabled:false,scrollTop:0,scrollHeight:100,clientHeight:100,childElementCount:0,
    className:'',title:'',type:'',src:'',
    classList:{add(){},remove(){},toggle(){},contains(){return false;}},
    append(...items){for(const x of items){x.parentNode=node;node.children.push(x);}node.childElementCount=node.children.length;},
    prepend(x){x.parentNode=node;node.children.unshift(x);node.childElementCount=node.children.length;},
    insertBefore(x,next){x.parentNode=node;const at=next?node.children.indexOf(next):-1;if(at<0)node.children.push(x);else node.children.splice(at,0,x);node.childElementCount=node.children.length;},
    replaceChildren(...items){node.children=[];node.childElementCount=0;node.append(...items);},
    remove(){const p=node.parentNode;if(p){const at=p.children.indexOf(node);if(at>=0)p.children.splice(at,1);p.childElementCount=p.children.length;node.parentNode=null;}},
    setAttribute(k,v){node.attributes[k]=String(v);},getAttribute(k){return node.attributes[k] ?? null;},removeAttribute(k){delete node.attributes[k];},
    addEventListener(){},removeEventListener(){},dispatchEvent(){},focus(){},blur(){},scrollIntoView(){},closest(){return null;},
    querySelector(){return null;},querySelectorAll(){return [];},getClientRects(){return [];},getBoundingClientRect(){return {top:0,bottom:0,height:0};}};
  Object.defineProperty(node,'firstChild',{get:()=>node.children[0] || null});
  Object.defineProperty(node,'lastChild',{get:()=>node.children[node.children.length-1] || null});
  Object.defineProperty(node,'nextSibling',{get:()=>{
    const p=node.parentNode;if(!p)return null;return p.children[p.children.indexOf(node)+1] || null;}});
  return node;
}
const ROW={source:'host',role:'Manager',uuid:'11111111-1111-4111-8111-111111111111',executionId:'cc-11111111-1111-4111-8111-111111111111',
  generation:1,sessionRef:'sess1',scopes:[{label:'main',scopeRef:'scope1'}],lifecycle:'live',title:'Manager-Session-1',sessionLabel:'Manager-Session-1',
  createdAt:1792086607,capabilities:{nativeInput:true,controls:true}};
const managerPage=over=>env({source:'host',role:'Manager',executionId:ROW.executionId,generation:1,sessionRef:'sess1',scopeRef:'scope1',
  fileEpoch:'e1',leafRef:'leaf1',targetRef:'target1',events:[],eventUpdates:[],summaryUpdates:[],hasMore:false,nextCursor:null,deltaCursor:'d1',
  rowsAhead:false,indexLimit:null,change:'delta',status:'complete',traces:[],diagnostics:[],branches:[],liveState:{state:'ready'},...over});
const openCodePage=over=>({schemaVersion:1,backend:'opencode',source:'opencode',sessionRef:'ses_abcdefgh1234',scopeRef:'main',fileEpoch:'oe1',
  events:[],hasMore:false,nextCursor:null,deltaCursor:'od1',status:'complete',busy:false,runningCalls:[],...over});
const failure=()=>new Response(JSON.stringify(env({status:'read-failure',error:'pane host read deadline; native execution untouched'})),{status:502});

function phoneFixture() {
  const elements=new Map(),timers=new Map(),requests=[];let timerId=0;
  const el=id=>{if(!elements.has(id))elements.set(id,phoneNode('div',id));return elements.get(id);};
  const lines=phoneNode('div','m-lines');lines.append(el('m-read-status'));
  const doc={hidden:false,body:phoneNode('body','body'),documentElement:phoneNode('html','documentElement'),
    getElementById:id=>elements.get(id) || el(id),createElement:tag=>phoneNode(tag),createTextNode:text=>({textContent:text,children:[]}),addEventListener(){}};
  const answers={manager:()=>managerPage(),openCode:()=>openCodePage()};
  const storage={map:new Map(),get length(){return this.map.size;},key(i){return [...this.map.keys()][i] ?? null;},
    getItem(k){return this.map.get(k) ?? null;},setItem(k,v){this.map.set(k,String(v));},removeItem(k){this.map.delete(k);}};
  const ctx={console,URL,URLSearchParams,TextEncoder,TextDecoder,AbortController,DOMException,Response,crypto:webcrypto,localStorage:storage,
    document:doc,innerHeight:800,scrollY:0,scrollTo(){},requestAnimationFrame:()=>0,visualViewport:null,
    matchMedia:()=>({matches:false,addEventListener(){},addListener(){}}),
    performance:{now:()=>Date.now()},navigator:{clipboard:{writeText:async()=>{}}},
    location:{protocol:'http:',href:'http://pane.test/m',search:'',origin:'http://pane.test'},
    setTimeout:(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;},clearTimeout:id=>timers.delete(id),setInterval:()=>0,clearInterval(){},
    fetch:async url=>{requests.push(url);
      if(url.startsWith('/api/claude/events'))return answers.manager();
      if(url.startsWith('/api/opencode/events'))return answers.openCode();
      if(url.startsWith('/api/claude/sessions'))return new Response(JSON.stringify({schemaVersion:1,backend:'claude',status:'complete',hasMore:false,sessions:[]}));
      return new Response(JSON.stringify({schemaVersion:1,backend:'claude',status:'unavailable',error:'offline fixture: '+url}),{status:503});}};
  vm.createContext(ctx);
  vm.runInContext('globalThis.window=globalThis;',ctx);
  vm.runInContext(PHONE.replace(/\nphoneManager\.start\(\);\s*$/,'\n')+'\nglobalThis.__phone=phoneManager;',ctx);
  const phone=ctx.__phone;
  const settle=async(n=4)=>{for(let i=0;i<n;i++)await new Promise(r=>setImmediate(r));};
  const bindManagerChat=()=>{
    phone.state.viewing=null;phone.state.target={...ROW};phone.state.selected=ROW.executionId;phone.state.managers=[];
    phone.state.chat={revision:1,rows:new Map(),reader:phone.rules.READERS.manager,sessionRef:'sess1',scopeRef:'scope1',
      executionId:ROW.executionId,generation:1,fileEpoch:null,delta:null,next:null,lo:null,earlierEnd:null,seek:true,busy:false,loaded:false,start:false,
      top:null,hi:null,keep:null,paging:false};
    return phone.state.chat;
  };
  // One resident row, as applyEvents leaves it: it must survive every read of this conversation.
  const seedRow=()=>{
    const node=phoneNode('article'), chat=phone.state.chat;
    const row={key:'keep',node,head:phoneNode('div'),role:phoneNode('span'),time:phoneNode('span'),body:phoneNode('div'),speak:phoneNode('button'),
      calls:new Map(),thinking:new Map(),event:{kind:'user',uuid:'keep-uuid',text:'kept',seq:0,recordRef:'keep',calls:[]},kind:'m-user'};
    chat.rows.set('keep',row);el('m-messages').replaceChildren(node);
    return row;
  };
  const damagedCount=()=>{let n=0;const walk=list=>{for(const node of list){if(node.textContent===DAMAGED)n++;walk(node.children || []);}};walk(lines.children);return n;};
  return {phone,el,lines,answers,requests,settle,bindManagerChat,seedRow,damagedCount,timers};
}

test('U4.4 phone · a damaged page keeps the messages and shows exactly one quiet line beside the read status',async()=>{
  const f=phoneFixture(),chat=f.bindManagerChat(),row=f.seedRow();
  f.answers.manager=()=>new Response(JSON.stringify(managerPage({rowsUnreadable:true})));
  await f.phone.readChat();
  assert.equal(chat.damaged,true);
  assert.equal(f.damagedCount(),1);
  const lines=f.lines.children.filter(n=>n.textContent===DAMAGED);
  assert.equal(lines.length,1);
  // The read status keeps its own, ordinary wording (blank once rows are on screen): the damaged note never replaces it.
  assert.equal(f.el('m-read-status').textContent,'');
  assert.doesNotMatch(f.el('m-read-status').textContent,new RegExp(DAMAGED.slice(0,40)));
  // The rows on screen are untouched, never re-created or blanked.
  assert.equal(f.el('m-messages').children.length,1);
  assert.equal(f.el('m-messages').children[0],row.node);
  assert.equal(chat.rows.get('keep'),row);
  // Two more damaged pages: still one line, the same node, and the row stays.
  await f.phone.readChat();
  await f.phone.readChat();
  assert.equal(f.damagedCount(),1,'one line however many damaged pages arrive');
  assert.equal(f.lines.children.filter(n=>n.textContent===DAMAGED)[0],lines[0],'the same quiet line, not a growing stack');
  assert.equal(f.el('m-messages').children[0],row.node);
});

test('U4.4 phone · an ordinary page or an old host without the field shows no damaged line',async()=>{
  for(const [label,page] of [['an old host (the field absent)',()=>managerPage()],['an ordinary readable page',()=>managerPage({rowsUnreadable:false})]]) {
    const f=phoneFixture();f.bindManagerChat();
    f.answers.manager=()=>new Response(JSON.stringify(page()));
    await f.phone.readChat();
    assert.notEqual(f.phone.state.chat.damaged,true,label);
    assert.equal(f.damagedCount(),0,label);
    assert.equal(f.el('m-read-status').textContent,'No prose messages in this conversation yet.',label);
  }
});

test('U4.4 phone · the note coexists with "Catching up", survives a slow read and clears on the repaired page',async()=>{
  const f=phoneFixture(),chat=f.bindManagerChat(),row=f.seedRow();
  f.answers.manager=()=>new Response(JSON.stringify(managerPage({rowsUnreadable:true})));
  await f.phone.readChat();
  assert.equal(f.damagedCount(),1);
  // The same conversation's read is slow/refused: the existing quiet line says so, the damaged line stays beside it.
  f.answers.manager=()=>failure();
  await f.phone.readChat();
  assert.equal(f.el('m-read-status').textContent,'Catching up … the messages on screen stay; newer ones follow by themselves.');
  assert.equal(f.damagedCount(),1,'preserved through the failed read of the same conversation');
  assert.equal(f.el('m-messages').children[0],row.node,'the messages on screen stay');
  // The host answers again, readable: the note clears and the row is still there.
  f.answers.manager=()=>new Response(JSON.stringify(managerPage({rowsUnreadable:false})));
  await f.phone.readChat();
  assert.equal(chat.damaged,false);
  assert.equal(f.damagedCount(),0);
  assert.equal(f.el('m-messages').children[0],row.node);
});

test('U4.4 phone · a viewed read-only conversation carries the same note, and its ordinary page clears it',async()=>{
  const f=phoneFixture();f.bindManagerChat();
  f.answers.openCode=()=>new Response(JSON.stringify(openCodePage({rowsUnreadable:true})));
  f.phone.openSession({type:'open-session',schemaVersion:1,session:{backend:'opencode',id:'ses_abcdefgh1234',sessionRef:'ses_abcdefgh1234',
    scopeRef:'main',title:'a worker',label:'Worker'}});
  await f.settle();
  assert.equal(f.phone.state.viewing?.chat.damaged,true);
  assert.equal(f.damagedCount(),1);
  assert.equal(f.el('m-read-status').textContent,'No prose messages in this conversation yet.','the read status keeps its own line');
  f.answers.openCode=()=>new Response(JSON.stringify(openCodePage()));
  await f.phone.readChat();
  assert.equal(f.phone.state.viewing.chat.damaged,false);
  assert.equal(f.damagedCount(),0);
});

test('U4.4 phone · switching conversation clears the note at once; a damaged reopened conversation brings its own back',async()=>{
  const f=phoneFixture(),chat=f.bindManagerChat();
  f.answers.manager=()=>new Response(JSON.stringify(managerPage({rowsUnreadable:true})));
  await f.phone.readChat();
  assert.equal(f.damagedCount(),1);
  // Read › opens a different conversation (an OpenCode session, read-only): the note belongs to the old one.
  f.phone.openSession({type:'open-session',schemaVersion:1,session:{backend:'opencode',id:'ses_abcdefgh1234',sessionRef:'ses_abcdefgh1234',
    scopeRef:'main',title:'a worker',label:'Worker'}});
  assert.equal(f.damagedCount(),0,'cleared by the switch, before any read of the new conversation');
  await f.settle();
  assert.equal(f.damagedCount(),0);
  assert.equal(f.phone.state.viewing?.sessionRef,'ses_abcdefgh1234');
  // Back to the damaged Manager conversation: its own read shows the line again, one only.
  f.phone.state.viewing=null;f.bindManagerChat();
  const back=f.phone.state.chat;
  f.answers.manager=()=>new Response(JSON.stringify(managerPage({rowsUnreadable:true})));
  await f.phone.readChat();
  assert.equal(back.damaged,true);
  assert.equal(f.damagedCount(),1);
});
