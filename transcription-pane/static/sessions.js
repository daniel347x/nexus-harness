/* Read-only live/history viewer. One editor/target receiver and one server mailbox remain authoritative. */
(() => {
  'use strict';
  const PAGE_BYTES = 8 * 1024 * 1024, WINDOW_BYTES = 16 * 1024 * 1024, WINDOW_EVENTS = 200, BOOKMARKS = 256;
  // Sidebar: host summaries are paged independently of on-demand native history. Each group lists
  // GROUP_PAGE sessions at a time behind its own "show more"; optional row details are viewport-only.
  const GROUP_PAGE = 30;
  // Conversation: ▲/▼ clicks queue up to NAV_QUEUE moves; a move loads at most JUMP_PAGES pages;
  // scrolling within EDGE_PX of either end loads the next batch that way.
  const NAV_QUEUE = 20, JUMP_PAGES = 300, EDGE_PX = 160;
  // Program tree: PROGRAM_PAGE programs per home group behind "show more" (favorites unpaged); expander choices (program,
  // Conductor, seat) persist by stable key; OpenCode program rows refresh every OC_POLL_MS on their own.
  const PROGRAM_PAGE = 12, LINEAGE_OPEN = 'pane_lineage_open', LINEAGE_OPEN_MAX = 1000, OC_POLL_MS = 15000, CALL_TURNS = 32;
  // Busy/tool evidence must not wait for the full inventory's background scan.
  const OC_ACTIVITY_MS = 2000, OC_EVIDENCE_MS = 15000;
  // A selected, server-validated OpenCode Manager refreshes its own readback (activity, model, context) on this lane.
  const OC_MANAGER_MS = 2000;
  // A row's glance (💬, either backend) is read when it opens, then again every GLANCE_POLL_MS while it stays open (at
  // most GLANCE_POLL_MAX open glances, one read at a time); never as part of the list's own reads.
  const GLANCE_POLL_MS = 5000, GLANCE_POLL_MAX = 8, CLAUDE_UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
  // A 📦 History session key (history_archive.SESSION_KEY): a Claude session or subagent, or an OpenCode ses_ id.
  const HISTORY_KEY = /^(?:claude:cc-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?:\/agent-[A-Za-z0-9_-]{1,256})?|opencode:ses_[A-Za-z0-9]{8,64})$/;
  const SIDEBAR_SECTIONS = 'pane_sidebar_sections';
  // "Open in CodeNomad": the request's state is read every FOCUS_POLL_MS until it is done, failed or expired.
  const FOCUS_POLL_MS = 700, FOCUS_FINAL = ['done','failed','expired','superseded'];
  // The session list's width (percent of the page), dragged on its divider and remembered in this browser.
  const SIDE_WIDTH = 'pane_sidebar_width', SIDE_DEFAULT = 34, SIDE_MIN = 16, SIDE_MAX = 70;
  // Subagent groups Dan folded by hand (session|turn|agent keys, newest last, bounded).
  const GROUPS_CLOSED = 'pane_subagent_groups_closed', GROUPS_CLOSED_MAX = 300;
  // Refusals that mean "these handles no longer name anything" (a pane-server restart, an evicted or
  // rebuilt index, a newer generation): the view resyncs in place by position instead of stopping.
  const RESYNC = new Set(['stale','stale-epoch','stale-generation','gap','reload-required','changed-index']);
  // A resync warns only after this long of real failures (or when the conversation is not listed).
  const RESYNC_WARN_MS = 30000;
  // A history read still unanswered after this long says "Catching up" quietly; the rows on screen stay.
  const SLOW_READ_MS = 4000;
  // Quiet host information only, refreshed by the existing full inventory read, never a work gate.
  const HOST_LAG_BUSY_MS = 1000;
  // The facade's per-read damage witness (a top-level Boolean on the events page): the host's stored rows for this
  // execution could not be read, so the viewer shows what the raw log still yields. It is a quiet line of its own
  // beside the edge's, never a warning, never replacing "Catching up", and never a reason to blank or disable rows.
  const DAMAGED_ROWS = 'stored rows for this conversation are damaged; showing what the raw log allows';
  const ref = value => typeof value === 'string' && /^[A-Za-z0-9_-]{1,256}$/.test(value);
  const bytes = value => new TextEncoder().encode(value).length;
  const id = () => globalThis.crypto?.randomUUID?.() || ('history-' + Date.now() + '-' + Math.random().toString(36).slice(2));
  // Tokens as the context line reads them: 294246 -> 294K, 1000000 -> 1M. Display only.
  const tokenCount = value => value >= 1000000 ? (Math.round(value / 100000) / 10) + 'M' : value >= 1000 ? Math.round(value / 1000) + 'K' : String(value);
  // Decimal MB, matching how Dan reads the raw stream sizes; source bytes only.
  const megabytes = value => (value / 1000000).toFixed(1) + ' MB';
  // Wire vocabulary used by both the native reader and the host call join. New
  // display statuses are not identity failures: retain the event, label unknown.
  const CALL_STATUS = Object.freeze({'not-final-fragment':'streaming (not-final fragment)',streaming:'streaming',stored:'stored',error:'error','pending-or-missing':'pending-or-missing',ambiguous:'ambiguous'});
  // @beacon[
  //   id=opc-pane@createClaudeHistory,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=Shared desktop viewer/sidebar and phone Programs owner: inventory health paints one quiet top-bar load caption without any control or targeting effect; Manager-family/Running now/Favorites placement, last-good conversation rows and the per-conversation damaged-rows notice stay independent.,
  // ]
  function createClaudeHistory(doc = document) {
    const $ = name => doc.getElementById(name), clientId = id();
    const sidebarOnly=doc.body?.dataset?.view === 'programs';
    // Retain these actual controls when a redraw moves them out of the empty child footer into row metadata.
    const childControls={refresh:$('subagents-refresh'),more:$('subagents-more')};
    const view = doc.defaultView, parentEditor = view?.frameElement?.id === 'claude-sessions-frame' && view.parent !== view ? view.parent : null;
    const origin = view?.origin || view?.location?.origin;
    const runningStrip=globalThis.opcToolReading?.runningTools?.($('running-tools'),doc);
    const state = { session:null, scope:null, leaf:null, epoch:null, target:null, generation:0, listGeneration:0,
      listAbort:null, groupShown:new Map(), renaming:null, navQueue:Promise.resolve(), navPending:0, edgeBusy:false, startKey:undefined, abort:null, loading:false, loadingMode:null,
      pages:[], pageAt:-1, rows:new Map(), identities:new Map(), order:[], used:0, delta:null, next:null, rowsAhead:null, indexingContinuation:null, firstCursor:null,
      accepted:null, pending:null, failed:null, clickSequence:0, queued:null, posting:false, mailbox:null, callTurns:new Map(),
      newCount:0, targetAction:null, targetSequence:0, channel:null, blocked:false, pollBusy:false, nextPoll:0, failures:0,
      resync:null, resyncBusy:false, resyncWarn:null, readError:null, catchingUp:null, rowsUnreadable:false,
      inventory:[], nativeHistory:[], nativeHistoryLoaded:false, listWindowDays:null, view:'week', viewSupported:{claude:true,opencode:true}, archive:null, sourceGeneration:0,
      stars:{}, starsAvailable:null, starsBusy:false, starRevision:0, starVersions:new Map(), starQueues:new Map(), liveState:null, incidents:[], incidentsKnown:false, incidentOverflow:false, targetInfo:null, tailEvicted:false, observationError:null,
      contextReadout:null, sourceProgress:null, warnings:new Set(), childParent:null, children:[], childNext:null,
      childBusy:false, childGeneration:0, childNextPoll:0, childPaged:false, inventoryBusy:false, inventoryNextPoll:Date.now()+10000,
      hostActivity:new Map(), hostStoreRef:null, hostActivityBusy:false, hostActivityNextPoll:0, hostActivityFailure:null,
      follow:true, followPaused:false, selectionPause:false, scrollIntent:0, expanded:new Set(), collapsed:new Set(), childCache:new Map(),
      activity:new Map(), subagentActivity:new Map(), agents:new Map(), runs:new Map(), groupClosed:new Set(storedList(GROUPS_CLOSED)), activityHidden:false, parentLive:null, parentLiveAt:0,
      oc:{rows:[],status:'loading',error:null,stale:false}, ocBusy:false, ocGeneration:0, ocAbort:null, ocNextPoll:0, glances:new Map(), glanceBusy:new Set(), glancePolling:false, glanceNextPoll:0, glanceLarge:null, ocTurns:new Map(), lineageOpen:storedPairs(LINEAGE_OPEN),
      ocActivity:null, ocActivityBusy:false, ocActivityNextPoll:0, ocActivityAgain:false,
      ocManagerBusy:false, ocManagerNextPoll:0,
      tele:new Map(), teleReads:new Map(), teleWanted:null, teleOpen:new Set(), teleWidth:null, teleRetry:false,
      detailWanted:new Map(), detailRunning:new Set(), claudeTurns:new Map(), hostActivityAt:0,
      focus:null, focusBusy:false, focusNextPoll:0,
      tree:null, treeDefaults:new Map(), placed:new Map(), sidebarHover:false,
      sections:storedSections(), activeOnly:new Map(), unfolded:new Set(),
      listAnchor:null, listHint:null, listTouch:false, listTouchAt:0, listHeldAt:0, listScrolledAt:0, listOwnTop:null, listSlack:0, listSlackTop:null, listSettle:null };
    function message(name, text, error=false) { $(name).textContent = text; $(name).dataset.error = String(error); }
    // @beacon[
    //   id=opc-pane@paintHostLoad,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=One neutral desktop/phone sidebar caption from the existing inventory's optional hostLoad: queued/running conversations take precedence, otherwise lag above one second; no load, clear debt, History or an unobserved first page hides it, with no request, dialog or disabled control.,
    // ]
    function paintHostLoad(load) {
      const line=$('sessions-host-load');if(!line)return;
      const sets=Number.isSafeInteger(load?.rows_behind_sets) && load.rows_behind_sets>0?load.rows_behind_sets:0;
      const lag=typeof load?.loop_lag_ms_max_60s==='number' && Number.isFinite(load.loop_lag_ms_max_60s)?load.loop_lag_ms_max_60s:0;
      const text=state.archive?'':sets?`host catching up · ${sets} conversations`:lag>HOST_LAG_BUSY_MS?`host busy · lag ${(lag/1000).toFixed(1)} s`:'';
      line.textContent=text;line.title=text;line.hidden=!text;
    }
    function warn(field) { if(!state.warnings.has(field)){state.warnings.add(field);globalThis.console?.warn?.('History display metadata unavailable: '+field);} }
    function listField(value,field) {if(value==null)return [];if(Array.isArray(value))return value;warn(field);return [];}
    function count(value,field) {if(value==null)return null;if(Number.isSafeInteger(value)&&value>=0)return value;warn(field);return null;}
    function callStatus(value) {if(Object.hasOwn(CALL_STATUS,value))return CALL_STATUS[value];warn('call status');return 'unknown';}
    function eventIdentity(event) {
      if(typeof event.uuid!=='string' || !event.uuid)return null;
      const identity=[event.scope ?? state.scope.scopeRef,event.uuid];
      // Native task IDs are generation-local; replayed prose UUIDs are not.
      if(event.kind==='task' && Number.isSafeInteger(event.generation) && event.generation>=1)identity.push(event.generation);
      return JSON.stringify(identity);
    }
    function resident(event) {const identity=eventIdentity(event);return state.rows.get(event.recordRef) || (identity?state.rows.get(state.identities.get(identity)):[...state.rows.values()].find(row=>JSON.parse(row.signature).recordRef===event.recordRef));}
    function element(tag, text, cls) { const node = doc.createElement(tag); if (text !== undefined) node.textContent=text; if(cls)node.className=cls; return node; }
    function storedList(key) { try{const v=JSON.parse(doc.defaultView?.localStorage?.getItem(key) || '[]');return Array.isArray(v)?v.filter(x=>typeof x==='string'):[];}catch(_){return [];} }
    function storedPairs(key) { try{const v=JSON.parse(doc.defaultView?.localStorage?.getItem(key) || '[]');return new Map(Array.isArray(v)?v.filter(x=>Array.isArray(x)&&typeof x[0]==='string'&&typeof x[1]==='boolean'):[]);}catch(_){return new Map();} }
    function storeList(key,values) { try{doc.defaultView?.localStorage?.setItem(key,JSON.stringify(values));}catch(_){/* display preference only */} }
    function storedSections() {
      try{const value=JSON.parse(doc.defaultView?.localStorage?.getItem(SIDEBAR_SECTIONS) || '{}');
        if(!value || typeof value!=='object' || Array.isArray(value))return {collapsed:new Set(),activeOnly:new Set()};
        const keys=list=>new Set(Array.isArray(list)?list.filter(key=>typeof key==='string'):[]);
        return {collapsed:keys(value.collapsed),activeOnly:keys(value.activeOnly)};
      }catch(_){return {collapsed:new Set(),activeOnly:new Set()};}
    }
    function storeSections() {
      try{doc.defaultView?.localStorage?.setItem(SIDEBAR_SECTIONS,JSON.stringify({collapsed:[...state.sections.collapsed],activeOnly:[...state.sections.activeOnly]}));}catch(_){/* display preference only */}
    }
    // Human-readable time for an epoch-seconds stamp: 21:58:03 today, otherwise Oct 14 21:58.
    function clock(seconds) {
      if(typeof seconds!=='number' || !Number.isFinite(seconds) || seconds<=0)return '';
      const d=new Date(seconds*1000),hms=d.toTimeString().slice(0,8);
      return d.toDateString()===new Date(Date.now()).toDateString()?hms:d.toLocaleDateString('en-US',{month:'short',day:'numeric'})+' '+hms.slice(0,5);
    }
    // Dan's full turn time: "Mon, Dec 4th, 2026 3:07 PM ET" in the browser's zone (US zones
    // read ET/CT/MT/PT, others keep their short name). timeOnly drops the date part.
    function fullTime(seconds,{timeOnly=false,timeZone}={}) {
      if(typeof seconds!=='number' || !Number.isFinite(seconds) || seconds<=0)return '';
      try {
        const parts={};for(const p of new Intl.DateTimeFormat('en-US',{weekday:'short',month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true,timeZoneName:'short',...(timeZone?{timeZone}:{})}).formatToParts(new Date(seconds*1000)))parts[p.type]=p.value;
        const day=Number(parts.day),suffix=day%100>=11&&day%100<=13?'th':['th','st','nd','rd'][day%10]||'th';
        const zone=String(parts.timeZoneName || '').replace(/^([ECMP])[SD]T$/,'$1T');
        const time=parts.hour+':'+parts.minute+' '+String(parts.dayPeriod || '').toUpperCase()+(zone?' '+zone:'');
        return timeOnly?time:`${parts.weekday}, ${parts.month} ${day}${suffix}, ${parts.year} ${time}`;
      } catch(_) {return clock(seconds);}
    }
    async function json(url, options = {}, responseLimit = PAGE_BYTES) {
      const ctrl = new AbortController(), abort = () => ctrl.abort();
      options.signal?.addEventListener('abort',abort,{once:true});
      if (options.signal?.aborted) ctrl.abort();
      const timer=setTimeout(abort,12000);
      try {
        const response=await fetch(url,{...options,signal:ctrl.signal,cache:'no-store'});
        let text='';
        if (response.body?.getReader) {
          const reader=response.body.getReader(), decoder=new TextDecoder('utf-8',{fatal:true}); let total=0;
          try { while(true) { const part=await reader.read(); if(part.done)break; total+=part.value.byteLength;
            if(total>responseLimit)throw new Error('history response limit; not cropped'); text+=decoder.decode(part.value,{stream:true}); }
            text+=decoder.decode();
          } catch(e) { await reader.cancel().catch(()=>{}); throw e; }
        } else { text=await response.text(); if(bytes(text)>responseLimit)throw new Error('history response limit; not cropped'); }
        if(ctrl.signal.aborted)throw new Error('history request cancelled or timed out');
        const data=JSON.parse(text);
        if(!response.ok) { const e=new Error(data.error || ('HTTP '+response.status)); e.code=response.status; e.status=data.status; throw e; }
        return data;
      } finally {clearTimeout(timer);options.signal?.removeEventListener('abort',abort);}
    }
    // An OpenCode conversation object (openCodeSession): its server-validated Manager facts are the only
    // thing that can make it a Send/control target; otherwise it is view-only. A name or inventory row
    // never grants that by itself.
    function isOpenCode(row) { return row?.backend==='opencode'; }
    // A 📦 History row (/api/history/*): an ended conversation as archived; no live evidence ever applies to it.
    function archived(row) { return row?.storeRef==='history'; }
    // A row or session that claims to be a genuine root Manager: the server's program rows mark one with
    // manager:true/kind:manager; a bare ses_ id is checked by its full id instead.
    function openCodeManagerClaim(row) {
      return !!row && (row.manager===true || !!row.manager || row.kind==='manager' || row.role==='Manager' || row.ocRole==='manager');
    }
    // The server witness of the validated Manager this conversation targets (never invented locally).
    function openCodeManagerTarget(row) {
      if(!isOpenCode(row) || !row?.manager || row.managerIdentityChanged)return null;
      const target=row.manager.target;
      return target && target.backend==='opencode' && target.sessionId===row.id &&
        typeof target.witness==='string' && /^[a-f0-9]{64}$/.test(target.witness) ? target : null;
    }
    function openCodeManagerModel(model) {
      return model && typeof model==='object' && typeof model.providerID==='string' && typeof model.id==='string' ?
        model.providerID+'/'+model.id+(model.variant?' · '+model.variant:'') : null;
    }
    // OpenCode Manager readback, labelled exactly as the server measured it: measured / unknown-window /
    // unknown. Never a Claude figure, a session cumulative total or an assumed window.
    function openCodeContextText(context) {
      const c=context;
      if(!c || typeof c!=='object')return 'context unknown';
      const tokens=Number.isSafeInteger(c.usedTokens) && c.usedTokens>=0 ? c.usedTokens : null;
      if(tokens===null)return 'context unknown'+(typeof c.basis==='string'&&c.basis?' ('+c.basis+')':'');
      const window=Number.isSafeInteger(c.window) && c.window>0 ? c.window : null;
      const at=typeof c.measuredAt==='number' && Number.isFinite(c.measuredAt) && c.measuredAt>0 ? ' · as of '+clock(c.measuredAt/1000) : '';
      const basis=typeof c.basis==='string'&&c.basis?' · '+c.basis:'';
      return 'context '+tokenCount(tokens)+(window?' / '+tokenCount(window)+' ('+Math.round(100*tokens/window)+'%)':' · window unknown')+at+basis;
    }
    // Inventory, subagent and Claude history pages expect Claude; only an OpenCode conversation page expects OpenCode.
    function envelope(data,backend='claude') { if(!data || data.schemaVersion!==1 || data.backend!==backend)throw new Error('wrong history schema/backend'); }
    // The registry role: the only role that carries authority (Send, controls, targeting).
    function role(row) { return row.parentUuid && row.role==='subagent' ? 'subagent' : row.source!=='host' ? 'history' : ['manager','conductor','worker','helper','base'].includes(String(row.role).toLowerCase()) ? String(row.role).toLowerCase() : 'unknown'; }
    const ROLE_BADGE=Object.freeze({manager:'Manager',conductor:'Conductor','lead-seat':'Lead seat','quick-worker':'Quick worker',helper:'Helper',worker:'Worker',base:'Base',subagent:'Subagent',history:'History',unknown:'Unknown host role'});
    // The real role for the sidebar badge, filter and the Manager group (display only): the
    // server's displayRole (host registry role refined by seat labels; a host twin's role for its
    // native transcript; else the working folder), never a name. Older rows fall back to role().
    function realRole(row) {
      const plain=role(row);
      return plain!=='subagent' && Object.hasOwn(ROLE_BADGE,row?.displayRole) && row.displayRole!=='subagent' ? row.displayRole : plain;
    }
    function roleLabel(row) { return isOpenCode(row) ? ROLE_BADGE[row.ocRole] || 'OpenCode session' : ROLE_BADGE[realRole(row)]; }
    function roleSource(row) {
      if(isOpenCode(row))return row.ocRole?'OpenCode session · role from its launch labels':'OpenCode session · not listed in the sidebar (opened by its id)';
      return {host:'role from the host registry',
        'host-twin':'the native transcript of a host session; role from its host record',
        folder:'role from its working folder (no host record)'}[row?.roleSource] ||
        (role(row)==='subagent'?'native subagent of a listed session':row?.source==='host'?'role from the host registry':'no host record: role unknown');
    }
    // Lifecycle and authority are Claude host facts: an OpenCode conversation has none of them, also
    // through the selected-state fallbacks (state.liveState / targetInfo) a Claude view left behind.
    function writable(row) { return !isOpenCode(row) && row?.source==='host' && role(row)==='manager' && !ended(row) && row.capabilities?.nativeInput===true && row.capabilities?.controls===true; }
    function ended(row) { return !isOpenCode(row) && (row?.lifecycle==='ended' || row?.lifecycle==='sleeping' || ['exited','exited_draining','launch_failed','stopped','collected'].includes(row?.state) || ['exited','exited_draining','launch_failed','stopped','collected'].includes(state.liveState?.state) && row===state.session); }
    // A collected conversation sleeps, not ended: nothing is lost and the next Send
    // reconnects it. Proved-ended registered Managers keep the same reconnect Send path.
    function sleeping(row) { return !isOpenCode(row) && (row?.lifecycle==='sleeping' || row?.close_reason==='collected' || (row===state.session && state.liveState?.close_reason==='collected')); }
    function reconnectable(row) { return !isOpenCode(row) && row?.source==='host' && role(row)==='manager' && ended(row) && row.capabilities?.reconnect===true; }
    function successor(row) { return isOpenCode(row) ? null : row?.successor || state.targetInfo?.successor || state.inventory.find(r=>writable(r) && r.cwd===row?.cwd && r.executionId!==row.executionId); }
    function endedText(row) { const live=successor(row);const replacement=live?' The live Manager is '+(live.title || live.executionId)+'.':' No live Manager in this workspace is ready yet; the list refreshes automatically.';
      if(row?.capabilities?.reconnect!==true && typeof row?.reconnectBlocked==='string' && row.reconnectBlocked)
        return 'This Manager has ended and Send cannot reconnect it: '+row.reconnectBlocked+replacement;
      return sleeping(row) ? 'This Manager is sleeping (reconnects on Send).'+replacement : 'This Manager has ended.'+replacement+' Send to it reconnects the conversation.'; }
    function evidence(value) { return value===undefined || value===null ? 'unknown' : typeof value==='string' ? value : JSON.stringify(value); }
    // The context figure rides the same page responses as the conversation; it is the
    // last completed model call's prompt (input + cache read + cache creation, no output)
    // with its honest as-of time, or an explicit unknown — never a byte ratio or a guess.
    function contextText() {
      const readout=state.contextReadout;
      if(!state.session || !readout || typeof readout!=='object')return '';
      if(readout.state==='measured' && Number.isSafeInteger(readout.occupancy_tokens) && readout.occupancy_tokens>=0) {
        // Measured tokens are shown even when the native window is not available yet;
        // the fraction appears only with a real window, never an assumed one.
        const window=Number.isSafeInteger(readout.window_tokens) && readout.window_tokens>0 ? readout.window_tokens : null;
        const percent=window? ' ('+Math.round(100*readout.occupancy_tokens/window)+'%)' : '';
        const at=typeof readout.measured_at==='number' && Number.isFinite(readout.measured_at) && readout.measured_at>0
          ? ' · as of '+new Date(readout.measured_at*1000).toTimeString().slice(0,5) : '';
        return ' · context '+tokenCount(readout.occupancy_tokens)+(window?' / '+tokenCount(window)+percent:'')+at;
      }
      const reason=typeof readout.reason==='string'&&readout.reason?': '+readout.reason.slice(0,160):'';
      return ' · context unknown'+reason;
    }
    // @beacon[
    //   id=opc-pane@contextGauge,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The status bar's context pie: keeps the last measured Claude figure by the viewer's stable conversation identity across reissued handles/generations, never another session or subagent. A validated OpenCode Manager shows its own per-Manager server readback (measured or unknown-window, with basis and as-of) and never a Claude figure; an unvalidated OpenCode conversation has no figure: unknown.,
    // ]
    function contextGauge() {
      const node=$('context-gauge');if(!node)return;
      const readout=state.contextReadout,session=identityOf(state.session);
      if(session && !isOpenCode(state.session) && readout?.state==='measured' && Number.isSafeInteger(readout.occupancy_tokens) && readout.occupancy_tokens>=0)
        state.lastContext={...readout,identity:session};
      const known=session && !isOpenCode(state.session) && state.lastContext?.identity===session ? state.lastContext : null;
      const set=(kind,title,fill)=>{if(node.dataset)node.dataset.known=kind;node.title=title;node.setAttribute?.('aria-label',title);
        if(fill==null)node.style?.removeProperty?.('--gauge-fill');else node.style?.setProperty?.('--gauge-fill',fill.toFixed(1));};
      if(archived(state.session))return set('false','Context: none · an archived conversation has no live context',null);
      if(isOpenCode(state.session)) {
        const manager=state.session?.manager;
        if(!manager)return set('false','Context: unknown · OpenCode conversations report no context figure here',null);
        const c=manager.context||{};
        const tokens=Number.isSafeInteger(c.usedTokens) && c.usedTokens>=0 ? c.usedTokens : null;
        if(tokens===null)return set('false','Context: unknown · OpenCode Manager reported no measured call yet'+(typeof c.basis==='string'&&c.basis?' ('+c.basis+')':''),null);
        const window=Number.isSafeInteger(c.window) && c.window>0 ? c.window : null,percent=window?Math.min(100,100*tokens/window):null;
        const at=typeof c.measuredAt==='number' && Number.isFinite(c.measuredAt) && c.measuredAt>0 ? clock(c.measuredAt/1000) : '';
        const title='Context '+tokens.toLocaleString('en-US')+' tokens'+(window?' of '+window.toLocaleString('en-US')+' ('+percent.toFixed(1)+'%)':' · window unknown')+
          (at?' · as of '+at:'')+' · OpenCode Manager, newest measured assistant call'+(typeof c.basis==='string'&&c.basis?' · '+c.basis:'');
        return set(window?'true':'tokens',title,percent);
      }
      if(!known)return set('false','Context: no figure known yet'+(session && typeof readout?.reason==='string' && readout.reason?' ('+readout.reason.slice(0,160)+')':''),null);
      const tokens=known.occupancy_tokens,window=Number.isSafeInteger(known.window_tokens) && known.window_tokens>0 ? known.window_tokens : null;
      const at=typeof known.measured_at==='number' && Number.isFinite(known.measured_at) && known.measured_at>0 ? ' · as of '+clock(known.measured_at) : '';
      const stale=known!==readout && readout?.state!=='measured' ? ' · newest read unknown'+(readout?.reason?': '+String(readout.reason).slice(0,120):'')+' (showing the last known figure)' : '';
      const percent=window ? Math.min(100,100*tokens/window) : null;
      const title='Context '+tokens.toLocaleString('en-US')+' tokens'+(window?' of '+window.toLocaleString('en-US')+' ('+percent.toFixed(1)+'%)':' · window unknown')+
        at+' · last completed model call (input + cache)'+(known.source?' · '+known.source:'')+stale;
      set(window?'true':'tokens',title,percent);
    }
    // @beacon[
    //   id=opc-pane@managerRowGauge,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=A Claude Manager row's context gauge where its green live dot was (the /m chips' twin): live (writable) is the status-bar pie in a green ring, a plain green dot while no share is known; closed is a gray ring, filled gray with the last share only when the list row's lastContext already carries it. The on-screen session's own last measured figure wins; nothing is fetched for it.,
    // ]
    function managerGauge(row) {
      const open=writable(row),node=element('span',undefined,'manager-gauge'),session=identityOf(row);
      const seen=session && state.lastContext?.identity===session ? state.lastContext : null,listed=row.lastContext;
      const tokens=seen ? seen.occupancy_tokens : listed?.tokens,window=seen ? seen.window_tokens : listed?.window;
      const known=Number.isSafeInteger(tokens) && tokens>=0,percent=known && Number.isSafeInteger(window) && window>0 ? Math.min(100,100*tokens/window) : null;
      const words=(open?'live':'closed')+(percent===null?(known?' · '+tokenCount(tokens)+' context':''):(open?' · ':' · last ')+Math.round(percent)+'%'+(open?' context':''));
      node.dataset.open=String(open);node.dataset.known=String(percent!==null);
      if(percent!==null)node.style?.setProperty?.('--gauge-fill',percent.toFixed(1));
      node.title=words;node.setAttribute?.('role','img');node.setAttribute?.('aria-label',words);
      return node;
    }
    function sourceText() {
      const progress=state.sourceProgress;
      if(!progress || typeof progress!=='object' || !Number.isSafeInteger(progress.indexedBytes) || progress.indexedBytes<0)return '';
      const label=progress.kind==='host-raw'?'raw stream':progress.kind==='native-transcript'?'transcript':'source';
      return megabytes(progress.indexedBytes)+(Number.isSafeInteger(progress.sourceBytes)&&progress.sourceBytes>=0?' / '+megabytes(progress.sourceBytes):'')+' indexed ('+label+')';
    }
    // @beacon[
    //   id=opc-pane@renderClaudeActivity,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Desktop conversation ribbon names the viewed session beside its context pie, with event/read age and context/as-of but no activity words or background count. Keeps real offline/read, lifecycle and settings warnings; OpenCode context remains its validated Manager's own. Phone Chat is separate and unchanged; no display state drives controls.,
    // ]
    function activity(now=Date.now()) {
      haltBanner(now);
      const banner=$('history-activity');if(!banner)return;
      const row=state.session,live=state.liveState ?? row?.liveState,observed=live?.activity;
      const name=$('history-session-name');if(name){const label=row?sessionName(row):'';text(name,label);name.title=label;name.hidden=!label;}
      if(archived(row)) {
        text(banner,'📦 Archived conversation · read-only');banner.dataset.state='archived';banner.dataset.context='none';
        banner.title='Read from 📦 History: prompts, answers and a one-line preview of each tool call as they were archived. Nothing live is observed for it.';
        contextGauge();return;
      }
      if(isOpenCode(row)) {
        const fact=ocEvidence(row),at=fact.at ?? state.ocObservedAt,busy=fact.busy,manager=row?.manager;
        const age=Number.isFinite(at)?Math.max(0,Math.floor((now-at)/1000))+' s ago':'not read yet';
        const label=(fact.error?'last read failed: '+String(fact.error).slice(0,160)+' · last good read '+age:'read '+age)+
          (manager?' · '+openCodeContextText(manager.context)+' · target validated':' · view only');
        text(banner,label);banner.dataset.state=busy===true?'busy':busy===false?'idle':'unknown';
        contextGauge();
        banner.dataset.context=manager?(Number.isSafeInteger(manager.context?.usedTokens)?'measured':'unknown'):'none';
        banner.title=manager?'OpenCode Manager readback (server validated by its creation and folder witness): busy/idle is observed separately, and context is the newest measured assistant call of this Manager only — never a Claude figure or a global quota.':
          'OpenCode reports only busy or idle; following reads it every '+(busy===true?'second':'3 seconds')+'.';
        return;
      }
      let label='Choose a session';
      if(row && role(row)==='subagent')label='Native subagent transcript';
      else if(row && row.source!=='host')label='Stored history';
      else if(row) {
        const offline=!!state.observationError || ['offline','disconnected','exited','stopped','failed'].includes(live?.state);
        if(ended(row))label=sleeping(row)?'sleeping (reconnects on Send)':endedText(row);
        else label=offline?'Offline / observation unavailable':'';
        const at=observed?.lastEventAt;
        // WSL host and Windows browser wall clocks can differ by milliseconds.
        // Small forward skew displays zero; a genuinely future timestamp is unknown.
        label+=(label?' · ':'')+(typeof at==='number'&&Number.isFinite(at)&&at>0&&at*1000<=now+5000?'last event '+Math.max(0,Math.floor((now-at*1000)/1000))+' s ago':'last event time unknown');
        // The host could not confirm this session's model and thinking level; its Sends still go through.
        if(live?.settings_unverified===true && !live.settings_mismatch)label+=' · ⚠ settings unverified';
      }
      text(banner,label+contextText());banner.dataset.state=state.observationError?'unknown':observed?.state || 'unknown';
      contextGauge();
      banner.dataset.context=state.contextReadout?.state || 'none';
      banner.title=typeof state.contextReadout?.label==='string'?state.contextReadout.label:'';
    }
    // A host session halted for a usage-limit lockout (quota-halt.js words it): {halt,line} from liveOf's newest
    // record of this generation, else null. A Manager's line says Send reconnects it; any other role's, its Conductor.
    function haltOf(row,now=Date.now()) {
      const words=globalThis.opcQuotaHalt;
      if(!words || !row || row.source!=='host' || role(row)==='subagent')return null;
      const halt=words.active(liveOf(row),row.generation);
      return halt?{halt,line:words.line(halt,{manager:role(row)==='manager',now})}:null;
    }
    // @beacon[
    //   id=opc-pane@haltBanner,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The conversation's ⛔ usage-limit halt banner under the activity banner: shown while the selected host session's own generation carries quota_halt (one line: window, reset or reset since, what to do), hidden once the next accepted input clears it.,
    // ]
    function haltBanner(now=Date.now()) {
      const node=$('history-halt');if(!node)return;
      const found=isOpenCode(state.session)?null:haltOf(state.session,now),line=found?.line || '';
      if(node.dataset.line===line)return;
      node.dataset.line=line;node.hidden=!line;text(node,line);
    }
    // Sidebar names: the Manager-Session number when the reader found one, else a real
    // title, else a short UUID. The full UUID stays on hover and behind a copy control.
    function sessionName(row) {
      if(isOpenCode(row))return row.title || row.id; // its recorded title, else the bare ses_ id
      if(typeof row?.sessionLabel==='string' && row.sessionLabel)return row.sessionLabel;
      if(typeof row?.title==='string' && row.title && row.title!==row.uuid)return row.title;
      return 'Session '+String(row?.uuid || 'unknown').slice(0,8);
    }
    // The row's second line (line 1 carries the role word). Inside a program the program row
    // already names its Manager Session.
    function sessionStatus(row,inProgram=false) {
      if(isOpenCode(row))return 'opencode · '+(row.busy===true?'busy':row.busy===false?'idle':'activity unknown')+' · view only';
      return `${row.source} · ${row.source==='host' ? ended(row)?(sleeping(row)?'sleeping (reconnects on Send)':'ended'):writable(row)?'live':evidence(row.state ?? row.status) : evidence(row.status)}${writable(row)?' · Send ready':reconnectable(row)?' · Send reconnects it':' · history / context only'}`+
        (typeof row.managerSession==='string' && realRole(row)!=='manager' && !inProgram?' · for Manager-Session-'+row.managerSession:'')+
        (hostActivityFailure(row)?' · activity unknown ('+hostActivityFailure(row)+')':'');
    }
    // A row's name without the title prefixes the oc_* scripts write ("[ap:CORE]-001 ▸ ⚡ quick worker ·", a
    // programless quick fork's "Manager-Session-NNN ▸ ⚡ quick fork worker ·"): the tree, the breadcrumb or
    // the row's own line already says all of that. A fork worker keeps one mark, 🍴: it holds the Manager's
    // conversation, which nothing else on its row says; a forklift (a fork that launches one program) keeps 🚜.
    // A sub-session's prefix (Manager-Session-084-b) goes too.
    function shortTitle(text) {
      const tail=String(text || '').replace(/^\s*\[ap:[A-Z0-9]+\](?:-\d{3})?\s*(?:·|▸)?\s*/,'').replace(/^\s*Manager-Session-\d{1,6}(?:-[A-Za-z])?\s*(?=▸)/,'').replace(/^\s*▸\s*/,'');
      const forklift=/^🚜\s*forklift\s*·\s*([\s\S]*)$/i.exec(tail);
      if(forklift)return forklift[1].trim()?'🚜 '+forklift[1].trim():'';
      const name=tail.replace(/^(?:⚡\s*)?(?:quick\s+)?(?:fork worker|worker|helper|seat)\s*·\s*/i,'').trim();
      return name && /^(?:⚡\s*)?(?:quick\s+)?fork worker\s*·/i.test(tail) ? '🍴 '+name : name;
    }
    // A name chosen with ✎ rename wins exactly as typed; a host seat's script title loses its prefixes.
    function displayName(row) {
      const name=sessionName(row);
      if(isOpenCode(row))return shortTitle(name) || name;
      return row?.nameSource==='chosen' || row?.source!=='host' ? name : shortTitle(name) || name;
    }
    function conductorName(n,title,programTitle,entry) {
      const own=shortTitle(title).replace(/^⚡\s*/,'');
      return (Number.isSafeInteger(n)?'-'+String(n).padStart(3,'0'):'number unknown')+(own && own!==programTitle?' · '+own:'')+(entry==='resume'?' · resumed':'');
    }
    // Line 1 of every row: the role word first, then the name (one line, ellipsis; the full text is on hover).
    function nameLine(roleName,name,waiting=null,waitingText='waiting') {
      const line=element('span',undefined,'session-name'),word=element('span',(ROLE_BADGE[roleName] || 'Unknown')+':','role-word');
      word.dataset.role=roleName;line.append(word,element('span',name,'name-text'));
      // Background Waiting is distinct from busy; a foreground helper-wait flag can accompany the spinner.
      if(waiting){const flag=element('span',waitingText,'waiting-flag');flag.title=waiting;line.append(flag);}
      return line;
    }
    function backendChip(backend) {
      const chip=element('span',backend==='opencode'?'OpenCode':'Claude Code','backend-chip');chip.dataset.backend=backend;
      chip.title=backend==='opencode'?(sidebarOnly?'An OpenCode session: tap for a glance; its Read conversation › opens the whole conversation in Chat':'An OpenCode session: click to read its whole conversation here (view only); 💬 opens a quick glance'):
        sidebarOnly?'A Claude Code session: Read › opens its conversation in Chat (read-only)':'A Claude Code session: click to open its conversation here';return chip;
    }
    const shortModel=model=>typeof model==='string' && model?model.split('/').at(-1):null;
    // A recorded model id as Dan reads it: claude-opus-5-5 → Opus 5.5, claude-fable-5-1 → Fable 5.1,
    // claude-3-5-sonnet-20241022 → Sonnet 3.5. Any other id is shown exactly as recorded.
    function modelName(id) {
      if(typeof id!=='string' || !id)return null;
      const cap=s=>s.charAt(0).toUpperCase()+s.slice(1);
      let m=/^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$/.exec(id);
      if(m)return cap(m[1])+' '+m[2]+(m[3]!==undefined?'.'+m[3]:'');
      m=/^claude-(\d+)(?:-(\d{1,2}))?-([a-z]+)(?:-\d{8})?$/.exec(id);
      return m?cap(m[3])+' '+m[1]+(m[2]!==undefined?'.'+m[2]:''):id;
    }
    const LEVEL_ORDER=['minimal','low','medium','high','xhigh','max'];
    // @beacon[
    //   id=opc-pane@turnFactsParts,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Sidebar model/thinking facts: the models (up to three, else "multiple models") and levels a session's own turns recorded, from the server's fully read transcript ledger only; levels only when every turn recorded one, nothing at all while unread.,
    // ]
    function factsParts(facts) {
      if(!facts || facts.status!=='complete' || facts.modelsComplete!==true || !Array.isArray(facts.models) || !facts.models.length)return null;
      const names=[...new Set(facts.models.map(modelName).filter(Boolean))];
      const rank=level=>{const i=LEVEL_ORDER.indexOf(level);return i<0?LEVEL_ORDER.length:i;};
      const levels=facts.levelsComplete===true && Array.isArray(facts.levels) ? [...new Set(facts.levels.filter(l=>typeof l==='string' && l))].sort((a,b)=>rank(a)-rank(b)) : [];
      return {models:names.length>3?'multiple models':names.join(', '),levels:levels.length<=3?levels:[],
        title:'Recorded by its own turns: '+facts.models.join(', ')+(levels.length?' · thinking '+levels.join(', '):' · thinking level not recorded for every turn')};
    }
    // A block's own label: the model and thinking level recorded on that message, or nothing. An
    // OpenCode block shows its own recorded model and variant (OpenCode's thinking level), never the
    // session's current settings; a missing one says it was not recorded.
    function turnModel(event) {
      if(isOpenCode(state.session)) {
        if(event?.kind!=='assistant')return null;
        const model=typeof event.model==='string' && event.model?event.model:null,variant=typeof event.effort==='string' && event.effort?event.effort:null;
        if(!model && !variant)return null;
        return {text:'('+(model?shortModel(model):'model not recorded')+(variant?' · '+variant:'')+')',
          title:'Recorded on this OpenCode block: '+(model || 'model not recorded')+(variant?' · variant '+variant:' · no variant recorded')+(typeof event.agent==='string' && event.agent?' · agent '+event.agent:'')};
      }
      const name=event?.kind==='assistant'?modelName(event.model):null;if(!name)return null;
      const level=typeof event.effort==='string' && event.effort?event.effort:null;
      return {text:'('+name+(level?' · '+level:'')+')',
        title:'Recorded on this message: '+event.model+(level?' · thinking '+level:' · no thinking level recorded for it')};
    }
    // Eastern time for the sidebar hover, DST-correct: "Thu Oct 15, 7:02 PM ET" (a past year is named).
    function easternTime(seconds) {
      if(typeof seconds!=='number' || !Number.isFinite(seconds) || seconds<=0)return null;
      try {
        const zone='America/New_York',parts={};
        for(const p of new Intl.DateTimeFormat('en-US',{timeZone:zone,weekday:'short',month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true}).formatToParts(new Date(seconds*1000)))parts[p.type]=p.value;
        const year=new Intl.DateTimeFormat('en-US',{timeZone:zone,year:'numeric'}).format(new Date(Date.now()));
        return `${parts.weekday} ${parts.month} ${parts.day}${parts.year!==year?', '+parts.year:''}, ${parts.hour}:${parts.minute} ${String(parts.dayPeriod || '').toUpperCase()} ET`;
      } catch(_) {return null;}
    }
    // @beacon[
    //   id=opc-pane@turnTimes,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The first two sidebar hover lines: first and latest turn in Eastern time, before the existing details; unread or unrecorded ends say unknown. Program estimates disclose their inventory-only basis on the new meters.,
    // ]
    function turnTimes(firstAt,lastAt,why='unknown') {
      const first=easternTime(firstAt),last=easternTime(lastAt);
      return (first?'First: '+first:'first turn: '+why)+'\n'+(last?'Last: '+last:'last turn: '+why);
    }
    // A Claude row's turn times, from its fully read transcript ledger (turnFacts).
    function claudeTurnTimes(row) {
      const f=row?.turnFacts;
      return turnTimes(f?.firstAt,f?.lastAt,f?.status==='scanning'?'unknown (transcript still being read)':'unknown');
    }
    // "2026-10-18 23:52 ET": Eastern (America/New_York, so EDT/EST), 24-hour; unknown → null.
    function easternStamp(seconds) {
      if(typeof seconds!=='number' || !Number.isFinite(seconds) || seconds<=0)return null;
      try {
        const p={};for(const x of new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(seconds*1000)))p[x.type]=x.value;
        return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute} ET`;
      } catch(_) {return null;}
    }
    // @beacon[
    //   id=opc-pane@rowTimes,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The "(first 2026-10-08 21:14 ET · last 2026-10-18 23:52 ET)" parenthetical in Eastern time: on a session row's status line right of its models (its own first and latest turn), and on a program row's meta line (its sessions' earliest start and latest end, from programFacts); an end not known yet reads "—"; nothing at all while neither is known. Wraps only between its two halves, so a narrow list moves it down instead of cutting the models or a time.,
    // ]
    function rowTimes(firstAt,lastAt,hover,basis='Its own first and latest turn (a prompt, answer, tool call or result), Eastern time') {
      const first=easternStamp(firstAt),last=easternStamp(lastAt);
      if(!first && !last)return null;
      // Two unbreakable halves: a row too narrow for the whole wraps between them, never inside a time.
      const span=element('span',undefined,'session-times');
      span.append(element('span','(first '+(first || '—')+' ·','times-part'),element('span',' '),element('span','last '+(last || '—')+')','times-part'));
      span.title=hover+'\n'+basis;
      return span;
    }
    const dollars=value=>typeof value==='number' && Number.isFinite(value) && value>=0?'$'+value.toFixed(value<1?3:2):null;
    // The desktop ⊟'s first-state tooltip (sessions.html carries the same text before the first render).
    const COLLAPSE_TITLE='Close every program and freestanding tree (what is inside them stays as it was) and fold the home sections; Running now stays open. Click again for only what is running';
    // The home section of the freestanding forest (quick workers and helpers outside any program, both backends).
    const QUICK_SECTION=Object.freeze({key:'folder:quick-worker',label:'Quick workers and helpers · quick-worker',
      title:'Quick workers a Manager started outside any program, Claude Code and OpenCode, newest first, each helper under its lead (a helper whose lead is not listed sits at section level, with a note); a transcript with no host record is listed here when its working folder is quick-worker.'});
    // A Manager's forklifts (oc.sh seat quick --fork-self --forklift): their own home section, right after Quick workers.
    const FORKLIFT_SECTION=Object.freeze({key:'folder:forklifts',label:'Forklifts',
      title:'Forks of a Manager that prepare and launch one program (🚜). The program itself is listed under Quick or Full programs, exactly as without a forklift.'});
    const MANAGER_SECTION=Object.freeze({key:'managers',label:'Manager Sessions',
      title:'Manager conversations on Claude Code or OpenCode: by Manager-Session number, highest first, each primary followed by its sub-sessions (a first); names without a number last, newest first. A green dot marks the live ones; it never moves a row. Native registry roles and actual OpenCode root Manager agents decide who is listed; a name only orders, never confers authority.'});
    // Structural home groups: true Managers, quick workers and helpers, the manager-sessions
    // workspace, then one catch-all. The role decides; the working folder only where no role is
    // known (label-less history). Arbitrary folder names never create sections or need an allowlist.
    // @beacon[
    //   id=opc-pane@groupOf,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The home section of a session outside a program: actual OpenCode Manager inventory or native role places Managers together; quick workers/helpers keep their existing section, and folders only classify unknown roles. A Manager's own subagent tree stays with it, never in the quick-worker section.,
    // ]
    function groupOf(row) {
      const real=isOpenCode(row) && row.kind==='manager'?'manager':realRole(row);
      if(real==='manager')return MANAGER_SECTION;
      if(real==='quick-worker' || real==='helper')return QUICK_SECTION;
      const cwd=String(row.cwd || 'unknown'),leaf=cwd.split(/[\\/]/).filter(Boolean).at(-1) || cwd;
      if(leaf==='quick-worker')return QUICK_SECTION;
      if(leaf==='manager-sessions')return {key:'folder:manager-sessions',label:'manager-sessions folder · not Managers',title:'Sessions in the manager-sessions working folder that are not identified as Managers.'};
      return {key:'other',label:'Other',title:'Every other session outside a program or the recognised Manager/quick-worker groups; each row keeps its working folder on hover.'};
    }
    // @beacon[
    //   id=opc-pane@forestSection,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The home section of a freestanding tree, by its root: an OpenCode Manager's subagent tree stays with the Managers; a Claude forklift (title Manager-Session-NNN[-x] ▸ 🚜 forklift ·) gets Forklifts; every other one, Quick workers. The program a forklift launches is placed by its own registry, never under it.,
    // ]
    function forestSection(it) {
      let root=it;while(root.parent)root=root.parent;
      if(root.backend==='claude' && /^\s*Manager-Session-\d{1,6}(?:-[A-Za-z])?\s*▸\s*🚜\s*forklift\s*·/.test(String(root.lin.title || '')))return FORKLIFT_SECTION;
      return root.backend==='opencode' && root.lin.kind==='manager'?MANAGER_SECTION:QUICK_SECTION;
    }
    // @beacon[
    //   id=opc-pane@liveOf,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The host-record merge: strictly newer same-generation activity overrides inventory, then selected/watched-parent polls may win. Observation time is the read's start, never the completion of an older history scan; equal-time inventory rows remain authoritative.,
    // ]
    function liveOf(row) {
      if(!row)return {};
      let listed={...row,...(row.liveState && typeof row.liveState==='object' ? row.liveState : {})},at=state.inventoryAt||0;
      const observed=state.hostActivity.get(row.executionId),fresh=observed?.row;
      if(row.source==='host' && fresh && fresh.generation===row.generation && observed.at>at){listed={...listed,...fresh.liveState};at=observed.at;}
      if(state.session?.sessionRef===row.sessionRef && state.liveState && (state.liveStateAt||0)>=at)return {...listed,...state.liveState};
      if(state.childParent && role(state.childParent)!=='subagent' && role(row)!=='subagent' && identityOf(state.childParent)===identityOf(row) &&
          state.parentLive && (state.parentLiveAt||0)>=at)return {...listed,...state.parentLive};
      return listed;
    }
    function hostActivityFailure(row) {
      const failed=state.hostActivityFailure;if(row?.source!=='host' || archived(row))return null;
      const selected=state.session?.sessionRef===row.sessionRef?state.liveStateAt || 0:0;
      const parent=identityOf(state.childParent)===identityOf(row)?state.parentLiveAt || 0:0;
      const observed=state.hostActivity.get(row.executionId),same=observed?.row?.generation===row.generation?observed:null;
      const at=Math.max(state.inventoryAt || 0,same?.at || 0,selected,parent);
      if(same?.error && same.at>=Math.max(state.inventoryAt || 0,selected,parent))return same.error;
      if(failed && at<=failed.at)return 'host read failed: '+failed.error;
      return !at?'loading current activity':Date.now()-at>OC_EVIDENCE_MS?'last observation stale; refreshing':null;
    }
    // @beacon[
    //   id=opc-pane@activeNow,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Claude busy primitive for sidebarActivity: its OWN turn is open or a Send unanswered in liveOf's newest host record, and that observation is fresh and readable. Failed/missing/stale reads say unknown, never keep an old running mark; streamed activity and subagents never light it.,
    // ]
    // One rule: the Manager's own turn is running, however it started (Dan's Send or a
    // background-task notification), or a Send is still unanswered. The host's
    // primary_turn_open (primary system/init opens a turn, its primary result closes it) is the
    // only turn signal. Streamed activity is not: only the selected view has it, and a stream
    // shape it does not model leaves it stale (183: a notification-started turn ended with
    // 'writing' still showing while a subagent ran), so selected and listed rows disagreed.
    function activeNow(row) {
      if(row?.source!=='host' || ended(row) || hostActivityFailure(row))return false;
      const live=liveOf(row);
      return live.primary_turn_open===true || (Array.isArray(live.pending_message_uuids) && live.pending_message_uuids.length>0);
    }
    // Background tasks still open, from the same newest host evidence as the busy primitive.
    function backgroundCount(row) {
      if(hostActivityFailure(row))return 0;
      const tasks=liveOf(row).background_tasks;
      return tasks && typeof tasks==='object' && !Array.isArray(tasks) && !tasks.status ? Object.keys(tasks).length : 0;
    }
    // @beacon[
    //   id=opc-pane@waitingNow,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Claude waiting primitive for sidebarActivity: not ended, own turn closed, at least one background task still open; host fields from liveOf only, never streamed activity.,
    // ]
    function waitingNow(row) { return row?.source==='host' && !ended(row) && !activeNow(row) && backgroundCount(row)>0; }

    // @beacon[
    //   id=opc-pane@WAIT_RECOGNISERS,
    //   slice_labels=opc-pane,
    //   kind=span,
    //   comment=Owned-script wait recognisers shared by Claude background tasks and OpenCode foreground calls; commandWaits follows literal executable argv and common shell/env/timeout wrappers (not echoed examples), quoted paths/IDs work, and foreground helper waits stay busy rather than claiming a nonblocking doorbell.,
    // ]
    const WAIT_RECOGNISERS=Object.freeze([
      {kind:'doorbell', flag:'🔔 Doorbell', pattern:/\boc(?:_seat\.(?:sh|py)["']?\s+wait|\.sh["']?\s+seat\s+wait)\s+["']?((?:cc-|ses_)[A-Za-z0-9_-]+)/, note:'not blocking: wakes when that session stops'},
      {kind:'map-refresh', flag:'🗺️ Map refresh', pattern:/\boc_map_refresh\.sh["']?\s+["']?([0-9A-Fa-f-]{8,36})/, note:'the governed source-map refresh'},
    ]);
    // Identify our executable argv, not an echoed command or a test fixture string. This is a
    // display recogniser, not a shell evaluator: dynamic variables simply have no named target.
    function commandWaits(command) {
      const found=[],segments=[];let words=[],word='',quote=null,started=false;
      const token=()=>{if(started)words.push(word);word='';started=false;};
      const segment=()=>{token();if(words.length)segments.push(words);words=[];};
      for(let i=0;i<command.length;i++) {
        const c=command[i];
        if(c==='\\' && quote!=="'" && i+1<command.length){word+=command[++i];started=true;continue;}
        if(quote){if(c===quote)quote=null;else word+=c;continue;}
        if(c==='"' || c==="'"){quote=c;started=true;continue;}
        if(c==='#' && !started){while(i<command.length && command[i]!=='\n')i++;segment();continue;}
        if(';|&()\n'.includes(c)){segment();continue;}
        if(/\s/.test(c)){token();continue;}
        word+=c;started=true;
      }
      segment();
      const base=s=>String(s || '').split(/[\\/]/).at(-1),assignment=s=>/^[A-Za-z_]\w*=/.test(s || '');
      for(let args of segments) {
        args=[...args];while(assignment(args[0]) || ['do','then','exec','command'].includes(args[0]))args.shift();
        if(base(args[0])==='env') {
          args.shift();while(args.length && (args[0].startsWith('-') || assignment(args[0]))) {
            const flag=args.shift();if(['-u','--unset','-C','--chdir'].includes(flag))args.shift();
          }
        }
        if(base(args[0])==='timeout') {
          args.shift();while(args[0]?.startsWith('-')){const flag=args.shift();if(['-s','--signal','-k','--kill-after'].includes(flag))args.shift();}
          args.shift();
        }
        if(['bash','sh','python','python3'].includes(base(args[0]))) {
          args.shift();let nested=false;
          while(args[0]?.startsWith('-')){const flag=args.shift();if(/^-[a-z]*c[a-z]*$/.test(flag)){nested=true;break;}}
          if(nested){if(args[0] && args[0]!==command)found.push(...commandWaits(args[0]));continue;}
        }
        const executable=base(args[0]);if(!['oc.sh','oc_seat.sh','oc_seat.py','oc_map_refresh.sh'].includes(executable))continue;
        const invocation=[executable,...args.slice(1)].join(' ');
        for(const recogniser of WAIT_RECOGNISERS){const match=invocation.match(recogniser.pattern);if(match)found.push({recogniser,match});}
      }
      return found;
    }
    // @beacon-close[
    //   id=opc-pane@WAIT_RECOGNISERS,
    // ]
    function waitsOf(row) {
      if(row?.source!=='host')return [];
      const tasks=liveOf(row).background_tasks;
      if(!tasks || typeof tasks!=='object' || Array.isArray(tasks) || tasks.status)return [];
      return Object.values(tasks).map(task=>{
        const command=typeof task?.tool_input?.command==='string'?task.tool_input.command:'';
        const recognised=commandWaits(command)[0];
        return {task,recogniser:recognised?.recogniser || null,match:recognised?.match || null};
      });
    }
    // Newer evidence wins, never the completion time of the slow inventory request. Unknown is
    // not idle: keep the last observation available on hover, but don't spin it forever.
    function ocEvidence(row) {
      if(archived(row))return {busy:false,calls:[],at:null,error:null,stale:false}; // as archived: idle, nothing to observe
      const a=state.ocActivity,now=Date.now();let fact=null;
      if(a){const detail=a.sessions.get(row.id);fact={busy:a.active.has(row.id),at:a.observedAt,error:a.error,calls:detail?.toolStatus==='unavailable'?null:detail?.runningCalls || [],toolStatus:detail?.toolStatus};}
      if(ocSelected(row.id) && Number.isFinite(state.ocObservedAt) && (!fact || state.ocObservedAt>=fact.at))
        fact={busy:state.ocBusyFact,at:state.ocObservedAt,error:state.observationError,calls:state.ocRunningCalls || []};
      if(!fact){const error=ocSelected(row.id) && state.observationError || state.oc.error;
        const at=state.oc.observedAt,stale=state.oc.stale || error || !Number.isFinite(at) || now-at>OC_EVIDENCE_MS;
        return {busy:stale?null:typeof row.busy==='boolean'?row.busy:null,calls:[],at,error};}
      const stale=!!fact.error || !Number.isFinite(fact.at) || now-fact.at>OC_EVIDENCE_MS;
      return {...fact,busy:stale?null:fact.busy,calls:stale?[]:fact.calls,stale};
    }
    function ocBusy(it) { return ocEvidence(it.row).busy===true; }
    function blockingWaits(row) {
      const fact=ocEvidence(row);
      if(fact.busy!==true)return [];
      return (Array.isArray(fact.calls)?fact.calls:[]).flatMap(call=>typeof call.command==='string'?commandWaits(call.command).map(wait=>({...wait,call,blocking:true})):[]);
    }
    function waitTarget(sid) {
      const oc=state.oc.rows.find(r=>r.id===sid),row=state.inventory.find(r=>r.executionId===sid) || oc;
      return {row,oc,name:row?(oc?shortTitle(row.title) || row.title || sid:displayName(row)):sid};
    }
    function waitDescription(wait) {
      const {recogniser,match}=wait;
      if(recogniser.kind==='map-refresh')return '🗺️ map refresh of folder '+match[1].slice(0,8)+(wait.blocking?' · blocking foreground call':'')+' · since '+(clock(wait.call?.time ?? wait.task?.started_at) || 'not recorded');
      const sid=match[1],{row,oc,name}=waitTarget(sid),word=row?(oc?ROLE_BADGE[nodeRole({backend:'opencode',row})]:roleLabel(row)):'not listed';
      const core=row?.lineage?.program || row?.program,free=!!state.tree?.byId.get(sid)?.home.forest;
      return '🔔 waits on '+(row || wait.blocking?name:'not listed')+' · '+word+(wait.blocking?' · '+sid:'')+' · '+(core?'[ap:'+core+']':free?'no program':'program not recorded')+' · '+
        (row?.socket?'socket '+row.socket:'socket not recorded')+' — '+(wait.blocking?'blocking foreground wait: this session stays busy until the command returns':recogniser.note);
    }
    // Separate links below the row, not interactive controls nested inside its open button.
    function waitLinks(waits) {
      const seen=new Set(),box=element('div',undefined,'session-waits');
      for(const wait of waits) {
        if(wait.recogniser?.kind!=='doorbell' || seen.has(wait.match[1]))continue;
        const sid=wait.match[1];seen.add(sid);const {row,oc,name}=waitTarget(sid);
        const link=element('span','↳ '+name,'wait-target');link.title=waitDescription(wait);
        const open=sid.startsWith('ses_')?{backend:'opencode',id:sid,title:row?.title || sid,label:'OpenCode'}:row?claudeOpen(row,displayName(row)):null;
        if(sidebarOnly && open) {
          const chip=readChip(open,'↳ '+name,'wait-target');chip.title=link.title;box.append(chip);
        } else {
          if(open)pressable(link,()=>{showPane('conversation');sid.startsWith('ses_')?void openOpenCode(oc || sid):void browse(row);});
          box.append(link);
        }
      }
      return seen.size?box:null;
    }
    const OC_WAITING_NOTE='OpenCode reports only busy or idle (/api/session/active): a parent can read idle while its background subagents run, so this "waiting" is a best guess from a busy session started under it.';
    // @beacon[
    //   id=opc-pane@sidebarActivity,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Fresh per-render activity for every program and session; OpenCode uses independent timestamped activity evidence (foreground waits stay busy), Claude uses host turn/task evidence; only a known-idle OpenCode parent may get the labelled child-wait inference.,
    // ]
    function sidebarActivity(tree,roots) {
      const map=new Map(),parents=new Set();
      const entry=(own='quiet',kids={busy:0,waiting:0},agents=0)=>({own,kids,agents,any:own!=='quiet' || kids.busy+kids.waiting+agents>0});
      const nativeAgents=parent=>{
        const identity=identityOf(parent),running=runningAgents(parent);parents.add(identity);
        const rows=[...state.inventory.filter(r=>role(r)==='subagent' && r.parentUuid===parent.uuid),
          ...(state.childCache.get(parent.sessionRef)?.rows || []),...(identityOf(state.childParent)===identity?state.children:[])];
        const ids=new Set([...running,...rows.map(r=>r.agentId).filter(id=>typeof id==='string')]);
        for(const id of ids)map.set('a:'+identity+':'+id,entry(running.has(id)?'busy':'quiet'));
        return running.size;
      };
      const sumKids=children=>{
        const kids={busy:0,waiting:0};
        for(const child of children){const e=map.get(child.key);kids.busy+=Number(e.own==='busy')+e.kids.busy+e.agents;kids.waiting+=Number(e.own==='waiting')+e.kids.waiting;}
        return kids;
      };
      const visit=it=>{
        for(const child of it.children)visit(child);
        const own=it.backend==='opencode' ? (ocBusy(it)?'busy':ocEvidence(it.row).busy===false && it.children.some(c=>map.get(c.key).own!=='quiet')?'waiting':'quiet') :
          activeNow(it.row)?'busy':waitingNow(it.row)?'waiting':'quiet';
        map.set(it.key,entry(own,sumKids(it.children),it.backend==='claude'?nativeAgents(it.row):0));
      };
      for(const p of tree.programs){for(const it of p.top)visit(it);map.set(p.key,entry('quiet',sumKids(p.top)));}
      for(const it of tree.forest?.top || [])visit(it);
      for(const row of roots)map.set('r:'+identityOf(row),entry(activeNow(row)?'busy':waitingNow(row)?'waiting':'quiet',undefined,nativeAgents(row)));
      // The watched native list may be detached (its parent is not in this inventory).
      if(state.childParent && !parents.has(identityOf(state.childParent)))nativeAgents(state.childParent);
      return map;
    }
    function activityOf(key) { return state.activity.get(key) || {own:'quiet',kids:{busy:0,waiting:0},agents:0,any:false}; }
    function rollupBadge(entry,{treeHidden,agentsHidden}) {
      const busy=(treeHidden?entry.kids.busy:0)+(agentsHidden?entry.agents:0),waiting=treeHidden?entry.kids.waiting:0;
      if(!busy && !waiting)return null;
      const text=[busy?busy+' busy':null,waiting?waiting+' waiting':null].filter(Boolean).join(' · '),badge=element('span',text,'rollup-badge');
      badge.dataset.state=waiting?'waiting':'busy';
      badge.title='Under this row: '+text+'. Busy = the yellow spinner (a turn is open). Waiting = its turn is closed while a background task runs (not blocked).';
      // Backend provenance only: counts/states still come exclusively from the activity entries.
      if(treeHidden && [...(state.tree?.byId.values() || [])].some(it=>it.backend==='opencode' && activityOf(it.key).own!=='quiet' &&
          (state.tree.ancestors.get(it.key) || []).some(key=>activityOf(key)===entry)))badge.title+=' OpenCode waiting is a best guess.';
      return badge;
    }
    // A session's identity that survives a pane-server restart (its handles do not): the host
    // execution, else the transcript (source + UUID), a subagent by its parent and agent. An
    // OpenCode conversation is its ses_ id, decided before any Claude role logic; an archived one on screen is its key.
    function identityOf(row) {
      if(!row)return null;
      if(row.backend==='history')return 'history:'+row.key;
      if(isOpenCode(row))return 'opencode:'+row.id;
      if(role(row)==='subagent')return typeof row.agentId==='string'?'sub:'+row.parentUuid+':'+row.agentId:'ref:'+row.sessionRef;
      return row.source==='host' && typeof row.executionId==='string' ? 'host:'+row.executionId : 'native:'+row.source+':'+row.uuid;
    }
    // The row whose conversation is on screen, also after a restart re-issued every handle.
    function isSelected(row) { return !!state.session && !!row && isOpenCode(state.session)===isOpenCode(row) && (state.session.sessionRef===row.sessionRef || identityOf(state.session)===identityOf(row)); }
    // Stars are durable unit membership, never the selected Manager's "pinned" placement.
    function starKey(row) {
      if(isOpenCode(row) && /^ses_[A-Za-z0-9]+$/.test(row.id || ''))return 'session:opencode:'+row.id;
      const uuid=role(row)==='subagent'?row.parentUuid:row.uuid;
      if(!CLAUDE_UUID.test(uuid || ''))return null;
      if(role(row)==='subagent')return /^[A-Za-z0-9_-]+$/.test(row.agentId || '')?'session:claude:cc-'+uuid+'/agent-'+row.agentId:null;
      return 'session:claude:cc-'+uuid;
    }
    function starButton(key,program=false) {
      if(!key || state.starsAvailable!==true)return null;
      const on=Object.hasOwn(state.stars,key),button=element('span',on?'★':'☆','session-star');
      button.classList.toggle('starred',on);button.dataset.star=key;
      button.title=(on?'Unstar':'Star')+' this '+(program?'program · shown in Favorite programs when not running':'session')+' · a star keeps its whole unit in the live list';
      button.setAttribute('aria-label',(on?'Unstar':'Star')+' '+(program?'program':'session'));button.setAttribute('aria-pressed',String(on));
      pressable(button,()=>void toggleStar(key));return button;
    }
    async function loadStars() {
      if(state.starsBusy || state.starsAvailable===false)return;
      state.starsBusy=true;const revision=state.starRevision;
      try {
        const data=await json('/api/pins');
        if(data?.schemaVersion!==1 || !data.pins || typeof data.pins!=='object' || Array.isArray(data.pins))throw new Error('invalid pins schema');
        state.starsAvailable=true;if(revision===state.starRevision && !state.starQueues.size)state.stars=data.pins;
      } catch(e) {if(e.code===404){state.starsAvailable=false;state.stars={};}}
      finally {state.starsBusy=false;inventory();}
    }
    // @beacon[
    //   id=opc-pane@toggleStar,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Toggle one durable session/program/subagent star without a confirmation or cap; revisions keep overlapping writes/reads honest, the server's one SessionPins store owns persistence, and an older pane's 404 hides stars without hiding the list.,
    // ]
    async function toggleStar(key) {
      const previous=state.stars[key],on=!Object.hasOwn(state.stars,key),revision=++state.starRevision;
      state.starVersions.set(key,revision);
      if(on)state.stars[key]={pinned_at:Date.now()/1000};else delete state.stars[key];inventory();
      const write=(state.starQueues.get(key) || Promise.resolve()).catch(()=>{}).then(()=>json('/api/pins',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({schemaVersion:1,key,pinned:on})}));
      state.starQueues.set(key,write);
      try {
        const data=await write;
        if(data?.schemaVersion!==1 || !data.pins || typeof data.pins!=='object')throw new Error('invalid pins schema');
        if(state.starVersions.get(key)===revision){if(Object.hasOwn(data.pins,key))state.stars[key]=data.pins[key];else delete state.stars[key];}
        void list();void ocList();
      } catch(e) {
        if(e.code===404){state.starsAvailable=false;state.stars={};}
        else {if(state.starVersions.get(key)===revision){if(previous)state.stars[key]=previous;else delete state.stars[key];}
          message('sessions-status','Star was not saved: '+e.message,true);}
      }
      if(state.starQueues.get(key)===write)state.starQueues.delete(key);
      inventory();
    }
    // An OpenCode tree row (an /api/opencode/programs row, or its 📦 History copy) is outlined by its own ses_ id.
    function ocSelected(id) { return (isOpenCode(state.session) || state.session?.archivedBackend==='opencode') && state.session.id===id; }
    function managerNames(row) { return isOpenCode(row)?[row.title]:[row?.sessionLabel,row?.title]; }
    function isManagerRow(row) {
      return !!row && !archived(row) && (isOpenCode(row)?!row.parentId && openCodeManagerClaim(row):realRole(row)==='manager' && role(row)!=='subagent');
    }
    // The top group is a browsing preference, never the editor's Send target. Explicit Manager choices
    // stick until another Manager is chosen; with none, every new inventory can improve the newest-index fallback.
    function selectedManager() {
      if(state.archive)return null;
      const row=sidebarOnly?state.sidebarManager:state.session;
      if(isManagerRow(row))state.topManager=row;
      if(state.topManager)return state.topManager;
      if(state.fallbackManager)return state.fallbackManager;
      const rows=[...state.inventory,...state.oc.rows,...(state.managerFamilyRows?.claude || []),...(state.managerFamilyRows?.opencode || [])].filter(isManagerRow);
      const key=r=>({names:managerNames(r),created:r.createdAt,id:identityOf(r)}),compare=globalThis.opcManagerOrder?.compare;
      rows.sort((a,b)=>compare?compare(key(a),key(b)):(b.createdAt || 0)-(a.createdAt || 0));
      return state.fallbackManager=rows[0] || null;
    }
    function sameManagerFamily(row,selected=selectedManager()) {
      if(!selected)return false;
      const parse=globalThis.opcManagerOrder?.parse,a=parse?.(...managerNames(row)),b=parse?.(...managerNames(selected));
      return a && b?a.index===b.index:identityOf(row)===identityOf(selected);
    }
    function familyRows(rows,opencode=false) {
      return (rows || []).filter(row=>(opencode?openCodeManagerClaim(row):realRole(row)==='manager' && role(row)!=='subagent') && sameManagerFamily(row));
    }
    // @beacon[
    //   id=opc-pane@selectManagerFamily,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Phone Programs keeps the last chosen Manager family while reading other sessions; the already-read full picker supplies every sibling and the newest-index fallback when no Manager is chosen. No extra reads, targeting, draft, star or fold changes.,
    // ]
    function selectManagerFamily(row,rows=[]) {
      if(!sidebarOnly)return;
      const signature=JSON.stringify([row,rows]);if(signature===state.sidebarManagerSignature)return;
      state.sidebarManagerSignature=signature;
      if(isManagerRow(row))state.sidebarManager=row;
      state.managerFamilyRows={claude:rows.filter(r=>!isOpenCode(r) && isManagerRow(r)),opencode:rows.filter(r=>isOpenCode(r) && isManagerRow(r))};
      inventory();
    }
    // @beacon[
    //   id=opc-pane@loadManagerFamily,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Desktop reads existing unwindowed Manager summaries on the list cadence, keeping every numbered family available for sticky browsing and newest-index reload fallback, including closed old siblings. Both backends publish independently; no transcripts, targeting or universe changes, and superseded sources/History cannot publish.,
    // ]
    async function loadManagerFamily() {
      if(sidebarOnly || state.archive)return;
      const source=state.sourceGeneration,key='all';
      if(state.familyLoad?.key===key || state.familyRead?.key===key && Date.now()-state.familyRead.at<10000)return;
      const load={key};state.familyLoad=load;
      const current=()=>!state.archive && source===state.sourceGeneration;
      const previous=state.managerFamilyRows || {claude:[],opencode:[]};
      await Promise.all([
        (async()=>{try {
          const rows=[],seen=new Set();let cursor=null;
          do {
            const data=await json('/api/claude/sessions?details=0&native=0&role=Manager'+(cursor?'&cursor='+encodeURIComponent(cursor):''));envelope(data);
            if(!current())return;
            if(!Array.isArray(data.sessions) || data.hasMore && (!ref(data.nextCursor) || seen.has(data.nextCursor)))throw new Error('invalid Manager inventory');
            rows.push(...data.sessions);cursor=data.hasMore?data.nextCursor:null;if(cursor)seen.add(cursor);
          } while(cursor);
          if(current()){state.managerFamilyRows={...(state.managerFamilyRows || previous),claude:rows.filter(isManagerRow)};inventory();}
        } catch(_){/* Keep the last family summaries; ordinary inventory/activity still runs independently. */}})(),
        (async()=>{try {
          const data=await json('/api/opencode/programs');
          if(current() && data.status==='complete' && Array.isArray(data.sessions)){
            state.managerFamilyRows={...(state.managerFamilyRows || previous),opencode:data.sessions.filter(isManagerRow)};inventory();}
        } catch(_){/* An unavailable backend must not hide the other half of the family. */}})()
      ]);
      if(current()){state.familyRead={key,at:Date.now()};if(!state.topManager)state.topManager=selectedManager();}
      if(state.familyLoad===load)state.familyLoad=null;
    }
    function programStarred(entry) { return !state.archive && entry.key.startsWith('p:') && Object.hasOwn(state.stars,'program:'+entry.core); }
    // @beacon[
    //   id=opc-pane@placeOf,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The sole all-view placement decision: remembered Manager family (newest index until one is chosen) pinned independently of the viewed worker, active or pointer-held work in Running now, starred programs in Favorite programs, everything else home. Session stars and History never create favorites.,
    // ]
    function placeOf(entry) {
      if(($('sessions-role').value || 'all')==='all' && entry.node?.backend==='opencode' && !entry.node.parent && entry.node.lin.kind==='manager' && sameManagerFamily(entry.node.row))return 'pinned';
      if(($('sessions-role').value || 'all')==='all' && entry.key.startsWith('r:') &&
        realRole(entry.row)==='manager' && role(entry.row)!=='subagent' && sameManagerFamily(entry.row))return 'pinned';
      if(activityOf(entry.key).any)return 'running';
      if(state.sidebarHover && state.placed.get(entry.key)==='running')return 'running';
      if(($('sessions-role').value || 'all')==='all' && programStarred(entry))return 'favorite';
      return 'home';
    }
    // @beacon[
    //   id=opc-pane@setSidebarHover,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Holds running placements while the pointer is inside the list; leaving immediately rebuilds from current activity without the hold. The phone page wires it for a mouse only (a touch lift is a pointerleave, and that rebuild swallowed iOS taps).,
    // ]
    function setSidebarHover(on) { state.sidebarHover=!!on;if(!on)inventory(); }
    function childButton(row,parent) {
      const tail=typeof row.title==='string' && row.title.includes(' ▸ ') ? row.title.slice(row.title.indexOf(' ▸ ')+3) : row.title;
      const type=row.agentType || String(tail || 'subagent').split(' · ')[0],desc=row.description || String(tail || '').split(' · ').slice(1).join(' · ');
      // What the subagent is doing leads (bright); dim below, its agent name and the model and thinking
      // level its own turns recorded (manager-quick-high → "manager-quick · Opus 5.5 · high": the name's
      // level suffix goes only when it is exactly the one level recorded). Nothing recorded: the name alone.
      const facts=factsParts(row.turnFacts),levels=facts?.levels || [];
      const base=levels.length===1 && type.endsWith('-'+levels[0])?type.slice(0,-(levels[0].length+1)):type;
      const sub=facts?[desc?base:null,facts.models,levels.join(', ')].filter(Boolean).join(' · '):desc?type:'';
      const button=element(sidebarOnly?'div':'button',undefined,sidebarOnly?'sidebar-child':undefined);button.type='button';button.append(element('span',desc || type,'child-title'));
      if(sub)button.append(element('span',sub,'child-sub'));
      const times=rowTimes(row.turnFacts?.firstAt,row.turnFacts?.lastAt,claudeTurnTimes(row));
      if(times){times.classList.add('child-times');button.append(times);}
      button.title=claudeTurnTimes(row)+'\n'+(row.title || type)+(facts?'\n'+facts.title:'')+' · native subagent · view-only; transcript growth is not task acceptance';
      button.setAttribute('aria-pressed',String(isSelected(row)));
      if(activityOf('a:'+identityOf(parent)+':'+row.agentId).own==='busy'){button.classList.add('active');button.title+=' · running now';}
      if(readable(row)){button.classList.add('readable');button.append(readChip(claudeOpen(row,desc || type)));}
      button.dataset.anchor='a:'+identityOf(parent)+':'+(row.agentId || row.sessionRef);
      const star=starButton(starKey(row));if(star)button.children[0].append(star);
      if(!sidebarOnly)button.onclick=()=>{showPane('conversation');void browse(row);};return button;
    }
    // Phone Programs only: Read › asks the phone page around this frame to open the session read-only
    // in its Chat (m.js accepts it only from this frame and origin). The desktop sidebar never shows it.
    function readChip(session,label='Read ›',cls='session-read') {
      const chip=element('span',label,cls);chip.title='Read this conversation in Chat (read-only)';chip.setAttribute('aria-label',chip.title);
      pressable(chip,()=>{if(view?.parent && view.parent!==view)view.parent.postMessage({type:'open-session',schemaVersion:1,session:historyOpen(session) || session},origin);});
      return chip;
    }
    function claudeOpen(row,title) {
      const sub=role(row)==='subagent';
      return {backend:'claude',sessionRef:row.sessionRef,scopeRef:(row.scopes.find(s=>s.label==='main') || row.scopes[0]).scopeRef,
        executionId:typeof row.executionId==='string'?row.executionId:null,uuid:row.uuid || null,source:row.source || null,
        parentUuid:sub?row.parentUuid || null:null,agentId:sub && typeof row.agentId==='string'?row.agentId:null,title,label:ROLE_BADGE[realRole(row)] || 'Session'};
    }
    // The 📦 History row a sidebar descriptor or a glance id names (a Claude sessionRef or uuid, an OpenCode ses_ id) while
    // History is the list's source; null otherwise.
    function historyRow(id) {
      if(!state.archive || typeof id!=='string' || !id)return null;
      return (id.startsWith('ses_')?state.oc.rows.find(r=>r.id===id):state.inventory.find(r=>r.sessionRef===id || (r.uuid===id && role(r)!=='subagent'))) || null;
    }
    // Phone Read ›: an archived row opens read-only through the History reader (m.js READERS.history), never a live one.
    function historyOpen(session) {
      const row=historyRow(session?.backend==='opencode'?session.id:session?.sessionRef),key=row?.archived?.key;
      if(!archived(row) || typeof key!=='string' || !ref(row.sessionRef) || !ref(row.scopes?.[0]?.scopeRef))return null;
      return {backend:'history',key,sessionRef:row.sessionRef,scopeRef:row.scopes[0].scopeRef,title:session.title || displayName(row),label:session.label || 'History'};
    }
    function readable(row) { return sidebarOnly && ref(row?.sessionRef) && Array.isArray(row.scopes) && !!row.scopes[0]?.scopeRef; }
    function pressable(node,action) {
      node.setAttribute('role','button');node.setAttribute('tabindex','0');
      node.onclick=event=>{event?.stopPropagation?.();action();};
      node.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault?.();event.stopPropagation?.();action();}};
    }
    function copyText(node,value) {
      const done=ok=>{node.classList.toggle('copied',ok);text(node,ok?'✓':'!');setTimeout(()=>{node.classList.toggle('copied',false);text(node,'⧉');},1200);};
      const fallback=()=>{try{const area=doc.createElement('textarea');area.value=value;doc.body.append(area);area.select();const ok=doc.execCommand('copy');area.remove();done(ok);}catch(_){done(false);}};
      try{const clip=view?.navigator?.clipboard;if(clip?.writeText)clip.writeText(value).then(()=>done(true),fallback);else fallback();}catch(_){fallback();}
    }
    // @beacon[
    //   id=opc-pane@peekChildren,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Discover a visible capable row's native children once through the optional-detail queue (or refresh on deliberate expansion), so a capability alone never draws an empty expander. Parent-scoped summaries only, never a poll or target; errors keep the known children.,
    // ]
    async function peekChildren(row) {
      if(archived(row))return; // its archived children are already on its page, nested under it by parentUuid
      const key=row.sessionRef,prior=state.childCache.get(key);state.childCache.set(key,{rows:prior?.rows || [],loading:true});inventory();
      try {
        const data=await json('/api/claude/subagents?'+new URLSearchParams({sessionRef:key}));envelope(data);
        if(!Array.isArray(data.sessions) || data.sessions.some(r=>!ref(r.sessionRef)||!r.parentUuid||r.role!=='subagent'||!Array.isArray(r.scopes)))throw new Error('invalid subagent inventory schema');
        state.childCache.set(key,{rows:data.sessions,more:!!data.hasMore});
      } catch(e) {state.childCache.set(key,{rows:prior?.rows || [],error:e.message});}
      inventory();
    }
    // ------------------------------------------------------------- program tree (lineage)
    // @beacon[
    //   id=opc-pane@programFacts,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=One fresh program aggregate from listed members only: honest Claude/OpenCode endpoints (the program row's first/last parenthetical and hover), first-turn age fading over the host-reported list window, and a square-root duration scale capped at four days. Unknown endpoints stay unknown; no reads or carried state.,
    // ]
    function programFacts(members,now,listWindowDays) {
      const stamp=v=>typeof v==='number' && Number.isFinite(v) && v>0 && Number.isFinite(new Date(v*1000).getTime())?v:null;
      let first=Infinity,last=-Infinity,missingStarts=0,missingEnds=0,scanning=0,claude=0,createdStarts=0,opencode=0;
      for(const {backend,row} of members) {
        let start,end;
        if(backend==='claude') {
          claude++;start=stamp(row.turnFacts?.firstAt);
          if(start===null){start=stamp(row.createdAt);if(start!==null)createdStarts++;}
          end=row.turnFacts?.status==='complete'?stamp(row.turnFacts.lastAt):null;
          if(end===null && row.turnFacts?.status==='scanning')scanning++;
        } else {
          opencode++;start=stamp(row.createdAt);
          const ends=[stamp(row.updatedAt),stamp(row.idleAt)].filter(v=>v!==null);
          end=ends.length?Math.max(...ends):null;
        }
        // Missing starts/ends cannot be silently omitted from a min/max across the whole program.
        if(start===null)missingStarts++;else first=Math.min(first,start);
        if(end!==null && start!==null && end<start)end=null;
        if(end===null)missingEnds++;else last=Math.max(last,end);
      }
      const firstAt=missingStarts?null:first,lastAt=missingEnds?null:last;
      const duration=firstAt!==null && lastAt!==null?lastAt-firstAt:null;
      // The existing 18px track: at least 2px when known, square-root growth through four days.
      const durationFraction=duration===null?0:Math.max(1/9,Math.sqrt(Math.min(duration,4*86400)/(4*86400)));
      const short=n=>n.toFixed(1).replace(/\.0$/,'');
      const durationLabel=duration===null?'—':duration<60?'<1m':duration<3600?Math.floor(duration/60)+'m':duration<86400?short(duration/3600)+'h':short(duration/86400)+'d';
      // Eastern calendar days, not 24-hour spans: yesterday stays yesterday over a DST change.
      const day=seconds=>{const parts={};for(const p of new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'numeric',day:'numeric'}).formatToParts(new Date(seconds*1000)))parts[p.type]=p.value;return Date.UTC(Number(parts.year),Number(parts.month)-1,Number(parts.day))/86400000;};
      const ageDays=firstAt===null || firstAt>now?null:day(now)-day(firstAt);
      const nameFade=ageDays===null || typeof listWindowDays!=='number' || !Number.isFinite(listWindowDays) || listWindowDays<=0?null:Math.min(1,ageDays/listWindowDays);
      const ageLabel=ageDays===null?'—':ageDays===0?'today':ageDays<7?ageDays+'d':Math.floor(ageDays/7)+'w';
      const ageBucket=ageDays===null?'unknown':ageDays===0?'today':ageDays===1?'yesterday':ageDays<7?'recent':'old';
      const why='unknown'+(scanning?' ('+scanning+' transcript'+(scanning===1?'':'s')+' still being read)':'');
      const basis=[claude?'Claude Code: own first/latest turn ledger'+(createdStarts?'; creation stamp used for '+createdStarts+' missing first turn'+(createdStarts===1?'':'s')+' (estimate)':'')+'; an unread or missing end stays unknown.':null,
        opencode?'OpenCode: session creation to max(updated, idle) stamps — estimates of turns, not exact message times.':null,
        missingStarts?missingStarts+' member start(s) unknown.':null,missingEnds?missingEnds+' member end(s) '+why+'.':null,
        'Listed sessions only; duration ends at latest turn, never the seal or the current time.'].filter(Boolean).join('\n');
      return {firstAt,lastAt,duration,durationFraction,durationLabel,ageDays,ageLabel,ageBucket,nameFade,why,basis};
    }

    // @beacon[
    //   id=opc-pane@lineageTree,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Both backends' launch-edge trees: program Conductors → seats → helpers plus freestanding roots. Program names use the newest Conductor's stored label directly, with the existing orphan label/core fallback and no title enrichment; actual OpenCode Managers keep their own home. Unlisted parents never hide a row; facts/activity keep their source basis.,
    // ]
    function lineageTree(claudeRows,ocRows=[],listWindowDays=null) {
      const programs=new Map(),byId=new Map(),items=[],coreOf=value=>/^[A-Z0-9]{6}$/.test(value || '')?value:null;
      const program=core=>{if(!programs.has(core))programs.set(core,{core,key:'p:'+core,slots:new Map(),top:[],members:[]});return programs.get(core);};
      // The freestanding forest's pseudo-home: no program row of its own; its roots sit in their section.
      const forest={key:'forest',forest:true,groupKey:QUICK_SECTION.key,slots:new Map(),top:[],members:[]};
      // Claude: a program's lineage, or a freestanding one (labels quick, no program). OpenCode: the server
      // lists program sessions, freestanding quick ones and actual Manager roots; their no-program trees are freestanding.
      for(const row of claudeRows)if(row?.source==='host' && role(row)!=='subagent' && typeof row.executionId==='string' &&
          (coreOf(row.lineage?.program) || (row.lineage && !row.lineage.program && row.lineage.quick===true)))
        items.push({backend:'claude',row,lin:row.lineage,id:row.executionId});
      for(const row of ocRows)if(typeof row?.id==='string' && (coreOf(row.program) || !row.program))items.push({backend:'opencode',row,lin:row,id:row.id});
      for(const it of items){if(byId.has(it.id))continue;byId.set(it.id,it);
        Object.assign(it,{key:'s:'+it.id,children:[],parent:null,note:null,created:typeof it.row.createdAt==='number'&&Number.isFinite(it.row.createdAt)?it.row.createdAt:0});
        it.home=coreOf(it.lin.program)?program(it.lin.program):forest;it.home.members.push(it);}
      const nodes=[...byId.values()],number=v=>Number.isSafeInteger(v) && v>0?v:null,byCreated=(a,b)=>a.created-b.created;
      const label=n=>'Conductor -'+String(n).padStart(3,'0'),short=id=>String(id).startsWith('cc-')?String(id).slice(0,11):String(id).slice(0,16)+'…';
      for(const it of nodes.filter(x=>x.lin.kind==='conductor').sort((a,b)=>(number(a.lin.conductorN)||1e9)-(number(b.lin.conductorN)||1e9) || byCreated(a,b))) {
        const n=number(it.lin.conductorN);
        if(n && !it.home.slots.has(n)){it.home.slots.set(n,it);it.slot=n;}else if(!n)it.note='Conductor number not recorded';
      }
      for(const it of nodes) {
        if(it.lin.kind==='conductor')continue;
        const lin=it.lin,p=it.home,pid=lin.parentSeat || lin.parentId;let note=null;
        // The only direct-id edge: a helper's lead (or an OpenCode child's parent).
        if(pid){const lead=byId.get(pid);if(lead && lead!==it && lead.home===p){it.parent=lead;continue;}note='started by '+short(pid)+', not listed';}
        if(p.forest){it.note=note;continue;} // outside a program there are no Conductor edges
        let n=number(lin.conductorN);
        if(!n && lin.conductorId){const c=byId.get(lin.conductorId);if(c?.lin.kind==='conductor')n=number(c.lin.conductorN);}
        if(n){const slot=p.slots.get(n);if(slot)it.parent=slot;else note=note || 'started by '+label(n)+(lin.conductorId?' ('+short(lin.conductorId)+')':'')+', not listed';it.note=note;continue;}
        if(lin.conductorId){it.note=note || 'started by '+short(lin.conductorId)+', not listed';continue;}
        // oc_quick starts a program's first worker before its Conductor (older launches stamped no
        // number): that worker belongs to row 001, never to a later Conductor.
        const first=p.slots.get(1);
        if(first && lin.quick===true && !(it.created && first.created && it.created>first.created+120)){it.parent=first;it.note=note;continue;}
        it.note=note || 'Conductor number not recorded';
      }
      // A chain must end at its program: a loop (never stamped by the scripts) is cut, not followed.
      for(const it of nodes){let at=it.parent,steps=0;while(at && at!==it && steps<12){at=at.parent;steps++;}if(at){it.parent=null;it.note='lineage loop: shown at program level';}}
      for(const it of nodes)(it.parent?it.parent.children:it.home.top).push(it);
      for(const it of nodes)it.children.sort(byCreated);
      const titleOf=it=>it.backend==='claude'?(it.lin.title || it.row.title):it.row.title,now=Date.now()/1000;
      for(const p of programs.values()) {
        p.top.sort((a,b)=>(a.slot?0:1)-(b.slot?0:1) || (a.slot||0)-(b.slot||0) || byCreated(a,b));
        const newest=[...p.slots.values()].sort((a,b)=>b.slot-a.slot)[0] || p.members.filter(m=>m.lin.kind==='conductor').sort(byCreated).at(-1);
        const first=[...p.members].sort(byCreated).find(m=>m.lin.kind!=='conductor');
        p.conductorListed=!!newest;
        p.title=shortTitle(newest?titleOf(newest):first?titleOf(first):'').replace(/^⚡\s*/,'') || '[ap:'+p.core+']';
        const flag=p.members.map(m=>m.lin.quickProgram).find(v=>typeof v==='boolean');
        p.kind=flag!==undefined?(flag?'quick':'full'):newest?(/^⚡/.test(shortTitle(titleOf(newest)))?'quick':'full'):p.members.some(m=>m.lin.quick===true)?'quick':'full';
        p.managerSession=newest?.lin.managerSession || p.members.map(m=>m.lin.managerSession).find(v=>typeof v==='string') || null;
        p.newest=Math.max(0,...p.members.map(m=>m.created));p.count=p.members.length;
        p.facts=programFacts(p.members,now,listWindowDays);
      }
      // Ancestors are the expanders above a node: its program row, then its tree parents (a forest node: parents only).
      const ancestors=new Map();
      for(const it of nodes){const keys=[];for(let at=it.parent;at;at=at.parent)keys.unshift(at.key);if(!it.home.forest)keys.unshift(it.home.key);ancestors.set(it.key,keys);}
      return {programs:[...programs.values()],forest,byId,ancestors};
    }
    // A tree node and every node under it.
    function subtree(it) { const out=[it];for(const child of it.children)out.push(...subtree(child));return out; }
    // Program liveness stays distinct from activity: Claude not ended, OpenCode busy in this render's map.
    function memberLive(it) { return it.backend==='opencode' ? activityOf(it.key).own==='busy' : !ended(it.row) && !hostActivityFailure(it.row); }
    function nodeRole(it) {
      if(it.backend==='claude')return realRole(it.row);
      const r=it.row;
      return r.kind==='manager'?'manager':r.kind==='conductor'?'conductor':r.kind==='helper'?'helper':r.kind==='subagent'?(it.parent?.lin.kind==='conductor'?'worker':'subagent'):
        r.quick===true?'quick-worker':r.seatRole==='lead'?'lead-seat':'worker';
    }
    function nodeName(it) {
      if(it.lin.kind==='conductor' && !(it.backend==='claude' && it.row.nameSource==='chosen'))
        return conductorName(it.lin.conductorN,it.backend==='claude'?(it.lin.title || it.row.title):it.row.title,it.home.title,it.lin.entryAction);
      return it.backend==='claude'?displayName(it.row):shortTitle(it.row.title) || it.row.title || it.row.id;
    }
    // One line under a filtered (flattened) row: where it sits in its program, or in the freestanding forest.
    function breadcrumb(it) {
      const chain=[];for(let at=it.parent;at;at=at.parent)chain.unshift(at);
      const home=it.home.forest?({managers:'Manager Sessions','folder:forklifts':'Forklifts'}[forestSection(it).key] || 'Quick workers'):'';
      return [it.home.forest?home:'[ap:'+it.home.core+']',...chain.map(a=>a.lin.kind==='conductor'?'-'+String(a.lin.conductorN || '?').padStart(3,'0'):
        (ROLE_BADGE[nodeRole(a)] || 'Session')+' '+nodeName(a).slice(0,40))].join(' ▸ ')+' ▸';
    }
    // A tree row's own handle: a seat's or helper's socket, a Conductor's program core with its number ([ap:CORE]-NNN:
    // a Conductor has no socket; it works on its program's root).
    function treeHandle(it) {
      if(it.lin.kind==='conductor'){if(it.home.forest)return null;const n=Number.isSafeInteger(it.lin.conductorN) && it.lin.conductorN>0?'-'+String(it.lin.conductorN).padStart(3,'0'):'';return '[ap:'+it.home.core+']'+n;}
      return typeof it.row.socket==='string' && it.row.socket.trim()?it.row.socket.trim():null;
    }
    // Line 1's name with that handle at its left, where the name does not already carry it: Dan's flow starts from a tap on it.
    function handleName(name,handle) {
      if(!handle || String(name).includes(handle))return name;
      const number=/^\[ap:[A-Z0-9]+\](-\d{3})$/.exec(handle)?.[1];
      if(number && String(name).startsWith(number))return handle+String(name).slice(number.length);
      return handle+' · '+name;
    }
    // ---- where a socket sits (View's root widget asks; the tree is the sidebar's own, nothing is read for it)
    // One identity for a handle: an Anchor Point by its core, a UUID by its first 8 hex (a socket is stored either way).
    function handleKey(handle) {
      const text=String(handle || '').trim(),ap=/^\[ap:([A-Za-z0-9]{3,16})\]$/.exec(text);if(ap)return 'ap:'+ap[1].toUpperCase();
      const hex=/^([0-9a-f]{8})(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?$/i.exec(text);return hex?'hex:'+hex[1].toLowerCase():null;
    }
    function positionLine(it) {
      const handle=treeHandle(it),own=activityOf(it.key).own;let name=nodeName(it);
      const number=handle && /-(\d{3})$/.exec(handle)?.[0];
      if(number && name.startsWith(number))name=name.slice(number.length);else if(handle && name.startsWith(handle))name=name.slice(handle.length);
      name=name.replace(/^\s*·\s*/,'').trim();
      const models=it.backend==='claude'?factsParts(it.row.turnFacts)?.models:shortModel(it.row.model);
      return {id:it.id,key:it.key,role:ROLE_BADGE[nodeRole(it)] || 'Session',handle,name:name.slice(0,300),
        status:[it.backend==='claude'?'Claude Code':'OpenCode',own==='busy'?'running now':own==='waiting'?'waiting':null,models].filter(Boolean).join(' · ')};
    }
    function positionProgram(home,it=null) {
      if(home.forest)return {key:null,forest:true,title:it?forestSection(it).label:'Quick workers and helpers'};
      return {key:home.key,core:home.core,title:home.title,kind:home.kind,managerSession:home.managerSession,count:home.count,
        conductors:home.members.filter(m=>m.lin.kind==='conductor').length};
    }
    // @beacon[
    //   id=opc-pane@programPosition,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Where a handle sits in the sidebar's own tree for View: socket matches and program Conductors retain their branch and role facts, including a freestanding Manager tree's actual home section rather than a quick-worker label. Active first, then newest Conductor; at most 12 cards (8 for a program root), null without a match; no reads.,
    // ]
    function programPosition(handle,active=null) {
      const key=handleKey(handle),tree=state.tree;if(!key || !tree)return null;
      const nodes=[...tree.byId.values()],sessions=nodes.filter(it=>it.lin.kind!=='conductor' && handleKey(it.row.socket)===key);
      const program=key.startsWith('ap:')?tree.programs.find(p=>'ap:'+p.core===key) || null:null;
      const members=[...new Set([...sessions,...(program?program.members.filter(it=>it.lin.kind==='conductor'):[])])];
      if(!members.length)return null;
      const conductorOf=it=>{for(let at=it;at;at=at.parent)if(at.lin.kind==='conductor')return Number.isSafeInteger(at.lin.conductorN)?at.lin.conductorN:0;return 0;};
      members.sort((a,b)=>Number(b.id===active)-Number(a.id===active) || conductorOf(b)-conductorOf(a) || b.created-a.created);
      const chain=it=>{const lines=[];for(let at=it;at;at=at.parent)lines.unshift(positionLine(at));return lines;};
      return {handle:String(handle),kind:sessions.length?'socket':'program',program:program?positionProgram(program):null,total:members.length,
        cards:members.slice(0,sessions.length?12:8).map(it=>({id:it.id,active:it.id===active,program:positionProgram(it.home,it),
          manager:it.lin.managerSession || it.row.managerSession || null,chain:chain(it)}))};
    }
    // The same, once the list can answer: at once when it already holds the tapped session (or any match with none
    // tapped), else after the first complete walk of both backends (at most waitMs), then whatever it holds.
    async function positionWhenReady(handle,active=null,waitMs=25000) {
      const started=Date.now();
      for(;;) {
        const found=programPosition(handle,active),ready=!!state.tree && !!state.inventoryAt && state.oc.status!=='loading';
        if(ready || (found && (!active || found.cards.some(c=>c.id===active))) || Date.now()-started>waitMs)return found;
        await new Promise(resolve=>setTimeout(resolve,250));
      }
    }
    // ---- "show it in Programs / the sidebar": open only its ancestors, scroll it into view, flash it (never its glance)
    const REVEAL_MS=20000,FLASH_MS=2400;
    const revealing=key=>!!state.reveal?.keys?.has(key) && Date.now()-state.reveal.at<REVEAL_MS;
    const flashing=key=>state.reveal?.key===key && Date.now()-state.reveal.at<FLASH_MS;
    function revealOpen() {
      const r=state.reveal,tree=state.tree;if(!r || !tree)return false;
      const it=r.key.startsWith('s:')?tree.byId.get(r.key.slice(2)):null,p=!it && r.key.startsWith('p:')?tree.programs.find(x=>x.key===r.key):null;
      if(!it && !p)return false;
      const home=it?it.home:p,ancestors=it?tree.ancestors.get(it.key) || []:[];
      r.keys=new Set([r.key,...ancestors]);
      for(const g of [home.forest?forestSection(it).key:'programs:'+home.kind,'programs:running',...(placeOf(home)==='favorite'?['programs:favorite']:[])])state.sections.collapsed.delete(g);
      storeSections();
      // A forest tree's own key is its top session's; a program's is the program row.
      const top=it?(home.forest?[...ancestors,it.key][0]:home.key):p.key;
      if(ancestors.length)setLineageOpen(ancestors,true);
      for(const overlay of state.activeOnly.values()){overlay.shown.add(top);for(const k of ancestors)overlay.open.set(k,true);}
      r.opened=true;return true;
    }
    function revealScroll() {
      const r=state.reveal,scroller=$('sessions-scroll'),list=$('sessions-list');if(!r || r.done || !scroller?.clientHeight || !list)return false;
      const nodes=[...list.querySelectorAll('[data-anchor]')].filter(n=>n.dataset.anchor===r.key);
      const node=nodes.find(n=>n.closest('.session-group')?.dataset.group==='programs:running') || nodes[0];if(!node)return false;
      const box=(node.closest('.session-item') || node).getBoundingClientRect(),top=scroller.getBoundingClientRect().top;
      scroller.scrollTop=Math.max(0,Math.round(scroller.scrollTop+box.top-top-(scroller.clientHeight-box.height)/2));
      r.done=true;rememberList();return true;
    }
    // @beacon[
    //   id=opc-pane@revealInList,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Shows one session or program for View: every-session filter, its home and actual Running now or Favorite programs section unfolded, ancestors opened and active-only/page limits bypassed, then scrolled and flashed without a glance or conversation change. Later inventory arrivals can complete the reveal within 20 s.,
    // ]
    function revealInList(key) {
      if(typeof key!=='string' || !/^[sp]:/.test(key))return false;
      state.reveal={key,at:Date.now(),keys:new Set([key]),opened:false,done:false};
      if(($('sessions-role').value || 'all')!=='all')$('sessions-role').value='all';
      revealOpen();inventory(true);  // its redraw scrolls to the row once it is drawn (revealScroll)
      return state.reveal.done || revealScroll();
    }
    // A handle tapped on this page (handle-links.js): where it came from, for View. A session row's own handle (its socket,
    // a Conductor's [ap:CORE]-NNN, a program row's [ap:CORE]) is a Programs root with that session active and its
    // position already worked out; a handle in the desktop conversation roots View at the turn it was tapped in; any
    // other handle here is a root from elsewhere.
    function originOf(handle,node) {
      const where=sidebarOnly?'Programs':node?.closest?.('#history, #oc-header')?'the conversation':'the sidebar';
      const anchor=node?.closest?.('[data-anchor]')?.dataset.anchor || '',key=handleKey(handle),tree=state.tree;
      const id=/^s:(.+?)(?::glance|:tele)?$/.exec(anchor)?.[1],it=id?tree?.byId.get(id):null;
      if(it && key && handleKey(String(treeHandle(it) || '').replace(/-\d{3}$/,''))===key)return {from:'programs',active:it.id,where,position:programPosition(handle,it.id)};
      const core=/^p:([A-Z0-9]{6})$/.exec(anchor)?.[1];
      if(core && key==='ap:'+core)return {from:'programs',active:null,where,position:programPosition(handle,null)};
      const turn=sidebarOnly?null:turnAt(node);
      return turn?{from:'outside',where,turn}:{from:'outside',where};
    }
    // The conversation turn a handle was tapped in (the desktop's turnAt; the phone's is m.js's): this session, the row's
    // position (seq) and UUID, user / assistant / thinking / tool and, on a tool bar, its call. View keeps it on its root.
    function turnAt(node) {
      const article=node?.closest?.('#history-events article.history-event'),s=state.session;if(!article || !s)return null;
      const row=[...state.rows.values()].find(r=>r.node===article);if(!row || !Number.isSafeInteger(row.seq))return null;
      const call=row.calls.find(c=>c.node?.contains?.(node) || c.child?.contains?.(node)),thinking=row.thinking.some(b=>b.node?.contains?.(node));
      const oc=isOpenCode(s),kind=row.facts?.kind || 'assistant';
      const descriptor={backend:oc?'opencode':'claude',identity:identityOf(s),id:oc?s.id:null,sessionRef:s.sessionRef || null,scopeLabel:state.scope?.label || null,
        source:s.source || null,uuid:s.uuid || null,executionId:s.executionId || null,parentUuid:s.parentUuid || null,agentId:s.agentId || null};
      return {session:{kind:!oc && realRole(s)==='manager'?'manager':'viewer',executionId:s.executionId || null,title:displayName(s),label:roleLabel(s),descriptor},
        seq:row.seq,uuid:rowUuid(row),role:kind,kind:call?'tool':thinking?'thinking':kind,call:call?.id || null,tool:call?.name || null,part:null,
        time:Number.isFinite(row.time)?row.time:null};
    }
    function rowUuid(row) { try{const u=JSON.parse(row.signature).uuid;return typeof u==='string' && u?u:null;}catch(_){return null;} }
    // @beacon[
    //   id=opc-pane@callTurn,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The conversation turn of one tool call, by its callRef (the inspector's held call): its bar on screen now gives the turn (turnAt, and it is remembered, bounded CALL_TURNS), else the turn remembered when that call was on screen (Inspect remembers it at the click), else null. The editor's inspector uses it so a handle there roots View at the held call's turn, as one in the conversation does.,
    // ]
    function callTurn(callRef) {
      if(!ref(callRef))return null;
      const call=[...state.rows.values()].flatMap(r=>r.calls).find(c=>c.callRef===callRef),turn=call?turnAt(call.node):null;
      if(!turn)return state.callTurns.get(callRef) || null;
      state.callTurns.delete(callRef);state.callTurns.set(callRef,turn);
      while(state.callTurns.size>CALL_TURNS)state.callTurns.delete(state.callTurns.keys().next().value);
      return state.callTurns.get(callRef);
    }
    // @beacon[
    //   id=opc-pane@sidebarOpenInView,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=This page's Open in View (handle-links.js calls it with the element pressed): works out where the tap came from (originOf: a session row's socket or a Conductor's or program row's core → a Programs root, that session active, its position attached; a handle in the conversation → the turn it was tapped in; anything else → from elsewhere). The desktop opens it in this page's own View pane (openView); the phone's sidebar page hands it to /m around its frame; a sidebar page on its own, with no View around it, falls back to one reused View tab.,
    // ]
    function openInView(handle,context=null) {
      const origin=originOf(handle,context?.node || null);
      if(openView(handle,origin))return;
      try{const up=view?.parent && view.parent!==view?view.parent.opcOpenInView:null;if(typeof up==='function')return void up(handle,origin);}catch(_){/* not our origin */}
      view?.open?.('/glimpse.html?handle='+encodeURIComponent(handle),'opc-view');
    }
    // ---- the desktop View pane: the conversation column's 💬 Conversation | 📄 View (not on the phone's sidebar page).
    // View lies over the conversation, which stays laid out underneath, out of reach (inert): its scroll, Following and
    // reads go on, so 💬 brings it back exactly as it was left.
    function viewApi() { try{return $('desktop-view-frame')?.contentWindow?.opcGlimpseView || null;}catch(_){return null;} }
    function viewShown() { return $('history')?.dataset?.pane==='view'; }
    // @beacon[
    //   id=opc-pane@desktopShowPane,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The desktop conversation column's switch: 'view' shows the View pane over the conversation (glimpse.html in desktop-view-frame, loaded on first use; a View already loaded picks up a history another desktop View changed, resume), the conversation underneath kept laid out and made inert; 'conversation' takes View away and the conversation is exactly as it was left, Following included. A deliberate open of a session from the sidebar brings the conversation forward; Select in sidebar never does. A no-op on the phone's sidebar page.,
    // ]
    function showPane(pane,resume=true) {
      const box=$('history'),frame=$('desktop-view-frame'),panel=$('desktop-view'),switcher=$('history-switch');
      if(sidebarOnly || !box?.dataset || !frame?.setAttribute || !panel)return false;
      const show=pane==='view',was=viewShown();
      if(switcher)box.style?.setProperty?.('--view-top',(switcher.offsetTop+switcher.offsetHeight+3)+'px');
      box.dataset.pane=show?'view':'conversation';panel.hidden=!show;
      for(const node of box.children || [])if(node!==switcher && node!==panel)node.inert=show;
      $('pane-conversation')?.setAttribute('aria-selected',String(!show));$('pane-view')?.setAttribute('aria-selected',String(show));
      if(show && !frame.getAttribute('src'))frame.src='/glimpse.html';
      else if(show && !was && resume)void viewApi()?.resume?.();
      if(!show && was && state.follow && state.session)stickToLatest();
      return true;
    }
    // A handle for View (this page's own taps, or the editor around this frame): a fresh root in the View pane, which
    // comes forward. A frame not loaded yet starts at /glimpse.html?handle=…&from=…&active=…&where=…&turn=…, a loaded one
    // gets opcGlimpseView.openHandle, one still loading gets it on load.
    function openView(handle,origin={from:'outside'}) {
      const frame=$('desktop-view-frame');if(sidebarOnly || !frame?.setAttribute || !$('desktop-view'))return false;
      if(!frame.getAttribute('src')) {
        const query=new URLSearchParams({handle});
        for(const key of ['from','active','where'])if(typeof origin?.[key]==='string' && origin[key])query.set(key,origin[key]);
        if(origin?.turn)query.set('turn',JSON.stringify(origin.turn));
        frame.src='/glimpse.html?'+query;
      } else if(viewApi())void viewApi().openHandle(handle,'node',origin);
      else frame.addEventListener('load',()=>void viewApi()?.openHandle(handle,'node',origin),{once:true});
      showPane('view',false);
      return true;
    }
    // @beacon[
    //   id=opc-pane@desktopShowTurn,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=View's conversation-turn root on the desktop (Show in conversation): the conversation pane comes forward on that session (already on screen, else found in the list by its identity, a subagent under its parent, an OpenCode conversation by its ses_ id), the window around the turn is read from its position when it is not on screen (⤒ First's paging, from 20 rows before it, Paused; ↓ Latest returns to the newest), then the turn is scrolled to the middle of the conversation and flashed; a thinking turn opens its thinking, a tool turn flashes its bar (never an Inspect).,
    // ]
    async function showTurn(turn) {
      const d=turn?.session?.descriptor;if(sidebarOnly || !d || typeof d.identity!=='string' || !Number.isSafeInteger(turn.seq))return false;
      showPane('conversation');
      if(identityOf(state.session)!==d.identity) {
        if(d.backend==='opencode'){if(typeof d.id!=='string' || !d.id.startsWith('ses_')){notice('That conversation cannot be opened from here.',true);return false;}await openOpenCode(d.id);}
        else {
          const listed=()=>state.inventory.find(r=>identityOf(r)===d.identity && Array.isArray(r.scopes));
          let row=listed();if(!row){await list();row=listed();}
          if(!row && d.parentUuid && d.agentId)row=await sameSession({parentUuid:d.parentUuid,agentId:d.agentId,role:'subagent',source:d.source}).catch(()=>null);
          if(!row){notice((turn.session.title || 'That conversation')+' is not listed any more.',true);return false;}
          await browse(row,row.scopes?.find(s=>s.label===d.scopeLabel) || null);
        }
        if(identityOf(state.session)!==d.identity)return false;
      }
      const onScreen=loose=>{const rows=[...state.rows.values()];return turn.uuid && !loose?rows.find(r=>rowUuid(r)===turn.uuid):rows.find(r=>r.seq===turn.seq);};
      let row=onScreen(false);
      if(!row){await fromStart(Math.max(0,turn.seq-20));row=onScreen(false);}
      for(let i=0;!row && i<4 && state.next;i++){await loadNewer();row=onScreen(false);}
      row=row || onScreen(true);
      if(!row){notice('That turn is not in this conversation any more.',true);return false;}
      if(turn.kind==='thinking')for(const block of row.thinking)if(!block.node.classList.contains('thinking-empty'))block.node.open=true;
      const call=turn.call?row.calls.find(c=>c.id===turn.call):null,target=call?.node || row.node;
      if(state.follow)setFollow(false,{byScroll:true});
      const viewport=$('history-viewport'),r=target.getBoundingClientRect(),v=viewport.getBoundingClientRect();
      viewport.scrollTop+=r.top-v.top-Math.max(8,(viewport.clientHeight-Math.min(r.height,viewport.clientHeight))/2);
      target.classList.remove('reveal-flash');void target.offsetWidth;target.classList.add('reveal-flash');
      setTimeout(()=>target.classList.remove('reveal-flash'),2600);
      return true;
    }
    function lineageOpen(key,fallback) { return state.lineageOpen.has(key)?state.lineageOpen.get(key):fallback; }
    function setLineageOpen(key,open) {
      for(const k of typeof key==='string'?[key]:key){state.lineageOpen.delete(k);state.lineageOpen.set(k,!!(typeof open==='function'?open(k):open));}
      while(state.lineageOpen.size>LINEAGE_OPEN_MAX)state.lineageOpen.delete(state.lineageOpen.keys().next().value);
      try{doc.defaultView?.localStorage?.setItem(LINEAGE_OPEN,JSON.stringify([...state.lineageOpen]));}catch(_){/* display preference only */}
    }
    // @beacon[
    //   id=opc-pane@minimalOpen,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The one minimal-active-subtree rule for program toggles and active-only overlays: active programs open, members open only over busy/waiting tree descendants, quiet-Conductor folds closed; native lists stay untouched.,
    // ]
    function minimalOpen(key) {
      if(key.startsWith('fold:'))return false;
      const entry=activityOf(key);return key.startsWith('p:')?entry.any:entry.kids.busy+entry.kids.waiting>0;
    }
    function activeOverlay(entry) {
      if(!entry)return null;
      const place=entry.groupKey==null?placeOf(entry):null,groupKey=entry.groupKey ?? (place==='running' || place==='favorite'?'programs:'+place:'programs:'+entry.kind);
      if(groupKey==='programs:running' || !state.sections.activeOnly.has(groupKey))return null;
      if(!state.activeOnly.has(groupKey))state.activeOnly.set(groupKey,{shown:new Set(),open:new Map()});
      return state.activeOnly.get(groupKey);
    }
    // @beacon[
    //   id=opc-pane@viewOpen,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The sole program-tree open-state router: active-only sections use their page-only overlay, otherwise the existing bounded expander store; paired setViewOpen batches a toggle into one stored write.,
    // ]
    function viewOpen(key,fallback,entry) {
      const overlay=activeOverlay(entry);return overlay?(overlay.open.has(key)?overlay.open.get(key):minimalOpen(key)):lineageOpen(key,fallback);
    }
    function setViewOpen(key,open,entry) {
      const overlay=activeOverlay(entry);
      if(!overlay){setLineageOpen(key,open);return;}
      for(const k of typeof key==='string'?[key]:key)overlay.open.set(k,!!(typeof open==='function'?open(k):open));
    }
    // Fresh runs, never retained: activity/selection split them. Under the pointer,
    // preserve shown rows AND their open ribbon, rather than dissolving the ribbon
    // because its own members were drawn. An explicit fold click releases just its own held rows.
    function conductorRuns(p) {
      const selected=it=>(it.backend==='claude' && (isSelected(it.row) || (state.session && role(state.session)==='subagent' && state.childParent && identityOf(state.childParent)===identityOf(it.row)))) ||
        (it.backend==='opencode' && ocSelected(it.row.id)) || revealing(it.key) || it.children.some(selected);
      const runs=[];let run=[],expanded=false;
      const flush=()=>{if(run.length>1)runs.push({key:'fold:'+run[0].key,members:run});run=[];expanded=false;};
      for(const it of p.top) {
        if(it.lin.kind!=='conductor' || !Number.isSafeInteger(it.lin.conductorN) || it.lin.conductorN<=0 || activityOf(it.key).any || selected(it)){flush();continue;}
        const opensRibbon=state.sidebarHover && viewOpen('fold:'+it.key,false,p);
        // A newly quiet predecessor must not swallow an already open ribbon into a
        // different (closed) key while hovered. No prior-run cache is needed.
        if(state.sidebarHover && opensRibbon && !expanded && run.length)flush();
        if(!run.length)expanded=opensRibbon;
        if(state.sidebarHover && state.unfolded.has(it.key) && !expanded)flush();
        else run.push(it);
      }
      flush();return runs;
    }
    function programKeys(p,runs) { return [p.key,...p.members.filter(it=>it.children.length).map(it=>it.key),...runs.map(run=>run.key)]; }
    // The list's ⊞ (open) opens programs/tree nodes/current folds only, never sections. Closed, it
    // shuts every program and known home section but keeps Running now open (⊟ is now foldTree's two-state toggle).
    // Active-only entries keep expander writes in their overlay; its filter is untouched.
    function setAllPrograms(open) {
      const tree=state.tree;if(!tree)return;
      const stored=[];
      for(const p of tree.programs){const keys=open?programKeys(p,conductorRuns(p)):[p.key];
        if(activeOverlay(p))setViewOpen(keys,open,p);else stored.push(...keys);}
      // Freestanding trees have no program row: ⊞ opens their nodes, ⊟ closes their section.
      if(open)for(const it of tree.forest.top){
        const keys=subtree(it).filter(node=>node.children.length).map(node=>node.key),entry={...tree.forest,groupKey:forestSection(it).key};
        if(keys.length){if(activeOverlay(entry))setViewOpen(keys,true,entry);else stored.push(...keys);}
      }
      if(stored.length)setLineageOpen(stored,open);
      if(!open){
        for(const p of tree.programs)state.sections.collapsed.add('programs:'+p.kind);
        state.sections.collapsed.add('programs:favorite');
        for(const it of tree.forest.top)state.sections.collapsed.add(forestSection(it).key);
        for(const row of state.inventory)state.sections.collapsed.add(groupOf(row).key);
        state.sections.collapsed.delete('programs:running');storeSections();
      }
      inventory();
    }
    // @beacon[
    //   id=opc-pane@foldTree,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Shared phone/desktop two-state fold: close top-level trees and home sections including Favorite programs, retaining inner states and Running now; otherwise open only busy/waiting work in the sections drawing it. Phone mirrors overlays into saved keys; desktop follows each row's placement and active-only route.,
    // ]
    function foldTree(collapse,mirror) {
      const tree=state.tree;if(!tree)return;
      // A freestanding node's entry follows its top's placement (Running now or its section), as reveal() does.
      const forestEntry=it=>mirror?{...tree.forest,groupKey:forestSection(it).key}:{groupKey:state.placed.get(tree.ancestors.get(it.key)?.[0] || it.key)==='running'?'programs:running':forestSection(it).key};
      const stored=[],overlaid=[],open=collapse?false:minimalOpen;
      const route=(keys,entry)=>{if(!keys.length)return;const overlay=activeOverlay(entry);if(mirror || !overlay)stored.push(...keys);if(overlay)overlaid.push([keys,entry]);};
      const routeForest=members=>{const by=new Map();for(const it of members){const entry=forestEntry(it);if(!by.has(entry.groupKey))by.set(entry.groupKey,[[],entry]);by.get(entry.groupKey)[0].push(it.key);}for(const [keys,entry] of by.values())route(keys,entry);};
      if(collapse) {
        for(const p of tree.programs)route([p.key],p);
        routeForest(tree.forest.top.filter(it=>it.children.length));
        const inner=new Set([...tree.programs.flatMap(p=>p.members),...tree.forest.members.filter(it=>it.parent)].filter(it=>it.backend==='claude').map(it=>it.row.sessionRef));
        for(const key of [...state.expanded])if(!inner.has(key))state.expanded.delete(key);
        for(const p of tree.programs)state.sections.collapsed.add('programs:'+p.kind);
        state.sections.collapsed.add('programs:favorite');
        for(const it of tree.forest.top)state.sections.collapsed.add(forestSection(it).key);
        for(const row of state.inventory)state.sections.collapsed.add(groupOf(row).key);
      } else {
        for(const p of tree.programs){
          const runs=conductorRuns(p);
          for(const run of runs)for(const member of run.members)state.unfolded.delete(member.key);
          route(programKeys(p,runs),p);
        }
        routeForest(tree.forest.members.filter(it=>it.children.length));
        for(const key of state.groupKeys || [])if(state.activeGroups?.has(key))state.sections.collapsed.delete(key);else state.sections.collapsed.add(key);
      }
      if(stored.length)setLineageOpen(stored,open);
      for(const [keys,entry] of overlaid)setViewOpen(keys,open,entry);
      state.sections.collapsed.delete('programs:running');storeSections();
      inventory();
    }
    // @beacon[
    //   id=opc-pane@cyclePhoneTree,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The phone Programs view's one two-tap control, stepped by its remembered state.treeCycle: first tap foldTree's collapse (top-level rows close, inner state kept, home sections fold, Running now stays), second tap its fold-to-active (only busy or waiting work opens, only the sections that draw it).,
    // ]
    function cyclePhoneTree() {
      if(!state.tree)return;
      const collapse=state.treeCycle!=='collapsed';
      state.treeCycle=collapse?'collapsed':'running';
      foldTree(collapse,true);cycleLabel();
    }
    // @beacon[
    //   id=opc-pane@treeExpanded,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The desktop ⊟'s state from actually open program/freestanding trees, including Favorite programs and Manager homes, judged at their current placement through the renderer's same viewOpen rule; no remembered toggle flag.,
    // ]
    function treeExpanded() {
      const tree=state.tree;if(!tree)return false;
      const shown=(key,home)=>{const place=state.placed.get(key),groupKey=place==='running' || place==='favorite'?'programs:'+place:home;return state.sections.collapsed.has(groupKey)?null:groupKey;};
      for(const p of tree.programs){const groupKey=shown(p.key,'programs:'+p.kind);if(groupKey && viewOpen(p.key,p.live || p.active,{...p,groupKey}))return true;}
      for(const it of tree.forest.top)if(it.children.length){const groupKey=shown(it.key,forestSection(it).key);if(groupKey && viewOpen(it.key,true,{key:it.key,node:it,groupKey}))return true;}
      return false;
    }
    // The desktop ⊟ says what its next click does.
    function collapseLabel() {
      const button=$('sessions-collapse');if(!button || sidebarOnly)return;
      const collapse=treeExpanded();button.dataset.next=collapse?'collapse':'running';
      button.title=collapse?COLLAPSE_TITLE:'Open only what is busy or waiting: each running program and tree folds to its active and waiting sessions, and only the sections that draw them open';
      button.setAttribute('aria-label',collapse?'Collapse programs and home sections':'Show only running work');
    }
    function cycleLabel() {
      const button=$('sessions-cycle');if(!button)return;
      const running=state.treeCycle==='collapsed';
      button.textContent=running?'Show running':'Collapse';button.dataset.next=running?'running':'collapse';
      button.title=running?'Open only what is busy or waiting: the smallest tree that shows it':'Close every top-level row and fold the home sections (Running now stays); inner rows keep their state';
    }
    // Selecting a session opens its program, Conductor and lead seat (a remembered choice, like a click);
    // a freestanding one opens its lead, in the view (its section or Running now) where its tree is drawn.
    // An OpenCode conversation is found by its ses_ id; an unlisted one reveals nothing (no host execution is invented).
    function reveal(row) {
      const key=isOpenCode(row) || row?.archivedBackend==='opencode'?row.id:row?.executionId;
      const it=state.tree?.byId.get(key),keys=state.tree?.ancestors.get('s:'+(key || ''));
      const home=it?.home.forest?{groupKey:state.placed.get(keys?.[0] || it.key)==='running'?'programs:running':it.home.groupKey}:it?.home;
      for(const key of keys || [])if(!viewOpen(key,state.treeDefaults.get(key) ?? true,home))setViewOpen(key,true,home);
    }
    // @beacon[
    //   id=opc-pane@listOpenCodePrograms,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=View-bound OpenCode tree metadata with stored session titles on its independent 15 s cadence, without a conversation-page byte cap or title-enrichment reads. Old-server view 400 retries without it; superseded views cannot publish. Keep source observedAt and last-known rows on failure; inventory and optional details never gate fast activity.,
    // ]
    async function ocList() {
      if(state.ocBusy || state.archive)return;state.ocBusy=true; // never while 📦 History is the list's source

      const generation=++state.ocGeneration,selectedView=setView();state.ocAbort?.abort();state.ocAbort=new AbortController();
      try {
        let data;
        // This endpoint is deliberately unpaged/uncapped. Conversation and
        // detail reads still use json's ordinary per-page byte allowance.
        try {data=await json('/api/opencode/programs'+(state.viewSupported.opencode?'?view='+selectedView:''),{signal:state.ocAbort.signal},Infinity);}
        catch(e) {if(e.code!==400 || !state.viewSupported.opencode)throw e;state.viewSupported.opencode=false;data=await json('/api/opencode/programs',{signal:state.ocAbort.signal},Infinity);}
        if(generation!==state.ocGeneration)return;
        if(!data || data.schemaVersion!==1 || data.backend!=='opencode' || !Array.isArray(data.sessions))throw new Error('invalid OpenCode program list');
        if(data.status!=='complete')throw new Error(typeof data.error==='string' && data.error?data.error:'status '+data.status);
        state.oc={rows:data.sessions.filter(r=>r && typeof r.id==='string' && /^ses_[A-Za-z0-9]{8,64}$/.test(r.id)),status:'complete',error:null,stale:false,
          observedAt:typeof data.observedAt==='number' && data.observedAt>0?data.observedAt*1000:null};
      } catch(e) {if(generation===state.ocGeneration)state.oc={rows:state.oc.rows,status:'unavailable',error:String(e.message || e).slice(0,200),stale:state.oc.rows.length>0};}
      finally {if(generation===state.ocGeneration){state.ocBusy=false;state.ocNextPoll=Date.now()+OC_POLL_MS;}}
      if(generation===state.ocGeneration)inventory();
    }
    // @beacon[
    //   id=opc-pane@pollOpenCodeActivity,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Two-second busy and foreground-call observations independent of the slow inventory; source timestamps prevent stale-cache spinners, failures say unknown, unchanged displayed facts do not rebuild the list, and a deliberate Refresh during an in-flight read is replayed once.,
    // ]
    async function pollOpenCodeActivity(force=false) {
      if(state.ocActivityBusy){if(force)state.ocActivityAgain=true;return;}
      state.ocActivityBusy=true;
      const signature=()=>JSON.stringify(state.oc.rows.map(r=>{const f=ocEvidence(r);return [r.id,f.busy,f.calls,f.toolStatus,f.error || null];}));
      const before=state.ocActivityShown ?? signature(),source=state.sourceGeneration;
      try {
        const data=await json('/api/opencode/activity');
        if(source!==state.sourceGeneration)return; // the list's source changed (📦 History): this observation is superseded
        if(data?.schemaVersion!==1 || data.backend!=='opencode' || data.status!=='complete' || !Array.isArray(data.active) ||
            !Array.isArray(data.sessions) || !Number.isFinite(data.observedAt))throw new Error(data?.error || 'OpenCode activity unavailable');
        const at=data.observedAt*1000;
        if(!state.ocActivity || at>=state.ocActivity.observedAt)state.ocActivity={active:new Set(data.active),
          sessions:new Map(data.sessions.filter(r=>typeof r?.id==='string').map(r=>[r.id,r])),observedAt:at,error:data.stale?(data.error || 'OpenCode activity refresh failed'):null};
      } catch(e) {
        state.ocActivity={...(state.ocActivity || {active:new Set(),sessions:new Map(),observedAt:0}),error:String(e.message || e)};
      } finally {
        state.ocActivityBusy=false;state.ocActivityNextPoll=Date.now()+OC_ACTIVITY_MS;
        const after=signature();state.ocActivityShown=after;if(force || before!==after)inventory();
        if(state.ocActivityAgain){state.ocActivityAgain=false;void pollOpenCodeActivity();}
      }
    }
    // @beacon[
    //   id=opc-pane@openCodeGlance,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=A session row's 💬 chip (on the phone, a row tap with nothing under it) opens or closes an inline glance under it, the same for both backends: the newest text messages (clipped), the running tool call or else the latest one (the conversation's own compact preview and its time: glanceCall) and its id with copy; an OpenCode glance (/api/opencode/glance) also shows status, outcome, model and cost, a Claude glance (/api/claude/glance: its transcript's tail only) the row's busy/idle, model and newest record. Read when it opens and every GLANCE_POLL_MS while open (pollGlances), never with the list; it never selects, browses or targets anything in the conversation view. On the desktop the row itself opens the whole conversation; on the phone the glance's Read conversation › opens it read-only in the phone's Chat, and its ⤢ Large shows it as a large-type sheet (openLargeGlance), which closes with the glance.,
    // ]
    async function toggleGlance(id,cwd=null) {
      if(state.glances.has(id)){state.glances.delete(id);if(state.glanceLarge?.id===id)closeLargeGlance();inventory();return;}
      state.glances.set(id,{loading:true,cwd});state.glanceNextPoll=Date.now()+GLANCE_POLL_MS;inventory();await loadGlance(id);
    }
    // One read of an open glance: an OpenCode ses_ id, or a Claude session UUID (its folder only as the server's lookup
    // hint). quiet (the open glances' refresh): no loading state, and a redraw only when what the glance shows changed.
    async function loadGlance(id,quiet=false) {
      const prior=state.glances.get(id);if(!prior || state.glanceBusy.has(id))return;
      // 📦 History: the archived row's glance, by its archive key (/api/history/glance answers key, not uuid/id).
      const claude=!id.startsWith('ses_'),key=historyRow(id)?.archived?.key,backend=key?'history':claude?'claude':'opencode';let next;
      state.glanceBusy.add(id);
      if(!quiet){state.glances.set(id,{...prior,loading:true});inventory();}
      try {
        const data=await json(key?'/api/history/glance?'+new URLSearchParams({key}):claude?'/api/claude/glance?'+new URLSearchParams({uuid:id,...(prior.cwd?{cwd:prior.cwd}:{})}):'/api/opencode/glance?'+new URLSearchParams({id}));
        if(!data || data.schemaVersion!==1 || data.backend!==backend || (key?data.key!==key:(claude?data.uuid:data.id)!==id) || (data.status==='complete' && !Array.isArray(data.messages)))throw new Error('invalid '+(key?'History':claude?'Claude':'OpenCode')+' glance');
        next=data.status==='complete'?{data,readAt:Date.now()}:{data:prior.data,readAt:prior.readAt,error:data.error || 'unavailable'};
      } catch(e) {next={data:prior.data,readAt:prior.readAt,error:e.message};}
      finally {state.glanceBusy.delete(id);}
      const current=state.glances.get(id);if(!current)return; // closed meanwhile
      next.cwd=prior.cwd;state.glances.set(id,next);
      if(!quiet || glanceShown(current)!==glanceShown(next))inventory();
      if(state.glanceLarge?.id===id)paintLargeGlance();
    }
    // What a glance shows (its error and data, never how it was read): a quiet re-read that changes none of it redraws nothing.
    function glanceShown(g) { return JSON.stringify([g.error || null,g.data?{...g.data,read:undefined}:null]); }
    // @beacon[
    //   id=opc-pane@pollGlances,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The open glances' own refresh (desktop and phone, while the page is visible): every GLANCE_POLL_MS, one read at a time, at most GLANCE_POLL_MAX of them, each redrawing the list only when what it shows changed. Closed glances are never read; nothing here touches the inventory, activity or telemetry reads.,
    // ]
    async function pollGlances() {
      if(state.glancePolling)return;
      state.glancePolling=true;state.glanceNextPoll=Date.now()+GLANCE_POLL_MS;
      try {for(const id of [...state.glances.keys()].slice(0,GLANCE_POLL_MAX))await loadGlance(id,true);}
      finally {state.glancePolling=false;state.glanceNextPoll=Date.now()+GLANCE_POLL_MS;}
    }
    // who: {title,label} of the row; a Claude row adds its activity (busy decides "running") and its Read › session.
    // large: the same glance inside its large-type sheet (paintLargeGlance).
    function glanceBox(id,who=null,large=false) {
      const claude=!id.startsWith('ses_'),g=state.glances.get(id) || {},d=g.data;
      const box=element('div',undefined,claude?'claude-glance':'oc-glance'),head=element('div',undefined,'glance-head');
      // Phone: ⤢ Large (floated beside the head line) shows this glance as a large-type sheet; an open sheet follows this
      // row's latest who (busy/idle, title).
      if(sidebarOnly && !large) {
        const big=element('span','⤢ Large','glance-large-open');big.title='Show this glance large: big bright text, every message whole';
        big.setAttribute('aria-label',big.title);pressable(big,()=>openLargeGlance(id,who));box.append(big);
        if(state.glanceLarge?.id===id){state.glanceLarge.who=who;Promise.resolve().then(()=>paintLargeGlance());}
      }
      if(g.error)head.append(element('span',(claude?'Transcript unavailable: ':'OpenCode unavailable: ')+g.error+(d?' · showing the last glance':''),'glance-error'));
      else if(g.loading && !d)head.append(element('span','loading …'));
      const current=!claude && state.ocActivity?ocEvidence({id}):null;
      const busy=!g.error && (claude?who?.activity==='busy':current?current.busy===true:d?.busy===true);
      if(d)head.append(element('span',(claude?[who?.activity==='quiet' || !who?.activity?'idle':who.activity,modelName(d.model),d.lastAt?'latest record '+clock(d.lastAt):null]:
        [d.busy?'busy':'idle',d.outcome?'outcome '+d.outcome:null,d.agent,shortModel(d.model),dollars(d.cost),d.idleAt && !d.busy?'idle since '+clock(d.idleAt):null]).filter(Boolean).join(' · ')));
      box.append(head);
      if(claude && who?.halt)box.append(element('div',who.halt,'glance-halt'));
      if(d) {
        const list=element('div',undefined,'glance-messages');
        for(const m of d.messages) {
          const line=element('div',undefined,'glance-msg');line.dataset.role=m.role;
          line.append(element('span',(m.role==='user'?'prompt':m.role==='assistant'?'said':String(m.role))+(clock(m.time)?' · '+clock(m.time):''),'glance-who'),element('span',String(m.text),'glance-text'));
          line.title=(Number.isSafeInteger(m.chars)?m.chars.toLocaleString('en-US')+' characters':'')+(String(m.text).endsWith('…')?(sidebarOnly?' (shortened here; Read conversation › shows it whole)':' (shortened here; click the row for the whole conversation)'):'');list.append(line);
        }
        if(!d.messages.length)list.append(element('div','(no text messages yet)','glance-who'));
        if(d.partial)list.append(element('div','(any earlier ones lie further back than a glance reads)','glance-who'));
        box.append(list);
        const currentCalls=!claude && current?current.calls:!claude && Array.isArray(d.runningCalls)?d.runningCalls:null;
        const calls=currentCalls?.length?currentCalls.map(c=>({...c,open:true})):d.call?[d.call]:[];
        for(const call of calls)box.append(glanceCall(call,busy && (current?Array.isArray(currentCalls) && currentCalls.length>0:currentCalls===null || currentCalls.length>0),current?.at ?? g.readAt,id,!!current));
        if(current?.toolStatus==='unavailable')box.append(element('div','Running tool details unavailable · busy/idle was read separately','glance-error'));
      }
      if(!sidebarOnly && !claude)box.append(focusLine(id,d));
      const foot=element('div',undefined,'glance-foot'),copy=element('span','⧉','glance-copy'),refresh=element('button','refresh','link');
      copy.title='Copy '+id;copy.setAttribute('aria-label',claude?'Copy the session UUID':'Copy the OpenCode session id');pressable(copy,()=>copyText(copy,id));
      refresh.type='button';refresh.onclick=event=>{event?.stopPropagation?.();void loadGlance(id);};
      // Phone: the whole conversation opens read-only in Chat; desktop: the row itself opens it.
      const read=!sidebarOnly?element('span','· whole conversation: click the row','glance-who'):
        claude?(who?.open?readChip(who.open,'Read conversation ›','glance-read'):null):
        readChip({backend:'opencode',id,title:who?.title || d?.title || id,label:who?.label || 'OpenCode'},'Read conversation ›','glance-read');
      foot.append(element('span',id,'glance-id'),copy,refresh);if(read)foot.append(read);box.append(foot);
      if(read && large)read.addEventListener('click',closeLargeGlance); // off to Chat: the sheet is done
      return box;
    }
    // @beacon[
    //   id=opc-pane@openLargeGlance,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Phone Programs only: an open glance's ⤢ Large shows that same glance (glanceBox, large) as a sheet over the whole frame, in large bright type with every message whole and the tool call's preview unclipped. It reads nothing itself: it repaints from the glance's own reads (loadGlance, pollGlances) and its row's latest busy/idle and halt line, only when what it shows changed, keeping the reading position (the newest end while there). Close, Read conversation › or closing the glance closes it.,
    // ]
    function openLargeGlance(id,who) {state.glanceLarge={id,who,shown:null};paintLargeGlance(true);}
    function closeLargeGlance() {state.glanceLarge=null;$('glance-large')?.remove();doc.removeEventListener?.('keydown',largeGlanceKey,true);}
    function largeGlanceKey(event) {if(event.key==='Escape' && state.glanceLarge){event.preventDefault?.();closeLargeGlance();}}
    function paintLargeGlance(opening=false) {
      const large=state.glanceLarge,g=large && state.glances.get(large.id);
      if(!large)return;
      if(!g){closeLargeGlance();return;}
      let sheet=$('glance-large');
      if(!sheet) {
        sheet=element('div',undefined,'glance-large');sheet.id='glance-large';sheet.setAttribute('role','dialog');sheet.setAttribute('aria-modal','true');
        const top=element('div',undefined,'glance-large-top'),title=element('div',undefined,'glance-large-title'),x=element('button','✕','glance-large-x');
        const body=element('div',undefined,'glance-large-body'),foot=element('div',undefined,'glance-large-foot'),close=element('button','Close','glance-large-close');
        title.id='glance-large-title';sheet.setAttribute('aria-labelledby',title.id);x.type=close.type='button';x.setAttribute('aria-label','Close the large glance');
        x.onclick=close.onclick=event=>{event?.stopPropagation?.();closeLargeGlance();};
        top.append(title,x);foot.append(close);sheet.append(top,body,foot);doc.body.append(sheet);
        doc.addEventListener?.('keydown',largeGlanceKey,true);
      }
      const shown=JSON.stringify([glanceShown(g),large.who?.activity || null,large.who?.title || null,large.who?.label || null,large.who?.halt || null]);
      if(!opening && shown===large.shown)return;
      large.shown=shown;
      const title=$('glance-large-title');text(title,(large.who?.label?large.who.label+' · ':'')+(large.who?.title || large.id));
      const body=sheet.querySelector('.glance-large-body'),atEnd=opening || body.scrollHeight-body.scrollTop-body.clientHeight<40,top=body.scrollTop;
      body.replaceChildren(glanceBox(large.id,large.who,true));
      body.scrollTop=atEnd?body.scrollHeight:top;
    }
    // The glance's tool call, either backend: the running one, else the latest; its name, the conversation row's own compact
    // preview (opcToolPreview) and its time as the row says it (opcToolReading.callDuration): the total for a finished
    // call, the time so far for one running now (no result yet while the session is busy), nothing when unknown.
    function glanceCall(c,busy,readAt=Date.now(),glanceId=null,activity=false) {
      const box=element('div',undefined,'glance-call'),running=c.open===true && busy;
      const start=Number.isFinite(c.time)?c.time*1000:NaN,end=running?Date.now():Number.isFinite(c.resultTime)?c.resultTime*1000:NaN;
      const took=(running?globalThis.opcToolReading?.elapsedText?.(start,end):globalThis.opcToolReading?.callDuration?.(start,end)) || '',error=c.status==='error';
      box.dataset.state=running?'running':c.open?'open':error?'error':'done';
      box.append(element('span',(running?'tool call running':'latest tool call')+(clock(c.time)?' · '+clock(c.time):''),'glance-who'));
      const bar=element('span',undefined,'glance-call-bar');bar.append(element('span',String(c.name || 'tool'),'glance-call-name'));
      const time=running?(took?took+' so far':''):c.open?'no result yet':[error?'error':null,took].filter(Boolean).join(' · ');
      if(time) {
        const label=element('span',time,'glance-call-time');
        if(running){label.dataset.start=String(start);label.dataset.readAt=String(readAt);if(glanceId)label.dataset.glanceId=glanceId;label.dataset.activity=String(activity);}
        bar.append(label);
      }
      box.append(bar);
      let preview='';try{preview=globalThis.opcToolPreview?.preview?.(c.previewData,c.name,{descriptor:true}) || '';}catch(_){preview='';}
      if(preview)box.append(element('span',preview,'glance-call-preview'));
      box.title=running?'Running now: '+(took?took+' so far, ':'')+'from the call to this read':c.open?'No result recorded yet':
        took?'Took '+took+': from the call to its stored result':'Its time is not recorded';
      return box;
    }
    function tickGlanceTimes(now=Date.now()) {
      for(const node of doc.querySelectorAll?.('.glance-call-time[data-start]') || []) {
        const g=state.glances.get(node.dataset.glanceId),a=node.dataset.activity==='true'?ocEvidence({id:node.dataset.glanceId}):null;
        const start=Number(node.dataset.start),at=a?.at ?? g?.readAt ?? Number(node.dataset.readAt),fresh=!(a?.error || g?.error) && Number.isFinite(at) && now-at<=OC_EVIDENCE_MS;
        text(node,fresh?(globalThis.opcToolReading?.elapsedText?.(start,now) || '')+' so far':'last observation stale');
      }
    }
    // The glance's "Open in CodeNomad" button and what became of the latest request for this session.
    function focusLine(id,d) {
      const row=state.oc.rows.find(r=>r.id===id),directory=d?.directory || row?.directory || null;
      const line=element('div',undefined,'glance-focus'),button=element('button','Open in CodeNomad','focus-open');button.type='button';
      const f=state.focus?.sessionId===id?state.focus:null,busy=!!f && !FOCUS_FINAL.includes(f.state);
      button.disabled=!directory || busy;
      button.title=directory?'Show this session in the CodeNomad page beside the pane: its workspace tab for '+directory+', then its row in the session list':'OpenCode did not say which folder this session runs in';
      button.onclick=event=>{event?.stopPropagation?.();void openInCodeNomad(id,{directory,title:d?.title || row?.title || null,parentId:d?.parentId || row?.parentId || null});};
      line.append(button);
      if(f){const status=element('span',focusText(f),'focus-status');status.dataset.state=f.state;if(f.missingTab)status.dataset.missingTab='true';line.append(status);}
      return line;
    }
    function focusText(f) {
      if(f.state==='posting')return 'asking CodeNomad …';
      if(f.state==='pending')return f.missingTab?(f.missingTab.message?'✗ '+f.missingTab.message:'Open the CodeNomad tab for '+f.directory+': this finishes by itself once it is open (waiting up to a minute)'):
        f.noPage?'No CodeNomad page is answering yet: is the CodeNomad tab open and the extension loaded? (still waiting)':'waiting for CodeNomad …';
      if(f.state==='claimed')return 'CodeNomad is switching to it …';
      if(f.state==='done')return '✓ shown in CodeNomad';
      return '✗ '+(f.outcome?.message || f.error || f.state);
    }
    // @beacon[
    //   id=opc-pane@openInCodeNomad,
    //   slice_labels=opc-pane,opc-codenomad-tools,
    //   kind=ast,
    //   comment=Open in CodeNomad, from a desktop glance or the OpenCode conversation header: posts one focus request (session id, folder, title) to the pane server, then follows it (pending → claimed → done / failed / expired; a missing workspace tab says which folder to open and waits) and shows its state in both places; never browses, targets or changes the held tool.,
    // ]
    async function openInCodeNomad(id,{directory,title=null,parentId=null}) {
      if(!directory)return;
      const nonce=globalThis.crypto?.randomUUID?.() || ('focus-'+Date.now()+'-'+Math.random().toString(36).slice(2));
      state.focus={sessionId:id,nonce,directory,state:'posting'};inventory();header();
      try {
        const data=await json('/api/focus-request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sessionId:id,directory,title:title?String(title).slice(0,400):null,parentId,nonce})});
        focusAccept(data,nonce);
      } catch(e) {if(state.focus?.nonce===nonce)state.focus={...state.focus,state:'failed',error:'the pane server refused: '+e.message};}
      state.focusNextPoll=Date.now()+FOCUS_POLL_MS;inventory();header();
    }
    function focusAccept(data,nonce) {
      const r=data?.request;
      if(!data || data.schemaVersion!==1 || !r || r.nonce!==nonce)throw new Error('the pane server no longer holds this request');
      if(state.focus?.nonce!==nonce)return false;
      const missingTab=r.missingTab && typeof r.missingTab==='object'?{message:typeof r.missingTab.message==='string'?r.missingTab.message:null}:null;
      const next={...state.focus,state:String(r.state),missingTab,noPage:r.noPage===true,outcome:r.outcome || null},changed=focusText(next)!==focusText(state.focus);
      state.focus=next;return changed;
    }
    async function focusPoll() {
      const f=state.focus;if(!f || state.focusBusy || f.state==='posting' || FOCUS_FINAL.includes(f.state))return;
      state.focusBusy=true;let changed=false;
      try {changed=focusAccept(await json('/api/focus-request'),f.nonce);}
      catch(e) {if(state.focus?.nonce===f.nonce){state.focus={...state.focus,state:'failed',error:e.message};changed=true;}}
      finally {state.focusBusy=false;state.focusNextPoll=Date.now()+FOCUS_POLL_MS;}
      if(changed){inventory();header();}
    }
    // An OpenCode row's turn times are read when the row is first drawn (one small server read, at most
    // in the viewport-only optional lane, cached per session) and kept while the row's own stamps (updated, idle, busy)
    // stand; a busy row re-reads after 10 s. The row's times parenthetical and its hover share them.
    const ocTurnSig=r=>[r.updatedAt,r.idleAt,r.busy===true].join('|');
    const OC_TURN_SLOTS=1,ocTurnWaiting=[];let ocTurnRunning=0;
    function ocTurnSlot(task) {
      return new Promise((resolve,reject)=>{
        const run=()=>{ocTurnRunning++;task().then(resolve,reject).finally(()=>{ocTurnRunning--;ocTurnWaiting.shift()?.();});};
        if(ocTurnRunning<OC_TURN_SLOTS)run();else ocTurnWaiting.push(run);
      });
    }
    function ocTurnLine(r) {
      const t=state.ocTurns.get(r.id);
      if(!t || t.sig!==ocTurnSig(r))return turnTimes(null,null,'unknown (turn times still being read)');
      if(t.error)return turnTimes(null,null,'unknown (OpenCode did not answer)');
      return turnTimes(t.data.firstAt,t.data.lastAt);
    }
    function ocTurns(r) {
      const t=state.ocTurns.get(r.id),sig=ocTurnSig(r);
      if(t?.loading)return t.loading;
      if(t && t.sig===sig && (t.error?Date.now()-t.at<30000:r.busy!==true || Date.now()-t.at<10000))return Promise.resolve();
      const loading=(async()=>{
        let next;
        try {
          const data=await ocTurnSlot(()=>json('/api/opencode/turns?'+new URLSearchParams({id:r.id})));
          if(!data || data.schemaVersion!==1 || data.backend!=='opencode' || data.id!==r.id)throw new Error('invalid OpenCode turn times');
          next=data.status==='complete'?{data,sig,at:Date.now()}:{error:data.error || 'unavailable',sig,at:Date.now()};
        } catch(e) {next={error:e.message,sig,at:Date.now()};}
        state.ocTurns.set(r.id,next);
      })();
      state.ocTurns.set(r.id,{...t,loading});
      return loading;
    }
    // ------------------------------------------------------------- telemetry (oc.sh telemetry) on tree rows
    // Each program (or freestanding quick-worker tree) with a viewport row reads its numbers through /api/telemetry:
    // again when its drawn rows' own stamps move, every 15 s while something in it is busy or waiting, 5 s
    // after "computing", 30 s after a failure. The server recomputes only when a session's records changed.
    const TELE_ACTIVE_MS=15000,TELE_MIN_MS=5000,TELE_RETRY_MS=30000,TELE_SIDS=40;
    function teleStamp(it) {
      const r=it.row;
      return it.id+'='+(it.backend==='claude'?[r.turnFacts?.lastAt,r.turnFacts?.status]:[r.updatedAt,r.idleAt,r.busy===true]).join(',');
    }
    // A drawn tree row (by its session id) joins its program's read, or its freestanding tree's.
    function teleWant(id,node) {
      const it=state.tree?.byId.get(id),wanted=state.teleWanted;
      if(!it || !wanted)return;
      let root=it;while(root.parent)root=root.parent;
      const key=it.home.forest?'f:'+root.id:'p:'+it.home.core;
      let w=wanted.get(key);
      if(!w){w={program:it.home.forest?null:it.home.core,sids:new Set(),stamps:new Set(),nodes:[],active:activityOf(it.home.forest?root.key:it.home.key).any};wanted.set(key,w);}
      w.sids.add(it.id);w.stamps.add(teleStamp(it));if(node)w.nodes.push(node);
    }
    function teleFlush() {
      const wanted=state.teleWanted,now=Date.now();state.teleWanted=null;
      for(const [key,w] of wanted || []) {
        const sig=[...w.stamps].sort().join('|'),r=state.teleReads.get(key);
        if(r?.loading || (r && now-r.at<(r.error?TELE_RETRY_MS:TELE_MIN_MS)))continue;
        if(r && !r.error && r.sig===sig && !(w.active && now-r.at>=TELE_ACTIVE_MS))continue;
        detailWant('tele:'+key,w.nodes,w.active,0,()=>teleRead(key,w,sig));
      }
      detailFlush();
    }
    // @beacon[
    //   id=opc-pane@sidebarDetails,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Two optional sidebar detail reads at most: visible pinned Managers qualify above the scroller, other rows inside its viewport. Active/newest work leads; queued collapsed/offscreen work is discarded on redraw. Turns, child discovery and telemetry share these slots; inventory/activity never wait for them.,
    // ]
    function detailFlush() {
      // Every queued read here is live (turns, child discovery, telemetry); 📦 History brings its own facts.
      if(state.archive || doc.hidden || doc.defaultView?.frameElement?.closest?.('[hidden]'))return;
      const onScreen=node=>{
        if(!node || node.isConnected===false)return false;
        if(typeof node.getBoundingClientRect!=='function')return true;
        const r=node.getBoundingClientRect(),scroll=node.closest?.('#sessions-pinned')?null:$('sessions-scroll')?.getBoundingClientRect?.();
        return r.bottom>Math.max(0,scroll?.top || 0) && r.top<Math.min(view?.innerHeight || 1000,scroll?.bottom || Infinity);
      };
      const pending=[...state.detailWanted].filter(([key,w])=>!state.detailRunning.has(key) && w.nodes.some(onScreen))
        .sort((a,b)=>Number(b[1].active)-Number(a[1].active) || b[1].recent-a[1].recent);
      while(state.detailRunning.size<2 && pending.length){
        const [key,w]=pending.shift();state.detailWanted.delete(key);state.detailRunning.add(key);
        Promise.resolve().then(w.read).catch(()=>{}).finally(()=>{state.detailRunning.delete(key);detailFlush();});
      }
    }
    function detailWant(key,nodes,active,recent,read) {state.detailWanted.set(key,{nodes:Array.isArray(nodes)?nodes:[nodes],active,recent:recent || 0,read});}
    async function claudeTurns(row) {
      const old=state.claudeTurns.get(row.uuid),now=Date.now();
      if(old?.loading || old && old.generation===row.generation && now-old.at<(old.error?30000:old.data?.status==='scanning'?5000:ended(row)?Infinity:10000))return;
      state.claudeTurns.set(row.uuid,{...old,loading:true});
      try {
        const data=await json('/api/claude/turns?'+new URLSearchParams({uuid:row.uuid,...(row.cwd?{cwd:row.cwd}:{})}));envelope(data);
        if(data.uuid!==row.uuid)throw new Error('invalid Claude turn facts');
        state.claudeTurns.set(row.uuid,{data:data.turnFacts,generation:row.generation,at:Date.now()});
        if(JSON.stringify(old?.data)!==JSON.stringify(data.turnFacts))inventory();
      } catch(e) {state.claudeTurns.set(row.uuid,{...old,generation:row.generation,loading:false,error:String(e.message || e),at:Date.now()});}
    }
    function sidebarFreshness() {
      const node=$('sessions-freshness');if(!node)return;
      const h=state.archive;
      if(h) {
        // 📦 History's own quiet line (an empty archive says so here): what is loaded, never live activity.
        const total=h.total?.units,swept=h.lastSweepAt?clock(h.lastSweepAt):null;
        node.textContent=h.error?'📦 History unavailable: '+h.error+' · ⟳ reads it again':h.status==='empty'?'📦 History is empty: nothing has been archived yet':
          !h.status?'📦 History · loading…':'📦 History · '+h.units.length+(Number.isSafeInteger(total)?' of '+total:'')+' units, newest first'+
          (h.more?' · Show more loads older ones':' · the whole archive')+(swept?' · last archived '+swept:'');
        node.title='📦 History: every archived conversation, read from the archive only; nothing live is read while it is shown. Another view returns to the live list.';
        return;
      }
      const oc=state.ocActivity,host=state.hostActivityAt;
      const ocFresh=!!oc && !oc.error && Date.now()-oc.observedAt<=OC_EVIDENCE_MS;
      const hostFresh=!!host && !state.hostActivityFailure && Date.now()-host<=OC_EVIDENCE_MS;
      const text=hostFresh && ocFresh?'Current activity checked'+(state.inventoryBusy?' · session list loading':''):
        'Updating current activity… '+[!hostFresh?'Claude Code':null,!ocFresh?'OpenCode':null].filter(Boolean).join(' · ')+
        (state.hostActivityFailure || oc?.error?' (unavailable; retrying)':'');
      node.textContent=text;node.title='Running marks require an activity observation less than 15 seconds old. Session history and telemetry load separately, only visible row details are read.';
      const signature=[hostFresh,ocFresh].join('|');
      if(state.freshnessSignature!==signature){state.freshnessSignature=signature;inventory();}
    }
    // @beacon[
    //   id=opc-pane@teleRead,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=One /api/telemetry read (a program, or one freestanding tree's sessions): keeps each session's entry (oc_telemetry's row texts per job segment, flags, notes, live) and redraws only when one changed; "computing" asks again in 5 s, a failure in 30 s and keeps the rows' last numbers.,
    // ]
    async function teleRead(key,w,sig) {
      state.teleReads.set(key,{...state.teleReads.get(key),loading:true});
      const query=w.program?new URLSearchParams({program:w.program}):new URLSearchParams([...w.sids].slice(0,TELE_SIDS).map(sid=>['sid',sid]));
      let next,changed=false;
      try {
        const data=await json('/api/telemetry?'+query);
        if(!data || data.schemaVersion!==1 || data.backend!=='telemetry' || !data.sessions || typeof data.sessions!=='object')throw new Error('invalid telemetry answer');
        if(data.status==='unavailable')throw new Error(data.error || 'unavailable');
        for(const [sid,entry] of Object.entries(data.sessions))
          if(entry && Array.isArray(entry.segments) && JSON.stringify(state.tele.get(sid))!==JSON.stringify(entry)){state.tele.set(sid,entry);changed=true;}
        next={sig:data.status==='complete'?sig:null,at:Date.now()};
      } catch(e) {next={sig,error:String(e.message || e).slice(0,200),at:Date.now()};}
      state.teleReads.set(key,next);
      if(changed)inventory();
    }
    const ROLE_TITLE=role=>String(role).toLowerCase().replace(/^./,c=>c.toUpperCase());
    // A phase line's legend, per backend. The OpenCode (GPT) text is unchanged; a Claude run's line comes from its host's
    // own stream records, so its labels are the host's honest clocks, and the last sentence names what may be a bound.
    const STREAM_LEGEND_GPT='GPT · each run\'s second line = minutes and shares of wall time (oc.sh telemetry --phases over that run alone): wait = for the first output · think = thinking · write = writing tool calls · tools = own tool execution · helpers = blocking seat waits + idle gaps tied to a helper-wait completion · idle = unmatched gaps · other = inside-step text, tails and unknown time (idle/other shown from 5%). Active shares below exclude helpers and idle; concurrent own work is never helper idle.';
    const STREAM_LEGEND_CLAUDE='Claude · each run\'s second line = minutes and shares of wall time from the host\'s retained stream records over that run alone: wait = the successful attempt\'s first output (retries separate) · think = the native thinking window (a thinking block\'s open to close, narration excluded: a span, not pure compute) · write = observed tool-block start to input stop (a buffered burst can hide earlier generation) · tools = tool dispatch to result, not tool-body-only · helpers = blocking seat waits + idle gaps tied to a successful native task/run completion · idle = unmatched gaps · other = inside-step text, tails and unknown time (idle/other shown from 5%). Active shares below exclude helpers and idle; concurrent own work is never helper idle. The long form names buffering, missing clocks and native-versus-arrival clock disagreements; a run with no host stream recording says so in place of the line. Resume generations have separate phase lines.';
    const STREAM_TOOLS_NOTE='Tools split: tests = blocking executed test-runner time; refresh = blocking map refreshes and their job-status calls; other = other tool elapsed time. Tests + refresh + other equals the tools union (overlap disclosed), not CPU time. Tests in background and refreshes in background are separate concurrent clocks, not blocking cost. A later explicit wait/poll on that job moves just its waited interval into blocking tools. Mixed test/work shells stay other tools and are flagged; refresh/work mixtures do too. Unmatched/unfinished background runs are never extrapolated. Helper suites count on the helper, not again on its parent.';
    const teleStreams=seg=>Array.isArray(seg.stream?.runs)&&seg.stream.runs.length?seg.stream.runs:seg.stream?[seg.stream]:[];
    function teleText(entry,live) {
      const lines=['Telemetry · oc.sh telemetry --sid '+entry.sid+' (--full for message indexes) · context in KT (kilo-tokens)'+
        (live?'\nso far: running now, or active in the last 15 minutes; these numbers can still grow':'')];
      const resumed=entry.segments.some(seg=>seg.kind==='follow_up')?' · a follow-up\'s carried = the context it already held when it arrived, src@ = at its first source read':'';
      lines.push(entry.phases?(entry.unit?entry.unit+' · ':'')+'implementer · start-up = context at the first source read · edit@ = at the first edit · report@ = at the first own-socket write after the last edit · end = at the last response'+resumed+' · minutes = loading/recon/implementation/finalization · ⚑ = review flags (listed below)':
        (entry.role==='unknown'?'No role named in its records':ROLE_TITLE(entry.role))+': phases are not classified for this role yet; context at its first and last response and the wall clock only');
      if(entry.streamPhases)lines.push(entry.backend==='claude'?STREAM_LEGEND_CLAUDE:entry.role==='HELPER'?STREAM_LEGEND_GPT.replace(/^GPT/,'OpenCode helper'):STREAM_LEGEND_GPT);
      if(entry.streamPhases)lines.push(STREAM_TOOLS_NOTE);
      for(const seg of entry.segments){if(seg.line)lines.push(seg.line);for(const stream of teleStreams(seg))if(stream.full)lines.push('  '+stream.full);for(const f of seg.flags || [])lines.push('  ⚑ '+f);for(const n of seg.notes || [])lines.push('  · '+n);}
      return lines.join('\n');
    }
    // @beacon[
    //   id=opc-pane@teleSpan,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Shared gray telemetry grid: context moments per job plus GPT/Claude phase lines (native resume generations separately), with tests/other tools, helper waits and honest recording-unavailable lines. Phase chips wrap from the moments' column; hover/tap preserves each exact run's active shares, clock/buffering/mixed-work flags and CLI command.,
    // ]
    function teleSpan(sid,busy) {
      const entry=state.tele.get(sid),segs=Array.isArray(entry?.segments)?entry.segments:[];
      const block=element('span',undefined,'session-tele');let live=false,cols=0;
      segs.forEach((seg,n)=>{
        const text=[seg?.short,seg?.full].find(t=>typeof t==='string' && t);
        if(!text)return;
        const now=n===segs.length-1 && (seg.live===true || busy),resumed=seg.kind==='follow_up';live=live || now;
        const run=element('span',undefined,'tele-run'+(resumed?' tele-resumed':''));run.dataset.run=String(seg.segment ?? n);
        // Cells: the mark, each moment, each arrow and the ⚑, one grid column each across the row's runs. A moment and the
        // arrow (or ⚑) after it are one unit, so a row too narrow for the grid wraps between moments (teleFit).
        const flag=/^(.*?)( ⚑\d+)$/.exec(text),moments=(flag?flag[1]:text).split(' → '),body=element('span',undefined,'tele-body');
        run.append(element('span',(resumed?'↪️':'▶️')+' ','tele-mark'),body);
        moments.forEach((moment,i)=>{
          const unit=element('span',undefined,'tele-unit');unit.append(element('span',moment,'tele-cell'));
          if(i<moments.length-1)unit.append(element('span',' → ','tele-arrow'));else if(flag)unit.append(element('span',flag[2],'tele-flag'));
          body.append(unit);
        });
        if(now){const unit=element('span',undefined,'tele-unit');unit.append(element('span',' so far','tele-tail'));body.append(unit);}
        cols=Math.max(cols,2*moments.length+(flag?1:0));
        block.append(run);
        // A GPT or Claude run's phase line (oc_telemetry pane_stream_phases) under its own line, from the moments'
        // column to the row's end; it wraps between its parts, never inside one.
        for(const stream of teleStreams(seg))if(typeof stream.line==='string' && stream.line) {
          const phases=element('span',undefined,'tele-phases');phases.dataset.run=run.dataset.run;
          if(stream.available===false)phases.dataset.available='false';
          if(stream.generation!=null)phases.dataset.generation=String(stream.generation);
          stream.line.split(' · ').forEach((part,i)=>{if(i)phases.append(element('span',' · ','tele-sep'));phases.append(element('span',part,'tele-phase'));});
          block.append(phases);
        }
      });
      if(!block.children.length)return null;
      block.style.setProperty('--tele-cols',String(cols));
      block.title=teleText(entry,live);
      if(sidebarOnly){
        block.setAttribute('aria-expanded',String(state.teleOpen.has(sid)));
        pressable(block,()=>{if(state.teleOpen.has(sid))state.teleOpen.delete(sid);else state.teleOpen.add(sid);inventory();});
      }
      return block;
    }
    // @beacon[
    //   id=opc-pane@teleFit,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Fits each drawn row's telemetry grid (teleSpan) to its row: a grid wider than the row wraps instead (tele-wrap: each run's moments flow onto the lines they need, between moments, every cell keeping its grid column's width, so the runs still line up), and goes back to the grid once the row is wide enough (a GPT or Claude run's phase line is indented by the mark column's width). Reads, then writes; run on every list redraw before the reading position is restored, and when the list's width changes; a list not laid out (the phone's hidden Programs frame) is fitted on its first frame on screen (an animation frame, or the width observer).,
    // ]
    function teleFit(retry=false) {
      // A list not laid out (the phone's Programs frame hidden behind Chat) cannot be measured. It is fitted on its first
      // frame on screen: by an animation frame where the browser holds a hidden frame's back until it shows, else by the
      // width observer (wire), which saw the list at zero width and sees it come back.
      const scroller=$('sessions-scroll');
      if(scroller && !scroller.clientWidth) {
        if(!retry && !state.teleRetry && typeof view?.requestAnimationFrame==='function'){state.teleRetry=true;view.requestAnimationFrame(()=>{state.teleRetry=false;teleFit(true);});}
        return;
      }
      const blocks=[],changes=[],leaves=run=>[...run.querySelectorAll('.tele-mark,.tele-cell,.tele-arrow,.tele-flag')];
      for(const box of [$('sessions-pinned'),$('sessions-list')])if(box?.querySelectorAll)blocks.push(...box.querySelectorAll('.session-tele'));
      for(const block of blocks) {
        const room=block.clientWidth;if(!room)continue;
        if(block.classList.contains('tele-wrap')){if(Number(block.dataset.gridWidth)<=room)changes.push({block});continue;}
        if(block.scrollWidth<=room)continue;
        const widest=[...block.children].map(leaves).reduce((a,b)=>b.length>a.length?b:a,[]);
        changes.push({block,grid:block.scrollWidth,widths:widest.map(n=>n.getBoundingClientRect().width)});
      }
      for(const {block,grid,widths} of changes) {
        block.classList.toggle('tele-wrap',!!widths);
        if(widths)block.dataset.gridWidth=String(grid);else delete block.dataset.gridWidth;
        for(const run of block.children)leaves(run).forEach((n,i)=>{n.style.minWidth=widths?.[i]?widths[i]+'px':'';});
        // A GPT or Claude run's phase line starts where the moments do: past the mark column's width.
        for(const phases of block.querySelectorAll('.tele-phases'))phases.style.paddingLeft=widths?.[0]?widths[0]+'px':'';
      }
    }
    // The plain-words key to the rows' telemetry, in each page's "list info" block (desktop sidebar and phone Programs).
    // A paragraph is a string or a list of runs; a run is a string or [tag, text] (b, code).
    const TELE_KEY=[
      ['h','Key to the telemetry data'],
      ['p','The gray lines under each session\'s times show how much the agent was holding in its memory (its context) at four moments of its job, in thousands of tokens (KT), and how long each stretch took.'],
      ['p',[['b','Each run gets its own line'],', oldest first, opened by a mark: ▶️ the agent\'s first run, ↪️ each follow-up message that resumed it (one more line per follow-up): the four moments of that run alone, its first bracket the time from the follow-up\'s arrival to its first read of the code. The numbers line up in columns from line to line, and ',['b','so far'],' ends the line of a run still going.']],
      ['h','The four numbers, in order:'],
      ['ol',[['Start-up:',' the context when the agent first read source code. Everything before this was loading: its instructions, the job and its materials.'],
        ['First edit:',' the context when it first changed the code (or, for a writing job, the page it is producing).'],
        ['Report:',' the context when it started writing its report, after its last edit.'],
        ['End:',' the context at its last reply.']]],
      ['p',[['b','The minutes in brackets'],' belong to the stretch that ends at that column\'s moment, always in the same column: the first is loading, the second reading the code, the third making the changes, the fourth writing the report. A 0 means under half a minute.']],
      ['p',[['b','A dash'],' means that moment hasn\'t happened yet (or never did).']],
      ['p',[['b','Parentheses around a whole cell'],' mean that stretch is still running: the agent is in it right now, and the cell shows its context so far and its minutes so far, both still growing. For example ',['code','343 (0) → 376 (3) → (419 KT, 8 min) → —'],': the agent has been making changes for 8 minutes, is at 419 KT, and hasn\'t started its report.']],
      ['p',[['b','A finished run'],' whose agent never edited (or never reported) keeps that stretch\'s minutes beside the dash in its own column: ',['code','62 (0) → — (11 min) → — → 186 KT'],'.']],
      ['p',[['b','⚑ and a number:'],' how many of these moments were placed by a best guess rather than read straight off a clear step. Tap the line (phone) or point at it (computer) to see which, and why.']],
      ['p',[['b','The long form'],' (in that same tap or pointer view) says the same thing with labels: ',['code','start-up 50.3 KT · edit@57.2 KT · report@427 KT · end 481 KT · 0/0/96/14 min'],'. Its minutes come as one group, in order: loading, reading the code, making the changes, writing the report; a dash there means that stretch never began, and the one before it ran on.']],
      ['p',[['b','GPT sessions'],' have a second line under each run: minutes and shares of ',['b','wall time'],', e.g. ',['code','wall · wait 2.4m 7% · think 1.6m 5% · write 2.5m 8% · tools 1.4m 4% · helpers 24m 75%'],'. ',['b','wait'],' is waiting for the model\'s first output, ',['b','think'],' its thinking, ',['b','write'],' writing its tool calls, ',['b','tools'],' its own tools running. ',['b','helpers'],' separates blocking seat waits and idle gaps tied by shell ID to a successful background seat-wait completion; concurrent own work is never helper idle. ',['b','idle'],' (unmatched gaps) and ',['b','other'],' (inside-step text, tails, unknown time) appear from 5%. Hover/tap adds shares of ',['b','active time'],' (wall minus helpers and idle, including inside-step other), the blocking/background split and the exact ',['code','oc.sh telemetry --sid … --phases'],' command. Mixed wait/work shells cannot be split and remain tools with a flag; missing completion receipts stay idle. Running steps are not extrapolated.']],
      ['p',[['b','Claude sessions'],' have the same second line, measured from retained host streams: ',['b','wait'],' is the successful attempt\'s first output, with retries separate; ',['b','think window'],' is the native thinking-block interval, excluding witnessed narration, not pure compute; ',['b','write stream'],' is observed tool-block start to input stop (buffering can hide earlier generation); ',['b','tools elapsed'],' is dispatch to result, not tool-body-only. Resume generations have separate lines. Missing host recordings show ',['code','phases unavailable · no host stream recording'],'. Hover/tap names native/arrival clock disagreements and other ambiguities.']],
      ['p',[['b','Tests and other tools'],': ',STREAM_TOOLS_NOTE]],
      ['p',[['b','Other roles'],' (Conductors, recons, helpers, auditors) show ',['code','context A → B KT · N min'],' on each run\'s context line. Their worker job-context phases are not classified; supported GPT/Claude and OpenCode-helper stream timing is the second line. A fork starts at its own launch message: A includes the base material it inherited, but its minutes and growth exclude the base\'s work.']]];
    function teleKey() {
      const box=$('telemetry-key');if(!box || box.children?.length)return;
      const runs=(node,parts)=>{for(const part of typeof parts==='string'?[parts]:parts)node.append(typeof part==='string'?element('span',part):element(part[0],part[1]));return node;};
      for(const [kind,body] of TELE_KEY) {
        if(kind==='ol'){const list=element('ol');for(const [term,text] of body)list.append(runs(element('li'),[['b',term],text]));box.append(list);}
        else box.append(runs(element(kind==='h'?'h4':'p'),body));
      }
    }
    // Phone: the whole account under the row, opened by a tap on its telemetry.
    function teleDetail(sid,busy) {
      const entry=state.tele.get(sid);
      if(!sidebarOnly || !entry || !state.teleOpen.has(sid))return null;
      return element('div',teleText(entry,entry.segments.at(-1)?.live===true || busy),'tele-detail');
    }
    // @beacon[
    //   id=opc-pane@ocItem,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=OpenCode row shares the sidebar's left gutter and a full-two-line action rail (copy and glance), with durable star, fresh activity and viewport-only details. Actual children alone get a tree expander. Desktop Manager selection validates Send; phone keeps its glance/tree/Read adapter.,
    // ]
    function ocItem(it,crumb=null,treeHidden=false,tap=null) {
      const r=it.row,roleName=nodeRole(it),open=state.glances.has(r.id),entry=activityOf(it.key),fact=ocEvidence(r),waits=blockingWaits(r);
      const item=element('div',undefined,'session-item oc-item');item.classList.toggle('active',entry.own==='busy');item.classList.toggle('glancing',open);
      const button=element('button',undefined,'session-open');button.type='button';button.dataset.anchor=it.key;
      const waitText=waits.length?waits.map(waitDescription).join('\n'):entry.own==='waiting'?'Waiting (best effort): idle, while a session started under it is busy. '+OC_WAITING_NOTE:null;
      item.classList.toggle('reveal-flash',flashing(it.key));
      const line=nameLine(roleName,handleName(nodeName(it),treeHandle(it)),waitText,waits.length?(waits.some(w=>w.recogniser.kind==='doorbell')?'🔔 Waiting on helper':'🗺️ Map refresh'):'waiting');
      const badge=rollupBadge(entry,{treeHidden,agentsHidden:false});if(badge)line.append(badge);button.append(line);
      item.classList.toggle('in-active-program',!!crumb && it.home.active===true);
      const meta=element('span',undefined,'session-meta');
      // A freestanding row has no program row above it to name its Manager Session, so it names it itself.
      const forManager=it.home.forest && typeof r.managerSession==='string'?'for Manager-Session-'+r.managerSession:null;
      const status=fact.busy===null?(state.oc.stale?'OpenCode unavailable (last known) · ':'')+'activity unknown (last observation stale/unavailable)':entry.own==='busy'?'busy':'idle';
      const statusNode=element('span',' '+[status,fact.toolStatus==='unavailable'?'running tool details unavailable':null,r.outcome,shortModel(r.model),dollars(r.cost),forManager,roleName==='manager'?'Manager · select to Send':null].filter(Boolean).join(' · '),'meta-status');
      statusNode.title=[fact.at?'Busy/idle observed '+clock(fact.at/1000):'Busy/idle from the session inventory',fact.error,state.oc.stale?'Session names/metadata are last known; inventory unavailable':null].filter(Boolean).join('\n');
      meta.append(backendChip('opencode'),statusNode);
      // Its own first and latest turn last on the line, once read (the same read as the hover); its telemetry under it.
      teleWant(r.id,button);
      const tele=teleSpan(r.id,entry.own!=='quiet');
      let shown=null;
      const showTimes=()=>{
        const reading=button.isConnected?listAnchor():null;
        const t=state.ocTurns.get(r.id),d=t && t.sig===ocTurnSig(r) && !t.error?t.data:null;
        const times=d?rowTimes(d.firstAt,d.lastAt,ocTurnLine(r)):null;
        for(const node of shown || [])node.remove();
        shown=times?[element('span',' '),times]:null;
        meta.classList.toggle('meta-times',!!times);for(const node of shown || [])meta.append(node);
        if(reading){restoreList(reading);rememberList();}
      };
      showTimes();
      button.append(meta);
      if(tele)button.append(tele);
      if(it.note)button.append(element('span',it.note,'session-note'));
      if(crumb)button.append(element('span',crumb,'session-crumb'));
      const how=sidebarOnly?(tap?'Tap to open or close the sessions it started; 💬 opens a glance.':'Tap for a glance under this row; its Read conversation › opens the whole conversation in Chat.'):
        roleName==='manager'?'Click to read its whole conversation here; selecting a validated Manager targets Send/Refine in the editor. 💬 opens a quick glance under this row.':
        'Click to read its whole conversation here (view only); 💬 opens a quick glance under this row.';
      const title=()=>[ocTurnLine(r),r.title,r.id,'OpenCode · '+(r.agent || 'agent unknown')+(r.model?' · '+r.model:''),it.note,how].filter(Boolean).join('\n');
      button.title=title();
      const hover=()=>{detailWant('turn:'+r.id,button,entry.any,r.updatedAt || r.createdAt,()=>ocTurns(r).then(()=>{button.title=title();showTimes();}));detailFlush();};
      button.onpointerenter=hover;button.onfocus=hover;
      detailWant('turn:'+r.id,button,entry.any,r.updatedAt || r.createdAt,()=>ocTurns(r).then(()=>{button.title=title();showTimes();}));
      const actions=element('span',undefined,'session-actions'),copy=element('span','⧉','session-copy');
      copy.title='Copy session ID '+r.id;copy.setAttribute('aria-label','Copy session ID');pressable(copy,()=>copyText(copy,r.id));actions.append(copy);
      const glanceChip=()=>{
        const chip=element('span','💬','oc-glance-chip');chip.title=(open?'Close':'Open')+' a glance at this OpenCode session';
        chip.setAttribute('aria-label',chip.title);chip.setAttribute('aria-expanded',String(open));pressable(chip,()=>void toggleGlance(r.id));return chip;
      };
      if(sidebarOnly) {
        // Phone: a row tap is a glance (or its tree, below); the glance's Read conversation › opens it in Chat.
        button.setAttribute('aria-expanded',String(open));
        button.onclick=()=>void toggleGlance(r.id);
        item.append(button);
        // Phone: a row with sessions under it opens or closes them; its glance moves to a small chip.
        if(tap){button.onclick=tap.toggle;button.setAttribute('aria-expanded',String(tap.open));}
      } else {
        // Desktop: the row opens its whole conversation in the same viewer; the 💬 chip alone opens the glance.
        const selected=ocSelected(r.id);item.classList.toggle('selected',selected);
        button.setAttribute('aria-pressed',String(selected));
        button.onclick=()=>{showPane('conversation');void openOpenCode(r);};
        item.append(button);
      }
      actions.append(glanceChip());item.append(actions);
      const links=waitLinks(waits);if(links)item.append(links);
      const star=starButton('session:opencode:'+r.id);if(star)line.append(star);
      const detail=teleDetail(r.id,entry.own!=='quiet');if(detail){detail.dataset.anchor=it.key+':tele';item.append(detail);}
      if(open){const glance=glanceBox(r.id,{title:shortTitle(r.title) || r.title || r.id,label:ROLE_BADGE[roleName] || 'OpenCode'});glance.dataset.anchor=it.key+':glance';item.append(glance);}
      return item;
    }
    // ------------------------------------------------------------- the list's reading position
    // Every redraw replaces the whole list, which leaves the browser's own scroll anchoring nothing to hold
    // (and iOS WebKit, Chrome on iOS included, has none), so the list keeps its own: rows carry data-anchor
    // keys that survive redraws and section moves. Prefer rows whose starts are readable BELOW the sticky
    // program header: an earlier partly covered row can grow at its bottom and push the row being read
    // away without moving its own top. Partial rows remain fallbacks, not the first anchor. A stuck
    // program head itself is never passive; a deliberate tap still anchors it under the finger.
    const LIST_ANCHORS=6,LIST_SETTLE_MS=300,LIST_HOLD_MS=6000;
    function anchorBox(node) { return node.classList.contains('program-head')?node.parentNode:node; }
    // @beacon[
    //   id=opc-pane@listAnchor,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Explicit desktop/phone reading anchors: first prefer row starts below the sticky program header, with partially covered rows only as fallbacks, so late sibling/telemetry growth cannot pin an obscured predecessor and push the reader away. Stable keys/offsets survive redraws; tapped heads, whole-pixel WebKit slack and touch-scroll holds are retained.,
    // ]
    function listAnchor() {
      const scroller=$('sessions-scroll'),list=$('sessions-list');
      // At the very top, nothing is held: new rows simply appear above, as before.
      if(!scroller?.getBoundingClientRect || !list?.querySelectorAll || !scroller.clientHeight || !(scroller.scrollTop>0))return null;
      const top=scroller.getBoundingClientRect().top,bottom=top+scroller.clientHeight,rows=[],partial=[];
      const nodes=list.querySelectorAll('[data-anchor]');let readableTop=top;
      for(const node of nodes)if(node.classList.contains('program-head')){
        const r=node.getBoundingClientRect();
        if(r.top<=top+0.5 && r.bottom>top && anchorBox(node).getBoundingClientRect().top<r.top-0.5)
          readableTop=Math.max(readableTop,r.bottom);
      }
      // What the last restore could not place (whole-pixel scroll positions: restoreList) is taken off while the list is
      // still where it put it, so each redraw aims at the place Dan was reading, never at the last rounding of it.
      const slack=state.listSlackTop===scroller.scrollTop?state.listSlack:0;
      for(const node of nodes) {
        const r=node.getBoundingClientRect();if(!r.height || r.bottom<=readableTop)continue;
        if(r.top>=bottom)break;
        const y=anchorBox(node).getBoundingClientRect().top;if(r.top>y+0.5)continue; // a stuck head
        (r.top>=readableTop-0.5?rows:partial).push({key:node.dataset.anchor,group:node.closest('.session-group')?.dataset.group,y:y-top-slack});
      }
      const anchors=[...rows,...partial].slice(0,LIST_ANCHORS);
      return anchors.length?anchors:null;
    }
    // Remembered while the list is on screen (a hidden list keeps the last one for when it shows again).
    function rememberList() { if($('sessions-scroll')?.clientHeight)state.listAnchor=listAnchor(); }
    // The first anchor still drawn goes back to its distance from the top; a stub that gave way to its
    // entry is found as the entry. A tapped row (state.listHint) goes first: it stays under the finger.
    function restoreList(rows) {
      const scroller=$('sessions-scroll'),list=$('sessions-list');
      if(!rows || !scroller?.getBoundingClientRect || !list?.querySelectorAll || !scroller.clientHeight)return;
      const drawn=new Map();
      for(const node of list.querySelectorAll('[data-anchor]')){const key=node.dataset.anchor;if(!drawn.has(key))drawn.set(key,[]);drawn.get(key).push(node);}
      for(const want of rows) {
        const nodes=drawn.get(want.key) || (want.key.startsWith('stub:')?drawn.get(want.key.slice(5)):null);
        const node=nodes?.find(n=>n.closest('.session-group')?.dataset.group===want.group) || nodes?.[0];
        if(!node)continue;
        const top=scroller.getBoundingClientRect().top,delta=anchorBox(node).getBoundingClientRect().top-top-want.y;
        // WebKit (iOS) keeps whole-pixel scroll positions and drops the fraction of any it is given, so each correction
        // landed up to a pixel low and the next capture started from there: a creep. The position set is rounded, and
        // what is left over at it (under a pixel) is remembered for the next capture (listAnchor).
        if(Math.abs(delta)>=0.5){scroller.scrollTop=Math.round(scroller.scrollTop+delta);state.listOwnTop=scroller.scrollTop;}
        const left=anchorBox(node).getBoundingClientRect().top-top-want.y;
        state.listSlack=Math.abs(left)<1?left:0;state.listSlackTop=scroller.scrollTop;
        return;
      }
    }
    function tappedAnchor(target) {
      const scroller=$('sessions-scroll'),node=target?.closest?.('#sessions-list [data-anchor]');
      if(!node || !scroller?.getBoundingClientRect)return null;
      return {key:node.dataset.anchor,group:node.closest('.session-group')?.dataset.group,y:node.getBoundingClientRect().top-scroller.getBoundingClientRect().top};
    }
    // A finger on the list, or a scroll still moving (a flick's momentum), holds background redraws back, but never
    // for long: a lost touch end must not freeze the list. A touch with no movement for LIST_HOLD_MS counts as lifted,
    // and a redraw held for LIST_HOLD_MS goes ahead whatever the flags say.
    function listBusy() {
      const now=Date.now();
      if(state.listHeldAt && now-state.listHeldAt>=LIST_HOLD_MS)return false;
      return (state.listTouch && now-state.listTouchAt<LIST_HOLD_MS) || now-state.listScrolledAt<LIST_SETTLE_MS;
    }
    function settleList() {
      if(state.listSettle)return;
      state.listSettle=setTimeout(()=>{state.listSettle=null;if(!state.inventoryDirty || state.renaming)return;if(listBusy())settleList();else inventory();},LIST_SETTLE_MS);
    }
    // @beacon[
    //   id=opc-pane@renderClaudeInventory,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Rebuild shared desktop/phone rows with a sticky Manager family above Running now, Favorites and homes. Equal left gutters and two-line action rails replace tiny corner icons; native expanders require known children, paging/retry controls sit on metadata, and an empty child footer never draws. Waits, anchors, stars, detail lanes and home paging stay independent.,
    // ]
    function inventory(now=false) {
      state.fallbackManager=null; // one fallback choice per render, shared by every family's membership test
      const reading=state.listHint?.row?[state.listHint.row,...(listAnchor() || [])]:listAnchor();
      for(const row of state.inventory){const facts=state.claudeTurns.get(row.uuid);if(facts?.data && facts.generation===row.generation)row.turnFacts=facts.data;}
      const fragment=doc.createDocumentFragment(),pinned=doc.createDocumentFragment(),groups=new Map(),placements=new Map(),unfolded=new Set(),filter=$('sessions-role').value || 'all';let shown=0,matching=0;
      // Open (live, writable) sessions first, then everything newest created first: the
      // creation stamp is the transcript's first record (server-supplied), never the name.
      const created=row=>typeof row.createdAt==='number' && Number.isFinite(row.createdAt) ? row.createdAt : 0;
      const extra=state.archive?[]:familyRows(state.managerFamilyRows?.claude),known=new Set(state.inventory.map(identityOf));
      const rows=[...state.inventory,...extra.filter(row=>!known.has(identityOf(row)))].sort((a,b)=>Number(writable(b))-Number(writable(a)) || created(b)-created(a)),primaries=rows.filter(r=>role(r)!=='subagent');
      const parentOf=child=>primaries.find(p=>p.uuid===child.parentUuid),nested=new Map();
      if(filter==='all')for(const row of rows)if(role(row)==='subagent'){const p=parentOf(row);if(p){if(!nested.has(p.sessionRef))nested.set(p.sessionRef,[]);nested.get(p.sessionRef).push(row);}}
      // A host session's own native transcript ("Native history boundary") is the same
      // conversation: it is offered under its host row, never listed as a second session.
      const hostUuids=new Set(primaries.filter(r=>r.source==='host' && r.uuid).map(r=>r.uuid)),twins=new Map();
      for(const row of primaries)if(row.source!=='host' && row.uuid && hostUuids.has(row.uuid) && !twins.has(row.uuid))twins.set(row.uuid,row);
      // By handle, else by UUID: a pane-server restart re-issues every handle, and a watched parent
      // matched by neither shows its children twice (nested AND in the detached block).
      const watched=state.childParent,watchedRow=!watched?null:role(watched)==='subagent'?parentOf(watched):
        primaries.find(p=>p.sessionRef===watched.sessionRef) || primaries.find(p=>p.uuid && p.uuid===watched.uuid) || null;
      const block=$('subagents-block');let placed=false;
      // Sessions with launch edges (Claude host rows, OpenCode's rows) form one tree per program, plus the
      // freestanding forest of quick workers and helpers outside any program.
      const ocKnown=new Set(state.oc.rows.map(row=>row.id)),ocFamily=state.archive?[]:familyRows(state.managerFamilyRows?.opencode,true);
      const tree=lineageTree(primaries,[...state.oc.rows,...ocFamily.filter(row=>!ocKnown.has(row.id))],state.listWindowDays),inTree=new Set();state.tree=tree;state.treeDefaults=new Map();
      if(state.reveal && !state.reveal.opened && Date.now()-state.reveal.at<REVEAL_MS)revealOpen(); // a session drawn for the first time
      for(const it of tree.byId.values())if(it.backend==='claude')inTree.add(it.row);
      const roots=rows.filter(row=>!inTree.has(row) && !(filter==='all' && role(row)==='subagent' && parentOf(row)) && twins.get(row.uuid)!==row);
      state.activity=sidebarActivity(tree,roots);
      for(const p of tree.programs){p.live=p.members.some(memberLive);p.active=activityOf(p.key).any;}
      renderChildren();
      if(state.renaming){state.inventoryDirty=true;return;} // never rebuild the DOM under a rename in progress
      // Nor under a finger on the list or a moving scroll (a tap is never held back): settleList redraws once it rests,
      // or once it has been held LIST_HOLD_MS.
      if(now!==true && !state.listHint && listBusy()){state.inventoryDirty=true;if(!state.listHeldAt)state.listHeldAt=Date.now();settleList();return;}
      state.inventoryDirty=false;state.listHeldAt=0;state.teleWanted=new Map();state.detailWanted=new Map();
      const folds=new Map(tree.programs.map(p=>[p.key,conductorRuns(p)]));
      const programGroup=kind=>kind==='running'?{key:'programs:running',label:'Running now',programs:true,
        title:'Everything active now: programs, Manager Sessions and other sessions with something busy (the yellow spinner) or waiting, in them or under them. Each keeps a dim stub at its usual place.'}:
        kind==='favorite'?{key:'programs:favorite',label:'Favorite programs',programs:true,
        title:'Programs starred with ★: full programs first, then quick programs, each in home order. Running programs stay in Running now, with yellow stubs here at the top. Every favorite is listed, without Show more; session stars do not add favorites.'}:
        {key:'programs:'+kind,label:kind==='quick'?'Quick programs':'Full programs',programs:true,
        title:(kind==='quick'?'Quick programs (Conductor-based)':'Full programs')+': each program → its Conductor sessions by number → the seats each one started → their helpers, Claude Code and OpenCode together. A session whose parent is not listed sits one level higher, with a note.'};
      // A group holds programs (program groups) and entries, backend-neutral: a flat root row
      // {key:'r:…',row}, a freestanding tree {key:'s:…',node} or, under a filter, one flattened tree
      // node {key:'s:…',node,crumb}. size = the sessions an entry stands for (its group's count).
      const groupFor=g=>{let group=groups.get(g.key);if(!group){group={...g,entries:[],programs:g.programs ? [] : null};groups.set(g.key,group);}return group;};
      const sessionsIn=entries=>entries.reduce((sum,entry)=>sum+entry.size,0);
      if(filter==='all') {
        if(!state.archive)groupFor(programGroup('favorite'));
        for(const p of tree.programs){const group=groupFor(programGroup(p.kind)),place=placeOf(p);placements.set(p.key,place);
          group.programs.push(p);matching+=p.count;
          if(programStarred(p))groupFor(programGroup('favorite')).programs.push(p);
          if(place==='running')groupFor(programGroup('running')).programs.push(p);}
        for(const it of tree.forest.top){const entry={key:it.key,node:it,created:it.created,size:subtree(it).length},place=placeOf(entry);
          placements.set(entry.key,place);groupFor(forestSection(it)).entries.push(entry);matching+=entry.size;
          if(place==='running')groupFor(programGroup('running')).entries.push(entry);}
      } else {
        // Flattened with a breadcrumb: a program's node in its program group (Running now while the program is
        // active), a freestanding node in its home section (filtered views ignore Running now, like flat roots).
        for(const it of tree.byId.values())if(nodeRole(it)===filter){
          const group=groupFor(it.home.forest?forestSection(it):programGroup(activityOf(it.home.key).any?'running':it.home.kind));
          group.entries.push({key:it.key,node:it,crumb:breadcrumb(it),created:it.created,size:1});matching++;}
      }
      for(const row of rows) {
        if(inTree.has(row))continue; // listed in its tree
        if(filter!=='all' && realRole(row)!==filter)continue;
        if(filter==='all' && role(row)==='subagent' && parentOf(row))continue; // shown under its parent
        if(twins.get(row.uuid)===row)continue; // shown under its host row
        const entry={key:'r:'+identityOf(row),row,created:created(row),size:1};
        groupFor(groupOf(row)).entries.push(entry);matching++;
        if(filter==='all') {
          const place=placeOf(entry);placements.set(entry.key,place);
          if(place==='running')groupFor(programGroup('running')).entries.push(entry);
        }
      }
      // Program groups newest first; home sections keep open (writable: the green dot) sessions first, then newest
      // first, freestanding trees by their root's creation among the flat rows. Manager Sessions orders by number
      // instead (manager-order.js: Manager-Session number highest first, each primary followed by its sub-sessions,
      // both backends together; a green dot never moves a row). A page without manager-order.js keeps newest first.
      const liveEntry=entry=>!!entry.row && writable(entry.row),managerOrder=globalThis.opcManagerOrder?.compare;
      const managerKey=entry=>{const row=entry.row || entry.node.row;
        return {names:entry.node?.backend==='opencode'?[row.title]:[row.sessionLabel,row.title],created:entry.created,id:entry.key};};
      const homeOrder=group=>group.key===MANAGER_SECTION.key && managerOrder?(a,b)=>managerOrder(managerKey(a),managerKey(b)):(a,b)=>Number(liveEntry(b))-Number(liveEntry(a)) || b.created-a.created;
      for(const group of groups.values())group.entries.sort(group.programs?(a,b)=>b.created-a.created:homeOrder(group));
      // Pin in the SAME Manager order as home, not in backend/read/creation order; the selected sub-session never
      // jumps ahead of its primary, and a closed sibling never falls back into the lists below.
      if(filter==='all')for(const entry of groups.get(MANAGER_SECTION.key)?.entries || [])if(placements.get(entry.key)==='pinned')
        pinned.append(entry.node?treeNode(entry.node,{...entry,groupKey:MANAGER_SECTION.key}):item(entry.row));
      // The listed Managers' names, for the editor's ↳ New sub-session (opcSidebar.managerNames); a view filtered to
      // another role keeps the last full list.
      if(groups.has(MANAGER_SECTION.key) && ['all','manager'].includes(filter))state.managerNames=groups.get(MANAGER_SECTION.key).entries.map(entry=>managerKey(entry).names);
      // Active work leads in Running now; home sections have the fixed structural order.
      // Favorite programs is the one flat section between Running now and all the homes.
      const rank=g=>g.key==='programs:running'?-1:g.key==='programs:favorite'?-.5:g.key==='managers'?0:g.key==='programs:full'?1:g.key==='programs:quick'?2:g.key==='folder:quick-worker'?3:g.key==='folder:forklifts'?3.5:g.key==='folder:manager-sessions'?4:5;
      const ordered=[...groups.values()].sort((a,b)=>rank(a)-rank(b));
      // foldTree's fold-to-active (phone "Show running", desktop ⊟'s second click) opens exactly the
      // sections that draw active work in full (everything else has a stub at home while Running now shows it).
      state.groupKeys=ordered.map(g=>g.key);
      state.activeGroups=new Set(ordered.filter(g=>filter==='all'?g.key==='programs:running':
        g.entries.some(e=>activityOf(e.key).any) || (g.programs || []).some(p=>activityOf(p.key).any)).map(g=>g.key));
      // freestanding: a node outside any program (no program row above it names its Manager Session).
      // tap (phone only): {open,toggle} of the tree under this row; a tap anywhere on the row toggles it.
      function item(row,{node=null,crumb=null,inActive=false,activityKey=node?.key || 'r:'+identityOf(row),treeHidden=false,freestanding=!!node?.home.forest,tap=null}={}) {
        const item=element('div',undefined,'session-item'),key=row.sessionRef,isWatched=watchedRow===row,twin=row.source==='host'?twins.get(row.uuid):null;
        const entry=activityOf(activityKey),kids=nested.get(key) || [],cached=state.childCache.get(key);
        const nativeKids=isWatched && state.childLoaded?state.children:[...(cached?.rows || []),...kids];
        const canExpand=role(row)!=='subagent' && nativeKids.length>0;
        const expanded=!!canExpand && (isWatched?!state.collapsed.has(key):state.expanded.has(key));
        item.classList.toggle('selected',isSelected(row));item.classList.toggle('live',writable(row));item.classList.toggle('active',entry.own==='busy');
        item.classList.toggle('in-active-program',inActive);
        const open=element(sidebarOnly?'div':'button',undefined,'session-open');open.type='button';
        item.classList.toggle('reveal-flash',flashing(activityKey));
        const name=handleName(node?nodeName(node):displayName(row),node?treeHandle(node):typeof row.socket==='string'?row.socket.trim():null),background=backgroundCount(row);
        const waits=entry.own==='waiting'?waitsOf(row):[],recogniser=waits[0]?.recogniser;
        const waitingText=recogniser && waits.every(w=>w.recogniser===recogniser)?recogniser.flag:'waiting';
        const waitLines=waits.map(wait=>{
          const {task,recogniser}=wait;
          if(recogniser)return waitDescription(wait);
          if(task?.tool_input && typeof task.tool_input==='object') {
            const {command,description,name}=task.tool_input;
            return globalThis.opcToolPreview.preview({command,description},name);
          }
          return (task?.subagent_type || task?.task_type || 'unknown')+': '+(task?.description || 'description not recorded')+
            (task?.task_type==='local_bash'?' (command not recorded)':'');
        });
        const waiting=entry.own==='waiting'?'Waiting (waiting, not blocked): its turn is closed; it is woken when one of its '+background+' background task'+(background===1?'':'s')+' completes.'+
          (waitLines.length?'\n'+waitLines.join('\n'):''):null;
        const line=nameLine(realRole(row),name,waiting,waitingText),halted=haltOf(row);
        // A Claude Manager's context gauge replaces the green live dot (CSS drops the dot for .has-gauge).
        if(!isOpenCode(row) && row.source==='host' && role(row)==='manager'){line.append(managerGauge(row));item.classList.add('has-gauge');}
        // ⛔ a usage-limit halt (haltOf): the whole line on hover and in its glance.
        if(halted){const flag=element('span','⛔','halt-flag');flag.title=halted.line;flag.setAttribute('aria-label',halted.line);line.append(flag);item.classList.add('halted');}
        const badge=rollupBadge(entry,{treeHidden,agentsHidden:!expanded});if(badge)line.append(badge);open.append(line);
        if(row.sessionLabel && !node && typeof row.title==='string' && row.title && row.title!==row.uuid && row.title!==row.sessionLabel)open.append(element('span',row.title,'session-meta'));
        const meta=element('span',undefined,'session-meta');
        if(node || crumb)meta.append(backendChip('claude'));
        meta.append(element('span',(node || crumb?' ':'')+sessionStatus(row,!!(node || crumb) && !freestanding),'meta-status'));
        // The models and thinking levels its own turns recorded, on the same line: "(Opus 5.5 · high)",
        // then its own first and latest turn: "(first … ET · last … ET)", each its own wrap unit.
        const facts=factsParts(row.turnFacts);
        if(facts){const used=element('span',' ('+facts.models+(facts.levels.length?' · '+facts.levels.join(', '):'')+')','session-models');used.title=facts.title;meta.append(used);}
        const times=rowTimes(row.turnFacts?.firstAt,row.turnFacts?.lastAt,claudeTurnTimes(row));
        if(times){meta.classList.add('meta-times');meta.append(element('span',' '),times);}
        // More is real paging; Refresh is needed to return from a later page or retry discovery. Ordinary
        // first-page refresh is redundant with the three-second watch, so it takes no space on the row.
        if(isWatched){const controls=element('span',undefined,'session-child-controls');
          for(const [id,show] of [['refresh',state.childPaged || !!state.childError],['more',!!state.childNext]]){
            const control=childControls[id];if(control){control.hidden=!show;control.disabled=state.childBusy || id==='more' && !state.childNext;if(show)controls.append(control);}
          }
          if(controls.children.length)meta.prepend(controls); // useful controls precede any clipped status text
        }
        // A tree row's telemetry (oc.sh telemetry) on its own lines under that one, one line per run.
        const treeRow=!!(node || crumb) && typeof row.executionId==='string',tele=treeRow?teleSpan(row.executionId,entry.own!=='quiet'):null;
        if(treeRow)teleWant(row.executionId,open);
        if(row.source==='host' && CLAUDE_UUID.test(row.uuid || ''))detailWant('turn:'+row.uuid,open,entry.any,row.turnFacts?.lastAt || row.createdAt,()=>claudeTurns(row));
        if(!isWatched && row.capabilities?.subagents===true && role(row)!=='subagent' && !cached && !kids.length)
          detailWant('children:'+identityOf(row),open,entry.any,row.createdAt,()=>peekChildren(row));
        open.append(meta);
        if(tele)open.append(tele);
        if(node?.note)open.append(element('span',node.note,'session-note'));
        if(crumb)open.append(element('span',crumb,'session-crumb'));
        if(background && !ended(row))open.append(element('span','◌ '+background+' in background','session-bg'));
        open.title=[halted?.line,claudeTurnTimes(row),name,'UUID '+(row.uuid || 'unknown'),row.title && row.title!==row.uuid && row.title!==name?row.title:null,row.cwd,roleLabel(row)+' · '+roleSource(row),node?.note,hostActivityFailure(row)?'Activity last known: '+hostActivityFailure(row):null].filter(Boolean).join('\n');
        open.dataset.role=role(row);open.dataset.anchor=activityKey;open.setAttribute('aria-pressed',String(isSelected(row)));
        if(!sidebarOnly)open.onclick=()=>{showPane('conversation');void browse(row);};item.append(open);
        const star=starButton(starKey(row));if(star)line.append(star);
        const links=waitLinks(waits);if(links)item.append(links);
        const detail=treeRow?teleDetail(row.executionId,entry.own!=='quiet'):null;if(detail){detail.dataset.anchor=activityKey+':tele';item.append(detail);}
        const actions=element('span',undefined,'session-actions');
        if(readable(row)){item.classList.add('readable');item.append(readChip(claudeOpen(row,displayName(row))));}
        if(!sidebarOnly && role(row)!=='subagent' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(row.uuid || '')) {
          const pencil=element('span','✎','session-rename');pencil.title='Rename this session (display name only; an empty name restores the automatic one)';
          pencil.setAttribute('aria-label','Rename session');pressable(pencil,()=>startRename(row,open));actions.append(pencil);
        }
        if(typeof row.uuid==='string' && row.uuid){const copy=element('span','⧉','session-copy');copy.title='Copy session UUID '+row.uuid;copy.setAttribute('aria-label','Copy session UUID');pressable(copy,()=>copyText(copy,row.uuid));actions.append(copy);}
        if(twin) {
          const link=element('span','↳ its native transcript','session-twin');link.title='The same conversation as Claude Code stored it (may hold history from before the host owned it). View only.';
          link.setAttribute('aria-pressed',String(isSelected(twin)));if(!sidebarOnly)pressable(link,()=>{showPane('conversation');void browse(twin);});item.append(link);
        }
        // Its glance (toggleGlance), as on an OpenCode row: the 💬 chip, or on the phone a tap on a row with nothing under it.
        const glanceId=role(row)!=='subagent' && CLAUDE_UUID.test(row.uuid || '')?row.uuid:null,glancing=!!glanceId && state.glances.has(glanceId);
        const glanceTap=!!glanceId && sidebarOnly && !(tap || canExpand);item.classList.toggle('glancing',glancing);
        if(glanceId) {
          const chip=element('span','💬','session-glance-chip');chip.title=(glancing?'Close':'Open')+' a glance at this session: its latest messages and tool call';
          chip.setAttribute('aria-label',chip.title);chip.setAttribute('aria-expanded',String(glancing));pressable(chip,()=>void toggleGlance(glanceId,row.cwd || null));
          item.classList.add('has-glance-chip');actions.append(chip);
        }
        if(actions.children.length)item.append(actions);
        if(glancing) {
          const glance=glanceBox(glanceId,{title:name,label:ROLE_BADGE[realRole(row)] || 'Session',activity:entry.own,open:readable(row)?claudeOpen(row,displayName(row)):null,halt:halted?.line || null});
          glance.dataset.anchor=activityKey+':glance';item.append(glance);
        }
        const toggleNative=()=>{
          if(isWatched){if(state.collapsed.has(key))state.collapsed.delete(key);else state.collapsed.add(key);}
          else if(state.expanded.has(key))state.expanded.delete(key);else{state.expanded.add(key);void peekChildren(row);}
          inventory();
        };
        // Phone: the whole row opens or closes what is under it (its tree first, else its native subagents, else its glance).
        if(sidebarOnly && (tap || canExpand)){pressable(open,tap?tap.toggle:toggleNative);open.setAttribute('aria-expanded',String(tap?tap.open:expanded));}
        else if(glanceTap){pressable(open,()=>void toggleGlance(glanceId,row.cwd || null));open.setAttribute('aria-expanded',String(glancing));}
        if(canExpand) {
          const known=new Set(nativeKids.map(r=>r.sessionRef)).size;
          const toggle=element('span',expanded?'▾':'▸','session-toggle');toggle.title=(expanded?'Hide':'Show')+' native subagents';
          toggle.setAttribute('aria-expanded',String(expanded));if(known)toggle.title+=' ('+known+')';
          pressable(toggle,toggleNative);
          item.append(toggle);
          if(expanded && isWatched && state.childLoaded && block){block.hidden=false;delete block.dataset.detached;item.append(block);placed=true;}
          else if(expanded) {
            const box=element('div',undefined,'session-children'),list=element('div',undefined,'child-list'),seen=new Set();
            for(const child of nativeKids)if(!seen.has(child.sessionRef)){seen.add(child.sessionRef);list.append(childButton(child,row));}
            box.append(list);
            const notice=cached?.error?'unavailable: '+cached.error:cached?.more?'more on the parent page':'';
            if(notice)box.append(element('div',notice,'children-foot'));item.append(box);
          }
        }
        if(isWatched && state.childError && !placed && block){block.hidden=false;delete block.dataset.detached;item.append(block);placed=true;}
        shown++;
        return item;
      }
      // One session of a tree (a program's or a freestanding one) and, when open, the sessions it started.
      function treeNode(it,entry) {
        const box=element('div',undefined,'tree-node'),kids=it.children,open=kids.length?viewOpen(it.key,true,entry):false;
        if(it.lin.kind==='conductor')unfolded.add(it.key);
        const tap=kids.length?{open,toggle:()=>{setViewOpen(it.key,!open,entry);inventory();}}:null;
        if(kids.length) {
          state.treeDefaults.set(it.key,true);
          const toggle=element('span',open?'▾':'▸','tree-toggle');toggle.title=(open?'Hide':'Show')+' the '+kids.length+' session'+(kids.length===1?'':'s')+' it started';
          toggle.setAttribute('aria-expanded',String(open));pressable(toggle,tap.toggle);box.append(toggle);
        }
        box.append(it.backend==='claude'?item(it.row,{node:it,treeHidden:!!kids.length && !open,tap}):(shown++,ocItem(it,null,!!kids.length && !open,tap)));
        if(open){const sub=element('div',undefined,'tree-children');for(const kid of kids)sub.append(treeNode(kid,entry));box.append(sub);}
        return box;
      }
      // A program row: its newest Conductor's stored label, core, Manager Session and size; open while live.
      function programBlock(p,groupKey) {
        const entry={...p,groupKey},runs=folds.get(p.key),keys=programKeys(p,runs);
        state.treeDefaults.set(p.key,p.live || p.active);
        const open=viewOpen(p.key,p.live || p.active,entry),box=element('div',undefined,'program'),head=element('div',undefined,'program-head');
        box.classList.toggle('live',p.live);box.classList.toggle('active-program',p.active); // CSS paints its rectangle above the sticky head, including when collapsed
        const toggle=element('span',open?'▾':'▸','tree-toggle'),title=element('span',undefined,'program-title');
        const facts=p.facts,name=element('span',p.title,'name-text');
        if(facts.nameFade!==null){name.classList.add('program-name-age');name.setAttribute('style','--program-name-fade:'+facts.nameFade*100+'%');}
        title.append(name);
        const badge=rollupBadge(activityOf(p.key),{treeHidden:!open,agentsHidden:false});if(badge)title.append(badge);
        const minimal=keys.every(key=>viewOpen(key,key===p.key?p.live || p.active:!key.startsWith('fold:'),entry)===minimalOpen(key));
        const fold=element('span',minimal?'⊞':'⊟','program-fold');fold.title=minimal?'Open everything in this program':'Fold to what is active: everything busy or waiting stays visible';
        pressable(fold,()=>{if(!minimal)for(const run of runs)for(const member of run.members)state.unfolded.delete(member.key);setViewOpen(keys,minimal?true:minimalOpen,entry);inventory();});title.append(fold);
        const star=starButton('program:'+p.core,true);if(star)title.append(star);
        const meters=element('span',undefined,'program-meters');
        const count=element('span',undefined,'program-meter-count'),blocks=element('span',undefined,'program-count-blocks');
        const countBucket=1+[4,8,16,32,64].filter(edge=>p.count>=edge).length;count.dataset.bucket=String(countBucket);
        blocks.setAttribute('aria-hidden','true');for(let i=0;i<countBucket;i++)blocks.append(element('span',undefined,'program-count-block'));
        count.append(blocks,element('span',String(p.count),'program-meter-value'));count.title=p.count+' listed sessions · count blocks: 1–3 / 4–7 / 8–15 / 16–31 / 32–63 / 64+.';
        const duration=element('span',undefined,'program-meter-duration'),track=element('span',undefined,'program-duration-track'),bar=element('span',undefined,'program-duration-bar');
        bar.setAttribute('style','--program-duration-fraction:'+facts.durationFraction);track.setAttribute('aria-hidden','true');track.append(bar);duration.append(track,element('span',facts.durationLabel,'program-duration-label'));
        const basis=facts.basis+(state.oc.stale && p.members.some(m=>m.backend==='opencode')?'\nOpenCode unavailable: its stamps are last known.':'');
        duration.title='Duration: '+(facts.duration===null?facts.why:facts.durationLabel)+' · first to latest turn. Bar: square-root scale, capped at 4 days; known durations fill at least 2 of 18 px.\n'+basis;
        const age=element('span',facts.ageLabel,'program-meter-age');age.dataset.age=facts.ageBucket;
        age.title='Age from program start: '+(facts.ageDays===null?'unknown':facts.ageDays+' Eastern calendar day(s)')+'; later follow-ups do not reset it.\n'+basis;
        meters.append(count,duration,age);title.append(meters);
        // Reserve the live dot's existing width on quiet roots too, without restyling that dot.
        if(!p.live){const tail=element('span',undefined,'program-meter-tail');tail.setAttribute('aria-hidden','true');title.append(tail);}
        // The meta line, then the program's first and latest turn as on a session row: the hover's own
        // facts (no read), so it fills in on the refresh that completes them and is absent while neither is known.
        const meta=element('span',undefined,'program-meta'),hover=turnTimes(facts.firstAt,facts.lastAt,facts.why);
        meta.append(element('span',['[ap:'+p.core+']',p.managerSession?'from Manager-Session-'+p.managerSession:null,p.count+' session'+(p.count===1?'':'s'),p.conductorListed?null:'Conductor not listed'].filter(Boolean).join(' · '),'meta-status'));
        const times=rowTimes(facts.firstAt,facts.lastAt,hover,'The earliest first turn and the latest turn across its listed sessions, Eastern time\n'+basis);
        if(times){meta.classList.add('meta-times');meta.append(element('span',' '),times);}
        head.append(toggle,title,meta);
        head.title=hover+'\n'+p.title+'\n[ap:'+p.core+'] · '+(p.kind==='quick'?'quick program':'full program')+(p.active?' · active now (a session in it has its turn open or is waiting)':p.live?' · running':' · ended')+(p.managerSession?' · launched from Manager-Session-'+p.managerSession:'')+'\nClick to '+(open?'close':'open')+' it';
        head.setAttribute('aria-expanded',String(open));head.dataset.anchor=p.key;head.classList.toggle('reveal-flash',flashing(p.key));pressable(head,()=>{setViewOpen(p.key,!open,entry);inventory();});
        box.append(head);
        if(open){const kids=element('div',undefined,'tree-children'),byFirst=new Map(runs.map(run=>[run.members[0].key,run]));
          for(let i=0;i<p.top.length;i++){
            const it=p.top[i],run=byFirst.get(it.key);
            if(!run){kids.append(treeNode(it,entry));continue;}
            const expanded=viewOpen(run.key,false,entry),first=run.members[0],last=run.members.at(-1);
            const label=expanded?'▾':'▸ Conductors -'+String(first.lin.conductorN).padStart(3,'0')+' – -'+String(last.lin.conductorN).padStart(3,'0');
            const ribbon=element('div',label,'conductor-fold');ribbon.title=expanded?'Fold these quiet Conductors':'Show these quiet Conductors';ribbon.setAttribute('aria-expanded',String(expanded));ribbon.dataset.anchor=run.key;
            pressable(ribbon,()=>{if(expanded)for(const member of run.members)state.unfolded.delete(member.key);setViewOpen(run.key,!expanded,entry);inventory();});kids.append(ribbon);
            if(expanded)for(const member of run.members)kids.append(treeNode(member,entry));i+=run.members.length-1;
          }
          box.append(kids);
        }
        return box;
      }
      const memberSelected=it=>it.backend==='claude'?isSelected(it.row):ocSelected(it.row.id);
      const selectedIn=p=>revealing(p.key) || p.members.some(it=>memberSelected(it) || revealing(it.key));
      const entrySelected=entry=>entry.row?isSelected(entry.row):(entry.crumb?[entry.node]:subtree(entry.node)).some(it=>memberSelected(it) || revealing(it.key));
      function foot(group,total,limit,page,unit) {
        // 📦 History: the archive's next page loads behind this same foot once the loaded entries run out.
        const older=!!state.archive?.more && !state.archive.error;
        if(!(total>limit || limit>page || older))return null;
        const node=element('div',undefined,'group-foot'),hidden=Math.max(0,total-limit);node.dataset.anchor='foot:'+group.key;
        if(hidden || older){const more=element('button',hidden?'Show '+Math.min(page,hidden)+' more of '+hidden+' older '+(unit?unit+' ':'')+'in this group':'Show more · older '+(unit || 'sessions')+' from 📦 History','link');more.type='button';
          more.onclick=()=>{state.groupShown.set(group.key,limit+page);if(older && limit+page>=total)void historyPage();inventory();};node.append(more);}
        if(limit>page){const fewer=element('button','Show only the newest '+page,'link');fewer.type='button';
          fewer.onclick=()=>{state.groupShown.delete(group.key);inventory();};node.append(fewer);}
        return node;
      }
      // Stubs are plain text only: the entry's sole interactive row is drawn in its destination.
      const stub=(name,place,key)=>{const node=element('div','↑ '+name+' · '+(place==='favorite'?'in Favorite programs':(place==='pinned'?'pinned':'shown')+' at top'),'session-stub');node.dataset.anchor='stub:'+key;return node;};
      // One entry: a flattened node under its breadcrumb, a freestanding tree, or a flat root row. At home, an
      // entry drawn in Running now (or pinned) leaves its stub instead.
      const drawEntry=(entry,groupKey)=>{
        const it=entry.node;
        if(entry.crumb){if(it.lin.kind==='conductor')unfolded.add(it.key);
          return it.backend==='claude'?item(it.row,{crumb:entry.crumb,inActive:it.home.active===true,activityKey:it.key,freestanding:!!it.home.forest}):(shown++,ocItem(it,entry.crumb));}
        if(filter==='all' && groupKey!=='programs:running' && placements.get(entry.key)!=='home')return stub(it?nodeName(it):displayName(entry.row),placements.get(entry.key),entry.key);
        return it?treeNode(it,{...entry,groupKey}):item(entry.row);
      };
      for(const group of ordered) {
        const box=element('div',undefined,'session-group'),heading=element('h3'),running=group.key==='programs:running' && filter==='all',favorite=group.key==='programs:favorite';
        box.dataset.group=group.key;heading.dataset.anchor='h:'+group.key;
        const sessions=sessionsIn(group.entries);
        const count=running?[group.programs.length?group.programs.length+' program'+(group.programs.length===1?'':'s'):null,
          sessions?sessions+' session'+(sessions===1?'':'s'):null].filter(Boolean).join(' · '):
          group.programs?.length ? group.programs.length+' program'+(group.programs.length===1?'':'s') : group.programs ? String(group.entries.length) : String(sessions);
        const collapsed=state.sections.collapsed.has(group.key),sectionToggle=element('span',collapsed?'▸':'▾','section-toggle');
        sectionToggle.title=collapsed?'Show this section':'Collapse this section';sectionToggle.setAttribute('aria-expanded',String(!collapsed));
        const toggleSection=()=>{if(collapsed)state.sections.collapsed.delete(group.key);else state.sections.collapsed.add(group.key);storeSections();inventory();};
        pressable(sectionToggle,toggleSection);heading.onclick=toggleSection;
        heading.append(sectionToggle,element('span',group.label),element('span',' · '+count,'group-count'));heading.title=group.title;
        if(group.key!=='programs:running'){
          const on=state.sections.activeOnly.has(group.key),active=element('span','active only','section-active');active.setAttribute('aria-pressed',String(on));
          active.title='Show only entries with something busy or waiting; once shown, an entry stays until you turn this off';
          pressable(active,()=>{if(on){state.sections.activeOnly.delete(group.key);state.activeOnly.delete(group.key);}else{state.sections.activeOnly.add(group.key);state.activeOnly.set(group.key,{shown:new Set(),open:new Map()});}storeSections();inventory();});heading.append(active);
        }
        box.append(heading);
        if(collapsed){fragment.append(box);continue;}
        const includeEntry=entry=>{
          const overlay=activeOverlay({...entry,groupKey:group.key});if(!overlay)return true;
          if(!overlay.shown.has(entry.key) && !activityOf(entry.key).any)return false;
          overlay.shown.add(entry.key);
          const keys=entry.key.startsWith('p:')?programKeys(entry,folds.get(entry.key)):entry.node && !entry.crumb?subtree(entry.node).filter(it=>it.children.length).map(it=>it.key):[];
          for(const key of keys)if(!overlay.open.has(key))overlay.open.set(key,minimalOpen(key));
          return true;
        };
        // Every section that lists OpenCode rows says so when OpenCode does not answer.
        if((group.programs && (!favorite || group.programs.length) || group.key===QUICK_SECTION.key || group.key===MANAGER_SECTION.key && group.entries.some(e=>e.node?.backend==='opencode')) && state.oc.status==='unavailable')
          box.append(element('div','OpenCode unavailable: '+state.oc.error+(state.oc.stale?' · OpenCode rows show their last known state':' · Claude Code sessions only')+' · retrying','oc-unavailable'));
        const isManager=entry=>entry.node?.lin.kind==='manager' || !!entry.row && realRole(entry.row)==='manager';
        if(running)for(const entry of group.entries)if(isManager(entry))box.append(entry.node?drawEntry(entry,group.key):item(entry.row));
        if(group.programs && filter==='all') {
          // A starred program's home stub keeps its chronological position, even while running.
          // Unstarred live programs retain their old boost. Favorites reuse that same home order
          // within full/quick; only their Running now stubs are lifted to the very top.
          const homeOrder=(a,b)=>Number(b.live && !programStarred(b))-Number(a.live && !programStarred(a)) || b.newest-a.newest;
          const programs=group.programs.sort(running?(a,b)=>Number(b.live)-Number(a.live) || b.newest-a.newest:favorite?(a,b)=>
            Number(placements.get(b.key)==='running')-Number(placements.get(a.key)==='running') || Number(a.kind==='quick')-Number(b.kind==='quick') || homeOrder(a,b):homeOrder);
          const limit=favorite?programs.length:Math.max(PROGRAM_PAGE,state.groupShown.get(group.key) || 0);
          programs.forEach((p,i)=>{if((i<limit || selectedIn(p)) && includeEntry(p)){
            const place=placements.get(p.key),here=running || (favorite?place==='favorite':place==='home');
            // Two stubs for a running favorite need distinct reading anchors; its home anchor stays unchanged.
            box.append(here?programBlock(p,group.key):stub(p.title,place,favorite?group.key+':'+p.key:p.key));}});
          if(!favorite){const more=foot(group,programs.length,limit,PROGRAM_PAGE,'programs');if(more)box.append(more);}
        } else {
          const list=group.entries,limit=Math.max(GROUP_PAGE,state.groupShown.get(group.key) || 0);
          // The session on screen stays listed even beyond its group's limit.
          list.forEach((entry,i)=>{if((i<limit || entrySelected(entry)) && includeEntry(entry))box.append(drawEntry(entry,group.key));});
          const more=foot(group,list.length,limit,GROUP_PAGE,'');if(more)box.append(more);
        }
        if(running)for(const entry of group.entries)if(!isManager(entry))box.append(drawEntry(entry,group.key));
        fragment.append(box);
      }
      // The rows being read keep their place on screen across the swap (listAnchor), whatever moved above them.
      $('sessions-pinned').replaceChildren(pinned);$('sessions-pinned').hidden=!$('sessions-pinned').children.length;
      $('sessions-list').replaceChildren(fragment);
      teleFit();restoreList(reading);rememberList();
      if(state.reveal?.opened && !state.reveal.done && Date.now()-state.reveal.at<REVEAL_MS)revealScroll();
      if(block && !placed){block.hidden=!watched || !!watchedRow || !(state.children.length || state.childError);block.dataset.detached='true';const aside=$('sessions-list').parentNode;if(aside && block.parentNode!==aside)aside.append(block);}
      if(!state.listPartial){state.placed=placements;state.unfolded=unfolded;}
      collapseLabel();
      $('sessions-filter-status').textContent=`${shown} shown · ${matching} ${filter==='all'?'listed':'match the filter'} · ${state.inventory.length} in the whole inventory${state.listPartial?' (still loading)':''} · OpenCode: ${state.oc.status==='complete'?state.oc.rows.length+' sessions':state.oc.status+(state.oc.error?' ('+state.oc.error+')':'')}`;
      teleFlush();
    }
    // @beacon[
    //   id=opc-pane@renameSession,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Sidebar rename: the row's name turns into an input (Enter or leaving saves, Escape cancels, empty restores the automatic name) and writes the same display-only name store New Manager writes; never identity or Send authority.,
    // ]
    function startRename(row,open) {
      if(state.renaming || !open.parentNode)return;
      const current=sessionName(row),automatic=current===row.uuid || /^Session [0-9a-f]{8}$/.test(current);
      const input=element('input',undefined,'session-rename-input');input.type='text';input.maxLength=80;input.spellcheck=false;
      input.value=automatic?'':current;input.placeholder='Manager-Session-NNN';input.setAttribute('aria-label','New name for '+current);
      state.renaming={uuid:row.uuid};open.parentNode.insertBefore(input,open);open.hidden=true; // an input inside a button would not type
      let done=false;
      const finish=async save=>{
        if(done)return;done=true;
        const value=input.value.trim();
        if(save && value!==(automatic?'':current)) {
          notice('Renaming …');
          try {
            const data=await json('/api/claude/rename',{method:'POST',headers:{'Content-Type':'application/json'},
              body:JSON.stringify({schemaVersion:1,backend:'claude',uuid:row.uuid,name:value})});envelope(data);
            for(const r of [...state.inventory,state.session,state.childParent])if(r && r.uuid===row.uuid && role(r)!=='subagent'){
              if(data.name){r.sessionLabel=data.name;r.nameSource='chosen';}else if(r.nameSource==='chosen'){delete r.sessionLabel;delete r.nameSource;}}
            notice(data.name?'Renamed: '+data.name+' (display name only)':'Name cleared: the automatic name returns with the next list refresh');
            state.inventoryNextPoll=0;
          } catch(e) {notice('Rename failed: '+e.message+' · the name is unchanged',true);}
        }
        state.renaming=null;inventory();
        if(state.session?.uuid===row.uuid){$('history-title').textContent=sessionName(state.session)+(state.session.uuid?' · '+state.session.uuid:'');ribbon();}
      };
      input.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault?.();void finish(true);}else if(event.key==='Escape'){event.preventDefault?.();void finish(false);}event.stopPropagation?.();};
      input.onblur=()=>void finish(true);
      input.onclick=event=>event.stopPropagation?.();
      input.focus?.();input.select?.();
    }
    // A short visible line under the list controls (rename results); it fades after a while.
    function notice(text,error=false) {
      const node=$('sessions-notice');if(!node)return;
      clearTimeout(state.noticeTimer);node.hidden=!text;message('sessions-notice',text,error);
      if(text && !/…$/.test(text))state.noticeTimer=setTimeout(()=>{node.hidden=true;},error?15000:6000);
    }
    function readoutField(value) {
      if(!value || typeof value!=='object' || typeof value.state!=='string')return null;
      return value;
    }
    function progressField(value) {
      if(!value || typeof value!=='object' || !Number.isSafeInteger(value.indexedBytes) || value.indexedBytes<0)return null;
      return value;
    }
    // The OpenCode conversation header (hidden for Claude): OpenCode, its role and recorded title (else the
    // bare id), the ses_ id with Copy, and Open in CodeNomad through the glance's focus request and status.
    // Rebuilt only when what it shows changes, so a focused button or copy survives the polls.
    function header() {
      const box=$('oc-header');if(!box)return;
      const s=state.session;
      if(!isOpenCode(s)){if(!box.hidden){box.hidden=true;box.replaceChildren();delete box.dataset.signature;}return;}
      const f=state.focus?.sessionId===s.id?state.focus:null,manager=s.manager||null,model=openCodeManagerModel(manager?.model) || s.model;
      const signature=JSON.stringify([s.id,s.title,s.ocRole,s.agent,model,s.directory,s.parentId,f?focusText(f):null,
        manager?[manager.target?.witness,manager.busy,manager.context?.status,manager.context?.usedTokens,manager.context?.window,manager.context?.measuredAt,s.managerIdentityChanged,s.managerError,s.managerReadError]:null]);
      box.hidden=false;if(box.dataset.signature===signature)return;box.dataset.signature=signature;
      const top=element('div',undefined,'oc-header-line'),facts=element('div',undefined,'oc-header-facts');
      const chip=element('span','OpenCode','backend-chip');chip.dataset.backend='opencode';
      chip.title=manager?'A server-validated OpenCode Manager: selecting it targets Send/Refine in the editor':'An OpenCode conversation: view only here';
      const name=element('span',displayName(s),'oc-header-title');name.title=s.title || s.id;
      const copy=element('span','⧉','oc-header-copy');copy.title='Copy '+s.id;copy.setAttribute('aria-label','Copy the OpenCode session id');pressable(copy,()=>copyText(copy,s.id));
      top.append(chip,element('span',roleLabel(s)+':','oc-header-role'),name,element('span',s.id,'oc-header-id'),copy,focusLine(s.id,{directory:s.directory,title:s.title,parentId:s.parentId}));
      if(!s.directory)top.append(element('span','folder not recorded · Open in CodeNomad unavailable','oc-header-missing'));
      text(facts,[manager?'Manager · select to Send/Refine':'view only',s.agent?'agent '+s.agent:'agent unknown',model?'model '+(manager?model:shortModel(model)):'model unknown',s.directory || 'folder unknown',s.parentId?'started by '+s.parentId:null].filter(Boolean).join(' · '));
      facts.title=model || '';
      box.replaceChildren(top,facts);
    }
    // An OpenCode page's display facts only: its busy fact and the same-id sessionInfo (which gives an
    // unlisted child its title and folder); there is no host, incident, target or context state to keep.
    function ocDisclosure(data) {
      const s=state.session,manager=s.manager||null;
      if(Object.hasOwn(data,'busy')){state.ocBusyFact=data.busy===true?true:data.busy===false?false:null;state.ocObservedAt=Number.isFinite(data.observedAt)?data.observedAt*1000:Date.now();s.busy=state.ocBusyFact;}
      if(Object.hasOwn(data,'runningCalls'))state.ocRunningCalls=Array.isArray(data.runningCalls)?data.runningCalls:null;
      const current=ocEvidence(s);runningStrip?.update(current.calls,current.busy,current.at,current.error);
      const info=data.sessionInfo;
      if(info && typeof info==='object' && info.id===s.id) {
        const fact=v=>typeof v==='string' && v?v:null;
        if(fact(info.title) && (info.title!==s.id || !s.title))s.title=info.title;
        for(const key of ['agent','model','directory','parentId'])if(fact(info[key]))s[key]=info[key];
      }
      const model=openCodeManagerModel(manager?.model) || s.model || 'unknown';
      $('history-title').textContent=(manager?'OpenCode Manager · ':'OpenCode · ')+sessionName(s)+(sessionName(s)!==s.id?' · '+s.id:'');
      $('history-source').textContent=`${manager?'OpenCode Manager':'OpenCode'} · ${roleLabel(s)} · ${s.id} · ${s.directory || 'folder unknown'} · agent ${s.agent || 'unknown'} · model ${model}${s.parentId?' · started by '+s.parentId:''}`;
      if(manager) {
        $('history-capabilities').textContent='OpenCode Manager · this row is a root Manager the server validated by its full session id (creation and folder witness); selecting it targets Send/Refine in the editor and your draft stays. Send goes directly to this Manager — its model and context readback are per-Manager, not the global OpenCode quota. Text and thinking appear as each block is stored; tool states follow live. Browsing never sends, launches or resumes anything.'+
          (s.managerIdentityChanged?' The Manager identity changed; select it again before sending.':'')+(s.managerError?' Manager validation failed: '+s.managerError:'');
      } else {
        $('history-capabilities').textContent='View only · an OpenCode conversation is never a Send, Refine or control target here (OpenCode Send mode in the editor is unchanged). Text and thinking appear as each block is stored; tool states follow live. Browsing never sends, launches or resumes anything.'+
          (!manager && s.managerClaim==='row' && s.managerError?' This listed Manager did not validate: '+s.managerError:'');
      }
      const link=$('history-successor');if(link){link.hidden=true;link.textContent='';link.onclick=null;}
      const live=manager?'OpenCode Manager evidence: the server validated this root Manager (creation + folder witness) and reads its busy/idle separately; no Claude host evidence or incidents exist for this backend.':
        'OpenCode reports only busy or idle: no host evidence, ownership or incidents exist for this backend.';
      if($('history-live').textContent!==live)$('history-live').textContent=live;
      $('history-observation').textContent=(manager?'OpenCode Manager · ':'OpenCode · ')+(state.ocBusyFact===true?'busy':state.ocBusyFact===false?'idle':'activity unknown')+
        (manager?' · '+openCodeContextText(manager.context)+' · target '+(openCodeManagerTarget(s)?'validated':'withheld')+(s.managerReadError?' · last read failed: '+s.managerReadError:''):
          ' · read-only')+'; Inspect selects one call in the shared inspector and never changes the target.';
      header();ribbon();controls();activity();
    }
    // A 📦 History conversation's display facts only (its page's same-id sessionInfo fills an OpenCode one's title and
    // folder): read-only by nature, so there is no live, host, incident, target or context state to keep.
    function historyDisclosure(data) {
      const s=state.session,oc=s.archivedBackend==='opencode',info=data.sessionInfo,name=sessionName(s);
      if(oc && info && typeof info==='object' && info.id===s.id)for(const key of ['title','agent','model','directory','parentId'])
        if(typeof info[key]==='string' && info[key] && !(key==='title' && info.title===s.id && s.title))s[key]=info[key];
      header();
      const id=oc?s.id:s.uuid,label=oc?ROLE_BADGE[s.ocRole] || 'OpenCode session':roleLabel(s);
      $('history-title').textContent='📦 '+sessionName(s)+(id && sessionName(s)!==id?' · '+id:'');
      $('history-source').textContent=`📦 History · ${oc?'OpenCode':'Claude'} · ${label} · ${id || name} · ${(oc?s.directory:s.cwd) || 'folder not recorded'}`+
        (s.archived?.archivedAt?' · archived '+clock(s.archived.archivedAt):'');
      $('history-capabilities').textContent='Archived conversation · read-only: its prompts, answers and a one-line preview of each tool call, as archived (no thinking). It is never a Send, Refine or Inspect target and has no context figure.';
      const link=$('history-successor');if(link){link.hidden=true;link.textContent='';link.onclick=null;}
      const live='Read from the 📦 History archive: no live, host or OpenCode evidence applies to it.';
      if($('history-live').textContent!==live)$('history-live').textContent=live;
      $('history-observation').textContent='📦 History · read-only · nothing live is read for this conversation.';
      activity();
    }
    function disclosure(data={}) {
      if(archived(state.session))return historyDisclosure(data);
      if(isOpenCode(state.session))return ocDisclosure(data);
      header();
      const previousState=state.liveState?.state || state.session?.state;
      if(data.hostLaunch || (['launching','initializing'].includes(previousState) && data.liveState?.state==='ready') ||
          Array.isArray(data.incidents) && data.incidents.some(i=>['session_launched','launch','launch_ready'].includes(i?.category)))state.inventoryNextPoll=0;
      if(Object.hasOwn(data,'targetInfo'))state.targetInfo=data.targetInfo;
      if(Object.hasOwn(data,'liveState')){state.liveState=data.liveState;state.liveStateAt=Date.now();}
      if(Object.hasOwn(data,'liveState') && state.session?.source==='host' && role(state.session)==='manager'){state.parentLive=data.liveState;state.parentLiveAt=Date.now();}
      if(state.session) {
        const marks=JSON.stringify([activeNow(state.session),backgroundCount(state.session),[...runningAgents(state.childParent || state.session)]]);
        if(marks!==state.sidebarMarks){state.sidebarMarks=marks;inventory();renderChildren();}
      }
      if(Object.hasOwn(data,'contextReadout')){state.contextReadout=readoutField(data.contextReadout);contextGauge();}
      if(Object.hasOwn(data,'sourceProgress'))state.sourceProgress=progressField(data.sourceProgress);
      if(Object.hasOwn(data,'incidents')){
        const incidents=listField(data.incidents,'incidents').filter(i=>i&&typeof i==='object').slice(-100);
        if(incidents.length!==data.incidents?.length)warn('incidents');
        const incidentKey=i=>JSON.stringify([i.execution_id,i.generation,i.incident_id || i.cursor || JSON.stringify(i)]);
        const seen=new Map(state.incidents.map(i=>[incidentKey(i),i]));
        for(const i of incidents)seen.set(incidentKey(i),i);
        state.incidentOverflow ||= seen.size>100;state.incidents=[...seen.values()].slice(-100);state.incidentsKnown=true;
      }
      const row=state.session || {},info=state.targetInfo || row,host=row.source==='host';
      $('history-source').textContent=`Claude · ${roleLabel(row)} · ${row.uuid || 'unknown'} · ${row.source || 'unknown'} · ${row.cwd || 'unknown'}${host?` · ${info.executionId || row.executionId || 'execution unknown'} · generation ${info.generation ?? row.generation ?? 'unknown'}`:''}`;
      $('history-capabilities').textContent=(ended(row)?endedText(row)+' Archived, fully witnessed prose can still be used for Refine.'+(reconnectable(row)?' Send reconnects the conversation.':''):writable(info)?
        (parentEditor?'Live Manager · selecting this row targets Send/Refine in the editor. ':'Live Manager · open the integrated pane to select it for Send. '):host && role(row)==='manager'?
        'Connecting · readiness is checked automatically; your draft stays in the editor. ':'History / context only · no live input authority. ')+
        'Browsing never launches or resumes a process. Inspector, subagent and context-readout notices do not revoke a live Manager’s Send authority.';
      const liveManager=ended(row)?successor(row):null,link=$('history-successor');
      if(link){link.hidden=!liveManager;link.textContent=liveManager?'Select live Manager: '+(liveManager.title || liveManager.executionId):'';
        link.onclick=()=>{const full=state.inventory.find(r=>r.executionId===liveManager?.executionId) || liveManager;
          if(full.scopes)void browse(full);else void list().then(()=>{const row=state.inventory.find(r=>r.executionId===liveManager?.executionId);if(row)void browse(row);});};}
      const text=host?`Host evidence (not inferred busy/idle): ${evidence(state.liveState ?? row.state)}\nIncidents (last 100 observed; earlier evidence remains paged${state.incidentOverflow?'; older observed entries evicted':''}): ${evidence(state.incidents)}`:'Stored history · no host live-state evidence.';
      if($('history-live').textContent!==text)$('history-live').textContent=text;
      const live=state.liveState ?? row.liveState ?? {};
      $('history-observation').textContent=host ? `Host transport: ${evidence(live.state ?? row.state)} · owner: ${evidence(live.owner)} · native retry: ${evidence(live.native_retry_active)} · background unsettled: ${evidence(live.background_unsettled)} · silence: ${evidence(live.silence_observation ?? live.silence)} · incidents shown: ${state.incidentsKnown ? state.incidents.length : 'unknown'}${state.incidentsKnown && state.incidents.length ? ' · '+state.incidents.slice(-3).map(i=>i.category || 'unknown').join(', ') : ''} · task acceptance: unjudged` : 'Stored transcript · live activity and ownership unknown; browsing grants no input authority.';
      activity();
    }
    // The target the selected conversation offers right now: its server witness for a validated OpenCode
    // Manager, else the Claude page's targetRef. Browsing an ordinary OpenCode row never inherits one.
    function selectionTarget() {
      if(isOpenCode(state.session))return openCodeManagerTarget(state.session);
      return state.target;
    }
    function controls() {
      if(sidebarOnly)return;
      $('history-target').disabled=!selectionTarget() || state.blocked;
      $('history-reload').disabled=!state.session;
      $('history-more').disabled=!state.next || state.loading || state.blocked || !!state.resync;
      $('history-older').disabled=!state.session || atStart() || state.blocked || !!state.resync;
      $('history-latest').disabled=!state.session || state.loading || state.blocked || !!state.resync;
      ribbon();edge();
      // `state.used` is the viewer's resident-row memory (twice the UTF-8 bytes of each
      // mounted row's signature, bounded at 16 MiB) — not the session's context.
      const source=sourceText();
      $('history-window').textContent=`${state.order.length} resident events · viewer memory ${state.used} bytes (resident rows, not context)${source?' · viewer source '+source:''} · bounded ${WINDOW_EVENTS} events / 16 MiB. ${atStart()?'The conversation’s first message is loaded. ':'Scroll up (or ↑ / Load earlier messages) for earlier messages; ⤒ First opens the very start. '}Eviction never changes the held call.`;
    }
    // @beacon[
    //   id=opc-pane@listClaudeInventory,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Page host summaries with native=0/details=0 and the page-local view; the first page paints its optional quiet host-load caption without an extra request and a failed first page clears unobserved load. Titles, stars, independent Manager-family placement, old-server fallback and deliberate native history keep their existing paths; failures retain rows.,
    // ]
    async function list(withHistory=false) {
      if(state.archive)return; // never while 📦 History is the list's source (a resync of a live conversation waits)
      state.inventoryBusy=true;
      void loadStars();void loadManagerFamily();const selectedView=setView();
      const generation=++state.listGeneration; state.listAbort?.abort(); state.listAbort=new AbortController();
      const rows=[],seen=new Set(),diagnostics=[],first=!state.inventory.length,at=Date.now();let cursor=null,pages=0,status='',listWindowDays=null;
      try {
        do {
          const route='/api/claude/sessions?details=0'+(withHistory?'':'&native=0')+(cursor?'&cursor='+encodeURIComponent(cursor):'');let data;
          try {data=await json(route+(state.viewSupported.claude?'&view='+selectedView:''),{signal:state.listAbort.signal});}
          catch(e) {if(e.code!==400 || !state.viewSupported.claude)throw e;state.viewSupported.claude=false;data=await json(route,{signal:state.listAbort.signal});}
          if(generation!==state.listGeneration)return; envelope(data);
          if(!Array.isArray(data.sessions) || data.sessions.length>100 || (data.hasMore && !ref(data.nextCursor)))throw new Error('invalid inventory page');
          if(data.hasMore && (data.nextCursor===cursor || seen.has(data.nextCursor)))throw new Error('inventory cursor did not advance; refresh scan');
          for(const row of data.sessions) {
            if(!ref(row.sessionRef) || !Array.isArray(row.scopes))throw new Error('invalid session identity');
          }
          // A restart reissues store handles: activity must not wait for the history tail to adopt this one.
          if(pages===0)state.hostStoreRef=data.sessions.find(row=>row.source==='host')?.storeRef || null;
          if(pages===0)paintHostLoad(data.hostLoad);
          if(pages===0)listWindowDays=typeof data.listWindowDays==='number' && Number.isFinite(data.listWindowDays) && data.listWindowDays>0?data.listWindowDays:null;
          rows.push(...data.sessions);diagnostics.push(...(Array.isArray(data.diagnostics)?data.diagnostics:[]));status=data.status;
          for(const row of data.sessions)if(row.source==='host')state.hostActivity.set(row.executionId,{row,at});
          cursor=data.hasMore?data.nextCursor:null;if(cursor)seen.add(cursor);
          pages++;
          // The first scan fills the list as it arrives; a refresh replaces it only when complete, but a session it has
          // not listed yet is added as soon as its page arrives (new sessions come first; a walk can take a minute, or
          // fail on a later page): nothing is removed or replaced until the walk completes.
          if(first && cursor){state.inventory=rows.slice();state.listWindowDays=listWindowDays;state.listPartial=true;inventory();}
          else if(!first && cursor) {
            const key=row=>(row.source || '')+':'+(row.executionId || row.uuid || row.sessionRef),known=new Set(state.inventory.map(key));
            const fresh=data.sessions.filter(row=>!known.has(key(row)));
            if(fresh.length){state.inventory=[...state.inventory,...fresh];inventory();}
          }
          // A deliberate native-history walk yields between pages; current activity has its own lane.
          if(cursor)await new Promise(resolve=>setTimeout(resolve,withHistory?250:0));
        } while(cursor);
        if(withHistory){state.nativeHistory=rows.filter(row=>row.source!=='host');state.nativeHistoryLoaded=true;}
        else {state.hostActivityAt=at;state.hostActivityFailure=null;}
        const ids=new Set(rows.map(row=>row.sessionRef));
        state.inventory=[...rows,...state.nativeHistory.filter(row=>!ids.has(row.sessionRef))];state.listWindowDays=listWindowDays;state.listPartial=false;state.listRetry=0;state.inventoryAt=at;inventory();
        message('sessions-status',`${status} · ${rows.length} sessions · ${withHistory?'host and stored history':'host inventory; stored native history loads on request'} (${pages} server page${pages===1?'':'s'})\n`+diagnostics.map(x=>x.status||x.error).join('\n'));
      } catch(e) {if(generation===state.listGeneration){
        if(pages===0)paintHostLoad(null);
        if(first && rows.length){state.inventory=rows;state.listWindowDays=listWindowDays;inventory();}
        if(!withHistory && !rows.length)state.hostActivityFailure={at:Date.now(),error:String(e.message || e)};
        // Another pane tab restarting the server's scan answers 409: try again shortly, not in ten seconds.
        if(e.code===409)state.listRetry=(state.listRetry || 0)+1;
        message('sessions-status','List unavailable: '+e.message+(state.inventory.length?' · the last list read stays shown':'')+' · ⟳ starts a new scan.',true);}}
      if(generation===state.listGeneration){
        state.inventoryBusy=false;state.inventoryNextPoll=Date.now()+(state.listRetry>0 && state.listRetry<4?500:10000);controls();}
    }
    // @beacon[
    //   id=opc-pane@pollHostActivity,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Claude activity every three visible seconds from transcript-free live=1 host summaries: no terminal history on this lane. Publish each page/new work immediately, then mark disappeared live rows unknown. Failures stop running claims without fabricating idle; no transcript walk, target or control changes.,
    // ]
    async function pollHostActivity() {
      const store=state.hostStoreRef || state.inventory.find(row=>row.source==='host')?.storeRef;
      if(!ref(store) || state.hostActivityBusy)return;
      state.hostActivityBusy=true;
      const rows=[],seen=new Set(),at=Date.now(),source=state.sourceGeneration;let cursor=null,pages=0;
      try {
        do {
          const data=await json('/api/claude/sessions?details=0&live=1&storeRef='+encodeURIComponent(store)+(cursor?'&cursor='+encodeURIComponent(cursor):''));envelope(data);
          if(source!==state.sourceGeneration)return; // the list's source changed (📦 History): never merge live rows into it
          if(!Array.isArray(data.sessions) || data.sessions.length>100 || (data.hasMore && !ref(data.nextCursor)))throw new Error('invalid inventory page');
          if(data.hasMore && (data.nextCursor===cursor || seen.has(data.nextCursor)))throw new Error('inventory cursor did not advance');
          for(const row of data.sessions)if(!ref(row.sessionRef) || !Array.isArray(row.scopes))throw new Error('invalid session identity');
          rows.push(...data.sessions);cursor=data.hasMore?data.nextCursor:null;if(cursor)seen.add(cursor);
          for(const row of data.sessions)if(row.source==='host')state.hostActivity.set(row.executionId,{row,at});
          const known=new Map(state.inventory.map(row=>[row.executionId || row.sessionRef,row]));
          for(const row of data.sessions)known.set(row.executionId || row.sessionRef,row);
          state.inventory=[...known.values()];if(pages===0 || !cursor)inventory();
          pages++;
          if(cursor)await new Promise(resolve=>setTimeout(resolve,0));
        } while(cursor);
        for(const row of rows)if(row.source==='host')state.hostActivity.set(row.executionId,{row,at});
        const present=new Set(rows.map(row=>row.executionId));
        for(const row of state.inventory)if(row.source==='host' && !ended(row) && !present.has(row.executionId))
          state.hostActivity.set(row.executionId,{row,at,error:'not in the current host inventory'});
        state.hostActivityFailure=null;state.hostActivityAt=at;inventory();
      } catch(e) {
        // Keep the last host facts, but never silently pass them off as a current observation.
        state.hostActivityFailure={at:Date.now(),error:String(e.message || e)};inventory();
      }
      finally {state.hostActivityBusy=false;state.hostActivityNextPoll=Date.now()+3000;}
    }
    // @beacon[
    //   id=opc-pane@setView,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The sole page-local view setter/readback: week/month/all, or 📦 History (its one branch, first: the archive becomes the list's source through useSource and page 1 is read again); reload defaults to week, no URL/storage/server preference. A switch supersedes old reads and starts the two live inventories independently (leaving History empties the archive's rows first); the conversation and Send target never change.,
    // ]
    function setView(next) {
      if(next===undefined)return state.view;
      if(next==='history'){state.view=next;if($('sessions-view'))$('sessions-view').value=next;useSource(true);return historyPage();}
      if(!['week','month','all'].includes(next))return Promise.resolve(false);
      if(state.archive)useSource(false); // back from 📦 History: the live list fills as its first pages arrive
      state.view=next;if($('sessions-view'))$('sessions-view').value=next;
      state.listAbort?.abort();state.listGeneration++;state.inventoryBusy=false;
      state.ocAbort?.abort();state.ocGeneration++;state.ocBusy=false;
      state.groupShown.clear();state.inventoryNextPoll=0;state.ocNextPoll=0;
      return Promise.all([list(),ocList()]);
    }
    // @beacon[
    //   id=opc-pane@useSource,
    //   slice_labels=opc-pane,opc-history,
    //   kind=ast,
    //   comment=The ONE sidebar live/History source switch: supersede live reads, clear their load caption and row/evidence/detail caches, then set state.archive, the one polling/rendering flag; History fills the same inventory/oc fields without new host reads, conversation or Send changes.,
    // ]
    function useSource(history) {
      state.listAbort?.abort();state.listGeneration++;state.inventoryBusy=false;
      state.ocAbort?.abort();state.ocGeneration++;state.ocBusy=false;
      state.archive?.abort.abort();state.sourceGeneration++;
      Object.assign(state,{inventory:[],nativeHistory:[],nativeHistoryLoaded:false,listWindowDays:null,inventoryAt:0,listPartial:false,
        oc:{rows:[],status:'loading',error:null,stale:false},hostActivity:new Map(),hostActivityAt:0,hostActivityFailure:null,ocActivity:null,ocActivityShown:null});
      for(const cache of [state.glances,state.claudeTurns,state.ocTurns,state.tele,state.teleReads,state.groupShown])cache.clear();
      if(state.glanceLarge)closeLargeGlance();
      state.archive=history?{units:[],seen:new Set(),next:null,more:false,busy:false,error:null,total:null,status:null,lastSweepAt:null,abort:new AbortController()}:null;
      paintHostLoad(null);
      const native=$('sessions-native-history');if(native)native.hidden=!!history; // unmanaged transcripts of the live stores only
      inventory();sidebarFreshness();
    }
    // @beacon[
    //   id=opc-pane@historyPage,
    //   slice_labels=opc-pane,opc-history,
    //   kind=ast,
    //   comment=Reads the next /api/history/programs page (before = the last nextBefore) while 📦 History is the source: whole units newest first, each unit exactly once, no byte cap (a unit is never split); its rows join state.inventory/state.oc, their stored telemetry and OpenCode turn facts fill the renderers' detail caches, so nothing live is read; a stale answer after a switch is dropped.,
    // ]
    async function historyPage() {
      const h=state.archive;if(!h || h.busy || (h.status && !h.more))return false;
      h.busy=true;h.error=null;sidebarFreshness();
      try {
        const data=await json('/api/history/programs'+(h.next?'?'+new URLSearchParams({before:h.next}):''),{signal:h.abort.signal},Infinity);
        if(state.archive!==h)return false;
        if(!data || data.schemaVersion!==1 || data.backend!=='history' || !['complete','empty'].includes(data.status) || !Array.isArray(data.units) ||
          !Array.isArray(data.claude) || !Array.isArray(data.opencode) || (data.hasMore && !ref(data.nextBefore)))throw new Error('invalid History page');
        const now=Date.now(),have=new Set([...state.inventory,...state.oc.rows].map(r=>r.archived?.key));
        const fresh=data.units.filter(u=>typeof u?.unit==='string' && !h.seen.has(u.unit));for(const u of fresh)h.seen.add(u.unit);
        const claude=data.claude.filter(r=>archived(r) && ref(r.sessionRef) && Array.isArray(r.scopes) && !have.has(r.archived?.key));
        const opencode=data.opencode.filter(r=>archived(r) && /^ses_[A-Za-z0-9]{8,64}$/.test(r.id || '') && !have.has(r.archived?.key));
        for(const r of [...claude,...opencode]){const sid=r.executionId || r.id;if(typeof sid==='string' && Array.isArray(r.telemetry?.segments))state.tele.set(sid,r.telemetry);}
        for(const r of opencode){const f=r.turnFacts;if(f?.status==='complete')state.ocTurns.set(r.id,{data:{firstAt:f.firstAt,lastAt:f.lastAt},sig:ocTurnSig(r),at:now});}
        h.units.push(...fresh);state.inventory=[...state.inventory,...claude];
        state.oc={rows:[...state.oc.rows,...opencode],status:'complete',error:null,stale:false,observedAt:now};
        Object.assign(h,{next:data.hasMore?data.nextBefore:null,more:!!data.hasMore,status:data.status,total:data.total || null,lastSweepAt:data.archive?.lastSweepAt ?? null});
        state.inventoryAt=now;state.listWindowDays=null;
        message('sessions-status','📦 History · '+data.status+' · '+h.units.length+' of '+(data.total?.units ?? '?')+' units loaded · '+(data.total?.sessions ?? '?')+' sessions archived · newest first');
        return true;
      } catch(e) {if(state.archive===h)h.error=String(e.message || e).slice(0,200);return false;}
      finally {if(state.archive===h){h.busy=false;inventory();sidebarFreshness();}}
    }
    function reset() {
      clearTimeout(state.targetAction?.timer);state.targetAction=null;
      state.abort?.abort();state.generation++;state.loading=false;state.loadingMode=null;state.blocked=false;
      state.epoch=null;state.leaf=null;state.target=null;state.pages=[];state.pageAt=-1;state.delta=null;state.next=null;state.rowsAhead=null;state.indexingContinuation=null;state.startKey=undefined;
      state.rows.clear();state.identities.clear();state.warnings.clear();state.order=[];state.used=0;state.newCount=0;state.accepted=null;state.pending=null;state.failed=null;state.queued=null;
      state.failures=0;state.nextPoll=0;
      state.liveState=null;state.incidents=[];state.incidentsKnown=false;state.incidentOverflow=false;state.targetInfo=null;state.tailEvicted=false;state.observationError=null;
      state.contextReadout=null;state.sourceProgress=null;state.dropped=false;state.indexLimit=null;state.blockedReason=null;
      state.ocBusyFact=null;state.ocObservedAt=null;state.ocRunningCalls=null;state.ocManagerBusy=false;state.ocManagerNextPoll=0;runningStrip?.update(null,false,null,null);
      state.resync=null;state.resyncWarn=null;state.catchingUp=null;state.rowsUnreadable=false;
      state.subagentActivity.clear();state.agents.clear();state.runs.clear();state.lastJump=null;
      $('history-events').replaceChildren();$('history-viewport').scrollTop=0; controls();
    }
    // @beacon[
    //   id=opc-pane@browse,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Opens either backend in the one viewer, newest Following on a switch. Manager selection remembers its whole numbered family at the top; browsing workers/children keeps that family and the editor target. Claude main Managers and GET-validated OpenCode Managers alone auto-target; History stays read-only and no browsing wakes a process.,
    // ]
    async function browse(session, scope=null, leaf=null) {
      const childGeneration=state.childGeneration;
      // A glance away from the default group remembers that visible group too, not just an explicit Manager click.
      if(!state.topManager)state.topManager=selectedManager();
      if(archived(session) && session.backend!=='history')session=historySession(session);
      if(isOpenCode(session) || archived(session)) {
        // An OpenCode or archived conversation watches no native subagents: the Claude parent and its in-flight child reads go.
        if(state.childParent){state.childParent=null;state.children=[];state.childNext=null;state.childNextPoll=0;state.childPaged=false;state.childGeneration++;
          state.parentLive=null;state.parentLiveAt=0;renderChildren();}
      } else if(role(session)!=='subagent' && state.childParent?.sessionRef!==session.sessionRef) {
        state.childParent=session;state.children=[];state.childNext=null;state.childNextPoll=0;state.childPaged=false;state.childGeneration++;
        state.parentLive=null;state.parentLiveAt=0;
        renderChildren();
      } else if(role(session)==='subagent' && state.childParent?.uuid!==session.parentUuid) {
        state.childParent=session;state.children=[];state.childNext=null;state.childNextPoll=0;state.childPaged=false;state.childGeneration++;
        state.parentLive=null;state.parentLiveAt=0;
        renderChildren();
      }
      // Opening a different conversation lands on its newest turn, following, whatever the
      // last one was doing; a reload of the same conversation keeps Dan's choice.
      const switched=state.session?.sessionRef!==session.sessionRef || isOpenCode(state.session)!==isOpenCode(session);
      reset();state.session=session;state.scope=scope || session.scopes.find(x=>x.label==='main') || session.scopes[0];
      if(switched){state.follow=true;state.followPaused=false;state.selectionPause=false;}
      $('history-title').textContent=sessionName(session)+(session.uuid && sessionName(session)!==session.uuid?' · '+session.uuid:'');ribbon();
      if(isManagerRow(session))state.topManager=session;
      if(childGeneration!==state.childGeneration){state.childError=null;state.childLoaded=false;message('subagents-status','');}
      reveal(session);disclosure();inventory();renderChildren();
      if(isManagerRow(session) && globalThis.opcManagerOrder?.parse(...managerNames(session)))void loadManagerFamily();
      const scopes=doc.createDocumentFragment();for(const item of session.scopes){const opt=element('option',item.label);opt.value=item.scopeRef;scopes.append(opt);}
      $('history-scope').replaceChildren(scopes);$('history-scope').value=state.scope?.scopeRef || '';
      if(!state.scope){message('history-status','No supported scope; inspect inventory diagnostics.',true);return;}
      const auto=!isOpenCode(session) && !archived(session) && parentEditor && state.scope.label==='main' && session.source==='host' && role(session)==='manager' && (!ended(session) || reconnectable(session));
      // Inventory carries session-only authority, so validation starts alongside
      // the history read, never after a cold replay drain. No text is sent.
      if(auto && ref(session.targetRef))useTarget(session.targetRef,true);
      state.leaf=leaf;
      // A claimed OpenCode Manager is validated by its full sid in its own GET, beside the history read:
      // an empty or still-loading conversation is still usable as a target.
      const managerCheck=isOpenCode(session) && !!session.managerClaim ? validateOpenCodeManager(session,state.generation) : null;
      await page(null,'initial');
      if(managerCheck)await managerCheck;
      if(auto && state.session===session && !session.targetRef && state.target && !state.targetAction)useTarget(state.target,true);
      // Following: the next poll tick pages on to the newest turn instead of resting on page one.
      if(state.follow && state.session===session && state.next && !state.indexingContinuation)state.nextPoll=0;
    }
    // @beacon[
    //   id=opc-pane@openCodeSession,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Builds the viewer's OpenCode conversation object from a sidebar row, a bare ses_ id (listed or not) or the conversation on screen: backend and source opencode, one main scope, every Claude capability and identity absent (no targetRef, execution, generation, uuid, parent uuid or agent id), recorded display facts only (title else the id; agent/model/folder/parent else null; role word from the sidebar tree). A row that claims a genuine root Manager (manager:true/kind manager) or a bare sid is marked for the server's full-id validation; until it answers, every capability stays false.,
    // ]
    function openCodeSession(rowOrId) {
      const given=rowOrId && typeof rowOrId==='object'?rowOrId:null,id=given?given.id:rowOrId;
      if(typeof id!=='string' || !/^ses_[A-Za-z0-9]{8,64}$/.test(id))throw new Error('not an OpenCode session id: '+String(id).slice(0,80));
      const listed=state.oc.rows.find(r=>r.id===id),node=state.tree?.byId.get(id);
      const fact=key=>[listed?.[key],given?.[key]].find(v=>typeof v==='string' && v) || null;
      const busy=[listed?.busy,given?.busy].find(v=>typeof v==='boolean');
      const claimed=openCodeManagerClaim(given || listed),claim=claimed?'row':typeof rowOrId==='string'?'id':given?.managerClaim || null;
      const ocRole=node?.backend==='opencode'?nodeRole(node):typeof given?.ocRole==='string'?given.ocRole:claimed?'manager':null;
      return {backend:'opencode',source:'opencode',id,sessionRef:id,scopes:[{label:'main',scopeRef:'main'}],
        capabilities:{history:false,nativeInput:false,controls:false,subagents:false,reconnect:false},
        title:fact('title') || id,agent:fact('agent'),model:fact('model'),directory:fact('directory'),parentId:fact('parentId'),busy:busy ?? null,
        manager:null,target:null,managerClaim:claim,managerError:null,managerIdentityChanged:false,ocRole};
    }
    // @beacon[
    //   id=opc-pane@historySession,
    //   slice_labels=opc-pane,opc-history,
    //   kind=ast,
    //   comment=The viewer's 📦 History conversation object (openCodeSession's counterpart) from an archived row of either backend: backend history with its archived key and the row's own handles and one main scope, no target or Manager claim, so address reads /api/history/events and no live route, Inspect, Send, Refine target or context applies; an OpenCode row keeps its ses_ id, title and tree role word for its outline and header.,
    // ]
    function historySession(row) {
      // Its backend is its archived key's (an OpenCode program row names backend opencode only when it is a Manager).
      const oc=String(row.archived?.key || '').startsWith('opencode:'),node=oc?state.tree?.byId.get(row.id):null;
      return {...row,backend:'history',storeRef:'history',key:row.archived.key,archivedBackend:oc?'opencode':'claude',id:oc?row.id:null,
        title:oc?row.title || row.id:row.title,ocRole:oc?(node?.backend==='opencode'?nodeRole(node):row.ocRole || null):null,
        targetRef:null,manager:null,managerClaim:null};
    }
    // @beacon[
    //   id=opc-pane@openArchived,
    //   slice_labels=opc-pane,opc-history,
    //   kind=ast,
    //   comment=An archived tool call's ↳ child link: one GET /api/history/sessions?keys= for that archived key, then the child opens read-only in the same viewer (browse); a key the archive does not hold says so in the notice line, nothing live is read.,
    // ]
    async function openArchived(key) {
      if(sidebarOnly || !HISTORY_KEY.test(key || ''))return;
      try {
        const data=await json('/api/history/sessions?'+new URLSearchParams({keys:key}),{},Infinity);
        if(!data || data.schemaVersion!==1 || data.backend!=='history' || !Array.isArray(data.claude) || !Array.isArray(data.opencode))throw new Error('invalid History answer');
        const row=[...data.claude,...data.opencode].find(r=>archived(r) && r.archived?.key===key && ref(r.sessionRef) && Array.isArray(r.scopes) && r.scopes.length);
        if(!row){notice('That child conversation is not in 📦 History ('+key.slice(key.indexOf(':')+1)+').',true);return;}
        showPane('conversation');await browse(historySession(row));
      } catch(e) {notice('Cannot open that archived conversation: '+e.message,true);}
    }
    // A root-Manager GET is adoption-ready only when it names this exact full sid and witness with Send authority.
    function openCodeManagerData(data,sid) {
      return !!data && data.schemaVersion===1 && data.backend==='opencode' && data.sessionId===sid &&
        data.target?.backend==='opencode' && data.target.sessionId===sid && /^[a-f0-9]{64}$/.test(data.target.witness || '') &&
        data.capabilities?.send===true;
    }
    // A genuine Manager that no longer validates (a replaced identity, or no longer a root Manager) loses
    // Send authority at once; its facts stay visible and a deliberate reselection can validate it again.
    function revokeOpenCodeManager(session,message) {
      session.managerIdentityChanged=true;session.target=null;
      session.capabilities={...session.capabilities,send:false,control:false,context:false};
      session.managerError=String(message || 'OpenCode Manager authority changed').slice(0,200);
    }
    // Adopt one server-validated Manager readback: its witness, model readback, per-Manager context and
    // target capability. A changed witness (a replaced session) never reuses the old target.
    function applyOpenCodeManager(session,data,{autoTarget=false}={}) {
      const prior=session.manager,changed=!!session.managerIdentityChanged || (!!prior?.target && prior.target.witness!==data.target.witness);
      session.managerIdentityChanged=changed;session.target=changed?null:data.target;
      session.manager={...data,context:data.context && typeof data.context==='object'?data.context:null};
      session.capabilities={...session.capabilities,
        send:data.capabilities?.send===true,control:data.capabilities?.control===true,context:data.capabilities?.context===true};
      session.ocRole='manager';session.managerError=null;session.managerReadError=null;session.managerReadAt=Date.now();
      if(typeof data.title==='string' && data.title)session.title=data.title;
      for(const key of ['agent','directory'])if(typeof data[key]==='string' && data[key])session[key]=data[key];
      if(Number.isFinite(data.createdAt))session.createdAt=data.createdAt;
      if(typeof data.busy==='boolean' || data.busy===null)session.busy=data.busy ?? null;
      if(Number.isFinite(data.observedAt)){state.ocObservedAt=data.observedAt*1000;state.ocBusyFact=typeof data.busy==='boolean'?data.busy:null;}
      disclosure();controls();contextGauge();
      if(autoTarget && parentEditor && !state.targetAction && openCodeManagerTarget(session))useTarget(session.target,true);
      return true;
    }
    // @beacon[
    //   id=opc-pane@validateOpenCodeManager,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=GET-only validation of a selected OpenCode Manager by its full sid, beside the history read: a genuine root Manager gets its witness, model and per-Manager context, then the parent editor is auto-targeted; any other answer or a failure leaves the conversation exactly view-only and the editor's target untouched. Never launches, resumes, sends or controls anything.,
    // ]
    async function validateOpenCodeManager(session,generation) {
      try {
        const data=await json('/api/opencode/manager?'+new URLSearchParams({id:session.id}));
        if(generation!==state.generation || state.session!==session)return false;
        if(!openCodeManagerData(data,session.id))throw new Error('OpenCode Manager response schema/identity mismatch');
        return applyOpenCodeManager(session,data,{autoTarget:true});
      } catch(e) {
        if(generation===state.generation && state.session===session) {
          session.managerError=String(e.message || e).slice(0,200);
          const authority=e.code===409 && (e.status==='not-manager' || e.status==='identity');
          if(authority && session.manager)revokeOpenCodeManager(session,e.message);
          // A row that claimed a Manager but did not validate says so; an ordinary bare sid stays plain view-only.
          if(session.managerClaim==='row' || session.manager){disclosure();controls();}
        }
        return false;
      }
    }
    // @beacon[
    //   id=opc-pane@pollOpenCodeManager,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The selected validated OpenCode Manager's own two-second GET: refresh busy/idle, model and the newest measured call's occupancy without another target action or any Claude read; failures keep the last facts and say the read failed, and a changed witness drops Send authority rather than reusing it.,
    // ]
    async function pollOpenCodeManager() {
      const session=state.session,generation=state.generation;
      if(!session || !isOpenCode(session) || !session.manager || state.ocManagerBusy)return;
      state.ocManagerBusy=true;
      try {
        const data=await json('/api/opencode/manager?'+new URLSearchParams({id:session.id}));
        if(generation!==state.generation || state.session!==session)return;
        if(!openCodeManagerData(data,session.id))throw new Error('OpenCode Manager response schema/identity mismatch');
        applyOpenCodeManager(session,data,{autoTarget:false});
      } catch(e) {
        if(generation===state.generation && state.session===session){
          session.managerReadError=String(e.message || e).slice(0,200);
          if(e.code===409 && (e.status==='not-manager' || e.status==='identity')){revokeOpenCodeManager(session,e.message);disclosure();controls();}
          activity();
        }
      } finally {state.ocManagerBusy=false;state.ocManagerNextPoll=Date.now()+OC_MANAGER_MS;}
    }
    // @beacon[
    //   id=opc-pane@openOpenCode,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The desktop entry point for an OpenCode conversation: a sidebar row click, a subagent call's child link or the editor's browse-opencode notice (a bare ses_ id, listed or not) opens it through browse in the same viewer at its newest rows, Following. A genuine root Manager is GET-validated by that full sid and auto-targets its parent editor; any other session stays view-only, never a glance, a Send or an Inspect. A 📦 History row opens read-only as its historySession, never validated. The phone's sidebar-only page never opens one here.,
    // ]
    async function openOpenCode(rowOrId) {
      if(sidebarOnly)return;
      if(archived(rowOrId))return browse(historySession(rowOrId)); // a 📦 History row: read-only, never /api/opencode/manager
      let session;
      try{session=openCodeSession(rowOrId);}catch(e){notice('Cannot open that OpenCode conversation: '+e.message,true);return;}
      await browse(session);
    }
    // @beacon[
    //   id=opc-pane@loadState,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The sole loading-word predicate for the edge and Follow toggle: stale then resync then first-page/indexing wait then catching up (a slow or failed read: the rows on screen stay) then older read then real newer rows or behind then native index limit; metadata-only paging stays quiet.,
    // ]
    function loadState() {
      if(!state.session)return null;
      if(state.blocked || state.resyncWarn)return 'stale';
      if(state.resync)return 'reconnecting';
      if(!state.epoch || state.indexingContinuation)return 'still-loading';
      if(state.catchingUp)return 'catching-up';
      if(state.loadingMode==='older')return 'older';
      if(state.next && state.rowsAhead!==false)return state.follow?'newer':'behind';
      if(state.indexLimit)return 'limit';
      return null;
    }
    // @beacon[
    //   id=opc-pane@historyEdge,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The edge line reads loadState only: stopped updating with Reconnect, reconnecting in place, calm first-page/indexing progress, a quiet catching-up note after a slow or failed read (rows stay, clears by itself), older above, real newer loading or behind with its actions, or native history not indexed; metadata-only paging stays hidden. Beside it, a damaged-rows read adds its own quiet line (stored rows unreadable: showing what the raw log allows) without replacing that line or the rows, and with no warning kind or action.,
    // ]
    function edge() {
      const node=$('history-edge');if(!node)return;
      const last=state.rows.get(state.order.at(-1)),since=last?.time?fullTime(last.time):'';
      const damaged=state.rowsUnreadable?DAMAGED_ROWS:'';
      let kind='',line='',actions=[];
      switch(loadState()) {
      case 'stale':kind='stale';line='⚠ Not updating'+(since?' since '+since:'')+': '+(state.resyncWarn || state.blockedReason || 'the server refused the next read')+
        (state.resyncWarn?' · still retrying by itself':'')+'. Newer messages may exist below this point.';
        actions=[['⟳ Reconnect',()=>void reconnect()]];break;
      case 'reconnecting':kind='loading';line='Reconnecting … the rows on screen stay.';break;
      case 'still-loading': {
        kind='loading';line=writable(state.session)?'Still loading. Feel free to send messages while the load completes.':'Still loading …';
        const source=sourceText();if(source)line+=' · '+source;
        break;
      }
      case 'catching-up':kind='loading';line='Catching up … the rows on screen stay; newer messages follow by themselves.'+
        (writable(state.session)?' Feel free to send messages meanwhile.':'');break;
      case 'older':kind='loading';line='Loading older messages above …';break;
      case 'newer':kind='loading';line='Loading newer messages …';break;
      case 'behind':kind='behind';line='Newer messages are not loaded below this point.';actions=[['↓ Load them',()=>void navigate(loadNewer)],['Jump to latest',()=>setFollow(true)]];break;
      case 'limit':kind='limit';
        const limit=state.indexLimit;
        line=typeof limit.message==='string'&&limit.message?'⚠ '+limit.message:'Later history is not indexed here (the viewer’s index bound was reached): newer messages are not shown.';
        // Native transcripts still report their own contained-reader limit.
        break;
      }
      const signature=kind+'|'+line+'|'+damaged;
      if(node.dataset.signature===signature)return;
      node.dataset.signature=signature;node.dataset.kind=kind || (damaged?'notice':'');node.hidden=!(kind || damaged);
      const parts=kind?[element('span',line,'edge-text')]:[];
      for(const [name,act] of actions){const b=element('button',name,'link edge-action');b.type='button';b.onclick=act;parts.push(b);}
      // A separate quiet line of its own: the load state above keeps its wording, and the rows keep their place.
      if(damaged)parts.push(element('span',damaged,'edge-text edge-damaged'));
      node.replaceChildren(...parts);
    }
    // @beacon[
    //   id=opc-pane@reconnectHistory,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Reconnect reads fresh host handles, adding native history only for the stored conversation actually selected (a child through its parent), then finds that same stable identity and reloads newest Following. No selection opens the newest Manager; OpenCode reopens the same ses_ and a 📦 History conversation its same key directly, without any Claude list or fallback.,
    // ]
    async function reconnect({afterRestart=null}={}) {
      if(state.reconnecting)return;state.reconnecting=true;ribbon();
      const was=state.session,scopeLabel=state.scope?.label;
      try {
        if(isOpenCode(was) || archived(was)) {
          const row=archived(was)?was:openCodeSession(was); // an archived one reopens by its key: no list, Manager or subagents read
          state.failed=null;state.pending=null;mark();
          message('inspect-status','Inspect selects one held call in the existing editor.');message('history-status','');
          state.follow=true;state.followPaused=false;state.selectionPause=false;state.newCount=0;
          await browse(row);
          notice(afterRestart?'Reloaded '+sessionName(row)+' at its newest turn: the rows on screen could not be matched after the pane server lost its place ('+afterRestart+'); nothing was lost':
            'Reconnected: '+sessionName(row)+' · its newest turn');
          return;
        }
        const withHistory=!!was && (role(was)==='subagent'?state.childParent?.source:was.source)!=='host';
        const before=state.inventoryAt;await list(withHistory);if(state.inventoryAt===before)await list(withHistory); // one retry: another tab may hold the scan
        const row=was?await sameSession(was):newestManager();
        if(!row){notice(was?'Reconnect: '+sessionName(was)+' is no longer listed; choose a session':'Reconnect: no Manager is listed yet',true);return;}
        if(afterRestart){
          // A resync whose rows could not be matched (new code changed how rows are built): the full
          // reload is the honest fallback, said neutrally; nothing was lost.
          state.follow=true;state.followPaused=false;state.selectionPause=false;state.newCount=0;
          await browse(row,row.scopes?.find(s=>s.label===scopeLabel) || null);
          notice('Reloaded '+sessionName(row)+' after the pane server restarted ('+afterRestart+') · its newest turn');
          return;
        }
        // The fold's lines from the old handles start again from neutral; the orange subagent line
        // refreshes by itself once its discovery succeeds.
        state.failed=null;state.pending=null;mark();
        message('inspect-status','Inspect selects one held call in the existing editor.');message('history-status','');
        state.follow=true;state.followPaused=false;state.selectionPause=false;state.newCount=0;
        await browse(row,row.scopes?.find(s=>s.label===scopeLabel) || null);
        notice((was?'Reconnected: ':'Opened the newest Manager: ')+sessionName(row)+' · its newest turn');
      } catch(e) {notice('Reconnect failed: '+e.message,true);}
      finally {state.reconnecting=false;ribbon();}
    }
    // The same conversation in the freshly read list: its host execution (a newer generation is the
    // same conversation), its transcript, or a subagent found again under its parent. An OpenCode
    // conversation is the same ses_, rebuilt without any inventory or subagent read.
    async function sameSession(was) {
      if(isOpenCode(was))return openCodeSession(was);
      if(role(was)!=='subagent')
        return state.inventory.filter(r=>role(r)!=='subagent' && identityOf(r)===identityOf(was)).sort((a,b)=>(b.generation || 0)-(a.generation || 0))[0] || null;
      const parent=state.inventory.filter(r=>role(r)!=='subagent' && r.uuid===was.parentUuid).sort((a,b)=>Number(b.source==='host')-Number(a.source==='host'))[0];
      if(!parent)return null;
      let cursor=null;
      for(let i=0;i<64;i++) {
        const data=await json('/api/claude/subagents?'+new URLSearchParams({sessionRef:parent.sessionRef,...(cursor?{cursor}:{})}));envelope(data);
        const hit=listField(data.sessions,'subagents').find(r=>r && identityOf(r)===identityOf(was) && ref(r.sessionRef) && Array.isArray(r.scopes));
        if(hit)return hit;
        if(!data.hasMore || !ref(data.nextCursor))break;cursor=data.nextCursor;
      }
      return null;
    }
    // With nothing selected: the newest Manager (a live one first).
    function newestManager() {
      const created=r=>typeof r.createdAt==='number' && Number.isFinite(r.createdAt)?r.createdAt:0;
      return state.inventory.filter(r=>role(r)!=='subagent' && realRole(r)==='manager' && !r.hostTwin && Array.isArray(r.scopes))
        .sort((a,b)=>Number(writable(b))-Number(writable(a)) || created(b)-created(a))[0] || null;
    }
    // ------------------------------------------------------------- resync in place
    // Every row on screen carries its position (seq); a resync can only check positions.
    function resyncable() { return state.order.length>0 && state.order.every(k=>Number.isSafeInteger(state.rows.get(k)?.seq)); }
    function startResync(reason) {
      if(state.resync || !state.session)return;
      state.resync={since:Date.now(),reason,attempts:0,failures:0,nextAt:0,lo:null,hi:null,covered:-1};
      edge();ribbon();controls();
    }
    // A resident row takes a fresh record handle (its key), keeping its node, place and identity.
    function rekey(row,key) {
      const old=row.key;if(old===key)return;
      state.rows.delete(old);state.rows.set(key,row);row.key=key;
      const at=state.order.indexOf(old);if(at>=0)state.order[at]=key;
      if(row.identity && state.identities.get(row.identity)===old)state.identities.set(row.identity,key);
      if(state.startKey===old)state.startKey=key;
      if(state.lastJump?.key===old)state.lastJump.key=key;
    }
    // The resync's check: each fresh row at a position already on screen must be the same record
    // there (its kind, and its UUID wherever both have one), and no fresh row elsewhere may carry
    // the identity of a row on screen. A match takes the fresh handle in place, so a reply cut off
    // mid-stream (no UUID yet) completes instead of freezing; anything else is a mismatch.
    function adopt(events,cursor) {
      const mismatch=why=>Object.assign(new Error(why),{resyncMismatch:true});
      const bySeq=new Map(),identities=new Set();
      for(const key of state.order){const row=state.rows.get(key);if(row){bySeq.set(row.seq,row);if(row.identity)identities.add(row.identity);}}
      for(const event of events) {
        if(!event || !ref(event.recordRef) || !Number.isSafeInteger(event.seq))throw mismatch('a row came back without its position');
        const row=bySeq.get(event.seq),identity=eventIdentity(event);
        if(!row){if(identity && identities.has(identity))throw mismatch('a message on screen moved to row '+event.seq);continue;}
        const was=JSON.parse(row.signature),old=typeof was.uuid==='string'&&was.uuid?was.uuid:null,now=typeof event.uuid==='string'&&event.uuid?event.uuid:null;
        if(was.kind!==event.kind || (old && now && old!==now))throw mismatch('row '+event.seq+' is a different message now');
        rekey(row,event.recordRef);row.from=cursor;row.page=null; // reconcile tags it with this read's checkpoint
        state.resync.covered=Math.max(state.resync.covered,event.seq);
      }
      if(events.length)state.resync.covered=Math.max(state.resync.covered,events.at(-1).seq);
    }
    // A read the server refused (identity, schema, a limit), as opposed to one that was slow or went unanswered.
    function refusedRead(e) { return [400,403,404,409,413].includes(e?.code) || /schema|identity|limit/.test(e?.message || ''); }
    // A read that was slow or went unanswered (a 5xx: the server's own deadline or the host unavailable; a timed-out
    // or cancelled request; the network). Only these are "catching up"; any other failure keeps its own wording.
    function slowRead(e) { return (Number.isInteger(e?.code) && e.code >= 500) || ['AbortError','TypeError'].includes(e?.name) || /timed out/.test(e?.message || ''); }
    function retryResync(job,reason,unlisted=false,slow=false) {
      job.failures++;job.reason=reason;
      // Warn only when re-acquiring really fails: the conversation is gone, or reads keep being refused.
      // A slow or unanswered read is the host catching up: quiet, however long it takes.
      if(slow)state.catchingUp=state.catchingUp || {since:job.since};
      else if(unlisted?job.failures>=2:Date.now()-job.since>=RESYNC_WARN_MS)state.resyncWarn=reason;
      job.nextAt=Date.now()+Math.min(15000,1000*2**Math.min(4,job.failures-1));
      edge();ribbon();controls();
    }
    function finishResync(job) {
      if(state.resync!==job)return;
      state.resync=null;state.resyncWarn=null;state.catchingUp=null;state.blocked=false;state.blockedReason=null;state.observationError=null;state.nextPoll=0;
      message('history-status','Resynced after a pane-server restart: the '+state.order.length+' rows on screen were checked against the server and continue in place (nothing lost or doubled).');
      edge();ribbon();controls();activity();
    }
    // @beacon[
    //   id=opc-pane@resyncHistory,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Resync void handles from fresh host summaries (native history only for the selected stored conversation), match the same identity and verify fromSeq rows from its oldest resident position. Child activity re-delivers, mismatches reload, real failures warn; OpenCode still re-reads that same ses_ and a 📦 History conversation (a 409 stale-epoch: it was archived again) its same key by position with no Claude inventory.,
    // ]
    async function resync() {
      const job=state.resync;
      if(!job || state.resyncBusy || state.reconnecting || Date.now()<job.nextAt)return;
      state.resyncBusy=true;job.attempts++;
      const fallback=async why=>{state.resync=null;state.resyncWarn=null;await reconnect({afterRestart:why});};
      try {
        if(!(job.swapped && state.session===job.row)) {
          const was=state.session,scopeLabel=state.scope?.label;
          let row;
          if(archived(was))row=was; // 📦 History: its handles are its key's; re-read by position, never a list
          else if(isOpenCode(was))row=openCodeSession(was); // never the Claude list, a Manager or a subagents read
          else {
            const withHistory=!!was && (role(was)==='subagent'?state.childParent?.source:was.source)!=='host';
            const before=state.inventoryAt;await list(withHistory);
            if(state.resync!==job)return;
            if(state.inventoryAt===before)return retryResync(job,'the session list could not be read',false,true);
            row=await sameSession(was);
          }
          if(state.resync!==job)return;
          if(!row)return retryResync(job,sessionName(was)+' is no longer listed',true);
          const scope=row.scopes?.find(s=>s.label===scopeLabel) || row.scopes?.find(s=>s.label==='main') || row.scopes?.[0];
          if(!scope)return retryResync(job,'no supported scope',true);
          if(!resyncable())return void await fallback('the rows on screen carry no positions');
          // Fresh handles for the same conversation; reads in flight on the old ones are dropped.
          state.abort?.abort();state.generation++;state.loading=false;state.blocked=false;state.blockedReason=null;
          state.session=row;state.scope=scope;
          state.epoch=null;state.leaf=null;state.target=null;state.pages=[];state.pageAt=-1;state.delta=null;state.next=null;state.indexingContinuation=null;
          state.subagentActivity.clear(); // re-delivered under the fresh handles with the rows they follow
          if(state.childParent){const fresh=identityOf(state.childParent)===identityOf(was)?row:
            state.inventory.find(r=>role(r)!=='subagent' && identityOf(r)===identityOf(state.childParent));
            if(fresh){state.childParent=fresh;state.childNextPoll=0;state.childPaged=false;state.childGeneration++;}}
          reveal(row);inventory();
          const seqs=state.order.map(k=>state.rows.get(k).seq);
          Object.assign(job,{swapped:true,row,lo:seqs[0],hi:seqs.at(-1),covered:seqs[0]-1,cursor:{fromSeq:seqs[0]}});
        }
        for(let i=0,waits=0;i<400 && state.resync===job;i++) {
          const added=await page(job.cursor,'resync');
          if(state.resync!==job)return;
          if(added===null){await new Promise(resolve=>setTimeout(resolve,0));continue;}
          const error=state.readError;
          if(error?.resyncWait) {
            // A cold index: read again at once (each read drains more of it), a bounded number per step.
            if(++waits>=60 || doc.hidden){job.nextAt=Date.now();return;}
            await new Promise(resolve=>setTimeout(resolve,0));continue;
          }
          if(error?.resyncMismatch)return void await fallback(error.message);
          if(error){job.swapped=false;return retryResync(job,error.message,false,slowRead(error));}
          if(job.covered>=job.hi)return finishResync(job);
          if(!state.next)return void await fallback('the server has fewer rows than were on screen');
          job.cursor=state.next;
        }
      } catch(e) {if(state.resync===job){job.swapped=false;retryResync(job,e.message);}}
      finally {state.resyncBusy=false;}
    }
    async function browseUuid(uuid) {
      const find=()=>state.inventory.find(r=>r.source==='host' && r.uuid===uuid && role(r)!=='subagent');
      let row=find();
      if(!row){await list();row=find();}
      if(row && state.session?.sessionRef!==row.sessionRef)await browse(row);
    }
    // The page URL by backend, decided first: an archived conversation reads /api/history/events with its key, an
    // OpenCode one /api/opencode/events with its ses_ id; each with the current epoch and exactly one of a server
    // cursor or a position (never scope/leaf/target).
    function address(cursor) {
      if(archived(state.session)) {
        const q=new URLSearchParams({key:state.session.key});
        if(state.epoch)q.set('fileEpoch',state.epoch);
        if(cursor && typeof cursor==='object')q.set('fromSeq',String(cursor.fromSeq));else if(cursor)q.set('cursor',cursor);
        return '/api/history/events?'+q;
      }
      if(isOpenCode(state.session)) {
        const q=new URLSearchParams({id:state.session.id});
        if(state.epoch)q.set('fileEpoch',state.epoch);
        if(cursor && typeof cursor==='object')q.set('fromSeq',String(cursor.fromSeq));else if(cursor)q.set('cursor',cursor);
        return '/api/opencode/events?'+q;
      }
      const q=new URLSearchParams({sessionRef:state.session.sessionRef,scopeRef:state.scope.scopeRef});
      if(state.epoch)q.set('fileEpoch',state.epoch);
      // A position read ({fromSeq}) is a first page starting at that row, with fresh cursors.
      if(cursor && typeof cursor==='object')q.set('fromSeq',String(cursor.fromSeq));
      else if(cursor)q.set('cursor',cursor);else if(state.leaf)q.set('leafRef',state.leaf);
      return '/api/claude/events?'+q;
    }
    function anchor() {
      const viewport=$('history-viewport'), top=viewport.getBoundingClientRect().top;
      for(const key of state.order){const row=state.rows.get(key);if(!row.node.hidden && row.node.getBoundingClientRect().bottom>top)return {key,y:row.node.getBoundingClientRect().top};}
      return null;
    }
    function restoreAnchor(saved) { const row=saved&&state.rows.get(saved.key); if(row)$('history-viewport').scrollTop+=row.node.getBoundingClientRect().top-saved.y; }
    function atLatest() {const v=$('history-viewport');return v.scrollHeight-v.scrollTop-v.clientHeight<24;}
    function stickToLatest() {const v=$('history-viewport');v.scrollTop=v.scrollHeight;}
    // The one follow invariant: "Following" lit means pinned to the last pixel. Every path that
    // grows the view pins directly; this check also runs on a clock and after every poll, so
    // a growth path nobody thought of can lag by at most a second, never stay unpinned.
    // A hand scroll in progress (last 1.5 s) is left alone: its own handler decides.
    function enforceFollow() {
      if(!state.follow || !state.session || Date.now()-state.scrollIntent<1500)return;
      const v=$('history-viewport');if(v && v.scrollHeight-v.scrollTop-v.clientHeight>1)stickToLatest();
    }
    // A text selection in the conversation used to hold the view silently while the toggle
    // still read "Following" (and a selection left behind in this frame, when Dan went back
    // to the editor, held it forever). Now selecting text pauses following visibly; the
    // pause ends by itself when the selection is cleared or Dan leaves the conversation.
    function selectionInHistory() {
      const selection=doc.getSelection?.();if(!selection || selection.isCollapsed || !selection.rangeCount)return false;
      return selected($('history-events'));
    }
    function selectionChanged() {
      if(state.follow && state.session && selectionInHistory() && (doc.hasFocus?.() ?? true)) {
        state.follow=false;state.followPaused=true;state.selectionPause=true;ribbon();
      } else if(state.selectionPause && !selectionInHistory())setFollow(true,{byScroll:true});
    }
    function leftConversation() {
      if(!state.selectionPause)return;
      // The copy is done; an inactive selection in this frame has no further use and would
      // otherwise keep deferring that row's repaints.
      try{if(selectionInHistory())doc.getSelection().removeAllRanges();}catch(_){/* display only */}
      setFollow(true,{byScroll:true});
    }
    // @beacon[
    //   id=opc-pane@setClaudeFollow,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Follow toggle: on pins the newest turn (jumping to latest first) and stays pinned to the last pixel, and counts everything as seen; off never moves the reading position. A deliberate scroll up or a text selection pauses it visibly; scrolling back to the bottom, clearing the selection or leaving the frame resumes.,
    // ]
    function setFollow(on,{byScroll=false}={}) {
      state.follow=!!on;state.followPaused=!on && byScroll;state.selectionPause=false;
      if(on){state.newCount=0;seen().seenAll();if(!byScroll && state.session)void latest();else stickToLatest();}
      ribbon();
    }
    // The rows' seen-ness (seen-latest.js): one tracker for the conversation viewport; a final reply is classify's turn-final.
    function seen() {
      return state.seen || (state.seen=globalThis.opcSeenLatest?.create($('history-viewport'),{onChange:()=>ribbon(),isFinal:node=>node.classList.contains('turn-final')}) ||
        {enabled:false,track:()=>false,seenAll(){},below:()=>0});
    }
    // live: the rows came from the live tail while paused; answers whether new material arrived.
    function trackRows(rows,live) {
      let arrived=false;
      for(const row of rows)if(state.rows.get(row.key)===row)arrived=seen().track(row.node,{live,version:row.signature})||arrived;
      return arrived;
    }
    // Any part of the last displayed row's last block on screen: nothing is below.
    function lastInView() {
      const shows=node=>!node.hidden && !node.classList?.contains('seen-end') && node.getClientRects().length>0;
      let row=null;for(let i=state.order.length-1;i>=0 && !row;i--){const node=state.rows.get(state.order[i])?.node;if(node && shows(node))row=node;}
      if(!row)return true;
      const last=[...row.children].filter(shows).at(-1) || row,r=last.getBoundingClientRect(),v=$('history-viewport').getBoundingClientRect();
      return r.bottom-v.top>1 && v.bottom-r.top>1;
    }
    // @beacon[
    //   id=opc-pane@historyLatestBadge,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The floating ↓ Latest badge at the bottom right of the conversation (the phone's, on the desktop): shown while paused with the last row wholly off screen or newer rows not loaded; plain when all below has been seen, the accent (orange) "N new ↓" (seen-latest.js) while the unseen rows ending below the view are only working turns, red while at least one of them is a final reply (classify's turn-final, judged at every repaint), pulsing on each arrival and on turning red; the same count feeds the Paused label. Click = the Follow toggle's jump.,
    // ]
    function latestBadge() {
      const tracker=seen(),unseen=tracker.enabled?tracker.tally():{count:state.newCount,final:0};state.unseenBelow=unseen.count;
      const badge=$('history-latest-badge');if(typeof badge?.getClientRects!=='function')return; // no layout (a test double): nothing to place
      badge.hidden=!state.session || !state.order.length || state.follow || (loadState()!=='behind' && lastInView());
      if(globalThis.opcSeenLatest)opcSeenLatest.paintBadge(badge,badge.hidden?0:state.unseenBelow,'↓ Latest',unseen.final);
    }
    function ribbon() {
      const loading=loadState(),again=$('ribbon-reconnect'),stale=loading==='stale',behind=loading==='behind';
      if(again){again.disabled=!!state.reconnecting;again.dataset.attention=String(stale);text(again,state.reconnecting?'⟳ Reconnecting …':'⟳ Reconnect');}
      const button=$('history-follow');if(!button)return;
      latestBadge();
      // Following lit means pinned to the newest turn the server has: a view that stopped updating
      // (an expired handle, a refused read) never shows it, whatever the toggle was.
      button.setAttribute('aria-pressed',String(state.follow && !stale));
      if(stale)button.dataset.state='stale';else delete button.dataset.state;
      const suffix={newer:' · loading newer',older:' · loading older','still-loading':' · still loading',reconnecting:' · reconnecting'}[loading] || '';
      text($('history-follow-label'),stale?'Not updating':state.follow?'Following'+suffix:
        (state.selectionPause?'Paused · text selected':'Paused')+(state.unseenBelow?` · ${state.unseenBelow} new ↓`:'')+(behind?' · newer below':''));
      button.title=stale?'This conversation stopped updating: '+(state.resyncWarn || state.blockedReason || 'the server refused the next read')+'. Newer messages may exist. ⟳ Reconnect (right end of this ribbon) reloads it at its newest turn.':
        state.follow?'Following the newest turn. Click (or scroll up) to pause; the view then never moves on its own.':
        'Paused: new turns are added below without moving your view. Click to jump to the newest turn and follow it'+(state.followPaused?'; scrolling to the bottom also resumes.':'.');
      const tools=$('history-tools'),chip=$('ribbon-tools');if(chip&&tools)chip.setAttribute('aria-pressed',String(!!tools.checked));
      const agents=$('ribbon-subagents');if(agents){agents.setAttribute('aria-pressed',String(!state.activityHidden));
        agents.title=state.activityHidden?'Subagent activity folded: each group shows one header line. Click to show every entry.':'Subagent activity shown (one colored group per subagent between your turns). Click to fold each group to one line.';}
      if($('ribbon-session')){text($('ribbon-session'),state.session?(state.session.manager?'OpenCode Manager · ':'')+sessionName(state.session):'');
        $('ribbon-session').title=!state.session?'':archived(state.session)?'📦 History · '+state.session.key+' · read-only':isOpenCode(state.session)?
          (state.session.manager?'OpenCode Manager '+state.session.id+' · direct Send/Refine target · validated by full id':'OpenCode session '+state.session.id+' · view only'):'UUID '+(state.session.uuid || 'unknown');}
      for(const id of ['nav-start','nav-user-prev','nav-user-next','nav-final-prev','nav-final-next'])if($(id))$(id).disabled=!state.session;
    }
    // Jump to the previous/next turn of one kind relative to the top of the view.
    const LAND=6;
    // Answers whether it moved; navigate() loads more history when it did not.
    function jump(kind,direction) {
      const viewport=$('history-viewport'),top=viewport.getBoundingClientRect().top;let target=null;
      const last=state.lastJump?.kind===kind?state.lastJump:null;
      for(const key of state.order){const row=state.rows.get(key);if(!row?.node.classList.contains(kind) || row.node.offsetParent===null)continue;
        const y=row.node.getBoundingClientRect().top-top;
        // A jump lands its target 6px below the top: "next" must look past that spot,
        // or it finds the same row again (it used to stick on the first one). Near the
        // bottom the view cannot scroll that far, so the row just jumped to is skipped too.
        const stuck=last && last.key===key && Math.abs(last.y-y)<2;
        if(direction<0 && y<-4 && !(stuck && last.direction<0))target=row;
        else if(direction>0 && y>LAND+4 && !stuck){target=row;break;}}
      if(!target)return false;
      if(state.follow)setFollow(false,{byScroll:true});
      viewport.scrollTop+=target.node.getBoundingClientRect().top-top-LAND;
      state.lastJump={kind,direction,key:target.key,y:target.node.getBoundingClientRect().top-top};
      return true;
    }
    function contains(node,child) { for(let at=child;at;at=at.parentNode)if(at===node)return true;return false; }
    function selected(node) {
      const selection=doc.getSelection?.();if(!selection || selection.isCollapsed || !selection.rangeCount)return false;
      if(contains(node,selection.anchorNode)||contains(node,selection.focusNode))return true;
      for(let i=0;i<selection.rangeCount;i++)try{if(selection.getRangeAt(i).intersectsNode(node))return true;}catch(_){/* detached range */}
      return false;
    }
    function protectedRow(row) {return selected(row.node)||contains(row.node,doc.activeElement);}
    function removesFocusedTool(row,event) {return row.calls.some(call=>contains(call.node,doc.activeElement)&&!(event.calls || []).some(c=>c.id===call.id&&c.block===call.block)) ||
      row.thinking.some(block=>contains(block.node,doc.activeElement)&&!listField(event.thinking,'thinking').some(b=>b?.blockIndex===block.blockIndex));}
    function text(node,value) {if(node.textContent!==value)node.textContent=value;}
    function mark() {
      for(const row of state.rows.values())for(const call of row.calls){
        const key=call.key,node=call.node;
        node.classList.toggle('accepted',state.accepted?.key===key);node.classList.toggle('pending',state.pending?.key===key);node.classList.toggle('failed',state.failed===key);
        node.setAttribute('aria-pressed',String(state.accepted?.key===key));
      }
    }
    function validateThinking(event) {
      const blocks=listField(event.thinking,'thinking');
      if(blocks.some((b,i,all)=>!b || !Number.isSafeInteger(b.blockIndex) || b.blockIndex<0 ||
        all.findIndex(x=>x.blockIndex===b.blockIndex)!==i))throw new Error('invalid thinking summary identity schema');
      for(const b of blocks)if(!['stored','omitted','redacted','body-limit'].includes(b.availability) || typeof b.streaming!=='boolean' ||
        (b.availability==='stored'&&typeof b.text!=='string'))warn('thinking display metadata');
    }
    // The ONE end-of-turn receipt renderer. An OpenCode receipt adds its recorded reasoning tokens (the
    // turn's last step) and shows an unknown cost or token count as —; a recorded zero stays 0.
    function resultMarker(event) {
      const receipt=event.receipt || {},oc=isOpenCode(state.session),parts=[receipt.origin?.kind==='task-notification'?'Background result':'End of turn'];
      const outcome=typeof receipt.subtype==='string'&&receipt.subtype?receipt.subtype:receipt.is_error===true?'error':receipt.is_error===false?'completed':'outcome unknown';
      parts.push(outcome);
      if(receipt.is_error===true && outcome!=='error')parts.push('error receipt');
      if(typeof receipt.total_cost_usd==='number'&&Number.isFinite(receipt.total_cost_usd)&&receipt.total_cost_usd>=0)
        parts.push('list-price estimate $'+receipt.total_cost_usd.toFixed(4)+' (not billed spend)');
      else if(oc)parts.push('cost —');
      const usage=[];
      for(const [key,label]of [['input_tokens','input'],['output_tokens','output'],['reasoning_tokens','reasoning'],['cache_read_input_tokens','cache read'],['cache_creation_input_tokens','cache write']]) {
        const n=receipt.usage?.[key];if(Number.isSafeInteger(n)&&n>=0)usage.push(label+' '+n.toLocaleString('en-US'));else if(oc)usage.push(label+' —');
      }
      if(usage.length)parts.push((oc?'tokens (last step): ':'tokens: ')+usage.join(', '));
      if(Array.isArray(receipt.permission_denials))parts.push('permission denials '+receipt.permission_denials.length);
      else if(Number.isSafeInteger(receipt.permission_denials_count)&&receipt.permission_denials_count>=0)parts.push('permission denials '+receipt.permission_denials_count);
      return parts.join(' · ');
    }
    // Refresh only an OpenCode button's witness, never its mounted body or disclosure.
    function bindOpenCodeCall(call,epoch) {
      const {sessionRef,scopeRef,recordRef,...identity}=call.openCodeIdentity;
      const key=JSON.stringify([sessionRef,scopeRef,epoch,recordRef,call.block,call.id,identity.callRef]);
      call.key=key;
      call.node.onclick=()=>inspect({key,backend:'opencode',...identity,fileEpoch:epoch});
    }
    function updateRow(row,event,epoch) {
      if(!ref(event.recordRef) || !Array.isArray(event.calls || []))throw new Error('invalid event identity');
      validateThinking(event);
      const node=row.node,oc=isOpenCode(state.session),hist=archived(state.session),sessionId=oc?state.session.id:null;
      // A readable time where the UUID used to be; the UUID stays on hover only.
      const when=fullTime(event.time),odd=event.status && !['stored','metadata'].includes(event.status)?event.status:'';
      text(row.meta,[when || 'time unknown',odd].filter(Boolean).join(' · '));
      row.meta.title=(when?'Recorded '+new Date(event.time*1000).toLocaleString('en-US')+'\n':'')+'Record UUID '+(event.uuid || event.recordRef);
      row.turnModel=turnModel(event);if(row.model)row.model.title=row.turnModel?.title || ''; // shown by classify on Claude blocks
      const result=event.kind==='result';node.classList.toggle('end-of-turn',result);
      if(result) {
        // Native result.result repeats the final answer. Keep its raw receipt in
        // the backend, but never mount it as a second reply in the conversation.
        text(row.prose,'');row.prose.remove();
        if(!row.result){row.result=element('div',undefined,'result-marker');node.append(row.result);}
        text(row.result,resultMarker(event));
      } else {
        if(row.result){row.result.remove();row.result=null;}
        if(row.prose.parentNode!==node)node.append(row.prose);
        const source=typeof event.text==='string'?event.text:`Stored body unavailable · ${event.status} · ${event.bodyBytes ?? event.textBytes ?? 'unknown'} bytes`;
        // Display-only Markdown on final genuine prose. Thinking and tool bodies
        // retain their separate safe-text/inspector paths; partial replies stay text.
        // OpenCode's labelled meta rows (an unknown part, a malformed tool) keep their recorded text as plain text.
        const markdown=['user','assistant'].includes(event.kind) && typeof event.text==='string' && event.partial!==true && !(oc && event.isMeta===true);
        if(globalThis.opcMessageMarkdown)opcMessageMarkdown.render(row.prose,source,markdown);
        else text(row.prose,source); // complete safe text if a local asset fails to load
        codeCopies(row.prose);
      }
      // The block's copy button copies the message alone, as its own Markdown source:
      // never thinking, tool input or output (those live in their own blocks/inspector).
      row.copySource=!result && ['user','assistant'].includes(event.kind) && typeof event.text==='string' && event.text.trim()?event.text:null;
      row.copy.hidden=row.copySource===null;
      // Read aloud the same message the copy button copies, once it is final.
      row.speakEvent=event;row.speak.hidden=row.copySource===null || event.partial===true || !speakable(event);
      const thinking=[];
      for(const b of result?[]:listField(event.thinking,'thinking')) {
        let block=row.thinking.find(x=>x.blockIndex===b.blockIndex);
        if(!block) {
          const details=element('details',undefined,'event-thinking'),summary=element('summary'),body=element('pre',undefined,'thinking-text');
          // OpenCode stores the reasoning itself, not a generated summary.
          const note=element('span',undefined,'thinking-note');summary.append(element('span',oc?'Thinking':'Thinking summary','thinking-name'),note);
          // 🔊 reads this thinking aloud; its press never opens or closes the disclosure.
          const speak=element('span','🔊','thinking-speak');speak.title='Read this thinking aloud (rewritten for listening)';
          speak.setAttribute('role','button');speak.setAttribute('tabindex','0');speak.setAttribute('aria-label','Read thinking aloud');summary.append(speak);
          // An empty disclosure has nothing to open: keep it a quiet one-line marker.
          summary.onclick=e=>{if(details.classList.contains('thinking-empty'))e?.preventDefault?.();};
          details.open=b.streaming===true;details.append(summary,body);block={node:details,summary,note,body,speak,blockIndex:b.blockIndex};
          const say=e=>{e?.preventDefault?.();e?.stopPropagation?.();readAloud(block.event,'thinking:'+block.blockIndex,block.text);};
          speak.onclick=say;speak.onkeydown=e=>{if(e.key==='Enter'||e.key===' ')say(e);};
        }
        // Absent or not-yet-arrived thinking is a small dim parenthetical, never an alarm.
        const stored=b.availability==='stored'&&typeof b.text==='string',hasText=stored&&b.text.length>0;
        const notes={omitted:'(omitted)',redacted:'(redacted)','body-limit':'(too large to show · body-limit)'};
        text(block.note,stored?(b.streaming?(hasText?'(streaming)':'(loading)'):(hasText?'':'(empty)')):(notes[b.availability] || '(availability unknown)'));
        text(block.body,hasText?b.text:stored || notes[b.availability]?'':'Summary availability unknown.');
        block.event=event;block.text=hasText?b.text:'';block.speak.hidden=!hasText || b.streaming===true || !speakable(event);
        block.node.classList.toggle('thinking-empty',!hasText && (stored || !!notes[b.availability]));
        thinking.push(block);
      }
      for(const block of row.thinking)if(!thinking.includes(block))block.node.remove();
      // Insert only new blocks. Mounted disclosures, including their open state and
      // selected text, survive streamed and final replacements under the same key.
      for(let i=0;i<thinking.length;i++)if(thinking[i].node.parentNode!==node) {
        const next=thinking.slice(i+1).find(b=>b.node.parentNode===node)?.node || row.prose;
        if(node.insertBefore)node.insertBefore(thinking[i].node,next);else node.append(thinking[i].node);
      }
      row.thinking=thinking;
      const calls=[];
      for(const c of event.calls || []) {
        if(!ref(c.callRef) || !Number.isSafeInteger(c.block))throw new Error('invalid call witness');
        const key=JSON.stringify([state.session.sessionRef,state.scope.scopeRef,epoch,event.recordRef,c.block,c.id,c.callRef]);
        let call=row.calls.find(x=>x.id===c.id && x.block===c.block);
        if(!call){
          const button=element('button',undefined,'tool-bar');button.type='button';
          // How long it took (or its elapsed time while it runs), then its stored input and output sizes.
          const sizes=element('span',undefined,'tool-sizes');
          const time=element('span',undefined,'tool-time');
          sizes.append(time,element('span',undefined,'tool-size'),element('span',undefined,'tool-size'));
          button.append(element('span',undefined,'tool-title'),element('span',undefined,'tool-preview'),sizes);
          call={node:button,time};
        }
        const button=call.node;Object.assign(call,{key,id:c.id,block:c.block,callRef:c.callRef,name:c.name,status:c.status,
          startMs:Number.isFinite(c.time)&&c.time>0?c.time*1000:null,endMs:Number.isFinite(c.resultTime)?c.resultTime*1000:null});
        const status=callStatus(c.status);
        button.setAttribute('aria-label','Inspect '+c.name+' · '+status);
        text(button.children[0],`${c.name} · ${status}`);
        const previewSignature=JSON.stringify([c.name,c.previewData]);
        if(call.previewSignature!==previewSignature){text(button.children[1],opcToolPreview.preview(c.previewData,c.name,{descriptor:true}));call.previewSignature=previewSignature;}
        // Its stored total time now; markRunning replaces it with a ticking elapsed time while it runs.
        paintCallTime(call);
        for(const [i,name,value] of [[1,'In',c.inputBytes],[2,'Out',c.outputBytes]]) {
          const caption=button.children[2].children[i],known=count(value,name+' bytes');caption.hidden=hist; // the archive keeps a preview, no input or output
          text(caption,name+' stored: '+opcToolText.size(known));
          caption.title='KiB = 1024 UTF-8 bytes of retained raw Copy, not tokens or lost originals';opcToolText.emphasizeToolSize(caption,known);
          // The shared gradient stays; the top of the scale (100 KiB+, 200 KiB+) is lifted further here.
          const tier=known!=null&&known>=200*1024?'huge':known!=null&&known>=100*1024?'large':'';if((caption.dataset.tier||'')!==tier)caption.dataset.tier=tier;
        }
        button.classList.toggle('archived-call',hist);
        if(hist) {
          // 📦 An archived call is its one-line preview: nothing to inspect, the bar stays as it reads.
          button.onclick=null;button.setAttribute('aria-disabled','true');button.setAttribute('aria-label',c.name+' · '+status+' · archived preview (no Inspect)');
          button.title='Archived: a one-line preview of this call (no Inspect)';
        } else if(!oc)button.onclick=()=>inspect({key,callRef:c.callRef,fileEpoch:epoch});
        else if(typeof c.messageId==='string' && c.messageId && Number.isSafeInteger(c.partIndex) && c.partIndex>=0) {
          // The exact OpenCode part, frozen into the click before any await (backend, session, message, part, epoch).
          call.openCodeIdentity={sessionRef:state.session.sessionRef,scopeRef:state.scope.scopeRef,recordRef:event.recordRef,
            sessionId,messageId:c.messageId,partIndex:c.partIndex,partId:c.id,callRef:c.callRef};
          button.disabled=false;bindOpenCodeCall(call,epoch);
        } else {call.openCodeIdentity=null;button.onclick=null;button.disabled=true;button.setAttribute('aria-label',c.name+' · '+status+' · not inspectable: no complete part identity');}
        // A subagent call's child session opens as its own conversation, beside (never inside) the Inspect bar; an
        // archived call's childKey opens the archived child the same way, read-only.
        const child=oc && typeof c.childSessionId==='string' && /^ses_[A-Za-z0-9]{8,64}$/.test(c.childSessionId)?c.childSessionId:null;
        const childKey=hist && typeof c.childKey==='string' && HISTORY_KEY.test(c.childKey)?c.childKey:null,childName=child || childKey?.slice(childKey.indexOf(':')+1);
        if(childName) {
          if(!call.child){call.child=element('button',undefined,'oc-child-link');call.child.type='button';}
          text(call.child,'↳ Open child conversation · '+childName);call.child.setAttribute('aria-label','Open child conversation '+childName);
          call.child.title='Open the session this call started ('+childName+') as its own conversation here'+(childKey?', read-only from 📦 History':'')+'. It does not inspect this call.';
          call.child.onclick=event=>{event?.stopPropagation?.();void (childKey?openArchived(childKey):openOpenCode(child));};
        } else if(call.child){call.child.remove();call.child=null;}
        calls.push(call);
      }
      // Keep existing tools in place. New/removed tools are the only structural edits;
      // host partial/final reconciliation retains block order and call identity.
      for(const call of row.calls)if(!calls.includes(call)){call.node.remove();call.child?.remove();}
      const callNodes=calls.flatMap(call=>call.child?[call.node,call.child]:[call.node]);
      for(let i=0;i<callNodes.length;i++)if(callNodes[i].parentNode!==node) {
        const next=callNodes.slice(i+1).find(n=>n.parentNode===node) || row.orphan || null;
        if(node.insertBefore)node.insertBefore(callNodes[i],next);else node.append(callNodes[i]);
      }
      // An OpenCode shell MESSAGE is tool-styled (and kept by Tools only) but has no call to inspect.
      row.calls=calls;node.classList.toggle('has-tools',!!calls.length || (oc && event.kind==='shell'));node.classList.toggle('oc-shell',oc && event.kind==='shell');
      const orphans=listField(event.orphanResults,'orphanResults'),orphan=orphans.length?'Orphan tool results: '+orphans.join(', '):'';
      if(orphan){if(!row.orphan){row.orphan=element('p');node.append(row.orphan);}text(row.orphan,orphan);}
      else if(row.orphan){row.orphan.remove();row.orphan=null;}
      // Facts for the turn classifier; a record with nothing to read becomes a flag.
      // Native task observations (a background command finished, a task started) are
      // bookkeeping, not conversation: always a one-line flag.
      const empty=event.kind==='task' || (!result && event.partial!==true && typeof event.text==='string' && !event.text.trim() && !calls.length && !thinking.length && !orphans.length);
      row.facts={kind:event.kind,result,empty,partial:event.partial===true,calls:calls.length,thinking:thinking.length,
        text:!result && (typeof event.text!=='string' || !!event.text.trim()),toolResults:Number.isSafeInteger(event.toolResults)?event.toolResults:null,
        oc:oc?ocRowFacts(event,calls.length):null};
      if(empty)flag(row,event);else if(row.flag){row.flag.remove();row.flag=null;}
      row.renderedSignature=JSON.stringify(event);row.deferred=null;
    }
    // A message the 🔊 can read aloud: a stored record id to key its saved reading by (and cut the context at), and the
    // shared player loaded (read-aloud.js).
    function speakable(event) {return typeof event?.uuid==='string' && /^[A-Za-z0-9_.:-]{1,200}$/.test(event.uuid) && !!globalThis.opcReadAloud;}
    // @beacon[
    //   id=opc-pane@readAloudTurn,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The 🔊 beside a turn's copy button (and on a thinking block's header) opens read-aloud.js's player for that message: its identity (backend, conversation, record/row id, part), its own text and speaker, and the context read that cuts this conversation's prose at it (a Claude main path's targetRef, any OpenCode session); inside the editor's frame, the editor's own Refine settings.,
    // ]
    function readAloud(event,part='text',text=event?.text) {
      if(!event || !speakable(event) || typeof text!=='string' || !text.trim())return;
      const session=state.session,oc=isOpenCode(session),thinking=part!=='text';
      const badge=!oc && session?ROLE_BADGE[realRole(session)]:null;
      const label=(badge && !/^(History|Unknown host role)$/.test(badge)?badge:null) || (session?.label!=='Session'?session?.label:null) ||
        (oc?(session?.manager?'OpenCode Manager':session?.agent || 'OpenCode'):'agent');
      const contextQuery=oc?{backend:'opencode',id:session.id}:ref(state.target)?{backend:'claude',targetRef:state.target}:null;
      opcReadAloud.open({doc,backend:oc?'opencode':'claude',conversation:(oc?session?.id:session?.uuid) || (oc?'opencode':'claude'),
        message:event.uuid,part,text,
        speaker:event.kind==='user'?'the user':thinking?'the assistant ('+label+'), thinking to itself before it answered':'the assistant ('+label+')',
        title:[session?sessionName(session):'',event.kind==='user'?'You':thinking?label+' · thinking':label,fullTime(event.time)].filter(Boolean).join(' · '),
        contextQuery,contextNote:contextQuery?'':'no conversation context (this view is not a main conversation path)',
        settings:()=>{try{return parentEditor?.opcRefineSettings?.() || null;}catch(_){return null;}}});
    }
    // @beacon[
    //   id=opc-pane@messageCopyButtons,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Hover copy buttons: one beside each chat block's date copying that message's own Markdown source only, and one on each rendered code block copying its text; the 🔊 Read aloud button sits just before the message's copy button.,
    // ]
    function codeCopies(prose) {
      for(const pre of prose.querySelectorAll?.('pre') || []) {
        if(pre.parentNode?.classList?.contains('code-wrap'))continue;
        const wrap=element('div',undefined,'code-wrap'),button=element('span','⧉','code-copy');
        button.title='Copy this code block';button.setAttribute('aria-label','Copy code block');
        pressable(button,()=>copyText(button,pre.textContent));
        pre.replaceWith(wrap);wrap.append(pre,button);
      }
    }
    function makeRow(event,epoch) {
      const row={node:element('article',undefined,'history-event'),label:element('div',undefined,'event-label'),prose:element('div',undefined,'event-prose'),thinking:[],calls:[],size:0};
      row.role=element('span',undefined,'event-role');row.meta=element('span',undefined,'event-meta');row.model=element('span',undefined,'event-model');
      row.copy=element('span','⧉','event-copy');row.copy.title='Copy this message (Markdown source; no thinking or tools)';row.copy.setAttribute('aria-label','Copy message');
      pressable(row.copy,()=>{if(row.copySource!==null)copyText(row.copy,row.copySource);});
      row.speak=element('span','🔊','event-speak');row.speak.title='Read this message aloud: rewritten for listening, then spoken (a second click replays the saved reading)';
      row.speak.setAttribute('aria-label','Read message aloud');pressable(row.speak,()=>readAloud(row.speakEvent,'text',row.copySource));
      row.label.append(row.role,row.model,row.speak,row.copy,row.meta);
      row.node.append(row.label,row.prose);updateRow(row,event,epoch);return row;
    }
    // OpenCode display facts for classify: what an unusual row is (labelled, never hidden), and the quiet
    // one-line marker of a block with nothing readable (empty, encrypted-only or still-loading reasoning; a
    // started step with no part yet), which a later readable body replaces in place under the same key.
    function ocRowFacts(event,calls) {
      const blocks=listField(event.thinking,'thinking'),readable=b=>b?.availability==='stored' && typeof b.text==='string' && b.text.length>0;
      const prose=typeof event.text==='string' && !!event.text.trim();
      let quiet=null;
      if(event.kind==='assistant' && !calls && blocks.length && !prose && !blocks.some(readable))
        quiet=blocks.some(b=>b?.availability==='redacted') || event.originKind==='encrypted-reasoning'?'Thinking · encrypted; no readable text':
          blocks.some(b=>b?.streaming===true)?'Thinking · loading':blocks.some(b=>b?.availability==='omitted')?'Thinking · omitted':
          blocks.some(b=>b?.availability==='body-limit')?'Thinking · too large to show (body-limit)':'Thinking · empty';
      else if(event.kind==='assistant' && event.originKind==='assistant-empty' && !calls && !blocks.length)quiet='Step started · no content recorded'+(event.partial===true?' yet':'');
      const known={synthetic:'synthetic message',system:'system message',skill:'skill',shell:'shell'};
      const what=event.status==='response-limit'?String(event.kind)+' · too large to show here':
        Object.hasOwn(known,event.kind)?known[event.kind]:
        event.kind==='assistant' && event.originKind==='future-part'?'unknown part · '+event.originKind:
        event.kind==='assistant' && event.originKind==='tool'?'tool · not inspectable (no unique part id)':
        event.kind==='assistant' && event.isMeta===true?String(event.originKind || 'recorded detail'):
        !['user','assistant','result','task'].includes(event.kind)?'unknown message type · '+String(event.kind):null;
      return {quiet,what,origin:typeof event.originKind==='string'?event.originKind:null};
    }
    // A record with no conversation content: a one-line flag whose disclosure holds its metadata.
    // An OpenCode task flag (compaction, a switch, an assistant error/retry state) names its original type.
    function flag(row,event) {
      if(!row.flag){const summary=element('summary');summary.append(element('span','⚑','flag-mark'),element('span'));row.flag=element('details',undefined,'event-flag');row.flag.append(summary,element('dl'));row.node.append(row.flag);}
      const oc=isOpenCode(state.session),taskLine=key=>String(event.text || '').split('\n').find(l=>l.startsWith(key+': '))?.slice(key.length+2);
      const kind=oc && event.kind==='task'?(taskLine('description') || 'OpenCode · '+(event.originKind || 'task')):
        oc && event.kind==='user'?'user message · no text recorded':
        event.kind==='task'?'task · '+(taskLine('description') || taskLine('summary') || taskLine('task_id') || 'native task')+(taskLine('status')?' · '+taskLine('status'):''):
        event.kind==='user'?(event.isMeta===true?'meta message':typeof event.originKind==='string'&&event.originKind?event.originKind.replace(/[-_]/g,' '):
        event.toolResults>0?'tool result record':'user record without prose'):String(event.kind)+(event.status&&!['metadata','stored'].includes(event.status)?' · '+event.status:'');
      text(row.flag.children[0].children[1],(event.kind==='task'?'':'event · ')+kind+(fullTime(event.time)?' · '+fullTime(event.time):''));
      const list=doc.createDocumentFragment();
      for(const [name,value] of [['kind',event.kind],['status',event.status],['time',fullTime(event.time)],['uuid',event.uuid],['scope',event.scope],['tool results',oc?null:event.toolResults],
        ['detail',event.kind==='task'?event.text:null],['model',oc?event.model:null],['agent',oc?event.agent:null],
        ['meta',event.isMeta],['origin',event.originKind],['text bytes',event.textBytes],['replays',event.replays],['provenance',event.provenance]]) {
        if(value===undefined || value===null || value==='' || value===false || (name==='replays'&&value===0))continue;
        list.append(element('dt',name),element('dd',String(value)));
      }
      row.flag.children[1].replaceChildren(list);
    }
    const TURNS=['turn-user','turn-final','turn-mid','turn-tools','turn-thinking'];
    // ------------------------------------------------------------- subagent activity
    // One color per subagent, by its index among the Manager's subagents (cycling).
    const PALETTE=['#e6a23c','#4fc3f7','#ce93d8','#81c784','#ff8a65','#f48fb1','#c5e1a5','#9fa8da','#4db6ac','#fff176'];
    const RUNNING=['pending-or-missing','not-final-fragment','streaming'];
    // Host activity states that evidence work actually in flight. Absent/unknown activity is not a
    // running claim: only a recorded working state is (idle never is).
    const WORKING_ACTIVITY=new Set(['thinking','writing','tool','retrying','permission']);
    // An OpenCode busy fact older than this is no longer evidence of anything running (a visible page
    // reads it every second while busy, and a failed read names itself in observationError).
    const BUSY_FACT_MS=15000;
    function agentInfo(agent) {return state.agents.get(agent) || null;}
    function agentColor(agent) {const i=agentInfo(agent)?.index;const n=Number.isSafeInteger(i)?i:[...state.agents.keys()].indexOf(agent);return PALETTE[((n%PALETTE.length)+PALETTE.length)%PALETTE.length];}
    function agentName(agent) {const info=agentInfo(agent);return info?.description || info?.subagentType || 'subagent';}
    function turnRow(row) {return !!row && TURNS.some(name=>row.node.classList.contains(name));}
    // @beacon[
    //   id=opc-pane@ingestSubagentActivity,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Keeps conversation-only subagentActivity entries and per-agent labels (description, color index, running state) keyed for in-place updates; bounded and reset with selection, separate from the sidebar activity map.,
    // ]
    function ingestActivity(data) {
      for(const a of listField(data.subagents,'subagents'))if(a && typeof a.agent==='string')state.agents.set(a.agent,a);
      if(!Array.isArray(data.subagentActivity))return;
      for(const e of data.subagentActivity)if(e && typeof e.key==='string' && typeof e.agent==='string' && Number.isSafeInteger(e.ordinal))state.subagentActivity.set(e.key,e);
      if(state.subagentActivity.size>4000)for(const e of [...state.subagentActivity.values()].sort((a,b)=>a.ordinal-b.ordinal).slice(0,state.subagentActivity.size-4000))state.subagentActivity.delete(e.key);
    }
    function fillEntry(item,e,info) {
      const [time,name,preview]=item.node.children;text(time,clock(e.time));
      const flat=value=>String(value || '').replace(/\s+/g,' ').trim().slice(0,240);
      let running=false;
      if(e.type==='call') {
        let shown='';try{shown=opcToolPreview.preview(e.previewData,e.name,{descriptor:true});}catch(_){shown='';}
        text(name,e.name);text(preview,flat(shown));
        running=RUNNING.includes(e.status) && info?.state==='running';
        item.node.title=e.name+' · '+(running?'running now':callStatus(e.status))+(clock(e.time)?' · '+clock(e.time):'')+' · click to open it in the tool inspector';
        if(ref(e.callRef))pressable(item.node,()=>inspect({key:'activity:'+e.key,callRef:e.callRef,fileEpoch:state.epoch}));
      } else if(e.type==='text') {
        text(name,'wrote');text(preview,flat(e.text));item.node.title='Subagent text (shortened)';
      } else {
        const agentTask=e.taskType==='local_agent';
        text(name,agentTask?(e.status==='task_started'?'started':(e.taskStatus || 'finished')):'background '+(e.taskStatus==='failed'?'✗':e.taskStatus?'✓':'…'));
        text(preview,flat(agentTask && e.status==='task_started'?e.description:(e.summary || e.description)));
        item.node.title=agentTask?'The subagent '+(e.status==='task_started'?'started':'finished'):'A background command the subagent started (run_in_background)';
      }
      item.node.classList.toggle('running',running);item.node.classList.toggle('error',e.status==='error' || e.taskStatus==='failed');
    }
    function groupKey(after,agent) {return (state.session?.uuid || state.session?.sessionRef || '')+'|'+after+'|'+agent;}
    // Expanded by default; a group Dan folds (or reopens) by hand keeps that choice across reloads.
    function rememberGroup(key,closed) {
      state.groupClosed.delete(key);if(closed)state.groupClosed.add(key);
      const kept=[...state.groupClosed].slice(-GROUPS_CLOSED_MAX);state.groupClosed=new Set(kept);storeList(GROUPS_CLOSED,kept);
    }
    function renderGroup(run,after,agent,list) {
      const key=groupKey(after,agent);let g=run.groups.get(agent);
      if(!g) {
        const node=element('details',undefined,'subagent-group'),summary=element('summary'),body=element('div',undefined,'sg-entries');
        summary.append(element('span','⚑','sg-mark'),element('span',undefined,'sg-name'),element('span',undefined,'sg-count'),element('span',undefined,'sg-time'));
        node.append(summary,body);node.open=!state.activityHidden && !state.groupClosed.has(key);
        // Only Dan's own click on the summary records a choice; programmatic opens/folds never do.
        summary.addEventListener?.('click',()=>{if(!state.activityHidden)setTimeout(()=>rememberGroup(key,!node.open),0);});
        g={node,summary,body,entries:new Map()};run.groups.set(agent,g);
      }
      const info=agentInfo(agent),calls=list.filter(e=>e.type==='call').length,first=fullTime(list[0].time);
      const sameDay=new Date(list[0].time*1000).toDateString()===new Date(list.at(-1).time*1000).toDateString(),last=fullTime(list.at(-1).time,{timeOnly:sameDay});
      g.node.style.setProperty('--agent-color',agentColor(agent));
      text(g.summary.children[1],agentName(agent));
      text(g.summary.children[2],calls+' call'+(calls===1?'':'s')+(list.length>calls?' · '+(list.length-calls)+' other':''));
      text(g.summary.children[3],first+(last && !first.endsWith(last)?' – '+last:''));
      g.node.title=(info?.subagentType || 'subagent')+' · '+agentName(agent)+' · '+(info?.state==='running'?'running':info?.state || 'state unknown')+' · subagent activity, display only';
      g.node.classList.toggle('running',info?.state==='running');
      const nodes=[];
      for(const e of list) {
        let item=g.entries.get(e.key);
        if(!item){item={node:element('div',undefined,'sg-entry')};item.node.append(element('span',undefined,'sg-etime'),element('span',undefined,'sg-ename'),element('span',undefined,'sg-epreview'));g.entries.set(e.key,item);}
        const signature=JSON.stringify(e)+'|'+(info?.state || '');if(item.signature!==signature){item.signature=signature;fillEntry(item,e,info);}
        nodes.push(item.node);
      }
      for(const [k,item] of g.entries)if(!nodes.includes(item.node))g.entries.delete(k);
      if(g.body.children.length!==nodes.length || nodes.some((n,i)=>g.body.children[i]!==n))g.body.replaceChildren(...nodes);
      return g.node;
    }
    // @beacon[
    //   id=opc-pane@renderSubagentActivity,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Renders the conversation-only subagentActivity store between primary turns: tiny colored groups, expanded by default or header-only when hidden, after the turn and folded records; entries Inspect their call and nodes move only when misplaced.,
    // ]
    function renderActivity() {
      const host=$('history-events');if(!host)return;
      const runs=new Map();
      for(const e of state.subagentActivity.values()){const k=e.after ?? '';if(!runs.has(k))runs.set(k,[]);runs.get(k).push(e);}
      const keep=new Set();
      for(const [after,entries] of runs) {
        let refNode=null;
        if(after) {
          const at=state.order.indexOf(after);if(at<0)continue;
          let j=at+1;while(j<state.order.length && !turnRow(state.rows.get(state.order[j])))j++;
          refNode=j<state.order.length?state.rows.get(state.order[j]).node:null;
        } else {
          if(!state.order.length || !atStart())continue; // the session's start is not resident
          refNode=state.rows.get(state.order[0]).node;
        }
        let run=state.runs.get(after);
        if(!run){run={after,node:element('div',undefined,'subagent-run'),groups:new Map()};state.runs.set(after,run);}
        keep.add(after);
        const byAgent=new Map();
        for(const e of entries.sort((a,b)=>a.ordinal-b.ordinal)){if(!byAgent.has(e.agent))byAgent.set(e.agent,[]);byAgent.get(e.agent).push(e);}
        const nodes=[...byAgent].map(([agent,list])=>renderGroup(run,after,agent,list));
        for(const agent of [...run.groups.keys()])if(!byAgent.has(agent))run.groups.delete(agent);
        if(run.node.children.length!==nodes.length || nodes.some((n,i)=>run.node.children[i]!==n))run.node.replaceChildren(...nodes);
        const kids=host.children,at=Array.prototype.indexOf.call(kids,run.node);
        const placed=at>=0 && (refNode?kids[at+1]===refNode:at===kids.length-1);
        if(!placed){if(refNode && host.insertBefore)host.insertBefore(run.node,refNode);else host.append(run.node);}
      }
      for(const [after,run] of state.runs)if(!keep.has(after)){run.node.remove();state.runs.delete(after);}
    }
    function setActivityHidden(hidden) {
      // The chip folds every group for this page view only: a reload starts expanded again.
      state.activityHidden=!!hidden;
      const saved=anchor();
      for(const run of state.runs.values())for(const [agent,g] of run.groups)g.node.open=!state.activityHidden && !state.groupClosed.has(groupKey(run.after,agent));
      if(state.follow)stickToLatest();else restoreAnchor(saved);ribbon();
    }
    // Agent IDs running under this explicit parent, from liveOf's newest native
    // background-task registry only (local_agent or the older untyped task shape).
    function runningAgents(parentRow) {
      if(hostActivityFailure(parentRow))return new Set();
      const ids=new Set(),tasks=liveOf(parentRow).background_tasks;
      if(tasks && typeof tasks==='object' && !tasks.status)for(const [id,t] of Object.entries(tasks))if(!t || t.task_type==='local_agent' || t.task_type===undefined)ids.add(id);
      return ids;
    }

    // @beacon[
    //   id=opc-pane@classifyClaudeTurns,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Display-only turn roles over the resident window: your messages, final answers (assistant prose with no tool call before the next user turn), mid-round prose, tool rows; tool-result carrier records fold away, other empty records become flags. OpenCode blocks read "OpenCode · tools / answer / thinking", unusual rows keep a labelled readable line (synthetic, system, skill, shell, unknown type or part) and empty or encrypted reasoning is one quiet dim line.,
    // ]
    function classify() {
      const child=role(state.session || {})==='subagent',who=isOpenCode(state.session) || state.session?.archivedBackend==='opencode'?'OpenCode':'Claude';let previousTools=false;
      for(const key of state.order){const row=state.rows.get(key),f=row?.facts;if(!f)continue;
        // Claude Code stores every tool result as a user record already shown under its call.
        if(f.empty)row.silent=f.kind==='user' && (f.toolResults!==null ? f.toolResults>0 : previousTools);
        else {row.silent=false;previousTools=f.calls>0;}
      }
      let toolsAhead=false;
      for(let i=state.order.length-1;i>=0;i--) {
        const row=state.rows.get(state.order[i]),f=row?.facts;if(!f)continue;
        let turn=null,label=String(f.kind);
        if(f.result)toolsAhead=false;
        else if(f.empty)label='';
        else if(f.kind==='user'){if(f.text){turn='turn-user';label=child?'Prompt':'You';toolsAhead=false;}}
        else if(f.oc?.quiet)label=f.oc.quiet;
        else if(f.oc?.what){label=who+' · '+f.oc.what;if(f.kind==='shell')turn='turn-tools';}
        else if(f.kind==='assistant') {
          if(f.calls){turn='turn-tools';label=who+' · tools';toolsAhead=true;}
          else if(f.text){const final=!toolsAhead && !f.partial;turn=final?'turn-final':'turn-mid';label=final?who+' · answer':who;}
          else if(f.thinking){turn='turn-thinking';label=who+' · thinking';}
          else label=who;
        } else if(f.kind==='task')label='Task observation';
        for(const name of TURNS)row.node.classList.toggle(name,name===turn);
        row.node.classList.toggle('event-quiet',!f.empty && !!f.oc?.quiet);
        row.node.classList.toggle('event-silent',f.empty && row.silent);row.node.classList.toggle('event-flagged',f.empty && !row.silent);
        if(row.role)text(row.role,label);
        // Claude blocks only (answer, tools, thinking, prose): the model/level recorded on that message.
        if(row.model)text(row.model,f.kind==='assistant' && label && row.turnModel?row.turnModel.text:'');
      }
      markRunning(child);
    }
    // @beacon[
    //   id=opc-pane@paintCallTime,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=One conversation call's time cell in place: the stored total (callDuration, from the call's stored time and its result's stored resultTime) once finished, else a whole-second elapsed time ("12s so far", elapsedText) while it is marked running; an unknown start, a clock reading before it or a missing stored end stays blank, never a guessed number. Text and title only, so a once-a-second tick never rebuilds a row, moves scroll or reads anything.,
    // ]
    function paintCallTime(call, now=Date.now()) {
      const node=call.time;if(!node)return;
      const start=Number.isFinite(call.startMs)?call.startMs:null,running=call.running===true;
      const took=running?(start!==null?globalThis.opcToolReading?.elapsedText?.(start,now) || '':''):
        start!==null&&Number.isFinite(call.endMs)?globalThis.opcToolReading?.callDuration?.(start,call.endMs) || '':'';
      // "so far" keeps a live number from reading as a finished duration.
      text(node,running&&took?took+' so far':took);node.hidden=!took;
      node.title=running?(took?'Running for '+took+': from the call to now':start===null?'Running now: its start time is not recorded':'Running now: its elapsed time is unavailable'):
        took?'Took '+took+': from the call to its stored result':'';
      node.dataset.running=String(running);
    }
    // @beacon[
    //   id=opc-pane@tickCallTimers,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Once-a-second running-tool strip and elapsed cells: recheck evidence freshness on every tick (even without a page/classify update), then repaint only classes and time text in place, never bodies, scrolling or requests.,
    // ]
    function tickCallTimers(now=Date.now()) {
      const current=isOpenCode(state.session)?ocEvidence(state.session):null;
      runningStrip?.update(current?.calls,current?.busy,current?.at,current?.error,now);
      markRunning(role(state.session || {})==='subagent',now);
    }
    // @beacon[
    //   id=opc-pane@markRunningCalls,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Yellow conversation call plus its ticking elapsed time, from evidence only: a live Manager's result-less call in its still-open turn (no end-of-turn receipt after it; the host must record a working activity state — unknown, idle or an offline read never claims one), or a subagent's result-less call while its watched parent's host task registry says it runs. An OpenCode call is yellow only while its page says busy (a fresh fact, not a failed read) and it is pending or streaming after the last end-of-turn receipt; idle, unknown and stale busy never mark one, and a background subagent's own status is not consulted.,
    // ]
    function markRunning(child,now=Date.now()) {
      const session=state.session || {},host=session.source==='host' && role(session)==='manager' && !ended(session);
      let busy=false;
      if(isOpenCode(session)) {
        const at=state.ocObservedAt,age=Number.isFinite(at)?now-at:Infinity;
        busy=state.ocBusyFact===true && !state.observationError && age<=BUSY_FACT_MS;
      } else if(host) busy=!state.observationError && WORKING_ACTIVITY.has(state.liveState?.activity?.state);
      const childRunning=child && typeof session.agentId==='string' && runningAgents(state.childParent).has(session.agentId);
      let open=true;
      for(let i=state.order.length-1;i>=0;i--) {
        const row=state.rows.get(state.order[i]);if(!row?.facts)continue;
        if(row.facts.result)open=false;
        for(const call of row.calls) {
          const running=RUNNING.includes(call.status) && (child?childRunning:busy && open);
          call.running=running;call.node.classList.toggle('running',running);
          const title=call.node.children?.[0];
          if(title)text(title,call.name+' · '+(running && call.status==='pending-or-missing'?'running now':callStatus(call.status)));
           paintCallTime(call,now);
        }
      }
    }
    // @beacon[
    //   id=opc-pane@trimHistoryWindow,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Keeps the resident window one contiguous run: over its bound rows leave from an END only, never from the middle (which kept the reading anchor as an island above a silent gap). Growing at the bottom the oldest go (paused: only those above the reading position), else the newest go and the window is behind; growing at the top the newest go.,
    // ]
    function trim(saved,grew) {
      const over=()=>state.order.length>WINDOW_EVENTS || state.used>WINDOW_BYTES;
      // Following: the reader is at the bottom, so any row above may go (a text selection pauses
      // following). Paused: only rows above the reading position go from the top; the newest rows
      // may go from the bottom (the window is then "behind" and they are read again on demand).
      const free=side=>{
        const key=side==='head'?state.order[0]:state.order.at(-1),row=state.rows.get(key);
        if(!row)return false;
        if(state.follow)return side==='head'?!selected(row.node):grew==='older' && !protectedRow(row);
        return key!==saved?.key && !protectedRow(row);
      };
      while(over()) {
        const sides=grew==='older'?['tail','head']:['head','tail'];let side=sides.find(free);
        if(!side && state.follow && state.order.length && selected(state.rows.get(state.order[0]).node)) {
          // A text selection the window would slide past is kept: following pauses (as selecting
          // does) and the newest rows wait below instead.
          state.follow=false;state.followPaused=true;state.selectionPause=true;side=sides.find(free);
        }
        if(!side)throw new Error('history view limit while text/focus is held; release selection then reload, last view retained');
        const key=side==='head'?state.order[0]:state.order.at(-1);
        if(side==='tail'){state.tailEvicted=true;state.dropped=true;}
        const row=state.rows.get(key);state.order.splice(state.order.indexOf(key),1);state.used-=row.size;row.node.remove();state.rows.delete(key);
        if(row.identity)state.identities.delete(row.identity);
      }
    }
    // @beacon[
    //   id=opc-pane@flushHistoryRepaints,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Release deferred live row text only after browser selection ends; preserve mounted prose/thinking/tool nodes, focus and viewport while keeping inspector snapshots independent.,
    // ]
    function flushRepaints() {
      const saved=anchor(),changed=[];
      for(const row of state.rows.values())if(row.deferred && !selected(row.node) && !removesFocusedTool(row,row.deferred.event)) {
        const {event,epoch}=row.deferred;updateRow(row,event,epoch);state.used-=row.size;row.size=2*bytes(row.signature);state.used+=row.size;changed.push(row);
      }
      // A row held for Dan's text selection is the one he was reading: it stays seen.
      if(changed.length){classify();if(state.follow)stickToLatest();else restoreAnchor(saved);trackRows(changed,false);mark();controls();}
    }
    // @beacon[
    //   id=opc-pane@placeNewRows,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Where new rows go: with the server's positions (seq) a row is mounted only where it extends the resident run at either end; a row further away is not mounted and marks the window behind (read again later), so no silent gap can form. Rows without positions (an older server) keep the plain append/prepend order.,
    // ]
    function placeNew(fresh,mode) {
      const seqs=state.order.map(k=>state.rows.get(k)?.seq);
      if(!fresh.length || !fresh.every(f=>Number.isSafeInteger(f.event.seq)) || !seqs.every(Number.isSafeInteger))
        return mode==='older'?{head:fresh,tail:[]}:{head:[],tail:fresh};
      const ascending=[...fresh].sort((a,b)=>a.event.seq-b.event.seq),head=[],tail=[];
      let lo=seqs.length?seqs[0]:ascending[0].event.seq,hi=seqs.length?seqs.at(-1):lo-1;
      for(const f of ascending)if(f.event.seq===hi+1){tail.push(f);hi++;}
      for(const f of [...ascending].reverse())if(f.event.seq===lo-1){head.unshift(f);lo--;}
      // Rows past the newest resident one are read again once the window reaches them.
      if(ascending.some(f=>f.event.seq>hi))state.dropped=true;
      return {head,tail};
    }
    // @beacon[
    //   id=opc-pane@reconcileHistory,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Reconcile record and UUID-scope keys (native tasks also generation-scoped) without duplicate mounts: refresh resident OpenCode Inspect bindings and deferred epochs even for unchanged JSON without repainting bodies; selected partial-final text stays deferred, new rows extend the contiguous run, then trim from an end; preserve the pixel anchor and never transfer an old-epoch highlight. Live-tail rows (a delta read while paused) go to the seen tracker as possibly new; rows filled in above never are.,
    // ]
    function reconcile(events, mode, epoch, checkpoint=null, from=null) {
      const saved=anchor(), fresh=[], repainted=[];let updated=0;
      for(const event of events) {
        if(!event || !ref(event.recordRef) || !Array.isArray(event.calls || []))throw new Error('invalid event identity schema');
        const calls=event.calls || [];
        if(calls.some((c,i)=>!c || !ref(c.callRef) || !Number.isSafeInteger(c.block) || c.block<0 || typeof c.id!=='string' ||
          calls.findIndex(x=>x?.id===c.id&&x?.block===c.block)!==i))throw new Error('invalid call witness identity schema');
        validateThinking(event); // refuse before accounting or deferring a new body
        const old=resident(event), identity=eventIdentity(event) || old?.identity || null, key=old?.key || event.recordRef, signature=JSON.stringify(event);
        if(identity && state.identities.has(identity) && state.identities.get(identity)!==key)throw Object.assign(new Error('conflicting event identity; reload/reselect'),{code:409});
        if(old && isOpenCode(state.session)) {
          // An index-local epoch can change while the deterministic row JSON stays identical.
          // Do not migrate accepted/pending/failed keys: only a fresh click accepts the new witness.
          for(const call of old.calls)if(call.openCodeIdentity)bindOpenCodeCall(call,epoch);
          if(old.deferred)old.deferred.epoch=epoch;
        }
        if(old?.signature===signature)continue; // unchanged mounted prose/selection never repainted
        const held=old && (selected(old.node)||removesFocusedTool(old,event)),size=2*bytes(signature)+(held?2*bytes(old.renderedSignature):0);
        const protectedBytes=held?[...state.rows.values()].reduce((sum,r)=>sum+(r!==old&&protectedRow(r)?r.size:0),0):0;
        if(size+protectedBytes>WINDOW_BYTES)throw new Error('history view limit while text/focus is held; release selection then reload, last view retained');
        if(!old) { // a later copy in the same page updates the earlier one, as for a resident row
          const twin=fresh.find(f=>f.key===key || (identity && f.identity===identity));
          if(twin)Object.assign(twin,{event,signature,size,identity:identity || twin.identity});else fresh.push({event,key,identity,signature,size});
          continue;
        }
        state.used-=old.size;if(held)old.deferred={event,epoch};else{updateRow(old,event,epoch);updated++;repainted.push(old);}
        // A host final can supply a native UUID where its streaming row previously
        // carried a synthetic one. The unchanged record witness owns that swap.
        if(old.identity && old.identity!==identity)state.identities.delete(old.identity);
        old.signature=signature;old.size=size;old.key=key;old.identity=identity;
        if(Number.isSafeInteger(event.seq))old.seq=event.seq;
        if(checkpoint && !old.page)old.page=checkpoint;
        if(identity)state.identities.set(identity,key);
        state.used+=old.size;
      }
      const {head,tail}=placeNew(fresh,mode),mounted=[...head,...tail];
      // For the seen tracker: rows filled in above are never new; rows extending the tail may be.
      const touched=[...repainted],above=[];
      for(const f of mounted) {
        const row=makeRow(f.event,epoch);
        Object.assign(row,{signature:f.signature,size:f.size,key:f.key,identity:f.identity,seq:Number.isSafeInteger(f.event.seq)?f.event.seq:null,from,time:f.event.time});
        if(checkpoint)row.page=checkpoint; // the page this row is read from (older-page lookup)
        if(f.identity)state.identities.set(f.identity,f.key);
        state.rows.set(f.key,row);state.used+=row.size;(head.includes(f)?above:touched).push(row);
      }
      state.order=[...head.map(f=>f.key),...state.order,...tail.map(f=>f.key)];
      const host=$('history-events');
      for(const f of [...head].reverse())host.prepend(state.rows.get(f.key).node);
      for(const f of tail)host.append(state.rows.get(f.key).node);
      trim(saved,head.length && !tail.length?'older':'newer');classify();renderActivity();
      const added=mounted.filter(f=>state.rows.has(f.key)).length;
      if(mode==='delta' && !state.follow)state.newCount+=tail.filter(f=>state.rows.has(f.key)).length;
      // Follow on: every page pins the newest content into view, including pages that only
      // grew a subagent group or reclassified a row (no added/updated record); an explicit
      // text selection still holds it. Follow off: the reading position never moves.
      // Following implies no live selection (selecting pauses it), so nothing else holds it;
      // an older page filled in above a following view keeps it on the newest pixel too.
      if(state.follow ||(mode==='latest' && !selected(host) && !contains(host,doc.activeElement)))stickToLatest();else restoreAnchor(saved);
      trackRows(above,false);
      const arrived=trackRows(touched,!state.follow && (mode==='delta' || (mode==='summary' && state.loadingMode==='delta')));
      mark(); controls();
      const badge=$('history-latest-badge');if(arrived && badge?.dataset.unseen)opcSeenLatest.pulseBadge(badge);
      return added;
    }
    function summaryUpdates(updates) {
      const events=[];
      for(const update of listField(updates,'summaryUpdates')) {
        if(!update || typeof update!=='object'){warn('summary update');continue;}
        const row=resident(update);
        if(Array.isArray(update.calls)){if(row)events.push(update);continue;} // host full-event projection
        const call=row?.calls.find(c=>c.id===update.id&&c.block===update.block);
        if(!call)continue; // no off-viewport body/row cache
        callStatus(update.status);
        const event=events.find(x=>x.recordRef===update.recordRef) || JSON.parse(row.signature),entry=event.calls.find(c=>c.id===update.id&&c.block===update.block);
        entry.status=update.status;entry.outputBytes=count(update.outputBytes,'outputBytes');
        for(const name of ['previewData','inputBytes','time','resultTime'])if(Object.hasOwn(update,name))entry[name]=update[name];
        if(!events.includes(event))events.push(event);
      }
      if(events.length)reconcile(events,'summary',state.epoch);
    }
    // Reads one history page; answers the number of rows it added, or null when another read is
    // still in flight (the caller may wait and ask again).
    async function page(cursor=null,mode='forward',bookmark=null) {
      if(!state.session || !state.scope || state.blocked)return 0;
      if(state.loading)return null;
      const generation=state.generation,readStartedAt=Date.now(),resyncing=mode==='resync';state.loading=true;state.loadingMode=mode;state.abort=new AbortController();controls();
      let added=0;state.readError=null;
      // A read slow to answer says so quietly ("Catching up"); the rows on screen stay and it clears itself.
      const slow=setTimeout(()=>{if(generation===state.generation && state.loading && !state.catchingUp){state.catchingUp={since:readStartedAt};edge();ribbon();}},SLOW_READ_MS);
      try {
        // OpenCode and 📦 History conversations have one linear scope and are never a target: no leaf, branches or child activity.
        const oc=isOpenCode(state.session),hist=archived(state.session),flat=oc || hist;
        const data=await json(address(cursor),{signal:state.abort.signal});if(generation!==state.generation)return 0;envelope(data,hist?'history':oc?'opencode':'claude');
        if(data.sessionRef!==state.session.sessionRef || data.scopeRef!==state.scope.scopeRef || !ref(data.fileEpoch) || (oc && data.source!=='opencode') || (hist && data.source!=='history') ||
          (state.epoch && data.fileEpoch!==state.epoch) || !Array.isArray(data.events) || data.events.length>100 || (data.hasMore&&!ref(data.nextCursor)))throw Object.assign(new Error('history identity/FileEpoch changed; reload/reselect'),{code:409,status:'stale-epoch'});
        if(resyncing) {
          // The restarted server rebuilds its index from the start: wait (each read drains more of
          // it) until it stands, so no row on screen is compared with a half-built copy.
          if(['indexing','validating'].includes(data.status))throw Object.assign(new Error('the pane server is still loading this conversation'),{resyncWait:true});
          adopt(data.events,cursor);mode='forward';
        }
        state.epoch=data.fileEpoch;state.leaf=flat?null:data.leafRef;state.target=!flat && state.scope.label==='main'?data.targetRef:null;
        // This read's own damage witness (true only when the host's rows answer said unreadable): an ordinary page
        // or an old host clears it, a failed read never touches it, and a conversation switch drops it (reset).
        state.rowsUnreadable=data.rowsUnreadable===true;
        state.observationError=null;disclosure(data);
        const branches=doc.createDocumentFragment(),choose=element('option','Choose an explicit main leaf','');choose.value='';branches.append(choose);
        for(const b of flat?[]:listField(data.branches,'branches')) {if(!b || !ref(b.leafRef)){warn('branch label');continue;}const opt=element('option',b.uuid);opt.value=b.leafRef;branches.append(opt);}
        $('history-leaf').replaceChildren(branches);$('history-leaf').value=flat || data.status==='ambiguous'?'':data.leafRef;
        $('history-leaf').disabled=flat || state.scope.label!=='main';
        if(!flat)ingestActivity(data);
        const checkpoint={cursor,next:data.hasMore?data.nextCursor:null,epoch:data.fileEpoch};
        // Rows remember the page they came from, so "older" can always find the page before them.
        const tag=mode==='delta'?null:bookmark!==null?(state.pages[bookmark] || checkpoint):checkpoint;
        state.dropped=false;
        added=reconcile(data.events,mode,data.fileEpoch,tag,cursor);
        if(mode!=='delta' && mode!=='older' && !data.hasMore && !state.dropped)state.tailEvicted=false; // the newest page is resident again
        state.indexLimit=data.indexLimit && typeof data.indexLimit==='object'?data.indexLimit:null;
        if(!cursor && mode!=='delta') {
          // The session's very first record (or none at all yet): atStart() compares against it.
          const first=data.events.length?resident(data.events[0]):null;
          state.startKey=first && data.events[0].seq===0?first.key:undefined;
        }
        if(data.eventUpdates!==undefined) {
          // This field carries replacement bodies, not cosmetic metadata. Never
          // advance the cursor after ignoring a malformed replacement envelope.
          if(!Array.isArray(data.eventUpdates) || data.eventUpdates.length>100 ||
            data.eventUpdates.some(event=>!event || !ref(event.recordRef)))throw new Error('invalid live event update schema');
          reconcile(data.eventUpdates.filter(event=>resident(event)),'summary',data.fileEpoch);
        }
        summaryUpdates(data.summaryUpdates);
        if(mode!=='delta') {
          if(bookmark!==null)state.pageAt=bookmark;else {state.pages.splice(state.pageAt+1);state.pages.push(checkpoint);state.pageAt=state.pages.length-1;}
          if(state.pages.length>BOOKMARKS){state.pages.shift();state.pageAt--;}
        }
        if(data.indexingContinuation!=null && (state.session.source!=='host' || !ref(data.indexingContinuation) ||
          data.status!=='indexing' || !data.hasMore || data.indexingContinuation!==data.nextCursor))throw new Error('invalid indexing continuation schema');
        if(state.dropped) {
          // Rows past the newest resident one were not mounted (or just left the window): the page
          // that delivered the newest resident row, read again, continues exactly after it. Until
          // then the window is behind: Following catches up, Paused shows "newer below".
          const resume=state.rows.get(state.order.at(-1))?.from;
          if(resume)state.next=resume;else if(mode!=='older')state.next=cursor || checkpoint.next;
          state.rowsAhead=true; // Known rows beyond the resident end, even if none remain beyond this page.
          state.indexingContinuation=null;
        } else if(mode!=='older') {
          // Earlier pages leave the forward cursors as they were: they still continue the newest row.
          state.next=checkpoint.next;state.rowsAhead=typeof data.rowsAhead==='boolean'?data.rowsAhead:null;
          state.delta=data.deltaCursor;state.indexingContinuation=data.indexingContinuation ?? null;
        }
        state.failures=0;state.nextPoll=state.indexingContinuation?0:readStartedAt+pollDelay();
        if(state.catchingUp){state.catchingUp=null;edge();ribbon();} // caught up: the note clears by itself
        const limit=data.indexLimit;
        const source=sourceText();
        message('history-status',(data.status==='indexing'?`Loading history … ${state.order.length} events${source?' · '+source:''}`:`${data.status} · ${data.change} · ${state.newCount} new events`)+`${data.hasMore && data.rowsAhead!==false?' · Later pages available':''}\n`+
          (limit ? `${limit.message} Indexed ${limit.indexedRecords} records / ${limit.indexedBytes} source bytes of ${limit.sourceBytes}.\n` : '')+
          `Traces: ${listField(data.traces,'traces').join(', ') || 'none'}\n`+listField(data.diagnostics,'diagnostics').map(d=>`${d?.offset}: ${d?.status}`).join('\n')+
          (state.warnings.size?'\nSome display metadata is unknown; history retained.':''),data.status==='ambiguous'||!!limit);
      } catch(e) {if(generation===state.generation){
        state.readError=e;
        // Void handles (a pane-server restart, an evicted index, a newer generation) start a resync
        // in place; the rows on screen stay and nothing is marked stale unless that really fails.
        const restart=!resyncing && e.code===409 && RESYNC.has(e.status);
        if(resyncing || restart) {
          if(restart)startResync(e.message);
          if(!e.resyncWait)message('history-status','Reconnecting: the server no longer knows this view’s handles ('+e.message+'; a pane-server restart, or its index was rebuilt) · the rows on screen stay and are checked again; nothing is lost',false);
          return 0;
        }
        state.observationError=e.message;activity();
        if(refusedRead(e)){state.blocked=true;state.blockedReason=e.message;}
        const indexing=(!!state.indexingContinuation || (state.session.source==='host'&&!state.epoch))&&!state.blocked;
        const delay=Math.min(indexing?3000:15000,3000*++state.failures);
        state.nextPoll=Date.now()+delay;
        const stillLoading=(!state.epoch || !!state.indexingContinuation)&&!state.blocked;
        // A slow or unanswered read never blanks the rows on screen: the edge says "Catching up", quietly, and
        // the read is retried by itself (the detail line below keeps its T5.6 wording).
        if(!state.blocked && slowRead(e)){state.catchingUp=state.catchingUp || {since:readStartedAt};edge();ribbon();}
        message('history-status',stillLoading?`Loading history … ${state.order.length} events · Retry in ${delay/1000} s: ${e.message}`:'History unavailable: '+e.message+' · Last visible page retained. Reload/reselect for identity boundaries.',!stillLoading);
      }} finally {clearTimeout(slow);if(generation===state.generation){state.loading=false;state.loadingMode=null;controls();}}
      return added;
    }
    async function latest() {
      if(state.resync)return; // the resync brings the window back first; Following then pages on
      // A protected range can force newer views off the window. A deliberate jump
      // reloads their durable pages instead of pretending the older held window is latest.
      if(state.tailEvicted && !state.next && !state.loading && !state.blocked) {
        const expected=state.generation+1;await browse(state.session,state.scope);if(state.generation!==expected)return;
      }
      const generation=state.generation;state.newCount=0;
      // Yield between bounded pages. No first-N/age cutoff; evicted history stays reloadable.
      while(state.next && !state.blocked && generation===state.generation){const next=state.next;await pageWhenIdle(next,'latest');if(generation!==state.generation)return;if(state.next===next){message('history-status','Paging did not advance or is unavailable; last page retained. Reload/reselect before continuing.',true);break;}await new Promise(resolve=>setTimeout(resolve,0));}
      if(generation===state.generation){$('history-viewport').scrollTop=$('history-viewport').scrollHeight;seen().seenAll();ribbon();
        $('history-status').textContent=$('history-status').textContent.replace(/\b[0-9]+ new events/,'0 new events');}
    }
    // ---------------------------------------------------------- earlier / later history
    // The session's very first record is on screen (or the session has none yet).
    function atStart() {
      if(!state.session)return false;
      if(state.rows.get(state.order[0])?.seq===0)return true; // position 0 is the start, whatever the handles
      if(state.startKey===undefined)return false;
      return state.startKey==='' || state.order[0]===state.startKey;
    }
    // The checkpoint index of the page holding the oldest resident row.
    function oldestPage() {
      for(const key of state.order){const at=state.pages.indexOf(state.rows.get(key)?.page);if(at>=0)return at;}
      return state.pageAt;
    }
    // One page read, waiting (bounded) for a read already in flight (the poll's, an edge load's).
    async function pageWhenIdle(cursor,mode,bookmark=null) {
      const session=state.session;
      for(let i=0;i<400;i++) {
        const added=await page(cursor,mode,bookmark);
        if(added!==null || state.session!==session)return added ?? 0;
        await new Promise(resolve=>setTimeout(resolve,i<20?0:25));
      }
      return 0;
    }
    // @beacon[
    //   id=opc-pane@loadOlderHistory,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The ONE "earlier messages" mechanism shared by scrolling to the top, ↑ past the oldest message on screen and Load earlier: rereads the page holding the oldest resident row, else the one before it, from the reader's checkpoints, else by position (fromSeq, after a resync), above a stable reading position; only with neither does it reopen the conversation at its first page.,
    // ]
    async function loadOlder() {
      if(!state.session || state.blocked || atStart() || !state.pages.length)return 0;
      const session=state.session;
      for(let at=oldestPage();at>=0;at--) {
        const added=await pageWhenIdle(state.pages[at].cursor,'older',at);
        if(state.session!==session || state.blocked)return 0;
        if(added || atStart())return added;
      }
      // Positions reach where checkpoints do not (a resync replaced them, or the eviction of old
      // checkpoints): read the rows just above the oldest one on screen.
      const above=await olderBySeq(session);
      if(above || atStart() || state.session!==session || state.blocked)return above;
      // Checkpoints only reach back BOOKMARKS pages: the start itself is always page one.
      return state.pages[0]?.cursor!==null ? fromStart() : 0;
    }
    async function olderBySeq(session) {
      const lo=state.rows.get(state.order[0])?.seq;
      if(!Number.isSafeInteger(lo) || lo<=0)return 0;
      // A smaller step when a byte-bounded page stops short of the rows just above.
      for(const step of [100,25,1]) {
        const checkpoint={cursor:{fromSeq:Math.max(0,lo-step)},next:null,epoch:state.epoch};
        state.pages.unshift(checkpoint);state.pageAt++;
        const added=await pageWhenIdle(checkpoint.cursor,'older',0);
        if(state.session!==session || state.blocked || added)return added;
      }
      return 0;
    }
    // The same mechanism forward: Load later, scrolling to the bottom and ↓ past the newest row.
    async function loadNewer() {
      const session=state.session;let added=0;
      for(let i=0;i<JUMP_PAGES && !added && state.next && state.session===session && !state.blocked;i++) {
        const next=state.next;added=await pageWhenIdle(next,'forward');
        if(state.next===next)break;
      }
      return added;
    }
    // Reopens the conversation at its first page, paused (the reading position never moves on its own), or at a row
    // position (View's Show in conversation reads the window around a turn this way).
    // Host, OpenCode and 📦 History conversations read position 0 explicitly; their null cursor would reopen the tail.
    async function fromStart(seq=0) {
      if(!state.session || !state.scope)return 0;
      const session=state.session,scope=state.scope,leaf=state.leaf;
      state.follow=false;state.followPaused=true;state.selectionPause=false;
      reset();state.session=session;state.scope=scope;disclosure();ribbon();
      state.leaf=leaf;const added=await pageWhenIdle(seq>0 || state.session.source==='host' || isOpenCode(state.session) || archived(state.session)?{fromSeq:seq}:null,'initial');
      $('history-viewport').scrollTop=0;return added;
    }
    // @beacon[
    //   id=opc-pane@navigateHistory,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Ribbon ↑/↓ and ⤒ First as one queue: each click is a move done in order (five quick clicks = five moves), and a move past the loaded window loads earlier/later pages until it finds the message or reaches the session's real start or end.,
    // ]
    function navigate(task) {
      if(!state.session || state.navPending>=NAV_QUEUE)return state.navQueue;
      const session=state.session;state.navPending++;
      state.navQueue=state.navQueue.then(async()=>{if(state.session===session)await task();})
        .catch(e=>globalThis.console?.warn?.('History navigation: '+e.message)).finally(()=>{state.navPending--;});
      return state.navQueue;
    }
    async function jumpLoading(kind,direction) {
      const session=state.session;
      for(let i=0;i<JUMP_PAGES && state.session===session;i++) {
        if(jump(kind,direction))return true;
        if(direction<0 && state.follow)setFollow(false,{byScroll:true}); // loading earlier pages must not re-pin the bottom
        if(!(direction<0?await loadOlder():await loadNewer()))return jump(kind,direction);
      }
      return false;
    }
    async function toFirst() {
      if(!atStart())await fromStart();
      // Land on the conversation's first message (Dan's, or a subagent's prompt), else the very top.
      const first=state.order.map(key=>state.rows.get(key)).find(row=>row?.node.classList.contains('turn-user'));
      const viewport=$('history-viewport');
      if(state.follow)setFollow(false,{byScroll:true});
      viewport.scrollTop=first?viewport.scrollTop+first.node.getBoundingClientRect().top-viewport.getBoundingClientRect().top-LAND:0;
      state.lastJump=first?{kind:'turn-user',direction:-1,key:first.key,y:LAND}:null;
    }
    // Scrolling near either end loads the next batch that way (one at a time).
    async function edgeLoad(direction) {
      if(state.edgeBusy || state.navPending)return;state.edgeBusy=true;
      try{if(direction<0)await loadOlder();else await loadNewer();}finally{state.edgeBusy=false;}
    }
    async function mailbox() {
      const data=await json('/api/tool-selection');
      if(!ref(data.epoch) || !Number.isSafeInteger(data.revision))throw new Error('invalid mailbox cursor');
      if(state.mailbox?.epoch===data.epoch && data.revision<state.mailbox.revision)return data;
      state.mailbox={epoch:data.epoch,revision:data.revision};
      if(state.accepted && (state.accepted.epoch!==data.epoch || state.accepted.revision!==data.revision || data.selection?.callRef!==state.accepted.callRef)) {state.accepted=null;mark();message('inspect-status','Held call closed or replaced; click deliberately to select again.');}
      return data;
    }
    // One deliberate click: its backend and exact call identity are frozen here, before any await.
    function inspect(call) {
      const action={...call,backend:call.backend==='opencode'?'opencode':'claude',generation:state.generation,sequence:++state.clickSequence,observedAt:Date.now()};
      callTurn(call.callRef);  // remembered now, while its row is on screen: the inspector's handles root View at this turn
      state.queued=action;state.pending=action;state.accepted=null;state.failed=null;mark();
      message('inspect-status','Inspect pending · not yet accepted');void relay();
    }
    // An OpenCode ACK must name exactly the frozen click: backend, session, message, part, call, epoch, selector, sequence.
    function ocAck(data,action) {
      const s=data?.selection;
      return data?.schemaVersion===1 && data.backend==='opencode' && data.ok===true && ref(data.epoch) && Number.isSafeInteger(data.revision) && !!s &&
        s.backend==='opencode' && s.source==='opencode' && s.sessionId===action.sessionId && s.messageId===action.messageId && s.partId===action.partId &&
        s.partIndex===action.partIndex && s.callRef===action.callRef && s.fileEpoch===action.fileEpoch && s.selectorId===clientId && s.sequence===action.sequence;
    }
    // @beacon[
    //   id=opc-pane@relayInspect,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Posts the latest deliberate Inspect click by its frozen backend (Claude callRef/epoch to /api/claude/inspect; an OpenCode part to /api/opencode/inspect), checks the ACK names exactly that click, then requires the shared mailbox's epoch, revision and selection to still agree before the accepted highlight; a later click or a browse drops a late ACK; nothing retries by itself.,
    // ]
    async function relay() {
      if(state.posting)return;state.posting=true;
      try {while(state.queued){const action=state.queued;state.queued=null;
        try {
          const oc=action.backend==='opencode';
          const data=oc?await json('/api/opencode/inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({schemaVersion:1,backend:'opencode',id:action.sessionId,messageId:action.messageId,partIndex:action.partIndex,callRef:action.callRef,fileEpoch:action.fileEpoch,selectorId:clientId,sequence:action.sequence,observedAt:action.observedAt})}):
            await json('/api/claude/inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({schemaVersion:1,backend:'claude',callRef:action.callRef,fileEpoch:action.fileEpoch,selectorId:clientId,sequence:action.sequence,observedAt:action.observedAt})});
          if(action.generation!==state.generation || action.sequence!==state.clickSequence)continue;
          if(oc?!ocAck(data,action):data.selection?.backend!=='claude' || data.selection.callRef!==action.callRef || data.selection.fileEpoch!==action.fileEpoch || data.selection.selectorId!==clientId || data.selection.sequence!==action.sequence)throw new Error('Inspect ACK identity changed');
          const current=await mailbox();
          if(action.generation!==state.generation || action.sequence!==state.clickSequence)continue;
          if(current.epoch!==data.epoch || current.revision!==data.revision || current.selection?.callRef!==action.callRef ||
            (oc && (current.selection?.backend!=='opencode' || current.selection?.sessionId!==action.sessionId || current.selection?.partId!==action.partId)))throw new Error('Inspect closed or replaced before ACK');
          state.accepted={...action,epoch:data.epoch,revision:data.revision};state.pending=null;
          message('inspect-status','Exact call accepted in the existing inspector; target unchanged');
        } catch(e) {if(action.generation===state.generation&&action.sequence===state.clickSequence){state.pending=null;state.accepted=null;state.failed=action.key;message('inspect-status','Inspect not accepted: '+e.message+' · Click deliberately to retry.',true);}}
        mark();
      }} finally {state.posting=false;}
    }
    function finishTarget(text,error) {clearTimeout(state.targetAction?.timer);state.targetAction=null;message('target-status',text,error);}
    function targetMessage(event) {
      const m=event.data,a=state.targetAction;if(!a || !m || m.schemaVersion!==1 || m.clientId!==clientId)return;
      const direct=event.source===parentEditor && parentEditor && event.origin===origin;
      if(event.source && !direct)return;
      if(m.type==='presence' && m.requestId===a.id && typeof m.editorId==='string' && m.editorId.length<=128 && Number.isSafeInteger(m.targetRevision)) {
        a.editors.add(m.editorId);
        if(direct && !a.sent){a.editorId=m.editorId;a.targetRevision=m.targetRevision;}
      } else if(direct && a.sent && m.type==='ack' && m.editorId===a.editorId && m.actionId===a.id && m.actionRevision===a.sequence && typeof m.applied==='boolean') {
        if(m.pending===true && a.automatic && !m.applied){clearTimeout(a.timer);message('target-status',m.reason || 'Connecting · checking the selected Manager again');return;}
        finishTarget((m.applied?(String(m.reason).startsWith('Ready:')?'':'Ready: '):'Target not applied: ')+(m.reason||'explicit parent editor ACK')+
          (a.editors.size>1?' · another pane tab is open; '+(m.applied?'target applied here only':'no other tab was targeted'):''),!m.applied);
      }
    }
    // @beacon[
    //   id=opc-pane@useClaudeTarget,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Integrated viewer addresses only its actual same-origin parent editor; BroadcastChannel discovers duplicate tabs but never carries target authority, and standalone history remains read-only. A validated OpenCode Manager posts its backend-qualified full-id target form with no targetRef or Claude fields; an ordinary OpenCode conversation posts nothing, and a delayed earlier action for the other backend (or another session) is suppressed before it can replace the editor's target.,
    // ]
    function useTarget(target=selectionTarget(),automatic=false) {
      const oc=isOpenCode(state.session),manager=oc?openCodeManagerTarget(state.session):null;
      if(oc && !manager){message('target-status','Not a Send/Refine target: an OpenCode conversation is view-only here; the editor keeps its current target (OpenCode Send mode is unchanged).');return;}
      const destination=oc?manager:target;
      if(!destination || (state.blocked && !automatic))return;
      if(!parentEditor){message('target-status','Target not applied: standalone sessions is read-only. Use the sessions panel inside the pane you want to target.',true);return;}
      clearTimeout(state.targetAction?.timer);
      const action={id:id(),sequence:++state.targetSequence,backend:oc?'opencode':'claude',target:destination,identity:identityOf(state.session),automatic,editorId:null,editors:new Set(),sent:false};
      state.targetAction=action;
      // A late timer may only address the conversation and witness it was created for; a browse or a
      // backend switch in between never turns an old Claude action into a target under an OpenCode Manager.
      const live=()=>state.targetAction===action && (action.backend==='opencode'?
        isOpenCode(state.session) && openCodeManagerTarget(state.session)?.sessionId===action.target.sessionId &&
          openCodeManagerTarget(state.session)?.witness===action.target.witness :
        !isOpenCode(state.session) && identityOf(state.session)===action.identity);
      action.timer=setTimeout(()=>{
        if(!live())return;
        if(!action.editorId)return finishTarget('Target not applied: no parent editor answered. Reload this pane, then click again.',true);
        action.sent=true;
        action.timer=setTimeout(()=>finishTarget('Target ACK timed out; application unknown, not claimed. Check this editor before another deliberate action.',true),13000);
        const payload=action.backend==='opencode'?
          {schemaVersion:1,type:'target',clientId,editorId:action.editorId,actionId:action.id,actionRevision:action.sequence,targetRevision:action.targetRevision,backend:'opencode',sessionId:action.target.sessionId,...(automatic?{automatic:true}:{})}:
          {schemaVersion:1,type:'target',clientId,editorId:action.editorId,actionId:action.id,actionRevision:action.sequence,targetRevision:action.targetRevision,targetRef:action.target,...(automatic?{automatic:true}:{})};
        parentEditor.postMessage(payload,origin);
      },1500);
      message('target-status',automatic?'Connecting to '+(state.session?.title || state.session?.id || state.session?.uuid)+' · checking Send target':'Waiting for this parent editor; target not yet applied');
      const hello={schemaVersion:1,type:'hello',clientId,requestId:action.id};
      parentEditor.postMessage(hello,origin);
      try{state.channel?.postMessage(hello);}catch(_){/* optional duplicate discovery only */}
    }
    function renderChildren() {
      const list=$('subagents-list');if(!list)return;
      // The model/thinking label is part of the signature: a child is first listed before its
      // transcript records a turn, so without it a running child kept its label-less first render.
      const parent=state.childParent,signature=JSON.stringify(state.children.map(row=>{const f=factsParts(row.turnFacts);
        return [row.sessionRef,row.title,activityOf('a:'+identityOf(parent)+':'+row.agentId).own==='busy',f?f.models:null,f?f.levels:null,row.turnFacts?.firstAt ?? null,row.turnFacts?.lastAt ?? null];}));
      if(signature===state.childSignature) {
        for(let i=0;i<state.children.length;i++)list.children[i]?.setAttribute('aria-pressed',String(isSelected(state.children[i])));
        if($('subagents-more'))$('subagents-more').disabled=!state.childNext || state.childBusy;
        return;
      }
      state.childSignature=signature;
      const fragment=doc.createDocumentFragment();
      for(const row of state.children)fragment.append(childButton(row,parent));
      list.replaceChildren(fragment);if($('subagents-more'))$('subagents-more').disabled=!state.childNext || state.childBusy;
    }
    // @beacon[
    //   id=opc-pane@pollClaudeSubagents,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Parent-scoped native child pages keep the three-second watch and explicit More/Refresh paging; only actual children draw an expander and successful discovery leaves no empty/view-only footer. Failed discovery keeps its real warning and retry on metadata. OpenCode children stay on their existing inventory tree; no selection/target changes.,
    // ]
    async function children(cursor=null) {
      if(!state.childParent || state.childBusy || isOpenCode(state.childParent) || isOpenCode(state.session))return;
      const parent=state.childParent,generation=state.childGeneration;state.childBusy=true;
      try {
        const data=await json('/api/claude/subagents?'+new URLSearchParams({sessionRef:parent.sessionRef,...(cursor?{cursor}:{})}));
        if(generation!==state.childGeneration)return;envelope(data);
        if(!Array.isArray(data.sessions) || data.sessions.length>8 || data.sessions.some(row=>!ref(row.sessionRef)||!row.parentUuid||row.role!=='subagent'||!Array.isArray(row.scopes)) || (data.hasMore&&!ref(data.nextCursor)))throw new Error('invalid subagent inventory schema');
        state.children=data.sessions;state.childNext=data.hasMore?data.nextCursor:null;state.childPaged=!!cursor;state.childLoaded=true;state.childError=null;
        message('subagents-status','');inventory();
        $('subagents-status').title=`${parent.title || parent.uuid} · transcript growth is not task acceptance`;
        state.childNextPoll=Date.now()+3000;
      } catch(e){if(generation===state.childGeneration){state.childNextPoll=Date.now()+15000;state.childError=e.message;message('subagents-status','Subagent discovery unavailable: '+e.message+' · last page retained',true);inventory();}}
      finally {state.childBusy=false;if(childControls.more)childControls.more.disabled=!state.childNext;if(childControls.refresh)childControls.refresh.disabled=false;}
    }
    // Conversation polling cadence. OpenCode first: about 1 s while its page says busy, 3 s idle or unknown
    // (never the native-subagent 600 ms); a host or native subagent conversation 600 ms, else 3 s.
    function pollDelay() {
      if(isOpenCode(state.session))return state.ocBusyFact===true?1000:3000;
      return state.session?.source==='host'||role(state.session || {})==='subagent'?600:3000;
    }
    // @beacon[
    //   id=opc-pane@pollClaudeHistory,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Visible-page refreshes keep host summaries, Claude activity, fast OpenCode busy/call facts, the selected validated OpenCode Manager's own two-second readback and glances independent of the two-slot optional-detail lane; refresh the visible freshness caption and admit newly visible details without walking native history. Phone remains sidebar-only; conversation paging is unchanged.,
    // ]
    async function poll() {
      if(!sidebarOnly)flushRepaints();
      if(state.pollBusy || doc.hidden || doc.defaultView?.frameElement?.closest?.('[hidden]'))return;state.pollBusy=true;
      try {
        // The live sidebar readers below run only while the list's source is live: none while 📦 History is (useSource).
        const live=!state.archive;
        // The whole-list refresh runs beside the conversation poll, never in front of it.
        if(live && !state.inventoryBusy && !state.renaming && Date.now()>=state.inventoryNextPoll)void list();
        if(live && !state.hostActivityBusy && Date.now()>=state.hostActivityNextPoll)void pollHostActivity();
        // OpenCode's program rows on their own cadence: the Claude rows never wait for them.
         if(live && !state.ocBusy && Date.now()>=state.ocNextPoll)void ocList();
         if(live && !state.ocActivityBusy && Date.now()>=state.ocActivityNextPoll)void pollOpenCodeActivity();
         // The selected Manager's model/context/busy readback: its own GET lane, never a Claude read.
         if(live && state.session?.manager && !state.ocManagerBusy && Date.now()>=state.ocManagerNextPoll)void pollOpenCodeManager();
        // Open glances on their own modest cadence, only while open: never part of the reads above.
        if(live && state.glances.size && !state.glancePolling && Date.now()>=state.glanceNextPoll)void pollGlances();
        detailFlush();
        sidebarFreshness();
        if(sidebarOnly)return;
        if(state.focus && Date.now()>=state.focusNextPoll)void focusPoll();
        if(!state.childPaged && state.childParent?.capabilities?.subagents===true && Date.now()>=state.childNextPoll)await children();
        if(state.accepted)try{await mailbox();}catch(e){state.accepted=null;mark();message('inspect-status','Mailbox unavailable; accepted highlight withheld: '+e.message,true);}
        // Void handles: the resync re-acquires them and re-reads the window in place, on its own cadence.
        if(state.resync)await resync();
        else if(!state.loading && !state.blocked && Date.now()>=state.nextPoll) {
          // Only the server's tail-only scan witness may auto-continue. Ordinary
          // unloaded conversation pages remain an explicit More/Jump action.
          const generation=state.generation;
          // Following: ordinary later pages are traversed to the newest turn (Jump to latest).
          // Paused, they remain an explicit Load later / Jump action.
          if(state.follow && state.next && !state.indexingContinuation)await latest();
          else if(!state.indexingContinuation && state.delta && !state.next)await page(state.delta,'delta');
          else if(role(state.session || {})==='subagent' && !archived(state.session) && !state.delta && !state.next && !state.loading)await page(null,'initial');
          while(state.indexingContinuation && generation===state.generation && !state.loading && !state.blocked &&
            Date.now()>=state.nextPoll && !doc.hidden && !doc.defaultView?.frameElement?.closest?.('[hidden]')) {
            const cursor=state.indexingContinuation;await page(cursor,'forward');
            if(generation!==state.generation || state.failures)break;
            if(state.indexingContinuation===cursor){warn('indexing cursor did not advance');state.nextPoll=Date.now()+3000;break;}
            await new Promise(resolve=>setTimeout(resolve,0));
          }
        }
      } finally {state.pollBusy=false;if(!sidebarOnly)enforceFollow();}
    }
    // @beacon[
    //   id=opc-pane@sideDivider,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=The draggable divider between the session list and the conversation (pointer capture, Left/Right arrows, Home/End), the round-5 column-divider pattern: one percent width in a CSS variable, clamped and remembered in this browser.,
    // ]
    function setSideWidth(value,save=false) {
      const main=$('sessions-divider')?.parentNode,width=Math.max(SIDE_MIN,Math.min(SIDE_MAX,Number.isFinite(value)?value:SIDE_DEFAULT));
      state.sideWidth=width;main?.style?.setProperty?.('--side-width',width+'%');
      $('sessions-divider')?.setAttribute?.('aria-valuenow',String(Math.round(width)));
      if(save)try{doc.defaultView?.localStorage?.setItem(SIDE_WIDTH,String(Math.round(width*10)/10));}catch(_){/* display preference only */}
      return width;
    }
    function sideDivider() {
      const divider=$('sessions-divider');if(!divider?.addEventListener)return;
      let stored=null;try{stored=Number(doc.defaultView?.localStorage?.getItem(SIDE_WIDTH));}catch(_){stored=null;}
      setSideWidth(stored || SIDE_DEFAULT);
      divider.setAttribute('aria-valuemin',String(SIDE_MIN));divider.setAttribute('aria-valuemax',String(SIDE_MAX));
      let drag=null;
      const finish=()=>{if(!drag)return;drag=null;divider.classList.remove('dragging');doc.body?.classList?.remove('side-dragging');setSideWidth(state.sideWidth,true);};
      divider.addEventListener('pointerdown',event=>{
        if(event.button!==0)return;event.preventDefault();divider.focus?.();
        try{divider.setPointerCapture?.(event.pointerId);}catch(_){/* drag still works inside the divider */}
        drag={id:event.pointerId,x:event.clientX,width:state.sideWidth};divider.classList.add('dragging');doc.body?.classList?.add('side-dragging');
      });
      divider.addEventListener('pointermove',event=>{
        if(!drag || drag.id!==event.pointerId)return;
        const total=divider.parentNode?.getBoundingClientRect?.().width;if(total)setSideWidth(drag.width+(event.clientX-drag.x)/total*100);
      });
      divider.addEventListener('pointerup',finish);divider.addEventListener('pointercancel',finish);divider.addEventListener('lostpointercapture',finish);
      divider.addEventListener('keydown',event=>{
        if(event.key==='ArrowLeft' || event.key==='ArrowRight'){event.preventDefault();setSideWidth(state.sideWidth+(event.key==='ArrowLeft'?-2:2),true);}
        else if(event.key==='Home' || event.key==='End'){event.preventDefault();setSideWidth(event.key==='Home'?SIDE_MIN:SIDE_MAX,true);}
      });
    }
    function wire() {
      // The phone holds placements only for a real mouse: a touch pointer "leaves" on every lift,
      // and rebuilding the list there replaced the row iOS was about to click (taps did nothing).
      teleKey();
      const hover=on=>event=>{if(!sidebarOnly || event?.pointerType==='mouse')setSidebarHover(on);};
      $('sessions-scroll')?.addEventListener?.('pointerenter',hover(true));
      $('sessions-scroll')?.addEventListener?.('pointerleave',hover(false));
      // The list's own scroll anchoring (listAnchor): remembered on every scroll, background redraws held back
      // under a finger or a moving scroll (settleList), and in-place growth or shrinking (a row's times
      // arriving, a resize) put back by the ResizeObserver before paint.
      const scroller=$('sessions-scroll');
      if(scroller?.addEventListener) {
        scroller.addEventListener('scroll',()=>{
          if(state.listOwnTop===null || Math.abs(scroller.scrollTop-state.listOwnTop)>=1)state.listScrolledAt=Date.now();
          state.listOwnTop=null;rememberList();detailFlush();
        },{passive:true});
        const touched=()=>{state.listTouch=true;state.listTouchAt=Date.now();};
        scroller.addEventListener('touchstart',touched,{passive:true});scroller.addEventListener('touchmove',touched,{passive:true});
        // The touch's end is heard on the whole document (capture), not only on the list: it may end outside the list,
        // or on a node a redraw replaced (its events never reach the list again). A hidden or left page lifts it too.
        const lift=event=>{
          if(!state.listTouch)return;
          state.listTouch=(event?.touches?.length || 0)>0;
          if(!state.listTouch){state.listScrolledAt=Date.now();if(state.inventoryDirty)settleList();}
        };
        for(const type of ['touchend','touchcancel'])doc.addEventListener?.(type,lift,{capture:true,passive:true});
        doc.addEventListener?.('visibilitychange',()=>{if(doc.hidden)lift();});
        for(const type of ['pagehide','blur'])view?.addEventListener?.(type,()=>lift());
        if(typeof view?.ResizeObserver==='function'){const observer=new view.ResizeObserver(()=>{restoreList(state.listAnchor);rememberList();});
          for(const child of scroller.children || [])observer.observe(child);
          // A new list width (a resize, the sidebar dragged, a hidden page shown) fits the rows' telemetry again (teleFit);
          // the rows it changes are deeper than the scroller, so the observer above puts the reading position back.
          new view.ResizeObserver(()=>{if(scroller.clientWidth!==state.teleWidth){state.teleWidth=scroller.clientWidth;teleFit();}}).observe(scroller);}
      }
      // A deliberate action (a tap, a choice, a key) is never held back, and a tapped row is anchored first:
      // it stays under the finger through the redraw it causes. A redraw still held when it is done lands at once,
      // from the latest inventory.
      for(const type of ['click','change','keydown'])doc.addEventListener?.(type,event=>{
        state.listHint={row:tappedAnchor(event.target)};
        setTimeout(()=>{state.listHint=null;if(state.inventoryDirty && !state.renaming)inventory(true);},0);},true);
      if(childControls.refresh)childControls.refresh.onclick=event=>{event?.stopPropagation?.();state.childPaged=false;void children();};
      if(childControls.more)childControls.more.onclick=event=>{event?.stopPropagation?.();void children(state.childNext);};
      // ⟳ reads the list's own source again: 📦 History from its first page, the live list as before.
      $('sessions-refresh').onclick=()=>{if(state.archive){useSource(true);void historyPage();return;}void list();void ocList();void pollHostActivity();void pollOpenCodeActivity(true);};
      $('sessions-load-history')?.addEventListener?.('click',()=>void list(true));
      $('sessions-native-history')?.addEventListener?.('toggle',()=>{if($('sessions-native-history').open && !state.nativeHistoryLoaded && !state.inventoryBusy)void list(true);});
      if($('sessions-collapse')){$('sessions-collapse').onclick=()=>foldTree(treeExpanded(),false);collapseLabel();}
      if($('sessions-expand'))$('sessions-expand').onclick=()=>setAllPrograms(true);
      if(sidebarOnly && $('sessions-cycle')){$('sessions-cycle').onclick=cyclePhoneTree;cycleLabel();}
      sideDivider();
      $('sessions-role').onchange=inventory;
      if($('sessions-view')){$('sessions-view').value=setView();$('sessions-view').onchange=()=>void setView($('sessions-view').value);}
      // The phone's outdoor glare mode (glare.js) resizes the rows' text: redraw at once, so their telemetry is fitted again.
      if(sidebarOnly)view?.addEventListener?.('opc-glare',()=>inventory(true));
      setInterval(()=>{if(!doc.hidden && !doc.defaultView?.frameElement?.closest?.('[hidden]'))tickGlanceTimes();},1000);
      if(sidebarOnly)return; // This page is the existing sidebar, not a second conversation/editor.
      if($('pane-conversation'))$('pane-conversation').onclick=()=>showPane('conversation');
      if($('pane-view'))$('pane-view').onclick=()=>showPane('view');
      $('history-scope').onchange=()=>void browse(state.session,state.session.scopes.find(x=>x.scopeRef===$('history-scope').value));
      $('history-leaf').onchange=()=>void browse(state.session,state.scope,$('history-leaf').value || null);
      // Reload reads the conversation again and lands on its newest turn, Following.
      $('history-reload').onclick=()=>{state.follow=true;state.followPaused=false;state.selectionPause=false;void browse(state.session,state.scope);};
      $('history-target').onclick=()=>useTarget();
      $('history-more').onclick=()=>void navigate(loadNewer);
      $('history-older').onclick=()=>void navigate(loadOlder);
      $('history-latest').onclick=()=>void latest();
      if($('history-latest-badge'))$('history-latest-badge').onclick=()=>state.session && (state.blocked || state.resyncWarn)?void reconnect():setFollow(true);
      $('history-tools').onchange=()=>{const saved=anchor();$('history-events').classList.toggle('tools-only',$('history-tools').checked);if(state.follow)stickToLatest();else restoreAnchor(saved);ribbon();};
      // A view that stopped updating has nothing to follow: the toggle reconnects it instead.
      if($('history-follow'))$('history-follow').onclick=()=>state.session && (state.blocked || state.resyncWarn)?void reconnect():setFollow(!state.follow);
      if($('ribbon-reconnect'))$('ribbon-reconnect').onclick=()=>void reconnect();
      if($('ribbon-subagents'))$('ribbon-subagents').onclick=()=>setActivityHidden(!state.activityHidden);
      if($('ribbon-tools'))$('ribbon-tools').onclick=()=>{$('history-tools').checked=!$('history-tools').checked;$('history-tools').onchange();};
      if($('ribbon-thinking'))$('ribbon-thinking').onclick=()=>{
        const open=$('ribbon-thinking').getAttribute('aria-pressed')!=='true';$('ribbon-thinking').setAttribute('aria-pressed',String(open));
        const saved=anchor();for(const row of state.rows.values())for(const block of row.thinking)if(!block.node.classList.contains('thinking-empty'))block.node.open=open;
        if(state.follow)stickToLatest();else restoreAnchor(saved);
      };
      for(const [id,kind,direction] of [['nav-user-prev','turn-user',-1],['nav-user-next','turn-user',1],['nav-final-prev','turn-final',-1],['nav-final-next','turn-final',1]])
        if($(id))$(id).onclick=()=>void navigate(()=>jumpLoading(kind,direction));
      if($('nav-start'))$('nav-start').onclick=()=>void navigate(toFirst);
      // Only a deliberate user scroll (wheel, touch, keys, scrollbar drag) pauses or resumes
      // following; the viewer's own scroll writes never do.
      const viewport=$('history-viewport'),intent=()=>{state.scrollIntent=Date.now();};
      viewport.addEventListener?.('wheel',intent,{passive:true});viewport.addEventListener?.('touchmove',intent,{passive:true});
      viewport.addEventListener?.('pointerdown',intent);viewport.addEventListener?.('keydown',e=>{if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(e.key))intent();});
      // The view changes height when the expander opens or the window resizes: keep following.
      // So does the content itself: any growth (a new row, a streaming row, a subagent group
      // gaining entries, a disclosure opening) re-pins while following, whatever path caused it.
      if(typeof view?.ResizeObserver==='function'){const observer=new view.ResizeObserver(()=>{if(state.follow && state.session)stickToLatest();});
        observer.observe(viewport);observer.observe($('history-events'));}
      viewport.addEventListener?.('scroll',()=>{
        // The floating ↓ Latest badge follows every scroll, the viewer's own included (a few unseen rows measured).
        const unseen=state.unseenBelow;latestBadge();if(state.unseenBelow!==unseen)ribbon();
        if(Date.now()-state.scrollIntent>1500)return;
        const at=atLatest();
        if(state.follow && !at)setFollow(false,{byScroll:true});
        else if(!state.follow && state.selectionPause && !at){state.selectionPause=false;ribbon();} // now a reading pause
        else if(!state.follow && state.followPaused && at && !state.next)setFollow(true,{byScroll:true});
        // Like any long page: near the top the earlier messages load, near the bottom the later
        // ones (when the newest are not loaded), keeping the reading position where it is.
        if(viewport.scrollTop<EDGE_PX && !atStart())void edgeLoad(-1);
        else if(!state.follow && state.next && viewport.scrollHeight-viewport.scrollTop-viewport.clientHeight<EDGE_PX)void edgeLoad(1);
      });
      doc.addEventListener?.('selectionchange',selectionChanged);
      view?.addEventListener?.('blur',leftConversation);
      $('history-density').onchange=()=>$('history-events').style.setProperty('--preview-lines',$('history-density').value==='5'?'5':'3');
      $('history-events').classList.toggle('tools-only',$('history-tools').checked);
      doc.addEventListener?.('selectionchange',flushRepaints);
      doc.addEventListener?.('focusout',()=>setTimeout(flushRepaints,0));
      try{if(typeof BroadcastChannel==='function'){state.channel=new BroadcastChannel('pane-target-v1');state.channel.onmessage=targetMessage;}}catch(_){/* visible failure on deliberate action */}
      view?.addEventListener?.('message',targetMessage);
      view?.addEventListener?.('message',event=>{if(event.source===parentEditor && event.origin===origin && event.data?.schemaVersion===1 && event.data?.type==='host-launch')state.inventoryNextPoll=0;});
      // The editor launched a New Manager and selected it: show that conversation here too.
      view?.addEventListener?.('message',event=>{if(event.source===parentEditor && event.origin===origin && event.data?.schemaVersion===1 &&
        event.data?.type==='browse-session' && typeof event.data.uuid==='string'){showPane('conversation');void browseUuid(event.data.uuid);}});
      // The editor adopted a validated OpenCode Manager: open that exact conversation (its full sid is
      // validated again here, GET only) so an empty/new Manager is readable without CodeNomad.
      view?.addEventListener?.('message',event=>{if(event.source===parentEditor && event.origin===origin && event.data?.schemaVersion===1 &&
        event.data?.type==='browse-opencode' && typeof event.data.sessionId==='string' && /^ses_[A-Za-z0-9]{8,64}$/.test(event.data.sessionId)){showPane('conversation');void openOpenCode(event.data.sessionId);}});
      controls();activity();
      // Clock-only; polling remains independently throttled and pauses when hidden. The call timers
      // repaint their own time cell in place once a second, never the row bodies.
      setInterval(()=>{if(!doc.hidden && !doc.defaultView?.frameElement?.closest?.('[hidden]')){activity();enforceFollow();tickCallTimers();}},1000);
      // Observe selected/watched-parent mark inputs between list reads; every rebuild derives
      // all sidebar marks from a fresh activity map, never from streamed activity or an age guess.
      setInterval(()=>{
        if(doc.hidden || !state.session)return;
        const marks=JSON.stringify([activeNow(state.session),backgroundCount(state.session),[...runningAgents(state.childParent || state.session)]]);
        if(marks!==state.sidebarMarks){state.sidebarMarks=marks;inventory();renderChildren();}
      },2000);
    }
    wire();return {list,browse,page,latest,inspect,poll,pollDelay,useTarget,targetMessage,state,controls,reconcile,flushRepaints,activity,children,setFollow,enforceFollow,selectionChanged,leftConversation,classify,jump,renderActivity,setActivityHidden,clock,fullTime,tickCallTimers,
      inventory,selectManagerFamily,loadManagerFamily,detailFlush,sidebarFreshness,startRename,setView,historyPage,starKey,starButton,loadStars,toggleStar,atStart,loadOlder,loadNewer,fromStart,navigate,jumpLoading,toFirst,lineageTree,ocList,pollOpenCodeActivity,ocEvidence,blockingWaits,waitDescription,toggleGlance,pollGlances,openInCodeNomad,focusPoll,shortTitle,setSideWidth,
      reconnect,resync,edge,identityOf,isSelected,waitingNow,setAllPrograms,cyclePhoneTree,setSidebarHover,openOpenCode,openCodeSession,address,mailbox,header,
      programPosition,positionWhenReady,revealInList,originOf,openInView,handleKey,haltOf,haltBanner,turnAt,callTurn,showPane,openView,showTurn};
  }
  globalThis.opcClaudeHistory=Object.freeze({create:createClaudeHistory,limits:{PAGE_BYTES,WINDOW_BYTES,WINDOW_EVENTS,BOOKMARKS}});
  if(typeof document!=='undefined' && document.getElementById('sessions-list')) {
    if(globalThis.frameElement?.id==='claude-sessions-frame' || new URLSearchParams(globalThis.location?.search || '').get('embedded')==='1')document.body.classList.add('embedded');
    const history=createClaudeHistory();void history.list();setInterval(()=>void history.poll(),600);
    // View's root widget asks this list where a socket sits and to show a session (through /m, or the desktop pane);
    // the editor's ↳ New sub-session asks it for the listed Managers' names.
    globalThis.opcSidebar=Object.freeze({position:history.programPosition,positionWhenReady:history.positionWhenReady,reveal:history.revealInList,
      selectManager:history.selectManagerFamily,managerNames:()=>(history.state.managerNames || []).map(names=>[...names])});
    globalThis.opcOpenInView=(handle,context)=>history.openInView(handle,context);
    // The desktop page around View's frame (never the phone's sidebar page, whose View is /m's): where a socket sits, a
    // session shown in the list (View stays on screen), a conversation turn shown again; the editor around this frame
    // opens handles and the 📄 View link here, and asks for the inspector's held call's turn (turnOf).
    if(document.body?.dataset?.view!=='programs') {
      globalThis.opcProgramPosition=(handle,active)=>history.positionWhenReady(handle,active);
      globalThis.opcRevealSession=key=>history.revealInList(key);
      globalThis.opcShowTurn=turn=>history.showTurn(turn);
      globalThis.opcDesktopView=Object.freeze({open:(handle,origin)=>history.openView(handle,origin || {from:'outside'}),show:pane=>history.showPane(pane || 'view'),
        turnOf:callRef=>history.callTurn(callRef)});
    }
  }
})();
