const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const root=path.join(__dirname,'../static');
function api(source=fs.readFileSync(path.join(root,'diff-view.js'),'utf8')) {const ctx={};vm.runInNewContext(source,ctx);return ctx.opcDiffView;}
const hunk={oldStart:41,oldLines:3,newStart:61,newLines:4,lines:[' ctx','-old','+new','+extra',' tail','\\ No newline at end of file']};
test('true non-1 starts, exact characters and marker without fabricated numbers',()=>{
  const lines=api().lineModel(hunk);
  assert.deepEqual(Array.from(lines,x=>[x.old,x.next]),[[41,61],[42,null],[null,62],[null,63],[43,64],[null,null]]);
  assert.equal(lines[5].text,'\\ No newline at end of file');
  assert.equal(api().lineModel({...hunk,oldStart:0,newStart:1,lines:['+\tλ <>&']} )[0].text,'\tλ <>&');
  assert.equal(api().lineModel({...hunk,lines:[]}).length,0);
});
test('change runs pair with honest blank opposite cells',()=>{
  const a=api(),rows=a.pairLines(a.lineModel(hunk));
  assert.equal(rows[1].left.text,'old');assert.equal(rows[1].right.text,'new');
  assert.equal(rows[2].left,null);assert.equal(rows[2].right.text,'extra');
  const deleted=a.pairLines(a.lineModel({...hunk,lines:['-one','-two']}));
  assert.equal(deleted.length,2);assert.equal(deleted[1].right,null);
  assert.equal(rows.reduce((n,r)=>n+r.count,0),5);
});
function mounted(diff, {highlight=true}={}) {
  const frames=new Map(),listeners=new Map();let serial=0,selection=null,width=390,disconnected=false;
  class Node {
    constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.events=new Map();this.className='';this._text='';this.ownerDocument=doc;
      const properties=new Map();this.style={setProperty:(key,value)=>properties.set(key,value),removeProperty:key=>properties.delete(key),getPropertyValue:key=>properties.get(key)||''};}
    append(...nodes){for(const node of nodes){node.parent=this;node.parentNode=this;this.children.push(node);}}
    replaceChildren(...nodes){this.children=[];this._text='';this.append(...nodes);}
    get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
    set textContent(text){this._text=String(text);this.children=[];}
    setAttribute(k,v){this[k]=v;}
    contains(node){return node===this||this.children.some(n=>n.contains(node));}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);}
    addEventListener(k,f){this.events.set(k,f);} removeEventListener(k){this.events.delete(k);}
    getBoundingClientRect(){return {width};}
  }
  const doc={createElement:tag=>new Node(tag),createTextNode:text=>{const n=new Node('#text');n.textContent=text;return n;},
    getSelection:()=>selection,addEventListener:(k,f)=>listeners.set(k,f),removeEventListener:k=>listeners.delete(k)};
  let resize,calls=0;const actual=require(path.join(root,'vendor/highlight.min.js'));
  const win={setTimeout:fn=>{frames.set(++serial,fn);return serial;},clearTimeout:id=>frames.delete(id),
    ResizeObserver:class {constructor(fn){resize=fn;}observe(){}disconnect(){disconnected=true;}}};doc.defaultView=win;
  const ctx={hljs:highlight?{getLanguage:actual.getLanguage,highlight:(...a)=>{calls++;return actual.highlight(...a);}}:null};
  vm.runInNewContext(fs.readFileSync(path.join(root,'diff-view.js'),'utf8'),ctx);
  const container=new Node(),view=ctx.opcDiffView.mount(container,diff,{layout:'auto',wrap:true});
  function find(cls,node=container){return [node,...node.children.flatMap(n=>find(cls,n))].filter(n=>n.className.split(' ').includes(cls));}
  return {container,view,find,frames,listeners,calls:()=>calls,
    select:node=>{selection=node?{isCollapsed:false,anchorNode:node,focusNode:node}:null;listeners.get('selectionchange')?.();},
    resize:w=>{width=w;resize();},disconnected:()=>disconnected,
    tick:()=>{const current=[...frames.values()];frames.clear();current.forEach(fn=>fn());}};
}
const data=(lines=[' ctx','-const old = 1;','+const next = 2;'])=>({source:'claude-structured-patch',complete:true,files:[{path:'src/test.js',status:'modified',additions:1,deletions:1,hunks:[{oldStart:41,oldLines:2,newStart:61,newLines:2,lines}]}]});
test('mounted API: exact code, identical update, CSS-only Wrap, selection deferral and latest paint',()=>{
  const d=data(),f=mounted(d),code=f.find('opc-diff-code')[0],count=f.calls();
  assert.equal(Object.isFrozen(f.view),true);assert.equal(code.textContent,'ctx');
  f.view.update(JSON.parse(JSON.stringify(d)));assert.equal(f.find('opc-diff-code')[0],code);
  f.select(code);f.view.setWrap(false);assert.equal(f.find('opc-diff-code')[0],code);assert.equal(f.calls(),count);
  assert.equal(f.find('opc-diff')[0].dataset.wrap,'false');
  f.view.update(data(['+new first']));f.view.update(data(['+new latest']));assert.equal(f.find('opc-diff-code')[0],code);
  f.select(null);assert.equal(f.find('opc-diff-code')[0].textContent,'new latest');
  f.resize(900);assert.equal(f.find('opc-diff')[0].dataset.layout,'split');
  f.view.setLayout('inline');f.view.update(d);f.resize(1200);assert.equal(f.find('opc-diff')[0].dataset.layout,'inline');
  f.view.update(null);assert.equal(f.find('opc-diff')[0].hidden,true);
  f.view.destroy();assert.equal(f.container.children.length,0);assert.equal(f.listeners.size,0);assert.ok(f.disconnected());
  f.view.update(d);assert.equal(f.container.children.length,0);
});
test('initial 2000 across files; Show all yields complete source and destroy cancels callbacks',()=>{
  const d=data(Array.from({length:2501},(_,i)=>'+'+i));d.files.push({...d.files[0],path:'second.txt',hunks:[{oldStart:0,oldLines:0,newStart:1,newLines:1,lines:['+last']}]});
  const f=mounted(d);assert.equal(f.find('opc-diff-code').length,2000);assert.match(f.find('opc-diff-count')[0].textContent,/2,000 of 2,502/);
  assert.match(f.find('opc-diff-syntax')[0].textContent,/Plain code.*large hunk/);assert.equal(f.calls(),0);
  f.find('opc-diff-show')[0].events.get('click')();assert.equal(f.find('opc-diff-code').length,2000);assert.equal(f.frames.size,1);
  f.tick();assert.equal(f.find('opc-diff-code').length,2500);f.tick();assert.equal(f.find('opc-diff-code').length,2502);
  assert.equal(f.find('opc-diff-code').at(-1).textContent,'last');assert.equal(f.find('opc-diff-show')[0].hidden,true);
  f.view.update(d);assert.equal(f.find('opc-diff-code').length,2502);
  const g=mounted(d);g.find('opc-diff-show')[0].events.get('click')();const stale=[...g.frames.values()][0];g.view.destroy();
  assert.equal(g.frames.size,0);stale();assert.equal(g.container.children.length,0);
});
test('gutters fit all recorded coordinates before Show all and reset on changed small data',()=>{
  const d=data(Array.from({length:2501},()=>'+next'));d.files[0].hunks[0].newStart=98000;
  const f=mounted(d),root=f.find('opc-diff')[0],code=f.find('opc-diff-code')[0];
  assert.equal(root.style.getPropertyValue('--diff-number-width'),'6.6ch');
  assert.equal(f.find('opc-diff-code').length,2000);
  f.view.update(JSON.parse(JSON.stringify(d)));assert.equal(f.find('opc-diff-code')[0],code);
  f.select(code);f.view.update(data());assert.equal(root.style.getPropertyValue('--diff-number-width'),'6.6ch');
  f.select(null);assert.equal(root.style.getPropertyValue('--diff-number-width'),'');
});
test('non-diff reasons, incomplete content, create/delete/empty and unavailable highlighter remain readable',()=>{
  const f=mounted({files:[],complete:false,source:'unavailable',reason:'Shell command; no structured edit diff recorded (it may have edited files).'});
  assert.match(f.find('opc-diff-reason')[0].textContent,/may have edited files/);
  assert.equal(f.find('opc-diff-controls')[0].hidden,true,'no files: no inert Layout/Wrap/count row before the reason');
  const d=data(['+λ <>&']);d.complete=false;d.reason='new content; previous content not recorded';d.files[0].status='added';
  f.view.update(d);assert.match(f.find('opc-diff-reason')[0].textContent,/previous content not recorded/);
  assert.equal(f.find('opc-diff-controls')[0].hidden,false,'a diff with files keeps its controls');
  const g=mounted(d,{highlight:false});assert.equal(g.find('opc-diff-code')[0].textContent,'λ <>&');assert.match(g.find('opc-diff-syntax')[0].textContent,/Plain code/);
  d.files[0].hunks=[];d.files[0].status='deleted';g.view.update(d);assert.match(g.find('opc-diff-gap')[0].textContent,/empty file/);
});
test('container threshold and explicit override',()=>{
  const a=api();for(const w of [0,390,899])assert.equal(a.effectiveLayout('auto',w),'inline');
  for(const w of [900,1200])assert.equal(a.effectiveLayout('auto',w),'split');
  assert.equal(a.effectiveLayout('inline',1200),'inline');assert.equal(a.effectiveLayout('split',390),'split');
});
test('whole-side highlighting preserves multiline tokens and exact code; absent/unknown is plain',()=>{
  const a=api(),hl=require(path.join(root,'vendor/highlight.min.js'));
  assert.equal(a.languageForPath('src/foo.ts',hl),'typescript');assert.equal(a.languageForPath('foo.unknown',hl),null);
  const texts=['/* first','second */','const x = "λ";'];let calls=0;
  const lexical=a.highlightSide(texts,'javascript',hl);
  assert.ok(lexical.lines[1].some(t=>t.classes.includes('hljs-comment')),'multiline comment continuation must retain syntax scope');
  const spy={highlight:(...args)=>{calls++;assert.equal(args[0],texts.join('\n'));return hl.highlight(...args);}};
  const out=a.highlightSide(texts,'javascript',spy);assert.equal(calls,1);assert.equal(out.colored,true);
  assert.ok(out.lines[1].some(t=>t.classes.includes('hljs-comment')));
  assert.deepEqual(Array.from(out.lines,l=>l.map(t=>t.text).join('')),texts);
  for(const language of [null,'not-a-language'])assert.equal(a.highlightSide(texts,language,hl).colored,false);
  assert.equal(a.highlightSide(texts,'javascript',null).colored,false);
});
test('pinned common-language browser release, license and source agree',()=>{
  const hash=crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'vendor/highlight.min.js'))).digest('hex');
  assert.equal(hash,'c4a399dd6f488bc97a3546e3476747b3e714c99c57b9473154c6fb8d259b9381');
  const readme=fs.readFileSync(path.join(root,'vendor/README.md'),'utf8');
  assert.ok(readme.includes(hash));assert.ok(readme.includes('highlight.js 11.11.1 (@highlightjs/cdn-assets)'));
  assert.ok(readme.includes('https://registry.npmjs.org/@highlightjs/cdn-assets/-/cdn-assets-11.11.1.tgz'));
  assert.ok(fs.statSync(path.join(root,'vendor/highlight-LICENSE')).size>100);
  const ctx={};vm.runInNewContext(fs.readFileSync(path.join(root,'vendor/highlight.min.js'),'utf8'),ctx);
  assert.equal(ctx.hljs.versionString,'11.11.1');assert.ok(ctx.hljs.getLanguage('javascript'));
});
