// Focused, deterministic DOM/timer tests for the real toast controller; no browser, server or transport.
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/status-toast.js'),'utf8');
function fixture(phone=true) {
  const observers=[],ids=new Map(),timers=new Map(),frames=[]; let now=0,serial=0;
  class Node {
    constructor(tag='div',text='') { this.tagName=tag;this._text=text;this.childNodes=[];this.dataset={};this.className='';this.attrs={};this.events={};this.hidden=false;
      this.style={setProperty(k,v){this[k]=v;}};this.rect={left:10,right:338,top:20,bottom:330,width:328,height:310};
      this.classList={contains:name=>this.className.split(/\s+/).includes(name)}; }
    set textContent(text){this._text=String(text);this.childNodes=[];if(text)this.append(new Node('#text',String(text)));}
    get textContent(){return this.childNodes.length?this.childNodes.map(n=>n.textContent).join(''):this._text;}
    append(...children){for(const child of children){if(child.parentNode)child.parentNode.childNodes=child.parentNode.childNodes.filter(n=>n!==child);this.childNodes.push(child);child.parentNode=this;}}
    setAttribute(k,v){this.attrs[k]=v;}
    addEventListener(k,fn){(this.events[k] ||= []).push(fn);}
    fire(k,event={}){for(const fn of this.events[k] || [])fn(event);}
    getBoundingClientRect(){return this.rect;}
    focus(){doc.activeElement=this;}
  }
  const make=id=>{const node=new Node();node.id=id;ids.set(id,node);return node;};
  const status=make(phone?'m-status':'status'),recall=make(phone?'m-status-history':'status-history'),originalParent=new Node(),body=new Node();
  originalParent.append(status);body.append(originalParent);
  const conv=make('m-conversation'),margin=make('m-draft-margin'),draft=make('m-draft');make('m-chat-view');make('m-composer');make('m-app');
  conv.rect={left:10,right:338,top:-400,bottom:330,width:328,height:730};
  margin.rect={left:346,right:390,top:8,bottom:360,width:44,height:352};
  const doc={body,activeElement:draft,getElementById:id=>ids.get(id)||null,createElement:tag=>new Node(tag),createTextNode:text=>new Node('#text',text),events:{},
    addEventListener(k,fn){(this.events[k] ||= []).push(fn);}};
  const win={innerWidth:390,innerHeight:844,visualViewport:{offsetTop:0,offsetLeft:0,height:450,width:390,addEventListener(){}},
    addEventListener(){},getComputedStyle:n=>n.computed || {paddingTop:'8px',paddingRight:'0px'},
    setTimeout(fn,ms){const id=++serial;timers.set(id,{fn,at:now+ms});return id;},clearTimeout(id){timers.delete(id);},
    requestAnimationFrame(fn){frames.push(fn);return frames.length;},
    MutationObserver:class {constructor(fn){this.fn=fn;observers.push(this);}observe(node){this.node=node;}},ResizeObserver:class {observe(){}}};
  doc.defaultView=win;
  vm.runInNewContext(source,{document:doc});
  const toast=body.childNodes.find(n=>n.className==='pane-toast'),log=body.childNodes.find(n=>n.className==='pane-status-log');
  const capture=()=>observers.find(o=>o.node===status).fn(),layout=()=>{observers.find(o=>o.node===body).fn();for(const fn of frames.splice(0))fn();};
  const set=(text,error=false)=>{status.textContent=text;status.dataset.error=String(error);capture();};
  const tick=ms=>{now+=ms;for(const [id,t] of [...timers])if(t.at<=now){timers.delete(id);t.fn();}};
  return {doc,win,status,recall,toast,log,conv,margin,draft,set,tick,capture,layout,originalParent,timers};
}

test('same status node and full text survive fading; identical polling notes do not renew or reopen it',()=>{
  const f=fixture();assert.equal(f.status.parentNode,f.toast);assert.equal(f.originalParent.childNodes.length,0);
  f.set('Delivered. Unchanged draft cleared; original retained.');assert.equal(f.toast.dataset.visible,'true');
  f.tick(2000);f.set(f.status.textContent);f.tick(1000);assert.equal(f.toast.dataset.visible,'false');
  assert.match(f.status.textContent,/original retained/);f.set(f.status.textContent);assert.equal(f.toast.dataset.visible,'false');
  assert.equal(f.log.childNodes[1].childNodes.length,1);
});
test('errors cancel an older expiry and stay until dismissed or replaced, not overwritten by a stale timer',()=>{
  const f=fixture();f.set('Refining…');f.tick(2500);f.set('Provider failed; draft kept.',true);f.tick(10000);
  assert.equal(f.toast.dataset.error,'true');assert.equal(f.toast.dataset.visible,'true');assert.equal(f.recall.dataset.error,'true');
  f.toast.childNodes[2].fire('click');assert.equal(f.toast.dataset.visible,'false');assert.match(f.status.textContent,/draft kept/);
  f.set('Provider failed; draft kept.',true);assert.equal(f.toast.dataset.visible,'false');
  f.set('Sent successfully');assert.equal(f.toast.dataset.error,'false');f.tick(3000);assert.equal(f.toast.dataset.visible,'false');
});
test('the full page-local safe-text log retains notes and appended thinking reminders without changing status authority',()=>{
  const f=fixture(false),long='<script>untrusted</script> '+ 'Full original retained. '.repeat(300);
  f.set(long);const warning=f.doc.createElement('span');warning.textContent='Reminder: you are currently set to max';f.status.append(warning);f.capture();
  const entries=f.log.childNodes[1];assert.equal(entries.childNodes.length,2);assert.ok(entries.textContent.includes(long));assert.match(entries.textContent,/Reminder:/);
  f.tick(3000);f.recall.fire('click',{detail:0});assert.equal(f.log.hidden,false);assert.equal(f.status.textContent,long+warning.textContent);
  f.log.childNodes[0].childNodes[1].fire('click');assert.equal(f.log.hidden,true);
});
test('phone toast uses only visible conversation space; maximized draft badge lives in its non-editable margin',()=>{
  const f=fixture();f.set('Ready');assert.equal(f.toast.style['--toast-top'],'8px');assert.equal(f.toast.style['--toast-left'],'18px');
  assert.equal(f.toast.style['--toast-width'],'312px');assert.equal(f.toast.style.maxHeight,'318px');
  f.doc.body.dataset.maximized='draft';f.layout();assert.equal(f.toast.dataset.compact,'true');
  assert.equal(f.toast.style['--toast-width'],'34px');assert.equal(f.toast.style['--toast-left'],'351px');assert.equal(f.toast.childNodes[1].textContent,'i');
  f.set('Send failed',true);assert.equal(f.toast.childNodes[1].textContent,'!');
  f.doc.getElementById('m-chat-view').hidden=true;f.layout();assert.equal(f.toast.style.display,'none');
});
test('phone touch controls keep dictation focus; keyboard history activation has a focusable Close and restores it',()=>{
  const f=fixture();f.set('Reading context…');let prevented=false;
  f.toast.childNodes[1].fire('pointerdown',{preventDefault(){prevented=true;}});assert.equal(prevented,true);
  f.toast.childNodes[1].fire('click',{detail:1});assert.equal(f.doc.activeElement,f.draft);assert.equal(f.log.hidden,false);
  f.log.childNodes[0].childNodes[1].fire('click');
  f.recall.fire('click',{detail:0});assert.equal(f.doc.activeElement,f.log.childNodes[0].childNodes[1]);
  f.doc.events.keydown[0]({key:'Escape',preventDefault(){},stopImmediatePropagation(){}});
  assert.equal(f.log.hidden,true);assert.equal(f.doc.activeElement,f.draft);
});
test('safe-area placement reuses the shell top inset and excludes the margin right inset from the badge target',()=>{
  const f=fixture();f.doc.getElementById('m-app').computed={paddingTop:'47px'};
  f.set('Not under the notch');assert.equal(f.toast.style['--toast-top'],'47px');assert.equal(f.toast.style.maxHeight,'279px');
  f.margin.rect={left:302,right:390,top:8,bottom:360,width:88,height:352};f.margin.computed={paddingRight:'44px'};
  f.doc.body.dataset.maximized='draft';f.layout();assert.equal(f.toast.style['--toast-top'],'47px');
  assert.equal(f.toast.style['--toast-left'],'307px');assert.equal(f.toast.style['--toast-width'],'34px');
});
test('desktop .err is sticky and fading is presentation only, never a receipt/status subject clear',()=>{
  const f=fixture(false);f.status.className='status err';f.set('Uncertain Send; querying receipt.');f.tick(8000);
  assert.equal(f.toast.dataset.visible,'true');assert.equal(f.toast.dataset.error,'true');
  f.status.className='status ok';f.set('Delivered: exact replay ACK.');f.tick(3000);
  assert.equal(f.toast.dataset.visible,'false');assert.equal(f.status.className,'status ok');assert.equal(f.status.textContent,'Delivered: exact replay ACK.');
});

test('fitDraft: phone ignores scrollHeight, tablet preserves autosizing, maximize owns height and follow remains intact',()=>{
  const m=fs.readFileSync(path.join(__dirname,'../static/m.js'),'utf8');
  const fn=m.slice(m.indexOf('  function fitDraft() {'),m.indexOf('  function retainOriginal('));
  const box={style:{height:'900px'},scrollHeight:2000},scroll={scrollTop:12,scrollHeight:3000};let fits=0;
  const ctx={state:{follow:true,maximized:null},compactDraft:{matches:true},$:id=>id==='draft'?box:scroll,thumbStrip:{fit(){fits++;}}};
  vm.runInNewContext(fn+'\nfitDraft();',ctx);assert.equal(box.style.height,'');assert.equal(scroll.scrollTop,3000);assert.equal(fits,1);
  ctx.compactDraft.matches=false;ctx.state.follow=false;scroll.scrollTop=123;ctx.fitDraft();assert.equal(box.style.height,'2002px');assert.equal(scroll.scrollTop,123);
  ctx.state.maximized='draft';ctx.fitDraft();assert.equal(box.style.height,'');assert.equal(fits,2);
  assert.match(m,/matchMedia\('\(max-width: 499px\), \(max-height: 499px\)'\)/);
  assert.match(m,/compactDraft.addEventListener\('change',fitDraft\)/);
});
