const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const root=path.join(__dirname,'../static');
const source=fs.readFileSync(path.join(root,'message-markdown.js'),'utf8');
const fixture=fs.readFileSync(path.join(__dirname,'fixtures/message-markdown.md'),'utf8');
function setup({libraries=true,fail=false}={}) {
  let parsed=0,sanitized=0,html,options;
  const context={URL};
  if(libraries){
    const actual=require(path.join(root,'vendor/marked.umd.js'));
    context.marked={Marked:class extends actual.Marked {constructor(...args){super(...args);const parse=this.parse;this.parse=(...a)=>{parsed++;return parse(...a);};}}};
    // DOM/security assertions use actual Chrome+DOMPurify, not this VM double.
    context.DOMPurify={isSupported:true,sanitize:(text,opts)=>{sanitized++;html=text;options=opts;if(fail)throw Error('fixture failure');return {querySelectorAll:()=>[]};}};
  }
  vm.runInNewContext(source,context);
  const node={dataset:{},ownerDocument:{baseURI:'http://127.0.0.1:9911/sessions'},textContent:'',replaceChildren(fragment){this.fragment=fragment;this.replacements=(this.replacements||0)+1;}};
  return {node,render:context.opcMessageMarkdown.render,stats:()=>({parsed,sanitized,html,options})};
}
test('real pinned GFM parser covers fixture structure and escapes source HTML before sanitization',()=>{
  const f=setup();f.render(f.node,fixture,true);const {html,options}=f.stats();
  for(const tag of ['h1','strong','em','ul','ol','li','table','th','td','code','pre','blockquote','a'])assert.match(html,new RegExp('<'+tag+'(?:>| )'));
  assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<(?:img|script|svg)(?:>| )/);
  assert.equal(options.RETURN_DOM_FRAGMENT,true);assert.equal(options.ALLOW_DATA_ATTR,false);
  assert.equal(options.ALLOWED_TAGS.includes('img'),false);assert.equal(options.ALLOWED_ATTR.includes('onerror'),false);
  assert.equal(f.node.dataset.rendering,'markdown');
});
test('streaming stays exact text, final parses once; unchanged metadata never reparses or remounts',()=>{
  const f=setup();f.render(f.node,fixture,false);assert.equal(f.node.textContent,fixture);assert.equal(f.stats().parsed,0);
  f.render(f.node,fixture,true);const fragment=f.node.fragment;for(let n=0;n<100;n++)f.render(f.node,fixture,true);
  assert.equal(f.stats().parsed,1);assert.equal(f.stats().sanitized,1);assert.equal(f.node.fragment,fragment);assert.equal(f.node.replacements,1);
  f.render(f.node,fixture+' final',true);assert.equal(f.stats().parsed,2);
});
test('missing libraries or sanitizer error retain the whole source, never unsanitized markup',()=>{
  for(const options of [{libraries:false},{fail:true}]){
    const f=setup(options);f.render(f.node,fixture,true);assert.equal(f.node.textContent,fixture);assert.equal(f.node.fragment,undefined);
    assert.equal(f.node.dataset.rendering,'text');assert.match(f.node.title,/unavailable/);
  }
});
test('images are alt text only, dependencies are pinned local assets with licenses and no runtime CDN',()=>{
  const f=setup();f.render(f.node,'![alt text](https://tracking.invalid/image)',true);assert.match(f.stats().html,/alt text/);assert.doesNotMatch(f.stats().html,/<img/);
  const assets={'marked.umd.js':'b82b9334406d8bdd16b6236c08de1cb65b2a859873ca9503d9cc0b292ca3c12a','purify.min.js':'cd37898f9782adf54820c413191b5691803f0f137c799d6edcfd38523b4c2c31'};
  for(const [file,hash]of Object.entries(assets))assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'vendor',file))).digest('hex'),hash);
  const html=fs.readFileSync(path.join(root,'sessions.html'),'utf8');assert.match(html,/vendor\/marked.umd.js.*vendor\/purify.min.js.*message-markdown.js.*sessions.js/s);assert.doesNotMatch(html,/<script src="https?:/);
  for(const name of ['marked-LICENSE','DOMPurify-LICENSE','DOMPurify-LICENSE-MPL'])assert.ok(fs.statSync(path.join(root,'vendor',name)).size>100);
});
