// Read Aloud's browser half: the request Refine's engine assembles from the read-aloud prompt parts, and the
// provider settings the player reads. Offline: the three real scripts in a Node VM, a localStorage stub, no
// page, no fetch, no provider key.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const staticDir = path.join(__dirname, '..', 'static');
const defaults = fs.readFileSync(path.join(staticDir, 'refine-defaults.js'), 'utf8');
const core = fs.readFileSync(path.join(staticDir, 'refine-core.js'), 'utf8');
const readAloud = fs.readFileSync(path.join(staticDir, 'read-aloud.js'), 'utf8');
const SEPARATOR = '\n\n---\n\n';

function storage(saved = {}) {
  const values = new Map(Object.entries(saved));
  return {
    getItem: name => (values.has(name) ? values.get(name) : null),
    setItem: (name, value) => { values.set(name, String(value)); },
    removeItem: name => { values.delete(name); },
    clear: () => values.clear(),
  };
}

function boot(saved = {}) {
  const store = storage(saved);
  const context = { btoa, localStorage: store };
  vm.createContext(context);
  vm.runInContext(defaults + '\n' + core + '\n' + readAloud, context, { filename: 'read-aloud.vm.js' });
  const parts = vm.runInContext('({system: READ_ALOUD_DEFAULT_SYSTEM_PROMPT, context: READ_ALOUD_DEFAULT_CONTEXT_PREAMBLE,'
    + ' message: READ_ALOUD_DEFAULT_MESSAGE_PREAMBLE, finalfence: READ_ALOUD_DEFAULT_FINAL_FENCE})', context);
  return { api: context.opcReadAloud, store, context, parts };
}

// Plain host objects: the VM's own prototypes must not decide a deep comparison.
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function block(user, tag) {
  return user.match(new RegExp(`<${tag}>\\n([\\s\\S]*?)\\n</${tag}>`))?.[1] ?? null;
}

// The history the server's readers hand over: "[USER]/[ASSISTANT]" blocks joined by Refine's separator, with one
// cumulative UTF-16 end boundary per block (the leading separator belongs to the block after the first).
function historyOf(turns) {
  const history = turns.join(SEPARATOR);
  let end = 0;
  const boundaries = turns.map((turn, index) => {
    end += (index ? SEPARATOR.length : 0) + turn.length;
    return { id: 'block-' + index, end };
  });
  assert.equal(boundaries.at(-1).end, history.length);
  return { history, boundaries };
}

test('the request carries the read-aloud system prompt and the message between its tags', () => {
  const f = boot();
  const text = 'Manager said [ap:ABC123] and left /tmp/x alone.';
  const request = f.api.request({ text });
  assert.equal(request.system, f.parts.system);
  assert.equal(request.transcription, text);
  assert.equal(block(request.user, 'message'), text);
  assert.match(request.user, /\[ap:ABC123\]/);              // no dictionary was applied
  assert.doesNotMatch(request.user, /PROTECTED TERMS/);     // and none is claimed
  assert.doesNotMatch(request.user, /DRAFT BEFORE|DRAFT AFTER/);
  assert.equal(plain(request.anthropicUser).length, 1);
  assert.deepEqual(plain(request.anthropicUser), [{ type: 'text', text: request.user }]);
});

test('the user message orders the chat-history preamble, the message preamble and the final fence', () => {
  const f = boot();
  const { history, boundaries } = historyOf(['[USER]\nOld question.', '[ASSISTANT]\nOld answer.']);
  const text = 'New instruction to read.';
  const request = f.api.request({ text, speaker: 'the Conductor', history, historyBoundaries: boundaries });
  const order = ['===== CHAT HISTORY (READ-ONLY)', '<chat-history>', '</chat-history>', '===== MESSAGE TO READ ALOUD',
    '<message>', '</message>', '===== FINAL INSTRUCTION'].map(marker => request.user.indexOf(marker));
  assert.ok(order.every((at, index) => at >= 0 && (index === 0 || at > order[index - 1])), JSON.stringify(order));
  assert.equal(block(request.user, 'chat-history'), history);
  assert.equal(block(request.user, 'message'), text);
  assert.match(request.user, /written by the Conductor/);   // {{speaker}} substituted
  assert.doesNotMatch(request.user, /\{\{(speaker|message|context|transcription)\}\}/);
  assert.ok(request.user.endsWith(f.parts.finalfence));
  assert.ok(request.user.startsWith('===== CHAT HISTORY'));
});

test('no history means no chat-history block at all', () => {
  const f = boot();
  for (const history of ['', '   \n\t ']) {
    const request = f.api.request({ text: 'Only the message.', history, historyBoundaries: [{ id: 'stale', end: 1 }] });
    assert.doesNotMatch(request.user, /CHAT HISTORY|<chat-history>/);
    assert.ok(request.user.startsWith('===== MESSAGE TO READ ALOUD'));
    assert.equal(request.history, '');
    assert.equal(plain(request.anthropicUser).length, 1);
  }
  assert.equal(block(f.api.request({ text: 'Hi.' }).user, 'message'), 'Hi.');
});

test('an unknown speaker falls back to the neutral phrase instead of an empty slot', () => {
  const f = boot();
  const request = f.api.request({ text: 'Something to read.' });
  assert.match(request.user, /written by the speaker/);
});

test('the history is one Anthropic block per turn, cached on the last history turn', () => {
  const f = boot();
  const turns = ['[USER]\nOne.', '[ASSISTANT]\nTwo.', '[USER]\nThree.'];
  const { history, boundaries } = historyOf(turns);
  const request = f.api.request({ text: 'Read this.', history, historyBoundaries: boundaries });
  const blocks = plain(request.anthropicUser);
  assert.equal(blocks.length, 4);                                   // three turns + the volatile remainder
  assert.equal(blocks.map(entry => entry.text).join(''), request.user);
  assert.ok(blocks[0].text.startsWith('===== CHAT HISTORY (READ-ONLY)'));
  assert.ok(blocks[0].text.endsWith(turns[0]));
  assert.equal(blocks[1].text, SEPARATOR + turns[1]);
  assert.equal(blocks[2].text, SEPARATOR + turns[2]);
  assert.deepEqual(blocks.slice(0, 3).map(entry => entry.cache_control ? 'cached' : 'plain'),
    ['plain', 'plain', 'cached']);
  assert.deepEqual(blocks[2].cache_control, { type: 'ephemeral', ttl: '1h' });  // Refine's one-hour breakpoint
  assert.ok(blocks[3].text.startsWith('\n</chat-history>\n\n'));
  assert.ok(blocks[3].text.endsWith(f.parts.finalfence));
  assert.equal(blocks[3].cache_control, undefined);
});

test('unusable boundary metadata still splits the history at Refine separators, with no breakpoint moved', () => {
  const f = boot();
  const turns = ['[USER]\nOne.', '[ASSISTANT]\nTwo.', '[USER]\nThree.'];
  const { history } = historyOf(turns);
  for (const historyBoundaries of [undefined, [], [{ id: 'short', end: 3 }],
    historyOf(turns).boundaries.map(entry => ({ id: entry.id, end: entry.end + 40 }))]) {
    const blocks = plain(f.api.request({ text: 'x', history, historyBoundaries }).anthropicUser);
    assert.equal(blocks.length, 4);
    assert.equal(blocks[1].text, SEPARATOR + turns[1]);
    assert.equal(blocks[2].text, SEPARATOR + turns[2]);
    assert.equal(blocks[2].cache_control === undefined, false);
  }
});

test('a saved part overrides its default', () => {
  const f = boot({ refine_read_aloud_system_prompt: 'CUSTOM SPOKEN-WORD SYSTEM PROMPT',
    refine_read_aloud_message_preamble: '===== CUSTOM MESSAGE =====\n<message>\n{{message}}\n</message>' });
  const parts = f.api.parts();
  assert.deepEqual(Object.keys(parts).sort(), ['read_aloud_context', 'read_aloud_finalfence', 'read_aloud_message',
    'read_aloud_system']);
  assert.equal(parts.read_aloud_system, 'CUSTOM SPOKEN-WORD SYSTEM PROMPT');
  assert.equal(parts.read_aloud_message, '===== CUSTOM MESSAGE =====\n<message>\n{{message}}\n</message>');
  assert.equal(parts.read_aloud_context, f.parts.context);      // untouched parts keep their defaults
  assert.equal(parts.read_aloud_finalfence, f.parts.finalfence);
  const request = f.api.request({ text: 'Read me out.' });
  assert.equal(request.system, 'CUSTOM SPOKEN-WORD SYSTEM PROMPT');
  assert.match(request.user, /===== CUSTOM MESSAGE =====/);
  assert.equal(block(request.user, 'message'), 'Read me out.');
});

test('a saved part that lost its required placeholder falls back to the default', () => {
  const f = boot({ refine_read_aloud_message_preamble: 'a message with no slot',
    refine_read_aloud_context_preamble: 'history with no slot' });
  const parts = f.api.parts();
  assert.equal(parts.read_aloud_message, f.parts.message);
  assert.equal(parts.read_aloud_context, f.parts.context);
  const request = f.api.request({ text: 'Still readable.', history: 'Old turn.' });
  assert.equal(block(request.user, 'message'), 'Still readable.');
  assert.equal(block(request.user, 'chat-history'), 'Old turn.');
});

test('ownSettings starts from the phone defaults: Anthropic, claude-opus-5-5, medium thinking, whole history', () => {
  const f = boot();
  assert.deepEqual(plain(f.api.ownSettings()), { provider: 'anthropic', model: 'claude-opus-5-5', apiKey: '',
    thinking: 'medium', outputTokens: null, historyMode: 'whole', budgetKb: 50 });
});

test('ownSettings follows the saved Refine keys and refuses an unknown provider or thinking level', () => {
  const f = boot({ refine_provider: 'openrouter', refine_openrouter_api_key: 'offline-fixture-not-a-key',
    refine_openrouter_model: 'anthropic/claude-opus-5.5', refine_think_level: 'extreme',
    refine_history_mode: 'budgeted' });
  const settings = plain(f.api.ownSettings());
  assert.equal(settings.provider, 'openrouter');
  assert.equal(settings.model, 'anthropic/claude-opus-5.5');
  assert.equal(settings.apiKey, 'offline-fixture-not-a-key');
  assert.equal(settings.thinking, 'medium');       // 'extreme' is not one of Refine's levels
  assert.equal(settings.historyMode, 'budgeted');
  assert.equal(boot({ refine_provider: 'no-such-provider' }).api.ownSettings().provider, 'anthropic');
  assert.equal(boot({ refine_history_mode: 'recent' }).api.ownSettings().historyMode, 'whole');
});

test('the first saved model is the fallback when no model is chosen', () => {
  const listed = boot({ refine_provider: 'deepinfra', refine_deepinfra_models_list: '["first/model","second/model"]' });
  assert.equal(listed.api.ownSettings().model, 'first/model');
  const empty = boot({ refine_provider: 'deepinfra', refine_deepinfra_models_list: '[]' });
  assert.equal(empty.api.ownSettings().model, 'zai-org/GLM-5.2');   // the built-in default
  const broken = boot({ refine_provider: 'deepinfra', refine_deepinfra_models_list: 'not json' });
  assert.equal(broken.api.ownSettings().model, 'zai-org/GLM-5.2');
});

test('an item key carries the backend, conversation, message and part, defaulting to text', () => {
  const f = boot();
  assert.deepEqual(plain(f.api.itemKey({ backend: 'claude', conversation: 'conv-1', message: 'msg-1' })),
    { backend: 'claude', conversation: 'conv-1', message: 'msg-1', part: 'text' });
  assert.deepEqual(plain(f.api.itemKey({ backend: 'opencode', conversation: 'ses_1', message: 'msg_2',
    part: 'thinking:2' })), { backend: 'opencode', conversation: 'ses_1', message: 'msg_2', part: 'thinking:2' });
});

// The pop-over's speed control over a minimal fake page: just enough DOM for open(), an Audio stub that records its
// rates, and a fetch that answers a saved reading, so the select is exercised as the user's tap reaches it.
function fakePage() {
  class Node {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.parent = null; this.attrs = {}; this.dataset = {};
      this.className = ''; this.textContent = ''; this.disabled = false; this.hidden = false; this.value = ''; this.listeners = {};
      const self = this;
      this.classList = { toggle(c, on) { const s = new Set(self.className.split(' ').filter(Boolean)); if (on) s.add(c); else s.delete(c); self.className = [...s].join(' '); } };
    }
    setAttribute(k, v) { this.attrs[k] = String(v); if (k.startsWith('data-')) this.dataset[k.slice(5)] = String(v); }
    getAttribute(k) { return Object.hasOwn(this.attrs, k) ? this.attrs[k] : null; }
    append(...nodes) { for (const n of nodes) { n.parent = this; this.children.push(n); } }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); this.parent = null; }
    addEventListener(type, f) { (this.listeners[type] ||= []).push(f); }
    focus() {}
    all() { return this.children.flatMap(n => [n, ...n.all()]); }
    querySelectorAll(sel) { const cls = sel.match(/^\.([\w-]+)/)[1]; return this.all().filter(n => n.className.split(' ').includes(cls)); }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  }
  const audios = [];
  class Audio {
    constructor() { this.src = ''; this.readyState = 1; this.duration = 3; this.currentTime = 0; this.playbackRate = 1; this.defaultPlaybackRate = 1; audios.push(this); }
    play() { return Promise.resolve(); }
    pause() {}
    removeAttribute() { this.src = ''; }
    load() {}
    addEventListener() {}
    removeEventListener() {}
  }
  const win = { Audio, getSelection: () => '', prompt: () => null };
  const body = new Node('body');
  const doc = { defaultView: win, body, createElement: tag => new Node(tag), addEventListener() {}, removeEventListener() {} };
  const item = { id: 'item-1', text: 'Hello there.', sourceHash: 'x', chunks: [{ text: 'Hello there.', chars: 12, seconds: 3, cached: true }] };
  const fetch = async (url) => {
    if (String(url).startsWith('/api/read-aloud/item')) return { ok: true, json: async () => ({ found: true, item, config: { configured: true, playbackRate: 1.35 } }) };
    return { ok: true, blob: async () => new Blob(['RIFF']), headers: { get: () => '3' }, json: async () => ({}) };
  };
  return { doc, body, audios, fetch, win, Node };
}

function bootPage(saved = {}) {
  const page = fakePage(), store = storage(saved);
  const context = { btoa, localStorage: store, fetch: page.fetch, Blob, URL, crypto: globalThis.crypto, TextEncoder,
    AbortController, URLSearchParams, setInterval, clearInterval, setTimeout, clearTimeout };
  vm.createContext(context);
  vm.runInContext(defaults + '\n' + core + '\n' + readAloud, context, { filename: 'read-aloud.vm.js' });
  return { api: context.opcReadAloud, store, page };
}

async function settle() { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); }

test('the speed select is enabled from the moment the pop-over opens, set to 1.35× by default', async () => {
  const f = bootPage();
  f.api.open({ doc: f.page.doc, backend: 'claude', conversation: 'c1', message: 'm1', text: 'Hello there.' });
  try {
    const rate = f.page.body.querySelector('.ra-rate');
    assert.equal(rate.tagName, 'SELECT');
    assert.equal(rate.disabled, false);
    assert.equal(rate.value, '1.35');
    assert.deepEqual(rate.children.map(o => o.value), ['0.75', '1', '1.1', '1.2', '1.25', '1.35', '1.5', '1.75', '2']);
    await settle();
    assert.equal(rate.disabled, false);                   // still live once the saved reading is playing
    assert.equal(f.page.audios[0].playbackRate, 1.35);
  } finally { f.api.close(); }
});

test('choosing a speed changes the playing audio at once and is kept for the next replay', async () => {
  const f = bootPage();
  f.api.open({ doc: f.page.doc, backend: 'claude', conversation: 'c1', message: 'm1', text: 'Hello there.' });
  try {
    await settle();
    const rate = f.page.body.querySelector('.ra-rate'), audio = f.page.audios[0];
    rate.value = '1.75'; rate.onchange();
    assert.equal(audio.playbackRate, 1.75);
    assert.equal(audio.defaultPlaybackRate, 1.75);
    assert.equal(f.store.getItem('pane_read_aloud_rate_v1'), '1.75');
  } finally { f.api.close(); }
  f.api.open({ doc: f.page.doc, backend: 'claude', conversation: 'c1', message: 'm1', text: 'Hello there.' });
  try {
    assert.equal(f.page.body.querySelector('.ra-rate').value, '1.75');
    await settle();
    assert.equal(f.page.audios[1].playbackRate, 1.75);  // the replay of the saved reading plays at the kept speed
  } finally { f.api.close(); }
});

test('a speed chosen before playback starts (while the reading loads) is the one it plays at', async () => {
  const f = bootPage();
  f.api.open({ doc: f.page.doc, backend: 'claude', conversation: 'c1', message: 'm1', text: 'Hello there.' });
  try {
    const rate = f.page.body.querySelector('.ra-rate');
    rate.value = '0.75'; rate.onchange();
    await settle();
    assert.equal(f.page.body.querySelector('.ra-rate').value, '0.75');
    assert.equal(f.page.audios[0].playbackRate, 0.75);
  } finally { f.api.close(); }
});

test('no Read Aloud model chosen: the rewrite keeps Refine\'s provider, model and key exactly', () => {
  const f = boot({ refine_provider: 'anthropic', refine_anthropic_api_key: 'k-ant', refine_openrouter_api_key: 'k-or' });
  const refine = f.api.ownSettings();
  assert.equal(f.api.withChoice(refine, null), refine);
  assert.equal(f.api.withChoice(refine, { provider: 'anthropic', model: 'claude-opus-5-5' }), refine);
});

test('a Read Aloud model override switches provider, model and that provider\'s Refine key; thinking stays', () => {
  const f = boot({ refine_provider: 'anthropic', refine_anthropic_api_key: 'k-ant', refine_openrouter_api_key: ' k-or ',
    refine_think_level: 'high', refine_openrouter_models_list: '["anthropic/claude-opus-5.5","x-ai/grok-5"]' });
  const s = f.api.withChoice(f.api.ownSettings(), { provider: 'openrouter', model: 'x-ai/grok-5' });
  assert.deepEqual([s.provider, s.model, s.apiKey, s.thinking, s.outputTokens, s.readAloudModel],
    ['openrouter', 'x-ai/grok-5', 'k-or', 'high', null, true]);
  const first = f.api.withChoice(f.api.ownSettings(), { provider: 'openrouter', model: null });
  assert.equal(first.model, 'anthropic/claude-opus-5.5');           // provider only: the first model of Refine's list
  assert.deepEqual(plain(f.api.refineModels('deepinfra')), ['zai-org/GLM-5.2']);
});

test('a Read Aloud thinking level applies on its own or with its own model; an unknown level is ignored', () => {
  const f = boot({ refine_provider: 'anthropic', refine_anthropic_api_key: 'k-ant', refine_openrouter_api_key: 'k-or', refine_think_level: 'medium' });
  const alone = f.api.withChoice(f.api.ownSettings(), { provider: null, model: null, thinking: 'max' });
  assert.deepEqual([alone.provider, alone.model, alone.apiKey, alone.thinking, alone.readAloudModel, alone.readAloudThinking],
    ['anthropic', 'claude-opus-5-5', 'k-ant', 'max', undefined, true]);  // Refine's model and key, its own level
  const both = f.api.withChoice(f.api.ownSettings(), { provider: 'openrouter', model: 'x-ai/grok-5', thinking: 'low' });
  assert.deepEqual([both.provider, both.model, both.apiKey, both.thinking], ['openrouter', 'x-ai/grok-5', 'k-or', 'low']);
  const odd = f.api.withChoice(f.api.ownSettings(), { provider: null, model: null, thinking: 'extreme' });
  assert.equal(odd.thinking, 'medium');
  // The desktop passes the editor's own Refine settings; the override wins over them the same way.
  assert.equal(f.api.withChoice({ ...f.api.ownSettings(), thinking: 'xhigh' }, { thinking: 'low' }).thinking, 'low');
});

test('the rewrite job sends the Read Aloud thinking level to the provider and saves it with the reading', async () => {
  const calls = [];
  const fetch = async (url, init = {}) => {
    calls.push({ url: String(url), body: init.body ? JSON.parse(init.body) : null });
    const json = data => ({ ok: true, status: 200, json: async () => data });
    if (String(url) === '/api/read-aloud/settings') return json({ rewrite: { provider: null, model: null, thinking: 'max' } });
    if (String(url).startsWith('https://openrouter.ai/')) return json({ choices: [{ finish_reason: 'stop', message: { content: 'Spoken words.' } }] });
    if (String(url) === '/api/read-aloud/item') return json({ found: true });
    throw new Error('unexpected ' + url);
  };
  const context = { btoa, fetch, crypto: globalThis.crypto, TextEncoder, AbortController, URLSearchParams, setInterval, clearInterval, setTimeout, clearTimeout,
    localStorage: storage({ refine_provider: 'openrouter', refine_openrouter_api_key: 'k-or', refine_openrouter_model: 'anthropic/claude-opus-5.5', refine_think_level: 'low' }) };
  vm.createContext(context);
  vm.runInContext(defaults + '\n' + core + '\n' + readAloud, context, { filename: 'read-aloud.vm.js' });
  await context.opcReadAloud.rewrite({ backend: 'claude', conversation: 'c1', message: 'm1', text: 'Hello there.', contextNote: 'none' }).promise;
  const provider = calls.find(c => c.url.startsWith('https://openrouter.ai/'));
  assert.deepEqual(plain(provider.body.reasoning), { effort: 'max' });          // not Refine's 'low'
  assert.equal(provider.body.model, 'anthropic/claude-opus-5.5');              // the model stays Refine's
  assert.equal(calls.find(c => c.url === '/api/read-aloud/item').body.meta.thinking, 'max');
});

function settingsPage(views) {
  const page = fakePage(), store = storage(), calls = [];
  const fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : null });
    const answer = views[String(url)];
    const data = typeof answer === 'function' ? answer(calls.at(-1).body) : answer;
    return { ok: !(data && data.error && data.status), status: data && data.status && data.error ? 502 : 200, json: async () => data };
  };
  const context = { btoa, localStorage: store, fetch, URLSearchParams, setTimeout, clearTimeout };
  vm.createContext(context);
  vm.runInContext(defaults + '\n' + core + '\n' + readAloud, context, { filename: 'read-aloud.vm.js' });
  return { api: context.opcReadAloud, page, calls, store };
}

test('phone/pop-over prompt reset removes only Read Aloud overrides and never regenerates or changes Refine', async () => {
  const f = settingsPage({ '/api/read-aloud/settings': VIEW });
  f.store.setItem('refine_read_aloud_system_prompt', 'old plain-text prompt');
  f.store.setItem('refine_read_aloud_final_fence', 'old fence');
  f.store.setItem('refine_system_prompt', 'my own Refine prompt');
  const host = f.page.doc.createElement('div');
  f.api.settingsPanel(host, { doc: f.page.doc, open: true });
  await settle();
  const before = f.calls.length;
  host.all().find(n => n.tagName === 'BUTTON' && n.textContent === 'Restore Read Aloud prompt defaults').onclick();
  assert.equal(f.store.getItem('refine_read_aloud_system_prompt'), null);
  assert.equal(f.store.getItem('refine_read_aloud_final_fence'), null);
  assert.equal(f.store.getItem('refine_system_prompt'), 'my own Refine prompt');
  assert.equal(f.calls.length, before);
  assert.match(host.querySelector('.ra-set-status').textContent, /restored in this browser.*Regenerate/);
});

const VIEW = { configured: true, key: { set: true, masked: '…WXYZ' }, voiceId: 'v-old', voiceName: 'Rachel', modelId: 'eleven_multilingual_v2',
  defaultModelId: 'eleven_multilingual_v2', rewrite: { provider: null, model: null }, voices: null,
  models: [{ id: 'eleven_multilingual_v2', name: 'Eleven Multilingual v2' }, { id: 'eleven_v3', name: 'Eleven v3' }], modelsFromAccount: false };

test('the settings section reads nothing until opened, then shows the masked key and today\'s choices', async () => {
  const f = settingsPage({ '/api/read-aloud/settings': VIEW });
  const host = f.page.doc.createElement('div');
  const panel = f.api.settingsPanel(host, { doc: f.page.doc });
  assert.equal(f.calls.length, 0);
  panel.box.open = true; for (const fn of panel.box.listeners.toggle) fn();
  await settle();
  const one = cls => host.querySelector('.' + cls);
  assert.equal(one('ra-set-keynow').textContent, 'Saved · …WXYZ');
  assert.equal(one('ra-set-key').value, '');                       // the key itself is never filled back in
  assert.equal(one('ra-set-rewrite').value, '');                   // Same as Refine
  assert.equal(one('ra-set-voice').value, 'v-old');
  assert.deepEqual(one('ra-set-voice').children.map(o => o.textContent), ['Rachel']);
  assert.equal(one('ra-set-model').value, 'eleven_multilingual_v2');
  assert.deepEqual(f.calls.map(c => c.method + ' ' + c.url), ['GET /api/read-aloud/settings']);
});

test('↻ Load voices fills the picker from the account and a pick is saved server-side with its name', async () => {
  const loaded = { ...VIEW, voices: [{ id: 'v-a', name: 'Adam', category: 'cloned' }, { id: 'v-old', name: 'Rachel', category: 'premade' }] };
  const f = settingsPage({ '/api/read-aloud/settings': body => (body ? { ...loaded, voiceId: body.voiceId, voiceName: body.voiceName } : VIEW),
    '/api/read-aloud/settings/catalog': loaded });
  const host = f.page.doc.createElement('div');
  f.api.settingsPanel(host, { doc: f.page.doc, open: true });
  await settle();
  const button = text => host.all().find(n => n.tagName === 'BUTTON' && n.textContent === text);
  button('↻ Load voices').onclick();
  await settle();
  const voice = host.querySelector('.ra-set-voice');
  assert.deepEqual(voice.children.map(o => o.value), ['v-a', 'v-old']);
  voice.value = 'v-a'; voice.onchange();
  await settle();
  assert.deepEqual(plain(f.calls.at(-1)), { url: '/api/read-aloud/settings', method: 'POST', body: { schemaVersion: 1, voiceId: 'v-a', voiceName: 'Adam' } });
  assert.equal(host.querySelector('.ra-set-status').textContent, 'Voice Adam saved');
});

test('a pasted key is sent once and cleared from the field; the rewrite model saves provider and model', async () => {
  const f = settingsPage({ '/api/read-aloud/settings': body => ({ ...VIEW, ...(body && body.rewriteProvider ? { rewrite: { provider: body.rewriteProvider, model: body.rewriteModel } } : {}) }) });
  const host = f.page.doc.createElement('div');
  f.api.settingsPanel(host, { doc: f.page.doc, open: true });
  await settle();
  const key = host.querySelector('.ra-set-key');
  key.value = ' sk_new0123456789abcdef ';
  host.all().find(n => n.textContent === 'Save key').onclick();
  await settle();
  assert.deepEqual(plain(f.calls.at(-1).body), { schemaVersion: 1, apiKey: 'sk_new0123456789abcdef' });
  assert.equal(key.value, '');
  const rewrite = host.querySelector('.ra-set-rewrite');
  rewrite.value = 'deepinfra|zai-org/GLM-5.2'; rewrite.onchange();
  await settle();
  assert.deepEqual(plain(f.calls.at(-1).body), { schemaVersion: 1, rewriteProvider: 'deepinfra', rewriteModel: 'zai-org/GLM-5.2' });
  rewrite.value = ''; rewrite.onchange();
  await settle();
  assert.deepEqual(plain(f.calls.at(-1).body), { schemaVersion: 1, rewriteProvider: '' });
});

test('the rewrite thinking select offers Refine\'s levels, shows the saved one and saves its own field', async () => {
  let saved = null;
  const f = settingsPage({ '/api/read-aloud/settings': body => {
    if (body && 'rewriteThinking' in body) saved = body.rewriteThinking || null;
    return { ...VIEW, rewrite: { provider: null, model: null, thinking: saved } };
  } });
  saved = 'high';
  const host = f.page.doc.createElement('div');
  f.api.settingsPanel(host, { doc: f.page.doc, open: true });
  await settle();
  const think = host.querySelector('.ra-set-thinking');
  assert.deepEqual(think.children.map(o => o.value), ['', 'low', 'medium', 'high', 'xhigh', 'max']);
  assert.equal(think.value, 'high');
  think.value = 'max'; think.onchange();
  await settle();
  assert.deepEqual(plain(f.calls.at(-1).body), { schemaVersion: 1, rewriteThinking: 'max' });
  assert.equal(host.querySelector('.ra-set-status').textContent, 'Rewrite thinking max saved');
  assert.equal(host.querySelector('.ra-set-rewrite').value, '');   // the model stays Same as Refine
  think.value = ''; think.onchange();
  await settle();
  assert.deepEqual(plain(f.calls.at(-1).body), { schemaVersion: 1, rewriteThinking: '' });
  assert.equal(think.value, '');
});

// −10s / +10s over a saved item's parts: the real player (createPlayer) over an audio stub whose metadata arrives a
// turn after its src changes, and a fetch that can hold one part's synthesis back until the test releases it.
function skipRig({ seconds = [30, 30, 30], header = seconds, chars = null, held = [] } = {}) {
  class Audio {
    constructor() { this._src = ''; this.readyState = 0; this.duration = NaN; this.currentTime = 0; this.paused = true; this.error = null;
      this.playbackRate = 1; this.defaultPlaybackRate = 1; this.listeners = {}; }
    get src() { return this._src; }
    set src(v) {
      this._src = v; this.readyState = 0; this.currentTime = 0;
      setImmediate(() => { if (this._src === v) { this.readyState = 1; for (const f of this.listeners.loadedmetadata || []) f(); } });
    }
    addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
    removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter(g => g !== f); }
    play() { this.paused = false; return Promise.resolve(); }
    pause() { this.paused = true; }
    removeAttribute() { this._src = ''; }
    load() {}
  }
  const release = new Map(), fetched = [];
  const fetch = async (url, init) => {
    const { chunk } = JSON.parse(init.body);
    fetched.push(chunk);
    if (held.includes(chunk)) await new Promise((resolve, reject) => release.set(chunk, error => error ? reject(error) : resolve()));
    return { ok: true, blob: async () => new Blob(['part ' + chunk]), headers: { get: k => (k === 'X-Read-Aloud-Seconds' ? String(header[chunk]) : null) } };
  };
  const context = { btoa, localStorage: storage(), fetch, Blob, URL, setTimeout, clearTimeout, setInterval, clearInterval };
  vm.createContext(context);
  vm.runInContext(defaults + '\n' + core + '\n' + readAloud, context, { filename: 'read-aloud.vm.js' });
  const statuses = [], failures = [], audio = new Audio();
  const view = { audio, rate: 1.35, highlight() {}, paint() {}, status: t => statuses.push(t), ready: t => statuses.push(t), fail: e => failures.push(e) };
  const item = { id: 'item-1', chunks: seconds.map((s, i) => ({ text: 'Part ' + i + '.', chars: chars ? chars[i] : 450,
    seconds: held.includes(i) || header[i] !== s ? null : s, cached: !held.includes(i) })) };
  const player = context.opcReadAloud.createPlayer(view, item);
  return { player, audio, statuses, failures, fetched, release: (i, error) => release.get(i)(error), item };
}

test('a far block tap requests that exact part immediately, bypassing all intervening synthesis', async () => {
  const r = skipRig({ seconds: [30, 30, 30, 30, 30, 30], held: [0] });
  r.player.start(); await settle();                       // the first audio is still being made
  r.player.jump(4); await settle();
  assert.deepEqual(r.fetched, [0, 4, 5]);                 // selected part, then only its immediate neighbour
  assert.deepEqual([r.player.index, r.audio.currentTime, r.player.playing], [4, 0, true]);
  r.release(0, new Error('superseded first synthesis failed')); await settle();
  assert.equal(r.player.index, 4);
  assert.equal(r.statuses.at(-1), 'Playing part 5 of 6');
  assert.deepEqual(r.failures, []);                       // old failure cannot overwrite the successful tap
  r.player.destroy();
});

test('tapping an unsynthesized block silences the old audio at once and newest tap wins', async () => {
  const r = skipRig({ seconds: [30, 30, 30, 30, 30, 30], held: [1, 4] });
  r.player.start(); await settle();                       // part 1 plays while part 2 prefetch is blocked
  assert.equal(r.audio.paused, false);
  r.player.jump(4);
  assert.equal(r.audio.paused, true);                    // don't keep speaking the wrong paragraph
  await settle();
  assert.deepEqual(r.fetched, [0, 1, 4]);
  r.player.jump(2); await settle();                      // a second tap does not wait for the first target
  assert.deepEqual([r.player.index, r.audio.currentTime, r.player.playing], [2, 0, true]);
  r.release(4); r.release(1); await settle();
  assert.deepEqual([r.player.index, r.audio.currentTime], [2, 0]);
  assert.ok(!r.fetched.includes(5));                     // superseded targets don't prefetch
  const fetched = r.fetched.slice();
  r.player.jump(0); await settle();                      // already-loaded part replays without a fetch
  assert.deepEqual(r.fetched, fetched);
  assert.equal(r.player.index, 0);
  r.player.destroy();
});

test('pause during a tapped block synthesis keeps that block paused on arrival without prefetching more', async () => {
  const r = skipRig({ held: [2] });
  r.player.start(); await settle();
  r.player.jump(2); await settle();
  assert.equal(r.player.playing, true);                  // pause icon reflects pending play intent
  r.player.toggle();
  assert.equal(r.player.playing, false);
  r.release(2); await settle();
  assert.deepEqual([r.player.index, r.audio.currentTime, r.audio.paused, r.player.playing], [2, 0, true, false]);
  assert.equal(r.statuses.at(-1), 'Paused · part 3 of 3');
  r.player.destroy();
});

test('closing a pending block drops late audio and errors without restarting playback', async () => {
  const r = skipRig({ held: [0] });
  r.player.start(); await settle();
  r.player.destroy();
  r.release(0, new Error('late failure')); await settle();
  assert.equal(r.audio.paused, true);
  assert.deepEqual(r.failures, []);
});

test('after a selected block fails, Play retries that block rather than replaying the old audio', async () => {
  const r = skipRig({ held: [2] });
  r.player.start(); await settle();
  r.player.jump(2); await settle();
  r.release(2, new Error('temporary selected-part failure')); await settle();
  assert.equal(r.audio.paused, true);
  assert.equal(r.failures.length, 1);
  r.player.toggle(); await settle();
  assert.deepEqual(r.fetched, [0, 1, 2, 2]);
  r.release(2); await settle();
  assert.deepEqual([r.player.index, r.audio.currentTime, r.player.playing], [2, 0, true]);
  r.player.destroy();
});

test('−10s from 3 s into a part lands 7 s before the end of the part before it', async () => {
  const r = skipRig();
  r.player.start(); await settle();
  r.player.seek(63); await settle();                      // part 3 of 3, 3 s in
  assert.deepEqual([r.player.index, r.audio.currentTime], [2, 3]);
  r.player.skip(-10); await settle();
  assert.deepEqual([r.player.index, r.audio.currentTime, r.player.position()], [1, 23, 53]);
  assert.equal(r.player.playing, true);
  assert.equal(r.audio.playbackRate, 1.35);                // the speed survives the part switch
  assert.deepEqual(r.failures, []);
});

test('two quick taps move 20 s: the second counts from the first one\'s target, not the old audio', async () => {
  const r = skipRig();
  r.player.start(); await settle();
  r.player.seek(33); await settle();                      // part 2, 3 s in
  r.player.skip(-10); r.player.skip(-10);                 // the second tap lands before the first switch has loaded
  await settle();
  assert.deepEqual([r.player.index, r.audio.currentTime, r.player.position()], [0, 13, 13]);
  r.player.skip(10); r.player.skip(10); r.player.skip(10); await settle();
  assert.deepEqual([r.player.index, r.audio.currentTime], [1, 13]);
});

test('+10s into a part not synthesized yet waits for it, keeps the target, and the old part\'s end does not hijack it', async () => {
  const r = skipRig({ held: [2] });
  r.player.start(); await settle();
  r.player.seek(55); await settle();                      // part 2, 25 s in; part 3 synthesizing
  r.player.skip(10); await settle();
  assert.equal(r.statuses.at(-1), 'Synthesizing part 3 of 3 with ElevenLabs…');
  assert.equal(r.player.position(), 65);
  r.player.skip(10);                                      // a second tap while it waits adds up
  assert.equal(r.player.position(), 75);
  r.audio.onended();                                      // part 2 runs out meanwhile: nothing else starts
  assert.equal(r.player.index, 2);
  r.release(2); await settle();
  assert.deepEqual([r.player.index, r.audio.currentTime, r.player.playing], [2, 15, true]);
  assert.equal(r.statuses.at(-1), 'Playing part 3 of 3');
});

test('a target past a part\'s real end (its length was only estimated) carries on into the next part', async () => {
  const r = skipRig({ seconds: [30, 10, 30], header: [30, 4, 30], chars: [450, 150, 450], held: [1] });
  assert.equal(r.item.chunks[1].seconds, null);           // part 2's length is an estimate until it is fetched
  r.player.start(); await settle();
  r.player.seek(25); await settle();
  r.player.skip(10); await settle();                      // 5 s into part 2 by its estimate (10 s); it is only 4 s long
  assert.deepEqual([r.player.index, r.player.position()], [1, 35]);
  r.release(1); await settle();
  assert.deepEqual([r.player.index, r.audio.currentTime, r.player.playing], [2, 1, true]);
});

test('−10s near the start goes to 0; +10s past the very end finishes playback', async () => {
  const r = skipRig();
  r.player.start(); await settle();
  r.player.seek(4); await settle();
  r.player.skip(-10); await settle();
  assert.deepEqual([r.player.index, r.audio.currentTime], [0, 0]);
  r.player.seek(85); await settle();
  r.player.skip(10); await settle();
  assert.deepEqual([r.player.index, r.player.playing, r.player.position()], [2, false, 90]);
  assert.equal(r.audio.paused, true);
  assert.equal(r.statuses.at(-1), 'Finished · ⏮ or ▶ to hear it again');
  r.player.skip(-10); await settle();                     // back from the end plays the last 10 s
  assert.deepEqual([r.player.index, r.audio.currentTime, r.player.playing], [2, 20, true]);
});

test('a skip while paused moves the position and stays paused', async () => {
  const r = skipRig();
  r.player.start(); await settle();
  r.player.toggle();                                      // pause
  r.player.skip(10); await settle();
  assert.deepEqual([r.player.index, r.audio.currentTime, r.player.playing], [0, 10, false]);
});

test('the pop-over shows ⏮ −10s ▶ +10s, enabled once the reading plays, and each tap skips 10 s', async () => {
  const f = bootPage();
  f.api.open({ doc: f.page.doc, backend: 'claude', conversation: 'c1', message: 'm1', text: 'Hello there.' });
  try {
    const controls = f.page.body.querySelector('.ra-controls');
    assert.deepEqual(controls.children.slice(0, 4).map(b => b.textContent), ['⏮', '−10s', '▶', '+10s']);
    const back = controls.querySelector('.ra-back'), forward = controls.querySelector('.ra-forward');
    assert.equal(back.disabled, true);
    await settle();
    assert.deepEqual([back.disabled, forward.disabled], [false, false]);
    const audio = f.page.audios[0];
    audio.currentTime = 2.5;
    back.onclick(); await settle();
    assert.equal(audio.currentTime, 0);
    forward.onclick(); await settle();                    // the one 3 s part: past its end is the end
    assert.equal(f.api.current.player.playing, false);
    assert.equal(f.page.body.querySelector('.ra-state').textContent, 'Finished · ⏮ or ▶ to hear it again');
  } finally { f.api.close(); }
});

// ---- A long message rewritten in passes (splitPasses, the pass-by-pass job, the player that grows) ----

const PARA = i => 'Paragraph ' + i + ' ' + 'carries a thought the listener should hear in full, plainly. '.repeat(4).trim();
const LONG = ['# A long reply', ...Array.from({ length: 20 }, (_, i) => PARA(i))].join('\n\n');

test('a short message is one pass, unchanged; one long paragraph is cut only at sentence ends', () => {
  const f = boot();
  const short = '  Short **message** with [ap:ABC123].\n\n';
  assert.deepEqual(plain(f.api.splitPasses(short)), [short]);
  const sentence = i => 'Sentence ' + i + ' explains one more thing, e.g. a detail, at a reasonable length for the ear.';
  const paragraph = Array.from({ length: 40 }, (_, i) => sentence(i)).join(' ');
  const passes = f.api.splitPasses(paragraph);
  assert.ok(passes.length >= 2, passes.map(p => p.length).join(' + '));
  for (const p of passes) { assert.match(p, /^Sentence \d+ /); assert.match(p, /ear\.$/); }
  assert.equal(passes.join(' '), paragraph);
  assert.ok(passes[0].length <= f.api.PASSES.first);
});

test('passes cut between Markdown blocks: never inside a code block, a table or a list item; a heading stays with what follows', () => {
  const f = boot();
  const fence = '```js\nconst a = 1;\n\nconst b = 2;\n```';
  const table = '| a | b |\n|---|---|\n' + Array.from({ length: 12 }, (_, i) => '| row ' + i + ' | value ' + i + ' |').join('\n');
  const list = '1. First item with words:\n   - nested detail\n\n   A continuation paragraph of the first item.\n2. Second item.';
  const text = ['# Title', PARA(0), PARA(1), '## Code', fence, PARA(2), table, PARA(3), 'The list:', list, PARA(4), PARA(5),
    '## Closing', ...Array.from({ length: 14 }, (_, i) => PARA(6 + i))].join('\n\n');
  const passes = f.api.splitPasses(text);
  assert.ok(passes.length >= 3, passes.map(p => p.length).join(' + '));
  assert.ok(passes[0].length <= 1000);
  assert.ok(passes.slice(1, -1).every(p => p.length > passes[0].length));   // each later pass larger than the first
  const words = s => s.split(/\s+/).filter(Boolean).join(' ');
  assert.equal(words(passes.join('\n\n')), words(text));                    // nothing lost or doubled
  for (const piece of [fence, table, 'The list:\n\n' + list]) assert.ok(passes.some(p => p.includes(piece)), piece.slice(0, 24));
  for (const p of passes.slice(0, -1)) assert.doesNotMatch(p.split('\n').pop(), /^#{1,6}\s|:\s*$/);
});

test('a later pass carries the message spoken so far and the rest still to rewrite, in their own fixed wrappers', () => {
  const f = boot();
  const { history, boundaries } = historyOf(['[USER]\nOld question.', '[ASSISTANT]\nOld answer.']);
  const request = f.api.request({ text: 'The middle part.', speaker: 'the Manager', history, historyBoundaries: boundaries,
    spokenBefore: 'What was said first.', messageAfter: 'The **raw** rest.' });
  const order = ['\n<chat-history>\n', '===== SPOKEN SO FAR (READ-ONLY)', '\n<spoken-before>\n', '===== MESSAGE TO READ ALOUD', '\n<message>\n',
    '===== REST OF THE MESSAGE (READ-ONLY)', '\n<message-after>\n', '===== FINAL INSTRUCTION'].map(marker => request.user.indexOf(marker));
  assert.ok(order.every((at, i) => at >= 0 && (!i || at > order[i - 1])), JSON.stringify(order));
  assert.equal(block(request.user, 'spoken-before'), 'What was said first.');
  assert.equal(block(request.user, 'message'), 'The middle part.');
  assert.equal(block(request.user, 'message-after'), 'The **raw** rest.');
  assert.doesNotMatch(request.user, /DRAFT BEFORE|DRAFT AFTER|\{\{(spoken_before|message_after)\}\}/);
  const blocks = plain(request.anthropicUser);                     // the history stays the cached prefix, the rest volatile
  assert.deepEqual(blocks.map(b => b.cache_control ? 'cached' : 'plain'), ['plain', 'cached', 'plain']);
  assert.ok(blocks[2].text.includes('\n<spoken-before>\n') && blocks[2].text.includes('\n<message-after>\n'));
  assert.equal(blocks.map(b => b.text).join(''), request.user);
  const first = f.api.request({ text: 'The first part.', messageAfter: 'The rest.' });
  assert.doesNotMatch(first.user, /SPOKEN SO FAR/);
  assert.match(first.user, /REST OF THE MESSAGE/);
  assert.match(f.parts.system, /SPOKEN SO FAR\s+\(<spoken-before>\)/);    // the system prompt explains the parts
});

// An inert OpenRouter (JSON) provider answering pass k with "SPOKEN k.", and an item store that keeps one part per pass
// the way the server appends them; `gates` holds a pass's answer until the test opens it, `fail` makes one pass fail.
function pipeline({ fail = null } = {}) {
  const calls = [], systems = [], posts = [], gates = new Map(), item = { id: 'item-1', chunks: [], text: '', complete: true };
  const json = data => ({ ok: true, status: 200, json: async () => data });
  const fetch = async (url, init = {}) => {
    url = String(url);
    const body = init.body ? JSON.parse(init.body) : null;
    if (url === '/api/read-aloud/settings') return json({ rewrite: null });
    if (url.startsWith('/api/read-aloud/item?')) return json({ found: false, config: { configured: true, playbackRate: 1.35 } });
    if (url.startsWith('https://openrouter.ai/')) {
      const k = calls.length, content = body.messages[1].content;
      calls.push(Array.isArray(content) ? content.map(part => part.text).join('') : content);
      const system = body.messages[0].content;
      systems.push(Array.isArray(system) ? system.map(part => part.text).join('') : system);
      if (gates.has(k)) await gates.get(k);
      if (fail === k) return { ok: false, status: 400, json: async () => ({ error: { message: 'bad pass' } }) };
      return json({ choices: [{ finish_reason: 'stop', message: { content: 'SPOKEN ' + k + '.' } }], usage: { cost: 0.01 } });
    }
    if (url === '/api/read-aloud/item') {
      posts.push(body);
      if (!body.segment) Object.assign(item, { chunks: [], text: '' });
      item.chunks = [...item.chunks, { index: item.chunks.length, text: body.text, chars: body.text.length, cached: false, seconds: 3 }];
      item.text = item.text ? item.text + '\n\n' + body.text : body.text;
      item.complete = body.done !== false;
      return json({ found: true, item: { ...item, meta: body.meta, sourceHash: body.sourceHash }, config: { configured: true, playbackRate: 1.35 } });
    }
    if (url === '/api/read-aloud/audio') return { ok: true, blob: async () => new Blob(['RIFF']), headers: { get: () => '3' }, json: async () => ({}) };
    throw new Error('unexpected ' + url);
  };
  const hold = k => { let open; gates.set(k, new Promise(r => { open = r; })); return () => open(); };
  return { fetch, calls, systems, posts, hold };
}

function bootWith(fetch, page = null) {
  const context = { btoa, fetch, Blob, URL, crypto: globalThis.crypto, TextEncoder, AbortController, URLSearchParams, setInterval, clearInterval,
    setTimeout, clearTimeout, localStorage: storage({ refine_provider: 'openrouter', refine_openrouter_api_key: 'k-or' }) };
  vm.createContext(context);
  vm.runInContext(defaults + '\n' + core + '\n' + readAloud, context, { filename: 'read-aloud.vm.js' });
  return { api: context.opcReadAloud, page, store: context.localStorage };
}
async function until(cond) { for (let i = 0; i < 400 && !cond(); i++) await settle(); assert.ok(cond(), 'condition never held'); }
const SPEC = { backend: 'claude', conversation: 'c1', message: 'm1', text: LONG, contextNote: 'none' };

test('pass seams keep one active heading hierarchy without repeating or deleting prose', () => {
  const { api } = boot();
  const before = '## The plan\n\nOld plan.\n\n### The tests\n\nFirst tests.';
  assert.equal(api.continueHeadings('## The plan\n\n### The tests\n\nMore tests.', before), 'More tests.');
  assert.equal(api.continueHeadings('### The tests\n\nMore tests.', before), 'More tests.');
  assert.equal(api.continueHeadings('## The plan\n\n### Activation\n\nReload.', before), '### Activation\n\nReload.');
  assert.equal(api.continueHeadings('## A new topic\n\n### The tests\n\nNew tests.', before), '## A new topic\n\n### The tests\n\nNew tests.');
  assert.equal(api.continueHeadings('Continuing prose.\n\n## The plan\n\nA return.', before), 'Continuing prose.\n\n## The plan\n\nA return.');
  assert.equal(api.continueHeadings('## The plan\n\nFirst.', ''), '## The plan\n\nFirst.');
});

test('blocks show semantic headings safely and support tap and keyboard without hijacking scroll or selection', async () => {
  const page = fakePage(), fetched = [];
  const texts = ['## The plan\n\nFirst paragraph.', '### Tests\n\nSecond paragraph.', '## <img onerror=bad()>\n\nLast paragraph.'];
  const item = { id: 'item-blocks', text: texts.join('\n\n'), sourceHash: 'x',
    chunks: texts.map(text => ({ text, chars: text.length, cached: false, seconds: 3 })) };
  const fetch = async (url, init = {}) => {
    if (String(url).startsWith('/api/read-aloud/item?')) return { ok: true, json: async () => ({ found: true, item, config: { configured: true } }) };
    fetched.push(JSON.parse(init.body).chunk);
    return { ok: true, blob: async () => new Blob(['audio']), headers: { get: () => '3' } };
  };
  const f = bootWith(fetch, page);
  f.api.open({ doc: page.doc, ...SPEC, text: item.text });
  try {
    await settle();
    const blocks = page.body.querySelectorAll('.ra-chunk');
    assert.equal(blocks.length, 3);
    assert.deepEqual(blocks[0].children.map(n => [n.tagName, n.textContent]), [['H2', 'The plan'], ['P', 'First paragraph.']]);
    assert.equal(blocks[1].children[0].tagName, 'H3');
    assert.equal(blocks[2].children[0].textContent, '<img onerror=bad()>'); // safe text, no Markdown renderer/HTML
    assert.equal(blocks[2].getAttribute('role'), 'button');
    assert.equal(blocks[2].getAttribute('tabindex'), '0');
    const click = (b, extra = {}) => b.listeners.click[0]({ button: 0, ...extra });
    const down = b => b.listeners.pointerdown[0]({ button: 0, clientX: 10, clientY: 10 });
    down(blocks[2]); blocks[2].listeners.pointermove[0]({ clientX: 10, clientY: 60 }); click(blocks[2]); await settle();
    assert.equal(f.api.current.player.index, 0);           // a swipe is never a play command
    down(blocks[2]); blocks[2].listeners.pointercancel[0](); click(blocks[2]); await settle();
    assert.equal(f.api.current.player.index, 0);
    page.win.getSelection = () => 'selected text'; click(blocks[2]); await settle();
    assert.equal(f.api.current.player.index, 0);
    page.win.getSelection = () => ''; click(blocks[2], { ctrlKey: true }); await settle();
    assert.equal(f.api.current.player.index, 0);
    down(blocks[2]); click(blocks[2]); await settle();
    assert.equal(f.api.current.player.index, 2);
    let prevented = false, stopped = false;
    blocks[1].listeners.keydown[0]({ key: 'Enter', preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } }); await settle();
    assert.ok(prevented && stopped);
    assert.equal(f.api.current.player.index, 1);
  } finally { f.api.close(); }
});

test('a long message is rewritten pass by pass: each pass saved and handed on as it lands, with the spoken text so far', async () => {
  const p = pipeline(), f = bootWith(p.fetch);
  const passes = f.api.splitPasses(LONG);
  assert.ok(passes.length >= 3, passes.map(x => x.length).join(' + '));
  const job = f.api.rewrite(SPEC), seen = [];
  job.replies.add(reply => seen.push([reply.item.chunks.length, reply.item.complete]));
  const final = await job.promise;
  assert.equal(p.calls.length, passes.length);
  p.calls.forEach((user, k) => {
    assert.equal(block(user, 'message'), passes[k]);
    assert.equal(block(user, 'spoken-before'), k ? Array.from({ length: k }, (_, i) => 'SPOKEN ' + i + '.').join('\n\n') : null);
    assert.equal(block(user, 'message-after'), k < passes.length - 1 ? passes.slice(k + 1).join('\n\n') : null);
  });
  assert.deepEqual(p.posts.map(x => [x.segment, x.done, x.text]), plain(passes.map((_, k) => [k, k === passes.length - 1, 'SPOKEN ' + k + '.'])));
  assert.equal(new Set(p.posts.map(x => x.run)).size, 1);
  assert.match(p.posts[0].run, /^[A-Za-z0-9_-]{8,64}$/);
  assert.equal(new Set(p.posts.map(x => x.sourceHash)).size, 1);
  assert.deepEqual(plain(seen), plain(passes.map((_, k) => [k + 1, k === passes.length - 1])));
  assert.equal(final.item.complete, true);
  const meta = p.posts.at(-1).meta;
  assert.equal(meta.passes, passes.length);
  assert.ok(meta.firstMs <= meta.ms);
  assert.equal(meta.cost.toFixed(2), (0.01 * passes.length).toFixed(2));
});

test('restoring the prompt mid-reading changes the next rewrite, not later passes of the current run', async () => {
  const p = pipeline(), release = p.hold(1), f = bootWith(p.fetch);
  f.store.setItem('refine_read_aloud_system_prompt', 'One consistent prompt for this run.');
  const job = f.api.rewrite(SPEC);
  await until(() => p.calls.length === 2);
  f.store.removeItem('refine_read_aloud_system_prompt');
  release(); await job.promise;
  assert.ok(p.calls.length >= 3);
  assert.ok(p.systems.every(s => s === 'One consistent prompt for this run.'));
  assert.match(f.api.parts().read_aloud_system, /SECTION HEADINGS/);
});

test('a short message is still one rewrite and one whole save, exactly as before', async () => {
  const p = pipeline(), f = bootWith(p.fetch);
  await f.api.rewrite({ ...SPEC, text: 'Hello there.' }).promise;
  assert.equal(p.calls.length, 1);
  assert.equal(block(p.calls[0], 'message'), 'Hello there.');
  assert.doesNotMatch(p.calls[0], /SPOKEN SO FAR|REST OF THE MESSAGE/);
  assert.deepEqual(Object.keys(p.posts[0]).sort(), ['key', 'meta', 'schemaVersion', 'sourceHash', 'text']);
  assert.equal(p.posts[0].meta.passes, undefined);
});

test('Cancel during a later pass stops the passes left and keeps what was saved', async () => {
  const p = pipeline(), open = p.hold(1), f = bootWith(p.fetch);
  const job = f.api.rewrite(SPEC);
  await until(() => p.calls.length === 2);
  job.controller.abort(); open();
  await assert.rejects(job.promise, /Cancelled · the rest of the message was not rewritten\./);
  assert.equal(p.calls.length, 2);
  assert.deepEqual(p.posts.map(x => x.segment), [0]);
  assert.equal(f.api.jobs.size, 0);
});

test('a failed pass says which one, and the passes before it stay saved', async () => {
  const p = pipeline({ fail: 1 }), f = bootWith(p.fetch);
  const passes = f.api.splitPasses(LONG);
  await assert.rejects(f.api.rewrite(SPEC).promise, new RegExp('Pass 2 of ' + passes.length + ': The rewrite failed: bad pass'));
  assert.deepEqual(p.posts.map(x => [x.segment, x.done]), [[0, false]]);
});

test('the player starts on the first pass, holds at the end of what is ready, and carries on when the next pass lands', async () => {
  const p = pipeline(), open = p.hold(1), page = fakePage(), f = bootWith(p.fetch, page);
  const state = () => page.body.querySelector('.ra-state').textContent, audio = () => page.audios[0];
  f.api.open({ doc: page.doc, ...SPEC });
  try {
    await until(() => /^blob:/.test(audio().src));
    assert.match(state(), /^Playing part 1 of 1\+$/);                  // the voice starts after the first pass
    assert.equal(page.body.querySelectorAll('.ra-chunk').length, 1);
    assert.match(page.body.querySelector('.ra-job').textContent, /pass 2 of \d+/);
    assert.equal(page.body.querySelector('.ra-cancel').hidden, false);
    const first = audio().src;
    audio().onended();                                                 // the first pass played out before the second landed
    assert.equal(state(), 'Waiting for the next part of the rewrite…');
    assert.equal(page.body.querySelector('.ra-play').textContent, '⏸');
    open();
    await until(() => audio().src !== first);
    assert.match(state(), /^Playing part 2 of /);                      // carried on by itself
    await until(() => page.body.querySelector('.ra-job').textContent === '');
    const total = f.api.splitPasses(LONG).length;
    assert.equal(page.body.querySelectorAll('.ra-chunk').length, total);
    for (let i = 1; i < total; i++) { const src = audio().src; audio().onended(); if (i + 1 < total) await until(() => audio().src !== src); }
    assert.equal(state(), 'Finished · ⏮ or ▶ to hear it again');
    assert.equal(page.body.querySelector('.ra-regenerate').disabled, false);
  } finally { f.api.close(); }
});

test('through OpenRouter a Claude rewrite caches like Anthropic direct (Read Aloud only); router cache counts are read', async () => {
  const f = boot();
  const core = f.context.opcRefineCore;
  const { history, boundaries } = historyOf(['[USER]\nOne.', '[ASSISTANT]\nTwo.']);
  const req = f.api.request({ text: 'Read this.', history, historyBoundaries: boundaries, messageAfter: 'The rest.' });
  const run = async (provider, model, routerCache) => {
    let body = null;
    const fetch = async (url, init) => { body = JSON.parse(init.body); return { ok: true, status: 200, json: async () => ({
      choices: [{ finish_reason: 'stop', message: { content: 'Spoken.' } }],
      usage: { cost: 0.002, prompt_tokens_details: { cached_tokens: 1200, cache_write_tokens: 30 } } }) }; };
    const out = await core.callOnce({ provider, model, apiKey: 'k', system: req.system, user: req.user, anthropicUser: req.anthropicUser,
      routerCache, fetch, setInterval, clearInterval, controller: new AbortController() });
    return { body, out: plain(out) };
  };
  const claude = await run('openrouter', 'anthropic/claude-opus-5.5', true);
  const [system, user] = claude.body.messages;
  assert.deepEqual(plain(system.content), [{ type: 'text', text: req.system, cache_control: { type: 'ephemeral', ttl: '1h' } }]);
  assert.deepEqual(user.content.map(part => part.cache_control ? 'cached' : 'plain'), ['plain', 'cached', 'plain']);  // the history end
  assert.equal(user.content.map(part => part.text).join(''), req.user);          // the same words as the string prompt
  assert.deepEqual(claude.out.cache, { read: 1200, write: 30 });
  const refine = await run('openrouter', 'anthropic/claude-opus-5.5', false);    // Refine's own call: unchanged
  assert.deepEqual(refine.body.messages.map(m => typeof m.content), ['string', 'string']);
  assert.equal(refine.out.cache, undefined);
  const other = await run('openrouter', 'x-ai/grok-5', true);                    // not Claude: the string prompt, counts read
  assert.deepEqual(other.body.messages.map(m => typeof m.content), ['string', 'string']);
  assert.deepEqual(other.out.cache, { read: 1200, write: 30 });
  const deepinfra = await run('deepinfra', 'zai-org/GLM-5.2', true);             // DeepInfra caches a prefix by itself
  assert.equal(deepinfra.body.messages[1].content, req.user);
  assert.deepEqual(deepinfra.out.cache, { read: 1200, write: 30 });
});
