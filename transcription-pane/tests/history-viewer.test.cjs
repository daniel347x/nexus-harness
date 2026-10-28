// 📦 History's archived conversation (MasterSpec [ap:UHKPV1] §3.8, §7 U2.3): the desktop viewer reads an archived row
// through /api/history/events only (newest page, older by fromSeq, newer by cursor + fileEpoch, a 409 stale-epoch
// resyncs by position), draws its tools as previews with no Inspect and opens a childKey's archived child; the phone
// admits it read-only through READERS.history. Real static/sessions.js and m.js in a Node VM, a fake /api/history/*.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const source=fs.readFileSync(process.env.HISTORY_TEST_SOURCE || path.join(__dirname,'../static/sessions.js'),'utf8');
const core=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-preview-core.js'),'utf8');
const shared=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-record-text.js'),'utf8');
const reading=fs.readFileSync(path.join(__dirname,'../static/tool-reading.js'),'utf8');
const U=n=>String(n).padStart(8,'0')+'-0000-4000-8000-000000000000';
const b64u=text=>Buffer.from(text).toString('base64url');
const clone=v=>JSON.parse(JSON.stringify(v));
// Every conversation route the archived viewer must never reach (the live list's own reads are counted apart).
const LIVE=/^\/api\/(claude\/(events|subagents|tool-record|inspect|turns|glance|state|send|control|reconnect)|opencode\/(events|manager|inspect|tool-record|turns|glance|focus))/;
const CAPS={history:true,live:false,context:false,inspect:false,nativeInput:false,controls:false,subagents:false,reconnect:false};
const PREVIEW={schemaVersion:1,status:'available',value:{kind:'object',entries:[['description',{kind:'string',characters:9,jsonCharacters:11,head:'scan code',tail:'scan code'}]]}};
const refsOf=key=>({sessionRef:'h_'+b64u(key),scopeRef:'h_'+b64u(key+':main')});
// One archived sidebar row as /api/history/* answers it (§3.8's fixed values).
const archivedRow=(key,over)=>({storeRef:'history',sessionRef:refsOf(key).sessionRef,targetRef:null,fileEpoch:'h_e1',scopes:[{label:'main',scopeRef:refsOf(key).scopeRef}],
  lifecycle:'ended',liveState:null,lastContext:null,successor:null,capabilities:CAPS,archived:{key,archivedAt:1792001000,prunedAt:null},telemetry:null,...over});
const KEY='claude:cc-'+U(2),CHILD='opencode:ses_child00000001',HELPER='opencode:ses_helper0000001',GONE='opencode:ses_notarchived001';
const seat=archivedRow(KEY,{source:'host',uuid:U(2),executionId:'cc-'+U(2),generation:1,role:'manager',displayRole:'manager',state:'exited',status:'exited',
  title:'Manager-Session-225',sessionLabel:'Manager-Session-225',nameSource:'chosen',cwd:'/r/manager-sessions',createdAt:1792000002});
const ocRow=(key,over)=>archivedRow(key,{id:key.slice(9),busy:false,kind:'helper',title:'helper',createdAt:1792000100,updatedAt:1792000200,...over});
// One archived turn and call exactly as history_archive._event/_call shape them.
const turn=(id,seq,over={})=>({recordRef:'h_'+id+'_'+seq,uuid:'h_'+id+'_'+seq,seq,kind:seq%2?'assistant':'user',scope:'main',status:'stored',
  text:(seq%2?'answer ':'prompt ')+seq,textBytes:10,partial:false,time:1792000000+seq,thinking:[],calls:[],orphanResults:[],toolResults:0,isMeta:false,
  originKind:seq%2?'assistant':'user',model:seq%2?'claude-opus-5-5':null,effort:null,agent:null,receipt:null,...over});
const call=(id,seq,i,over={})=>({callRef:'h_'+id+'_'+seq+'_'+i,block:i,id:'h_'+id+'_'+seq+'_'+i,name:'Task',status:'stored',previewData:PREVIEW,
  inputBytes:null,outputBytes:null,time:null,resultTime:null,messageId:null,partIndex:null,childSessionId:null,childKey:null,...over});
function conversation(id,n,{epoch='h_e1',info=null,calls={}}={}) {
  return {epoch,info,turns:Array.from({length:n},(_,seq)=>turn(id,seq,calls[seq]?{kind:'assistant',calls:calls[seq](seq)}:{}))};
}
// /api/history/events and /api/history/sessions?keys=, offline: no position = the newest ≤100 rows (hasMore false), fromSeq or a
// cursor read forward, a cursor requires fileEpoch and excludes fromSeq, another fileEpoch is 409 stale-epoch.
function histServer(convs,rows) {
  const srv={convs,rows,reads:[],keyReads:[]};
  const reply=(code,status,error)=>new Response(JSON.stringify({schemaVersion:1,backend:'history',status,error}),{status:code});
  srv.handle=url=>{
    const u=new URL(url,'http://pane'),q=Object.fromEntries(u.searchParams);
    if(u.pathname==='/api/history/sessions'){
      srv.keyReads.push(q);const keys=q.keys.split(','),found=keys.map(k=>rows[k]).filter(Boolean);
      return {schemaVersion:1,backend:'history',status:'complete',claude:clone(found.filter(r=>!r.id)),opencode:clone(found.filter(r=>r.id)),missing:keys.filter(k=>!rows[k])};
    }
    if(u.pathname!=='/api/history/events')return reply(404,'missing','unknown History route');
    srv.reads.push(q);
    const c=convs[q.key];if(!c)return reply(404,'missing','no archived session has this key');
    if(q.cursor!==undefined && (!q.fileEpoch || q.fromSeq!==undefined))return reply(400,'bad-request','cursor requires fileEpoch and excludes fromSeq');
    if(q.fileEpoch && q.fileEpoch!==c.epoch)return reply(409,'stale-epoch','this conversation was archived again; resync it');
    const n=c.turns.length,positioned=q.cursor!==undefined || q.fromSeq!==undefined;
    const start=q.cursor!==undefined?Number(q.cursor.slice(2)):q.fromSeq!==undefined?Number(q.fromSeq):Math.max(0,n-100);
    const events=c.turns.slice(start,start+100),end=start+events.length,more=positioned && end<n;
    return {schemaVersion:1,backend:'history',source:'history',...refsOf(q.key),fileEpoch:c.epoch,events:clone(events),hasMore:more,nextCursor:more?'c_'+end:null,
      deltaCursor:null,rowsAhead:0,leafRef:null,targetRef:null,indexLimit:null,branches:[],traces:[],summaryUpdates:[],subagentActivity:[],diagnostics:[],eventUpdates:[],
      status:'complete',change:'snapshot',busy:false,runningCalls:[],observedAt:null,sessionInfo:c.info};
  };
  return srv;
}
function fixture(hist) {
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
  const parent={postMessage:m=>messages.push(m)},origin='http://127.0.0.1:9911';
  const doc={hidden:false,getElementById:el,createElement:t=>new Node(t),createDocumentFragment:()=>new Node('fragment'),
    defaultView:{frameElement:{id:'claude-sessions-frame'},parent,location:{origin},addEventListener(){}}};
  const ctx={TextEncoder,TextDecoder,AbortController,URLSearchParams,URL,Response,Date:class extends Date{static now(){return time;}},
    setTimeout:(f,ms)=>{if(ms===0){queueMicrotask(f);return 0;}timers.set(++timerId,f);return timerId;},clearTimeout:i=>timers.delete(i),setInterval(){},
    BroadcastChannel:class{postMessage(m){messages.push(m);}},fetch:async(url,options)=>{
      requests.push(url);
      const route=url.split('?')[0];let data;
      if(route.startsWith('/api/history/'))data=hist.handle(url);
      else if(route==='/api/pins')data={schemaVersion:1,pins:{}};
      else if(route==='/api/tool-selection')data={schemaVersion:1,backend:'claude',epoch:'mail',revision:1,selection:null};
      else if(route==='/api/claude/sessions')data={schemaVersion:1,backend:'claude',sessions:[],hasMore:false,status:'complete'};
      else if(route==='/api/opencode/programs')data={schemaVersion:1,backend:'opencode',status:'complete',sessions:[]};
      else if(route==='/api/opencode/activity')data={schemaVersion:1,backend:'opencode',status:'complete',observedAt:time/1000,active:[],sessions:[]};
      else return new Response(JSON.stringify({schemaVersion:1,backend:'claude',status:'unavailable',error:'offline fixture: '+route}),{status:503});
      return data instanceof Response?data:new Response(JSON.stringify(data));}};
  vm.runInNewContext(shared+'\n'+core+'\n'+reading+'\n'+source.replace("if(typeof document!=='undefined'", "if(false && typeof document!=='undefined'"),ctx);
  const api=ctx.opcClaudeHistory.create(doc);
  const flush=async(n=3)=>{for(let i=0;i<n;i++)await new Promise(r=>setImmediate(r));};
  return {api,el,requests,messages,flush,advance:(ms=3001)=>time+=ms,live:(from=0)=>requests.slice(from).filter(u=>LIVE.test(u))};
}
const find=(node,cls)=>{const out=[];const walk=n=>{if(n.classList?.contains(cls))out.push(n);for(const c of n.children||[])walk(c);};walk(node);return out;};
const resident=f=>Array.from(f.api.state.order,k=>f.api.state.rows.get(k));
const seqs=f=>resident(f).map(r=>r.seq);
const world=(epoch='h_e1')=>{
  const id='aaaaaaaaaaaa',convs={[KEY]:conversation(id,250,{epoch,calls:{247:s=>[call(id,s,0,{name:'Bash'})],249:s=>[call(id,s,0,{childKey:CHILD}),call(id,s,1,{childKey:GONE})]}}),
    [CHILD]:conversation('bbbbbbbbbbbb',6,{info:{id:'ses_child00000001',title:'a subagent',agent:'explore',model:'deepseek-v4.1-flash',directory:'/r',parentId:'ses_helper0000001'}}),
    [HELPER]:conversation('cccccccccccc',4,{info:{id:'ses_helper0000001',title:'helper',agent:'quick-helper',model:'deepseek-v4.1-flash',directory:'/r/quick-worker',parentId:null}})};
  const rows={[KEY]:seat,[CHILD]:ocRow(CHILD,{kind:'subagent',title:'a subagent',parentId:'ses_helper0000001'}),[HELPER]:ocRow(HELPER)};
  return histServer(convs,rows);
};

test('📦 an archived Claude row opens in the same viewer through /api/history/events only: its newest page, read-only, no target or context',async()=>{
  const hist=world(),f=fixture(hist);
  await f.api.browse(clone(seat));await f.flush();
  const s=f.api.state.session;
  assert.equal(s.backend,'history');assert.equal(s.key,KEY);assert.equal(s.archivedBackend,'claude');assert.equal(f.api.identityOf(s),'history:'+KEY);
  assert.deepEqual(hist.reads,[{key:KEY}],'one read: the newest page, no position, no epoch yet');
  assert.deepEqual(f.requests.filter(u=>!u.startsWith('/api/history/') && !u.startsWith('/api/pins') && !u.startsWith('/api/tool-selection')),[],'nothing live is read to open it');
  assert.deepEqual(seqs(f),Array.from({length:100},(_,i)=>150+i),'the newest 100 turns, dense');
  assert.equal(f.api.address(null),'/api/history/events?key='+encodeURIComponent(KEY)+'&fileEpoch=h_e1','the page URL is the History one, by key');
  assert.match(f.el('history-title').textContent,/^📦 Manager-Session-225/);
  assert.equal(f.el('history-activity').textContent,'📦 Archived conversation · read-only');
  assert.match(f.el('context-gauge').title,/^Context: none/);
  assert.equal(f.api.state.target,null);assert.equal(f.el('history-target').disabled,true,'never a Send/Refine target');
  assert.equal(f.api.state.childParent,null,'no native subagents are watched for it');
  assert.equal(f.messages.filter(m=>m?.type==='use-target' || m?.type==='target').length,0,'no target is offered to the editor');
  const before=f.requests.length;
  for(let i=0;i<4;i++){f.advance();await f.api.poll();await f.flush();}
  assert.deepEqual(f.requests.slice(before).filter(u=>u.startsWith('/api/history/events')),[],'an archived conversation is never re-polled (no delta, no next)');
  assert.deepEqual(f.live(),[],'no live conversation route, ever');
});

test('📦 its tool bars are previews with no Inspect; a childKey opens the archived child read-only; a key the archive lacks says so',async()=>{
  const hist=world(),f=fixture(hist);
  await f.api.browse(clone(seat));await f.flush();
  const last=resident(f).at(-1),[first,second]=last.calls;
  assert.equal(first.node.onclick,null,'no Inspect');assert.equal(first.node.getAttribute('aria-disabled'),'true');
  assert.ok(first.node.classList.contains('archived-call'));assert.match(first.node.getAttribute('aria-label'),/archived preview \(no Inspect\)/);
  assert.ok(first.node.children[2].children[1].hidden && first.node.children[2].children[2].hidden,'no In/Out stored sizes: the archive keeps a preview');
  assert.match(first.node.children[1].textContent,/scan code/,'the one-line preview is drawn');
  assert.match(first.child.textContent,/↳ Open child conversation · ses_child00000001/);
  assert.equal(resident(f).find(r=>r.seq===247).calls[0].child,undefined,'a call without childKey has no child link');
  const at=f.requests.length;
  first.child.onclick({stopPropagation(){}});await f.flush(6);
  assert.deepEqual(hist.keyReads,[{keys:CHILD}],'one /api/history/sessions?keys= read for the child');
  assert.equal(f.api.state.session.key,CHILD);assert.equal(f.api.state.session.archivedBackend,'opencode');assert.equal(f.api.state.session.id,'ses_child00000001');
  assert.deepEqual(hist.reads.at(-1),{key:CHILD});
  assert.match(f.el('history-title').textContent,/^📦 a subagent · ses_child00000001/);
  assert.match(f.el('history-source').textContent,/📦 History · OpenCode/);
  assert.deepEqual(f.requests.slice(at).filter(u=>u.startsWith('/api/opencode/')),[],'an archived OpenCode child never reaches /api/opencode/*');
  await f.api.browse(clone(seat));await f.flush();
  resident(f).at(-1).calls[1].child.onclick({stopPropagation(){}});await f.flush(6);
  assert.equal(f.api.state.session.key,KEY,'a missing child leaves the conversation as it is');
  assert.match(f.el('sessions-notice').textContent,/not in 📦 History \(ses_notarchived001\)/);
  assert.deepEqual(f.live(),[]);
});

test('📦 paging is the viewer’s own: older by fromSeq, newer by cursor + fileEpoch; a 409 stale-epoch resyncs in place by position with no list read',async()=>{
  const hist=world(),f=fixture(hist);
  await f.api.browse(clone(seat));await f.flush();
  await f.api.loadOlder();await f.flush();
  assert.deepEqual(hist.reads.at(-1),{key:KEY,fileEpoch:'h_e1',fromSeq:'50'},'older: the rows just above, by position, with the epoch');
  assert.equal(seqs(f)[0],50);
  await f.api.fromStart(0);await f.flush();
  assert.deepEqual(hist.reads.at(-1),{key:KEY,fromSeq:'0'},'⤒ First reads position 0 explicitly (a null cursor is the newest page here)');
  assert.equal(f.api.state.next,'c_100');
  await f.api.loadNewer();await f.flush();
  assert.deepEqual(hist.reads.at(-1),{key:KEY,fileEpoch:'h_e1',cursor:'c_100'},'newer: the server cursor with its fileEpoch, never with fromSeq');
  assert.ok(seqs(f).includes(199));
  // The conversation is archived again (a new body): the old epoch answers 409 and the viewer resyncs the same key by position.
  const atNow=f.requests.length,onScreen=seqs(f);hist.convs[KEY].epoch='h_e2';
  await f.api.loadNewer();await f.flush();
  assert.ok(f.api.state.resync,'409 stale-epoch starts the existing resync');
  const from=hist.reads.length;
  await f.api.resync();await f.flush();
  assert.equal(f.api.state.resync,null,'resynced');assert.equal(f.api.state.epoch,'h_e2');
  assert.deepEqual(seqs(f).slice(0,onScreen.length),onScreen,'the rows on screen stay in place');
  assert.deepEqual(hist.reads[from],{key:KEY,fromSeq:String(onScreen[0])},'re-read by position from the oldest row on screen, the old epoch dropped');
  assert.ok(hist.reads.slice(from+1).every(q=>q.fileEpoch==='h_e2'),'then onward with the new epoch');
  assert.deepEqual(f.requests.slice(atNow).filter(u=>!u.startsWith('/api/history/events')),[],'no session list, Manager or subagent read during the resync');
  assert.deepEqual(f.live(),[]);
});

test('📦 an archived OpenCode row opens by its key, never through /api/opencode/manager or events; it reads as OpenCode',async()=>{
  const hist=world(),f=fixture(hist);
  await f.api.openOpenCode(clone(hist.rows[HELPER]));await f.flush();
  assert.equal(f.api.state.session.backend,'history');assert.equal(f.api.state.session.id,'ses_helper0000001');
  assert.deepEqual(hist.reads,[{key:HELPER}]);
  assert.deepEqual(f.requests.filter(u=>u.startsWith('/api/opencode/')),[],'no OpenCode Manager validation, events or focus read');
  assert.equal(f.api.state.session.manager,null);assert.equal(f.api.state.target,null);
  const answer=resident(f).at(-1);
  assert.match(answer.node.children[0].children[0].textContent,/^OpenCode/,'its turns read as OpenCode');
  assert.equal(f.el('oc-header').hidden,true,'no live OpenCode header (CodeNomad focus) for an archived conversation');
  await f.api.openOpenCode('ses_helper0000001').catch(()=>{});await f.flush();
  assert.ok(f.requests.some(u=>u.startsWith('/api/opencode/events?id=ses_helper0000001')),'a bare ses_ id (the editor’s browse-opencode) still opens the live conversation');
});

// ---- the phone: m.js's own rules, loaded with a null DOM and its start() never run (phone-opencode-managers' pattern).
function phoneRules() {
  const SHARED=fs.readFileSync(path.join(__dirname,'../static/opencode-manager.js'),'utf8'),PHONE=fs.readFileSync(path.join(__dirname,'../static/m.js'),'utf8');
  const ORDER=fs.readFileSync(path.join(__dirname,'../static/manager-order.js'),'utf8');
  const storage={map:new Map(),get length(){return this.map.size;},key(i){return [...this.map.keys()][i] ?? null;},getItem(k){return this.map.get(k) ?? null;},setItem(k,v){this.map.set(k,String(v));},removeItem(k){this.map.delete(k);}};
  const ctx={console,URL,URLSearchParams,TextEncoder,TextDecoder,AbortController,DOMException,crypto:webcrypto,localStorage:storage,fetch:async()=>{throw new Error('offline');},
    setTimeout,clearTimeout,setInterval:()=>0,clearInterval(){}};
  ctx.document={getElementById:()=>null,addEventListener(){},createElement:()=>({style:{},dataset:{},classList:{toggle(){},add(){},remove(){}},append(){},replaceChildren(){},setAttribute(){},addEventListener(){}})};
  ctx.navigator={clipboard:{writeText:async()=>{}}};ctx.location={href:'http://pane.test/m',search:'',origin:'http://pane.test'};ctx.performance={now:()=>Date.now()};
  vm.createContext(ctx);
  vm.runInContext('globalThis.window=globalThis; window.matchMedia=()=>({matches:false,addEventListener(){},addListener(){}});',ctx);
  vm.runInContext(SHARED,ctx);vm.runInContext(ORDER,ctx);
  vm.runInContext(PHONE.replace(/\nphoneManager\.start\(\);\s*$/,'\n')+'\nglobalThis.__phone=phoneManager;',ctx);
  return ctx.__phone.rules;
}

test('📦 phone: Read › of an archived row is admitted as backend history by its key and read through READERS.history, read-only',()=>{
  const rules=phoneRules(),refs=refsOf(KEY),open=session=>({type:'open-session',schemaVersion:1,session});
  const v=rules.viewerSession(open({backend:'history',key:KEY,...refs,title:'Manager-Session-225',label:'Manager'}));
  assert.deepEqual({...v},{backend:'history',key:KEY,...refs,executionId:null,title:'Manager-Session-225',label:'Manager'},'never an execution: never matched to a Manager chip or promoted');
  assert.equal(rules.viewerSession(open({backend:'history',key:'claude:not-a-key',...refs})),null,'a malformed key is refused');
  assert.equal(rules.viewerSession(open({backend:'history',key:KEY,sessionRef:refs.sessionRef})),null,'both handles are required');
  const reader=rules.READERS.history,chat={key:KEY,sessionRef:refs.sessionRef};
  assert.equal(reader.backend,'history');assert.equal(reader.tool,null,'an archived call has no body to read');
  assert.equal(reader.events(chat,{}),'/api/history/events?key='+encodeURIComponent(KEY));
  assert.equal(reader.events(chat,{fromSeq:50,fileEpoch:'h_e1'}),'/api/history/events?key='+encodeURIComponent(KEY)+'&fromSeq=50&fileEpoch=h_e1');
  assert.equal(reader.same(chat,{source:'history',sessionRef:refs.sessionRef}),true);
  assert.equal(reader.same(chat,{source:'opencode',sessionRef:refs.sessionRef}),false,'a page from another backend is refused');
  assert.equal(reader.same(chat,{source:'history',sessionRef:'h_other'}),false,'a page of another conversation is refused');
});
