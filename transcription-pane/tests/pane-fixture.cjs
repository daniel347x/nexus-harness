// Offline VM and provider-stream fixtures. Fake API key string; never any external fetch.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const defaults = fs.readFileSync(path.join(__dirname, '../static/refine-defaults.js'), 'utf8');
const core = fs.readFileSync(path.join(__dirname, '../static/refine-core.js'), 'utf8');
const ocManager = fs.readFileSync(path.join(__dirname, '../static/opencode-manager.js'), 'utf8');
const contentText = content => typeof content === 'string' ? content : content.map(block => block.text).join('');
const sseFrame = e => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`;
function messageEvents(response) {
  const j = response || {};
  if (j.error) return [{ type: 'error', error: j.error }];
  const events = [{ type: 'message_start', message: {
    ...j, id: 'msg_offline', type: 'message', role: 'assistant', content: [], stop_reason: null,
    usage: { input_tokens: 100, output_tokens: 1, ...j.usage },
  } }];
  for (const [index, content_block] of (j.content || []).entries()) {
    events.push({ type: 'content_block_start', index, content_block }, { type: 'content_block_stop', index });
  }
  events.push({ type: 'message_delta', delta: { stop_reason: j.stop_reason }, usage: { output_tokens: 10 } }, { type: 'message_stop' });
  return events;
}
function streamResponse(input, { sizes = [4096], chunks, readHook, failAt = -1, onCancel } = {}) {
  const raw = typeof input === 'string' ? input : input.map(sseFrame).join('');
  if (!chunks) {
    const bytes = new TextEncoder().encode(raw); chunks = [];
    for (let offset = 0, i = 0; offset < bytes.length; i++) {
      const size = sizes[i % sizes.length]; chunks.push(bytes.subarray(offset, offset + size)); offset += size;
    }
  }
  let index = 0;
  const body = new ReadableStream({
    async pull(controller) {
      if (readHook) await readHook(index);
      if (index === failAt) { controller.error(new Error('offline network read failure')); return; }
      if (index >= chunks.length) { controller.close(); return; }
      controller.enqueue(chunks[index++]);
    },
    cancel() { if (onCancel) onCancel(); },
  });
  return { ok: true, headers: new Headers({ 'Content-Type': 'text/event-stream; charset=utf-8' }), body };
}

function pane({ provider = 'anthropic', response, clipboardFails = false, storage, fetchHook, responseHook, manualTimers = false, coreHook } = {}) {
  storage ||= new Map();
  storage.set('refine_provider', provider);
  storage.set(`refine_${provider}_api_key`, 'offline-fixture-not-a-key');
  const elements = new Map(), requests = [], copies = [];
  const pendingTimers = [];
  const el = id => {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', innerHTML: '', selectionStart: 0, selectionEnd: 0, style: {}, options: [], dataset:{},
      replaceChildren(...children) { this.children=children; this.options=children; this.value=children[0]?.value || ''; },
      focus() {}, setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; },
      add(o) { this.options.push(o); if (!this.value) this.value = o.value; }, showModal() {}, close() {}, addEventListener(name, fn) { this['on'+name] = fn; },
    });
    return elements.get(id);
  };
  let failWrite = false;
  const ctx = {
    document: { getElementById: el, addEventListener() {} }, TextEncoder, TextDecoder, AbortController, DOMException, URLSearchParams, crypto:webcrypto,
    localStorage: { get length() { return storage.size; }, key: i => [...storage.keys()][i] ?? null,
      getItem: k => storage.get(k) ?? null, setItem: (k, v) => {
      if (failWrite) throw new Error('fixture quota exceeded'); storage.set(k, v);
    }, removeItem: k => storage.delete(k) },
    navigator: { clipboard: { writeText: async t => { copies.push(t); if (clipboardFails) throw new Error('denied'); } } },
    confirm: () => true, prompt: () => { throw new Error('no live keys'); },
    Option: function(text, value) { this.text = text; this.value = value; },
    setInterval: () => 1, clearInterval() {},
    setTimeout: manualTimers ? ((fn) => { pendingTimers.push(fn); return pendingTimers.length; }) : (() => 1),
    clearTimeout() {},
    fetch: async (url, options) => {
      requests.push([url, options?.body && JSON.parse(options.body)]);
      if (fetchHook) await fetchHook(ctx, url);
      if (url === '/oc/api/session/active') return { ok: true, json: async () => ({ data: {} }) };
      if (url === '/api/send') return { ok: true, json: async () => ({ data: { id: 'offline-id' }, thinking: 'unchanged' }) };
      if (!url.startsWith('https://')) throw new Error('unexpected offline request ' + url);
      if (responseHook) return responseHook(ctx, url, options);
      return provider === 'anthropic' ? streamResponse(messageEvents(response)) : { ok: true, json: async () => response };
    },
  };
  vm.createContext(ctx);
  vm.runInContext(defaults + '\n' + core, ctx);
  vm.runInContext(ocManager, ctx);
  if (coreHook) coreHook(ctx);
  vm.runInContext(app.replace(/main\(\);\s*$/, '') + '\nthis.api = {state, K, refine, send, retainOriginal, readOriginals, responseText, callWithRetry, callOnce, readAnthropicStream, showRecovery, saveDraft, buildContext, getOutputTokens, refineOutputPolicy, renderOutputSettings, wireSettings, freezeRefineTarget, sameRefineTarget, revalidateRefineTarget, draftChanged, invalidateTarget, renderTarget, refreshContext, wire, validatedClaudeTarget, setBackend, pollWatcher, renderClaudeLayout, sendClaude, pollClaudeManager, controlClaude, initializeClaudeJournal, saveClaudeJournal, renderClaudeManager, isClaudeManager, reconnectableManager, sleepingManagerText, endedManagerText, validateReconnect, awaitReconnectedManager, blockClaudeTarget, reselectCurrentManager, acceptClaudeTarget, initializeTargetState, hostSessionMatches, openNewManager, launchNewManager, adoptLaunchedManager, renderNewManager, restoreLaunch, handleTargetMessage, claudeBusy, claudeSteerBlock, claudeRecoveryNote, pendingConversationSend, stuckClaudeSend, releaseClaudeSend, exportClearClaudeJournal, replayExclusions, copyText, status, resolveStatus};', ctx);
  vm.runInContext('Object.assign(this.api,{openCodeManagerApi,acceptOpenCodeManager,sendOpenCodeManager,controlOpenCodeManager,pollOpenCodeManager,applyOpenCodeSendReceipt,launchOpenCodeManager,adoptOpenCodeLaunch,newManagerRuntime,selectedOpenCodeModel,fillOpenCodeModels,loadOpenCodeManagerCatalog})',ctx);
  return { ctx, api: ctx.api, box: el('transcript'), el, requests, copies, storage,
    failWrites: () => { failWrite = true; },
    // Manual-timer mode: reconnect readiness sleeps are driven explicitly by the test.
    fireTimers: async () => { for (const fn of pendingTimers.splice(0)) fn(); await new Promise((resolve) => setImmediate(resolve)); },
    pendingTimers: () => pendingTimers.length };
}
module.exports = { pane, sseFrame, messageEvents, streamResponse, contentText };
