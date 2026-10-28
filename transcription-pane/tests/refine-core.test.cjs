// Standalone production core and real PC adapter; offline transports only, never real keys/inference.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pane, messageEvents, streamResponse, contentText } = require('./pane-fixture.cjs');
const defaults = fs.readFileSync(path.join(__dirname, '../static/refine-defaults.js'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, '../static/refine-core.js'), 'utf8');
const goodJSON = text => ({ choices: [{ finish_reason: 'stop', message: { content: text } }], usage: { cost: 0.012 } });
const goodAnthropic = text => ({ stop_reason: 'end_turn', content: [{ type: 'text', text }] });

function standalone() {
  const effects = [];
  const forbidden = name => () => { effects.push(name); throw new Error('unexpected ' + name); };
  const ctx = { TextEncoder, TextDecoder, AbortController, DOMException,
    fetch: forbidden('fetch'), setInterval: forbidden('setInterval'), clearInterval: forbidden('clearInterval'),
    setTimeout: forbidden('setTimeout'), clearTimeout: forbidden('clearTimeout') };
  // Even reading a page dependency fails, both on import and while running the core.
  for (const name of ['document', 'localStorage', 'state', 'main']) Object.defineProperty(ctx, name, { get: forbidden(name) });
  vm.createContext(ctx);
  const before = Object.keys(ctx);
  vm.runInContext(defaults + '\n' + source, ctx);
  const parts = vm.runInContext('({system:REFINE_DEFAULT_SYSTEM_PROMPT,context:REFINE_DEFAULT_CONTEXT_PREAMBLE,transcription:REFINE_DEFAULT_TRANSCRIPTION_PREAMBLE,finalfence:REFINE_DEFAULT_FINAL_FENCE})', ctx);
  return { ctx, core: ctx.opcRefineCore, parts, effects, addedGlobals: Object.keys(ctx).filter(k => !before.includes(k)) };
}
function settings(extra = {}) {
  return { provider: 'openrouter', model: 'offline-model', apiKey: 'offline-fixture-not-a-key',
    system: 'system', user: 'user', thinking: 'medium', outputTokens: 8192, ...extra };
}

test('standalone defaults/core import publishes one frozen global with no page, transport or timer side effects', () => {
  const f = standalone();
  assert.deepEqual(f.addedGlobals, ['opcRefineCore']);
  assert.equal(Object.isFrozen(f.core), true);
  assert.deepEqual(Object.keys(f.core).sort(), ['buildRequest', 'callOnce', 'callWithRetry', 'estimateAnthropicCost',
    'incompleteResponse', 'protectedBlock', 'readAnthropicStream', 'refineOutputPolicy', 'responseText', 'THINK_LEVELS', 'thinkClampFor', 'thinkingReminder', 'cacheSummary'].sort());
  assert.deepEqual(f.effects, []);
});

test('standalone assembly preserves exact order, effective parts, whole transcription and dictionary boundaries', () => {
  const f = standalone(), transcription = 'TargetTerm and xNotPresentSuffix 😀';
  const parts = { ...f.parts, system: 'effective system', context: 'H[{{context}}]', transcription: 'T[{{transcription}}]', finalfence: 'FINAL' };
  const request = f.core.buildRequest({ transcription, parts, history: 'history prose',
    draftBefore: 'before', draftAfter: 'after', dictionaryTerms: ['TargetTerm', 'NotPresent', 'HistoryOnly'] });
  const before = vm.runInContext('REFINE_DRAFT_BEFORE_PREAMBLE', f.ctx).replace('{{draft_before}}', 'before');
  const after = vm.runInContext('REFINE_DRAFT_AFTER_PREAMBLE', f.ctx).replace('{{draft_after}}', 'after');
  assert.equal(request.system, 'effective system');
  assert.equal(request.user, 'H[history prose]\n\n' + before + '\n\n'
    + f.core.protectedBlock(transcription, ['TargetTerm']) + 'T[' + transcription + ']\n\n' + after + '\n\nFINAL');
  assert.match(request.user, /- TargetTerm\n/);
  assert.doesNotMatch(request.user, /- NotPresent|- HistoryOnly/);
  assert.equal(request.transcription, transcription);
  assert.equal(request.draftBefore, 'before'); assert.equal(request.draftAfter, 'after');
  const whole = f.core.buildRequest({ transcription, parts, history: ' \n', draftBefore: '\t', draftAfter: '\n' });
  assert.equal(whole.user, 'T[' + transcription + ']\n\nFINAL');
  assert.deepEqual(f.effects, []);
});

test('standalone fake-fetch calls complete all providers without deadlines or page dependencies', async () => {
  const f = standalone();
  const request = f.core.buildRequest({ transcription: 'raw [ap:ABC123]', parts: f.parts, history: 'Manager prose', dictionaryTerms: ['[ap:ABC123]'] });
  for (const provider of ['anthropic', 'openrouter', 'deepinfra']) {
    const calls = [], model = provider === 'anthropic' ? 'claude-opus-5-5' : 'offline-model';
    const result = await f.core.callWithRetry(settings({ ...request, provider, model,
      outputTokens: f.core.refineOutputPolicy(provider, model).defaultTokens,
      fetch: async (url, options) => {
        calls.push({ url, options, body: JSON.parse(options.body) });
        return provider === 'anthropic' ? streamResponse(messageEvents(goodAnthropic('clean 😀')), { sizes: [1, 7, 31] })
          : { ok: true, json: async () => goodJSON('clean 😀') };
      } }));
    assert.equal(calls.length, 1); assert.equal(result.text, 'clean 😀');
    const { url, options, body } = calls[0];
    assert.equal(options.method, 'POST'); assert.equal(body.model, model);
    assert.equal(contentText(body.messages.at(-1).content), request.user);
    if (provider === 'anthropic') {
      assert.equal(url, 'https://api.anthropic.com/v1/messages');
      assert.equal(body.max_tokens, 128000); assert.equal(body.stream, true); assert.equal(contentText(body.system), request.system);
      assert.deepEqual(body.system[0].cache_control, { type: 'ephemeral', ttl: '1h' });
      assert.equal(body.thinking.type, 'adaptive'); assert.equal(body.output_config.effort, 'medium');
      assert.equal(options.headers['anthropic-dangerous-direct-browser-access'], 'true');
      assert.equal(result.estimated, true); assert.equal(result.cost, (100 * 4 + 10 * 20) / 1e6);
    } else {
      assert.equal(url, provider === 'openrouter' ? 'https://openrouter.ai/api/v1/chat/completions' : 'https://api.deepinfra.com/v1/openai/chat/completions');
      assert.equal(body.max_tokens, 8192); assert.equal(body.messages[0].content, request.system);
      assert.equal(options.headers.Authorization, 'Bearer offline-fixture-not-a-key');
      assert.equal(provider === 'openrouter' ? body.reasoning.effort : body.reasoning_effort, 'medium');
      assert.equal(result.cost, 0.012); assert.equal(result.estimated, false);
    }
  }
  assert.deepEqual(f.effects, []); // No deadline means no timer at all, including while fetching/reading.
});

test('real production refine calls exported core assembly and provider path with PC settings and exact request bytes', async () => {
  for (const provider of ['anthropic', 'openrouter', 'deepinfra']) {
    const seen = { build: [], provider: [] };
    const f = pane({ provider, response: provider === 'anthropic' ? goodAnthropic('clean') : goodJSON('clean'),
      coreHook(ctx) {
        const real = ctx.opcRefineCore;
        ctx.opcRefineCore = Object.freeze({ ...real,
          buildRequest(options) { const request = real.buildRequest(options); seen.build.push({ options, request }); return request; },
          callWithRetry(options) { seen.provider.push(options); return real.callWithRetry(options); },
        });
      } });
    f.storage.set('refine_dictionary', JSON.stringify(['TargetTerm']));
    f.storage.set('refine_system_prompt', 'effective PC system');
    f.storage.set('refine_think_level', 'high'); f.storage.set('pane_refine_output_tokens', '12345');
    f.api.state.target = { id: 'ses_offline' };
    vm.runInContext('refreshContext = async () => ({text:"Manager prose",bytes:13,turns:1});', f.ctx);
    f.box.value = 'before TargetTerm after'; f.box.setSelectionRange(7, 17);
    await f.api.refine();
    assert.equal(seen.build.length, 1); assert.equal(seen.provider.length, 1);
    const { options, request } = seen.build[0], call = seen.provider[0];
    assert.equal(options.transcription, 'TargetTerm'); assert.equal(options.history, 'Manager prose');
    assert.equal(options.draftBefore, 'before '); assert.equal(options.draftAfter, ' after');
    assert.equal(call.system, request.system); assert.equal(call.user, request.user);
    assert.equal(call.anthropicUser, request.anthropicUser);
    assert.equal(call.system, 'effective PC system'); assert.equal(call.thinking, 'high'); assert.equal(call.outputTokens, 12345);
    assert.equal(typeof call.deadline, 'function');
    const wire = f.requests.find(([url]) => url.startsWith('https://'))[1];
    assert.equal(contentText(wire.messages.at(-1).content), request.user);
    assert.equal(contentText(provider === 'anthropic' ? wire.system : wire.messages[0].content), request.system);
    if (provider === 'anthropic') assert.ok(wire.messages[0].content.some(block => block.cache_control));
    assert.equal(wire.max_tokens, 12345); assert.equal(f.box.value, 'before clean after');
    assert.equal(f.api.readOriginals()[0].text, 'before TargetTerm after');
  }
});

test('a moved caller deadline extends the same pending request and still aborts at its new time', async () => {
  const f = standalone(), ctrl = new AbortController();
  let now = 1000, end = 121000, tick, calls = 0, clears = 0;
  const pending = f.core.callWithRetry(settings({ controller: ctrl, deadline: () => end, now: () => now,
    setInterval(fn, ms) { assert.equal(ms, 500); tick = fn; return 1; }, clearInterval() { clears++; },
    fetch: async (_url, options) => {
      calls++;
      return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }));
    } }));
  end += 30000; now = 121001; tick();
  assert.equal(ctrl.signal.aborted, false); assert.equal(calls, 1);
  now = end; tick();
  await assert.rejects(pending, err => err.message === 'Refine timed out.' && err.wasAbort && !err.userCanceled);
  assert.equal(ctrl.reason_, 'timeout'); assert.equal(calls, 1); assert.ok(clears > 0);
});

test('PC retry delegates retain the existing per-attempt thinking and output-override reads', async () => {
  let calls = 0;
  const f = pane({ provider: 'openrouter', manualTimers: true, responseHook: async () => {
    if (++calls === 1) {
      f.storage.set('refine_think_level', 'low'); f.storage.set('pane_refine_output_tokens', '12000');
      return { ok: false, status: 503, json: async () => ({ error: { message: 'offline transient' } }) };
    }
    return { ok: true, json: async () => goodJSON('clean') };
  } });
  f.storage.set('refine_think_level', 'high');
  const pending = f.api.callWithRetry('openrouter', 'offline-model', 'offline-fixture-not-a-key', 'system', 'user', new AbortController());
  for (let i = 0; i < 100 && !f.pendingTimers(); i++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.pendingTimers(), 1);
  await f.fireTimers(); const result = await pending;
  assert.equal(result.text, 'clean'); assert.equal(calls, 2);
  assert.equal(f.requests[0][1].max_tokens, 8192); assert.equal(f.requests[0][1].reasoning.effort, 'high');
  assert.equal(f.requests[1][1].max_tokens, 12000); assert.equal(f.requests[1][1].reasoning.effort, 'low');
});

test('signal-only caller cancels a pending fetch without a deadline, timer, retry or page state', async () => {
  const f = standalone(), ctrl = new AbortController(); let calls = 0;
  const pending = f.core.callWithRetry(settings({ signal: ctrl.signal,
    fetch: async (_url, options) => {
      calls++;
      return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }));
    } }));
  ctrl.abort();
  await assert.rejects(pending, err => err.message === 'Refine canceled.' && err.userCanceled && !err.wasAbort);
  assert.equal(calls, 1); assert.deepEqual(f.effects, []);
});

test('shared retry policy preserves network/transient backoff and never retries semantic failures or rejected keys', async () => {
  const f = standalone();
  for (const status of [undefined, 429, 503, 401, 403]) {
    const delays = []; let calls = 0;
    await assert.rejects(f.core.callWithRetry(settings({
      setTimeout(fn, ms) { delays.push(ms); fn(); },
      fetch: async () => { calls++; const e = new Error('offline transport'); e.status = status; throw e; },
    })), /offline transport/);
    const expected = status === undefined ? [500, 1000] : status === 429 || status === 503 ? [500, 1000, 2000, 4000, 8000] : [];
    assert.deepEqual(delays, expected); assert.equal(calls, expected.length + 1);
  }
  let calls = 0;
  await assert.rejects(f.core.callWithRetry(settings({ fetch: async () => {
    calls++; return { ok: true, json: async () => ({ choices: [{ finish_reason: 'length', message: { content: 'partial' } }] }) };
  } })), err => err.noRetry && /finish_reason length/.test(err.message));
  assert.equal(calls, 1); assert.deepEqual(f.effects, []);
});

test('overlapping standalone Anthropic calls keep their buffers, text and costs independent', async () => {
  const f = standalone();
  const run = text => f.core.callWithRetry(settings({ provider: 'anthropic', model: 'claude-opus-5-5',
    fetch: async () => streamResponse(messageEvents(goodAnthropic(text)), { sizes: [1, 3, 11] }) }));
  const results = await Promise.all([run('first 你好'), run('second 😀')]);
  assert.deepEqual(results.map(r => r.text), ['first 你好', 'second 😀']);
  assert.equal(results[0].cost, results[1].cost); assert.deepEqual(f.effects, []);
});

test('xhigh and max reach every provider under the API names; fixed-budget and 4.6 Claude models get a level they accept', async () => {
  const f = standalone();
  assert.deepEqual([...f.core.THINK_LEVELS], ['low', 'medium', 'high', 'xhigh', 'max']);
  const sent = async (provider, model, thinking) => {
    let body; await f.core.callOnce(settings({ provider, model, thinking, fetch: async (_url, options) => {
      body = JSON.parse(options.body);
      return provider === 'anthropic' ? streamResponse(messageEvents(goodAnthropic('clean'))) : { ok: true, json: async () => goodJSON('clean') };
    } }));
    return body;
  };
  for (const lvl of ['xhigh', 'max']) {
    const b = await sent('anthropic', 'claude-opus-5-5', lvl);
    assert.equal(b.thinking.type, 'adaptive'); assert.equal(b.output_config.effort, lvl); assert.equal(b.max_tokens, 8192);
    assert.equal((await sent('openrouter', 'anthropic/claude-opus-5.5', lvl)).reasoning.effort, lvl);
    for (const model of ['deepseek-ai/DeepSeek-V4', 'Qwen/Qwen4-235B', 'zai-org/GLM-5.3', 'moonshotai/Kimi-K3']) {
      assert.equal((await sent('deepinfra', model, lvl)).reasoning_effort, lvl);
    }
  }
  assert.equal((await sent('anthropic', 'claude-opus-4-6', 'xhigh')).output_config.effort, 'high');
  assert.equal((await sent('anthropic', 'claude-sonnet-4-6', 'max')).output_config.effort, 'max');
  const fixed = await sent('anthropic', 'claude-sonnet-4-5', 'max');
  assert.deepEqual(fixed.thinking, { type: 'enabled', budget_tokens: 49152 }); assert.equal(fixed.max_tokens, 8192 + 49152);
  assert.equal((await sent('anthropic', 'claude-sonnet-4-5', 'xhigh')).thinking.budget_tokens, 32768);
  assert.deepEqual(f.effects, []);
});

test('the costly-thinking reminder: xhigh and max name themselves, high and below say nothing', () => {
  const f = standalone();
  assert.equal(f.core.thinkingReminder('max'), 'Reminder: you are currently set to max');
  assert.equal(f.core.thinkingReminder('xhigh'), 'Reminder: you are currently set to extra high');
  for (const lvl of ['low', 'medium', 'high', '', undefined, 'toString']) assert.equal(f.core.thinkingReminder(lvl), '');
});
