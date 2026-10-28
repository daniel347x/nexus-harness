// The View page controller (static/glimpse.js) in a VM with a small fake DOM, a recording B2 transport and a
// recording stand-in for the extension's GlimpseMd: the requests it sends, what it paints, what it copies, and what it
// never does (cache refresh unasked, a second renderer, an old reply over a new choice). glimpse-view.html runs the
// actual page with the actual shared renderer.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'../static');
const source=fs.readFileSync(path.join(root,'glimpse.js'),'utf8');
const historySource=fs.readFileSync(path.join(root,'view-history.js'),'utf8');  // loaded before glimpse.js, as on the page
const viewRootsFake=require('./view-roots-fake.js');  // Recent roots' server store, in memory
const pickersSource=fs.readFileSync(path.join(root,'view-pickers.js'),'utf8');
const pageHtml=fs.readFileSync(path.join(root,'glimpse.html'),'utf8');
// The daemon's real include_rows replies (S1's code), shared with test_glimpse_routes.py.
const ENVELOPES=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/glimpse-rows/envelopes.json'),'utf8')).records;
const envelope=name=>JSON.parse(JSON.stringify(ENVELOPES[name].structuredContent));

class El {
  constructor(id,tag='div'){this.id=id;this.tag=tag;this.value='';this.checked=false;this.hidden=false;this.disabled=false;this.open=true;this._text='';this._html='';
    this.dataset={};this.style={};this.children=[];this.attrs={};this.events={};this.scrollTop=0;this.scrollLeft=0;this.clientHeight=400;
    this.classList={add(){},remove(){},contains:()=>false};}
  get textContent(){return this._text+this.children.map(c=>c.textContent||'').join('');}
  set textContent(t){this._text=String(t);this.children=[];}
  get innerHTML(){return this._html;} set innerHTML(h){this._html=String(h);this.children=[];}
  get firstChild(){return this.children[0]||null;}
  focus(){}
  get options(){return this.children;}
  append(...nodes){for(const n of nodes){if(n.fragment)this.children.push(...n.children);else this.children.push(n);}}
  replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
  remove(){}
  setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k]??null;} hasAttribute(k){return k in this.attrs;}
  addEventListener(type,fn){(this.events[type]??=[]).push(fn);}
  fire(type,extra={}){for(const fn of this.events[type]||[])fn({type,target:this,preventDefault(){},shiftKey:false,...extra});}
  click(){this.fire('click');}
  querySelector(sel){return sel==='.g-seg'?(this.seg??=new El(this.id+'-seg')):null;}
  querySelectorAll(){return [];}
  getBoundingClientRect(){return {top:0,left:0,width:100,height:20,bottom:20,right:100};}
  closest(){return null;} select(){}
}

// One page load: elements by id, a transport whose replies the test controls, and a GlimpseMd stand-in.
function page({narrow=true,embedded=false,shared=true,rowPainter=true,assetFailures=[],catalog,search,storage,prep,lightbox,around,meta,list,roots=viewRootsFake(),viewEmbed=null}={}) {
  const els=new Map(),el=id=>{if(!els.has(id))els.set(id,new El(id,/dock|group|sort|direction/.test(id)?'select':'div'));return els.get(id);};
  el('g-sort').value='saved';el('g-direction').value='desc';el('g-direction').hidden=true;el('g-roots-panel').hidden=true;
  const log={fetches:[],renders:[],clipboard:[],toHtml:[],marked:0,compile:[],rowLists:[],docks:[],ancestors:[],meta:[],list:[],lb:[]},pending=[];
  // The Dock list's re-read (the full-screen picker's switches) and the ancestors read: replies the test controls.
  let docksReply=body=>({status:200,body:{schemaVersion:1,...body,docks:[{handle:'[ap:DOCK01]',name:'⭐ Pane Views Dock',favorite:true,local:false,depth:0},
    {handle:'loc-1',name:'Local child',favorite:false,local:true,depth:1},{handle:'all-1',name:'Plain Dock',favorite:false,local:false,depth:0}],errors:[]}});
  let ancestorsReply=body=>({status:200,body:{schemaVersion:1,handle:body.handle,uuids:body.uuids,markdown:'# ⦿ Root\n## • '+body.handle+' Leaf\n',bytes:20,renderedAt:'2026-10-24T08:00:00Z'}});
  let renderReply=body=>({status:200,body:{schemaVersion:1,markdown:'# Doc for '+JSON.stringify(body.value)+'\n',bytes:42,renderedAt:'2026-10-22T07:42:10Z',request:body}});
  const respond=(status,body)=>({ok:status>=200&&status<300,status,json:async()=>body});
  const fetch=async(url,options={})=>{
    log.fetches.push([String(url),options.method||'GET']);
    if(url==='/api/glimpse/catalog')return catalog?respond(...catalog):respond(200,{schemaVersion:1,docks:[{handle:'[ap:DOCK01]',name:'⭐ Pane Views Dock'}],groups:[{handle:'grp-1',name:'opc pane'}],errors:[]});
    if(url==='/api/glimpse/render'){const body=JSON.parse(options.body);log.renders.push(body);
      const reply=await renderReply(body);if(reply==='defer')return new Promise(resolve=>pending.push(r=>resolve(respond(r.status,r.body))));return respond(reply.status,reply.body);}
    if(url==='/api/glimpse/cache-refresh')return respond(200,{schemaVersion:1,attemptedAt:'a',completedAt:'2026-10-22T07:50:03Z'});
    if(url==='/api/glimpse/docks'){const body=JSON.parse(options.body);log.docks.push(body);const r=await docksReply(body);return respond(r.status,r.body);}
    if(url==='/api/glimpse/ancestors'){const body=JSON.parse(options.body);log.ancestors.push(body);const r=await ancestorsReply(body);return respond(r.status,r.body);}
    if(url==='/api/files/meta'){const body=JSON.parse(options.body);log.meta.push(body);const r=meta?meta(body):{status:200,body:{schemaVersion:1,files:body.paths.map(path=>({path,exists:true,width:1500,height:950}))}};return respond(r.status,r.body);}
    if(url==='/api/files/list'){const body=JSON.parse(options.body);log.list.push(body);if(!list)throw new Error('unexpected listing');const r=await list(body);return respond(r.status,r.body);}
    // Recent roots: the in-memory stand-in for view_roots.py (or a reply the test gives, e.g. a pane server from before it).
    if(url==='/api/view/roots'){const body=options.body?JSON.parse(options.body):null;const r=typeof roots==='function'?await roots(options.method||'GET',body):roots.handle(options.method||'GET',body);return respond(r.status,r.body);}
    if(String(url).startsWith('/nexus-md/'))return respond(404,{schemaVersion:1,status:'request',error:'Missing NEXUS asset glimpse-md-render.js in checkout /x; set nexus_checkout in ~/.config/opencode-companion/pane-glimpse.json, then restart pane'});
    throw new Error('unexpected request '+url);
  };
  const GlimpseMd=Object.freeze({
    toHtml:(md,opts)=>{log.toHtml.push({md,opts:{...opts}});if(opts.view==='source'&&opts.onSearchState)opts.onSearchState(opts.findRegex?{count:3,current:0}:{count:0,current:-1});
      return (opts.view==='source'?'<pre class="glimpse-md-preview-plain">':'<section data-shared>')+md+(opts.view==='source'?'</pre>':'</section>');},
    compileSourceSearch:q=>{log.compile.push(q);return q.includes('"')&&q.split('"').length%2===0?{ok:false,error:'unbalanced quotes'}:{ok:true,regex:q.trim()?new RegExp(q.trim(),'gi'):null};},
    sourceHtml:()=>{throw new Error('the page goes through toHtml');},escapeHtml:t=>t,
    // A recording stand-in for the shared painter: one child per row (row._rowEl, as the real one), a day line before
    // every third row under a date sort, and each row's click calling the host's onClick(index, event).
    ...(rowPainter?{renderRowList:(container,rows,opts)=>{log.rowLists.push({container,rows,opts:{...opts,selected:[...opts.selected]}});container.children=[];
      rows.forEach((row,i)=>{if(opts.sortBy!=='none'&&i%3===0){const sep=new El(null);sep.daysep=true;container.children.push(sep);}
        const el=new El(null);el.rowIndex=i;el.click=()=>{opts.onClick(i,{type:'click'});container.focus();};row._rowEl=el;container.children.push(el);});}}:{})});
  const document={getElementById:el,createElement:tag=>new El(null,tag),createDocumentFragment:()=>Object.assign(new El(null),{fragment:true}),
    body:new El('body'),execCommand:()=>false,documentElement:{clientHeight:800,dataset:viewEmbed?{viewEmbed}:{}}};
  const win={document,fetch,console,setTimeout,clearTimeout,performance,Promise,JSON,Number,Math,Date,Object,String,Array,RegExp,Error,Set,Map,
    navigator:{clipboard:{writeText:t=>{log.clipboard.push(t);return Promise.resolve();}}},
    matchMedia:()=>({matches:narrow}),glimpseAssetFailures:[...assetFailures],
    marked:{parse:()=>{log.marked++;return '<p>pane marked</p>';}},...(shared?{GlimpseMd}:{}),...(search?{location:{search},URLSearchParams}:{}),...(storage?{localStorage:storage}:{}),...(lightbox?{GlimpseLightbox:lightbox(log)}:{})};
  win.window=win;win.globalThis=win;win.parent=embedded?(around||{}):win;
  prep?.(el);
  vm.createContext(win);vm.runInContext(historySource,win);vm.runInContext(pickersSource,win);vm.runInContext(source,win);
  return {api:win.opcGlimpseView,el,log,win,pending,setReply:f=>{renderReply=f;},setDocks:f=>{docksReply=f;},setAncestors:f=>{ancestorsReply=f;},
    settle:()=>new Promise(r=>setTimeout(r,5)),
    // A tap on the picker row of catalog entry i (view-pickers.js paints one button per entry; expanders open first).
    pick:(id,i)=>{const row=el('g-'+id).children[0]?.children.find(b=>b.title===(id==='dock'?win.opcGlimpseView.state.catalog.docks:win.opcGlimpseView.state.catalog.groups)[i].name&&b.className.includes('g-pleaf'));
      if(!row)throw new Error('no picker row for '+id+' '+i);row.click();},
    rows:id=>el('g-'+id).children[0]?.children||[]};
}
const flush=()=>new Promise(r=>setTimeout(r,5));
const descendant=(el,cls)=>{
  if(String(el.className||'').split(' ').includes(cls))return el;
  for(const child of el.children||[]){const found=descendant(child,cls);if(found)return found;}
  return null;
};
const cardText=(p,key)=>{
  const card=p.el('g-roots-list').children.find(c=>c.dataset.key===key);
  return ['g-hcard-title','g-hcard-sub','g-hcard-source'].map(cls=>descendant(card,cls)?.textContent||'');
};

test('cold start: one Recent roots read and one catalog read, nothing rendered, no cache refresh; full names listed',async()=>{
  const p=page();await flush();
  assert.deepEqual(p.log.fetches,[['/api/view/roots','GET'],['/api/glimpse/catalog','GET']]);
  assert.deepEqual(p.rows('dock').map(o=>o.textContent),['⭐ Pane Views Dock']);
  assert.deepEqual(p.rows('group').map(o=>o.textContent),['opc pane']);
  assert.equal(p.el('g-dock-count').textContent,'1');assert.equal(p.el('g-group-count').textContent,'1');
  assert.equal(p.el('g-catalog-errors').textContent,'');
  assert.equal(p.el('g-ctl-body').hidden,false,'nothing shown yet: the controls stand open by themselves');
});

test('exact B2 requests: a phone Dock is compact; a node renders its body; desktop opens full',async()=>{
  const p=page();await flush();
  p.pick('dock',1-1);await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1)),'{"kind":"parcel","value":{"dock":"[ap:DOCK01]"},"options":{"headings_only":true,"include_rows":true}}');
  p.pick('group',0);await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1)),'{"kind":"parcel","value":{"group":"grp-1"},"options":{"headings_only":true,"include_rows":true}}');
  p.el('g-handle').value=' [ap:C3Q9NM] ';p.el('g-handle-form').fire('submit');await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1)),'{"kind":"parcel","value":{"node":"[ap:C3Q9NM]"},"options":{"headings_only":false,"include_rows":true}}');
  const seg=p.el('g-handle-form').querySelector('.g-seg');
  seg.fire('click',{target:{closest:()=>({dataset:{how:'subtree'}})}});p.el('g-handle-form').fire('submit');await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1)),'{"kind":"parcel","value":{"subtree":"[ap:C3Q9NM]"},"options":{"headings_only":true,"include_rows":true}}');
  const desk=page({narrow:false});await flush();desk.pick('dock',0);await flush();
  assert.equal(desk.log.renders.at(-1).options.headings_only,false);
  const framed=page({narrow:false,embedded:true});await flush();framed.pick('dock',0);await flush();
  assert.equal(framed.log.renders.at(-1).options.headings_only,true,'inside /m the page is a phone page');
});

test('the spec box sends Parcel or Estate as chosen, never guessed; odd specs pass through as typed',async()=>{
  const p=page();await flush();const seg=p.el('g-spec-box').querySelector('.g-seg'),as=kind=>seg.fire('click',{target:{closest:()=>({dataset:{kind}})}});
  p.el('g-spec').value='{"symbols":["glimpseStart"],"root":"25a49a99"}';p.el('g-spec-render').click();await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1)),'{"kind":"parcel","value":{"symbols":["glimpseStart"],"root":"25a49a99"},"options":{"headings_only":false,"include_rows":true}}');
  p.el('g-spec').value='opc · pane slice group';p.el('g-spec-render').click();await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1)),'{"kind":"parcel","value":"opc · pane slice group","options":{"headings_only":true,"include_rows":true}}');
  as('estate');p.el('g-spec').value='[ap:ESTATE1]';p.el('g-spec-render').click();await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1)),'{"kind":"estate","value":"[ap:ESTATE1]","options":{}}','an estate keeps each parcel\'s own read, even on a phone');
  assert.equal(p.el('g-headings').checked,false);
  as('parcel');p.el('g-spec').value='{"node": oops';p.el('g-spec-render').click();await flush();
  assert.equal(p.log.renders.at(-1).value,'{"node": oops');
});

test('options: sort with direction only for a date sort, recent; Full clears headings-only and recent',async()=>{
  const p=page();await flush();p.pick('dock',0);await flush();
  p.el('g-sort').value='created';p.el('g-sort').fire('change');await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1).options),'{"headings_only":true,"sort_by":"created","sort_direction":"desc","include_rows":true}');
  assert.equal(p.el('g-direction').hidden,false);
  p.el('g-recent').value='1w';p.el('g-recent').fire('change');await flush();
  assert.equal(p.log.renders.at(-1).options.recent,'1w');
  assert.equal(p.el('g-compact').hidden,false);
  assert.equal(p.el('g-compact-text').textContent,'Compact view: headings only · recent 1w, not the whole document.');
  p.el('g-full').click();await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1).options),'{"headings_only":false,"sort_by":"created","sort_direction":"desc","include_rows":true}');
  assert.equal(p.el('g-compact').hidden,true);
  assert.match(p.el('g-asof').textContent,/ · full · by created, newest first · /);
});

test('Refresh re-sends the current request exactly; nothing on the page refreshes the daemon\'s cache',async()=>{
  const p=page();await flush();p.pick('dock',0);await flush();
  const before=JSON.stringify(p.log.renders.at(-1));
  p.el('g-refresh').click();await flush();p.el('g-full').click();await flush();p.el('g-source').click();p.el('g-rendered').click();
  assert.equal(JSON.stringify(p.log.renders[1]),before);
  assert.equal(p.log.fetches.filter(([u])=>u==='/api/glimpse/cache-refresh').length,0);
  assert.equal(p.api.refreshCache,undefined,'the Refresh cache button is gone');
  assert.doesNotMatch(pageHtml,/g-cache|Refresh cache|g-catalog-status|<header/);
});

test('the Controls bar: open by itself while nothing is shown, folded by a pick, remembered per device, never forced open from outside',async()=>{
  const store=()=>{const m=new Map(),sets=[];return {sets,getItem:k=>m.has(k)?m.get(k):null,setItem:(k,v)=>{sets.push([k,v]);m.set(k,String(v));},removeItem:k=>m.delete(k)};};
  const s1=store(),p=page({storage:s1});await flush();
  const shown=x=>!x.el('g-ctl-body').hidden&&x.el('g-ctl-toggle').getAttribute('aria-expanded')==='true';
  assert.ok(shown(p),'empty View: open');assert.equal(s1.sets.length,0,'an automatic open is not remembered');
  p.pick('dock',0);await flush();
  assert.ok(!shown(p),'a pick folds it and the document takes the room');
  p.el('g-ctl-toggle').click();
  assert.ok(shown(p));assert.deepEqual(s1.sets.at(-1),['pane_view_controls_open_v1:page','1']);
  p.el('g-find').value='doc';p.el('g-find').fire('input');p.el('g-refresh').click();await flush();
  assert.ok(shown(p),'the toolbar\'s own work (find, Refresh, options) keeps it open');
  p.api.openHandle('[ap:OUT1]','node',{from:'outside'});await flush();
  assert.ok(shown(p),'opened by hand: a root from outside leaves it open');
  p.el('g-handle').value='3f3754e2';p.el('g-handle-form').fire('submit');await flush();
  assert.ok(!shown(p),'a pasted handle folds it too');assert.deepEqual(s1.sets.at(-1),['pane_view_controls_open_v1:page','0']);
  // a new page on this device: closed, and a root from outside does not force it open
  const q=page({storage:s1});q.api.openHandle('[ap:OUT2]','node',{from:'outside'});await flush();
  assert.ok(!shown(q));
  // remembered open: stays open after a root from outside
  s1.setItem('pane_view_controls_open_v1:page','1');
  const r=page({storage:s1});r.api.openHandle('[ap:OUT3]','node',{from:'outside'});await flush();
  assert.ok(shown(r));
  // empty View, a tap closes it and it stays closed; a first render that fails with nothing held opens it again (tap or not)
  const s2=store(),e=page({storage:s2});await flush();
  e.el('g-ctl-toggle').click();assert.ok(!shown(e));e.api.showStatus?.();assert.ok(!shown(e));
  const f=page({storage:store()});f.setReply(()=>({status:502,body:{schemaVersion:1,status:'daemon',error:'no tab'}}));
  f.api.openHandle('[ap:BAD]','node',{from:'outside'});assert.ok(!shown(f),'in flight: folded');await flush();
  assert.ok(shown(f),'failed with nothing to show: open, so the chooser is there');
  // the desktop keys its own
  const s3=store(),dsk=page({storage:s3,embedded:true,narrow:false});await flush();
  dsk.el('g-ctl-toggle').click();dsk.el('g-ctl-toggle').click();
  assert.ok(s3.sets.every(([k])=>k==='pane_view_controls_open_v1:framed'),'keyed by the page around');
});

test('the newest choice wins: an older reply arriving later is dropped',async()=>{
  const p=page();await flush();
  p.setReply(body=>body.value.dock?'defer':{status:200,body:{schemaVersion:1,markdown:'# newest\n',bytes:10,renderedAt:'2026-10-22T07:43:00Z',request:body}});
  p.pick('dock',0);await flush();
  p.el('g-handle').value='[ap:NEWEST]';p.el('g-handle-form').fire('submit');await flush();
  assert.equal(p.api.state.held.markdown,'# newest\n');
  p.pending[0]({status:200,body:{schemaVersion:1,markdown:'# old dock\n',bytes:11,renderedAt:'2026-10-22T07:42:00Z',request:p.log.renders[0]}});await flush();
  assert.equal(p.api.state.held.markdown,'# newest\n');
  assert.equal(p.api.state.held.label,'Node · [ap:NEWEST]');
  assert.match(p.el('g-asof').textContent,/^Node · \[ap:NEWEST\] · Rendered at /);
});

test('as-of is the render time with the applied options; a failure keeps the last document, labelled last successful',async()=>{
  const p=page();await flush();p.el('g-handle').value='3f3754e2';p.el('g-handle-form').fire('submit');await flush();
  assert.match(p.el('g-asof').textContent,/^Node · 3f3754e2 · Rendered at .+ · daemon cache snapshot · full · 42 bytes$/);
  assert.equal(p.el('g-asof').dataset.last,'false');
  const held=p.api.state.held;
  p.setReply(()=>({status:502,body:{schemaVersion:1,status:'daemon',error:'Open a Workflowy tab with the NEXUS extension on the PC; groups/Docks/symbols need its settings. Raw node/subtree works without it. no tab'}}));
  p.pick('dock',0);await flush();
  assert.equal(p.api.state.held,held);
  assert.match(p.el('g-asof').textContent,/^Last successful: Node · 3f3754e2 · Rendered at /);
  assert.equal(p.el('g-asof').dataset.last,'true');
  assert.equal(p.el('g-error').hidden,false);
  assert.match(p.el('g-error').textContent,/^⚠ Dock · ⭐ Pane Views Dock · Open a Workflowy tab/);
  p.el('g-copy').click();await flush();
  assert.equal(p.log.clipboard.at(-1),held.markdown);
});

test('painting is the shared GlimpseMd output, assigned as returned; never the page\'s marked; Copy MD is the whole held Markdown',async()=>{
  const p=page();await flush();
  const big='# Big\n\n'+'A paragraph of the document.\n\n'.repeat(5000);
  p.setReply(body=>({status:200,body:{schemaVersion:1,markdown:big,bytes:big.length,renderedAt:'2026-10-22T07:44:00Z',request:body}}));
  p.el('g-handle').value='[ap:BIG]';p.el('g-handle-form').fire('submit');await flush();
  const call=p.log.toHtml.at(-1);
  assert.equal(call.md,big);
  assert.equal(call.opts.view,'rendered');assert.equal(call.opts.contentFormat,'markdown');
  assert.equal(p.api.state.painted,'<section data-shared>'+big+'</section>');
  assert.equal(p.log.marked,0,'no second Markdown renderer');
  p.el('g-copy').click();await flush();
  assert.equal(p.log.clipboard.at(-1),big);
  assert.notEqual(p.log.clipboard.at(-1),p.el('g-doc').textContent);
});

test('Source and find use the shared compiler and the shared count/current; Rendered find switches to Source; no request',async()=>{
  const p=page();await flush();p.el('g-handle').value='[ap:X]';p.el('g-handle-form').fire('submit');await flush();
  const renders=p.log.renders.length;
  p.el('g-find').value='doc';p.el('g-find').fire('input');
  assert.deepEqual(p.log.compile.at(-1),'doc');
  assert.equal(p.api.state.view,'source');
  assert.equal(p.log.toHtml.at(-1).opts.view,'source');
  assert.equal(String(p.log.toHtml.at(-1).opts.findRegex),'/doc/gi');
  assert.equal(p.el('g-count').textContent,'1 / 3');
  p.el('g-find').value='"open';p.el('g-find').fire('input');
  assert.equal(p.el('g-count').textContent,'unbalanced quotes');
  assert.equal(p.el('g-count').dataset.error,'true');
  p.el('g-rendered').click();
  assert.equal(p.log.toHtml.at(-1).opts.view,'rendered');
  assert.equal(p.log.renders.length,renders);
});

test('a catalog failure is a line, not a blocker; a pasted node still renders',async()=>{
  const p=page({catalog:[502,{schemaVersion:1,status:'daemon',error:'daemon unreachable'}]});await flush();
  assert.equal(p.el('g-catalog-errors').textContent,'Docks and groups unavailable (a pasted handle still works) · daemon unreachable');
  assert.match(pageHtml,/<div id="g-status"[\s\S]*<div id="g-catalog-errors"><\/div>[\s\S]*<\/div>\s*<!-- The dock's row list/,'outside the Controls bar');
  p.el('g-handle').value='3f3754e2';p.el('g-handle-form').fire('submit');await flush();
  assert.equal(p.api.state.held.label,'Node · 3f3754e2');
  const partial=page({catalog:[200,{schemaVersion:1,docks:[{handle:'d',name:'D'}],groups:[],errors:[{source:'groups',error:'Open a Workflowy tab … no tab'}]}]});await flush();
  assert.equal(partial.el('g-catalog-errors').textContent,'Slice groups unavailable: Open a Workflowy tab … no tab');
  assert.equal(partial.rows('dock').length,1);
});

test('a missing shared renderer is named with the setting and the pane\'s diagnostic; nothing substitutes for it',async()=>{
  const p=page({shared:false,assetFailures:['/nexus-md/glimpse-md-render.js']});await flush();await flush();
  assert.equal(p.el('g-assets').hidden,false);
  assert.match(p.el('g-assets').textContent,/did not all load: \/nexus-md\/glimpse-md-render\.js\./);
  assert.match(p.el('g-assets').textContent,/pane-glimpse\.json/);
  assert.match(p.el('g-assets').textContent,/Missing NEXUS asset glimpse-md-render\.js in checkout \/x/);
  p.el('g-handle').value='3f3754e2';p.el('g-handle-form').fire('submit');await flush();
  assert.match(p.el('g-doc').textContent,/^Cannot paint: the shared renderer did not load/);
  assert.equal(p.log.marked,0);
  assert.ok(p.api.state.held.markdown.startsWith('# Doc'));
});

test('glimpse.html: only the extension\'s shared renderer, no frame/sanitizer; glare follower only when embedded',()=>{
  assert.doesNotMatch(pageHtml,/vendor\/marked|purify|message-markdown|<iframe|sandbox|Content-Security-Policy/i);
  const head=pageHtml.match(/<script>([\s\S]*?)<\/script>/)[1];
  assert.ok(pageHtml.indexOf(head)<pageHtml.indexOf('/glare.js'),'the follower mark is set before glare.js runs');
  for(const [embedded,expected] of [[false,false],[true,true]]){
    const attrs={},doc={documentElement:{setAttribute:(k)=>{attrs[k]=true;}}},ctx={document:doc};ctx.window=ctx;ctx.window.parent=embedded?{}:ctx;
    vm.runInNewContext(head,ctx);assert.equal(!!attrs['data-glare-follow'],expected);
  }
});

test('/m View is the whole page in one lazy, unsandboxed frame; the desktop has a View pane and a sheet, never a new tab',()=>{
  const m=fs.readFileSync(path.join(root,'m.html'),'utf8'),frame=m.match(/<iframe id="m-glimpse-frame"[^>]*>/)?.[0];
  assert.ok(frame,'the /m View frame exists');
  assert.doesNotMatch(frame,/sandbox|src=|srcdoc/,'unsandboxed, and loaded only on the first tap');
  assert.match(m,/<button id="m-view-glimpse" type="button" aria-pressed="false"[^>]*>View<\/button>/);
  const js=fs.readFileSync(path.join(root,'m.js'),'utf8');
  assert.match(js,/if \(!frame\.getAttribute\('src'\)\) frame\.src='\/glimpse\.html';/);
  assert.match(js,/state\.view === 'programs' \|\| state\.view === 'glimpse'\) \{ await pollReceipts\(\); pollControls\(\); return; \}/);
  // The desktop: the header's 📄 View is a button (no link, no new tab); OpenCode mode's sheet and the conversation
  // column's View pane each hold one lazy, unsandboxed frame whose id keys View's history as the desktop's.
  const index=fs.readFileSync(path.join(root,'index.html'),'utf8'),link=index.match(/<[a-z]+ id="glimpse-link"[^>]*>/)?.[0];
  assert.match(link,/^<button id="glimpse-link" type="button"/);assert.doesNotMatch(index,/target="_blank"|href="\/glimpse\.html"/);
  for(const [name,html] of [['index.html',index],['sessions.html',fs.readFileSync(path.join(root,'sessions.html'),'utf8')]]){
    const frame=html.match(/<iframe id="desktop-view-frame"[^>]*>/)?.[0];
    assert.ok(frame,name+' has the desktop View frame');assert.doesNotMatch(frame,/sandbox|src=|srcdoc/,name+': unsandboxed, loaded on first use');
  }
  assert.match(fs.readFileSync(path.join(root,'sessions.html'),'utf8'),/<button id="pane-conversation"[^>]*>💬 Conversation<\/button>\s*<button id="pane-view"[^>]*>📄 View<\/button>/);
  const app=fs.readFileSync(path.join(root,'app.js'),'utf8');
  assert.doesNotMatch(app,/window\.open|\.open\('\/glimpse/,'the editor page never opens a tab');
  assert.match(app,/globalThis\.opcOpenInView = \(handle, context\) => \{[^}]*inspectorTurn\(\)[\s\S]{0,200}?desktopOpenInView\(handle,/);
});

// ---- the row list (UnitSpec S §§5.3, 6.3, 6.4, 7): the daemon's rows, the shared painter, the tap, the bound, the races
const ok=(request,env,extra={})=>({status:200,body:{schemaVersion:1,markdown:env.markdown,bytes:Buffer.byteLength(env.markdown),renderedAt:extra.at||'2026-10-22T08:00:00Z',
  request,...('rows' in env?{rows:env.rows}:{}),...(env.render_spec?{render_spec:env.render_spec}:{})}});
const visible=p=>p.el('g-rows').children.filter(n=>!n.hidden);
const tapRow=(p,i)=>p.el('g-rows').children.find(n=>n.rowIndex===i).click();
async function dockWithRows(name='anchor_dock',opts={}){
  const p=page(opts);await flush();
  p.setReply(body=>ok(body,envelope(body.value.focus?name+'_tap':name)));
  p.pick('dock',0);await flush();
  return p;
}

test('rows arrive with the document and are painted only by GlimpseMd.renderRowList, with the held spec\'s sort',async()=>{
  const p=await dockWithRows('raw_root_modified_desc');
  const env=envelope('raw_root_modified_desc'),call=p.log.rowLists.at(-1);
  assert.equal(p.log.rowLists.length,1);
  assert.equal(call.container,p.el('g-rows'));
  assert.deepEqual(call.rows.map(r=>({...r,_rowEl:undefined})),env.rows.map(r=>({...r,_rowEl:undefined})));
  assert.equal(call.rows,p.api.state.held.rows,'the held canonical array itself, unchanged and unsliced');
  assert.deepEqual({sortBy:call.opts.sortBy,sortDir:call.opts.sortDir,selected:call.opts.selected,primary:call.opts.primary},{sortBy:'modified',sortDir:'desc',selected:[],primary:-1});
  assert.equal(p.el('g-rows-box').hidden,false);
  assert.equal(p.el('g-rows-count').textContent,'7 rows');
  assert.deepEqual(p.api.state.held.spec,env.render_spec);
  assert.equal(p.api.state.held.markdown,env.markdown);
  assert.equal(p.log.toHtml.at(-1).md,env.markdown);
});

test('no rows (an old daemon) is no list and no warning; [] is an empty list; an old asset without the export is no list',async()=>{
  const p=page();await flush();
  p.setReply(body=>ok(body,{markdown:'# old daemon\n'}));p.pick('dock',0);await flush();
  assert.equal(p.el('g-rows-box').hidden,true);assert.equal(p.log.rowLists.length,0);
  assert.equal(p.el('g-error').hidden,true);assert.equal(p.el('g-note').textContent,'');
  assert.equal(p.api.state.held.rows,null);
  p.setReply(body=>ok(body,{markdown:'(empty)',rows:[],render_spec:envelope('anchor_dock').render_spec}));p.el('g-refresh').click();await flush();
  assert.equal(p.el('g-rows-box').hidden,false);assert.deepEqual(p.log.rowLists.at(-1).rows,[]);assert.equal(p.el('g-rows-count').textContent,'0 rows');
  const old=page({rowPainter:false});await flush();
  old.setReply(body=>ok(body,envelope('anchor_dock')));old.pick('dock',0);await flush();
  assert.equal(old.el('g-rows-box').hidden,true);
  assert.equal(old.log.toHtml.at(-1).md,envelope('anchor_dock').markdown,'the document renders as before');
});

test('a tap sends a deep copy of the held spec with only focus replaced, through glimpse.run, include_rows only',async()=>{
  const p=await dockWithRows();
  const held=p.api.state.held,row=held.rows[3];
  tapRow(p,3);await flush();
  const sent=p.log.renders.at(-1);
  assert.deepEqual(sent,{kind:'parcel',value:{...held.spec,focus:{mode:'row',uuid:row.uuid,ancestor_path:row.ancestor_path,occurrence:row.occurrence}},options:{include_rows:true}});
  assert.equal(held.spec.focus,null,'the held spec is never mutated');
  assert.notEqual(sent.value.focus.ancestor_path,row.ancestor_path);
  assert.equal(Object.keys(sent.value).length,19);
  // the reply (the focused document with the whole list) is held; the tapped row is selected by identity
  const tap=envelope('anchor_dock_tap');
  assert.equal(p.api.state.held.markdown,tap.markdown);
  assert.deepEqual(p.log.rowLists.at(-1).opts.selected,[3]);assert.equal(p.log.rowLists.at(-1).opts.primary,3);
  assert.match(p.api.state.held.label,/^Dock · ⭐ Pane Views Dock › Child Dock #wfy-ns$/);
  assert.equal(p.log.fetches.filter(([u])=>u==='/api/glimpse/render').length,2,'one fetch per tap, no row fetch of its own');
  // Refresh repeats the focused request exactly; options and Full go back to the chosen document (its whole list)
  p.el('g-refresh').click();await flush();
  assert.deepEqual(p.log.renders.at(-1),sent);
  p.el('g-sort').value='created';p.el('g-sort').fire('change');await flush();
  assert.deepEqual(p.log.renders.at(-1),{kind:'parcel',value:{dock:'[ap:DOCK01]'},options:{headings_only:true,sort_by:'created',sort_direction:'desc',include_rows:true}});
});

test('W1 phone default: a compact echoed spec opens the note body, labels it full and Refresh repeats it',async()=>{
  for(const opts of [{}, {narrow:false,embedded:true}]){
    const p=page(opts);await flush();
    const compact=envelope('raw_root_created_asc');compact.render_spec.headings_only=true;
    p.setReply(body=>ok(body,body.value.focus?envelope('raw_root_created_asc_tap'):compact));
    p.pick('dock',0);await flush();
    const held=p.api.state.held,before=JSON.stringify(held.spec),row=held.rows[5];
    assert.equal(p.log.renders.at(-1).options.headings_only,true);
    assert.equal(p.el('g-compact').hidden,false);
    tapRow(p,5);await flush();
    const sent=p.log.renders.at(-1);
    assert.equal(sent.value.headings_only,false);
    assert.deepEqual(sent,{kind:'parcel',value:{...JSON.parse(before),headings_only:false,
      focus:{mode:'row',uuid:row.uuid,ancestor_path:row.ancestor_path,occurrence:row.occurrence}},options:{include_rows:true}});
    assert.equal(JSON.stringify(held.spec),before,'the held compact spec is not mutated');
    assert.notEqual(sent.value.focus.ancestor_path,row.ancestor_path);
    assert.match(p.el('g-asof').textContent,/ · full · /);
    assert.doesNotMatch(p.el('g-asof').textContent,/headings only/);
    assert.equal(p.el('g-compact').hidden,true);
    assert.equal(p.el('g-headings').checked,true,'checkbox remains the base document option');
    assert.equal(p.log.renders.length,2,'one fetch per tap');
    p.el('g-refresh').click();await flush();assert.deepEqual(p.log.renders.at(-1),sent);
    p.el('g-full').click();await flush();
    assert.deepEqual(p.log.renders.at(-1),{kind:'parcel',value:{dock:'[ap:DOCK01]'},options:{headings_only:false,include_rows:true}},'Full still returns to the base');
  }
});

test('W1 explicit headings-only and wide pages keep the echoed spec setting on a tap',async()=>{
  for(const narrow of [true,false]){
    const p=page({narrow});await flush();
    const compact=envelope('raw_root_created_asc');compact.render_spec.headings_only=true;
    p.setReply(body=>ok(body,compact));p.pick('dock',0);await flush();
    if(narrow){p.el('g-headings').checked=true;p.el('g-headings').fire('change');await flush();assert.equal(p.api.state.headingsChoice,true);}
    const held=p.api.state.held,row=held.rows[5];tapRow(p,5);await flush();
    assert.deepEqual(p.log.renders.at(-1).value,{...held.spec,focus:{mode:'row',uuid:row.uuid,ancestor_path:row.ancestor_path,occurrence:row.occurrence}});
    assert.equal(p.log.renders.at(-1).value.headings_only,true);
    assert.match(p.el('g-asof').textContent,/ · headings only · /);
    assert.equal(p.el('g-compact').hidden,false);
  }
});

test('repeated uuids: the tapped occurrence reaches the daemon unchanged, and selection follows identity in the new order',async()=>{
  const p=page();await flush();
  const asc=envelope('raw_root_created_asc'),desc=envelope('raw_root_modified_desc');
  p.setReply(body=>ok(body,body.value.focus?{...desc,markdown:'# focused\n'}:asc));
  p.pick('dock',0);await flush();
  const twins=asc.rows.map((r,i)=>[r,i]).filter(([r])=>r.uuid===asc.rows[1].uuid);
  assert.deepEqual(twins.map(([r,i])=>[i,r.occurrence]),[[1,0],[5,1]],'the fixture repeats Architecture as occurrences 0 and 1');
  tapRow(p,5);await flush();
  assert.deepEqual(p.log.renders.at(-1).value.focus,{mode:'row',uuid:asc.rows[5].uuid,ancestor_path:asc.rows[5].ancestor_path,occurrence:1});
  const expected=desc.rows.findIndex(r=>r.uuid===asc.rows[5].uuid&&r.occurrence===1);
  assert.equal(expected,5);
  assert.deepEqual(p.log.rowLists.at(-1).opts.selected,[expected]);
  // the other occurrence selects the other row
  tapRow(p,1);await flush();
  assert.deepEqual(p.log.rowLists.at(-1).opts.selected,[desc.rows.findIndex(r=>r.uuid===asc.rows[1].uuid&&r.occurrence===0)]);
});

test('§7 taps A then B: B wins every held component whichever reply lands first',async()=>{
  for(const order of [['A','B'],['B','A']]){
    const p=await dockWithRows('raw_root_created_asc');
    const base=envelope('raw_root_created_asc');
    p.setReply(()=> 'defer');
    tapRow(p,2);await flush();tapRow(p,4);await flush();
    const [reqA,reqB]=p.log.renders.slice(-2),[resA,resB]=p.pending.splice(-2);
    const reply={A:()=>resA(ok(reqA,{...base,markdown:'# A\n',rows:base.rows.slice(0,5)},{at:'2026-10-22T08:01:00Z'})),
      B:()=>resB(ok(reqB,{...base,markdown:'# B\n'},{at:'2026-10-22T08:02:00Z'}))};
    for(const k of order){reply[k]();await flush();}
    const held=p.api.state.held;
    assert.equal(held.markdown,'# B\n',order.join('→'));
    assert.equal(held.rows.length,7);assert.equal(held.renderedAt,'2026-10-22T08:02:00Z');
    assert.equal(held.selection.focus.uuid,base.rows[4].uuid);
    assert.equal(p.log.rowLists.at(-1).rows.length,7);assert.deepEqual(p.log.rowLists.at(-1).opts.selected,[4]);
    assert.match(p.el('g-asof').textContent,/› Deploy step · Rendered at /);
  }
});

test('§7 an old-daemon document-only reply overlapping a newer render never repaints the newer choice',async()=>{
  const p=page();await flush();
  p.setReply(body=>body.value.dock?'defer':ok(body,envelope('slice_group')));
  p.pick('dock',0);await flush();p.pick('group',0);await flush();
  assert.equal(p.api.state.held.rows.length,4);
  p.pending[0](ok(p.log.renders[0],{markdown:'# old daemon dock\n'}));await flush();
  assert.equal(p.api.state.held.label,'Group · opc pane');assert.equal(p.el('g-rows-box').hidden,false);
  assert.equal(p.log.rowLists.length,1);assert.equal(p.api.state.held.markdown,envelope('slice_group').markdown);
});

test('§7 a failed tap keeps the last document and list as one labelled snapshot; the stale-focus error stays visible',async()=>{
  const p=await dockWithRows();
  const held=p.api.state.held,lists=p.log.rowLists.length;
  const stale=ENVELOPES.stale_focus.structuredContent.error.extra.legacy_result.error;
  p.setReply(()=>({status:502,body:{schemaVersion:1,status:'daemon',error:stale}}));
  tapRow(p,2);await flush();
  assert.equal(p.api.state.held,held);assert.equal(p.log.rowLists.length,lists,'no repaint: no row claims the focus that failed');
  assert.match(p.el('g-asof').textContent,/^Last successful: Dock · ⭐ Pane Views Dock · Rendered at /);
  assert.equal(p.el('g-error').textContent,'⚠ Dock · ⭐ Pane Views Dock › Dock Beta · '+stale+'\nStill showing the last successful: Dock · ⭐ Pane Views Dock','outside the folded controls, the error says what is still shown');
  assert.equal(p.el('g-rows').dataset.busy,'false');
  assert.equal(p.log.renders.length,2,'no retry of a failed focus');
});

test('§6.3 bound: every row painted; rows after the 200th hidden with an exact label; Show all is display only, reversible',async()=>{
  const p=page();await flush();
  const spec={...envelope('raw_root_created_asc').render_spec};
  const rows=Array.from({length:450},(_,i)=>({uuid:'u'+i,ancestor_path:[],occurrence:0,depth:i%3,name:'Row '+i,created:1700000000+i*3600}));
  p.setReply(body=>ok(body,{markdown:'# big\n',rows,render_spec:spec}));
  p.pick('group',0);await flush();
  const renders=p.log.renders.length,list=p.el('g-rows');
  assert.equal(p.log.rowLists.at(-1).rows.length,450,'the painter gets the full array');
  assert.equal(visible(p).filter(n=>'rowIndex' in n).length,200);
  assert.equal(visible(p).filter(n=>'rowIndex' in n).at(-1).rowIndex,199);
  assert.ok(list.children.filter(n=>n.daysep&&n.hidden).length>0,'day lines past the bound are hidden too');
  assert.equal(p.el('g-rows-more').hidden,false);
  assert.equal(p.el('g-rows-more-text').textContent,'Showing 200 of 450 rows');
  p.el('g-rows-all').click();
  assert.equal(visible(p).length,list.children.length);
  assert.equal(p.el('g-rows-more-text').textContent,'Showing all 450 rows');assert.equal(p.el('g-rows-all').textContent,'Show first 200');
  p.el('g-rows-all').click();
  assert.equal(visible(p).filter(n=>'rowIndex' in n).length,200);
  assert.equal(p.log.renders.length,renders,'Show all never fetches');
  const small=page();await flush();small.setReply(body=>ok(body,envelope('anchor_dock')));small.pick('dock',0);await flush();
  assert.equal(small.el('g-rows-more').hidden,true);
});

test('§7 Show all, fold or a Source/find repaint during or after a new reply appends no stale list and makes no fetch',async()=>{
  const p=page();await flush();
  const many=Array.from({length:260},(_,i)=>({uuid:'m'+i,ancestor_path:[],occurrence:0,depth:0,name:'M'+i}));
  p.setReply(body=>ok(body,{markdown:'# many\n',rows:many,render_spec:envelope('anchor_dock').render_spec}));p.pick('dock',0);await flush();
  p.setReply(()=> 'defer');p.pick('group',0);await flush();
  p.el('g-rows-all').click();p.el('g-rows-box').open=false;p.el('g-source').click();p.el('g-find').value='M1';p.el('g-find').fire('input');p.el('g-rendered').click();
  assert.equal(p.log.rowLists.length,1,'view, find, fold and Show all never repaint the list');
  assert.equal(p.log.renders.length,2);
  p.pending[0](ok(p.log.renders[1],envelope('slice_group')));await flush();
  assert.equal(p.log.rowLists.length,2);assert.equal(p.el('g-rows').children.length,4,'only the new list');
  assert.equal(p.el('g-rows-more').hidden,true);
  assert.equal(p.log.fetches.filter(([u])=>u==='/api/glimpse/render').length,2);
});

test('§7 old → new daemon between renders: the next render gains rows with no setting; estates never ask for rows',async()=>{
  const p=page();await flush();
  p.setReply(body=>ok(body,{markdown:'# old\n'}));p.pick('dock',0);await flush();
  assert.equal(p.el('g-rows-box').hidden,true);
  p.setReply(body=>ok(body,envelope('anchor_dock')));p.el('g-refresh').click();await flush();
  assert.equal(p.el('g-rows-box').hidden,false);
  assert.ok(p.log.renders.every(r=>r.options.include_rows===true));
  const seg=p.el('g-spec-box').querySelector('.g-seg');seg.fire('click',{target:{closest:()=>({dataset:{kind:'estate'}})}});
  p.el('g-spec').value='[ap:ESTATE1]';p.el('g-spec-render').click();await flush();
  assert.equal('include_rows' in p.log.renders.at(-1).options,false);
});

test('a list without a render specification paints, but a tap says why it cannot open and fetches nothing',async()=>{
  const p=page();await flush();
  p.setReply(body=>ok(body,{markdown:'# x\n',rows:envelope('anchor_dock').rows}));p.pick('dock',0);await flush();
  assert.equal(p.el('g-rows-box').hidden,false);
  tapRow(p,1);await flush();
  assert.equal(p.log.renders.length,1);
  assert.match(p.el('g-note').textContent,/without a render specification/);
});

test('glimpse.js has no row builder, timestamp formatter or day-line logic of its own; the list host carries the shared scope class',()=>{
  assert.match(pageHtml,/<div id="g-rows" class="glimpse-md-row-list" tabindex="-1"/);
  assert.ok(pageHtml.indexOf('id="g-rows-box"')<pageHtml.indexOf('id="g-scroll"'),'the list sits above the document');
  assert.doesNotMatch(source,/createElement\(['"]div['"]\)[^\n]*dock-row|glimpse-md-dock-(row|daysep|marker|indent|label)|toLocale(Date|Time)?String\(undefined|getTimezoneOffset|86400000/);
  assert.equal((source.match(/renderRowList\(/g)||[]).length,0,'called through the held export, never redefined');
  assert.match(source,/painter\(list, rows, \{/);
});

test('a clicked handle (handle-links.js): openHandle and ?handle= render it as Node by default, Subtree/Dock when asked; the page registers itself as the opener',async()=>{
  const p=page({narrow:false});await flush();
  await p.api.openHandle('[ap:X8TYWJ]');
  assert.deepEqual(p.log.renders.at(-1),{kind:'parcel',value:{node:'[ap:X8TYWJ]'},options:{headings_only:false,include_rows:true}});
  assert.equal(p.el('g-handle').value,'[ap:X8TYWJ]');assert.equal(p.api.state.how,'node');
  await p.api.openHandle('9e210387','dock');assert.deepEqual(p.log.renders.at(-1).value,{dock:'9e210387'});
  await p.api.openHandle('  ');assert.equal(p.log.renders.length,2);
  await p.win.opcOpenInView('20261022');await flush();assert.deepEqual(p.log.renders.at(-1).value,{node:'20261022'});
  const q=page({narrow:false,search:'?handle=%5Bap%3AK7M2QF%5D'});await flush();await flush();
  assert.deepEqual(q.log.renders.map(r=>r.value),[{node:'[ap:K7M2QF]'}]);
  const s=page({narrow:false,search:'?handle=25a49a99&how=subtree'});await flush();await flush();
  assert.deepEqual(s.log.renders.map(r=>r.value),[{subtree:'25a49a99'}]);
  const none=page({narrow:false,search:'?x=1'});await flush();assert.equal(none.log.renders.length,0);
});

// ---- the two pickers (view-pickers.js), as the Chrome extension shows them; then the polish batch [ap:830HMA]
const PICKER_CATALOG={schemaVersion:1,errors:[],
  docks:[{handle:'d0',name:'🛰️ Project Maps',depth:0},{handle:'d1',name:'[ap:ECOT1S] 🗺️ NEXUS map',depth:1},{handle:'d2',name:'🗺️ PANE map',depth:2},
    {handle:'d3',name:'WORK',depth:0},{handle:'d4',name:'from a server without depths'}],
  groups:[{handle:'g-rec',name:'Work|Recruiting|RECRUITING[0]'},{handle:'x-work',name:'[-5]Work'},{handle:'x-rec',name:'[50]Work|Recruiting'},
    {handle:'g-core',name:'Nexus|CORE[0]'},{handle:'x-nexus',name:'[1]Nexus'},{handle:'g-last',name:'Nexus | LAST [50]'},{handle:'g-loose',name:'Loose'},
    {handle:'g-legal',name:'Misc|LEGAL[0]'},{handle:'x-wmisc',name:'[100]Work|Misc'},{handle:'x-dup',name:'[2]Nexus'}]};
const label=b=>b.textContent;
test('Favorite Docks: one row per Dock in the daemon\'s order, indented by its depth, the AP-ID apart; a tap renders it and marks it',async()=>{
  const p=page({catalog:[200,PICKER_CATALOG]});await flush();
  const rows=p.rows('dock');
  assert.deepEqual(rows.map(label),['🛰️ Project Maps','🗺️ NEXUS map[ap:ECOT1S]','🗺️ PANE map','WORK','from a server without depths']);
  assert.deepEqual(rows.map(b=>b.style.paddingLeft),['8px','24px','40px','8px','8px']);
  assert.deepEqual(rows[1].children.map(c=>c.className),['g-pname','g-pap'],'the name first, so names line up; the AP-ID trails');
  assert.ok(rows.every(b=>b.type==='button'&&b.className.includes('g-pleaf')&&b.title===(label(b).endsWith('[ap:ECOT1S]')?'[ap:ECOT1S] '+label(b).replace('[ap:ECOT1S]',''):label(b))));
  rows[2].click();await flush();
  assert.deepEqual(p.log.renders.at(-1),{kind:'parcel',value:{dock:'d2'},options:{headings_only:true,include_rows:true}});
  assert.equal(p.api.state.held.label,'Dock · 🗺️ PANE map');
  assert.deepEqual(p.rows('dock').map(b=>b.getAttribute('aria-current')),[null,null,'true',null,null]);
  assert.deepEqual(p.rows('group').map(b=>b.getAttribute('aria-current')).filter(Boolean),[]);
});

test('slice groups: the extension\'s pipe hierarchy — [n] order, dummy expanders, a synthetic category, a duplicate; collapsed until opened',async()=>{
  const p=page({catalog:[200,PICKER_CATALOG]});await flush();
  assert.equal(p.el('g-group-count').textContent,'5','expanders are sections, not groups');
  const shown=()=>p.rows('group').map(b=>'  '.repeat(Number(b.dataset.depth))+label(b));
  assert.deepEqual(shown(),['▸Work','▸Nexus','·Nexus (duplicate expander — empty; delete or renumber)','Loose','▸Misc']);
  const dup=p.rows('group')[2];assert.equal(dup.disabled,true);assert.ok(dup.className.includes('g-pdup'));
  assert.ok(p.rows('group')[4].className.includes('g-psynth'),'Misc has no expander row of its own');
  assert.equal(p.rows('group')[0].getAttribute('aria-expanded'),'false');
  p.rows('group')[0].click();
  assert.deepEqual(shown(),['▾Work','  ▸Recruiting','  ▸Misc','▸Nexus','·Nexus (duplicate expander — empty; delete or renumber)','Loose','▸Misc']);
  p.rows('group')[1].click();p.rows('group').find(b=>label(b)==='▸Nexus').click();
  assert.deepEqual(shown(),['▾Work','  ▾Recruiting','    RECRUITING','  ▸Misc','▾Nexus','  CORE','  LAST','·Nexus (duplicate expander — empty; delete or renumber)','Loose','▸Misc']);
  assert.equal(p.log.renders.length,0,'opening a section renders nothing');
  p.rows('group').find(b=>label(b)==='    RECRUITING'.trim()).click();await flush();
  assert.deepEqual(p.log.renders.at(-1),{kind:'parcel',value:{group:'g-rec'},options:{headings_only:true,include_rows:true}});
  assert.equal(p.api.state.held.label,'Group · RECRUITING','the short name, as the extension\'s compact surfaces name a group');
  assert.equal(p.rows('group').find(b=>b.getAttribute('aria-current')==='true').title,'Work|Recruiting|RECRUITING[0]');
  p.rows('group')[0].click();
  assert.deepEqual(shown().slice(0,2),['▸Work','▾Nexus'],'a section closes again; the others stay as they were');
});

test('L5 order: Saved order sends no sort_by, Document order sends sort_by none; Full drops a pasted spec\'s since; raw_text paints as text',async()=>{
  const p=page();await flush();p.pick('dock',0);await flush();
  assert.equal('sort_by' in p.log.renders.at(-1).options,false,'Saved order: the scope\'s own');
  p.el('g-sort').value='none';p.el('g-sort').fire('change');await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1).options),'{"headings_only":true,"sort_by":"none","include_rows":true}');
  assert.equal(p.el('g-direction').hidden,true);
  assert.match(p.el('g-asof').textContent,/ · headings only · document order · /);
  p.el('g-sort').value='saved';p.el('g-sort').fire('change');await flush();
  const spec={kind:'raw_root',root_uuid:'abcd1234',since:'2026-10-01',content_format:'raw_text'};
  p.el('g-spec').value=JSON.stringify(spec);p.el('g-spec-render').click();await flush();
  assert.deepEqual(p.log.renders.at(-1).value,spec);
  assert.equal(p.log.toHtml.at(-1).opts.contentFormat,'raw_text','a pasted raw_text spec is painted as plain text');
  p.el('g-full').click();await flush();
  assert.deepEqual(p.log.renders.at(-1).value,{kind:'raw_root',root_uuid:'abcd1234',content_format:'raw_text'},'Full: the whole document, no since window');
  assert.equal(p.api.history().root().selection.value.since,'2026-10-01','the history keeps the spec as pasted');
  p.pick('dock',0);await flush();
  assert.equal(p.log.toHtml.at(-1).opts.contentFormat,'markdown');
});

test('L11 the daemon\'s status lines show as notices beside the document, never in its Markdown; a reply without them clears them',async()=>{
  const p=page();await flush();
  const lines=['⚠️⚠️⚠️ ARCHIVED NODE · from archive x.json','resolved: abcd1234 Doc (corrected from abcd12345)'];
  p.setReply(body=>({status:200,body:{schemaVersion:1,markdown:'# Doc\n',bytes:6,renderedAt:'2026-10-23T07:00:00Z',request:body,notices:[...lines,'',7]}}));
  p.pick('dock',0);await flush();
  assert.equal(p.el('g-notices').hidden,false);assert.equal(p.el('g-notices').textContent,lines.join('\n'));
  assert.equal(p.log.toHtml.at(-1).md,'# Doc\n');
  p.el('g-copy').click();await flush();assert.equal(p.log.clipboard.at(-1),'# Doc\n');
  p.setReply(body=>({status:200,body:{schemaVersion:1,markdown:'# Other\n',bytes:8,renderedAt:'2026-10-23T07:01:00Z',request:body}}));
  p.el('g-refresh').click();await flush();
  assert.equal(p.el('g-notices').hidden,true);
});

test('L12 a long fault scrolls inside a bounded #g-error; L8 the phone tabs fit; L1 a no-files diff hides its control row',()=>{
  const css=fs.readFileSync(path.join(root,'glimpse.css'),'utf8');
  assert.match(css,/#g-error \{[^}]*max-height: 20svh;[^}]*overflow-y: auto;/);
  assert.match(fs.readFileSync(path.join(root,'m.css'),'utf8'),/@media \(max-width: 440px\) \{ #m-views button \{ flex: 1 1 auto; min-width: 0; padding: 5px 4px; \} \}/);
  assert.match(fs.readFileSync(path.join(root,'diff-view.js'),'utf8'),/controls\.hidden = !value\?\.files\?\.length;/);
  assert.match(pageHtml,/<div id="g-dock" class="g-picker-list" aria-label="Favorite Docks" data-opc-no-handles><\/div>/);
  assert.ok(pageHtml.indexOf('/view-pickers.js')<pageHtml.indexOf('/glimpse.js'),'the pickers load before the page script');
});

// ---- the View pane's room and context (2026-10-24): dividers, include archives, full-screen pickers, where it lives
const FOOTER='📦 3 archived subtree(s) under this scope were left out; include_archives=true includes them (rules: system(verb=\'describe\', params={\'of\': \'archives.list\', \'detail\': \'full\'}))';
const memory=()=>{const m=new Map(),sets=[];return {m,sets,getItem:k=>m.has(k)?m.get(k):null,setItem:(k,v)=>{sets.push([k,v]);m.set(k,String(v));},removeItem:k=>m.delete(k)};};

test('include archives: off sends nothing; the switch and the footer\'s one tap re-send the current request with only include_archives changed',async()=>{
  const p=page();await flush();
  p.setReply(body=>({status:200,body:{schemaVersion:1,markdown:'# D\n',bytes:4,renderedAt:'2026-10-24T07:42:10Z',request:body,notices:['resolved: x',FOOTER],
    ...(body.options.include_archives?{}:{archivesLeftOut:3})}}));
  p.pick('dock',0);await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1).options),'{"headings_only":true,"include_rows":true}','off by default: never a false');
  // the daemon's agent-facing footer becomes a plain line with one tap; its other status lines stay
  assert.equal(p.el('g-notices').textContent,'resolved: x');
  assert.equal(p.el('g-arch').hidden,false);
  assert.equal(p.el('g-arch-text').textContent,'📦 3 archived subtrees were left out');
  assert.equal(p.el('g-arch-toggle').textContent,'Include them');
  p.el('g-arch-toggle').click();await flush();
  assert.equal(JSON.stringify(p.log.renders.at(-1)),'{"kind":"parcel","value":{"dock":"[ap:DOCK01]"},"options":{"headings_only":true,"include_rows":true,"include_archives":true}}');
  assert.equal(p.el('g-archives').checked,true,'the Options switch follows');
  assert.equal(p.el('g-arch-text').textContent,'📦 Archives included (read-only, marked 📦)');
  assert.equal(p.el('g-arch-toggle').textContent,'Leave them out');
  assert.match(p.el('g-asof').textContent,/ · archives included · /);
  p.pick('group',0);await flush();
  assert.equal(p.log.renders.at(-1).options.include_archives,true,'a later choice keeps the switch');
  p.el('g-archives').checked=false;p.el('g-archives').fire('change');await flush();
  assert.equal('include_archives' in p.log.renders.at(-1).options,false);
  assert.equal(p.el('g-arch-text').textContent,'📦 3 archived subtrees were left out');
  // a plain Markdown reply (an estate) carries the footer in its text: found at its end
  const q=page();await flush();
  q.setReply(body=>({status:200,body:{schemaVersion:1,markdown:'# Estate\n\ntext\n\n'+FOOTER.replace('3 archived','1 archived')+'\n',bytes:9,renderedAt:'2026-10-24T07:42:10Z',request:body}}));
  q.pick('dock',0);await flush();
  assert.equal(q.el('g-arch-text').textContent,'📦 1 archived subtree was left out');
  // nothing left out, nothing included: no line
  const r=page();await flush();r.pick('dock',0);await flush();
  assert.equal(r.el('g-arch').hidden,true);
});

test('include archives on a tapped row: the tap asks for it, and switching it keeps the tapped row',async()=>{
  const p=await dockWithRows();
  p.el('g-archives').checked=true;p.el('g-archives').fire('change');await flush();
  assert.equal(p.log.renders.at(-1).options.include_archives,true);
  tapRow(p,3);await flush();
  const sent=p.log.renders.at(-1);
  assert.deepEqual(sent.options,{include_rows:true,include_archives:true});
  p.el('g-arch-toggle').click();await flush();
  assert.deepEqual(p.log.renders.at(-1),{...sent,options:{include_rows:true}},'the same focused request, archives left out');
});

test('the Dock switches: All / Local re-read the Docks alone, mark ⭐ and Ⓛ, are remembered per device; a failure goes back to the list shown',async()=>{
  const s=memory(),p=page({storage:s});await flush();
  assert.deepEqual(p.log.fetches,[['/api/view/roots','GET'],['/api/glimpse/catalog','GET']],'cold start: Recent roots and the catalog only');
  p.api.openSheet('dock');
  assert.equal(p.el('g-psheet').hidden,false);assert.equal(p.el('g-psheet-title').textContent,'Favorite Docks');
  p.el('g-docks-all').checked=true;p.el('g-docks-all').fire('change');await flush();
  assert.deepEqual(p.log.docks.at(-1),{all:true,local:false});
  assert.deepEqual(s.sets.at(-1),['pane_view_dock_list_v1:page','{"all":true,"local":false}']);
  assert.equal(p.el('g-psheet-title').textContent,'All Docks');assert.equal(p.el('g-dock-label').textContent,'All Docks');
  assert.equal(p.el('g-dock-count').textContent,'3');
  const sheetRows=()=>p.el('g-psheet-list').children[0].children;
  assert.deepEqual(sheetRows().map(b=>b.textContent),['⭐ Pane Views Dock⭐','Local child','Plain Dock'],'⭐ on an all-Docks list');
  p.el('g-docks-local').checked=true;p.el('g-docks-local').fire('change');await flush();
  assert.deepEqual(p.log.docks.at(-1),{all:true,local:true});
  assert.deepEqual(sheetRows().map(b=>b.textContent),['⭐ Pane Views Dock⭐','Local childⓁ','Plain Dock']);
  assert.equal(sheetRows()[1].dataset.depth,'1');
  // a new page on this device reads the remembered list after the catalog
  const q=page({storage:s});await flush();await flush();
  assert.deepEqual(q.log.fetches.map(f=>f[0]),['/api/view/roots','/api/glimpse/catalog','/api/glimpse/docks']);
  assert.equal(q.el('g-dock-label').textContent,'All + local Docks');
  // a pane server not restarted yet (404): the list shown stays, the switches go back to it, and it says so
  const r=page();await flush();r.api.openSheet('dock');
  r.setDocks(()=>({status:404,body:{schemaVersion:1,status:'request',error:'unknown glimpse route'}}));
  r.el('g-docks-all').checked=true;r.el('g-docks-all').fire('change');await flush();
  assert.equal(r.el('g-docks-all').checked,false);
  assert.match(r.el('g-psheet-status').textContent,/^⚠ All Docks unavailable, still showing Favorite Docks: unknown glimpse route \(this list needs the pane server restarted once\)$/);
  assert.equal(r.el('g-dock-count').textContent,'1');
});

test('the full-screen picker: a filter keeps a match\'s parent Docks, a tap closes it and renders a fresh root; groups open every section',async()=>{
  const docks=[{handle:'a',name:'Shelf',depth:0},{handle:'b',name:'[ap:NEEDLE] Needle Dock',depth:1},{handle:'c',name:'Other',depth:1},{handle:'d',name:'Far',depth:0}];
  const groups=[{handle:'g1',name:'Pane | View [1]'},{handle:'g2',name:'Pane | Send [2]'},{handle:'g3',name:'Top'}];
  const p=page({catalog:[200,{schemaVersion:1,docks,groups,errors:[]}]});await flush();
  p.api.openSheet('dock');
  p.el('g-psheet-filter').value='needle';p.el('g-psheet-filter').fire('input');
  const rows=()=>p.el('g-psheet-list').children[0].children;
  assert.deepEqual(rows().map(b=>b.title),['Shelf','[ap:NEEDLE] Needle Dock']);
  assert.match(rows()[0].className,/g-pctx/);assert.doesNotMatch(rows()[1].className,/g-pctx/);
  assert.equal(p.el('g-psheet-count').textContent,'1 of 4');
  rows()[1].click();await flush();
  assert.equal(p.el('g-psheet').hidden,true);
  assert.deepEqual(p.log.renders.at(-1).value,{dock:'b'});
  assert.equal(p.api.history().root().selection.label,'Dock · [ap:NEEDLE] Needle Dock');
  p.api.openSheet('group');
  assert.equal(p.el('g-psheet-docks').hidden,true);assert.equal(p.el('g-psheet-groups').hidden,false);
  assert.deepEqual(rows().map(b=>b.textContent),['▸Pane','Top'],'sections closed, as in the compact picker');
  p.el('g-groups-open').checked=true;p.el('g-groups-open').fire('change');
  assert.deepEqual(rows().map(b=>b.textContent),['▾Pane','View','Send','Top']);
  p.el('g-psheet-filter').value='send';p.el('g-psheet-filter').fire('input');
  assert.deepEqual(rows().map(b=>b.textContent),['▾Pane','Send']);assert.equal(p.el('g-psheet-count').textContent,'1 of 3');
  p.el('g-psheet-filter').value='zzz';p.el('g-psheet-filter').fire('input');
  assert.equal(p.el('g-psheet-status').textContent,'Nothing matches “zzz”.');
  p.el('g-psheet-close').click();assert.equal(p.el('g-psheet').hidden,true);
  // the Choose line's buttons and each compact picker's ⤢ open it, never toggling their details
  let prevented=0;const ev={preventDefault(){prevented++;},stopPropagation(){}};
  p.el('g-pick-groups').fire('click',ev);assert.equal(p.el('g-psheet').hidden,false);assert.equal(p.el('g-psheet-title').textContent,'Slice groups');
  p.el('g-dock-full').fire('click',ev);assert.equal(p.el('g-psheet-title').textContent,'Favorite Docks');assert.equal(prevented,2);
});

test('where it lives: one node on screen shows the button; it reads node.ancestors once per chain and a handle in it opens in View',async()=>{
  const p=page();await flush();
  p.pick('group',0);await flush();
  assert.equal(p.el('g-where').hidden,true,'a slice group is no one node');
  p.el('g-handle').value='[ap:LEAF01]';p.el('g-handle-form').fire('submit');await flush();
  assert.equal(p.el('g-where').hidden,false);
  p.el('g-where').click();await flush();
  assert.deepEqual(p.log.ancestors,[{handle:'[ap:LEAF01]',uuids:'leaf'}]);
  assert.equal(p.el('g-anc').hidden,false);assert.equal(p.el('g-anc-title').textContent,'Where [ap:LEAF01] lives');
  assert.equal(p.log.toHtml.at(-1).md,'# ⦿ Root\n## • [ap:LEAF01] Leaf\n','painted by the shared renderer');
  assert.equal(p.el('g-anc-doc').innerHTML,'<section data-shared># ⦿ Root\n## • [ap:LEAF01] Leaf\n</section>');
  p.el('g-anc-close').click();p.el('g-where').click();await flush();
  assert.equal(p.log.ancestors.length,1,'the same chain is not read twice');
  p.el('g-anc').querySelector('.g-seg').fire('click',{target:{closest:()=>({dataset:{uuids:'every-level'}})}});await flush();
  assert.deepEqual(p.log.ancestors.at(-1),{handle:'[ap:LEAF01]',uuids:'every-level'});
  p.el('g-anc-copy').click();await flush();assert.equal(p.log.clipboard.at(-1),'# ⦿ Root\n## • [ap:LEAF01] Leaf\n');
  // a handle tapped inside the chain: a new entry in View, the pop-up closed
  const inside={closest:sel=>sel==='#g-anc'?{}:null};
  p.win.opcOpenInView('3f3754e2',{node:inside});await flush();
  assert.equal(p.el('g-anc').hidden,true);
  assert.deepEqual(p.log.renders.at(-1).value,{node:'3f3754e2'});
  assert.equal(p.api.history().state.entries.length,2);
  // a Dock pick and a tapped row are one node too (the row by its uuid); an estate never
  assert.equal(p.api.nodeHandle({selection:{kind:'parcel',value:{dock:'d0c4'}}}),'d0c4');
  assert.equal(p.api.nodeHandle({selection:{kind:'parcel',value:{group:'g'},focus:{uuid:'row-uuid'}}}),'row-uuid');
  assert.equal(p.api.nodeHandle({selection:{kind:'parcel',value:{kind:'raw_root',root_uuid:'r00t'}}}),'r00t');
  assert.equal(p.api.nodeHandle({selection:{kind:'estate',value:{node:'x'}}}),null);
  assert.equal(p.api.nodeHandle({selection:{kind:'parcel',value:{tags:['t'],root:'x'}}}),null);
  // a failure says why
  p.setAncestors(()=>({status:404,body:{schemaVersion:1,status:'request',error:'unknown glimpse route'}}));
  p.el('g-where').click();await flush();
  assert.equal(p.el('g-anc-status').textContent,'⚠ Could not read where it lives: unknown glimpse route (this needs the pane server restarted once)');
});

test('the dividers: a drag or the keys set the list\'s max-height, bounded by its content and the document\'s 64 px, remembered per device',async()=>{
  const s=memory();
  const prep=el=>{const page=el('g-page');page.style.setProperty=(k,v)=>{page.style[k]=v;};
    el('g-rows').scrollHeight=300;el('g-rows').getBoundingClientRect=()=>({top:0,left:0,width:100,height:100,bottom:100,right:100});
    el('g-scroll').getBoundingClientRect=()=>({top:0,left:0,width:100,height:250,bottom:250,right:100});};
  const p=page({storage:s,prep});await flush();
  const divider=p.el('g-rows-split'),style=p.el('g-page').style;
  divider.fire('pointerdown',{button:0,pointerId:7,clientY:100});
  divider.fire('pointermove',{pointerId:7,clientY:150});
  assert.equal(style['--g-rows-max'],'150px');
  divider.fire('pointermove',{pointerId:9,clientY:400});assert.equal(style['--g-rows-max'],'150px','another pointer is ignored');
  divider.fire('pointermove',{pointerId:7,clientY:900});assert.equal(style['--g-rows-max'],'286px','no further than the document\'s 64 px (100 + 250 − 64)');
  divider.fire('pointerup',{pointerId:7});
  assert.deepEqual(s.sets.at(-1),['pane_view_splits_v1:page','{"rows":0.715}'],'a fraction of the page (286 / 400)');
  divider.fire('keydown',{key:'ArrowDown'});assert.equal(style['--g-rows-max'],'120px','Down: 5% of the page more than the list is now (100 + 20)');
  divider.fire('keydown',{key:'Home'});assert.equal(style['--g-rows-max'],'40px');
  p.el('g-ctl-split').fire('keydown',{key:'End'});assert.ok('--g-ctl-max' in style);
  // a new page on this device starts from the stored fractions
  const q=page({storage:s,prep});await flush();
  assert.equal(q.el('g-page').style['--g-rows-max'],'40px');
  // each divider shows only beside what it sizes
  assert.equal(q.el('g-ctl-split').hidden,false,'empty View: the controls stand open');
  q.setReply(body=>ok(body,envelope('anchor_dock')));q.pick('dock',0);await flush();
  assert.equal(q.el('g-ctl-split').hidden,true,'folded controls: no divider under them');
  assert.equal(q.el('g-rows-split').hidden,false,'a row list: its divider');
  q.el('g-rows-box').open=false;q.el('g-rows-box').fire('toggle');assert.equal(q.el('g-rows-split').hidden,true);
  q.el('g-rows-box').open=true;q.el('g-rows-box').fire('toggle');assert.equal(q.el('g-rows-split').hidden,false);
});

test('the page carries the dividers, the archives switch and line, both sheets and the where button; touch-action none on the handles',()=>{
  const css=fs.readFileSync(path.join(root,'glimpse.css'),'utf8');
  for(const id of ['g-ctl-split','g-rows-split','g-archives','g-arch','g-arch-toggle','g-psheet','g-psheet-filter','g-docks-all','g-docks-local','g-groups-open','g-pick-docks','g-pick-groups','g-dock-full','g-group-full','g-where','g-anc','g-anc-doc'])
    assert.match(pageHtml,new RegExp('id="'+id+'"'),id);
  assert.match(pageHtml,/<div id="g-rows-split" class="g-split" role="separator" aria-orientation="horizontal" tabindex="0"/);
  assert.ok(pageHtml.indexOf('id="g-rows-box"')<pageHtml.indexOf('id="g-rows-split"')&&pageHtml.indexOf('id="g-rows-split"')<pageHtml.indexOf('id="g-scroll"'));
  assert.match(css,/\.g-split \{[^}]*touch-action: none;/);
  assert.match(css,/#g-ctl-body \{[^}]*max-height: var\(--g-ctl-max, 62svh\); overflow-y: auto;/);
  assert.match(css,/#g-rows \{ max-height: var\(--g-rows-max, 34vh\);/);
  assert.match(pageHtml,/<section id="g-psheet" class="g-sheet g-sheet-full"[^>]*data-opc-no-handles hidden>/);
  assert.doesNotMatch(pageHtml.match(/<section id="g-anc"[^>]*>/)[0],/data-opc-no-handles/,'handles in the chain are live');
});

// ---- images: the lightbox module (a recording stand-in here; glimpse-lightbox.html runs the real one with PhotoSwipe)
function lightboxStub({imageOnly=true,groups,live=[]}={}){
  const two=groups||[{label:'Opus',heading:'Opus · O',images:[{path:'/mnt/e/o/a.png',src:'/mnt/e/o/a.png',mapped:'/files/mnt/e/o/a.png'},{path:'/mnt/e/o/b.png'}]},
    {label:'Fable',heading:'Fable · F',images:[{path:'/mnt/e/f/c.png'},{src:'https://e.com/r.png',mapped:'https://e.com/r.png',path:null}]}];
  return log=>Object.freeze({
    mapImages:(root,mapSrc)=>{log.lb.push(['map',mapSrc('/mnt/e/My Shots/a#1?.png')]);return 1;},
    // live lines: the folders/globs this document names; expanded, their listed files join a group of their own
    livePaths:()=>[...live],
    expandLive:(root,lookup)=>{const got=live.map(p=>lookup(p));log.lb.push(['expand',got.map(l=>l?(l.error?'error: '+l.error:l.files.length):null)]);
      log.listed=got.every(l=>l&&!l.error)?got.flatMap(l=>l.files.map(f=>f.path)):null;return 0;},
    sheets:()=>{log.lb.push(['sheets']);return 1;},
    collect:()=>{log.lb.push(['collect']);const out=JSON.parse(JSON.stringify(two));if(live.length&&log.listed)out.push({label:'Shots',heading:'Shots',images:log.listed.map(path=>({path}))});return out;},
    imageOnly:()=>imageOnly,
    applySizes:(root,lookup)=>log.lb.push(['sizes',lookup('/mnt/e/o/a.png')?.width??null]),
    wire:(container,options)=>{log.lb.push(['wire',container.id]);log.onOpen=options.onOpen;},
    open:(groups,options)=>{log.lb.push(['open',options.start,groups.length,options.sizes(groups[0].images[0])?.width??null]);options.onImmersive(true);
      const c={on:true,isOpen:()=>c.on,close:()=>{if(!c.on)return;c.on=false;log.lb.push(['close']);options.onImmersive(false);options.onClose(0,null);},
        goTo:i=>log.lb.push(['goTo',i]),refresh:()=>log.lb.push(['refresh'])};log.controller=c;return c;},
    // side by side: the groups it was given, the first one's size; its controller and options kept for the test
    compare:(groups,options)=>{log.lb.push(['compare',groups.length,options.sizes(groups[0].images[0])?.width??null]);
      const c={on:true,isOpen:()=>c.on,close:()=>{if(!c.on)return;c.on=false;log.lb.push(['compare-close']);options.onClose?.();}};log.compare={c,options};return c;}});
}

test('a gallery: local paths map to /files/ (each segment encoded), one meta read, the lightbox opens by itself once, immersive on /m',async()=>{
  const immersive=[],p=page({lightbox:lightboxStub(),embedded:true,narrow:true,around:{opcViewImmersive:on=>immersive.push(on)}});await flush();
  assert.deepEqual(p.log.lb,[['wire','g-doc']]);
  p.el('g-handle').value='[ap:PZNQ9J]';p.el('g-handle-form').fire('submit');await flush();await flush();
  assert.deepEqual(p.log.lb.slice(1,4),[['map','/files/mnt/e/My%20Shots/a%231%3F.png'],['sheets'],['collect']]);
  assert.deepEqual(p.log.meta,[{paths:['/mnt/e/o/a.png','/mnt/e/o/b.png','/mnt/e/f/c.png']}],'one call, local paths only, each once');
  assert.deepEqual(p.log.lb.filter(e=>e[0]==='open'),[['open',0,2,1500]],'opened at the first image, sized from the meta reply');
  assert.deepEqual(immersive,[true],'the phone page is asked for the whole screen');
  assert.equal(p.el('g-images').hidden,false);assert.equal(p.el('g-images-text').textContent,'4 images · 2 groups');
  // Back (the lightbox closes): the page gets its screen back; Refresh of the same gallery stays on its contact sheet
  p.log.controller.close();assert.deepEqual(immersive,[true,false]);
  p.el('g-refresh').click();await flush();await flush();
  assert.equal(p.log.lb.filter(e=>e[0]==='open').length,1,'Refresh never reopens it');
  assert.equal(p.log.meta.length,2,'a fresh reply reads every size again (a regenerated screenshot)');
  // a tap on an image (wire's onOpen) or the chip opens it there; an open lightbox just moves
  p.log.onOpen(3);assert.deepEqual(p.log.lb.filter(e=>e[0]==='open').at(-1),['open',3,2,1500]);
  p.el('g-images-open').click();assert.deepEqual(p.log.lb.at(-1),['goTo',0]);
  // a new document closes the open lightbox first; being a gallery too, it opens by itself again
  p.el('g-handle').value='[ap:OTHER1]';p.el('g-handle-form').fire('submit');await flush();await flush();
  assert.deepEqual(p.log.lb.filter(e=>e[0]==='close'||e[0]==='open').map(e=>e[0]),['open','close','open','close','open']);
});

test('a document with images and prose: the chip and taps open it, nothing opens by itself; Source shows no images',async()=>{
  const p=page({lightbox:lightboxStub({imageOnly:false}),narrow:false});await flush();
  p.el('g-handle').value='[ap:MIXED1]';p.el('g-handle-form').fire('submit');await flush();await flush();
  assert.equal(p.log.lb.filter(e=>e[0]==='open').length,0);
  assert.equal(p.el('g-images-text').textContent,'4 images · 2 groups');
  p.el('g-images-open').click();assert.deepEqual(p.log.lb.at(-1),['open',0,2,1500]);
  p.log.controller.close();
  p.el('g-source').click();await flush();
  assert.equal(p.el('g-images').hidden,true,'Source: no images, no chip');
  assert.equal(p.log.lb.filter(e=>e[0]==='map').length,1,'Source is never mapped');
  p.el('g-rendered').click();await flush();
  assert.equal(p.el('g-images').hidden,false);
  assert.equal(p.log.meta.length,1,'repainting the same document reads no size twice');
});

test('a failed meta read still opens the gallery (each image measured once loaded); hidden View opens nothing',async()=>{
  const p=page({lightbox:lightboxStub(),meta:()=>({status:404,body:{error:'unknown'}})});await flush();
  p.el('g-handle').value='[ap:PZNQ9J]';p.el('g-handle-form').fire('submit');await flush();await flush();
  assert.deepEqual(p.log.lb.filter(e=>e[0]==='open'),[['open',0,2,null]]);
  const hidden=page({lightbox:lightboxStub()});await flush();
  hidden.win.document.documentElement.clientHeight=0;  // the /m View tab not on screen (its frame display:none)
  hidden.el('g-handle').value='[ap:PZNQ9J]';hidden.el('g-handle-form').fire('submit');await flush();await flush();
  assert.equal(hidden.log.lb.filter(e=>e[0]==='open').length,0);
  assert.equal(hidden.el('g-images').hidden,false,'the chip still offers it');
});

test('side by side: offered on a wide page for two groups or more, never on a phone or in /m; a panel opens the lightbox; a new document closes it',async()=>{
  const p=page({lightbox:lightboxStub({imageOnly:false}),narrow:false});await flush();
  assert.match(pageHtml,/<button id="g-images-compare" type="button"[^>]* hidden>⧉ Side by side<\/button>/,'nothing rendered yet: nothing offered');
  p.el('g-handle').value='[ap:MIXED1]';p.el('g-handle-form').fire('submit');await flush();await flush();
  assert.equal(p.el('g-images-compare').hidden,false);
  p.el('g-images-compare').click();
  assert.deepEqual(p.log.lb.filter(e=>e[0]==='compare'),[['compare',2,1500]],'on the document\'s groups, sized from the meta reply');
  assert.equal(p.api.openCompare(),true,'already open: nothing new');
  assert.equal(p.log.lb.filter(e=>e[0]==='compare').length,1);
  // a click on a panel's image: View's own lightbox over it, at that image
  p.log.compare.options.onOpen(2);assert.deepEqual(p.log.lb.filter(e=>e[0]==='open').at(-1),['open',2,2,1500]);
  p.log.controller.close();
  assert.equal(p.api.state.compare,p.log.compare.c,'closing the lightbox leaves side by side open');
  // a new document closes it
  p.el('g-handle').value='[ap:OTHER1]';p.el('g-handle-form').fire('submit');await flush();await flush();
  assert.ok(p.log.lb.some(e=>e[0]==='compare-close'));assert.equal(p.api.state.compare,null);
  // one group: nothing to put side by side
  const one=page({lightbox:lightboxStub({imageOnly:false,groups:[{label:'A',heading:'A',images:[{path:'/mnt/e/a.png'},{path:'/mnt/e/b.png'}]}]}),narrow:false});await flush();
  one.el('g-handle').value='[ap:ONE001]';one.el('g-handle-form').fire('submit');await flush();await flush();
  assert.equal(one.el('g-images-open').hidden,false);assert.equal(one.el('g-images-compare').hidden,true);assert.equal(one.api.openCompare(),false);
  // a phone-sized page, and /m's View frame: never offered
  for(const opts of [{narrow:true},{narrow:false,embedded:true}]){
    const phone=page({lightbox:lightboxStub({imageOnly:false}),...opts});await flush();
    phone.el('g-handle').value='[ap:MIXED1]';phone.el('g-handle-form').fire('submit');await flush();await flush();
    assert.equal(phone.el('g-images-open').hidden,false);assert.equal(phone.el('g-images-compare').hidden,true,JSON.stringify(opts));
    assert.equal(phone.api.openCompare(),false);assert.equal(phone.log.lb.filter(e=>e[0]==='compare').length,0);
  }
});

// ---- live gallery lines (answer 5): a folder or glob listed through /api/files/list at render time
const listedFiles={'/mnt/e/shots/':['/mnt/e/shots/s1.png','/mnt/e/shots/s2.png'],'/mnt/e/f/*.png':['/mnt/e/f/g1.png']};
const listReply=(files=listedFiles)=>body=>({status:200,body:{schemaVersion:1,lists:body.paths.map(path=>({path,kind:path.endsWith('/')?'folder':'glob',
  total:(files[path]||[]).length,files:(files[path]||[]).map(f=>({path:f,url:'/files'+f,exists:true,width:800,height:600}))}))}});
test('a live folder: one listing per document, painted again with its files, sized by the listing; the gallery opens once they are in',async()=>{
  let files=listedFiles;
  const p=page({lightbox:lightboxStub({live:['/mnt/e/shots/','/mnt/e/f/*.png']}),embedded:true,narrow:true,around:{opcViewImmersive:()=>{}},list:body=>listReply(files)(body)});await flush();
  p.el('g-handle').value='[ap:LIVE01]';p.el('g-handle-form').fire('submit');await flush();await flush();await flush();
  assert.deepEqual(p.log.list,[{paths:['/mnt/e/shots/','/mnt/e/f/*.png']}],'one call for every live line of the document');
  assert.deepEqual(p.log.lb.filter(e=>e[0]==='expand'),[['expand',[null,null]],['expand',[2,1]]],'painted first with its tiles waiting, then with the listed files');
  assert.equal(p.log.toHtml.length,2);
  assert.deepEqual(p.log.meta,[{paths:['/mnt/e/o/a.png','/mnt/e/o/b.png','/mnt/e/f/c.png']}],'the listed files are already sized: never read twice');
  const order=p.log.lb.map(e=>e[0]),opened=order.indexOf('open');
  assert.ok(opened>order.lastIndexOf('expand'),'the gallery opened only once its folders were listed');
  assert.equal(p.el('g-images-text').textContent,'7 images · 3 groups');
  assert.equal(p.api.state.imageMeta.get('/mnt/e/shots/s1.png').width,800);
  p.log.controller.close();
  // Refresh lists again (a folder an agent has added to): the same files paint nothing more; new ones repaint
  p.el('g-refresh').click();await flush();await flush();await flush();
  assert.equal(p.log.list.length,2);assert.equal(p.log.toHtml.length,3,'the same listing: no second paint');
  files={...listedFiles,'/mnt/e/shots/':[...listedFiles['/mnt/e/shots/'],'/mnt/e/shots/s3.png']};
  p.el('g-refresh').click();await flush();await flush();await flush();
  assert.equal(p.log.list.length,3);assert.equal(p.log.toHtml.length,5,'a new file: painted again');
  assert.deepEqual(p.log.lb.filter(e=>e[0]==='expand').at(-1),['expand',[3,1]]);
  assert.equal(p.log.lb.filter(e=>e[0]==='open').length,1,'Refresh never reopens it');
  // Source and back: no listing again, the held listings expand at once
  p.el('g-source').click();await flush();p.el('g-rendered').click();await flush();
  assert.equal(p.log.list.length,3);assert.deepEqual(p.log.lb.filter(e=>e[0]==='expand').at(-1),['expand',[3,1]]);
});

test('a live line a pane server cannot list yet (404) says so on its tile; nothing opens by itself without its images',async()=>{
  const p=page({lightbox:lightboxStub({live:['/mnt/e/shots/'],groups:[]}),list:()=>({status:404,body:{error:'unknown files route'}})});await flush();
  p.el('g-handle').value='[ap:LIVE01]';p.el('g-handle-form').fire('submit');await flush();await flush();await flush();
  assert.deepEqual(p.log.lb.filter(e=>e[0]==='expand').at(-1),['expand',['error: could not list it: the pane server needs its restart once']]);
  assert.equal(p.log.lb.filter(e=>e[0]==='open').length,0);
  assert.equal(p.api.state.listings.get('/mnt/e/shots/').files.length,0);
});

// ---- named estates (answer 3): a tapped or pasted handle whose note lists parcels renders as that estate
const ESTATE_UUID='5e7a7e00-0000-4000-8000-000000000001',ESTATE_NAME='[ap:ESTAT1] 🏛️ Bake-off estate #gallery';
function estateReplies(p,note='{"node": "[ap:GAL001]"}\n\n{"dock": "[ap:DOCK01]"}'){
  p.setReply(body=>{
    if(body.kind==='estate')return {status:200,body:{schemaVersion:1,markdown:'# 🏛️ ESTATE · '+ESTATE_NAME+'\n\n# 📄 [1] [ap:GAL001]\n',bytes:40,renderedAt:'2026-10-24T09:00:00Z',request:body}};
    const v=body.value,md=(v.node||v.subtree)==='[ap:ESTAT1]'?'# '+ESTATE_NAME+'\n\n'+note:'# [ap:PLAIN1] Notes\n\nJust prose about {"node": "[ap:GAL001]"} and more.';
    return {status:200,body:{schemaVersion:1,markdown:md,bytes:md.length,renderedAt:'2026-10-24T09:00:00Z',request:body,
      rows:[{uuid:ESTATE_UUID,depth:0,name:md.slice(2,md.indexOf('\n')),ancestor_path:[],occurrence:0}],render_spec:{kind:'lightning_rod',root_uuid:ESTATE_UUID}}};
  });
}
test('estateLines: the estate grammar\'s one parcel per line (blank lines fine); one other line and it is a plain node',async()=>{
  const p=page();await flush();
  const groups=[{handle:'grp-1',name:'opc pane'},{handle:'grp-2',name:'Recruiting | Pipeline'}],n=note=>p.api.estateLines(note,groups);
  const estates=[['{"node": "[ap:G1]"}\n{"node": "[ap:G2]"}',2],['\n{"dock": "[ap:D1]", "descend": true}\n\n{"subtree": "3f3754e2"}\n',2],
    ['{"tags": ["x", "y"], "root": "[ap:R1]"}',1],['{"symbols": ["fn"], "root": "7834ca3d"}',1],['{"ref": "[ap:P1]", "kind": "node", "how": "subtree"}',1],
    ['{"ref": "opc pane", "kind": "slice_group"}',1],['{"lens": {"kind": "group", "group_handle": "x"}, "alias": "L"}',1],
    ['{"alias": "Tab", "spec": {"kind": "raw_root", "root_uuid": "x"}}',1],['{"kind": "lightning_rod", "root_uuid": "abc"}',1],
    ['opc pane\n"Pipeline"\n[ap:DOCK01]\n3f3754e2\ngrp-2',5],['↩ moved from Inbox (abcd1234) · 2026-10-01\n\n{"node": "[ap:G1]"}',1],
    ['```json\n{"node": "[ap:G1]"}\n{"group": "opc pane"}\n```',2],['[\n  {"alias": "A", "spec": {"kind": "raw_root", "root_uuid": "x"}},\n  {"group": "opc pane"}\n]',2],
    ['{"node":\u00a0"[ap:G1]"}',1],['{"dock": "[ap:D1]", "recursive": false, "alias": "D"}',1]];
  for(const [note,count] of estates)assert.equal(n(note),count,note);
  const plain=['','  \n ','Hello world','opc pane\nsome prose here','See {"node": "[ap:G1]"} for the gallery','{"node": "[ap:G1]"}\nand a sentence',
    '![shot](/mnt/e/x.png)','/mnt/e/x/shots/','https://example.com/a','{"node": ""}','{"node": "[ap:G1]", "roles": ["recon"]}','{"dock": "x", "descend": "yes"}',
    '{"node": "a", "dock": "b"}','{"node": "x", "kind": "raw_root"}','{"ref": "E:\\\\x", "kind": "file"}','{"tags": [], "root": "r"}','{"foo": 1}','20261022',
    '```\n{"node": "[ap:G1]"}\n```\nThen prose','[1, 2]','{"subtree": "x", "descend": true}','{"ref": "x", "kind": "node", "descend": true}'];
  for(const note of plain)assert.equal(n(note),0,JSON.stringify(note));
  assert.equal(p.api.estateLines('opc pane',[]),0,'a bare name without the catalog stays a plain node');
});

test('a tapped handle whose note lists parcels renders as that estate (each parcel its own read); one tap shows the node, one tap back',async()=>{
  const p=page();await flush();estateReplies(p);
  p.api.openHandle('[ap:ESTAT1]','node',{from:'outside',where:'Chat'});await flush();await flush();
  assert.deepEqual(p.log.renders.map(r=>[r.kind,JSON.stringify(r.value),JSON.stringify(r.options)]),[
    ['parcel','{"node":"[ap:ESTAT1]"}','{"headings_only":false,"include_rows":true}'],['estate',JSON.stringify(ESTATE_UUID),'{}']]);
  assert.equal(p.api.state.held.label,'Estate · '+ESTATE_NAME);
  assert.equal(p.el('g-estate').hidden,false);
  assert.equal(p.el('g-estate-text').textContent,'🏛️ Estate · '+ESTATE_NAME+' · 2 parcels');
  assert.equal(p.el('g-estate-toggle').textContent,'Show the node');
  assert.equal(p.api.history().current().name,ESTATE_NAME,'the history entry keeps the node\'s own name');
  assert.equal(p.el('g-rows-box').hidden,true,'an estate has no row list');
  assert.equal(p.api.nodeHandle(),ESTATE_UUID,'where: the estate\'s own node');
  assert.match(p.el('g-asof').textContent,/ · each parcel's own read · /);
  assert.equal(p.el('g-compact').hidden,true);
  p.el('g-estate-toggle').click();await flush();
  assert.deepEqual([p.log.renders.length,p.log.renders[2].kind,JSON.stringify(p.log.renders[2].value)],[3,'parcel','{"node":"[ap:ESTAT1]"}'],'the node itself, its note as written');
  assert.equal(p.el('g-estate-text').textContent,'🏛️ This node is a named estate (its note lists 2 parcels)');
  assert.equal(p.el('g-estate-toggle').textContent,'Show the estate');
  p.el('g-refresh').click();await flush();
  assert.deepEqual([p.log.renders.length,p.log.renders[3].kind],[4,'parcel'],'Refresh keeps the node');
  p.el('g-estate-toggle').click();await flush();
  assert.deepEqual([p.log.renders.length,p.log.renders[4].kind,p.log.renders[4].value],[5,'estate',ESTATE_UUID],'back to the estate in one request');
  p.el('g-refresh').click();await flush();
  assert.deepEqual([p.log.renders.length,p.log.renders[5].kind],[6,'estate'],'Refresh of the estate is the estate again');
  // history: back to it later reads the node, then shows the estate again
  await p.api.history().go(0);await flush();
  assert.deepEqual(p.log.renders.slice(6).map(r=>r.kind),['parcel','estate']);
});

test('estate options: headings_only only when ticked by hand; a plain note, a typed {node} parcel and a Subtree read are never estates',async()=>{
  const desk=page({narrow:false});await flush();estateReplies(desk);
  desk.el('g-handle').value='[ap:ESTAT1]';desk.el('g-handle-form').fire('submit');await flush();await flush();
  assert.deepEqual(desk.log.renders.at(-1).options,{},'the desktop sends no headings_only for an estate either');
  desk.el('g-headings').checked=true;desk.el('g-headings').fire('change');await flush();
  assert.deepEqual([desk.log.renders.at(-1).kind,desk.log.renders.at(-1).options],['estate',{headings_only:true}],'ticked: the call overrides every parcel');
  assert.equal(desk.el('g-compact').hidden,false);
  desk.el('g-headings').checked=false;desk.el('g-headings').fire('change');await flush();
  assert.deepEqual(desk.log.renders.at(-1).options,{},'unticked: each parcel\'s own read again');
  const p=page();await flush();estateReplies(p);
  p.el('g-handle').value='[ap:PLAIN1]';p.el('g-handle-form').fire('submit');await flush();await flush();
  assert.equal(p.log.renders.length,1,'prose with JSON inside is a plain node');assert.equal(p.el('g-estate').hidden,true);
  const seg=p.el('g-spec-box').querySelector('.g-seg');seg.fire('click',{target:{closest:()=>({dataset:{kind:'parcel'}})}});
  p.el('g-spec').value='{"node": "[ap:ESTAT1]"}';p.el('g-spec-render').click();await flush();await flush();
  assert.deepEqual([p.log.renders.length,p.log.renders.at(-1).kind],[2,'parcel'],'{node} asks for the node on its own');
  assert.equal(p.el('g-estate').hidden,true);
  p.el('g-handle').value='[ap:ESTAT1]';p.el('g-handle-form').querySelector('.g-seg').fire('click',{target:{closest:()=>({dataset:{how:'subtree'}})}});
  p.el('g-handle-form').fire('submit');await flush();await flush();
  assert.deepEqual([p.log.renders.length,JSON.stringify(p.log.renders.at(-1).value)],[3,'{"subtree":"[ap:ESTAT1]"}']);
});

test('an estate of galleries opens straight into the lightbox as a gallery node does',async()=>{
  const p=page({lightbox:lightboxStub(),embedded:true,around:{opcViewImmersive:()=>{}}});await flush();estateReplies(p);
  p.api.openHandle('[ap:ESTAT1]','node',{from:'outside'});await flush();await flush();await flush();
  assert.equal(p.api.state.held.selection.kind,'estate');
  assert.deepEqual(p.log.lb.filter(e=>e[0]==='open'),[['open',0,2,1500]]);
});

test('an estate that cannot be rendered at all shows the node itself, the estate\'s error above it, and offers the estate again',async()=>{
  const p=page();await flush();let fail=true;
  // the daemon refuses the whole estate (every parcel failed) until fail is cleared
  estateReplies({setReply:f=>p.setReply(body=>body.kind==='estate'&&fail?{status:502,body:{schemaVersion:1,status:'daemon',error:'Open a Workflowy tab with the NEXUS extension on the PC'}}:f(body))});
  p.api.openHandle('[ap:ESTAT1]','node',{from:'outside'});await flush();await flush();await flush();
  assert.deepEqual(p.log.renders.map(r=>r.kind),['parcel','estate','parcel']);
  assert.equal(p.api.state.held.selection.raw,true);
  assert.equal(p.el('g-error').hidden,false);
  assert.match(p.el('g-error').textContent,/^⚠ Estate · \[ap:ESTAT1\] 🏛️ Bake-off estate #gallery · Open a Workflowy tab/);
  assert.equal(p.el('g-estate-toggle').textContent,'Show the estate');
  fail=false;p.el('g-estate-toggle').click();await flush();
  assert.deepEqual([p.log.renders.at(-1).kind,p.el('g-error').hidden,p.el('g-estate-toggle').textContent],['estate',true,'Show the node']);
});

// ---- Recent roots (the outer history) and the three modes (Full, Collapsed, Maximized)
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const plain=v=>JSON.parse(JSON.stringify(v));  // values from the page's realm, compared here
const ops=roots=>roots.log.slice(1).map(c=>c.op+(c.key?':'+c.key:''));

test('Recent roots: a fresh root is a visit, the inner history is saved there in order, the row counts the list',async()=>{
  const roots=viewRootsFake(),p=page({roots,narrow:false});await flush();
  const h=p.api.history();
  assert.deepEqual(roots.log,[{op:'read'}],'one read at the start, nothing written');
  assert.equal(p.el('g-roots').hidden,true,'nothing listed and nothing shown: no row');
  await p.api.openHandle('[ap:ROOTA1]','node',{from:'outside',where:'Chat'});await flush();
  assert.deepEqual(ops(roots),['visit:node:ap:ROOTA1']);
  assert.equal(roots.log[1].device,'page');assert.equal(roots.log[1].root.where,'Chat');
  assert.deepEqual([p.el('g-roots').hidden,p.el('g-roots-count').textContent,p.el('g-hist-nav').hidden],[false,'· 1',false],'row 2 shows at a lone root (maximize lives there)');
  await p.api.openFromPage('[ap:LINKA1]',{node:null});await flush();
  assert.deepEqual(ops(roots),['visit:node:ap:ROOTA1'],'a tap is saved after a pause, not at once');
  await sleep(760);await h.settled();
  assert.deepEqual(ops(roots),['visit:node:ap:ROOTA1','save:node:ap:ROOTA1']);
  assert.deepEqual([roots.db.roots[0].entries.map(e=>e.handle),roots.db.roots[0].cursor],[['[ap:ROOTA1]','[ap:LINKA1]'],1]);
  assert.match(roots.db.roots[0].entries[1].name,/^Doc for /,'named from its reply');
  // Back, then a new root: the pending save goes first, then the visit
  p.el('g-back').click();await flush();
  await p.api.openHandle('3f3754e2-1111-4222-8333-444455556666','node',{from:'outside'});await flush();await h.settled();
  assert.deepEqual(ops(roots).slice(2),['save:node:ap:ROOTA1','visit:node:hex:3f3754e2']);
  assert.equal(roots.db.roots.find(r=>r.key==='node:ap:ROOTA1').cursor,0,'where A was left: back at its root');
  assert.deepEqual(roots.list().roots.map(r=>r.key),['node:hex:3f3754e2','node:ap:ROOTA1'],'most recently used first');
  assert.equal(p.el('g-roots-count').textContent,'· 2');
});

test('Recent roots: a root opened again brings its digging back ahead of it; a card picked from the list goes back to where it stood',async()=>{
  const roots=viewRootsFake(),p=page({roots,narrow:false});await flush();const h=p.api.history();
  await p.api.openHandle('[ap:ROOTA1]','node',{from:'outside'});await flush();
  for(const x of ['[ap:LINKA1]','[ap:LINKB2]','[ap:LINKC3]'])await p.api.openFromPage(x,{node:null});
  p.el('g-back').click();await flush();await h.flushOuter();await h.settled();
  assert.equal(roots.db.roots[0].cursor,2,'left at 3/4');
  await p.api.openHandle('[ap:ROOTB2]','node',{from:'outside'});await flush();await h.settled();
  // A again from outside (another chat link): the root shown, the four entries back, ahead of it
  await p.api.openHandle('[ap:ROOTA1]','node',{from:'outside',where:'Chat'});await flush();await h.settled();await flush();
  assert.deepEqual(plain([h.state.entries.map(e=>e.handle),h.state.cursor]),[['[ap:ROOTA1]','[ap:LINKA1]','[ap:LINKB2]','[ap:LINKC3]'],0]);
  assert.equal(p.el('g-fwd').disabled,false);assert.equal(p.el('g-hist-count').textContent,'4');
  assert.equal(p.log.renders.at(-1).value.node,'[ap:ROOTA1]');
  // the cards: the root on screen first, its badge from this page; B with its own
  const cards=h.cards();
  assert.deepEqual(plain(cards.map(c=>[c.key,c.count,!!c.here])),[['node:ap:ROOTA1',4,true],['node:ap:ROOTB2',1,false]]);
  // dug to 3/4 again, then B picked from the list, then A picked back: A lands at 3/4, where it stood, and renders that entry
  p.el('g-fwd').click();await flush();p.el('g-fwd').click();await flush();
  await h.selectRoot('node:ap:ROOTB2');await flush();
  assert.deepEqual(plain([h.state.entries.length,h.root().handle,p.log.renders.at(-1).value.node]),[1,'[ap:ROOTB2]','[ap:ROOTB2]']);
  await h.selectRoot('node:ap:ROOTA1');await flush();
  assert.deepEqual(plain([h.state.cursor,h.state.entries.length,p.log.renders.at(-1).value.node]),[2,4,'[ap:LINKB2]']);
  assert.equal(p.el('g-now').textContent.startsWith('3/4 · '),true);
  // the card of a root left at its root says where its digging went on to
  await h.selectRoot('node:ap:ROOTB2');await flush();p.api.history().go(0);
  await h.selectRoot('node:ap:ROOTA1');await flush();p.el('g-back').click();p.el('g-back').click();await flush();
  await h.selectRoot('node:ap:ROOTB2');await flush();
  const left=h.cards().find(c=>c.key==='node:ap:ROOTA1');
  assert.deepEqual(plain([left.cursor,left.count,left.last.handle]),[0,4,'[ap:LINKC3]']);
  // a tap meanwhile wins over a visit's late answer: nothing is pushed under it
  const slow=viewRootsFake(),q=page({roots:(method,body)=>new Promise(r=>setTimeout(()=>r(slow.handle(method,body)),body?.op==='visit'?40:0)),narrow:false});await flush();
  slow.handle('POST',{schemaVersion:1,op:'save',key:'node:ap:ROOTA1',entries:[{id:1,root:true,handle:'[ap:ROOTA1]',how:'node'},{id:2,handle:'[ap:OLD001]',how:'node'}],cursor:1});
  await q.api.openHandle('[ap:ROOTA1]','node',{from:'outside'});await q.api.openFromPage('[ap:NEW001]',{node:null});await sleep(80);
  assert.deepEqual(plain(q.api.history().state.entries.map(e=>e.handle)),['[ap:ROOTA1]','[ap:NEW001]']);
});

test('Recent roots: a cold start with roots listed and nothing shown offers them; the pick renders it with its whole inner history',async()=>{
  const roots=viewRootsFake();
  roots.handle('POST',{schemaVersion:1,op:'save',key:'dock:ap:DOCK01',entries:[{id:3,root:true,kind:'pick',pick:'dock',selection:{label:'Dock · ⭐ Pane Views Dock',kind:'parcel',value:{dock:'[ap:DOCK01]'},body:false},label:'Dock · ⭐ Pane Views Dock',from:'view'},{id:4,handle:'[ap:INDOCK]',how:'node',name:'[ap:INDOCK] Inside'}],cursor:1});
  const p=page({roots,narrow:true});await flush();const h=p.api.history();
  assert.deepEqual([p.el('g-hist').hidden,p.el('g-hist-row').hidden,p.el('g-hist-nav').hidden,p.el('g-roots').hidden],[false,true,true,false],'row 3 alone');
  assert.equal(p.el('g-roots-count').textContent,'· 1');
  p.el('g-roots-open').click();await flush();
  assert.equal(p.el('g-roots-panel').hidden,false);assert.equal(p.el('g-roots-title').textContent,'Recent roots · 1');
  const [card]=h.cards();assert.deepEqual(plain([card.key,card.count,card.cursor,card.at.handle]),['dock:ap:DOCK01',2,1,'[ap:INDOCK]']);
  await h.selectRoot('dock:ap:DOCK01');await flush();
  assert.equal(p.el('g-roots-panel').hidden,true);
  assert.deepEqual(plain([h.root().kind,h.state.cursor,p.log.renders.at(-1).value]),['pick',1,{node:'[ap:INDOCK]'}]);
  assert.deepEqual(ops(roots).slice(-1),['open:dock:ap:DOCK01']);
  // the same Dock pasted as Dock is the same root
  const key=p.win.opcViewHistory.rootKey;
  assert.equal(key({handle:'[ap:dock01]',how:'dock'}),'dock:ap:DOCK01');
  assert.equal(key({selection:{kind:'parcel',value:{dock:'[ap:DOCK01]'}},pick:'dock'}),'dock:ap:DOCK01');
  assert.match(key({selection:{kind:'parcel',value:{group:'grp-1'}},pick:'group'}),/^pick:group:[0-9a-z]{1,7}$/);
});

test('Recent roots: a note on the root on screen lists it first if needed, shows on its card, and travels with the list',async()=>{
  const roots=viewRootsFake(),p=page({roots:(m,b)=>new Promise(r=>setTimeout(()=>r(roots.handle(m,b)),b?.op==='visit'?30:0)),narrow:false});await flush();const h=p.api.history();
  void p.api.openHandle('[ap:SHOTS1]','node',{from:'outside'});await flush();
  h.startNote('node:ap:SHOTS1');h.outer.editing.text='  the grouped screenshots for the bake-off ';
  assert.equal(await h.saveNote(),true);await h.settled();
  const listed=roots.db.roots.find(r=>r.key==='node:ap:SHOTS1');
  assert.equal(listed.note,'the grouped screenshots for the bake-off');
  assert.equal(h.cards()[0].note,'the grouped screenshots for the bake-off');assert.equal(h.outer.editing,null);
  // a note on a root no longer listed (removed on the other device): the list comes back with the 404 and says so
  roots.db.roots=[];h.startNote('node:ap:SHOTS1');h.outer.editing.text='x';h.outer.list=[];
  roots.handle('POST',{schemaVersion:1,op:'limit',limit:10});
  assert.equal((await h.saveNote()),true,'the root on screen is listed again first, then noted');
});

test('Recent roots: a pane server from before the list (its 404, a 405 for a change) says so once; the inner history works as before',async()=>{
  const calls=[],p=page({narrow:false,roots:(method,body)=>{calls.push(method+(body?':'+body.op:''));return method==='GET'?{status:404,body:{error:'not found'}}:{status:405,body:{error:'method not allowed'}};}});await flush();
  const h=p.api.history();
  assert.equal(h.outer.status,'missing');
  await p.api.openHandle('[ap:ROOTA1]','node',{from:'outside'});await p.api.openFromPage('[ap:LINKA1]',{node:null});await h.flushOuter();await flush();
  assert.deepEqual(calls,['GET'],'nothing is written to a server that cannot keep it');
  assert.deepEqual([h.state.entries.length,h.state.cursor],[2,1]);
  assert.deepEqual([p.el('g-roots').hidden,p.el('g-roots-count').textContent],[false,'need the pane server restarted once']);
});

test('the three modes: maximize keeps only ‹ › and the document, per device and only while a document is shown; Controls stay Full or Collapsed underneath',async()=>{
  const s=memory(),p=page({storage:s,narrow:false});await flush();const root=p.win.document.documentElement.dataset;
  p.el('g-max').click();
  assert.deepEqual([root.viewMax,p.el('g-max').getAttribute('aria-pressed')],['false','true'],'nothing shown: the chooser stays in reach');
  assert.deepEqual(s.sets.at(-1),['pane_view_max_v1:page','1']);
  await p.api.openHandle('[ap:ROOTA1]','node',{from:'outside'});await flush();
  assert.equal(root.viewMax,'true');
  assert.match(p.el('g-now').textContent,/^1\/1 · Doc for /,'row 2 leads with the root name while its widget is away');
  assert.match(p.el('g-max').getAttribute('aria-label'),/^Restore/);
  p.el('g-max').click();
  assert.equal(root.viewMax,'false');assert.match(p.el('g-now').textContent,/^1\/1 · Doc for /);
  // another page on this device starts maximized once something is shown
  const q=page({storage:s,narrow:false});q.el('g-max').click();await flush();
  await q.api.openHandle('[ap:ROOTB2]','node',{from:'outside'});await flush();
  assert.equal(q.win.document.documentElement.dataset.viewMax,'true');
  // the Controls' own toggle still switches Full and Collapsed
  q.el('g-max').click();q.el('g-ctl-toggle').click();assert.equal(q.el('g-ctl-body').hidden,false);q.el('g-ctl-toggle').click();assert.equal(q.el('g-ctl-body').hidden,true);
});

test('maximize in /m: the page around takes the whole screen, asks again when the tab is shown, and after the lightbox gives it back',async()=>{
  const calls=[],immersive=[],around={opcViewMaximized:on=>calls.push(on),opcViewImmersive:on=>immersive.push(on)};
  const p=page({embedded:true,viewEmbed:'phone',around,lightbox:lightboxStub(),storage:memory()});await flush();
  await p.api.openHandle('[ap:ROOTA1]','node',{from:'outside'});await flush();await flush();
  p.log.controller?.close();calls.length=0;
  p.el('g-max').click();assert.deepEqual(calls,[true]);
  p.api.shown();assert.deepEqual(calls,[true,true],'the View tab shown again: asked again');
  p.log.onOpen(0);p.log.controller.close();
  assert.deepEqual(calls,[true,true,true],'the lightbox closed: the whole screen asked for again');
  p.el('g-max').click();assert.deepEqual(calls.at(-1),false);
  const desk=page({narrow:false,embedded:true,viewEmbed:'desktop',around,storage:memory()});await flush();
  await desk.api.openHandle('[ap:ROOTA1]','node',{from:'outside'});await flush();calls.length=0;
  desk.el('g-max').click();assert.deepEqual(calls,[],'the desktop pane maximizes inside its own frame');
});

test('Recent roots: AP-ID, UUID, chat and socket all lead with the node name, then description, then retained source; inner entries too',async()=>{
  const p=page();await flush();
  p.setReply(body=>({status:200,body:{markdown:'# Real node '+body.value.node+'\n\nA useful first line\n\nLater text',request:body,rows:[{name:'Real node '+body.value.node}]}}));
  const turn={seq:42,kind:'assistant',time:1000,session:{kind:'manager',title:'Manager-Session-227'}};
  for(const [handle,origin] of [['[ap:APNAME]',{from:'pasted'}],['3f3754e2-1111-4222-8333-444455556666',{from:'outside'}],['[ap:CHAT01]',{from:'outside',where:'Chat',turn}]]){
    await p.api.openHandle(handle,'node',origin);await p.api.history().settled();p.api.history().openRoots();await flush();
    const words=cardText(p,p.win.opcViewHistory.rootKey({handle}));
    assert.equal(words[0],'Real node '+handle);assert.equal(words[1],'A useful first line');
    assert.ok(words[2].includes(p.win.opcViewHistory.shortHandle(handle)));
    if(origin.turn)assert.match(words[2],/Manager-Session-227 · assistant/);
    assert.equal(p.el('g-root').children[0].textContent,'Real node '+handle);
    assert.match(p.el('g-now').textContent,/^1\/1 · Real node /);
  }
  await p.api.history().push('[ap:INNER1]');p.api.history().openSheet();
  const entry=p.el('g-hist-list').children[1];
  assert.equal(descendant(entry,'g-hcard-title').textContent,'Real node [ap:INNER1]');
  assert.equal(descendant(entry,'g-hcard-sub').textContent,'A useful first line');
  assert.equal(descendant(entry,'g-hcard-source').textContent,'Node · [ap:INNER1] · opened in View');
  assert.match(p.el('g-now').textContent,/^2\/2 · Real node \[ap:INNER1\] · A useful first line · Node/);
  const position={kind:'socket',total:2,cards:[{id:'seat',active:true,chain:[{role:'Lead seat',name:'old session label'}]}]};
  await p.api.openHandle('[ap:SOCKET]','node',{from:'programs',position});await p.api.history().settled();p.api.history().openRoots();await flush();
  const words=cardText(p,'node:ap:SOCKET');assert.equal(words[0],'Real node [ap:SOCKET]');assert.match(words[2],/Lead seat · 2 sessions · old session label/);
});

test('Recent roots: Dock and slice-group picks reuse their real full name and description from the catalog, never a render ancestor',async()=>{
  const roots=viewRootsFake();
  const p=page({roots,catalog:[200,{docks:[{handle:'[ap:DOCK01]',name:'The actual Dock #tag',description:'Dock description\nSecond line'}],
    groups:[{handle:'grp-1',name:'Work | The actual group',description:'Group description'}]}]});await flush();
  for(const [kind,name,description] of [['dock','The actual Dock #tag','Dock description'],['group','Work | The actual group','Group description']]){
    if(kind==='group')p.rows('group')[0].click();  // expand Work, then use the actual group picker
    const row=p.api.state.catalog[kind==='dock'?'docks':'groups'][0];
    p.pick(kind,0);
    await flush();await p.api.history().settled();p.api.history().openRoots();await flush();
    const key=p.win.opcViewHistory.rootKey(p.api.history().root()),words=cardText(p,key);
    assert.equal(words[0],name);assert.equal(words[1],description);assert.match(words[2],/picked in View/);
    assert.ok(words[2].includes(kind==='dock'?'Dock':'Slice group'));assert.ok(words[2].includes(row.handle));
  }
  assert.equal(roots.log.filter(x=>x.op==='describe').length,0,'no metadata fetch for catalog facts');
});

test('Recent roots: estate handles backfill their node name; other parcels show the telling spec; descriptions and new names filter',async()=>{
  const roots=viewRootsFake({metadata:e=>e.selection?.kind==='estate'?{name:'Grouped screenshots estate',description:'Three comparison groups'}:null});
  const p=page({roots});await flush();
  const seg=p.el('g-spec-box').querySelector('.g-seg');seg.fire('click',{target:{closest:()=>({dataset:{kind:'estate'}})}});
  p.el('g-spec').value='[ap:ESTATE1]';p.el('g-spec-render').click();await flush();await p.api.history().settled();await p.api.history().metadataSettled();
  p.api.history().openRoots();await flush();
  const key=p.win.opcViewHistory.rootKey(p.api.history().root());assert.deepEqual(cardText(p,key).slice(0,2),['Grouped screenshots estate','Three comparison groups']);
  assert.match(cardText(p,key)[2],/picked in View · Estate/);
  seg.fire('click',{target:{closest:()=>({dataset:{kind:'parcel'}})}});
  for(const text of ['{"tags":["screenshots"],"root":"25a49a99"}','{"symbols":["render","paint"],"root":"25a49a99"}','{"lens":{"kind":"merge","operation":"union","inputs":[{"group":"Views"}]}}']){
    p.el('g-spec').value=text;p.el('g-spec-render').click();await flush();await p.api.history().settled();p.api.history().openRoots();await flush();
    const words=cardText(p,p.win.opcViewHistory.rootKey(p.api.history().root()));assert.equal(words[0],text);assert.match(words[2],/picked in View · Parcel/);
  }
  for(const q of ['Grouped screenshots','Three comparison']){
    p.el('g-roots-filter').value=q;p.el('g-roots-filter').fire('input');
    assert.equal(p.el('g-roots-list').children.length,1);assert.equal(p.el('g-roots-list').children[0].dataset.key,key);
  }
});

test('256-character display caps never shorten the names/descriptions searched by Filter, including old stored descriptions',async()=>{
  const roots=viewRootsFake(),name='START '+ 'x'.repeat(150)+' NAME-NEEDLE '+ 'y'.repeat(150)+' [ap:CAP256] #tag',
    description='ABOUT '+ 'a'.repeat(135)+' DESC-NEEDLE '+ 'b'.repeat(135)+' THE END';
  roots.handle('POST',{op:'visit',key:'node:ap:CAP256',root:{id:1,root:true,handle:'[ap:CAP256]',how:'node',name,description,metadataVersion:1},device:'phone'});
  const p=page({roots});await flush();const h=p.api.history();h.openRoots();await flush();
  const words=cardText(p,'node:ap:CAP256');
  for(const text of words.slice(0,2)){assert.ok(Array.from(text).length<=256);assert.ok(text.includes('…'));}
  assert.ok(words[0].startsWith('START ')&&words[0].endsWith('[ap:CAP256] #tag'));
  assert.ok(words[1].startsWith('ABOUT ')&&words[1].endsWith('THE END'));
  assert.ok(!words[0].includes('NAME-NEEDLE')&&!words[1].includes('DESC-NEEDLE'));
  for(const query of ['NAME-NEEDLE','DESC-NEEDLE']){
    p.el('g-roots-filter').value=query;p.el('g-roots-filter').fire('input');
    assert.equal(p.el('g-roots-list').children.length,1);assert.equal(p.el('g-roots-list').children[0].dataset.key,'node:ap:CAP256');
  }
  assert.equal(h.outer.list[0].root.description,description,'not prefix-cut at the new display limit');
  assert.equal(roots.db.roots[0].entries[0].description,description,'no production/store migration required');
});

test('Recent roots: backfill old nameless entries once, in batches, persist it, and never wait for it to render a new root',async()=>{
  const roots=viewRootsFake({metadata:(e,key)=>({name:'Backfilled '+key,description:'Cached note line'})});
  for(let i=0;i<14;i++)roots.handle('POST',{op:'visit',key:'node:ap:OLD'+String(i).padStart(3,'0'),root:{id:1,handle:'[ap:OLD'+String(i).padStart(3,'0')+']',how:'node',root:true},device:'desktop'});
  const original=JSON.stringify(roots.db.roots.map(r=>({key:r.key,note:r.note,cursor:r.cursor,count:r.entries.length,visited:r.visited})));
  let release;const transport=async(method,body)=>{if(body?.op==='describe'&&!release)await new Promise(r=>release=r);return roots.handle(method,body);};
  const p=page({roots:transport});await flush();
  await p.api.openHandle('[ap:NEWONE]');assert.ok(p.api.state.held,'a new root renders while metadata is waiting');
  release();await flush();await p.api.history().settled();await p.api.history().metadataSettled();
  const batches=roots.log.filter(x=>x.op==='describe');assert.equal(batches.length,2);assert.ok(batches.every(b=>b.keys.length<=12));
  assert.ok(roots.db.roots.filter(r=>r.key!=='node:ap:NEWONE').every(r=>r.entries[0].metadataVersion===1&&r.entries[0].name.startsWith('Backfilled')));
  assert.equal(JSON.stringify(roots.db.roots.filter(r=>r.key!=='node:ap:NEWONE').map(r=>({key:r.key,note:r.note,cursor:r.cursor,count:r.entries.length,visited:r.visited}))),original);
  await p.api.history().readOuter();await p.api.history().metadataSettled();assert.equal(roots.log.filter(x=>x.op==='describe').length,2,'no repeat for resolved entries');
  await p.api.history().flushOuter();
  const q=page({roots});await flush();await q.api.history().metadataSettled();assert.equal(roots.log.filter(x=>x.op==='describe').length,2,'saved metadata survives another page');
});

test('root metadata: a broad render omitting its root note never erases a picker description; a focused child never renames the root',async()=>{
  const roots=viewRootsFake(),p=page({narrow:false,roots,catalog:[200,{docks:[{handle:'[ap:DOCK01]',name:'Project Dock',description:'The Dock description'}],groups:[]}]});await flush();
  p.setReply(body=>({status:200,body:{markdown:'# Project Dock\n\n## Dock Alpha\n\nChild body',request:body,
    rows:[{uuid:'root',name:'Project Dock'},{uuid:'child',name:'Dock Alpha'}],render_spec:{kind:'raw_root',root_uuid:'root',headings_only:false}}}));
  p.pick('dock',0);await flush();
  assert.equal(p.api.history().root().description,'The Dock description');
  p.setReply(body=>({status:200,body:{markdown:'# Dock Alpha\n\nChild body',request:body,rows:[{uuid:'child',name:'Dock Alpha'}],render_spec:{kind:'raw_root',root_uuid:'root',focus:{uuid:'child'}}}}));
  tapRow(p,1);await flush();
  assert.equal(p.api.history().root().name,'Project Dock');assert.equal(p.api.history().root().description,'The Dock description');
  assert.equal(roots.log.filter(x=>x.op==='describe').length,0);
});

test('root metadata: an unavailable backfill is fail-soft and attempted only once per page',async()=>{
  const roots=viewRootsFake();roots.handle('POST',{op:'visit',key:'node:ap:MISSING',root:{id:1,root:true,handle:'[ap:MISSING]',how:'node'}});
  const p=page({roots:async(method,body)=>body?.op==='describe'?{status:400,body:{schemaVersion:1,error:'old server'}}:roots.handle(method,body)});await flush();await p.api.history().metadataSettled();
  p.api.history().openRoots();await flush();assert.equal(p.el('g-roots-status').textContent,'');
  assert.equal(p.log.fetches.filter(([url,method])=>url==='/api/view/roots'&&method==='POST').length,1);
  assert.equal(p.el('g-roots-list').children[0].children[0].children[0].textContent,'[ap:MISSING]');
});
