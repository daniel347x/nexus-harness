/* Transcription Control · OpenCode — the pane beside CodeNomad (Chrome split view).
 *
 * Dan dictates into the box (Wispr Flow types there), ✨ Refine cleans it with the open conversation as context,
 * 📤 Send posts it to the OpenCode session, and CodeNomad shows it. Which session: the CodeNomad watcher
 * extension reports the one CodeNomad has open (relayed by server.py), or Dan picks one by hand.
 *
 * Refine is the TypingMind widget's Refine, ported: the same four editable prompt parts (refine-defaults.js,
 * copied verbatim), the same dictionary protect-list, providers, thinking levels, retry and timeout rules, and
 * the SAME localStorage key names, so the widget's settings import 1:1. What changed: the context is no longer
 * hand-copied into slots; it is read from OpenCode, the newest messages first, up to the budget. Only Dan's
 * messages and the assistant's prose go in, never tool calls (Anthropic flags tool inputs sent back inside a
 * user message).
 */
'use strict';

// ---------------------------------------------------------------- settings (widget-compatible key names)
const K = {
  provider: 'refine_provider',
  think: 'refine_think_level',
  dict: 'refine_dictionary',
  totalCost: 'refine_total_cost',
  key: { anthropic: 'refine_anthropic_api_key', openrouter: 'refine_openrouter_api_key', deepinfra: 'refine_deepinfra_api_key' },
  model: { anthropic: 'refine_anthropic_model', openrouter: 'refine_openrouter_model', deepinfra: 'refine_deepinfra_model' },
  models: { anthropic: 'refine_anthropic_models_list', openrouter: 'refine_openrouter_models_list', deepinfra: 'refine_deepinfra_models_list' },
  // pane-only
  ctxKb: 'pane_context_kb',
  // Shared with the phone: 'whole' (default) sends all earlier prose; 'budgeted' keeps the configured byte budget.
  historyMode: 'refine_history_mode',
  targetMode: 'pane_target_mode',
  lastSent: 'pane_last_sent',
  draft: 'pane_draft',
  recovery: 'pane_originals_v1',
  outputTokens: 'pane_refine_output_tokens',
  backend: 'pane_backend_v1',
  claudeTarget: 'pane_claude_target_v1',
  nativeNoContext: 'pane_claude_no_context_v1',
  claudeSend: 'pane_claude_send_v1',
  claudeControl: 'pane_claude_control_v1',
  claudeLaunch: 'pane_claude_launch_v1',
  managerTarget: 'pane_manager_target_v1',
  managerDraft: 'pane_manager_draft_v1',
  // A bad journal's export-and-clear: the exported copy (when it fits) and the recorded disposition.
  claudeJournalQuarantine: 'pane_claude_journal_quarantine_v1',
  claudeJournalDisposition: 'pane_claude_journal_disposition_v1',
  // display only: Claude-mode column widths (percent) and the details fold
  mainSplit: 'pane_main_split_v1',
  toolSplit: 'pane_tool_split_v1',
  detailsOpen: 'pane_claude_details_open_v1',
};
const IMPORTABLE = /^refine_(provider|think_level|history_mode|dictionary|total_cost|system_prompt|context_preamble|transcription_preamble|final_fence|read_aloud_(system_prompt|context_preamble|message_preamble|final_fence)|(anthropic|openrouter|deepinfra)_(api_key|model|models_list))$/;
const PROVIDERS = {
  anthropic: { label: 'Anthropic (Claude)', models: ['claude-opus-4-8', 'claude-sonnet-5', 'claude-opus-4-7', 'claude-haiku-4-5', 'claude-opus-5-5'] },
  openrouter: { label: 'OpenRouter', models: ['anthropic/claude-opus-4.8', 'anthropic/claude-sonnet-5', 'anthropic/claude-haiku-4.5'] },
  deepinfra: { label: 'DeepInfra', models: ['zai-org/GLM-5.2'] },
};
const RECOVERY_LIMIT = 2 * 1024 * 1024; // serialized UTF-16 bytes; browser quota is shared with settings/draft
const RECOVERY_COUNT = 10;
// Selection Refine sends the rest of the edit box whole: each side (draft before / after the selection)
// is cropped only past this UTF-8 ceiling, keeping the text nearest the selection. Separate from, and
// never shared with, the configured chat-history budget.
const REFINE_DRAFT_LIMIT = 256 * 1024;
const PARTS = [
  { id: 'system', label: 'System prompt (cleanup behavior)', storage: 'refine_system_prompt', def: REFINE_DEFAULT_SYSTEM_PROMPT, requires: [] },
  { id: 'context', label: 'Chat-history preamble (before <chat-history>)', storage: 'refine_context_preamble', def: REFINE_DEFAULT_CONTEXT_PREAMBLE, requires: ['{{context}}'] },
  { id: 'transcription', label: 'Transcription preamble (before text)', storage: 'refine_transcription_preamble', def: REFINE_DEFAULT_TRANSCRIPTION_PREAMBLE, requires: ['{{transcription}}'] },
  { id: 'finalfence', label: 'Final instruction fence (anti-injection)', storage: 'refine_final_fence', def: REFINE_DEFAULT_FINAL_FENCE, requires: [] },
];
// The 📜 Prompt editor lists Refine's four parts, then Read Aloud's four (refine-defaults.js READ_ALOUD_PARTS), edited,
// saved and restored the same way; Refine's request reads only its own four (PARTS).
const PROMPT_PARTS = [...PARTS, ...READ_ALOUD_PARTS];

const $ = (id) => document.getElementById(id);
const ls = {
  get: (k, d = null) => { const v = localStorage.getItem(k); return v === null ? d : v; },
  set: (k, v) => localStorage.setItem(k, v),
  del: (k) => localStorage.removeItem(k),
};
// @beacon[
//   id=opc-pane@paneStatusSubjects,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=One status line with an owner: a line set while a Send/control is uncertain names its subject, and the confirming receipt replaces it (resolveStatus) unless a newer line already did; uncertainty never sticks after the evidence arrives.,
// ]
// `subject` ties an uncertain line to the operation it is about ('send:<uuid>', 'control:<id>').
// Any newer line clears the tie, so evidence only ever replaces the line it is about.
let statusSubject = null;
function status(msg, kind = '', subject = null) { const el = $('status'); el.textContent = msg; el.className = 'status ' + kind; statusSubject = subject; }
// `keep` leaves the line tied to its subject when the evidence is partial (accepted, not yet acknowledged).
function resolveStatus(subject, msg, kind = 'ok', keep = false) { if (subject && statusSubject === subject) status(msg, kind, keep ? subject : null); }
function kb(bytes) { return (bytes / 1024).toFixed(1) + ' KB'; }
function utf8len(s) { return new TextEncoder().encode(s).length; }
function utf8Tail(s, maxBytes) {
  const bytes = new TextEncoder().encode(s);
  let start = Math.max(0, bytes.length - maxBytes);
  while (start < bytes.length && (bytes[start] & 0xc0) === 0x80) start++; // no split Unicode code point
  return new TextDecoder().decode(bytes.subarray(start));
}
function utf8Head(s, maxBytes) {
  const bytes = new TextEncoder().encode(s);
  let end = Math.min(bytes.length, maxBytes);
  while (end > 0 && end < bytes.length && (bytes[end] & 0xc0) === 0x80) end--; // no split Unicode code point
  return new TextDecoder().decode(bytes.subarray(0, end));
}
function short(sid) { return sid ? sid.slice(0, 12) + '…' : ''; }

// @beacon[
//   id=opc-pane@retainOriginal,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Browser-local full-original recovery before Refine/Send/restore: native legacy recovery is retained; OpenCode Managers use independent uncapped per-original slots and exact readback, never cropping or evicting their long dictation.,
// ]
function retainOriginal(text, action) {
  try {
    if (state.backend === 'claude' && state.ocManagerTarget) {
      const raw = JSON.stringify({text,action,at:new Date().toISOString()}), key = 'pane_manager_original_v1:'+crypto.randomUUID();
      ls.set(key,raw); if (ls.get(key) !== raw) throw new Error('original write readback failed');
      return true;
    }
    const old = readOriginals();
    const entry = { text, action, at: new Date().toISOString() };
    const next = [entry, ...old.filter((e) => e.text !== text)].slice(0, RECOVERY_COUNT);
    while (next.length > 1 && JSON.stringify(next).length * 2 > RECOVERY_LIMIT) next.pop();
    const encoded = JSON.stringify(next);
    if (encoded.length * 2 > RECOVERY_LIMIT) throw new Error('original exceeds the 2 MiB recovery limit; save it externally or work in smaller pieces');
    ls.set(K.recovery, encoded); // atomic setItem: a quota failure leaves the previous history intact
    if (ls.get(K.recovery) !== encoded) throw new Error('recovery write could not be verified');
    return true;
  } catch (e) {
    status('original NOT saved: ' + e.message + ' — operation blocked; text unchanged', 'err');
    return false;
  }
}
function readOriginals() {
  const entries = JSON.parse(ls.get(K.recovery, '[]'));
  if (!Array.isArray(entries) || entries.some((e) => !e || typeof e.text !== 'string' || typeof e.at !== 'string' || typeof e.action !== 'string')) {
    throw new Error('invalid recovery history; export it before clearing browser storage');
  }
  return entries;
}
// The clipboard is supplemental: a write the browser never settles (no focus, a pending
// permission prompt) gives up after 2.5 seconds instead of holding Refine or Send.
const CLIPBOARD_MS = 2500;
async function copyText(text) {
  let timer;
  try {
    return await Promise.race([navigator.clipboard.writeText(text).then(() => true),
      new Promise((resolve) => { timer = setTimeout(() => resolve(false), CLIPBOARD_MS); })]);
  } catch (e) { return false; } finally { clearTimeout(timer); }
}
function showRecovery() {
  try {
    const extra=[];
    for (let i=0;i<localStorage.length;i++) { const key=localStorage.key(i); if (key?.startsWith('pane_manager_original_v1:')) extra.push(JSON.parse(ls.get(key))); }
    if (globalThis.opcOpenCodeManager) for (const op of openCodeManagerApi().entries('send')) extra.push({text:op.intent.original,action:'OpenCode Send '+op.operationId,at:new Date(op.createdAt).toISOString()});
    const entries = [...extra.reverse(),...readOriginals()], sel = $('recovery-select');
    sel.innerHTML = '';
    entries.forEach((e, i) => sel.add(new Option(e.at + ' · before ' + e.action + ' · ' + kb(utf8len(e.text)), String(i))));
    const preview = () => { $('recovery-text').value = entries[Number(sel.value)]?.text || ''; };
    sel.onchange = preview; preview();
    $('recovery-restore').disabled = !entries.length;
    $('recovery-restore').onclick = () => {
      if (state.refineAbort || state.sending) return status('wait for Refine/Send before restoring', 'err');
      const text = entries[Number(sel.value)]?.text;
      if (text === undefined) return;
      const box = $('transcript');
      if (box.value && box.value !== text) {
        if (!confirm('Restore this original? The current box will also be saved in recovery.')) return;
        if (!retainOriginal(box.value, 'restore')) return;
      }
      box.value = text; draftChanged(); const saved = saveDraft(); box.focus(); $('recovery-dialog').close();
      status('original restored from browser-local recovery' + (saved ? '' : ' · draft NOT saved; keep page open/save externally'), saved ? 'ok' : 'err');
    };
    $('recovery-dialog').showModal();
  } catch (e) { status('recovery unavailable: ' + e.message, 'err'); }
}

// ---------------------------------------------------------------- OpenCode (through server.py's /oc proxy)
async function oc(method, path, body) {
  const resp = await fetch('/oc' + path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let parsed = null;
  try { parsed = await resp.json(); } catch (e) {}
  if (!resp.ok) {
    const msg = (parsed && (parsed.message || parsed.error)) || ('HTTP ' + resp.status);
    const err = new Error(msg); err.status = resp.status; throw err;
  }
  return parsed && typeof parsed === 'object' && 'data' in parsed ? parsed : { data: parsed };
}

// ---------------------------------------------------------------- target session
const state = {
  sessions: [],          // recent sessions for the picker
  watcher: null,         // last /api/active-session report
  target: null,          // { id, title, agent, model }
  targetSource: 'none',  // 'codenomad' | 'manual' | 'stale' | 'none'
  ctx: null,             // { sid, text, bytes, turns, at }
  refineAbort: null, refineTimeoutEnd: null,
  backend: 'opencode', targetRevision: 0,
  draftRevision: 0, pageEpoch: globalThis.crypto?.randomUUID?.() || String(Date.now()) + '-' + Math.random(),
  claudeTarget: null,
  nativeTargetStatus: 'no target', targetValidation: 0, targetValidationAbort: null,
  targetChannel: null, editorId: null, targetActions: new Map(),
  claudeTargetHint: null, claudeTargetError: null, claudeReselect: null, claudeSuccessor: null, claudeRecoveryRevision: 0,
  claudeManager: null, claudeSend: null, claudeControl: null, claudeJournalError: null,
  claudeObserving: false, claudeControlSending: false,
  claudeSends: new Map(), claudeControls: new Map(), claudeRecovering: false, claudePendingSelection: null,
  claudeLaunch: null, claudeReconnectRefusal: null,
  ocManagerTarget: null, ocManager: null, ocManagerCatalog: null, ocManagerObserving: false, ocManagerError: null,
};

// @beacon[
//   id=opc-pane@invalidateTarget,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Monotonic target cancellation seam: explicit backend/path/target changes invalidate context and Refine without touching draft or held inspection.,
// ]
function invalidateTarget() {
  state.targetRevision++;
  state.targetValidation++;
  state.targetValidationAbort?.abort(); state.targetValidationAbort = null;
  state.ctx = null;
  if (state.refineAbort) { state.refineAbort.reason_ = 'target'; state.refineAbort.abort(); }
  $('ctx-info').textContent = '—'; $('cache-info').textContent = '—';
}

function draftChanged() {
  state.draftRevision++;
}

// @beacon[
//   id=opc-pane@freezeRefineTarget,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Freeze the selected backend-qualified Manager or legacy target with monotonic target/page/draft revisions before awaits; context outages never imply deliberate no-context.,
// ]
function freezeRefineTarget() {
  return Object.freeze({backend: state.backend, targetRevision: state.targetRevision,
    draftRevision: state.draftRevision, pageEpoch: state.pageEpoch,
    noContext: state.backend === 'claude' && ls.get(K.nativeNoContext, 'false') === 'true',
    target: state.backend === 'claude' ? (state.ocManagerTarget ? Object.freeze({...state.ocManagerTarget}) : state.claudeTarget ? Object.freeze({...state.claudeTarget}) : null) :
      (state.target ? Object.freeze({...state.target}) : null)});
}

function sameRefineTarget(frozen, draft = false) {
  if (state.backend !== frozen.backend || state.targetRevision !== frozen.targetRevision ||
      state.pageEpoch !== frozen.pageEpoch || (draft && state.draftRevision !== frozen.draftRevision)) return false;
  if (frozen.backend !== 'claude') return (state.target?.id || null) === (frozen.target?.id || null);
  if ((ls.get(K.nativeNoContext, 'false') === 'true') !== frozen.noContext) return false;
  if (frozen.target?.backend === 'opencode' || state.ocManagerTarget)
    return globalThis.opcOpenCodeManager?.same(state.ocManagerTarget,frozen.target) === true;
  const current = state.claudeTarget, original = frozen.target;
  if (!original || !current) return original === current;
  return ['targetRef','storeRef','sessionRef','scopeRef','leafRef','fileEpoch','uuid','leafUuid','scope','source','executionId','generation'].every(k=>current[k] === original[k]);
}

async function revalidateRefineTarget(frozen, signal, context = null) {
  if (!sameRefineTarget(frozen, true)) throw new Error('Refine target/draft changed; original retained');
  if (frozen.target?.backend === 'opencode' && !frozen.noContext) {
    const actual=await openCodeManagerApi().validate(frozen.target.sessionId,signal);
    if (!openCodeManagerApi().same(actual.target,frozen.target)) throw new Error('OpenCode Manager witness changed; original retained');
  } else if (frozen.backend === 'claude' && frozen.target && !frozen.noContext) {
    const host = frozen.target.source === 'host', pin = host && context ? context : null;
    // A host pin is the server's to judge: its pinned read refuses only a message Refine read being
    // rewritten, so a live turn moving on is fine. leafUuid is not compared: the frozen path's last
    // row may be the reply still in flight, which takes a record UUID as each of its blocks lands.
    const actual = await fetchClaudeTarget(frozen.target.targetRef, signal, pin, !host);
    if (host ? !['source','executionId','generation','uuid','role','scope'].every(k=>actual[k] === frozen.target[k]) || (pin &&
        !['leafRef','fileEpoch','contextIdentity'].every(k=>actual[k] === pin[k])) :
        !['targetRef','storeRef','sessionRef','scopeRef','leafRef','fileEpoch','uuid','leafUuid','scope','source','executionId','generation'].every(k=>actual[k] === frozen.target[k])) {
      throw new Error('Claude source/path/epoch changed; original retained');
    }
  }
  if (!sameRefineTarget(frozen, true) || signal?.aborted) throw new Error('Refine canceled or target/draft changed; original retained');
}
function setBackend(backend) {
  if (!['opencode', 'claude'].includes(backend)) return;
  // Persist before changing authority: a quota failure cannot silently apply it.
  try { ls.set(K.backend, backend); }
  catch (e) { renderTarget(); return status('backend not applied: storage unavailable', 'err'); }
  invalidateTarget(); state.backend = backend; state.target = null;
  resolveTarget();
}
function targetRefValid(ref) { return typeof ref === 'string' && /^[A-Za-z0-9_-]{24,64}$/.test(ref); }
function validatedClaudeTarget(data, ref) {
  const t = data?.targetInfo;
  const refs = ['storeRef', 'sessionRef', 'scopeRef', 'leafRef', 'fileEpoch'];
  const host = t?.source === 'host';
  // A host target's authority read (GET /api/claude/target) answers from its registry state alone: its
  // history fields stay null, and Refine's frozen pin is minted and checked by its own history reads.
  const authority = host && data?.status === 'authority', unread = authority ? ['leafRef','fileEpoch'] : [];
  const fail = reason => { throw new Error('Claude target validation failed: '+reason+'; reselect'); };
  if (data?.schemaVersion !== 1 || data.backend !== 'claude') fail('response schema/backend mismatch');
  if (data.targetRef !== ref) fail('target reference changed');
  if (!authority && !(host ? ['complete','indexing','index-limit'] : ['complete','incomplete-tail']).includes(data.status)) fail('history status '+String(data.status || 'unavailable'));
  if (!t || t.schemaVersion !== 1 || t.backend !== 'claude') fail('target metadata missing or invalid');
  if (t.targetRef !== ref) fail('metadata target reference changed');
  for (const k of refs) if (unread.includes(k) ? t[k] !== null : typeof t[k] !== 'string' || !t[k] || t[k].length > 256) fail(k+' missing or invalid');
  for (const k of ['sessionRef','scopeRef','leafRef','fileEpoch']) if (!unread.includes(k) && t[k] !== data[k]) fail(k+' response mismatch');
  if (!/^[0-9a-f-]{36}$/i.test(t.uuid)) fail('session UUID invalid');
  if (authority ? t.leafUuid !== null || t.contextIdentity !== null || t.status !== 'authority' :
      !/^[0-9a-f-]{36}$/i.test(t.leafUuid) && !(t.leafUuid === null &&
      (host || (t.source === 'native' && data.events?.length === 0 && data.branches?.length <= 1 && data.diagnostics?.length === 0)))) fail('history leaf identity invalid');
  if (t.scope !== 'main') fail('only the main conversation can be targeted');
  if (!['native','windows-history','host'].includes(t.source)) fail('unsupported source '+String(t.source));
  if (typeof t.title !== 'string' || t.title.length > 4096) fail('title metadata invalid');
  if (host) {
    if (t.executionId !== 'cc-'+t.uuid) fail('host execution ID mismatch');
    if (!Number.isSafeInteger(t.generation) || t.generation < 1) fail('host generation invalid');
    if (!['Manager','conductor','worker','helper','base'].includes(t.role)) fail('host role invalid');
    if (typeof t.capabilities?.nativeInput !== 'boolean' || typeof t.capabilities?.controls !== 'boolean' ||
        t.capabilities.nativeInput !== t.capabilities.controls || (t.role !== 'Manager' && (t.capabilities.nativeInput || t.capabilities.controls))) fail('host role/capabilities mismatch');
  }
  return {schemaVersion:1, backend:'claude', targetRef:ref, ...Object.fromEntries(refs.map(k => [k,t[k]])),
     uuid:t.uuid, leafUuid:t.leafUuid, scope:'main', title:t.title, source:t.source, bindingRef:null,
        ...(host ? {executionId:t.executionId,generation:t.generation,role:t.role,cwd:t.cwd,lifecycle:t.lifecycle,successor:t.successor,contextIdentity:t.contextIdentity,capabilities:{...t.capabilities},
          reconnectBlocked:typeof t.reconnectBlocked === 'string' && t.reconnectBlocked ? t.reconnectBlocked.slice(0,800) : null} : {})};
}
// @beacon[
//   id=opc-pane@claudeTargetAuthority,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=One GET of a host target's or row's authority from its registry state (GET /api/claude/target), never a page of history, so a slow history never decides whether Dan can Send; a native history target or an older pane server answers through the history read as before.,
// ]
async function claudeTargetAuthority(params, signal = null, budgetMs = 12000) {
  // An HTTP observation budget only: aborting it changes nothing on the host.
  const ctrl = new AbortController(), timer = setTimeout(() => ctrl.abort(), budgetMs), cancel = () => ctrl.abort();
  signal?.addEventListener('abort', cancel, {once:true});
  if (signal?.aborted) ctrl.abort();
  try {
    for (const route of ['/api/claude/target?', '/api/claude/events?']) {
      const resp = await fetch(route + new URLSearchParams(params), {signal:ctrl.signal, cache:'no-store'});
      const text = await resp.text();
      if (utf8len(text) > 8 * 1024 * 1024) throw new Error('native target response exceeds limit');
      const data = JSON.parse(text);
      if (resp.ok) return data; // callers keep the target fields only, never event/prose/tool bodies
      // A native history target, or a pane server that predates the authority read: its history read.
      if (route === '/api/claude/target?' && ['history-target','missing'].includes(data?.status)) continue;
      const error = new Error(data.message || data.error || ('HTTP ' + resp.status)); error.status = resp.status; throw error;
    }
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', cancel); }
}
async function fetchClaudeTarget(ref, signal, pin = null, history = false) {
  // Selection and Send authority: the host's registry state alone. Only a Refine pin (or a known native
  // history target) needs the history read, whose frozen-path checks are exactly as before.
  if (!pin && !history) return validatedClaudeTarget(await claudeTargetAuthority({targetRef:ref}, signal), ref);
  const query = new URLSearchParams({targetRef:ref,...(pin ? {leafRef:pin.leafRef,fileEpoch:pin.fileEpoch} : {})});
  const resp = await fetch('/api/claude/events?' + query, {signal, cache:'no-store'});
  const text = await resp.text();
  if (utf8len(text) > 8 * 1024 * 1024) throw new Error('native target response exceeds limit');
  const data = JSON.parse(text);
  if (!resp.ok) throw new Error(data.message || data.error || ('HTTP ' + resp.status));
  return validatedClaudeTarget(data, ref); // Do not retain event/prose/tool bodies.
}

function hostSessionMatches(a, b) {
  return a?.source === 'host' && b?.source === 'host' &&
    ['executionId','generation','uuid','role','scope'].every(k=>a[k] === b[k]) &&
    ['nativeInput','controls'].every(k=>a.capabilities?.[k] === b.capabilities?.[k]);
}
async function currentManagerRow(hint, current) {
  if (hint?.source !== 'host' || hint.role !== 'Manager' || hint.executionId !== 'cc-'+hint.uuid) return null;
  const seen = new Set(), candidates = []; let cursor = null;
  do {
    const data = await claudeRequest('/api/claude/sessions'+(cursor ? '?cursor='+encodeURIComponent(cursor) : ''));
    if (!current()) return null;
    if (data?.schemaVersion !== 1 || data.backend !== 'claude' || !Array.isArray(data.sessions)) throw new Error('Manager inventory schema invalid');
    candidates.push(...data.sessions.filter(r=>r.source==='host' && r.role==='Manager' && r.executionId==='cc-'+r.uuid && r.state==='ready' && r.capabilities?.nativeInput===true && r.capabilities?.controls===true && targetRefValid(r.sessionRef) && targetRefValid(r.scopes?.find(s=>s.label==='main')?.scopeRef)));
    const row = data.sessions.find(r=>r.source === 'host' && r.executionId === hint.executionId);
    if (row) {
      const ready = candidates.find(r=>r.executionId===hint.executionId && r.uuid===hint.uuid && Number.isSafeInteger(r.generation) && r.generation>0);
      // The same conversation, collected or proved ended, stays selectable: its row is
      // a reconnectable Send target (Send reconnects it), never a silent other Manager.
      const reconnectable = !ready && row.role === 'Manager' && row.uuid === hint.uuid &&
        (row.lifecycle === 'sleeping' || row.lifecycle === 'ended') && row.capabilities?.reconnect === true &&
        Number.isSafeInteger(row.generation) && row.generation > 0 &&
        targetRefValid(row.sessionRef) && targetRefValid(row.scopes?.find(s=>s.label==='main')?.scopeRef) ? row : null;
      if (current()) state.claudeSuccessor = ready || reconnectable ? null : candidates.find(r=>r.executionId!==hint.executionId && typeof row.cwd==='string' && r.cwd===row.cwd) || null;
      return ready || reconnectable || null;
    }
    if (!data.hasMore) return null;
    cursor = data.nextCursor;
    if (!targetRefValid(cursor) || seen.has(cursor)) throw new Error('Manager inventory did not advance');
    seen.add(cursor);
  } while (current());
  return null;
}
// @beacon[
//   id=opc-pane@blockClaudeTarget,
//   slice_labels=opc-pane,
//   kind=ast,
   //   comment=Retain draft and receipts while automatically rediscovering the same registered Manager; a successor is offered but never silently targeted.,
// ]
async function blockClaudeTarget(error) {
  const reason = error.message || String(error), revision = state.targetRevision, recovery = ++state.claudeRecoveryRevision;
  state.claudeTargetError = reason; state.claudeReselect = null;
  const host = (state.claudeTargetHint || state.claudeTarget)?.source === 'host';
  state.nativeTargetStatus = (host ? 'Reconnecting: ' : 'blocked: ')+reason;
  status((host ? 'Checking this Manager again · draft retained: ' : 'Claude target blocked: ')+reason,host ? 'info' : 'err'); renderTarget();
  const current = () => state.targetRevision === revision && state.claudeRecoveryRevision === recovery && state.claudeTargetError === reason;
  try {
    const row = await currentManagerRow(state.claudeTargetHint || state.claudeTarget,current);
    if (current()) { state.claudeReselect = row; renderTarget(); }
  } catch (_) { /* Keep original precise failure; outage is not Send authority. */ }
}
// @beacon[
//   id=opc-pane@recoverClaudeTarget,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Revalidate saved or temporarily unavailable same-execution authority without resending; a collected/ended registered Manager rebinds as a reconnectable Send target, a new generation cancels frozen work and old receipts stay query-only.,
// ]
async function recoverClaudeTarget() {
  const hint = state.claudeTargetHint || state.claudeTarget;
  if (state.claudeRecovering || state.targetValidationAbort || state.sending || state.refineAbort || state.claudeControlSending ||
      state.backend !== 'claude' || hint?.source !== 'host' || hint.role !== 'Manager') return;
  state.claudeRecovering = true;
  const revision = state.targetRevision, recovery = state.claudeRecoveryRevision;
  const current = () => state.backend === 'claude' && revision === state.targetRevision && recovery === state.claudeRecoveryRevision;
  try {
    const row = await currentManagerRow(hint,current);
    if (!current()) return;
    if (!row) { state.claudeReselect = null; return; }
    // Already the same conversation's reconnectable target (collected/ended): nothing to
    // rediscover — no repeated replay read and no target churn on the poll cadence.
    if (!state.claudeTargetError && reconnectableManager(state.claudeTarget) &&
        state.claudeTarget.executionId === row.executionId && state.claudeTarget.generation === row.generation) return;
    // The row's authority from its registry state, never a page of its history.
    const data = await claudeTargetAuthority({sessionRef:row.sessionRef,scopeRef:row.scopes.find(s=>s.label==='main').scopeRef});
    if (!current()) return;
    const target = validatedClaudeTarget(data,data.targetRef);
    if (target.executionId !== hint.executionId || target.uuid !== hint.uuid || target.generation !== row.generation || target.role !== 'Manager') throw new Error('Manager changed during reconnect');
    await acceptClaudeTarget(target.targetRef,revision,current);
    status(state.nativeTargetStatus,isClaudeManager(state.claudeTarget) ? 'ok' : 'info');
  } catch (_) { /* Next modest poll retries discovery. No execution or blind resend. */ }
  finally { state.claudeRecovering = false; }
}
// @beacon[
//   id=opc-pane@reselectCurrentManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Explicitly validate the same Manager (live, or its collected/ended conversation for reconnect-on-Send) or its advertised same-workspace successor; drafts and old-generation receipts stay separate and no process is launched.,
// ]
async function reselectCurrentManager() {
  if ((!state.claudeReselect && !state.claudeSuccessor) || state.sending || state.refineAbort || state.claudeControlSending) return;
  const hint = state.claudeSuccessor || state.claudeTargetHint || state.claudeTarget, revision = state.targetRevision, recovery = ++state.claudeRecoveryRevision;
  const current = () => state.backend === 'claude' && state.targetRevision === revision && state.claudeRecoveryRevision === recovery;
  try {
    const row = await currentManagerRow(hint,current);
    if (!current()) return;
    if (!row) throw new Error('the same Manager is no longer ready; select it after explicit launch/resume');
    const scope = row.scopes.find(s=>s.label === 'main');
    const data = await claudeTargetAuthority({sessionRef:row.sessionRef,scopeRef:scope.scopeRef});
    if (!current()) return;
    const target = validatedClaudeTarget(data,data.targetRef);
    const selectable = isClaudeManager(target) || reconnectableManager(target);
    if (!selectable || target.executionId !== hint.executionId || target.generation !== row.generation) throw new Error('Manager identity changed during reselect; not applied');
    await acceptClaudeTarget(target.targetRef,revision,current);
    status(isClaudeManager(target) ? 'Current Manager reselected here · generation '+target.generation+' · draft retained' : state.nativeTargetStatus,isClaudeManager(target) ? 'ok' : 'info');
  } catch (e) { if (current()) await blockClaudeTarget(e); }
}

// @beacon[
//   id=opc-pane@acceptClaudeTarget,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Validate explicit target authority (a host target from its registry state alone, never a history page; a native history target through its history read) then ACK only the current target action; selecting Claude replaces an OpenCode Manager without changing its drafts or journal, and retains native legacy recovery keys.,
// ]
async function acceptClaudeTarget(ref, expectedRevision, currentAction, revalidate = false) {
  if (!targetRefValid(ref) || expectedRevision !== state.targetRevision) throw new Error('target revision changed; not applied');
  state.targetValidationAbort?.abort();
  const generation = ++state.targetValidation, ctrl = new AbortController();
  state.targetValidationAbort = ctrl;
  renderTarget();
  const timeout = setTimeout(() => ctrl.abort(), 12000);
  try {
    const target = await fetchClaudeTarget(ref, ctrl.signal);
    if (revalidate && state.claudeTargetHint?.source === 'host' && !hostSessionMatches(target,state.claudeTargetHint)) {
      throw new Error('host session generation/role/capabilities changed; reselect current Manager');
    }
    if (ctrl.signal.aborted || generation !== state.targetValidation || expectedRevision !== state.targetRevision || !currentAction()) {
      throw new Error('newer target action won; not applied');
    }
    if (!revalidate) {
      // Write both keys before publishing authority. No saved binding or arm.
      const previous = ls.get(K.claudeTarget), oldBackend = ls.get(K.backend);
      try { ls.set(K.claudeTarget, JSON.stringify(target)); ls.set(K.backend, 'claude');
        ls.set(K.managerTarget,JSON.stringify({backend:'claude',target,selection:globalThis.crypto?.randomUUID?.() || state.pageEpoch+'-'+Date.now()+'-'+Math.random()})); }
      catch (e) {
        try { previous === null ? ls.del(K.claudeTarget) : ls.set(K.claudeTarget, previous);
          oldBackend === null ? ls.del(K.backend) : ls.set(K.backend, oldBackend); } catch (_) {}
        throw new Error('target not applied: storage unavailable; reload revalidates');
      }
      invalidateTarget(); state.backend = 'claude'; state.target = null;
    }
    state.ocManagerTarget = null; state.ocManager = null; state.ocManagerCatalog = null;
    state.claudeTarget = target; state.claudeTargetHint = target; state.claudeManager = null;
    state.claudeTargetError = null; state.claudeReselect = null; state.claudeSuccessor = null; state.claudeRecoveryRevision++;
    selectClaudeJournals(target);
    state.nativeTargetStatus = isClaudeManager(target) ? 'Ready: sending to '+target.title :
      (target.lifecycle === 'ended' || target.lifecycle === 'sleeping') ? endedManagerText(target) :
      target.source === 'host' && target.role === 'Manager' ? 'Connecting to '+target.title+' · checking readiness automatically' : 'read-only history context';
    renderTarget(); return target;
  } finally {
    clearTimeout(timeout);
    if (state.targetValidationAbort === ctrl) state.targetValidationAbort = null;
    renderTarget();
  }
}
// The previous Manager (OpenCode or Claude) stops being the Send/Refine target, and its recovery hint goes
// with it so a poll cannot quietly reselect it. Drafts, journals and receipts are untouched.
function revokeManagerAuthority() {
  if (!state.ocManagerTarget && !state.claudeTarget && !state.claudeTargetHint) return;
  state.ocManagerTarget = null; state.ocManager = null; state.ocManagerCatalog = null; state.ocManagerError = null;
  state.claudeTarget = null; state.claudeTargetHint = null; state.claudeManager = null;
  state.claudeReselect = null; state.claudeSuccessor = null; state.claudeTargetError = null; state.claudeRecoveryRevision++;
  state.nativeTargetStatus = 'Selecting · Send waits until the newly selected Manager is confirmed';
  renderTarget();
}
async function handleTargetMessage(event) {
  const m = event.data;
  if (!m || m.schemaVersion !== 1 || typeof m.clientId !== 'string' || m.clientId.length > 128) return;
  const direct = event.source && event.source === $('claude-sessions-frame')?.contentWindow && event.origin === (globalThis.origin || globalThis.location?.origin);
  // BroadcastChannel is discovery only. Only this page's actual same-origin
  // iframe can apply a target; a standalone viewer or another tab cannot.
  if (event.source && !direct) return;
  const reply = message => { try { direct ? event.source.postMessage(message,event.origin) : state.targetChannel?.postMessage(message); } catch (_) {} };
  if (m.type === 'hello') {
    if (Object.keys(m).some(k => !['schemaVersion','type','clientId','requestId'].includes(k)) || typeof m.requestId !== 'string' || m.requestId.length > 128) return;
    return reply({schemaVersion:1,type:'presence',clientId:m.clientId,requestId:m.requestId,
      editorId:state.editorId,targetRevision:state.targetRevision});
  }
  if (!direct || m.type !== 'target' || Object.keys(m).some(k => !['schemaVersion','type','clientId','editorId','actionId','actionRevision','targetRevision','targetRef','automatic','backend','sessionId'].includes(k)) ||
      m.editorId !== state.editorId || typeof m.actionId !== 'string' || !m.actionId || m.actionId.length > 128 ||
      !Number.isSafeInteger(m.actionRevision) || m.actionRevision < 1 || !Number.isSafeInteger(m.targetRevision) ||
      (m.automatic !== undefined && typeof m.automatic !== 'boolean') ||
       !(m.backend === 'opencode' ? /^ses_[A-Za-z0-9]{8,64}$/.test(m.sessionId || '') : targetRefValid(m.targetRef))) return;
  const ack = (applied, reason, pending=false) => reply({schemaVersion:1,type:'ack',clientId:m.clientId,editorId:state.editorId,
    actionId:m.actionId,actionRevision:m.actionRevision,applied,reason,targetRevision:state.targetRevision,...(pending?{pending:true}:{})});
  const last = state.targetActions.get(m.clientId) || 0;
  if (m.actionRevision <= last) return ack(false, 'old action; not applied');
  if (!state.targetActions.has(m.clientId) && state.targetActions.size >= 32) return ack(false, 'editor client limit; reload history');
  state.targetActions.set(m.clientId, m.actionRevision);
  draftChanged(); // A deliberate target action invalidates an older ACK even before its validation GET returns.
  state.claudePendingSelection = null;
  // Selecting a different Manager revokes the previous one's Send authority now, in both runtimes:
  // a selection that fails or times out leaves no target, never the old one behind the row on screen.
  // (A request carrying an outdated target revision is refused below and revokes nothing.)
  if (m.targetRevision === state.targetRevision &&
      !(m.backend === 'opencode' ? state.ocManagerTarget?.sessionId === m.sessionId :
        !state.ocManagerTarget && state.claudeTarget?.targetRef === m.targetRef)) revokeManagerAuthority();
  const current = () => state.targetActions.get(m.clientId) === m.actionRevision && state.targetRevision === m.targetRevision;
  try {
    if (m.backend === 'opencode') {
      await acceptOpenCodeManager(m.sessionId,m.targetRevision,()=>state.targetActions.get(m.clientId) === m.actionRevision);
      status(state.nativeTargetStatus,'ok'); ack(true,state.nativeTargetStatus); return;
    }
    await acceptClaudeTarget(m.targetRef, m.targetRevision, () => state.targetActions.get(m.clientId) === m.actionRevision);
    if (state.claudeLaunch && state.claudeTarget?.uuid !== state.claudeLaunch.uuid) {
      // Dan chose another target while a New Manager was starting: his choice wins.
      newManagerStatus(state.claudeLaunch.name+' keeps starting and stays in the list; it was not selected because you chose '+state.claudeTarget.title+'.');
      state.claudeLaunch = null; saveLaunch(null);
    }
    status(state.nativeTargetStatus,isClaudeManager() ? 'ok' : 'info');
    ack(true, state.nativeTargetStatus);
  } catch (e) {
    if (m.automatic && m.backend !== 'opencode' && current()) {
      state.claudePendingSelection = {ref:m.targetRef,revision:m.targetRevision,current,ack};
      state.nativeTargetStatus = 'Connecting · checking the selected Manager again; draft retained';
      status(state.nativeTargetStatus,'info'); ack(false,state.nativeTargetStatus,true);
    } else { status('Target not applied: '+e.message,'err'); ack(false, e.message); }
  } finally { renderTarget(); }
}
async function initializeTargetState() {
  state.editorId = globalThis.crypto?.randomUUID?.() || ('pane-' + Date.now() + '-' + Math.random().toString(36).slice(2));
  globalThis.addEventListener?.('message',handleTargetMessage);
  if (typeof BroadcastChannel === 'function') {
    try { state.targetChannel = new BroadcastChannel('pane-target-v1'); state.targetChannel.onmessage = handleTargetMessage; }
    catch (_) { /* integrated direct relay still works */ }
  }
  try {
    state.backend = ls.get(K.backend, 'opencode') === 'claude' ? 'claude' : 'opencode';
    const managerSaved = JSON.parse(ls.get(K.managerTarget,'null'));
    if (managerSaved?.backend === 'opencode') {
      await acceptOpenCodeManager(managerSaved.target.sessionId,state.targetRevision,()=>true,true,managerSaved.target);
      restoreLaunch(); renderTarget(); return;
    }
    const raw = ls.get(K.claudeTarget);
    if (raw) {
      const stored = JSON.parse(raw);
      if (stored.schemaVersion !== 1 || !targetRefValid(stored.targetRef)) throw new Error('invalid saved target');
      state.claudeTargetHint = stored; // recovery hint only, never Send authority
      state.nativeTargetStatus = 'revalidating saved target';
      // Stored labels/binding never become live authority, even before the await.
      await acceptClaudeTarget(stored.targetRef, state.targetRevision, () => true, true);
    }
  } catch (e) { await blockClaudeTarget(e); await recoverClaudeTarget(); }
  restoreLaunch();
  renderTarget();
}

async function loadSessions() {
  if (state.backend === 'claude') return;
  try {
    const r = await oc('GET', '/api/session?limit=100');
    const cutoff = Date.now() - 3 * 24 * 3600 * 1000;
    const recent = (r.data || []).filter((s) => ((s.time && (s.time.updated || s.time.created)) || 0) >= cutoff);
    recent.sort((a, b) => ['manager', 'manager-gpt'].includes(b.agent) - ['manager', 'manager-gpt'].includes(a.agent)
      || ((b.time.updated || 0) - (a.time.updated || 0)));
    state.sessions = recent;
    renderSelect();
    // Dismiss our recovered failure, but leave Send/Refine messages alone.
    if (state.backend !== 'claude' && $('status').textContent.startsWith('session list failed: ')) status('');
  } catch (e) { if (state.backend !== 'claude') status('session list failed: ' + e.message, 'err'); }
}

function renderSelect() {
  const sel = $('target-select');
  const mode = ls.get(K.targetMode, 'follow');
  sel.innerHTML = '';
  const follow = new Option('↔ follow CodeNomad', 'follow');
  sel.add(follow);
  for (const s of state.sessions) {
    const tag = ['manager', 'manager-gpt'].includes(s.agent) ? '🛰️ ' : '';
    sel.add(new Option(tag + (s.title || s.id) + '  · ' + short(s.id), s.id));
  }
  sel.value = [...sel.options].some((o) => o.value === mode) ? mode : 'follow';
}

async function pollWatcher() {
  if (state.backend === 'claude') return;
  if (state.polling) return;  // never overlap: a slow resolve must not stack up behind the 700 ms timer
  state.polling = true;
  try {
    try {
      const r = await fetch('/api/active-session');
      state.watcher = await r.json();
    } catch (e) { state.watcher = null; }
    if (state.backend !== 'claude') await resolveTarget(); // cache for returning to OpenCode, never native authority
  } finally { state.polling = false; }
}

// @beacon[
//   id=opc-pane@resolveTarget,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Discriminated target owner: the integrated Manager surface selects validated Claude or OpenCode authority independently of the preserved legacy follow/manual/stale watcher adapter.,
// ]
async function resolveTarget() {
  if (state.backend === 'claude') { renderTarget(); return; }
  const mode = ls.get(K.targetMode, 'follow');
  let sid = null, source = 'none';
  if (mode !== 'follow') { sid = mode; source = 'manual'; }
  else if (state.watcher && state.watcher.sessionId) {
    sid = state.watcher.sessionId;
    source = (state.watcher.age_s !== null && state.watcher.age_s < 45) ? 'codenomad' : 'stale';
  }
  const changed = !state.target || state.target.id !== sid;
  state.targetSource = source;
  if (changed) {
    invalidateTarget();
    state.target = sid ? { id: sid, title: (state.watcher && state.watcher.sessionId === sid && state.watcher.title) || sid } : null;
    state.ctx = null;
    if (sid) {
      const targetRevision = state.targetRevision;
      try {
        const s = (await oc('GET', '/api/session/' + encodeURIComponent(sid))).data;
        // A manual pick or watcher update may have won while this GET was pending.
        if (state.backend === 'claude' || state.targetRevision !== targetRevision || ls.get(K.targetMode, 'follow') !== mode || state.target?.id !== sid) return;
        state.target = { id: sid, title: s.title || sid, agent: s.agent, model: s.model };
      } catch (e) { /* keep the watcher's title */ }
      refreshContext(false);
    } else {
      $('ctx-info').textContent = '—'; $('cache-info').textContent = '—';
    }
  }
  renderTarget();
}

// @beacon[
//   id=opc-pane@renderClaudeLayout,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Lazy same-origin sessions embed in Claude mode; retain dictation/inspector and iframe scroll on switches with no OpenCode layout or transport changes.,
// ]
function renderClaudeLayout(native) {
  // Read the inspector's reading positions before any layout change: a reader inside a
  // container that stops rendering loses them.
  const readers = ['inspector-input', 'inspector-output'].map(id => $(id)), kept = readers.map(r => [r?.scrollTop, r?.scrollLeft]);
  document.body?.classList?.toggle('claude-mode', native);
  document.body?.classList?.toggle('fold-closed', ls.get(K.detailsOpen, 'false') !== 'true');
  const viewer = $('claude-viewer'), frame = $('claude-sessions-frame');
  if (viewer) viewer.hidden = !native;
  if (native && frame && !frame.getAttribute?.('src')) frame.setAttribute?.('src', '/sessions?embedded=1');
  if (placeToolInspector(native)) readers.forEach((r, i) => { if (r) { r.scrollTop = kept[i][0] || 0; r.scrollLeft = kept[i][1] || 0; } });
}
// @beacon[
//   id=opc-pane@placeToolInspector,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Claude mode gives the held tool inspector its own full-height column right of the transcription column; OpenCode mode puts it back under dictation. Same node moved, reader scroll kept.,
// ]
function placeToolInspector(native) {
  const inspector = $('tool-inspector'), home = native ? $('tool-column') : $('work-area');
  if (!inspector || typeof home?.append !== 'function' || inspector.parentNode === home) return false;
  home.append(inspector);
  return true;
}
// @beacon[
//   id=opc-pane@columnDivider,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Draggable vertical divider (pointer capture, arrow keys, Home/End) writing one percent width to a CSS variable, remembered in this browser; used for the main split and the tool column.,
// ]
function columnDivider(divider, container, property, key, initial, minimum, maximum, fromRight = false) {
  if (!divider?.addEventListener || typeof container?.style?.setProperty !== 'function') return;
  let percentage = Number(ls.get(key, String(initial))), dragging = null;
  const apply = (value, save = false) => {
    percentage = Math.max(minimum, Math.min(maximum, Number.isFinite(value) ? value : initial));
    container.style.setProperty(property, percentage + '%');
    divider.setAttribute?.('aria-valuenow', String(Math.round(percentage)));
    if (save) try { ls.set(key, String(Math.round(percentage * 10) / 10)); } catch (_) { /* display preference only */ }
  };
  divider.setAttribute?.('aria-valuemin', String(minimum)); divider.setAttribute?.('aria-valuemax', String(maximum));
  apply(percentage);
  const finish = () => { if (!dragging) return; dragging = null; divider.classList?.remove('dragging'); document.body?.classList?.remove('column-dragging'); apply(percentage, true); };
  divider.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    event.preventDefault(); divider.focus?.(); try { divider.setPointerCapture?.(event.pointerId); } catch (_) { /* synthetic or ended pointer: drag still works inside the divider */ }
    dragging = { id: event.pointerId, x: event.clientX, percentage };
    divider.classList?.add('dragging'); document.body?.classList?.add('column-dragging');
  });
  divider.addEventListener('pointermove', event => {
    if (!dragging || dragging.id !== event.pointerId) return;
    const width = container.getBoundingClientRect().width;
    if (width) apply(dragging.percentage + (event.clientX - dragging.x) / width * 100 * (fromRight ? -1 : 1));
  });
  divider.addEventListener('pointerup', finish); divider.addEventListener('pointercancel', finish);
  divider.addEventListener('lostpointercapture', finish);
  divider.addEventListener('keydown', event => {
    const step = (fromRight ? -1 : 1) * 2;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); apply(percentage + (event.key === 'ArrowLeft' ? -step : step), true); }
    else if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); apply(event.key === 'Home' ? minimum : maximum, true); }
  });
}
function toggleClaudeDetails(open = ls.get(K.detailsOpen, 'false') !== 'true') {
  try { ls.set(K.detailsOpen, String(open)); } catch (_) { /* display preference only */ }
  document.body?.classList?.toggle('fold-closed', !open);
  const button = $('claude-fold');
  if (button) { button.textContent = (open ? '▾' : '▸') + ' details'; button.setAttribute?.('aria-expanded', String(open)); }
}

function renderTarget() {
  const native = state.backend === 'claude';
  renderClaudeLayout(native);
  $('backend-select').value = native ? 'claude' : 'opencode';
  $('target-select').disabled = native;
  if (native && state.ocManagerTarget) {
    const t=state.ocManagerTarget, api=openCodeManagerApi(); let pending=null;
    try { pending=api.pending(t); } catch (e) { state.ocManagerError=e.message; }
    $('send-btn').disabled=!!(state.sending || state.refineAbort || state.targetValidationAbort || pending);
    if (!state.refineAbort) $('refine-btn').disabled=!!state.targetValidationAbort && ls.get(K.nativeNoContext,'false') !== 'true';
    $('native-context-choice').hidden=false; $('native-no-context').checked=ls.get(K.nativeNoContext,'false') === 'true';
    $('backend-status').textContent='OpenCode Manager · direct Send (busy → native steer) · no CodeNomad required';
    $('claude-reselect').hidden=true;
    const pill=$('target-pill'); pill.className='pill pill-manual';
    pill.textContent='OpenCode · Manager · '+(state.ocManager?.title || t.sessionId);
    pill.title=t.sessionId+'\n'+(state.ocManager?.directory || '')+'\nTargetRevision '+state.targetRevision;
    if (ls.get(K.nativeNoContext,'false') === 'true') $('ctx-info').textContent='Conversation context omitted by explicit choice';
    else if (state.ctx?.backend !== 'opencode') $('ctx-info').textContent='— · refresh OpenCode main conversation context';
    $('cache-info').textContent=opcOpenCodeManager.contextLabel(state.ocManager?.context);
    renderClaudeManager(); renderNewManager(); return;
  }
  $('send-btn').disabled = !!(!!state.sending || !!state.refineAbort ||
   (native && ((!isClaudeManager() && !reconnectableManager()) || !!state.targetValidationAbort || !!state.claudePendingSelection || !!state.claudeTargetError || !!state.claudeJournalError || !!state.claudeControlSending ||
      (pendingConversationSend() && !reconnectableManager()))));
  if (!state.refineAbort) $('refine-btn').disabled = native && (!state.claudeTarget || !!state.targetValidationAbort || !!state.claudePendingSelection) && ls.get(K.nativeNoContext, 'false') !== 'true';
  $('native-context-choice').hidden = !native;
  $('native-no-context').checked = ls.get(K.nativeNoContext, 'false') === 'true';
  $('backend-status').textContent = native ? (isClaudeManager() ? 'Claude · Manager selected for Send · history/inspector/context notices do not revoke Send' :
    (state.sending && reconnectableManager(state.claudeTarget)) ? 'Claude · Reconnecting to '+state.claudeTarget.title+' … · the message follows when the Manager is ready; draft retained' :
    (state.claudeReconnectRefusal && state.claudeReconnectRefusal.executionId === state.claudeTarget?.executionId &&
      state.claudeReconnectRefusal.generation === state.claudeTarget?.generation) ?
      'Claude · '+state.claudeTarget.title+' could not be reconnected: '+state.claudeReconnectRefusal.reason+' · ＋ New Manager starts a fresh one; your draft is kept' :
    (state.claudeTarget?.lifecycle === 'ended' || state.claudeTarget?.lifecycle === 'sleeping') ? endedManagerText(state.claudeTarget) :
    'Claude · '+state.nativeTargetStatus+' · Send unavailable until a live Manager is selected · Refine may use history or explicitly omit context') : 'OpenCode / CodeNomad';
  renderClaudeManager(); renderNewManager();
  const reselect = $('claude-reselect');
  if (reselect) { reselect.hidden = !native || (!state.claudeReselect && !state.claudeSuccessor); reselect.disabled = !!state.sending || !!state.refineAbort || !!state.claudeControlSending;
    reselect.textContent = state.claudeSuccessor ? 'Select live Manager: '+state.claudeSuccessor.title : 'Reconnect to selected Manager'; }
  const pill = $('target-pill');
  if (native) {
    const t = state.claudeTarget;
    pill.className = 'pill pill-manual';
    // Round 9a: name only (the UUID and source are on hover); the status text shows only while
    // it is not plain "Ready", which the title bar's status already says.
    const ready = !!t && isClaudeManager() && !state.claudeTargetError && !state.targetValidationAbort;
    pill.textContent = 'Claude · ' + (t ? (t.role ? t.role + ' · ' : 'history · ') + t.title : 'no validated target') + (ready ? '' : ' · ' + state.nativeTargetStatus);
    pill.title = t ? [t.title + ' · ' + state.nativeTargetStatus,t.uuid,t.source,t.sessionRef,t.scopeRef,t.leafRef,t.fileEpoch,'TargetRevision '+state.targetRevision].join('\n') : state.nativeTargetStatus;
    if (ls.get(K.nativeNoContext, 'false') === 'true') $('ctx-info').textContent = 'Conversation context omitted by explicit choice';
    else if (!state.ctx || state.ctx.backend !== 'claude') $('ctx-info').textContent = '— · select a main Claude target / refresh context';
    $('cache-info').textContent = '— · native metric unavailable';
    return;
  }
  const t = state.target;
  if (!t) {
    pill.className = 'pill pill-none';
    // A fresh report with no session is not silence: either none is open, or CodeNomad's page
    // changed under the watcher's selectors (read on 0.20.1). Say so rather than "silent".
    const reporting = state.watcher && state.watcher.age_s !== null && state.watcher.age_s < 45;
    pill.textContent = ls.get(K.targetMode, 'follow') !== 'follow' ? 'OpenCode · no session' :
      reporting ? 'OpenCode · no session (the CodeNomad watcher sees none it recognises; if one is open, CodeNomad may have changed)' :
      'OpenCode · no session (CodeNomad watcher silent)';
    return;
  }
  const cls = { codenomad: 'pill-live', manual: 'pill-manual', stale: 'pill-stale' }[state.targetSource] || 'pill-none';
  const how = { codenomad: 'following CodeNomad', manual: 'picked by hand', stale: 'CodeNomad watcher silent: last known' }[state.targetSource] || '';
  pill.className = 'pill ' + cls;
  pill.textContent = 'OpenCode · ' + (['manager', 'manager-gpt'].includes(t.agent) ? '🛰️ ' : (t.agent ? '⚠ ' + t.agent + ' · ' : '')) + t.title + ' · ' + t.id + ' · ' + how;
  pill.title = t.id + '\n' + how + (t.model ? '\n' + t.model.providerID + '/' + t.model.id + (t.model.variant ? ' · ' + t.model.variant : '') : '');
}

// ---------------------------------------------------------------- context from the conversation
function messageText(m) {
  if (m.type === 'user') return (m.text || '').trim();
  if (m.type === 'assistant') {
    return (m.content || []).filter((p) => p && p.type === 'text' && p.text).map((p) => p.text).join('\n\n').trim();
  }
  return '';
}

const CONTEXT_SEPARATOR = '\n\n---\n\n';
// The shared setting: whole conversation unless Budgeted history was explicitly chosen. Any other stored value
// (an old or invalid one) reads as the default rather than blocking Refine.
function refineHistoryMode() { return ls.get(K.historyMode) === 'budgeted' ? 'budgeted' : 'whole'; }
// Optional cache-placement metadata, not conversation authority. The core validates end offsets
// and falls back to text-only partitioning if they are missing/malformed; the text still Refines.
function readHistoryBoundaries(value) { return Array.isArray(value) ? value : []; }
// A stable identity per formatted OpenCode block: the message's own wire id, else this read's newest-first position.
function contextMessageId(m, ordinal) {
  for (const key of ['id', 'uuid', 'messageId', 'recordRef']) if (typeof m[key] === 'string' && m[key]) return m[key];
  return 'order-' + ordinal;
}
// @beacon[
//   id=opc-pane@buildContext,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Freeze genuine Refine prose independently of Send authority: either-device OpenCode Managers use the guarded server paged context; native Claude and legacy desktop OpenCode retain whole/budgeted reads and UTF16 boundaries.,
// ]
async function buildContext(sid, signal) {
  const mode = refineHistoryMode(), whole = mode === 'whole';
  if (sid?.target?.backend === 'opencode') {
    const saved=ls.get(K.ctxKb,'50');
    if (!whole && (!/^\d+$/.test(saved) || Number(saved)<5 || Number(saved)>400)) throw new Error('Context budget must be 5–400 KiB');
    const ctx=await openCodeManagerApi().context(sid.target,mode,Number(saved)*1024,signal);
    if (!sameRefineTarget(sid)) throw new Error('OpenCode context target changed; original retained');
    return ctx;
  }
  if (sid && typeof sid === 'object' && sid.backend === 'claude') {
    const frozen = sid, t = frozen.target;
    if (!t || t.scope !== 'main') throw new Error('Claude Refine requires a validated main history target; or explicitly choose no conversation context');
    const saved = ls.get(K.ctxKb, '50'), budget = whole ? null : Number(saved) * 1024;
    if (!whole && (!/^\d+$/.test(saved) || Number(saved) < 5 || Number(saved) > 400)) throw new Error('Claude context budget must be 5–400 KiB; saved value not changed');
    const ctrl = new AbortController(), timeout = setTimeout(()=>ctrl.abort(),12000);
    const cancel = () => ctrl.abort();
    signal?.addEventListener('abort', cancel, {once:true});
    if (signal?.aborted) ctrl.abort();
    try {
      const query = new URLSearchParams({targetRef:t.targetRef,targetRevision:String(frozen.targetRevision),historyMode:mode});
      if (!whole) query.set('budgetBytes', String(budget)); // whole history: no byte budget is asked for or enforced
      const resp = await fetch('/api/claude/context?' + query, {signal:ctrl.signal,cache:'no-store'});
      const wire = await resp.text();
      if (ctrl.signal.aborted) throw new Error('Claude context read timed out');
      if (utf8len(wire) > 8*1024*1024) throw new Error('Claude context response exceeds limit; not cropped');
      const ctx = JSON.parse(wire);
      if (!resp.ok) throw new Error(ctx.error || 'Claude context HTTP ' + resp.status);
      if (ctx.schemaVersion === 1 && ctx.historyMode !== undefined && ctx.historyMode !== mode)
        throw new Error('Claude context history mode mismatch (' + ctx.historyMode + ' while ' + mode + ' was requested); original retained');
      if (ctx.schemaVersion !== 1 || ctx.backend !== 'claude' || ctx.status !== 'stored' ||
          ctx.targetRevision !== frozen.targetRevision || typeof ctx.text !== 'string' ||
          ctx.bytes !== utf8len(ctx.text) || (!whole && ctx.bytes > budget) ||
          !(t.source === 'host' ? ['targetRef','storeRef','sessionRef','scopeRef'] :
            ['targetRef','storeRef','sessionRef','scopeRef','leafRef','fileEpoch','leafUuid']).every(k=>ctx[k] === t[k]) ||
          (t.source === 'host' && (ctx.source !== 'host' || ctx.executionId !== t.executionId || ctx.generation !== t.generation ||
            !targetRefValid(ctx.leafRef) || !targetRefValid(ctx.fileEpoch) || !targetRefValid(ctx.contextIdentity) ||
            !(ctx.leafUuid === null || /^[0-9a-f-]{36}$/i.test(ctx.leafUuid)))) ||
          !Number.isSafeInteger(ctx.turns) || ctx.turns < 0 || !Number.isSafeInteger(ctx.users) || ctx.users < 0 || ctx.users > ctx.turns ||
          !Number.isSafeInteger(ctx.readAt) || ctx.readAt < 0) {
        throw new Error('Claude context identity/epoch/schema mismatch; original retained');
      }
      const historyBoundaries = readHistoryBoundaries(ctx.historyBoundaries);
      if (!sameRefineTarget(frozen)) throw new Error('Claude context target changed; original retained');
      return {...ctx, historyMode:mode, historyBoundaries, at:new Date(ctx.readAt), lastTokens:[]};
    } finally { clearTimeout(timeout); signal?.removeEventListener('abort', cancel); }
  }
  const saved = ls.get(K.ctxKb, '50');
  const budget = whole ? null : Math.max(5, parseInt(saved, 10) || 50) * 1024;
  const entries = []; let used = 0, turns = 0, users = 0, lastTokens = [];
  const seenCursors = new Set();
  let path = '/api/session/' + encodeURIComponent(sid) + '/message?order=desc&limit=200';
  for (let page = 0; path && (whole || used < budget); page++) {
    if (!whole && page >= 8) break; // Budgeted history keeps today's bounded read; whole history pages to exhaustion
    const r = await oc('GET', path);
    for (const m of r.data || []) {
      if (m.type === 'assistant' && m.tokens && lastTokens.length < 5) lastTokens.push(m);
      const text = messageText(m);
      if (!text || (!whole && used >= budget)) continue;
      const label = m.type === 'user' ? '[USER]' : '[ASSISTANT]';
      let block = label + '\n' + text;
      const size = utf8len(block) + CONTEXT_SEPARATOR.length;
      if (!whole && used + size > budget) {
        // Keep the tail of the oldest block that only partly fits.
        const room = budget - used - utf8len(label) - 10;
        if (room < 400) { used = budget; continue; }
        block = label + '\n…' + utf8Tail(text, room);
      }
      entries.push({ id: contextMessageId(m, entries.length), block }); used += utf8len(block) + CONTEXT_SEPARATOR.length;
      turns++; if (m.type === 'user') users++;
    }
    const next = r.cursor && r.cursor.next;
    if (!next) { path = null; break; }
    if (seenCursors.has(next)) throw new Error('OpenCode context pagination did not advance; history is incomplete');
    seenCursors.add(next);
    path = '/api/session/' + encodeURIComponent(sid) + '/message?cursor=' + encodeURIComponent(next) + '&limit=200';
  }
  const chronological = entries.reverse();
  const text = chronological.map((e) => e.block).join(CONTEXT_SEPARATOR);
  let end = 0;
  const historyBoundaries = chronological.map((e, i) => { end += (i ? CONTEXT_SEPARATOR.length : 0) + e.block.length; return { id: e.id, end }; });
  return { sid, text, bytes: whole ? utf8len(text) : used, turns, users, lastTokens, historyMode: mode, historyBoundaries, at: new Date() };
}

function renderCache(msgs) {
  const el = $('cache-info');
  if (!msgs || !msgs.length) { el.textContent = '—'; return; }
  const fmt = (n) => (n || 0).toLocaleString();
  const parts = msgs.slice(0, 5).map((m) => {
    const t = m.tokens || {}; const c = t.cache || {};
    const total = (t.input || 0) + (c.read || 0) + (c.write || 0);
    return total ? Math.round(100 * (c.read || 0) / total) + '%' : '–';
  });
  const t0 = msgs[0].tokens || {}; const c0 = t0.cache || {};
  el.textContent = 'last turn: in ' + fmt(t0.input) + ' · cache read ' + fmt(c0.read) + ' · write ' + fmt(c0.write)
    + ' · out ' + fmt(t0.output) + '   |   cached, newest first: ' + parts.join(' ');
}

// Rows a replay diagnostic kept out of the context: named on the context line, never silent.
function replayExclusions(ctx) {
  const n = Array.isArray(ctx?.exclusions) ? ctx.exclusions.filter(e => /^replay diagnostic/.test(String(e?.reason || ''))).length : 0;
  return n ? ' · ' + n + ' left out (replay diagnostic)' : '';
}
async function refreshContext(announce = true) {
  if (state.backend === 'claude') {
    const frozen = freezeRefineTarget();
    if (frozen.noContext) { state.ctx = null; renderTarget(); return null; }
    try {
      const ctx = await buildContext(frozen);
      if (!sameRefineTarget(frozen)) throw new Error('Claude context target changed');
      state.ctx = ctx;
      $('ctx-info').textContent = kb(ctx.bytes) + ' · ' + ctx.turns + ' prose turns · read ' + ctx.at.toLocaleTimeString() + replayExclusions(ctx);
      $('cache-info').textContent = '— · native metric unavailable';
      if (announce) status('Claude main-path context refreshed', 'ok');
      return ctx;
    } catch (e) {
      if (sameRefineTarget(frozen)) { state.ctx = null; $('ctx-info').textContent = 'Context blocked: ' + e.message; }
      if (announce) status('Claude context blocked: ' + e.message, 'err');
      return null; // refresh is display-only; refine calls buildContext directly and requires success
    }
  }
  const revision = state.targetRevision;
  const t = state.target;
  if (!t) return null;
  try {
    const ctx = await buildContext(t.id);
    if (state.backend === 'claude' || state.targetRevision !== revision || !state.target || state.target.id !== ctx.sid) return null;
    state.ctx = ctx;
    $('ctx-info').textContent = kb(ctx.bytes) + ' · ' + ctx.turns + ' turns (' + ctx.users + ' yours) · read ' + ctx.at.toLocaleTimeString();
    renderCache(ctx.lastTokens);
    if (announce) status('context refreshed', 'ok');
    return ctx;
  } catch (e) {
    if (state.backend === 'claude' || state.targetRevision !== revision) return null;
    $('ctx-info').textContent = 'context failed: ' + e.message;
    return null;
  }
}

// ---------------------------------------------------------------- Refine (ported from the TypingMind widget)
function getProvider() { const p = ls.get(K.provider, 'anthropic'); return PROVIDERS[p] ? p : 'anthropic'; }
function getModels(p) {
  try { const a = JSON.parse(ls.get(K.models[p], 'null')); if (Array.isArray(a) && a.length) return a; } catch (e) {}
  return PROVIDERS[p].models.slice();
}
function getModel(p) { const m = ls.get(K.model[p]); const list = getModels(p); return m && list.includes(m) ? m : list[0]; }
function getThink() { const v = ls.get(K.think, 'medium'); return opcRefineCore.THINK_LEVELS.includes(v) ? v : 'medium'; }
// The higher levels think longer before the first word: give them a longer initial Refine deadline (+30s still adds).
function refineDeadlineMs(level = getThink()) { return { xhigh: 240000, max: 360000 }[level] || 120000; }
function refineOutputPolicy(p, model) {
  return opcRefineCore.refineOutputPolicy(p, model);
}
function getOutputTokens(p = getProvider(), model = getModel(p)) {
  const policy = refineOutputPolicy(p, model);
  const n = Number(ls.get(K.outputTokens, String(policy.defaultTokens)));
  if (!Number.isInteger(n) || n < 1024 || n > policy.maxTokens) throw new Error('Refine output budget must be 1024–' + policy.maxTokens + ' tokens for this provider/model; reset to model default or edit the explicit override');
  return n;
}
function renderOutputSettings() {
  const p = getProvider(), policy = refineOutputPolicy(p, getModel(p));
  $('output-tokens').max = String(policy.maxTokens);
  $('output-tokens').value = ls.get(K.outputTokens, String(policy.defaultTokens));
  $('output-note').textContent = (ls.get(K.outputTokens) === null ? 'Model default: ' : 'Explicit override (preserved): ')
    + 'default ' + policy.defaultTokens + ', allowed up to ' + policy.maxTokens + ' tokens. Thinking may share this cap; fixed Claude thinking is added separately. Actual generated tokens cost money; incomplete output never replaces input.';
}
function thinkClampFor(model, level) {
  return opcRefineCore.thinkClampFor(model, level);
}
function getPart(part) {
  let text = ls.get(part.storage);
  if (text === null) text = part.def;
  for (const req of part.requires) if (!text.includes(req)) return part.def; // a broken override never drops content
  return text;
}
function substitute(text, map) { let out = text; for (const k in map) out = out.split(k).join(map[k]); return out; }
function refineSelectionContext(full, start, end) {
  // The unsent draft around a selection: everything before it and everything after it, never mixed
  // with chat history. Whitespace-only sides are omitted; each side is cropped only past
  // REFINE_DRAFT_LIMIT, keeping the end nearest the selection (before: its tail; after: its head).
  const prefix = full.substring(0, start), suffix = full.substring(end);
  let draftBefore = prefix.trim() ? prefix : '', draftAfter = suffix.trim() ? suffix : '';
  if (utf8len(draftBefore) > REFINE_DRAFT_LIMIT) draftBefore = '…' + utf8Tail(draftBefore, REFINE_DRAFT_LIMIT - utf8len('…'));
  if (utf8len(draftAfter) > REFINE_DRAFT_LIMIT) draftAfter = utf8Head(draftAfter, REFINE_DRAFT_LIMIT - utf8len('…')) + '…';
  return { draftBefore, draftAfter };
}
// The passage Refine cleans: the box's selection, or the whole box when nothing is selected.
function refineSelection(box) {
  const full = box.value;
  let a = box.selectionStart, b = box.selectionEnd;
  const usingSel = a != null && b != null && b > a;
  if (!usingSel) { a = 0; b = full.length; }
  return { full, a, b, usingSel, target: full.substring(a, b) };
}
// @beacon[
//   id=opc-pane@refineRequest,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=The one PC Refine request assembly (chat history with its per-block boundaries, draft-before/after of a selection, prompt parts, dictionary) through opcRefineCore.buildRequest; refine() sends it and the Refine-context View's Full prompt shows it, so the two never drift.,
// ]
function refineRequest(full, a, b, usingSel, ctx) {
  const history = ctx && ctx.text.trim() ? ctx.text : '';
  // Chat history (already sent) and the unsent draft around a selection are separate read-only blocks.
  const { draftBefore, draftAfter } = usingSel ? refineSelectionContext(full, a, b) : { draftBefore: '', draftAfter: '' };
  const part = Object.fromEntries(PARTS.map((x) => [x.id, getPart(x)]));
  // Boundaries describe the history blocks (UTF-16 ends in history); they are empty whenever no history is sent.
  const historyBoundaries = history && Array.isArray(ctx?.historyBoundaries) ? ctx.historyBoundaries : [];
  return opcRefineCore.buildRequest({ transcription: full.substring(a, b), parts: part, history, historyBoundaries,
    draftBefore, draftAfter, dictionaryTerms: dictTerms() });
}
function ensureKey(p) {
  let key = ls.get(K.key[p]);
  if (!key) {
    key = (prompt('Enter your ' + PROVIDERS[p].label + ' API key (stored in this browser only):') || '').trim();
    if (key) ls.set(K.key[p], key);
  }
  return key || null;
}
function dictTerms() { try { const a = JSON.parse(ls.get(K.dict, '[]')); return Array.isArray(a) ? a : []; } catch (e) { return []; } }
function protectedBlock(text) {
  return opcRefineCore.protectedBlock(text, dictTerms());
}
function estimateAnthropicCost(model, usage) {
  return opcRefineCore.estimateAnthropicCost(model, usage);
}

function incompleteResponse(reason) {
  return opcRefineCore.incompleteResponse(reason);
}
function responseText(p, j) {
  return opcRefineCore.responseText(p, j);
}

async function readAnthropicStream(resp, ctrl) {
  return opcRefineCore.readAnthropicStream(resp, ctrl);
}

function refineCallOptions(p, model, apiKey, system, user, ctrl, anthropicUser, cacheKey) {
  return { provider: p, model, apiKey, system, user, controller: ctrl, anthropicUser, cacheKey, routerCache: true,
    // Preserve the PC's settings read on each existing retry, without page reads inside the core.
    get thinking() { return getThink(); }, get outputTokens() { return getOutputTokens(p, model); },
    deadline: () => state.refineTimeoutEnd };
}
async function callOnce(p, model, apiKey, system, user, ctrl, anthropicUser, cacheKey) {
  return opcRefineCore.callOnce(refineCallOptions(p, model, apiKey, system, user, ctrl, anthropicUser, cacheKey));
}

async function callWithRetry(p, model, apiKey, system, user, ctrl, anthropicUser, cacheKey) {
  return opcRefineCore.callWithRetry(refineCallOptions(p, model, apiKey, system, user, ctrl, anthropicUser, cacheKey));
}
// A stable key per logical Refine target (never targetRef or host generation), so an Anthropic prompt cache
// survives a generation resume; a deliberate no-context Refine has its own key.
function refineCacheKey(frozen) {
  if (frozen.noContext) return 'no-context';
  if (frozen.target?.backend === 'opencode') return 'opencode:'+frozen.target.sessionId;
  if (frozen.backend === 'claude') { const t = frozen.target; return t ? 'claude:' + (t.executionId || t.uuid) : 'no-context'; }
  return frozen.target?.id ? 'opencode:' + frozen.target.id : 'no-context';
}

// @beacon[
//   id=opc-pane@opcRefineSettings,
//   slice_labels=opc-pane,
//   kind=span,
//   comment=This editor's Refine settings for Read Aloud in the conversation frame (sessions.js → read-aloud.js): the same provider, model, key, thinking, output cap and whole/budgeted history choice Refine itself uses here, read fresh at each click.,
// ]
globalThis.opcRefineSettings = () => {
  const p = getProvider(), model = getModel(p);
  let outputTokens = null;
  try { outputTokens = getOutputTokens(p, model); } catch (_) { /* an invalid saved cap: the model default, as the core uses */ }
  return { provider: p, model, apiKey: ls.get(K.key[p]) || '', thinking: getThink(), outputTokens,
    historyMode: refineHistoryMode(), budgetKb: Number(ls.get(K.ctxKb, '50')) || 50 };
};
// @beacon-close[
//   id=opc-pane@opcRefineSettings,
// ]

function showRefineRunning(ctrl) {
  const slot = $('refine-slot');
  slot.innerHTML = '<button id="cancel-btn" class="btn btn-cancel">⏹ Cancel <span id="countdown">' + Math.floor(refineDeadlineMs() / 60000) + ':00</span></button> '
    + '<button id="plus30-btn" class="btn btn-quiet">+30s</button>';
  $('cancel-btn').onclick = () => { ctrl.reason_ = 'user'; ctrl.abort(); };
  $('plus30-btn').onclick = () => { state.refineTimeoutEnd += 30000; };
  const tick = setInterval(() => {
    const el = $('countdown');
    if (!el || !state.refineTimeoutEnd) return clearInterval(tick);
    const s = Math.max(0, Math.ceil((state.refineTimeoutEnd - Date.now()) / 1000));
    el.textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
    el.style.color = s <= 30 && Math.floor(Date.now() / 500) % 2 ? 'var(--red)' : '';
  }, 250);
}
// Beneath a completed Refine's status line, at xhigh or max: the red reminder of that costly level; the next status replaces it.
function remindThinking(level) {
  const text = opcRefineCore.thinkingReminder(level), el = $('status');
  if (!text || typeof el.append !== 'function') return;
  const line = document.createElement('span'); line.className = 'think-warn'; line.textContent = text; el.append(line);
}
function restoreRefineButton(label) {
  $('refine-slot').innerHTML = '<button id="refine-btn" class="btn btn-refine">' + (label || '✨ Refine') + '</button>';
  const b = $('refine-btn');
  b.onclick = refine;
  if (label) { b.disabled = true; setTimeout(() => { b.innerHTML = '✨ Refine'; renderTarget(); }, 2000); }
}

// @beacon[
//   id=opc-pane@refine,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=PC Refine adapter assembles its request through refineRequest (the Refine-context View shows the same one) and the shared provider core with live cancel/+30s on a thinking-scaled deadline (2/4/6 min); it passes the core the request's Anthropic text-block user message and a stable per-target cache key, retains context and 256 KiB selection-side guards plus full-original recovery, applies only complete output into unchanged input, and shows the provider's own reported cache reads/writes beside its cost; a completion at xhigh or max adds the red thinking reminder beneath its status.,
// ]
async function refine() {
  if (state.refineAbort) { state.refineAbort.reason_ = 'user'; state.refineAbort.abort(); return; }
  if (state.sending) return status('a Send is running', 'err');
  if (state.backend === 'claude' && (state.targetValidationAbort || state.claudePendingSelection) && ls.get(K.nativeNoContext,'false') !== 'true') return status('Connecting to the selected Manager · original retained','info');
  const frozen = freezeRefineTarget(), targetRevision = frozen.targetRevision;
  if (frozen.backend === 'claude' && !frozen.target && !frozen.noContext) return status('Claude Refine unavailable: select a main history target or explicitly omit context; text retained', 'err');
  const box = $('transcript');
  const { full, a, b, usingSel, target } = refineSelection(box);
  if (!full.trim()) return status('nothing to refine', 'err');
  if (!target.trim()) return status('the selection is empty', 'err');
  const p = getProvider(), model = getModel(p);
  const apiKey = ensureKey(p);
  if (!apiKey) return;
  if (!retainOriginal(full, 'Refine')) return;
  const ctrl = new AbortController();
  state.refineAbort = ctrl;
  state.refineTimeoutEnd = Date.now() + refineDeadlineMs();
  box.readOnly = true; $('send-btn').disabled = true;
  showRefineRunning(ctrl);
  const t0 = Date.now();
  let doneLabel = null;
  try {
    await copyText(target);
    status('reading context…', 'info');
    if (!sameRefineTarget(frozen, true)) throw new Error('Refine target/draft changed; original retained');
    const ctx = frozen.backend === 'claude' ? (frozen.noContext ? null : await buildContext(frozen, ctrl.signal)) :
      (frozen.target ? (await refreshContext(false)) : null);
    await revalidateRefineTarget(frozen, ctrl.signal, ctx);
    if (frozen.backend === 'claude' && ctx) {
      state.ctx = ctx;
      $('ctx-info').textContent = kb(ctx.bytes) + ' · ' + ctx.turns + ' prose turns · ' + (usingSel ? 'conversation context read' : 'submitted context') + replayExclusions(ctx);
    }
    if (ctrl.signal.aborted) { const e = new Error('Refine canceled.'); e.userCanceled = true; throw e; }
    const request = refineRequest(full, a, b, usingSel, ctx);
    const { history, draftBefore, draftAfter } = request;
    status('✨ refining ' + (usingSel ? 'selection' : 'everything') + ' via ' + PROVIDERS[p].label + ' · ' + model
      + ' · output cap ' + getOutputTokens(p, model) + ' tokens (thinking may share it)'
      + (usingSel ? ' · chat history ' + kb(utf8len(history)) + ' · draft before ' + kb(utf8len(draftBefore)) + ' · after ' + kb(utf8len(draftAfter))
        + (frozen.noContext ? ' · conversation context omitted by explicit choice' : '') :
        (ctx ? ' · context ' + kb(ctx.bytes) : frozen.backend === 'claude' ? ' · conversation context omitted by explicit choice' : ' · no context')), 'info');
    const res = await callWithRetry(p, model, apiKey, request.system, request.user, ctrl, request.anthropicUser, refineCacheKey(frozen)), think = getThink();
    await revalidateRefineTarget(frozen, ctrl.signal, ctx);
    const cleaned = (res.text || '').replace(/\s+$/, '');
    if (!cleaned.trim()) throw incompleteResponse('empty text');
    if (ctrl.signal.aborted) { const e = new Error('Refine canceled.'); e.userCanceled = true; throw e; }
    const copied = await copyText(cleaned);
    await revalidateRefineTarget(frozen, ctrl.signal, ctx);
    if (ctrl.signal.aborted) { const e = new Error('Refine canceled.'); e.userCanceled = true; throw e; }
    showCost(res.cost, res.estimated, Date.now() - t0, res.cache);
    if (box.value !== full) {
      status('input changed meanwhile: original/result not applied' + (copied ? '; result on clipboard' : '; clipboard unavailable'), 'err');
      remindThinking(think);
      doneLabel = '⚠ Selection changed';
    } else {
      box.value = full.substring(0, a) + cleaned + full.substring(b); draftChanged();
      box.focus({ preventScroll: true });
      box.setSelectionRange(a, a + cleaned.length);
      const draftSaved = saveDraft();
      status('✨ refined ✓ · original in recovery' + (copied ? '' : ' · clipboard unavailable')
        + (draftSaved ? '' : ' · refined draft NOT saved; keep page open/save externally'), draftSaved ? 'ok' : 'err');
      remindThinking(think);
      doneLabel = usingSel ? '✓ Replaced selection' : '✓ Refined';
    }
  } catch (err) {
    if (err.userCanceled) { status('refine canceled', 'info'); doneLabel = '✓ Canceled'; }
    else if (err.status === 401 || err.status === 403) { ls.del(K.key[p]); status('API key rejected (' + err.status + '): cleared, refine again to re-enter it', 'err'); }
    else { status('refine failed: ' + err.message, 'err'); doneLabel = '✗ Failed'; }
  } finally {
    state.refineAbort = null; state.refineTimeoutEnd = null;
    box.readOnly = false; $('send-btn').disabled = state.backend === 'claude';
    restoreRefineButton(doneLabel);
    renderTarget();
  }
}

// The core formats the provider's own reported cache tokens; routers report none, and undefined adds nothing.
function refineCacheSummary(cache) {
  return typeof opcRefineCore.cacheSummary === 'function' ? (opcRefineCore.cacheSummary(cache) || '') : '';
}
function showCost(cost, estimated, ms, cache) {
  let total = parseFloat(ls.get(K.totalCost, '0')) || 0;
  if (typeof cost === 'number' && !isNaN(cost)) { total += cost; ls.set(K.totalCost, String(total)); }
  const c = typeof cost === 'number' ? (estimated ? '~$' : '$') + (cost < 0.01 ? cost.toFixed(5) : cost.toFixed(4)) : '—';
  const cacheLine = refineCacheSummary(cache);
  $('cost-info').textContent = 'last: ' + c + ' · ' + (ms / 1000).toFixed(1) + 's'
    + (cacheLine ? ' · ' + cacheLine : '') + '   total: $' + total.toFixed(2);
}

// @beacon[
//   id=opc-pane@showRefineView,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Read the current whole/budgeted context as Refine would (not an older cached selection), then show chat/draft surroundings or the exact Full prompt from refineRequest; open at the end and send nothing.,
// ]
async function showRefineView() {
  const ctx = await refreshContext(false);
  const noContext = state.backend === 'claude' && ls.get(K.nativeNoContext, 'false') === 'true';
  const missing = state.backend === 'claude' ? (noContext ? 'Conversation context omitted by explicit choice' : 'Context unavailable; required-context Refine is blocked') : '(no session)';
  const { full, a, b, usingSel, target } = refineSelection($('transcript'));
  const request = refineRequest(full, a, b, usingSel, ctx), p = getProvider();
  $('ctx-dialog').showModal(); // Escape closes a modal dialog natively.
  opcRefineView.render($('ctx-body'), {
    mode: state.refineViewMode, history: ctx ? ctx.text : null, historyNote: missing,
    historyDetail: ctx ? ctx.turns + (state.backend === 'claude' ? ' prose turns' : ' turns (' + ctx.users + ' yours)') + ' · read ' + ctx.at.toLocaleTimeString() + replayExclusions(ctx) : '',
    usingSelection: usingSel, draftBefore: request.draftBefore, draftAfter: request.draftAfter,
    // A required context that could not be read blocks Refine, so there is no prompt to show.
    request: state.backend === 'claude' && !ctx && !noContext ? null : request, promptNote: missing,
    promptDetail: PROVIDERS[p].label + ' · ' + getModel(p) + ' · thinking ' + getThink() + (target.trim() ? '' : ' · nothing to refine yet'),
  }, { historyId: 'ctx-pre', onMode: (mode) => { state.refineViewMode = mode; } });
}

// ---------------------------------------------------------------- Native Manager (separate from OpenCode)
function isClaudeManager(t = state.claudeTarget) {
  return !!t && t.source === 'host' && t.role === 'Manager' && t.executionId === 'cc-' + t.uuid &&
    Number.isSafeInteger(t.generation) && t.generation > 0 && t.capabilities?.nativeInput === true && t.capabilities?.controls === true;
}
// @beacon[
//   id=opc-pane@reconnectableManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=A registered Manager conversation with no live process (collected = sleeping, or proved ended) whose Send reconnects it; history, other roles and ownership-unknown never qualify.,
// ]
function reconnectableManager(t = state.claudeTarget) {
  return !!t && t.source === 'host' && t.role === 'Manager' && t.executionId === 'cc-' + t.uuid &&
    Number.isSafeInteger(t.generation) && t.generation > 0 &&
    (t.lifecycle === 'sleeping' || t.lifecycle === 'ended') && t.capabilities?.reconnect === true;
}
function sleepingManagerText(t) {
  return 'This Manager is sleeping (reconnects on Send).' +
    (t?.successor ? ' The live Manager is '+t.successor.title+'.' : '');
}
function endedManagerText(t) {
  // The host's own reason, in plain words, when Send cannot reconnect this conversation.
  if (t?.capabilities?.reconnect !== true && typeof t?.reconnectBlocked === 'string' && t.reconnectBlocked)
    return 'This Manager has ended and Send cannot reconnect it: '+t.reconnectBlocked+(t?.successor ? ' The live Manager is '+t.successor.title+'.' : '');
  if (t?.lifecycle === 'sleeping') return sleepingManagerText(t);
  return 'This Manager has ended. '+(t?.successor ? 'The live Manager is '+t.successor.title+'.' : 'No live Manager in this workspace is ready yet; the list refreshes automatically.')+' Send to it reconnects the conversation.';
}
// ---------------------------------------------------------------- OpenCode Manager device adapter (the same editor/viewer)
let ocManagerJournal = null;
function openCodeManagerApi() {
  return ocManagerJournal ||= opcOpenCodeManager.create({prefix:'pane_manager_v1'});
}
// @beacon[
//   id=opc-pane@acceptOpenCodeManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=GET-only root-Manager validation by full OpenCode id and server creation/directory witness, independent of inventory age; retarget revisions and direct-frame action order win, drafts and native legacy keys remain intact.,
// ]
async function acceptOpenCodeManager(sid, expectedRevision=state.targetRevision, current=()=>true, revalidate=false, saved=null) {
  if (expectedRevision !== state.targetRevision) throw new Error('target revision changed; not applied');
  state.targetValidationAbort?.abort();
  const ctrl=new AbortController(), validation=++state.targetValidation;
  state.targetValidationAbort=ctrl; renderTarget();
  try {
    const data=await openCodeManagerApi().validate(sid,ctrl.signal);
    if (saved && !openCodeManagerApi().same(saved,data.target)) throw new Error('Saved OpenCode Manager witness changed; select it deliberately');
    if (ctrl.signal.aborted || validation !== state.targetValidation || expectedRevision !== state.targetRevision || !current()) throw new Error('newer target action won; not applied');
    if (!revalidate) {
      ls.set(K.managerTarget,JSON.stringify({backend:'opencode',target:data.target,selection:crypto.randomUUID()}));
      ls.set(K.backend,'claude'); invalidateTarget(); state.backend='claude'; state.target=null;
      if (state.claudeLaunch) { state.claudeLaunch=null; saveLaunch(null); } // explicit runtime selection wins over an older native launch
    }
    state.ocManagerTarget=data.target; state.ocManager=data; state.ocManagerError=null; state.ocManagerCatalog=null;
    state.claudePendingSelection=null; state.claudeTargetError=null;
    state.nativeTargetStatus='Ready: sending directly to OpenCode Manager '+data.title;
    void loadOpenCodeManagerCatalog(data.target);
    return data.target;
  } finally { if (state.targetValidationAbort === ctrl) state.targetValidationAbort=null; renderTarget(); }
}
async function loadOpenCodeManagerCatalog(target=state.ocManagerTarget) {
  if (!target) return;
  try {
    const data=await openCodeManagerApi().request('manager-models?'+new URLSearchParams({id:target.sessionId}));
    if (openCodeManagerApi().same(target,state.ocManagerTarget)) { state.ocManagerCatalog=data.models; renderOpenCodeManager(true); }
  } catch (e) { if (openCodeManagerApi().same(target,state.ocManagerTarget)) state.ocManagerError='Model catalog: '+e.message; }
}
function fillOpenCodeModels(select, variants, catalog, model=null) {
  if (!select || !variants) return;
  const key=m=>m.providerID+'/'+m.id;
  const chosen=model ? key(model) : select.value;
  if (catalog) {
    select.replaceChildren(...catalog.map(m=>new Option(key(m)+(m.name && m.name !== m.id ? ' · '+m.name : ''),key(m))));
    if ([...select.options].some(o=>o.value === chosen)) select.value=chosen;
  }
  const hit=(catalog || []).find(m=>key(m) === select.value), previous=variants.value;
  const levels=Array.isArray(hit?.variants) ? hit.variants : Object.keys(hit?.variants || {});
  variants.replaceChildren(new Option('Default',''),...levels.map(v=>new Option(v,v)));
  variants.value=model && key(model) === select.value ? model.variant || '' : levels.includes(previous) ? previous : '';
}
function selectedOpenCodeModel(select,variants) {
  const value=select?.value || '', slash=value.indexOf('/');
  if (slash<1) throw new Error('Choose a catalog model');
  return {providerID:value.slice(0,slash),id:value.slice(slash+1),...(variants?.value ? {variant:variants.value} : {})};
}
function renderOpenCodeManager(fresh=false) {
  const t=state.ocManagerTarget, panel=$('opencode-manager-panel'); if (!t || !panel) return;
  const data=state.ocManager, model=data?.model;
  $('opencode-effective').textContent=(model ? model.providerID+'/'+model.id+' · thinking '+(model.variant || 'Default')+' · session readback' : 'Model readback unknown')+
    ' · affects future calls, not a running call';
  $('opencode-context').textContent=opcOpenCodeManager.contextLabel(data?.context);
  $('opencode-observation').textContent=(data?.busy === true ? 'Working · Send steers at the next step boundary' : data?.busy === false ? 'Idle · Send goes directly to this Manager' : 'Activity unknown · Send remains available')+
    (state.ocManagerError ? ' · '+state.ocManagerError : '');
  const quick=$('claude-interrupt-now'); if (quick) { quick.hidden=false; quick.disabled=false; quick.classList?.toggle('busy',data?.busy === true); quick.title='Interrupt this OpenCode Manager once; your draft stays'; }
  const choices=t.sessionId+'/'+t.witness;
  if (state.ocManagerCatalog && (fresh || $('opencode-model').dataset.target !== choices)) {
    fillOpenCodeModels($('opencode-model'),$('opencode-variant'),state.ocManagerCatalog,model);
    $('opencode-model').dataset.target=choices;
  }
  let entries=[]; try { entries=openCodeManagerApi().entries(); } catch (e) { state.ocManagerError=e.message; }
  $('opencode-receipts').textContent=entries.filter(r=>r.kind !== 'launch').map(r=>
    (r.kind === 'send' ? 'Send ' : 'Control ')+r.operationId+' · '+r.intent.target.sessionId+' · '+
    (r.receipt?.state || r.phase)+(r.releasedAt ? ' · released by Dan, never resent' : '')+
    (r.receipt?.detail ? ' · '+r.receipt.detail : '')+(r.error ? ' · '+r.error : '')).join('\n') || 'No pane OpenCode receipt';
  let pending=null; try { pending=openCodeManagerApi().pending(t); } catch (e) { state.ocManagerError=e.message; }
  $('opencode-send-release').hidden=!pending; $('opencode-send-release').disabled=!!state.sending;
}
// @beacon[
//   id=opc-pane@applyOpenCodeSendReceipt,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Exact native pending/delivered evidence can clear only the unchanged desktop page/target/draft plus exact persisted selection/draft markers; originals and old-target journals stay retained, never a reload clear or a second Send.,
// ]
function applyOpenCodeSendReceipt(record) {
  const api=openCodeManagerApi();
  if (!api.acknowledged(record) || record.handledAck) return record;
  const original=record.intent.original, frozen=record.frozen;
  let note='Exact '+record.receipt.state+' receipt · original retained';
  const unchanged=frozen && sameRefineTarget(frozen,true) && $('transcript').value === original &&
    record.savedTarget === ls.get(K.managerTarget) && record.savedDraft === ls.get(K.managerDraft);
  if (unchanged && !record.draftSaveFailed) {
    try {
      ls.set(K.lastSent,record.intent.submitted);
      if (clearSavedDraft(original)) { $('transcript').value=''; draftChanged(); clearTimeout(draftTimer); note+=' · unchanged draft cleared'; }
      else note+=' · stored draft changed; kept';
    } catch (e) { note+=' · draft kept: '+e.message; }
  } else note+=' · draft/page/target changed; retained draft WAS sent, do not resend';
  record=api.save({...record,handledAck:true,note});
  if (api.same(record.intent.target,state.ocManagerTarget) && frozen?.targetRevision === state.targetRevision)
    status('OpenCode '+note,'ok','opencode:'+record.operationId);
  return record;
}
// @beacon[
//   id=opc-pane@sendOpenCodeManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Desktop direct OpenCode Manager Send journals complete original/submitted/target/native id before one steer POST; busy and unknown context never refuse it. Exact receipt plus unchanged page/target/draft and persisted markers alone clear; a lost reply only queries.,
// ]
async function sendOpenCodeManager() {
  const api=openCodeManagerApi(), original=$('transcript').value;
  if (!original.trim()) return status('nothing to send','err');
  if (state.refineAbort || state.sending || state.targetValidationAbort) return;
  const frozen=freezeRefineTarget(), target=frozen.target;
  let prior;
  try { prior=api.pending(target); }
  catch (e) { return status('OpenCode recovery: '+e.message+' · draft kept; nothing sent','err'); }
  if (prior) { prior=await api.poll(prior); applyOpenCodeSendReceipt(prior); if (api.pending(target)) return status('This OpenCode Send is uncertain; query its receipt or use I checked — release it. Nothing resent.','info'); }
  clearTimeout(draftTimer); const draftSaved=saveDraft();
  state.sending=true; renderTarget();
  try {
    const observed=await api.validate(target.sessionId);
    if (!api.same(observed.target,target) || !sameRefineTarget(frozen,true) || $('transcript').value !== original) throw new Error('Manager or draft changed; nothing sent');
    const record=await api.begin('send',{target,original,submitted:original.trim(),delivery:'steer'},
      {frozen,savedTarget:ls.get(K.managerTarget),savedDraft:ls.get(K.managerDraft),draftSaveFailed:!draftSaved});
    if (!sameRefineTarget(frozen,true) || $('transcript').value !== original) { api.release(record); throw new Error('Manager or draft changed before POST; prepared intent released, never sent'); }
    const result=await api.execute(record);
    if (api.acknowledged(result)) applyOpenCodeSendReceipt(result);
    else status('OpenCode Send '+(result.receipt?.state || 'uncertain')+' · full original retained; querying this id only','info','opencode:'+result.operationId);
  } catch (e) { status('OpenCode Send: '+e.message+' · draft retained; any saved intent is query-only','err'); }
  finally { state.sending=false; renderTarget(); }
}
// @beacon[
//   id=opc-pane@controlOpenCodeManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Desktop OpenCode model/variant and Interrupt intents use persisted caller records and one native POST, including while busy; setters verify session readback, uncertain interrupts never repeat, and controls never change Refine settings or drafts.,
// ]
async function controlOpenCodeManager(operation) {
  const api=openCodeManagerApi(), target=state.ocManagerTarget, revision=state.targetRevision;
  if (!target) return;
  const op=operation === 'interrupt' ? 'interrupt' : 'set_model';
  try {
    const model=op === 'interrupt' ? null : selectedOpenCodeModel($('opencode-model'),$('opencode-variant'));
    const record=await api.begin('control',{target,operation:op,model},{targetRevision:revision});
    if (revision !== state.targetRevision || !api.same(target,state.ocManagerTarget)) { api.release(record); return status('Target changed; control not submitted','info'); }
    const result=await api.execute(record);
    status('OpenCode '+op+' · '+(result.receipt?.state || 'uncertain')+' · '+(result.receipt?.detail || 'readback pending; never automatically repeated'),'info');
    await pollOpenCodeManager();
    if (op !== 'interrupt') { state.ocManagerCatalog=null; await loadOpenCodeManagerCatalog(target); }
  } catch (e) { status('OpenCode control: '+e.message+' · intent retained, never automatically repeated','err'); }
}
// @beacon[
//   id=opc-pane@pollOpenCodeManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Desktop OpenCode observation reconciles saved intents query-only across reload/restart and retains target-qualified receipts; late evidence cannot clear another Manager, an edited draft or a new page. Manager context/settings are separate from account quota.,
// ]
async function pollOpenCodeManager() {
  if (state.ocManagerObserving || !state.ocManagerTarget) return;
  state.ocManagerObserving=true;
  const api=openCodeManagerApi(), target=state.ocManagerTarget;
  try {
    try { const data=await api.validate(target.sessionId); if (api.same(target,state.ocManagerTarget) && api.same(target,data.target)) { state.ocManager=data; state.ocManagerError=null; } }
    catch (e) { if (api.same(target,state.ocManagerTarget)) state.ocManagerError=e.message; }
    for (const record of api.entries()) {
      if (record.releasedAt || ['failed','conflict','completed','created','applied'].includes(record.receipt?.state)) continue;
      const result=await api.poll(record);
      if (result.kind === 'send') applyOpenCodeSendReceipt(result);
    }
    await adoptOpenCodeLaunch();
  } catch (e) { state.ocManagerError=e.message;
  } finally { state.ocManagerObserving=false; renderTarget(); }
}
// ---------------------------------------------------------------- New Manager (the escape hatch)
// ＋ New Manager sits at the top of the conversation view's session list, always visible: its
// controls are the same-origin sessions frame's DOM, driven from here like the rest of the editor.
function nm(id) {
  const own = $(id); if (own) return own;
  try { return $('claude-sessions-frame')?.contentDocument?.getElementById?.(id) || null; } catch (_) { return null; }
}
// @beacon[
//   id=opc-pane@wireNewManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Wires the existing New Manager form in the same-origin sessions frame, including native Claude or catalog-backed OpenCode runtime/model/persona choices, ↳ Sub-session and the default first messages; launch and target adoption stay in the editor.,
// ]
function wireNewManager() {
  const button = nm('claude-new-manager'); if (!button || button.dataset?.wired === '1') return;
  if (button.dataset) button.dataset.wired = '1';
  button.onclick = () => void openNewManager();
  if (nm('claude-new-launch')) nm('claude-new-launch').onclick = () => void launchNewManager();
  if (nm('claude-new-cancel')) nm('claude-new-cancel').onclick = () => { nm('claude-new-manager-form').hidden = true; };
  if (nm('claude-new-sub')) nm('claude-new-sub').onclick = () => void openNewSub();
  if (nm('claude-new-sub-send')) nm('claude-new-sub-send').onclick = () => void launchNewSub();
  if (nm('claude-new-sub-cancel')) nm('claude-new-sub-cancel').onclick = () => { nm('claude-new-sub-form').hidden = true; };
  if (nm('claude-first-save')) nm('claude-first-save').onclick = () => void saveFirstMessages(false);
  if (nm('claude-first-restore')) nm('claude-first-restore').onclick = () => void saveFirstMessages(true);
  if (nm('manager-new-runtime')) nm('manager-new-runtime').onchange = () => void newManagerRuntime();
  if (nm('opencode-new-model')) nm('opencode-new-model').onchange = () => fillOpenCodeModels(nm('opencode-new-model'),nm('opencode-new-variant'),state.ocLaunchCatalog);
  for (const id of ['claude-new-model','claude-new-effort']) if (nm(id)) nm(id).onchange = nm(id).oninput = e => { if (e.target.dataset) e.target.dataset.chosen = '1'; };
  if (nm('claude-new-name')) nm('claude-new-name').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.ctrlKey) { e.preventDefault(); e.stopPropagation?.(); void launchNewManager(); } });
  renderNewManager();
}
function newManagerStatus(text, error = false) {
  const node = nm('claude-new-status'); if (!node) return;
  node.textContent = text || ''; if (node.dataset) node.dataset.state = error ? 'error' : 'info';
}
function saveLaunch(launch) {
  try { if (launch) ls.set(K.claudeLaunch, JSON.stringify({uuid:launch.uuid,name:launch.name,started:launch.started,phase:launch.phase,
    ...(typeof launch.opening === 'string' ? {opening:launch.opening} : {})}));
    else ls.del(K.claudeLaunch); } catch (_) { /* cosmetic resume only; the inventory still shows the row */ }
}
function restoreLaunch() {
  try {
    const saved = JSON.parse(ls.get(K.claudeLaunch, 'null'));
    if (saved && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(saved.uuid || '') &&
        typeof saved.name === 'string' && saved.phase === 'waiting' && Date.now() - saved.started < 15 * 60 * 1000)
      state.claudeLaunch = {...saved, ...(typeof saved.opening === 'string' ? {} : {opening:undefined})};
    else if (saved) saveLaunch(null);
  } catch (_) { saveLaunch(null); }
}
function notifySessions(message) {
  try { const frame = $('claude-sessions-frame'); frame?.contentWindow?.postMessage?.({schemaVersion:1,...message}, globalThis.location?.origin || '*'); }
  catch (_) { /* the list also refreshes on its own cadence */ }
}
function renderNewManager() {
  const row = nm('claude-new-manager-row'); if (!row) return;
  row.hidden = state.backend !== 'claude';
  const launching = nm('manager-new-runtime')?.value === 'opencode' ? state.ocLaunching : state.claudeLaunch?.phase === 'launching';
  if (nm('claude-new-manager')) nm('claude-new-manager').disabled = state.claudeLaunch?.phase === 'launching';
  if (nm('claude-new-launch')) nm('claude-new-launch').disabled = launching || !!state.sending || !!state.refineAbort;
  if (nm('claude-new-sub')) nm('claude-new-sub').disabled = state.claudeLaunch?.phase === 'launching';
  if (nm('claude-new-sub-send')) nm('claude-new-sub-send').disabled = launching || !!state.sending || !!state.refineAbort;
}
// ＋ New Manager and ↳ Sub-session share ONE set of runtime/model/thinking controls (#manager-new-choices): it moves
// into the box being opened, so a sub-session offers exactly New Manager's choices. One box is open at a time.
function placeManagerChoices(slotId) {
  const choices = nm('manager-new-choices'), slot = nm(slotId);
  if (choices && typeof slot?.appendChild === 'function' && choices.parentNode !== slot) slot.appendChild(choices);
}
async function openNewManager() {
  const form = nm('claude-new-manager-form'); if (!form || state.backend !== 'claude') return;
  if (nm('claude-new-sub-form')) nm('claude-new-sub-form').hidden = true;
  placeManagerChoices('claude-new-choices-slot');
  form.hidden = false;
  const busy = isClaudeManager() && state.claudeManager?.liveState ? claudeBusy(state.claudeManager.liveState) : '';
  newManagerStatus(busy ? 'Note: '+state.claudeTarget.title+' is busy ('+busy+'). It keeps running; the new Manager is a separate conversation.' :
    'A fresh conversation with today’s Manager prompt; its first message goes out when it is ready. The current Manager keeps running untouched.');
  void loadFirstMessages(true).catch(e => newManagerStatus('Default first messages unavailable ('+e.message+'); a launch now sends none.', true));
  try {
    const d = await claudeRequest('/api/claude/launch-defaults');
    if (!nm('claude-new-name').value) nm('claude-new-name').value = typeof d.name === 'string' ? d.name : '';
    if (typeof d.model === 'string' && !nm('claude-new-model').dataset?.chosen) nm('claude-new-model').value = d.model;
    if (typeof d.effort === 'string' && !nm('claude-new-effort').dataset?.chosen) nm('claude-new-effort').value = d.effort;
  } catch (e) { if (!nm('claude-new-name').value) newManagerStatus('Suggested name unavailable ('+e.message+'); type one, e.g. Manager-Session-183.'); }
  if (nm('manager-new-runtime')?.value === 'opencode') await newManagerRuntime();
  nm('claude-new-name').focus?.();
}
// @beacon[
//   id=opc-pane@newManagerRuntime,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=The runtime choice shared by ＋ New Manager and ↳ Sub-session selects native Claude fields or real OpenCode catalog/persona/variant choices (a chosen persona, model and level survive a re-read); workspace MCP status is informational and changing it never changes the current Manager. False when the catalog read failed.,
// ]
async function newManagerRuntime() {
  const openCode=nm('manager-new-runtime')?.value === 'opencode';
  renderNewManager();
  for (const id of ['claude-new-model','claude-new-effort']) { const label=nm(id)?.closest?.('label'); if (label) label.hidden=openCode; }
  if (nm('opencode-new-settings')) nm('opencode-new-settings').hidden=!openCode;
  if (!openCode) return true;
  try {
    const data=await openCodeManagerApi().request('launch-defaults'); state.ocLaunchCatalog=data.models;
    fillOpenCodeModels(nm('opencode-new-model'),nm('opencode-new-variant'),data.models);
    const agent=nm('opencode-new-agent'), previous=agent?.value;
    agent?.replaceChildren(...data.agents.map(a=>new Option(a,a)));
    if (agent && data.agents.includes(previous)) agent.value=previous;
    newManagerStatus('OpenCode · explicit catalog model · MCP '+(data.mcp ? data.mcp.map(m=>(m.name || m.id)+': '+m.status).join(', ') : 'state unknown (not a launch gate)'));
    return true;
  } catch (e) { newManagerStatus('OpenCode catalog: '+e.message,true); return false; }
}
// @beacon[
//   id=opc-pane@launchOpenCodeManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Desktop New Manager OpenCode runtime persists the launch intent (and its first message, if any) and server-clock ses id before one create; real catalog model/variant and installed persona are explicit, and lost replies only query the same launch. given = {name, opening} from ↳ Sub-session.,
// ]
async function launchOpenCodeManager(given={}) {
  if (state.ocLaunching) return false;
  state.ocLaunching=true; renderNewManager();
  try {
    const name=String(given.name ?? nm('claude-new-name').value ?? '').trim();
    if (!name) throw new Error('Type a name first');
    const model=selectedOpenCodeModel(nm('opencode-new-model'),nm('opencode-new-variant'));
    const opening=given.opening ?? await newManagerOpening(name);
    const record=await openCodeManagerApi().begin('launch',{name,agent:nm('opencode-new-agent').value,model},
      {launchSelection:ls.get(K.managerTarget),launchRevision:state.targetRevision,launchPage:state.pageEpoch,...(opening ? {opening} : {})});
    const result=await openCodeManagerApi().execute(record);
    newManagerStatus('OpenCode '+name+' · '+(result.receipt?.state || 'uncertain')+(opening ? ' · its first message goes out once it is selected' : ' · nothing sent'));
    await adoptOpenCodeLaunch(result);
    return true;
  } catch (e) { newManagerStatus('OpenCode launch: '+e.message+' · saved intents are query-only; current target and draft kept',true); return false; }
  finally { state.ocLaunching=false; renderTarget(); }
}
// @beacon[
//   id=opc-pane@adoptOpenCodeLaunch,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Query-only desktop launch recovery adopts the exact created native session if no newer selection token/revision won, including across reloads; never repeats create, sends the launch's saved first message once after the adoption is recorded, and selecting another Manager preserves that choice.,
// ]
async function adoptOpenCodeLaunch(given=null) {
  if (!globalThis.opcOpenCodeManager || state.ocAdopting) return;
  state.ocAdopting=true;
  const api=openCodeManagerApi();
  try {
  for (let record of given ? [given] : api.entries('launch')) {
    if (record.adopted || record.releasedAt || record.receipt?.state === 'conflict' || record.receipt?.state === 'failed') continue;
    if (!api.acknowledged(record)) record=await api.poll(record);
    if (!api.acknowledged(record) || record.receipt.state !== 'created') continue;
    if (record.launchSelection !== ls.get(K.managerTarget) ||
        record.launchPage === state.pageEpoch && record.launchRevision !== state.targetRevision) {
      api.save({...record,adopted:true,notSelected:'A newer deliberate target selection won'}); continue;
    }
    if (state.sending || state.refineAbort || state.targetValidationAbort) continue;
    await acceptOpenCodeManager(record.nativeId,state.targetRevision,()=>record.launchSelection === ls.get(K.managerTarget));
    api.save({...record,adopted:true});
    if (nm('claude-new-manager-form')) nm('claude-new-manager-form').hidden=true;
    notifySessions({type:'browse-opencode',sessionId:record.nativeId});
    const opening=typeof record.opening === 'string' && record.opening.trim() ? record.opening : null;
    status('OpenCode Manager '+record.intent.name+' created and selected · '+(opening ? 'sending its first message' : 'no opening turn')+'; your draft is kept','ok');
    if (opening) void sendOpeningOpenCode(record.nativeId,opening);
  }
  } catch (e) { newManagerStatus('OpenCode launch observation: '+e.message+' · no intent repeated',true); }
  finally { state.ocAdopting=false; }
}
// @beacon[
//   id=opc-pane@launchNewManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=The existing New Manager form dispatches its explicit runtime: native Claude keeps its caller-UUID host launch/adoption (Opus 5.5 / xhigh unless chosen); OpenCode uses a persisted catalog-backed native creation intent. Each launch carries its first message (the saved default, or ↳ Sub-session's text via given = {name, opening}), sent once it is adopted; current Managers remain untouched.,
// ]
async function launchNewManager(given={}) {
  if (nm('manager-new-runtime')?.value === 'opencode') return launchOpenCodeManager(given);
  if (state.claudeLaunch?.phase === 'launching') return false;
  // Free entry: any model the host's own launch rule accepts (a new release needs no pane change).
  const name = String(given.name ?? nm('claude-new-name').value ?? '').trim(), model = String(nm('claude-new-model').value || '').trim() || 'claude-opus-5-5',
    effort = nm('claude-new-effort').value || 'xhigh';
  if (!name) { newManagerStatus('Type a name first, e.g. Manager-Session-183.', true); return false; }
  if (!globalThis.crypto?.randomUUID) { newManagerStatus('This browser cannot allocate a session UUID; nothing was launched.', true); return false; }
  const launch = {uuid:crypto.randomUUID(), name, model, effort, phase:'launching', started:Date.now()};
  state.claudeLaunch = launch; renderNewManager();
  const opening = given.opening ?? await newManagerOpening(name);
  if (opening) launch.opening = opening;
  saveLaunch(launch);
  const previous = isClaudeManager() ? state.claudeTarget.title : null;
  newManagerStatus('Launching '+name+' …'+(previous ? ' '+previous+' keeps running untouched.' : ''));
  try {
    const data = await claudeRequest('/api/claude/launch-manager',
      {schemaVersion:1,backend:'claude',nativeUuid:launch.uuid,name,model,effort}, 150000);
    if (data.uuid !== launch.uuid || data.executionId !== 'cc-'+launch.uuid) throw new Error('the launch answered a different session; nothing selected');
    launch.phase = 'waiting';
    newManagerStatus(name+' launched'+(data.hostStarted ? ' (the Claude host was not running, so it was started first)' : '')+
      ' · starting up; it becomes the Send target automatically when ready'+(opening ? ' and its first message goes out then' : '')+'. Your draft is kept.');
  } catch (e) {
    if (e.status === 502 || e.name === 'AbortError') {
      // The request may be with the host already; the browser UUID makes a repeat harmless.
      launch.phase = 'waiting';
      newManagerStatus('No reply yet ('+e.message+'). If the launch went through, '+name+' appears in the list and is selected automatically.');
    } else {
      launch.phase = 'failed';
      newManagerStatus('New Manager not launched: '+e.message+' · nothing changed; your draft and current target are kept.', true);
    }
  }
  if (state.claudeLaunch === launch) { saveLaunch(launch.phase === 'waiting' ? launch : null); if (launch.phase !== 'waiting') state.claudeLaunch = null; }
  notifySessions({type:'host-launch'});
  renderTarget();
  if (launch.phase === 'waiting') void adoptLaunchedManager();
  return launch.phase === 'waiting';
}
// The order's helpers (manager-order.js): the sessions frame's copy, which also lists the Managers.
function managerOrder() {
  try { return $('claude-sessions-frame')?.contentWindow?.opcManagerOrder || globalThis.opcManagerOrder || null; }
  catch (_) { return globalThis.opcManagerOrder || null; }
}
// Dan's default first messages (GET/POST /api/claude/first-messages): one saved copy for desktop and phone, read
// fresh for every launch (the last good copy if that read fails); fill puts them in the form's editor.
async function loadFirstMessages(fill = false) {
  let data;
  try { data = await claudeRequest('/api/claude/first-messages'); }
  catch (e) { if (state.firstMessages && !fill) return state.firstMessages; throw e; }
  if (typeof data?.manager !== 'string' || typeof data.subSession !== 'string') throw new Error('first-message reply schema invalid');
  state.firstMessages = data;
  if (fill && nm('claude-first-manager')) nm('claude-first-manager').value = data.manager;
  if (fill && nm('claude-first-sub')) nm('claude-first-sub').value = data.subSession;
  return data;
}
async function saveFirstMessages(restore) {
  try {
    const body = restore ? {manager:'', subSession:''} : {manager:nm('claude-first-manager').value, subSession:nm('claude-first-sub').value};
    const data = await claudeRequest('/api/claude/first-messages', {schemaVersion:1, backend:'claude', ...body});
    if (typeof data?.manager !== 'string' || typeof data.subSession !== 'string') throw new Error('first-message reply schema invalid');
    state.firstMessages = data; nm('claude-first-manager').value = data.manager; nm('claude-first-sub').value = data.subSession;
    newManagerStatus(restore ? 'Default first messages restored.' : 'Default first messages saved, for the desktop and the phone.');
  } catch (e) { newManagerStatus('Default first messages not saved: '+e.message, true); }
}
// A ＋ New Manager launch's first message: the saved default with its number filled in, unless the form's
// checkbox is off. Unavailable: the launch goes ahead without one, and says so.
async function newManagerOpening(name) {
  if (nm('claude-new-opening')?.checked === false) return null;
  try {
    const order = managerOrder(); if (!order?.opening) throw new Error('manager-order.js is not loaded');
    const text = order.opening((await loadFirstMessages()).manager, name);
    return text.trim() ? text : null;
  } catch (e) { newManagerStatus('First message unavailable ('+e.message+'); '+name+' starts without one.', true); return null; }
}
// @beacon[
//   id=opc-pane@openNewSub,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=↳ Sub-session: names the next sub-session of the newest primary in the sidebar's Manager Sessions (manager-order.js nextSub) and opens its first-message box, the saved default with number and letter filled in and room for the reason, under ＋ New Manager's own runtime/model/thinking controls (moved into the box; default Native Claude, so a crisis sub-session can be OpenCode/GPT with the Claude host down); reopening keeps Dan's text and choices.,
// ]
async function openNewSub() {
  const form = nm('claude-new-sub-form'); if (!form || state.backend !== 'claude') return;
  let names = [];
  try { names = $('claude-sessions-frame')?.contentWindow?.opcSidebar?.managerNames?.() || []; } catch (_) { /* no list yet */ }
  const next = managerOrder()?.nextSub?.(names);
  if (nm('claude-new-manager-form')) nm('claude-new-manager-form').hidden = true;
  placeManagerChoices('claude-new-sub-choices-slot');
  form.hidden = false;
  if (!next) {
    nm('claude-new-sub-title').textContent = 'New sub-session'; form.dataset.name = '';
    return newManagerStatus(names.some(n => managerOrder()?.parse?.(...n)) ? 'The newest Manager-Session has no letter left after z.' :
      'No numbered Manager-Session in the list yet: nothing to start a sub-session of.', true);
  }
  nm('claude-new-sub-title').textContent = 'Sub-session '+next.id+' of '+next.parent;
  const box = nm('claude-new-sub-text');
  if (form.dataset.name !== next.name || !box.value.trim()) {
    try { box.value = managerOrder().opening((await loadFirstMessages()).subSession, next.name); }
    catch (e) { return newManagerStatus('Sub-session first message unavailable: '+e.message, true); }
  }
  form.dataset.name = next.name;
  box.focus?.(); try { box.setSelectionRange(box.value.length, box.value.length); } catch (_) { /* focus is enough */ }
  // OpenCode already chosen (this page load): its catalog is read again; a failed read keeps its own status in view.
  if (nm('manager-new-runtime')?.value === 'opencode' && !await newManagerRuntime()) return;
  newManagerStatus('Add the reason at the end, choose the runtime, model and thinking, then Send: it starts '+next.name+' and sends this as its first message.');
}
async function launchNewSub() {
  const form = nm('claude-new-sub-form'), name = form?.dataset?.name, text = nm('claude-new-sub-text')?.value || '';
  if (!name) return newManagerStatus('Open ↳ Sub-session again: no sub-session is named yet.', true);
  if (!text.trim()) return newManagerStatus('The first message is empty; nothing was started.', true);
  if (await launchNewManager({name, opening:text})) { form.hidden = true; nm('claude-new-sub-text').value = ''; form.dataset.name = ''; }
}
// A first message that could not go out: in an empty draft, for a deliberate Send; a draft of Dan's is never touched.
function openingNotSent(text, why) {
  const box = $('transcript'), placed = !box.value.trim();
  if (placed) { box.value = text; draftChanged(); saveDraft(); }
  status('First message not sent: '+why+(placed ? ' · it is in the text box: press Send' : ' · your draft is kept (the first message is not in it)'),'err');
}
// @beacon[
//   id=opc-pane@sendOpening,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=The first message of a ＋ New Manager / ↳ Sub-session launch: one journaled host Send of the given text (never the draft) to the Claude Manager just adopted; its replay ACK clears, copies and records nothing, an uncertain reply is query-only and a refusal hands the text to an empty draft.,
// ]
async function sendOpening(t, text) {
  if (!isClaudeManager(t) || state.sending || state.refineAbort || state.claudeControlSending || state.claudeJournalError || pendingConversationSend(t))
    return openingNotSent(text, 'the Manager was not free for it');
  state.sending = true; renderTarget();
  let op;
  try {
    op = {schemaVersion:1,target:t,messageUuid:crypto.randomUUID(),original:text,submitted:text.trim(),at:Date.now(),
      frozen:freezeRefineTarget(),acknowledged:false,uncertain:true,opening:true,handledAck:true};
    saveClaudeJournal(K.claudeSend,op); state.claudeSend = op;
    const result = await claudeRequest('/api/claude/send',{...claudeIdentity(t),messageUuid:op.messageUuid,original:op.original,submitted:op.submitted});
    applyClaudeSendReceipt(op,result);
    status('First message sent to '+t.title+' · '+(op.acknowledged ? 'exact replay acknowledged' : op.receipt?.durable ? 'durably accepted' :
      'acceptance uncertain; querying same UUID only'),op.receipt?.durable ? 'ok' : 'err',op.acknowledged ? null : 'send:'+op.messageUuid);
  } catch (e) {
    if (op) {
      op.refused = [400,403,404,405,409,413].includes(e.status); op.uncertain = !op.refused; op.error = e.message;
      op.note = op.refused ? 'server refused the first message' : 'response uncertain; querying same UUID only, never resend';
      try { saveClaudeJournal(K.claudeSend,op); } catch (_) { /* the visible status says what happened */ }
    }
    if (!op || op.refused) openingNotSent(text, e.message);
    else status('First message uncertain: '+e.message+' · querying its receipt only, never resent','err','send:'+op.messageUuid);
  } finally { state.sending = false; renderTarget(); }
}
// The same for a new OpenCode Manager: one journaled intent, executed once; its ACK clears nothing.
async function sendOpeningOpenCode(sessionId, text) {
  const api = openCodeManagerApi(), target = state.ocManagerTarget;
  if (target?.sessionId !== sessionId || state.sending || state.refineAbort) return openingNotSent(text, 'the new Manager was not the free Send target');
  state.sending = true; renderTarget();
  try {
    const record = await api.begin('send',{target,original:text,submitted:text.trim(),delivery:'steer'},{opening:true,handledAck:true});
    const result = await api.execute(record);
    status('First message '+(api.acknowledged(result) ? 'delivered' : (result.receipt?.state || 'uncertain')+' · querying this id only, never resent'),
      api.acknowledged(result) ? 'ok' : 'info','opencode:'+result.operationId);
  } catch (e) { openingNotSent(text, e.message); }
  finally { state.sending = false; renderTarget(); }
}
async function launchedManagerRow(uuid) {
  const seen = new Set(); let cursor = null;
  do {
    const data = await claudeRequest('/api/claude/sessions'+(cursor ? '?cursor='+encodeURIComponent(cursor) : ''));
    if (data?.schemaVersion !== 1 || data.backend !== 'claude' || !Array.isArray(data.sessions)) throw new Error('session inventory schema invalid');
    const row = data.sessions.find(r => r.source === 'host' && r.uuid === uuid);
    if (row) return row;
    if (!data.hasMore) return null;
    cursor = data.nextCursor;
    if (!targetRefValid(cursor) || seen.has(cursor)) return null;
    seen.add(cursor);
  } while (true);
}
// @beacon[
//   id=opc-pane@adoptLaunchedManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Selects a pane-launched Manager as the Send target once its row is ready, through the ordinary validated target path, then sends the launch's first message once (sendOpening); Dan choosing another target first cancels both, and a failed launch shows the host's own reason.,
// ]
async function adoptLaunchedManager() {
  const launch = state.claudeLaunch;
  if (!launch || launch.phase !== 'waiting' || launch.adopting || state.backend !== 'claude') return;
  if (state.sending || state.refineAbort || state.claudeControlSending || state.targetValidationAbort) return; // next poll
  launch.adopting = true;
  try {
    const row = await launchedManagerRow(launch.uuid);
    if (state.claudeLaunch !== launch) return;
    if (!row) {
      if (Date.now() - launch.started > 10 * 60 * 1000) { state.claudeLaunch = null; saveLaunch(null);
        newManagerStatus(launch.name+' did not appear in the session list; check: bash scripts/claude_host.sh call list', true); }
      return;
    }
    if (row.lifecycle === 'ended' || ['launch_failed','launch_blocked','exited'].includes(row.state)) {
      state.claudeLaunch = null; saveLaunch(null);
      return newManagerStatus(launch.name+' did not start ('+String(row.state)+'). The host’s incidents for it say why; your draft and target are unchanged.', true);
    }
    if (row.capabilities?.nativeInput !== true) { newManagerStatus(launch.name+' is starting ('+String(row.state)+') · it is selected automatically when ready.'); return; }
    const scope = row.scopes?.find(s => s.label === 'main');
    if (!targetRefValid(row.sessionRef) || !targetRefValid(scope?.scopeRef)) return;
    const data = await claudeTargetAuthority({sessionRef:row.sessionRef,scopeRef:scope.scopeRef});
    if (state.claudeLaunch !== launch) return;
    const target = validatedClaudeTarget(data, data.targetRef);
    if (!isClaudeManager(target) || target.uuid !== launch.uuid) return;
    const previous = isClaudeManager() && state.claudeTarget.uuid !== launch.uuid ? state.claudeTarget.title : null;
    await acceptClaudeTarget(target.targetRef, state.targetRevision, () => state.claudeLaunch === launch);
    // Forgotten before its first message goes out: a reload from here on never sends it a second time.
    state.claudeLaunch = null; saveLaunch(null);
    status('Ready: sending to '+launch.name+(previous ? ' · '+previous+' keeps running and stays in the list' : ''),'ok');
    newManagerStatus(launch.name+' is ready and selected for Send'+(launch.opening ? '; its first message is going out.' : '.'));
    if (nm('claude-new-manager-form')) nm('claude-new-manager-form').hidden = true;
    if (nm('claude-new-name')) nm('claude-new-name').value = '';
    notifySessions({type:'browse-session', uuid:launch.uuid});
    if (launch.opening && state.claudeTarget?.uuid === launch.uuid) void sendOpening(state.claudeTarget, launch.opening);
  } catch (e) { launch.lastError = e.message; /* the next poll tries again; nothing launched or sent */ }
  finally { launch.adopting = false; renderTarget(); }
}
function claudeOperationKey(t) { return t?.executionId+'/'+t?.generation; }
function journalMap(key) { return key === K.claudeSend ? state.claudeSends : state.claudeControls; }
function rememberClaudeJournals() {
  if (state.claudeSend?.target) state.claudeSends.set(claudeOperationKey(state.claudeSend.target),state.claudeSend);
  if (state.claudeControl?.target) state.claudeControls.set(claudeOperationKey(state.claudeControl.target),state.claudeControl);
}
function selectClaudeJournals(t) {
  rememberClaudeJournals();
  const key = claudeOperationKey(t);
  state.claudeSend = state.claudeSends.get(key) || null;
  state.claudeControl = state.claudeControls.get(key) || null;
}
function pendingClaudeControl() { return !!state.claudeControl && currentClaudeOperation(state.claudeControl) && !state.claudeControl.refused && !state.claudeControl.settled; }
// @beacon[
//   id=opc-pane@pendingConversationSend,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=The same conversation's unresolved Send, any generation: the current generation is unresolved until its exact replay ACK, an earlier one until its exact durable receipt has been queried — never a blind new-UUID repeat; a different conversation's journal stays independent.,
// ]
function pendingConversationSend(t = state.claudeTarget) {
  if (!t?.executionId) return null;
  rememberClaudeJournals();
  for (const candidate of state.claudeSends.values()) {
    // A Send Dan released after checking it ("I checked, release it") no longer holds the conversation.
    if (!candidate || candidate.refused || candidate.released || candidate.target?.executionId !== t.executionId) continue;
    // A native result for that exact UUID settles it too: an echo that never matched byte for byte
    // (no replay ACK) must not hold every later Send forever once its turn has completed.
    // A steer an interrupt dropped from Claude's queue is settled too (never delivered, never resent).
    if (candidate.target.generation === t.generation ? !(candidate.acknowledged || candidate.receipt?.completed === true ||
        candidate.receipt?.cancelled === true) : candidate.receipt?.durable !== true) return candidate;
  }
  return null;
}
// A Send unresolved this long is offered Dan's deliberate disposition (an entry from before
// Sends were timed qualifies at once).
const STUCK_SEND_MS = 30000;
function stuckClaudeSend(t = state.claudeTarget) {
  const op = pendingConversationSend(t);
  if (!op || (op.steer && op.receipt?.queued && !op.uncertain && !op.receipt?.cancelled)) return null;  // a queued steer is on its way
  return !Number.isFinite(op.at) || Date.now() - op.at >= STUCK_SEND_MS ? op : null;
}
// @beacon[
//   id=opc-pane@releaseClaudeSend,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Way out of a Send whose receipt never resolves: Dan's deliberate "I checked, release it" disposition, recorded on the journal entry; the original stays in recovery and the receipt stays visible, and it is never resent.,
// ]
function releaseClaudeSend() {
  const op = stuckClaudeSend();
  if (!op) return status('No unresolved Send holds this conversation','info');
  if (!confirm('Release message '+op.messageUuid.slice(0,8)+' ('+(op.note || 'receipt unresolved')+')?\n\n'+
      'Only release it after checking the conversation: you can see whether the Manager got it. '+
      'It is never resent; its text stays in ↩ originals and in its receipt line, marked released by you. Your next Send is a new message.')) return;
  op.released = {at:new Date().toISOString(), by:'operator', note:op.note || null};
  op.note = (op.note ? op.note+' · ' : '')+'released by Dan after checking (never resent)';
  try { saveClaudeJournal(K.claudeSend, op); }
  catch (e) { delete op.released; return status('Release not recorded: '+e.message+' · the Send still holds; save the original externally','err'); }
  status('Released message '+op.messageUuid.slice(0,8)+' (your disposition is recorded; it is never resent) · Send is open','ok');
  renderTarget();
}
function claudeJournalKeys() {
  // Every stored journal key (index and per-generation slots), read straight from storage.
  const keys = new Set([K.claudeSend, K.claudeControl]);
  try { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i);
    if (typeof k === 'string' && [K.claudeSend, K.claudeControl].some(p => k.startsWith(p+':'))) keys.add(k); } }
  catch (_) { /* index keys only */ }
  return [...keys].filter(k => ls.get(k) !== null);
}
// @beacon[
//   id=opc-pane@exportClearClaudeJournal,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Way out of an unreadable Send/control journal: export every journal key (a downloaded file, plus a quarantined copy when storage allows), record the disposition, then clear it so Send and controls open; never clears without a recoverable copy.,
// ]
function exportClearClaudeJournal() {
  if (!state.claudeJournalError) return;
  const keys = claudeJournalKeys(), stamp = new Date().toISOString();
  const text = JSON.stringify({schemaVersion:1, exportedAt:stamp, reason:state.claudeJournalError,
    entries:Object.fromEntries(keys.map(k => [k, ls.get(k)]))}, null, 1);
  if (!confirm('Export and clear the pane’s Send/control recovery journal?\n\n'+state.claudeJournalError+'\n\n'+
      'Its '+keys.length+' stored entries (originals included) are downloaded as a file first, and kept in this browser too when there is room. '+
      'Nothing is resent; Send and the controls open afterwards.')) return;
  let downloaded = false, quarantined = null;
  try {
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], {type:'application/json'}));
    a.download = 'pane-claude-journal-'+stamp.replace(/[:.]/g,'-')+'.json'; a.click(); downloaded = true;
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  } catch (_) { /* the quarantined copy below must then hold it */ }
  try { const key = K.claudeJournalQuarantine+':'+stamp; ls.set(key, text); if (ls.get(key) === text) quarantined = key; }
  catch (_) { /* storage full: the downloaded file is the copy */ }
  if (!downloaded && !quarantined) return status('Journal NOT cleared: neither a download nor a stored copy was possible · save the browser storage first','err');
  const disposition = {at:stamp, reason:state.claudeJournalError, keys, downloaded, quarantined};
  try { ls.set(K.claudeJournalDisposition, JSON.stringify(disposition)); } catch (_) { /* the export itself names the reason */ }
  for (const k of keys) ls.del(k);
  state.claudeJournalError = null; state.claudeSends.clear(); state.claudeControls.clear();
  state.claudeSend = null; state.claudeControl = null;
  initializeClaudeJournal();
  if (!state.claudeJournalError) status('Recovery journal exported'+(downloaded ? ' (downloaded)' : '')+(quarantined ? ' and kept as '+quarantined : '')+
    ' and cleared · Send and controls are open; nothing was resent','ok');
  renderTarget();
}
function claudeIdentity(t) {
  return {schemaVersion:1,backend:'claude',targetRef:t.targetRef,executionId:t.executionId,generation:t.generation};
}
function claudeQuery(t, extra = {}) {
  const {schemaVersion,backend,...identity} = claudeIdentity(t);
  return new URLSearchParams({...identity,...extra}).toString();
}
function claudeEnvelope(data, t) {
  const reason = !data || data.schemaVersion !== 1 || data.backend !== 'claude' ? 'response schema/backend mismatch' :
    data.targetRef !== t.targetRef ? 'target reference changed' : data.executionId !== t.executionId ? 'execution ID changed' :
    data.generation !== t.generation ? 'host generation changed from '+t.generation+' to '+String(data.generation) : data.uuid !== t.uuid ? 'session UUID changed' : null;
  if (reason) { const error = new Error('native response validation failed: '+reason+'; draft retained'); error.targetValidation = true; throw error; }
  return data;
}
async function claudeRequest(url, body, budgetMs = 12000) {
  // This is only an HTTP observation budget. Aborting it cannot cancel an accepted host operation.
  const ctrl = new AbortController(), timer = setTimeout(()=>ctrl.abort(),budgetMs);
  try {
    const resp = await fetch(url, {method:body === undefined ? 'GET' : 'POST',cache:'no-store',signal:ctrl.signal,
      ...(body === undefined ? {} : {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const wire = await resp.text();
    if (utf8len(wire) > 8*1024*1024) throw new Error('native response exceeds observation limit');
    const data = JSON.parse(wire);
    if (!resp.ok) { const error = new Error(data.message || data.error || ('HTTP '+resp.status)); error.status = resp.status; throw error; }
    return data;
  } finally { clearTimeout(timer); }
}
function readClaudeJournals(key) {
  const raw = ls.get(key); if (!raw) return new Map();
  const saved = JSON.parse(raw), id = key === K.claudeSend ? 'messageUuid' : 'requestId';
  const operations = saved.schemaVersion === 2 ? (() => {
    if (!Array.isArray(saved.slots) || new Set(saved.slots).size !== saved.slots.length || saved.slots.some(s=>typeof s !== 'string' || !/^cc-[0-9a-f-]{36}\/[1-9][0-9]*$/.test(s))) throw new Error('invalid native journal references; preserve storage');
    return saved.slots.map(slot=>{const op=JSON.parse(ls.get(key+':'+slot,'null'));if(!op || claudeOperationKey(op.target)!==slot)throw new Error('native journal reference missing or changed; preserve storage');return op;});
  })() : [saved];
  for (const op of operations) {
    const t = op.target;
    if (op.schemaVersion !== 1 || !t || !isClaudeManager(t) || !targetRefValid(t.targetRef) ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(op[id] || '') ||
        (key === K.claudeSend && (typeof op.original !== 'string' || typeof op.submitted !== 'string' || !op.submitted || !op.frozen)) ||
        (key === K.claudeControl && !['set_model','apply_effort','interrupt'].includes(op.operation))) {
      throw new Error('invalid saved native operation; preserve storage and inspect receipts before Send');
    }
  }
  return new Map(operations.map(op=>[claudeOperationKey(op.target),op]));
}
// @beacon[
//   id=opc-pane@saveClaudeJournal,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Saves and reads back a bounded execution/generation journal; validates and merges the latest stored references with local/legacy slots so alternating tabs retain each other's recovery; corrupt references still refuse before Send.,
// ]
function saveClaudeJournal(key, value) {
  const slot = claudeOperationKey(value.target), storageKey = key+':'+slot;
  const encoded = JSON.stringify(value);
  if (encoded.length*2 + (!value.receipt ? 16384 : 0) > 4*1024*1024) throw new Error('native operation journal exceeds browser recovery bound; save externally');
  const map = journalMap(key), stored = readClaudeJournals(key);
  const merged = new Map([...stored,...map]);
  const slots = [...new Set([...merged.keys(),slot])];
  const index = JSON.stringify({schemaVersion:2,slots});
  if (index.length*2 > 4*1024*1024) throw new Error('native journal reference index exceeds recovery bound; preserve storage');
  // Migrate legacy entries before replacing their reference index. A failed
  // migration leaves the old v1 recovery record in place and prevents the POST.
  for (const [oldSlot,old] of merged) if (oldSlot !== slot && ls.get(key+':'+oldSlot) === null) {
    const prior = JSON.stringify(old); ls.set(key+':'+oldSlot,prior);
    if (ls.get(key+':'+oldSlot) !== prior) throw new Error('native legacy journal migration readback failed');
  }
  ls.set(storageKey, encoded);
  if (ls.get(storageKey) !== encoded) throw new Error('native operation journal readback failed');
  ls.set(key,index); if (ls.get(key) !== index) throw new Error('native journal index readback failed');
  map.set(slot,value);
}
function initializeClaudeJournal() {
  try {
    for (const [key,field] of [[K.claudeSend,'claudeSend'],[K.claudeControl,'claudeControl']]) {
      for (const op of readClaudeJournals(key).values()) {
      const t = op.target;
      // Saved flags are not fresh receipt evidence. Never clear a restored draft from an old page.
      // Older v1 receipts duplicated the text. Keep its exact operation texts once.
      if (field === 'claudeSend' && op.receipt) { delete op.receipt.original; delete op.receipt.submitted; }
      const restored = {...op, ...(field === 'claudeSend' ? {acknowledged:false} : {})};
      journalMap(key).set(claudeOperationKey(t),restored); state[field] = restored;
      }
    }
  } catch (e) { state.claudeJournalError = e.message; status('native recovery blocked: '+e.message,'err'); }
}
function currentClaudeOperation(op) {
  return state.backend === 'claude' && !!state.claudeTarget && !!op?.target &&
    state.claudeTarget.executionId === op.target.executionId && state.claudeTarget.generation === op.target.generation;
}
// @beacon[
//   id=opc-pane@claudeBusy,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Send gate mirrors the host's own-turn rule: background agents/tasks and subagent permission prompts never block; own turns/retries/unknown state give a plain reason without claiming another device's pending message belongs to this browser.,
// ]
function claudeBusy(s) {
  if (!s) return '';
  // Only the Manager's OWN turn blocks. Native session_state stays 'running' while
  // background agents/tasks work after the primary result; primary_turn_open === false
  // (host witness: the primary result closed the turn) tells the two apart. Without that
  // witness (older host) the conservative rule stays. A subagent's own permission prompt
  // (agent_id) is not the Manager's dialog.
  const native = s.native_session_state, closed = s.primary_turn_open === false;
  const known = native === 'idle' || native === 'running' || native === 'requires_action';
  const ownPermission = s.pending_native_requests && typeof s.pending_native_requests === 'object' &&
    Object.values(s.pending_native_requests).some(r => !r || typeof r !== 'object' || typeof r.agent_id !== 'string');
  // A scheduled automatic recovery is no turn: Dan's Send replaces its `continue` (the host
  // cancels it), and only a recovery already sending is refused, by the host itself.
  return s.pending_message_uuids?.length ? 'the Manager is still answering a previous message' :
    s.native_retry_active ? 'Claude Code is retrying a failed request' :
    native === 'requires_action' && !closed ? 'the Manager is waiting for a permission answer' :
    native === 'running' && !closed ? 'the Manager is working on its own turn' :
    s.native_state_required === true && !known ? 'the Manager’s current state is not known yet' :
    Number(s.native_queued_turns) > 0 ? 'Claude Code still holds a queued message' :
    ownPermission ? 'the Manager is waiting for a permission answer' : '';
}
// @beacon[
//   id=opc-pane@claudeSteerBlock,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Send while the Manager is busy is a host steer (Claude Code hands it to the Manager at its next tool step, a native retry in its turn included); a scheduled recovery never refuses it: the host supersedes the recovery or, while one is sending, refuses with its own reason.,
// ]
function claudeSteerBlock(s) {
  if (!s) return 'the Manager’s state is not known yet';
  // A native retry inside the Manager's turn is no blocker: Claude Code holds the steer through it.
  // Neither is a scheduled recovery: the host decides (supersede, or "sending right now").
  return '';
}
// A pending automatic recovery in plain words: a wait after a capacity/quota error or a crash.
function claudeRecoveryNote(s) {
  return s?.pending_recovery ? 'An automatic recovery is scheduled (after a capacity/quota error or a crash): '+
    'your Send replaces its “continue”; only while it is sending right now does the host ask you to wait a moment. ' : '';
}
// @beacon[
//   id=opc-pane@renderClaudeManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=The selected Manager controls dispatch by runtime: OpenCode uses real catalog/model readbacks and native steer/interrupt receipts; Claude keeps its verified-settings, incidents and recovery behavior without sharing identities or control policy.,
// ]
function renderClaudeManager() {
  if ($('opencode-manager-panel')) $('opencode-manager-panel').hidden=!(state.backend === 'claude' && state.ocManagerTarget);
  if (state.backend === 'claude' && state.ocManagerTarget) {
    $('claude-manager-panel').hidden=true; renderOpenCodeManager(); return;
  }
  const panel = $('claude-manager-panel'); if (!panel) return;
  const manager = state.backend === 'claude' && isClaudeManager();
  panel.hidden = state.backend !== 'claude' || (!manager && !state.claudeSend && !state.claudeControl && !state.claudeSends.size && !state.claudeControls.size && !state.claudeJournalError);
  const observed = manager && state.claudeManager?.targetRef === state.claudeTarget.targetRef ? state.claudeManager : null;
  const s = observed?.liveState, applied = s?.controls_effective;
  const verified = !!applied && typeof applied.model === 'string' && !!applied.model && typeof applied.effort === 'string' && !!applied.effort &&
    s.setting_control_state === 'applied' && !s.setting_control_pending && !s.settings_mismatch && !observed?.error;
  // The host could not confirm the model/thinking level, but nothing names a different one: Send
  // goes through, and the pane says so plainly (a positive mismatch still blocks, host-side).
  const unverified = !verified && s?.settings_unverified === true && !s.settings_mismatch && !observed?.error;
  const choicesFor = manager ? state.claudeTarget.executionId+'/'+state.claudeTarget.generation : null;
  if (verified) {
    const model = $('claude-model'), effort = $('claude-effort'), previous = state.claudeAppliedChoices;
    const fresh = state.claudeChoicesFor !== choicesFor || !previous;
    // Compare against the last choices WE populated, not against a moving readback:
    // a deferred poll/control must not turn an idle, old display into a local edit.
    const pending = state.claudeControlSending || pendingClaudeControl();
    // A now-applied local selection is no longer dirty. Reconcile each field so
    // applying a model can also follow an effort changed by that native switch.
    if (!fresh && !pending) {
      if (model.value === applied.model) previous.choiceModel = applied.model;
      if (effort.value === applied.effort) previous.choiceEffort = applied.effort;
    }
    const dirty = !fresh && (model.value !== previous.choiceModel || effort.value !== previous.choiceEffort);
    if (fresh || !pending && !dirty) {
      if (model.options && ![...model.options].some(o=>o.value === applied.model)) model.add(new Option(applied.model,applied.model));
      model.value = applied.model; effort.value = applied.effort;
      state.claudeChoicesFor = choicesFor;
      state.claudeAppliedChoices = {choiceModel:applied.model,choiceEffort:applied.effort};
    }
    // Keep the last verified pair even when a local choice/control prevents following it.
    Object.assign(state.claudeAppliedChoices,{model:applied.model,effort:applied.effort});
  }
  $('claude-effective').textContent = state.claudeJournalError ? 'RECOVERY BLOCKED · '+state.claudeJournalError : !manager ? 'View-only target; any receipt below belongs to its labeled original Manager' :
    (applied?.model || 'Model unknown')+' · thinking '+(applied?.effort || 'unknown')+' · '+(verified ? 'applied' : unverified ? '⚠ settings unverified' : 'pending');
  if ($('claude-effective').dataset) $('claude-effective').dataset.state = verified ? 'applied' : unverified ? 'unverified' : 'pending';
  $('claude-effective').title = verified ? 'Native effective model and thinking level verified by applied settings readback.' :
    unverified ? 'Settings unverified: the host could not confirm this Manager’s model and thinking level ('+
      (typeof s.settings_unverified_disclosure === 'string' ? s.settings_unverified_disclosure : 'no usable readback')+'). Send still goes through; press Re-check settings to verify.' :
    'Pending / unverified. Values, if present, are the last effective readback, not the requested selection; Send authority remains host-checked.';
  const observation = $('claude-manager-observation');
  if (observation) observation.textContent = !manager ? '' : observed?.error ? 'Observation unavailable: '+observed.error :
    state.claudeJournalError ? 'Recovery blocked; preserve originals.' : !s ? 'Awaiting host observation.' :
    (claudeBusy(s) ? 'Busy: '+claudeBusy(s)+'. '+(claudeSteerBlock(s) ? 'Send waits for it' :
      'Send reaches it at its next tool step (one long-running command delays it until that command ends)')+'; ■ Interrupt stops it. ' : '')+
    claudeRecoveryNote(s)+
    (unverified ? '⚠ Settings unverified: the host could not confirm the model and thinking level; Send goes through, warned. Press Re-check settings to verify. ' :
      !verified ? 'Settings pending — press Re-check settings. ' : '')+
    (s.pending_escalations?.length ? s.pending_escalations.length+' open incident(s) for you (listed below, each with Resolve).' : '');
  renderClaudeIncidents(manager && !observed?.error ? observed?.openIncidents : null);
  // The ways out, always in view while they apply: a Send whose receipt never resolved, a bad journal.
  const stuck = state.backend === 'claude' && !state.claudeJournalError ? stuckClaudeSend() : null;
  const release = $('claude-send-release'), exporter = $('claude-journal-export'), wayOut = $('claude-way-out');
  if (release) { release.hidden = !stuck; release.disabled = !!state.sending; }
  if (exporter) exporter.hidden = !state.claudeJournalError;
  if (wayOut) wayOut.hidden = !stuck && !state.claudeJournalError;
  if ($('claude-way-out-text')) $('claude-way-out-text').textContent = state.claudeJournalError ?
    'This browser’s Send/control journal cannot be read, so Send and the controls wait: export it (originals included) and clear it.' :
    stuck ? 'Send waits for message '+stuck.messageUuid.slice(0,8)+' ('+(stuck.note || 'receipt unresolved')+'). Check the conversation, then release it; it is never resent.' : '';
  const blocked = !manager || !!state.targetValidationAbort || !!state.claudePendingSelection || !!state.claudeTargetError || !!state.claudeJournalError || !!state.sending || !!state.claudeControlSending || !!state.refineAbort;
  const pendingControl = pendingClaudeControl();
  for (const id of ['claude-model-apply','claude-effort-apply']) $(id).disabled = blocked || !!pendingControl || claudeBusy(s);
  if ($('claude-settings-recheck')) $('claude-settings-recheck').disabled = blocked;
  // The visible Interrupt beside Send: shown for a live Manager target, lit while its own turn runs.
  const quick = $('claude-interrupt-now');
  if (quick) { quick.hidden = !manager; quick.disabled = blocked || !!pendingControl;
    quick.classList?.toggle('busy', !!(s && claudeBusy(s)));
    quick.title = s && claudeBusy(s) ? 'Stop the Manager’s current turn ('+claudeBusy(s)+'). Your draft stays.' : 'Stop the Manager’s current turn, if it is working. Your draft stays.'; }
  $('claude-model').disabled = !manager; $('claude-effort').disabled = !manager;
  $('claude-wait').disabled = !manager;
  const op = state.claudeSend, r = op?.receipt;
  $('claude-send-receipt').textContent = !op ? 'No pane Send receipt' : (op.steer ? 'Steer ' : 'Send ')+op.messageUuid+' · '+op.target.uuid+' / g'+op.target.generation+
    ' · accepted '+(r?.durable === true ? 'yes' : 'not proved')+(op.steer ? ' · queued '+(r?.queued === true ? 'yes' : 'no')+' · delivered '+(op.acknowledged ? 'yes' : 'no') :
    ' · replay-acknowledged '+(op.acknowledged ? 'yes' : 'no'))+
    ' · started '+(r?.started === true ? 'yes' : 'no')+' · completed '+(r?.completed === true ? 'yes (receipt, not task acceptance)' : 'no')+
    ' · uncertain '+(op.uncertain || r?.uncertain || r?.status === 'uncertain' ? 'yes' : 'no')+
    (op.refused ? ' · refused: '+op.error : '')+(op.note ? ' · '+op.note : '');
  const c = state.claudeControl, cr = c?.receipt;
  $('claude-control-receipt').textContent = !c ? 'No pane control receipt' : 'Control '+c.requestId+' · '+c.target.uuid+' / g'+c.target.generation+' · '+c.operation+
    (c.value ? ' requested '+c.value : '')+
    ' · '+(cr?.state || cr?.status || (c.refused ? 'refused' : 'uncertain/pending'))+' · applied '+(cr?.applied === true ? 'verified' : 'not proved')+
    (cr?.effective ? ' · readback '+(cr.effective.model || 'unknown')+' / '+(cr.effective.effort || 'unknown') : '')+
    (cr?.disclosure ? ' · '+cr.disclosure : '')+(c.error ? ' · '+c.error : '')+
    (c.operation === 'interrupt' ? ' · task settlement '+(cr?.settlement?.settled === true ? 'proved' : 'not proved')+'; interruption receipt alone is not settlement' : '');
  const historicalSends = [...state.claudeSends.values()].filter(o=>o !== op);
  const historicalControls = [...state.claudeControls.values()].filter(o=>o !== c);
  for (const old of historicalSends) $('claude-send-receipt').textContent += '\nOld Manager '+old.target.title+' · '+old.target.executionId+' / g'+old.target.generation+' · Send '+old.messageUuid+' · '+(old.acknowledged?'exact ACK':old.released?'released by Dan':'pending/uncertain')+' · query only; never resend'+(old.note?' · '+old.note:'');
  for (const old of historicalControls) $('claude-control-receipt').textContent += '\nOld Manager '+old.target.title+' · '+old.target.executionId+' / g'+old.target.generation+' · control '+old.requestId+' · '+(old.settled?'settled':'pending/uncertain')+' · query only; never repeat'+(old.error?' · '+old.error:'');
}
// The Manager's open incidents (escalations addressed to Dan), each with its way out: Resolve.
function renderClaudeIncidents(items) {
  const box = $('claude-incidents'); if (!box) return;
  const list = Array.isArray(items) ? items.filter(i => i && typeof i.incidentId === 'string') : [];
  const signature = JSON.stringify(list.map(i => [i.incidentId, i.category, i.summary]));
  box.hidden = !list.length;
  if (state.claudeIncidentsSignature === signature) return;
  state.claudeIncidentsSignature = signature;
  if (typeof box.replaceChildren !== 'function') { box.textContent = list.map(i => '⚠ '+i.category+(i.summary ? ' · '+i.summary : '')).join('\n'); return; }
  box.replaceChildren();
  for (const item of list) {
    const row = document.createElement('div'); row.className = 'incident';
    const when = typeof item.time === 'number' ? new Date(item.time*1000).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : 'time unknown';
    const text = document.createElement('span'); text.className = 'incident-text';
    text.textContent = '⚠ '+String(item.category || 'incident').replace(/_/g,' ')+' · '+when+(item.summary ? ' · '+item.summary : '');
    const button = document.createElement('button'); button.className = 'mini'; button.type = 'button'; button.textContent = 'Resolve…';
    button.title = 'Record your disposition on this incident (the host keeps it) and close it. Nothing is sent to the Manager.';
    button.onclick = () => void resolveClaudeIncident(item.incidentId);
    row.append(text, button); box.append(row);
  }
}
// @beacon[
//   id=opc-pane@resolveClaudeIncident,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=The incidents panel's Resolve: Dan's typed disposition goes to the host's own resolve_incident verb (POST /api/claude/resolve-incident) for one open incident of the targeted Manager; recorded on the incident, never a Send or a native control.,
// ]
async function resolveClaudeIncident(incidentId) {
  const t = state.claudeTarget && {...state.claudeTarget};
  if (!t?.executionId || typeof incidentId !== 'string') return;
  const item = (state.claudeManager?.openIncidents || []).find(i => i.incidentId === incidentId);
  const disposition = prompt('Resolve this incident ('+String(item?.category || 'incident').replace(/_/g,' ')+')?\n\n'+(item?.summary || '')+
    '\n\nYour disposition is recorded on it by the Claude host; nothing is sent to the Manager.', 'Checked by Dan in the pane');
  if (disposition === null) return;
  if (!disposition.trim()) return status('Incident not resolved: a disposition is required','err');
  try {
    claudeEnvelope(await claudeRequest('/api/claude/resolve-incident',{...claudeIdentity(t),incidentId,disposition:disposition.trim()}),t);
    status('Incident resolved ('+String(item?.category || incidentId.slice(0,8)).replace(/_/g,' ')+'): your disposition is recorded; nothing was sent','ok');
  } catch (e) { status('Incident not resolved: '+e.message,'err'); }
  finally { await pollClaudeManager(); }
}
function applyClaudeSendReceipt(op, data) {
  claudeEnvelope(data, op.target);
  const r = data.receipt;
  if (!r) { op.uncertain = true; op.note = 'receipt not found yet; query only, never resend'; return; }
  if (r.execution_id !== op.target.executionId || r.generation !== op.target.generation || r.message_uuid !== op.messageUuid ||
      r.original !== op.original || r.submitted !== op.submitted || r.durable !== true) throw new Error('native exact receipt mismatch; never clear/retry');
  // Exact strings were just compared to the immutable journal, not stored again.
  op.receipt = {receiptRef:op.messageUuid,durable:true,acknowledged:r.acknowledged === true,
    started:r.started === true,completed:r.completed === true,uncertain:r.uncertain === true,
    queued:r.queued === true,delivered:r.delivered === true,cancelled:r.cancelled === true,
    status:typeof r.status === 'string' ? r.status.slice(0,128) : null};
  op.acknowledged = r.acknowledged === true; op.uncertain = r.uncertain === true || r.status === 'uncertain';
  // A steer's echo is its delivery: the Manager received it at its next tool step.
  const queued = op.steer && !op.acknowledged && op.receipt.queued && !op.uncertain && !op.receipt.cancelled;
  op.note = op.acknowledged ? (op.steer ? 'delivered (exact replay ACK)' : 'exact replay ACK') :
    op.receipt.cancelled ? 'cancelled before delivery (an interrupt dropped it); never resent' :
    queued ? 'queued: Claude Code hands it to the Manager at its next tool step' : 'durably accepted; awaiting exact replay ACK';
  saveClaudeJournal(K.claudeSend, op);
  // Durable acceptance is confirming evidence: an "uncertain" line about this Send gives way.
  if (!op.acknowledged) resolveStatus('send:'+op.messageUuid, queued ?
    'Queued for the Manager: it lands at its next tool step (one long-running command delays it until that command ends) · not resent' :
    'Manager Send accepted by the host (durable receipt); awaiting its replay ACK · draft retained', 'ok', true);
  // A queued steer is held by Claude Code itself, so its unchanged draft clears now; a plain
  // Send's draft still waits for the exact replay ACK.
  if ((op.acknowledged || queued) && !op.handledAck) {
    let saved = true; try { ls.set(K.lastSent,op.submitted); } catch (_) { saved = false; }
    const unchanged = !op.draftSaveFailed && sameRefineTarget(op.frozen,true) && $('transcript').value === op.original;
    if (saved && unchanged) {
      // Persist the cleared draft BEFORE clearing the visible editor. Quota failure preserves it.
      let cleared;
      try { cleared = clearSavedDraft(op.original); }
      catch (e) { op.note += ' · draft NOT cleared: '+e.message; saveClaudeJournal(K.claudeSend,op); return; }
      if (cleared) {
        $('transcript').value = ''; draftChanged(); clearTimeout(draftTimer);
        op.note += ' · unchanged draft cleared; original retained';
      } else op.note += ' · stored draft changed; draft retained';
    } else op.note += !saved ? ' · last-sent NOT saved; draft retained' : op.draftSaveFailed ?
      ' · draft NOT saved at Send; draft retained' : ' · draft/page/target changed; draft retained';
    op.handledAck = true; saveClaudeJournal(K.claudeSend,op);
    // Supplemental clipboard work cannot delay the exact draft/target check or steal focus.
    copyText(op.submitted);
  }
  if (op.acknowledged) resolveStatus('send:'+op.messageUuid, (op.steer ? 'Delivered to the busy Manager at its next tool step: message ' :
    'Manager Send delivered: the host acknowledged message ')+op.messageUuid.slice(0,8)+' · '+
    (/unchanged draft cleared/.test(op.note || '') ? 'the sent draft was cleared (↩ last sent restores it)' : 'the retained draft WAS sent — do not resend it'), 'ok');
}

// @beacon[
//   id=opc-pane@pollClaudeManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Reconciles durable caller IDs and pending launches without semantic retries, dispatching selected OpenCode Managers to their own observation adapter; native settings and exact-generation journals retain their original behavior.,
// ]
async function pollClaudeManager() {
  await adoptOpenCodeLaunch();
  if (state.backend === 'claude' && state.ocManagerTarget) { await pollOpenCodeManager(); return; }
  if (state.claudeObserving) return;
  state.claudeObserving = true;
  try {
    const selection = state.claudePendingSelection;
    if (selection && !state.targetValidationAbort && !state.sending && !state.refineAbort && !state.claudeControlSending) {
      if (!selection.current()) state.claudePendingSelection = null;
      else try {
        const target = await acceptClaudeTarget(selection.ref,selection.revision,selection.current);
        state.claudePendingSelection = null; status(state.nativeTargetStatus,'ok'); selection.ack(true,state.nativeTargetStatus);
      } catch (_) { /* Retry only target validation, never an intent. */ }
    }
    if (state.claudeLaunch?.phase === 'waiting') await adoptLaunchedManager();
    if (!state.claudePendingSelection && state.backend === 'claude' && (state.claudeTargetError || (!isClaudeManager() && (state.claudeTargetHint || state.claudeTarget)?.role === 'Manager'))) await recoverClaudeTarget();
    const t = state.backend === 'claude' && isClaudeManager() && !state.claudeTargetError ? {...state.claudeTarget} : null;
    if (t) {
      try {
        const data = claudeEnvelope(await claudeRequest('/api/claude/state?'+claudeQuery(t)),t);
        if (state.backend === 'claude' && state.claudeTarget?.targetRef === t.targetRef && state.claudeTarget.generation === t.generation) state.claudeManager = data;
      } catch (e) {
        if (state.backend === 'claude' && state.claudeTarget?.targetRef === t.targetRef) {
          state.claudeManager = {targetRef:t.targetRef,error:e.message};
          if (e.targetValidation || [400,403,404,409].includes(e.status)) await blockClaudeTarget(e);
        }
      }
    }
    rememberClaudeJournals();
    for (const op of state.claudeSends.values()) {
    if (op && !op.refused && !op.released && !op.receipt?.cancelled && (!op.receipt?.completed || !op.acknowledged)) {
      try { applyClaudeSendReceipt(op,await claudeRequest('/api/claude/receipt?'+claudeQuery(op.target,{messageUuid:op.messageUuid}))); }
      catch (e) { op.uncertain = true; op.note = 'receipt observation failed: '+e.message+'; query only, never resend'; }
    }
    }
    for (const control of state.claudeControls.values()) {
    if (control && !control.refused && (!control.settled ||
        control.operation === 'interrupt' && control.receipt?.settlement?.settled !== true)) {
      try {
        const data = claudeEnvelope(await claudeRequest('/api/claude/control-receipt?'+claudeQuery(control.target,{requestId:control.requestId})),control.target);
        applyClaudeControlReceipt(control,data.controlReceipt);
      } catch (e) { control.error = 'receipt observation failed: '+e.message+'; no repeat control'; }
    }
    }
  } finally { state.claudeObserving = false; renderTarget(); }
}

// @beacon[
//   id=opc-pane@validateReconnect,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Binds one reconnect reply to the exact execution/UUID and the expected old generation, allowing the host to answer a newer current generation; anything else refuses before a Send.,
// ]
function validateReconnect(data, t) {
  const fail = reason => { throw new Error('reconnect response invalid: '+reason+'; message not sent'); };
  if (!data || data.schemaVersion !== 1 || data.backend !== 'claude') fail('response schema/backend mismatch');
  if (data.executionId !== t.executionId) fail('execution ID changed');
  if (data.uuid !== t.uuid) fail('session UUID changed');
  if (data.role !== 'Manager') fail('role is not a Manager');
  if (data.previousGeneration !== t.generation) fail('expected generation mismatch');
  if (!Number.isSafeInteger(data.generation) || data.generation < t.generation) fail('host generation regressed');
  if (!targetRefValid(data.targetRef)) fail('fresh target reference missing');
  return data;
}
// @beacon[
//   id=opc-pane@awaitReconnectedManager,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Observes the resumed generation through the ordinary state read until it is writable; every await is bracketed by the frozen original selection guard, so a retarget or edit cancels, and nothing is queued or killed.,
// ]
async function awaitReconnectedManager(t, record, exact) {
  const live = {...t, targetRef:record.targetRef, generation:record.generation, lifecycle:record.lifecycle,
    state:record.state, capabilities:{...t.capabilities, ...record.capabilities}};
  const ceiling = Date.now() + 10 * 60 * 1000; // observation ceiling only; the new native process keeps starting
  for (;;) {
    exact();
    const data = claudeEnvelope(await claudeRequest('/api/claude/state?'+claudeQuery(live)), live);
    const observed = {...live, lifecycle:data.lifecycle, state:data.state,
      capabilities:{...live.capabilities, ...(data.capabilities || {})}};
    if (isClaudeManager(observed)) return observed;
    if (observed.lifecycle === 'ended' || observed.lifecycle === 'sleeping')
      throw new Error('reconnect left the conversation without a live process ('+String(data.state)+'); message not sent');
    if (Date.now() >= ceiling)
      throw new Error('reconnect is still initializing; the conversation keeps starting — Send again when the Manager is ready');
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
}

// @beacon[
//   id=opc-pane@sendClaude,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Manager Send flushes this tab's draft and journals full original/UUID before one POST (a busy own turn takes a steer); sleeping targets reconnect first without resending unresolved UUIDs; frozen/rebound revisions and exact ACK plus unchanged persisted original guard the clear.,
// ]
async function sendClaude() {
  if (state.targetValidationAbort || state.claudePendingSelection) return status('Connecting to the selected Manager · draft retained','info');
  const selected = state.claudeTarget;
  if (selected?.lifecycle === 'ended' && !reconnectableManager(selected)) return status(endedManagerText(selected),'info');
  if (selected?.source === 'host' && selected.role === 'Manager' && !isClaudeManager(selected) && !reconnectableManager(selected)) return status('Connecting · readiness is checked automatically; draft retained','info');
  if (state.claudeTargetError) return status('Claude Send blocked: '+state.claudeTargetError,'err');
  if (!isClaudeManager(selected) && !reconnectableManager(selected)) return status('Claude Send unavailable: only a registered Manager is writable; this target is view-only','err');
  if (state.claudeJournalError) return status('native Send blocked: '+state.claudeJournalError,'err');
  if (state.refineAbort || state.claudeControlSending) return status('wait for Refine/control; draft retained','err');
  let prior = pendingConversationSend(selected);
  if (prior && !reconnectableManager(selected)) {
    status('previous Send on this conversation pending/uncertain; checking its exact receipt, never resending; draft retained','err','send:'+prior.messageUuid);
    await pollClaudeManager();
    prior = pendingConversationSend(selected);
    if (prior?.steer && prior.receipt?.queued && !prior.uncertain && !prior.receipt?.cancelled)
      return status('Your previous message is queued for the Manager, not delivered yet: it lands at its next tool step. Send this one once it is delivered · draft kept','info','send:'+prior.messageUuid);
    if (prior) {    // still unresolved: never a blind new UUID, but always a way through
      renderTarget();
      if (!stuckClaudeSend(selected))
        return status('Your previous message '+prior.messageUuid.slice(0,8)+' is still awaiting its delivery confirmation; Send this one once it is confirmed · draft kept','info','send:'+prior.messageUuid);
      return status('Not sent — your previous message '+prior.messageUuid.slice(0,8)+' to this conversation has no confirmed delivery yet ('+
        (prior.note || 'receipt unresolved')+'). Look at the conversation: if you know whether it arrived, press “I checked — release it” and Send again. It is never resent; your draft is kept.','err','send:'+prior.messageUuid);
    }
  }
  const full = $('transcript').value, text = full.trim();
  if (!text) return status('nothing to send','err');
  // Send can return a queued steer before the ordinary 400 ms input debounce.
  // Flush our deliberate edit now, never at ACK over another tab's newer text.
  clearTimeout(draftTimer); const draftSaved = saveDraft(), frozen = freezeRefineTarget();
  state.sending = true; renderTarget();
  let op;
  try {
    let t = frozen.target, rebound = false, rebindRevision = null;
    // The frozen selection must survive every await until the deliberate rebind; the
    // rebind then advances the target revision, and that new exact revision (not "any")
    // is the witness every later guard requires, so an away/back retarget cancels too.
    const original = () => {
      if (!sameRefineTarget(frozen,true) || $('transcript').value !== full)
        throw new Error('target/draft changed; check it and Send again');
    };
    const reboundExact = () => {
      if (state.backend !== 'claude' || state.pageEpoch !== frozen.pageEpoch || state.draftRevision !== frozen.draftRevision ||
          $('transcript').value !== full || state.targetRevision !== rebindRevision ||
          state.claudeTarget?.targetRef !== t.targetRef || state.claudeTarget?.generation !== t.generation)
        throw new Error('target/draft changed after reconnect; message not sent');
    };
    if (!isClaudeManager(t)) {
      // Send to a sleeping/ended conversation reconnects it first. The reconnect is
      // idempotent, carries no message and is never a resend; the message below is one
      // new journaled Send to the rebounded generation.
      status('Reconnecting to '+t.title+' …','info');
      let record;
      try { record = validateReconnect(await claudeRequest('/api/claude/reconnect', claudeIdentity(t), 60000), t); }
      catch (e) {
        // A refused reconnect is not a broken target: keep the host's exact reason on screen.
        if (e.status === 409) { e.reconnectRefused = true;
          state.claudeReconnectRefusal = {executionId:t.executionId, generation:t.generation, reason:e.message}; }
        throw e;
      }
      state.claudeReconnectRefusal = null;
      original();
      const ready = await awaitReconnectedManager(t, record, original);
      original();
      t = await acceptClaudeTarget(ready.targetRef, frozen.targetRevision, () => { original(); return true; });
      rebindRevision = state.targetRevision;
      if (!isClaudeManager(t) || t.executionId !== frozen.target.executionId || t.uuid !== frozen.target.uuid ||
          t.generation !== ready.generation) throw new Error('reconnected generation did not validate; message not sent');
      rebound = true;
      // An earlier Send on this conversation (any generation) is never repeated by a new
      // UUID until it has been reconciled: the generation the pane was sitting on needs
      // its exact replay ACK, an earlier one its exact durable receipt. The reconnect
      // above may be what lets the native replay acknowledge it.
      const earlier = pendingConversationSend(frozen.target);
      if (earlier) {
        const sameGeneration = earlier.target.generation === frozen.target.generation;
        const resolved = () => sameGeneration ? earlier.acknowledged === true : earlier.receipt?.durable === true;
        if (!resolved()) await pollClaudeManager();
        if (!resolved())
          throw new Error('the earlier Send '+earlier.messageUuid+' on this conversation is not reconciled yet (its exact '+(sameGeneration ? 'replay ACK' : 'durable receipt')+' is still missing); the conversation was reconnected, the new message was not sent — inspect its receipt, then Send again');
      }
    }
    (rebound ? reboundExact : original)();
    const data = claudeEnvelope(await claudeRequest('/api/claude/state?'+claudeQuery(t)),t);
    state.claudeManager = data;
    const busy = claudeBusy(data.liveState), blocked = busy && claudeSteerBlock(data.liveState);
    if (blocked) { status('Not sent — the Manager is busy: '+busy+'. Your draft is kept and nothing was queued. Press Send again when it finishes, or press ■ Interrupt (next to Send) to stop it.','err'); return; }
    // Busy with its own turn: the message goes now as a host steer and reaches the Manager at its
    // next tool step, in the same turn (one long-running command delays it until that command ends).
    const steer = !!busy;
    if (!retainOriginal(full,'Claude Send')) return;
    if (!globalThis.crypto?.randomUUID) throw new Error('secure message UUID unavailable');
    // A reconnected Send freezes the *rebound* authority last, so an exact ACK may clear
    // the unchanged draft exactly as an ordinary live Send does.
    op = {schemaVersion:1,target:t,messageUuid:crypto.randomUUID(),original:full,submitted:text,at:Date.now(),
      frozen:rebound ? freezeRefineTarget() : frozen,acknowledged:false,uncertain:true,
      ...(!draftSaved ? {draftSaveFailed:true} : {}),...(steer ? {steer:true} : {})};
    saveClaudeJournal(K.claudeSend,op); state.claudeSend = op;
    // No awaits between the final revision check, journal readback and single semantic POST.
    (rebound ? reboundExact : original)();
    const result = await claudeRequest('/api/claude/send',{...claudeIdentity(t),messageUuid:op.messageUuid,original:full,submitted:text,...(steer ? {steer:true} : {})});
    applyClaudeSendReceipt(op,result);
    // Settings the host could not confirm never stop a Send; the receipt line says so.
    const warned = data.liveState?.settings_unverified === true && !data.liveState?.settings_mismatch ?
      ' · ⚠ settings unverified: the host could not confirm the model/thinking level (Send went through)' : '';
    status((steer ? (op.acknowledged ? 'Delivered to the busy Manager at its next tool step' : op.receipt?.durable ?
      'Queued for the busy Manager: it lands at its next tool step (one long-running command delays it until that command ends); draft kept until it is queued' :
      'acceptance uncertain; querying same UUID only') :
      'Manager Send receipt: '+(op.acknowledged ? 'exact replay acknowledged' : op.receipt?.durable ?
      'durably accepted; draft kept until exact replay ACK' : 'acceptance uncertain; querying same UUID only'))+warned,op.receipt?.durable ? 'ok' : 'err',
      op.acknowledged ? null : 'send:'+op.messageUuid);
  } catch (e) {
    if (op) {
      op.refused = [400,403,404,405,409,413].includes(e.status); op.uncertain = !op.refused; op.error = e.message;
      op.note = op.refused ? 'server refused; draft retained' : 'response uncertain; querying same UUID only, never resend';
      try { saveClaudeJournal(K.claudeSend,op); } catch (_) {}
    }
    if (e.reconnectRefused) status('Not sent — Claude would not reconnect '+(selected?.title || 'this Manager')+': '+e.message+
      (/New Manager/.test(e.message) ? '' : ' Way out: ＋ New Manager starts a fresh conversation (this one stays in the list).')+' Your draft is kept.','err');
    else status((op ? 'Manager Send '+(op.refused ? 'not submitted' : 'uncertain') : 'Manager Send not submitted')+': '+e.message+' · draft retained','err',
      op && !op.refused ? 'send:'+op.messageUuid : null);
    if (!op && !e.reconnectRefused && (e.targetValidation || [400,403,404,409].includes(e.status))) await blockClaudeTarget(e);
  } finally { state.sending = false; renderTarget(); }
}
// @beacon[
//   id=opc-pane@recheckClaudeSettings,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=The way out of a stale settings hold: one read-only native get_settings readback; a matching applied model/thinking clears the gate, anything else is shown in plain words. Nothing is set or sent.,
// ]
async function recheckClaudeSettings() {
  if (!isClaudeManager() || state.claudeControlSending) return;
  const t = {...state.claudeTarget};
  state.claudeControlSending = true; renderTarget();
  try {
    const data = claudeEnvelope(await claudeRequest('/api/claude/control',{...claudeIdentity(t),requestId:crypto.randomUUID(),operation:'get_settings'}),t);
    const r = data.settingsReadback || {};
    status(r.applied ? 'Settings re-checked: '+(r.model || 'model unknown')+' · thinking '+(r.effort || 'unknown')+
      (r.blocks_send ? ' · still held: '+((r.gate && r.gate.disclosure) || 'apply the model/thinking you want') : ' · verified, Send is open') :
      'Settings re-check did not verify: '+(r.disclosure || r.state || 'no readback')+' · apply the model/thinking you want, or try again', r.applied && !r.blocks_send ? 'ok' : 'err');
  } catch (e) { status('Settings re-check failed: '+e.message+' · nothing was changed','err'); }
  finally { state.claudeControlSending = false; await pollClaudeManager(); }
}
function applyClaudeControlReceipt(op,r) {
  if (!r) { op.error = 'receipt not found yet; query only'; return; }
  if (r.request_id !== op.requestId || r.generation !== op.target.generation) throw new Error('native control receipt mismatch');
  op.receipt = r;
  // A setter's reply still awaits readback. Uncertain keeps the operation pinned/queryable.
  // For interrupt, replied ends the control request, not the turn/background work.
  op.settled = r.applied === true || ['refused','failed','unverified','mismatch','mismatched','unsupported','superseded'].includes(r.state) ||
    (op.operation === 'interrupt' && r.state === 'replied');
  op.error = null; saveClaudeJournal(K.claudeControl,op);
  if (op.settled) resolveStatus('control:'+op.requestId, 'native control '+op.operation+' receipt: '+(r.applied === true ? 'applied and verified' : String(r.state || 'settled')), r.applied === true || op.operation === 'interrupt' ? 'ok' : 'info');
}

// @beacon[
//   id=opc-pane@controlClaude,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Explicit Manager-only native controls with persisted caller IDs and applied readback; lost replies reconcile without repeat controls or Refine/default mutation.,
// ]
async function controlClaude(operation,value) {
  if (state.backend === 'claude' && state.ocManagerTarget) return controlOpenCodeManager(operation,value);
  if (state.targetValidationAbort || state.claudePendingSelection) return status('Connecting to the selected Manager · control not submitted','info');
  if (state.claudeTarget?.source === 'host' && state.claudeTarget.role === 'Manager' && state.claudeTarget.lifecycle !== 'ended' && !isClaudeManager()) return status('Connecting · readiness is checked automatically; control not submitted','info');
  if (state.claudeTargetError) return status('native control blocked: '+state.claudeTargetError,'err');
  if (state.backend !== 'claude' || !isClaudeManager()) return status('native controls unavailable: view-only target','err');
  if (state.sending || state.refineAbort || state.claudeControlSending || state.claudeJournalError) return status('native control blocked; draft retained','err');
  if (pendingClaudeControl()) return status('previous control pending/uncertain; refresh its receipt, never repeat','err');
  if (!['set_model','apply_effort','interrupt'].includes(operation)) return;
  if (operation === 'interrupt' && !confirm('Stop the Manager’s current turn? Anything a tool already did stays done (nothing is rolled back). Your draft stays in the box; nothing is sent.')) return;
  const frozen = freezeRefineTarget(), t = frozen.target;
  let op;
  state.claudeControlSending = true; renderTarget();
  try {
    const data = claudeEnvelope(await claudeRequest('/api/claude/state?'+claudeQuery(t)),t);
    if (!sameRefineTarget(frozen)) throw new Error('target changed; control not submitted');
    if (operation !== 'interrupt' && claudeBusy(data.liveState)) throw new Error('the Manager is busy ('+claudeBusy(data.liveState)+'); try again when it finishes, or press ■ Interrupt');
    if (!globalThis.crypto?.randomUUID) throw new Error('secure control UUID unavailable');
    op = {schemaVersion:1,target:t,requestId:crypto.randomUUID(),operation,...(value ? {value} : {}),settled:false};
    saveClaudeJournal(K.claudeControl,op); state.claudeControl = op;
    const result = claudeEnvelope(await claudeRequest('/api/claude/control',{...claudeIdentity(t),requestId:op.requestId,operation,
      ...(operation === 'set_model' ? {model:value} : operation === 'apply_effort' ? {effort:value} : {reason:'Explicit pane operator Interrupt'})}),t);
    applyClaudeControlReceipt(op,result.controlReceipt);
    status('native control '+(op.receipt?.applied === true ? 'applied and verified' : 'pending; inspect native receipt/effective readback'),'info',
      op.settled ? null : 'control:'+op.requestId);
  } catch (e) {
    if (op) { op.refused = [400,403,404,405,409,413].includes(e.status); op.error = e.message; try { saveClaudeJournal(K.claudeControl,op); } catch (_) {} }
    status('native control '+(op && !op.refused ? 'uncertain — query only' : 'not submitted')+': '+e.message,'err',op && !op.refused ? 'control:'+op.requestId : null);
    if (!op && (e.targetValidation || [400,403,404,409].includes(e.status))) await blockClaudeTarget(e);
  } finally { state.claudeControlSending = false; renderTarget(); }
}

// ---------------------------------------------------------------- Send (OpenCode path preserved)
// @beacon[
//   id=opc-pane@send,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Target-discriminated Send: integrated OpenCode Managers journal one native steer and exact receipts; native Claude and legacy OpenCode follow/manual/queue keep their existing adapters and draft guards.,
// ]
async function send() {
  if (state.sending) return;
  if (state.backend === 'claude' && state.ocManagerTarget) return sendOpenCodeManager();
  if (state.backend === 'claude') return sendClaude();
  const targetRevision = state.targetRevision;
  const box = $('transcript');
  const full = box.value, text = full.trim();
  if (!text) return status('nothing to send', 'err');
  if (state.refineAbort) return status('a refine is running', 'err');
  const t = state.target && { ...state.target }; // immutable target snapshot across awaits
  if (!t) return status('no session to send to: open one in CodeNomad, or pick one', 'err');
  if (state.targetSource === 'stale' && !confirm('The CodeNomad watcher has gone quiet. Send to the last known session anyway?\n\n' + t.title)) return;
  // A Manager Session runs as manager or manager-gpt (inlined: the tests lift each function out on its own).
  if (t.agent && !['manager', 'manager-gpt'].includes(t.agent) && !confirm('This session is not a Manager Session (agent: ' + t.agent + '). Send anyway?\n\n' + t.title)) return;
  const mode = ls.get(K.targetMode, 'follow');
  const revision = state.watcher?.revision;
  const body = { text, sessionId: t.id, targetMode: mode === 'follow' ? 'follow' : 'manual', watcherRevision: revision };
  clearTimeout(draftTimer); const draftSavedAtSend = saveDraft();
  state.sending = true;
  $('send-btn').disabled = true;
  try {
    try {
      const active = (await oc('GET', '/api/session/active')).data || {};
      if (active && typeof active === 'object' && t.id in active) {
        if (!confirm('The session is busy. Queue this message for after the current turn ends?\n\nQueued Send will NOT apply the CodeNomad thinking selector or change the current generation.')) return;
        body.delivery = 'queue';
      }
    } catch (e) { /* server rechecks busy status before any thinking switch */ }
    if (state.backend === 'claude' || state.targetRevision !== targetRevision || state.target?.id !== t.id || ls.get(K.targetMode, 'follow') !== mode ||
        (mode === 'follow' && state.watcher?.revision !== revision)) throw new Error('target/selection changed; check it and Send again');
    if (!retainOriginal(full, 'Send')) return;
    const resp = await fetch('/api/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const r = await resp.json();
    if (!resp.ok) throw new Error(r.error || ('HTTP ' + resp.status));
    const id = r.data && r.data.id;
    let lastSaved = true;
    try { ls.set(K.lastSent, text); } catch (e) { lastSaved = false; }
    const copied = await copyText(text);
    let draftSaved = draftSavedAtSend, storedChanged = false;
    if (draftSaved && state.backend !== 'claude' && state.targetRevision === targetRevision && box.value === full) {
      try {
        if (clearSavedDraft(full)) { box.value = ''; draftChanged(); clearTimeout(draftTimer); }
        else storedChanged = true;
      } catch (_) { draftSaved = false; }
    } // retain edits/retarget or another tab's saved text during Send
    status('📤 sent' + (body.delivery ? ' (queued)' : '') + ' → ' + t.title + (id ? '  ' + id : '') + ' · ' + r.thinking
      + (state.backend === 'claude' || state.targetRevision !== targetRevision ? ' · target changed; current draft retained' : '')
      + ' · original in recovery' + (copied ? '' : ' · clipboard unavailable') + (lastSaved ? '' : ' · last-sent shortcut not saved')
      + (storedChanged ? ' · stored draft changed; draft retained' : '')
      + (draftSaved ? '' : ' · draft NOT cleared: cleared draft could not be persisted/read back'), draftSaved ? 'ok' : 'err');
    setTimeout(() => refreshContext(false), 1500);
  } catch (e) {
    status('send failed: ' + e.message + ' (the text is still in the box)', 'err');
  } finally { state.sending = false; $('send-btn').disabled = state.backend === 'claude'; box.focus(); }
}

// ---------------------------------------------------------------- settings UI
function renderModelSelect() {
  const p = getProvider();
  $('provider-select').value = p;
  const sel = $('model-select'); sel.innerHTML = '';
  for (const m of getModels(p)) sel.add(new Option(m, m));
  sel.value = getModel(p);
  $('think-select').value = getThink();
  renderOutputSettings();
}
function renderPart() {
  const part = PROMPT_PARTS.find((x) => x.id === $('part-select').value) || PROMPT_PARTS[0];
  $('part-text').value = getPart(part);
  $('part-note').textContent = ls.get(part.storage) === null ? 'default' : 'your override';
}
function renderDict() {
  const terms = dictTerms();
  $('dict-count').textContent = terms.length ? '(' + terms.length + ' terms)' : '(none)';
  $('dict-text').value = terms.length ? JSON.stringify(terms) : '';
}
function parseDict(v) {
  let s = (v || '').trim();
  if (!s) return [];
  if (s.includes('```')) s = s.replace(/```[a-zA-Z]*/g, '').trim();
  let parsed = null;
  try { parsed = JSON.parse(s); } catch (e) {}
  if (!Array.isArray(parsed)) { const i = s.indexOf('['), j = s.lastIndexOf(']'); if (i !== -1 && j > i) { try { parsed = JSON.parse(s.slice(i, j + 1)); } catch (e) {} } }
  const seen = new Set(), out = [];
  const add = (t) => { t = (t || '').trim(); if (t && !seen.has(t)) { seen.add(t); out.push(t); } };
  if (Array.isArray(parsed)) parsed.forEach((x) => { if (typeof x === 'string') add(x); });
  else s.split('\n').forEach((line) => {
    let t = line.trim(); if (!t || t === '[' || t === ']') return;
    if (t.endsWith(',')) t = t.slice(0, -1).trim();
    if (t.length >= 2 && t[0] === '"' && t[t.length - 1] === '"') t = t.slice(1, -1);
    add(t);
  });
  return out;
}
// DevTools' copy() returns undefined, which reads like a failure; the wrapper returns a confirmation instead
// (the count and the key NAMES, never the values, so no API key is echoed into the console).
const EXPORT_SNIPPET = "(()=>{const o=Object.fromEntries(Object.keys(localStorage).filter(k=>/^refine_(provider|think_level|history_mode|dictionary|total_cost|system_prompt|context_preamble|transcription_preamble|final_fence|read_aloud_(system_prompt|context_preamble|message_preamble|final_fence)|(anthropic|openrouter|deepinfra)_(api_key|model|models_list))$/.test(k)).map(k=>[k,localStorage.getItem(k)]));copy(JSON.stringify(o));return 'Copied '+Object.keys(o).length+' Refine settings: '+Object.keys(o).join(', ')+'. Now paste them into the pane: Refine settings > Import.'})()";

function wireSettings() {
  $('provider-select').onchange = (e) => { ls.set(K.provider, e.target.value); renderModelSelect(); };
  $('model-select').onchange = (e) => { ls.set(K.model[getProvider()], e.target.value); renderOutputSettings(); };
  $('think-select').onchange = (e) => ls.set(K.think, e.target.value);
  $('model-add').onclick = () => {
    const p = getProvider(); const m = (prompt('Model id for ' + PROVIDERS[p].label + ':') || '').trim();
    if (!m) return; const list = getModels(p); if (!list.includes(m)) list.unshift(m);
    ls.set(K.models[p], JSON.stringify(list)); ls.set(K.model[p], m); renderModelSelect();
  };
  $('model-remove').onclick = () => {
    const p = getProvider(); const m = $('model-select').value;
    const list = getModels(p).filter((x) => x !== m); if (!list.length) return status('keep at least one model', 'err');
    ls.set(K.models[p], JSON.stringify(list)); ls.del(K.model[p]); renderModelSelect();
  };
  $('key-clear').onclick = () => { const p = getProvider(); if (confirm('Clear the stored ' + PROVIDERS[p].label + ' API key?')) { ls.del(K.key[p]); status('key cleared', 'ok'); } };
  $('history-mode').value = refineHistoryMode();
  $('history-mode').onchange = (e) => {
    ls.set(K.historyMode, e.target.value === 'budgeted' ? 'budgeted' : 'whole'); // one click back to the whole conversation
    e.target.value = refineHistoryMode();
    state.ctx = null;
    refreshContext();
  };
  $('ctx-kb').value = ls.get(K.ctxKb, '50');
  $('ctx-kb').onchange = (e) => {
    ls.set(K.ctxKb, String(Math.max(5, parseInt(e.target.value, 10) || 50)));
    if (refineHistoryMode() === 'budgeted') refreshContext(); // the budget only shapes a budgeted read
  };
  renderOutputSettings();
  $('output-tokens').onchange = (e) => {
    const n = Number(e.target.value);
    const policy = refineOutputPolicy(getProvider(), getModel(getProvider()));
    if (!Number.isInteger(n) || n < 1024 || n > policy.maxTokens) return status('output budget must be 1024–' + policy.maxTokens + ' tokens for this provider/model', 'err');
    ls.set(K.outputTokens, String(n));
    renderOutputSettings();
  };
  $('output-reset').onclick = () => { ls.del(K.outputTokens); renderOutputSettings(); status('Refine output budget reset to this model default', 'ok'); };

  for (const part of PROMPT_PARTS) $('part-select').add(new Option(part.label, part.id));
  $('part-select').onchange = renderPart;
  $('part-save').onclick = () => {
    const part = PROMPT_PARTS.find((x) => x.id === $('part-select').value); const text = $('part-text').value;
    const missing = part.requires.filter((r) => !text.includes(r));
    if (missing.length) return status('the ' + part.id + ' part must keep ' + missing.join(', '), 'err');
    if (text === part.def) ls.del(part.storage); else ls.set(part.storage, text);
    renderPart(); status('prompt part saved', 'ok');
  };
  $('part-restore').onclick = () => { const part = PROMPT_PARTS.find((x) => x.id === $('part-select').value); ls.del(part.storage); renderPart(); status('default restored', 'ok'); };
  renderPart();

  $('dict-file').onchange = async (e) => {
    const f = e.target.files && e.target.files[0]; if (!f) return;
    const terms = parseDict(await f.text()); ls.set(K.dict, JSON.stringify(terms)); renderDict(); status('dictionary: ' + terms.length + ' terms', 'ok');
  };
  $('dict-save').onclick = () => { const terms = parseDict($('dict-text').value); ls.set(K.dict, JSON.stringify(terms)); renderDict(); status('dictionary: ' + terms.length + ' terms', 'ok'); };
  renderDict();

  $('export-snippet').textContent = EXPORT_SNIPPET;
  $('export-copy').onclick = () => navigator.clipboard.writeText(EXPORT_SNIPPET).then(() => status('snippet copied', 'ok'));
  $('import-btn').onclick = () => {
    let obj; try { obj = JSON.parse($('import-text').value); } catch (e) { return ($('import-note').textContent = 'not valid JSON'); }
    let n = 0; for (const [k, v] of Object.entries(obj || {})) if (IMPORTABLE.test(k) && typeof v === 'string') { ls.set(k, v); n++; }
    $('import-note').textContent = 'imported ' + n + ' settings'; $('import-text').value = '';
    if ($('history-mode')) $('history-mode').value = refineHistoryMode();
    renderModelSelect(); renderPart(); renderDict();
  };
  renderModelSelect();
}

// ---------------------------------------------------------------- wiring
let draftTimer = null;
function clearSavedDraft(original) {
  if (ls.get(K.draft) !== original) return false;
  ls.set(K.draft,'');
  if (ls.get(K.draft) !== '') throw new Error('draft clear readback failed');
  return true;
}
function saveDraft() {
  try { ls.set(K.draft, $('transcript').value);
    if (K.managerDraft) ls.set(K.managerDraft,JSON.stringify({page:state.pageEpoch,revision:state.draftRevision,token:globalThis.crypto?.randomUUID?.() || Math.random()}));
    return true; }
  catch (e) { status('draft NOT saved: browser storage unavailable/full; keep the page open and save text externally', 'err'); return false; }
}

// ---- View on the desktop, never a new tab: a handle clicked in this page (the inspector, the editor) or the header's
// 📄 View opens View in the conversation column's own View pane in Claude mode (the sessions frame's opcDesktopView),
// and in OpenCode mode (CodeNomad's split, no conversation column) as a sheet over this pane, with Close.
// @beacon[
//   id=opc-pane@desktopOpenInView,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=The desktop editor page's Open in View (handle-links.js through opcOpenInView, and the header's 📄 View with no handle): Claude mode hands the handle (a fresh View root, from the editor, or from the inspector rooted at the held call's conversation turn when known: opcOpenInView, inspectorTurn) to the sessions frame's View pane, waiting for that frame's load when it is not up yet; OpenCode mode opens the View sheet over the pane (glimpse.html in desktop-view-frame, loaded on first use with ?handle=…; a loaded one gets openHandle, or with no handle picks up the stored desktop history, resume). Never a new tab.,
// ]
function desktopOpenInView(handle = null, origin = null) {
  if (document.body?.classList?.contains('claude-mode')) {
    const frame = $('claude-sessions-frame');
    const go = () => {
      let view = null; try { view = frame?.contentWindow?.opcDesktopView || null; } catch (_) { return false; }
      if (!view) return false;
      if (handle) view.open(handle, origin || { from: 'outside' }); else view.show('view');
      return true;
    };
    if (!go()) frame?.addEventListener?.('load', go, { once: true });
    return;
  }
  const sheet = $('view-sheet'), frame = $('desktop-view-frame');
  if (!sheet || !frame?.setAttribute) return;
  const view = () => { try { return frame.contentWindow?.opcGlimpseView || null; } catch (_) { return null; } };
  const open = () => void view()?.openHandle(handle, 'node', origin || { from: 'outside' });
  if (!frame.getAttribute('src')) {
    const query = new URLSearchParams(handle ? { handle } : {});
    if (handle) for (const key of ['from', 'where']) if (typeof origin?.[key] === 'string' && origin[key]) query.set(key, origin[key]);
    frame.setAttribute('src', '/glimpse.html' + (handle ? '?' + query : ''));
  } else if (handle) { if (view()) open(); else frame.addEventListener('load', open, { once: true }); }
  else if (sheet.hidden) void view()?.resume?.();
  sheet.hidden = false;
}
function closeViewSheet() { const sheet = $('view-sheet'); if (!sheet || sheet.hidden) return; sheet.hidden = true; $('transcript')?.focus?.(); }
// Where a handle clicked in this page came from, for View's root widget: in the inspector, the held call's
// conversation turn when the sessions frame knows it (opcDesktopView.turnOf), so View roots there as for a handle
// clicked in the conversation, with Show in conversation; otherwise "tapped in the inspector" or "in the editor".
function inspectorTurn() {
  if (!document.body?.classList?.contains('claude-mode')) return null;
  try {
    const callRef = globalThis.opcToolInspector?.heldCallRef?.();
    return callRef ? $('claude-sessions-frame')?.contentWindow?.opcDesktopView?.turnOf?.(callRef) || null : null;
  } catch (_) { return null; }
}
globalThis.opcOpenInView = (handle, context) => {
  const inspector = !!context?.node?.closest?.('#tool-inspector'), turn = inspector ? inspectorTurn() : null;
  desktopOpenInView(handle, { from: 'outside', where: inspector ? 'the inspector' : 'the editor', ...(turn ? { turn } : {}) });
};

// @beacon[
//   id=opc-pane@wire,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=The existing editor's dictation, backend-qualified Manager target/recovery, OpenCode catalog controls and explicit no-context wiring plus global shortcuts; storage revisions protect other tabs, and tool reading/copy/resize never sends or refines a draft.,
// ]
function wire() {
  const box = $('transcript');
  box.value = ls.get(K.draft, ''); draftChanged();
  box.addEventListener('input', () => { draftChanged(); clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 400); });
  $('refine-btn').onclick = refine;
  $('send-btn').onclick = send;
  $('restore-btn').onclick = () => {
    if (state.refineAbort || state.sending) return status('wait for Refine/Send before restoring', 'err');
    const last = ls.get(K.lastSent); if (!last) return status('nothing sent yet', 'err');
    if (box.value.trim() && !confirm('Replace what is in the box with the last sent message?')) return;
    if (box.value && !retainOriginal(box.value, 'last-sent restore')) return;
    box.value = last; draftChanged(); saveDraft(); box.focus();
  };
  $('recovery-btn').onclick = showRecovery;
  if ($('claude-fold')) { $('claude-fold').onclick = () => toggleClaudeDetails(); toggleClaudeDetails(ls.get(K.detailsOpen, 'false') === 'true'); }
  columnDivider($('main-divider'), $('pane-layout'), '--main-split', K.mainSplit, 40, 20, 80);
  columnDivider($('tool-divider'), $('pane-editor'), '--tool-split', K.toolSplit, 45, 20, 70, true);
  $('recovery-close').onclick = () => $('recovery-dialog').close();
  $('backend-select').onchange = (e) => setBackend(e.target.value);
  $('claude-state-refresh').onclick = pollClaudeManager;
  if ($('claude-settings-recheck')) $('claude-settings-recheck').onclick = () => void recheckClaudeSettings();
  $('claude-reselect').onclick = reselectCurrentManager;
  wireNewManager(); $('claude-sessions-frame')?.addEventListener?.('load', wireNewManager);
  $('claude-model-apply').onclick = () => controlClaude('set_model',$('claude-model').value);
  $('claude-effort-apply').onclick = () => controlClaude('apply_effort',$('claude-effort').value);
  if ($('claude-interrupt-now')) $('claude-interrupt-now').onclick = () => controlClaude('interrupt');
  if ($('claude-send-release')) $('claude-send-release').onclick = () => releaseClaudeSend();
  if ($('claude-journal-export')) $('claude-journal-export').onclick = () => exportClearClaudeJournal();
  $('claude-wait').onclick = () => { status('Waiting only — draft retained; nothing queued or sent','info'); pollClaudeManager(); };
  if ($('opencode-model')) $('opencode-model').onchange=()=>fillOpenCodeModels($('opencode-model'),$('opencode-variant'),state.ocManagerCatalog);
  if ($('opencode-model-apply')) $('opencode-model-apply').onclick=()=>controlOpenCodeManager('set_model');
  if ($('opencode-refresh')) $('opencode-refresh').onclick=()=>pollOpenCodeManager();
  if ($('opencode-send-release')) $('opencode-send-release').onclick=()=>{
    const op=openCodeManagerApi().pending(state.ocManagerTarget);
    if (op) { openCodeManagerApi().release(op); status('OpenCode Send released after your check · original retained; never resent','info'); renderTarget(); }
  };
  globalThis.addEventListener?.('storage',e=>{ if (e.key === K.managerDraft) draftChanged(); });
  $('target-select').onchange = (e) => {
    if (state.backend === 'claude') return;
    ls.set(K.targetMode, e.target.value); invalidateTarget(); state.target = null; resolveTarget();
  };
  $('ctx-refresh').onclick = () => refreshContext(true);
  $('native-no-context').onchange = (e) => {
    try { ls.set(K.nativeNoContext, e.target.checked ? 'true' : 'false'); }
    catch (_) { e.target.checked = ls.get(K.nativeNoContext, 'false') === 'true'; return status('Context choice not saved; unchanged', 'err'); }
    state.ctx = null;
    if (state.refineAbort) { state.refineAbort.reason_ = 'context'; state.refineAbort.abort(); }
    renderTarget();
  };
  $('ctx-view').onclick = showRefineView;
  $('ctx-close').onclick = () => $('ctx-dialog').close();
  if ($('glimpse-link')) $('glimpse-link').onclick = () => desktopOpenInView();
  if ($('view-sheet-close')) $('view-sheet-close').onclick = closeViewSheet;
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('view-sheet')?.hidden === false) { e.preventDefault(); closeViewSheet(); return; }
    const sendKey = (e.key === 'Enter' && e.ctrlKey && !e.shiftKey && !e.altKey)          // Ctrl+Enter
      || (e.key === 'Enter' && e.ctrlKey && e.altKey && e.shiftKey)                       // Ctrl+Alt+Shift+Enter (ULTIMATE ULTIMATE)
      || (e.key === 'F11' && e.shiftKey) || (e.key === 'F4' && !e.ctrlKey && !e.altKey);  // Shift+F11 (AHK) / F4
    // Reading/copying/resizing a tool must never submit or refine dictation.
    if ((sendKey || (e.altKey && !e.ctrlKey && /^(r|R)$/.test(e.key))) &&
        e.target?.closest?.('#tool-inspector, #inspector-outer-divider')) {
      e.preventDefault(); return;
    }
    if (sendKey) { e.preventDefault(); send(); return; }
    if (e.altKey && !e.ctrlKey && (e.key === 'r' || e.key === 'R')) { e.preventDefault(); refine(); }
  });
  wireSettings();
}

// @beacon[
//   id=opc-pane@paneActivityLease,
//   slice_labels=opc-pane,
//   kind=ast,
//   comment=Holds a lease on the pane server while Send, Refine, a Manager control, a launch or an incident resolve runs (the server cannot see a Refine's provider call or a Send from journal to receipt), so start.sh restart waits for a quiet moment; leases expire if the tab closes.,
// ]
function paneActivity(kind, work) {
  return async function (...args) {
    // Fire-and-forget beacons: never a delay before the work, never a failure of it.
    const beacon = globalThis.navigator?.sendBeacon?.bind(globalThis.navigator);
    if (typeof beacon !== 'function' || typeof Blob !== 'function') return work.apply(this, args);
    const id = globalThis.crypto?.randomUUID?.() || 'activity-'+Date.now()+'-'+Math.random().toString(36).slice(2);
    const post = active => { try { beacon('/api/pane-activity', new Blob([JSON.stringify({schemaVersion:1, id, kind, active})], {type:'text/plain'})); }
      catch (_) { /* best effort: the restart gate also sees the server's own requests */ } };
    post(true);
    const renew = setInterval(() => post(true), 60000);
    try { return await work.apply(this, args); } finally { clearInterval(renew); post(false); }
  };
}
send = paneActivity('send', send);
refine = paneActivity('refine', refine);
controlClaude = paneActivity('control', controlClaude);
controlOpenCodeManager = paneActivity('control', controlOpenCodeManager);
recheckClaudeSettings = paneActivity('control', recheckClaudeSettings);
launchNewManager = paneActivity('launch', launchNewManager);
sendOpening = paneActivity('send', sendOpening);
sendOpeningOpenCode = paneActivity('send', sendOpeningOpenCode);
resolveClaudeIncident = paneActivity('resolve', resolveClaudeIncident);

async function main() {
  wire();
  initializeClaudeJournal();
  await initializeTargetState();
  // Native recovery/polling must never wait for unrelated OpenCode services.
  if (state.claudeJournalError) status('native recovery blocked: '+state.claudeJournalError,'err');
  else if (!state.claudeTargetError) status(state.backend === 'claude' ? state.nativeTargetStatus : 'ready', 'ok');
  setInterval(pollWatcher, 700);
  setInterval(loadSessions, 60000);
  pollClaudeManager();
  setInterval(pollClaudeManager,2000);
  setInterval(() => { if (state.target && !state.refineAbort) refreshContext(false); }, 30000);
  $('transcript').focus();
  // Also allow a cold OpenCode page to switch to Claude while that service is
  // unavailable: all native timers are already running, and these are independent.
  if (state.backend !== 'claude') { void loadSessions(); void pollWatcher(); }
}
main();
