'use strict';

const phoneManager = (() => {
  const $ = id => document.getElementById('m-' + id);
  const runningStrip=globalThis.opcToolReading?.runningTools?.($('running-tools'),document);
  const NS = 'pane_m_v1:', KEYS = {draft:NS+'draft', target:NS+'target', original:NS+'original', send:NS+'send:', control:NS+'control:', noContext:NS+'no_context', selectionEpoch:NS+'selection_epoch', selectionMarker:NS+'selection_marker', ocTarget:NS+'target:opencode:', claudeNames:NS+'claude_manager_names', thinkingRound:NS+'thinking_current_round'};
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const OC_SID = /^ses_[A-Za-z0-9]{8,64}$/;
  // The lead's shared server-journal mechanics (static/opencode-manager.js). Absent (an old fixture or an older
  // page) only disables first-class OpenCode Managers; native Claude Managers keep working exactly as before.
  const ocApi = globalThis.opcOpenCodeManager?.create({prefix:NS+'manager'}) || null;

  // A Manager row's backend and the one identity this page selects it by: a Claude executionId, an OpenCode ses_ id.
  // Never an OpenCode id in Claude execution fields: every Claude-only adapter stays behind backend==='claude'.
  function backendOf(row) { return row?.backend === 'opencode' || OC_SID.test(row?.sessionId || '') ? 'opencode' : 'claude'; }
  function mid(row) { return !row ? null : backendOf(row) === 'opencode' ? (row.sessionId || row.id || null) : row.executionId || null; }
  function ocTarget(t) { return t && backendOf(t) === 'opencode' ? {backend:'opencode',sessionId:t.sessionId,witness:t.witness} : null; }
  function ocReady(t) { return !!ocApi && !!t && backendOf(t) === 'opencode' && !!t.witness && ocApi.valid(ocTarget(t)) && t.capabilities?.send === true; }
  function ocKnown(row) { return backendOf(row) === 'opencode' && row.kind === 'manager' && row.manager === true && OC_SID.test(row.id || row.sessionId || ''); }
  function ocName(model) {
    if (!model || typeof model !== 'object') return 'model unknown';
    const label = typeof model.name === 'string' && model.name ? model.name : [model.providerID, model.id].filter(Boolean).join('/') || 'model unknown';
    return label + (model.variant ? ' · ' + model.variant : '');
  }
  function noContext() { return setting(KEYS.noContext,'false') === 'true'; }
  function selectionToken(id) { return !id ? null : (OC_SID.test(id) ? 'opencode:' : 'claude:')+id; }
  function createdMs(row) {
    const n=Number(row.createdAt);
    return n>0 ? (backendOf(row)==='opencode' && n<100000000000 ? n*1000 : n) : Date.parse(row.createdAt) || 0;
  }
  // The names a Manager row is known by, as the desktop reads them (sessions.js managerKey): an OpenCode Manager's
  // title; a Claude Manager's label, then its title. They order the chips and name ↳ New sub-session.
  function managerNames(row) { return backendOf(row) === 'opencode' ? [row.title] : [row.sessionLabel,row.title]; }
  // The Claude Managers' names from the last good Claude read, kept on this phone for ↳ alone: a page loaded while the
  // Claude host is down lists no Claude chip, yet the newest primary is usually a Claude Manager. Display names only,
  // never an identity or a target; written only when they change.
  function rememberClaudeNames(rows) {
    const value=JSON.stringify(rows.filter(t=>backendOf(t) === 'claude').map(managerNames).slice(0,200));
    if (value === state.claudeNamesSaved) return;
    try { write(KEYS.claudeNames,value); state.claudeNamesSaved=value; } catch (_) { /* ↳ then names from the chips alone */ }
  }
  function rememberedClaudeNames() {
    try {
      const names=JSON.parse(read(KEYS.claudeNames) || '[]');
      return Array.isArray(names) ? names.filter(n=>Array.isArray(n) && n.every(x=>x == null || typeof x === 'string')) : [];
    } catch (_) { return []; }
  }
  // The chip strip's order, the desktop Manager Sessions' own (manager-order.js): by Manager-Session number, highest
  // first, each primary followed by its sub-sessions (so the newest primary is always the leftmost chip), both
  // backends together; liveness never moves a chip. A page without manager-order.js keeps newest created first.
  function managerOrder(a,b) {
    const compare=globalThis.opcManagerOrder?.compare;
    if (!compare) return createdMs(b)-createdMs(a) || String(mid(a)).localeCompare(String(mid(b)));
    const key=row=>({names:managerNames(row), created:createdMs(row), id:mid(row)});
    return compare(key(a),key(b));
  }
  // A Manager's last activity in seconds, from what the list already carries: a Claude row's host activity stamp,
  // its transcript's last turn or its creation; an OpenCode row's update, idle or creation time. null = unknown.
  function lastActive(row) {
    const stamps=backendOf(row) === 'opencode' ? [row.updatedAt,row.idleAt,row.createdAt] :
      [row.liveState?.last_activity_at,row.turnFacts?.lastAt,row.createdAt];
    const known=stamps.filter(n=>typeof n === 'number' && Number.isFinite(n) && n > 0).map(n=>n > 100000000000 ? n/1000 : n);
    return known.length ? Math.max(...known) : null;
  }
  // Open: a Claude Manager neither sleeping nor ended (live, getting ready); an OpenCode Manager while busy.
  function openManager(row) { return backendOf(row) === 'opencode' ? row.busy === true : !['sleeping','ended'].includes(row.lifecycle); }
  // The chips shown (manager-order.js recent): active within STRIP_DAYS, open, the newest Manager-Session number
  // (primary and sub-sessions) and the selected one; state.managers itself stays whole for selection and ↳ names.
  function stripRows() {
    const recent=globalThis.opcManagerOrder?.recent; if (!recent) return state.managers;
    return recent(state.managers.map(row=>({row,names:managerNames(row),lastActive:lastActive(row),open:openManager(row),
      keep:mid(row) === state.selected})),Date.now()/1000).map(item=>item.row);
  }
  const RESYNC = new Set(['stale','stale-epoch','stale-generation','gap','reload-required','changed-index','generation','target-mismatch','view-only','exit']);
  // A slow or failed conversation read never blanks the messages on screen: this quiet line says so and clears
  // itself with the next good read (SLOW_READ_MS: a read still unanswered after this long says it too).
  const CATCHING_UP = 'Catching up … the messages on screen stay; newer ones follow by themselves.', SLOW_READ_MS = 4000;
  // The facade's per-read damage witness (a top-level Boolean on the events page): this conversation's stored rows
  // could not be read, so the page shows what the raw log still yields. It is a quiet line of its own beside
  // "Catching up", never a warning, and never a reason to blank rows, disable a button or change Send/Refine.
  const DAMAGED_ROWS = 'stored rows for this conversation are damaged; showing what the raw log allows';
  const REFUSED = new Set([400,403,404,405,409,413]);
  const IMPORTABLE = /^refine_(provider|think_level|history_mode|dictionary|system_prompt|context_preamble|transcription_preamble|final_fence|read_aloud_(system_prompt|context_preamble|message_preamble|final_fence)|(anthropic|openrouter|deepinfra)_(api_key|model|models_list))$/;
  const DEFAULT_MODELS = {anthropic:'claude-opus-5-5', openrouter:'anthropic/claude-opus-5.5', deepinfra:'zai-org/GLM-5.2'};
  const state = {managers:[], target:null, selected:null, selectionRevision:0, selectionEpoch:0, selectionMarker:null, draftRevision:0, page:crypto.randomUUID(), bootDraft:null,
    journals:new Map(), receiptBusy:new Set(), badJournals:new Map(), chat:null, listBusy:false, pollBusy:false, sending:false, checking:false,
    refineAbort:null, follow:true, stripDriving:false, stripConvAt:null, scrollIntent:-Infinity, nextList:0, config:new Map(), view:'chat', maximized:null, refineViewMode:'context', refineViewSeq:0,
    // Tool reading preferences: page-local, defaults after every reload, as on the desktop.
    toolView:{input:'readable',output:'readable'}, wrap:true, launch:null,
    contexts:new Map(), contextNext:new Map(), contextBusy:new Set(), usage:null, usageError:null, usageBusy:false, usageNext:0, stripUsageNext:0,
    // A non-Manager session opened read-only from Programs (Read ›): it owns the conversation panel while
    // open; state.selected/target/chat, the draft, journals, Refine and Re-check stay with the Manager.
    viewing:null, ocActivityBusy:false, ocActivityNext:0,
    // Press and hold a Manager chip: its model/thinking sheet (tune) and the latest control per execution.
    tune:null, controls:new Map(), controlBusy:new Set(), interruptSending:false,
    // First-class OpenCode Managers: their measured Context reads, the shared journal's last read error, and the
    // slower state cadence (the fast activity read carries busy).
    ocContexts:new Map(), ocContextNext:new Map(), ocContextBusy:new Set(), ocJournalError:null, ocNextState:0, ocControlNext:new Map()};

  function notice(text, error=false, id='status') { $(id).textContent = text; $(id).dataset.error = String(error); }
  function read(key) { return localStorage.getItem(key); }
  function write(key, value) {
    try {
      localStorage.setItem(key, value);
      if (read(key) !== value) throw new Error('browser storage readback failed');
    } catch (e) {
      throw new Error('Browser storage is full or unavailable. Copy your draft and Last original somewhere safe; '+
        'do not clear site data while any Send is unresolved. '+e.message);
    }
  }
  function setting(key, fallback=null) {
    if (state.config.has(key)) return state.config.get(key);
    try { return read(key) ?? fallback; } catch (_) { return fallback; }
  }
  function saveSetting(key, value) {
    state.config.set(key,value);
    try { write(key,value); return true; }
    catch (e) { notice('Setting is only on this page: '+e.message,true,'setup-status'); return false; }
  }
  function saveDraft(text=$('draft').value) {
    // The saved draft belongs to the selected target: a Claude executionId (never an OpenCode ses_ id here), and the
    // backend-qualified selection key separately for the OpenCode journal's exact-target checks.
    write(KEYS.draft,JSON.stringify({schemaVersion:1,target:state.selected,
      executionId:OC_SID.test(state.selected || '') ? null : (state.selected || null),text,
      owner:state.selected || null,selectionToken:selectionToken(state.selected),draftRevision:state.draftRevision,selectionEpoch:state.selectionEpoch,
      writer:state.page,revisionToken:crypto.randomUUID()}));
  }
  function changedDraft() {
    state.draftRevision++;
    try { saveDraft(); } catch (e) { notice('Draft is visible but not saved: '+e.message,true); }
    fitDraft();
  }
  // Layout viewport, never the keyboard's visualViewport: an iPad stays an iPad with its keyboard up.
  const compactDraft=window.matchMedia('(max-width: 499px), (max-height: 499px)');
  // @beacon[
  //   id=opc-pane@phoneFitDraft,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Normal phone-sized layout (either layout viewport dimension below 500 CSS px) keeps three scrolling lines regardless of dictation length; larger layouts retain content autosizing to the CSS cap. Maximize owns its own height. Preserve newest-message following and keyboard-shell home without using keyboard-height guesses.,
  // ]
  function fitDraft() {
    const box=$('draft'), scroll=$('conversation'), follow=state.follow;
    if (state.maximized === 'draft') { box.style.height=''; return; }
    if (compactDraft.matches) box.style.height=''; // the CSS media query owns the fixed compact height
    else { box.style.height='auto'; box.style.height=(box.scrollHeight+2)+'px'; }
    if (follow) scroll.scrollTop=scroll.scrollHeight;
    thumbStrip?.fit();  // keep the keyboard shell at home, including an iPad's growing box
  }
  function retainOriginal(text) {
    $('original').value = text;
    try { write(KEYS.original,text); return ''; }
    catch (e) { return 'Original remains in Last original on this page; browser backup failed: '+e.message; }
  }
  function registered(t) {
    return !!t && t.source === 'host' && t.role === 'Manager' && UUID.test(t.uuid) &&
      t.executionId === 'cc-'+t.uuid && Number.isSafeInteger(t.generation) && t.generation > 0 && typeof t.targetRef === 'string' && !!t.targetRef;
  }
  function writable(t) { return registered(t) && t.capabilities?.nativeInput === true && t.capabilities?.controls === true; }
  function reconnectable(t) { return registered(t) && ['sleeping','ended'].includes(t.lifecycle) && t.capabilities?.reconnect === true; }
  function identity(t) { return {schemaVersion:1,backend:'claude',targetRef:t.targetRef,executionId:t.executionId,generation:t.generation}; }
  function query(t, extra={}) {
    const {schemaVersion,backend,...fields} = identity(t);
    return new URLSearchParams({...fields,...extra}).toString();
  }
  function envelope(data, t=null, backend='claude') {
    if (!data || data.schemaVersion !== 1 || data.backend !== backend ||
        (t && ['targetRef','executionId','generation','uuid'].some(k=>data[k] !== t[k])))
      throw new Error('Manager response identity/schema mismatch; draft retained');
    return data;
  }
  async function request(url, body, {signal, budget=12000, backend='claude'}={}) {
    // This observes HTTP, never cancels host work or permits a repeated Send.
    const controller = new AbortController(), cancel = () => controller.abort();
    signal?.addEventListener('abort',cancel,{once:true});
    if (signal?.aborted) cancel();
    const timer = setTimeout(cancel,budget);
    try {
      const response = await fetch(url,{method:body === undefined ? 'GET' : 'POST',cache:'no-store',signal:controller.signal,
        ...(body === undefined ? {} : {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
      const data = JSON.parse(await response.text());
      if (!response.ok) { const e = new Error(data.message || data.error || 'HTTP '+response.status); e.status=response.status; e.code=data.status; throw e; }
      envelope(data,null,backend);
      if (RESYNC.has(data.status)) { const e = new Error(data.error || data.status); e.code=data.status; throw e; }
      return data;
    } finally { clearTimeout(timer); signal?.removeEventListener('abort',cancel); }
  }
  // Where a conversation's pages and tool bodies are read, and whose pages they must be: the selected
  // Manager (exact execution and generation, unchanged), a viewed Claude session (its host execution,
  // else the transcript handle it was opened with), a viewed OpenCode session (its ses_ id) or a 📦 History
  // conversation (its archived key; read-only by nature, its tools a one-line preview with no body to read).
  const claudeEvents=(chat,q)=>'/api/claude/events?'+new URLSearchParams({sessionRef:chat.sessionRef,scopeRef:chat.scopeRef,...q});
  const claudeTool=(chat,callRef,fileEpoch)=>'/api/claude/tool-record?'+new URLSearchParams({callRef,fileEpoch});
  const READERS={
    manager:{backend:'claude',events:claudeEvents,tool:claudeTool,
      same:(chat,data)=>data.source === 'host' && data.role === 'Manager' && data.executionId === chat.executionId && data.generation === chat.generation},
    claude:{backend:'claude',events:claudeEvents,tool:claudeTool,
      same:(chat,data)=>chat.executionId ? data.source === 'host' && data.executionId === chat.executionId : !data.executionId && data.sessionRef === chat.sessionRef},
    opencode:{backend:'opencode',events:(chat,q)=>'/api/opencode/events?'+new URLSearchParams({id:chat.sessionRef,...q}),
      tool:(chat,callRef,fileEpoch)=>'/api/opencode/tool-record?'+new URLSearchParams({id:chat.sessionRef,callRef,fileEpoch}),
      same:(chat,data)=>data.source === 'opencode' && data.sessionRef === chat.sessionRef},
    history:{backend:'history',events:(chat,q)=>'/api/history/events?'+new URLSearchParams({key:chat.key,...q}),tool:null,
      same:(chat,data)=>data.source === 'history' && data.sessionRef === chat.sessionRef}};
  const HISTORY_KEY=/^(?:claude:cc-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?:\/agent-[A-Za-z0-9_-]{1,256})?|opencode:ses_[A-Za-z0-9]{8,64})$/;
  // A chat's resident window, by row position (seq): every position from top to hi that was read is on screen
  // (or invisible: a tool result, a reminder); keep holds the live tail's trim back to that position (phonePage).
  const WINDOW={top:null,hi:null,keep:null,paging:false};
  // The conversation on screen: the viewed session's while one is open, else the Manager's.
  function shown() { return state.viewing ? state.viewing.chat : state.chat; }
  // The damaged-rows note gets one quiet line of its own beside the read status (.m-lines hides an empty one), made
  // on first use: no markup or CSS of its own, and it can never overwrite what "Catching up" says.
  let damagedNode=null;
  function damagedLine() {
    if (damagedNode) return damagedNode;
    const read=$('read-status'), host=read?.parentNode; if (!host) return null;
    damagedNode=document.createElement('span'); damagedNode.id='m-damaged-status'; damagedNode.setAttribute('role','status');
    host.insertBefore(damagedNode,read.nextSibling);
    return damagedNode;
  }
  // The shown conversation's own read observation: the note stands until a successful page of the same conversation
  // says the rows are readable (or another conversation is shown); a failed read leaves it, and the rows, alone.
  function paintDamaged() {
    const node=damagedLine(); if (!node) return;
    text(node,shown()?.damaged ? DAMAGED_ROWS : '');
  }
  function frozenIntent() { return {page:state.page,selectionRevision:state.selectionRevision,draftRevision:state.draftRevision,executionId:state.selected}; }
  function unchanged(witness, text) {
    return witness.page === state.page && witness.selectionRevision === state.selectionRevision &&
      witness.draftRevision === state.draftRevision && witness.executionId === state.selected && $('draft').value === text;
  }
  function guard(witness,text) { if (!unchanged(witness,text)) throw new Error('Manager or text changed; message not sent, draft kept'); }
  const delay = ms => new Promise(resolve=>setTimeout(resolve,ms));
  // The desktop's PaneActivity lease (app.js paneActivity), on the phone: a sendBeacon lease so `start.sh restart`
  // waits for work the server cannot see between journal and receipt (and a Refine's provider call). Fire-and-forget;
  // it never delays or fails the work, and the tab closing drops the lease.
  function paneActivity(kind, work) {
    return async function(...args) {
      // A lease belongs to the real pane page on its HTTP(S) origin; an about:/srcdoc document (the offline fixtures, or
      // any non-origin embed) cannot hold a start.sh restart and skips the beacon without changing the work.
      if (!/^https?:$/.test(globalThis.location?.protocol || '')) return work.apply(this,args);
      const beacon=globalThis.navigator?.sendBeacon?.bind(globalThis.navigator);
      if (typeof beacon !== 'function' || typeof Blob !== 'function') return work.apply(this,args);
      const id=globalThis.crypto?.randomUUID?.() || 'activity-'+Date.now()+'-'+Math.random().toString(36).slice(2);
      const post=active=>{ try { beacon('/api/pane-activity',new Blob([JSON.stringify({schemaVersion:1,id,kind,active})],{type:'text/plain'})); } catch (_) {} };
      post(true);
      // The server's lease lasts 180 s (a Refine's two-minute deadline plus its +30 s), so no renewal timer is needed
      // for the phone's operations; the finally lands the release even when the work throws.
      try { return await work.apply(this,args); }
      finally { post(false); }
    };
  }

  // One line of chips in managerOrder (by Manager-Session number, each primary before its sub-sessions), cut to the
  // last week (stripRows): a context gauge for open/closed, the full name on hover. Claude and OpenCode Managers
  // share the strip; the selection key is the row's own native identity (executionId, or an OpenCode ses_ id).
  // @beacon[
  //   id=opc-pane@phoneRenderList,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Draws the /m chip strip in managerOrder from stripRows (one-week cutoff, open/newest/selected kept), reusing chips by native id; gauges and halt flags stay local. Shares the selected Manager and existing full Manager summaries with Programs so its whole family stays at the top, without targeting or draft changes.,
  // ]
  function renderList() {
    const container=$('managers'), old=new Map([...container.children].filter(n=>n.dataset.executionId).map(n=>[n.dataset.executionId,n]));
    for (const row of stripRows()) {
      const key=mid(row); if (!key) continue;
      let button=old.get(key);
      if (!button) {
        button=element('button',undefined,'m-manager'); button.type='button'; button.dataset.executionId=key;
        const index=element('span',undefined,'m-index'); index.setAttribute('aria-hidden','true');
        button.append(element('span',undefined,'m-dot'),index,element('span',undefined,'m-halt-flag'),element('span',undefined,'m-name'));
        // A tap selects; a press and hold opens the model/thinking sheet and swallows that tap.
        button.addEventListener('click',()=>{ if (button.holdFired) { button.holdFired=false; return; } selectManager(button.dataset.executionId); });
        phoneHoldManager(button);
      }
      button.dataset.backend=backendOf(row);
      markManager(button,row);
      // While a session is viewed, the Manager underneath is held (outlined), not pressed.
      button.setAttribute('aria-pressed',String(key === state.selected && !state.viewing));
      button.dataset.held=String(key === state.selected && !!state.viewing);
      container.append(button); old.delete(key);
    }
    for (const button of old.values()) button.remove();
    renderViewerChip(container);
    if (state.tune) renderTune();
    syncProgramManager();
  }
  // @beacon[
  //   id=opc-pane@syncProgramManager,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Shares the phone's existing selected Manager and full picker summaries with the lazy Programs sidebar, on selection/list updates and frame load; no new reads, no frame opened for it, and no target, draft or saved-state mutation.,
  // ]
  function syncProgramManager() {
    const frame=$('programs-frame');
    if(!state.programManagerWired){frame.addEventListener('load',syncProgramManager);state.programManagerWired=true;}
    const row=state.managers.find(t=>mid(t) === state.selected) || (mid(state.target) === state.selected ? state.target : null);
    try { frame.contentWindow?.opcSidebar?.selectManager?.(row,state.managers); } catch (_) { /* frame not ready yet; its load retries */ }
  }
  // A chip's context share, from what the page already holds: the selected Claude Manager's own newest measured
  // figure (its chat pages and the Context view), else the list row's lastContext (the host's last completed call);
  // an OpenCode Manager's from its own state read or the Context view. {percent, tokens} or null; never a fetch.
  function chipContext(row) {
    let tokens=null, window=null;
    if (backendOf(row) === 'opencode') {
      const c=(mid(state.target) === mid(row) ? state.target?.context : null) || state.ocContexts?.get(mid(row))?.context;
      if (c && Number.isSafeInteger(c.usedTokens)) { tokens=c.usedTokens; window=c.window; }
    } else {
      const seen=state.contexts.get(row.executionId)?.last, listed=row.lastContext;
      if (seen) { tokens=seen.occupancy_tokens; window=seen.window_tokens; }
      else if (listed && Number.isSafeInteger(listed.tokens)) { tokens=listed.tokens; window=listed.window; }
    }
    if (!Number.isSafeInteger(tokens) || tokens < 0) return null;
    return {tokens,percent:Number.isSafeInteger(window) && window > 0 ? Math.min(100,100*tokens/window) : null};
  }
  // Quota shading on the chips: how near the active subscription is to its limits, the same for every chip of a model
  // family (every Claude Code chip from the Claude reading, every OpenCode openai/* chip from the ChatGPT plan's; any
  // other model none). Each window is 0 below QUOTA_SHADE_FROM % used, rising smoothly to 1 at QUOTA_SHADE_FULL %.
  const QUOTA_SHADE_FROM=65, QUOTA_SHADE_FULL=95;
  // @beacon[
  //   id=opc-pane@phoneQuotaShade,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=A chip's quota shading {weekly, fiveHour} (0–1 each, null = no such window) from state.usage only, phoneReadUsage's cached GET /api/usage read (Context, or Chat's slower cadence); never a request per chip. Claude Code chips take the Claude reading, OpenCode openai/* chips the ChatGPT plan's (weekly only when it has no five-hour window), anything else null; no reading, an unavailable one, or a window whose reset has passed shades nothing.,
  // ]
  function quotaShade(row) {
    const model=typeof row?.model === 'string' ? row.model : row?.model ? [row.model.providerID,row.model.id].join('/') : '';
    const family=backendOf(row) === 'opencode' ? (/^openai\//.test(model) ? 'chatgpt' : null) : 'claude';
    const reading=family ? state.usage?.[family] : null;
    if (!reading || (!reading.ok && !reading.stale)) return null;
    const now=Date.now()/1000, windows=Array.isArray(reading.windows) ? reading.windows : [];
    const level=w => {
      if (!w || typeof w.percent !== 'number' || !Number.isFinite(w.percent)) return null;
      const resets=typeof w.resets_at === 'number' ? w.resets_at : typeof w.resets_at === 'string' ? Date.parse(w.resets_at)/1000 : NaN;
      if (Number.isFinite(resets) && resets <= now) return 0; // reset since the reading: nothing to show
      return Math.max(0,Math.min(1,(w.percent-QUOTA_SHADE_FROM)/(QUOTA_SHADE_FULL-QUOTA_SHADE_FROM)));
    };
    const weekly=level(windows.find(w=>w?.kind === 'weekly_all') || windows.find(w=>/^weekly$/i.test(String(w?.label || ''))));
    const fiveHour=level(windows.find(w=>w?.kind === 'session') || windows.find(w=>/^(five-hour|5h)$/i.test(String(w?.label || ''))));
    return weekly === null && fiveHour === null ? null : {family,weekly,fiveHour};
  }
  // @beacon[
  //   id=opc-pane@phoneMarkManager,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=One chip's context gauge (where the live dot was), its Manager-Session index right after it (CSS-drawn, never elided; the name after it may elide), quota shading, name, ⛔ halt and hover. Open Claude Manager: the Context view's pie in a green ring ("live · 42% context"; a plain green dot while no share is known). Closed (sleeping, ended, any OpenCode Manager): a gray ring, filled gray with the last known share only when chipContext already has it ("closed · last 42%" / "closed"); nothing is fetched for it. Shading: quotaShade's weekly level tints the top half, five-hour the bottom.,
  // ]
  function markManager(button,row) {
    const opencode=backendOf(row) === 'opencode';
    const life=opencode ? 'opencode' : row.lifecycle || row.status || 'observing', name=row.title || mid(row);
    const halt=opencode ? '' : haltLine(row.executionId === state.target?.executionId ? state.target : row,true);
    const open=!opencode && openManager(row), context=chipContext(row), percent=context?.percent ?? null;
    const words=(open ? 'live' : 'closed')+(percent === null ? (context ? ' · '+tokenCount(context.tokens)+' context' : '') :
      (open ? ' · ' : ' · last ')+Math.round(percent)+'%'+(open ? ' context' : ''));
    const gauge=button.firstChild;
    gauge.dataset.life=life; gauge.dataset.open=String(open); gauge.dataset.known=String(percent !== null);
    if (percent === null) gauge.style.removeProperty('--gauge-fill'); else gauge.style.setProperty('--gauge-fill',percent.toFixed(1));
    gauge.title=words; gauge.setAttribute('role','img'); gauge.setAttribute('aria-label',words);
    const shade=quotaShade(row);
    if (shade) button.dataset.quota=shade.family; else delete button.dataset.quota;
    for (const [name,value] of [['--quota-week',shade?.weekly],['--quota-five',shade?.fiveHour]])
      if (value) button.style.setProperty(name,value.toFixed(3)); else button.style.removeProperty(name);
    // The Manager-Session index, drawn by CSS beside the gauge and never elided (on an iPhone the name's end, where the
    // number is, is what elides); the name itself stays the chip's text and accessible name.
    const index=globalThis.opcManagerOrder?.index?.(...managerNames(row)) || null, flag=button.querySelector('.m-halt-flag');
    if (index) button.dataset.index=index; else delete button.dataset.index;
    button.querySelector('.m-index').dataset.index=index || '';
    text(button.lastChild,name); text(flag,halt ? '⛔' : ''); flag.hidden=!halt; button.dataset.halted=String(!!halt);
    button.title=(halt ? halt+'\n' : '')+name+' · '+words+' · '+(opencode ? 'OpenCode Manager' : life === 'sleeping' ? 'sleeping · reconnects on Send' : life)+' · press and hold: model and thinking';
  }
  // The viewed session's own chip, first in the strip: its role, its name and ✕ (back to the Manager).
  function renderViewerChip(container) {
    const v=state.viewing; let chip=container.querySelector('.m-viewer');
    if (!v) { chip?.remove(); return; }
    if (!chip) {
      chip=element('span',undefined,'m-manager m-viewer'); chip.setAttribute('aria-pressed','true');
      const close=element('button','✕','m-viewer-close'); close.type='button';
      close.title='Close this read-only view: back to your Manager'; close.setAttribute('aria-label',close.title);
      close.addEventListener('click',()=>void leaveViewer());
      chip.append(element('span',undefined,'m-viewer-role'),element('span',undefined,'m-name'),close);
    }
    text(chip.children[0],v.label); text(chip.children[1],v.title); chip.title=v.label+' · '+v.title+' · read-only';
    if (container.firstChild !== chip) container.prepend(chip);
  }
  function renderViewerState() {
    const v=state.viewing;
    const working=viewerActivity().busy;
    $('title').textContent=v.title; $('state').dataset.busy=String(working === true);
    // The chip is narrow on a phone; the state line carries the whole name.
    $('state').textContent='Read-only · '+v.label+': '+v.title+(v.backend === 'history' ? ' · 📦 archived' : working === true ? ' · Working' : working === false ? ' · idle' : ' · activity unknown');
    renderHalt(haltLine(v,v.label === 'Manager'));
  }
  // A session halted for a usage-limit lockout: the one line quota-halt.js words from its live state (this generation's
  // quota_halt), else ''. A Manager's says Send reconnects it; any other role's, its Conductor.
  function haltLine(row,manager) {
    const words=globalThis.opcQuotaHalt, halt=words?.active(row?.liveState,Number.isSafeInteger(row?.generation) ? row.generation : null);
    return halt ? words.line(halt,{manager}) : '';
  }
  // @beacon[
  //   id=opc-pane@phoneHaltBanner,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Phone Chat's ⛔ usage-limit halt banner under the state line, for the selected Manager or the viewed session: shown while its own generation carries quota_halt, hidden once the next accepted input clears it.,
  // ]
  function renderHalt(line) {
    const node=$('halt'); if (!node) return;
    if (node.textContent !== line) node.textContent=line;
    node.hidden=!line;
  }
  function renderState(data) {
    renderInterrupt();
    // A viewed session's composer is one line; the draft and its buttons wait for the Manager.
    $('composer').dataset.readonly=String(!!state.viewing); $('readonly').hidden=!state.viewing;
    if (state.viewing) { renderViewerState(); renderJournals(); return; }
    const t=state.target, opencode=backendOf(t) === 'opencode';
    $('title').textContent=t?.title || (state.selected ? 'Manager unavailable' : 'Choose a Manager');
    const live=data?.liveState || t?.liveState;
    const working=opencode ? openCodeLive().busy === true : !!t && t.lifecycle !== 'sleeping' && busy(live);
    $('state').dataset.busy=String(working);
    $('state').textContent=!t ? (state.selected ? '' : 'Tap a Manager to read it') : opencode ?
      'OpenCode Manager · '+(working ? 'Working' : openCodeLive().busy === false ? 'idle' : 'activity unknown')+
        (t.model ? ' · '+ocName(t.model) : '') :
      (t.lifecycle === 'sleeping' ? 'Sleeping · Send reconnects this Manager' : working ? 'Working' :
        t.lifecycle === 'live' ? (live ? 'Live · idle' : 'Live') : t.lifecycle === 'pending' ? 'Getting ready · Send waits for it' : t.lifecycle || 'Observing')+
      (live?.settings_unverified && !live?.settings_mismatch ? ' · settings unverified (Send still goes through)' : '')+
      (live?.settings_mismatch ? ' · '+(live.settings_gate?.disclosure || live.settings_disclosure || 'settings differ; Re-check settings reads the applied values') : '');
    renderHalt(opencode ? '' : t ? haltLine({...t,liveState:live},true) : '');
    const chip=t && $('managers').querySelector('[data-execution-id="'+mid(t)+'"]');
    const listed=chip && state.managers.find(row=>mid(row) === mid(t)); if (listed) markManager(chip,listed);
    $('recheck').disabled=state.checking || !writable(t);
    renderJournals();
  }
  function bindManager(row, force=false) {
    const old=state.target;
    state.target={...row,backend:'claude',scope:'main'};
    if (force || !old || backendOf(old) !== 'claude' || ['targetRef','generation','sessionRef'].some(k=>old[k] !== row[k])) {
      state.chat={revision:(state.chat?.revision || 0)+1,rows:new Map(),reader:READERS.manager,sessionRef:row.sessionRef,
        scopeRef:mainScope(row),
        executionId:row.executionId,generation:row.generation,fileEpoch:null,delta:null,next:null,lo:null,earlierEnd:null,seek:true,busy:false,loaded:false,start:false,...WINDOW};
      // A viewed session owns the panel: the Manager's fresh chat waits underneath, read when it comes back.
      // A resync keeps the messages on screen until the fresh chat's first page arrives (phoneReadChat swaps).
      if (!state.viewing) { if (state.keepRows) state.chat.swap=true; else clearConversation(); }
    }
    renderState();
  }
  // An OpenCode Manager's conversation is the same READERS.opencode conversation the viewer already renders; a
  // validated state read supplies its witness, and until then the chat is readable but Send refuses without one.
  function bindOpenCode(row, force=false, deliberate=false) {
    const old=state.target, sessionId=mid(row); if (!sessionId) return;
    const changed=!old || backendOf(old) !== 'opencode' || old.sessionId !== sessionId;
    if (changed) state.ocNextState=0;
    const saved=savedOpenCodeTarget(sessionId);
    state.target={...row,backend:'opencode',sessionId,scope:'main',witness:deliberate ? null : row.witness || saved?.witness || null,
      capabilities:{send:true,control:true,context:true,...(row.capabilities || {})}};
    if (force || changed || !state.chat || state.chat.reader !== READERS.opencode) {
      state.chat={revision:(state.chat?.revision || 0)+1,rows:new Map(),reader:READERS.opencode,viewer:false,label:'Manager',you:'You',
        sessionRef:sessionId,scopeRef:'main',executionId:null,generation:null,fileEpoch:null,delta:null,next:null,lo:null,earlierEnd:null,seek:true,busy:false,loaded:false,start:false,...WINDOW};
      if (!state.viewing) { if (state.keepRows) state.chat.swap=true; else clearConversation(); }
    }
    renderState();
  }
  function mainScope(row) { return (row.scopes?.find(s=>s.label === 'main') || row.scopes?.[0])?.scopeRef; }
  function clearConversation() {
    if (shown()) thinkingMemory(shown()); // same-conversation paging/resync keeps choices; another conversation starts fresh
    state.follow=true; $('latest').hidden=true; $('first').setAttribute('aria-pressed','false'); $('messages').replaceChildren(); sweepDiffs(); notice('Loading recent messages…',false,'read-status'); paintDamaged();
  }
  // @beacon[
  //   id=opc-pane@phoneSelectManager,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=A deliberate chip tap selects its backend-qualified native identity, increments local revisions and writes a unique cross-tab selection marker before awaits; drafts remain intact. OpenCode explicitly revalidates its full id and creation/folder witness, while reload retains the saved witness rather than silently adopting a replacement.,
  // ]
  async function selectManager(executionId) {
    const row=state.managers.find(t=>mid(t) === executionId); if (!row) return;
    // A Manager chip always leaves a viewed session; the Manager underneath comes back as it was left.
    if (state.viewing && executionId === state.selected) return leaveViewer();
    state.viewing=null;
    state.selected=executionId; state.selectionRevision++;
    state.selectionEpoch=(state.selectionEpoch || 0)+1;
    state.selectionMarker=crypto.randomUUID();
    try { write(KEYS.target,executionId); write(KEYS.selectionEpoch,String(state.selectionEpoch)); write(KEYS.selectionMarker,state.selectionMarker); saveDraft(); } catch (e) { notice('Selection/draft only on this page: '+e.message,true); }
    if (backendOf(row) === 'opencode') { state.ocNextState=0; bindOpenCode(row,true,true); }
    else bindManager(row,true);
    renderList();
    $('managers').querySelector('[aria-pressed="true"]')?.scrollIntoView?.({block:'nearest',inline:'nearest'});
    await Promise.all([phoneReadChat(),observeState()]);
  }
  // @beacon[
  //   id=opc-pane@phoneLoadManagers,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Cold-loads and completes the host Manager inventory cursor chain, then merges the sidebar's genuine OpenCode Manager rows (kind manager / manager true, never a title), so both runtimes share one chip strip in managerOrder (Manager-Session number highest first, each primary before its sub-sessions; liveness never reorders). A saved/selected Manager is rebound by its own id whatever the window shows: an OpenCode ses_ id is revalidated through GET /api/opencode/manager even when absent from the list. A good Claude read's Manager names are kept on the phone for ↳ (rememberClaudeNames). Refresh never wakes or selects a successor; a ＋ New Manager launch is adopted the moment its own id exists, and choosing another Manager meanwhile wins.,
  // ]
  async function phoneLoadManagers() {
    if (state.listBusy) return;
    state.listBusy=true;
    try {
      const rows=new Map(); let cursor=null, claudeError=null;
      try {
        do {
          const data=await request('/api/claude/sessions?'+new URLSearchParams({role:'Manager',...(cursor ? {cursor} : {})}));
          if (!Array.isArray(data.sessions)) throw new Error('Manager inventory missing');
          for (const row of data.sessions) if (registered(row)) rows.set(row.executionId,{...row,backend:'claude'});
          renderList(); notice(data.status === 'discovering' ? 'Discovering Managers…' : '',false,'list-status');
          cursor=data.hasMore ? data.nextCursor : null;
          if (data.hasMore && !cursor) throw new Error('Manager inventory continuation missing');
        } while (cursor);
      } catch (e) { claudeError=e; }
      state.claudeListError=claudeError ? claudeError.message : null;
      if (!claudeError) rememberClaudeNames([...rows.values()]);
      // A Claude inventory failure must never hide first-class OpenCode Managers or drop the native cached/partial
      // rows: keep the last native list and continue with the independent OpenCode read.
      if (claudeError) for (const t of state.managers) if (backendOf(t) === 'claude' && !rows.has(mid(t))) rows.set(mid(t),t);
      // Genuine OpenCode Managers: the same read-only programs inventory Programs shows, filtered by the server's
      // actual manager/manager-gpt authority (an unavailable read never removes the Claude Managers already listed).
      let ocError=null;
      if (ocApi) {
        try {
          const data=await ocApi.request('programs');
          if (data.status === 'complete' && Array.isArray(data.sessions)) {
            for (const row of data.sessions) if (ocKnown(row)) rows.set(row.id,{...row,backend:'opencode',sessionId:row.id});
          } else if (data.status === 'unavailable') ocError=data.error || 'OpenCode inventory unavailable';
        } catch (e) { ocError=e.message; }
      }
      state.managers=[...rows.values()].sort(managerOrder);
      renderList();
      if (state.selected) {
        const row=rows.get(state.selected);
        if (row) {
          if (backendOf(row) === 'opencode') {
            if (backendOf(state.target) === 'opencode' && mid(state.target) === mid(row))
              state.target={...state.target,title:row.title || state.target.title};
            else bindOpenCode(row);
          } else bindManager(row);
        } else if (OC_SID.test(state.selected) && ocApi) {
          // A saved OpenCode Manager must stay revalidatable by its full ses_ id, whatever the list window shows.
          const adopted=await revalidateOpenCodeManager(state.selected);
          if (adopted && backendOf(state.target) === 'opencode' && state.target.sessionId === adopted.id &&
              state.target.witness && adopted.witness !== state.target.witness) {
            if (!state.viewing) notice('Selected OpenCode Manager creation/directory witness changed; keeping the validated target.',true,'read-status');
          } else if (adopted) { rows.set(adopted.id,adopted); state.managers=[...rows.values()].sort(managerOrder); renderList(); bindOpenCode(adopted,true); }
          else if (!state.viewing) notice('Selected OpenCode Manager is unavailable; retrying the same session id.',true,'read-status');
        } else if (!claudeError) {
          // A failed Claude read is not evidence that its selected Manager disappeared; keep the native target/chat.
          state.target=null; state.chat=null; renderState();
          if (!state.viewing) notice('Selected Manager is unavailable; retrying the same execution.',true,'read-status');
        }
      }
      const notes=[];
      if (claudeError) notes.push('Claude Managers unavailable: '+claudeError.message);
      if (ocError) notes.push('OpenCode Managers unavailable: '+ocError);
      if (notes.length) notice(notes.join(' · ')+' · retrying',true,'list-status');
      else if (!state.managers.length) notice('No Managers found.',false,'list-status');
      if (state.launch?.runtime === 'opencode') adoptOcLaunched();
      else if (state.launch?.phase === 'waiting') adoptLaunched(rows);
      state.nextList=Date.now()+((state.launch?.phase === 'waiting' || state.launch?.runtime === 'opencode') ? 1000 : (claudeError || ocError) ? 2000 : 5000);
    } catch (e) {
      notice('Managers unavailable: '+e.message+' · retrying',true,'list-status');
      state.nextList=RESYNC.has(e.code) ? 0 : Date.now()+2000;
    } finally { state.listBusy=false; }
  }
  // The exact manager state for one ses_ id, or null when the id is not an owned root Manager: the saved-target leg
  // that does not depend on any list window or on the programs inventory.
  function savedOpenCodeTarget(sid) {
    try { const t=JSON.parse(read(KEYS.ocTarget+sid) || 'null'); return ocApi?.valid(t) && t.sessionId === sid ? t : null; }
    catch (_) { return null; }
  }
  function rememberOpenCodeTarget(target) {
    try { write(KEYS.ocTarget+target.sessionId,JSON.stringify(target)); }
    catch (e) { notice('Manager witness only on this page: '+e.message,true); }
  }
  async function revalidateOpenCodeManager(sid, {deliberate=false}={}) {
    if (!ocApi || !OC_SID.test(sid || '')) return null;
    try {
      const data=await ocApi.request('manager?'+new URLSearchParams({id:sid}));
      if (!ocApi.valid(data.target) || data.target.sessionId !== sid || data.capabilities?.send !== true) return null;
      const saved=savedOpenCodeTarget(sid);
      if (!deliberate && saved && !ocApi.same(saved,data.target)) return null;
      return {...data,id:sid,backend:'opencode',sessionId:sid,kind:'manager',manager:true,role:'Manager',witness:data.target.witness};
    } catch (_) { return null; }
  }
  async function resync(reason) {
    // The messages on screen stay until the same conversation's fresh first page has arrived: a slow or failed
    // read (or the server's replay retry answering stale-epoch) never blanks them.
    const kept=!state.viewing && $('messages')?.childElementCount > 0;
    if (!state.viewing) notice(kept ? CATCHING_UP : 'Reconnecting to the same conversation: '+reason,false,'read-status');
    state.chat=null; state.keepRows=kept;
    try {
      await phoneLoadManagers();
      if (state.target && !state.chat) {
        if (backendOf(state.target) === 'opencode') bindOpenCode(state.target,true);
        else bindManager(state.target,true);
      }
    } finally { state.keepRows=false; }
  }
  // The Programs view's open-session message, checked field by field before it addresses anything.
  function viewerSession(d) {
    const s=d?.session, str=(v,max=4096)=>typeof v === 'string' && !!v && v.length <= max, opt=k=>str(s[k]) ? s[k] : null;
    if (d?.type !== 'open-session' || d.schemaVersion !== 1 || !s || typeof s !== 'object') return null;
    const title=str(s.title,400) ? s.title : null, label=str(s.label,40) ? s.label : null;
    if (s.backend === 'opencode') return /^ses_[A-Za-z0-9]{8,64}$/.test(s.id || '') ?
      {backend:'opencode',sessionRef:s.id,scopeRef:'main',executionId:null,title:title || s.id,label:label || 'OpenCode'} : null;
    // 📦 History: an archived conversation by its key and the handles its row carries; never a Manager, never promoted.
    if (s.backend === 'history') return HISTORY_KEY.test(s.key || '') && str(s.sessionRef) && str(s.scopeRef) ?
      {backend:'history',key:s.key,sessionRef:s.sessionRef,scopeRef:s.scopeRef,executionId:null,title:title || s.key,label:label || 'History'} : null;
    if (s.backend !== 'claude' || !str(s.sessionRef) || !str(s.scopeRef)) return null;
    return {backend:'claude',sessionRef:s.sessionRef,scopeRef:s.scopeRef,executionId:opt('executionId'),uuid:opt('uuid'),source:opt('source'),
      parentUuid:opt('parentUuid'),agentId:opt('agentId'),title:title || 'Session '+String(s.uuid || '').slice(0,8),label:label || 'Session'};
  }
  // @beacon[
  //   id=opc-pane@phoneOpenSession,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Programs' Read › (a Claude row or native subagent) or Read conversation › (an OpenCode glance) opens that session read-only in Chat: a listed Manager opens as itself (Claude by executionId, OpenCode by ses_ id); an OpenCode session not in the inventory is validated through GET /api/opencode/manager and becomes the writable Manager only on a genuine manager/manager-gpt answer, else it stays the viewed session with its own chip and reader (READERS), never through state.selected, so the Manager's draft, Send journals, Refine and Re-check stay untouched; a 📦 History row opens read-only by its archived key (READERS.history), never matched to a Manager or validated; ✕ or any Manager chip leaves it.,
  // ]
  function phoneOpenSession(message) {
    const s=viewerSession(message); if (!s) return;
    const manager=s.backend === 'opencode' ? state.managers.find(t=>backendOf(t) === 'opencode' && mid(t) === s.sessionRef) :
      s.executionId && state.managers.find(t=>backendOf(t) === 'claude' && mid(t) === s.executionId);
    showPhoneView('chat');
    if (manager) { if (state.viewing || mid(manager) !== state.selected) void selectManager(mid(manager)); return; }
    state.viewing=s; bindViewer(); renderList(); renderState();
    $('managers').querySelector('.m-viewer')?.scrollIntoView?.({block:'nearest',inline:'nearest'});
    void phoneReadChat();
    if (s.backend === 'opencode') void promoteOpenCodeViewer(s);
  }
  // Read › on an OpenCode session the inventory did not list: one GET-only validation decides. A real Manager replaces
  // the read-only view with the selected target (the user's own tap is the selection); a worker, Conductor or subagent
  // stays read-only. A changed selection revision or another chip tap cancels the promotion.
  async function promoteOpenCodeViewer(s) {
    const revision=state.selectionRevision;
    const row=await revalidateOpenCodeManager(s.sessionRef,{deliberate:true});
    if (!row || state.viewing !== s || state.selectionRevision !== revision) return;
    if (!state.managers.some(t=>mid(t) === row.id)) {
      state.managers=[...state.managers,row].sort(managerOrder);
    }
    state.viewing=null;
    await selectManager(row.id);
  }
  // @beacon[
  //   id=opc-pane@phoneOpenInView,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=A handle tapped anywhere on /m (handle-links.js; the Programs frame calls it through its parent with where the tap came from) opens in the View tab as Node, a fresh root of View's history: origin {from:'programs', active, position} for a session row's socket, else {from:'outside', where:'Chat'}. A View frame not loaded yet starts at /glimpse.html?handle=…&from=…&active=…&where=…, a loaded one gets opcGlimpseView.openHandle, one still loading gets it on load. Chat, the draft and the Manager stay as they are.,
  // ]
  function phoneOpenInView(handle,origin={from:'outside',where:'Chat'}) {
    const frame=$('glimpse-frame'), view=()=>{ try { return frame.contentWindow?.opcGlimpseView || null; } catch (_) { return null; } };
    if (!frame.getAttribute('src')) {
      const query=new URLSearchParams({handle});
      for (const key of ['from','active','where']) if (typeof origin?.[key] === 'string' && origin[key]) query.set(key,origin[key]);
      if (origin?.turn) query.set('turn',JSON.stringify(origin.turn));
      frame.src='/glimpse.html?'+query;
    }
    else if (view()) void view().openHandle(handle,'node',origin);
    else frame.addEventListener('load',()=>void view()?.openHandle(handle,'node',origin),{once:true});
    showPhoneView('glimpse');
  }
  // The Programs frame's list (sessions.js opcSidebar) for View's root widget, its frame loaded in the background if
  // Programs was never opened (the same reads Programs makes); null if it is not up within waitMs.
  async function programsSidebar(waitMs=20000) {
    const frame=$('programs-frame'), started=Date.now();
    if (!frame.getAttribute('src') && !frame.hasAttribute('srcdoc')) frame.src='/m-programs.html';
    for (;;) {
      try { const api=frame.contentWindow?.opcSidebar; if (api) return api; } catch (_) { return null; }
      if (Date.now()-started > waitMs) return null;
      await new Promise(resolve=>setTimeout(resolve,100));
    }
  }
  // View asks where a socket sits (its root widget's cards) …
  async function phoneProgramPosition(handle,active=null) { const api=await programsSidebar(); return api ? api.positionWhenReady(handle,active) : null; }
  // @beacon[
  //   id=opc-pane@phoneRevealSession,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=View's root widget: a line tapped (not its handle) shows that session or program in Programs: the Programs tab comes up and its list opens only the ancestors needed, scrolls the row into view and flashes it (sessions.js reveal); View stays as it was for the way back.,
  // ]
  async function phoneRevealSession(key) {
    showPhoneView('programs');
    const api=await programsSidebar(); if (!api) return false;
    return api.reveal(key);  // the frame is on screen now; a list not drawn yet shows it on the redraw that draws it
  }
  // The conversation turn a handle was tapped in (Chat: the Manager's, or a viewed session's): its session, its row's
  // position and UUID, user / assistant / thinking / tool and, in a tool, input or output. View keeps it on its root.
  function turnAt(node) {
    const chat=shown(), article=node?.closest?.('article.m-row'); if (!chat || !article) return null;
    const row=[...chat.rows.values()].find(r=>r.node === article); if (!row || !Number.isSafeInteger(row.event?.seq)) return null;
    const v=state.viewing, manager=!v && state.managers.find(t=>mid(t) === state.selected);
    const session=v ? {kind:'viewer',title:v.title,label:v.label,descriptor:{backend:v.backend,id:v.backend === 'opencode' ? v.sessionRef : undefined,key:v.key,sessionRef:v.sessionRef,scopeRef:v.scopeRef,
      executionId:v.executionId,uuid:v.uuid,source:v.source,parentUuid:v.parentUuid,agentId:v.agentId,title:v.title,label:v.label}} :
      manager ? {kind:'manager',executionId:backendOf(manager) === 'claude' ? manager.executionId : null,
        opencodeSessionId:backendOf(manager) === 'opencode' ? mid(manager) : null,title:manager.title || mid(manager),label:'Manager'} : null;
    if (!session) return null;
    const tool=node.closest('details.m-tool'), call=tool ? [...row.calls.entries()].find(([,c])=>c.node === tool) : null;
    const thinking=[...row.thinking.values()].some(b=>b.node.contains(node));
    const part=tool ? (node.closest('pre.m-io')?.getAttribute('aria-label') || '').replace(/^Tool /,'') || null : null;
    return {session,seq:row.event.seq,uuid:row.event.uuid || null,role:row.event.kind,kind:call ? 'tool' : thinking ? 'thinking' : row.event.kind,
      call:call ? call[0] : null,tool:call ? call[1].data?.name || null : null,part:part === 'input' || part === 'output' ? part : null,time:row.event.time ?? null};
  }
  // @beacon[
  //   id=opc-pane@phoneShowTurn,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=View's conversation-turn root (Show in conversation): Chat shows that session again (its Manager chip, or the viewed session reopened read-only), the window around the turn is read from its position when it is not on screen (⤒ First's one held page, from 20 rows before it; ↓ Latest returns to the newest), then the turn is scrolled into view and flashed; a tool turn opens, and the input or output it came from is scrolled into view.,
  // ]
  async function phoneShowTurn(turn) {
    if (!turn?.session || !Number.isSafeInteger(turn.seq)) return false;
    showPhoneView('chat');
    const s=turn.session, find=chat=>[...(chat?.rows.values() || [])].find(r=>turn.uuid ? r.event.uuid === turn.uuid : r.event.seq === turn.seq);
    if (s.kind === 'manager') {
      const id=s.executionId || s.opencodeSessionId;
      if (!id || !state.managers.some(t=>mid(t) === id)) { notice('That Manager is no longer listed.',true,'read-status'); return false; }
      if (state.viewing || state.selected !== id) await selectManager(id);
    } else if (!(state.viewing && state.viewing.sessionRef === s.descriptor?.sessionRef)) {
      phoneOpenSession({type:'open-session',schemaVersion:1,session:s.descriptor});
      if (!state.viewing) { notice('That conversation cannot be opened from here.',true,'read-status'); return false; }
    }
    let chat=shown();
    if (!find(chat)) {
      if (!rebindShown()) return false;
      chat=shown(); chat.start=true; chat.from=Math.max(0,turn.seq-20); chat.seek=false; state.follow=false;
      await phoneReadChat();
    }
    const row=find(shown()); if (!row) { notice('That turn is not in this conversation any more.',true,'read-status'); return false; }
    const call=turn.call ? row.calls.get(turn.call) : null;
    if (call && !call.node.open) { call.node.open=true; call.node.dispatchEvent(new Event('toggle')); }
    const target=call ? (turn.part ? call.node.querySelector('pre.m-io[aria-label="Tool '+turn.part+'"]') || call.node : call.node) : row.node;
    const box=$('conversation'), r=target.getBoundingClientRect(), c=box.getBoundingClientRect();
    box.scrollTop+=r.top-c.top-Math.max(8,(box.clientHeight-Math.min(r.height,box.clientHeight))/2);
    state.follow=false; offerLatest(shown());
    row.node.classList.remove('m-flash'); void row.node.offsetWidth; row.node.classList.add('m-flash');
    setTimeout(()=>row.node.classList.remove('m-flash'),2600);
    return true;
  }
  function bindViewer() {
    const v=state.viewing;
    v.chat={revision:(v.chat?.revision || 0)+1,rows:new Map(),reader:READERS[v.backend],viewer:true,label:v.label,you:v.agentId ? 'Prompt' : 'You',
      sessionRef:v.sessionRef,scopeRef:v.scopeRef,executionId:v.executionId,key:v.key ?? null,fileEpoch:null,delta:null,next:null,lo:null,earlierEnd:null,seek:true,busy:false,loaded:false,start:false,...WINDOW};
    clearConversation();
  }
  // ✕ on the viewed chip (or the Manager underneath tapped): that Manager comes back as it was left —
  // the same selection, draft and Send state, its conversation read fresh. Nothing is selected anew.
  async function leaveViewer() {
    if (!state.viewing) return;
    state.viewing=null;
    if (state.target) { if (backendOf(state.target) === 'opencode') bindOpenCode(state.target,true); else bindManager(state.target,true); }
    else { clearConversation(); notice('',false,'read-status'); }
    renderList(); renderState();
    await phoneReadChat();
  }
  // @beacon[
  //   id=opc-pane@phoneResyncViewer,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=A viewed session whose handles went void (pane restart, rebuilt index): OpenCode rereads the same ses_ tail and 📦 History the same archived key (a re-archived conversation's new epoch); Claude scans the whole session list once (no role filter) for the same host execution, else source + UUID, a subagent under its parent by agent id, then reads it fresh; not listed retries every 10 s, never another session.,
  // ]
  async function phoneResyncViewer(reason='') {
    const v=state.viewing; if (!v || v.finding || Date.now() < (v.retryAt || 0)) return;
    notice('Reconnecting to the same conversation'+(reason ? ': '+reason : '')+' …',false,'read-status');
    if (v.backend === 'opencode' || v.backend === 'history') { bindViewer(); return; }  // same ses_ id / archived key: no list
    v.chat=null; v.finding=true;
    try {
      const row=await findClaudeSession(v);
      if (state.viewing !== v) return;
      if (!row) { v.retryAt=Date.now()+10000; notice(v.title+' is not listed right now; trying again · ✕ returns to your Manager.',true,'read-status'); return; }
      Object.assign(v,{sessionRef:row.sessionRef,scopeRef:mainScope(row),executionId:typeof row.executionId === 'string' ? row.executionId : null,retryAt:0});
      bindViewer();
    } catch (e) {
      if (state.viewing === v) { v.retryAt=Date.now()+5000; notice('Reconnecting failed: '+e.message+' · retrying',true,'read-status'); }
    } finally { v.finding=false; }
  }
  async function findClaudeSession(v) {
    const rows=[]; let cursor=null, pages=0;
    do {
      const data=await request('/api/claude/sessions'+(cursor ? '?'+new URLSearchParams({cursor}) : ''));
      if (!Array.isArray(data.sessions)) throw new Error('session list missing');
      rows.push(...data.sessions); cursor=data.hasMore ? data.nextCursor : null;
    } while (cursor && ++pages < 40);
    const primary=rows.filter(r=>r?.role !== 'subagent' && Array.isArray(r.scopes));
    if (!v.agentId) return primary.filter(r=>v.executionId ? r.executionId === v.executionId : r.source === v.source && r.uuid === v.uuid)
      .sort((a,b)=>(b.generation || 0)-(a.generation || 0))[0] || null;
    const parent=primary.filter(r=>r.uuid === v.parentUuid).sort((a,b)=>Number(b.source === 'host')-Number(a.source === 'host'))[0];
    for (let next=null, i=0; parent && i<64; i++) {
      const data=await request('/api/claude/subagents?'+new URLSearchParams({sessionRef:parent.sessionRef,...(next ? {cursor:next} : {})}));
      const hit=(data.sessions || []).find(r=>r?.agentId === v.agentId && r.parentUuid === v.parentUuid && Array.isArray(r.scopes));
      if (hit) return hit;
      if (!data.hasMore || !data.nextCursor) break;
      next=data.nextCursor;
    }
    return null;
  }
  async function observeState() {
    const t=state.target, revision=state.selectionRevision; if (!t) return;
    if (backendOf(t) === 'opencode') return observeOpenCodeState(t,revision);
    try {
      const data=envelope(await request('/api/claude/state?'+query(t)),t);
      if (revision !== state.selectionRevision || state.target?.targetRef !== t.targetRef) return;
      if (data.source !== 'host' || data.role !== 'Manager') throw new Error('not a host Manager');
      state.target={...t,capabilities:data.capabilities,lifecycle:data.lifecycle,liveState:data.liveState,liveStateAt:Date.now()}; renderState(data);
    } catch (e) {
      if (revision !== state.selectionRevision) return;
      if (RESYNC.has(e.code)) await resync(e.message);
      else notice('Manager state unavailable: '+e.message+' · retrying',true,'state');
    }
  }
  // An OpenCode Manager's exact state: the server revalidates root Manager authority and returns the target witness
  // (creation time + directory). Its measured context and busy fact are kept apart from Claude's liveState; the fast
  // activity read keeps busy fresher between these reads, and a context read is never Send authority.
  async function observeOpenCodeState(t,revision) {
    if (!ocApi) return notice('OpenCode Manager support is not loaded on this page; reload it.',true,'state');
    if (Date.now() < (state.ocNextState || 0)) return;
    try {
      const data=await ocApi.request('manager?'+new URLSearchParams({id:t.sessionId}));
      if (revision !== state.selectionRevision || state.target?.sessionId !== t.sessionId) return;
      if (!ocApi.valid(data.target) || data.target.sessionId !== t.sessionId || data.capabilities?.send !== true)
        throw new Error('OpenCode Manager identity/authority did not validate');
      if (t.witness && data.target.witness !== t.witness)
        throw new Error('OpenCode Manager creation/directory witness changed; keeping the validated target');
      const target={...t,...data,backend:'opencode',sessionId:t.sessionId,target:undefined,witness:data.target.witness,
        busy:typeof data.busy === 'boolean' ? data.busy : null,busyAt:Number.isFinite(data.observedAt) ? data.observedAt*1000 : Date.now(),
        model:data.model || t.model,agent:data.agent || t.agent,capabilities:data.capabilities,context:data.context || null};
      delete target.target;
      state.target=target;
      rememberOpenCodeTarget(data.target);
      const row=state.managers.find(r=>backendOf(r) === 'opencode' && mid(r) === t.sessionId);
      if (row) Object.assign(row,{title:data.title || row.title,model:data.model ?? row.model,agent:data.agent ?? row.agent,busy:data.busy,busyAt:target.busyAt});
      state.ocNextState=Date.now()+10000;
      renderState(data);
    } catch (e) {
      if (revision !== state.selectionRevision) return;
      state.ocNextState=Date.now()+3000;
      if (RESYNC.has(e.code)) await resync(e.message);
      else notice('OpenCode Manager state unavailable: '+e.message+' · retrying',true,'state');
    }
  }
  function prose(event) {
    return typeof event.text === 'string' && !!event.text.trim() &&
      (event.kind === 'user' || event.kind === 'assistant');
  }
  function element(tag, text, className) {
    const node=document.createElement(tag);
    if (text !== undefined) node.textContent=text;
    if (className) node.className=className;
    return node;
  }
  function text(node, value) { if (node.textContent !== value) node.textContent=value; }
  function place(container, nodes) {
    let next=container.firstChild;
    for (const node of nodes) {
      if (node !== next) container.insertBefore(node,next);
      next=node.nextSibling;
    }
  }
  const CALL_FLAGS={'not-final-fragment':'streaming',streaming:'streaming','pending-or-missing':'pending',error:'error',ambiguous:'ambiguous'};
  const PENDING=new Set(['not-final-fragment','streaming','pending-or-missing']);
  const MAXIMIZE='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const RESTORE='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  // A busy fact older than this is no longer evidence of anything running: the conversation and the
  // Manager's state are each read about once a second while visible, and an absent read is unknown.
  const BUSY_FACT_MS=15000;
  function clockTime(seconds) {
    if (!Number.isFinite(seconds)) return '';
    const date=new Date(seconds*1000), today=new Date(Date.now()).toDateString() === date.toDateString();
    return date.toLocaleString([],today ? {hour:'numeric',minute:'2-digit'} : {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  }
  // The clipboard, falling back to a hidden selected textarea and execCommand where the browser refuses it.
  async function writeClipboard(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (_) { /* the fallback below */ }
    const area=document.createElement('textarea'); area.value=text; area.setAttribute('readonly','');
    area.style.position='fixed'; area.style.top='0'; area.style.opacity='0'; document.body.append(area);
    area.select(); area.setSelectionRange(0,text.length);
    let ok=false; try { ok=document.execCommand('copy'); } catch (_) { /* reported by the caller */ }
    area.remove(); return ok;
  }
  // Copy buttons: selecting text in a small scroller is hard on an iPhone.
  function copyButton(label, source, title) {
    const button=element('button',label,'m-copy'); button.type='button'; button.title=title; button.setAttribute('aria-label',title);
    button.addEventListener('click',async event=>{
      event.preventDefault(); event.stopPropagation();
      const value=source(); if (typeof value !== 'string') return;
      button.textContent=await writeClipboard(value) ? 'Copied' : 'Copy failed';
      clearTimeout(button.timer); button.timer=setTimeout(()=>{button.textContent=label;},1500);
    });
    return button;
  }
  // Below the text box, Refine setup and Last original share one row: a toggle opens its own panel
  // below the row and closes the other; null closes both.
  const EXTRAS={setup:'setup',original:'original-panel'};
  function setExtra(open) {
    for (const [key,panel] of Object.entries(EXTRAS)) {
      $(panel).hidden=key !== open; $(key+'-toggle').setAttribute('aria-expanded',String(key === open));
    }
  }
  // Last original → Copy: the whole original in one tap, instead of iOS's select-and-drag.
  async function copyOriginal() {
    const button=$('original-copy'), text=$('original').value;
    button.textContent=!text ? 'Nothing yet' : await writeClipboard(text) ? '✓ Copied' : 'Copy failed';
    clearTimeout(button.timer); button.timer=setTimeout(()=>{button.textContent='Copy';},1500);
  }
  // The desktop inspector's three reading states per region, as one switch: Readable = Pretty JSON +
  // Readable text, Pretty = Pretty JSON only, Raw = the exact stored text. Page-wide, like the desktop's.
  const TOOL_VIEWS=[['readable','Readable','Readable text: multiline JSON string values as documents, Markdown-looking text rendered (each block has Rendered / Source). Copy is unchanged.'],
    ['pretty','Pretty','Pretty JSON: the exact JSON tokens, indented. Copy is unchanged.'],['raw','Raw','The exact stored text']];
  // The desktop's shared argument preview, re-presented as "key: value" pairs when it is a whole object.
  function previewNodes(data) {
    const json=opcToolPreview.preview(data.previewData,data.name,{descriptor:true});
    try {
      const value=JSON.parse(json);
      if (value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length)
        return Object.entries(value).flatMap(([key,v],i)=>[...(i ? [document.createTextNode('  ·  ')] : []),
          element('span',key+': ','m-k'),document.createTextNode(typeof v === 'string' ? v : JSON.stringify(v))]);
    } catch (_) {}
    return [document.createTextNode(json)];
  }
  function sizeLabel(node, prefix, value) {
    text(node,Number.isSafeInteger(value) ? prefix+' '+opcToolText.size(value) : '');
    opcToolText.emphasizeToolSize(node,Number.isSafeInteger(value) ? value : null);
  }
  function toolNode(chat, call) {
    const node=element('details',undefined,'m-tool'), summary=element('summary'), line=element('span',undefined,'m-tool-line');
    const name=element('span',undefined,'m-tool-name'), flag=element('span',undefined,'m-tool-flag'), sizes=element('span',undefined,'m-tool-sizes');
    const preview=element('span',undefined,'m-tool-preview'), inSize=element('span',undefined,'m-size'), outSize=element('span',undefined,'m-size');
    // How long it took, then its stored input and output sizes.
    const took=element('span',undefined,'m-took'); took.hidden=true;
    line.append(name,flag); sizes.append(took,inSize,outSize); summary.append(line,sizes,preview);
    const body=element('div',undefined,'m-tool-body'), top=element('div',undefined,'m-tool-top'), status=element('span',undefined,'m-tool-status');
    // Its state as one emoji in a fixed-width slot, then the call's time (toolStatus).
    const mark=element('span',undefined,'m-tool-mark'), when=element('span',undefined,'m-tool-when'); mark.setAttribute('role','img'); status.append(mark,when);
    const wrap=element('button','Wrap','m-small m-wrap'); wrap.type='button'; wrap.setAttribute('aria-pressed',String(state.wrap));
    wrap.title='Wrap long lines in every tool view. Off keeps each line whole and scrolls sideways; Copy is unchanged.';
    wrap.addEventListener('click',()=>setToolWrap(!state.wrap));
    const refresh=element('button','Refresh','m-small'); refresh.type='button'; top.append(status,wrap,refresh); body.append(top);
    // The recorded diff's place, above Input and Output: paintDiff mounts the shared reader here once a snapshot carries one.
    const diffHost=element('div',undefined,'m-diff'); diffHost.hidden=true; body.append(diffHost);
    const io={};
    for (const kind of ['input','output']) {
      const head=element('div',undefined,'m-io-head'), size=element('span',undefined,'m-io-size'), modes=element('span',undefined,'m-modes');
      modes.setAttribute('role','group'); modes.setAttribute('aria-label',(kind === 'input' ? 'Input' : 'Output')+' view'); modes.hidden=true;
      for (const [mode,label,title] of TOOL_VIEWS) {
        const button=element('button',label); button.type='button'; button.dataset.mode=mode; button.title=title;
        button.addEventListener('click',()=>setToolView(kind,mode)); modes.append(button);
      }
      const copy=copyButton('Copy',()=>call.snapshot?.[kind],'Copy the exact stored '+kind); copy.className='m-small';
      head.append(element('span',kind,'m-io-label'),size,modes,copy);
      const caption=element('div',undefined,'m-io-caption'), view=element('pre',undefined,'m-io');
      view.tabIndex=0; view.setAttribute('aria-label','Tool '+kind);
      // Rendered / Source choices of this region's Markdown blocks survive repaints.
      body.append(head,caption,view); io[kind]={size,modes,copy,caption,view,choices:new Set()};
    }
    node.append(summary,body);
    Object.assign(call,{node,name,flag,took,inSize,outSize,preview,status,mark,when,io,diffHost,diffValue:null}); toolStatus(call,'unread','');
    // 📦 An archived call is its one-line preview: the bar never opens onto an input or output it does not have.
    if (!chat.reader.tool) { node.classList.add('m-tool-archived'); summary.title='Archived: a one-line preview of this call'; summary.addEventListener('click',e=>e.preventDefault()); }
    node.addEventListener('toggle',()=>{
      if (!node.open) return;
      call.reveal=true; phoneReadTool(chat,call); revealTool(call);
    });
    refresh.addEventListener('click',()=>phoneReadTool(chat,call));
    return call;
  }
  // An opened call stays in view and pauses following, so the next poll does not scroll it away.
  function revealTool(call) {
    const scroll=$('conversation'), r=call.node.getBoundingClientRect(), c=scroll.getBoundingClientRect();
    if (r.bottom > c.bottom) scroll.scrollTop+=Math.min(r.bottom-c.bottom+6,r.top-c.top-6);
    followCheck();
  }
  // @beacon[
  //   id=opc-pane@phonePaintTool,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Paints an opened phone tool: first its recorded diff through the shared diff reader (diff-view.js, via paintDiff: mounted once, updated in place, Auto inline at phone width), then input and output through the desktop inspector's shared reading view (tool-reading.js): Readable / Pretty / Raw per region, the desktop's captions, Rendered / Source Markdown blocks; the switch shows only where it changes something, and a text selection defers the repaint (the diff reader defers its own).,
  // ]
  function paintTool(call) {
    const s=call.snapshot; if (!s) return;
    paintDiff(call,s.diff ?? null);
    let deferred=false;
    for (const kind of ['input','output']) {
      const io=call.io[kind], value=s[kind], stored=typeof value === 'string', view=call.views[kind], mode=state.toolView[kind];
      const count=Number.isSafeInteger(s[kind+'Bytes']) ? s[kind+'Bytes'] : stored ? opcToolText.bytes(value) : null;
      text(io.size,stored ? opcToolText.size(count) : s[kind+'Availability'] || 'unavailable');
      io.copy.disabled=!stored;
      const shown=opcToolReading.regionView(view,stored ? value : '',mode !== 'raw',mode === 'readable',stored ? count : null);
      // Plain text reads the same in all three views: no switch and no caption for it.
      const choices=stored && (view.reason !== 'non-JSON' || !!opcToolReading.regionView(view,value,true,true,count).pieces);
      io.modes.hidden=!choices;
      for (const button of io.modes.children) button.setAttribute('aria-pressed',String(button.dataset.mode === mode));
      text(io.caption,stored && (choices || view.reason === 'format limit') ? shown.caption.replace(/^ · /,'') : '');
      io.view.dataset.missing=String(!stored);
      if (!opcToolReading.paint(io.view,stored ? shown.text : 'Not available · '+(s[kind+'Availability'] || 'unknown'),
        stored ? shown.pieces : null,io.choices)) deferred=true;
    }
    // Never repaint under a text selection Dan is making; selectionchange catches up.
    call.deferred=deferred;
  }
  // Tool bars holding a shared diff reader, so Wrap reaches them and a bar leaving the page stops its reader.
  const diffCalls=new Set();
  // A snapshot's normalized diff (A1's, as served) into the one shared reader above Input and Output: mounted on the
  // first snapshot that carries one, then updated in place (unchanged data keeps its code, scroll, layout choice and
  // a live selection; changed data under a selection waits for it to end). Only a new snapshot object is passed again;
  // null hides the place. Without the reader script the raw readers below stay, under a note saying so.
  function paintDiff(call, value) {
    if (value === call.diffValue) return;
    if (!call.node.isConnected) { dropDiff(call); return; }
    const host=call.diffHost; call.diffValue=value; host.hidden=value === null;
    if (value === null) { call.diffReader?.destroy(); call.diffReader=null; host.replaceChildren(); diffCalls.delete(call); return; }
    if (!globalThis.opcDiffView) { host.replaceChildren(element('p','Diff display unavailable (diff-view.js did not load) · Input and Output below are unchanged','m-diff-note')); return; }
    try {
      if (call.diffReader) call.diffReader.update(value);
      else { host.replaceChildren(); call.diffReader=opcDiffView.mount(host,value,{layout:'auto',wrap:state.wrap}); diffCalls.add(call); }
    } catch (e) {
      call.diffReader?.destroy(); call.diffReader=null; diffCalls.delete(call);
      host.replaceChildren(element('p','Diff display unavailable: '+e.message+' · Input and Output below are unchanged','m-diff-note'));
    }
  }
  function dropDiff(call) {
    call.diffReader?.destroy(); call.diffReader=null; call.diffValue=null; diffCalls.delete(call);
    call.diffHost.replaceChildren(); call.diffHost.hidden=true;
  }
  // Bars trimmed, gone from their row or left behind by a chat switch: their readers stop observers and pending Show all.
  function sweepDiffs() { for (const call of diffCalls) if (!call.node.isConnected) dropDiff(call); }
  function eachTool(fn) {
    for (const row of shown()?.rows.values() || []) for (const call of row.calls.values()) if (call.snapshot) fn(call);
  }
  function setToolView(kind, mode) { state.toolView[kind]=mode; eachTool(paintTool); }
  // One Wrap for every tool view, the diffs included (CSS only: no repaint, a selection survives).
  function setToolWrap(on) {
    state.wrap=on; $('messages').classList.toggle('m-nowrap',!on);
    for (const call of diffCalls) call.diffReader?.setWrap(on);
    for (const button of $('messages').querySelectorAll('.m-wrap')) button.setAttribute('aria-pressed',String(on));
  }
  // An opened tool's status: one emoji in a fixed-width slot, then the call's time. While a live call refreshes, the emoji
  // flips between 📖 (a read in flight) and ⏳ (no result yet) and the time beside it stays as it was, so the line never
  // re-wraps and the panes under it never jump. The words are the slot's label and the line's title.
  const TOOL_STATES={unread:['·','not read yet'],reading:['📖','reading the stored input and output'],pending:['⏳','pending: no result stored yet'],
    streaming:['✍️','streaming: its input is still arriving'],stored:['✅','complete'],error:['❌','error'],ambiguous:['❔','ambiguous'],
    failed:['⚠️','tool read unavailable']};
  const SNAPSHOT_STATES={stored:'stored','pending-or-missing':'pending','not-final-fragment':'streaming',streaming:'streaming',error:'error',ambiguous:'ambiguous'};
  function toolStatus(call, state, detail, notes) {
    const [emoji,word]=TOOL_STATES[state] || ['❔','status '+(state || 'unknown')];
    text(call.mark,emoji); call.mark.setAttribute('aria-label',word); call.status.dataset.state=state || 'unknown';
    text(call.when,detail); call.status.title=[word,notes,detail].filter(Boolean).join(' · ');
  }
  // @beacon[
  //   id=opc-pane@phoneReadTool,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=An opened phone tool bar reads its conversation reader's exact callRef/fileEpoch snapshot (Claude /api/claude/tool-record, OpenCode /api/opencode/tool-record; never the inspector mailbox), computes the desktop's reading views once per snapshot and paints its recorded diff (the same snapshot's normalized diff field, no further fetch) above input and output with copy buttons; its status line is one emoji in a fixed-width slot (toolStatus: 📖 reading, ⏳ pending, ✅ complete …, the word as its title) and the call's Eastern date and time and, when the result's time is stored, how long it took, kept while a read runs so a live call's refresh never re-wraps the line; open live calls refresh until settled and stale replies cannot cross conversations.,
  // ]
  async function phoneReadTool(chat, call) {
    if (shown() !== chat || !call.node.open || !chat.reader.tool) return;
    if (call.busy) { call.again=true; return; } // A Refresh tap during a read is never lost.
    const callRef=call.data.callRef, fileEpoch=chat.fileEpoch;
    // The time stays beside the reading mark (before the first read, the time the conversation page gave the call).
    const known=call.timeText ?? opcToolReading.callTimeText(Number.isFinite(call.data.time) ? call.data.time*1000 : NaN,
      Number.isFinite(call.data.resultTime) ? call.data.resultTime*1000 : NaN);
    call.busy=true; call.refresh=false; toolStatus(call,'reading',known); call.status.dataset.error='false';
    try {
      const snapshot=await request(chat.reader.tool(chat,callRef,fileEpoch),undefined,{budget:30000,backend:chat.reader.backend});
      if (shown() !== chat || call.data.callRef !== callRef || chat.fileEpoch !== fileEpoch) return;
      if (snapshot.callRef !== callRef || snapshot.fileEpoch !== fileEpoch) throw new Error('Tool snapshot identity changed');
      const input=typeof snapshot.input === 'string' ? snapshot.input : '', output=typeof snapshot.output === 'string' ? snapshot.output : '';
      // The desktop's views, computed once per fetched text (not on each poll or switch).
      if (call.views?.inputText !== input || call.views?.outputText !== output)
        call.views={...opcToolReading.snapshotViews(input,output),inputText:input,outputText:output};
      call.snapshot=snapshot; paintTool(call);
      // The call's time in Eastern time, and how long it took when its result's time is stored too.
      const when=opcToolReading.callTimeText(Number.isFinite(snapshot.time) ? snapshot.time*1000 : NaN,
        Number.isFinite(snapshot.resultTime) ? snapshot.resultTime*1000 : NaN);
      call.timeText=when;
      toolStatus(call,snapshot.inputFinal === false ? 'streaming' : SNAPSHOT_STATES[snapshot.status] || snapshot.status,when,
        [snapshot.inputFinal === false ? 'input still streaming' : '',snapshot.live && !snapshot.terminal ? 'updating: this view refreshes itself' : ''].filter(Boolean).join(' · '));
      call.status.dataset.error='false';
      if (call.reveal) { call.reveal=false; revealTool(call); }
    } catch (e) {
      if (shown() !== chat || call.data.callRef !== callRef) return;
      toolStatus(call,'failed','Tool read unavailable: '+e.message+' · tap Refresh to retry.'); call.status.dataset.error='true';
    } finally {
      call.busy=false;
      if (call.again) { call.again=false; phoneReadTool(chat,call); }
    }
  }
  function toolViews(chat, row, event) {
    const calls=new Map();
    for (const data of event.calls || []) {
      const key=JSON.stringify([data.block,data.id]);
      const call=row.calls.get(key) || toolNode(chat,{});
      if (call.data?.callRef !== data.callRef || call.data?.status !== data.status || call.data?.outputBytes !== data.outputBytes) call.refresh=true;
      if (call.data && call.data.callRef !== data.callRef) call.timeText=undefined;
      call.data=data;
      call.startMs=Number.isFinite(data.time) && data.time > 0 ? data.time*1000 : null;
      call.endMs=Number.isFinite(data.resultTime) ? data.resultTime*1000 : null;
      text(call.name,data.name || 'Tool');
      text(call.flag,data.status === 'stored' ? '' : CALL_FLAGS[data.status] || data.status || 'unknown');
      call.node.classList.toggle('pending',PENDING.has(data.status)); call.node.classList.toggle('failed',data.status === 'error');
      // Its stored total now; refreshToolRunning replaces it with a ticking elapsed time while it runs.
      paintToolTime(call);
      sizeLabel(call.inSize,'in',data.inputBytes); sizeLabel(call.outSize,'out',data.outputBytes);
      const signature=JSON.stringify([data.name,data.previewData]);
      if (signature !== call.previewSignature) { call.preview.replaceChildren(...previewNodes(data)); call.previewSignature=signature; }
      calls.set(key,call);
    }
    for (const [key,call] of row.calls) if (!calls.has(key)) call.node.remove();
    row.calls=calls;
  }
  // The shown conversation's running evidence, the same fact its state line reads: a viewed session's
  // own page busy flag (OpenCode busy/idle; a Claude viewer its live state), else the Manager's host live
  // state. Unknown, a sleeping Manager, an absent live state or a fact older than its read window is
  // never busy.
  function chatWorking() {
    if(state.viewing)return viewerActivity().busy===true;
    if(shown()?.readError)return false;
    const fresh=at=>!Number.isFinite(at) || Date.now()-at <= BUSY_FACT_MS;
    const t=state.target;
    if(backendOf(t)==='opencode')return openCodeLive().busy===true;
    return !!t && t.lifecycle !== 'sleeping' && busy(t.liveState) && fresh(t.liveStateAt);
  }
  // The shown OpenCode conversation's busy/current-call evidence: the viewed session's, or a selected OpenCode
  // Manager's (the fast activity read first, then the state read's fact). A stale/error/absent fact is unknown.
  function openCodeLive(now=Date.now()) {
    if (state.viewing) return viewerActivity(now);
    const t=state.target;
    if (backendOf(t) !== 'opencode') return {busy:null};
    const fast=t.activity,latest=fast && (!Number.isFinite(t.busyAt) || fast.at>t.busyAt)?fast:
      {busy:t.busy,at:t.busyAt,calls:state.chat?.runningCalls,error:state.chat?.readError};
    return {...latest,busy:latest.error || !Number.isFinite(latest.at) || now-latest.at>BUSY_FACT_MS?null:latest.busy};
  }
  // One collapsed call's time cell in place: its stored total once finished, or a whole-second elapsed
  // time while it runs ("12s so far"); an unknown start, a clock before it or a missing stored end stays
  // blank. Text and title only: no body rebuild, scroll move or read.
  function paintToolTime(call, now=Date.now()) {
    const node=call.took; if (!node) return;
    const start=Number.isFinite(call.startMs) ? call.startMs : null, running=call.running === true;
    const took=running ? start !== null ? opcToolReading.elapsedText(start,now) : '' :
      start !== null && Number.isFinite(call.endMs) ? opcToolReading.callDuration(start,call.endMs) : '';
    text(node,running && took ? took+' so far' : took); node.hidden=!took;
    node.title=running ? (took ? 'Running for '+took+': from the call to now' : start !== null ? 'Running now: its elapsed time is unavailable' :
      'Running now: its start time is not recorded') : took ? 'Took '+took+': from the call to its stored result' : '';
  }
  // @beacon[
  //   id=opc-pane@refreshPhoneToolRunning,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Phone current-tool strip and yellow elapsed-time rows use fresh busy evidence plus pending/streaming state after the last receipt, for a viewed OpenCode session and the selected OpenCode Manager; idle, unknown, failed reads and terminal calls never claim running. Ticks change only classes/time cells, not bodies, scroll or requests.,
  // ]
  function refreshToolRunning(now=Date.now()) {
    const shownChat=shown();
    if(state.viewing)renderViewerState();
    const current=shownChat?.reader?.backend === 'opencode' ? openCodeLive(now) : null;
    runningStrip?.update(current?.calls,current ? current.busy : false,current?.at,current?.error,now);
    if (state.view !== 'chat') return;
    const chat=shown(); if (!chat) return;
    const working=chatWorking(), receipt=Number.isInteger(chat.receiptSeq) ? chat.receiptSeq : null;
    for (const row of chat.rows.values()) for (const call of row.calls.values()) {
      const data=call.data, seq=row.event?.seq;
      const running=!!data && PENDING.has(data.status) && working &&
        (receipt === null || (Number.isInteger(seq) && seq > receipt));
      call.running=running; call.node.classList.toggle('running',running);
      paintToolTime(call,now);
    }
  }
  function viewerActivity(now=Date.now()) {
    const v=state.viewing;if(!v)return {busy:null};
    const fast=v.activity,latest=fast && (!Number.isFinite(v.busyAt) || fast.at>v.busyAt)?fast:
      {busy:v.busy,at:v.busyAt,calls:v.chat?.runningCalls,error:v.chat?.readError};
    return {...latest,busy:latest.error || !Number.isFinite(latest.at) || now-latest.at>BUSY_FACT_MS?null:latest.busy};
  }
  // @beacon[
  //   id=opc-pane@phoneOpenCodeActivity,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Phone Chat reads the fast OpenCode busy/current-call snapshot independently of history loading, for a viewed session or the selected OpenCode Manager; exact conversation identity and source timestamps win, stale failures say unknown, and no call body or Send state is touched.,
  // ]
  async function phoneOpenCodeActivity() {
    const v=state.viewing, chat=shown();
    const sid=v?.backend === 'opencode' ? v.sessionRef : (!v && backendOf(state.target) === 'opencode' ? state.target.sessionId : null);
    if(state.view!=='chat' || !sid || chat?.reader?.backend!=='opencode' || state.ocActivityBusy || Date.now()<state.ocActivityNext)return;
    state.ocActivityBusy=true;
    try {
      const d=await request('/api/opencode/activity',undefined,{backend:'opencode'});
      if(state.viewing!==v || (v ? v.sessionRef : state.target?.sessionId)!==sid)return;
      if(d.status!=='complete' || !Array.isArray(d.active) || !Array.isArray(d.sessions) || !Number.isFinite(d.observedAt))throw new Error(d.error || 'OpenCode activity unavailable');
      const row=d.sessions.find(r=>r.id===sid), activity={busy:d.active.includes(sid),at:d.observedAt*1000,
        calls:row?.toolStatus==='unavailable'?null:row?.runningCalls || [],error:d.stale?(d.error || 'activity refresh failed'):null};
      if (v) v.activity=activity;
      else if (backendOf(state.target) === 'opencode' && state.target.sessionId === sid)
        state.target={...state.target,activity,busy:activity.busy,busyAt:activity.at};
    } catch(e) {
      if(state.viewing===v && v) v.activity={...(v.activity || {at:0,busy:null}),error:e.message};
      else if(!v && backendOf(state.target) === 'opencode' && state.target.sessionId === sid)
        state.target={...state.target,activity:{...(state.target.activity || {at:0,busy:null}),error:e.message}};
    }
    finally {state.ocActivityBusy=false;state.ocActivityNext=Date.now()+2000;
      if((v && state.viewing===v) || (!v && backendOf(state.target) === 'opencode' && state.target.sessionId === sid)) { refreshToolRunning(); renderState(); }}
  }
  function currentRoundThinking() { return setting(KEYS.thinkingRound,'true') !== 'false'; }
  // One shown conversation's disclosure memory, outside its disposable chat/rows. Stable identities survive fresh
  // server handles, First/Latest and row eviction; changing conversations (or reloading the page) forgets it.
  function thinkingMemory(chat) {
    const v=chat.viewer ? state.viewing : null;
    const conversation=JSON.stringify([chat.reader.backend,chat.executionId || v?.uuid || chat.key || chat.sessionRef,v?.agentId || null]);
    if (state.thinkingMemory?.conversation !== conversation)
      state.thinkingMemory={conversation,latestUser:null,choices:new Map()};
    return state.thinkingMemory;
  }
  function thinkingButton() {
    const button=$('thinking-mode'), on=currentRoundThinking();
    button.setAttribute('aria-pressed',String(on));
    button.title=on ? 'Thinking: current round (ON). Opens this round and folds earlier rounds. Tap for keep open (OFF).' :
      'Thinking: keep open (OFF). Opens every block; your collapses stay. Tap for current round (ON).';
    button.setAttribute('aria-label',button.title);
  }
  // @beacon[
  //   id=opc-pane@phoneThinkingMode,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Phone thinking disclosures: default current-round opens every block after the newest known user message and folds older rounds; a new user message expires hand choices only in this mode. Keep-open defaults every block open and retains hand collapses. Conversation/row/block memory survives First/Latest and fresh rows; mode changes deliberately reapply defaults, never fetch or change events.,
  // ]
  function applyThinkingMode(chat, ordered) {
    const memory=thinkingMemory(chat), current=currentRoundThinking();
    const user=ordered.findLast(row=>row.event.kind === 'user');
    if (user && (memory.latestUser === null || user.event.seq > memory.latestUser)) {
      // Keep the newest known boundary even when paging back to an earlier window. A tool-result carrier has no
      // prose row and cannot start a round. Every assistant step, tool and final answer after this user belongs to it.
      if (current) memory.choices.clear();
      memory.latestUser=user.event.seq;
    }
    for (const row of ordered) for (const block of row.thinking.values()) {
      const open=memory.choices.get(block.key) ?? (!current || memory.latestUser === null || row.event.seq > memory.latestUser);
      if (block.node.open !== open) block.node.open=open;
    }
  }
  function toggleThinkingMode() {
    const chat=shown(), follow=state.follow, anchor=readingAnchor();
    saveSetting(KEYS.thinkingRound,currentRoundThinking() ? 'false' : 'true'); thinkingButton();
    if (!chat) return;
    thinkingMemory(chat).choices.clear();
    applyThinkingMode(chat,[...chat.rows.values()].sort((a,b)=>a.event.seq-b.event.seq));
    if (follow) $('conversation').scrollTop=$('conversation').scrollHeight; else keepAnchor(anchor);
    offerLatest(chat);
  }
  // Thinking summaries, keyed independently of their DOM nodes; blocks with no stored text stay out of the way.
  function thinkingViews(chat, row, event) {
    const blocks=new Map();
    for (const data of event.thinking || []) {
      if (data.availability !== 'stored' || typeof data.text !== 'string' || !data.text) continue;
      let block=row.thinking.get(data.blockIndex);
      if (!block) {
        const node=element('details',undefined,'m-thinking'), summary=element('summary'), body=element('pre');
        const key=JSON.stringify([thinkingMemory(chat).conversation,row.key,data.blockIndex]);
        node.append(summary,body); block={node,summary,body,key};
        // Own the native summary's activation (keyboard Enter/Space also clicks it), so the anchor is captured
        // BEFORE layout changes. No asynchronous toggle event can mistake an automatic fold for a hand choice.
        summary.addEventListener('click',event=>{
          event.preventDefault();
          if (shown() !== chat) return;
          const follow=state.follow, anchor=readingAnchor(), open=!node.open;
          thinkingMemory(chat).choices.set(key,open); node.open=open;
          if (follow) $('conversation').scrollTop=$('conversation').scrollHeight; else keepAnchor(anchor);
          offerLatest(chat);
        });
      }
      text(block.summary,'Thinking'+(data.streaming ? ' · streaming' : ''));
      text(block.body,data.text);
      blocks.set(data.blockIndex,block);
    }
    for (const [key,block] of row.thinking) if (!blocks.has(key)) block.node.remove();
    row.thinking=blocks;
  }
  function hasThinking(event) {
    return (event.thinking || []).some(b=>b.availability === 'stored' && typeof b.text === 'string' && !!b.text);
  }
  function makeRow(chat, key) {
    const row={key,node:element('article',undefined,'m-row'),head:element('div',undefined,'m-head'),role:element('span',undefined,'m-role'),
      time:element('span',undefined,'m-time'),body:element('div',undefined,'m-prose'),calls:new Map(),thinking:new Map()};
    row.speak=element('button','🔊','m-speak'); row.speak.type='button';
    row.speak.title='Read this message aloud (rewritten for listening; a second tap replays it)'; row.speak.setAttribute('aria-label','Read this message aloud');
    row.speak.addEventListener('click',event=>{ event.preventDefault(); event.stopPropagation(); phoneReadAloud(chat,row.event); });
    row.head.append(row.role,row.time,row.speak,copyButton('⧉',()=>row.event?.text,'Copy this message'));
    chat.rows.set(key,row); return row;
  }
  function speakable(event) { return !!globalThis.opcReadAloud && prose(event) && !event.partial && /^[A-Za-z0-9_.:-]{1,200}$/.test(event.uuid || ''); }
  // @beacon[
  //   id=opc-pane@phoneReadAloud,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The phone's 🔊 beside a message's copy button: read-aloud.js's full-screen player for that message, with this device's Refine settings and the conversation's prose before it (the Manager's or a viewed session's main path; any OpenCode session).,
  // ]
  function phoneReadAloud(chat, event) {
    if (!event || !speakable(event)) return;
    const oc=chat.reader.backend === 'opencode', who=chat.label || 'Manager', p=provider();
    const conversation=oc ? chat.sessionRef : ((chat.viewer ? state.viewing?.uuid : state.target?.uuid) || 'claude');
    opcReadAloud.open({backend:oc ? 'opencode' : 'claude',conversation,message:event.uuid,part:'text',text:event.text,
      speaker:event.kind === 'user' ? 'the user' : 'the assistant ('+who+')',
      title:[event.kind === 'user' ? (chat.you || 'You') : who,clockTime(event.time)].filter(Boolean).join(' · '),
      contextQuery:oc ? {backend:'opencode',id:chat.sessionRef} : chat.targetRef ? {backend:'claude',targetRef:chat.targetRef} : null,
      contextNote:'no conversation context (this view has no main conversation path)',
      settings:()=>({provider:p,model:model(p),apiKey:(setting('refine_'+p+'_api_key','') || '').trim(),
        thinking:setting('refine_think_level','medium'),historyMode:contextMode(),budgetKb:50})});
  }
  // @beacon[
  //   id=opc-pane@phoneClassify,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The desktop's turn roles on the phone: your messages green, final answers red, mid-round prose dim orange, tool records blue and thinking-only records quiet; labels name the actual speaker. Applies the phone's current-round/keep-open thinking rule over the same ordered conversation rows.,
  // ]
  function classify(ordered, chat) {
    const who=chat.label || 'Manager', you=chat.you || 'You';
    let toolsAhead=false;
    for (let i=ordered.length-1;i>=0;i--) {
      const row=ordered[i], event=row.event;
      let kind;
      if (event.kind === 'user') { kind='m-user'; toolsAhead=false; }
      else if (row.calls.size) { kind='m-tools'; toolsAhead=true; }
      else if (prose(event)) kind=!toolsAhead && !event.partial ? 'm-final' : 'm-mid';
      else kind='m-thinking-row';
      if (row.kind !== kind) { row.node.className='m-row '+kind; row.kind=kind; }
      text(row.role,kind === 'm-user' ? you : event.partial ? who+' · writing…' : who);
      row.head.hidden=!['m-user','m-final','m-mid'].includes(kind);
    }
    applyThinkingMode(chat,ordered);
  }
  // Answers the rows it created or repainted, for the seen tracker (trackRows).
  function applyEvents(chat, events, summaries=[]) {
    const touched=new Set();
    for (const event of [...events,...summaries.filter(update=>Array.isArray(update.calls))]) {
      // The end-of-turn receipt barrier: a call before the newest result row is not running, whatever
      // its stored status says. The row itself stays out of the conversation (as on the desktop).
      if (event.kind === 'result' && Number.isInteger(event.seq)) chat.receiptSeq=Math.max(chat.receiptSeq ?? -1,event.seq);
      const key=event.uuid ? (event.scope || 'main')+':'+event.uuid : event.recordRef || chat.fileEpoch+':'+event.seq;
      let row=chat.rows.get(key) || [...chat.rows.values()].find(r=>r.event.recordRef && r.event.recordRef === event.recordRef);
      const visible=event.kind === 'user' ? prose(event) : event.kind === 'assistant' && (prose(event) || !!event.calls?.length || hasThinking(event));
      if (!visible) { if (row) { row.node.remove(); chat.rows.delete(row.key); } continue; }
      row=row || makeRow(chat,key);
      row.event=event; row.body.hidden=!prose(event); row.speak.hidden=!speakable(event);
      if (row.text !== event.text || row.partial !== !!event.partial) {
        row.body.dataset.partial=String(!!event.partial);
        const source=typeof event.text === 'string' ? event.text : '';
        // OpenCode's labelled meta rows (an unknown part, a malformed tool) stay plain text, as on the desktop.
        if (event.partial) row.body.textContent=source; else opcMessageMarkdown.render(row.body,source,!(chat.reader.backend === 'opencode' && event.isMeta === true));
        row.text=event.text; row.partial=!!event.partial;
      }
      text(row.time,clockTime(event.time));
      toolViews(chat,row,event); thinkingViews(chat,row,event);
      place(row.node,[row.head,...[...row.thinking.values()].map(b=>b.node),row.body,...[...row.calls.values()].map(c=>c.node)]);
      touched.add(row);
    }
    // The desktop reader can finish an older call through metadata alone; never
    // fetch a collapsed body just to update its status or size.
    const changed=new Set();
    for (const update of summaries) {
      if (Array.isArray(update.calls)) continue;
      const row=[...chat.rows.values()].find(row=>row.event.recordRef === update.recordRef);
      if (!row) continue;
      row.event={...row.event,calls:(row.event.calls || []).map(call=>call.id === update.id && call.block === update.block ? {...call,...update} : call)};
      changed.add(row);
    }
    for (const row of changed) { toolViews(chat,row,row.event); touched.add(row); }
    const ordered=[...chat.rows.values()].sort((a,b)=>a.event.seq-b.event.seq);
    // The live tail keeps its recent-prose window, including the tools between its turns; a tool-only
    // tail must not evict the latest readable exchange. Nothing at or after keep goes (earlier pages Dan
    // loaded, or the window he is reading above the newest), and ⤒ First's pages keep everything read.
    const proseRows=ordered.filter(row=>prose(row.event));
    let cutoff=chat.start ? undefined : proseRows.at(-80)?.event.seq;
    if (cutoff !== undefined && chat.keep !== null) cutoff=Math.min(cutoff,chat.keep);
    for (const row of ordered) if (cutoff !== undefined && row.event.seq < cutoff) { row.node.remove(); chat.rows.delete(row.key); }
    if (cutoff !== undefined) chat.top=Math.max(chat.top ?? cutoff,cutoff);
    const kept=ordered.filter(row=>chat.rows.has(row.key));
    classify(kept,chat);
    place($('messages'),kept.map(row=>row.node));
    sweepDiffs();
    refreshToolRunning();
    for (const row of kept) for (const call of row.calls.values())
      if (call.node.open && (call.refresh || call.snapshot?.live && !call.snapshot.terminal)) phoneReadTool(chat,call);
    return [...touched].filter(row=>chat.rows.has(row.key));
  }
  // @beacon[
  //   id=opc-pane@phoneFirst,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=⤒ First: the desktop's fromStart on the phone. Rebinds the conversation on screen (the Manager, or a viewed session) with a fresh chat that reads ONE page from fromSeq 0 (bounded like any page) and holds still, scrolled to the top, so the opening prompt can be copied; scrolling down past its end loads the next pages (phonePage) until the newest, where it becomes the live tail; ↓ Latest (phoneLatest) rebinds the ordinary live tail at once.,
  // ]
  async function phoneFirst() {
    if (!rebindShown()) return;
    const chat=shown(); chat.start=true; chat.seek=false; state.follow=false;
    $('first').setAttribute('aria-pressed','true');
    notice('Opening the conversation’s first message…',false,'read-status');
    await phoneReadChat();
  }
  function rebindShown() {
    if (state.viewing) bindViewer();
    else if (state.target) {
      if (backendOf(state.target) === 'opencode') bindOpenCode(state.target,true);
      else bindManager(state.target,true);
    } else return false;
    return true;
  }
  function phoneLatest() {
    if (shown()?.start && rebindShown()) { void phoneReadChat(); return; }
    const n=$('conversation'); n.scrollTop=n.scrollHeight; seen().seenAll(); followCheck();
  }
  function followCheck() {
    // Near the end counts as following, except while the thumb strip drives the conversation: there only the very end
    // does (within the pixel a scroller may snap), so the poll's follow never pulls a drag back as it leaves the newest message.
    const n=$('conversation'), chat=shown(); state.follow=!chat?.start && n.scrollHeight-n.clientHeight-n.scrollTop<(state.stripDriving ? 2 : 32);
    if (state.follow) seen().seenAll(); // reaching the end has shown everything
    if (state.follow && chat) chat.keep=null; // back at the newest: the earlier pages loaded above may go again
    offerLatest(chat);
  }
  // @beacon[
  //   id=opc-pane@phonePage,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Scrolling past either end of what is loaded reads the next batch that way, so no stretch of a long conversation is out of reach (the desktop's loadOlder/loadNewer on the phone; edgePage is the trigger). Up: the rows just above the oldest one on screen, by position (fromSeq, stepping on until they meet it), placed above with the reading position anchored; the live tail then keeps them (chat.keep) until Dan follows the newest again. Down, after ⤒ First or a turn opened from View: the rows after the newest loaded, by position; past the conversation's last row the view becomes the live tail (that page's delta cursor), everything loaded kept. One read at a time, serialized with the poll's; rows it loads count as seen; a failed read says so and the next scroll retries.,
  // ]
  async function phonePage(direction) {
    const chat=shown();
    if (!chat || chat.paging || !chat.loaded || chat.readError || !chat.sessionRef || !chat.scopeRef) return 0;
    if (direction < 0 ? !(chat.top > 0) : !(chat.start && Number.isSafeInteger(chat.hi))) return 0;
    chat.paging=true;
    try {
      for (let i=0; chat.busy && i<400; i++) await delay(25);  // the poll's read in flight finishes first
      if (shown() !== chat || chat.busy) return 0;
      chat.busy=true;
      try {
        notice(direction < 0 ? 'Loading earlier messages…' : 'Loading later messages…',false,'read-status');
        const added=direction < 0 ? await pageEarlier(chat) : await pageLater(chat);
        if (shown() === chat) notice(chat.start ? (chat.from ? 'The turn you came from · ↓ Latest returns to the newest.' : 'First messages · ↓ Latest returns to the newest.') : '',false,'read-status');
        return added;
      } finally { chat.busy=false; }
    } catch (e) {
      if (shown() !== chat) return 0;
      if (RESYNC.has(e.code)) await resyncChat(chat,e.message);
      else notice((direction < 0 ? 'Earlier' : 'Later')+' messages unavailable: '+e.message+' · scroll again to retry',true,'read-status');
      return 0;
    } finally { chat.paging=false; }
  }
  // One page by position, checked like the poll's pages; null once the conversation on screen changed.
  async function pageAt(chat, fromSeq) {
    const data=await request(chat.reader.events(chat,{fromSeq,...(chat.fileEpoch ? {fileEpoch:chat.fileEpoch} : {})}),undefined,{backend:chat.reader.backend});
    if (shown() !== chat) return null;
    if (!chat.reader.same(chat,data)) throw new Error('Conversation '+(chat.viewer ? '' : 'Manager ')+'identity mismatch');
    if (chat.fileEpoch && data.fileEpoch !== chat.fileEpoch) { const e=new Error('conversation epoch changed'); e.code='stale-epoch'; throw e; }
    if (!Array.isArray(data.events)) throw new Error('conversation rows missing');
    chat.damaged=data.rowsUnreadable===true; paintDamaged(); // the same per-read witness, on a paging read
    return data;
  }
  const PAGE_ROWS=100;  // the readers' page bound
  // The batch just above the oldest row on screen, read whole before any of it is shown (a page can stop short of its
  // bound), so the window never has a gap; a batch of rows that show nothing (tool results) reads on to the next one.
  async function pageEarlier(chat) {
    for (let batch=0; batch<20 && chat.top > 0; batch++) {
      const end=chat.top, from=Math.max(0,end-PAGE_ROWS), events=[];
      let at=from;
      while (at < end) {
        const data=await pageAt(chat,at); if (!data) return 0;
        const rows=data.events.filter(e=>Number.isSafeInteger(e.seq) && e.seq >= at && e.seq < end);
        if (!rows.length) break;
        events.push(...rows); at=rows.at(-1).seq+1;
      }
      if (at < end) throw new Error('the rows above did not come back whole');  // the history changed under it: no gap, never spin
      const anchor=readingAnchor();
      if (!chat.start) chat.keep=from;  // the live tail keeps them while Dan reads up here
      chat.top=from;
      const touched=applyEvents(chat,events);
      keepAnchor(anchor); trackRows(touched,false); offerLatest(chat);
      if (touched.length) return touched.length;
    }
    return 0;
  }
  // The page after the newest row loaded (⤒ First's window); at the conversation's end the view joins the live tail.
  async function pageLater(chat) {
    const from=chat.hi+1, data=await pageAt(chat,from); if (!data) return 0;
    const events=data.events.filter(e=>Number.isSafeInteger(e.seq) && e.seq >= from);
    const anchor=readingAnchor();
    if (events.length) chat.hi=Math.max(...events.map(e=>e.seq));
    const touched=applyEvents(chat,events);
    keepAnchor(anchor); trackRows(touched,false);
    if (data.status !== 'indexing' && data.deltaCursor && (!events.length || (data.rowsAhead ?? data.hasMore) === false)) {
      // The newest row is on screen: from here this is the live tail, read on by its delta, every loaded row kept until
      // Dan follows the newest. The rows that arrive later while he reads above are new to him.
      Object.assign(chat,{start:false,from:undefined,delta:data.deltaCursor,next:null,seek:false,keep:chat.top});
      $('first').setAttribute('aria-pressed','false');
      followCheck();
    }
    offerLatest(chat);
    return touched.length;
  }
  // The row at the top of the conversation and where it sits, so rows placed above it leave the reading position.
  function readingAnchor() {
    const top=$('conversation').getBoundingClientRect().top;
    for (const node of $('messages').children) { const r=node.getBoundingClientRect(); if (r.bottom > top) return {node,y:r.top-top}; }
    return null;
  }
  function keepAnchor(anchor) {
    if (!anchor?.node.isConnected) return;
    const box=$('conversation');  // whole pixels: WebKit drops a scroll position's fraction
    box.scrollTop=Math.round(box.scrollTop+anchor.node.getBoundingClientRect().top-box.getBoundingClientRect().top-anchor.y);
  }
  // Only Dan's own scrolling pages (a touch, the wheel, keys, the thumb strip), never the page's own scroll writes;
  // a touch pushing against an end pages even where no scroll event comes.
  const EDGE_PX=480, INTENT_MS=3000;
  function edgePage() {
    const chat=shown(), n=$('conversation');
    if (!chat || chat.paging || state.view !== 'chat' || (!state.stripDriving && performance.now()-state.scrollIntent > INTENT_MS)) return;
    if (n.scrollTop < EDGE_PX && chat.top > 0) void phonePage(-1);
    else if (chat.start && n.scrollHeight-n.clientHeight-n.scrollTop < EDGE_PX) void phonePage(1);
  }
  // The rows' seen-ness (seen-latest.js), one tracker for the conversation scroller whatever it shows.
  // A final reply is classify's m-final (prose, settled, no tool call after it before your next message).
  function seen() {
    return state.seen || (state.seen=globalThis.opcSeenLatest?.create($('conversation'),{onChange:()=>offerLatest(shown()),isFinal:node=>node.classList.contains('m-final')}) ||
      {track:()=>false,seenAll(){},below:()=>0,tally:()=>({count:0,final:0})});
  }
  // Rows a read created or repainted. live: they came from the live tail (the delta) while Dan is
  // scrolled up, so a new row (or a seen row that grew) whose end is off screen is new; everything
  // else (the opening read, continuation pages, earlier history filled in above, any read while
  // following) is born seen. Answers whether new material arrived.
  function trackRows(rows, live) {
    let arrived=false;
    for (const row of rows) arrived=seen().track(row.node,{live,version:JSON.stringify(row.event)}) || arrived;
    return arrived;
  }
  // @beacon[
  //   id=opc-pane@offerLatest,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The ↓ Latest badge: offered only while every part of the last item is off screen (or after ⤒ First); lit plain "↓ Latest" when all below has been seen, the accent (orange) "N new ↓" (seen-latest.js paintBadge) while the unseen rows ending below the view are only working turns, and red while at least one of them is a final reply (classify's m-final, judged at every repaint), pulsing about 5 s whenever new material arrives (trackRows) or it turns red.,
  // ]
  // ↓ Latest means "something below you": offered only while every part of the
  // last item (the last visible block of the last row: a message, its thinking or its last tool
  // call) is off screen, so a view parked just above the end does not offer it, and anything new
  // arriving below brings it back. After ⤒ First the newest window is not loaded, so it stays offered.
  // Whether what is below is NEW (not yet seen) turns it into the accent "N new ↓"; red once a final reply is among it.
  function offerLatest(chat) {
    const badge=$('latest');
    badge.hidden=!chat?.rows.size || (!chat.start && (state.follow || lastInView()));
    const unseen=seen().tally(); // also marks unseen rows whose end is on screen now
    if (globalThis.opcSeenLatest) opcSeenLatest.paintBadge(badge,badge.hidden || chat.start ? 0 : unseen.count,'↓ Latest',unseen.final);
  }
  function lastInView() {
    const shows=node=>!node.hidden && !node.classList.contains('seen-end') && node.getClientRects().length > 0;
    let row=$('messages').lastElementChild;
    while (row && !shows(row)) row=row.previousElementSibling;
    if (!row) return true;
    const last=[...row.children].filter(shows).at(-1) || row;
    const r=last.getBoundingClientRect(), c=$('conversation').getBoundingClientRect();
    return r.bottom-c.top > 1 && c.bottom-r.top > 1;
  }
  // @beacon[
  //   id=opc-pane@phoneReadChat,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Reads the shown conversation through its exact backend reader: tail/deltas and checked resync retain keyed rows and disclosure choices, anchoring the paused row even when earlier thinking folds; Following stays at the end. Slow/failed reads keep messages with a quiet catching-up line; resync swaps only after a fresh page. First and positional paging keep the window reachable, rowsUnreadable adds only its own quiet note, OpenCode carries fresh running-call evidence, and reading never changes Send targeting.,
  // ]
  async function phoneReadChat() {
    if (state.viewing && !state.viewing.chat) return phoneResyncViewer();
    const chat=shown(); if (!chat || chat.busy || !chat.sessionRef || !chat.scopeRef || chat.start && chat.loaded) return;
    // An archived conversation never changes: once its newest page is read, only Dan's own paging reads it again.
    if (chat.reader.backend === 'history' && chat.loaded && !chat.next && !(chat.seek && chat.lo > 0)) return;
    chat.busy=true;
    const slow=setTimeout(()=>{ if (shown() === chat && chat.busy && $('messages')?.childElementCount) notice(CATCHING_UP,false,'read-status'); },SLOW_READ_MS);
    try {
      const params={};
      if (chat.start) params.fromSeq=chat.from ?? 0;  // ⤒ First, or a turn View jumps back to (phoneShowTurn)
      else if (chat.next) params.cursor=chat.next;
      else if (chat.loaded && chat.seek && chat.lo > 0) {
        params.fromSeq=Math.max(0,chat.lo-100); chat.earlierEnd=chat.lo;
      }
      else if (chat.delta) params.cursor=chat.delta;
      if (chat.fileEpoch) params.fileEpoch=chat.fileEpoch;
      // The live tail: the delta, and the continuation pages of a delta that had more.
      const fromTail=!chat.start && params.fromSeq === undefined && params.cursor !== undefined &&
        (params.cursor === chat.delta || (chat.tailChain && params.cursor === chat.next));
      const data=await request(chat.reader.events(chat,params),undefined,{backend:chat.reader.backend});
      if (shown() !== chat) return;
      if (!chat.reader.same(chat,data)) throw new Error('Conversation '+(chat.viewer ? '' : 'Manager ')+'identity mismatch');
      if (chat.fileEpoch && data.fileEpoch !== chat.fileEpoch) { await resyncChat(chat,'conversation epoch changed'); return; }
      if (!Array.isArray(data.events)) throw new Error('conversation rows missing');
      chat.readError=null;
      if (chat.swap) { chat.swap=false; clearConversation(); } // a resync's kept messages give way only now
      // This read's own damage witness: an ordinary page or an old host clears the note, a failed read leaves it.
      chat.damaged=data.rowsUnreadable===true; paintDamaged();
      if (chat.viewer) {
        state.viewing.busy=chat.reader.backend === 'opencode' ? (typeof data.busy === 'boolean' ? data.busy : null) : data.liveState ? busy(data.liveState) : null;
        state.viewing.busyAt=Number.isFinite(data.observedAt)?data.observedAt*1000:Date.now();
        if (chat.reader.backend !== 'opencode') { state.viewing.liveState=data.liveState || null; if (Number.isSafeInteger(data.generation)) state.viewing.generation=data.generation; }
        renderViewerState();
      } else if (chat.reader.backend === 'claude') rememberContext(chat.executionId,data.contextReadout);
      if(chat.reader.backend==='opencode')chat.runningCalls=Array.isArray(data.runningCalls)?data.runningCalls:null;
      chat.fileEpoch=data.fileEpoch; chat.sessionRef=data.sessionRef; chat.scopeRef=data.scopeRef;
      if (chat.reader.backend !== 'opencode') chat.targetRef=typeof data.targetRef === 'string' && data.targetRef ? data.targetRef : null; // Read Aloud's context path
      chat.delta=data.deltaCursor || chat.delta; chat.next=data.hasMore && !chat.start ? data.nextCursor : null;
      chat.tailChain=fromTail && !!chat.next;
      const scroll=$('conversation'), top=scroll.scrollTop, anchor=!state.follow && !chat.start ? readingAnchor() : null;
      const positions=data.events.map(e=>e.seq).filter(Number.isSafeInteger);
      if (positions.length) { chat.top=Math.min(chat.top ?? Infinity,...positions); chat.hi=Math.max(chat.hi ?? -1,...positions); }
      // Read above the newest, the window holds still: the tail's trim waits until Dan follows it again (followCheck).
      if (!state.follow && !chat.start && chat.keep === null && chat.top !== null) chat.keep=chat.top;
      const touched=applyEvents(chat,[...data.events,...(data.eventUpdates || [])],data.summaryUpdates || []);
      const previousLo=chat.lo;
      if (positions.length) chat.lo=Math.min(chat.lo ?? Infinity,...positions);
      // An earlier read need only reach the tail already seen, not scan it again.
      if (chat.earlierEnd !== null && positions.length && Math.max(...positions) >= chat.earlierEnd-1) {
        chat.next=null; chat.earlierEnd=null;
      }
      const kinds=new Set([...chat.rows.values()].filter(r=>prose(r.event)).map(r=>r.event.kind));
      chat.seek=!chat.start && !(kinds.has('user') && kinds.has('assistant')) && (chat.lo === null || chat.lo > 0) &&
        !(chat.lo === null && !data.hasMore && data.status === 'complete');
      // The opening page is read once; an index still being built is read again on the next poll.
      chat.loaded=!chat.start || chat.rows.size > 0 || data.status !== 'indexing';
      if (chat.start) scroll.scrollTop=0; else if (state.follow) scroll.scrollTop=scroll.scrollHeight; else { scroll.scrollTop=top; keepAnchor(anchor); }
      const arrived=trackRows(touched,fromTail && !state.follow);
      offerLatest(chat);
      if (arrived && $('latest').dataset.unseen) opcSeenLatest.pulseBadge($('latest'));
      notice(chat.start && chat.rows.size ? (chat.from ? 'The turn you came from · ↓ Latest returns to the newest.' : 'First messages · ↓ Latest returns to the newest.') : chat.seek ? 'Looking for the latest conversation before tool activity…' : data.status === 'indexing' ? 'Loading conversation…' :
        chat.rows.size ? '' : 'No prose messages in this conversation yet.',false,'read-status');
      // Keep the cold path quick, including a tail made entirely of tools. Yield between pages.
      const progressed=chat.lo !== previousLo || (chat.next && chat.next !== params.cursor) ||
        (params.fromSeq === undefined && (data.events.length || data.eventUpdates?.length));
      if (progressed && (chat.next || (chat.seek && chat.lo > 0))) setTimeout(()=>phoneReadChat(),0);
    } catch (e) {
      if (shown() !== chat) return;
      chat.readError=e.message;refreshToolRunning();
      if (RESYNC.has(e.code)) await resyncChat(chat,e.message);
      // Slow or unanswered (a 5xx, a timed-out request, the network): the messages on screen stay, quietly. A refusal
      // or a page that failed its checks (another execution, missing rows) says why, as before.
      else if ($('messages')?.childElementCount && ((Number.isInteger(e.status) && e.status >= 500) || ['AbortError','TypeError'].includes(e.name)))
        notice(CATCHING_UP,false,'read-status');
      else notice('Conversation unavailable: '+e.message+' · retrying',true,'read-status');
    } finally { clearTimeout(slow); chat.busy=false; }
  }
  function resyncChat(chat, reason) { return chat.viewer ? phoneResyncViewer(reason) : resync(reason); }

  function journalValid(op, key) {
    return op?.schemaVersion === 1 && registered(op.target) && UUID.test(op.messageUuid) &&
      key === KEYS.send+op.messageUuid && typeof op.original === 'string' && typeof op.submitted === 'string' &&
      op.submitted === op.original.trim() && !!op.submitted && Number.isFinite(op.createdAt) &&
      typeof op.witness?.page === 'string' && Number.isSafeInteger(op.witness.selectionRevision) && Number.isSafeInteger(op.witness.draftRevision) &&
      op.witness.executionId === op.target.executionId && ['pending','uncertain','accepted','settled','refused','released'].includes(op.state);
  }
  // @beacon[
  //   id=opc-pane@prunePhoneJournals,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Retains the newest 50 finished phone Sends from live storage; unresolved or unreadable records and ACKs still needing draft handling are never pruned.,
  // ]
  function prunePhoneJournals() {
    const finished=[];
    for (let i=0;i<localStorage.length;i++) {
      const key=localStorage.key(i); if (!key?.startsWith(KEYS.send)) continue;
      const raw=read(key); let op;
      try { op=JSON.parse(raw); } catch (_) { continue; }
      if (journalValid(op,key) && ['settled','refused','released'].includes(op.state) &&
          !(op.state === 'settled' && op.receipt?.acknowledged && !op.handledAck)) finished.push({key,raw,op});
    }
    finished.sort((a,b)=>b.op.createdAt-a.op.createdAt || a.key.localeCompare(b.key));
    for (const {key,raw,op} of finished.slice(50)) {
      if (read(key) !== raw) continue; // Another tab's newer, possibly unresolved record wins.
      localStorage.removeItem(key); state.journals.delete(op.messageUuid);
    }
  }
  function saveJournal(op) {
    prunePhoneJournals(); // Free an existing backlog before attempting the durable write.
    const key=KEYS.send+op.messageUuid; write(key,JSON.stringify(op)); state.journals.set(op.messageUuid,op);
    prunePhoneJournals();
  }
  function restoreJournals() {
    try {
      for (let i=0;i<localStorage.length;i++) {
        const key=localStorage.key(i); if (!key?.startsWith(KEYS.send)) continue;
        const raw=read(key);
        try {
          const op=JSON.parse(raw); if (!journalValid(op,key)) throw new Error('invalid record');
          if (!state.journals.has(op.messageUuid)) state.journals.set(op.messageUuid,op);
        } catch (_) { state.badJournals.set(key,raw); }
      }
      prunePhoneJournals();
    } catch (e) { notice('Phone journal could not be read: '+e.message+' · a new Send must still save its journal.',true); }
    renderJournals();
  }
  function pending(t) {
    return [...state.journals.values()].find(op=>op.target.executionId === t?.executionId && !['settled','refused','released'].includes(op.state));
  }
  function releaseSend(messageUuid) {
    const op=state.journals.get(messageUuid); if (!op || ['settled','refused','released'].includes(op.state)) return;
    const updated={...op,state:'released',releasedAt:Date.now(),note:'Deliberately released after checking; never resent. Original retained.'};
    try { saveJournal(updated); notice(updated.note); renderJournals(); }
    catch (e) { notice('Release not saved: '+e.message+' · draft retained',true); }
  }

  // ---- First-class OpenCode Managers: the shared device journal (static/opencode-manager.js) owns the durable
  // records; this page owns the target, draft and draft-clearing rules. Keys are backend/target/operation qualified.
  const OC_PREFIX=NS+'manager:opencode:';
  // The phone's read of the shared journal: one unreadable record is set aside (shown raw by ocBadRecords) and never
  // hides or holds a valid operation; the shared client's own strict entries() stays the protocol authority.
  function ocEntries(kind, target=null) {
    if (!ocApi) return [];
    const out=[];
    for (let i=0;i<localStorage.length;i++) {
      const key=localStorage.key(i); if (!key?.startsWith(OC_PREFIX)) continue;
      let rec; try { rec=JSON.parse(read(key)); } catch (_) { continue; }
      if (!rec?.operationId || !rec.intent || !['send','control','launch'].includes(rec.kind)) continue;
      if (kind && rec.kind !== kind) continue;
      if (target && !ocApi.same(rec.intent.target,target)) continue;
      out.push(rec);
    }
    return out.sort((a,b)=>(a.createdAt || 0)-(b.createdAt || 0));
  }
  function ocPending(t) {
    const sid=mid(t) || (OC_SID.test(state.selected || '') ? state.selected : null); if (!ocApi || !sid) return null;
    // Match the backend-qualified target id, not a witness that may not be read yet on reload: an unresolved Send
    // must hold its own target from the first paint.
    return ocEntries('send').find(r=>r.intent?.target?.sessionId === sid && !r.releasedAt &&
      !ocApi.acknowledged(r) && r.receipt?.state !== 'failed') || null;
  }
  // Every unreadable shared record in raw copyable form: shown and set aside, never a Send hold.
  function ocBadRecords() {
    const out=[];
    if (!ocApi) return out;
    for (let i=0;i<localStorage.length;i++) {
      const key=localStorage.key(i); if (!key?.startsWith(OC_PREFIX)) continue;
      const raw=read(key);
      try {
        const rec=JSON.parse(raw);
        if (!rec?.operationId || !rec.intent || !['send','control','launch'].includes(rec.kind) ||
            (rec.kind !== 'launch' && !ocApi.valid(rec.intent.target))) throw new Error('invalid record');
      } catch (_) { out.push([key,raw]); }
    }
    return out;
  }
  function ocSendNote(record) {
    const r=record.receipt, status=r?.state, ack=ocApi.acknowledged(record);
    if (record.releasedAt) return 'Released after checking; never resent. Original retained.';
    if (record.error && record.phase === 'uncertain' && !r) return 'This Send could not be prepared or posted: '+record.error+' Original retained.';
    if (r?.state === 'conflict') return 'OpenCode admitted this operation with different target/body evidence; not acknowledged. Check the conversation; original retained.';
    if (r?.state === 'failed') return 'OpenCode refused this operation'+(r.detail ? ': '+r.detail : '')+'; original retained.';
    if (r?.state === 'prepared') return 'The pane holds this intent; no native Send evidence yet. Checking this operation only; never resending.';
    if (ack) return status === 'pending' ? 'Durably pending in the Manager’s inbox; it is taken at its next step boundary.' : 'Delivered (exact native receipt).';
    if (r?.state === 'uncertain') return (r.detail || 'Receipt uncertain')+' · checking this operation only, never resending.';
    return 'Accepted by the pane; waiting for exact native evidence (this operation only).';
  }
  // The one rule for clearing an OpenCode Send's draft: exact native evidence, the unchanged same backend target and
  // witness, the same page's draft revision (or an exact restored draft after a reload), and the persisted draft still
  // holding that exact original. A delayed ACK for another target can satisfy none of it.
  // @beacon[
  //   id=opc-pane@ocDraftClearable,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The one rule for clearing an OpenCode Send's phone draft: exact native evidence (the shared journal's acknowledged receipt), the unchanged same backend target witness, the same page's draft revision or an exact restored draft after reload, and a persisted draft still holding that exact original. A delayed ACK for another target satisfies none of it.,
  // ]
  function ocDraftClearable(record, snap={}) {
    const target=record?.intent?.target;
    if (!ocApi || !record || !target || record.releasedAt || !ocApi.acknowledged(record)) return false;
    if (snap.selected !== target.sessionId || mid(snap.currentTarget) !== target.sessionId ||
        snap.currentTarget?.witness !== target.witness) return false;
    if (snap.draftValue !== record.intent.original) return false;
    const saved=snap.saved;
    if (!saved || (saved.target ?? saved.executionId) !== snap.selected || saved.text !== record.intent.original) return false;
    if (record.savedDraftToken && (saved.revisionToken !== record.savedDraftToken || saved.writer !== record.page)) return false;
    if (record.selectionMarker && (snap.selectionMarker !== record.selectionMarker || snap.storedSelectionMarker !== record.selectionMarker)) return false;
    const restored=record.page !== snap.page;
    if (restored) {
      // A reload may clear only the exact boot-restored draft, not edited text, not an away-and-back selection, and not
      // a different tab's draft. Boot captures the restored text, owner and the revision/epoch it was restored with.
      const boot=snap.boot;
      if (!boot || boot.text !== snap.draftValue || boot.owner !== snap.selected ||
          (boot.ownerToken && boot.ownerToken !== selectionToken(snap.selected)) ||
          (saved.owner && saved.owner !== snap.selected) ||
          (saved.selectionEpoch != null && Number.isSafeInteger(saved.selectionEpoch) && saved.selectionEpoch !== snap.selectionEpoch) ||
          boot.draftRevision !== snap.draftRevision || boot.selectionRevision !== snap.selectionRevision ||
          (Number.isSafeInteger(boot.selectionEpoch) && boot.selectionEpoch !== snap.selectionEpoch) ||
          (Number.isSafeInteger(record.selectionEpoch) && record.selectionEpoch !== snap.selectionEpoch) ||
          (Number.isSafeInteger(saved.draftRevision) && saved.draftRevision !== boot.draftRevision)) return false;
      return true;
    }
    // Same page: the exact Send witness (revision + epoch + unchanged text) is required.
    if (record.page !== snap.page || record.selectionRevision !== snap.selectionRevision ||
        record.draftRevision !== snap.draftRevision) return false;
    if (Number.isSafeInteger(record.selectionEpoch) && record.selectionEpoch !== snap.selectionEpoch) return false;
    return true;
  }
  // @beacon[
  //   id=opc-pane@phoneApplyOcReceipt,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=An OpenCode Send's exact native evidence (a pending inbox item or delivered history item with the same session/native id/type/text) is the only thing that clears a draft, and only for the unchanged same backend+target witness, the same page revision or an exact restored draft after reload; conflict/uncertain/failed never clear, and a delayed ACK for another target neither clears nor holds this one.,
  // ]
  function phoneApplyOcReceipt(record,{quiet=false}={}) {
    const ack=ocApi.acknowledged(record), target=record.intent?.target;
    let note=ocSendNote(record);
    let updated=record;
    if (ack && !record.releasedAt && !record.handledAck && target) {
      let saved=null; try { saved=JSON.parse(read(KEYS.draft) || 'null'); } catch (_) {}
      let text=note;
      if (ocDraftClearable(record,{currentTarget:state.target,selected:state.selected,page:state.page,
          selectionRevision:state.selectionRevision,draftRevision:state.draftRevision,draftValue:$('draft').value,
          selectionEpoch:state.selectionEpoch,selectionMarker:state.selectionMarker,storedSelectionMarker:read(KEYS.selectionMarker),boot:state.bootDraft,saved})) {
        try {
          saveDraft(''); $('draft').value=''; state.draftRevision++; fitDraft();
          text+=' Unchanged draft cleared; original retained in the journal.';
        } catch (e) { text+=' Draft could not be saved as cleared: '+e.message+'; text retained.'; }
      } else text+=' Draft/Manager changed; text retained.';
      updated=ocApi.save({...record,handledAck:true,note:text});
      note=text;
    } else if (record.note !== note) {
      updated=ocApi.save({...record,note});
    }
    if (!quiet || ack) {
      const failure=['conflict','failed'].includes(record.receipt?.state);
      if (target?.sessionId === state.selected) notice(note,failure);
      else if (ack) notice((record.intent?.target?.sessionId || 'An OpenCode Manager')+': '+note,failure);
    }
    renderJournals();
    return updated;
  }
  async function releaseOcSend(operationId) {
    const rec=ocEntries('send').find(r=>r.operationId === operationId); if (!rec) return;
    try { ocApi.release(rec); notice('Released after checking; never resent. Original retained.'); renderJournals(); }
    catch (e) { notice('Release not saved: '+e.message+' · draft retained',true); }
  }
  async function pollOpenCodeReceipts() {
    if (!ocApi) return;
    for (const rec of ocEntries('send')) {
      if (rec.releasedAt || rec.handledAck || state.receiptBusy.has('oc:'+rec.operationId)) continue;
      if (ocApi.acknowledged(rec)) { phoneApplyOcReceipt(rec,{quiet:true}); continue; }
      if (['failed','conflict'].includes(rec.receipt?.state)) continue;
      state.receiptBusy.add('oc:'+rec.operationId);
      try { phoneApplyOcReceipt(await ocApi.poll(rec),{quiet:true}); }
      catch (e) { rec.note='Receipt observation unavailable: '+e.message+' · same operation only, never resend'; }
      finally { state.receiptBusy.delete('oc:'+rec.operationId); }
    }
  }
  function renderJournals() {
    // Send is locked from the press until that Send's outcome is known (its exact ACK, a refusal, a failure before the
    // POST) or Dan releases it after 30 s with the link below, the way out of a receipt that never resolves; a reload
    // restores the same lock from the journal. Each backend/target holds only its own Send.
    const ocPendingTarget=(backendOf(state.target) === 'opencode' || OC_SID.test(state.selected || '')) ? state.target : null;
    const ocHeld=ocPendingTarget ? ocPending(ocPendingTarget) : null;
    $('send').disabled=state.sending || !!pending(state.target) || !!ocHeld;
    const releases=$('release'); releases.replaceChildren();
    for (const op of state.journals.values()) {
      if (op.target.executionId === state.selected && !['settled','refused','released'].includes(op.state) && Date.now()-op.createdAt >= 30000) {
        const link=document.createElement('a'); link.href='#'; link.textContent='I checked — release it';
        link.addEventListener('click',e=>{e.preventDefault();releaseSend(op.messageUuid);});
        const p=document.createElement('p'); p.append(document.createTextNode('Send '+op.messageUuid.slice(0,8)+' is unresolved. Check the conversation, then '),link); releases.append(p);
      }
    }
    if (ocApi && (backendOf(state.target) === 'opencode' || OC_SID.test(state.selected || ''))) for (const rec of ocEntries('send').filter(r=>r.intent?.target?.sessionId === (mid(state.target) || state.selected))) {
      if (rec.releasedAt || ocApi.acknowledged(rec) || rec.receipt?.state === 'failed' || Date.now()-(rec.createdAt || 0) < 30000) continue;
      const link=document.createElement('a'); link.href='#'; link.textContent='I checked — release it';
      link.addEventListener('click',e=>{e.preventDefault();void releaseOcSend(rec.operationId);});
      const p=document.createElement('p'); p.append(document.createTextNode('OpenCode Send '+String(rec.operationId).slice(0,8)+' is unresolved. Check the conversation, then '),link); releases.append(p);
    }
    $('unreadable').replaceChildren();
    for (const [key,raw] of state.badJournals) {
      const details=document.createElement('details'), summary=document.createElement('summary'), text=document.createElement('textarea');
      summary.textContent='Unreadable phone Send record set aside (does not hold Send): '+key;
      text.readOnly=true; text.value=raw ?? ''; text.rows=5; details.append(summary,text); $('unreadable').append(details);
    }
    for (const [key,raw] of ocBadRecords()) {
      const details=document.createElement('details'), summary=document.createElement('summary'), text=document.createElement('textarea');
      summary.textContent='Unreadable OpenCode Manager record set aside (does not hold Send): '+key;
      text.readOnly=true; text.value=raw ?? ''; text.rows=5; details.append(summary,text); $('unreadable').append(details);
    }
  }
  // @beacon[
  //   id=opc-pane@phoneApplyReceipt,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Matches exact durable host UUID/generation/original/submitted evidence; only ACK or safe queued steer clears an unchanged same-Manager draft after verified persistence, including exact restored text on reload, never after release.,
  // ]
  function phoneApplyReceipt(op, data) {
    // A deliberate release may have landed while this read was in flight.
    op=state.journals.get(op.messageUuid) || op;
    envelope(data,op.target);
    const r=data.receipt;
    if (!r) { op.note='Receipt not found yet; querying this UUID only, never resending.'; saveJournal(op); return; }
    if (r.execution_id !== op.target.executionId || r.generation !== op.target.generation || r.message_uuid !== op.messageUuid ||
        r.original !== op.original || r.submitted !== op.submitted || r.durable !== true)
      throw new Error('Exact receipt mismatch; never clear or resend');
    const receipt={durable:true,...Object.fromEntries(['acknowledged','started','completed','queued','delivered','cancelled','uncertain'].map(k=>[k,r[k] === true])),status:r.status};
    const queued=op.steer && receipt.queued && !receipt.uncertain && r.status !== 'uncertain' && !receipt.cancelled;
    const released=op.state === 'released';
    op.receipt=receipt;
    if (!released) op.state=receipt.acknowledged || receipt.completed || receipt.cancelled ? 'settled' : 'accepted';
    op.note=released ? 'Released; receipt observed, never resent.' : receipt.acknowledged ? 'Delivered (exact replay ACK).' :
      receipt.cancelled ? 'Cancelled before delivery; text retained, never resent.' : queued ? 'Queued for the busy Manager at its next tool step.' :
      receipt.completed ? 'Completed; no exact ACK, text retained.' : 'Durably accepted; awaiting exact replay ACK.';
    saveJournal(op);
    if (!released && (receipt.acknowledged || queued) && !op.handledAck) {
      const restored=op.witness.page !== state.page;
      const same=state.selected === op.target.executionId && $('draft').value === op.original &&
        (restored || unchanged(op.witness,op.original));
      if (same) {
        // Do not clobber changed persisted text from another phone tab either.
        const saved=JSON.parse(read(KEYS.draft) || 'null');
        if (saved?.executionId === state.selected && saved.text === op.original) {
          saveDraft(''); $('draft').value=''; state.draftRevision++; fitDraft();
          op.note+=' Unchanged draft cleared; original retained in the journal.';
        } else op.note+=' Stored draft differs; text retained.';
      } else op.note+=' Draft/Manager changed; text retained.';
      op.handledAck=true; saveJournal(op);
    }
    if (op.target.executionId === state.selected) notice(op.note);
    renderJournals();
  }
  async function pollReceipts() {
    for (const op of state.journals.values()) {
      if ((op.state === 'settled' && !(op.receipt?.acknowledged && !op.handledAck)) || ['refused','released'].includes(op.state) || state.receiptBusy.has(op.messageUuid)) continue;
      state.receiptBusy.add(op.messageUuid);
      try { phoneApplyReceipt(op,await request('/api/claude/receipt?'+query(op.target,{messageUuid:op.messageUuid}))); }
      catch (e) { op.note='Receipt observation unavailable: '+e.message+' · same UUID only, never resend'; }
      finally { state.receiptBusy.delete(op.messageUuid); }
    }
    await pollOpenCodeReceipts();
    renderJournals();
  }
  function busy(live) {
    if (!live) return false;
    const native=live.native_session_state, closed=live.primary_turn_open === false;
    const ownPermission=live.pending_native_requests && Object.values(live.pending_native_requests).some(r=>!r || typeof r.agent_id !== 'string');
    return !!(live.pending_message_uuids?.length || live.native_retry_active ||
      (['running','requires_action'].includes(native) && !closed) || Number(live.native_queued_turns)>0 || ownPermission ||
      (live.native_state_required && !['idle','running','requires_action'].includes(native)));
  }
  async function reconnect(t, witness, original) {
    notice('Reconnecting to '+t.title+'…');
    let record;
    try { record=await request('/api/claude/reconnect',identity(t),{budget:60000}); }
    catch (e) {
      if (!REFUSED.has(e.status)) await phoneLoadManagers(); // Lost reconnect is not a Send. Rediscover; no automatic reconnect/Send retry.
      throw e;
    }
    guard(witness,original);
    if (record.executionId !== t.executionId || record.uuid !== t.uuid || record.role !== 'Manager' || record.previousGeneration !== t.generation ||
        !Number.isSafeInteger(record.generation) || record.generation < t.generation || typeof record.targetRef !== 'string' || !record.targetRef)
      throw new Error('Reconnect identity/generation mismatch; message not sent');
    return awaitReady({...t,...record,source:'host',capabilities:{...t.capabilities,...record.capabilities}},witness,original,'Connecting to '+t.title);
  }
  // Observes the Send's Manager until its native input is ready: after a reconnect, or while a
  // Manager is still starting (a fresh ＋ New Manager). An edit or another Manager cancels the Send.
  async function awaitReady(live, witness, original, label) {
    for (;;) {
      guard(witness,original);
      try {
        const data=envelope(await request('/api/claude/state?'+query(live)),live);
        guard(witness,original);
        live={...live,lifecycle:data.lifecycle,capabilities:data.capabilities,liveState:data.liveState};
        if (writable(live)) { state.target=live; renderState(data); state.nextList=0; return live; }
        notice(label+' · '+(data.lifecycle || 'waiting for native readiness')+' · edit or choose another Manager to cancel this Send');
      } catch (e) {
        guard(witness,original);
        if (REFUSED.has(e.status)) throw e;
        notice(label+': '+e.message+' · observing, no message sent');
      }
      await delay(2000);
    }
  }
  // @beacon[
  //   id=opc-pane@phoneSend,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Deliberate Send clears the Refine cue, validates host state, reconnects sleeping Managers, waits for one still starting (a fresh New Manager) and steers busy own turns; full original/identity/revision/UUID is saved before one POST, with uncertain outcomes query-only and same-execution holds releasable. A selected OpenCode Manager takes phoneOpenCodeSend instead (one server-journal intent, native steer). The button stays disabled from the press until that Send's outcome is known or released (renderJournals).,
  // ]
  async function phoneSend() {
    if (state.viewing) return notice('Read-only · Send goes to Managers; draft kept.',true); // a viewed session never receives Send
    $('draft').classList.remove('m-refined');
    if (state.sending) return;
    let t=state.target;
    if (backendOf(t) === 'opencode') return openCodeSend();
    if (!registered(t)) return notice('Choose a listed Manager; draft kept.',true);
    const original=$('draft').value, submitted=original.trim(); if (!submitted) return notice('Nothing to send.');
    restoreJournals();
    const prior=pending(t);
    if (prior) { notice('Previous Send '+prior.messageUuid.slice(0,8)+' is unresolved; checking its receipt only. Draft kept.',true); await pollReceipts(); return; }
    const witness=frozenIntent(); let op, posted=false;
    state.sending=true; renderState();
    try {
      if (reconnectable(t)) t=await reconnect(t,witness,original);
      guard(witness,original);
      const data=envelope(await request('/api/claude/state?'+query(t)),t);
      guard(witness,original);
      if (data.source !== 'host' || data.role !== 'Manager') throw new Error('Host Manager authority missing');
      t={...t,lifecycle:data.lifecycle,capabilities:data.capabilities,liveState:data.liveState};
      if (!writable(t) && data.lifecycle === 'pending') t=await awaitReady(t,witness,original,'Waiting for '+t.title+' to be ready');
      if (!writable(t)) throw new Error(t.reconnectBlocked || 'Manager native input is not ready; draft kept');
      const recoveryNote=retainOriginal(original), messageUuid=crypto.randomUUID();
      if (read(KEYS.send+messageUuid) !== null) throw new Error('Message UUID already has a journal; nothing sent');
      op={schemaVersion:1,target:{...identity(t),source:'host',role:'Manager',uuid:t.uuid,title:t.title},
        messageUuid,original,submitted,createdAt:Date.now(),state:'pending',witness,
        ...(busy(t.liveState) ? {steer:true} : {})};
      saveJournal(op);
      // No await between the durable readback, final input witness and the sole POST.
      guard(witness,original); posted=true;
      const reply=await request('/api/claude/send',{...identity(t),messageUuid,original,submitted,...(op.steer ? {steer:true} : {})});
      phoneApplyReceipt(op,reply);
      if (recoveryNote) notice($('status').textContent+' '+recoveryNote,true);
      if (t.liveState?.settings_unverified && !t.liveState?.settings_mismatch) notice($('status').textContent+' · settings unverified (Send still goes through)');
    } catch (e) {
      if (op) {
        op.state=!posted || REFUSED.has(e.status) ? 'refused' : 'uncertain';
        op.note=(posted ? (op.state === 'refused' ? 'Host refused: ' : 'Reply uncertain; querying same UUID only, never resending: ') : 'Not posted: ')+e.message;
        try { saveJournal(op); } catch (_) { /* Visible draft and any existing journal stay intact. */ }
      }
      notice((op?.note || 'Not sent: '+e.message)+' · draft kept',true);
      if (!posted && RESYNC.has(e.code)) await resync(e.message);
    } finally { state.sending=false; renderState(); }
  }

  // @beacon[
  //   id=opc-pane@phoneOpenCodeSend,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=An OpenCode Manager Send has no watcher and no busy refusal: the exact target is revalidated (witness, root-Manager authority), the full original/submitted text and backend-qualified target are saved and read back by the shared journal before ONE execute request, and busy Managers take the native steer. A lost reply only polls that operation's receipt; a mismatch is a visible conflict, never success; the button stays held until exact evidence or Dan's deliberate release.,
  // ]
  async function openCodeSend() {
    const original=$('draft').value, submitted=original.trim();
    if (!submitted) return notice('Nothing to send.');
    if (!ocApi) return notice('OpenCode Manager support is not loaded on this page; reload it. Draft kept.',true);
    let t=state.target;
    if (backendOf(t) !== 'opencode' || !t.sessionId) return notice('Choose a listed Manager; draft kept.',true);
    restoreJournals();
    const prior=ocPending(t);
    if (prior) { notice('Previous OpenCode Send is unresolved; checking that operation only. Draft kept.',true); await pollOpenCodeReceipts(); return; }
    const revision=state.selectionRevision, page=state.page, draftRevision=state.draftRevision,
      selectionEpoch=state.selectionEpoch, selected=state.selected, witnessBefore=state.target?.witness;
    let savedDraftToken=null;
    try { saveDraft(original); savedDraftToken=JSON.parse(read(KEYS.draft)).revisionToken; } catch (_) { /* journal still retains the full original; no draft clear */ }
    state.sending=true; renderState();
    let record=null, posted=false;
    try {
      // Revalidate immediately before the durable intent: a changed Manager, selection epoch, draft revision or text
      // cancels before anything is stored; a changed creation/directory witness is a changed target, never adopted.
      const data=await ocApi.request('manager?'+new URLSearchParams({id:t.sessionId}));
      if (revision !== state.selectionRevision || state.selected !== selected || state.selectionEpoch !== selectionEpoch)
        throw new Error('Manager changed; message not sent, draft kept');
      if (state.draftRevision !== draftRevision || $('draft').value !== original) throw new Error('Text changed; message not sent, draft kept');
      if (!ocApi.valid(data.target) || data.target.sessionId !== t.sessionId || data.capabilities?.send !== true)
        throw new Error('OpenCode Manager authority missing; message not sent, draft kept');
      if (witnessBefore && data.target.witness !== witnessBefore)
        throw new Error('OpenCode Manager creation/directory witness changed; this is a different target, not sent');
      t={...t,...data,backend:'opencode',sessionId:t.sessionId,target:undefined,witness:data.target.witness,
        busy:typeof data.busy === 'boolean' ? data.busy : null,busyAt:Number.isFinite(data.observedAt) ? data.observedAt*1000 : Date.now(),
        capabilities:data.capabilities,context:data.context || null};
      delete t.target;
      state.target=t; renderState(data);
      rememberOpenCodeTarget(data.target);
      const recoveryNote=retainOriginal(original);
      const local={page:state.page,selectionRevision:revision,draftRevision,selected,
        selectionEpoch,selectionMarker:state.selectionMarker,savedDraftToken,backend:'opencode',sessionId:t.sessionId,witness:t.witness};
      record=await ocApi.begin('send',{target:ocTarget(t),original,submitted,delivery:'steer'},local);
      // No await between the persisted intent's readback and the single execute request.
      if (revision !== state.selectionRevision || state.selected !== selected || state.selectionEpoch !== selectionEpoch ||
          state.draftRevision !== draftRevision || $('draft').value !== original ||
          (witnessBefore && state.target?.witness !== witnessBefore)) {
        ocApi.release(record); // Never posted: the draft and the original are retained, and this is not a Send.
        return notice('Manager or text changed; message not sent, draft kept.',true);
      }
      posted=true;
      const result=await ocApi.execute(record); // one upstream POST; a lost reply is polled, never reposted
      phoneApplyOcReceipt(result);
      if (recoveryNote) notice($('status').textContent+' '+recoveryNote,true);
    } catch (e) {
      const note=(posted ? 'Reply uncertain; checking this operation only, never resending: ' : 'Not sent: ')+e.message;
      if (record) { try { ocApi.save({...record,note,error:e.message,phase:'uncertain'}); } catch (_) {} }
      notice(note+' · draft kept',true);
      if (!posted && RESYNC.has(e.code)) await resync(e.message);
    } finally { state.sending=false; renderState(); renderJournals(); }
  }

  function provider() { const p=setting('refine_provider','anthropic'); return Object.hasOwn(DEFAULT_MODELS,p) ? p : 'anthropic'; }
  function model(p) {
    const saved=setting('refine_'+p+'_model'); if (saved?.trim()) return saved;
    try { const models=JSON.parse(setting('refine_'+p+'_models_list','[]')); if (Array.isArray(models) && typeof models[0] === 'string' && models[0].trim()) return models[0]; } catch (_) {}
    return DEFAULT_MODELS[p];
  }
  function dictionary() {
    const terms=JSON.parse(setting('refine_dictionary','[]'));
    if (!Array.isArray(terms) || terms.some(t=>typeof t !== 'string')) throw new Error('Dictionary must be a JSON array of spellings; edit it in Refine setup');
    return terms;
  }
  function parts() {
    const part=(key,def,required) => { const saved=setting(key); return saved === null || (required && !saved.includes(required)) ? def : saved; };
    return {system:part('refine_system_prompt',REFINE_DEFAULT_SYSTEM_PROMPT),context:part('refine_context_preamble',REFINE_DEFAULT_CONTEXT_PREAMBLE,'{{context}}'),
      transcription:part('refine_transcription_preamble',REFINE_DEFAULT_TRANSCRIPTION_PREAMBLE,'{{transcription}}'),finalfence:part('refine_final_fence',REFINE_DEFAULT_FINAL_FENCE)};
  }
  function showSetup() {
    const p=provider(); $('provider').value=p; $('model').value=model(p); $('key').value=setting('refine_'+p+'_api_key','');
    $('thinking').value=setting('refine_think_level','medium'); $('dictionary').value=setting('refine_dictionary','[]');
    $('no-context').checked=noContext();
  }
  function importSettings() {
    try {
      const data=JSON.parse($('import').value);
      if (!data || Array.isArray(data) || typeof data !== 'object') throw new Error('Paste a settings JSON object');
      let count=0, saved=true;
      for (const [key,value] of Object.entries(data)) if (IMPORTABLE.test(key) && typeof value === 'string') { saved=saveSetting(key,value) && saved; count++; }
      showSetup(); $('import').value=''; notice('Imported '+count+' Refine settings (dictionary included when exported).'+
        (saved ? '' : ' Browser storage failed; some settings are only on this page.'),!saved,'setup-status');
    } catch (e) { notice('Import not applied: '+e.message,true,'setup-status'); }
  }
  // Beneath a completed Refine's line, at xhigh or max: the red reminder of that costly level; the next notice replaces it.
  function remindThinking(level) {
    const text=opcRefineCore.thinkingReminder(level); if (text) $('status').append(element('span',text,'m-think-warn'));
  }
  // The passage Refine cleans: the selection, or the whole box when nothing is selected.
  function refinePassage(box) {
    const original=box.value, usingSelection=box.selectionEnd > box.selectionStart;
    const start=usingSelection ? box.selectionStart : 0, end=usingSelection ? box.selectionEnd : original.length;
    return {original,usingSelection,start,end,transcription:original.slice(start,end)};
  }
  // @beacon[
  //   id=opc-pane@breakPassage,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The After --- button's passage: the text after the last line that is only --- (outside ``` / ~~~ fences; surrounding blank lines left in place) as a selection-like range, so everything above goes as draft-before context. A non-empty selection wins and no --- means the whole box: exactly refinePassage.,
  // ]
  function breakPassage(box) {
    const passage=refinePassage(box), {original}=passage;
    if (passage.usingSelection) return passage;
    let at=-1, offset=0, fence=null;
    for (const line of original.split('\n')) {
      const mark=/^ {0,3}(`{3,}|~{3,})/.exec(line)?.[1];
      if (fence) { if (mark && mark[0] === fence[0] && mark.length >= fence.length && !line.trim().slice(mark.length)) fence=null; }
      else if (mark) fence=mark;
      else if (/^[ \t]*---[ \t]*\r?$/.test(line)) at=Math.min(offset+line.length+1,original.length);
      offset+=line.length+1;
    }
    if (at < 0) return passage;
    const rest=original.slice(at), start=at+rest.length-rest.trimStart().length, end=Math.max(start,original.trimEnd().length);
    return {original,usingSelection:true,sinceBreak:true,start,end,transcription:original.slice(start,end)};
  }
  // After --- only looks unavailable while the box holds a selection (aria-disabled, never disabled): a missed iOS
  // selection event can then never lock it, and its tap keeps the pointerdown guard that preserves the range.
  function markBreakButton() {
    const box=$('draft'), button=$('refine-break');
    button.setAttribute('aria-disabled',String(box.selectionEnd > box.selectionStart));
  }
  // A Manager's main conversation, read and checked as Refine requires it: the whole conversation by default (no byte
  // budget), an explicitly chosen Budgeted history, or nothing at all by explicit no-context choice. The OpenCode read
  // is the guarded server route (whole = paged to exhaustion, never glance text or resident rows).
  function sameManagerTarget(t) {
    if (backendOf(t) === 'opencode')
      return state.selected === t.sessionId && state.target?.sessionId === t.sessionId && state.target?.witness === t.witness;
    return state.selected === t.executionId;
  }
  async function refineContext(t, revision, signal) {
    if (backendOf(t) === 'opencode') {
      if (!ocReady(t)) throw new Error('OpenCode Manager identity is not validated yet; text kept');
      const mode=contextMode(), ctx=await ocApi.context(ocTarget(t),mode,51200,signal);
      if (revision !== state.selectionRevision || !sameManagerTarget(t)) throw new Error('Selected Manager changed; text kept');
      return {text:ctx.text,historyBoundaries:ctx.historyBoundaries,turns:ctx.turns,historyMode:mode,bytes:ctx.bytes,backend:'opencode'};
    }
    const mode=contextMode(), query={targetRef:t.targetRef,targetRevision:revision,historyMode:mode};
    if (mode === 'budgeted') query.budgetBytes=51200;
    const ctx=await request('/api/claude/context?'+new URLSearchParams(query),undefined,{signal});
    if (ctx.status !== 'stored' || ctx.source !== 'host' || ctx.targetRef !== t.targetRef || ctx.executionId !== t.executionId ||
        ctx.generation !== t.generation || ctx.targetRevision !== revision || typeof ctx.text !== 'string' || (ctx.scope && ctx.scope !== 'main') ||
        (ctx.historyMode != null && ctx.historyMode !== mode))
      throw new Error('Selected Manager context mismatch; text kept');
    return ctx;
  }
  // The shared history setting: whole conversation unless Budgeted history was explicitly chosen.
  function contextMode() { return setting('refine_history_mode','whole') === 'budgeted' ? 'budgeted' : 'whole'; }
  // Optional cache-placement hints: the core checks UTF-16 ends and falls back to text-only
  // partitioning if absent/malformed, never blocking Refine or changing conversation prose.
  function historyBoundaries(ctx) {
    return Array.isArray(ctx?.historyBoundaries) ? ctx.historyBoundaries : [];
  }
  // The core formats the provider's own reported cache tokens; a router reports none, so nothing is invented.
  function cacheLine(cache) { return typeof opcRefineCore.cacheSummary === 'function' ? (opcRefineCore.cacheSummary(cache) || '') : ''; }
  // The last successful Refine's cache counters and cost, kept in this browser and shown at the top of Refine setup
  // until the next Refine replaces it (the status line is overwritten by the next Send).
  function showLastRefine() {
    const el=$('last-refine'); if (!el) return;
    let line=''; try { line=read(NS+'last_refine') || ''; } catch (_) {}
    el.textContent=line; el.hidden=!line;
  }
  function recordLastRefine(cache, result) {
    const c=result && typeof result.cost === 'number' ? result.cost*100 : null;
    const cost=c === null ? '' : ' · '+(result.estimated ? '≈' : '')+(c < 1 ? c.toFixed(2) : c.toFixed(1))+'¢';
    const when=new Date().toLocaleTimeString([], {hour:'numeric', minute:'2-digit'});
    try { write(NS+'last_refine','Last Refine ('+when+'): '+(cache || 'no cache counters from this provider')+cost); } catch (_) {}
    showLastRefine();
  }
  showLastRefine();
  // @beacon[
  //   id=opc-pane@phoneRefineRequest,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The one phone Refine request assembly (chat history with its per-block boundaries, draft before/after a selection, dictionary, prompt parts) through opcRefineCore.buildRequest; phoneRefine sends it and the Refine-context View's Full prompt shows it, so the two never drift.,
  // ]
  function refineRequest(passage, history, boundaries=[]) {
    const {original,usingSelection,start,end,transcription}=passage;
    return opcRefineCore.buildRequest({transcription,history,historyBoundaries:history.trim() ? boundaries : [],dictionaryTerms:dictionary(),parts:parts(),
      draftBefore:usingSelection ? original.slice(0,start) : '',draftAfter:usingSelection ? original.slice(end) : ''});
  }
  // @beacon[
  //   id=opc-pane@phoneRefine,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Phone Refine retains full originals and shared provider billing: genuine whole/budgeted Manager prose, or explicit no-context without target validation. Monotonic draft/selection revisions guard complete-output application even for edits away and back; the context View shows the same request. An end-reaching replacement leaves an unhighlighted caret at the box's end and scrolls there without focusing; only a middle selection stays selected with the m-refined cue. mode 'break' (After ---) takes breakPassage instead and also lands at the end, including its preserved trailing blank space; that button shows Cancel while it runs and either button cancels.,
  // ]
  async function phoneRefine(mode) {
    if (state.viewing) return; // Refine reads a Manager's context; a viewed session has no composer
    $('draft').classList.remove('m-refined');
    if (state.refineAbort) { state.refineAbort.abort(); return; }
    const sinceBreak=mode === 'break', button=$(sinceBreak ? 'refine-break' : 'refine'), label=button.textContent;
    const box=$('draft'), passage=sinceBreak ? breakPassage(box) : refinePassage(box), {original,usingSelection,start,end,transcription}=passage, t=state.target, selection=state.selectionRevision,
      draftRevision=state.draftRevision, omit=noContext();
    if (!original.trim()) return notice('Nothing to Refine.');
    if (!transcription.trim()) return notice(passage.sinceBreak ? 'Nothing after the last ---; text kept.' : 'The selection is empty; text kept.');
    if (!omit && !registered(t) && !ocReady(t)) return notice('Choose a validated Manager for Refine; text kept.',true);
    if (backendOf(t) === 'opencode' && !noContext() && !ocReady(t)) return notice('This OpenCode Manager is not validated yet; text kept.',true);
    const p=provider(), apiKey=setting('refine_'+p+'_api_key','').trim();
    if (!apiKey) { setExtra('setup'); $('key').focus(); return notice('Enter or import your Refine key. Reading and Send need no key.'); }
    const backupNote=retainOriginal(original), controller=new AbortController();
    state.refineAbort=controller; button.textContent='Cancel'; button.dataset.busy='true';
    notice((omit ? 'Refining without conversation context (explicit choice)…' : 'Reading this Manager’s context…')+(backupNote ? ' '+backupNote : ''));
    try {
      const ctx=omit ? null : await refineContext(t,selection,controller.signal);
      if (controller.signal.aborted) throw new Error('Refine cancelled');
      if ((!omit && !sameManagerTarget(t)) || selection !== state.selectionRevision || draftRevision !== state.draftRevision || box.value !== original) throw new Error('Manager or text changed; Refine not applied');
      const requestText=refineRequest(passage,ctx ? ctx.text : '',ctx ? historyBoundaries(ctx) : []), thinking=setting('refine_think_level','medium');
      notice((omit ? 'Refining without conversation context' : 'Refining with this Manager’s context and your dictionary')+'…'+(backupNote ? ' '+backupNote : ''));
      const result=await opcRefineCore.callWithRetry({provider:p,model:model(p),apiKey,thinking,
        system:requestText.system,user:requestText.user,anthropicUser:requestText.anthropicUser,routerCache:true,
        cacheKey:omit ? 'no-context' : backendOf(t) === 'opencode' ? 'opencode:'+t.sessionId : 'claude:'+(t.executionId||t.uuid),signal:controller.signal});
      if (controller.signal.aborted) throw new Error('Refine cancelled; text kept');
      if ((!omit && !sameManagerTarget(t)) || selection !== state.selectionRevision || draftRevision !== state.draftRevision || box.value !== original) {
        notice('Refine completed, but the Manager or text changed; your text was kept.'); return remindThinking(thinking);
      }
      const cleaned=result.text.replace(/\s+$/,'');
      box.value=original.slice(0,start)+cleaned+original.slice(end); changedDraft();
      // After --- is the trailing block too: its parser leaves surrounding blank space outside the replacement.
      if (end === original.length || passage.sinceBreak) {
        box.setSelectionRange(box.value.length,box.value.length);
        box.scrollTop=box.scrollHeight;  // no focus(): Refine must not open the keyboard just to show the end
      } else { box.setSelectionRange(start,start+cleaned.length); box.classList.add('m-refined'); }
      const cache=cacheLine(result.cache); // the provider's actual cache reads/writes, never an estimate
      recordLastRefine(cache,result);
      notice((passage.sinceBreak ? 'Refined the part after the last ---.' : usingSelection ? 'Refined selection.' : 'Refined.')+(cache ? ' '+cache : '')+' Full original is in Last original.'+(backupNote ? ' '+backupNote : ''));
      remindThinking(thinking);
    } catch (e) { notice((controller.signal.aborted ? 'Refine cancelled; text kept.' : 'Refine failed: '+e.message+' · text kept; tap Refine to retry.'),!controller.signal.aborted); }
    finally { state.refineAbort=null; button.textContent=label; button.dataset.busy='false'; markBreakButton(); }
  }
  // @beacon[
  //   id=opc-pane@phoneRecheck,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=One fresh get_settings request reads applied model/effort and clears a matching stale host hold, even busy or settings-held; no setters or Send, then ordinary state observation.,
  // ]
  async function phoneRecheck() {
    const t=state.target; if (!writable(t) || state.checking || state.viewing) return;
    state.checking=true; renderState();
    try {
      const data=envelope(await request('/api/claude/control',{...identity(t),requestId:crypto.randomUUID(),operation:'get_settings'}),t), r=data.settingsReadback || {};
      notice(r.applied ? 'Settings re-checked: '+(r.model || 'model unknown')+' · thinking '+(r.effort || 'unknown')+
        (r.blocks_send ? ' · still held: '+(r.gate?.disclosure || r.disclosure || 'host settings hold') : ' · verified, Send is open') :
        'Settings re-check did not verify: '+(r.disclosure || r.state || 'no readback')+' · try again',!r.applied || !!r.blocks_send);
    } catch (e) { notice('Settings re-check failed: '+e.message+' · nothing was changed',true); }
    finally { state.checking=false; await observeState(); renderState(); }
  }

  // Press and hold a Manager chip: that Manager's model and thinking in a bottom sheet, the desktop's
  // Apply model / Apply thinking (app.js controlClaude) at phone size: a fresh state read refuses a
  // busy Manager, the caller requestId is saved and read back before one POST /api/claude/control
  // (set_model / apply_effort), and only the host's applied readback is ever called applied. A lost
  // reply queries that request's receipt, never repeats it. The option lists are index.html's.
  const HOLD_MS=480, HOLD_SLOP=10, CONTROL_POLL_MS=2000, TUNE_READ_MS=2000;
  const CONTROL_SETTLED=new Set(['refused','failed','unverified','mismatch','mismatched','unsupported','superseded']);
  // @beacon[
  //   id=opc-pane@phoneHoldManager,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Press and hold (480 ms, finger still) on a Manager chip opens its model/thinking sheet without selecting it: the chip's following tap is swallowed, a scroll or lift cancels, iOS callout/selection is off in CSS; a right-click or Android long-press contextmenu opens it too.,
  // ]
  function phoneHoldManager(button) {
    let timer=null, start=null;
    const cancel=()=>{ clearTimeout(timer); timer=null; button.dataset.holding='false'; };
    button.addEventListener('pointerdown',event=>{
      button.holdFired=false; cancel();
      if (!event.isPrimary || event.button !== 0) return;
      start={x:event.clientX,y:event.clientY}; button.dataset.holding='true';
      timer=setTimeout(()=>{
        timer=null; button.dataset.holding='false'; button.holdFired=true;
        navigator.vibrate?.(12);
        phoneOpenTune(button.dataset.executionId);
      },HOLD_MS);
    });
    button.addEventListener('pointermove',event=>{
      if (timer && start && Math.hypot(event.clientX-start.x,event.clientY-start.y) > HOLD_SLOP) cancel();
    });
    for (const kind of ['pointerup','pointercancel','pointerleave']) button.addEventListener(kind,()=>{ if (timer) cancel(); });
    button.addEventListener('contextmenu',event=>{
      event.preventDefault();
      if (!timer && !button.holdFired) phoneOpenTune(button.dataset.executionId); // a mouse right-click
    });
  }
  function tuneRow(tune=state.tune) { return tune ? state.managers.find(t=>mid(t) === tune.executionId) || null : null; }
  function ocTuneRow(tune=state.tune) { return tune && tune.backend === 'opencode' ? state.managers.find(t=>mid(t) === tune.executionId) || null : null; }
  function ocModelKey(model) { return model && typeof model === 'object' ? [model.providerID,model.id].filter(Boolean).join('/') : ''; }
  function ocModelById(models, applied) {
    return (models || []).find(m=>m.providerID === applied?.providerID && (m.id === applied?.id)) || null;
  }
  function ocTuneApplied(tune=state.tune) {
    const row=ocTuneRow(tune), data=tune?.data;
    if (!row) return null;
    if (data?.target && data.target.sessionId === mid(row)) return data.model || null;
    return row.model || null;
  }
  // The one model reference the sheet would set now, or null when nothing changed. It always rides the real catalog
  // model object's providerID/id, with the chosen (or applied) variant when that model has one.
  function ocTuneVariant(tune, applied, variants, modelPicked) {
    if (tune.picked.variant) return tune.pick.variant;
    if (modelPicked) return variants.includes(tune.pick.variant) ? tune.pick.variant : variants[0] || null;
    return applied?.variant && variants.includes(applied.variant) ? applied.variant : null;
  }
  function ocTuneChange(tune=state.tune) {
    const applied=ocTuneApplied(tune); if (!tune) return null;
    const modelPicked=!!(tune.pick.model && tune.picked.model);
    const model=modelPicked ? tune.pick.model : ocModelById(tune.models,applied);
    if (!model || !model.providerID || !model.id) return null;
    const variants=Object.keys(model.variants || {});
    const variant=ocTuneVariant(tune,applied,variants,modelPicked);
    const ref={providerID:model.providerID,id:model.id,...(variant && variants.includes(variant) ? {variant} : {})};
    const changed=!applied || applied.providerID !== ref.providerID || applied.id !== ref.id ||
      (applied.variant || null) !== (ref.variant || null);
    return changed ? ref : null;
  }
  // The model/thinking sheet for an OpenCode Manager: the real location-specific catalog (a select; 400+ models is
  // normal) and that model's own thinking variants. It reads the session's actual model for the applied readback and
  // never invents a Claude effort. A setter applies while the Manager is busy (native affects future calls).
  function renderOcTune() {
    const tune=state.tune; if (!tune) return;
    const row=ocTuneRow(tune), applied=ocTuneApplied(tune);
    $('tune-efforts').hidden=true; $('tune-model-box').hidden=true; $('tune-effort-label').hidden=true;
    $('tune-oc').hidden=false;
    text($('tune-for'),row?.title || tune.executionId);
    const line=$('tune-now'), ref=ocTuneChange(tune);
    const verdict=tune.readError || !tune.data ? 'pending' : 'applied';
    text(line,tune.readError ? 'Settings unavailable: '+tune.readError : !tune.data ? 'Reading its model…' :
      (applied ? ocName(applied) : 'Model unknown')+' · '+(verdict === 'applied' ? 'applied' : 'unknown'));
    line.dataset.state=verdict;
    // The catalog select keeps the chosen model selected; option values are the ORIGINAL tune.models indexes, so the
    // display sort never changes which model pickOcModel reads. An applied model the catalog misses still shows.
    const select=$('tune-oc-model');
    const models=(tune.models || []).map((m,i)=>({m,i})).sort((a,b)=>String(a.m.name || a.m.id).localeCompare(String(b.m.name || b.m.id)));
    const signature=models.map(e=>ocModelKey(e.m)).join('|')+'#applied:'+ocModelKey(applied);
    if (select.dataset.signature !== signature) {
      select.replaceChildren();
      for (const {m,i} of models) {
        const option=element('option',(m.name || m.id || m.providerID)+' · '+m.providerID);
        option.value=String(i); option.dataset.key=ocModelKey(m); select.append(option);
      }
      if (!models.length) select.append(element('option','Reading the catalog…'));
      select.dataset.signature=signature;
    }
    const chosen=tune.pick.model || ocModelById(tune.models,applied);
    const chosenOption=models.find(e=>chosen && e.m.providerID === chosen.providerID && e.m.id === chosen.id);
    if (chosenOption) select.value=String(chosenOption.i);
    const variants=Object.keys(chosen?.variants || {});
    const vbox=$('tune-oc-variants');
    const choices=[{value:'',label:'Default'},...variants.map(v=>({value:v,label:v}))];
    const vkey=(chosen ? ocModelKey(chosen) : '')+'#'+choices.map(c=>c.value).join('|')+'#'+(tune.pick.variant || applied?.variant || '');
    if (vbox.dataset.signature !== vkey) {
      vbox.replaceChildren(...choices.map(c=>{ const b=element('button',c.label); b.type='button'; b.setAttribute('role','radio'); b.dataset.value=c.value; return b; }));
      vbox.dataset.signature=vkey;
    }
    const variant=ocTuneVariant(tune,applied,variants,!!(tune.pick.model && tune.picked.model));
    for (const b of vbox.children) {
      b.setAttribute('aria-checked',String(b.dataset.value === (variant || '')));
      b.dataset.current=String(verdict === 'applied' && b.dataset.value === (applied?.variant || ''));
      b.disabled=tune.busy;
    }
    text($('tune-oc-variants-empty'),variants.length ? '' : (chosen ? 'This model has no separate thinking levels; Default is its model setting.' : ''));
    const apply=$('tune-apply');
    text(apply,tune.busy ? 'Applying…' : ref ? 'Apply model · '+ocName(ref) : 'Nothing to apply');
    const block=!row ? 'This Manager is no longer listed.' : '';
    apply.disabled=tune.busy || !!block || !ref;
    notice(block,false,'tune-note');
    notice(tune.status,tune.error,'tune-status');
  }
  async function readOcTune(tune) {
    const row=ocTuneRow(tune); if (!row || !ocApi) return;
    tune.reading=true;
    try {
      const data=await ocApi.request('manager?'+new URLSearchParams({id:mid(row)}));
      if (state.tune !== tune) return;
      if (!ocApi.valid(data.target) || data.target.sessionId !== mid(row)) throw new Error('Manager identity changed');
      tune.data=data; tune.readError=null;
      if (!tune.models.length) {
        const list=await ocApi.request('manager-models?'+new URLSearchParams({id:mid(row)}));
        if (state.tune === tune && Array.isArray(list.models)) tune.models=list.models;
      }
    } catch (e) { if (state.tune === tune) { tune.readError=e.message; tune.data=null; } }
    finally { tune.reading=false; tune.next=Date.now()+TUNE_READ_MS; }
    if (state.tune === tune) renderTune();
  }
  function pickOcModel(index) {
    const tune=state.tune; if (!tune || tune.busy) return;
    const model=(tune.models || [])[Number(index)]; if (!model) return;
    tune.pick.model=model; tune.picked.model=true; tune.picked.variant=false;
    const variants=Object.keys(model.variants || {});
    tune.pick.variant=variants.includes(ocTuneApplied(tune)?.variant) ? ocTuneApplied(tune).variant : variants[0] || null;
    tune.status=''; tune.error=false; renderTune();
  }
  function pickOcVariant(value) {
    const tune=state.tune; if (!tune || tune.busy || value === undefined) return;
    tune.pick.variant=value || null; tune.picked.variant=true; tune.status=''; tune.error=false; renderTune();
  }
  // @beacon[
  //   id=opc-pane@applyOcTune,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The held chip's OpenCode model/variant setter uses its own validated witness, even without selecting that Manager; selection/sheet changes before execute cancel the prepared intent. One journaled native setter works while busy, readback alone reports applied, and lost replies never repeat it.,
  // ]
  async function applyOcTune() {
    const tune=state.tune; if (!tune || tune.busy) return;
    const row=ocTuneRow(tune), ref=ocTuneChange(tune); if (!row || !ref) return;
    const revision=state.selectionRevision, epoch=state.selectionEpoch, selected=state.selected,
      witnessBefore=tune.data?.target?.witness || row.witness || savedOpenCodeTarget(mid(row))?.witness, sid=mid(row);
    tune.busy=true; tune.status='Applying '+ocName(ref)+'…'; tune.error=false; renderTune();
    try {
      const data=await ocApi.request('manager?'+new URLSearchParams({id:sid}));
      if (state.tune !== tune || revision !== state.selectionRevision || epoch !== state.selectionEpoch || selected !== state.selected)
        throw new Error('Manager changed while the model change was being prepared; not submitted');
      if (!ocApi.valid(data.target) || data.target.sessionId !== sid) throw new Error('Manager identity changed');
      if (witnessBefore && data.target.witness !== witnessBefore)
        throw new Error('Manager creation/directory witness changed; not this target');
      tune.data=data;
      const target={backend:'opencode',sessionId:sid,witness:data.target.witness};
      const local={page:state.page,selectionRevision:revision,draftRevision:state.draftRevision,selected,
        selectionEpoch:state.selectionEpoch,backend:'opencode',sessionId:sid,witness:data.target.witness};
      const rec=await ocApi.begin('control',{target,operation:'set_model',model:ref},local);
      if (state.tune !== tune || revision !== state.selectionRevision || epoch !== state.selectionEpoch || state.selected !== selected ||
          (witnessBefore && tune.data?.target?.witness !== data.target.witness)) {
        ocApi.release(rec); // never execute a prepare made for another selection
        return notice('Manager changed before the model setter was sent; not applied. The old intent is retained.',true);
      }
      const out=await ocApi.execute(rec);
      tune.status=ocControlNote(out); tune.error=ocControlError(out);
      if (out.receipt?.state === 'applied') {
        tune.picked.model=false; tune.picked.variant=false;
        if (state.tune === tune) { tune.data={...data,model:out.receipt.readback || ref}; }
      }
      if (ocControlSettled(out)) { try { ocApi.save({...out,handledOutcome:true,note:tune.status}); } catch (_) {} }
      tune.next=0;
    } catch (e) {
      if (state.tune === tune) { tune.status='Not submitted: '+e.message+' · nothing changed.'; tune.error=true; }
      if (RESYNC.has(e.code)) state.nextList=0;
    } finally {
      tune.busy=false; tune.next=0;
      if (state.tune === tune) { renderTune(); void readTune(); }
    }
  }
  function modelName(id) {
    const option=id ? [...$('tune-models').children].find(b=>b.dataset.value === id) : null;
    return option ? option.textContent : id || 'Model unknown';
  }
  // The desktop badge's rule (app.js renderClaudeManager): applied only when the effective readback
  // and the host's control state agree; unverified when the host cannot confirm; else pending.
  function settingsReadback(live, error=false) {
    const a=live?.controls_effective;
    const verified=!error && !!a && typeof a.model === 'string' && !!a.model && typeof a.effort === 'string' && !!a.effort &&
      live.setting_control_state === 'applied' && !live.setting_control_pending && !live.settings_mismatch;
    const unverified=!error && !verified && live?.settings_unverified === true && !live.settings_mismatch;
    return {model:typeof a?.model === 'string' && a.model ? a.model : null, effort:typeof a?.effort === 'string' && a.effort ? a.effort : null,
      verdict:verified ? 'applied' : unverified ? 'unverified' : 'pending'};
  }
  // Interrupt has its own slot so an uncertain settings change never holds an emergency stop or loses its receipt.
  function controlSlot(t, operation) { return t.executionId+(operation === 'interrupt' ? ':interrupt' : ''); }
  function pendingControl(t, operation=null) {
    if (!t) return null;
    const slots=operation === 'interrupt' ? [controlSlot(t,'interrupt')] : [controlSlot(t),controlSlot(t,'interrupt')];
    return slots.map(key=>state.controls.get(key)).find(op=>op && op.target.generation === t.generation && !op.settled && !op.refused) || null;
  }
  function controlLabel(op) { return op.operation === 'interrupt' ? 'Interrupt' : op.operation === 'set_model' ? 'Model '+modelName(op.value) : 'Thinking '+op.value; }
  function controlError(op) { return !!op.refused || (op.settled && !op.receipt?.applied && !(op.operation === 'interrupt' && op.receipt?.state === 'replied')); }
  function controlNote(op) {
    const r=op.receipt, label=controlLabel(op);
    if (op.operation === 'interrupt') {
      if (controlError(op)) return 'Interrupt failed: '+(op.error || r?.disclosure || r?.state || 'refused by the host')+'.';
      if (r?.state === 'replied') return "Interrupted the Manager's turn at "+new Date(op.repliedAt).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',hour12:false})+'. Send it a note about what happened.';
      return 'Interrupt requested · waiting for the host'+(op.error ? ': '+op.error : '')+'; this request is checked, never repeated. Your draft stays.';
    }
    if (op.refused) return label+' not applied: '+(op.error || 'refused by the host')+' · nothing changed.';
    if (r?.applied) return label+' applied and verified'+(r.effective ? ' · readback: '+modelName(r.effective.model)+' · thinking '+(r.effective.effort || 'unknown') : '')+'.';
    if (op.settled) return label+' not applied: '+(r?.state || 'settled')+(r?.disclosure ? ' · '+r.disclosure : '')+'.';
    return label+' requested · waiting for Claude Code’s applied readback'+(op.error ? ' ('+op.error+')' : '')+'; this request is checked, never repeated.';
  }
  function saveControl(op) { const slot=controlSlot(op.target,op.operation); write(KEYS.control+slot,JSON.stringify(op)); state.controls.set(slot,op); }
  function restoreControls() {
    try {
      for (let i=0;i<localStorage.length;i++) {
        const key=localStorage.key(i); if (!key?.startsWith(KEYS.control)) continue;
        try {
          const op=JSON.parse(read(key));
          if (op?.schemaVersion === 1 && registered(op.target) && key === KEYS.control+controlSlot(op.target,op.operation) && UUID.test(op.requestId) &&
              (op.operation === 'interrupt' || (['set_model','apply_effort'].includes(op.operation) && typeof op.value === 'string')))
            state.controls.set(controlSlot(op.target,op.operation),op);
        } catch (_) { /* An unreadable record stays in storage; it never holds a control. */ }
      }
    } catch (_) {}
  }
  // The desktop's receipt rule: a setter awaits applied readback; Interrupt's native replied ends the
  // control request, not background work or proof of turn settlement. Terminal failures settle either.
  function applyControlReceipt(op, r) {
    if (!r) { op.error='receipt not found yet'; saveControl(op); return; }
    if (r.request_id !== op.requestId || r.generation !== op.target.generation) throw new Error('control receipt mismatch');
    const status=String(r.state || r.status || '');
    op.receipt={state:status,applied:r.applied === true,disclosure:typeof r.disclosure === 'string' ? r.disclosure.slice(0,400) : '',
      effective:r.effective && typeof r.effective === 'object' ? {model:typeof r.effective.model === 'string' ? r.effective.model : null,
        effort:typeof r.effective.effort === 'string' ? r.effective.effort : null} : null};
    op.settled=r.applied === true || CONTROL_SETTLED.has(status) || (op.operation === 'interrupt' && status === 'replied'); op.error=null;
    if (op.operation === 'interrupt' && status === 'replied') op.repliedAt=op.repliedAt || Date.now();
    saveControl(op);
  }
  async function pollControls() {
    for (const op of state.controls.values()) {
      if (op.settled || op.refused || state.controlBusy.has(op.requestId) || Date.now() < (op.nextPoll || 0)) continue;
      state.controlBusy.add(op.requestId); op.nextPoll=Date.now()+CONTROL_POLL_MS;
      try {
        const data=envelope(await request('/api/claude/control-receipt?'+query(op.target,{requestId:op.requestId})),op.target);
        applyControlReceipt(op,data.controlReceipt);
        if (op.settled) {
          if (op.operation === 'interrupt') {
            notice((op.target.executionId === state.selected ? '' : (op.target.title || op.target.executionId)+': ')+controlNote(op),controlError(op));
            void Promise.all([observeState(),phoneReadChat()]);
          } else if (state.tune?.executionId === op.target.executionId) {
            const tune=state.tune; tune.status=controlNote(op); tune.error=!op.receipt?.applied; tune.next=0;
            if (op.receipt?.applied) tune.picked[op.operation === 'set_model' ? 'model' : 'effort']=false;
          }
          else notice((op.target.title || op.target.executionId)+': '+controlNote(op),!op.receipt?.applied);
        }
      } catch (e) { op.error='receipt observation failed: '+e.message; }
      finally { state.controlBusy.delete(op.requestId); }
    }
    await pollOcControls();
    if (state.tune) renderTune();
    renderInterrupt();
  }
  // OpenCode Manager controls: the shared client's control entries, one set_model or interrupt operation each.
  // An interrupt has its own record, so an uncertain model change never holds the emergency stop.
  function ocControlsFor(t) { return t && backendOf(t) === 'opencode' ? ocEntries('control').filter(r=>r.intent?.target?.sessionId === mid(t)) : []; }
  function ocControlSettled(rec) {
    if (!rec || rec.releasedAt) return true;
    const r=rec.receipt;
    if (!r) return false;
    if (['failed','conflict','applied'].includes(r.state)) return true;
    if (rec.intent?.operation === 'interrupt' && typeof r.interrupted === 'boolean') return true;
    return false;
  }
  function ocPendingControl(t, operation=null) {
    return ocControlsFor(t).find(rec=>!ocControlSettled(rec) && (!operation || rec.intent?.operation === operation)) || null;
  }
  function ocControlError(rec) {
    if (!rec) return false;
    return ['failed','conflict','uncertain'].includes(rec.receipt?.state);
  }
  function ocControlNote(rec) {
    if (!rec) return '';
    const r=rec.receipt, operation=rec.intent?.operation;
    if (operation === 'interrupt') {
      if (r?.state === 'failed' || r?.state === 'conflict') return 'Interrupt failed: '+(r.detail || 'the pane refused this operation')+'.';
      if (r?.interrupted === true) return 'Native accepted the interrupt at '+new Date((r.repliedAt || Date.now()/1000)*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false})+'; cleanup and tools may still be settling. Send it a note about what happened.';
      if (r?.interrupted === false) return 'Native reports nothing interrupted.';
      return 'Interrupt requested; this operation is checked, never repeated. Your draft stays.';
    }
    if (r?.state === 'applied') return 'Model applied and verified · '+ocName(r.readback || rec.intent?.model)+'. It affects future calls, not a call already running.';
    if (r?.state === 'failed' || r?.state === 'conflict') return 'Model not applied: '+(r.detail || 'the pane refused this operation')+'.';
    if (r?.state === 'uncertain') return 'Model change uncertain; checking this operation only, never repeating it.';
    return 'Model change requested · waiting for the session readback; this operation is checked, never repeated.';
  }
  async function pollOcControls() {
    if (!ocApi) return;
    for (const rec of ocEntries('control')) {
      if (rec.handledOutcome || ocControlSettled(rec) || state.controlBusy.has('oc:'+rec.operationId) ||
          Date.now() < (state.ocControlNext.get(rec.operationId) || 0)) continue;
      state.ocControlNext.set(rec.operationId,Date.now()+CONTROL_POLL_MS);
      state.controlBusy.add('oc:'+rec.operationId);
      try {
        const out=await ocApi.poll(rec);
        if (ocControlSettled(out) && !out.handledOutcome) {
          const note=ocControlNote(out), failure=ocControlError(out);
          if (state.tune?.executionId === out.intent?.target?.sessionId) { state.tune.status=note; state.tune.error=failure; state.tune.next=0; }
          else notice(note,failure);
          ocApi.save({...out,handledOutcome:true,note});
          if (out.intent?.operation === 'interrupt') void Promise.all([observeState(),phoneReadChat()]);
        }
      } catch (e) { /* A missing receipt stays query-only; the record is retained. */ }
      finally { state.controlBusy.delete('oc:'+rec.operationId); }
    }
  }
  function renderInterrupt() {
    const t=state.target, opencode=backendOf(t) === 'opencode';
    const waiting=opencode ? ocPendingControl(t,'interrupt') : pendingControl(t,'interrupt'), working=opencode ? null : busy(t?.liveState);
    // An OpenCode Manager has no idle-only refusal: the native interrupt is available on a validated target.
    $('interrupt').disabled=!!state.viewing || state.interruptSending || !!waiting ||
      (opencode ? !ocReady(t) : !writable(t) || !working);
    text($('interrupt-caption'),state.interruptSending ? 'Requesting Interrupt…' : waiting ? 'Interrupt requested · checking its receipt' :
      opencode ? (!ocReady(t) ? 'Choose a validated OpenCode Manager first' : "Stop the Manager's current turn now") :
      !registered(t) ? 'Choose a Manager first' : !writable(t) ? 'Nothing to interrupt: the Manager is not running' :
      !working ? 'Nothing to interrupt: the Manager is idle' : "Stop the Manager's current turn now");
  }
  // @beacon[
  //   id=opc-pane@phoneInterrupt,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The composer menu's no-confirm emergency Interrupt for a Claude Manager: fresh state and exact selected Manager/generation, one saved native control intent, existing receipt polling (replied is acknowledged, not background settlement), timestamped status and normal refresh. Never reconnects/closes a session or changes the draft; Send stays independent. A selected OpenCode Manager takes phoneInterruptOpenCode instead (one native interrupt, no idle gate).,
  // ]
  async function phoneInterrupt() {
    setMoreMenu(false);
    const t=state.target && {...state.target}, revision=state.selectionRevision;
    if (backendOf(t) === 'opencode') return phoneInterruptOpenCode(t);
    if (state.viewing || !registered(t)) return notice('Choose a Manager to interrupt.');
    if (state.interruptSending) return;
    const previous=pendingControl(t,'interrupt');
    if (previous) { notice(controlNote(previous),controlError(previous)); void pollControls(); return; }
    let op, posted=false;
    state.interruptSending=true; renderInterrupt();
    try {
      const data=envelope(await request('/api/claude/state?'+query(t)),t);
      if (state.viewing || state.selectionRevision !== revision || state.target?.targetRef !== t.targetRef || state.target?.generation !== t.generation)
        throw new Error('selected Manager changed; Interrupt not submitted');
      if (data.source !== 'host' || data.role !== 'Manager') throw new Error('host Manager authority missing');
      state.target={...t,capabilities:data.capabilities,lifecycle:data.lifecycle,liveState:data.liveState,liveStateAt:Date.now()}; renderState(data);
      if (!writable(state.target) || !busy(data.liveState)) return notice('Nothing to interrupt: the Manager is idle.');
      op={schemaVersion:1,target:{...identity(t),source:'host',role:'Manager',uuid:t.uuid,title:t.title},requestId:crypto.randomUUID(),
        operation:'interrupt',createdAt:Date.now(),settled:false};
      saveControl(op);
      posted=true;
      const reply=envelope(await request('/api/claude/control',{...identity(t),requestId:op.requestId,operation:'interrupt',
        reason:'Explicit phone pane operator Interrupt'}),t);
      applyControlReceipt(op,reply.controlReceipt);
      notice(controlNote(op),controlError(op));
    } catch (e) {
      if (op && posted) {
        op.refused=REFUSED.has(e.status); op.error=e.message;
        try { saveControl(op); } catch (_) {}
      }
      notice(op && posted ? controlNote(op) : 'Interrupt not submitted: '+e.message,true);
      if (RESYNC.has(e.code)) state.nextList=0;
    } finally {
      if (op) op.nextPoll=Date.now()+CONTROL_POLL_MS;
      state.interruptSending=false; renderInterrupt();
      void Promise.all([observeState(),phoneReadChat()]);
    }
  }
  // @beacon[
  //   id=opc-pane@phoneInterruptOpenCode,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The composer menu's no-confirm Interrupt for a validated OpenCode Manager: a fresh GET-only authority/witness read, one saved control intent (operation interrupt, no model), ONE native interrupt request and then only receipt queries of that same operation; there is no idle-only gate, no retry and no reconnect, the draft and Send stay independent, and an uncertain reply is never repeated against a later turn.,
  // ]
  async function phoneInterruptOpenCode(t) {
    if (state.interruptSending) return;
    const revision=state.selectionRevision, epoch=state.selectionEpoch, selected=state.selected,
      witnessBefore=state.target?.witness, sid=t.sessionId || mid(t);
    const previous=ocPendingControl(t,'interrupt');
    if (previous) { notice(ocControlNote(previous),ocControlError(previous)); void pollOcControls(); return; }
    state.interruptSending=true; renderInterrupt();
    try {
      if (!ocApi) throw new Error('OpenCode Manager support is not loaded');
      const data=await ocApi.request('manager?'+new URLSearchParams({id:sid}));
      if (state.viewing || revision !== state.selectionRevision || epoch !== state.selectionEpoch || selected !== state.selected || mid(state.target) !== sid)
        throw new Error('selected Manager changed; Interrupt not submitted');
      if (!ocApi.valid(data.target) || data.target.sessionId !== sid) throw new Error('OpenCode Manager authority missing');
      if (witnessBefore && data.target.witness !== witnessBefore)
        throw new Error('OpenCode Manager creation/directory witness changed; Interrupt not submitted');
      state.target={...state.target,...data,backend:'opencode',sessionId:sid,target:undefined,witness:data.target.witness,
        busy:typeof data.busy === 'boolean' ? data.busy : null,busyAt:Number.isFinite(data.observedAt) ? data.observedAt*1000 : Date.now(),
        context:data.context || null};
      delete state.target.target;
      renderState(data);
      const local={page:state.page,selectionRevision:revision,draftRevision:state.draftRevision,selected,
        selectionEpoch:epoch,backend:'opencode',sessionId:sid,witness:data.target.witness};
      const rec=await ocApi.begin('control',{target:{backend:'opencode',sessionId:sid,witness:data.target.witness},operation:'interrupt',model:null},local);
      if (revision !== state.selectionRevision || epoch !== state.selectionEpoch || selected !== state.selected ||
          (witnessBefore && state.target?.witness !== data.target.witness)) {
        ocApi.release(rec);
        return notice('Manager changed before the Interrupt was sent; not submitted.',true);
      }
      const out=await ocApi.execute(rec);
      const note=ocControlNote(out);
      notice(note,ocControlError(out));
      if (ocControlSettled(out)) { try { ocApi.save({...out,handledOutcome:true,note}); } catch (_) {} }
      state.ocControlNext.set(out.operationId,Date.now()+CONTROL_POLL_MS);
    } catch (e) {
      notice('Interrupt not submitted: '+e.message,true);
      if (RESYNC.has(e.code)) state.nextList=0;
    } finally {
      state.interruptSending=false; renderInterrupt();
      void Promise.all([observeState(),phoneReadChat()]);
    }
  }
  function phoneOpenTune(executionId) {
    const row=state.managers.find(t=>mid(t) === executionId); if (!row) return;
    setMoreMenu(false); setNewForm(false); $('draft').blur();
    const backend=backendOf(row);
    if (state.tune?.executionId !== executionId || state.tune?.backend !== backend)
      state.tune={executionId,backend,data:null,error:false,readError:null,
        pick:backend === 'opencode' ? {model:null,variant:null} : {model:null,effort:null},
        picked:backend === 'opencode' ? {model:false,variant:false} : {model:false,effort:false},
        models:[],next:0,reading:false,busy:false,status:''};
    $('tune').hidden=false; document.body.classList.add('m-tune-open');
    renderTune(); void readTune();
  }
  function closeTune() { state.tune=null; $('tune').hidden=true; document.body.classList.remove('m-tune-open'); }
  async function readTune() {
    const tune=state.tune, row=tuneRow(tune);
    if (!tune || !row || tune.reading || Date.now() < tune.next) return;
    if (tune.backend === 'opencode') return readOcTune(tune);
    tune.reading=true;
    try {
      const data=envelope(await request('/api/claude/state?'+query(row)),row);
      if (state.tune === tune) { tune.data=data; tune.readError=null; }
    } catch (e) { if (state.tune === tune) { tune.data=null; tune.readError=e.message; } }
    finally { tune.reading=false; tune.next=Date.now()+TUNE_READ_MS; }
    if (state.tune === tune) renderTune();
  }
  // What Apply would do now, or why it waits.
  function tunePlan(tune=state.tune) {
    const row=tuneRow(tune), data=tune?.data?.targetRef === row?.targetRef ? tune.data : null;
    const live=data ? {...row,lifecycle:data.lifecycle,capabilities:data.capabilities,liveState:data.liveState} : row;
    const now=settingsReadback(live?.liveState,!!tune?.readError);
    const block=!row ? 'This Manager is no longer listed.' :
      tune.busy ? '' :
      ['sleeping','ended'].includes(live.lifecycle) ? 'Sleeping: send it a message to wake it, then change its model or thinking.' :
      live.lifecycle === 'pending' ? 'Still starting: its model and thinking can be changed once it is ready.' :
      !writable(live) ? 'Its controls are not available right now.' :
      pendingControl(row) ? 'The last change is still waiting for its readback; it is checked, never repeated.' :
      state.sending ? 'A Send is in flight; try again in a moment.' :
      busy(live.liveState) ? 'Working on its own turn: try again when the turn finishes.' : '';
    const changes=[];
    // A field Dan touched counts even when it equals an unverified value; untouched fields follow the readback.
    const changed=key=>tune?.pick[key] && (tune.pick[key] !== now[key] || (tune.picked[key] && now.verdict !== 'applied'));
    if (changed('model')) changes.push({operation:'set_model',value:tune.pick.model});
    if (changed('effort')) changes.push({operation:'apply_effort',value:tune.pick.effort});
    return {row,live,now,block,changes};
  }
  function renderTune() {
    const tune=state.tune; if (!tune) return;
    if (tune.backend === 'opencode') return renderOcTune();
    const ocBox=$('tune-oc'); if (ocBox) ocBox.hidden=true;
    $('tune-efforts').hidden=false; $('tune-model-box').hidden=false; $('tune-effort-label').hidden=false;
    // Untouched choices follow the readback first, so the plan below compares the current picks.
    const follow=tunePlan(tune).now;
    for (const key of ['model','effort']) if (!tune.picked[key] && follow[key]) tune.pick[key]=follow[key];
    const {row,now,block,changes}=tunePlan(tune);
    text($('tune-for'),row?.title || tune.executionId);
    const line=$('tune-now');
    text(line,tune.readError ? 'Settings unavailable: '+tune.readError : !tune.data && !row?.liveState ? 'Reading its settings…' :
      (now.model ? modelName(now.model) : 'Model unknown')+' · thinking '+(now.effort || 'unknown')+' · '+
      (now.verdict === 'applied' ? 'applied' : now.verdict === 'unverified' ? '⚠ settings unverified' : 'pending'));
    line.dataset.state=tune.readError ? 'pending' : now.verdict;
    // An applied model this phone does not list still shows as itself (the desktop adds an option too).
    const models=$('tune-models');
    if (now.model && ![...models.children].some(b=>b.dataset.value === now.model)) {
      const extra=element('button',now.model); extra.type='button'; extra.setAttribute('role','radio'); extra.dataset.value=now.model; models.append(extra);
    }
    for (const [group,key] of [[$('tune-efforts'),'effort'],[models,'model']]) for (const b of group.children) {
      b.setAttribute('aria-checked',String(b.dataset.value === tune.pick[key]));
      b.dataset.current=String(now.verdict === 'applied' && b.dataset.value === now[key]);
      b.disabled=tune.busy;
    }
    text($('tune-model-name'),modelName(tune.pick.model));
    const apply=$('tune-apply'), effort=changes.find(c=>c.operation === 'apply_effort'), model=changes.find(c=>c.operation === 'set_model');
    text(apply,tune.busy ? 'Applying…' : model && effort ? 'Apply model, then thinking' : model ? 'Apply model · '+modelName(model.value) :
      effort ? 'Apply thinking · '+effort.value : 'Nothing to apply');
    apply.disabled=tune.busy || !!block || !changes.length;
    notice(block,false,'tune-note');
    notice(tune.status,tune.error,'tune-status');
  }
  function pickTune(key, value) {
    const tune=state.tune; if (!tune || tune.busy || !value) return;
    tune.pick[key]=value; tune.picked[key]=true; tune.status=''; tune.error=false; renderTune();
  }
  // One native control, as the desktop's controlClaude sends it.
  async function phoneControl(tune, operation, value) {
    const t={...tuneRow(tune)};
    if (!registered(t)) throw new Error('this Manager is no longer listed');
    const data=envelope(await request('/api/claude/state?'+query(t)),t);
    if (state.tune !== tune) throw new Error('sheet closed');
    if (data.source !== 'host' || data.role !== 'Manager') throw new Error('host Manager authority missing');
    tune.data=data;
    if (!writable({...t,capabilities:data.capabilities})) throw new Error('its controls are not available ('+(data.lifecycle || 'not ready')+')');
    if (busy(data.liveState)) throw new Error('the Manager is busy with its own turn; try again when it finishes');
    if (pendingControl(t)) throw new Error('the previous change is still pending; it is checked, never repeated');
    const op={schemaVersion:1,target:{...identity(t),source:'host',role:'Manager',uuid:t.uuid,title:t.title},requestId:crypto.randomUUID(),
      operation,value,createdAt:Date.now(),settled:false};
    saveControl(op); // saved and read back before the POST; a storage failure posts nothing
    try {
      const reply=envelope(await request('/api/claude/control',{...identity(t),requestId:op.requestId,operation,
        ...(operation === 'set_model' ? {model:value} : {effort:value})}),t);
      applyControlReceipt(op,reply.controlReceipt);
    } catch (e) {
      op.refused=REFUSED.has(e.status); op.error=e.message;
      try { saveControl(op); } catch (_) {}
    }
    op.nextPoll=Date.now()+CONTROL_POLL_MS;
    return op;
  }
  // @beacon[
  //   id=opc-pane@phoneApplyTune,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The sheet's Apply for a Claude Manager: the changed model and/or thinking as the desktop's set_model / apply_effort controls (model first; thinking only once the model is verified applied), each after a fresh not-busy state read with its requestId saved before the POST; the sheet reports applied only from the host's readback, pending ones are polled by receipt. An OpenCode sheet takes applyOcTune (one catalog model+variant intent, no busy refusal).,
  // ]
  async function phoneApplyTune() {
    const tune=state.tune; if (!tune || tune.busy) return;
    if (tune.backend === 'opencode') return applyOcTune();
    const plan=tunePlan(tune); if (plan.block || !plan.changes.length) return;
    tune.busy=true; tune.status='Applying '+plan.changes.map(c=>c.operation === 'set_model' ? 'model '+modelName(c.value) : 'thinking '+c.value).join(', then ')+'…'; tune.error=false;
    renderTune();
    try {
      let last=null;
      for (const [i,change] of plan.changes.entries()) {
        const key=change.operation === 'set_model' ? 'model' : 'effort';
        // A model switch can move the thinking level with it: skip a thinking change it already made.
        if (last?.receipt?.effective?.[key] === change.value) { tune.picked[key]=false; continue; }
        const op=last=await phoneControl(tune,change.operation,change.value);
        if (state.tune !== tune) return;
        tune.status=controlNote(op); tune.error=!!op.refused || (op.settled && !op.receipt?.applied);
        // An applied choice is no longer a local edit: that field follows the readback again. A pending,
        // refused or unapplied one stays shown as chosen (Apply retries it once nothing is pending).
        if (op.receipt?.applied) tune.picked[key]=false;
        if (!op.receipt?.applied) {
          if (i < plan.changes.length-1 && !op.refused && !op.settled) tune.status+=' Thinking waits until the model shows applied; then tap Apply again.';
          break;
        }
      }
    } catch (e) {
      if (state.tune === tune) { tune.status='Not submitted: '+e.message+' · nothing changed.'; tune.error=true; }
      if (RESYNC.has(e.code)) state.nextList=0;
    } finally {
      tune.busy=false; tune.next=0;
      if (state.tune === tune) { renderTune(); void readTune(); }
    }
  }
  // ⋯ More: the rarely used actions in a small menu above the button row.
  function setMoreMenu(open) {
    if (open) renderInterrupt();
    $('more-menu').hidden=!open; $('more').setAttribute('aria-expanded',String(open));
  }
  // ＋ New Manager: the desktop's New Manager (app.js openNewManager / launchNewManager /
  // adoptLaunchedManager), phone-sized: the same defaults route, the same one launch request and
  // reply checks, the same lost-reply rule. The new Manager is selected in the chip row as soon as
  // the list carries it, before it has any message; the first Send waits for it to be ready.
  const LAUNCH_MS=150000, LAUNCH_WATCH_MS=10*60*1000;
  const ocLaunch={models:[],agents:[],agent:'manager',mcp:null,loaded:false,reading:false,error:null};
  // A launch's status line: New Manager's, or ↳'s while its box is the one open (both launch through here).
  function newStatus(value, error=false) { notice(value,error,$('new-form').hidden && $('sub-form') && !$('sub-form').hidden ? 'sub-status' : 'new-status'); }
  // ＋ New Manager and ↳ share ONE set of runtime/model/thinking controls (#m-new-choices): it moves into the box
  // being opened, so a sub-session offers exactly New Manager's choices. One box is open at a time.
  function placeChoices(slot) {
    const choices=$('new-choices'), home=$(slot);
    if (choices && home && choices.parentNode !== home) home.append(choices);
  }
  function newRuntime() { return $('new-runtime')?.value === 'opencode' ? 'opencode' : 'claude'; }
  function ocLaunchModel() {
    const raw=$('new-oc-model')?.value, i=Number(raw);
    return raw !== '' && raw != null && Number.isInteger(i) ? ocLaunch.models[i] || null : null;
  }
  function newRef() {
    const model=ocLaunchModel(); if (!model) return null;
    const variant=$('new-oc-variant')?.value || '';
    return {providerID:model.providerID,id:model.id,...(variant ? {variant} : {})};
  }
  function newChoice() {
    if (newRuntime() === 'opencode') {
      text($('new-choice'), ($('new-agent')?.value || ocLaunch.agent || 'manager')+' · '+(newRef() ? ocName(newRef()) : 'choose a model'));
      return;
    }
    text($('new-choice'),($('new-model').value.trim() || 'claude-opus-5-5')+' · '+($('new-effort').value || 'xhigh'));
  }
  // Runtime choice: the native Claude form fields or the real OpenCode catalog/persona fields, one at a time.
  function showRuntimePanels() {
    const open=newRuntime() === 'opencode';
    $('new-claude-fields').hidden=open; $('new-oc-fields').hidden=!open;
    if (open && !ocLaunch.loaded && !ocLaunch.reading) void loadOcLaunchDefaults();
    if (state.launch) renderNewManager();
    newChoice();
  }
  function fillOcLaunchVariants() {
    const select=$('new-oc-variant'), model=ocLaunchModel(), variants=model ? Object.keys(model.variants || {}) : [], previous=select.value;
    select.replaceChildren(element('option','model default'));
    select.firstChild.value='';
    for (const v of variants) { const option=element('option',v); option.value=v; select.append(option); }
    select.value=variants.includes(previous) ? previous : variants.includes('medium') ? 'medium' : variants[0] || '';
    newChoice();
  }
  async function loadOcLaunchDefaults() {
    if (!ocApi || ocLaunch.reading || ocLaunch.loaded) return;
    ocLaunch.reading=true;
    try {
      const data=await ocApi.request('launch-defaults');
      ocLaunch.models=Array.isArray(data.models) ? data.models.filter(m=>m && m.providerID && m.id) : [];
      ocLaunch.agents=Array.isArray(data.agents) ? data.agents.filter(a=>typeof a === 'string') : [];
      ocLaunch.agent=typeof data.agent === 'string' ? data.agent : 'manager';
      ocLaunch.mcp=Array.isArray(data.mcp) ? data.mcp : data.mcp || null;
      ocLaunch.loaded=true; ocLaunch.error=null;
      const agent=$('new-agent'), previousAgent=agent.value;
      agent.replaceChildren();
      for (const name of (ocLaunch.agents.length ? ocLaunch.agents : ['manager','manager-gpt'])) { const option=element('option',name); option.value=name; agent.append(option); }
      agent.value=ocLaunch.agents.includes(previousAgent) ? previousAgent : ocLaunch.agent;
      const model=$('new-oc-model');
      model.replaceChildren();
      for (const [i,m] of ocLaunch.models.entries()) {
        const option=element('option',(m.name || m.id)+(m.providerID ? ' · '+m.providerID : ''));
        option.value=String(i); model.append(option);
      }
      if (!ocLaunch.models.length) { const option=element('option','No catalog model available'); option.value=''; model.append(option); }
      fillOcLaunchVariants();
      const mcp=$('new-mcp');
      text(mcp,ocLaunch.mcp === null ? 'MCP state was not reported for this workspace; it is information only.' :
        ocLaunch.mcp.length ? 'MCP: '+ocLaunch.mcp.map(r=>r.name+(r.status ? ' ('+r.status+')' : '')).join(', ')+' · tools connect on its first turn in this folder.' :
        'No MCP servers are configured for this workspace.');
    } catch (e) {
      ocLaunch.error=e.message;
      newChoice();
      if (newRuntime() === 'opencode') newStatus('OpenCode launch defaults unavailable: '+e.message+' · type and Start remain yours; nothing is sent.',true);
    } finally { ocLaunch.reading=false; }
  }
  function setNewForm(open) { $('new-form').hidden=!open; $('new').setAttribute('aria-expanded',String(open)); }
  function renderNewManager() {
    const launching=state.launch?.phase === 'launching' &&
      (state.launch.runtime === 'opencode' ? newRuntime() === 'opencode' : newRuntime() === 'claude');
    // Native Claude keeps its original rule: only an in-flight native launch disables Start. An unresolved OC launch
    // holds Start only while the OpenCode runtime is the one shown; switching to Claude is never held by it.
    const ocHeld=state.launch?.runtime === 'opencode' && newRuntime() === 'opencode';
    $('new-start').disabled=launching || ocHeld; text($('new-start'),launching ? 'Starting…' : 'Start');
    $('new').dataset.busy=String(!!state.launch);
    if ($('sub-send')) { $('sub-send').disabled=state.launch?.phase === 'launching'; text($('sub-send'),state.launch?.phase === 'launching' ? 'Starting…' : 'Send'); }
  }
  async function phoneOpenNewManager() {
    setMoreMenu(false); setSubForm(false); placeChoices('new-choices-slot'); setNewForm(true); showRuntimePanels();
    void loadFirstMessages(true).catch(e=>newStatus('Default first messages unavailable ('+e.message+'); a launch now sends none.',true));
    if (state.launch) return; // its own status line is already saying how the launch goes
    newStatus('A fresh conversation with today’s Manager prompt; its first message goes out when it is ready.'+(state.target ? ' '+state.target.title+' keeps running untouched.' : ''));
    try {
      const d=await request('/api/claude/launch-defaults');
      if (!$('new-name').value) $('new-name').value=typeof d.name === 'string' ? d.name : '';
      if (typeof d.model === 'string' && !$('new-model').dataset.chosen) $('new-model').value=d.model;
      if (typeof d.effort === 'string' && !$('new-effort').dataset.chosen) $('new-effort').value=d.effort;
    } catch (e) { if (!$('new-name').value) newStatus('Suggested name unavailable ('+e.message+'); type one, e.g. Manager-Session-183.',true); }
    newChoice();
  }
  // @beacon[
  //   id=opc-pane@phoneLaunchManager,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Phone ＋ New Manager Start: the desktop's exact POST /api/claude/launch-manager with a browser-allocated UUID (a repeat or lost reply answers the first launch, never a second conversation) and its reply checks, Opus 5.5 / xhigh unless chosen; the current Manager and the draft are untouched, and the list then adopts the new Manager and sends its first message (the saved default, or ↳'s text via given = {name, opening}).,
  // ]
  async function phoneLaunchManager(given={}) {
    if (state.launch && state.launch.runtime !== 'opencode' && state.launch.phase === 'launching') return false;
    if (newRuntime() === 'opencode') return phoneLaunchOpenCode(given);
    // Free entry: any model the host's own launch rule accepts (a new release needs no phone change).
    const name=String(given.name ?? $('new-name').value).trim(), model=$('new-model').value.trim() || 'claude-opus-5-5', effort=$('new-effort').value || 'xhigh';
    if (!name) { newStatus('Type a name first, e.g. Manager-Session-183.',true); return false; }
    $('new-name').blur();
    const launch={uuid:crypto.randomUUID(),name,model,effort,phase:'launching',started:Date.now(),selectionRevision:state.selectionRevision};
    state.launch=launch; renderNewManager();
    const opening=given.opening ?? await newOpening(name);
    if (opening) launch.opening=opening;
    newStatus('Starting '+name+' …'+(state.target ? ' '+state.target.title+' keeps running untouched.' : ''));
    try {
      const data=await request('/api/claude/launch-manager',{schemaVersion:1,backend:'claude',nativeUuid:launch.uuid,name,model,effort},{budget:LAUNCH_MS});
      if (data.uuid !== launch.uuid || data.executionId !== 'cc-'+launch.uuid) throw new Error('the launch answered a different session; nothing selected');
      launch.phase='waiting';
      newStatus(name+' started'+(data.hostStarted ? ' (the Claude host was not running, so it was started first)' : '')+' · selecting it…');
    } catch (e) {
      if (e.status === 502 || e.name === 'AbortError') {
        // The request may be with the host already; the browser UUID makes a repeat harmless.
        launch.phase='waiting';
        newStatus('No reply yet ('+e.message+'). If the launch went through, '+name+' appears in the list and is selected automatically.');
      } else {
        launch.phase='failed';
        newStatus('New Manager not started: '+e.message+' · nothing changed; your draft and current Manager are kept.',true);
      }
    }
    if (launch.phase === 'failed' && state.launch === launch) state.launch=null;
    renderNewManager();
    if (launch.phase === 'waiting') { state.nextList=0; await phoneLoadManagers(); }
    return launch.phase === 'waiting';
  }
  // @beacon[
  //   id=opc-pane@phoneLaunchOpenCodeManager,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=＋ New Manager on OpenCode: the chosen real-catalog model + variant and installed persona, with the workspace's MCP state as information only. The caller create id and exact intent are persisted by the shared journal before one execute request; a lost reply or reload polls that same native ses_ id (never a second create), the form closes only on the created receipt, and the new Manager is selected and sent the launch's first message once (phoneSendOpeningOpenCode). The current Manager and draft stay untouched.,
  // ]
  async function phoneLaunchOpenCode(given={}) {
    // A native launch in flight owns the one launch slot; an OpenCode launch owns it only while still launching. A
    // Claude runtime is free to start even if an OpenCode launch is unresolved (it is never held by that).
    if (state.launch && state.launch.runtime !== 'opencode') return false;
    if (state.launch?.runtime === 'opencode') { if (state.launch.phase !== 'launching') await adoptOcLaunched(); return false; }
    if (!ocApi) { newStatus('OpenCode Manager support is not loaded on this page; reload it. Nothing was started.',true); return false; }
    const name=String(given.name ?? $('new-name').value).trim(), agent=$('new-agent').value || 'manager', ref=newRef();
    if (!name) { newStatus('Type a name first, e.g. Manager-Session-183.',true); return false; }
    if (!ref) { newStatus('Choose an available model from the catalog first; nothing is started.',true); return false; }
    $('new-name').blur();
    const launch={runtime:'opencode',name,agent,model:ref,phase:'launching',started:Date.now(),
      selectionRevision:state.selectionRevision,selectionEpoch:state.selectionEpoch,selectionMarker:state.selectionMarker,selected:state.selected,page:state.page,
      operationId:null,nativeId:null,record:null,polling:false};
    state.launch=launch; renderNewManager();
    const opening=given.opening ?? await newOpening(name);
    if (opening) launch.opening=opening;
    newStatus('Starting '+name+' · OpenCode · '+agent+' · '+ocName(ref)+'.'+(opening ? ' Its first message goes out once it is selected.' : ' No opening turn is sent.')+(state.target ? ' '+state.target.title+' keeps running untouched.' : ''));
    let pendingLaunch=null;
    try {
      const record=await ocApi.begin('launch',{name,agent,model:ref},{
        page:state.page,selectionRevision:state.selectionRevision,draftRevision:state.draftRevision,selected:state.selected,
        selectionEpoch:state.selectionEpoch,selectionMarker:state.selectionMarker,backend:'opencode',sessionId:null,...(opening ? {opening} : {})});
      pendingLaunch={...launch,operationId:record.operationId,nativeId:record.nativeId,record,phase:'waiting'};
      if (state.launch === launch) state.launch=pendingLaunch;
      renderNewManager();
      const executed=await ocApi.execute(record);
      if (state.launch !== pendingLaunch) {
        ocApi.save({...executed,handledLaunch:true,note:'New Manager intent retained; a newer launch owns selection.'}); return;
      }
      state.launch={...state.launch,record:executed}; renderNewManager();
      if (executed.receipt?.state === 'created') { await adoptOpenCodeLaunch(executed); return true; }
      if (['failed','conflict'].includes(executed.receipt?.state)) {
        state.launch=null; renderNewManager();
        newStatus('New Manager not started: '+(executed.receipt?.detail || executed.receipt?.state)+' · nothing changed; your draft and current Manager are kept.',true);
        return false;
      }
      newStatus('Launch intent saved; the same operation is checked for its created receipt — never a second conversation.');
      return true;
    } catch (e) {
      if (state.launch === launch || state.launch === pendingLaunch) state.launch=null; renderNewManager();
      notice('New Manager not started: '+e.message+' · saved intents are query-only; your draft and current Manager are kept.',true);
      return false;
    }
  }
  async function adoptOpenCodeLaunch(record) {
    if (state.launch?.operationId && record?.operationId && state.launch.operationId !== record.operationId) return;
    const launch=state.launch || {}, sid=record?.nativeId, target=record?.receipt?.target;
    if (!ocApi.valid(target) || target.sessionId !== sid) return notice('The launch answered a session whose exact identity did not verify; nothing selected. This intent is checked, never created again.',true);
    // Another tab's persisted selection epoch/target is authority too: never adopt into a selection this tab has not
    // seen (the same page can have two open phone tabs).
    const storedEpoch=Math.max(0,parseInt(read(KEYS.selectionEpoch) || '0',10) || 0), storedTarget=read(KEYS.target);
    if (storedEpoch !== state.selectionEpoch || (storedTarget && storedTarget !== state.selected) ||
        (launch.selectionMarker && (launch.selectionMarker !== state.selectionMarker || read(KEYS.selectionMarker) !== launch.selectionMarker))) {
      state.launch=null; renderNewManager();
      try { ocApi.save({...record,handledLaunch:true,note:'Another tab changed the selection; not adopted.'}); } catch (_) {}
      return notice(launch.name+' started ('+sid+'); it was not selected because another tab changed the selection.');
    }
    const chosen=state.selected, revision=state.selectionRevision, epoch=state.selectionEpoch;
    const sameTarget=launch.selected === chosen;
    const sameRevision=launch.selectionRevision == null || launch.selectionRevision === revision;
    const sameEpoch=!Number.isSafeInteger(launch.selectionEpoch) || launch.selectionEpoch === epoch;
    state.launch=null; renderNewManager(); $('new-name').value='';
    if (!sameTarget || !sameRevision || !sameEpoch) {
      try { ocApi.save({...record,handledLaunch:true,note:'Another Manager/selection was chosen; not adopted.'}); } catch (_) {}
      return notice(launch.name+' started ('+sid+'); it was not selected because you chose another Manager.');
    }
    try { ocApi.save({...record,handledLaunch:true,note:'Selected after an exact created receipt'}); } catch (_) {}
    let row=await revalidateOpenCodeManager(sid);
    if (!row) row={id:sid,backend:'opencode',sessionId:sid,kind:'manager',manager:true,role:'Manager',title:launch.name,
      agent:launch.agent,model:launch.model,witness:target.witness,busy:false};
    if (state.selectionEpoch !== epoch || state.selectionRevision !== revision || state.selected !== chosen ||
        (launch.selectionMarker && read(KEYS.selectionMarker) !== launch.selectionMarker)) {
      try { ocApi.save({...record,handledLaunch:true,note:'Selection changed during identity revalidation; not adopted.'}); } catch (_) {}
      return notice(launch.name+' started ('+sid+'); it was not selected because the Manager choice changed during validation.');
    }
    if (!state.managers.some(t=>mid(t) === sid)) state.managers=[...state.managers,row].sort(managerOrder);
    newStatus(''); setNewForm(false); renderList();
    await selectManager(sid);
    const opening=typeof record.opening === 'string' && record.opening.trim() ? record.opening : null;
    notice(launch.name+' is selected · '+(opening ? 'sending its first message…' : 'no opening turn was sent.'));
    if (opening) void phoneSendOpeningOpenCode(sid,opening);
  }
  async function adoptOcLaunched() {
    const launch=state.launch;
    if (!launch || launch.runtime !== 'opencode' || launch.phase === 'launching' || launch.polling) return;
    launch.polling=true;
    try {
      const record=launch.record || ocEntries('launch').find(r=>r.operationId === launch.operationId);
      if (!record) return;
      const out=await ocApi.poll(record);
      launch.record=out;
      if (out.receipt?.state === 'created') await adoptOpenCodeLaunch(out);
      else if (['failed','conflict'].includes(out.receipt?.state)) {
        state.launch=null; renderNewManager();
        newStatus('New Manager not started: '+(out.receipt?.detail || out.receipt?.state)+' · nothing changed.',true);
      }
    } finally { launch.polling=false; }
  }
  // @beacon[
  //   id=opc-pane@restoreOpenCodeLaunch,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=A launch persisted before a reload is adopted by polling its same native ses_ id only when the user did not choose another Manager since; an unresolved intent is never re-created, never pointed at a different session and its original record is retained.,
  // ]
  // A launch persisted before a reload: poll/adopt the same native ses_ id only when the user did not choose another
  // Manager since; an unresolved intent is never re-created and never URLs a different session.
  function restoreOpenCodeLaunch() {
    if (!ocApi) return;
    try {
      const records=ocApi.entries('launch').filter(r=>!r.handledLaunch && !['failed','conflict'].includes(r.receipt?.state));
      const rec=records.at(-1); if (!rec) return;
      const sameEpoch=(!Number.isSafeInteger(rec.selectionEpoch) || rec.selectionEpoch === state.selectionEpoch) &&
        (!rec.selectionMarker || rec.selectionMarker === state.selectionMarker);
      if (!sameEpoch || (rec.selected ?? null) !== (state.selected ?? null)) {
        ocApi.save({...rec,handledLaunch:true,note:'Another Manager/selection was chosen; this launch was not adopted after reload.'});
        return;
      }
      state.launch={runtime:'opencode',name:rec.intent?.name,agent:rec.intent?.agent,model:rec.intent?.model,phase:'waiting',
        started:rec.createdAt,selectionRevision:null,selectionEpoch:rec.selectionEpoch ?? null,selectionMarker:rec.selectionMarker ?? null,selected:rec.selected ?? null,page:rec.page,
        operationId:rec.operationId,nativeId:rec.nativeId,record:rec,polling:false};
    } catch (_) { /* renderJournals shows an unreadable record */ }
  }

  // @beacon[
  //   id=opc-pane@phoneAdoptLaunched,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Selects a ＋ New Manager launch in the chip row the moment the Manager list carries it, before it has any message, and hands its first message to phoneSendOpening (which waits for it to be ready); choosing another Manager meanwhile wins and sends nothing, and a launch that ended or never appears says so.,
  // ]
  function adoptLaunched(rows) {
    const launch=state.launch, row=rows.get('cc-'+launch.uuid);
    if (!row) {
      if (Date.now()-launch.started > LAUNCH_WATCH_MS) {
        state.launch=null; renderNewManager();
        newStatus(launch.name+' did not appear in the Manager list; check: bash scripts/claude_host.sh call list',true);
      }
      return;
    }
    state.launch=null; renderNewManager(); $('new-name').value='';
    if (row.lifecycle === 'ended' || ['launch_failed','launch_blocked','exited'].includes(row.state))
      return newStatus(launch.name+' did not start ('+String(row.state)+'). The host’s incidents for it say why; your draft and Manager are unchanged.',true);
    if (state.selectionRevision !== launch.selectionRevision)
      return newStatus(launch.name+' is in the Manager list; it was not selected because you chose another Manager'+(launch.opening ? ', and its first message was not sent.' : '.'));
    newStatus(''); setNewForm(false);
    void selectManager(row.executionId);
    notice(launch.name+' is selected'+(launch.opening ? ' · its first message goes out as soon as it is ready.' :
      writable(row) ? ' and ready for your first message.' : ' · starting up; your first Send waits until it is ready.'));
    if (launch.opening) void phoneSendOpening(row.executionId,launch.opening);
  }
  // Dan's default first messages (GET/POST /api/claude/first-messages): one saved copy for the phone and the desktop,
  // read fresh for every launch (the last good copy if that read fails); fill puts them in New Manager's editor.
  async function loadFirstMessages(fill=false) {
    let data;
    try { data=await request('/api/claude/first-messages'); }
    catch (e) { if (state.firstMessages && !fill) return state.firstMessages; throw e; }
    if (typeof data.manager !== 'string' || typeof data.subSession !== 'string') throw new Error('first-message reply schema invalid');
    state.firstMessages=data;
    if (fill && $('first-manager')) { $('first-manager').value=data.manager; $('first-sub').value=data.subSession; }
    return data;
  }
  async function saveFirstMessages(restore) {
    try {
      const body=restore ? {manager:'',subSession:''} : {manager:$('first-manager').value,subSession:$('first-sub').value};
      const data=await request('/api/claude/first-messages',{schemaVersion:1,backend:'claude',...body});
      if (typeof data.manager !== 'string' || typeof data.subSession !== 'string') throw new Error('first-message reply schema invalid');
      state.firstMessages=data; $('first-manager').value=data.manager; $('first-sub').value=data.subSession;
      newStatus(restore ? 'Default first messages restored.' : 'Default first messages saved, for the phone and the desktop.');
    } catch (e) { newStatus('Default first messages not saved: '+e.message,true); }
  }
  // A ＋ New Manager launch's first message: the saved default with its number filled in, unless unticked.
  // Unavailable: the launch goes ahead without one, and says so.
  async function newOpening(name) {
    if ($('new-opening')?.checked === false) return null;
    try {
      const order=globalThis.opcManagerOrder; if (!order?.opening) throw new Error('manager-order.js is not loaded');
      const text=order.opening((await loadFirstMessages()).manager,name);
      return text.trim() ? text : null;
    } catch (e) { notice('First message unavailable ('+e.message+'); '+name+' starts without one.',true); return null; }
  }
  function setSubForm(open) { if (!$('sub-form')) return; $('sub-form').hidden=!open; $('new-sub').setAttribute('aria-expanded',String(open)); }
  function subStatus(value, error=false) { notice(value,error,'sub-status'); }
  // @beacon[
  //   id=opc-pane@phoneOpenNewSub,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Phone ↳ New sub-session: names the next sub-session of the newest primary among the chips (manager-order.js nextSub; with the Claude read failing and no Claude chip, also the Claude names this phone last saw) and opens its first-message box, the saved default with number and letter filled in and room for the reason, under ＋ New Manager's own runtime/model/thinking controls (moved into the box; default Claude, so a crisis sub-session can be OpenCode/GPT with the Claude host down); Send starts it through New Manager's launch with that text as its first message.,
  // ]
  async function phoneOpenNewSub() {
    setMoreMenu(false); setNewForm(false); placeChoices('sub-choices-slot'); setSubForm(true); subStatus(''); showRuntimePanels();
    // Claude host down and no Claude chip listed: number from the Claude Managers this phone last saw as well.
    const remembered=state.claudeListError && !state.managers.some(t=>backendOf(t) === 'claude') ? rememberedClaudeNames() : [];
    const order=globalThis.opcManagerOrder, names=state.managers.map(managerNames).concat(remembered), next=order?.nextSub?.(names), form=$('sub-form');
    if (!next) {
      text($('sub-title'),'New sub-session'); form.dataset.name='';
      return subStatus(names.some(n=>order?.parse?.(...n)) ? 'The newest Manager-Session has no letter left after z.' :
        'No numbered Manager-Session in the list yet: nothing to start a sub-session of.',true);
    }
    text($('sub-title'),'Sub-session '+next.id+' of '+next.parent);
    const box=$('sub-text');
    if (form.dataset.name !== next.name || !box.value.trim()) {
      try { box.value=order.opening((await loadFirstMessages()).subSession,next.name); }
      catch (e) { return subStatus('Sub-session first message unavailable: '+e.message,true); }
    }
    form.dataset.name=next.name;
    // An OpenCode catalog failure already said so here (newStatus writes this box while it is open): keep it in view.
    if ($('sub-status').dataset.error !== 'true')
      subStatus((remembered.length ? 'Claude Managers are unavailable, so the number also counts the ones this phone last saw. ' : '')+
        'Add the reason at the end, choose the runtime, model and thinking, then Send: it starts '+next.name+' and sends this as its first message.');
    box.focus(); try { box.setSelectionRange(box.value.length,box.value.length); } catch (_) { /* focus is enough */ }
  }
  async function phoneLaunchNewSub() {
    const form=$('sub-form'), name=form.dataset.name, opening=$('sub-text').value;
    if (!name) return subStatus('Open ↳ again: no sub-session is named yet.',true);
    if (!opening.trim()) return subStatus('The first message is empty; nothing was started.',true);
    $('sub-text').blur(); subStatus('');
    if (await phoneLaunchManager({name,opening})) { setSubForm(false); $('sub-text').value=''; form.dataset.name=''; }
    // The launch's own refusal is already in this box (newStatus); one said elsewhere (the page's status) is copied in.
    else if ($('sub-status').dataset.error !== 'true')
      subStatus('Not started.'+($('status').dataset.error === 'true' && $('status').textContent ? ' '+$('status').textContent : ''),true);
  }
  // A first message that could not go out: in an empty draft for a deliberate Send; a draft of Dan's is never touched.
  function openingNotSent(id, text, why) {
    const box=$('draft'), placed=state.selected === id && !box.value.trim();
    if (placed) { box.value=text; changedDraft(); }
    notice('First message not sent: '+why+(placed ? ' · it is in the text box: tap Send' : ' · your draft is kept (the first message is not in it)'),true);
  }
  // @beacon[
  //   id=opc-pane@phoneSendOpening,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The first message of a phone ＋ New Manager / ↳ launch, never the draft: waits for the new Claude Manager's native input (every 2 s, up to the launch watch), then one journaled host Send as phoneSend journals one; its ACK clears nothing. Choosing another Manager first cancels it; a refusal hands the text to an empty draft.,
  // ]
  async function phoneSendOpening(executionId, text) {
    const started=Date.now();
    for (;;) {
      if (state.selected !== executionId) return openingNotSent(executionId,text,'you chose another Manager first');
      let t=state.target;
      if (registered(t) && t.executionId === executionId && !state.sending) {
        try {
          const data=envelope(await request('/api/claude/state?'+query(t)),t);
          if (state.selected === executionId && !state.sending) {
            t={...t,lifecycle:data.lifecycle,capabilities:data.capabilities,liveState:data.liveState};
            if (writable(t)) { state.target=t; renderState(data); return postOpening(t,text); }
          }
        } catch (e) { if (REFUSED.has(e.status)) return openingNotSent(executionId,text,e.message); }
      }
      if (Date.now()-started > LAUNCH_WATCH_MS) return openingNotSent(executionId,text,'it was not ready after ten minutes');
      await delay(2000);
    }
  }
  async function postOpening(t, text) {
    restoreJournals();
    if (pending(t)) return openingNotSent(t.executionId,text,'an earlier Send to it is unresolved');
    state.sending=true; renderState();
    let op, posted=false;
    try {
      const messageUuid=crypto.randomUUID();
      op={schemaVersion:1,target:{...identity(t),source:'host',role:'Manager',uuid:t.uuid,title:t.title},messageUuid,original:text,submitted:text.trim(),
        createdAt:Date.now(),state:'pending',witness:frozenIntent(),opening:true,handledAck:true};
      saveJournal(op); posted=true;
      phoneApplyReceipt(op,await request('/api/claude/send',{...identity(t),messageUuid,original:op.original,submitted:op.submitted}));
      notice('First message to '+t.title+': '+(state.journals.get(messageUuid)?.note || 'sent'));
    } catch (e) {
      if (op) {
        op.state=!posted || REFUSED.has(e.status) ? 'refused' : 'uncertain';
        op.note=(op.state === 'refused' ? 'Host refused the first message: ' : 'Reply uncertain; querying same UUID only, never resending: ')+e.message;
        try { saveJournal(op); } catch (_) { /* the notice below says what happened */ }
      }
      if (!op || op.state === 'refused') openingNotSent(t.executionId,text,e.message);
      else notice('First message uncertain: '+e.message+' · querying its receipt only, never resent',true);
    } finally { state.sending=false; renderState(); renderJournals(); }
  }
  // The same for a new OpenCode Manager, once it is the validated selected target: one journaled intent, executed once.
  async function phoneSendOpeningOpenCode(sid, text) {
    const started=Date.now();
    while (state.selected === sid && !(ocReady(state.target) && state.target.sessionId === sid && !state.sending)) {
      if (Date.now()-started > 60000) break;
      await delay(1000);
    }
    const t=state.target;
    if (state.selected !== sid || !ocReady(t) || t.sessionId !== sid || state.sending)
      return openingNotSent(sid,text,'the new Manager was not the free Send target');
    state.sending=true; renderState();
    try {
      const record=await ocApi.begin('send',{target:ocTarget(t),original:text,submitted:text.trim(),delivery:'steer'},{page:state.page,
        selectionRevision:state.selectionRevision,draftRevision:state.draftRevision,selected:sid,selectionEpoch:state.selectionEpoch,
        selectionMarker:state.selectionMarker,backend:'opencode',sessionId:sid,witness:t.witness,opening:true,handledAck:true});
      const result=await ocApi.execute(record);
      phoneApplyOcReceipt(result);
      notice('First message '+(ocApi.acknowledged(result) ? 'delivered' : (result.receipt?.state || 'uncertain')+' · checking this operation only, never resent'));
    } catch (e) { openingNotSent(sid,text,e.message); }
    finally { state.sending=false; renderState(); renderJournals(); }
  }
  function closeRefineView() {
    state.refineViewSeq++; $('refine-sheet').hidden=true; document.body.classList.remove('m-sheet-open');
  }
  // @beacon[
  //   id=opc-pane@phoneRefineView,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The phone Refine-context View (⋯ → What Refine sends): a full-screen sheet drawn by the shared refine-view.js over this Manager's context read exactly as Refine reads it (whole/budgeted guarded prose for either backend), or the same full prompt with no history under an explicit no-context choice; a failed read shows why and offers no prompt; opens at the end and sends nothing.,
  // ]
  async function phoneRefineView() {
    setMoreMenu(false);
    if (state.viewing) return;
    const box=$('draft'), passage=refinePassage(box), t=state.target, revision=state.selectionRevision, seq=++state.refineViewSeq, p=provider();
    $('refine-sheet').hidden=false; document.body.classList.add('m-sheet-open'); box.blur(); // reading, not typing
    text($('rv-for'),t?.title || '');
    const label=$('provider').querySelector('option[value="'+p+'"]')?.textContent || p;
    const paint=(ctx, note, omitted=false)=>{
      if (seq !== state.refineViewSeq) return; // closed, or opened again meanwhile
      let built=null, failure='';
      try { built=refineRequest(passage,ctx ? ctx.text : '',ctx ? historyBoundaries(ctx) : []); } catch (e) { failure='Refine would stop here: '+e.message; }
      opcRefineView.render($('rv-body'),{mode:state.refineViewMode,history:ctx ? ctx.text : null,historyNote:note,
        historyDetail:Number.isSafeInteger(ctx?.turns) ? ctx.turns+' prose turns' : '',usingSelection:passage.usingSelection,
        draftBefore:built ? built.draftBefore : passage.original.slice(0,passage.start),draftAfter:built ? built.draftAfter : passage.original.slice(passage.end),
        // An explicit no-context Refine still has a full prompt to show; a failed read does not run at all.
        request:(ctx || omitted) ? built : null,promptNote:(ctx || omitted) ? failure : note,
        promptDetail:label+' · '+model(p)+' · thinking '+setting('refine_think_level','medium')+(passage.transcription.trim() ? '' : ' · nothing to refine yet')},
        {onMode:mode=>{state.refineViewMode=mode;}});
    };
    if (noContext()) return paint(null,'Conversation context omitted by explicit choice; Close returns to the same draft.',true);
    if (!registered(t) && !ocReady(t)) return paint(null,'Choose a validated Manager: Refine reads its conversation as context.');
    if (backendOf(t) === 'opencode' && !ocReady(t)) return paint(null,'This OpenCode Manager is not validated yet; close and open this view to read again.');
    paint(null,'Reading this Manager’s context…');
    try { paint(await refineContext(t,revision)); }
    catch (e) { paint(null,'Context not read: '+e.message+'. Refine does not run without it; close and open this view to read again, or choose Refine without conversation context in Refine setup.'); }
  }
  // Context view: the desktop's context gauge for each live Manager (and the selected one), and
  // Dan's subscription allotments from the pane's cached `oc.sh usage --json` reading.
  // STRIP_USAGE_MS: the Chat view's own slower read of the same cached /api/usage, so the chips' quota shading is current
  // without opening Context (the server's shared cache admits at most one provider read per account per 2 minutes).
  const CONTEXT_MS=10000, USAGE_MS=15000, USAGE_PENDING_MS=3000, USAGE_STALE_SECONDS=300, STRIP_USAGE_MS=90000;
  // Tokens as the desktop's context line reads them: 294246 -> 294K, 1000000 -> 1M.
  const tokenCount=value => value >= 1000000 ? (Math.round(value/100000)/10)+'M' : value >= 1000 ? Math.round(value/1000)+'K' : String(value);
  function contextRows() {
    return state.managers.filter(t=>backendOf(t) === 'claude' && (t.executionId === state.selected || !['sleeping','ended'].includes(t.lifecycle)))
      .sort((a,b)=>Number(b.executionId === state.selected)-Number(a.executionId === state.selected));
  }
  // OpenCode Managers have their own measured native readout (newest assistant call's input + cache + output +
  // reasoning), kept in its own section and never mixed with Claude's gauge or the account allotments.
  function ocContextRows() {
    return state.managers.filter(t=>backendOf(t) === 'opencode' && OC_SID.test(t.sessionId || ''))
      .sort((a,b)=>Number(b.sessionId === state.selected)-Number(a.sessionId === state.selected));
  }
  // The desktop gauge's rule: keep the last measured figure for the conversation; a newer
  // unknown read keeps its reason beside it, never a guess.
  function rememberContext(executionId, readout) {
    if (!readout || typeof readout !== 'object' || typeof readout.state !== 'string') return;
    const measured=readout.state === 'measured' && Number.isSafeInteger(readout.occupancy_tokens) && readout.occupancy_tokens >= 0;
    state.contexts.set(executionId,{last:measured ? readout : state.contexts.get(executionId)?.last || null,newest:readout});
    // The chip's gauge follows the same figure.
    const chip=$('managers')?.querySelector('[data-execution-id="'+executionId+'"]'), listed=chip && state.managers.find(row=>mid(row) === executionId);
    if (listed) markManager(chip,listed);
  }
  // @beacon[
  //   id=opc-pane@phoneReadContexts,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Context view: reads each live (or selected) Claude Manager's desktop contextReadout every 10 s from an exact-execution events page that starts past the last row (no rows, the same figure the desktop gauge shows), and each OpenCode Manager's own measured native context from GET /api/opencode/manager in a separate section; the open chat's own pages feed Claude's figure too, and an OpenCode unknown stays unknown without ever blocking Send.,
  // ]
  async function phoneReadContexts() {
    const now=Date.now();
    await Promise.all(contextRows().map(async t=>{
      const scopeRef=(t.scopes?.find(s=>s.label === 'main') || t.scopes?.[0])?.scopeRef;
      if (!t.sessionRef || !scopeRef || state.contextBusy.has(t.executionId) || now < (state.contextNext.get(t.executionId) || 0)) return;
      state.contextBusy.add(t.executionId);
      try {
        const data=await request('/api/claude/events?'+new URLSearchParams({sessionRef:t.sessionRef,scopeRef,fromSeq:String(Number.MAX_SAFE_INTEGER)}));
        if (data.source !== 'host' || data.role !== 'Manager' || data.executionId !== t.executionId) throw new Error('context identity mismatch');
        rememberContext(t.executionId,data.contextReadout);
      } catch (e) {
        state.contexts.set(t.executionId,{last:state.contexts.get(t.executionId)?.last || null,newest:{state:'unavailable',reason:e.message}});
        if (RESYNC.has(e.code)) state.nextList=0; // reissued handles arrive with the next Manager list
      } finally { state.contextBusy.delete(t.executionId); state.contextNext.set(t.executionId,Date.now()+CONTEXT_MS); }
    }));
    await Promise.all(ocContextRows().map(async t=>{
      const key='oc:'+t.sessionId;
      if (!ocApi || state.contextBusy.has(key) || now < (state.ocContextNext.get(key) || 0)) return;
      state.contextBusy.add(key);
      try {
        const data=await ocApi.request('manager?'+new URLSearchParams({id:t.sessionId}));
        if (!ocApi.valid(data.target) || data.target.sessionId !== t.sessionId) throw new Error('context identity mismatch');
        state.ocContexts.set(t.sessionId,{context:data.context || null,title:data.title,model:data.model,readAt:Date.now()});
      } catch (e) {
        const old=state.ocContexts.get(t.sessionId) || {};
        state.ocContexts.set(t.sessionId,{...old,error:e.message,readAt:Date.now()});
      } finally { state.contextBusy.delete(key); state.ocContextNext.set(key,Date.now()+CONTEXT_MS); }
    }));
    renderContext();
  }
  // @beacon[
  //   id=opc-pane@phoneReadUsage,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The default/active usage poll (every 15 s in Context, every 90 s in Chat) supplies Claude banked resets and the chips' quota shading (each landed reading repaints the strip); the separate native GPT controller owns viewed-account quota and banked resets so late default polls cannot overwrite it. Ordinary reads never force and deliberate Refresh remains cache/backoff bounded.,
  // ]
  async function phoneReadUsage(force=false) {
    if (state.usageBusy || (!force && Date.now() < state.usageNext)) return;
    state.usageBusy=true;
    const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),12000);
    try {
      const response=await fetch('/api/usage'+(force ? '?refresh=1' : ''),{cache:'no-store',signal:controller.signal});
      const data=JSON.parse(await response.text());
      if (!response.ok || data?.schemaVersion !== 1 || data.backend !== 'usage') throw new Error(data?.error || 'HTTP '+response.status);
      state.usage={...data,receivedAt:Date.now()}; state.usageError=null;
    } catch (e) { state.usageError=e.name === 'AbortError' ? 'the pane did not answer' : e.message; }
    finally {
      clearTimeout(timer); state.usageBusy=false;
      state.usageNext=Date.now()+(state.usage?.refreshing ? USAGE_PENDING_MS : USAGE_MS);
    }
    renderContext(); renderList(); // the chips' quota shading follows the same reading
  }
  function percentText(value) { return Number.isFinite(value) ? Math.round(value)+'%' : '—'; }
  function ago(seconds) {
    return seconds < 45 ? 'just now' : seconds < 90 ? '1 min ago' : seconds < 3600 ? Math.round(seconds/60)+' min ago' :
      seconds < 86400 ? (Math.round(seconds/360)/10)+' h ago' : Math.round(seconds/86400)+' d ago';
  }
  function span(seconds) {
    const m=Math.max(0,Math.round(seconds/60)), d=Math.floor(m/1440), h=Math.floor(m%1440/60);
    return d ? d+'d '+h+'h' : h ? h+'h'+(m%60 ? ' '+(m%60)+'m' : '') : (m%60)+'m';
  }
  // "resets 9:00 PM · in 2h 31m" (a weekday or date when it is not today), from the PC's clock.
  function resetText(value, now, recorded=false) {
    const at=typeof value === 'number' ? value : typeof value === 'string' ? Date.parse(value)/1000 : NaN;
    if (!Number.isFinite(at)) return 'reset time unknown';
    const date=new Date(at*1000), days=(at-now)/86400, today=new Date(now*1000).toDateString() === date.toDateString();
    const when=date.toLocaleString([],today ? {hour:'numeric',minute:'2-digit'} : days < 6 ? {weekday:'short',hour:'numeric',minute:'2-digit'} :
      {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
    return (recorded ? 'recorded reset ' : 'resets ')+when+(at > now ? ' · in '+span(at-now) : '');
  }
  function windowLabel(label) { const text=String(label || 'window').replace(/^five-hour\b/,'5-hour'); return text[0].toUpperCase()+text.slice(1); }
  function planLabel(plan) { const max=/max_(\d+)x/i.exec(plan || ''); return max ? 'Max '+max[1]+'×' : plan || ''; }
  function meterNode({fill=null,known='true',dot=null,label,sub='',percent='',amount='',selected=false,flag='',title=''}) {
    const node=element('div',undefined,'m-meter'), main=element('span',undefined,'m-meter-main'), figures=element('span',undefined,'m-meter-figures');
    let lead;
    if (dot !== null) { lead=element('span',undefined,'m-dot-state'); lead.dataset.on=String(dot); }
    else {
      lead=element('span',undefined,'m-gauge'); lead.dataset.known=fill === null && known === 'true' ? 'false' : known;
      if (fill !== null) lead.style.setProperty('--gauge-fill',Math.max(0,Math.min(100,fill)).toFixed(1));
      lead.setAttribute('role','img'); lead.setAttribute('aria-label',label+': '+(percent || 'unknown'));
    }
    main.append(element('span',label,'m-meter-label'));
    for (const line of [sub].flat()) if (line) main.append(element('span',line,'m-meter-sub'));
    if (percent) figures.append(element('span',percent,'m-meter-percent'));
    if (amount) figures.append(element('span',amount,'m-meter-amount'));
    node.append(lead,main,figures);
    if (selected) node.dataset.selected='true';
    if (flag) node.dataset.flag=flag;
    if (title) node.title=title;
    return node;
  }
  function noteNode(value, flag='') { const node=element('div',value,'m-note'); if (flag) node.dataset.flag=flag; return node; }
  // Rebuilt only when what it shows changed, so a once-a-second tick never churns the DOM.
  function fillMeters(container, nodes) {
    const key=nodes.map(n=>n.outerHTML).join('');
    if (container.renderKey !== key) { container.replaceChildren(...nodes); container.renderKey=key; }
  }
  // Banked rate-limit resets, one entry per source that reports them, each reading carrying a
  // reset_credits list of the same shape (a Claude grant adds count and clears).
  // GPT banked resets belong to the viewed native account in OCChatGPTAccounts, not this default poll.
  const BANKED_SOURCES=[{name:'Claude (active account)',reading:u=>u?.claude}];
  const WEEKDAYS=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const CLEARS={five_hour:'5-hour',seven_day:'weekly'};
  // "Thu 10-22 15:24": the phone's local time, to the minute.
  function minuteStamp(seconds) {
    const d=new Date(seconds*1000), two=n=>String(n).padStart(2,'0');
    return WEEKDAYS[d.getDay()]+' '+two(d.getMonth()+1)+'-'+two(d.getDate())+' '+two(d.getHours())+':'+two(d.getMinutes());
  }
  // @beacon[
  //   id=opc-pane@bankedResetNodes,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Banked resets by source: held grants, expiry/countdown and cleared windows, urgent within 24h; primary or banked-only 429s show a quiet note and the shared endpoint retry countdown, never a false zero. Retained lists show their own observation age and stale flag independently of healthy quota.,
  // ]
  function bankedResetNodes(u, now, waiting) {
    const nodes=[], at=c=>Number.isFinite(c?.expires_at) ? c.expires_at : Infinity, count=c=>Number.isInteger(c.count) && c.count > 0 ? c.count : 1;
    const retryText=r=>{ const at=Number.isFinite(r?.retry_at) ? r.retry_at : r?.reset_credits_retry_at;
      const left=Number.isFinite(at) ? at-now : null; return left !== null && left > 0 ? ' · next read in '+span(left) : ''; };
    for (const source of BANKED_SOURCES) {
      const r=source.reading(u), credits=r?.reset_credits, stale=!!r?.stale || !!r?.reset_credits_stale;
      if (!r) { nodes.push(noteNode(source.name+': '+waiting)); continue; }
      if (!Array.isArray(credits)) {
        if (r.rate_limited || r.reset_credits_rate_limited) {
          // A backoff is not a failed read to shout about: the provider's quiet note and its gate.
          const note=r.reset_credits_note || r.note;
          nodes.push(noteNode(source.name+': banked resets not read yet'+(note ? ' — '+note : '')+retryText(r)));
        } else {
          nodes.push(noteNode(source.name+': banked resets '+(r.ok || stale ? 'could not be read' : 'unknown · '+(r.error || 'reading unavailable'))+
            (r.reset_credits_error ? ' · '+r.reset_credits_error : ''),'error'));
        }
        continue;
      }
      const bankedAt=Number.isFinite(r.reset_credits_observed_at) ? r.reset_credits_observed_at : null;
      const bankedAge=bankedAt !== null ? Math.max(0, now-bankedAt) : Number.isFinite(r.reset_credits_age_seconds) ? r.reset_credits_age_seconds : null;
      const ageNote=bankedAge !== null && bankedAge >= 60 ? ' · banked list read '+ago(bankedAge) : '';
      const live=credits.filter(c=>c && at(c) > now).sort((a,b)=>at(a)-at(b)), total=live.reduce((n,c)=>n+count(c),0);
      nodes.push(noteNode(source.name+' · '+(total ? total+' available' : 'none banked')+(stale ? ' · as of the last reading' : '')+ageNote+retryText(r),stale ? 'warn' : ''));
      for (const c of live) {
        const known=at(c) !== Infinity, left=known ? at(c)-now : null, urgent=known && left < 86400;
        const clears=Array.isArray(c.clears) && c.clears.length ? 'clears '+c.clears.map(k=>CLEARS[k] || String(k).replace(/_/g,' ')).join(' + ') : '';
        const held=c.paused || c.usable_now === false ? 'held · not usable right now' : '';
        nodes.push(meterNode({dot:urgent ? 'urgent' : true,label:(count(c) > 1 ? count(c)+'× ' : '')+(known ? minuteStamp(at(c)) : 'expiry unknown'),
          percent:known ? 'in '+span(left) : '',
          sub:[urgent ? '⚠ use it within 24 h or lose it' : '',clears,held],flag:urgent ? 'urgent' : stale ? 'stale' : '',
          title:source.name+' banked reset'+(c.title ? ' · '+c.title : '')+(known ? ' · expires '+new Date(at(c)*1000).toString() : '')+
            (Number.isFinite(c.granted_at) ? ' · granted '+minuteStamp(c.granted_at) : '')}));
      }
    }
    return nodes;
  }
  // @beacon[
  //   id=opc-pane@ocManagerMeter,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Context view's OpenCode Manager meter: the newest measured assistant call's input + cache read + cache write + output + reasoning against that model's window, with its basis and as-of time; unknown never becomes zero or a blocker, and it is a separate row from Claude's gauge and the account allotments.,
  // ]
  function ocManagerMeter(t) {
    const known=state.ocContexts.get(t.sessionId), c=known?.context, selected=t.sessionId === state.selected;
    const label=t.title || t.sessionId, err=known?.error;
    if (!known) return meterNode({known:'false',label,selected,percent:'—',sub:'reading…'});
    if (!c) return meterNode({known:'false',label,selected,percent:'—',
      sub:err ? 'context read failed: '+String(err).slice(0,120) : 'no measured assistant call yet'});
    const used=Number.isFinite(c.usedTokens) && c.usedTokens >= 0 ? c.usedTokens : null;
    const window=Number.isFinite(c.window) && c.window > 0 ? c.window : null;
    const share=used !== null && window ? Math.min(100,100*used/window) : null;
    const at=Number.isFinite(c.measuredAt) && c.measuredAt > 0 ? ' · as of '+clockTime(c.measuredAt) : '';
    const basis=typeof c.basis === 'string' && c.basis ? c.basis : 'newest assistant call';
    return meterNode({known:used === null ? 'false' : window ? 'true' : 'tokens',fill:share,label,selected,
      percent:share === null ? '—' : Math.round(share)+'%',
      amount:used === null ? '—' : tokenCount(used)+' / '+(window ? tokenCount(window) : '?'),
      sub:'OpenCode · '+basis, flag:err ? 'stale' : '',
      title:'OpenCode context '+(used === null ? 'unknown' : used.toLocaleString('en-US')+' tokens')+
        (window ? ' of '+window.toLocaleString('en-US')+(share !== null ? ' ('+share.toFixed(1)+'%)' : '') : ' · window unknown')+at+
        (err ? ' · last read failed: '+String(err).slice(0,120) : '')});
  }
  function managerMeter(t) {
    const known=state.contexts.get(t.executionId), r=known?.last, newest=known?.newest, selected=t.executionId === state.selected;
    const label=t.title || t.executionId, sleeping=t.lifecycle === 'sleeping' ? 'sleeping · ' : '';
    if (!r) return meterNode({known:'false',label,selected,percent:'—',
      sub:sleeping+(newest ? 'no figure yet'+(newest.reason ? ': '+String(newest.reason).slice(0,120) : '') : 'reading…')});
    const tokens=r.occupancy_tokens, window=Number.isSafeInteger(r.window_tokens) && r.window_tokens > 0 ? r.window_tokens : null;
    const share=window ? Math.min(100,100*tokens/window) : null, stale=newest !== r && newest?.state !== 'measured';
    const at=Number.isFinite(r.measured_at) && r.measured_at > 0 ? ' · as of '+clockTime(r.measured_at) : '';
    return meterNode({known:window ? 'true' : 'tokens',fill:share,label,selected,percent:share === null ? '—' : Math.round(share)+'%',
      amount:tokenCount(tokens)+' / '+(window ? tokenCount(window) : '?'),
      sub:sleeping ? 'sleeping' : stale ? 'last known figure · newest read unknown' : '', flag:stale ? 'stale' : '',
      title:'Context '+tokens.toLocaleString('en-US')+' tokens'+(window ? ' of '+window.toLocaleString('en-US')+' ('+share.toFixed(1)+'%)' : ' · window unknown')+at+
        ' · last completed model call (input + cache)'+(stale && newest?.reason ? ' · newest read: '+String(newest.reason).slice(0,120) : '')});
  }
  // @beacon[
  //   id=opc-pane@renderPhoneContext,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Phone Context composes Manager meters active-Claude banked resets and each provider controller's own viewed-account quota; the default usage poll never paints GPT quota plan or banked labels. No selection or native action.,
  // ]
  function renderContext() {
    if (state.view !== 'context') return;
    fillMeters($('context-managers'),contextRows().map(managerMeter));
    const ocSection=$('context-opencode'); if (ocSection) fillMeters(ocSection,ocContextRows().map(ocManagerMeter));
    const u=state.usage, now=u ? u.now+(Date.now()-u.receivedAt)/1000 : Date.now()/1000;
    const waiting=!u ? (state.usageError ? 'Allotments unavailable: '+state.usageError : 'Reading allotments…') : 'Reading allotments…';
    // Both subscription controllers own their viewed login; no default/late poll can repaint it.
    window.OCAccounts?.get($('account'))?.paintQuota();
    window.OCChatGPTAccounts?.get($('chatgpt-account'))?.paintQuota();
    fillMeters($('banked'),bankedResetNodes(u,now,waiting));
    const age=Number.isFinite(u?.ageSeconds) ? u.ageSeconds+(Date.now()-u.receivedAt)/1000 : null;
    const old=age !== null && age > USAGE_STALE_SECONDS, failed=u?.error || (u && state.usageError);
    const wait=Number.isFinite(u?.retryAt) ? Math.max(0, u.retryAt-now) : null;
    $('usage-status').parentNode.dataset.stale=String(!!(old || failed));
    text($('usage-status'),u?.refreshing && age === null ? 'Reading the providers…' :
      age === null ? (failed ? 'Not read: '+failed : '') :
      (old ? 'Reading is '+ago(age).replace(/ ago$/,'')+' old' : 'Updated '+ago(age))+(u.refreshing ? ' · updating…' : '')+
      (wait !== null ? ' · waiting out the Claude rate limit · next read in '+span(wait) : '')+(failed ? ' · last refresh failed: '+failed : ''));
  }
  async function poll() {
    if (state.pollBusy) return;
    state.pollBusy=true;
    try {
      // Programs and View are pages in frames: here only the receipts and controls the Managers need.
      if (state.view === 'programs' || state.view === 'glimpse') { await pollReceipts(); pollControls(); return; }
      if (Date.now() >= state.nextList) await phoneLoadManagers();
      // A slow old receipt must not hold up the current conversation's read cadence.
      pollReceipts(); pollControls(); if (state.tune) void readTune();
      if (state.view === 'context') { void phoneReadContexts(); void phoneReadUsage(); renderContext(); return; }
      // The chips' quota shading: one cached usage read per STRIP_USAGE_MS (poll runs only while the page is visible).
      if (Date.now() >= state.stripUsageNext) { state.stripUsageNext=Date.now()+STRIP_USAGE_MS; void phoneReadUsage(); }
      // A viewed session's page carries its own state; the Manager is observed again once it is back.
      await Promise.all([phoneReadChat(),state.viewing ? null : observeState()]);
    } finally { state.pollBusy=false; }
  }
  const PHONE_VIEWS=['chat','programs','context','glimpse'];
  function showPhoneView(view) {
    setMaximized(null); setMoreMenu(false); closeTune();
    state.view=view;
    $('chat-view').hidden=view !== 'chat'; $('extras').hidden=view !== 'chat'; $('programs').hidden=view !== 'programs'; $('context').hidden=view !== 'context';
    $('glimpse').hidden=view !== 'glimpse';
    for (const name of PHONE_VIEWS) $('view-'+name).setAttribute('aria-pressed',String(view === name));
    $('view-'+view).scrollIntoView?.({block:'nearest',inline:'nearest'});
    if (view !== 'chat') $('draft').blur();
    if (view === 'programs') {
      const frame=$('programs-frame'); if (!frame.getAttribute('src')) frame.src='/m-programs.html';
      syncProgramManager();
    } else if (view === 'glimpse') {
      // The View page loads once, on the first tap, and stays mounted (its document, scroll and choices kept); it reads
      // nothing until Dan chooses, and nothing at all while hidden. Shown again, a maximized View takes the screen again.
      const frame=$('glimpse-frame'); if (!frame.getAttribute('src')) frame.src='/glimpse.html';
      else { try { frame.contentWindow?.opcGlimpseView?.shown?.(); } catch (_) { /* not loaded yet: it asks by itself */ } }
    } else if (view === 'context') {
      state.contextNext.clear(); state.ocContextNext.clear(); state.nextList=0; renderContext(); void phoneReadUsage(); void window.OCAccounts?.get($('account'))?.refresh(false);
      void window.OCChatGPTAccounts?.get($('chatgpt-account'))?.refresh(); void poll();
    } else { state.nextList=0; void poll(); }
  }
  function updatePhoneViewport() {
    const viewport=window.visualViewport, style=document.documentElement.style, height=viewport?.height || window.innerHeight;
    style.setProperty('--m-viewport-top',(viewport?.offsetTop || 0)+'px');
    // Under a pressed text-box button the shell keeps its height (the chat is one screen tall, so a resize would move the
    // button); it follows the screen's top only, and the tap's end applies the rest (wireThumbStrip letGo).
    if (thumbStrip?.held()) return;
    style.setProperty('--m-viewport-height',height+'px');
    document.body.dataset.keyboard=String(window.innerHeight-height > 120);
    thumbStrip?.fit();  // the keyboard shell keeps the text box and its buttons at home as the keyboard comes and goes
  }
  // The keyboard shell's handle (wireThumbStrip), so maximize can hand the shell back.
  let thumbStrip=null;
  // @beacon[
  //   id=opc-pane@phoneThumbStrip,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Phone keyboard shell: while the draft has focus, #m-page is fixed over visualViewport and #m-scroll scrolls inside it, the chat one screen tall, its right-edge strip immovable. stripScroll joins frame, conversation history and rows above into one continuous scroll; a downward flick rests at home with the draft and buttons visible. Nothing moves under the caret-placing focus tap, even when iOS places it later: m.css reserves the strip's room so draft text never rewraps, and opening on focus keeps the box's screen position until a viewport resize brings it home. Home follows layout changes until a drag/touch moves away. A draft showing its end before focus keeps its INNER scroll at the end after viewport size/pan or box-height changes and subsequent fits, normal or maximized; no focus-time scroll, caret write, timer or scroll-event correction. A compact box keeps the opening tap's last-line caret; a tall/maximized editor leaves a caret before nonblank trailing text to native scrolling instead of pinning it off screen. A pre-scrolled-up draft is untouched; its own touchmove/wheel/navigation, second tap, range selection, mid-text input or blur releases the end pin. Independently, the conversation's top visible reading line is taken afresh on each shell opening/closing and restored after sync/off/fit, viewport resize/pan and iOS page scroll-back, using visualViewport.pageTop less window.scrollY for iOS WebKit and Chrome client coordinates. This corrects the shell's conversation rewrap without scroll anchoring; Dan's press/touch/wheel releases that line, and Following keeps following instead. A composer-button press holds frame height/home/blur-shell-off until its click, cancellation or next touch elsewhere; wireTapOnLift acts the action row on lift and its click releases the hold, so iOS cannot move the button away before hit-testing. Only the exact conversation end counts as Following while the strip drives, never rejudged by its delayed scroll event. A strip tap blurs, a drag keeps focus/range; the maximized draft has its own thumb margin.,
  // ]
  function wireThumbStrip() {
    const strip=$('thumb'), box=$('draft'), view=$('scroll'), conv=$('conversation');
    // The finger on the screen (client coordinates follow the layout viewport, which iOS moves under the keyboard);
    // the shell sits on the screen, so this is also the finger on the shell.
    const screenY=event=>event.clientY-(window.visualViewport?.offsetTop || 0);
    const room=node=>Math.max(0,node.scrollHeight-node.clientHeight);
    // The shell scroller's two landmarks: the chat filling the screen (its top at the top), and home (the text box and
    // its buttons at the bottom of the screen); whole pixels, which every engine's scroller can reach exactly.
    const viewTop=()=>view.getBoundingClientRect().top+view.clientTop;
    const chatAt=()=>Math.min(room(view),Math.max(0,Math.round(view.scrollTop+$('chat').getBoundingClientRect().top-viewTop())));
    const homeAt=()=>Math.min(room(view),Math.max(0,Math.round(view.scrollTop+$('composer').getBoundingClientRect().bottom+8-viewTop()-view.clientHeight)));
    // settle: the frame's height when a tap's focus opened the shell away from home (home follows once it resizes: the
    // keyboard); press: a finger on one of the text box's buttons, holding the shell still (below); hold: the line at the
    // top of what Dan sees of the conversation, kept the top line as the keyboard comes and goes (below).
    let shell=false, home=true, drag=null, glide=0, glideRun=0, carry=0, pageScroll=0, settle=null, press=null, hold=null;
    // The draft's INNER scroll, independent of shell home and the conversation's reading line. Remember the end before
    // focus can scroll it; re-pin only after the viewport/box changes size, never under the caret-placing focus tap.
    // No scroll-event correction (which would fight native scrolling), focus, caret write or timed retry.
    let draftEnd=false, draftTap=null, draftOpening=null;
    const draftAtEnd=()=>room(box)-box.scrollTop <= 1;
    const draftViewport=()=>window.visualViewport?.height || window.innerHeight;
    const releaseDraftEnd=()=>{ draftEnd=false; draftTap=null; };
    box.addEventListener('pointerdown',event=>{
      releaseDraftEnd();
      if (document.activeElement !== box && event.isPrimary && event.button === 0) draftTap=draftAtEnd();
    },{passive:true});
    for (const kind of ['touchmove','wheel','pointercancel']) box.addEventListener(kind,releaseDraftEnd,{passive:true});
    box.addEventListener('keydown',event=>{ if (/^(Arrow|Page|Home|End)/.test(event.key)) releaseDraftEnd(); });
    // The opening tap may land a few characters short on the last line: that collapsed caret must not cancel its pin.
    // A range, a second tap, navigation or typing elsewhere is deliberate. Input capture precedes changedDraft's fit.
    const draftSelection=()=>{ if (document.activeElement === box && box.selectionStart !== box.selectionEnd) releaseDraftEnd(); };
    box.addEventListener('select',draftSelection); document.addEventListener('selectionchange',draftSelection);
    box.addEventListener('input',()=>{ if (box.selectionEnd !== box.value.length) releaseDraftEnd(); },{capture:true});
    function goHome() { view.scrollTop=homeAt(); home=true; }
    function stop() {
      glideRun++; cancelAnimationFrame(glide); glide=0; carry=0; drag=null; settle=null; delete strip.dataset.pressed; state.stripDriving=false;
    }
    // The reading line. Opening or closing the shell narrows or widens the content by the strip's width (every row
    // rewraps, about 13 % taller or shorter) and moves the chat (one screen tall, home), and the keyboard then shrinks
    // the screen and iOS pans or scrolls the page under it; the conversation's scrollTop stays the same number, so
    // uncorrected it would point turns away from what Dan was reading, thousands of pixels on a long conversation (Safari
    // before 27 has no scroll anchoring, and on an iPhone with iOS 27 none acted either). So at every opening and every
    // closing the line at the top of what he sees of the conversation is taken afresh (the block it is in and how far
    // down that block) and kept the top line through every change that follows: the shell's own (sync, off, fit: home,
    // the box), each of the keyboard's resizes and pans as it comes or goes, and the page scrolling under it (iOS scrolls
    // it back as the keyboard leaves). Each put-back measures the page as it is then, so however many viewport events
    // iOS sends, in whatever order, the line ends at the top of what is on screen of the conversation, the same line
    // every time. Dan's own touch, press or wheel anywhere lets it go, and a conversation following its newest message
    // keeps following instead. Only the conversation's own scrollTop moves.
    // The screen in client coordinates (getBoundingClientRect's), which engines measure from different places: Chrome
    // from the layout viewport, so the screen's top is visualViewport.offsetTop; iOS WebKit from the visual viewport
    // itself, so it is 0 (with the keyboard up an iPhone reports offsetTop 340 and window.scrollY 340, and the frame
    // over the screen reads 0). Where the visual viewport sits in the document less the document's scroll is the
    // screen's top in both.
    function screenNow() {
      const vv=window.visualViewport, top=!vv ? 0 : Number.isFinite(vv.pageTop) ? vv.pageTop-window.scrollY : vv.offsetTop || 0;
      return {top,bottom:top+(vv?.height || window.innerHeight)};
    }
    // The part of the conversation on the screen: its box clipped to the screen and, with the shell on, to its frame.
    function seenPart() {
      const c=conv.getBoundingClientRect(), s=screenNow(), f=shell ? $('page').getBoundingClientRect() : s, top=c.top+conv.clientTop;
      return {top:Math.max(top,s.top,f.top),bottom:Math.min(top+conv.clientHeight,s.bottom,f.bottom)};
    }
    // The line at the top of that part (1 px in): the innermost block there, and how far down that block it is.
    function readingLine() {
      if (state.follow || state.view !== 'chat') return null;
      const part=seenPart(), y=part.top+1;
      if (!(part.bottom > part.top)) return null;
      const row=[...$('messages').children].find(n=>n.getBoundingClientRect().bottom > y); if (!row) return null;
      let node=row;  // the innermost block on that line (a deeper block comes later in document order)
      for (const n of row.querySelectorAll('p,li,pre,table,blockquote,h1,h2,h3,h4,h5,h6,summary,.m-head')) {
        const r=n.getBoundingClientRect(); if (r.height > 0 && r.top <= y && r.bottom > y) node=n;
      }
      const r=node.getBoundingClientRect();
      return {node,fraction:(y-r.top)/Math.max(1,r.height)};
    }
    // The held line back at the top of what is on screen of the conversation now (1 px in, as it was taken).
    function keepReading() {
      if (!hold) return;
      if (state.follow || !hold.node.isConnected) { hold=null; return; }  // following wins; a row repainted away is gone
      const part=seenPart(), r=hold.node.getBoundingClientRect();
      if (!(part.bottom > part.top) || !r.height) return;  // the conversation is not on the screen (a maximized panel, another view)
      const move=Math.round(r.top+hold.fraction*r.height-part.top-1);  // whole pixels: WebKit drops a scroll position's fraction
      if (!move) return;
      const at=conv.scrollTop; conv.scrollTop=at+move;
      if (conv.scrollTop !== at) stripLog?.record('hold:'+Math.round(conv.scrollTop-at));
    }
    // The shell is on while the text box has focus in the combined view (not maximized, not a read-only view); it opens at
    // home, or, for the box's own focus (keep), exactly where the box already is. The reading line is taken afresh first
    // (one still held from an earlier change stays only while the conversation is not on the screen to take it from)
    // and put back once the shell has changed (opening at home, where the box was, or closing).
    function sync(focused, keep=false) {
      const was=keep && !shell ? box.getBoundingClientRect().top : null, on=focused && !state.viewing && !state.maximized;
      if (on !== shell) hold=readingLine() || (state.follow ? null : hold);
      document.body.dataset.thumb=focused && !state.viewing ? 'on' : '';
      if (on === shell) return;
      shell=on; stop(); stripLog?.record(on ? 'shell-on' : 'shell-off');
      if (on && was === null) goHome();
      else if (on) {
        view.scrollTop+=box.getBoundingClientRect().top-was;
        home=Math.abs(view.scrollTop-homeAt()) < 2; if (!home) settle=view.clientHeight;
      }
      keepReading();
    }
    function off() {
      const was=shell; sync(false);
      if (was && pageScroll) { window.scrollTo(0,pageScroll); keepReading(); }
    }
    // A tap that focuses the box places its caret at the finger's point in the layout as the browser finds it then, which
    // on iOS can be after this handler and after the next task. So the shell moves nothing under the finger: the box's
    // text keeps its width (m.css: its right padding holds the strip's room until the shell takes it) and the box keeps
    // its place on the screen; home comes when the keyboard resizes the frame.
    box.addEventListener('focus',()=>{
      draftEnd=draftTap ?? draftAtEnd(); draftTap=null;
      draftOpening={viewport:draftViewport(),top:window.visualViewport?.offsetTop || 0,height:box.clientHeight,resized:false};
      if (!shell) pageScroll=window.scrollY; sync(true,true);
    });
    box.addEventListener('blur',()=>{ releaseDraftEnd(); draftOpening=null; if (press) press.blurred=true; else off(); });
    // A finger on one of the text box's buttons (or its release link) holds the shell still until that tap's click: no
    // home keeping (the status line changing size), no new frame height (the keyboard resizing; updatePhoneViewport) and
    // no shell going off on a blur moves the button between the touch and the click, which iOS hit-tests again where the
    // finger lifted (a button moved away lost its click, and iOS hid the keyboard instead). The click, a cancelled touch
    // or the next touch elsewhere lets go; what was held back happens then.
    function letGo() {
      const held=press; press=null; if (!held) return;
      if (held.blurred && document.activeElement !== box) off();
      updatePhoneViewport();  // the screen's size now, then fit
    }
    document.addEventListener('pointerdown',event=>{
      if (shell && event.target.closest?.('#m-composer button, #m-composer a')) press=press || {blurred:false}; else letGo();
    },true);
    document.addEventListener('click',letGo); $('composer').addEventListener('pointercancel',letGo);
    // One continuous scroll through the combined view read as one document (+ toward the text box, − toward the oldest
    // message): the shell first brings the chat to fill the screen, then the conversation scrolls to its end, then the
    // shell goes on. Each part hands on exactly what it did not move (scrollers snap to whole pixels, WebKit's always);
    // a fraction held back inside a part is carried into the next move, and at the very end nothing is carried, so
    // reversing moves at once. Answers whether there was any way left to go.
    function stripScroll(delta) {
      let want=delta+carry, open=false, convMoved=false; carry=0;
      const chat=chatAt(), stages=want > 0 ? [[view,chat],[conv,room(conv)],[view,room(view)]] : [[view,chat],[conv,0],[view,0]];
      for (const [node,limit] of stages) {
        const at=node.scrollTop, left=limit-at;
        if (left*want <= 0 || Math.abs(left) < .5) continue;  // nothing left this way in this part
        const whole=Math.abs(want) >= Math.abs(left);
        node.scrollTop=whole ? limit : at+want;
        const got=node.scrollTop-at;
        open=true; convMoved=convMoved || (node === conv && got !== 0); want-=got;
        if (!whole) { carry=want; break; }
      }
      // At once, before a poll can act on a stale follow; the scroll event this write raises comes later (after the lift,
      // when the strip no longer drives) and must not decide again by the looser rule (the conversation's scroll listener).
      if (convMoved) { followCheck(); state.stripConvAt=conv.scrollTop; }
      home=Math.abs(view.scrollTop-homeAt()) < 2;
      return open;
    }
    // How far a scroll toward the bottom still is from home (0 at or past it).
    function untilHome() {
      const at=view.scrollTop, end=homeAt(), chat=chatAt();
      return at >= end-.5 ? 0 : Math.max(0,chat-at)+room(conv)-conv.scrollTop+end-Math.max(at,chat);
    }
    strip.addEventListener('pointerdown',event=>{
      event.preventDefault();  // a press must not move focus: the editor keeps its keyboard and range until a tap
      stop(); hold=null;  // Dan scrolls himself now
      const y=screenY(event);
      drag={id:event.pointerId,start:y,y,moved:false,samples:[[event.timeStamp,y]]}; state.stripDriving=true;
      try { strip.setPointerCapture(event.pointerId); } catch {}
      strip.dataset.pressed='true'; stripLog?.record('down',y);
    });
    strip.addEventListener('pointermove',event=>{
      if (!drag || event.pointerId !== drag.id) return;
      const y=screenY(event);
      if (drag.moved || Math.abs(y-drag.start) >= 8) {
        drag.moved=true; stripScroll(drag.y-y); drag.y=y;
        drag.samples.push([event.timeStamp,y]); if (drag.samples.length > 8) drag.samples.shift();
      }
      stripLog?.record('move',y);
    });
    const end=(event,cancelled)=>{
      if (!drag || event.pointerId !== drag.id) return;
      const done=drag, y=screenY(event); drag=null; delete strip.dataset.pressed; stripLog?.record(cancelled ? 'cancel' : 'up',y);
      if (cancelled || !done.moved) { state.stripDriving=false; if (!cancelled) box.blur(); return; }
      // A flick glides on, slowing like a native scroll, until it stops or meets the end; one toward the bottom from
      // above home comes to rest there, with the text box and its buttons in view, rather than sliding past them. Each
      // glide is its own run: a new press or the shell's end (stop) retires it at its next frame.
      const recent=done.samples.filter(([t])=>event.timeStamp-t <= 100), [t0,y0]=recent[0] || [event.timeStamp,y];
      let speed=event.timeStamp > t0 ? (y0-y)/(event.timeStamp-t0) : 0, last=performance.now();
      const run=++glideRun, bounded=speed > 0 && untilHome() > 0;
      const step=now=>{
        if (run !== glideRun) return;
        const dt=Math.min(32,now-last); last=now;
        const d=speed*dt, toHome=bounded ? untilHome() : Infinity, arrive=d >= toHome; speed*=Math.pow(.996,dt);
        const open=stripScroll(arrive ? toHome : d); stripLog?.record('glide');
        if (!open || arrive || Math.abs(speed) < .03) { glide=0; state.stripDriving=false; return; }
        glide=requestAnimationFrame(step);
      };
      if (Math.abs(speed) > .15) glide=requestAnimationFrame(step); else state.stripDriving=false;
    };
    strip.addEventListener('pointerup',event=>end(event,false)); strip.addEventListener('pointercancel',event=>end(event,true));
    // Belt and braces for WebKit: the strip's own touch never pans anything underneath (its drags are routed above).
    strip.addEventListener('touchmove',event=>event.preventDefault(),{passive:false});
    // A touch on the shell's content scrolls it natively; home is wherever that leaves it.
    view.addEventListener('scroll',()=>{ if (shell && !drag && !glide) home=Math.abs(view.scrollTop-homeAt()) < 2; },{passive:true});
    // Dan's own doing lets the reading line go: a press anywhere in the page (a tap into the box comes before its focus,
    // which takes the line afresh), the wheel, a touch moving, a key in the conversation; the next opening or closing
    // holds the line he is on then.
    const letLineGo=()=>{ hold=null; };
    for (const kind of ['pointerdown','wheel']) document.addEventListener(kind,letLineGo,{capture:true,passive:true});
    conv.addEventListener('touchstart',letLineGo,{passive:true}); view.addEventListener('touchmove',letLineGo,{passive:true}); conv.addEventListener('keydown',letLineGo);
    // Kept in place as the layout changes: home stays home (the keyboard arriving or leaving, the text box growing, a
    // status line), a conversation that follows its newest message keeps following when its box resizes, and otherwise
    // a held reading line stays the top line of what is on screen of the conversation, the shell on or off (the keyboard
    // leaving after the shell has gone). The viewport and the text box call it directly; the observer catches everything
    // else. A shell opened where the box was goes home once the frame resizes; under a pressed button nothing moves until
    // that tap is done (letGo).
    function fit() {
      if (state.follow && room(conv)-conv.scrollTop > .5) conv.scrollTop=conv.scrollHeight;
      if (press) return;
      if (shell && settle !== null && Math.abs(view.clientHeight-settle) > .5) { settle=null; home=true; }
      if (shell && home && !drag && !glide) { const at=homeAt(); if (Math.abs(view.scrollTop-at) > .5) view.scrollTop=at; }
      if (!drag && !glide) keepReading();
      if (draftEnd && draftOpening && document.activeElement === box && box.clientHeight > 0) {
        const viewport=draftViewport(), top=window.visualViewport?.offsetTop || 0, height=box.clientHeight;
        draftOpening.resized ||= viewport !== draftOpening.viewport || top !== draftOpening.top || height !== draftOpening.height;
        Object.assign(draftOpening,{viewport,top,height});
        // A compact box shows only the last three lines. In a tall editor a tap can choose text far above the end:
        // leave its native caret scroll alone rather than pin that caret off screen when the keyboard shrinks it.
        if (draftOpening.resized && !draftAtEnd() && (compactDraft.matches && state.maximized !== 'draft' || !box.value.slice(box.selectionEnd).trim())) {
          const was=box.scrollTop; box.scrollTop=box.scrollHeight;
          stripLog?.record('draft-pin:'+Math.round(box.scrollTop-was));
        }
      }
    }
    if (typeof ResizeObserver === 'function') { const keep=new ResizeObserver(fit); keep.observe(view); keep.observe($('composer')); keep.observe(conv); keep.observe(box); }
    // The page scrolling under the conversation moves what is on screen of it (iOS scrolls the page back as the keyboard
    // leaves, after the shell has gone): the held line goes back to the top.
    window.addEventListener('scroll',()=>{ if (hold && !press && !drag && !glide) keepReading(); },{passive:true});
    return {sync:()=>sync(document.activeElement === box),fit,held:()=>!!press};
  }
  // @beacon[
  //   id=opc-pane@phoneTapOnLift,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The text box's action row (Refine, After ---, ⋯, Send) acts when a touch lifts on a button, not on the click iOS sends after it: between the two, the compat mousedown moves focus (Send leaves the box) and the keyboard shell with its thumb strip comes or goes, shifting the row before iOS hit-tests the click where the finger lifted, so the tap was lost or landed elsewhere. A lift within 12 px of its touch and inside the button as it was then clicks the button at once; the click iOS sends next, wherever it lands, is swallowed once (the next touch or a second clears that). The pointerdown selection guard (wirePasteSelection) is untouched; mouse, keyboard and VoiceOver keep the ordinary click.,
  // ]
  function wireTapOnLift() {
    let tap=null, swallowUntil=0;
    document.addEventListener('pointerdown',event=>{
      swallowUntil=0;  // a new touch: the last lift's click is not coming any more
      const button=event.pointerType !== 'mouse' && event.isPrimary ? event.target.closest?.('#m-actions > button') : null;
      tap=button ? {button,id:event.pointerId,x:event.clientX,y:event.clientY,rect:button.getBoundingClientRect()} : null;
    },true);
    document.addEventListener('pointercancel',()=>{ tap=null; },true);
    document.addEventListener('pointerup',event=>{
      const t=tap, x=event.clientX, y=event.clientY; tap=null;
      if (!t || event.pointerId !== t.id || t.button.disabled || Math.hypot(x-t.x,y-t.y) > 12 ||
          x < t.rect.left || x > t.rect.right || y < t.rect.top || y > t.rect.bottom) return;
      t.button.click();  // its own click handler, now, before anything moves; bubbling lets the shell go (letGo)
      swallowUntil=performance.now()+1000;
    },true);
    window.addEventListener('click',event=>{
      if (!swallowUntil) return;
      const late=performance.now() < swallowUntil; swallowUntil=0;
      if (late) { event.preventDefault(); event.stopImmediatePropagation(); }
    },true);
  }
  // ⋯ → Strip diagnostics (or ?thumbdebug=1): null until switched on for this page load.
  let stripLog=null;
  // @beacon[
  //   id=opc-pane@phoneStripLog,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=On-device keyboard-shell diagnostics, off until ⋯ → Strip diagnostics or ?thumbdebug=1, for this page load: strip pointer/glide, focus/blur/shell, reading-line hold:N and inner-draft draft-pin:N corrections, viewport/window/scroller events and frames during a drag/glide. Each row carries the finger, view/conversation/draft scroll positions and maxima, caret range, windowY, visualViewport offsets/height/pageTop and shell/strip screen boxes. Copy retains the last 10 s, also shown below setup; browser-only, no request or stored log.,
  // ]
  function phoneStripLog() {
    const rows=[], t0=performance.now(), KEEP_MS=10000, n=v=>Number.isFinite(v) ? String(Math.round(v*10)/10) : '-';
    let frames=0, until=0;
    const record=(kind, finger)=>{
      const now=performance.now(), vv=window.visualViewport, view=$('scroll'), conv=$('conversation'), box=$('draft');
      const shellBox=$('page').getBoundingClientRect(), stripBox=$('thumb').getBoundingClientRect();
      rows.push([now,[n(now-t0),kind,n(finger),n(view.scrollTop),n(view.scrollHeight-view.clientHeight),n(conv.scrollTop),n(conv.scrollHeight-conv.clientHeight),
        n(window.scrollY),n(vv?.offsetTop),n(vv?.height),n(vv?.pageTop),n(window.innerHeight),n(shellBox.top),n(shellBox.height),n(stripBox.top),n(stripBox.height),
        state.follow ? 'follow' : '-',document.body.dataset.thumb === 'on' ? (state.maximized ? 'maximized' : 'shell') : '-',
        n(box.scrollTop),n(box.scrollHeight-box.clientHeight),n(box.selectionStart),n(box.selectionEnd)].join(' ')]);
      while (rows.length > 4000 || now-rows[0][0] > KEEP_MS) rows.shift();
    };
    // Every frame while a drag or glide runs, and a little after it: what the screen did between the events.
    const frame=()=>{ record('frame'); frames=performance.now() < until ? requestAnimationFrame(frame) : 0; };
    window.visualViewport?.addEventListener('resize',()=>record('vv-resize')); window.visualViewport?.addEventListener('scroll',()=>record('vv-scroll'));
    window.addEventListener('scroll',()=>record('window-scroll'),{passive:true});
    $('scroll').addEventListener('scroll',()=>record('view-scroll'),{passive:true});
    $('conversation').addEventListener('scroll',()=>record('conv-scroll'),{passive:true});
    $('draft').addEventListener('scroll',()=>record('draft-scroll'),{passive:true});
    $('draft').addEventListener('focus',()=>record('focus')); $('draft').addEventListener('blur',()=>record('blur'));
    record('start');
    return {
      record(kind, finger) {
        record(kind,finger); until=Math.max(until,performance.now()+600);
        if (!frames) frames=requestAnimationFrame(frame);
      },
      text:()=>['Thumb strip diagnostics · '+new Date(Date.now()).toISOString()+' · '+rows.length+' rows, the last '+KEEP_MS/1000+' s',navigator.userAgent,
        'screen '+screen.width+'x'+screen.height+' @'+devicePixelRatio+' · window '+innerWidth+'x'+innerHeight+' · standalone '+(navigator.standalone === true)+
          ' · visualViewport '+!!window.visualViewport,
        'columns: ms event finger viewTop viewMax convTop convMax windowY vvTop vvHeight vvPageTop innerHeight shellTop shellHeight stripTop stripHeight follow mode draftTop draftMax caretStart caretEnd',
        ...rows.map(row=>row[1])].join('\n')};
  }
  function startStripLog() {
    if (stripLog) return;
    stripLog=phoneStripLog();
    const item=$('strip-log'); item.firstChild.textContent='Copy strip diagnostics'; item.lastChild.textContent='Recording: copies the last 10 s for the Manager';
  }
  // ⋯ → Strip diagnostics: the first tap starts recording, each later tap copies the log for the Manager chat.
  async function phoneStripDiagnostics() {
    setMoreMenu(false);
    if (!stripLog) {
      startStripLog();
      return notice('Strip diagnostics are recording. Use the strip, then ⋯ → Copy strip diagnostics and paste it into the Manager chat.');
    }
    const text=stripLog.text();
    $('strip-log-text').value=text; $('strip-log-box').hidden=false;
    try { await navigator.clipboard.writeText(text); notice('Copied '+text.split('\n').length+' lines of strip diagnostics: paste them into the Manager chat.'); }
    catch (e) { $('strip-log-box').open=true; notice('Copy failed ('+e.message+'): the log is under Strip diagnostics below the setup; select it there and copy.',true); }
  }
  // @beacon[
  //   id=opc-pane@phoneMaximize,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Independent text box/chat/programs maximize toggles: the panel takes the visible screen (visualViewport keeps the text box's compact button row above the iPhone keyboard), reusing its mounted DOM; the same button restores the combined view, its keyboard shell back at home when the text box still has the keyboard. The maximized text box scrolls as a panel (a runway below its buttons) and has a thumb margin beside the editor: a drag there scrolls, a tap blurs the editor (the escape from the iPad's floating keyboard bar). 'glimpse' (no button) is View's frame over the whole screen, edge to edge, while its image lightbox is open (opcViewImmersive); 'view' (View's own maximize button, inside its frame) is the same frame over the whole screen on View's own background, the top bar and tabs away until View restores (opcViewMaximized).,
  // ]
  function setMaximized(kind) {
    const previous=state.maximized;
    if (kind && !previous) state.normalScroll=window.scrollY;
    state.maximized=kind; document.body.classList.toggle('m-maximized',!!kind); document.body.dataset.maximized=kind || '';
    const panels={draft:'composer',chat:'chat',programs:'programs'}, names={draft:'text box',chat:'chat',programs:'programs'};
    for (const name of Object.keys(panels)) {
      const on=name === kind, button=$(name+'-max'), label=on ? 'Restore the normal view' : 'Maximize '+names[name];
      $(panels[name]).classList.toggle('m-maximized-panel',on); button.innerHTML=on ? RESTORE : MAXIMIZE;
      button.setAttribute('aria-pressed',String(on)); button.setAttribute('aria-label',label); button.title=label;
    }
    $('glimpse').classList.toggle('m-maximized-panel',kind === 'glimpse' || kind === 'view');
    if (kind !== 'draft') $('composer').scrollTop=0;
    updatePhoneViewport();
    // Maximizing never opens the keyboard by itself; tap into the text to type.
    if (kind && kind !== 'draft') $('draft').blur();
    thumbStrip?.sync();  // the keyboard shell steps aside for a maximized panel and comes back, at home, after it
    fitDraft();
    requestAnimationFrame(()=>{
      if (state.follow) { const n=$('conversation'); n.scrollTop=n.scrollHeight; }
      if (previous && !state.maximized) window.scrollTo(0,state.normalScroll || 0);
    });
  }
  // View's image lightbox asks for the whole screen while it is open (its frame calls parent.opcViewImmersive): the top bar
  // and the tab row go away, and come back when it closes. Only while View is the tab on screen.
  function phoneViewImmersive(on) {
    if (on) { if (state.view === 'glimpse' && state.maximized !== 'glimpse') setMaximized('glimpse'); }
    else if (state.maximized === 'glimpse') setMaximized(null);
    return state.maximized === 'glimpse';
  }
  // View maximized by its own button (its frame calls parent.opcViewMaximized): the whole screen too, on View's own
  // background, until it restores; the lightbox may take it meanwhile, and View asks again when it closes.
  function phoneViewMaximized(on) {
    if (on) { if (state.view === 'glimpse' && !state.maximized) setMaximized('view'); }
    else if (state.maximized === 'view') setMaximized(null);
    return state.maximized === 'view';
  }
  // @beacon[
  //   id=opc-pane@phonePaste,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The ⋯ → Paste tap reads the clipboard (Safari's own Paste bubble is the only prompt) and selects exactly the pasted passage for Refine: at a cursor it pastes there; a selection (the passage just pasted or refined) is never replaced, the new passage follows it as its own paragraph; no keyboard is opened.,
  // ]
  async function phonePaste() {
    if (state.viewing) return;
    const box=$('draft');
    let pending;
    try { pending=navigator.clipboard.readText(); } // Must start inside the tap's user activation, before any await.
    catch (e) { return notice('Clipboard paste unavailable: '+e.message+' · use the iPhone’s Paste menu instead; text kept.',true); }
    const original=box.value, start=box.selectionStart, end=box.selectionEnd;
    try {
      const pasted=(await pending).replace(/\r\n?/g,'\n');
      if (!pasted) return notice('Clipboard is empty.');
      // If Dan edited while Safari's Paste bubble was up, paste at his current range.
      const value=box.value, same=value === original, a=same ? start : box.selectionStart, at=same ? end : box.selectionEnd;
      const before=value.slice(0,at), appending=a !== at || at === value.length;
      const gap=!appending || !before.trim() || before.endsWith('\n\n') ? '' : before.endsWith('\n') ? '\n' : '\n\n';
      box.setRangeText(gap+pasted,at,at,'end'); box.setSelectionRange(at+gap.length,at+gap.length+pasted.length);
      changedDraft(); markBreakButton();
      if (at === value.length) box.scrollTop=box.scrollHeight;
      notice('Pasted and selected · Refine cleans just this passage.');
    } catch (e) { notice('Clipboard paste unavailable: '+e.message+' · use the iPhone’s Paste menu instead; text kept.',true); }
  }
  function wirePasteSelection() {
    const box=$('draft'); let paste=null;
    const capture=()=>{paste={value:box.value,start:box.selectionStart,end:box.selectionEnd};};
    box.addEventListener('paste',capture);
    box.addEventListener('beforeinput',event=>{
      if (event.inputType === 'insertFromPaste') { if (!paste) capture(); }
      else paste=null;
    });
    box.addEventListener('input',()=>{
      changedDraft();
      const before=paste; paste=null; if (!before) return;
      // Let the browser perform its native paste (including undo and newline normalization).
      // The length delta excludes the replaced selection, so repeated text is unambiguous.
      const value=box.value, end=value.length-(before.value.length-before.end), revision=state.draftRevision;
      if (end < before.start || value.slice(0,before.start) !== before.value.slice(0,before.start) || value.slice(end) !== before.value.slice(before.end)) return;
      setTimeout(()=>{if (box.value === value && state.draftRevision === revision) box.setSelectionRange(before.start,end);},0);
    });
    // A touch on either action must not discard Safari's textarea range before its click.
    for (const action of ['paste','refine','refine-break','draft-max','more','interrupt','refine-view','teams','strip-log']) $(action).addEventListener('pointerdown',event=>{if (document.activeElement === box) event.preventDefault();});
    // After --- follows the selection through the events the box and the document already fire; no polling.
    for (const kind of ['input','select','focus','blur']) box.addEventListener(kind,markBreakButton);
    document.addEventListener('selectionchange',markBreakButton);
    markBreakButton();
  }
  // @beacon[
  //   id=opc-pane@phoneStart,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Starts phone chat without setup, restores recovery and draft sizing, and wires Refine/After ---/Send, the ⋯ menu, First/Latest and positional paging, the saved current-round/keep-open thinking toggle, New Manager/sub-session and runtime choices, model/thinking controls, Context/Programs/View and keyboard-aware maximize/thumb-strip controls. Restores pending controls/launches and explicit no-context Refine; only the actual Programs frame may open a read-only session, and Following stays at the end through glare text-size changes.,
  // ]
  async function phoneStart() {
    try {
      state.selected=read(KEYS.target);
      state.selectionEpoch=Math.max(0,parseInt(read(KEYS.selectionEpoch) || '0',10) || 0);
      state.selectionMarker=read(KEYS.selectionMarker);
      const saved=JSON.parse(read(KEYS.draft) || 'null');
      if (saved && typeof saved.text === 'string') {
        $('draft').value=saved.text;
        state.draftRevision=Number.isSafeInteger(saved.draftRevision) ? saved.draftRevision : 0;
        state.bootDraft={text:saved.text,owner:saved.target ?? saved.executionId ?? null,
          ownerToken:saved.selectionToken ?? selectionToken(saved.target ?? saved.executionId ?? null),
          draftRevision:state.draftRevision,selectionRevision:state.selectionRevision,selectionEpoch:state.selectionEpoch};
      }
      $('original').value=read(KEYS.original) || '';
    } catch (e) { notice('Phone draft recovery unavailable: '+e.message,true); }
    restoreJournals(); restoreControls(); restoreOpenCodeLaunch(); showSetup(); fitDraft(); setMaximized(null);
    compactDraft.addEventListener('change',fitDraft);
    wirePasteSelection();
    $('paste').addEventListener('click',()=>{const pasting=phonePaste(); setMoreMenu(false); return pasting;}); // the clipboard read starts first
    for (const key of Object.keys(EXTRAS)) $(key+'-toggle').addEventListener('click',()=>setExtra($(EXTRAS[key]).hidden ? key : null));
    $('original-copy').addEventListener('click',()=>void copyOriginal());
    for (const view of PHONE_VIEWS) $('view-'+view).addEventListener('click',()=>showPhoneView(view));
    $('usage-refresh').addEventListener('click',()=>void phoneReadUsage(true));
    for (const kind of ['draft','chat','programs']) $(kind+'-max').addEventListener('click',()=>setMaximized(state.maximized === kind ? null : kind));
    // The maximized text box's thumb margin: a tap hides the keyboard (iOS does not blur on its own); a drag scrolls the panel.
    $('draft-margin').addEventListener('click',()=>$('draft').blur());
    thumbStrip=wireThumbStrip();  // the combined view's equivalent: the keyboard shell and its strip while the text box has focus
    wireTapOnLift();  // Refine, After ---, ⋯ and Send act on a touch's lift, before the shell can come or go under the finger
    // ⋯ → Strip diagnostics, or /m?thumbdebug=1 recording from the start.
    $('strip-log').addEventListener('click',()=>void phoneStripDiagnostics());
    if (new URLSearchParams(location.search).get('thumbdebug') === '1') startStripLog();
    window.visualViewport?.addEventListener('resize',updatePhoneViewport); window.visualViewport?.addEventListener('scroll',updatePhoneViewport);
    window.addEventListener('resize',updatePhoneViewport); updatePhoneViewport();
    $('refine').addEventListener('click',paneActivity('refine',()=>phoneRefine()));
    $('refine-break').addEventListener('click',paneActivity('refine',()=>phoneRefine('break'))); $('send').addEventListener('click',paneActivity('send',phoneSend));
    $('more').addEventListener('click',()=>setMoreMenu($('more-menu').hidden));
    $('interrupt').addEventListener('click',paneActivity('control',phoneInterrupt));
    $('status-history').addEventListener('click',()=>setMoreMenu(false)); // status-toast.js opens the log without changing the draft
    $('recheck').addEventListener('click',()=>{setMoreMenu(false); void phoneRecheck();});
    $('refine-view').addEventListener('click',()=>void phoneRefineView());
    $('teams').addEventListener('click',()=>setMoreMenu(false));  // teams-break.js opens its popover
    $('rv-close').addEventListener('click',closeRefineView);
    // The model/thinking sheet: a fresh touch outside it (not the hold's own lift) or Close dismisses it.
    $('tune-close').addEventListener('click',closeTune);
    $('tune-backdrop').addEventListener('pointerdown',event=>{event.preventDefault(); closeTune();});
    $('tune-apply').addEventListener('click',paneActivity('control',phoneApplyTune));
    for (const [group,key] of [['tune-efforts','effort'],['tune-models','model']])
      $(group).addEventListener('click',event=>pickTune(key,event.target.closest?.('button')?.dataset.value));
    $('tune-oc-model').addEventListener('change',()=>pickOcModel($('tune-oc-model').value));
    $('tune-oc-variants').addEventListener('click',event=>pickOcVariant(event.target.closest?.('button')?.dataset.value));
    $('new').addEventListener('click',()=>$('new-form').hidden ? void phoneOpenNewManager() : setNewForm(false));
    $('new-close').addEventListener('click',()=>setNewForm(false));
    $('new-sub').addEventListener('click',()=>$('sub-form').hidden ? void phoneOpenNewSub() : setSubForm(false));
    $('sub-close').addEventListener('click',()=>setSubForm(false));
    $('sub-form').addEventListener('submit',event=>{event.preventDefault(); void paneActivity('launch',phoneLaunchNewSub)();});
    $('first-save').addEventListener('click',()=>void saveFirstMessages(false));
    $('first-restore').addEventListener('click',()=>void saveFirstMessages(true));
    $('new-form').addEventListener('submit',event=>{event.preventDefault(); void paneActivity('launch',phoneLaunchManager)();});
    $('new-runtime').addEventListener('change',()=>{showRuntimePanels();});
    $('new-agent').addEventListener('change',()=>newChoice());
    $('new-oc-model').addEventListener('change',()=>fillOcLaunchVariants());
    $('new-oc-variant').addEventListener('change',()=>newChoice());
    for (const id of ['new-model','new-effort']) for (const kind of ['input','change'])
      $(id).addEventListener(kind,()=>{$(id).dataset.chosen='1'; newChoice();});
    // The menu closes on any tap outside it; Escape closes the menu, then the View, then New Manager.
    document.addEventListener('pointerdown',event=>{if (!$('more-menu').hidden && !event.target.closest?.('#m-more-menu, #m-more')) setMoreMenu(false);},true);
    document.addEventListener('keydown',event=>{
      if (event.key !== 'Escape') return;
      if (!$('more-menu').hidden) setMoreMenu(false); else if (!$('tune').hidden) closeTune(); else if (!$('refine-sheet').hidden) closeRefineView(); else if (!$('new-form').hidden) setNewForm(false);
      else if (!$('sub-form').hidden) setSubForm(false);
    });
    $('provider').addEventListener('change',()=>{saveSetting('refine_provider',$('provider').value);showSetup();});
    $('key').addEventListener('change',()=>saveSetting('refine_'+provider()+'_api_key',$('key').value.trim()));
    $('model').addEventListener('change',()=>saveSetting('refine_'+provider()+'_model',$('model').value.trim()));
    $('thinking').addEventListener('change',()=>saveSetting('refine_think_level',$('thinking').value));
    $('no-context').addEventListener('change',()=>{saveSetting(KEYS.noContext,$('no-context').checked ? 'true' : 'false');state.refineAbort?.abort();});
    $('dictionary').addEventListener('change',()=>{
      try { const terms=JSON.parse($('dictionary').value); if (!Array.isArray(terms) || terms.some(t=>typeof t !== 'string')) throw new Error('expected an array of spellings');
        saveSetting('refine_dictionary',$('dictionary').value); notice('Dictionary saved.',false,'setup-status'); }
      catch (e) { notice('Dictionary not saved: '+e.message,true,'setup-status'); }
    });
    $('import').addEventListener('change',importSettings);
    // Scrolling near either end of what is loaded reads the next batch that way (phonePage), on Dan's own scrolling only.
    const conversation=$('conversation'), intent=()=>{ state.scrollIntent=performance.now(); };
    for (const kind of ['touchstart','touchend','wheel','pointerdown']) conversation.addEventListener(kind,intent,{passive:true});
    conversation.addEventListener('keydown',event=>{ if (['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(event.key)) intent(); });
    conversation.addEventListener('touchmove',()=>{ intent(); edgePage(); },{passive:true});
    // A scroll event at exactly the place the thumb strip left the conversation is the strip's own, already judged at
    // once (stripScroll); the first scroll anywhere else judges following again.
    conversation.addEventListener('scroll',()=>{
      if (state.stripConvAt === null || conversation.scrollTop !== state.stripConvAt) { state.stripConvAt=null; followCheck(); }
      edgePage();
    },{passive:true});
    // Outdoor glare mode (glare.js) grows the text: a conversation following its newest message keeps following it.
    window.addEventListener('opc-glare',()=>{if (state.follow) { const n=$('conversation'); n.scrollTop=n.scrollHeight; }});
    document.addEventListener('selectionchange',()=>eachTool(call=>{if (call.deferred) paintTool(call);}));
    $('latest').addEventListener('click',phoneLatest);
    $('first').addEventListener('click',()=>void phoneFirst());
    thinkingButton(); $('thinking-mode').addEventListener('click',toggleThinkingMode);
    // Only the Programs view (the same-origin sidebar frame) opens a session here.
    window.addEventListener('message',event=>{
      if (event.source !== $('programs-frame').contentWindow || event.origin !== window.origin) return;
      phoneOpenSession(event.data);
    });
    document.addEventListener('visibilitychange',()=>{if (!document.hidden) { state.nextList=0; poll(); }});
    // The clock: the visible tool rows' elapsed times tick once a second in place, then the ordinary
    // poll (its own cadence is unchanged).
    setInterval(()=>{if (!document.hidden) { refreshToolRunning(); void phoneOpenCodeActivity(); poll(); }},1000);
    await poll();
  }
  return {start:phoneStart,loadManagers:phoneLoadManagers,selectManager,readChat:phoneReadChat,observeState,
    paste:phonePaste,refine:paneActivity('refine',()=>phoneRefine()),refineBreak:paneActivity('refine',()=>phoneRefine('break')),send:paneActivity('send',phoneSend),interrupt:paneActivity('control',phoneInterrupt),recheck:phoneRecheck,refineView:phoneRefineView,closeRefineView,poll,pollReceipts,releaseSend,showView:showPhoneView,
    readContexts:phoneReadContexts,readUsage:phoneReadUsage,quotaShade,openNewManager:phoneOpenNewManager,launchManager:paneActivity('launch',phoneLaunchManager),openNewSub:phoneOpenNewSub,launchNewSub:paneActivity('launch',phoneLaunchNewSub),first:phoneFirst,latest:phoneLatest,
    openSession:phoneOpenSession,openInView:phoneOpenInView,programPosition:phoneProgramPosition,revealSession:phoneRevealSession,turnAt,showTurn:phoneShowTurn,
    openCodeActivity:phoneOpenCodeActivity,leaveViewer,openTune:phoneOpenTune,closeTune,applyTune:paneActivity('control',phoneApplyTune),readTune,pollControls,viewImmersive:phoneViewImmersive,viewMaximized:phoneViewMaximized,state,
    // Pure rules for the targeted Node checks: backend identity, the draft-clear evidence rule and control evidence.
    rules:{backendOf,mid,ocTarget,ocDraftClearable,ocSendNote,ocControlNote,ocControlSettled,ocPendingControl,ocTuneChange,noContext,createdMs,managerOrder,managerNames,viewerSession,READERS}};
})();
// handle-links.js: a handle tapped here (Chat) opens in the View tab, its conversation turn the root; the Programs frame
// passes its own origin.
globalThis.opcOpenInView=(handle,origin)=>phoneManager.openInView(handle,origin?.from ? origin :
  {from:'outside',where:'Chat',...(phoneManager.turnAt(origin?.node) ? {turn:phoneManager.turnAt(origin.node)} : {})});
// View's root widget (its frame): where a socket sits, and show a session in Programs.
globalThis.opcProgramPosition=(handle,active)=>phoneManager.programPosition(handle,active);
globalThis.opcRevealSession=key=>phoneManager.revealSession(key);
globalThis.opcShowTurn=turn=>phoneManager.showTurn(turn);
// View's image lightbox (its frame): the whole screen while it is open; View maximized by its own button, the same.
globalThis.opcViewImmersive=on=>phoneManager.viewImmersive(!!on);
globalThis.opcViewMaximized=on=>phoneManager.viewMaximized(!!on);

phoneManager.start();
