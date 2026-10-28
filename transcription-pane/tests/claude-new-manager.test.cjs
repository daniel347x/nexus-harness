// New Manager from the pane: one named fresh conversation, selected for Send once ready.
// Real app.js in the offline VM; inert transport; no process, socket, port or inference.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const {pane} = require('./pane-fixture.cjs');
const ORDER = fs.readFileSync(path.join(__dirname, '../static/manager-order.js'), 'utf8');
const ref = c => c.repeat(32);
const OLD = '11111111-1111-4111-8111-111111111111';
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise(resolve => setImmediate(resolve)); };
const response = (data, status = 200) => ({ok: status < 400, status, text: async () => JSON.stringify(data)});

function oldTarget() {
  return {schemaVersion:1,backend:'claude',targetRef:ref('T'),storeRef:ref('S'),sessionRef:ref('J'),scopeRef:ref('M'),
    leafRef:ref('L'),fileEpoch:ref('E'),uuid:OLD,leafUuid:null,scope:'main',source:'host',title:'Manager-Session-182',
    cwd:'/w',executionId:'cc-'+OLD,generation:3,role:'Manager',lifecycle:'live',capabilities:{nativeInput:true,controls:true,reconnect:true}};
}

function setup({launchError = null, ready = true, managerNames = [], order = true} = {}) {
  const f = pane();
  // The sessions frame's manager-order.js and its Manager list (opcSidebar.managerNames), as on the real page.
  if (order) vm.runInContext(ORDER, f.ctx);
  vm.runInContext('Object.assign(this.api,{openNewSub,launchNewSub,saveFirstMessages})', f.ctx);
  f.api.state.backend = 'claude';
  f.api.state.claudeTarget = oldTarget();
  const server = {calls: [], launched: null, ready, launchError, posted: [], sends: [],
    messages: {manager: 'Greetings · this is going to be Manager-Session-{N}.', subSession: 'Greetings · sub-session {N}-{letter} of Manager-Session-{N}.\n\nReason for this sub-session: '}};
  f.el('claude-sessions-frame').contentWindow = {postMessage: message => server.posted.push(message),
    opcManagerOrder: order ? f.ctx.opcManagerOrder : undefined, opcSidebar: {managerNames: () => managerNames}};
  const info = () => ({schemaVersion:1,backend:'claude',targetRef:ref('N'),storeRef:ref('s'),sessionRef:ref('a'),scopeRef:ref('b'),
    leafRef:ref('c'),fileEpoch:ref('d'),uuid:server.launched,leafUuid:null,scope:'main',title:server.name,source:'host',
    executionId:'cc-'+server.launched,generation:1,role:'Manager',lifecycle:'live',capabilities:{nativeInput:true,controls:true,reconnect:true}});
  f.ctx.fetch = async (url, options) => {
    const body = options?.body && JSON.parse(options.body);
    server.calls.push({url, body});
    if (url === '/api/claude/launch-defaults')
      return response({schemaVersion:1,backend:'claude',name:'Manager-Session-183',model:'claude-opus-5-5',effort:'xhigh'});
    if (url === '/api/claude/first-messages') {
      if (body) for (const k of ['manager','subSession']) if (k in body) server.messages[k] = body[k] || 'default '+k;
      return response({schemaVersion:1,backend:'claude',...server.messages,defaults:{},custom:{}});
    }
    if (url === '/api/claude/send') {
      server.sends.push(body);
      const t = info();
      return response({schemaVersion:1,backend:'claude',targetRef:t.targetRef,executionId:t.executionId,generation:t.generation,uuid:t.uuid,
        status:'accepted',receipt:{execution_id:t.executionId,generation:t.generation,message_uuid:body.messageUuid,original:body.original,
          submitted:body.submitted,durable:true,acknowledged:true}});
    }
    if (url === '/api/claude/launch-manager') {
      if (server.launchError) return response({error: server.launchError}, 409);
      server.launched = body.nativeUuid; server.name = body.name;
      return response({schemaVersion:1,backend:'claude',executionId:'cc-'+body.nativeUuid,uuid:body.nativeUuid,name:body.name,
        generation:1,state:'initializing',hostStarted:false,sent:false});
    }
    if (url.startsWith('/api/claude/sessions')) {
      const rows = server.launched ? [{source:'host',uuid:server.launched,role:'Manager',executionId:'cc-'+server.launched,generation:1,
        state:server.ready?'ready':'initializing',lifecycle:server.ready?'live':'pending',
        capabilities:{nativeInput:server.ready,controls:server.ready},sessionRef:ref('a'),scopes:[{label:'main',scopeRef:ref('b')}]}] : [];
      return response({schemaVersion:1,backend:'claude',sessions:rows,hasMore:false});
    }
    if (/^\/api\/claude\/(events|target)\?/.test(url)) {
      const t = info();
      return response({schemaVersion:1,backend:'claude',targetRef:t.targetRef,status:'complete',sessionRef:t.sessionRef,
        scopeRef:t.scopeRef,leafRef:t.leafRef,fileEpoch:t.fileEpoch,events:[],targetInfo:t});
    }
    if (url.startsWith('/api/claude/state?')) return response({error:'not needed'}, 409);
    throw new Error('unexpected request '+url);
  };
  return {f, server};
}

test('New Manager launches one named conversation and selects it once ready; the old Manager is untouched', async () => {
  const {f, server} = setup();
  f.api.state.claudeManager = {targetRef:ref('T'),liveState:{pending_message_uuids:['m'],native_session_state:'running'}};
  await f.api.openNewManager();
  assert.equal(f.el('claude-new-name').value, 'Manager-Session-183');
  assert.match(f.el('claude-new-status').textContent, /Manager-Session-182 is busy .*keeps running/);
  f.box.value = 'my unsent dictation';
  await f.api.launchNewManager();
  await settle();
  const post = server.calls.find(c => c.url === '/api/claude/launch-manager').body;
  assert.deepEqual(Object.keys(post).sort(), ['backend','effort','model','name','nativeUuid','schemaVersion']);
  assert.match(post.nativeUuid, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.deepEqual([post.name, post.model, post.effort], ['Manager-Session-183', 'claude-opus-5-5', 'xhigh']);
  assert.equal(f.api.state.claudeTarget.uuid, post.nativeUuid);
  assert.equal(f.api.state.claudeTarget.title, 'Manager-Session-183');
  assert.equal(f.api.isClaudeManager(), true);
  assert.equal(f.api.state.claudeLaunch, null);
  assert.equal(f.box.value, 'my unsent dictation');  // the draft is never touched
  // Nothing went to the old Manager (no control or reconnect at all); the new one got its first message, once: the
  // saved default with its number filled in, never the draft, and its ACK neither clears nor copies anything.
  assert.ok(!server.calls.some(c => /\/api\/claude\/(control|reconnect)/.test(c.url)));
  assert.equal(server.sends.length, 1);
  assert.deepEqual([server.sends[0].executionId, server.sends[0].original, server.sends[0].submitted],
    ['cc-'+post.nativeUuid, 'Greetings · this is going to be Manager-Session-183.', 'Greetings · this is going to be Manager-Session-183.']);
  assert.equal(f.api.state.claudeSend.opening, true);
  assert.equal(f.api.state.claudeSend.acknowledged, true);
  assert.equal(f.box.value, 'my unsent dictation');
  assert.deepEqual(f.copies, []);
  assert.match(f.el('status').textContent, /First message sent to Manager-Session-183/);
  assert.deepEqual(server.posted.map(m => m.type), ['host-launch', 'browse-session']);
  assert.equal(server.posted[1].uuid, post.nativeUuid);
});

test('an explicit high choice survives refreshed defaults and is launched unchanged', async () => {
  const {f, server} = setup();
  vm.runInContext('wireNewManager()', f.ctx);
  await f.api.openNewManager();
  f.el('claude-new-effort').value = 'high';
  f.el('claude-new-effort').onchange({target:f.el('claude-new-effort')});
  await f.api.openNewManager();
  assert.equal(f.el('claude-new-effort').value, 'high');
  await f.api.launchNewManager();
  assert.equal(server.calls.find(c => c.url === '/api/claude/launch-manager').body.effort, 'high');
});

test('with its checkbox off New Manager sends nothing; without manager-order.js it launches anyway and says so', async () => {
  for (const variant of ['unticked', 'no-order']) {
    const {f, server} = setup({order: variant !== 'no-order'});
    f.el('claude-new-name').value = 'Manager-Session-184';
    if (variant === 'unticked') f.el('claude-new-opening').checked = false;
    await f.api.launchNewManager();
    await settle();
    assert.equal(f.api.state.claudeTarget.title, 'Manager-Session-184', variant);
    assert.equal(server.sends.length, 0, variant);
    // No form defaults were loaded: the empty-field launch fallback must agree with the HTML and route.
    assert.equal(server.calls.find(c => c.url === '/api/claude/launch-manager').body.effort, 'xhigh', variant);
    assert.ok(!server.calls.some(c => c.url === '/api/claude/first-messages'), variant);
  }
});

test('↳ Sub-session names the newest primary’s next letter, fills the saved default and sends the edited text as the first message', async () => {
  const {f, server} = setup({managerNames: [['Manager-Session-219', 'x'], ['Manager-Session-219-a'], ['Manager-Session-219-c'], [null, 'Manager-Session-218-f']]});
  await f.api.openNewSub();
  assert.equal(f.el('claude-new-sub-form').hidden, false);
  assert.equal(f.el('claude-new-sub-title').textContent, 'Sub-session 219-d of Manager-Session-219');
  assert.equal(f.el('claude-new-sub-text').value, 'Greetings · sub-session 219-d of Manager-Session-219.\n\nReason for this sub-session: ');
  f.el('claude-new-sub-text').value += 'check the test run';
  await f.api.openNewSub();  // reopening keeps Dan's text for the same name
  assert.match(f.el('claude-new-sub-text').value, /check the test run$/);
  f.box.value = 'my unsent dictation';
  await f.api.launchNewSub();
  await settle();
  const post = server.calls.find(c => c.url === '/api/claude/launch-manager').body;
  assert.equal(post.name, 'Manager-Session-219-d');
  assert.equal(post.effort, 'xhigh');
  assert.equal(f.el('claude-new-sub-form').hidden, true);
  assert.equal(f.api.state.claudeTarget.title, 'Manager-Session-219-d');
  assert.equal(server.sends.length, 1);
  assert.equal(server.sends[0].original, 'Greetings · sub-session 219-d of Manager-Session-219.\n\nReason for this sub-session: check the test run');
  assert.equal(f.box.value, 'my unsent dictation');
});

test('↳ Sub-session with no numbered Manager listed starts nothing; Save and Restore defaults write the shared copy', async () => {
  const {f, server} = setup({managerNames: [[null, 'a4dfde44-748a-488c-9f8c-d46bad861f04']]});
  await f.api.openNewSub();
  assert.match(f.el('claude-new-status').textContent, /No numbered Manager-Session in the list yet/);
  await f.api.launchNewSub();
  assert.ok(!server.calls.some(c => c.url === '/api/claude/launch-manager'));
  f.el('claude-first-manager').value = 'Hi {N}'; f.el('claude-first-sub').value = 'Sub {N}-{letter}';
  await f.api.saveFirstMessages(false);
  assert.deepEqual(server.calls.at(-1).body, {schemaVersion:1, backend:'claude', manager:'Hi {N}', subSession:'Sub {N}-{letter}'});
  await f.api.saveFirstMessages(true);
  assert.deepEqual(server.calls.at(-1).body, {schemaVersion:1, backend:'claude', manager:'', subSession:''});
  assert.equal(f.el('claude-first-manager').value, 'default manager');
  assert.match(f.el('claude-new-status').textContent, /restored/);
});

test('a launch still starting is selected on a later poll, and survives a reload', async () => {
  const {f, server} = setup({ready: false});
  f.el('claude-new-name').value = 'Manager-Session-190';
  await f.api.launchNewManager();
  await settle();
  assert.equal(f.api.state.claudeTarget.uuid, OLD);
  assert.equal(f.api.state.claudeLaunch.phase, 'waiting');
  assert.match(f.el('claude-new-status').textContent, /starting .* selected automatically/);
  const saved = JSON.parse(f.storage.get('pane_claude_launch_v1'));
  assert.equal(saved.uuid, server.launched);
  f.api.state.claudeLaunch = null; f.api.restoreLaunch();      // a reload restores the watch
  assert.equal(f.api.state.claudeLaunch.uuid, server.launched);
  server.ready = true;
  await f.api.adoptLaunchedManager();
  assert.equal(f.api.state.claudeTarget.uuid, server.launched);
  assert.equal(f.storage.get('pane_claude_launch_v1'), undefined);
});

test('a refused launch shows the reason and changes nothing', async () => {
  const {f, server} = setup({launchError: 'the Manager prompt could not be composed: anchor drift; nothing was launched'});
  f.el('claude-new-name').value = 'Manager-Session-183';
  await f.api.launchNewManager();
  await settle();
  assert.match(f.el('claude-new-status').textContent, /not launched: the Manager prompt could not be composed/);
  assert.equal(f.api.state.claudeTarget.uuid, OLD);
  assert.equal(f.api.state.claudeLaunch, null);
  assert.ok(!server.calls.some(c => c.url.startsWith('/api/claude/sessions')));
});

test('choosing another target while the new Manager starts wins over the automatic selection', async () => {
  const {f, server} = setup({ready: false});
  f.el('claude-new-name').value = 'Manager-Session-183';
  await f.api.launchNewManager();
  await settle();
  f.api.state.claudeTarget = oldTarget();
  // Dan explicitly picks a row: the same post-accept rule handleTargetMessage applies.
  const launch = f.api.state.claudeLaunch;
  assert.ok(launch);
  f.api.state.claudeLaunch = null;
  server.ready = true;
  await f.api.adoptLaunchedManager();
  assert.equal(f.api.state.claudeTarget.uuid, OLD);
});

test('an empty name is refused before any request', async () => {
  const {f, server} = setup();
  f.el('claude-new-name').value = '   ';
  await f.api.launchNewManager();
  assert.match(f.el('claude-new-status').textContent, /Type a name first/);
  assert.equal(server.calls.length, 0);
});

test('guard sweep: an earlier Send whose turn completed never holds later Sends, even without a byte-exact echo', () => {
  const {f} = setup();
  const t = f.api.state.claudeTarget;
  const op = {schemaVersion:1,target:t,messageUuid:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',original:'x',submitted:'x',acknowledged:false};
  f.api.state.claudeSend = op;
  assert.equal(f.api.pendingConversationSend(t), op);                       // still unresolved: held
  op.receipt = {durable:true, completed:true};                              // native result for that UUID
  assert.equal(f.api.pendingConversationSend(t), null);
});
