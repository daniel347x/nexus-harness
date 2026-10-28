/* Shared browser Refine engine. Load refine-defaults.js first; callers own settings, context and UI. */
(() => {
  'use strict';
  const ENDPOINT = {
    anthropic: 'https://api.anthropic.com/v1/messages',
    openrouter: 'https://openrouter.ai/api/v1/chat/completions',
    deepinfra: 'https://api.deepinfra.com/v1/openai/chat/completions',
  };
  // Direct API list prices, $/MTok input/output/cache read (not routed-provider prices).
  const ANTHROPIC_PRICING = [
    [/^claude-opus-5-5$/, [4, 20, 0.20]],
    [/^claude-opus-(?:4-[5-8]|5)$/, [5, 25, 0.50]],
    [/^claude-(?:opus-4(?:-1)?|3-opus)$/, [15, 75, 1.50]],
    [/^claude-sonnet-5-5$/, [2, 10, 0.10]],
    [/^claude-sonnet-5$/, [2, 10, 0.20]],
    [/^claude-(?:sonnet-4(?:-[56])?|3(?:-[57])?-sonnet)$/, [3, 15, 0.30]],
    [/^claude-haiku-4-5$/, [1, 5, 0.10]],
    [/^claude-3-5-haiku$/, [0.80, 4, 0.08]],
  ];
  const ANTHROPIC_CACHE = Object.freeze({ type: 'ephemeral', ttl: '1h' });
  const CACHE_LIFETIME_MS = 60 * 60 * 1000;
  // One placement hint per logical target; never retains prompt text or an API key.
  const cacheMemo = new Map();
  const MAX_TOKENS = 8192;

  function refineOutputPolicy(p, model) {
    // Manager verified the Anthropic-direct Opus 5.5 catalogue: 1M context / 128,000 output.
    // Do not infer this allowance for aliases, routed models, or other providers/models.
    return p === 'anthropic' && model === 'claude-opus-5-5'
      ? { defaultTokens: 128000, maxTokens: 128000 }
      : { defaultTokens: MAX_TOKENS, maxTokens: 65536 };
  }
  // The Refine thinking choices, lowest first; the API's own names. Both routers accept xhigh/max in their enum
  // (OpenRouter maps a level a model lacks to its nearest), so the routed clamp below only reshapes low/medium/high.
  const THINK_LEVELS = Object.freeze(['low', 'medium', 'high', 'xhigh', 'max']);
  // A Refine completed at a costly level says so beneath its completion line (phone and desktop alike), so the
  // setting is not left on by mistake; high and below say nothing.
  const COSTLY_THINKING = Object.freeze({ xhigh: 'extra high', max: 'max' });
  function thinkingReminder(level) { return Object.hasOwn(COSTLY_THINKING, level) ? 'Reminder: you are currently set to ' + COSTLY_THINKING[level] : ''; }
  function thinkClampFor(model, level) {
    const m = String(model || '').toLowerCase();
    if (/qwen/.test(m)) return level === 'high' ? 'xhigh' : level;
    if (/kimi-k3|kimi[-_.]?k[-_.]?3|glm-5[-._][3-9]|glm-[6-9]|deepseek/.test(m)) return level === 'medium' ? 'low' : level;
    return level;
  }
  function substitute(text, map) { let out = text; for (const k in map) out = out.split(k).join(map[k]); return out; }
  function isWordChar(ch) { return !!ch && /[A-Za-z0-9_]/.test(ch); }
  function protectedBlock(text, dictionaryTerms = []) {
    const present = [];
    for (const term of dictionaryTerms) {
      if (!term) continue;
      let from = 0, idx;
      while ((idx = text.indexOf(term, from)) !== -1) {
        const before = idx > 0 ? text.charAt(idx - 1) : '';
        const after = idx + term.length < text.length ? text.charAt(idx + term.length) : '';
        if (!isWordChar(before) && !isWordChar(after)) { present.push(term); break; }
        from = idx + 1;
      }
    }
    if (!present.length) return '';
    return [
      '===== PROTECTED TERMS (custom vocabulary already applied to the text below) =====',
      'These terms were applied by a personal auto-correct dictionary — a semi-smart, pre-established list',
      'of custom words/phrases set up by the user — BEFORE this text reached you. They are almost always the',
      'intended, deliberate forms. DEFAULT: reproduce each EXACTLY as written — keep its casing, spelling,',
      'and punctuation, do not split or join it, and do not "correct" it into an ordinary word or phrase.',
      'ESCAPE HATCH: that dictionary is far less intelligent than you, so it occasionally MISFIRES (applies',
      'a custom term where the surrounding context clearly shows an ordinary word was meant). If — and ONLY',
      'if — you have VERY HIGH confidence from context that a specific listed term was such a misfire, you',
      'may change it back; otherwise keep it. Bias heavily toward keeping these.',
      present.map((t) => '- ' + t).join('\n'),
      '',
    ].join('\n') + '\n';
  }

  // @beacon[
  //   id=opc-pane@buildRefineRequest,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Assemble unchanged prompt text plus Anthropic history blocks from UTF16 boundaries; cache before the closing history wrapper and volatile dictionary/draft/transcription; the fixed read-only wrappers around the target are Refine's draft before/after by default, or the caller's own (Read Aloud: the message spoken so far / the rest still to rewrite); no page state or target cropping.,
  // ]
  function buildRequest({ transcription, parts, history = '', historyBoundaries, draftBefore = '', draftAfter = '', dictionaryTerms = [], wrappers = null }) {
    history = history.trim() ? history : '';
    draftBefore = draftBefore.trim() ? draftBefore : '';
    draftAfter = draftAfter.trim() ? draftAfter : '';
    // Each wrapper is [preamble, its placeholder]: what comes before the target, then what follows it.
    const [beforeWrap, beforeSlot] = wrappers?.before || [REFINE_DRAFT_BEFORE_PREAMBLE, '{{draft_before}}'];
    const [afterWrap, afterSlot] = wrappers?.after || [REFINE_DRAFT_AFTER_PREAMBLE, '{{draft_after}}'];
    const context = history ? substitute(parts.context, { '{{context}}': history }) + '\n\n' : '';
    const user = context
      + (draftBefore ? substitute(beforeWrap, { [beforeSlot]: draftBefore }) + '\n\n' : '')
      + protectedBlock(transcription, dictionaryTerms) + substitute(parts.transcription, { '{{transcription}}': transcription }) + '\n\n'
      + (draftAfter ? substitute(afterWrap, { [afterSlot]: draftAfter }) + '\n\n' : '')
      + parts.finalfence;
    const placeholder = parts.context.indexOf('{{context}}');
    const prefix = history ? (placeholder < 0 ? context : parts.context.slice(0, placeholder) + history) : '';
    let anthropicUser = [];
    if (prefix.trim()) {
      if (placeholder >= 0) {
        const ends = Array.isArray(historyBoundaries) ? historyBoundaries.map(b => b?.end) : [];
        // Old callers can still supply a string alone. Bad optional boundary metadata changes
        // only cache placement, never the text sent or permission to Refine.
        const valid = ends.length && ends.at(-1) === history.length && ends.every((end, i) =>
          Number.isSafeInteger(end) && end > (i ? ends[i - 1] : 0) &&
          !(end < history.length && /[\uD800-\uDBFF]/.test(history[end - 1]) && /[\uDC00-\uDFFF]/.test(history[end])));
        let start = 0;
        const turns = valid ? ends.map(end => { const text = history.slice(start, end); start = end; return text; })
          : history.split(/(?=\n\n---\n\n\[(?:USER|ASSISTANT)\]\n)/);
        turns[0] = parts.context.slice(0, placeholder) + turns[0];
        anthropicUser = turns.map(text => ({ type: 'text', text }));
      } else anthropicUser = [{ type: 'text', text: prefix }];
      anthropicUser.at(-1).cache_control = { ...ANTHROPIC_CACHE };
    }
    // Separators belong to the following block. The closing wrapper cannot be cached on
    // the last history turn: it would move when history grows and destroy that boundary.
    anthropicUser.push({ type: 'text', text: user.slice(anthropicUser.length ? prefix.length : 0) });
    return { system: parts.system, user, anthropicUser, transcription, history, draftBefore, draftAfter };
  }

  // The provider verifies real prefix identity. This small checksum is only a placement hint:
  // an edited/cropped/repartitioned prefix must not inherit an old breakpoint by array index.
  function prefixFingerprint(blocks, end = blocks.length - 1) {
    let a = 2166136261, b = 2246822519;
    for (let i = 0; i <= end; i++) {
      const text = blocks[i].text, framed = text.length + ':' + text;
      for (let j = 0; j < framed.length; j++) {
        const code = framed.charCodeAt(j);
        a = Math.imul(a ^ code, 16777619); b = Math.imul(b ^ code, 3266489917);
      }
    }
    return (a >>> 0).toString(36) + ':' + (b >>> 0).toString(36) + ':' + (end + 1);
  }

  // @beacon[
  //   id=opc-pane@anthropicRefinePrompt,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=One-hour system/current-history breakpoints plus an unexpired matching previous-end memo; three markers maximum and all prompt text is sent on a miss; the memo contains no prose or credentials.,
  // ]
  function anthropicPrompt({ system, user, anthropicUser, cacheKey, model, thinking }, at) {
    const blocks = (anthropicUser || [{ type: 'text', text: user }]).map(block => ({ ...block,
      ...(block.cache_control ? { cache_control: { ...ANTHROPIC_CACHE } } : {}) }));
    const end = blocks.reduce((last, block, index) => block.cache_control ? index : last, -1);
    const key = typeof cacheKey === 'string' && cacheKey ? cacheKey : null;
    const scope = JSON.stringify([model, thinking, prefixFingerprint([{ text: system || '' }])]);
    const previous = key && cacheMemo.get(key);
    if (end >= 0 && previous && previous.scope === scope && at - previous.at < CACHE_LIFETIME_MS &&
        previous.end < end && prefixFingerprint(blocks, previous.end) === previous.fingerprint) {
      blocks[previous.end].cache_control = { ...ANTHROPIC_CACHE };
    }
    const memo = key && end >= 0 ? { key, scope, end, at, fingerprint: prefixFingerprint(blocks, end) } : null;
    return { system: system?.trim() ? [{ type: 'text', text: system, cache_control: { ...ANTHROPIC_CACHE } }] : system,
      messages: [{ role: 'user', content: blocks }], memo };
  }

  function anthropicRates(model, usage) {
    const m = String(model || '').toLowerCase().replace(/-\d{8}$/, '');
    if (m === 'claude-haiku-5-5') {
      const writes = usage.cache_creation_input_tokens ?? ((usage.cache_creation?.ephemeral_5m_input_tokens || 0)
        + (usage.cache_creation?.ephemeral_1h_input_tokens || 0));
      return (usage.input_tokens || 0) + (usage.cache_read_input_tokens || 0) + writes > 100000
        ? [0.50, 2.50, 0.05] : [0.10, 0.50, 0.01];
    }
    return ANTHROPIC_PRICING.find(([pattern]) => pattern.test(m))?.[1] || null;
  }

  // @beacon[
  //   id=opc-pane@estimateAnthropicRefineCost,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Direct-model input/output/read prices with separate 1.25x five-minute and 2x one-hour writes; never double-charge aggregate creation tokens, and unknown models remain unpriced rather than blocked.,
  // ]
  function estimateAnthropicCost(model, usage) {
    if (!usage) return null;
    const rates = anthropicRates(model, usage);
    if (!rates) return null;
    const [inPer, outPer, readPer] = rates;
    const writes5m = usage.cache_creation?.ephemeral_5m_input_tokens || 0;
    const writes1h = usage.cache_creation?.ephemeral_1h_input_tokens || 0;
    const unspecified = Math.max(0, (usage.cache_creation_input_tokens || 0) - writes5m - writes1h);
    return ((usage.input_tokens || 0) * inPer + (usage.output_tokens || 0) * outPer
      + (usage.cache_read_input_tokens || 0) * readPer
      + (writes5m * 1.25 + (writes1h + unspecified) * 2) * inPer) / 1e6;
  }

  // @beacon[
  //   id=opc-pane@refineCacheSummary,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Compact per-call measured cache read/write counts for desktop cost and phone completion lines; absent provider counters stay unknown, routers have no added readout.,
  // ]
  function cacheSummary(cache) {
    if (!cache) return '';
    const count = value => typeof value !== 'number' ? '—' : value < 1000 ? String(value)
      : (Math.round(value / 100) / 10) + 'K';
    return 'cache: read ' + count(cache.read) + ' · wrote ' + count(cache.write);
  }
  function incompleteResponse(reason) {
    const e = new Error('Refine output not accepted (' + reason + '). Original intact in the box/recovery. For an output limit, increase the Refine output budget deliberately or refine smaller selections.');
    e.noRetry = true; // a semantic failure is not a network error; never pay for automatic retries
    return e;
  }
  function responseText(p, j) {
    if (!j || j.error) throw incompleteResponse('provider error/invalid response');
    if (j.status && j.status !== 'completed') throw incompleteResponse('status ' + j.status);
    if (j.incomplete_details) throw incompleteResponse('incomplete response');
    if (p === 'anthropic') {
      if (j.stop_reason !== 'end_turn') throw incompleteResponse('stop_reason ' + (j.stop_reason || 'missing'));
      if (!Array.isArray(j.content) || j.content.some((b) => b?.type === 'refusal' || (b?.type !== 'text' && b?.type !== 'thinking' && b?.type !== 'redacted_thinking'))) {
        throw incompleteResponse('refusal/unsupported content');
      }
      const text = j.content.filter((b) => b?.type === 'text').map((b) => b.text).join('');
      if (j.content.some((b) => b?.type === 'text' && typeof b.text !== 'string') || !text.trim()) throw incompleteResponse('empty/invalid text');
      return text;
    }
    const c = j.choices?.[0];
    if (c?.finish_reason !== 'stop') throw incompleteResponse('finish_reason ' + (c?.finish_reason || 'missing'));
    // OpenRouter may expose the underlying provider stop reason in addition to its normalized one.
    if (c.native_finish_reason && !['stop', 'end_turn', 'stop_sequence', 'STOP'].includes(c.native_finish_reason)) throw incompleteResponse('native_finish_reason ' + c.native_finish_reason);
    if (c.message?.refusal || c.message?.tool_calls?.length || c.message?.function_call) throw incompleteResponse('refusal/tool response');
    const text = c.message?.content;
    if (typeof text !== 'string' || !text.trim()) throw incompleteResponse('empty/invalid text');
    return text;
  }

  // @beacon[
  //   id=opc-pane@readAnthropicStream,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Shared per-call Anthropic SSE buffer by block index; merge cumulative usage including cache-TTL buckets and require terminal message_stop/end_turn; malformed partial error or aborted streams never permit paid continuation.,
  // ]
  async function readAnthropicStream(resp, ctrl) {
    if (resp.headers?.get('Content-Type')?.toLowerCase().split(';')[0].trim() !== 'text/event-stream' || !resp.body?.getReader) {
      throw incompleteResponse('Anthropic streaming response unavailable');
    }
    let reader;
    try { reader = resp.body.getReader(); } catch (err) { throw incompleteResponse('unreadable Anthropic stream'); }
    const decoder = new TextDecoder('utf-8', { fatal: true });
    const blocks = new Map();
    let message = null, terminal = false, finalDelta = false;
    let line = '', afterCR = false, eventName = '', data = [];
    const checkAbort = () => { if (ctrl.signal.aborted) throw new DOMException('Aborted', 'AbortError'); };
    const mergeUsage = (usage, initial = false) => {
      if (!usage || typeof usage !== 'object' || !Number.isInteger(usage.output_tokens) || usage.output_tokens < 0 ||
          (initial && (!Number.isInteger(usage.input_tokens) || usage.input_tokens < 0))) throw incompleteResponse('missing/invalid stream usage');
      for (const k of ['input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens']) {
        if (usage[k] !== undefined && (!Number.isInteger(usage[k]) || usage[k] < 0)) throw incompleteResponse('invalid stream token usage');
      }
      for (const k of ['ephemeral_5m_input_tokens', 'ephemeral_1h_input_tokens']) {
        const value = usage.cache_creation?.[k];
        if (value !== undefined && (!Number.isInteger(value) || value < 0)) throw incompleteResponse('invalid stream cache usage');
      }
      const creation = { ...message.usage.cache_creation, ...usage.cache_creation };
      message.usage = { ...message.usage, ...usage }; // cumulative counters overwrite, never add deltas
      if (Object.keys(creation).length) message.usage.cache_creation = creation;
    };
    const dispatch = () => {
      const name = eventName, raw = data.join('\n'); eventName = ''; data = [];
      if (name === 'ping') return;
      // Anthropic documents future event types; ignore unknown event names, not known malformed events.
      if (name && !['message_start', 'content_block_start', 'content_block_delta', 'content_block_stop', 'message_delta', 'message_stop', 'error', 'message'].includes(name)) return;
      if (!raw) { if (name) throw incompleteResponse('empty stream event'); return; }
      let e;
      try { e = JSON.parse(raw); } catch (err) { throw incompleteResponse('malformed stream JSON'); }
      if (!e || typeof e.type !== 'string' || (name && name !== 'message' && name !== e.type)) throw incompleteResponse('stream event type mismatch');
      if (e.type === 'error') throw incompleteResponse('stream error ' + (e.error?.type || 'unknown'));
      if (e.type === 'ping') return;
      if (!['message_start', 'content_block_start', 'content_block_delta', 'content_block_stop', 'message_delta', 'message_stop'].includes(e.type)) return;
      if (terminal) throw incompleteResponse('event after message_stop');
      if (e.type === 'message_start') {
        if (message || !e.message || e.message.role !== 'assistant' || !Array.isArray(e.message.content) || e.message.content.length) throw incompleteResponse('invalid/duplicate message_start');
        message = { ...e.message, content: [], usage: {} }; mergeUsage(e.message.usage, true); return;
      }
      if (!message) throw incompleteResponse('stream event before message_start');
      if (e.type === 'message_delta') {
        if ([...blocks.values()].some((b) => !b.stopped) || !e.delta || typeof e.delta !== 'object') throw incompleteResponse('premature/invalid message_delta');
        Object.assign(message, e.delta); mergeUsage(e.usage);
        if (e.delta.stop_reason != null) {
          if (finalDelta) throw incompleteResponse('duplicate terminal message_delta');
          finalDelta = true;
          if (e.delta.stop_reason !== 'end_turn') throw incompleteResponse('stop_reason ' + e.delta.stop_reason);
        }
        return;
      }
      if (e.type === 'message_stop') {
        if (!finalDelta || message.stop_reason !== 'end_turn' || [...blocks.values()].some((b) => !b.stopped)) throw incompleteResponse('message_stop without complete output');
        terminal = true; return;
      }
      if (finalDelta || !Number.isInteger(e.index) || e.index < 0) throw incompleteResponse('invalid stream block order/index');
      if (e.type === 'content_block_start') {
        const block = e.content_block;
        if (blocks.has(e.index) || !block || !['text', 'thinking', 'redacted_thinking'].includes(block.type) ||
            (block.type === 'text' && typeof block.text !== 'string')) throw incompleteResponse('invalid/unsupported/refusal stream block');
        // Keep text only. Thinking/signatures are validated but never accumulated into the textarea.
        blocks.set(e.index, { type: block.type, text: block.type === 'text' ? [block.text] : [], stopped: false }); return;
      }
      const block = blocks.get(e.index);
      if (!block || block.stopped) throw incompleteResponse('delta/stop for absent or closed block');
      if (e.type === 'content_block_stop') { block.stopped = true; return; }
      const delta = e.delta;
      if (block.type === 'text' && delta?.type === 'text_delta' && typeof delta.text === 'string') block.text.push(delta.text);
      else if (block.type === 'text' && delta?.type === 'citations_delta' && delta.citation) { /* not transcription text */ }
      else if (block.type === 'thinking' && ((delta?.type === 'thinking_delta' && typeof delta.thinking === 'string') ||
          (delta?.type === 'signature_delta' && typeof delta.signature === 'string'))) { /* deliberately not retained */ }
      else throw incompleteResponse('unsupported/invalid stream delta');
    };
    const onLine = (value) => {
      if (!value) { dispatch(); return; }
      if (value.startsWith(':')) return;
      const colon = value.indexOf(':'), field = colon < 0 ? value : value.slice(0, colon);
      let v = colon < 0 ? '' : value.slice(colon + 1); if (v.startsWith(' ')) v = v.slice(1);
      if (field === 'event') eventName = v; else if (field === 'data') data.push(v);
    };
    const decodeLines = (text) => {
      for (const ch of text) {
        if (afterCR) { afterCR = false; if (ch === '\n') continue; }
        if (ch === '\r' || ch === '\n') { onLine(line); line = ''; afterCR = ch === '\r'; }
        else line += ch;
      }
    };
    const cancel = () => { reader.cancel().catch(() => {}); };
    ctrl.signal.addEventListener('abort', cancel, { once: true });
    try {
      checkAbort();
      for (;;) {
        const chunk = await reader.read(); checkAbort();
        if (chunk.done) { decodeLines(decoder.decode()); break; }
        decodeLines(decoder.decode(chunk.value, { stream: true }));
      }
      if (line || eventName || data.length) throw incompleteResponse('unterminated SSE frame/EOF');
      if (!terminal) throw incompleteResponse('stream EOF before message_stop');
      const ordered = [...blocks.entries()].sort(([a], [b]) => a - b);
      if (ordered.some(([index], i) => index !== i)) throw incompleteResponse('missing stream block');
      message.content = ordered.map(([, b]) => ({ type: b.type, ...(b.type === 'text' ? { text: b.text.join('') } : {}) }));
      return message;
    } catch (err) {
      if (ctrl.signal.aborted) throw new DOMException('Aborted', 'AbortError');
      if (err.noRetry) throw err;
      // Once streaming starts, EOF/read/UTF-8 failure is not permission for a second paid generation.
      throw incompleteResponse('stream interrupted/invalid encoding');
    } finally {
      ctrl.signal.removeEventListener('abort', cancel);
      try { await reader.cancel(); } catch (err) { /* already closed/errored */ }
      reader.releaseLock();
    }
  }

  // @beacon[
  //   id=opc-pane@callOnce,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Shared provider call with terminal buffering and measured cache counts; Anthropic uses one-hour system/previous/current history breakpoints and model-specific costs while routers retain string prompts unless routerCache is set (Refine on desktop and phone, and Read Aloud's passes: a Claude model through OpenRouter gets the system/history breakpoints in content parts, and a router's cached-token counts are read); deadlines and thinking remain caller-owned.,
  // ]
  async function callOnce(options) {
    const { provider: p, model, apiKey, system, user, anthropicUser, cacheKey, thinking: lvl = 'medium', routerCache = false,
      outputTokens = refineOutputPolicy(p, model).defaultTokens,
      controller: ctrl = new AbortController(), signal, deadline, now = () => Date.now(),
      fetch: transport = (...args) => globalThis.fetch(...args),
      setInterval: startInterval = (...args) => globalThis.setInterval(...args),
      clearInterval: stopInterval = (...args) => globalThis.clearInterval(...args) } = options;
    // Controllers preserve the PC's reason_ flags; a separate caller signal means user cancellation.
    const cancel = () => { ctrl.reason_ = 'user'; ctrl.abort(); };
    if (signal && signal !== ctrl.signal) {
      if (signal.aborted) cancel(); else signal.addEventListener('abort', cancel, { once: true });
    }
    const timer = deadline ? startInterval(() => {
      if (ctrl.signal.aborted) return stopInterval(timer);
      const end = deadline();
      if (end && now() >= end) { ctrl.reason_ = 'timeout'; ctrl.abort(); stopInterval(timer); }
    }, 500) : null;
    try {
      if (p === 'openrouter' || p === 'deepinfra') {
        // Through OpenRouter a Claude model caches only at Anthropic's own breakpoints, sent in content parts (DeepInfra
        // caches a repeated prefix by itself); Read Aloud asks for them, since each pass re-sends the same history.
        const parts = routerCache && p === 'openrouter' && /^anthropic\//.test(String(model)) && Array.isArray(anthropicUser) && !!system?.trim();
        const messages = parts
          ? [{ role: 'system', content: [{ type: 'text', text: system, cache_control: { ...ANTHROPIC_CACHE } }] },
            { role: 'user', content: anthropicUser.map(block => ({ type: 'text', text: block.text, ...(block.cache_control ? { cache_control: { ...ANTHROPIC_CACHE } } : {}) })) }]
          : [{ role: 'system', content: system }, { role: 'user', content: user }];
        const b = { model, max_tokens: outputTokens, messages, usage: { include: true } };
        if (p === 'openrouter') b.reasoning = { effort: thinkClampFor(model, lvl) }; else b.reasoning_effort = thinkClampFor(model, lvl);
        const resp = await transport(ENDPOINT[p], {
          method: 'POST', signal: ctrl.signal,
          headers: { Authorization: 'Bearer ' + apiKey, 'Content-Type': 'application/json', 'X-Title': 'Transcription Control (OpenCode pane)' },
          body: JSON.stringify(b),
        });
        const j = await resp.json().catch(() => null);
        if (!resp.ok) { const e = new Error((j && j.error && (j.error.message || j.error)) || 'HTTP ' + resp.status); e.status = resp.status; throw e; }
        if (ctrl.signal.aborted) throw new DOMException('Aborted', 'AbortError');
        const txt = responseText(p, j);
        const cost = j.usage ? (typeof j.usage.estimated_cost === 'number' ? j.usage.estimated_cost : (typeof j.usage.cost === 'number' ? j.usage.cost : null)) : null;
        const details = routerCache && j.usage?.prompt_tokens_details;
        return { text: txt, cost, estimated: false,
          ...(details ? { cache: { read: Number.isInteger(details.cached_tokens) ? details.cached_tokens : null,
            write: Number.isInteger(details.cache_write_tokens) ? details.cache_write_tokens : null } } : {}) };
      }
      const prompt = anthropicPrompt({ system, user, anthropicUser, cacheKey, model, thinking: lvl }, now());
      const b = { model, max_tokens: outputTokens, stream: true, system: prompt.system, messages: prompt.messages };
      const m = String(model).toLowerCase();
      if (/claude-3|claude-(opus|sonnet|haiku)-4-[0-5](?![0-9])/.test(m)) {
        const budget = { low: 4096, medium: 10240, high: 20480, xhigh: 32768, max: 49152 }[lvl] || 10240;
        b.thinking = { type: 'enabled', budget_tokens: budget };
        b.max_tokens += budget; // reserve the selected text allowance separately from fixed thinking
      } else {
        b.thinking = { type: 'adaptive' };
        // Opus/Sonnet 4.6 accept max but not xhigh: step down to high rather than draw a 400.
        b.output_config = { effort: lvl === 'xhigh' && /claude-(opus|sonnet)-4-6(?![0-9])/.test(m) ? 'high' : lvl };
      }
      const resp = await transport(ENDPOINT.anthropic, {
        method: 'POST', signal: ctrl.signal,
        headers: { 'x-api-key': apiKey, 'anthropic-version': '2023-06-01', 'anthropic-dangerous-direct-browser-access': 'true', 'Content-Type': 'application/json' },
        body: JSON.stringify(b),
      });
      if (!resp.ok) {
        const j = await resp.json().catch(() => null);
        const e = new Error((j && j.error && (j.error.message || j.error.type)) || 'HTTP ' + resp.status); e.status = resp.status; throw e;
      }
      const j = await readAnthropicStream(resp, ctrl);
      if (ctrl.signal.aborted) throw new DOMException('Aborted', 'AbortError');
      const txt = responseText(p, j);
      // A failed stream leaves the last successful hint intact. Completion ordering cannot
      // replace a newer call's hint with an older overlapping call's end.
      if (prompt.memo && (!cacheMemo.has(prompt.memo.key) || cacheMemo.get(prompt.memo.key).at <= prompt.memo.at)) {
        const { key, ...memo } = prompt.memo; cacheMemo.set(key, memo);
      }
      const usage = j.usage;
      const writes = usage.cache_creation_input_tokens ?? (usage.cache_creation
        ? (usage.cache_creation.ephemeral_5m_input_tokens || 0) + (usage.cache_creation.ephemeral_1h_input_tokens || 0) : null);
      return { text: txt, cost: estimateAnthropicCost(model, usage), estimated: true,
        cache: { read: usage.cache_read_input_tokens ?? null, write: writes } };
    } catch (err) {
      if (err && err.name === 'AbortError') {
        const e = new Error(ctrl.reason_ === 'user' ? 'Refine canceled.' : 'Refine timed out.');
        e.userCanceled = ctrl.reason_ === 'user'; e.wasAbort = ctrl.reason_ !== 'user'; throw e;
      }
      throw err;
    } finally {
      if (timer !== null) stopInterval(timer);
      if (signal && signal !== ctrl.signal) signal.removeEventListener('abort', cancel);
    }
  }

  // @beacon[
  //   id=opc-pane@retryRefineRequest,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The single existing Refine retry policy: bounded pre-stream network and HTTP transient backoff; never retry semantic or started-stream failure or cancellation and never escalate output or switch models.,
  // ]
  async function callWithRetry(options) {
    const delays = [500, 1000, 2000, 4000, 8000];
    const sleep = options.setTimeout || ((...args) => globalThis.setTimeout(...args));
    let networkTries = 0, lastErr = null;
    for (let attempt = 0; attempt <= delays.length; attempt++) {
      try { return await callOnce(options); } catch (err) {
        lastErr = err;
        if (err.userCanceled || err.wasAbort || err.noRetry) throw err;
        const isNetwork = err.status === undefined;
        const transient = isNetwork || err.status === 429 || (err.status >= 500 && err.status <= 599);
        if (!transient || attempt === delays.length) throw err;
        if (isNetwork && ++networkTries > 2) throw err;
        await new Promise((res) => sleep(res, delays[attempt]));
        if (options.controller?.signal.aborted || options.signal?.aborted) throw err;
      }
    }
    throw lastErr || new Error('Refine request failed');
  }

  globalThis.opcRefineCore = Object.freeze({ buildRequest, callOnce, callWithRetry,
    refineOutputPolicy, thinkClampFor, THINK_LEVELS, thinkingReminder, protectedBlock, responseText, incompleteResponse,
    readAnthropicStream, estimateAnthropicCost, cacheSummary });
})();
