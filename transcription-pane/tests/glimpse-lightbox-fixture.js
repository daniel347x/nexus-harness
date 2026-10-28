// The actual static/glimpse.html with its actual scripts and styles, the extension's actual shared renderer, its
// glimpse-lightbox.js/.css and the vendored PhotoSwipe 5, all served at /nexus-md/ from the configured NEXUS checkout by
// the fixture server's test-only alias; the gallery's images are real PNGs (fixtures/lightbox/, written by make.py) at
// /files/mnt/z/lightbox-fixture/…, as the pane's /files/ route names a file. Only fetch is replaced: the render and
// /api/files/meta replies. glimpse-lightbox.html runs it as /m's View frame (390 px, id m-glimpse-frame) with this page
// as /m around it (opcViewImmersive), touch gestures by synthetic pointer events; glimpse-lightbox-desktop.html as the
// desktop View pane (1400 px) with keys and the mouse. Two pages, so each fits the runner's virtual-time budget.
// glimpse-lightbox-live.html: a gallery of live folder/glob lines (/api/files/list answered from a table, as the pane
// lists the fixture folder) and a named estate of two galleries tapped as a handle, at 390 px then 1400 px.
// glimpse-lightbox-compare.html: side by side, not offered at 390 px in /m's frame, then at 1400 px two groups (a
// before/after note), three (the gallery), nudges, the shared zoom and pan, the lightbox over it, and an estate's live groups.
// ?shot=phone-open|phone-zoomed|phone-sheet|phone-mixed (the phone page), desktop-open|desktop-sheet (the desktop page),
// live-open|live-sheet|estate-open|estate-node|estate-desktop (the live page), compare-two|compare-three|compare-zoomed|
// compare-estate (the compare page) stops there for a Chrome screenshot.
(async()=>{
  const PART=['desktop','sheet','live','compare'].includes(document.documentElement.dataset.part)?document.documentElement.dataset.part:'phone';
  const t0=performance.now(),checks=[],record=(name,ok)=>{checks.push({name,passed:!!ok});document.getElementById('results').textContent='running… '+checks.length+' at '+Math.round(performance.now()-t0)+' ms: '+name;},check=(name,ok)=>{record(name,ok);if(!ok)throw new Error(name);};
  const soft=(name,ok)=>record(name,ok);
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async(fn,what='fixture wait')=>{for(let i=0;i<800;i++){try{if(fn())return true;}catch(_){}await sleep(5);}throw new Error(what+' did not settle');};
  const ROOT='/mnt/z/lightbox-fixture/';
  const gallery=['# [ap:PZNQ9J] 🖼️ GALLERY · fixture bake-off (10 images, one missing)','',
    'One gallery node, three groups (one heading per candidate). Each line is a plain Markdown image with its absolute path.','',
    '## Opus · candidate O · worktree `wt-ui-opus`',
    '![card · light]('+ROOT+'o1.png)','![card · dark]('+ROOT+'o2.png "the dark theme")','![edit · light](Z:/lightbox-fixture/o3.png)','![empty · light](file://'+ROOT+'o4.png)','',
    '## Fable · candidate F','![f · one]('+ROOT+'f1.png)','![f · two]('+ROOT+'f2.png)','![gone]('+ROOT+'nope.png)','',
    '## Astra · candidate A','![wide]('+ROOT+'a1.png)','![narrow · dark]('+ROOT+'a2.png)','![narrow · light]('+ROOT+'a%20space/a3.png)'].join('\n');
  const mixed=['# Notes with a few images','',
    'This is a real document: '+'it talks about the screenshots at some length, the way a design review does, before and after showing them. '.repeat(5),'',
    '![inline one]('+ROOT+'o1.png) sits inside a sentence.','',
    '![shots folder]('+ROOT+'empty/)','![shots glob]('+ROOT+'x*.png)','',
    '![pair a]('+ROOT+'f1.png)','![pair b]('+ROOT+'f2.png)'].join('\n');
  // Live lines: globs, a folder written with a drive letter and %20, a fixed line mixed in, an empty folder, a missing one
  const live=['# [ap:LIVE01] ⚗️ live gallery · fixture','','Each line names a folder or a glob; View lists it when it renders.','',
    '## Opus','![Opus]('+ROOT+'o*.png)','','## Fable','![]('+ROOT+'f?.png)','![wide one]('+ROOT+'a1.png)','',
    '## Astra','![Astra](Z:/lightbox-fixture/a%20space/)','','## Nothing there','![empty]('+ROOT+'empty/)','![gone]('+ROOT+'nope/)'].join('\n');
  // A before/after note: two groups, with prose enough that it does not open by itself
  const pair=['# [ap:PAIR01] A UI fix, before and after','','The card and the edit screen, before the fix and after it. '+
    'The review walks through what changed and why, screen by screen, the way a design review does it. '.repeat(4),'',
    '## Before','![card before]('+ROOT+'o1.png)','![edit before]('+ROOT+'o3.png)','','## After','![card after]('+ROOT+'o2.png)','![edit after]('+ROOT+'o4.png)'].join('\n');
  const SIZES={'o1.png':[1500,950],'o2.png':[1500,950],'o3.png':[1500,950],'o4.png':[1500,950],'f1.png':[1500,950],'f2.png':[1500,950],
    'a1.png':[1500,950],'a2.png':[600,760],'a space/a3.png':[600,760]};
  // What the pane's /api/files/list says of each live path (its natural name order), by path relative to ROOT
  const LISTS={'o*.png':['o1.png','o2.png','o3.png','o4.png'],'f?.png':['f1.png','f2.png'],'a space/':['a space/a3.png'],'empty/':[],'x*.png':[],
    'nope/':{error:'no such folder: '+ROOT+'nope/'}};
  // A named estate of two galleries (recipe B): its Node read is two parcel lines; the estate is the daemon's assembled document
  const ESTATE_UUID='e57a7e00-0000-4000-8000-0000000000e5',ESTATE_NAME='[ap:ESTAT1] 🏛️ two galleries (recipe B)';
  const scope=uuid=>'> render_scope: {"kind":"lightning_rod","root_uuid":"'+uuid+'","selection_kind":"symbols"}';
  const estate=['# 🏛️ ESTATE · '+ESTATE_NAME+' · handle '+ESTATE_UUID+' · 2 entries · 2 rendered','','# 📄 [1] [ap:PZNQ9J]','',scope('97828139'),'',gallery,'','---','',
    '# 📄 [2] [ap:LIVE01]','',scope('78c69f00'),'',live].join('\n');
  const fx=window.lightboxFixture={requests:[],renders:[],meta:[],lists:[],immersive:[],errors:[],catalogReads:0,metaReply:null,
    markdown:body=>{const v=JSON.stringify(body.value);return body.kind==='estate'?estate:v.includes('ESTAT1')?'# '+ESTATE_NAME+'\n\n{"node": "[ap:PZNQ9J]"}\n{"node": "[ap:LIVE01]"}':
      v.includes('MIXED')?mixed:v.includes('LIVE01')?live:v.includes('PAIR01')?pair:gallery;},
    rows:body=>body.kind==='parcel'&&JSON.stringify(body.value).includes('ESTAT1')?[{uuid:ESTATE_UUID,depth:0,name:ESTATE_NAME,ancestor_path:[],occurrence:0}]:null};
  // The page around the frame: /m's opcViewImmersive (its setMaximized('glimpse')), and its glare follower.
  window.opcViewImmersive=on=>fx.immersive.push(on);
  window.opcGlare=Object.freeze({on:()=>false});
  const mock=`
    const fx=parent.lightboxFixture;
    addEventListener('error',e=>fx.errors.push(String(e.message||e.target?.src||'resource error')),true);
    addEventListener('unhandledrejection',e=>fx.errors.push('rejection: '+String(e.reason?.message||e.reason)));
    // Headless Chrome under a virtual-time budget runs no animation frames in this frame; PhotoSwipe moves its slides,
    // zooms and springs in requestAnimationFrame, so here a frame is a 16 ms timer (fixture only).
    window.requestAnimationFrame=cb=>setTimeout(()=>cb(performance.now()),16);window.cancelAnimationFrame=id=>clearTimeout(id);
    // Reduced motion (the lightbox then opens, closes and zooms without animating), so each page fits its time budget.
    const realMatch=window.matchMedia.bind(window);
    window.matchMedia=query=>/prefers-reduced-motion:\s*reduce/.test(query)?{matches:true,media:query,onchange:null,addEventListener(){},removeEventListener(){},addListener(){},removeListener(){},dispatchEvent:()=>false}:realMatch(query);
    Object.defineProperty(window,'localStorage',{configurable:true,value:{get length(){return 0;},key:()=>null,getItem:()=>null,setItem:()=>{},removeItem:()=>{}}});
    const realFetch=window.fetch.bind(window),json=(status,body)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
    window.fetch=async(url,options={})=>{
      const u=new URL(String(url),parent.location.href);fx.requests.push([u.pathname,options.method||'GET']);
      if(u.pathname.startsWith('/nexus-md/'))return realFetch(url,options);
      if(u.pathname==='/api/glimpse/catalog'){fx.catalogReads++;return json(200,{schemaVersion:1,docks:[],groups:[],errors:[],readAt:'2026-10-24T07:00:00Z'});}
      if(u.pathname==='/api/glimpse/render'){const body=JSON.parse(options.body);fx.renders.push(body);const markdown=fx.markdown(body),rows=fx.rows(body);
        return json(200,{schemaVersion:1,markdown,bytes:new TextEncoder().encode(markdown).length,renderedAt:'2026-10-24T07:42:10Z',request:body,...(rows?{rows}:{})});}
      if(u.pathname==='/api/files/list'){const body=JSON.parse(options.body);fx.lists.push(body);
        const lists=${JSON.stringify(LISTS)},sizes=${JSON.stringify(SIZES)},root=${JSON.stringify(ROOT)};
        return json(200,{schemaVersion:1,lists:body.paths.map(path=>{const got=lists[path.slice(root.length)],kind=path.endsWith('/')?'folder':'glob';
          if(!got)return {path,files:[],total:0,error:'no such folder: '+path};if(got.error)return {path,files:[],total:0,error:got.error};
          return {path,kind,total:got.length,files:got.map(n=>({path:root+n,url:'/files'+encodeURI(root+n),exists:true,bytes:1,mtime:1,type:'image/png',width:sizes[n][0],height:sizes[n][1]}))};})});}
      if(u.pathname==='/api/files/meta'){const body=JSON.parse(options.body);fx.meta.push(body);if(fx.metaReply)return fx.metaReply(body);
        const sizes=${JSON.stringify(SIZES)},root=${JSON.stringify(ROOT)};
        return json(200,{schemaVersion:1,files:body.paths.map(path=>{const size=sizes[path.slice(root.length)];
          return size?{path,url:'/files'+encodeURI(path),exists:true,bytes:1,mtime:1,type:'image/png',width:size[0],height:size[1]}:{path,url:'/files'+encodeURI(path),exists:false,error:'no such file'};})});}
      throw new Error('No real transport permitted: '+url);
    };`;
  const SHOT=new URLSearchParams(location.search).get('shot'),shot=name=>{if(SHOT===name)throw Object.assign(new Error('stopped for the '+name+' screenshot'),{shot:true});};
  let w,d,api;
  try {
    const base=new URL('../static/',location.href),html=await (await fetch(new URL('glimpse.html',base))).text();
    const safe=t=>t.replace(/<\/script/gi,'<\\/script');
    const page=()=>html.replace('<script>',()=>'<script>'+safe(mock)+'<\/script>\n  <script>')
      .replace(/(src|href)="\/((?:glimpse|glare|handle-links|view-history|view-pickers)\.(?:js|css))"/g,(_,a,f)=>a+'="/transcription-pane/static/'+f+'"');
    const frame=document.querySelector('iframe');
    const load=async()=>{const loaded=new Promise(r=>frame.onload=r);frame.srcdoc=page();await loaded;w=frame.contentWindow;d=w.document;
      api=w.opcGlimpseView;await until(()=>api&&fx.catalogReads>0&&api.state.catalog,'catalog');};
    const g=id=>d.getElementById('g-'+id);
    const box=()=>api.state.lightbox,ps=()=>api.state.lightbox?.pswp;
    const opened=()=>until(()=>box()?.isOpen()&&ps().opener?.isOpen&&!ps().opener.isOpening,'lightbox open');
    const closed=()=>until(()=>!box()?.isOpen()&&!d.querySelector('.pswp'),'lightbox closed');
    const q=sel=>d.querySelector('.pswp '+sel);
    const pos=()=>q('.glimpse-lb-pos')?.textContent,caption=()=>q('.glimpse-lb-caption')?.textContent;
    const chipOn=()=>[...d.querySelectorAll('.glimpse-lb-chip')].findIndex(c=>c.getAttribute('aria-current')==='true');
    const slideImg=()=>ps().currSlide?.content?.element;
    const zoom=()=>ps().currSlide.currZoomLevel,levels=()=>ps().currSlide.zoomLevels;
    // PhotoSwipe's animations (slide, zoom, spring) all finished, and the main scroll back on a slide
    const settle=async()=>{await sleep(30);if(!ps())return;await until(()=>!ps().animations.activeAnimations.length&&!ps().mainScroll.isShifted(),'animations');await sleep(10);};
    // Synthetic touch pointers on what is under them, as a finger's (PhotoSwipe reads pointer events; it needs no trusted ones)
    const target=(x,y)=>d.elementFromPoint(x,y)||d.querySelector('.pswp__scroll-wrap');
    const pointer=(type,x,y,id,el)=>el.dispatchEvent(new w.PointerEvent(type,{bubbles:true,cancelable:true,composed:true,pointerId:id,pointerType:'touch',
      isPrimary:id===1,clientX:x,clientY:y,button:type==='pointermove'?-1:0,buttons:type==='pointerup'?0:1,width:20,height:20,pressure:type==='pointerup'?0:.5}));
    async function swipe(dx,y=420){const x0=dx<0?290:100,el=target(x0,y);pointer('pointerdown',x0,y,1,el);
      for(let i=1;i<=8;i++){await sleep(12);pointer('pointermove',x0+dx*i/8,y,1,el);}await sleep(12);pointer('pointerup',x0+dx,y,1,el);await settle();}
    async function tap(x,y){const el=target(x,y);pointer('pointerdown',x,y,1,el);await sleep(40);pointer('pointerup',x,y,1,el);}
    async function doubleTap(x,y){await tap(x,y);await sleep(90);await tap(x,y);await settle();}
    async function pinch(x,y,spread){const el=target(x,y);pointer('pointerdown',x-40,y,1,el);pointer('pointerdown',x+40,y,2,el);
      for(let i=1;i<=8;i++){await sleep(12);pointer('pointermove',x-40-spread*i/8,y,1,el);pointer('pointermove',x+40+spread*i/8,y,2,el);}
      await sleep(12);pointer('pointerup',x+40+spread,y,2,el);pointer('pointerup',x-40-spread,y,1,el);await settle();}
    const key=k=>d.dispatchEvent(new w.KeyboardEvent('keydown',{key:k,bubbles:true,cancelable:true}));
    const rawRequests=()=>w.performance.getEntriesByType('resource').map(e=>new URL(e.name).pathname).filter(p=>p.startsWith('/mnt/')||p.startsWith('/Z:')||p.startsWith('/z:'));

    if(PART==='phone'){
      // ================= /m's View frame, 390 px: Dan taps the gallery's handle in a Manager's chat
      await load();
      check('the phone embed, with the lightbox module and PhotoSwipe loaded beside the shared renderer',d.documentElement.dataset.viewEmbed==='phone'&&
        typeof w.GlimpseLightbox?.open==='function'&&typeof w.PhotoSwipe==='function'&&!w.glimpseLightboxFailures.length&&!w.glimpseAssetFailures.length);
      api.openHandle('[ap:PZNQ9J]','node',{from:'outside',where:'Chat'});
      await opened();
      check('an image-only document opens straight into the lightbox, at its first image',ps().currIndex===0&&d.querySelector('.pswp.glimpse-lb.pswp--open'));
      check('it asked /m for the whole screen (opcViewImmersive true)',JSON.stringify(fx.immersive)==='[true]');
      check('one sizes read for the document\'s local paths, each once, as WSL paths (Z:/ and file:// mapped)',fx.meta.length===1&&
        JSON.stringify(fx.meta[0].paths)===JSON.stringify(['o1.png','o2.png','o3.png','o4.png','f1.png','f2.png','nope.png','a1.png','a2.png','a space/a3.png'].map(n=>ROOT+n)));
      check('three group chips with their counts, Opus current; position within the group',
        [...d.querySelectorAll('.glimpse-lb-chip')].map(c=>c.textContent).join('|')==='Opus 4|Fable 3|Astra 3'&&chipOn()===0&&pos()==='1 / 4');
      check('caption, file name and the whole heading on the caption lines',caption()==='card · light'&&
        q('.glimpse-lb-file').textContent==='o1.png · Opus · candidate O · worktree wt-ui-opus'&&q('.glimpse-lb-title').hidden);
      check('the filmstrip holds the group\'s four thumbnails, the first current',d.querySelectorAll('.glimpse-lb-thumb').length===4&&
        d.querySelector('.glimpse-lb-thumb[aria-current="true"]')?.dataset.index==='0');
      await until(()=>slideImg()?.complete&&slideImg().naturalWidth===1500,'first image loaded');
      check('the slide shows the file through /files/, at the size the meta reply gave (1500×950)',new URL(slideImg().src).pathname==='/files'+ROOT+'o1.png'&&
        ps().currSlide.width===1500&&ps().currSlide.height===950);
      check('map before mount: no request ever went to a raw local path',!rawRequests().length);
      check('Back has one history entry to close',w.history.state?.glimpseLightbox);
      shot('phone-open');
      // swipe through Opus into Fable
      await swipe(-260);
      check('a swipe left shows the next image: 2 / 4, its caption and title line',ps().currIndex===1&&pos()==='2 / 4'&&caption()==='card · dark'&&
        q('.glimpse-lb-title').textContent==='the dark theme'&&!q('.glimpse-lb-title').hidden);
      await swipe(-260);await swipe(-260);
      check('… the Z:/ and file:// images too',ps().currIndex===3&&pos()==='4 / 4'&&new URL(slideImg().src).pathname==='/files'+ROOT+'o4.png');
      await swipe(-260);
      check('past the group\'s last image it goes on into Fable: its chip lit, 1 / 3, its own filmstrip',ps().currIndex===4&&chipOn()===1&&pos()==='1 / 3'&&
        d.querySelectorAll('.glimpse-lb-thumb').length===3);
      await swipe(260);
      check('a swipe right goes back into Opus',ps().currIndex===3&&chipOn()===0);
      await swipe(-260);
      // pinch into a detail, double-tap back to fit and in again
      await until(()=>slideImg()?.complete,'f1 loaded');
      const fit=levels().initial;
      await pinch(195,420,90);
      check('a pinch zooms in ('+zoom().toFixed(2)+' from fit '+fit.toFixed(2)+')',zoom()>fit*1.8);
      shot('phone-zoomed');
      await swipe(-120);
      check('while zoomed a drag pans the image instead of swiping',ps().currIndex===4);
      await doubleTap(195,420);
      check('a double-tap at zoom goes back to fit',Math.abs(zoom()-fit)<1e-3);
      await doubleTap(120,400);
      check('a double-tap at fit zooms in at the tapped point',zoom()>fit*1.5);
      await doubleTap(120,400);
      // a tap hides and shows the chrome
      await tap(195,420);await sleep(340);await settle();
      check('a tap hides the chrome for an unobstructed look (and the fit grows into its room)',!d.querySelector('.pswp').classList.contains('pswp--ui-visible')&&levels().initial>=fit);
      await tap(195,420);await sleep(340);await settle();
      check('… and a tap shows it again',d.querySelector('.pswp').classList.contains('pswp--ui-visible'));
      await swipe(-260);await swipe(-260);
      check('a missing file is its own slide, saying so with its path',ps().currIndex===6&&q('.pswp__item .glimpse-lb-gone')?.textContent.includes('No such file')&&
        [...d.querySelectorAll('.pswp .glimpse-lb-gone code')].some(c=>c.textContent===ROOT+'nope.png'));
      // tap Astra, then a thumbnail
      d.querySelectorAll('.glimpse-lb-chip')[2].click();await settle();
      check('tapping Astra jumps to its first image: 1 / 3',ps().currIndex===7&&chipOn()===2&&pos()==='1 / 3');
      d.querySelector('.glimpse-lb-thumb[data-index="9"]').click();await settle();
      await until(()=>slideImg()?.complete&&slideImg().naturalWidth===600,'a3 loaded');
      check('a filmstrip tap jumps there; a path with a space (%20) loads, a portrait 600×760',ps().currIndex===9&&pos()==='3 / 3'&&
        new URL(slideImg().src).pathname==='/files'+ROOT+'a%20space/a3.png');
      // Back lands on the contact sheet (glimpse-lightbox-sheet.html goes on from there)
      w.history.back();
      await closed();
      check('Back closes it and gives /m its screen back',JSON.stringify(fx.immersive)==='[true,false]'&&!w.history.state?.glimpseLightbox);
    }
    if(PART==='sheet'){
      await load();
      api.openHandle('[ap:PZNQ9J]','node',{from:'outside',where:'Chat'});
      await opened();
      check('the gallery opened by itself on the phone',ps().currIndex===0&&JSON.stringify(fx.immersive)==='[true]');
      w.history.back();
      await closed();
      check('Back closes it and gives /m its screen back',JSON.stringify(fx.immersive)==='[true,false]'&&!w.history.state?.glimpseLightbox);
      const sheets=g('doc').querySelectorAll('.glimpse-lb-sheet'),tiles=g('doc').querySelectorAll('.glimpse-lb-tile');
      check('… onto the contact sheet: three grids, ten tiles with their captions',sheets.length===3&&tiles.length===10&&tiles[1].querySelector('figcaption').textContent==='card · dark');
      const cols=new Set([...sheets[0].querySelectorAll('.glimpse-lb-tile')].map(t=>Math.round(t.getBoundingClientRect().left))).size;
      check('two tiles across a phone held upright',cols===2);
      const gone=g('doc').querySelector('.glimpse-lb-tile[data-missing="true"]');
      check('the missing file is a dim labelled tile with its path, not a broken icon',gone&&gone.querySelector('.glimpse-lb-note').textContent.includes(ROOT+'nope.png')&&
        w.getComputedStyle(gone.querySelector('img')).display==='none');
      check('the chip says what the lightbox holds',!g('images').hidden&&g('images-text').textContent==='10 images · 3 groups');
      await until(()=>[...tiles].filter(t=>!t.dataset.missing).every(t=>t.querySelector('img').complete),'tiles loaded');
      check('every tile loaded through /files/, no raw path requested',[...tiles].filter(t=>!t.dataset.missing).every(t=>t.querySelector('img').naturalWidth>0&&new URL(t.querySelector('img').src).pathname.startsWith('/files/'))&&!rawRequests().length);
      shot('phone-sheet');
      // a tap on a tile opens it there; Esc closes; a fast reopen keeps its own Back
      tiles[8].click();await opened();
      check('a tap on a tile opens the lightbox at that image',ps().currIndex===8&&caption()==='narrow · dark'&&fx.immersive.at(-1)===true);
      key('Escape');await closed();
      tiles[0].click();await opened();
      await sleep(300);
      check('reopened at once after a close, it stays open (the earlier Back does not close it) and has its own entry',box().isOpen()&&w.history.state?.glimpseLightbox);
      w.history.back();await closed();
      gone.click();await sleep(300);
      check('a missing tile opens nothing',!box()?.isOpen()&&!d.querySelector('.pswp'));
      g('images-open').click();await opened();
      check('the chip opens it at the first image',ps().currIndex===0);
      w.history.back();await closed();
      // Refresh keeps the contact sheet; a document with images and prose never opens by itself
      const opensBefore=fx.immersive.length;
      g('refresh').click();await until(()=>!api.state.busy,'refresh');await sleep(400);
      check('Refresh of the gallery stays on its contact sheet',fx.immersive.length===opensBefore&&!d.querySelector('.pswp')&&fx.meta.length>=2);
      api.openHandle('[ap:MIXED1]','node',{from:'outside'});await until(()=>api.state.held?.label.includes('MIXED1')&&!api.state.busy,'mixed');await sleep(400);
      check('a document with prose and images does not open by itself; its chip does',!d.querySelector('.pswp')&&g('images-text').textContent==='3 images');
      const liveTiles=[...g('doc').querySelectorAll('.glimpse-lb-tile[data-live]')];
      check('an empty folder and a glob with no match are labelled tiles saying so, never silently nothing, never requested, never in the lightbox',liveTiles.length===2&&
        liveTiles[0].querySelector('.glimpse-lb-note').textContent==='📁 no images in this folder · '+ROOT+'empty/'&&
        liveTiles[1].querySelector('.glimpse-lb-note').textContent==='🔎 no image matches · '+ROOT+'x*.png'&&
        liveTiles.every(t=>t.dataset.liveState==='empty'&&!t.querySelector('img').getAttribute('src'))&&!rawRequests().length&&
        JSON.stringify(fx.lists.at(-1)?.paths)===JSON.stringify([ROOT+'empty/',ROOT+'x*.png']));
      const inline=g('doc').querySelector('p > img[data-glimpse-lb]');
      check('an image inside a sentence stays inline, and opens the lightbox when tapped',inline&&!inline.closest('.glimpse-lb-tile'));
      shot('phone-mixed');
      inline.click();await opened();
      check('… at its own place',ps().currIndex===0&&caption()==='inline one');
      key('Escape');await closed();
      check('Source shows no images and no chip; Rendered brings them back',(g('source').click(),g('images').hidden&&!g('doc').querySelector('img'))&&
        (g('rendered').click(),!g('images').hidden));
      // a pane server not yet restarted: no /api/files/meta (404) — the lightbox still opens, measuring each image itself
      fx.metaReply=()=>new Response(JSON.stringify({error:'not found'}),{status:404,headers:{'Content-Type':'application/json'}});
      api.state.imageMeta.clear();
      api.openHandle('[ap:PZNQ9J]','node',{from:'outside'});await opened();
      await until(()=>ps().currSlide?.width===1500,'measured size');
      check('without a meta reply the gallery still opens; each slide is measured once its image loads',ps().currIndex===0&&ps().currSlide.height===950);
      w.history.back();await closed();
      fx.metaReply=null;
      soft('no page error ['+fx.errors.filter(e=>!/nope\.png|resource error/.test(e)).join('; ')+']',!fx.errors.filter(e=>!/nope\.png|resource error/.test(e)).length);

    }
    if(PART==='desktop'){
      // ================= the desktop View pane, 1400 px: keys, 1:1, ⛶, a click outside the image
      frame.id='desktop-view-frame';frame.style.width='1400px';frame.style.height='900px';
      delete window.opcViewImmersive;  // the desktop page around View (sessions.html, index.html) has no immersive bridge
      await load();
      check('the desktop embed',d.documentElement.dataset.viewEmbed==='desktop');
      api.openHandle('[ap:PZNQ9J]','node',{from:'outside'});await opened();
      await until(()=>slideImg()?.complete,'desktop first image');
      check('the gallery opens straight away on the desktop too, over the View pane',ps().currIndex===0&&d.querySelector('.pswp--open')&&!fx.immersive.length);
      {const img=slideImg().getBoundingClientRect(),bar=d.querySelector('.pswp__top-bar').getBoundingClientRect(),foot=q('.glimpse-lb-foot').getBoundingClientRect();
        check('the fitted image clears the top bar and the caption with its filmstrip ('+Math.round(img.top)+'–'+Math.round(img.bottom)+' between '+Math.round(bar.bottom)+' and '+Math.round(foot.top)+')',
          img.top>=bar.bottom-1&&img.bottom<=foot.top+1&&img.height>500);}
      shot('desktop-open');
      key('ArrowRight');await settle();
      check('→ next',ps().currIndex===1);
      key('PageDown');await settle();
      check('PageDown: the next group',ps().currIndex===4&&chipOn()===1);
      key('End');await settle();
      check('End: the group\'s last image',ps().currIndex===6);
      key('Home');await settle();
      check('Home: the group\'s first',ps().currIndex===4);
      key('ArrowUp');await settle();
      check('↑ (at fit): the previous group',ps().currIndex===0&&chipOn()===0);
      key('ArrowDown');await settle();
      check('↓ (at fit): the next group',ps().currIndex===4);
      await until(()=>slideImg()?.complete,'f1 desktop');
      key('1');await settle();
      check('1: actual pixels',Math.abs(zoom()-1)<1e-3);
      key('ArrowDown');await settle();
      check('… where ↓ pans instead of changing group',ps().currIndex===4);
      key('0');await settle();
      check('0: back to fit',Math.abs(zoom()-levels().initial)<1e-3);
      key('+');await settle();
      check('+ zooms in',zoom()>levels().initial*1.2);
      key('-');key('-');await settle();
      check('− zooms back out, never past fit',Math.abs(zoom()-levels().initial)<1e-3);
      const fitShown=levels().initial;
      key(' ');await settle();
      check('Space hides the chrome; the image takes its room ('+fitShown.toFixed(3)+' → '+levels().initial.toFixed(3)+')',
        !d.querySelector('.pswp').classList.contains('pswp--ui-visible')&&levels().initial>fitShown);
      key(' ');await settle();
      check('… and shows it again',d.querySelector('.pswp').classList.contains('pswp--ui-visible')&&Math.abs(levels().initial-fitShown)<1e-3);
      check('⛶ (full screen) is offered where the frame allows it, beside 1:1',!!q('.pswp__button--glimpse-full')===!!d.fullscreenEnabled&&!!q('.pswp__button--glimpse-actual'));
      q('.pswp__button--glimpse-actual').click();await settle();
      check('the 1:1 button: actual pixels',Math.abs(zoom()-1)<1e-3);
      key('0');await settle();
      // a click outside the image closes
      const r=slideImg().getBoundingClientRect(),bx=Math.max(90,Math.round(r.left-25)),by=Math.round(r.top+r.height/2),bg=d.elementFromPoint(bx,by);
      const mouse=(type,x,y,el)=>el.dispatchEvent(new w.PointerEvent(type,{bubbles:true,cancelable:true,pointerId:7,pointerType:'mouse',isPrimary:true,clientX:x,clientY:y,button:0,buttons:type==='pointerup'?0:1}));
      check('beside the fitted image is the slide\'s background ('+(bg?.className||'nothing')+')',bg?.classList.contains('pswp__item')||bg?.classList.contains('pswp__zoom-wrap'));
      mouse('pointerdown',bx,by,bg);await sleep(30);mouse('pointerup',bx,by,bg);await closed();
      check('a click outside the image closes it',!d.querySelector('.pswp')&&!w.history.state?.glimpseLightbox);
      g('images-open').click();await opened();
      key('Escape');await closed();
      check('Esc closes it',!d.querySelector('.pswp'));
      const desktopCols=new Set([...g('doc').querySelector('.glimpse-lb-sheet').querySelectorAll('.glimpse-lb-tile')].map(t=>Math.round(t.getBoundingClientRect().left))).size;
      check('the desktop contact sheet sets the tiles about 220 px wide, several across',desktopCols===4);
      shot('desktop-sheet');
      soft('no page error on the desktop ['+fx.errors.filter(e=>!/nope\.png|resource error/.test(e)).join('; ')+']',!fx.errors.filter(e=>!/nope\.png|resource error/.test(e)).length);
    }
    if(PART==='live'){
      const chips=()=>[...d.querySelectorAll('.glimpse-lb-chip')].map(c=>c.textContent).join('|');
      // ================= /m's View frame, 390 px: an agent handed Dan a gallery of live folder/glob lines
      await load();
      api.openHandle('[ap:LIVE01]','node',{from:'outside',where:'Chat'});
      await opened();
      check('one listing for the document\'s five live lines, in document order (Z:/ and %20 as the WSL path)',fx.lists.length===1&&
        JSON.stringify(fx.lists[0].paths)===JSON.stringify([ROOT+'o*.png',ROOT+'f?.png',ROOT+'a space/',ROOT+'empty/',ROOT+'nope/']));
      check('a gallery of live lines opens straight into the lightbox once they are listed',ps().currIndex===0&&JSON.stringify(fx.immersive)==='[true]');
      check('the groups: Opus (its line\'s alt text), Fable (no alt: the heading\'s, the fixed line mixed in), Astra',chips()==='Opus 4|Fable 3|Astra 1'&&pos()==='1 / 4');
      check('each listed file captioned by its name, the group on the file line',caption()==='o1'&&q('.glimpse-lb-file').textContent==='o1.png · Opus');
      check('only the fixed line\'s size was read: the listing sized the rest',fx.meta.length===1&&JSON.stringify(fx.meta[0].paths)===JSON.stringify([ROOT+'a1.png']));
      await until(()=>slideImg()?.complete&&slideImg().naturalWidth===1500,'live first image');
      check('the listed file shows through /files/, no raw path requested',new URL(slideImg().src).pathname==='/files'+ROOT+'o1.png'&&!rawRequests().length);
      shot('live-open');
      for(let i=0;i<4;i++)await swipe(-260);
      check('swiping on past Opus: Fable\'s glob, then its fixed line',ps().currIndex===4&&chipOn()===1&&pos()==='1 / 3'&&caption()==='f1');
      await swipe(-260);await swipe(-260);
      check('… the fixed line mixed in among the listed files',ps().currIndex===6&&caption()==='wide one');
      await swipe(-260);
      await until(()=>slideImg()?.complete&&slideImg().naturalWidth===600,'a3 loaded');
      check('Astra: the folder written as Z:/…/a%20space/, a portrait 600×760 sized by its listing',ps().currIndex===7&&chipOn()===2&&
        ps().currSlide.width===600&&ps().currSlide.height===760&&new URL(slideImg().src).pathname==='/files'+ROOT+'a%20space/a3.png');
      w.history.back();await closed();
      const tiles=[...g('doc').querySelectorAll('.glimpse-lb-tile')],notes=[...g('doc').querySelectorAll('.glimpse-lb-tile[data-live] .glimpse-lb-note')].map(n=>n.textContent);
      check('Back lands on the contact sheet: the listed files as tiles under their headings (a folder of one file too)',tiles.length===10&&
        tiles[0].querySelector('figcaption').textContent==='o1'&&tiles[7].querySelector('figcaption').textContent==='a3'&&
        g('doc').querySelectorAll('.glimpse-lb-sheet').length===4&&g('images-text').textContent==='8 images · 3 groups');
      check('an empty folder and a missing one are labelled tiles saying so',JSON.stringify(notes)===JSON.stringify(['📁 no images in this folder · '+ROOT+'empty/','⚠ no such folder: '+ROOT+'nope/']));
      shot('live-sheet');
      // Refresh lists again: a folder an agent has just added to (the same files here: nothing moves, nothing reopens)
      const opens=fx.immersive.length;
      g('refresh').click();await until(()=>!api.state.busy&&fx.lists.length===2,'relist');await sleep(300);
      check('Refresh lists the live lines again and stays on the contact sheet',fx.immersive.length===opens&&!d.querySelector('.pswp')&&
        g('doc').querySelectorAll('.glimpse-lb-tile').length===10);
      // ================= a named estate of two galleries, tapped as a handle (recipe B)
      api.openHandle('[ap:ESTAT1]','node',{from:'outside',where:'Chat'});
      await opened();
      check('the handle is read as Node, then rendered as its estate: each parcel its own read (no headings_only), by the node\'s uuid',
        JSON.stringify(fx.renders.slice(-2).map(r=>[r.kind,r.value,r.options]))===JSON.stringify([['parcel',{node:'[ap:ESTAT1]'},{headings_only:false,include_rows:true}],['estate',ESTATE_UUID,{}]]));
      check('an estate that is all galleries opens straight away, its galleries\' groups in one sequence',chips()==='Opus 4|Fable 3|Astra 3|Opus 4|Fable 3|Astra 1'&&ps().currIndex===0);
      shot('estate-open');
      d.querySelectorAll('.glimpse-lb-chip')[3].click();await settle();
      check('… the second gallery\'s live Opus too',ps().currIndex===10&&caption()==='o1'&&chipOn()===3);
      w.history.back();await closed();
      check('labelled Estate · ‹name›, with one tap to see the node itself',!g('estate').hidden&&g('estate-text').textContent==='🏛️ Estate · '+ESTATE_NAME+' · 2 parcels'&&
        g('estate-toggle').textContent==='Show the node'&&api.state.held.label==='Estate · '+ESTATE_NAME&&g('images-text').textContent==='18 images · 6 groups');
      g('estate-toggle').click();await until(()=>!api.state.busy&&api.state.held?.selection?.raw,'the node');await sleep(200);
      check('the node itself: its two parcel lines as written, and one tap back',g('doc').textContent.includes('{"node": "[ap:PZNQ9J]"}')&&!d.querySelector('.pswp')&&
        g('estate-text').textContent==='🏛️ This node is a named estate (its note lists 2 parcels)'&&g('estate-toggle').textContent==='Show the estate');
      shot('estate-node');
      g('estate-toggle').click();await opened();
      check('… back to the estate (a new document: its gallery opens again)',api.state.held.selection.kind==='estate'&&fx.renders.at(-1).kind==='estate');
      key('Escape');await closed();
      // ================= the desktop View pane, 1400 px: the same estate, keys
      frame.id='desktop-view-frame';frame.style.width='1400px';frame.style.height='900px';
      delete window.opcViewImmersive;
      await load();
      check('the desktop embed',d.documentElement.dataset.viewEmbed==='desktop');
      api.openHandle('[ap:ESTAT1]','node',{from:'outside'});await opened();
      check('on the desktop the tapped estate opens straight away too, no headings_only sent',fx.renders.at(-1).kind==='estate'&&JSON.stringify(fx.renders.at(-1).options)==='{}'&&
        chips()==='Opus 4|Fable 3|Astra 3|Opus 4|Fable 3|Astra 1');
      for(let i=0;i<3;i++){key('PageDown');await settle();}
      check('PageDown steps group by group into the second gallery\'s live lines',ps().currIndex===10&&chipOn()===3);
      await until(()=>slideImg()?.complete,'desktop live image');
      shot('estate-desktop');
      key('Escape');await closed();
      const cols=new Set([...g('doc').querySelectorAll('.glimpse-lb-sheet')[3].querySelectorAll('.glimpse-lb-tile')].map(t=>Math.round(t.getBoundingClientRect().left))).size;
      check('the desktop contact sheet of the live Opus, several across; the estate line with its one tap',cols===4&&!g('estate').hidden&&g('estate-toggle').textContent==='Show the node');
      soft('no page error ['+fx.errors.filter(e=>!/nope\.png|resource error/.test(e)).join('; ')+']',!fx.errors.filter(e=>!/nope\.png|resource error/.test(e)).length);
    }
    if(PART==='compare'){
      const cmp=()=>api.state.compare,root=()=>d.querySelector('.glimpse-cmp'),panels=()=>[...d.querySelectorAll('.glimpse-cmp-panel')];
      const heads=()=>panels().map(p=>p.querySelector('.glimpse-cmp-name').textContent+' '+p.querySelector('.glimpse-cmp-pos').textContent).join(' | ');
      const captions=()=>panels().map(p=>p.querySelector('.glimpse-cmp-caption').textContent).join(' | ');
      const pressed=()=>[...d.querySelectorAll('.glimpse-cmp-chip')].map(c=>c.getAttribute('aria-pressed')==='true'?c.textContent:'·').join('|');
      const imgOf=k=>panels()[k].querySelector('.glimpse-cmp-img'),viewOf=k=>panels()[k].querySelector('.glimpse-cmp-view');
      const loaded=()=>until(()=>panels().every(p=>{const img=p.querySelector('.glimpse-cmp-img');return img.hidden||img.complete&&img.naturalWidth>0&&img.style.visibility!=='hidden';}),'panels loaded');
      const box=k=>{const r=imgOf(k).getBoundingClientRect(),v=viewOf(k).getBoundingClientRect();return {left:r.left-v.left,top:r.top-v.top,width:r.width,height:r.height,vw:v.width,vh:v.height};};
      const fitted=k=>{const b=box(k);return b.left>=-0.5&&b.top>=-0.5&&b.left+b.width<=b.vw+0.5&&b.top+b.height<=b.vh+0.5&&(Math.abs(b.width-b.vw)<1||Math.abs(b.height-b.vh)<1);};
      const mouse=(type,x,y,el)=>el.dispatchEvent(new w.PointerEvent(type,{bubbles:true,cancelable:true,pointerId:7,pointerType:'mouse',isPrimary:true,clientX:x,clientY:y,
        button:type==='pointermove'?-1:0,buttons:type==='pointerup'?0:1}));
      const middle=k=>{const v=viewOf(k).getBoundingClientRect();return [Math.round(v.left+v.width/2),Math.round(v.top+v.height/2)];};
      async function clickPanel(k){const [x,y]=middle(k),el=viewOf(k);mouse('pointerdown',x,y,el);await sleep(30);mouse('pointerup',x,y,el);}
      async function drag(k,dx){const [x,y]=middle(k),el=viewOf(k);mouse('pointerdown',x,y,el);for(let i=1;i<=6;i++){await sleep(10);mouse('pointermove',x+dx*i/6,y,el);}await sleep(10);mouse('pointerup',x+dx,y,el);await sleep(20);}
      const rendered=label=>until(()=>api.state.held?.label.includes(label)&&!api.state.busy,'rendered '+label);
      // ================= /m's View frame, 390 px: never offered
      await load();
      api.openHandle('[ap:PAIR01]','node',{from:'outside',where:'Chat'});await rendered('PAIR01');await sleep(200);
      check('on the phone a note of two groups gets its 🖼️ chip but no side by side',!g('images').hidden&&g('images-text').textContent==='4 images · 2 groups'&&
        g('images-compare').hidden&&api.openCompare()===false&&!root());
      // ================= the desktop View pane, 1400 px
      frame.id='desktop-view-frame';frame.style.width='1400px';frame.style.height='900px';
      delete window.opcViewImmersive;
      await load();
      api.openHandle('[ap:PAIR01]','node',{from:'outside'});await rendered('PAIR01');await sleep(200);
      check('on the desktop ⧉ Side by side sits beside the 🖼️ chip',!g('images-compare').hidden&&!d.querySelector('.pswp'));
      g('images-compare').click();await until(()=>root(),'side by side open');await loaded();
      check('two groups: two panels side by side, each at its first image, with caption and position',panels().length===2&&
        heads()==='Before 1 / 2 | After 1 / 2'&&captions()==='card before | card after'&&pressed()==='1Before 2|2After 2');
      check('… each fitted into its own half of the pane, through /files/',fitted(0)&&fitted(1)&&box(0).vw>600&&
        new URL(imgOf(0).src).pathname==='/files'+ROOT+'o1.png'&&new URL(imgOf(1).src).pathname==='/files'+ROOT+'o2.png');
      check('the file line names the file and the heading',panels()[1].querySelector('.glimpse-cmp-file').textContent==='o2.png · After');
      check('Back has its own history entry to close',!!w.history.state?.glimpseCompare);
      shot('compare-two');
      key('ArrowRight');await loaded();
      check('→ steps every panel together',heads()==='Before 2 / 2 | After 2 / 2'&&captions()==='edit before | edit after'&&d.querySelector('.glimpse-cmp-steppos').textContent==='2 / 2');
      key('ArrowRight');
      check('… and stops at the last step',heads()==='Before 2 / 2 | After 2 / 2');
      key('Escape');await until(()=>!root(),'closed');await sleep(50);
      check('Esc closes it and takes its history entry back',!cmp()&&!w.history.state?.glimpseCompare);
      // three groups: the gallery opens straight into the lightbox; Esc, then ⧉
      api.openHandle('[ap:PZNQ9J]','node',{from:'outside'});await opened();
      key('Escape');await closed();
      g('images-compare').click();await until(()=>root(),'three open');await loaded();
      check('three groups: three panels, the first image of each',panels().length===3&&heads()==='Opus 1 / 4 | Fable 1 / 3 | Astra 1 / 3'&&
        captions()==='card · light | f · one | wide');
      {const w0=panels()[0].getBoundingClientRect().width,w2=panels()[2].getBoundingClientRect().width;
        check('… the same width each, about a third of the pane ('+Math.round(w0)+' px)',Math.abs(w0-w2)<2&&w0>420&&w0<470&&fitted(0)&&fitted(2));}
      shot('compare-three');
      key('ArrowRight');await loaded();
      check('→ : image 2 of each',heads()==='Opus 2 / 4 | Fable 2 / 3 | Astra 2 / 3');
      panels()[1].querySelectorAll('.glimpse-cmp-nudge')[1].click();await loaded();
      const gone=()=>panels()[1].querySelector('.glimpse-cmp-gone');
      check('Fable\'s own nudge › moves Fable alone, marked +1: its missing file says so; the others stay',heads()==='Opus 2 / 4 | Fable 3 / 3 | Astra 2 / 3'&&
        panels()[1].querySelector('.glimpse-cmp-off').textContent==='+1'&&!gone().hidden&&gone().textContent.includes('No such file')&&gone().textContent.includes(ROOT+'nope.png')&&
        !d.querySelector('.glimpse-cmp-bar .glimpse-cmp-btn[title^="Drop"]').hidden);
      key('ArrowRight');await loaded();
      check('→ again: they step together, the nudge kept; past Fable\'s last image its panel says so',heads()==='Opus 3 / 4 | Fable – / 3 | Astra 3 / 3'&&
        gone().textContent.includes('past the last of Fable\'s 3 images'));
      key('End');await loaded();
      check('End: the last step, where only Opus has an image left',heads()==='Opus 4 / 4 | Fable – / 3 | Astra – / 3'&&d.querySelector('.glimpse-cmp-steppos').textContent==='5 / 5');
      key('Home');await loaded();
      check('Home: the first step, Fable (one ahead) alone',heads()==='Opus – / 4 | Fable 1 / 3 | Astra – / 3');
      key('a');await loaded();
      check('A lines them up again: no nudge, image 1 of each',heads()==='Opus 1 / 4 | Fable 1 / 3 | Astra 1 / 3'&&panels()[1].querySelector('.glimpse-cmp-off').textContent===''&&
        d.querySelector('.glimpse-cmp-bar .glimpse-cmp-btn[title^="Drop"]').hidden);
      // one shared zoom and pan
      key('1');await sleep(30);
      {const a=box(0),c=box(2);
        check('1: actual pixels in every panel, the same point of each mid-panel (aligned)',Math.abs(a.width-1500)<1&&Math.abs(c.width-1500)<1&&
          Math.abs(a.left-c.left)<1&&Math.abs(a.top-c.top)<1&&Math.abs(a.left-(a.vw/2-750))<1&&root().dataset.zoomed==='true');}
      shot('compare-zoomed');
      const before=box(2).left;
      await drag(0,-200);
      check('a drag in one panel pans them all together ('+Math.round(before)+' → '+Math.round(box(2).left)+')',Math.abs(box(2).left-(before-200))<2&&Math.abs(box(0).left-box(2).left)<1&&
        !d.querySelector('.pswp'));
      viewOf(1).dispatchEvent(new w.WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:-100,clientX:middle(1)[0],clientY:middle(1)[1]}));await sleep(30);
      check('the wheel zooms every panel at once ('+Math.round(box(0).width)+' px)',Math.abs(box(0).width-1800)<1&&Math.abs(box(2).width-1800)<1);
      key('0');await sleep(30);
      check('0: every panel fitted again',fitted(0)&&fitted(1)&&fitted(2)&&root().dataset.zoomed==='false');
      key('+');await sleep(30);
      const zoomedIn=box(0).width;
      key('-');key('-');await sleep(30);
      check('+ zooms in ('+Math.round(zoomedIn)+' px), − back out to fit, never past it',zoomedIn>box(0).vw*1.2&&fitted(0)&&root().dataset.zoomed==='false');
      // the chips: a group out and in again
      d.querySelectorAll('.glimpse-cmp-chip')[2].click();await loaded();
      check('a lit chip takes its group out: two panels',panels().length===2&&heads()==='Opus 1 / 4 | Fable 1 / 3'&&pressed()==='1Opus 4|2Fable 3|·');
      d.querySelectorAll('.glimpse-cmp-chip')[2].click();await loaded();
      check('… and back in, in document order',panels().length===3&&heads()==='Opus 1 / 4 | Fable 1 / 3 | Astra 1 / 3');
      // a click on a panel's image: the ordinary lightbox over it; its keys win; Esc and Back come back to the panels
      await clickPanel(2);await opened();
      check('a click on Astra\'s image opens it in the lightbox, over the panels',ps().currIndex===7&&caption()==='wide'&&!!root());
      key('ArrowRight');await settle();
      check('… where the keys are the lightbox\'s (the panels do not step)',ps().currIndex===8&&heads()==='Opus 1 / 4 | Fable 1 / 3 | Astra 1 / 3');
      key('Escape');await closed();await sleep(50);
      check('Esc closes the lightbox; side by side is still there, with its history entry',!!root()&&cmp()?.isOpen()&&!!w.history.state?.glimpseCompare&&!w.history.state?.glimpseLightbox);
      key('ArrowRight');await loaded();
      check('… and has the keys again',heads()==='Opus 2 / 4 | Fable 2 / 3 | Astra 2 / 3');
      w.history.back();await until(()=>!root(),'Back closes it');await sleep(50);
      check('Back closes side by side',!cmp()&&!d.querySelector('.glimpse-cmp'));
      // an estate of two galleries, one of them live folders: its groups in side by side too
      api.openHandle('[ap:ESTAT1]','node',{from:'outside'});await opened();
      key('Escape');await closed();
      g('images-compare').click();await until(()=>root(),'estate open');await loaded();
      check('a tapped estate of galleries: its six groups as chips, the first three shown',pressed()==='1Opus 4|2Fable 3|3Astra 3|·|·|·'&&panels().length===3);
      d.querySelectorAll('.glimpse-cmp-chip')[3].click();await loaded();
      check('the live Opus chip takes the right-most panel\'s place: Opus beside Opus, the listed file captioned by its name',heads()==='Opus 1 / 4 | Fable 1 / 3 | Opus 1 / 4'&&
        captions()==='card · light | f · one | o1'&&new URL(imgOf(2).src).pathname==='/files'+ROOT+'o1.png'&&fitted(2));
      d.querySelectorAll('.glimpse-cmp-chip')[5].click();await loaded();
      check('… and the live Astra folder (Z:/…%20…/), a portrait sized by its listing, fitted',heads()==='Opus 1 / 4 | Fable 1 / 3 | Astra 1 / 1'&&captions().endsWith('| a3')&&
        fitted(2)&&box(2).height>box(2).width);
      shot('compare-estate');
      d.querySelector('.glimpse-cmp-close').click();await until(()=>!root(),'✕');await sleep(50);
      check('✕ closes it',!cmp()&&!w.history.state?.glimpseCompare);
      soft('no page error ['+fx.errors.filter(e=>!/nope\.png|resource error/.test(e)).join('; ')+']',!fx.errors.filter(e=>!/nope\.png|resource error/.test(e)).length);
    }
  } catch(e) { if(!e.shot&&!checks.some(c=>!c.passed))checks.push({name:'fixture: '+e.message,passed:false}); if(e.shot)checks.push({name:e.message,passed:true}); }
  document.getElementById('results').textContent=JSON.stringify({passed:checks.filter(x=>x.passed).length,failed:checks.filter(x=>!x.passed).length,checks},null,2);
  document.body.dataset.testPassed=String(checks.length>0&&checks.every(x=>x.passed));
})();
