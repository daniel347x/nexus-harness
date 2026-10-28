/* Read Aloud: one conversation turn rewritten for listening, saved on the pane server with its ElevenLabs audio,
 * and played in a pop-over player. Shared by the desktop conversation viewer (sessions.js) and the phone page (m.js);
 * load refine-defaults.js and refine-core.js first.
 *
 * The rewrite is Refine's own call (opcRefineCore: the same provider, model, key, thinking, retry and Anthropic prompt
 * caching), with the read-aloud prompt parts (refine-defaults.js READ_ALOUD_*) and the conversation's prose BEFORE the
 * clicked message as its chat history (the server's Refine context readers, cut at that message). The pane server
 * keeps the result keyed to conversation + message, makes paragraph-sized tap/synthesis parts and synthesizes each
 * part once through ElevenLabs (the key never reaches the page); the player plays part by part and prefetches the
 * next, so a long reply starts as soon as its first part is ready. A second click on the same turn replays it.
 */
(() => {
  'use strict';
  const RATE_KEY = 'pane_read_aloud_rate_v1';
  const RATES = [0.75, 1, 1.1, 1.2, 1.25, 1.35, 1.5, 1.75, 2];
  const DEFAULT_MODELS = { anthropic: 'claude-opus-5-5', openrouter: 'anthropic/claude-opus-5.5', deepinfra: 'zai-org/GLM-5.2' };
  const PROVIDER_LABEL = { anthropic: 'Anthropic', openrouter: 'OpenRouter', deepinfra: 'DeepInfra' };
  // A twentieth of a second of silence played inside the click itself, so iOS lets the same element play later
  // without a new tap (8 kHz, 8-bit mono PCM: 44-byte header, then 400 samples at the 128 midpoint).
  const SILENT_WAV = (() => {
    const bytes = new Uint8Array(444).fill(128), view = new DataView(bytes.buffer), text = (at, s) => [...s].forEach((c, i) => { bytes[at + i] = c.charCodeAt(0); });
    text(0, 'RIFF'); view.setUint32(4, 436, true); text(8, 'WAVEfmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true); view.setUint32(24, 8000, true); view.setUint32(28, 8000, true); view.setUint16(32, 1, true);
    view.setUint16(34, 8, true); text(36, 'data'); view.setUint32(40, 400, true);
    return 'data:audio/wav;base64,' + btoa(String.fromCharCode(...bytes));
  })();
  const DEFAULT_CPS = 15; // spoken characters per second, for the seek bar until a part's real length is known
  const SKIP_SECONDS = 10; // −10s / +10s and ←/→: seconds of the audio itself, whatever the playback speed
  // A long message is rewritten in passes so the voice starts after the first one: a short first pass (it decides how soon
  // the voice starts), then larger ones (the rewrite runs many times faster than the voice reads, so each pass's audio
  // covers the next pass's rewrite). A message up to `single` characters is one pass: the old single rewrite, unchanged.
  const PASSES = Object.freeze({ single: 1200, first: 700, second: 2000, max: 6000 });
  const jobs = new Map(); // item key → the rewrite in flight; it outlives a closed player, and a reopen attaches to it
  let current = null; // the one open player

  function store() { try { return globalThis.localStorage || null; } catch (_) { return null; } }
  function setting(key, fallback = null) { try { const v = store()?.getItem(key); return v === null || v === undefined ? fallback : v; } catch (_) { return fallback; } }
  function part(p) { const saved = setting(p.storage); return saved === null || p.requires.some(r => !saved.includes(r)) ? p.def : saved; }
  function parts() { return Object.fromEntries(READ_ALOUD_PARTS.map(p => [p.id, part(p)])); }
  function substitute(text, map) { let out = text; for (const k in map) out = out.split(k).join(map[k]); return out; }
  function kb(bytes) { return (bytes / 1024).toFixed(1) + ' KB'; }
  function clock(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
    const s = Math.floor(seconds), h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, r = String(s % 60).padStart(2, '0');
    return h ? h + ':' + String(m).padStart(2, '0') + ':' + r : m + ':' + r;
  }
  function plain(message, extra = {}) { return Object.assign(new Error(message), extra); }

  // Refine's provider settings when the page passes none (the phone's rule: the saved model, else the first listed,
  // else the default). The desktop conversation frame passes the editor's own (app.js opcRefineSettings).
  function ownSettings() {
    const saved = setting('refine_provider', 'anthropic'), p = Object.hasOwn(DEFAULT_MODELS, saved) ? saved : 'anthropic';
    let model = setting('refine_' + p + '_model');
    if (!model || !model.trim()) {
      try { const list = JSON.parse(setting('refine_' + p + '_models_list', '[]')); model = Array.isArray(list) && typeof list[0] === 'string' && list[0].trim() ? list[0] : DEFAULT_MODELS[p]; }
      catch (_) { model = DEFAULT_MODELS[p]; }
    }
    const think = setting('refine_think_level', 'medium');
    return { provider: p, model, apiKey: (setting('refine_' + p + '_api_key', '') || '').trim(),
      thinking: opcRefineCore.THINK_LEVELS.includes(think) ? think : 'medium', outputTokens: null,
      historyMode: setting('refine_history_mode', 'whole') === 'budgeted' ? 'budgeted' : 'whole', budgetKb: 50 };
  }
  function settingsFor(spec) {
    let s = null;
    try { s = typeof spec.settings === 'function' ? spec.settings() : null; } catch (_) { s = null; }
    return s && typeof s.provider === 'string' ? { ...ownSettings(), ...s } : ownSettings();
  }
  // Refine's model list for one provider in this browser (the saved list, the saved model, the built-in default):
  // what the 🔊 Read Aloud rewrite-model picker offers, and a provider-only override's model.
  function refineModels(p) {
    let list = [];
    try { const a = JSON.parse(setting('refine_' + p + '_models_list', '[]')); if (Array.isArray(a)) list = a.filter(m => typeof m === 'string' && m.trim()); } catch (_) {}
    for (const m of [setting('refine_' + p + '_model'), DEFAULT_MODELS[p]]) if (m && m.trim() && !list.includes(m)) list.push(m);
    return list;
  }
  // The rewrite's own provider/model and thinking level from the 🔊 Read Aloud settings (server-side, shared by phone and
  // desktop), each independently else Refine's (null = both Refine's). Another provider uses Refine's key for it in this
  // browser; history stays Refine's.
  async function rewriteChoice() {
    try { const got = await api('GET', '/api/read-aloud/settings'); return got && got.rewrite && (got.rewrite.provider || got.rewrite.thinking) ? got.rewrite : null; }
    catch (_) { return null; }
  }
  function withChoice(s, choice) {
    if (!choice) return s;
    let out = s;
    if (Object.hasOwn(DEFAULT_MODELS, choice.provider || '')) {
      const p = choice.provider, model = choice.model || refineModels(p)[0];
      if (p !== s.provider || model !== s.model)
        out = { ...out, provider: p, model, apiKey: (setting('refine_' + p + '_api_key', '') || '').trim(), outputTokens: null, readAloudModel: true };
    }
    // The level is Refine's own scale; refine-core maps it per provider exactly as it does for Refine.
    if (opcRefineCore.THINK_LEVELS.includes(choice.thinking)) out = { ...out, thinking: choice.thinking, readAloudThinking: true };
    return out;
  }

  // @beacon[
  //   id=opc-pane@readAloudRequest,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The one Read Aloud request assembly: the read-aloud prompt parts (saved overrides or defaults) through opcRefineCore.buildRequest in Refine's anti-injection order, chat history before the clicked message with its UTF-16 block boundaries (cached like Refine's), the message (or one pass's part of it) as the target, no dictionary; a later pass adds the message spoken so far and an earlier one the rest still to rewrite, in their own fixed wrappers around the target (Refine's draft-before/after slots).,
  // ]
  function request({ text, speaker, history = '', historyBoundaries = [], spokenBefore = '', messageAfter = '' }, partSet = parts()) {
    return opcRefineCore.buildRequest({ transcription: text, history, historyBoundaries: history.trim() ? historyBoundaries : [], dictionaryTerms: [],
      draftBefore: spokenBefore, draftAfter: messageAfter,
      wrappers: { before: [READ_ALOUD_SPOKEN_BEFORE_PREAMBLE, '{{spoken_before}}'], after: [READ_ALOUD_MESSAGE_AFTER_PREAMBLE, '{{message_after}}'] },
      parts: { system: partSet.read_aloud_system, context: partSet.read_aloud_context, finalfence: partSet.read_aloud_finalfence,
        transcription: substitute(partSet.read_aloud_message, { '{{speaker}}': speaker || 'the speaker', '{{message}}': '{{transcription}}' }) } });
  }

  // Where an over-long unit may be cut so its first piece fits `room`: before a top-level list item, else (plain prose)
  // after a sentence end; never inside a table, a fenced code block or a block quote. The last cut that fits, else the
  // first one past it (a piece that is too long beats none), else -1.
  function cutAt(unit, room) {
    if (/^ {0,3}(?:\||`{3,}|~{3,}|>)/m.test(unit)) return -1;
    const cuts = [], items = [...unit.matchAll(/\n( {0,3})(?:[-*+]|\d{1,9}[.)])\s/g)], first = unit.match(/^( {0,3})(?:[-*+]|\d{1,9}[.)])\s/);
    // Only before an item of the outermost list: a nested item is still inside its parent item.
    const top = Math.min(first ? first[1].length : Infinity, ...items.map(m => m[1].length));
    for (const m of items) if (m[1].length === top) cuts.push(m.index + 1);
    if (!items.length && !first) {
      for (const m of unit.matchAll(/[.!?…]['"”’)\]]*\s+/g)) {
        if (!/(?:^|\s)(?:e\.g|i\.e|vs|etc|cf|Mr|Mrs|Ms|Dr|St|No)\.$/i.test(unit.slice(0, m.index + 1))) cuts.push(m.index + m[0].length);
      }
    }
    // Never right after a heading or a line that introduces what follows (as between units).
    const leads = c => { const before = unit.slice(0, c).trimEnd(); const line = before.slice(before.lastIndexOf('\n') + 1); return /^ {0,3}#{1,6}\s/.test(line) || /:\s*$/.test(line); };
    const inside = cuts.filter(c => c > 0 && c < unit.trimEnd().length && !leads(c));
    const fits = inside.filter(c => c <= room);
    return fits.length ? fits[fits.length - 1] : inside.length ? inside[0] : -1;
  }

  // @beacon[
  //   id=opc-pane@readAloudPasses,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Splits a long message into its rewrite passes between Markdown blocks: a short first pass (about 700 characters) so the voice starts soon, then about 2,000 and doubling up to 6,000; a fenced code block, a table and a list item stay whole (an indented continuation stays with its item), a heading or a line ending in a colon stays with what follows, an over-long unit is cut only between list items or at a sentence end, and a short tail joins the pass before it; a message up to 1,200 characters is one pass, the text unchanged.,
  // ]
  function splitPasses(text, plan = PASSES) {
    const source = String(text || '');
    if (source.trim().length <= plan.single) return [source];
    const blocks = [];
    let lines = [], fence = null;
    const flush = () => { if (lines.length) blocks.push(lines.join('\n')); lines = []; };
    for (const line of source.replace(/\r\n?/g, '\n').split('\n')) {
      if (fence) {
        lines.push(line);
        const close = line.match(/^ {0,3}(`{3,}|~{3,})\s*$/);
        if (close && close[1][0] === fence[0] && close[1].length >= fence.length) fence = null;
        continue;
      }
      const open = line.match(/^ {0,3}(`{3,}|~{3,})/);
      if (open) { fence = open[1]; lines.push(line); continue; } // a fenced block stays whole, blank lines and all
      if (line.trim()) lines.push(line); else flush();
    }
    flush();
    const units = [];
    let glue = false;
    for (const block of blocks) {
      if (units.length && (glue || /^(?: {2,}|\t)\S/.test(block))) units[units.length - 1] += '\n\n' + block;
      else units.push(block);
      const last = block.split('\n').pop();
      glue = /^ {0,3}#{1,6}\s/.test(last) || /:\s*$/.test(last); // a heading or an introduction never ends a pass
    }
    // seams[k]: the source's own whitespace between pass k and pass k+1 (a blank line, or the space of a cut).
    const passes = [], seams = [], queue = units.slice();
    let current = '';
    const target = () => passes.length ? Math.min(plan.max, plan.second * 2 ** (passes.length - 1)) : plan.first;
    while (queue.length) {
      const unit = queue.shift(), size = target(), room = size - (current ? current.length + 2 : 0);
      if (unit.length <= room) { current = current ? current + '\n\n' + unit : unit; continue; }
      if (current && current.length >= size / 2) { passes.push(current); seams.push('\n\n'); current = ''; queue.unshift(unit); continue; }
      const cut = cutAt(unit, room), head = cut > 0 ? unit.slice(0, cut).trimEnd() : unit;
      if (cut > 0) queue.unshift(unit.slice(cut).trimStart());
      passes.push((current ? current + '\n\n' : '') + head);
      seams.push(cut > 0 ? unit.slice(head.length, unit.length - queue[0].length) : '\n\n');
      current = '';
    }
    // A tail under a quarter of its pass's size is not worth a call of its own: it joins the pass before it.
    if (current) passes.push(current); else seams.pop();
    if (passes.length > 1 && passes[passes.length - 1].length < Math.min(plan.max, plan.second * 2 ** (passes.length - 2)) / 4)
      passes.push(passes.splice(-2, 2).join(seams.pop()));
    return passes.length > 1 ? passes : [source];
  }

  function itemKey(spec) { return { backend: spec.backend, conversation: spec.conversation, message: spec.message, part: spec.part || 'text' }; }
  function keyString(spec) { return JSON.stringify(Object.values(itemKey(spec))); }
  async function sha256(text) {
    if (!globalThis.crypto?.subtle) throw plain('This page is not a secure context, so the message cannot be fingerprinted; open the pane at 127.0.0.1 or its https address.');
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
  }
  async function api(method, path, body, signal) {
    const response = await fetch(path, { method, cache: 'no-store', signal,
      ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
    const data = await response.json().catch(() => null);
    if (!response.ok) throw plain((data && data.error) || 'HTTP ' + response.status, { httpCode: response.status, reason: data && data.status });
    return data;
  }
  // The desktop's PaneActivity lease (app.js paneActivity): a pane-server restart waits while a rewrite runs.
  function lease() {
    const beacon = globalThis.navigator?.sendBeacon?.bind(globalThis.navigator);
    if (typeof beacon !== 'function' || typeof Blob !== 'function') return () => {};
    const id = globalThis.crypto?.randomUUID?.() || 'read-aloud-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    const post = active => { try { beacon('/api/pane-activity', new Blob([JSON.stringify({ schemaVersion: 1, id, kind: 'read-aloud', active })], { type: 'text/plain' })); } catch (_) {} };
    post(true);
    const renew = setInterval(() => post(true), 60000);
    return () => { clearInterval(renew); post(false); };
  }
  // The passes' measured cost and cache counts, added up for the saved reading (a pass without a price leaves it unpriced).
  function tally(sums, result) {
    if (typeof result.cost === 'number') sums.cost += result.cost; else sums.priced = false;
    sums.estimated = sums.estimated || !!result.estimated;
    if (result.cache) sums.cached = true;
    for (const k of ['read', 'write']) if (typeof result.cache?.[k] === 'number') sums[k] = (sums[k] || 0) + result.cache[k];
  }
  // One multi-pass rewrite's id: the server appends a pass only to the run that started the reading.
  function runId() { return globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12); }
  // A model may echo the active section at a pass seam despite the prompt. Drop only an identical leading active
  // heading chain, never prose, a new topic, or a later return to an earlier topic. The saved prefix is immutable.
  function continueHeadings(text, spokenBefore) {
    if (!spokenBefore) return text;
    const heading = /^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/;
    const active = new Map(), label = s => s.trim().replace(/\s+/g, ' ').toLocaleLowerCase();
    for (const line of spokenBefore.split('\n')) {
      const m = line.match(heading);
      if (!m) continue;
      for (const level of active.keys()) if (level >= m[1].length) active.delete(level);
      active.set(m[1].length, label(m[2]));
    }
    const lines = text.split('\n');
    let from = 0;
    while (from < lines.length) {
      if (!lines[from].trim()) { from++; continue; }
      const m = lines[from].match(heading);
      if (!m || active.get(m[1].length) !== label(m[2])) break;
      from++;
    }
    return lines.slice(from).join('\n').trimStart();
  }
  // kept: earlier passes are already saved (and playing), so "nothing was saved" would be untrue.
  function providerError(e, s, kept = false) {
    const lost = kept ? 'the rest of the message was not rewritten' : 'nothing was saved';
    if (e && (e.userCanceled || e.name === 'AbortError')) return 'Cancelled · ' + lost + '.';
    if (e && e.wasAbort) return 'The rewrite ran past its time limit and was stopped; ' + lost + '. Try again, or lower ' +
      (s.readAloudThinking ? 'the rewrite thinking in 🔊 Read Aloud settings.' : 'Refine\'s thinking level.');
    if (e && (e.status === 401 || e.status === 403)) return (PROVIDER_LABEL[s.provider] || s.provider) + ' rejected the Refine API key (HTTP ' + e.status + '). Check it in ⚙ Refine settings.';
    if (e && e.status === 429) return (PROVIDER_LABEL[s.provider] || s.provider) + ' is rate-limiting (HTTP 429) even after retries; try again in a minute.';
    return 'The rewrite failed: ' + ((e && e.message) || String(e));
  }

  // @beacon[
  //   id=opc-pane@readAloudRewrite,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Listening rewrite job outlives its player: prompt parts frozen for the run; unchanged short-first passes see cached history plus all earlier rewritten text/headings and the raw rest. An echoed active heading chain at a seam is removed without touching prose or the saved prefix. Each pass saves/publishes immediately while the next rewrites; provider/model/thinking overrides and activity lease remain Refine's. Cancel/failure keeps saved passes and a reopen attaches to the job.,
  // ]
  function rewrite(spec, { noContext = false } = {}) {
    const key = keyString(spec);
    if (jobs.has(key)) return jobs.get(key);
    // reply: the latest saved state (each pass is saved as it lands); replies: the open players to hand it to.
    const job = { key, phase: 'start', text: 'Starting…', controller: new AbortController(), listeners: new Set(), replies: new Set(), reply: null };
    const progress = (phase, text) => { job.phase = phase; job.text = text; for (const f of job.listeners) { try { f(phase, text); } catch (_) {} } };
    const publish = reply => { job.reply = reply; for (const f of job.replies) { try { f(reply); } catch (_) {} } };
    job.promise = (async () => {
      const release = lease(), started = Date.now();
      try {
        const s = withChoice(settingsFor(spec), await rewriteChoice());
        if (!s.apiKey && s.readAloudModel) throw plain('No ' + (PROVIDER_LABEL[s.provider] || s.provider) + ' API key in this browser for Read Aloud\'s rewrite model (' + s.model + '): add that provider\'s key in ⚙ Refine settings (the editor) or Refine setup (the phone), or set 🔊 Read Aloud back to Refine\'s model.');
        if (!s.apiKey) throw plain('No Refine API key in this browser. Read Aloud rewrites with Refine\'s provider: add the key in ⚙ Refine settings (the editor) or Refine setup (the phone), then try again.');
        let ctx = null, contextNote = noContext ? 'without the conversation (your choice)' : spec.contextQuery ? '' : (spec.contextNote || 'no conversation context for this view');
        if (!noContext && spec.contextQuery) {
          progress('context', 'Reading the conversation before this message…');
          const q = new URLSearchParams({ ...spec.contextQuery, before: spec.message, historyMode: s.historyMode });
          if (s.historyMode === 'budgeted') q.set('budgetBytes', String(Math.max(5, Math.min(400, Number(s.budgetKb) || 50)) * 1024));
          try { ctx = await api('GET', '/api/read-aloud/context?' + q, null, job.controller.signal); }
          catch (e) {
            if (job.controller.signal.aborted) throw e;
            throw plain('Could not read the conversation before this message: ' + e.message, { contextFailed: true });
          }
        }
        const passes = splitPasses(spec.text), n = passes.length, promptParts = parts(), where = ctx ? kb(ctx.bytes) + ' of conversation before it' : contextNote;
        const sourceHash = await sha256(spec.text), run = n > 1 ? runId() : null, spoken = [];
        const sums = { cost: 0, priced: true, estimated: false, cached: false, read: null, write: null };
        let reply = null, firstMs = null;
        for (let k = 0; k < n; k++) {
          const label = n > 1 ? 'Pass ' + (k + 1) + ' of ' + n + ': ' : '';
          progress('rewrite', 'Rewriting for listening · ' + (n > 1 ? 'pass ' + (k + 1) + ' of ' + n + ' · ' : '') + s.model + ' · thinking ' + s.thinking + ' · ' + where);
          const req = request({ text: passes[k], speaker: spec.speaker, history: ctx ? ctx.text : '', historyBoundaries: ctx ? ctx.historyBoundaries : [],
            spokenBefore: spoken.join('\n\n'), messageAfter: passes.slice(k + 1).join('\n\n') }, promptParts);
          const deadline = Date.now() + ({ xhigh: 240000, max: 360000 }[s.thinking] || 120000) + 60000;
          let result;
          try {
            result = await opcRefineCore.callWithRetry({ provider: s.provider, model: s.model, apiKey: s.apiKey, thinking: s.thinking,
              ...(Number.isInteger(s.outputTokens) ? { outputTokens: s.outputTokens } : {}),
              system: req.system, user: req.user, anthropicUser: req.anthropicUser, routerCache: true,
              cacheKey: 'read-aloud:' + spec.backend + ':' + spec.conversation, signal: job.controller.signal, deadline: () => deadline });
          } catch (e) { throw plain(label + providerError(e, s, !!reply), { cancelled: !!(e && e.userCanceled) }); }
          const text = continueHeadings(String(result.text || '').replace(/\s+$/, ''), spoken.join('\n\n'));
          if (!text.trim()) throw plain(label + 'The rewrite came back empty; ' + (reply ? 'the rest of the message was not rewritten.' : 'nothing was saved.'));
          spoken.push(n > 1 ? text.trim() : text);
          tally(sums, result);
          if (k === n - 1) progress('save', 'Saving the spoken version…');
          const meta = { provider: s.provider, model: s.model, thinking: s.thinking, ms: Date.now() - started,
            ...(n > 1 ? { passes: n, firstMs: firstMs === null ? Date.now() - started : firstMs } : {}),
            contextTurns: ctx ? ctx.turns : 0, contextBytes: ctx ? ctx.bytes : 0, contextNote: ctx ? '' : contextNote,
            cost: sums.priced ? sums.cost : null, estimated: sums.estimated, cache: sums.cached ? { read: sums.read, write: sums.write } : null,
            speaker: spec.speaker || null, title: spec.title || null, sourceChars: spec.text.length };
          reply = await api('POST', '/api/read-aloud/item', { schemaVersion: 1, key: itemKey(spec), sourceHash, text: spoken[k], meta,
            ...(n > 1 ? { run, segment: k, done: k === n - 1 } : {}) });
          if (firstMs === null) firstMs = Date.now() - started;
          publish(reply);
        }
        return reply;
      } catch (e) {
        if (job.controller.signal.aborted) throw plain(job.reply ? 'Cancelled · the rest of the message was not rewritten.' : 'Cancelled · nothing was saved.', { cancelled: true });
        throw e;
      } finally { release(); jobs.delete(key); }
    })();
    jobs.set(key, job);
    return job;
  }

  function metadata(audio) {
    return new Promise((resolve, reject) => {
      if (audio.readyState >= 1) return resolve();
      const off = () => { audio.removeEventListener('loadedmetadata', ok); audio.removeEventListener('error', bad); };
      const ok = () => { off(); resolve(); }, bad = () => { off(); reject(plain('The browser could not play this part of the audio.')); };
      audio.addEventListener('loadedmetadata', ok); audio.addEventListener('error', bad);
    });
  }

  // @beacon[
  //   id=opc-pane@readAloudPlayer,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=One audio element over stable saved parts (legacy or paragraph-sized): a block tap pauses the old audio and requests that exact part immediately, bypassing intervening synthesis; latest intent wins late replies/errors. The next part alone prefetches while playing. Pass growth appends without moving blocks; end-of-ready holds then resumes. Whole-reading seek, cumulative cross-part 10s skips, pending pause, speed and replay remain shared by phone and desktop.,
  // ]
  function createPlayer(view, item) {
    const audio = view.audio, count = () => item.chunks.length;
    // intent: whether the switch in progress (switching) will play; a skip or seek made during it keeps that intent.
    // more: the rewrite is still adding parts; held: playback reached the end of what is ready ({play}: carry on by itself).
    const P = { index: 0, offset: 0, playing: false, ended: false, switching: false, intent: false, ticket: 0, destroyed: false, loaded: new Map(), pending: new Map(),
      more: item.complete === false, held: null };
    const of = i => 'part ' + (i + 1) + ' of ' + count() + (P.more ? '+' : '');
    const known = i => (P.loaded.get(i) && P.loaded.get(i).seconds) || item.chunks[i].seconds || null;
    function cps() {
      let chars = 0, seconds = 0;
      for (let i = 0; i < count(); i++) { const k = known(i); if (k) { chars += item.chunks[i].chars; seconds += k; } }
      return seconds > 0 ? chars / seconds : DEFAULT_CPS;
    }
    const length = i => known(i) || item.chunks[i].chars / cps();
    const startOf = i => { let t = 0; for (let j = 0; j < i; j++) t += length(j); return t; };
    const total = () => startOf(count());
    const position = () => startOf(P.index) + (P.switching ? P.offset : (audio.currentTime || 0));
    async function load(i) {
      if (P.loaded.has(i)) return P.loaded.get(i);
      if (P.pending.has(i)) return P.pending.get(i);
      const promise = (async () => {
        const response = await fetch('/api/read-aloud/audio', { method: 'POST', cache: 'no-store', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ schemaVersion: 1, id: item.id, chunk: i }) });
        if (!response.ok) { const d = await response.json().catch(() => null); throw plain((d && d.error) || 'audio HTTP ' + response.status, { reason: d && d.status }); }
        const blob = await response.blob(), seconds = Number(response.headers.get('X-Read-Aloud-Seconds')) || null;
        if (P.destroyed) throw plain('closed');
        const entry = { url: URL.createObjectURL(blob), seconds };
        P.loaded.set(i, entry); item.chunks[i].cached = true;
        return entry;
      })();
      P.pending.set(i, promise);
      try { return await promise; } finally { P.pending.delete(i); }
    }
    async function playFrom(i, offset = 0, play = true, interrupt = false) {
      const ticket = ++P.ticket;
      P.index = i; P.offset = offset; P.ended = false; P.switching = true; P.intent = play; P.held = null;
      if (interrupt) { audio.pause(); P.playing = false; }
      view.highlight(i); view.paint();
      try {
        let entry = P.loaded.get(i);
        if (!entry) {
          view.status((item.chunks[i].cached ? 'Loading ' : 'Synthesizing ') + of(i) + (item.chunks[i].cached ? '…' : ' with ElevenLabs…'));
          entry = await load(i);
        }
        if (ticket !== P.ticket || P.destroyed) return;
        play = P.intent;
        // A target past this part's real end (a skip or seek made from estimated lengths) carries into the next part.
        const past = () => entry.seconds && offset >= entry.seconds;
        if (past() && i + 1 < count()) return playFrom(i + 1, offset - entry.seconds, play);
        if (audio.src !== entry.url) { audio.src = entry.url; await metadata(audio); }
        else if (audio.readyState < 1 && !audio.error) await metadata(audio); // an earlier, superseded switch set this src
        if (ticket !== P.ticket || P.destroyed) return;
        play = P.intent;
        if (Number.isFinite(audio.duration) && audio.duration > 0) entry.seconds = audio.duration;
        if (past()) {
          if (i + 1 < count()) return playFrom(i + 1, offset - entry.seconds, play);
          // Past the very end: the end of playback, as if it had played out (or, while more is coming, the hold).
          audio.pause();
          try { audio.currentTime = entry.seconds; } catch (_) {}
          P.switching = false; P.playing = false;
          return P.more ? hold(play) : finished();
        }
        try { audio.currentTime = Math.max(0, Math.min(offset, (entry.seconds || offset) - 0.05)); } catch (_) {}
        audio.defaultPlaybackRate = audio.playbackRate = view.rate; // a new src resets playbackRate to the default (Safari)
        P.switching = false;
        if (play) {
          try {
            await audio.play();
            if (ticket !== P.ticket || P.destroyed) return;
            P.playing = !audio.paused; view.ready(P.playing ? 'Playing ' + of(i) : 'Paused · ' + of(i));
          } catch (_) {
            if (ticket !== P.ticket || P.destroyed) return;
            P.playing = false; view.ready('Ready · press ▶ to play');
          }
        } else { P.playing = false; view.ready('Paused · ' + of(i)); }
        view.paint();
        if (P.playing && i + 1 < count()) load(i + 1).catch(() => {}); // paused taps don't spend on the next part
      } catch (e) {
        if (ticket !== P.ticket || P.destroyed) return; // an old request's failure cannot replace a newer tap's status
        audio.pause(); P.switching = false;
        throw e;
      }
    }
    function finished() { P.held = null; P.playing = false; P.ended = true; view.paint(); view.status('Finished · ⏮ or ▶ to hear it again'); }
    // The end of what is rewritten so far while more is coming: wait there, and carry on by itself if it was playing.
    function hold(play) {
      P.held = { play }; P.playing = false; P.ended = false;
      view.paint(); view.status(play ? 'Waiting for the next part of the rewrite…' : 'Paused · the rest is still being rewritten');
    }
    function run(promise) { promise.catch(e => { if (!P.destroyed) { P.playing = false; view.fail(e); } }); }
    // A switch in progress (a skip waiting for its part's synthesis) owns what plays next, not the old part's end.
    const wants = () => (P.switching ? P.intent : P.held ? P.held.play : P.playing || P.ended || !audio.src);
    audio.onended = () => {
      if (P.destroyed || P.switching) return;
      if (P.index + 1 < count()) run(playFrom(P.index + 1));
      else if (P.more) hold(true);
      else finished();
    };
    audio.onpause = () => { if (!P.switching && !audio.ended) { P.playing = false; view.paint(); } };
    audio.onplay = () => { P.playing = true; view.paint(); };
    const player = {
      get playing() { return P.switching ? P.intent : P.playing || !!(P.held && P.held.play); }, get index() { return P.index; }, get chunks() { return count(); },
      get more() { return P.more; },
      total, position, startOf,
      start() { run(playFrom(0)); },
      // The rewrite saved another pass: its parts join the end; a hold carries on into them, else the next part is
      // prefetched at once if it is the one just added.
      grow(next) {
        const before = count();
        for (let i = before; i < next.chunks.length; i++) item.chunks.push(next.chunks[i]);
        item.text = next.text; P.more = next.complete === false;
        if (P.destroyed) return;
        if (P.held) {
          const play = P.held.play;
          if (count() > before) run(playFrom(before, 0, play)); else if (!P.more) finished();
        } else if (count() > before && P.index + 1 === before && P.playing && !P.switching) load(before).catch(() => {});
        view.paint();
      },
      // No more parts are coming (the rewrite finished, failed or was cancelled): a hold becomes the end.
      finish() { P.more = false; if (P.held) finished(); view.paint(); },
      toggle() {
        if (P.held) {
          P.held.play = !P.held.play;
          view.status(P.held.play ? 'Waiting for the next part of the rewrite…' : 'Paused · the rest is still being rewritten'); view.paint(); return;
        }
        if (P.switching) {
          P.intent = !P.intent;
          if (!P.intent) { audio.pause(); P.playing = false; }
          view.status((P.intent ? 'Waiting to play · ' : 'Paused · preparing ') + of(P.index)); view.paint(); return;
        }
        if (P.playing) { audio.pause(); P.playing = false; view.status('Paused · ' + of(P.index)); view.paint(); return; }
        if (P.ended) return run(playFrom(0));
        if (!audio.src || audio.src === SILENT_WAV || P.loaded.get(P.index)?.url !== audio.src) return run(playFrom(P.index, P.offset));
        audio.defaultPlaybackRate = audio.playbackRate = view.rate;
        const ticket = P.ticket;
        audio.play().then(() => {
          if (ticket !== P.ticket || P.destroyed) return;
          P.playing = !audio.paused; view.status((P.playing ? 'Playing ' : 'Paused · ') + of(P.index)); view.paint();
          if (P.playing && P.index + 1 < count()) load(P.index + 1).catch(() => {});
        }, e => { if (ticket === P.ticket && !P.destroyed) view.fail(e); });
      },
      restart() { run(playFrom(0, 0, true)); },
      seek(seconds) {
        let i = 0;
        while (i < count() - 1 && seconds >= startOf(i + 1)) i++;
        run(playFrom(i, Math.max(0, seconds - startOf(i)), wants()));
      },
      // −10s / +10s and ←/→: from where playback is, or the target of the switch still in progress (so taps add up),
      // across part boundaries by the parts' known lengths. Forward stops at the first part whose length is not known
      // yet (playFrom carries an overshoot on once it is); back stops at 0 and forward past the end finishes (or holds
      // while more is coming).
      skip(delta) {
        let i = P.index, at = (P.switching ? P.offset : audio.currentTime || 0) + delta;
        while (at < 0 && i > 0) { i--; at += length(i); }
        while (i + 1 < count() && known(i) && at >= known(i)) { at -= known(i); i++; }
        run(playFrom(i, Math.max(0, at), wants()));
      },
      jump(i) { run(playFrom(Math.max(0, Math.min(count() - 1, i)), 0, true, true)); },
      rate(r) { audio.defaultPlaybackRate = audio.playbackRate = r; },
      destroy() {
        P.destroyed = true; P.ticket++;
        audio.onended = audio.onpause = audio.onplay = null;
        try { audio.pause(); audio.removeAttribute('src'); audio.load(); } catch (_) {}
        for (const entry of P.loaded.values()) { try { URL.revokeObjectURL(entry.url); } catch (_) {} }
        P.loaded.clear();
      },
    };
    return player;
  }

  function element(doc, tag, text, cls, attrs = {}) {
    const node = doc.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = text;
    if (cls) node.className = cls;
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }
  function savedRate(config) {
    const stored = Number(setting(RATE_KEY));
    if (Number.isFinite(stored) && stored >= 0.5 && stored <= 3) return stored;
    const configured = Number(config && config.playbackRate);
    return Number.isFinite(configured) && configured >= 0.5 && configured <= 3 ? configured : 1.35;
  }
  function describe(item, config) {
    const m = item.meta || {}, bits = [];
    if (m.model) bits.push('rewritten by ' + m.model + (m.thinking ? ' · ' + m.thinking : '') + (m.passes > 1 ? ' in ' + m.passes + ' passes' : ''));
    if (item.complete === false) bits.push('still being rewritten');
    if (m.contextTurns) bits.push(m.contextTurns + ' turns (' + kb(m.contextBytes || 0) + ') of context');
    else if (m.contextNote) bits.push(m.contextNote);
    if (typeof m.cost === 'number') bits.push((m.estimated ? '~$' : '$') + (m.cost < 0.01 ? m.cost.toFixed(4) : m.cost.toFixed(3)));
    if (m.cache && typeof opcRefineCore.cacheSummary === 'function') { const c = opcRefineCore.cacheSummary(m.cache); if (c) bits.push(c); }
    const voice = (item.voice && (item.voice.voiceName || item.voice.voiceId)) || (config && config.configured && (config.voiceName || config.voiceId));
    if (voice) bits.push('voice ' + voice);
    bits.push(item.chunks.length + (item.chunks.length === 1 ? ' part' : ' parts') + ' · ' + item.text.length.toLocaleString('en-US') + ' characters');
    if (item.createdAt) bits.push('saved ' + new Date(item.createdAt * 1000).toLocaleString());
    return bits.join(' · ');
  }

  // @beacon[
  //   id=opc-pane@readAloudSettingsPanel,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Shared 🔊 settings on phone/desktop/pop-over: independent rewrite model/thinking via server settings, masked ElevenLabs key/Test, lazy voice/model catalog, and an explicit browser-local Restore Read Aloud prompt defaults (only its four overrides; next Regenerate uses headings). Reads server only when opened; account calls only on Test/Load voices; never changes Refine prompts.,
  // ]
  function settingsPanel(host, { doc = host.ownerDocument || document, open: startOpen = false } = {}) {
    const win = doc.defaultView || globalThis;
    const box = element(doc, 'details', null, 'ra-settings');
    const body = element(doc, 'div', null, 'ra-settings-body');
    box.append(element(doc, 'summary', '🔊 Read Aloud'), body);
    const button = (text, title) => element(doc, 'button', text, 'ra-set-btn', { type: 'button', title });
    const line = (...nodes) => { const d = element(doc, 'div', null, 'ra-set-line'); d.append(...nodes); return d; };
    const label = text => element(doc, 'label', text, 'ra-set-label');
    const rewriteSel = element(doc, 'select', null, 'ra-set-rewrite', { 'aria-label': 'Read Aloud rewrite model' });
    const thinkSel = element(doc, 'select', null, 'ra-set-thinking', { 'aria-label': 'Read Aloud rewrite thinking level' });
    const keyNow = element(doc, 'span', '…', 'ra-set-keynow');
    const keyIn = element(doc, 'input', null, 'ra-set-key', { type: 'password', autocomplete: 'off', autocapitalize: 'none', spellcheck: 'false',
      placeholder: 'Paste a key to set or replace it', 'aria-label': 'ElevenLabs API key' });
    const keySave = button('Save key', 'Save this key on the pane server (elevenlabs.json, owner-only); it never comes back to the browser');
    const keyTest = button('Test', 'One cheap ElevenLabs call (the subscription read; no characters billed)');
    const voiceSel = element(doc, 'select', null, 'ra-set-voice', { 'aria-label': 'ElevenLabs voice' });
    const voiceLoad = button('↻ Load voices', 'Read the voices (and TTS models) of your ElevenLabs account now');
    const modelSel = element(doc, 'select', null, 'ra-set-model', { 'aria-label': 'ElevenLabs model' });
    const promptReset = button('Restore Read Aloud prompt defaults', 'Remove only this browser’s four Read aloud prompt overrides; Refine prompts and saved readings stay unchanged');
    const status = element(doc, 'div', '', 'ra-set-status', { role: 'status' });
    body.append(
      label('Rewrite model'), line(rewriteSel),
      label('Rewrite thinking'), line(thinkSel),
      element(doc, 'p', 'Both Same as Refine by default, each chosen on its own. Another model uses that provider\'s Refine API key in this browser; the thinking level is Refine\'s scale, applied to whichever model rewrites; history stays Refine\'s.', 'ra-set-hint'),
      label('ElevenLabs API key'), line(keyNow), line(keyIn, keySave, keyTest),
      label('Voice'), line(voiceSel, voiceLoad),
      label('Speech model'), line(modelSel),
      element(doc, 'p', 'Voice and speech model apply to readings made from now on; ↻ Regenerate re-reads a saved one. Kept on the pane server, the same on the phone and the desktop.', 'ra-set-hint'),
      label('Listening prompt'), line(promptReset),
      element(doc, 'p', 'New readings use section headings. A saved prompt override can keep the older plain-text rewrite; restoring these defaults affects this browser only, not Refine or saved audio.', 'ra-set-hint'),
      status);
    let view = null, busy = false;
    const say = (text, kind = '') => { status.textContent = text; status.dataset.kind = kind; };
    const option = (value, text) => { const o = element(doc, 'option', text); o.value = value; return o; };
    function paint() {
      const v = view; if (!v) return;
      const groups = [option('', 'Same as Refine')];
      const chosen = v.rewrite && v.rewrite.provider ? v.rewrite.provider + '|' + (v.rewrite.model || refineModels(v.rewrite.provider)[0] || '') : '';
      for (const p of Object.keys(DEFAULT_MODELS)) {
        const g = element(doc, 'optgroup', null, null, { label: PROVIDER_LABEL[p] });
        const models = refineModels(p);
        if (v.rewrite && v.rewrite.provider === p && v.rewrite.model && !models.includes(v.rewrite.model)) models.push(v.rewrite.model);
        for (const m of models) g.append(option(p + '|' + m, m));
        g.append(option(p + '|', 'Other ' + PROVIDER_LABEL[p] + ' model id…'));
        groups.push(g);
      }
      rewriteSel.replaceChildren(...groups); rewriteSel.value = chosen;
      thinkSel.replaceChildren(option('', 'Same as Refine'), ...opcRefineCore.THINK_LEVELS.map(level => option(level, level)));
      thinkSel.value = v.rewrite && opcRefineCore.THINK_LEVELS.includes(v.rewrite.thinking) ? v.rewrite.thinking : '';
      keyNow.textContent = v.key && v.key.set ? 'Saved · ' + v.key.masked : 'Not set yet';
      const voices = (v.voices || []).slice();
      if (!voices.some(x => x.id === v.voiceId)) voices.unshift({ id: v.voiceId, name: v.voiceName || v.voiceId });
      voiceSel.replaceChildren(...voices.map(x => option(x.id, x.name + (x.category ? ' · ' + x.category : ''))));
      voiceSel.value = v.voiceId;
      const models = (v.models || []).slice();
      if (!models.some(x => x.id === v.modelId)) models.unshift({ id: v.modelId, name: v.modelId });
      modelSel.replaceChildren(...models.map(x => option(x.id, x.name + (x.id === v.defaultModelId ? ' (default)' : ''))));
      modelSel.value = v.modelId;
      const keyless = !(v.key && v.key.set);
      for (const c of [rewriteSel, thinkSel, keyIn, keySave]) c.disabled = busy;
      for (const c of [keyTest, voiceLoad, voiceSel, modelSel]) c.disabled = busy || keyless;
    }
    async function call(method, path, payload, done) {
      busy = true; paint();
      try { const got = await api(method, path, payload ? { schemaVersion: 1, ...payload } : (method === 'POST' ? { schemaVersion: 1 } : undefined)); return done(got); }
      catch (e) { say(e.message || String(e), 'error'); return null; }
      finally { busy = false; paint(); }
    }
    const save = (payload, what) => call('POST', '/api/read-aloud/settings', payload, got => { view = got; say(what + ' saved', 'ok'); });
    function load() { return call('GET', '/api/read-aloud/settings', null, got => { view = got; if (!got.configured && got.error) say(got.error); }); }
    rewriteSel.onchange = () => {
      const [p, m] = rewriteSel.value.split('|');
      if (!p) return void save({ rewriteProvider: '' }, 'Rewrite model (Same as Refine)');
      let model = m;
      if (!model) { model = (win.prompt && win.prompt(PROVIDER_LABEL[p] + ' model id for Read Aloud\'s rewrite') || '').trim(); if (!model) return paint(); }
      void save({ rewriteProvider: p, rewriteModel: model }, 'Rewrite model ' + model);
    };
    thinkSel.onchange = () => { void save({ rewriteThinking: thinkSel.value }, 'Rewrite thinking ' + (thinkSel.value || '(Same as Refine)')); };
    keySave.onclick = () => {
      const key = keyIn.value.trim(); if (!key) { say('Paste the key first', 'error'); return; }
      void save({ apiKey: key }, 'ElevenLabs key').then(() => { if (status.dataset.kind === 'ok') keyIn.value = ''; });
    };
    keyTest.onclick = () => { say('Testing the key with ElevenLabs…'); void call('POST', '/api/read-aloud/settings/test', null, got => say(got.message, got.ok ? 'ok' : 'error')); };
    voiceLoad.onclick = () => {
      say('Reading your ElevenLabs voices…');
      void call('POST', '/api/read-aloud/settings/catalog', null, got => { view = got; say((got.voices || []).length + ' voices' + (got.modelsFromAccount ? ' and ' + got.models.length + ' speech models' : '') + ' from your ElevenLabs account', 'ok'); });
    };
    voiceSel.onchange = () => {
      const pick = (view.voices || []).find(x => x.id === voiceSel.value);
      void save({ voiceId: voiceSel.value, voiceName: pick ? pick.name : null }, 'Voice ' + (pick ? pick.name : voiceSel.value));
    };
    modelSel.onchange = () => { void save({ modelId: modelSel.value }, 'Speech model ' + modelSel.value); };
    promptReset.onclick = () => {
      try {
        const storage = store(); if (!storage) throw plain('Browser storage is unavailable.');
        for (const p of READ_ALOUD_PARTS) storage.removeItem(p.storage);
        say('Read Aloud prompt defaults restored in this browser. ↻ Regenerate a reading to use them.', 'ok');
      } catch (e) { say('Could not restore the listening prompt: ' + e.message, 'error'); }
    };
    let loaded = false;
    box.addEventListener('toggle', () => { if (box.open && !loaded) { loaded = true; void load(); } });
    host.append(box);
    if (startOpen) { box.open = true; if (!loaded) { loaded = true; void load(); } }
    return { box, load, get view() { return view; } };
  }
  // Every page that loads this file gets the section wherever it marks a host (m.html's Refine setup, index.html's
  // ⚙ Refine settings); nothing is read until the section is opened.
  function mountSettings(doc = globalThis.document) {
    if (!doc || typeof doc.querySelectorAll !== 'function') return;
    for (const host of doc.querySelectorAll('[data-read-aloud-settings]')) if (!host.firstChild) settingsPanel(host, { doc });
  }
  if (globalThis.document && typeof globalThis.document.addEventListener === 'function') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => mountSettings());
    else mountSettings();
  }

  // @beacon[
  //   id=opc-pane@readAloudOpen,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Shared phone/desktop Read Aloud sheet: opens and unlocks audio inside the tap; saved readings replay unchanged, new pass parts append as soon as saved. Paragraph-sized keyboard/touch targets render real headings safely, ignore scroll/long-press/text selection and don't auto-scroll a reader inspecting blocks. Tap starts that part with immediate synthesis priority; unrevised future text stays labelled until its pass lands. Existing seek/skips/speed/Regenerate/Cancel/Copy remain; closing stops audio but not the rewrite.,
  // ]
  function open(spec) {
    close();
    const doc = spec.doc || document, win = doc.defaultView || globalThis;
    const audio = new (win.Audio || Audio)();
    try { audio.src = SILENT_WAV; const p = audio.play(); if (p && p.then) p.then(() => { if (audio.src === SILENT_WAV) audio.pause(); }, () => {}); } catch (_) {}
    const overlay = element(doc, 'div', null, 'ra-overlay', { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Read aloud' });
    const panel = element(doc, 'div', null, 'ra-panel');
    const head = element(doc, 'div', null, 'ra-head');
    const closeBtn = element(doc, 'button', '✕', 'ra-close', { type: 'button', title: 'Close (Esc) · stops playback', 'aria-label': 'Close' });
    const gear = element(doc, 'button', '⚙', 'ra-gear', { type: 'button', title: 'Read Aloud settings: rewrite model, ElevenLabs key, voice and speech model', 'aria-label': 'Read Aloud settings', 'aria-expanded': 'false' });
    head.append(element(doc, 'strong', '🔊 Read aloud', 'ra-title'), element(doc, 'span', spec.title || '', 'ra-sub'), gear, closeBtn);
    const settingsHost = element(doc, 'div', null, 'ra-settings-host');
    settingsHost.hidden = true;
    const statusRow = element(doc, 'div', null, 'ra-status', { role: 'status' });
    const stateText = element(doc, 'span', 'Starting…', 'ra-state'), elapsed = element(doc, 'span', '0:00', 'ra-elapsed');
    statusRow.append(stateText, elapsed);
    const jobText = element(doc, 'div', '', 'ra-job', { role: 'status' }); // the rest of a long rewrite, while the voice plays
    const controls = element(doc, 'div', null, 'ra-controls');
    const play = element(doc, 'button', '▶', 'ra-play', { type: 'button', title: 'Play / pause (Space)', 'aria-label': 'Play' });
    const restart = element(doc, 'button', '⏮', 'ra-restart', { type: 'button', title: 'Back to the beginning (Home)', 'aria-label': 'Back to the beginning' });
    const back = element(doc, 'button', '−' + SKIP_SECONDS + 's', 'ra-back', { type: 'button', title: 'Back ' + SKIP_SECONDS + ' seconds (←); each tap goes ' + SKIP_SECONDS + ' more', 'aria-label': 'Back ' + SKIP_SECONDS + ' seconds' });
    const forward = element(doc, 'button', '+' + SKIP_SECONDS + 's', 'ra-forward', { type: 'button', title: 'Forward ' + SKIP_SECONDS + ' seconds (→); each tap goes ' + SKIP_SECONDS + ' more', 'aria-label': 'Forward ' + SKIP_SECONDS + ' seconds' });
    const seek = element(doc, 'input', null, 'ra-seek', { type: 'range', min: '0', max: '0', step: '0.1', value: '0', 'aria-label': 'Position' });
    const time = element(doc, 'span', '0:00 / 0:00', 'ra-time');
    const rate = element(doc, 'select', null, 'ra-rate', { 'aria-label': 'Speed', title: 'Playback speed (kept in this browser)' });
    controls.append(restart, back, play, forward, seek, time, rate);
    const textBox = element(doc, 'div', null, 'ra-text', { tabindex: '0' });
    const placeholder = 'Blocks become tappable as each rewrite pass arrives. Tap a block to listen from there.';
    textBox.append(element(doc, 'p', placeholder, 'ra-placeholder'));
    const foot = element(doc, 'div', null, 'ra-foot');
    const regen = element(doc, 'button', '↻ Regenerate', 'ra-regenerate', { type: 'button', title: 'Rewrite this turn again and make new audio (replaces the saved reading)' });
    const cancel = element(doc, 'button', 'Cancel', 'ra-cancel', { type: 'button', title: 'Stop the rewrite (what is already rewritten stays in this player)' });
    const bare = element(doc, 'button', 'Read without the conversation', 'ra-bare', { type: 'button', title: 'Rewrite this turn without the conversation before it' });
    const copy = element(doc, 'button', 'Copy text', 'ra-copy', { type: 'button', title: 'Copy the spoken version' });
    const info = element(doc, 'div', null, 'ra-info');
    foot.append(regen, cancel, bare, copy);
    panel.append(head, settingsHost, statusRow, jobText, controls, textBox, foot, info);
    overlay.append(panel);
    for (const b of [play, restart, back, forward, seek, regen, copy]) b.disabled = true; // the speed stays live: a choice made early applies when playback starts
    cancel.hidden = true; bare.hidden = true;

    const view = { spec, doc, audio, overlay, item: null, config: null, player: null, generation: 0, startedAt: Date.now(), readyAt: null,
      rate: 1.35, dragging: false, job: null, detach: null, closed: false };
    let textTouched = false;
    const selection = () => !!(win.getSelection && String(win.getSelection()));
    // A deliberate browse/selection owns this scroller until a block tap or restart, not playback's part changes.
    for (const type of ['pointerdown', 'touchstart', 'wheel']) textBox.addEventListener(type, () => { textTouched = true; }, { passive: true });
    const setState = (text, kind = '') => { stateText.textContent = text; statusRow.dataset.kind = kind; };
    const jobLine = (text, kind = '') => { jobText.textContent = text; jobText.dataset.kind = kind; };
    view.status = text => { if (!view.closed) setState(text); };
    view.ready = text => { if (view.readyAt === null) view.readyAt = Date.now(); setState(text || 'Ready', 'ok'); paintElapsed(); };
    view.fail = e => {
      if (view.closed) return;
      if (view.readyAt === null) view.readyAt = Date.now();
      setState((e && e.message) || String(e), 'error'); paintElapsed();
      regen.disabled = false; cancel.hidden = true; view.paint();
    };
    function paintElapsed() { elapsed.textContent = (view.readyAt === null ? '' : 'after ') + clock(((view.readyAt || Date.now()) - view.startedAt) / 1000); }
    view.highlight = i => {
      for (const p of textBox.querySelectorAll('.ra-chunk')) {
        const on = Number(p.dataset.index) === i;
        p.classList.toggle('ra-current', on); p.setAttribute('aria-current', String(on));
      }
      const node = textBox.querySelector('.ra-chunk[data-index="' + i + '"]');
      if (!textTouched && !selection() && node && typeof node.scrollIntoView === 'function') { try { node.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (_) {} }
    };
    view.paint = () => {
      const p = view.player;
      play.disabled = !p; restart.disabled = !p; back.disabled = !p; forward.disabled = !p; seek.disabled = !p;
      play.textContent = p && p.playing ? '⏸' : '▶'; play.setAttribute('aria-label', p && p.playing ? 'Pause' : 'Play');
      if (!p) { time.textContent = '0:00 / 0:00'; return; }
      const total = p.total(), at = Math.min(total, p.position());
      seek.max = String(Math.max(0.1, total).toFixed(1));
      if (!view.dragging) seek.value = String(at.toFixed(1));
      time.textContent = clock(view.dragging ? Number(seek.value) : at) + ' / ' + clock(total) + (p.more ? '+' : '');
    };
    function showText(item) {
      textBox.replaceChildren();
      if (!item) { textBox.append(element(doc, 'p', placeholder, 'ra-placeholder')); return; }
      appendText(item, 0);
    }
    // Each new part IS a tap block; legacy readings retain their original parts. Safe DOM text, never innerHTML.
    function appendText(item, from) {
      if (!from) textBox.replaceChildren();
      item.chunks.slice(from).forEach((chunk, j) => {
        const i = from + j, block = element(doc, 'div', null, 'ra-chunk', { 'data-index': String(i), title: 'Play from here',
          role: 'button', tabindex: '0', 'aria-label': 'Play from block ' + (i + 1) });
        let prose = [];
        const flush = () => { if (prose.length) block.append(element(doc, 'p', prose.join('\n'))); prose = []; };
        for (const line of chunk.text.split('\n')) {
          const h = line.match(/^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/);
          if (h) { flush(); block.append(element(doc, 'h' + h[1].length, h[2])); }
          else if (!line.trim()) flush(); else prose.push(line);
        }
        flush();
        let gesture = null, suppress = false;
        const activate = () => { if (view.player && !selection()) { textTouched = false; view.player.jump(i); } };
        // Native click only: never prevent touch/pointer defaults, capture the pointer, or disable selection. iOS
        // cancels a scrolling tap; the explicit movement/hold check also rejects synthetic clicks after a drag/hold.
        block.addEventListener('pointerdown', e => { if (e.isPrimary === false || e.button > 0) return; suppress = false;
          gesture = { x: e.clientX, y: e.clientY, at: Date.now(), scroll: textBox.scrollTop }; });
        block.addEventListener('pointermove', e => { if (gesture && Math.hypot(e.clientX - gesture.x, e.clientY - gesture.y) > 10) suppress = true; });
        block.addEventListener('pointercancel', () => { suppress = true; gesture = null; });
        block.addEventListener('contextmenu', () => { suppress = true; });
        block.addEventListener('click', e => {
          if (e.button > 0 || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
          if (gesture && (Date.now() - gesture.at > 450 || Math.abs(textBox.scrollTop - gesture.scroll) > 8)) suppress = true;
          gesture = null;
          if (suppress) { suppress = false; return; }
          activate();
        });
        block.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); activate(); } });
        textBox.append(block);
      });
    }
    function fillRates() {
      const r = view.rate, list = RATES.includes(r) ? RATES : [...RATES, r].sort((a, b) => a - b);
      rate.replaceChildren(...list.map(v => { const o = element(doc, 'option', v + '×'); o.value = String(v); return o; }));
      rate.value = String(r);
    }

    // One saved state of the reading: the first builds the text and the player and starts playing; a later pass's
    // appends its parts (the player carries on into them); a complete one finishes it. Idempotent for a repeated reply.
    function show(item, config, generation) {
      if (generation !== view.generation || view.closed) return;
      if (config) view.config = config;
      const from = view.item ? view.item.chunks.length : 0;
      if (!view.item) { view.rate = savedRate(view.config); fillRates(); }
      view.item = item;
      appendText(item, from);
      copy.disabled = false;
      info.textContent = describe(item, view.config);
      const complete = item.complete !== false;
      if (complete) {
        regen.disabled = false;
        sha256(spec.text).then(hash => {
          if (generation === view.generation && hash !== item.sourceHash)
            info.textContent = 'This message changed after it was saved; ↻ Regenerate reads the new version. · ' + describe(item, view.config);
        }, () => {});
      }
      if (view.config && view.config.configured === false) {
        if (!from) view.fail(plain(view.config.error || 'ElevenLabs is not set up on the pane server.'));
        return;
      }
      if (!view.player) { view.player = createPlayer(view, item); view.paint(); view.player.start(); }
      else { view.player.grow(item); if (complete) view.player.finish(); }
    }

    async function start({ regenerate = false, noContext = false } = {}) {
      const generation = ++view.generation;
      if (view.player) { view.player.destroy(); view.player = null; }
      view.detach?.(); view.detach = null;
      view.startedAt = Date.now(); view.readyAt = null; view.item = null;
      showText(null); info.textContent = ''; jobLine(''); bare.hidden = true; regen.disabled = true; copy.disabled = true;
      view.paint();
      try {
        if (!regenerate && !jobs.has(keyString(spec))) {
          setState('Looking for a saved reading…');
          const got = await api('GET', '/api/read-aloud/item?' + new URLSearchParams(itemKey(spec)));
          if (generation !== view.generation) return;
          view.config = got.config;
          // A reading whose rewrite stopped part-way (its page closed, a failure, Cancel) is rewritten again.
          if (got.found && got.item.complete !== false) return show(got.item, got.config, generation);
        }
        const job = rewrite(spec, { noContext });
        view.job = job;
        // Until the first pass lands the rewrite's progress is the status line; once the voice plays, the line beneath.
        const listen = (phase, text) => {
          if (generation !== view.generation) return;
          if (view.item) jobLine(text + (phase === 'rewrite' ? ' · More blocks become tappable when this pass arrives.' : '')); else setState(text);
          cancel.hidden = !['context', 'rewrite'].includes(phase);
        };
        const land = reply => show(reply.item, reply.config, generation);
        job.listeners.add(listen); job.replies.add(land);
        view.detach = () => { job.listeners.delete(listen); job.replies.delete(land); };
        if (job.reply) land(job.reply); // reopened mid-rewrite: what is already rewritten plays at once
        listen(job.phase, job.text);
        cancel.onclick = () => job.controller.abort();
        let reply;
        try { reply = await job.promise; }
        finally { cancel.hidden = true; view.detach?.(); view.detach = null; }
        if (generation !== view.generation) return;
        jobLine('');
        show(reply.item, reply.config, generation);
      } catch (e) {
        if (generation !== view.generation || view.closed) return;
        if (e && e.contextFailed) bare.hidden = false;
        if (view.item) { // the passes already rewritten keep playing; the failure (or Cancel) is said beneath them
          view.player?.finish(); jobLine((e && e.message) || String(e), 'error'); regen.disabled = false;
          info.textContent = describe({ ...view.item, complete: true }, view.config);
          return;
        }
        view.fail(e);
      }
    }
    view.start = start;
    view.rate = savedRate(null); fillRates();

    const tick = setInterval(() => { if (view.readyAt === null) paintElapsed(); view.paint(); }, 250);
    function onKey(event) {
      if (view.closed) return;
      if (event.key === 'Escape') { event.preventDefault(); close(); return; }
      const t = event.target, typing = t && (t.tagName === 'SELECT' || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'BUTTON'
        || (typeof t.closest === 'function' && t.closest('.ra-settings, .ra-chunk')));
      if (!view.player || typing && event.key !== 'Home') return;
      if (event.key === ' ') { event.preventDefault(); view.player.toggle(); }
      else if (event.key === 'Home') { event.preventDefault(); view.player.restart(); }
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault(); view.player.skip(event.key === 'ArrowLeft' ? -SKIP_SECONDS : SKIP_SECONDS);
      }
    }
    doc.addEventListener('keydown', onKey, true);
    view.dispose = () => {
      view.closed = true; view.generation++;
      clearInterval(tick); doc.removeEventListener('keydown', onKey, true);
      view.detach?.();
      if (view.player) view.player.destroy(); else { try { audio.pause(); } catch (_) {} }
      overlay.remove();
    };
    closeBtn.onclick = () => close();
    gear.onclick = () => {
      if (!settingsHost.firstChild) settingsPanel(settingsHost, { doc, open: true });
      settingsHost.hidden = !settingsHost.hidden; gear.setAttribute('aria-expanded', String(!settingsHost.hidden));
    };
    overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
    play.onclick = () => view.player && view.player.toggle();
    restart.onclick = () => { textTouched = false; if (view.player) view.player.restart(); };
    back.onclick = () => view.player && view.player.skip(-SKIP_SECONDS);
    forward.onclick = () => view.player && view.player.skip(SKIP_SECONDS);
    seek.addEventListener('input', () => { view.dragging = true; view.paint(); });
    seek.addEventListener('change', () => { view.dragging = false; if (view.player) view.player.seek(Number(seek.value)); });
    rate.onchange = () => {
      const r = Number(rate.value); if (!Number.isFinite(r)) return;
      view.rate = r; try { store()?.setItem(RATE_KEY, String(r)); } catch (_) {}
      if (view.player) view.player.rate(r);
    };
    regen.onclick = () => { void start({ regenerate: true }); };
    bare.onclick = () => { void start({ regenerate: true, noContext: true }); };
    copy.onclick = () => {
      const text = view.item ? view.item.text : '';
      const done = ok => { copy.textContent = ok ? '✓ Copied' : 'Copy failed'; setTimeout(() => { copy.textContent = 'Copy text'; }, 1200); };
      try { win.navigator.clipboard.writeText(text).then(() => done(true), () => done(false)); } catch (_) { done(false); }
    };
    (doc.body || doc.documentElement).append(overlay);
    current = view;
    try { closeBtn.focus({ preventScroll: true }); } catch (_) {}
    void start();
    return view;
  }

  function close() { const view = current; current = null; if (view) view.dispose(); }

  globalThis.opcReadAloud = Object.freeze({ open, close, request, parts, rewrite, ownSettings, itemKey, sha256, createPlayer,
    settingsPanel, mountSettings, refineModels, withChoice, splitPasses, continueHeadings, PASSES,
    get current() { return current; }, get jobs() { return jobs; } });
})();
