const assert = require('node:assert/strict');
const test = require('node:test');
const { pane, sseFrame, messageEvents, streamResponse } = require('./pane-fixture.cjs');

const START = { type: 'message_start', message: {
  id: 'msg_offline', role: 'assistant', type: 'message', content: [], stop_reason: null,
  usage: { input_tokens: 100, output_tokens: 1, cache_read_input_tokens: 10 },
} };
const begin = (index, type = 'text', text = '') => ({ type: 'content_block_start', index, content_block: type === 'text' ? { type, text } : { type } });
const delta = (index, text) => ({ type: 'content_block_delta', index, delta: { type: 'text_delta', text } });
const stop = index => ({ type: 'content_block_stop', index });
const final = (reason = 'end_turn', output_tokens = 10) => ({ type: 'message_delta', delta: { stop_reason: reason, stop_sequence: null }, usage: { output_tokens } });
const END = { type: 'message_stop' };
function good(text = 'cleaned') { return [START, begin(0), delta(0, text), stop(0), final(), END]; }
function fixture(input, options = {}) {
  const p = pane({ responseHook: () => streamResponse(input, options), clipboardFails: options.clipboardFails });
  p.storage.set('refine_anthropic_model', 'claude-opus-5-5'); p.box.value = 'original brain dump';
  return p;
}

test('exact Anthropic-direct Opus 5.5 gets native 128000 default, medium unchanged, stream:true', async () => {
  // >65,536 mock output tokens, not a live tokenizer or provider claim.
  const long = ' token'.repeat(70000) + '\nTAIL 你好 👩🏽‍💻 e\u0301';
  const events = [START, begin(0), delta(0, long), stop(0), final('end_turn', 70000), END];
  let p; p = fixture(events, { clipboardFails: true, sizes: [31, 65536, 997], readHook: () => {
    assert.equal(p.box.value, 'original brain dump', 'never show partial stream text');
  } });
  await p.api.refine();
  const request = p.requests[0][1];
  assert.equal(request.model, 'claude-opus-5-5'); assert.equal(request.max_tokens, 128000); assert.equal(request.stream, true);
  assert.equal(request.thinking.type, 'adaptive'); assert.equal(request.output_config.effort, 'medium');
  assert.equal(p.box.value, long); assert.equal(p.api.readOriginals()[0].text, 'original brain dump');
  assert.match(p.el('status').textContent, /refined ✓.*clipboard unavailable/); assert.equal(p.requests.length, 1);
});

test('split UTF-8 / CRLF / lone CR / multi-line data / comments / multiple frames preserve all text in block order', async () => {
  const expected = 'initial你好 👩🏽‍💻 e\u0301\nsecond tail';
  const events = [START, begin(0, 'thinking'),
    { type: 'content_block_delta', index: 0, delta: { type: 'thinking_delta', thinking: 'DO_NOT_COPY_PRIVATE_THINKING' } },
    { type: 'content_block_delta', index: 0, delta: { type: 'signature_delta', signature: 'DO_NOT_COPY_SIGNATURE' } }, stop(0),
    begin(2), delta(2, '\nsecond tail'), stop(2), begin(1, 'text', 'initial'), delta(1, '你好 👩🏽‍💻 e\u0301'),
    { type: 'content_block_delta', index: 1, delta: { type: 'citations_delta', citation: { text: 'not transcription' } } },
    stop(1), final(), END];
  let raw = ': comment\r\nid: ignored\r\nretry: 1000\r\n\r\n';
  raw += 'event: ping\r\ndata: {"type":"ping"}\r\n\r\n';
  raw += 'event: future_event\r\ndata: future format ignored\r\n\r\n';
  raw += events.map((e, i) => {
    if (i === 0) return 'event: message_start\r\ndata: {"type":"message_start",\r\ndata: "message":' + JSON.stringify(e.message) + '}\r\n\r\n';
    return sseFrame(e).replaceAll('\n', i % 2 ? '\r\n' : '\r');
  }).join('');
  for (const sizes of [[1], [2, 3, 7, 11], [100000]]) {
    const p = fixture(raw, { sizes }); await p.api.refine();
    assert.equal(p.box.value, expected, 'all network framing variants');
    assert.equal(p.requests.length, 1); assert.ok(!p.copies.at(-1).includes('DO_NOT_COPY'));
  }
});

test('message_start usage plus cumulative message_delta usage is overwritten, not summed, and costs survive', async () => {
  const usageDelta = final(); usageDelta.usage = { input_tokens: 200, output_tokens: 20, cache_read_input_tokens: 30, cache_creation_input_tokens: 40 };
  const p = fixture([START, begin(0), delta(0, 'cleaned'), stop(0), usageDelta, END]);
  const res = await p.api.callOnce('anthropic', 'claude-opus-5-5', 'offline-only', 'system', 'user', new AbortController());
  assert.equal(res.text, 'cleaned'); assert.equal(res.estimated, true);
  assert.equal(res.cost, (200 * 4 + 20 * 20 + 30 * 0.2 + 40 * 2 * 4) / 1e6);
  assert.equal(p.box.value, 'original brain dump');
});

test('initial cache TTL buckets survive omitted fields and later cumulative values overwrite without double counting', async () => {
  const start = { ...START, message: { ...START.message, usage: { input_tokens: 100, output_tokens: 1,
    cache_read_input_tokens: 2000, cache_creation_input_tokens: 300,
    cache_creation: { ephemeral_5m_input_tokens: 100, ephemeral_1h_input_tokens: 200 } } } };
  const done = final(); done.usage = { output_tokens: 20, cache_creation_input_tokens: 400, cache_creation: { ephemeral_1h_input_tokens: 300 } };
  const p = fixture([start, begin(0), delta(0, 'cleaned'), stop(0), done, END]);
  const result = await p.api.callOnce('anthropic', 'claude-opus-5-5', 'offline-only', 'system', 'user', new AbortController());
  assert.equal(result.cost, (100 * 4 + 20 * 20 + 2000 * .2 + (100 * 1.25 + 300 * 2) * 4) / 1e6);
  assert.equal(result.cache.read, 2000); assert.equal(result.cache.write, 400);
});

test('all stream failures fail closed, retain original, and never automatically retry a started stream', async () => {
  const partial = [START, begin(0), delta(0, 'partial')];
  const failures = [
    [...partial], // EOF inside content block
    [...partial, stop(0), final()], // end_turn is not proof of message_stop
    [...partial, stop(0), END], // terminal without final stop reason
    [...partial, stop(0), final('max_tokens'), END],
    [...partial, stop(0), final('refusal'), END],
    [...partial, stop(0), final('pause_turn'), END],
    [...partial, stop(0), final('unexpected'), END],
    [...partial, stop(0), final(null), END],
    [...partial, { type: 'error', error: { type: 'overloaded_error', message: 'offline provider error' } }],
    [START, begin(0, 'tool_use')], [START, begin(0, 'refusal')],
    [START, delta(0, 'delta without block')],
    [START, begin(0), begin(0)],
    [START, START],
    [START, begin(0), final(), END], // block not closed
    [START, begin(0), stop(0), delta(0, 'delta after close')],
    [START, begin(2), delta(2, 'missing indices'), stop(2), final(), END],
    [START, begin(0), { type: 'content_block_delta', index: 0, delta: { type: 'thinking_delta', thinking: 'wrong block' } }],
    [...good(), END],
    [...good(), { type: 'error', error: { type: 'api_error' } }],
    [START, begin(0), delta(0, '  '), stop(0), final(), END],
    [...partial, stop(0), { type: 'message_delta', delta: { stop_reason: 'end_turn' } }, END], // missing usage
    [...partial, stop(0), { type: 'message_delta', delta: { stop_reason: 'end_turn' }, usage: { output_tokens: -1 } }, END],
    [...partial.map(sseFrame), 'event: message_delta\ndata: not-json\n\n'].join(''),
    [...partial.map(sseFrame), 'event: message_stop\ndata: {"type":"message_delta"}\n\n'].join(''),
    good().map(sseFrame).join('').slice(0, -1), // terminal frame cut mid-delimiter
    good().map(sseFrame).join('') + 'event: message_delta\ndata: {',
    good().map(sseFrame).join('') + 'event: error\n\n',
  ];
  for (const input of failures) {
    const p = fixture(input); const before = p.box.value;
    await p.api.refine(); assert.equal(p.box.value, before);
    assert.equal(p.api.readOriginals()[0].text, before); assert.equal(p.requests.length, 1);
    assert.match(p.el('status').textContent, /refine failed.*not accepted/);
    assert.equal(p.box.readOnly, false); assert.equal(p.el('send-btn').disabled, false);
    assert.equal(p.copies.length, 1, 'partial result never copied over original either');
  }
});

test('network read failure and invalid UTF-8 do not get partial success or paid stream retry', async () => {
  const cases = [{ sizes: [100], failAt: 3 }, { chunks: [new Uint8Array([0xc3, 0x28])] }];
  for (const options of cases) {
    const p = fixture(good(), options); await p.api.refine();
    assert.equal(p.box.value, 'original brain dump'); assert.equal(p.requests.length, 1);
    assert.match(p.el('status').textContent, /stream interrupted\/invalid encoding/);
  }
});

test('cancel/+30s work while stream read is pending; timeout and late complete success cannot overwrite', async () => {
  let waiting, release;
  const ready = new Promise(r => { waiting = r; });
  const blocked = new Promise(r => { release = r; });
  const p = fixture(good(), { sizes: [100], readHook: async index => {
    if (index === 2) { waiting(); await blocked; }
  } });
  const run = p.api.refine(); await ready;
  const deadline = p.api.state.refineTimeoutEnd; p.el('plus30-btn').onclick();
  assert.equal(p.api.state.refineTimeoutEnd, deadline + 30000);
  p.el('cancel-btn').onclick(); await run; release();
  assert.equal(p.box.value, 'original brain dump'); assert.match(p.el('status').textContent, /canceled/);
  assert.equal(p.api.state.refineAbort, null); assert.equal(p.box.readOnly, false); assert.equal(p.requests.length, 1);
  let q; q = fixture(good(), { readHook: () => { q.api.state.refineAbort.reason_ = 'timeout'; q.api.state.refineAbort.abort(); } });
  await q.api.refine(); assert.equal(q.box.value, 'original brain dump'); assert.match(q.el('status').textContent, /timed out/);
});

test('model-specific default/UI ceiling, explicit override preservation, reset, routed/other-model compatibility', async () => {
  const p = fixture(good());
  assert.equal(p.api.getOutputTokens('anthropic', 'claude-opus-5-5'), 128000);
  assert.equal(p.api.getOutputTokens('anthropic', 'claude-opus-4-8'), 8192);
  assert.equal(p.api.getOutputTokens('anthropic', 'claude-opus-5-5-alias'), 8192);
  assert.equal(p.api.getOutputTokens('openrouter', 'anthropic/claude-opus-5.5'), 8192);
  assert.equal(p.api.getOutputTokens('deepinfra', 'claude-opus-5-5'), 8192);
  p.api.wireSettings(); assert.equal(p.el('output-tokens').value, '128000'); assert.equal(p.el('output-tokens').max, '128000');
  p.storage.set('pane_refine_output_tokens', '8192'); p.api.renderOutputSettings();
  assert.equal(p.api.getOutputTokens(), 8192); assert.equal(p.el('output-tokens').value, '8192');
  assert.match(p.el('output-note').textContent, /Explicit override/);
  p.el('output-reset').onclick(); assert.equal(p.api.getOutputTokens(), 128000);
  p.el('output-tokens').onchange({ target: { value: '96000' } }); assert.equal(p.api.getOutputTokens(), 96000);
  await p.api.refine(); assert.equal(p.requests[0][1].max_tokens, 96000); assert.equal(p.requests[0][1].output_config.effort, 'medium');
  p.storage.set('pane_refine_output_tokens', '128001');
  assert.throws(() => p.api.getOutputTokens(), /1024–128000/);
  assert.throws(() => p.api.getOutputTokens('openrouter', 'other'), /1024–65536/);
});

test('unsupported HTTP allowance or non-SSE Anthropic success fails without fallback/partial overwrite', async () => {
  for (const response of [
    { ok: false, status: 400, json: async () => ({ error: { message: 'max_tokens unsupported for this model' } }) },
    { ok: true, json: async () => ({ stop_reason: 'end_turn', content: [{ type: 'text', text: 'wrong transport' }] }) },
  ]) {
    const p = pane({ responseHook: () => response }); p.storage.set('refine_anthropic_model', 'claude-opus-5-5'); p.box.value = 'original';
    await p.api.refine(); assert.equal(p.box.value, 'original'); assert.equal(p.requests.length, 1);
    assert.match(p.el('status').textContent, /refine failed/);
  }
});
