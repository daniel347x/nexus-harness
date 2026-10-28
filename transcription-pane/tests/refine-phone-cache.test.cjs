// Actual phone module + core, with no startup, host process, real key or inference.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { messageEvents, streamResponse, contentText } = require('./pane-fixture.cjs');
const read = name => fs.readFileSync(path.join(__dirname, '../static', name), 'utf8');

function phoneFixture() {
  const elements = new Map(), store = new Map([['refine_anthropic_api_key', 'offline-not-a-key']]);
  const requests = [], bodies = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, { value:'', dataset:{}, style:{}, textContent:'',
      selectionStart:0, selectionEnd:0, classList:{add(){},remove(){}}, setAttribute(name,value){this[name]=String(value);},
      setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;} });
    return elements.get(id);
  };
  const target = {source:'host',role:'Manager',uuid:'11111111-1111-4111-8111-111111111111',
    executionId:'cc-11111111-1111-4111-8111-111111111111',generation:1,targetRef:'offline-target'};
  let text='[USER]\nQuestion 😀\n\n---\n\n[ASSISTANT]\nAnswer', boundaries;
  const ctx = {TextEncoder,TextDecoder,AbortController,DOMException,URLSearchParams,crypto:require('node:crypto').webcrypto,
    document:{getElementById:el},window:{matchMedia:()=>({matches:true})},
    localStorage:{getItem:key=>store.get(key)??null,setItem:(key,value)=>store.set(key,value)},
    setTimeout:()=>1,clearTimeout(){},setInterval(){throw new Error('phone provider has no deadline');},
    fetch:async(url,options)=>{
      if(url.startsWith('/api/claude/context?')) {
        const query=new URL(url,'http://fixture').searchParams; requests.push(query);
        return {ok:true,text:async()=>JSON.stringify({...target,schemaVersion:1,backend:'claude',status:'stored',targetRevision:0,
          historyMode:query.get('historyMode'),text,historyBoundaries:boundaries})};
      }
      assert.equal(url,'https://api.anthropic.com/v1/messages'); bodies.push(JSON.parse(options.body));
      return streamResponse(messageEvents({stop_reason:'end_turn',content:[{type:'text',text:'clean'}],
        usage:{cache_read_input_tokens:31000,cache_creation_input_tokens:1400,cache_creation:{ephemeral_1h_input_tokens:1400}}}));
    }};
  vm.createContext(ctx);
  vm.runInContext(read('refine-defaults.js')+'\n'+read('refine-core.js')+'\n'+read('m.js').replace(/phoneManager\.start\(\);\s*$/,'')+'\nthis.phone=phoneManager;',ctx);
  ctx.phone.state.target=target;ctx.phone.state.selected=target.executionId;
  return {ctx,el,store,requests,bodies,setHistory(value,ends){text=value;boundaries=ends;},async refine(value='raw words'){
    el('m-draft').value=value;el('m-draft').setSelectionRange(0,0);await ctx.phone.refine();
    assert.equal(el('m-draft').value,'clean',el('m-status').textContent);
  }};
}

test('phone defaults to whole history and shows the API cache counts on its completion line', async()=>{
  const f=phoneFixture(); await f.refine();
  assert.equal(f.requests[0].get('historyMode'),'whole');assert.equal(f.requests[0].has('budgetBytes'),false);
  assert.match(f.el('m-status').textContent,/cache: read 31K · wrote 1\.4K/);
  assert.equal(f.bodies[0].system[0].cache_control.ttl,'1h');
  assert.equal(f.bodies[0].messages[0].content.at(-1).cache_control,undefined);
  assert.match(contentText(f.bodies[0].messages[0].content),/<chat-history>[\s\S]*Question 😀[\s\S]*<transcription>/);
});

test('phone budgeted choice is explicit and malformed cache hints cannot block unchanged prose', async()=>{
  const f=phoneFixture();f.store.set('refine_history_mode','budgeted');
  const history='[USER]\nHistory 🦊';f.setHistory(history,[{id:'bad',end:1.5}]);
  await f.refine('dictation');
  assert.equal(f.requests[0].get('historyMode'),'budgeted');assert.equal(f.requests[0].get('budgetBytes'),'51200');
  assert.ok(contentText(f.bodies[0].messages[0].content).includes(history));
  f.setHistory(history,'not a boundary list');await f.refine('more dictation');
  assert.ok(contentText(f.bodies[1].messages[0].content).includes(history));
});
