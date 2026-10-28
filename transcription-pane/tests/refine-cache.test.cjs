// Production Refine core, offline API streams only; no credentials or paid inference.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { messageEvents, streamResponse, contentText } = require('./pane-fixture.cjs');
const defaults = fs.readFileSync(path.join(__dirname, '../static/refine-defaults.js'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, '../static/refine-core.js'), 'utf8');
const goodAnthropic = text => ({ stop_reason: 'end_turn', content: [{ type: 'text', text }] });
const goodJSON = text => ({ choices: [{ finish_reason: 'stop', message: { content: text } }], usage: { cost: .012 } });
function standalone() {
  const ctx = { TextEncoder, TextDecoder, AbortController, DOMException };
  vm.createContext(ctx); vm.runInContext(defaults + '\n' + source, ctx);
  const parts = vm.runInContext('({system:REFINE_DEFAULT_SYSTEM_PROMPT,context:REFINE_DEFAULT_CONTEXT_PREAMBLE,transcription:REFINE_DEFAULT_TRANSCRIPTION_PREAMBLE,finalfence:REFINE_DEFAULT_FINAL_FENCE})', ctx);
  return { ctx, core: ctx.opcRefineCore, parts };
}
function settings(extra = {}) {
  return { provider: 'anthropic', model: 'claude-opus-5-5', apiKey: 'offline-not-a-key', system: 'system', user: 'user', ...extra };
}
function historyFixture(count = 2) {
  const turns = Array.from({ length: count }, (_, i) => (i % 2 ? '[ASSISTANT]' : '[USER]') + '\nTurn ' + i + ' 😀');
  let end = 0;
  const historyBoundaries = turns.map((text, i) => ({ id: 'message-' + i, end: end += (i ? 7 : 0) + text.length }));
  return { history: turns.join('\n\n---\n\n'), historyBoundaries };
}
function anthropicHarness() {
  const f = standalone(), bodies = [];
  const send = async (count, extra = {}) => {
    const { request: requestExtra, response, transport, ...options } = extra;
    const request = f.core.buildRequest({ parts: f.parts, transcription: 'NewTerm um please check it',
      dictionaryTerms: ['NewTerm'], ...historyFixture(count), ...requestExtra });
    const result = await f.core.callOnce(settings({ ...request, cacheKey: 'claude:logical-target', now: () => 1000, ...options,
      fetch: async (_url, opts) => {
        const body = JSON.parse(opts.body); bodies.push(body);
        return transport ? transport(body, opts) : streamResponse(messageEvents(response || {
          ...goodAnthropic('clean'), usage: { cache_read_input_tokens: 0, cache_creation_input_tokens: 1000,
            cache_creation: { ephemeral_5m_input_tokens: 0, ephemeral_1h_input_tokens: 1000 } },
        }));
      } }));
    return { result, body: bodies.at(-1), request };
  };
  return { ...f, send, bodies };
}

// Independent documented lookup: write ONLY at marked ends; each marker searches back
// at most 20 boundaries for a prior write, never just for text which happens to be stable.
function cacheProbe() {
  const entries = new Set();
  return body => {
    const blocks = [...body.system, ...body.messages[0].content];
    const keys = blocks.map((_, i) => JSON.stringify(blocks.slice(0, i + 1).map(block => block.text)));
    const marked = blocks.flatMap((block, i) => block.cache_control ? [i] : []);
    let readThrough = -1;
    for (const end of marked) for (let i = end; i >= Math.max(0, end - 19); i--) {
      if (entries.has(keys[i])) { readThrough = Math.max(readThrough, i); break; }
    }
    for (const end of marked) entries.add(keys[end]);
    return { readThrough, writeThrough: marked.at(-1), marked };
  };
}

test('Anthropic assembly preserves text and Unicode with real boundaries, overrides and repeated placeholders', () => {
  const f = standalone();
  for (const context of [f.parts.context, 'CUSTOM[{{context}}]END', '{{context}} then {{context}}', 'fixed context', ' \n']) {
    const parts = { ...f.parts, context };
    const first = '[USER]\nFirst 😀\n\n---\n\n[USER]\nthis label is quoted prose, not a new message';
    const second = '[ASSISTANT]\nSecond 🦊', history = first + '\n\n---\n\n' + second;
    const request = f.core.buildRequest({ transcription: 'TargetTerm {{context}}', parts, history,
      historyBoundaries: [{ id: 'one', end: first.length }, { id: 'two', end: history.length }],
      draftBefore: 'before', draftAfter: 'after', dictionaryTerms: ['TargetTerm'] });
    assert.equal(contentText(request.anthropicUser), request.user);
    const marked = request.anthropicUser.filter(block => block.cache_control);
    assert.equal(marked.length, context.trim() ? 1 : 0);
    assert.equal(request.anthropicUser.at(-1).cache_control, undefined);
    assert.match(request.anthropicUser.at(-1).text, /<draft-before>[\s\S]*- TargetTerm[\s\S]*<transcription>[\s\S]*<draft-after>[\s\S]*FINAL INSTRUCTION/);
    if (context.includes('{{context}}')) assert.equal(request.anthropicUser.length, 3, 'real boundaries win over quoted labels');
  }
  for (const history of ['', ' \n']) {
    const request = f.core.buildRequest({ transcription: 'dictation', parts: f.parts, history });
    assert.equal(request.anthropicUser.length, 1); assert.equal(request.anthropicUser[0].text, request.user);
    assert.equal(request.anthropicUser[0].cache_control, undefined);
  }
});

test('legacy or malformed optional boundaries never lose text, crop it or split a surrogate pair', () => {
  const f = standalone(), history = '[USER]\nEmoji 😀', emojiMiddle = history.indexOf('😀') + 1;
  for (const historyBoundaries of [undefined, [], [{ end: -1 }], [{ end: 2 }, { end: 1 }], [{ end: emojiMiddle }, { end: history.length }]]) {
    const request = f.core.buildRequest({ parts: f.parts, transcription: 'raw', history, historyBoundaries });
    assert.equal(contentText(request.anthropicUser), request.user);
    assert.equal(request.anthropicUser.length, 2); assert.equal(request.anthropicUser[0].text.endsWith('😀'), true);
  }
});

test('one-hour previous/current-end breakpoints find a write even after more than twenty new prose blocks', async () => {
  const f = anthropicHarness(), probe = cacheProbe();
  const first = await f.send(2), second = await f.send(32, { now: () => 2000 });
  assert.deepEqual(probe(first.body), { readThrough: -1, writeThrough: 2, marked: [0, 2] });
  assert.deepEqual(probe(second.body), { readThrough: 2, writeThrough: 32, marked: [0, 2, 32] });
  for (const { body, request } of [first, second]) {
    assert.equal(body.messages.length, 1); assert.equal(body.messages[0].role, 'user');
    assert.equal(contentText(body.system), request.system); assert.equal(contentText(body.messages[0].content), request.user);
    assert.match(body.messages[0].content.at(-1).text, /^\n<\/chat-history>/);
    assert.equal(body.messages[0].content.at(-1).cache_control, undefined);
    const marked = [...body.system, ...body.messages[0].content].filter(block => block.cache_control);
    assert.ok(marked.length <= 3); assert.ok(marked.every(block => block.cache_control.ttl === '1h'));
    assert.doesNotMatch(marked.map(block => block.text).join(''), /NewTerm|PROTECTED TERMS|TRANSCRIPTION TO CLEAN/);
    assert.equal(request.anthropicUser.filter(block => block.cache_control).length, 1, 'memo never mutates caller blocks');
  }
});

test('memo deduplicates unchanged history and expiry sends all text and reports an actual cache miss', async () => {
  const f = anthropicHarness(); await f.send(2);
  const same = await f.send(2, { now: () => 2000, request: { transcription: 'different draft' } });
  assert.deepEqual([...same.body.system, ...same.body.messages[0].content].flatMap((b, i) => b.cache_control ? [i] : []), [0, 2]);
  const expired = await f.send(32, { now: () => 3602000, response: { ...goodAnthropic('clean'), usage: {
    cache_read_input_tokens: 0, cache_creation_input_tokens: 32000, cache_creation: { ephemeral_1h_input_tokens: 32000 },
  } } });
  assert.deepEqual([...expired.body.system, ...expired.body.messages[0].content].flatMap((b, i) => b.cache_control ? [i] : []), [0, 32]);
  assert.equal(contentText(expired.body.messages[0].content), expired.request.user);
  assert.equal(expired.result.cache.read, 0); assert.equal(expired.result.cache.write, 32000);
});

test('memo is target/model/effort/system-specific and an edited earlier prefix is an ordinary miss', async () => {
  for (const change of [
    { cacheKey: 'claude:another-target' }, { model: 'claude-opus-4-8' }, { thinking: 'high' },
    { request: { parts: { system: 'different system', context: 'H[{{context}}]', transcription: 'T[{{transcription}}]', finalfence: 'F' } } },
    { request: { history: historyFixture(32).history.replace('Turn 0', 'Edited 0') } },
  ]) {
    const f = anthropicHarness(); await f.send(2);
    const next = await f.send(32, { now: () => 2000, ...change });
    assert.equal([...next.body.system, ...next.body.messages[0].content].filter(b => b.cache_control).length, 2);
    assert.equal(contentText(next.body.messages[0].content), next.request.user);
  }
});

test('failed calls do not advance the memo; out-of-order completion cannot replace a newer end', async () => {
  const f = anthropicHarness(); await f.send(2);
  await assert.rejects(f.send(5, { now: () => 2000, transport: async () => ({ ok: false, status: 503,
    json: async () => ({ error: { message: 'offline failure' } }) }) }), /offline failure/);
  const afterFailure = await f.send(32, { now: () => 3000 });
  assert.ok(afterFailure.body.messages[0].content[1].cache_control);
  assert.equal(afterFailure.body.messages[0].content[4].cache_control, undefined);
  const g = anthropicHarness(); let release;
  const older = g.send(3, { now: () => 1000, transport: () => new Promise(resolve => { release = resolve; }) });
  await g.send(4, { now: () => 2000 });
  release(streamResponse(messageEvents(goodAnthropic('older')))); await older;
  const newer = await g.send(32, { now: () => 3000 });
  assert.ok(newer.body.messages[0].content[3].cache_control);
  assert.equal(newer.body.messages[0].content[2].cache_control, undefined);
});

test('routers retain exact string bodies and do not invent a cache result', async () => {
  const f = standalone(), request = f.core.buildRequest({ transcription: 'draft', parts: f.parts, ...historyFixture() });
  for (const provider of ['openrouter', 'deepinfra']) {
    let body;
    const result = await f.core.callOnce(settings({ ...request, provider, model: 'offline-model', cacheKey: 'same-target', fetch: async (_url, opts) => {
      body = JSON.parse(opts.body); return { ok: true, json: async () => goodJSON('clean') };
    } }));
    assert.deepEqual(body, { model: 'offline-model', max_tokens: 8192,
      messages: [{ role: 'system', content: request.system }, { role: 'user', content: request.user }], usage: { include: true },
      ...(provider === 'openrouter' ? { reasoning: { effort: 'medium' } } : { reasoning_effort: 'medium' }) });
    assert.equal(result.cache, undefined);
  }
});

test('current direct prices and five-minute/one-hour write buckets charge every token exactly once', () => {
  const { core } = standalone();
  const usage = { input_tokens: 100, output_tokens: 20, cache_read_input_tokens: 2000, cache_creation_input_tokens: 400,
    cache_creation: { ephemeral_5m_input_tokens: 100, ephemeral_1h_input_tokens: 300 } };
  for (const [model, input, output, read] of [
    ['claude-opus-5-5', 4, 20, .20], ['claude-opus-5-5-20261025', 4, 20, .20], ['claude-opus-4-8', 5, 25, .50],
    ['claude-opus-4-7', 5, 25, .50], ['claude-opus-4-6', 5, 25, .50], ['claude-opus-4-1', 15, 75, 1.50],
    ['claude-sonnet-5', 2, 10, .20], ['claude-sonnet-5-5', 2, 10, .10], ['claude-sonnet-4-6', 3, 15, .30],
    ['claude-haiku-4-5', 1, 5, .10], ['claude-3-5-haiku-20241022', .80, 4, .08],
  ]) {
    assert.equal(core.estimateAnthropicCost(model, usage), (100 * input + 20 * output + 2000 * read + (100 * 1.25 + 300 * 2) * input) / 1e6, model);
    assert.equal(core.estimateAnthropicCost(model, { ...usage, cache_creation: undefined }), (100 * input + 20 * output + 2000 * read + 400 * 2 * input) / 1e6, model);
    assert.equal(core.estimateAnthropicCost(model, {}), 0);
  }
  assert.equal(core.estimateAnthropicCost('claude-haiku-5-5', { input_tokens: 100000 }), .01);
  assert.equal(core.estimateAnthropicCost('claude-haiku-5-5', { input_tokens: 100000, cache_read_input_tokens: 1 }), (100000 * .5 + .05) / 1e6);
  for (const model of ['unknown', 'claude-opus-6', 'claude-sonnet-5-6']) assert.equal(core.estimateAnthropicCost(model, usage), null);
  assert.equal(core.estimateAnthropicCost('claude-opus-5-5', null), null);
});

test('cache summary uses measured counts including zero and keeps omitted counters unknown', async () => {
  const f = anthropicHarness();
  const hit = await f.send(2, { response: { ...goodAnthropic('clean'), usage: { cache_read_input_tokens: 31000,
    cache_creation_input_tokens: 1400, cache_creation: { ephemeral_1h_input_tokens: 1400 } } } });
  assert.equal(f.core.cacheSummary(hit.result.cache), 'cache: read 31K · wrote 1.4K');
  assert.equal(f.core.cacheSummary({ read: 0, write: 0 }), 'cache: read 0 · wrote 0');
  assert.equal(f.core.cacheSummary({ read: null, write: null }), 'cache: read — · wrote —');
  assert.equal(f.core.cacheSummary(), '');
  const unknown = await f.send(2, { response: goodAnthropic('clean') });
  assert.equal(f.core.cacheSummary(unknown.result.cache), 'cache: read — · wrote —');
});
