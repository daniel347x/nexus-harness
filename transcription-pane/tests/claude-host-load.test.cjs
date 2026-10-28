// U4.5 host load caption: the static #sessions-host-load line in .side-top is painted from the
// FIRST /api/claude/sessions inventory answer (never live=1 activity reads, never continuation
// pages, never a new health request). The line is pure information: no control changes, no
// mutation, no dialog, and 📦 History is never a live source.
//
// Runtime path only: these tests call the exported list()/poll()/setView() on the real
// sessions.js in an inert VM DOM (modeled on claude-live-sessions.test.cjs, trimmed to the
// sidebar/caption surface this change needs).
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/sessions.js'),'utf8');
const shared=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-record-text.js'),'utf8');
const core=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-preview-core.js'),'utf8');
const env=x=>({schemaVersion:1,backend:'claude',...x});
const CAPTION='sessions-host-load';
const host={sessionRef:'host',uuid:'native-uuid',executionId:'cc-native-uuid',storeRef:'store-1',generation:2,role:'manager',source:'host',
  title:'Manager',cwd:'fixture',state:{},capabilities:{},scopes:[{scopeRef:'main',label:'main'}]};
const pageRow=(n)=>({...host,sessionRef:'host-'+n,uuid:'native-'+n,executionId:'cc-native-'+n});
const emptyEvents=env({sessionRef:'host',scopeRef:'main',fileEpoch:'epoch',leafRef:'leaf',targetRef:null,events:[],branches:[],traces:[],
  diagnostics:[],status:'complete',change:'snapshot',hasMore:false,deltaCursor:null});
function fixture(transport,{sidebarOnly=false}={}) {
  const nodes=new Map(),requests=[];
  class Node {
    constructor(tag='div'){this.tag=tag;this.children=[];this.parentNode=null;this.content='';this.attributes={};this.dataset={};this.value='';
      this.scrollTop=0;this.clientHeight=100;this.isConnected=true;this.disabled=false;this.checked=false;this.title='';
      this.style={setProperty:(k,v)=>{this.attributes[k]=v;}};
      const cls=new Set();this.classList={add:x=>cls.add(x),remove:x=>cls.delete(x),toggle:(x,on)=>on?cls.add(x):cls.delete(x),contains:x=>cls.has(x)};}
    get hidden(){return Object.hasOwn(this.attributes,'hidden');}
    set hidden(v){if(v)this.attributes.hidden='';else delete this.attributes.hidden;}
    set textContent(v){this.content=v;this.replaceChildren();}
    get textContent(){return this.content+this.children.map(x=>x.textContent).join('');}
    append(...items){for(const x of items){if(x.tag==='fragment'){this.append(...[...x.children]);continue;}x.remove();x.parentNode=this;this.children.push(x);}}
    prepend(x){x.remove();x.parentNode=this;this.children.unshift(x);}
    insertBefore(x,next){x.remove();x.parentNode=this;this.children.splice(next?this.children.indexOf(next):this.children.length,0,x);}
    replaceChildren(...items){for(const x of this.children)x.parentNode=null;this.children=[];this.append(...items);}
    remove(){if(this.parentNode){const p=this.parentNode;p.children.splice(p.children.indexOf(this),1);this.parentNode=null;}}
    setAttribute(k,v){this.attributes[k]=v;}
    removeAttribute(k){delete this.attributes[k];}
    getAttribute(k){return Object.hasOwn(this.attributes,k)?this.attributes[k]:null;}
    get scrollHeight(){return 100;}
    getBoundingClientRect(){return {top:0,bottom:100,left:0,right:100,width:100,height:100};}
    checkVisibility(){return !this.hidden;}
  }
  const el=id=>{if(!nodes.has(id)){const node=new Node();node.id=id;nodes.set(id,node);}return nodes.get(id);};
  // The caption is STATIC page markup inside .side-top: the page never invents or moves it.
  const sideTop=new Node('div');sideTop.className='side-top';
  const caption=new Node('p');caption.id=CAPTION;caption.hidden=true;sideTop.append(caption);nodes.set(CAPTION,caption);
  const doc={hidden:false,body:{dataset:{view:sidebarOnly?'programs':''}},getElementById:el,
    querySelector:sel=>String(sel).startsWith('#')?el(String(sel).slice(1)):null,
    createElement:t=>new Node(t),createDocumentFragment:()=>new Node('fragment'),getSelection:()=>null,
    addEventListener:()=>{},defaultView:{localStorage:{getItem:()=>null,setItem(){},removeItem(){}}}};
  const context={Date,TextEncoder,TextDecoder,AbortController,URLSearchParams,Response,
    setTimeout:(f,ms)=>ms===0?queueMicrotask(f):0,clearTimeout(){},setInterval(){},
    BroadcastChannel:class{postMessage(){}},
    fetch:async(u,opts={})=>{
      const url=String(u),method=opts?.method||'GET';requests.push([url,method]);
      const body=data=>new Response(JSON.stringify(data));
      if(url==='/api/pins')return body({schemaVersion:1,pins:{}});
      if(url.startsWith('/api/telemetry'))return body({schemaVersion:1,backend:'telemetry',status:'unavailable',error:'offline fixture',sessions:{}});
      if(url.startsWith('/api/opencode/'))return body({schemaVersion:1,backend:'opencode',status:'complete',observedAt:Date.now()/1000,sessions:[],active:[],messages:[]});
      if(url.startsWith('/api/history/programs'))return body({schemaVersion:1,backend:'history',status:'empty',units:[],claude:[],opencode:[],hasMore:false,
        total:{units:0,sessions:0},archive:{lastSweepAt:null}});
      if(url.startsWith('/api/claude/turns'))return body(env({uuid:new URLSearchParams(url.split('?')[1]||'').get('uuid'),turnFacts:null}));
      if(url.startsWith('/api/claude/subagents'))return body(env({sessions:[],hasMore:false,status:'complete'}));
      if(url.startsWith('/api/claude/events'))return body(emptyEvents);
      if(url.startsWith('/api/claude/sessions')){
        const q=new URLSearchParams(url.split('?')[1]||'');
        // loadManagerFamily's own unwindowed read is a different lane, never the inventory's first answer.
        if(q.get('role')==='Manager')return body(env({sessions:[],status:'complete',hasMore:false}));
        const data=await transport?.(url,opts,q);
        return body(data===undefined?env({sessions:[host],status:'complete',hasMore:false}):data);
      }
      throw new Error('No live network: '+url);
    }};
  vm.runInNewContext(shared+'\n'+core+'\n'+source,context);
  const api=context.opcClaudeHistory.create(doc);
  const flush=async(times=4)=>{for(let i=0;i<times;i++)await new Promise(r=>setImmediate(r));};
  return {api,doc,el,requests,flush,caption:()=>el(CAPTION)};
}
const captionText=f=>f.caption().textContent;
const captionHidden=f=>{const node=f.caption();return node.hidden===true||node.style?.display==='none'||node.style?.visibility==='hidden';};
const inventoryReads=f=>f.requests.filter(([url])=>/^\/api\/claude\/sessions\?/.test(url)&&!/[?&]role=Manager\b/.test(url)&&!/[?&]live=1\b/.test(url));
const liveReads=f=>f.requests.filter(([url])=>/^\/api\/claude\/sessions\?/.test(url)&&/[?&]live=1\b/.test(url));
const shown=(f,text)=>{assert.equal(captionText(f),text,'the caption reads exactly this');assert.equal(captionHidden(f),false,'the caption is visible');};
const cleared=f=>{assert.equal(captionText(f),'','the caption is empty');assert.equal(captionHidden(f),true,'the caption is hidden');};
test('the first inventory answer paints one caption: behind sets beat loop lag and always read "conversations"',async()=>{
  let answer={rows_behind_sets:1,loop_lag_ms_max_60s:9000};
  const f=fixture(async(url,opts,q)=>q.get('live')==='1'?undefined:env({sessions:[host],status:'complete',hasMore:false,hostLoad:answer}));
  await f.api.list();await f.flush();
  shown(f,'host catching up · 1 conversations');
  answer={rows_behind_sets:0,loop_lag_ms_max_60s:1001};
  await f.api.list();await f.flush();
  shown(f,'host busy · lag 1.0 s');
  answer={rows_behind_sets:0,loop_lag_ms_max_60s:2500};
  await f.api.list();await f.flush();
  shown(f,'host busy · lag 2.5 s');
  assert.equal(inventoryReads(f).length,3,'each list() is one inventory read on this fixture');
});
test('exactly 1000 ms is not busy; zero, null and an older host without a load fact all clear it',async()=>{
  let answer={rows_behind_sets:0,loop_lag_ms_max_60s:1000};
  const f=fixture(async()=>env({sessions:[host],status:'complete',hasMore:false,...(answer===undefined?{}:{hostLoad:answer})}));
  await f.api.list();await f.flush();
  cleared(f);
  answer={rows_behind_sets:2,loop_lag_ms_max_60s:0};
  await f.api.list();await f.flush();
  shown(f,'host catching up · 2 conversations');
  answer={rows_behind_sets:0,loop_lag_ms_max_60s:0};
  await f.api.list();await f.flush();
  cleared(f);
  answer={rows_behind_sets:2,loop_lag_ms_max_60s:0};
  await f.api.list();await f.flush();
  shown(f,'host catching up · 2 conversations');
  answer=null; // an older host that answers hostLoad:null
  await f.api.list();await f.flush();
  cleared(f);
  answer=undefined; // and one that omits the field entirely
  await f.api.list();await f.flush();
  cleared(f);
});
test('only finite nonnegative facts paint: negative, NaN, Infinity, strings and missing values are no load',async()=>{
  let answer;
  const f=fixture(async()=>env({sessions:[host],status:'complete',hasMore:false,hostLoad:answer}));
  for(const bad of [{rows_behind_sets:-2,loop_lag_ms_max_60s:-1},{rows_behind_sets:NaN,loop_lag_ms_max_60s:NaN},
    {rows_behind_sets:Infinity,loop_lag_ms_max_60s:Infinity},{rows_behind_sets:'3',loop_lag_ms_max_60s:'5000'},
    {rows_behind_sets:undefined,loop_lag_ms_max_60s:undefined}]){
    answer=bad;await f.api.list();await f.flush();cleared(f);
  }
});
test('continuation pages never clear or replace the first page\'s load, and only the first page paints',async()=>{
  let continuation={rows_behind_sets:0,loop_lag_ms_max_60s:9000};
  const f=fixture(async(url,opts,q)=>q.get('live')==='1'?undefined:q.get('cursor')
    ?env({sessions:[pageRow(2)],status:'complete',hasMore:false,...(continuation===undefined?{}:{hostLoad:continuation})})
    :env({sessions:[host],status:'complete',hasMore:true,nextCursor:'inventory-2',hostLoad:{rows_behind_sets:3,loop_lag_ms_max_60s:0}}));
  await f.api.list();await f.flush();
  shown(f,'host catching up · 3 conversations');
  assert.equal(f.api.state.inventory.length,2,'both pages were read');
  assert.equal(inventoryReads(f).length,2);
  continuation=null; // the continuation's own snapshot carries no load at all
  await f.api.list();await f.flush();
  shown(f,'host catching up · 3 conversations');
  // A first page with no load never inherits one from a later continuation page.
  const g=fixture(async(url,opts,q)=>q.get('live')==='1'?undefined:q.get('cursor')
    ?env({sessions:[pageRow(2)],status:'complete',hasMore:false,hostLoad:{rows_behind_sets:2,loop_lag_ms_max_60s:0}})
    :env({sessions:[host],status:'complete',hasMore:true,nextCursor:'inventory-2'}));
  await g.api.list();await g.flush();
  cleared(g);
});
test('live=1 activity reads never paint the caption',async()=>{
  const f=fixture(async(url,opts,q)=>q.get('live')==='1'
    ?env({sessions:[host],status:'complete',hasMore:false,hostLoad:{rows_behind_sets:0,loop_lag_ms_max_60s:7000}})
    :env({sessions:[host],status:'complete',hasMore:false,hostLoad:{rows_behind_sets:2,loop_lag_ms_max_60s:0}}));
  await f.api.list();await f.flush();
  shown(f,'host catching up · 2 conversations');
  f.api.state.inventoryNextPoll=Date.now()+60000;f.api.state.hostActivityNextPoll=0;
  await f.api.poll();await f.flush(8);
  assert.equal(liveReads(f).length,1,'the activity lane did read once');
  shown(f,'host catching up · 2 conversations');
});
test('a failed first page clears the caption instead of leaving a stale "catching up"',async()=>{
  let fail=false;
  const f=fixture(async()=>{
    if(fail)throw Object.assign(new Error('inventory offline'),{code:503});
    return env({sessions:[host],status:'complete',hasMore:false,hostLoad:{rows_behind_sets:5,loop_lag_ms_max_60s:0}});
  });
  await f.api.list();await f.flush();
  shown(f,'host catching up · 5 conversations');
  fail=true;await f.api.list();await f.flush();
  cleared(f);
  assert.match(f.el('sessions-status').textContent,/List unavailable/);
});
test('📦 History hides the caption and reads no live inventory while it is the source',async()=>{
  const f=fixture(async(url,opts,q)=>q.get('live')==='1'?undefined:env({sessions:[host],status:'complete',hasMore:false,hostLoad:{rows_behind_sets:2,loop_lag_ms_max_60s:0}}));
  await f.api.list();await f.flush();
  shown(f,'host catching up · 2 conversations');
  await f.api.setView('history');await f.flush();
  cleared(f);
  const before=f.requests.length;
  f.api.state.inventoryNextPoll=0;f.api.state.hostActivityNextPoll=0;
  await f.api.poll();await f.flush(8);
  const during=f.requests.slice(before).map(([url])=>url);
  assert.equal(during.filter(url=>url.startsWith('/api/claude/sessions')).length,0,'no live inventory read in History');
  assert.equal(during.filter(url=>url.startsWith('/api/opencode/')).length,0,'no live OpenCode read in History');
  cleared(f);
  await f.api.setView('week');await f.flush();
  shown(f,'host catching up · 2 conversations');
});
test('desktop and phone (sidebarOnly) list paths paint the same line, disable no control and add no route',async()=>{
  for(const sidebarOnly of [false,true]){
    let answer={rows_behind_sets:1,loop_lag_ms_max_60s:1200};
    const f=fixture(async(url,opts,q)=>q.get('live')==='1'?undefined:env({sessions:[host],status:'complete',hasMore:false,...(answer?{hostLoad:answer}:{})}),{sidebarOnly});
    const ids=['sessions-refresh','sessions-role','sessions-cycle','sessions-collapse','sessions-expand','sessions-view'];
    const controls=ids.map(id=>{const node=f.el(id);node.disabled=false;return node;});
    await f.api.list();await f.flush();
    shown(f,'host catching up · 1 conversations');
    assert.equal(f.caption().tag,'p','the caption is its own static line, never a control');
    assert.equal(f.caption().disabled,false);
    assert.ok(controls.every(node=>!node.disabled),'no sidebar control is disabled while the caption shows');
    answer={rows_behind_sets:0,loop_lag_ms_max_60s:0};
    await f.api.list();await f.flush();
    cleared(f);
    assert.ok(controls.every(node=>!node.disabled),'no sidebar control is disabled when it clears');
    assert.equal(inventoryReads(f).length,2);
    assert.ok(f.requests.every(([,method])=>method==='GET'),'the caption work sends no mutation');
    assert.ok(f.requests.every(([url])=>!/health|host-?load|hostload|loop[_-]?lag/i.test(url)),'the caption work adds no health/load route');
  }
});
