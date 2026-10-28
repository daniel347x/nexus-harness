const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(process.env.HISTORY_TEST_SOURCE || path.join(__dirname,'../static/sessions.js'),'utf8');
const core=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-preview-core.js'),'utf8');
const shared=fs.readFileSync(path.join(__dirname,'../../chrome-extension/codenomad-session-watcher/tool-record-text.js'),'utf8');
const env=(fields={})=>({schemaVersion:1,backend:'claude',...fields});
const session={sessionRef:'session',uuid:'native-uuid',title:'Test',cwd:'unknown',source:'windows-history',scopes:[{scopeRef:'scope',label:'main'},{scopeRef:'trace',label:'agent:a'}]};
const event=(n,text='prose '+n)=>({recordRef:'r'+n,uuid:'uuid-'+n,kind:'assistant',status:'stored',text,calls:[]});
const callEvent=n=>({...event(n),calls:[{callRef:'call'+n,block:0,id:'id'+n,name:'mcp__workflowy__system',status:'pending-or-missing',inputBytes:0,outputBytes:null,previewData:env({status:'unavailable',reason:'test'})}]});
const page=(events,extra={})=>env({sessionRef:'session',scopeRef:'scope',fileEpoch:'epoch',leafRef:'leaf',targetRef:'target',events,branches:[{leafRef:'leaf',uuid:'leaf-uuid'}],traces:['agent:a'],diagnostics:[],status:'complete',change:'snapshot',hasMore:false,deltaCursor:'delta',...extra});
function fixture(transport,{embedded=true,opencode,family}={}) {
  const elements=new Map(),requests=[],messages=[],timers=new Map(),ocRequests=[],familyRequests=[];let time=100000,timerId=0,familyPending=false;
  // loadManagerFamily's unwindowed Manager summary read (role=Manager) is its own lane, never counted as a
  // conversation page or a windowed inventory read. Its paired OpenCode programs read keeps the test's rows.
  const familyClaude=url=>url.startsWith('/api/claude/sessions') && /[?&]role=Manager(?:&|$)/.test(url);
  const familyAnswer=async(url,options)=>{familyRequests.push([url,options]);const data=await family?.(url,options);return new Response(JSON.stringify(data ?? env({sessions:[],status:'complete',hasMore:false})));};
  class Node {
    constructor(tag='div'){this.tag=tag;this.children=[];this.parentNode=null;this.dataset={};this.attributes={};this.scrollTop=0;this.clientHeight=100;this.hidden=false;this.value='';this.content='';this.style={setProperty:(k,v)=>this.attributes[k]=v};
      const classes=new Set();this.classList={add:k=>classes.add(k),contains:k=>classes.has(k),toggle:(k,v)=>{if(v===undefined)v=!classes.has(k);v?classes.add(k):classes.delete(k);}};}
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
  let box=env({epoch:'mail',revision:1,selection:null});
  const parent={postMessage:m=>messages.push(m)},origin='http://127.0.0.1:9911';
  const doc={hidden:false,getElementById:el,createElement:t=>new Node(t),createDocumentFragment:()=>new Node('fragment'),
    defaultView:embedded?{frameElement:{id:'claude-sessions-frame'},parent,location:{origin},addEventListener(){}}:null};
  const ctx={TextEncoder,TextDecoder,AbortController,URLSearchParams,Response,Date:class extends Date{static now(){return time;}},
    setTimeout:(f,ms)=>{if(ms===0){queueMicrotask(f);return 0;}timers.set(++timerId,f);return timerId;},clearTimeout:i=>timers.delete(i),setInterval(){},
    BroadcastChannel:class{postMessage(m){messages.push(m);}},fetch:async(url,options)=>{
      // The list cadence's unwindowed Manager summary read is its own lane, never a conversation page nor a
      // windowed inventory read; its paired OpenCode programs read keeps the test's own rows.
      if(familyClaude(url)){familyPending=true;return familyAnswer(url,options);}
      if(familyPending && url==='/api/opencode/programs'){familyPending=false;familyRequests.push([url,options]);const d=await opencode?.(url);return new Response(JSON.stringify(d ?? {schemaVersion:1,backend:'opencode',status:'complete',sessions:[]}));}
      // OpenCode's program rows are their own channel (never counted with the Claude reads): offline unless a test answers.
      if(url.startsWith('/api/opencode/')){ocRequests.push(url);const d=await opencode?.(url);return new Response(JSON.stringify(d ?? {schemaVersion:1,backend:'opencode',status:'unavailable',error:'offline fixture',sessions:[]}));}
      requests.push([url,options]);
      let data=await transport?.(url,options);if(data===undefined){if(url==='/api/tool-selection')data=box;else if(url.startsWith('/api/claude/sessions'))data=env({sessions:[session],hasMore:false,status:'complete'});else data=page([event(1)]);}
      return data instanceof Response?data:new Response(JSON.stringify(data));}};
  vm.runInNewContext(shared+'\n'+core+'\n'+source.replace("if(typeof document!=='undefined'", "if(false && typeof document!=='undefined'"),ctx);
  const api=ctx.opcClaudeHistory.create(doc);return {api,ctx,el,requests,ocRequests,familyRequests,messages,timers,parent,origin,direct:data=>({data,source:parent,origin}),box:value=>box=value,advance:()=>time+=3001,flush:()=>new Promise(r=>setImmediate(r))};
}
test('real page loads only shared pure helpers and one history owner, no scanner/editor/second inspector',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/sessions.html'),'utf8');
  assert.match(html,/tool-record-text.js.*tool-preview-core.js.*sessions.js/s);assert.doesNotMatch(html,/tool-previews.js|app.js|tool-inspector.js/);
  // The only text boxes are New Manager's own launch fields (↳ Sub-session's first message, the default first
  // messages), hidden until opened: never a second dictation editor.
  assert.doesNotMatch(html.replace(/<section id="claude-new-manager-row"[\s\S]*?<\/section>/,''),/textarea/);
  assert.deepEqual([...html.matchAll(/<textarea id="([^"]+)"/g)].map(m=>m[1]),['claude-first-manager','claude-first-sub','claude-new-sub-text']);
  assert.doesNotMatch(source,/function (preview|cropString)\(/);assert.match(source,/opcToolPreview.preview\(/);
});
test('T5.7: the one Reload chip is on the ribbon directly after Subagents, not inside more info',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/sessions.html'),'utf8');
  const ribbon=html.match(/<div id="history-ribbon"[^>]*>([\s\S]*?)<\/div>/)?.[1];assert.ok(ribbon);
  assert.equal((html.match(/id="history-reload"/g)||[]).length,1);
  assert.match(ribbon,/<button id="ribbon-subagents"[^>]*>Subagents<\/button>\s*<button id="history-reload" class="chip" disabled title="Read this conversation again from the server and land on its newest turn, Following">Reload<\/button>/);
  const more=html.slice(html.indexOf('<details id="history-more-info"'),html.indexOf('<div id="history-ribbon"'));
  assert.doesNotMatch(more,/id="history-reload"/);
});
test('T5.8: the desktop ribbon names the viewed conversation in its own element beside the context pie, apart from the activity line',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/sessions.html'),'utf8');
  assert.match(html,/<div id="history-topbar"><span id="context-gauge"[^>]*><\/span>\s*<span id="history-session-name" hidden><\/span>\s*<div id="history-activity" role="status" aria-live="off">Choose a session<\/div>/);
  assert.equal((html.match(/id="history-session-name"/g)||[]).length,1,'one name element, never a second readback');
  assert.match(source,/\$\('history-session-name'\)/,'activity() owns the name text/title, not the sidebar');
});
test('T2.7: sidebar markup pins above its own scroll container and program heads stick without changing hover',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/sessions.html'),'utf8'),css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  assert.match(html,/<div class="side-top">[\s\S]*<div id="sessions-pinned" hidden><\/div>\s*<\/div>\s*<div id="sessions-scroll">/);
  assert.match(html,/<div id="sessions-scroll">\s*<details class="mini-more" id="sessions-info">[^]*?<\/details>\s*<div id="sessions-list"><\/div>\s*<\/div>/);
  assert.match(css,/aside \{ display:flex; flex-direction:column; overflow:hidden; font-size:var\(--side-fs\); padding-right:2px; \}/);
  assert.match(css,/#sessions-scroll \{ flex:1 1 auto; min-height:0; overflow:auto; overflow-anchor:none; \}/);
  assert.match(css,/\.program > \.program-head \{ position:sticky; top:0; z-index:1; \}/);
  assert.match(css,/\.program > \.program-head:not\(:hover\) \{ background:var\(--bg\); \}/);
  assert.match(css,/\.program-head:hover \{ background:var\(--side-item-hover\); \}/);
  // The desktop holds placements for every pointer; the phone (sidebar-only) for a mouse only, since
  // a touch lift is a pointerleave whose rebuild swallowed iOS taps.
  assert.match(source,/const hover=on=>event=>\{if\(!sidebarOnly \|\| event\?\.pointerType==='mouse'\)setSidebarHover\(on\);\};/);
  assert.match(source,/\$\('sessions-scroll'\)\?\.addEventListener\?\.\('pointerenter',hover\(true\)\)/);
  assert.match(source,/\$\('sessions-scroll'\)\?\.addEventListener\?\.\('pointerleave',hover\(false\)\)/);
});
test('U7.1 CSS/markup: the sidebar alone wheel-scrolls; program boxes, ribbons and descendants have no inner scroll region',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/sessions.html'),'utf8'),css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  const sidebar=css.slice(css.indexOf('/* ------------------------------------------------------------------ sidebar */'),css.indexOf('/* ------------------------------------------------------------------ conversation column */')).replace(/\/\*[^]*?\*\//g,'');
  const scrollers=[...sidebar.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>/\boverflow(?:-[xy])?\s*:\s*(?:auto|scroll)\b/.test(m[2]));
  assert.deepEqual(scrollers.map(m=>m[1].trim()),['#sessions-scroll'],'no program/child/ribbon style creates a wheel-scrollable box');
  const allScrollers=[...css.replace(/\/\*[^]*?\*\//g,'').matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>/\boverflow(?:-[xy])?\s*:\s*(?:auto|scroll)\b/.test(m[2]));
  assert.deepEqual(allScrollers.map(m=>m[1].trim()),['#sessions-scroll','#history-more-info[open]','#history-live','#history-viewport','#running-tools','#running-tools .running-tool-preview','.event-prose pre','.event-prose table'],'no broad rule elsewhere makes program descendants scrollable; current-tool strip is scoped outside the sidebar');
  for(const m of sidebar.matchAll(/([^{}]+)\{([^{}]*)\}/g))if(/^(?:\.program|\.tree-children|\.tree-node|\.conductor-fold)$/.test(m[1].trim()))assert.doesNotMatch(m[2],/(?:^|;)\s*(?:max-)?height\s*:/,'program content containers grow with their contents, not an inner viewport');
  assert.match(css,/\.program > \.program-head \{ position:sticky; top:0; z-index:1; \}/,'the requested sticky header is not sacrificed');
  // The one frame is the conversation column's View pane (its document scrolls inside itself), never in the sidebar.
  const viewFrame=/<div id="desktop-view"[^>]*><iframe id="desktop-view-frame"[^>]*><\/iframe><\/div>/;
  assert.ok(html.indexOf('<section id="history"')>=0 && html.search(viewFrame)>html.indexOf('<section id="history"'),'the View frame lives in the conversation column');
  // New Manager's launch fields (above the list, hidden until opened) are the one exception for text boxes.
  assert.doesNotMatch(html.replace(viewFrame,'').replace(/<section id="claude-new-manager-row"[\s\S]*?<\/section>/,''),/<(?:iframe|textarea)|style=["'][^"']*overflow/);
  assert.doesNotMatch(source.slice(source.indexOf('function treeNode('),source.indexOf('const selectedIn=')),/\.style|scrollTop|addEventListener[^\n]*wheel/,'program rendering creates no inline scroller or wheel handler');
});
test('U7.3 markup: collapse-all describes home sections and the Running now exception without changing either glyph',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/sessions.html'),'utf8');
  assert.match(html,/<button id="sessions-collapse" class="icon" data-next="collapse" title="Close every program and freestanding tree \(what is inside them stays as it was\) and fold the home sections; Running now stays open\. Click again for only what is running" aria-label="Collapse programs and home sections">⊟<\/button>/);
  assert.match(html,/<button id="sessions-expand" class="icon" title="Expand every program and the sessions under it" aria-label="Expand all programs">⊞<\/button>/);
});
test('U9.CSS: root meters keep fixed right-aligned cells, continuous fill and age colours; the original program styles remain exact',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  assert.match(css,/\.program-meters \{ flex:none; display:inline-grid; grid-template-columns:var\(--program-meter-count-width\) var\(--program-meter-duration-width\) var\(--program-meter-age-width\);[^}]*font:400 var\(--program-meter-fs\)\/1\.45 var\(--mono\);[^}]*text-align:right;/);
  assert.match(css,/--program-meter-count-width:7ch; --program-meter-duration-width:9ch; --program-meter-age-width:5ch/);
  assert.match(css,/\.program-meter-count,\.program-meter-duration \{ display:flex; justify-content:flex-end;/);
  assert.match(css,/\.program-count-block \{[^}]*background:var\(--program-meter-block\)/);
  assert.match(css,/\.program-duration-bar \{[^}]*width:calc\(100% \* var\(--program-duration-fraction,0\)\); background:var\(--program-meter-yellow\)/);
  assert.doesNotMatch(css,/--program-duration-step|\.program-duration-bar\[data-bucket=/);
  for(const age of ['today','yesterday','recent','old'])assert.ok(css.includes('.program-meter-age[data-age='+age+'] { color:var(--program-age-'+age+'); }'));
  assert.match(css,/--program-meter-live-space:13px/);assert.match(css,/\.program-meter-tail \{ flex:none; width:var\(--program-meter-live-space\); \}/);
  for(const rule of [
    '.program > .program-head { position:sticky; top:0; z-index:1; }',
    '.program-head { position:relative; display:block; cursor:pointer; border-radius:4px; padding:3px 6px 3px var(--tree-gutter); white-space:nowrap; overflow:hidden; }',
    '.program-title { display:flex; min-width:0; color:var(--program-title); font-weight:650; }',
    '.program-title > .name-text { min-width:0; overflow:hidden; text-overflow:ellipsis; }',
    '.program-meta { display:block; color:var(--program-meta); font-size:var(--fs-micro); overflow:hidden; text-overflow:ellipsis; }',
    '.program.live > .program-head .program-title::after { content:""; flex:none; align-self:center; width:7px; height:7px; margin-left:6px; border-radius:50%; background:var(--side-live); }',
    '.program-fold { flex:none; align-self:center; margin-left:auto; padding-left:6px; font-size:11px; font-weight:400; color:var(--side-group); opacity:.7; cursor:pointer; user-select:none; }'
  ])assert.ok(css.includes(rule),'existing rule unchanged: '+rule);
  const newRules=[...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>/program-meter|program-count-block|program-duration/.test(m[1]));
  assert.ok(newRules.every(m=>!/(?:^|,)\s*\.program(?:-head|-title|-meta|-fold)?(?:\s|$|[:>])/.test(m[1].trim())),'no new rule restyles an old element');
});
test('U10.CSS fade: the new root-name rule changes colour only, mixing the unchanged title and faded endpoints',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  assert.match(css,/\.program-name-age \{ color:color-mix\(in srgb, var\(--program-title\), var\(--program-name-faded\) var\(--program-name-fade\)\); \}/);
  assert.equal((css.match(/\.program-name-age\s*\{/g)||[]).length,1);
  const users=[...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>/--program-name-fade(?![\w-])/.test(m[2]));
  assert.equal(users.length,1);assert.equal(users[0][1].trim().split('\n').at(-1),'.program-name-age');
  // Dan (2026-10-20): the meta lines' gray is a little brighter; the name fade keeps its original endpoints exactly.
  assert.match(css,/--program-title:#e6eaf0; --program-meta:#9ba3b1; --program-name-faded:#7d8594;/,'original fade endpoints remain exact');
  assert.doesNotMatch(users[0][2],/opacity|filter|font|padding|margin|transition|animation/);
});
test('U10.CSS bar: only the duration fill changes; its 18px track, height and yellow remain exact',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  assert.ok(css.includes('.program-duration-bar { display:block; height:3px; width:calc(100% * var(--program-duration-fraction,0)); background:var(--program-meter-yellow); }'));
  assert.ok(css.includes('.program-duration-track { display:flex; justify-content:flex-end; align-items:center; width:18px; flex:none; }'));
  assert.doesNotMatch(css,/--program-duration-step|\.program-duration-bar\[data-bucket=/);
});
test('U12.10 CSS: one non-interactive rectangle paints above the opaque sticky head, not behind it',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  assert.match(css,/\.program\.active-program \{ position:relative; border-radius:5px; \}/);
  assert.match(css,/\.program\.active-program::after \{ content:""; position:absolute; inset:0; z-index:2; border:1\.5px solid var\(--active-outline\); border-radius:inherit; pointer-events:none; \}/);
  assert.doesNotMatch(css,/\.program\.active-program \{[^}]*outline:/,'replace the covered outline rather than doubling it');
  assert.ok(css.includes('.program > .program-head { position:sticky; top:0; z-index:1; }'));
  assert.ok(css.includes('.session-item.in-active-program { box-shadow:inset 0 0 0 1px var(--active-outline); }'));
  const painters=[...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>m[2].includes('1.5px solid var(--active-outline)'));
  assert.equal(painters.length,1);assert.equal(painters[0][1].trim().split('\n').at(-1),'.program.active-program::after','no group, open-state or scroll-position condition');
});
test('U12.13 CSS: larger blue-purple headers are scoped to top-level sections, never program or session rows',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../static/sessions.css'),'utf8');
  assert.match(css,/--section-heading-color:#a9b8ff; --section-heading-fs:15px; --section-heading-toggle-fs:12px;/);
  assert.ok(css.includes('#sessions-list > .session-group > h3 { font-size:var(--section-heading-fs); font-weight:650; color:var(--section-heading-color); }'));
  assert.ok(css.includes('#sessions-list > .session-group > h3 > .section-toggle { font-size:var(--section-heading-toggle-fs); color:var(--section-heading-color); opacity:1; }'));
  const users=[...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(m=>m[2].includes('var(--section-heading-'));
  assert.equal(users.length,2);assert.ok(users.every(m=>m[1].trim().split('\n').at(-1).startsWith('#sessions-list > .session-group > h3')));
  assert.ok(users.every(m=>!/(?:padding|margin|background|border|opacity|font-size):/.test(m[2].replace('opacity:1;','').replace(/font-size:var\(--section-heading-(?:toggle-)?fs\);/,''))),'only header type and colour, no section contents or spacing');
});
test('U12.12 gauge: a stale-generation resync keeps the same conversation last figure across fresh handles, with its unknown/as-of disclosure',async()=>{
  const old={...session,source:'host',role:'Manager',executionId:'cc-gauge-one',generation:1},fresh={...old,sessionRef:'fresh',generation:2},
    measured={state:'measured',occupancy_tokens:294246,window_tokens:1000000,measured_at:50},
    unknown={state:'unavailable',reason:'no completed model call yet in this generation'};
  const events=[{...event(1),seq:0},{...event(2),seq:1}];let stale=false;
  const f=fixture(url=>{
    if(url.startsWith('/api/claude/sessions'))return env({sessions:[fresh],hasMore:false,status:'complete'});
    const q=new URLSearchParams(url.split('?')[1]);
    if(stale && q.get('sessionRef')===old.sessionRef)return new Response(JSON.stringify({error:'host generation changed; reselect',status:'stale-generation'}),{status:409});
    return page(events,{sessionRef:q.get('sessionRef'),contextReadout:stale?unknown:measured});
  },{embedded:false});
  await f.api.browse(old);const node=f.api.state.rows.get('r1').node;
  assert.equal(f.el('context-gauge').dataset.known,'true');stale=true;
  await f.api.page('delta','delta');assert.ok(f.api.state.resync);await f.api.resync();
  assert.equal(f.api.state.resync,null);assert.equal(f.api.state.session.sessionRef,'fresh');assert.equal(f.api.state.rows.get('r1').node,node,'resync still checks and retains the resident rows');
  assert.equal(f.el('context-gauge').dataset.known,'true','a reissued handle must not orphan the same conversation figure');
  assert.match(f.el('context-gauge').title,/294,246 tokens.*29\.4%.*as of.*newest read unknown: no completed model call yet in this generation.*showing the last known figure/);
  assert.match(f.el('history-activity').textContent,/context unknown/,'the banner never claims a new-generation measurement');
  await f.api.browse({...fresh,sessionRef:'different',executionId:'cc-gauge-two'});
  assert.equal(f.el('context-gauge').dataset.known,'false','no figure leaks to a different execution');
});
test('U12.12 gauge identity: native handle reissues and child handle reissues retain only their own last figure',async()=>{
  const measured={state:'measured',occupancy_tokens:42000,window_tokens:100000,measured_at:50};let readout=measured;
  const f=fixture(url=>{const q=new URLSearchParams(url.split('?')[1]);return page([],{sessionRef:q.get('sessionRef'),contextReadout:readout});},{embedded:false});
  for(const original of [session,{...session,role:'subagent',parentUuid:'parent-one',agentId:'agent-one'}]) {
    readout=measured;await f.api.browse(original);readout={state:'unavailable',reason:'not measured yet'};
    await f.api.browse({...original,sessionRef:original.sessionRef+'-reissued'});
    assert.equal(f.el('context-gauge').dataset.known,'true','the existing stable identity, not the ephemeral ref, owns the figure');
    assert.match(f.el('context-gauge').title,/42,000 tokens.*showing the last known figure/);
    await f.api.browse({...original,sessionRef:'other-child',parentUuid:'parent-two',uuid:'other-native'});
    assert.equal(f.el('context-gauge').dataset.known,'false','neither another native session nor another parent can borrow it');
    if(original.role==='subagent') {
      await f.api.browse({...original,sessionRef:'other-agent',agentId:'agent-two'});
      assert.equal(f.el('context-gauge').dataset.known,'false','siblings under the same parent stay isolated too');
    }
  }
});
test('full >10k/25K/200K safe prose and shared native preview/counts; no selected-body GET on browse',async()=>{
  const f=fixture(()=>page([callEvent(0),event(1,'<img onerror=x>\n\t'+'🌈'.repeat(100001))]));await f.api.browse(session);
  assert.ok(f.el('history-events').textContent.endsWith('🌈'.repeat(100001)));assert.match(f.el('history-events').textContent,/Out stored: —/);
  assert.equal(f.api.state.rows.size,2);assert.ok(f.requests.every(([u])=>u.startsWith('/api/claude/events?')));assert.equal(f.messages.length,0);
});
test('production history calls shared descriptor core: different 200K tails, verb and original short-first ordering',async()=>{
  const str=value=>({kind:'string',characters:Array.from(value).length,jsonCharacters:Array.from(JSON.stringify(value)).length,head:Array.from(value).slice(0,128).join(''),tail:Array.from(value).slice(-128).join('')});
  const previews=[];
  for(const tail of ['TAIL-A😀','TAIL-B😀']) {
    const content='🌈'.repeat(200000)+tail, data={schemaVersion:1,status:'available',value:{kind:'object',entries:[['params',{kind:'object',entries:[['content',str(content)],['target',str('[ap:ABC123]')]]}],['verb',str('point.capture')]]}};
    const ev=callEvent(1);ev.calls[0].name='mcp__workflowy__anchor';ev.calls[0].previewData=data;
    const f=fixture(()=>page([ev]));await f.api.browse(session);const preview=f.api.state.rows.get('r1').calls[0].node.children[1].textContent,parsed=JSON.parse(preview);
    assert.equal(Object.keys(parsed)[0],'verb');assert.equal(Object.keys(parsed.params)[0],'target');assert.equal(parsed.params.content,Array.from(content).slice(0,64).join('')+'…'+Array.from(content).slice(-64).join(''));previews.push(preview);
  }
  assert.notEqual(previews[0],previews[1]);
});
test('exhaustive cursor navigation beyond 100/200 with bounded viewport, checkpoints and no hidden history cutoff',async()=>{
  const f=fixture(url=>{const q=new URLSearchParams(url.split('?')[1]);const n=Number((q.get('cursor')||'p0').slice(1));return page(Array.from({length:100},(_,i)=>event(n*100+i)),{hasMore:n<4,nextCursor:n<4?'p'+(n+1):null});});
  await f.api.browse(session);await f.api.latest();assert.equal(f.requests.length,5);assert.equal(f.api.state.rows.size,200);assert.equal(f.api.state.next,null);assert.equal(f.api.state.pages.length,5);
  await f.api.page('p3','older',3);assert.ok(f.api.state.rows.has('r300'));assert.equal(f.api.state.accepted,null);
});
test('unchanged keyed rows remain mounted; prepend pixel anchor and scrolled-up append/new count/Jump latest',async()=>{
  const f=fixture();await f.api.browse(session);f.api.reconcile([event(2),event(3)],'forward','epoch');
  const original=f.api.state.rows.get('r1').node;f.api.reconcile([event(1)],'delta','epoch');assert.equal(f.api.state.rows.get('r1').node,original);
  f.api.setFollow(false,{byScroll:true}); // Dan scrolls up: following pauses and the view must hold still
  f.el('history-viewport').scrollTop=25;const y=original.getBoundingClientRect().top;
  f.api.reconcile([event(0)],'older','epoch');assert.equal(original.getBoundingClientRect().top,y);
  const top=f.el('history-viewport').scrollTop;f.api.reconcile([event(4)],'delta','epoch');assert.equal(f.el('history-viewport').scrollTop,top);assert.equal(f.api.state.newCount,1);
  await f.api.latest();assert.equal(f.el('history-viewport').scrollTop,f.el('history-viewport').scrollHeight);
});
test('tools-only/density do not change cursors, full identities or held selection',async()=>{
  const f=fixture(()=>page([callEvent(1)]));await f.api.browse(session);const before=f.api.state.delta,row=f.api.state.rows.get('r1').node;
  f.el('history-tools').checked=true;f.el('history-tools').onchange();f.el('history-density').value='5';f.el('history-density').onchange();
  assert.equal(f.api.state.delta,before);assert.equal(f.api.state.rows.get('r1').node,row);assert.equal(f.el('history-events').attributes['--preview-lines'],'5');assert.equal(f.api.state.accepted,null);
});
test('append completion updates existing row counts/status without repaint, body fetch, target or selection change',async()=>{
  let appended=false;const f=fixture(()=>appended?page([event(2)],{change:'append',summaryUpdates:[{recordRef:'r1',block:0,id:'id1',status:'stored',outputBytes:262144}]}):page([callEvent(1)]));
  await f.api.browse(session);const mounted=f.api.state.rows.get('r1').node,call=mounted.children[2];appended=true;
  await f.api.page('delta','delta');assert.equal(f.api.state.rows.get('r1').node,mounted);assert.match(call.textContent,/stored.*Out stored: 256.0 KiB/s);
  assert.equal(f.api.state.accepted,null);assert.equal(f.messages.length,0);assert.equal(f.requests.filter(([u])=>u.includes('tool-record')||u.includes('/inspect')).length,0);
});
test('old scope response after A→B→A cannot paint or change target; FileEpoch error retains labeled snapshot',async()=>{
  let release;const f=fixture(()=>new Promise(r=>release=r));const old=f.api.browse(session);await f.flush();const rold=release;
  f.api.state.loading=false;const b=f.api.browse({...session,sessionRef:'B'});await f.flush();const rb=release;
  f.api.state.loading=false;const a=f.api.browse(session);await f.flush();release(page([event(3)]));await a;rb(page([event(2)],{sessionRef:'B'}));await b;
  rold(page([event(1)]));await old; // first request intentionally resolves last
  assert.equal(f.api.state.rows.has('r2'),false);assert.equal(f.api.state.rows.has('r3'),true);
  assert.equal(f.api.state.rows.has('r1'),false);
  assert.equal(f.api.state.target,'target');
});
test('scope trace and ambiguous leaf have no target; browse never relays Inspect/target',async()=>{
  const f=fixture(()=>page([],{scopeRef:'trace',targetRef:null,status:'ambiguous',traces:['agent:a'],diagnostics:[{offset:4,status:'unsupported'}]}));
  await f.api.browse(session,session.scopes[1]);assert.equal(f.api.state.target,null);assert.match(f.el('history-status').textContent,/unsupported/);assert.equal(f.messages.length,0);assert.equal(f.requests.length,1);
});
test('index saturation displays later-history marker while indexed prefix pages remain usable',async()=>{
  const limit={status:'index-limit',message:'Later history not indexed; only the indexed prefix is available.',indexedRecords:229,indexedBytes:3000000,sourceBytes:5933058};
  const f=fixture(url=>{const cursor=new URLSearchParams(url.split('?')[1]).get('cursor');return page([event(cursor?2:1)],{
    status:'index-limit',targetRef:null,indexLimit:limit,hasMore:!cursor,nextCursor:cursor?null:'prefix-next'});});
  await f.api.browse(session);assert.match(f.el('history-status').textContent,/Later history not indexed.*Indexed 229 records \/ 3000000 source bytes of 5933058/s);
  assert.equal(f.api.state.target,null);assert.equal(f.api.state.blocked,false);assert.equal(f.api.state.next,'prefix-next');
  await f.api.page(f.api.state.next);assert.ok(f.api.state.rows.has('r1'));assert.ok(f.api.state.rows.has('r2'));
  assert.equal(f.api.state.next,null);assert.match(f.el('history-status').textContent,/Later history not indexed/);
});
test('target direct parent presence/ACK is metadata-only; another tab answering first never receives it',async()=>{
  const f=fixture();await f.api.browse(session);f.api.useTarget();const first=f.messages[0];
  assert.equal(f.el('target-status').textContent.includes('not yet applied'),true);const absent=[...f.timers.values()].at(-1);absent();assert.match(f.el('target-status').textContent,/not applied: no parent editor/);
  f.api.useTarget();const hello=f.messages.at(-1);
  f.api.targetMessage({data:{schemaVersion:1,type:'presence',clientId:hello.clientId,requestId:hello.requestId,editorId:'another-editor',targetRevision:2}});
  assert.equal(f.messages.filter(m=>m.type==='target').length,0);
  f.api.targetMessage(f.direct({schemaVersion:1,type:'presence',clientId:hello.clientId,requestId:hello.requestId,editorId:'editor',targetRevision:9}));
  [...f.timers.values()].at(-1)();
  const action=f.messages.at(-1);assert.equal(action.targetRevision,9);assert.deepEqual(Object.keys(action).sort(),['schemaVersion','type','clientId','editorId','actionId','actionRevision','targetRevision','targetRef'].sort());
  f.api.targetMessage(f.direct({schemaVersion:1,type:'ack',clientId:first.clientId,editorId:'editor',actionId:first.requestId,actionRevision:1,applied:true}));assert.doesNotMatch(f.el('target-status').textContent,/Target applied/);
  f.api.targetMessage({data:{schemaVersion:1,type:'ack',clientId:action.clientId,editorId:'editor',actionId:action.actionId,actionRevision:action.actionRevision,applied:true}});assert.doesNotMatch(f.el('target-status').textContent,/Target applied/);
  f.api.targetMessage(f.direct({schemaVersion:1,type:'ack',clientId:action.clientId,editorId:'editor',actionId:action.actionId,actionRevision:action.actionRevision,applied:true,reason:'read-only'}));assert.match(f.el('target-status').textContent,/another pane tab is open; target applied here only/);
});
test('standalone sessions never sends a target or selects an integrated editor',async()=>{
  const f=fixture(undefined,{embedded:false});await f.api.browse(session);f.api.useTarget();
  assert.equal(f.messages.length,0);assert.match(f.el('target-status').textContent,/standalone sessions is read-only/);
});
test('integrated target delivery still works when BroadcastChannel duplicate discovery is unavailable',async()=>{
  const f=fixture();await f.api.browse(session);f.api.state.channel=null;f.api.useTarget();
  const hello=f.messages.at(-1);assert.equal(f.messages.length,1);
  f.api.targetMessage(f.direct({schemaVersion:1,type:'presence',clientId:hello.clientId,requestId:hello.requestId,editorId:'parent',targetRevision:3}));
  [...f.timers.values()].at(-1)();const action=f.messages.at(-1);
  f.api.targetMessage(f.direct({schemaVersion:1,type:'ack',clientId:action.clientId,editorId:'parent',actionId:action.actionId,actionRevision:action.actionRevision,applied:true}));
  assert.match(f.el('target-status').textContent,/Ready:/);assert.doesNotMatch(f.el('target-status').textContent,/other.*tab|here only/);assert.equal(action.targetRevision,3);
});
test('Inspect is serial/latest-coalesced, ACK-only highlight, no retry/arrival selection',async()=>{
  let release;let post=0;const f=fixture((url,opts)=>{if(url==='/api/claude/inspect'){post++;return new Promise(r=>release=()=>{const c=JSON.parse(opts.body);const m={epoch:'mail',revision:post,selection:{...c,selectorId:c.selectorId}};f.box(m);r(m);});}});
  await f.api.browse(session);const c=n=>({key:'key'+n,callRef:'call'+n,fileEpoch:'epoch'});
  f.api.inspect(c(1));await f.flush();f.api.inspect(c(2));f.api.inspect(c(3));assert.equal(post,1);assert.equal(f.api.state.accepted,null);release();await f.flush();assert.equal(post,2);release();await f.flush();await f.flush();
  assert.equal(f.api.state.accepted.callRef,'call3');assert.match(f.el('inspect-status').textContent,/accepted/);assert.equal(post,2);
});
test('close or another backend after POST commit but before delayed ACK cannot claim accepted highlight',async()=>{
  for(const selection of [null,{backend:'opencode',sessionId:'ses_real'}]) {
    let release;const f=fixture((url,opts)=>{if(url==='/api/claude/inspect'){const click=JSON.parse(opts.body),m={epoch:'mail',revision:2,selection:click};f.box(m);return new Promise(r=>release=()=>r(m));}});
    await f.api.browse(session);f.api.inspect({key:'held',callRef:'call1',fileEpoch:'epoch'});await f.flush();
    f.box({epoch:'mail',revision:3,selection});release();await f.flush();await f.flush();assert.equal(f.api.state.accepted,null);assert.match(f.el('inspect-status').textContent,/closed or replaced/);
    assert.equal(f.requests.filter(([u])=>u==='/api/claude/inspect').length,1);
  }
});
test('wrong FileEpoch/permanent response retains old view, blocks polls and requires deliberate reselect',async()=>{
  let wrong=false;const f=fixture(()=>page([event(wrong?2:1)],{fileEpoch:wrong?'replacement':'epoch'}));await f.api.browse(session);wrong=true;
  // FMPZO7: a changed FileEpoch means the server rebuilt its index (a restart, an eviction): the old view
  // stays, nothing is spliced, and a resync re-reads it by position instead of stopping for a reselect.
  await f.api.page('delta','delta');assert.equal(f.api.state.blocked,false);assert.ok(f.api.state.resync,'a resync starts');
  assert.ok(f.api.state.rows.has('r1'));assert.equal(f.api.state.rows.has('r2'),false);
  assert.match(f.el('history-status').textContent,/Reconnecting: the server no longer knows this view’s handles \(history identity\/FileEpoch changed/);
});
test('exact accepted call rehydrates remount and scope reset does not clear server held call',async()=>{
  const f=fixture((url,opts)=>{if(url.startsWith('/api/claude/events'))return page([callEvent(1)]);if(url==='/api/claude/inspect'){const c=JSON.parse(opts.body);const m={epoch:'mail',revision:2,selection:{...c}};f.box(m);return m;}});
  await f.api.browse(session);f.api.state.rows.get('r1').calls[0].node.onclick();await f.flush();await f.flush();assert.ok(f.api.state.accepted);assert.equal(f.api.state.rows.get('r1').calls[0].node.attributes['aria-pressed'],'true');
  f.api.reconcile([event(2)],'delta','epoch');assert.ok(f.api.state.accepted);const postCount=f.requests.filter(([u])=>u==='/api/claude/inspect').length;
  await f.api.browse(session);assert.equal(f.api.state.accepted,null);assert.equal(f.requests.filter(([u])=>u==='/api/claude/inspect').length,postCount);
  f.box({epoch:'mail',revision:3,selection:null});await f.api.poll();assert.equal(f.api.state.accepted,null);
});
test('production history sender and pane receiver handshake only across their own embedded window, browse independent',async()=>{
  const {pane}=require('./pane-fixture.cjs'),editor=pane();
  vm.runInContext('this.bridge={handleTargetMessage,setBackend};',editor.ctx);editor.api.state.editorId='editor';
  const target='T'.repeat(32),identity={storeRef:'S'.repeat(32),sessionRef:'J'.repeat(32),scopeRef:'M'.repeat(32),leafRef:'L'.repeat(32),fileEpoch:'E'.repeat(32)};
  const targetData=env({...identity,status:'complete',targetRef:target,targetInfo:env({...identity,targetRef:target,scope:'main',uuid:'11111111-1111-4111-8111-111111111111',leafUuid:'22222222-2222-4222-8222-222222222222',title:'Validated',source:'windows-history',bindingRef:null})});
  editor.ctx.fetch=async()=>({ok:true,text:async()=>JSON.stringify(targetData)});
  const f=fixture(()=>page([event(1)],{targetRef:target}));await f.api.browse(session);editor.box.value='draft stays';
  const work=[],child={postMessage:data=>f.api.targetMessage(f.direct(data))};
  editor.ctx.location={origin:f.origin};editor.el('claude-sessions-frame').contentWindow=child;
  f.parent.postMessage=data=>work.push(editor.ctx.bridge.handleTargetMessage({data,source:child,origin:f.origin}));
  assert.equal(editor.api.state.backend,'opencode');assert.equal(editor.api.state.targetRevision,0);
  f.api.useTarget();await Promise.all(work);[...f.timers.values()].at(-1)();await f.flush();await Promise.all(work);
  assert.equal(editor.api.state.backend,'claude');assert.equal(editor.api.state.claudeTarget.targetRef,target);assert.match(f.el('target-status').textContent,/Ready:/);
  assert.equal(editor.box.value,'draft stays');assert.equal(editor.el('send-btn').disabled,true);assert.equal(editor.api.state.targetRevision,1);
  await f.api.browse(session);assert.equal(editor.api.state.targetRevision,1);assert.equal(editor.api.state.claudeTarget.targetRef,target);
});
test('Dan: fresh live Manager is first and one row selection targets real app.js before its cold history completes, with no click or red',async()=>{
  const {pane}=require('./pane-fixture.cjs'),editor=pane();vm.runInContext('this.bridge={handleTargetMessage};',editor.ctx);editor.api.state.editorId='editor';
  const uuid='11111111-1111-4111-8111-111111111111',t=env({storeRef:'S'.repeat(32),sessionRef:'J'.repeat(32),scopeRef:'M'.repeat(32),leafRef:'L'.repeat(32),fileEpoch:'E'.repeat(32),targetRef:'T'.repeat(32),scope:'main',uuid,leafUuid:null,title:'Manager-Session-new',source:'host',executionId:'cc-'+uuid,generation:1,role:'Manager',lifecycle:'live',cwd:'/private/workspace',capabilities:{nativeInput:true,controls:true}});
  const live={...t,state:'ready',scopes:[{label:'main',scopeRef:t.scopeRef}]},old={...live,sessionRef:'old',targetRef:null,uuid:'ended-uuid',executionId:'cc-ended',title:'Old Manager',state:'exited',lifecycle:'ended',capabilities:{nativeInput:false,controls:false}};
  const targetData=env({...t,status:'complete',events:[],branches:[],diagnostics:[],targetInfo:t});let release;
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:[old,live],status:'complete',hasMore:false}):new Promise(r=>release=()=>r(targetData)));
  const work=[],child={postMessage:data=>f.api.targetMessage(f.direct(data))};editor.ctx.location={origin:f.origin};editor.el('claude-sessions-frame').contentWindow=child;
  f.parent.postMessage=data=>work.push(editor.ctx.bridge.handleTargetMessage({data,source:child,origin:f.origin}));editor.ctx.fetch=async()=>({ok:true,text:async()=>JSON.stringify(targetData)});editor.box.value='dictation stays';
  await f.api.list();const buttons=f.el('sessions-list').children.find(g=>g.dataset.group==='managers').children.slice(1);assert.match(buttons[0].textContent,/Manager-Session-new.*live.*Send ready/);assert.doesNotMatch(buttons[0].textContent,/view-only/);
  const browsing=f.api.browse(live);await f.flush();assert.equal(f.api.state.loading,true);assert.ok(f.messages.some(m=>m.type==='hello'));
  await Promise.all(work);f.timers.get(f.api.state.targetAction.timer)();await f.flush();await Promise.all(work);
  assert.equal(editor.api.state.claudeTarget.executionId,live.executionId);assert.equal(editor.el('send-btn').disabled,false);assert.match(editor.el('status').textContent,/Ready: sending to Manager-Session-new/);
  assert.equal(f.el('target-status').dataset.error,'false');assert.doesNotMatch(f.el('target-status').textContent,/here only|another.*tab/);assert.equal(editor.box.value,'dictation stays');
  assert.equal(f.api.state.loading,true);release();await browsing;
});
test('Dan: ended Manager explicitly identifies and links its live replacement; archived Refine remains a deliberate choice',async()=>{
  const live={...session,source:'host',role:'Manager',state:'ready',lifecycle:'live',cwd:'/private',executionId:'cc-new',title:'New live Manager',capabilities:{nativeInput:true,controls:true}},old={...live,sessionRef:'session',title:'Ended old Manager',executionId:'cc-old',state:'exited',lifecycle:'ended',capabilities:{nativeInput:false,controls:false},successor:live};
  const f=fixture(()=>page([],{targetRef:'archived',targetInfo:{...old},liveState:{state:'exited'}}));f.api.state.inventory=[old,live];await f.api.browse(old);
  assert.match(f.el('history-activity').textContent,/This Manager has ended.*live Manager is New live Manager/);assert.match(f.el('history-capabilities').textContent,/Archived.*Refine/);
  assert.equal(f.el('history-successor').hidden,false);assert.match(f.el('history-successor').textContent,/Select live Manager: New live Manager/);assert.equal(f.el('history-target').disabled,false);assert.equal(f.messages.length,0);
});
test('Dan 216: selecting a reconnectable ended Manager replaces the previous Send target without waking either conversation',async()=>{
  for(const lifecycle of ['ended','sleeping']){
    const {pane}=require('./pane-fixture.cjs'),editor=pane();
    editor.api.state.editorId='editor';
    const uuid='11111111-1111-4111-8111-111111111111',previousUuid='22222222-2222-4222-8222-222222222222';
    const t=env({storeRef:'S'.repeat(32),sessionRef:'J'.repeat(32),scopeRef:'M'.repeat(32),leafRef:'L'.repeat(32),fileEpoch:'E'.repeat(32),targetRef:'T'.repeat(32),scope:'main',uuid,leafUuid:null,title:'Manager-Session-216',source:'host',executionId:'cc-'+uuid,generation:1,role:'Manager',lifecycle,cwd:'/private/workspace',capabilities:{nativeInput:false,controls:false,reconnect:true}});
    const row={...t,state:'exited',scopes:[{label:'main',scopeRef:t.scopeRef}],liveState:{state:'exited',owner:'gone',close_reason:'quota_halt'}};
    editor.api.state.backend='claude';
    editor.api.state.claudeTarget={...t,uuid:previousUuid,executionId:'cc-'+previousUuid,targetRef:'P'.repeat(32),title:'Manager-Session-215',lifecycle:'live',capabilities:{nativeInput:true,controls:true}};
    editor.box.value='handoff for 216, never 215';
    const targetData=env({...t,status:'complete',events:[],branches:[],diagnostics:[],targetInfo:t});
    const f=fixture(()=>targetData),work=[],requests=[];
    const child={postMessage:data=>f.api.targetMessage(f.direct(data))};
    editor.ctx.location={origin:f.origin};editor.el('claude-sessions-frame').contentWindow=child;
    f.parent.postMessage=data=>work.push(editor.api.handleTargetMessage({data,source:child,origin:f.origin}));
    editor.ctx.fetch=async(url,options)=>{
      requests.push({url,method:options?.method||'GET'});
      assert.notEqual(options?.method,'POST','selecting a conversation never reconnects or sends');
      return {ok:true,text:async()=>JSON.stringify(targetData)};
    };
    await f.api.browse(row);await f.flush();await Promise.all(work);
    const action=f.api.state.targetAction;
    assert.ok(action,'the reconnectable Manager selection must target the editor, not leave 215 selected');
    f.timers.get(action.timer)();await f.flush();await Promise.all(work);
    assert.equal(editor.api.state.claudeTarget.executionId,row.executionId);
    assert.equal(editor.api.reconnectableManager(),true);
    assert.equal(editor.el('send-btn').disabled,false);
    assert.equal(editor.box.value,'handoff for 216, never 215');
    assert.ok(requests.length>0);assert.ok(requests.every(r=>r.method==='GET'));
  }
});
test('Dan 216: native workers and stored history never auto-target the editor; only a Manager row does',async()=>{
  const worker={...session,source:'host',role:'worker',state:'ready',lifecycle:'live',executionId:'cc-worker',title:'Worker row',capabilities:{nativeInput:true,controls:true,reconnect:true}};
  const history={...session,title:'Archived history',source:'windows-history',state:'unmanaged-history'};
  for(const row of [worker,history]) {
    const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:[row],status:'complete',hasMore:false}):page([event(1)],{targetInfo:row}));
    await f.api.browse(row);
    assert.equal(f.api.state.targetAction,null,'no target action is created for '+row.role);
    assert.equal(f.messages.filter(m=>m.type==='target'||m.type==='hello').length,0,'nothing is posted for '+row.role);
  }
});
test('Dan: inventory follows fresh launches on its modest automatic cadence without retargeting existing editor intent',async()=>{
  let launched=false,lists=0;const fresh={...session,sessionRef:'new',title:'Fresh Manager',source:'host',role:'Manager',state:'ready',capabilities:{nativeInput:true,controls:true}};
  const f=fixture(url=>{if(url.startsWith('/api/claude/sessions')){lists++;return env({sessions:launched?[session,fresh]:[session],status:'complete',hasMore:false});}return page([]);});await f.api.list();launched=true;
  for(let i=0;i<4;i++)f.advance();await f.api.poll();await f.flush();
  assert.equal(lists,2,'the launch cadence reads the windowed inventory twice, never the Manager lane');
  assert.equal(f.familyRequests.length,2,'the unwindowed Manager summaries ride their own lane');
  // The fresh Manager is the remembered newest fallback, so it pins at the top and leaves a home stub.
  assert.equal(f.el('sessions-pinned').hidden,false);assert.match(f.el('sessions-pinned').textContent,/Fresh Manager/);assert.match(f.el('sessions-pinned').textContent,/live/);
  assert.equal(f.el('sessions-list').children.find(g=>g.dataset.group==='managers').children[1].textContent,'↑ Fresh Manager · pinned at top');
  assert.equal(f.messages.length,0);
});
test('ended Manager successor link works even when the live row is outside the mounted inventory page',async()=>{
  const live={...session,sessionRef:'live-session',uuid:'live-uuid',title:'Live outside this page',source:'host',role:'Manager',state:'ready',lifecycle:'live',executionId:'cc-live',targetRef:'N'.repeat(32),scopes:[{label:'main',scopeRef:'live-scope'}],capabilities:{nativeInput:true,controls:true}},old={...session,source:'host',role:'Manager',state:'exited',lifecycle:'ended',successor:live};
  const f=fixture(url=>url.includes('sessionRef=live-session')?page([],{sessionRef:live.sessionRef,scopeRef:'live-scope',fileEpoch:'live-epoch',targetRef:live.targetRef,liveState:{state:'ready'},targetInfo:live}):page([],{targetInfo:old,liveState:{state:'exited'}}));
  f.api.state.inventory=[old];await f.api.browse(old);f.el('history-successor').onclick();await f.flush();
  assert.equal(f.api.state.session,live);assert.ok(f.messages.some(m=>m.type==='hello'));assert.equal(f.el('target-status').dataset.error,'false');assert.equal(f.api.state.blocked,false);
  assert.ok(f.requests.every(([url])=>!url.startsWith('/api/claude/sessions'))); // no first-page-only rediscovery dead end
});
test('accepted exact identity survives actual viewport eviction and older-page remount without another POST',async()=>{
  const f=fixture((url,opts)=>{if(url.startsWith('/api/claude/events'))return page([callEvent(1)]);if(url==='/api/claude/inspect'){const m={epoch:'mail',revision:2,selection:JSON.parse(opts.body)};f.box(m);return m;}});
  await f.api.browse(session);f.api.state.rows.get('r1').calls[0].node.onclick();await f.flush();await f.flush();const held=f.api.state.accepted.key;
  f.el('history-viewport').scrollTop=250;f.api.reconcile(Array.from({length:201},(_,i)=>event(i+10)),'forward','epoch');assert.equal(f.api.state.rows.has('r1'),false);assert.equal(f.api.state.accepted.key,held);
  const posts=f.requests.filter(([u])=>u==='/api/claude/inspect').length;f.api.reconcile([callEvent(1)],'older','epoch');assert.equal(f.api.state.rows.get('r1').calls[0].node.attributes['aria-pressed'],'true');assert.equal(f.requests.filter(([u])=>u==='/api/claude/inspect').length,posts);
});
test('one list read gathers the whole inventory (>100 rows over 17 server pages) into its group, with a show-more at the group bottom; browsing and target untouched',async()=>{
  const f=fixture(url=>{if(url.startsWith('/api/claude/events'))return page([event(1)]);const q=new URLSearchParams(url.split('?')[1]),n=Number((q.get('cursor')||'s0').slice(1));return env({sessions:Array.from({length:8},(_,i)=>({...session,sessionRef:'s'+(n*8+i),uuid:'id'+(n*8+i)})),status:n<16?'discovering':'complete',hasMore:n<16,nextCursor:n<16?'s'+(n+1):null});});
  await f.api.browse(session);const generation=f.api.state.generation,target=f.api.state.target;
  await f.api.list();assert.equal(f.api.state.inventory.length,136);assert.equal(f.requests.filter(([u])=>u.startsWith('/api/claude/sessions')).length,17);
  const other=()=>f.el('sessions-list').children.find(g=>g.dataset.group==='other'),group=other();assert.match(group.children[0].textContent,/Other · 136/);
  assert.equal(group.children.length,1+30+1); // heading, the newest 30, the group's own foot
  const foot=group.children.at(-1);assert.match(foot.textContent,/Show 30 more of 106 older in this group/);foot.children[0].onclick();
  assert.equal(other().children.length,1+60+1);assert.match(other().children.at(-1).textContent,/Show only the newest 30/);
  assert.equal(f.api.state.generation,generation);assert.equal(f.api.state.target,target);assert.equal(f.messages.length,0);
});
test('transient outage is bounded backoff with no overlap; oversized client response is loud and never a prefix',async()=>{
  let failure=false;const f=fixture(()=>failure?new Response(JSON.stringify({error:'offline'}),{status:502}):page([event(1)]));await f.api.browse(session);failure=true;
  await f.api.page('delta','delta');assert.equal(f.api.state.blocked,false);assert.equal(f.api.state.nextPoll,103000);const count=f.requests.length;
  await f.api.poll();assert.equal(f.requests.length,count);assert.ok(f.api.state.rows.has('r1'));
  const huge=fixture(()=>new Response('x'.repeat(8*1024*1024+1)));await huge.api.browse(session);assert.equal(huge.api.state.blocked,true);assert.equal(huge.api.state.rows.size,0);assert.match(huge.el('history-status').textContent,/limit; not cropped/);
});
test('Jump-to-latest continuation cannot paint stale error after A→B→A with the same issued cursor',async()=>{
  let release;const f=fixture(url=>{const q=new URLSearchParams(url.split('?')[1]);if(q.get('cursor')==='later')return new Promise(r=>release=r);return page([event(1)],{sessionRef:q.get('sessionRef'),hasMore:true,nextCursor:'later'});});
  await f.api.browse(session);const old=f.api.latest();await f.flush();
  await f.api.browse({...session,sessionRef:'B'});await f.api.browse(session);const status=f.el('history-status').textContent;
  release(page([event(2)]));await old;assert.equal(f.el('history-status').textContent,status);assert.equal(f.api.state.rows.has('r2'),false);assert.equal(f.api.state.next,'later');
});
test('Jump-to-latest clears the displayed new-event counter as well as viewport state',async()=>{
  let delta=false;const f=fixture(()=>page([event(delta?2:1)]));await f.api.browse(session);f.api.setFollow(false,{byScroll:true});
  f.api.reconcile([event(3),event(4)],'forward','epoch');f.el('history-viewport').scrollTop=0;delta=true;
  await f.api.page('delta','delta');assert.match(f.el('history-status').textContent,/1 new events/);await f.api.latest();assert.match(f.el('history-status').textContent,/0 new events/);
});
test('history pages show the measured context, the transcript source progress and a relabeled viewer memory',async()=>{
  const readout={state:'measured',occupancy_tokens:294246,window_tokens:1000000,fraction_used:0.294246,
    measured_at:1792005183.797,as_of:'2026-10-14T19:13:03Z',label:'measured: as of the previous model call (2026-10-14T19:13:03Z, 12s old)'};
  const progress={indexedBytes:1888752,indexedRecords:380,sourceBytes:1888752,status:'complete',kind:'native-transcript'};
  const f=fixture(()=>page([event(1)],{contextReadout:readout,sourceProgress:progress}));
  await f.api.browse(session);
  assert.match(f.el('history-activity').textContent,/ · context 294K \/ 1M \(29%\) · as of \d{2}:\d{2}$/);
  assert.equal(f.el('history-activity').dataset.context,'measured');
  assert.equal(f.el('history-activity').title,readout.label);
  assert.match(f.el('history-window').textContent,/viewer memory \d+ bytes \(resident rows, not context\)/);
  assert.match(f.el('history-window').textContent,/viewer source 1\.9 MB \/ 1\.9 MB indexed \(transcript\)/);
  assert.doesNotMatch(f.el('history-window').textContent,/stored view bytes/);
});
test('an unknown context readout says why, an absent one invents nothing, and later pages replace the figure',async()=>{
  const unknown={state:'unavailable',reason:'no completed model call yet in this generation',occupancy_tokens:null};
  const g=fixture(()=>page([event(1)],{contextReadout:unknown}));await g.api.browse(session);
  assert.match(g.el('history-activity').textContent,/ · context unknown: no completed model call yet in this generation$/);
  assert.equal(g.el('history-activity').dataset.context,'unavailable');
  const bare=fixture();await bare.api.browse(session);
  assert.equal(bare.api.state.contextReadout,null);assert.equal(bare.el('history-activity').dataset.context,'none');
  assert.doesNotMatch(bare.el('history-activity').textContent,/context (unknown|\d)/);
  let pageReadout={state:'measured',occupancy_tokens:294246,window_tokens:1000000,measured_at:1792005183.797};
  const f=fixture(()=>page([event(1)],pageReadout?{contextReadout:pageReadout}:{}));
  await f.api.browse(session);assert.match(f.el('history-activity').textContent,/context 294K/);
  pageReadout={state:'measured',occupancy_tokens:298875,window_tokens:1000000,measured_at:1792005364.917};
  await f.api.page('delta','delta');assert.match(f.el('history-activity').textContent,/context 299K/);
  pageReadout=null;
  await f.api.page('delta','delta');assert.match(f.el('history-activity').textContent,/context 299K/); // omission keeps the last figure
});
test('a measured readout without a native window still shows its tokens and as-of time',async()=>{
  const readout={state:'measured',occupancy_tokens:294246,window_tokens:null,fraction_used:null,
    measured_at:1792005183.797,as_of:'2026-10-14T19:13:03Z'};
  const f=fixture(()=>page([event(1)],{contextReadout:readout}));await f.api.browse(session);
  assert.match(f.el('history-activity').textContent,/ · context 294K · as of \d{2}:\d{2}$/);
  assert.doesNotMatch(f.el('history-activity').textContent,/context unknown/);
});
test('Dan: a collected Manager reads sleeping (reconnects on Send) and stays a selectable Send target',async()=>{
  const live={...session,source:'host',role:'Manager',state:'ready',lifecycle:'live',cwd:'/private',executionId:'cc-new',title:'New live Manager',capabilities:{nativeInput:true,controls:true}};
  const asleep={...live,sessionRef:'collected-session',title:'Collected Manager',uuid:'collected-uuid',executionId:'cc-collected',generation:1,
    state:'exited',lifecycle:'sleeping',close_reason:'collected',capabilities:{nativeInput:false,controls:false,reconnect:true},successor:live};
  const f=fixture(url=>url.startsWith('/api/claude/sessions')?env({sessions:[asleep,live],status:'complete',hasMore:false}):
    page([],{sessionRef:asleep.sessionRef,targetRef:'archived',targetInfo:{...asleep},liveState:{state:'exited',close_reason:'collected'}}));
  await f.api.browse(asleep);
  assert.match(f.el('history-activity').textContent,/^sleeping \(reconnects on Send\)/);
  assert.match(f.el('history-capabilities').textContent,/sleeping \(reconnects on Send\).*Send reconnects the conversation/);
  assert.equal(f.el('history-target').disabled,false);      // still selectable as a Send target
  f.api.useTarget();assert.ok(f.messages.some(m=>m.type==='hello'));
  await f.api.list();
  const row=f.el('sessions-pinned').children[0].textContent;
  assert.match(row,/sleeping \(reconnects on Send\) · Send reconnects it/);
  assert.doesNotMatch(row,/history \/ context only/);
});
test('ownership-unknown, worker and archived history rows gain no reconnect wording or Send capability',async()=>{
  const unknown={...session,source:'host',role:'Manager',state:'ownership_unknown',lifecycle:'pending',executionId:'cc-unknown',uuid:'unknown-uuid',title:'Unknown owner',capabilities:{nativeInput:false,controls:false,reconnect:false}};
  const worker={...session,source:'host',role:'worker',state:'exited',lifecycle:'ended',executionId:'cc-worker',uuid:'worker-uuid',title:'Worker row',capabilities:{nativeInput:false,controls:false,reconnect:false}};
  const history={...session,title:'Archived history',state:'unmanaged-history'};
  const f=fixture(()=>env({sessions:[unknown,worker,history],status:'complete',hasMore:false}));await f.api.list();
  // The ownership-unknown row is a Manager: the remembered fallback pins it, so its row lives above the groups.
  const pinned=f.el('sessions-pinned').children.map(n=>n.textContent);
  const rows=[...pinned,...f.el('sessions-list').children.flatMap(group=>group.children.slice(1)).filter(n=>n.className==='session-item').map(n=>n.textContent)];
  assert.equal(rows.length,3);assert.match(pinned[0],/Unknown owner/);
  for(const row of rows){assert.doesNotMatch(row,/reconnects on Send|Send reconnects it/);assert.match(row,/history \/ context only/);}
  assert.equal(f.el('sessions-list').children.find(g=>g.dataset.group==='managers').children[1].textContent,'↑ Unknown owner · pinned at top');
});

// ---------------------------------------------------------------- round 2: Dan's first real use
const hostSession={...session,source:'host',role:'Manager',executionId:'cc-native-uuid',generation:1,lifecycle:'live',state:'ready',
  capabilities:{history:true,nativeInput:true,controls:true,subagents:true},scopes:[{scopeRef:'scope',label:'main'}]};
const stamp=new Date(100000).setHours(21,58,3)/1000; // today 21:58:03 in the fixture clock
const turn=(n,kind,text,extra={})=>({recordRef:'r'+n,uuid:'uuid-'+n,kind,status:'stored',text,calls:[],time:stamp+n,...extra});
const act=(n,agent,after,extra={})=>({key:'a'+n,agent,after,ordinal:100+n,time:stamp+60+n,generation:1,type:'call',name:'mcp__workflowy__node',
  id:'c'+n,block:0,callRef:'callref'+n,status:'stored',previewData:env({status:'unavailable',reason:'test'}),...extra});
test('Dan: "next" in You/Answers walks forward through every message, not only the first',async()=>{
  const f=fixture(()=>page([turn(1,'user','one'),turn(2,'assistant','a'),turn(3,'user','two'),turn(4,'assistant','b'),turn(5,'user','three')]));
  await f.api.browse(session);f.el('history-viewport').scrollTop=0;
  const top=()=>f.api.state.order.map(k=>f.api.state.rows.get(k)).find(r=>r.node.getBoundingClientRect().top>=0 && r.node.classList.contains('turn-user'));
  f.api.jump('turn-user',1);assert.equal(top().key,'r3');
  f.api.jump('turn-user',1);assert.equal(top().key,'r5');
  f.api.jump('turn-user',-1);assert.equal(top().key,'r3');
});
test('Dan: a readable timestamp replaces the UUID on every turn; the UUID stays on hover',async()=>{
  const f=fixture(()=>page([turn(1,'user','hello')]));await f.api.browse(session);
  const meta=f.api.state.rows.get('r1').meta;
  assert.match(meta.textContent,/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun), [A-Z][a-z]{2} \d{1,2}(st|nd|rd|th), \d{4} 9:58 PM \S+$/);assert.doesNotMatch(meta.textContent,/uuid-1/);assert.match(meta.title,/Record UUID uuid-1/);
});
test('Dan: full turn time reads "Fri, Dec 4th, 2026 3:07 PM ET" with ordinals and US zone letters',()=>{
  const f=fixture(),at=(y,m,d,h,min)=>Date.UTC(y,m-1,d,h,min)/1000,ny={timeZone:'America/New_York'};
  assert.equal(f.api.fullTime(at(2026,12,4,20,7),ny),'Fri, Dec 4th, 2026 3:07 PM ET');
  assert.equal(f.api.fullTime(at(2026,7,1,16,0),ny),'Wed, Jul 1st, 2026 12:00 PM ET'); // daylight time reads ET too
  for(const [day,suffix] of [[2,'nd'],[3,'rd'],[11,'th'],[12,'th'],[13,'th'],[21,'st'],[22,'nd'],[23,'rd'],[31,'st']])
    assert.match(f.api.fullTime(at(2026,10,day,16,0),ny),new RegExp(', Oct '+day+suffix+', 2026 '));
  assert.equal(f.api.fullTime(at(2026,12,4,20,7),{...ny,timeOnly:true}),'3:07 PM ET');
  assert.equal(f.api.fullTime(at(2026,12,4,20,7),{timeZone:'America/Los_Angeles'}),'Fri, Dec 4th, 2026 12:07 PM PT');
  assert.equal(f.api.fullTime(null),'');
});
test('Dan: following pins pages that only grow a subagent group (no new primary row)',async()=>{
  const agents=[{agent:'toolA',index:0,description:'Long job',subagentType:'manager-quick',state:'running'}];
  let activity=[act(1,'toolA','r1')];
  const f=fixture(url=>url.startsWith('/api/claude/events')?page([turn(1,'user','go'),turn(2,'assistant','',{calls:callEvent(2).calls})],{subagentActivity:activity,subagents:agents}):undefined);
  await f.api.browse(hostSession);const viewport=f.el('history-viewport');assert.equal(f.api.state.follow,true);
  const before=viewport.scrollHeight;activity=[act(1,'toolA','r1'),act(2,'toolA','r2')];
  await f.api.page('delta','delta');
  assert.equal(f.el('history-events').children.filter(n=>n.className==='subagent-run').length,2);assert.ok(viewport.scrollHeight>before);
  assert.equal(viewport.scrollTop,viewport.scrollHeight,'the view follows the new subagent group');
  f.api.setFollow(false,{byScroll:true});viewport.scrollTop=40;activity=[act(1,'toolA','r1'),act(2,'toolA','r2'),act(3,'toolA','r2')];
  await f.api.page('delta','delta');assert.equal(viewport.scrollTop,40,'paused never moves');
});
test('Dan: subagent activity groups per subagent between primary turns, colored, expanded, foldable and inspectable',async()=>{
  const agents=[{agent:'toolA',index:0,description:'Opus UI bake-off (blind clone)',subagentType:'manager-quick',state:'running'},
                {agent:'toolB',index:1,description:'UI model research',subagentType:'manager-quick',state:'completed'}];
  const activity=[act(1,'toolA','r1'),act(2,'toolB','r1'),act(3,'toolA','r1',{status:'pending-or-missing'}),act(4,'toolB','r3')];
  const f=fixture(url=>url.startsWith('/api/claude/events')?page([turn(1,'user','go'),turn(2,'user','',{toolResults:1}),turn(3,'assistant','done')],
    {subagentActivity:activity,subagents:agents}):undefined);
  await f.api.browse(hostSession);
  const runs=f.el('history-events').children.filter(n=>n.className==='subagent-run');
  assert.equal(runs.length,2);
  const [first,second]=runs,groups=first.children;
  assert.equal(groups.length,2,'agent A twice and agent B once => two groups, A first');
  assert.match(groups[0].textContent,/Opus UI bake-off \(blind clone\)2 calls/);assert.match(groups[1].textContent,/UI model research1 call/);
  assert.equal(groups[0].open,true);assert.notEqual(groups[0].style.setProperty,undefined);
  const entries=groups[0].children[1].children;assert.equal(entries.length,2);
  assert.equal(entries[1].classList.contains('running'),true,'result-less call of a running subagent is yellow');
  assert.equal(entries[0].classList.contains('running'),false);
  assert.equal(second.children.length,1);
  f.el('ribbon-subagents').onclick();assert.equal(groups[0].open,false);assert.equal(f.el('ribbon-subagents').getAttribute('aria-pressed'),'false');
  f.el('ribbon-subagents').onclick();assert.equal(groups[0].open,true);
  entries[0].onclick();await f.flush();
  const post=f.requests.find(([u])=>u==='/api/claude/inspect');assert.ok(post);assert.equal(JSON.parse(post[1].body).callRef,'callref1');
});
test('native task observations are one-line flags, never full conversation rows',async()=>{
  const f=fixture(()=>page([turn(1,'task','Native task observation (not task acceptance or parent context)\ndescription: Run the suites\nstatus: completed',{status:'task_notification'})]));
  await f.api.browse(session);const row=f.api.state.rows.get('r1');
  assert.equal(row.node.classList.contains('event-flagged'),true);assert.match(row.flag.textContent,/task · Run the suites · completed/);
});

// ---------------------------------------------------------------- round 6: reach the first message
// Five pages of 100 records; each page opens with one of Dan's messages (r0 is the session's first).
const longSession=()=>fixture(url=>{
  if(!url.startsWith('/api/claude/events'))return undefined;
  const q=new URLSearchParams(url.split('?')[1]),k=q.get('cursor')?Number(q.get('cursor').slice(1)):0;
  const events=Array.from({length:100},(_,i)=>{const n=k*100+i;return {...(i===0?{...event(n,'you '+n),kind:'user'}:event(n)),seq:n};});
  return page(events,{hasMore:k<4,nextCursor:k<4?'c'+(k+1):null});
});
const firstUser=f=>f.api.state.order.find(key=>f.api.state.rows.get(key).node.classList.contains('turn-user'));
test('↑ on You walks back across pages it loads on demand until Dan\'s first message, then stops at the session start',async()=>{
  const f=longSession();await f.api.browse(session);await f.api.latest();
  assert.equal(f.api.state.order.length,200);assert.equal(f.api.state.order[0],'r300');assert.equal(f.api.atStart(),false);
  const landed=[];
  for(let i=0;i<5;i++){assert.equal(await f.api.jumpLoading('turn-user',-1),true);landed.push(f.api.state.lastJump.key);}
  assert.deepEqual(landed,['r400','r300','r200','r100','r0']);assert.equal(f.api.atStart(),true);assert.equal(f.api.state.follow,false);
  assert.equal(await f.api.jumpLoading('turn-user',-1),false); // the start: nothing earlier to load
  assert.ok(f.api.state.order.length<=200);
});
test('five quick ↑ clicks are queued, not dropped: five moves, in order',async()=>{
  const f=longSession();await f.api.browse(session);await f.api.latest();
  for(let i=0;i<5;i++)f.el('nav-user-prev').onclick();
  await f.api.state.navQueue;assert.equal(f.api.state.lastJump.key,'r0');assert.equal(f.api.state.navPending,0);
});
test('⤒ First reopens the conversation at its start (also when its checkpoint is gone) and lands on the first message, paused; scrolling on loads later pages',async()=>{
  const f=longSession();await f.api.browse(session);await f.api.latest();
  f.api.state.pages.splice(0,2); // as if the start's checkpoints were evicted
  await f.api.toFirst();assert.equal(f.api.state.order[0],'r0');assert.equal(firstUser(f),'r0');assert.equal(f.api.atStart(),true);
  assert.equal(f.api.state.follow,false);assert.equal(f.api.state.lastJump.key,'r0');assert.equal(f.api.state.next,'c1');
  assert.equal(await f.api.loadNewer(),100);assert.equal(f.api.state.order.at(-1),'r199');assert.equal(f.api.state.next,'c2');
  f.el('history-older').disabled=false;f.api.controls();assert.equal(f.el('history-older').disabled,true); // the start is on screen
});
test('Load earlier messages is the same mechanism: older pages prepend above a stable reading position, newest rows evicted',async()=>{
  const f=longSession();await f.api.browse(session);await f.api.latest();f.api.setFollow(false,{byScroll:true});
  f.el('history-viewport').scrollTop=5000;const anchorKey='r350',before=f.api.state.rows.get(anchorKey).node.getBoundingClientRect().top;
  f.el('history-older').onclick();await f.api.state.navQueue;
  assert.equal(f.api.state.order[0],'r200');assert.equal(f.api.state.order.at(-1),'r399');
  assert.equal(f.api.state.rows.get(anchorKey).node.getBoundingClientRect().top,before);
});
