const test = require('node:test');
const assert = require('node:assert/strict');
const {pane} = require('./pane-fixture.cjs');

const target = digit => {
  const uuid = digit.repeat(8)+'-'+digit.repeat(4)+'-4'+digit.repeat(3)+'-8'+digit.repeat(3)+'-'+digit.repeat(12);
  return {schemaVersion:1,backend:'claude',targetRef:digit.repeat(32),uuid,executionId:'cc-'+uuid,generation:1,
    source:'host',role:'Manager',scope:'main',title:'Manager '+digit,capabilities:{nativeInput:true,controls:true}};
};
function host() {
  const receipts = new Map(), calls = [], live = {state:'ready',owner:'live',pending_message_uuids:[]};
  const fetch = async (url, options) => {
    const body = options?.body && JSON.parse(options.body), query = new URLSearchParams(url.split('?')[1]);
    const identity = body || Object.fromEntries(query);
    const t = [target('1'),target('2')].find(t=>t.executionId === identity.executionId);
    assert.ok(t, 'request belongs to a registered offline Manager');
    calls.push({url,body});
    let result;
    if (url.startsWith('/api/claude/state?')) result = {liveState:live};
    else if (url === '/api/claude/send') {
      const receipt = {execution_id:t.executionId,generation:t.generation,message_uuid:body.messageUuid,
        original:body.original,submitted:body.submitted,durable:true,acknowledged:false,queued:body.steer === true,status:'accepted'};
      receipts.set(body.messageUuid,receipt); result = {receipt};
    } else if (url.startsWith('/api/claude/receipt?')) result = {receipt:receipts.get(query.get('messageUuid')) || null};
    else throw new Error('unexpected offline request '+url);
    return {ok:true,status:200,text:async()=>JSON.stringify({schemaVersion:1,backend:'claude',
      targetRef:t.targetRef,executionId:t.executionId,generation:t.generation,uuid:t.uuid,...result})};
  };
  return {fetch,receipts,calls,live,posts:()=>calls.filter(c=>c.url === '/api/claude/send')};
}
function tab(storage, server, t = target('1')) {
  const f = pane({storage});
  f.ctx.fetch = server.fetch;
  f.api.state.backend = 'claude'; f.api.state.claudeTarget = t;
  f.api.wire(); f.api.initializeClaudeJournal();
  return f;
}
function edit(f, text) {
  f.box.value = text; f.api.draftChanged();
  assert.equal(f.api.saveDraft(),true); // the ordinary input debounce has saved this edit
}
const slot = t => t.executionId+'/'+t.generation;

test('F1: stale independent tabs merge Send slots; a third VM restores and queries both without another POST',async()=>{
  const storage = new Map(), server = host();
  const a = tab(storage,server,target('1')), b = tab(storage,server,target('2'));
  assert.notEqual(a.api.state,b.api.state);
  edit(a,'A full original 😀'); await a.api.send();
  edit(b,'B full original 漢字'); await b.api.send();
  // A still has no B operation in memory when its receipt update saves its slot again.
  assert.equal(a.api.state.claudeSends.size,1); await a.api.pollClaudeManager();
  assert.deepEqual(JSON.parse(storage.get(a.api.K.claudeSend)).slots.sort(),[slot(a.api.state.claudeTarget),slot(b.api.state.claudeTarget)].sort());
  const reload = tab(storage,server);
  assert.equal(reload.api.state.claudeJournalError,null);
  assert.equal(reload.api.state.claudeSends.size,2);
  assert.deepEqual([...reload.api.state.claudeSends.values()].map(o=>o.original).sort(),['A full original 😀','B full original 漢字']);
  const before = server.calls.length; await reload.api.pollClaudeManager();
  assert.equal(server.calls.slice(before).filter(c=>c.url.startsWith('/api/claude/receipt?')).length,2);
  assert.equal(server.posts().length,2);
});

test('F1: old ACK preserves another VM’s newer persisted draft and a fresh page restores it',async()=>{
  const storage = new Map(), server = host(), a = tab(storage,server), b = tab(storage,server);
  edit(a,'A sent original'); await a.api.send();
  edit(b,'B newer unsent text 😀\nTHE TAIL');
  server.receipts.get(a.api.state.claudeSend.messageUuid).acknowledged = true;
  await a.api.pollClaudeManager();
  assert.equal(storage.get(a.api.K.draft),'B newer unsent text 😀\nTHE TAIL');
  assert.equal(a.box.value,'A sent original');
  assert.match(a.api.state.claudeSend.note,/stored draft changed; draft retained/);
  assert.doesNotMatch(a.api.state.claudeSend.note,/unchanged draft cleared/);
  const reload = tab(storage,server); await reload.api.pollClaudeManager();
  assert.equal(reload.box.value,'B newer unsent text 😀\nTHE TAIL');
  assert.equal(storage.get(a.api.K.draft),reload.box.value);
  assert.equal(server.posts().length,1);
});

test('F1: unchanged saved original still clears on exact ACK with one POST',async()=>{
  const storage = new Map(), server = host(), a = tab(storage,server);
  edit(a,'  unchanged original 😀\n '); await a.api.send();
  server.receipts.get(a.api.state.claudeSend.messageUuid).acknowledged = true;
  await a.api.pollClaudeManager();
  assert.equal(a.box.value,''); assert.equal(storage.get(a.api.K.draft),'');
  assert.equal(storage.get(a.api.K.lastSent),'unchanged original 😀');
  assert.match(a.api.state.claudeSend.note,/unchanged draft cleared/);
  const reload = tab(storage,server); await reload.api.pollClaudeManager();
  assert.equal(reload.box.value,''); assert.equal(server.posts().length,1);
});

test('F1: independent control slots merge through the same production journal writer',()=>{
  const storage = new Map(), server = host(), a = tab(storage,server,target('1')), b = tab(storage,server,target('2'));
  for (const f of [a,b,a]) f.api.saveClaudeJournal(f.api.K.claudeControl,{schemaVersion:1,target:f.api.state.claudeTarget,
    requestId:f.api.state.claudeTarget.uuid,operation:'apply_effort',value:'high',settled:false});
  const reload = tab(storage,server);
  assert.equal(reload.api.state.claudeJournalError,null); assert.equal(reload.api.state.claudeControls.size,2);
  assert.equal(server.posts().length,0);
});

test('F1: latest corrupt references are preserved and refuse a stale tab’s new Send',async()=>{
  const storage = new Map(), server = host(), a = tab(storage,server);
  const index = JSON.stringify({schemaVersion:2,slots:[slot(target('2'))]});
  storage.set(a.api.K.claudeSend,index);
  edit(a,'keep original'); await a.api.send();
  assert.equal(server.posts().length,0); assert.equal(a.box.value,'keep original');
  assert.equal(storage.get(a.api.K.claudeSend),index);
  assert.match(a.el('status').textContent,/reference missing or changed/);
});

test('F1: latest legacy record migrates even outside the stale tab’s Map; migration failure keeps the legacy index',async()=>{
  for (const failMigration of [false,true]) {
    const storage = new Map(), server = host(), a = tab(storage,server,target('1')), b = tab(storage,server,target('2'));
    edit(b,'legacy full original 😀'); await b.api.send();
    const legacy = JSON.stringify(b.api.state.claudeSend), key = a.api.K.claudeSend;
    storage.set(key,legacy); storage.delete(key+':'+slot(target('2')));
    if (failMigration) {
      const write = a.ctx.localStorage.setItem;
      a.ctx.localStorage.setItem = (k,v) => { if (k !== key+':'+slot(target('2'))) write(k,v); };
    }
    edit(a,'new original'); await a.api.send();
    if (failMigration) {
      assert.equal(storage.get(key),legacy); assert.equal(server.posts().length,1);
      assert.match(a.el('status').textContent,/legacy journal migration readback failed/);
    } else {
      const reload = tab(storage,server);
      assert.equal(reload.api.state.claudeSends.size,2); assert.equal(server.posts().length,2);
      assert.equal(reload.api.state.claudeSends.get(slot(target('2'))).original,'legacy full original 😀');
    }
  }
});

test('F1: failed draft-clear readback retains the visible original and only queries the receipt',async()=>{
  const storage = new Map(), server = host(), a = tab(storage,server);
  edit(a,'saved original'); await a.api.send();
  const write = a.ctx.localStorage.setItem;
  a.ctx.localStorage.setItem = (k,v) => { if (k !== a.api.K.draft || v !== '') write(k,v); };
  server.receipts.get(a.api.state.claudeSend.messageUuid).acknowledged = true;
  await a.api.pollClaudeManager(); await a.api.pollClaudeManager();
  assert.equal(a.box.value,'saved original'); assert.equal(storage.get(a.api.K.draft),'saved original');
  assert.match(a.api.state.claudeSend.note,/draft NOT cleared: draft clear readback failed/);
  assert.equal(server.posts().length,1);
});

test('F1: failed Send-time draft flush does not block Send or treat unrelated stored text as its own',async()=>{
  for (const persisted of [null,'unrelated text already saved before Send']) {
    const storage = new Map(), server = host(), a = tab(storage,server);
    if (persisted !== null) storage.set(a.api.K.draft,persisted);
    a.box.value = 'original not yet debounced'; a.api.draftChanged();
    const write = a.ctx.localStorage.setItem;
    a.ctx.localStorage.setItem = (k,v) => { if (k === a.api.K.draft) throw new Error('draft-only quota failure'); write(k,v); };
    await a.api.send(); server.receipts.get(a.api.state.claudeSend.messageUuid).acknowledged = true;
    await a.api.pollClaudeManager();
    assert.equal(storage.get(a.api.K.draft) ?? null,persisted);
    assert.equal(a.box.value,'original not yet debounced'); assert.equal(server.posts().length,1);
    assert.match(a.api.state.claudeSend.note,/draft NOT saved at Send; draft retained/);
  }
});

test('F1: Send flushes a pending input debounce before an immediate queued-steer clear',async()=>{
  const storage = new Map(), server = host(), a = tab(storage,server);
  storage.set(a.api.K.draft,'previous debounced draft');
  a.box.value = 'latest dictation inside 400 ms'; a.box.oninput();
  server.live.pending_message_uuids = ['another-device-message'];
  await a.api.send();
  assert.equal(server.posts().length,1); assert.equal(server.posts()[0].body.steer,true);
  assert.equal(server.posts()[0].body.original,'latest dictation inside 400 ms');
  assert.equal(a.box.value,''); assert.equal(storage.get(a.api.K.draft),'');
  assert.match(a.api.state.claudeSend.note,/unchanged draft cleared/);
});

test('F1: OpenCode’s automatic successful-Send clear also preserves another tab’s saved text',async()=>{
  const storage = new Map(), a = pane({storage}), b = pane({storage});
  a.api.state.target = {id:'ses_offline',title:'OpenCode Manager',agent:'manager'};
  edit(a,'OpenCode sent original');
  const fetch = a.ctx.fetch;
  a.ctx.fetch = async (url,options) => {
    const response = await fetch(url,options);
    if (url === '/api/send') edit(b,'B later draft');
    return response;
  };
  await a.api.send();
  assert.equal(a.box.value,'OpenCode sent original'); assert.equal(storage.get(a.api.K.draft),'B later draft');
  const reload = pane({storage}); reload.api.wire(); assert.equal(reload.box.value,'B later draft');
  assert.equal(a.requests.filter(([url])=>url === '/api/send').length,1);
});
