// 📦 History mode in the shared sidebar (MasterSpec [ap:UHKPV1] §3.8, §7 U2.3): the one data-source switch, its live-read
// gates, whole-unit paging behind the existing Show more foot and the History glance, through the existing renderers.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/sessions.js'),'utf8');
const shared=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-record-text.js'),'utf8');
const core=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-preview-core.js'),'utf8');
const U=n=>String(n).padStart(8,'0')+'-0000-4000-8000-000000000000';
const b64u=text=>Buffer.from(text).toString('base64url');
const CAPS={history:true,live:false,context:false,inspect:false,nativeInput:false,controls:false,subagents:false,reconnect:false};
// One archived row as /api/history/* answers it (§3.8's fixed values on both kinds).
const row=(key,over)=>({storeRef:'history',sessionRef:'h_'+b64u(key),targetRef:null,fileEpoch:'h_0123456789abcdef',scopes:[{label:'main',scopeRef:'h_'+b64u(key+':main')}],
  lifecycle:'ended',liveState:null,lastContext:null,successor:null,capabilities:CAPS,archived:{key,archivedAt:1792001000,prunedAt:null},telemetry:null,...over});
const facts={status:'complete',records:4,models:['claude-opus-5-5'],levels:['high'],modelsComplete:true,levelsComplete:true,firstAt:1792000000,lastAt:1792000300};
const tele=sid=>({sid,segments:[{segment:0,kind:'fresh',short:'50 (0) → 60 KT (3 min)',full:'start-up 50 KT · end 60 KT',flags:[],notes:[]}]});
const host=(n,lineage,over={})=>row('claude:cc-'+U(n),{source:'host',uuid:U(n),executionId:'cc-'+U(n),generation:1,role:'worker',displayRole:'quick-worker',state:'exited',status:'exited',
  title:lineage.title,cwd:'/r/quick-worker',createdAt:1792000000+n,lineage,turnFacts:facts,telemetry:tele('cc-'+U(n)),...over});
const TITLE='[ap:HISTRY]-001 · ⚡ archived program';
const cond=host(1,{program:'HISTRY',kind:'conductor',conductorN:1,quickProgram:true,title:TITLE},{role:'conductor',displayRole:'conductor'});
const seat=host(2,{program:'HISTRY',kind:'seat',conductorN:1,conductorId:'cc-'+U(1),quick:true,title:'[ap:HISTRY]-001 ▸ ⚡ quick worker · the seat'},{capabilities:{...CAPS,subagents:true}});
const sub=row('claude:cc-'+U(2)+'/agent-a1',{source:'native',uuid:U(2),parentUuid:U(2),agentId:'a1',role:'subagent',title:'seat ▸ Explore',status:'complete',state:'unmanaged-history',executionId:null,generation:null,cwd:'/r',createdAt:1792000010});
const oc=(id,over)=>row('opencode:'+id,{id,busy:false,createdAt:1792000100,updatedAt:1792000200,turnFacts:{status:'complete',firstAt:1792000100,lastAt:1792000200},...over});
const helper=oc('ses_helper0000001',{kind:'helper',program:'HISTRY',parentSeat:'cc-'+U(2),title:'helper',telemetry:tele('ses_helper0000001')});
const child=oc('ses_child00000001',{kind:'subagent',program:'HISTRY',parentId:'ses_helper0000001',title:'a subagent'});
const plain=oc('ses_plainroot0001',{kind:'seat',title:'a plain session'}),plainKid=oc('ses_plainkid00001',{kind:'subagent',parentId:'ses_plainroot0001',title:'its child'});
const orphan=oc('ses_orphankid0001',{kind:'subagent',parentId:'ses_goneparent0001',title:'orphan'});
const unit=(key,sessions)=>({unit:key,kind:key.split(':')[0],program:key.startsWith('program:')?key.slice(8):null,firstAt:1792000000,lastAt:1792000500,sessions,sealedAt:null,costUsd:null,telemetry:null});
const page=(units,claude,opencode,next=null)=>({schemaVersion:1,backend:'history',status:'complete',units,claude,opencode,hasMore:!!next,nextBefore:next,total:{units:4,sessions:9},archive:{lastSweepAt:1792002000}});
const PAGE1=page([unit('session:opencode:ses_goneparent0001',1),unit('session:opencode:ses_plainroot0001',2),unit('program:HISTRY',5)],[cond,seat,sub],[helper,child,plain,plainKid,orphan],'cursor2');
const native=row('claude:cc-'+U(9),{source:'native',uuid:U(9),role:'history',title:'An unmanaged transcript',status:'complete',state:'unmanaged-history',executionId:null,generation:null,cwd:'/r/quick-worker',createdAt:1791000000,turnFacts:facts});
const PAGE2=page([unit('session:claude:cc-'+U(9),1)],[native],[]);

function fixture({sidebarOnly=false}={}) {
  const nodes=new Map(),live=[],history=[];
  class Node {
    constructor(tag='div'){this.tag=tag;this.children=[];this.parentNode=null;this.content='';this.attributes={};this.dataset={};this.value='';this.scrollTop=0;this.clientHeight=100;this.style={setProperty:(k,v)=>this.attributes[k]=v};const cls=new Set();this.classList={add:x=>cls.add(x),toggle:(x,on)=>on?cls.add(x):cls.delete(x),contains:x=>cls.has(x)};}
    set textContent(v){this.content=v;this.replaceChildren();}get textContent(){return this.content+this.children.map(x=>x.textContent).join('');}
    append(...items){for(const x of items){if(x.tag==='fragment'){this.append(...[...x.children]);continue;}x.remove();x.parentNode=this;this.children.push(x);}}
    prepend(x){x.remove();x.parentNode=this;this.children.unshift(x);}
    insertBefore(x,next){x.remove();x.parentNode=this;this.children.splice(next?this.children.indexOf(next):this.children.length,0,x);}
    replaceChildren(...items){for(const x of this.children)x.parentNode=null;this.children=[];this.append(...items);}
    remove(){if(this.parentNode){const p=this.parentNode;p.children.splice(p.children.indexOf(this),1);this.parentNode=null;}}
    setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}
    getBoundingClientRect(){return {top:0,bottom:100};}
  }
  const el=id=>{if(!nodes.has(id)){const node=new Node();node.id=id;nodes.set(id,node);}return nodes.get(id);};
  const doc={hidden:false,activeElement:null,body:{dataset:{view:sidebarOnly?'programs':''}},getElementById:el,createElement:t=>new Node(t),createDocumentFragment:()=>new Node('fragment'),getSelection:()=>null,addEventListener(){}};
  const answers={history:null,glance:null};
  const context={TextEncoder,TextDecoder,AbortController,URLSearchParams,Response,setTimeout:(f,ms)=>ms===0?queueMicrotask(f):0,clearTimeout(){},setInterval(){},
    BroadcastChannel:class{postMessage(){}},fetch:async url=>{
      if(url.startsWith('/api/history/')){history.push(url);const d=url.startsWith('/api/history/glance')?answers.glance(url):answers.history(url);return new Response(JSON.stringify(d));}
      live.push(url);
      if(url.startsWith('/api/claude/sessions'))return new Response(JSON.stringify({schemaVersion:1,backend:'claude',status:'complete',hasMore:false,sessions:[{sessionRef:'live-row',uuid:U(50),executionId:'cc-'+U(50),generation:1,source:'host',role:'manager',title:'Live Manager',cwd:'/r',state:'ready',lifecycle:'live',capabilities:{},scopes:[{label:'main',scopeRef:'main'}]}]}));
      if(url.startsWith('/api/opencode/programs'))return new Response(JSON.stringify({schemaVersion:1,backend:'opencode',status:'complete',sessions:[],observedAt:Date.now()/1000}));
      if(url==='/api/pins')return new Response(JSON.stringify({schemaVersion:1,pins:{}}));
      return new Response(JSON.stringify({schemaVersion:1,backend:'opencode',status:'unavailable',error:'offline fixture',sessions:[],active:[]}));
    }};
  vm.runInNewContext(shared+'\n'+core+'\n'+source,context);
  const api=context.opcClaudeHistory.create(doc);
  return {api,el,live,history,answers,flush:()=>new Promise(r=>setImmediate(r))};
}
const find=(root,cls)=>{const out=[];const walk=n=>{if(String(n.className).split(' ').includes(cls))out.push(n);for(const c of n.children||[])walk(c);};walk(root);return out;};
const keysOf=api=>[...api.state.inventory,...api.state.oc.rows].map(r=>r.archived?.key || r.sessionRef).sort();

test('📦 History: nothing before it is picked; picking it stops every live read and draws whole units through the existing builders',async()=>{
  const f=fixture();f.answers.history=url=>url.includes('before=')?PAGE2:PAGE1;
  await f.api.list();await f.api.ocList();await f.flush();
  assert.equal(f.history.length,0,'no /api/history/* request before History is picked');
  assert.equal(f.api.setView(),'week','a new page starts on Last 7 days');
  await f.api.setView('history');await f.flush();
  assert.equal(f.api.setView(),'history');assert.equal(f.el('sessions-view').value,'history');
  assert.deepEqual(f.history,['/api/history/programs'],'page 1 only');
  assert.deepEqual(keysOf(f.api),[cond,seat,sub,helper,child,plain,plainKid,orphan].map(r=>r.archived.key).sort(),'the live rows are gone; the page rows are the source');
  const tree=f.api.state.tree,program=tree.programs.find(p=>p.core==='HISTRY');
  assert.ok(program,'lineageTree drew the archived program');
  assert.deepEqual([...program.members.map(m=>m.id)].sort(),['cc-'+U(1),'cc-'+U(2),'ses_child00000001','ses_helper0000001'].sort(),'the whole program: Conductor, seat, helper and its child');
  assert.equal(tree.byId.get('ses_helper0000001').parent?.id,'cc-'+U(2),'the helper sits under its lead seat');
  assert.equal(tree.byId.get('ses_plainkid00001').parent?.id,'ses_plainroot0001','an unprogrammed OpenCode session and its child');
  assert.equal(tree.byId.get('ses_orphankid0001').parent,null,'a child whose parent is not archived is its unit root');
  assert.match(tree.byId.get('ses_orphankid0001').note || '',/not listed/);
  assert.equal(program.facts.nameFade,null,'no list window: no name fade');
  assert.ok(f.api.state.tele.get('cc-'+U(1))?.segments,'telemetry from the page row');
  assert.equal(f.api.state.ocTurns.get('ses_helper0000001')?.data?.lastAt,1792000200,'OpenCode turn times from the page row');
  assert.match(f.el('sessions-freshness').textContent,/📦 History · 3 of 4 units/);
  const ocStatus=find(f.el('sessions-list'),'meta-status').map(n=>n.textContent).join(' ');
  assert.doesNotMatch(ocStatus,/activity unknown/,'an archived row carries no live-evidence wording');
  const liveAt=f.live.length;
  for(let i=0;i<4;i++){await f.api.poll();await f.flush();}
  await f.api.list();await f.api.ocList();f.api.detailFlush();await f.flush();
  assert.deepEqual(f.live.slice(liveAt),[],'no inventory, live poll, OpenCode list or activity, glance, turns or telemetry read in History');
});

test('📦 History: the existing Show more foot loads the next page when the loaded units run out, each unit exactly once',async()=>{
  const f=fixture();f.answers.history=url=>url.includes('before=')?PAGE2:PAGE1;
  await f.api.setView('history');await f.flush();
  const more=find(f.el('sessions-list'),'group-foot').flatMap(n=>n.children).find(b=>/from 📦 History/.test(b.textContent));
  assert.ok(more,'the foot offers the archive’s older units');
  more.onclick();await f.flush();await f.flush();
  assert.deepEqual(f.history,['/api/history/programs','/api/history/programs?before=cursor2']);
  assert.equal(f.api.state.archive.units.length,4);assert.equal(new Set(f.api.state.archive.units.map(u=>u.unit)).size,4,'every unit exactly once');
  assert.ok(f.api.state.inventory.some(r=>r.uuid===U(9)),'the next page joins the same source');
  assert.match(f.el('sessions-freshness').textContent,/the whole archive/);
  assert.equal(find(f.el('sessions-list'),'group-foot').flatMap(n=>n.children).some(b=>/from 📦 History/.test(b.textContent)),false,'nothing more to load');
  assert.equal(await f.api.historyPage(),false);assert.equal(f.history.length,2,'the archive is not read again');
});

test('📦 History: a glance reads /api/history/glance by the archive key and checks data.key; leaving restores the live list',async()=>{
  const f=fixture();f.answers.history=()=>PAGE1;
  f.answers.glance=url=>({schemaVersion:1,backend:'history',status:'complete',key:new URLSearchParams(url.split('?')[1]).get('key'),messages:[{role:'user',time:1792000000,chars:5,text:'hello'}],call:null,lastAt:1792000300,model:'claude-opus-5-5',partial:false});
  await f.api.setView('history');await f.flush();
  await f.api.toggleGlance(U(1));await f.flush();
  assert.deepEqual(f.history.slice(1),['/api/history/glance?key='+encodeURIComponent('claude:cc-'+U(1))]);
  assert.equal(f.api.state.glances.get(U(1)).data.messages[0].text,'hello');
  await f.api.toggleGlance('ses_helper0000001');await f.flush();
  assert.equal(f.history.at(-1),'/api/history/glance?key='+encodeURIComponent('opencode:ses_helper0000001'));
  f.answers.glance=()=>({schemaVersion:1,backend:'history',status:'complete',key:'claude:cc-'+U(2),messages:[],call:null,partial:false});
  await f.api.toggleGlance(U(1));await f.api.toggleGlance(U(1));await f.flush();
  assert.match(f.api.state.glances.get(U(1)).error || '',/invalid History glance/,'an answer for another key is refused');
  const liveAt=f.live.length;
  await f.api.setView('week');await f.flush();
  assert.equal(f.api.state.archive,null);assert.equal(f.api.setView(),'week');
  assert.ok(f.live.slice(liveAt).some(url=>url.startsWith('/api/claude/sessions') && url.includes('view=week')),'the live inventory reads again');
  assert.ok(f.live.slice(liveAt).some(url=>url.startsWith('/api/opencode/programs')));
  assert.deepEqual([...f.api.state.inventory.map(r=>r.sessionRef)],['live-row'],'no archived row stays in the live list');
  assert.equal(f.api.state.glances.size,0,'History glances do not follow into the live list');
  for(const file of ['sessions.html','m-programs.html'])
    assert.match(fs.readFileSync(path.join(__dirname,'../static',file),'utf8'),/value="all">Everything live<\/option><option value="history">📦 History<\/option><\/select>/,file+': the fourth entry');
});
