// Offline VM/DOM doubles only. No browser, keys, upstream requests or paid calls.
const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const { pane, contentText } = require('./pane-fixture.cjs');
const original = 'Start\n\n' + 'brain dump with detail '.repeat(10000) + '\nTAIL: 你好 👩🏽‍💻 e\u0301';
function anth(reason = 'end_turn') { return { stop_reason: reason, content: [{ type: 'text', text: 'cleaned' }] }; }
function chat(reason = 'stop') { return { choices: [{ finish_reason: reason, message: { content: 'cleaned' } }] }; }

test('every provider rejects limit/incomplete/refusal/unknown/missing statuses without retry or overwrite', async () => {
  for (const provider of ['anthropic', 'openrouter', 'deepinfra']) {
    const reasons = provider === 'anthropic' ? ['max_tokens', 'refusal', 'pause_turn', 'tool_use', 'stop_sequence', 'cancelled', 'unexpected', null] : ['length', 'max_tokens', 'content_filter', 'tool_calls', 'cancelled', 'error', 'unexpected', null];
    const bad = reasons.map(r => provider === 'anthropic' ? anth(r) : chat(r));
    bad.push({ ...(provider === 'anthropic' ? anth() : chat()), status: 'incomplete' });
    bad.push({ ...(provider === 'anthropic' ? anth() : chat()), incomplete_details: { reason: 'max_output_tokens' } });
    bad.push({ ...(provider === 'anthropic' ? anth() : chat()), error: { message: 'provider failed' } });
    bad.push(provider === 'anthropic' ? { stop_reason: 'end_turn', content: [{ type: 'refusal' }] } : { choices: [{ finish_reason: 'stop', message: { content: 'partial', refusal: 'refused' } }] });
    bad.push(provider === 'anthropic' ? { stop_reason: 'end_turn', content: [{ type: 'text', text: '  ' }] } : { choices: [{ finish_reason: 'stop', message: { content: '  ' } }] });
    if (provider !== 'anthropic') bad.push({ choices: [{ finish_reason: 'stop', native_finish_reason: 'max_tokens', message: { content: 'partial' } }] });
    for (const response of bad) {
      const p = pane({ provider, response }); p.box.value = original;
      await p.api.refine();
      assert.equal(p.box.value, original, JSON.stringify(response));
      assert.equal(p.api.readOriginals()[0].text, original);
      assert.equal(p.requests.length, 1, 'semantic rejection must not trigger paid retries');
      assert.match(p.el('status').textContent, /refine failed.*not accepted/);
      assert.equal(p.box.readOnly, false); assert.equal(p.api.state.refineAbort, null);
    }
  }
});

test('normal multi-block Anthropic success retains full selection original even when clipboard rejects', async () => {
  const p = pane({ clipboardFails: true, response: { stop_reason: 'end_turn', content: [
    { type: 'thinking', thinking: 'private' }, { type: 'text', text: 'one\n' }, { type: 'text', text: 'two' },
  ] } });
  p.box.value = 'prefix ORIGINAL suffix'; p.box.selectionStart = 7; p.box.selectionEnd = 15;
  await p.api.refine();
  assert.equal(p.box.value, 'prefix one\ntwo suffix');
  assert.equal(p.api.readOriginals()[0].text, 'prefix ORIGINAL suffix');
  assert.match(p.el('status').textContent, /refined ✓.*original in recovery.*clipboard unavailable/);
  // Selection only is the target; unselected body is not folded into transcription.
  assert.match(contentText(p.requests[0][1].messages[0].content), /ORIGINAL/);
  assert.doesNotMatch(contentText(p.requests[0][1].messages[0].content), /prefix ORIGINAL suffix/);
});

test('normal provider output applies; explicit overrides and generic/fixed-thinking budgets are preserved', async () => {
  for (const provider of ['anthropic', 'openrouter', 'deepinfra']) {
    const p = pane({ provider, response: provider === 'anthropic' ? anth() : chat() });
    p.storage.set('pane_refine_output_tokens', '16384'); p.box.value = 'original';
    await p.api.refine(); assert.equal(p.box.value, 'cleaned'); assert.equal(p.requests[0][1].max_tokens, 16384);
  }
  const p = pane({ response: anth() }); p.box.value = 'original';
  p.storage.set('refine_anthropic_model', 'claude-haiku-4-5');
  await p.api.refine(); assert.equal(p.requests[0][1].max_tokens, 8192 + 10240);
});

test('PC max/xhigh thinking reaches the request with a longer initial deadline; an unknown stored level falls back to medium', async () => {
  for (const [lvl, ms, sentAs] of [['max', 360000, 'max'], ['xhigh', 240000, 'xhigh'], ['high', 120000, 'high'], ['huge', 120000, 'medium']]) {
    let left = null;
    const p = pane({ response: anth(), fetchHook: ctx => { left = ctx.api.state.refineTimeoutEnd - Date.now(); } });
    p.storage.set('refine_think_level', lvl); p.box.value = 'original';
    await p.api.refine(); assert.equal(p.box.value, 'cleaned');
    assert.equal(p.requests[0][1].output_config.effort, sentAs);
    assert.ok(left > ms - 10000 && left <= ms, lvl + ' deadline ' + left);
  }
});

test('cancellation during context/provider and error keep original; cancel does not apply late success', async () => {
  const p = pane({ response: anth(), fetchHook: ctx => { ctx.api.state.refineAbort.reason_ = 'user'; ctx.api.state.refineAbort.abort(); } });
  p.box.value = original; await p.api.refine();
  assert.equal(p.box.value, original); assert.match(p.el('status').textContent, /canceled/);
  assert.equal(p.requests.length, 1);
  const q = pane({ response: anth() }); q.box.value = original;
  vm.runInContext('refreshContext = async () => { state.refineAbort.reason_ = "user"; state.refineAbort.abort(); return null; };', q.ctx);
  q.api.state.target = { id: 'ses_fixture' }; await q.api.refine();
  assert.equal(q.box.value, original); assert.equal(q.requests.length, 0);
});

test('provider timeout/error keeps original and releases controls', async () => {
  const p = pane({ response: anth(), fetchHook: ctx => { ctx.api.state.refineAbort.reason_ = 'timeout'; ctx.api.state.refineAbort.abort(); } });
  p.box.value = original; await p.api.refine();
  assert.equal(p.box.value, original); assert.match(p.el('status').textContent, /timed out/);
  assert.equal(p.requests.length, 1); assert.equal(p.api.readOriginals()[0].text, original);
  const q = pane({ response: anth() }); q.box.value = original;
  vm.runInContext('callWithRetry = async () => { const e = new Error("fixture HTTP 400"); e.status = 400; throw e; };', q.ctx);
  await q.api.refine(); assert.equal(q.box.value, original); assert.match(q.el('status').textContent, /refine failed.*HTTP 400/);
  assert.equal(q.box.readOnly, false); assert.equal(q.el('send-btn').disabled, false);
});

test('quota failure after successful Send is not misreported as send failure; recovered original remains', async () => {
  let p; p = pane({ fetchHook: (ctx, url) => { if (url === '/api/send') p.failWrites(); } });
  p.box.value = original; p.api.state.target = { id: 'ses_fixture', title: 'fixture' };
  await p.api.send();
  assert.equal(p.box.value, original); assert.equal(p.api.readOriginals()[0].text, original);
  assert.equal(p.storage.get(p.api.K.draft),original);
  assert.match(p.el('status').textContent, /sent.*last-sent shortcut not saved.*draft NOT cleared/);
  assert.doesNotMatch(p.el('status').textContent, /send failed/);
});

test('input edits during Refine are never overwritten, including outside selection', async () => {
  let p; p = pane({ response: anth(), fetchHook: () => { p.box.value = 'new prefix ORIGINAL suffix'; } });
  p.box.value = 'prefix ORIGINAL suffix'; p.box.selectionStart = 7; p.box.selectionEnd = 15;
  await p.api.refine(); assert.equal(p.box.value, 'new prefix ORIGINAL suffix');
  assert.match(p.el('status').textContent, /input changed/);
});

test('long Send preserves internal text and Unicode/tail, trims only boundary whitespace; history keeps untrimmed original', async () => {
  for (const text of ['x'.repeat(25000) + 'END', original, '汉字👩🏽‍💻\n\n'.repeat(20000) + 'END']) {
    const p = pane({ clipboardFails: true }); p.box.value = ' \n' + text + '\n ';
    p.api.state.target = { id: 'ses_fixture', title: 'fixture', agent: 'manager' };
    await p.api.send();
    assert.equal(p.requests[1][0], '/api/send'); assert.equal(p.requests[1][1].text, text);
    assert.equal(p.api.readOriginals()[0].text, ' \n' + text + '\n ');
    assert.equal(p.box.value, ''); assert.match(p.el('status').textContent, /sent.*clipboard unavailable/);
  }
});

test('recovery quota/corruption/oversize failure blocks Refine and Send, never crops or loses previous history', async () => {
  for (const op of ['refine', 'send']) for (const failure of ['quota', 'oversize', 'corrupt']) {
    const p = pane({ response: anth() }); assert.ok(p.api.retainOriginal('previous original', 'Refine'));
    const before = p.storage.get('pane_originals_v1');
    p.box.value = failure === 'oversize' ? 'x'.repeat(2 * 1024 * 1024) : original;
    if (failure === 'quota') p.failWrites();
    if (failure === 'corrupt') p.storage.set('pane_originals_v1', '{}');
    p.api.state.target = op === 'send' ? { id: 'ses_fixture', title: 'fixture' } : null;
    const full = p.box.value; await p.api[op]();
    assert.equal(p.box.value, full);
    assert.equal(p.requests.filter(r => r[0] !== '/oc/api/session/active').length, 0);
    assert.match(p.el('status').textContent, /original NOT saved.*blocked/);
    if (failure !== 'corrupt') assert.equal(p.storage.get('pane_originals_v1'), before);
  }
});

test('unverified recovery write blocks request; draft quota failure is visible', async () => {
  const p = pane({ response: anth() }); p.box.value = original;
  p.ctx.localStorage.setItem = () => {}; // unavailable store falsely appears to accept writes
  await p.api.refine(); assert.equal(p.requests.length, 0); assert.equal(p.box.value, original);
  assert.match(p.el('status').textContent, /could not be verified.*blocked/);
  p.failWrites();
  // Restore the throwing storage stub explicitly after the no-op fixture above.
  p.ctx.localStorage.setItem = () => { throw new Error('fixture quota'); };
  assert.equal(p.api.saveDraft(), false); assert.match(p.el('status').textContent, /draft NOT saved/);
});

test('history survives new page, bounded eviction/dedupe, recovery preview/restore protects displaced input', () => {
  const p = pane();
  for (let i = 0; i < 15; i++) assert.ok(p.api.retainOriginal('original ' + i, 'Refine'));
  assert.equal(p.api.readOriginals().length, 10); assert.equal(p.api.readOriginals().at(-1).text, 'original 5');
  assert.ok(p.api.retainOriginal('original 14', 'Send')); assert.equal(p.api.readOriginals().length, 10);
  const q = pane({ storage: p.storage }); q.box.value = 'displaced';
  q.api.showRecovery(); assert.equal(q.el('recovery-text').value, 'original 14');
  q.el('recovery-restore').onclick(); assert.equal(q.box.value, 'original 14');
  assert.equal(q.api.readOriginals()[0].text, 'displaced');
  for (let i = 0; i < 10; i++) assert.ok(q.api.retainOriginal('x'.repeat(200000) + i, 'Send'));
  assert.ok(q.storage.get('pane_originals_v1').length * 2 <= 2 * 1024 * 1024);
  assert.equal(q.api.readOriginals()[0].text, 'x'.repeat(200000) + 9);
});

test('separate context budget never truncates target; tool calls excluded from Refine context', async () => {
  const p = pane({ response: anth() }); p.box.value = original; p.api.state.target = { id: 'ses_fixture' };
  p.storage.set('refine_history_mode', 'budgeted'); // this check is about the explicit byte-budgeted read
  vm.runInContext(`oc = async () => ({ data: [
    { type: 'assistant', content: [{type: 'text', text: 'prose'}, {type: 'tool', input: 'NEVER_INCLUDE_TOOL_INPUT'}] },
    { type: 'user', text: '你好 👩🏽‍💻 context '.repeat(20000) }
  ] });`, p.ctx);
  await p.api.refine();
  const user = contentText(p.requests[0][1].messages[0].content);
  assert.ok(user.includes(original)); assert.ok(user.includes('prose')); assert.ok(!user.includes('NEVER_INCLUDE_TOOL_INPUT'));
  assert.ok(p.api.state.ctx.bytes <= 50 * 1024);
});
