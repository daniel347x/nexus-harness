const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = app.indexOf('async function send() {');
const end = app.indexOf('\n}', start) + 2;
assert.ok(start >= 0 && end > start);

function pane({ error, busy = false, confirm = () => true, mode = 'follow', source = 'codenomad', activeHook, sendHook } = {}) {
  const box = { value: 'hello\n\nworld', focus() {} }, button = {};
  const writes = [], confirmations = [], statuses = [], storage = {};
  const state = { draftRevision: 0, target: { id: 'ses_fixture', title: 'fixture', agent: 'manager' }, targetSource: source, watcher: { revision: 3 } };
  let draftClears = 0;
  const ctx = {
    state, $: id => id === 'transcript' ? box : button,
    status: (s, kind) => statuses.push([s, kind]),
    confirm: s => { confirmations.push(s); return confirm(s); },
    ls: { get: k => k === 'mode' ? mode : storage[k] ?? null,
      set: (k, v) => { storage[k] = v; if (k === 'draft' && v === '') draftClears++; } },
    K: { targetMode: 'mode', lastSent: 'last', draft: 'draft' }, draftTimer: null,
    oc: async (method, route) => {
      assert.equal(method, 'GET'); assert.equal(route, '/api/session/active');
      if (activeHook) await activeHook(state);
      return { data: busy ? { ses_fixture: {} } : {} };
    },
    fetch: async (route, options) => {
      assert.equal(route, '/api/send');
      writes.push(JSON.parse(options.body));
      if (sendHook) await sendHook(box);
      return { ok: !error, json: async () => error ? { error } : { data: { id: 'fixture-id' }, thinking: busy ? 'thinking unchanged (queued: selector not applied)' : 'thinking xhigh' } };
    },
    navigator: { clipboard: { writeText: async () => {} } },
    retainOriginal: () => true, copyText: async () => true,
    setTimeout: () => {}, clearTimeout() {}, refreshContext: () => {},
  };
  // Lift the real persistence helpers too: the ordinary Send-time flush is not
  // a destructive clear, and clearSavedDraft must read the actual saved original.
  const helpers = ['draftChanged','saveDraft','clearSavedDraft'].map(name=>{
    const from = app.indexOf('function '+name+'() {') >= 0 ? app.indexOf('function '+name+'() {') : app.indexOf('function '+name+'(original) {');
    assert.ok(from >= 0); return app.slice(from,app.indexOf('\n}',from)+2);
  }).join('\n');
  vm.createContext(ctx); vm.runInContext(helpers+'\n'+app.slice(start, end), ctx);
  return { ctx, box, button, writes, confirmations, statuses, storage, clears: () => draftClears };
}

test('normal Send carries session authority, preserves paragraphs and clears draft only on success', async () => {
  const p = pane(); await p.ctx.send();
  assert.deepEqual(p.writes[0], { text: 'hello\n\nworld', sessionId: 'ses_fixture', targetMode: 'follow', watcherRevision: 3 });
  assert.equal(p.box.value, ''); assert.equal(p.storage.last, 'hello\n\nworld');
  assert.match(p.statuses.at(-1)[0], /thinking xhigh/);
  assert.equal(p.clears(), 1); assert.equal(p.button.disabled, false);
  assert.equal(p.ctx.state.draftRevision,1);
});
test('failed switch retains transcript and lastSent, never falls back to /oc prompt', async () => {
  const p = pane({ error: 'thinking switch failed; prompt NOT sent' }); await p.ctx.send();
  assert.equal(p.box.value, 'hello\n\nworld'); assert.equal(p.clears(), 0); assert.equal(p.storage.last, undefined);
  assert.equal(p.storage.draft,'hello\n\nworld'); // ordinary Send-time debounce flush, not a clear
  assert.match(p.statuses.at(-1)[0], /switch failed.*still in the box/);
});
test('queued Send explicitly warns that selector will not be applied', async () => {
  const p = pane({ busy: true }); await p.ctx.send();
  assert.equal(p.writes[0].delivery, 'queue');
  assert.match(p.confirmations[0], /NOT apply.*thinking selector/);
  assert.match(p.statuses.at(-1)[0], /queued.*selector not applied/);
});
test('cancelled queue leaves draft and reenables Send', async () => {
  const p = pane({ busy: true, confirm: () => false }); await p.ctx.send();
  assert.equal(p.writes.length, 0); assert.equal(p.box.value, 'hello\n\nworld');
  assert.equal(p.button.disabled, false); assert.equal(p.ctx.state.sending, false);
});
test('target/selection change during active check refuses Send', async () => {
  for (const change of [s => { s.target.id = 'ses_other'; }, s => { s.watcher.revision++; }]) {
    const p = pane({ activeHook: change }); await p.ctx.send();
    assert.equal(p.writes.length, 0); assert.match(p.statuses.at(-1)[0], /target\/selection changed/);
    assert.equal(p.box.value, 'hello\n\nworld');
  }
});
test('stale and non-manager confirmations remain intact', async () => {
  const p = pane({ source: 'stale' }); p.ctx.state.target.agent = 'worker'; await p.ctx.send();
  assert.equal(p.confirmations.length, 2);
  assert.match(p.confirmations[0], /watcher has gone quiet/); assert.match(p.confirmations[1], /not a Manager Session/);
});
test('manual target is identified, not treated as a followed thinking selection', async () => {
  const p = pane({ mode: 'ses_fixture' }); await p.ctx.send();
  assert.equal(p.writes[0].targetMode, 'manual');
});
test('duplicate hotkeys cannot send twice while awaiting active status', async () => {
  let release;
  const p = pane({ activeHook: () => new Promise(r => { release = r; }) });
  const first = p.ctx.send(); await p.ctx.send(); release(); await first;
  assert.equal(p.writes.length, 1);
});
test('draft edited while Send is in flight is not erased', async () => {
  const p = pane({ sendHook: box => { box.value = 'new dictation'; } }); await p.ctx.send();
  assert.equal(p.box.value, 'new dictation'); assert.equal(p.clears(), 0);
});
