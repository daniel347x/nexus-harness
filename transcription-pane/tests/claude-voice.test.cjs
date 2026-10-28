const test = require('node:test');
const assert = require('node:assert/strict');
const {pane, streamResponse, messageEvents, contentText} = require('./pane-fixture.cjs');
const ref = c => c.repeat(32);
const wire = data => ({ok:true,text:async()=>JSON.stringify(data)});
function native(options={}) {
  const f = pane(options), t = {schemaVersion:1,backend:'claude',targetRef:ref('A'),storeRef:ref('S'),
    sessionRef:ref('J'),scopeRef:ref('M'),leafRef:ref('L'),fileEpoch:ref('E'),
    uuid:'11111111-1111-4111-8111-111111111111',leafUuid:'22222222-2222-4222-8222-222222222222',
    scope:'main',title:'Synthetic history',source:'windows-history',bindingRef:null};
  f.api.state.backend='claude'; f.api.state.claudeTarget=t;
  const local = [], providers = [];
  let context='[USER]\nFunction load_rows [ap:ABC123]\n\n---\n\n[ASSISTANT]\nProse only 😀',
    result='cleaned dictation', readHook=null, providerHook=null;
  f.ctx.fetch=async(url,opts)=>{
    if (url.startsWith('/api/claude/')) {
      local.push(url); if(readHook) return readHook(url,opts);
      if(url.startsWith('/api/claude/context?')) {
        const q=new URL(url,'http://fixture').searchParams;
        return wire({...t,status:'stored',targetRevision:Number(q.get('targetRevision')),text:context,
          bytes:Buffer.byteLength(context),turns:2,users:1,readAt:Date.now()});
      }
      if(url.startsWith('/api/claude/events?')) return wire({...t,status:'complete',events:[],targetInfo:t});
      throw new Error('no native input route allowed');
    }
    assert.ok(url.startsWith('https://'),url); providers.push(JSON.parse(opts.body));
    if(providerHook) return providerHook(url,opts);
    return streamResponse(messageEvents({content:[{type:'text',text:result}],stop_reason:'end_turn'}));
  };
  return {...f,t,local,providers,setRead(fn){readHook=fn;},setProvider(fn){providerHook=fn;},
    setContext(text){context=text;},setResult(text){result=text;}};
}
const tick = async predicate => {for(let i=0;i<50&&!predicate();i++) await Promise.resolve(); assert.ok(predicate());};

test('native Refine uses reader exact main prose, freezes identity, verifies full untrimmed original and preserves independent provider',async()=>{
  const f=native(); f.box.value='  before\n\ndictation 😀\nafter  '; f.box.selectionStart=10; f.box.selectionEnd=23;
  f.setResult('refined selection'); const original=f.box.value;
  await f.api.refine();
  assert.equal(f.api.readOriginals()[0].text,original);
  assert.equal(f.providers.length,1); const submitted=contentText(f.providers[0].messages[0].content);
  assert.ok(submitted.includes('Function load_rows [ap:ABC123]')); assert.ok(submitted.includes(original.substring(10,23)));
  assert.equal(f.box.value,original.substring(0,10)+'refined selection'+original.substring(23));
  assert.equal(f.api.state.draftRevision,1); assert.equal(f.local.filter(u=>u.includes('/events?')).length,3);
  assert.equal(f.storage.get('pane_backend_v1'),undefined); assert.equal(f.storage.get('refine_provider'),'anthropic');
  assert.match(f.el('status').textContent,/refined ✓/); assert.match(f.el('cache-info').textContent,/—/);
});

test('required native context HTTP/malformed/budget/identity/epoch/count/limit failures NEVER call provider or silently omit context',async()=>{
  for(const mode of ['http','malformed','budget','identity','epoch','bytes','oversize']) {
    const f=native(); f.box.value=' full original '; const original=f.box.value;
    if(mode==='budget') { f.storage.set('refine_history_mode','budgeted'); f.storage.set('pane_context_kb','401'); }
    f.setRead(async url=>{
      if(url.includes('/events?')) return wire({...f.t,status:'complete',targetInfo:f.t}); // only the required context fails
      if(mode==='http') return {ok:false,status:502,text:async()=>JSON.stringify({error:'outage'})};
      if(mode==='malformed') return {ok:true,text:async()=>'{'};
      if(mode==='oversize') return {ok:true,text:async()=> 'x'.repeat(8*1024*1024+1)};
      const ctx={...f.t,status:'stored',targetRevision:0,text:'prose',bytes:5,turns:1,users:1,readAt:Date.now()};
      if(mode==='identity') ctx.sessionRef=ref('X'); if(mode==='epoch') ctx.fileEpoch=ref('X'); if(mode==='bytes') ctx.bytes=0;
      return wire(ctx);
    });
    await f.api.refine(); assert.equal(f.providers.length,0,mode); assert.equal(f.box.value,original,mode);
    assert.equal(f.api.readOriginals()[0].text,original); assert.match(f.el('status').textContent,/failed/);
  }
});

test('explicit no-conversation-context works without a live binding and is distinct from an outage',async()=>{
  const f=native(); f.api.state.claudeTarget=null; f.storage.set('pane_claude_no_context_v1','true'); f.box.value='dictation';
  f.setRead(()=>{throw new Error('contextless has no implicit source');}); await f.api.refine();
  assert.equal(f.providers.length,1); assert.equal(f.local.length,0); assert.equal(f.box.value,'cleaned dictation');
  assert.doesNotMatch(contentText(f.providers[0].messages[0].content),/Function load_rows/);
  f.api.renderTarget(); assert.match(f.el('ctx-info').textContent,/explicit choice/);
  assert.equal(f.el('send-btn').disabled,true); // subscription input remains unqualified
});

test('context fetch A→B→A and full source-identity mutation during awaits cannot request a provider',async()=>{
  for(const mode of ['revision','source','scope','page']) {
    const f=native(); f.box.value='retained'; let release;
    f.setRead(url=>url.includes('/context?')?new Promise(resolve=>{release=resolve;}):wire({...f.t,status:'complete',targetInfo:f.t}));
    const pending=f.api.refine(); await tick(()=>release);
    if(mode==='revision') {f.api.invalidateTarget(); f.api.invalidateTarget();}
    if(mode==='source') f.api.state.claudeTarget={...f.t,fileEpoch:ref('X')};
    if(mode==='scope') f.api.state.claudeTarget={...f.t,leafRef:ref('X')};
    if(mode==='page') f.api.state.pageEpoch='new page';
    release(wire({...f.t,status:'stored',targetRevision:0,text:'prose',bytes:5,turns:1,users:1,readAt:Date.now()}));
    await pending; assert.equal(f.providers.length,0,mode); assert.equal(f.box.value,'retained',mode);
  }
});

test('native source replacement after context but before provider and after inference refuses old epoch',async()=>{
  for(const phase of ['before','after']) {
    const f=native(); f.box.value='full draft'; let events=0;
    const context={...f.t,status:'stored',targetRevision:0,text:'prose',bytes:5,turns:1,users:1,readAt:Date.now()};
    f.setRead(async url=>url.includes('/context?')?wire(context):
      (++events === (phase==='before'?1:2) ? {ok:false,status:409,text:async()=>JSON.stringify({error:'FileEpoch replacement'})} : wire({...f.t,status:'complete',targetInfo:f.t})));
    await f.api.refine(); assert.equal(f.providers.length,phase==='before'?0:1); assert.equal(f.box.value,'full draft');
    assert.equal(f.copies.includes('cleaned dictation'),false); assert.match(f.el('status').textContent,/failed/);
  }
});

test('source replacement inside the actual result-clipboard await is checked again before draft application',async()=>{
  const f=native(); f.box.value='original full'; let replaced=false;
  f.ctx.navigator.clipboard.writeText=async text=>{f.copies.push(text);if(text==='cleaned dictation')replaced=true;};
  const ctx={...f.t,status:'stored',targetRevision:0,text:'prose',bytes:5,turns:1,users:1,readAt:Date.now()};
  f.setRead(async url=>url.includes('/context?')?wire(ctx):replaced?
    {ok:false,status:409,text:async()=>JSON.stringify({error:'source replaced during clipboard'})}:wire({...f.t,status:'complete',targetInfo:f.t}));
  await f.api.refine();assert.equal(f.providers.length,1);assert.equal(f.box.value,'original full');
  assert.match(f.el('status').textContent,/failed/);assert.equal(f.api.readOriginals()[0].text,'original full');
});

test('pending native provider versus draft edit-away/back, restore, retarget-away/back and page reload cannot apply or copy stale output',async()=>{
  for(const mode of ['draft','restore','target','page','context-choice']) {
    const f=native(); f.box.value='original full'; let release;
    f.setProvider(()=>new Promise(resolve=>{release=resolve;})); const pending=f.api.refine(); await tick(()=>release);
    if(mode==='draft'||mode==='restore') {f.box.value='other'; f.api.draftChanged(); f.box.value='original full'; f.api.draftChanged();}
    if(mode==='target') {f.api.invalidateTarget(); f.api.invalidateTarget();}
    if(mode==='page') f.api.state.pageEpoch='replacement';
    if(mode==='context-choice') f.storage.set('pane_claude_no_context_v1','true');
    release(streamResponse(messageEvents({content:[{type:'text',text:'stale output'}],stop_reason:'end_turn'})));
    await pending; assert.equal(f.box.value,'original full',mode); assert.equal(f.copies.includes('stale output'),false,mode);
  }
});

test('native original storage failure blocks BEFORE context/provider; clipboard denial does not lose full backup',async()=>{
  const bad=native(); bad.box.value='original'; bad.failWrites(); await bad.api.refine();
  assert.equal(bad.local.length,0); assert.equal(bad.providers.length,0); assert.equal(bad.box.value,'original');
  const good=native({clipboardFails:true}); good.box.value='original'; await good.api.refine();
  assert.equal(good.box.value,'cleaned dictation'); assert.equal(good.api.readOriginals()[0].text,'original');
  assert.match(good.el('status').textContent,/clipboard unavailable/);
});

test('full 25K and 200K multiline Unicode transcription is not context-budget cropped; all SSE text blocks retained',async()=>{
  for(const size of [25000,200000]) {
    const f=native(), text='dictated 😀\n\n'.repeat(Math.ceil(size/14))+'TAIL'; f.box.value=text;
    f.setProvider(()=>streamResponse(messageEvents({content:[{type:'text',text:'first\n'},{type:'text',text:'second 😀'}],stop_reason:'end_turn'})));
    await f.api.refine(); assert.equal(f.api.readOriginals()[0].text,text); assert.ok(contentText(f.providers[0].messages[0].content).includes(text));
    assert.equal(f.box.value,'first\nsecond 😀');
  }
});

test('native missing/limit/incomplete/canceled provider output preserves original with no partial result clipboard',async()=>{
  for(const reason of ['max_tokens',null,'tool_use']) {
    const f=native(); f.box.value='retained';
    f.setProvider(()=>streamResponse(messageEvents({content:[{type:'text',text:'partial'}],stop_reason:reason})));
    await f.api.refine(); assert.equal(f.box.value,'retained'); assert.equal(f.copies.includes('partial'),false);
  }
  const f=native(); f.box.value='retained'; f.setProvider((_url,opts)=>{f.api.state.refineAbort.abort(); return streamResponse(messageEvents({content:[{type:'text',text:'partial'}],stop_reason:'end_turn'}));});
  await f.api.refine(); assert.equal(f.box.value,'retained'); assert.equal(f.copies.includes('partial'),false);
});

test('native context view uses exact reader text and cannot reuse cached context after outage',async()=>{
  const f=native(); const ctx=await f.api.refreshContext(); assert.equal(f.api.state.ctx.text,ctx.text);
  f.setRead(()=>{throw new Error('outage');}); await f.api.refreshContext(); assert.equal(f.api.state.ctx,null);
  assert.match(f.el('ctx-info').textContent,/Context blocked/); assert.match(f.el('cache-info').textContent,/—/);
});

test('native Refine status timer restores capability rather than old U2 unconditional disable',async()=>{
  const f=native(), callbacks=[]; f.ctx.setTimeout=(fn,ms)=>{if(ms===2000)callbacks.push(fn);return 1;};
  f.box.value='original'; await f.api.refine();assert.equal(callbacks.length,1);callbacks[0]();
  assert.equal(f.el('refine-btn').disabled,false);assert.equal(f.el('send-btn').disabled,true);
});

// Retained from the removed per-build suite: target validation still governs which opaque
// main-history targets the pane may read (nullable leaf only for an empty native conversation).
test('validated Claude target refuses a nullable main leaf outside an empty native conversation',()=>{
  const f=pane();
  const t={schemaVersion:1,backend:'claude',targetRef:ref('A'),storeRef:ref('S'),sessionRef:ref('J'),scopeRef:ref('M'),
    leafRef:ref('L'),fileEpoch:ref('E'),uuid:'11111111-1111-4111-8111-111111111111',leafUuid:null,scope:'main',
    title:'Current conversation',source:'native',bindingRef:null};
  for(const [source,branches,diagnostics] of [['windows-history',[],[]],['native',[{},{}],[]],['native',[],[{}]]]){
    const target={...t,source};
    assert.throws(()=>f.api.validatedClaudeTarget({...target,status:'complete',events:[],branches,diagnostics,targetInfo:target},target.targetRef),source+'/'+branches.length+'/'+diagnostics.length);
  }
  const empty={...t,source:'native'};
  assert.equal(f.api.validatedClaudeTarget({...empty,status:'complete',events:[],branches:[],diagnostics:[],targetInfo:empty},empty.targetRef).leafUuid,null);
});

test('a clipboard write that never settles gives up instead of holding Refine read-only (audit, smaller list)',async()=>{
  const f=native();let fire=null;f.ctx.setTimeout=(fn,ms)=>{if(ms===2500)fire=fn;return 1;};
  f.ctx.navigator.clipboard.writeText=()=>new Promise(()=>{});
  const pending=f.api.copyText('x');assert.ok(fire,'a deadline is armed');fire();
  assert.equal(await pending,false);
});
