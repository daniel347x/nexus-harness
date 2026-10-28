const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../watcher.js'), 'utf8');

function watcher() {
  const attrs = { 'data-session-id': 'ses_fixture', 'data-instance-id': 'fixture-instance' };
  const input = label => ({ getAttribute: key => key === 'aria-label' ? label.value : null });
  const modelLabel = { value: 'Model: GPT-6.1 Sol, provider: OpenAI, ID: openai/gpt-6.1-sol' };
  const thinkingLabel = { value: 'Thinking: xhigh' };
  const model = { getClientRects: () => [1], querySelector: () => input(modelLabel) };
  const thinking = { getClientRects: () => [1], querySelector: () => input(thinkingLabel) };
  const controls = { getClientRects: () => [1], querySelectorAll: q => q === '[data-model-selector-control]' ? [model] : [thinking] };
  const pane = { getClientRects: () => [1], getAttribute: key => attrs[key], querySelectorAll: () => [controls] };
  const stream = { getClientRects: () => [1], getAttribute: key => attrs[key], closest: () => pane };
  const streams = [stream], rows = [];
  const writes = [], timers = [];
  let observer, observerOptions, heartbeat, hold;
  const context = {
    document: {
      documentElement: {},
      querySelectorAll: q => q.startsWith('.message-stream') ? streams : rows,
      querySelector: () => ({ textContent: 'Fixture session' }),
    },
    CSS: { escape: s => s }, crypto: { randomUUID: () => 'fixture-watcher' },
    AbortSignal: { timeout: () => ({}) },
    Date: { now: () => 1000000 + writes.length },
    MutationObserver: class { constructor(cb) { observer = cb; } observe(el, options) { observerOptions = options; } },
    setTimeout: cb => { timers.push(cb); return 1; }, clearTimeout: () => {},
    setInterval: cb => { heartbeat = cb; },
    fetch: async (url, options) => {
      assert.equal(url, 'http://127.0.0.1:9911/api/active-session');
      writes.push(JSON.parse(options.body));
      if (hold) await hold;
      return { ok: true };
    },
  };
  vm.createContext(context); vm.runInContext(source, context);
  return { writes, modelLabel, thinkingLabel, pane, stream, streams, rows, model, thinking,
    options: () => observerOptions, mutate: () => { observer(); while (timers.length) timers.shift()(); },
    heartbeat: () => heartbeat(), hold: p => { hold = p; } };
}
const tick = () => new Promise(r => setImmediate(r));

test('initial report uses pinned model identity and selected xhigh', async () => {
  const w = watcher(); await tick();
  assert.deepEqual(w.writes[0].thinking, { providerID: 'openai', id: 'gpt-6.1-sol', variant: 'xhigh' });
  assert.equal(w.writes[0].sessionId, 'ses_fixture');
  assert.ok(w.options().attributeFilter.includes('aria-label'));
});
test('thinking-only aria-label changes report medium and explicit Default without switching session', async () => {
  const w = watcher(); await tick();
  for (const label of ['medium', 'Default']) {
    w.thinkingLabel.value = 'Thinking: ' + label; w.mutate(); await tick();
    assert.equal(w.writes.at(-1).thinking.variant, label === 'Default' ? null : label);
    assert.equal(w.writes.at(-1).sessionId, 'ses_fixture');
  }
});
test('unknown, absent, wrong-locale labels and hidden controls are not Default', async () => {
  for (const change of [w => { w.thinkingLabel.value = ''; }, w => { w.thinkingLabel.value = 'Pensée: Default'; },
    w => { w.modelLabel.value = 'stale assistant medium'; }, w => { w.thinking.getClientRects = () => []; }]) {
    const w = watcher(); await tick(); change(w); w.mutate(); await tick();
    assert.equal(w.writes.at(-1).thinking, null);
  }
});
test('selector from another session/instance or row fallback is never associated', async () => {
  for (const change of [w => { w.pane.getAttribute = k => k === 'data-session-id' ? 'ses_other' : 'fixture-instance'; },
    w => { w.pane.getAttribute = k => k === 'data-instance-id' ? 'other-instance' : 'ses_fixture'; },
    w => { w.stream.closest = () => null; }]) {
    const w = watcher(); await tick(); change(w); w.mutate(); await tick();
    assert.equal(w.writes.at(-1).thinking, null);
  }
});
test('hidden/multiple visible panes invalidate selection; disappearance reports null', async () => {
  const w = watcher(); await tick();
  w.streams.push({ ...w.stream, getClientRects: () => [] }); w.mutate(); await tick();
  assert.equal(w.writes.length, 1, 'hidden mounted pane ignored');
  w.streams[1].getClientRects = () => [1]; w.mutate(); await tick();
  assert.equal(w.writes.at(-1).sessionId, null);
  w.streams.length = 0; w.mutate(); await tick();
  assert.equal(w.writes.at(-1).thinking, null);
});
test('heartbeats repeat unchanged choice; writes serialized with coalesced latest choice', async () => {
  const w = watcher(); await tick();
  let release; w.hold(new Promise(r => { release = r; }));
  w.heartbeat();
  w.thinkingLabel.value = 'Thinking: medium'; w.mutate();
  w.thinkingLabel.value = 'Thinking: Default'; w.mutate();
  assert.equal(w.writes.length, 2, 'one request in flight');
  release(); await tick();
  assert.equal(w.writes.length, 3, 'superseded medium never posted');
  assert.equal(w.writes.at(-1).thinking.variant, null);
  assert.ok(w.writes.at(-1).sequence > w.writes[1].sequence);
});
