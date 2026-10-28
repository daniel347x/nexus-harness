'use strict';

// View's history (glimpse.html: the phone's View tab, the desktop View pane, the page on its own), in two layers.
// The inner history: a root and the handles opened from it, browser-style, kept in this page context's localStorage. The
// root comes from outside View (a session row's socket in Programs or the sidebar, a handle tapped in Chat or the
// conversation) or from View's own pickers (a Dock, a slice group, a pasted handle, a spec), and starts a fresh inner
// history; a handle tapped inside the document adds an entry. Earlier entries re-render without dropping the later
// ones; only a new handle opened from there cuts them. The root widget names the root first; the retained source and
// its pop-up say where a session's socket sits in its program, from the sidebar's own tree (the page around this frame
// answers; nothing here reads the inventory), one card per session.
// The outer history, "Recent roots": the roots shown lately, each kept with its own inner history and Dan's note, on
// the pane server (view_roots.py, through the store glimpse.js hands in), so the phone and the desktop share one list.
// Opening a root that is already listed brings its digging back; picking one from the list goes back to where it stood.
const opcViewHistory = (() => {
  const VERSION = 1, MAX_ENTRIES = 50, NAME_MAX = 2048, TITLE_MAX = 256, DESC_MAX = 256, LINE_MAX = 300, CARDS_MAX = 12, STORE_MAX = 96 * 1024;
  // Presentation is shorter than stored metadata: filtering still sees the whole available name/description.
  const METADATA_DESC_MAX = 320;
  const OUTER_BYTES = 120 * 1024, NOTE_MAX = 4000, SAVE_MS = 700;
  const AP = /^\[ap:([A-Za-z0-9]{3,16})\]$/, HEX = /^([0-9a-f]{8})(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?$/i;
  const PICK = Object.freeze({dock: 'Dock', group: 'Slice group', parcel: 'Parcel', estate: 'Estate'});
  const HOW = Object.freeze({node: 'Node', subtree: 'Subtree', dock: 'Dock'});
  const DEVICE = Object.freeze({phone: 'phone', desktop: 'desktop', page: 'View page', framed: 'View frame'});
  // One identity for a handle: an Anchor Point by its core, a UUID by its first 8 hex (a socket is stored either way).
  function handleKey(handle) {
    const s = String(handle || '').trim(), ap = AP.exec(s);
    if (ap) return 'ap:' + ap[1].toUpperCase();
    const hex = HEX.exec(s);
    return hex ? 'hex:' + hex[1].toLowerCase() : null;
  }
  // What a card shows of a handle: an Anchor Point as written, a whole UUID as its 8 hex.
  function shortHandle(handle) { const s = String(handle || '').trim(); return /^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(s) ? s.slice(0, 8) : s; }
  // A short, stable hash (FNV-1a, base 36) of what a pick chose.
  function hash(text) { let h = 0x811c9dc5; for (const ch of String(text)) { h ^= ch.codePointAt(0); h = Math.imul(h, 0x01000193) >>> 0; } return h.toString(36); }
  // One Recent-roots key per root (view_roots.py KEY): how a handle is read and its identity, so a Dock picked in the
  // list and the same Dock pasted as Dock (or a {node: …} spec and the handle) are one root; any other pick by its kind
  // and a short hash of what was chosen.
  function rootKey(entry) {
    if (!entry || typeof entry !== 'object') return null;
    if (entry.selection && typeof entry.selection === 'object') {
      const v = entry.selection.value, keys = v && typeof v === 'object' && !Array.isArray(v) ? Object.keys(v) : [];
      if (keys.length === 1 && HOW[keys[0]] && handleKey(v[keys[0]])) return keys[0] + ':' + handleKey(v[keys[0]]);
      return 'pick:' + (PICK[entry.pick] ? entry.pick : 'parcel') + ':' + hash(JSON.stringify([entry.selection.kind || null, v ?? null]));
    }
    const k = handleKey(entry.handle);
    return k ? (HOW[entry.how] ? entry.how : 'node') + ':' + k : null;
  }
  // A node's full Workflowy name, tags and all: the daemon's first row where it sent rows, else the Markdown's first heading
  // (a node render starts "# ‹its whole name›").
  function nameFrom(held) {
    const row = Array.isArray(held?.rows) && held.rows.length ? held.rows[0] : null;
    let name = typeof row?.name === 'string' && row.name.trim() ? row.name : null;
    if (!name) { const m = /^#[ \t]+(.+)$/m.exec(String(held?.markdown || '')); name = m ? m[1] : null; }
    return name ? middle(name.trim(), NAME_MAX) : null;
  }
  // Keep both ends (including trailing AP-IDs/tags), with a nearby word boundary when it costs at most 24 characters
  // per edge. Unbroken text uses the exact character budget; never split an emoji's surrogate pair.
  function middle(value, max = TITLE_MAX) {
    const chars = Array.from(String(value || '').replace(/\s+/g, ' ').trim());
    if (chars.length <= max) return chars.join('');
    let left = Math.ceil((max - 1) / 2), start = chars.length - Math.floor((max - 1) / 2);
    if (chars[left - 1] !== ' ' && chars[left] !== ' ') {
      for (let i = left - 1; i >= Math.max(1, left - 24); i--) if (chars[i] === ' ') { left = i; break; }
    }
    if (chars[start - 1] !== ' ' && chars[start] !== ' ') {
      for (let i = start; i < Math.min(chars.length - 1, start + 24); i++) if (chars[i] === ' ') { start = i + 1; break; }
    }
    return chars.slice(0, left).join('').trimEnd() + '…' + chars.slice(start).join('').trimStart();
  }
  // Metadata keeps the existing 320-character preview for Filter; only rootText/innerText shorten the displayed copy.
  // A note's first useful line, not a blank, provenance trail, divider, fence or hidden comment. A heading is useful.
  function descriptionLine(note, max = METADATA_DESC_MAX) {
    let comment = false;
    for (let line of String(note || '').split(/\r?\n/)) {
      line = line.trim();
      if (comment) { if (line.includes('-->')) comment = false; continue; }
      if (line.startsWith('<!--')) { comment = !line.includes('-->'); continue; }
      if (!line || /^(?:↩|```|~~~|[-*_]{3,}\s*$)/.test(line)) continue;
      if (/^#{1,6}\s+(?:Task|Purpose|Result|Overview|Description|Contents|Summary|Context|Background|Introduction)\s*$/i.test(line)) continue;
      return middle(line.replace(/^#{1,6}\s+/, '').replace(/^>\s?/, ''), max);
    }
    return '';
  }
  function descriptionFrom(held) {
    const markdown = String(held?.markdown || ''), heading = /^#[ \t]+[^\n]*(?:\n|$)/m.exec(markdown);
    let body = heading ? markdown.slice(heading.index + heading[0].length) : '';
    // Another rendered node's heading is not this root's note. Headings inside a node's own note remain useful.
    if (held?.rows?.length > 1) {
      const next = held.rows[1], lines = body.split('\n'), end = lines.findIndex(l => /^#{1,6}\s+/.test(l) && l.includes(next.name));
      if (end >= 0) body = lines.slice(0, end).join('\n');
    }
    return descriptionLine(body, METADATA_DESC_MAX);
  }
  // For a lens/parcel without one node or group name, its actual selection tells more than the word "Parcel".
  function parcelName(value) {
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return '';
    if (value.tags) return 'Tags · ' + [].concat(value.tags).join(' · ') + (value.root ? ' · ' + value.root : '');
    if (value.symbols) return 'Symbols · ' + [].concat(value.symbols).join(' · ') + (value.root ? ' · ' + value.root : '');
    if (value.group_name) return value.group_name;
    return JSON.stringify(value);
  }
  const str = (v, max = LINE_MAX) => typeof v === 'string' ? v.slice(0, max) : null;
  // A position from the page around (the sidebar's programPosition), reduced to what the cards show and bounded.
  function cleanPosition(pos) {
    if (!pos || typeof pos !== 'object' || !Array.isArray(pos.cards) || !pos.cards.length) return null;
    const program = p => p && typeof p === 'object' ? {key: str(p.key, 80), core: str(p.core, 16), forest: p.forest === true, title: str(p.title),
      kind: p.kind === 'quick' ? 'quick' : p.kind === 'full' ? 'full' : null, managerSession: str(p.managerSession, 8),
      count: Number.isSafeInteger(p.count) ? p.count : null, conductors: Number.isSafeInteger(p.conductors) ? p.conductors : null} : null;
    const line = l => ({id: str(l?.id, 120), key: str(l?.key, 130), role: str(l?.role, 40) || 'Session', handle: str(l?.handle, 60), name: str(l?.name) || '', status: str(l?.status) || ''});
    const cards = pos.cards.slice(0, CARDS_MAX).map(c => ({id: str(c?.id, 120), active: c?.active === true, program: program(c?.program), manager: str(c?.manager, 8),
      chain: Array.isArray(c?.chain) ? c.chain.slice(-8).map(line) : []})).filter(c => c.id && c.chain.length);
    if (!cards.length) return null;
    return {kind: pos.kind === 'program' ? 'program' : 'socket', program: program(pos.program), total: Number.isSafeInteger(pos.total) ? pos.total : cards.length, cards};
  }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  // The conversation turn a handle was tapped in (the page around records it): its session and the row's position,
  // bounded; the page around takes it back to show that turn (opcShowTurn).
  function cleanTurn(t) {
    if (!t || typeof t !== 'object' || !Number.isSafeInteger(t.seq) || !t.session || typeof t.session !== 'object') return null;
    try { const text = JSON.stringify(t); if (text.length > 4096) return null; } catch (_) { return null; }
    const s = t.session;
    return {seq: t.seq, uuid: str(t.uuid, 80), role: str(t.role, 20), kind: ['user', 'assistant', 'thinking', 'tool'].includes(t.kind) ? t.kind : 'assistant',
      call: str(t.call, 120), tool: str(t.tool, 80), part: t.part === 'input' || t.part === 'output' ? t.part : null, time: Number.isFinite(t.time) ? t.time : null,
      session: {kind: s.kind === 'viewer' ? 'viewer' : 'manager', executionId: str(s.executionId, 120), title: str(s.title, 200), label: str(s.label, 40),
        descriptor: s.descriptor && typeof s.descriptor === 'object' ? clone(s.descriptor) : null}};
  }
  // "💬 Manager-Session-208 · assistant · 11:02": which conversation and which turn.
  function turnText(t) {
    const when = t.time ? new Date(t.time * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : null;
    const what = t.kind === 'tool' ? (t.tool ? t.tool + ' ' : 'tool ') + (t.part || 'call') : t.kind === 'user' ? 'your message' : t.kind;
    return '💬 ' + [t.session.title || t.session.label || 'conversation', what, when].filter(Boolean).join(' · ');
  }
  // When a root was last used, for its card: the time today, "yesterday 14:02", a weekday this week, else the date.
  function whenText(seconds, now = Date.now()) {
    if (!Number.isFinite(seconds) || seconds <= 0) return '';
    const at = new Date(seconds * 1000), today = new Date(now), day = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const time = at.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}), days = Math.round((day(today) - day(at)) / 86400000);
    if (days <= 0) return time;
    if (days === 1) return 'yesterday ' + time;
    if (days < 7) return at.toLocaleDateString([], {weekday: 'short'}) + ' ' + time;
    return at.toLocaleDateString([], {month: 'short', day: 'numeric', ...(at.getFullYear() !== today.getFullYear() ? {year: 'numeric'} : {})});
  }
  // How deep an inner history goes, as a card's small badge shows it (brighter the deeper the digging).
  function depthOf(count) { return count <= 1 ? 0 : count <= 4 ? 1 : count <= 9 ? 2 : 3; }
  const utf8 = text => typeof TextEncoder === 'function' ? new TextEncoder().encode(text).length : text.length * 3;

  // @beacon[
  //   id=opc-pane@viewHistory,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Owns View's inner history and shared Recent roots: name and one-line description each use a smart 256-character middle cut; outer cards keep source/digging/time on one truncated action-row line and grow around names and Dan's notes. Inner cards share the caps and the strip stays fixed-height. Full available metadata remains searchable; render/picker facts and asynchronous cache-only enrichment never change notes or digging. Navigation, root pop-up, depth badges, note buttons and ordered saves remain shared by desktop and phone.,
  // ]
  function create({doc = document, storageKey, render, position = null, reveal = null, showTurn = null, revealPlace = 'Programs', roots = null, device = null, note = null}) {
    const $ = id => doc.getElementById('g-' + id);
    const state = {entries: [], cursor: 0, n: 0, lookups: 0};
    const pendingNames = new Set(), described = new Set();
    const storage = () => { try { return doc.defaultView?.localStorage || null; } catch (_) { return null; } };
    const el = (tag, cls, text) => { const n = doc.createElement(tag); if (cls) n.className = cls; if (text !== undefined && text !== null) n.textContent = text; return n; };
    const root = () => state.entries[0] || null, current = () => state.entries[state.cursor] || null;
    // ---- storage: bounded, validated on the way in; a failure only costs the history, never the page
    const wire = e => ({...e, lookup: e.lookup === 'pending' ? 'pending' : e.lookup || null});
    function save() {
      const store = storage(); if (!store || !storageKey) return;
      try {
        const data = {v: VERSION, n: state.n, cursor: state.cursor, entries: state.entries.map(wire)};
        let text = JSON.stringify(data);
        while (text.length > STORE_MAX && data.entries.length > 2) { data.entries.splice(1, 1); data.cursor = Math.max(0, data.cursor - 1); text = JSON.stringify(data); }
        store.setItem(storageKey, text); state.stored = text;
      } catch (_) { /* a full or blocked storage keeps the history for this page only */ }
    }
    function cleanEntry(e, first) {
      if (!e || typeof e !== 'object' || !Number.isSafeInteger(e.id)) return null;
      const metadata = {name: middle(e.name || e.selection?.name, NAME_MAX) || null, description: str(e.description ?? e.selection?.description, METADATA_DESC_MAX), metadataVersion: e.metadataVersion === 1 ? 1 : null};
      if (first && e.selection && typeof e.selection === 'object' && PICK[e.pick] && typeof e.selection.label === 'string')
        return {id: e.id, root: true, kind: 'pick', pick: e.pick, selection: e.selection, label: str(e.selection.label, NAME_MAX), from: 'view', ...metadata};
      if (typeof e.handle !== 'string' || !e.handle.trim() || e.handle.length > 200) return null;
      const out = {id: e.id, handle: e.handle.slice(0, 200), how: HOW[e.how] ? e.how : 'node', ...metadata};
      if (!first) return out;
      const position = cleanPosition(e.position);
      const turn = cleanTurn(e.turn);
      return {...out, root: true, kind: turn ? 'turn' : position ? position.kind : 'node', from: str(e.from, 20) || 'outside', where: str(e.where, 40), active: str(e.active, 120),
        position, turn, lookup: e.lookup === 'pending' ? 'pending' : position || e.lookup === 'done' ? 'done' : null};
    }
    function load() {
      try {
        const raw = storage()?.getItem(storageKey); state.stored = raw ?? null; if (!raw) return false;
        const data = JSON.parse(raw);
        if (!data || data.v !== VERSION || !Array.isArray(data.entries) || !data.entries.length) return false;
        const entries = data.entries.slice(0, MAX_ENTRIES).map((e, i) => cleanEntry(e, i === 0));
        if (entries.some(e => !e)) return false;
        state.entries = entries; state.cursor = Number.isSafeInteger(data.cursor) && data.cursor >= 0 && data.cursor < entries.length ? data.cursor : entries.length - 1;
        state.n = Math.max(Number.isSafeInteger(data.n) ? data.n : 0, ...entries.map(e => e.id));
        return true;
      } catch (_) { return false; }
    }

    // ---- the root's program position: asked of the page around this frame (the sidebar's tree), never read here
    function apply(entry, pos) {
      if (state.entries[0] !== entry) return;
      entry.position = cleanPosition(pos);
      entry.kind = entry.turn ? 'turn' : entry.position ? entry.position.kind : 'node';
      entry.lookup = 'done'; save(); queueSave(); paint();
    }
    function lookup(entry, given = null) {
      if (given) return apply(entry, given);
      if (typeof position !== 'function') { entry.lookup = null; return; }
      entry.lookup = 'pending'; paint();
      const seq = ++state.lookups;
      Promise.resolve().then(() => position(entry.handle, entry.active)).then(pos => { if (seq === state.lookups) apply(entry, pos); },
        () => { if (seq === state.lookups) apply(entry, null); });
    }

    // ---- the inner history itself
    function reroot(spec) {
      const origin = spec.origin || {};
      const entry = spec.selection
        ? {id: ++state.n, root: true, kind: 'pick', pick: PICK[spec.pick] ? spec.pick : 'parcel', selection: clone(spec.selection), label: spec.selection.label, from: 'view',
          name: middle(spec.selection.name, NAME_MAX) || null, description: descriptionLine(spec.selection.description, METADATA_DESC_MAX), metadataVersion: spec.selection.metadataVersion === 1 ? 1 : null}
        : {id: ++state.n, root: true, kind: 'node', handle: String(spec.handle || '').trim(), how: HOW[spec.how] ? spec.how : 'node', name: null,
          from: str(origin.from, 20) || 'outside', where: str(origin.where, 40), active: str(origin.active, 120), position: null, lookup: null, turn: cleanTurn(origin.turn)};
      if (entry.turn) entry.kind = 'turn';
      pendingNames.add(entry.id);
      void flushSave();  // the root leaving: its last change goes to Recent roots first
      state.entries = [entry]; state.cursor = 0; closeAll(); save(); paint();
      if (!entry.selection) lookup(entry, origin.position || null);
      visit(entry);
      return render(entry);
    }
    function push(handle, how = 'node') {
      const h = String(handle || '').trim(), now = current();
      if (!now) return reroot({handle: h, how, origin: {from: 'view'}});
      if (now.handle && handleKey(now.handle) && handleKey(now.handle) === handleKey(h) && (now.how || 'node') === how) { closeAll(); return render(now); }
      const entry = {id: ++state.n, handle: h, how: HOW[how] ? how : 'node', name: null};
      state.entries.splice(state.cursor + 1); state.entries.push(entry);
      while (state.entries.length > MAX_ENTRIES) state.entries.splice(1, 1);
      state.cursor = state.entries.length - 1; closeAll(); save(); queueSave(); paint();
      return render(entry);
    }
    function go(index) {
      const entry = state.entries[index]; if (!entry) return Promise.resolve(false);
      state.cursor = index; closeAll(); save(); queueSave(); paint();
      return render(entry);
    }
    function rendered(id, held) {
      const entry = state.entries.find(e => e.id === id);
      if (!entry) return;
      pendingNames.delete(id);
      const v = entry.selection?.value;
      const concrete = !entry.selection || (v && typeof v === 'object' && ['node', 'subtree', 'dock'].some(k => v[k]));
      // Group and estate render headings can be wrappers or a source-map ancestor, never their own real name.
      const name = concrete ? nameFrom(held) : null;
      if (name && (!entry.selection?.name || !entry.name)) entry.name = name;
      const full = held?.request?.options?.headings_only === false && !held?.selection?.focus;
      if (concrete && full) {
        const description = descriptionFrom(held), bodyOnly = !entry.selection ? entry.how === 'node' : !!v?.node;
        // Broad/chronological views can omit the root note. Never erase a picker's description with that omission.
        if (description || bodyOnly) entry.description = description;
        if (description || bodyOnly) entry.metadataVersion = 1;
      }
      save(); queueSave(); paint(); scheduleDescriptions();
    }
    function restore() {
      if (!load()) return null;
      const first = root();
      if (first && !first.selection && first.lookup === 'pending') lookup(first);
      paint();
      return current();
    }
    // Two Views of one page context share its key (the desktop's View pane and its OpenCode-mode sheet): when this one is
    // shown again, a history the other changed since this one last read or wrote it is taken up; else nothing changes.
    function sync() {
      let raw = null;
      try { raw = storage()?.getItem(storageKey) ?? null; } catch (_) { return null; }
      if (!raw || raw === state.stored) return null;
      if (!load()) return null;
      closeAll(); paint();
      return current();
    }

    // ---- Recent roots (the outer history): the pane server's list through the store glimpse.js hands in. Every write
    // goes out in order (one chain), each answered with the list; a root becoming current is a visit, a change to its
    // inner history a save (debounced), a card picked from the list an open whose inner history is adopted.
    const outer = {status: roots ? 'loading' : 'off', list: [], limit: 30, limits: [10, 20, 30, 50, 100], notedMax: 100, error: '', note: '',
      chain: Promise.resolve(), gen: 0, reads: 0, timer: null, pending: false, visiting: null, dirty: false, opening: null, filter: '', editing: null};
    // A card from the list, checked as the inner history checks its own root.
    function cleanCard(c) {
      if (!c || typeof c !== 'object' || typeof c.key !== 'string' || !c.key || c.key.length > 80) return null;
      const first = cleanEntry({...(c.root && typeof c.root === 'object' ? c.root : {}), id: 0}, true);
      if (!first) return null;
      const count = Number.isSafeInteger(c.count) && c.count > 0 ? Math.min(c.count, MAX_ENTRIES) : 1;
      const brief = e => e && typeof e === 'object' && typeof e.handle === 'string' && e.handle.trim() ? {handle: e.handle.slice(0, 200), how: HOW[e.how] ? e.how : 'node', name: str(e.name, NAME_MAX)} : null;
      const time = v => Number.isFinite(v) ? v : null;
      return {key: c.key, root: first, count, cursor: Number.isSafeInteger(c.cursor) ? Math.min(Math.max(0, c.cursor), count - 1) : 0, at: brief(c.at), last: brief(c.last),
        note: typeof c.note === 'string' ? c.note.slice(0, NOTE_MAX) : '', device: DEVICE[c.device] ? c.device : null,
        created: time(c.created), visited: time(c.visited), saved: time(c.saved)};
    }
    // One reply from the store: the list (a change's answer, or a read), the route missing (a pane server from before it:
    // its restart), or a failure kept beside what is listed.
    function accept(reply, gen = null) {
      const data = reply?.data;
      let enriched = false;
      if (data && data.schemaVersion === 1 && Array.isArray(data.roots)) {
        if (gen !== null && gen !== outer.gen) return false;  // a write answered since this read went out: its list is newer
        outer.gen++;
        outer.list = data.roots.map(cleanCard).filter(Boolean);
        const fresh = outer.list.find(c => c.key === rootKey(root()))?.root;
        if (fresh) enriched = mergeMetadata(root(), fresh);
        if (Array.isArray(data.limits) && data.limits.length && data.limits.every(Number.isSafeInteger)) outer.limits = data.limits;
        if (Number.isSafeInteger(data.limit)) outer.limit = data.limit;
        if (Number.isSafeInteger(data.notedMax)) outer.notedMax = data.notedMax;
        outer.status = 'ready'; outer.error = reply.ok === false && data.status !== 'missing' ? String(data.error || 'HTTP ' + reply.status) : '';
        if (data.status === 'missing') outer.note = 'That root is no longer in the list (removed, or past the limit on another device).';
      } else if (!data && (reply?.status === 404 || reply?.status === 405)) {
        outer.status = 'missing'; outer.error = '';
      } else {
        if (outer.status !== 'ready') outer.status = 'error';
        outer.error = String(data?.error || reply?.error || (reply?.status ? 'HTTP ' + reply.status : 'the pane server did not answer'));
      }
      if (enriched) { save(); paint(); } else paintOuter();
      scheduleDescriptions();
      return true;
    }
    function mergeMetadata(target, fresh) {
      if (!target || target.metadataVersion === 1 || fresh?.metadataVersion !== 1) return false;
      if (fresh.name) target.name = middle(fresh.name, NAME_MAX);
      target.description = str(fresh.description, METADATA_DESC_MAX) || ''; target.metadataVersion = 1;
      return true;
    }
    // Metadata is independent of the ordered visit/save chain: a slow daemon never holds up a View render or a note.
    // A reply contributes metadata only, never its older order/cursor/notes. Failed/missing roots keep their old words.
    // @beacon[
    //   id=opc-pane@viewHistoryDescribe,
    //   slice_labels=opc-pane,
    //   kind=ast,
    //   comment=Backfills missing root metadata in independent batches of at most 12; waits for an already-started render to supply its own facts, merges only name/description/version, and attempts a missing root only once per page. Never queues behind or ahead of visits/saves, restores an old list, or renders a card.,
    // ]
    function scheduleDescriptions() {
      if (!roots?.describe || outer.status !== 'ready' || outer.describing) return;
      const candidates = () => cards().filter(c => outer.list.some(listed => listed.key === c.key) && c.root.metadataVersion !== 1 && !described.has(c.key) && !pendingNames.has(c.root.id)).slice(0, 12);
      if (!candidates().length) return;
      outer.describing = Promise.resolve().then(async () => {
        for (;;) {
          const batch = candidates();
          if (!batch.length) break;
          const keys = batch.map(c => c.key); keys.forEach(k => described.add(k));
          let reply; try { reply = await roots.describe(keys); } catch (_) { continue; }
          if (!reply?.ok || !Array.isArray(reply.data?.roots)) continue;
          let changed = false;
          for (const fresh of reply.data.roots) {
            if (mergeMetadata(outer.list.find(c => c.key === fresh.key)?.root, fresh.root)) changed = true;
            if (rootKey(root()) === fresh.key && mergeMetadata(root(), fresh.root)) changed = true;
          }
          if (changed) { save(); paint(); }
        }
      }).finally(() => { outer.describing = null; scheduleDescriptions(); });
    }
    async function readOuter() {
      if (!roots) return null;
      const gen = outer.gen, seq = ++outer.reads;
      let reply;
      try { reply = await roots.read(); } catch (e) { reply = {ok: false, status: 0, data: null, error: e.message}; }
      if (seq !== outer.reads) return null;
      accept(reply, gen);
      return reply;
    }
    function writeOuter(change, options = {}) {
      if (!roots || outer.status === 'missing') return Promise.resolve(null);
      const send = () => Promise.resolve().then(() => roots.write({schemaVersion: 1, ...change}, options))
        .catch(e => ({ok: false, status: 0, data: null, error: e.message}))
        .then(reply => { accept(reply); return reply; });
      outer.chain = outer.chain.then(send, send);
      return outer.chain;
    }
    // This page's inner history as Recent roots keeps it (bounded as the page's own store is, and in bytes for the server).
    function snapshot() {
      const first = root(), key = first ? rootKey(first) : null; if (!key) return null;
      const entries = state.entries.map(wire); let cursor = state.cursor;
      while (entries.length > 2 && utf8(JSON.stringify(entries)) > OUTER_BYTES) { entries.splice(1, 1); cursor = Math.max(0, cursor - 1); }
      return {key, entries, cursor: Math.min(cursor, entries.length - 1)};
    }
    function queueSave() {
      if (!roots || outer.status === 'missing' || !root()) return;
      outer.pending = true;
      if (outer.visiting) { outer.dirty = true; return; }  // after the visit's answer, which may bring the root's digging back
      clearTimeout(outer.timer); outer.timer = setTimeout(() => void flushSave(), SAVE_MS);
    }
    function flushSave(keepalive = false) {
      clearTimeout(outer.timer); outer.timer = null;
      if (!outer.pending) return Promise.resolve(null);
      outer.pending = false;
      const snap = snapshot();
      return snap ? writeOuter({op: 'save', ...snap, ...(device ? {device} : {})}, {keepalive}) : Promise.resolve(null);
    }
    // A root becoming current: listed again on top; a root already listed brings its digging back, ahead of the root
    // shown (only while the inner history is still that root alone: a tap meanwhile wins).
    function visit(entry) {
      const key = rootKey(entry); if (!roots || !key || outer.status === 'missing') return;
      outer.visiting = entry; outer.note = '';
      void writeOuter({op: 'visit', key, root: wire(entry), ...(device ? {device} : {})}).then(reply => {
        if (outer.visiting === entry) outer.visiting = null;
        const opened = reply?.data?.opened;
        if (opened?.key === key && state.entries[0] === entry && state.entries.length === 1 && Array.isArray(opened.entries)) {
          const later = opened.entries.slice(1, MAX_ENTRIES).map(e => cleanEntry(e, false)).filter(Boolean);
          if (later.length) { state.entries.push(...later.map(e => ({...e, id: ++state.n}))); save(); paint(); }
        }
        if (outer.dirty) { outer.dirty = false; queueSave(); }
      });
    }
    // A card picked from the list: that root's whole inner history, at its cursor, rendered.
    async function select(key) {
      const first = root();
      if (first && rootKey(first) === key) { closeRoots(); return true; }
      void flushSave();
      outer.opening = key; outer.note = ''; paintOuter();
      const reply = await writeOuter({op: 'open', key, ...(device ? {device} : {})});
      if (outer.opening === key) outer.opening = null;
      const opened = reply?.data?.opened;
      if (!opened || opened.key !== key || !Array.isArray(opened.entries)) { paintOuter(); return false; }
      return adopt(opened);
    }
    function adopt(opened) {
      const entries = opened.entries.slice(0, MAX_ENTRIES), first = cleanEntry(entries[0], true);
      if (!first) { outer.error = 'That root could not be read back.'; paintOuter(); return Promise.resolve(false); }
      state.entries = [first, ...entries.slice(1).map(e => cleanEntry(e, false)).filter(Boolean)].map(e => ({...e, id: ++state.n}));
      state.cursor = Math.min(Math.max(0, Number.isSafeInteger(opened.cursor) ? opened.cursor : 0), state.entries.length - 1);
      closeAll(); save(); paint();
      if (!first.selection && first.lookup === 'pending') lookup(state.entries[0]);
      return render(current());
    }
    function startNote(key) {
      const card = cards().find(c => c.key === key); if (!card) return;
      outer.editing = {key, text: card.note || ''}; paintPanel(true);
      const area = $('roots-list')?.querySelector?.('textarea');
      area?.focus?.(); try { area?.setSelectionRange?.(area.value.length, area.value.length); } catch (_) { /* the caret where it lands */ }
    }
    async function saveNote() {
      const editing = outer.editing; if (!editing) return false;
      // A note on the root on screen before its visit was answered: listed first, then noted.
      if (!outer.list.some(c => c.key === editing.key)) { const snap = snapshot(); if (snap?.key === editing.key) await writeOuter({op: 'save', ...snap, ...(device ? {device} : {})}); }
      const reply = await writeOuter({op: 'note', key: editing.key, note: editing.text.slice(0, NOTE_MAX)});
      if (reply?.ok !== false && reply?.data?.schemaVersion === 1) { if (outer.editing === editing) outer.editing = null; paintPanel(true); return true; }
      paintPanel(); return false;
    }
    function cancelNote() { outer.editing = null; paintPanel(true); }
    async function removeRoot(key) { await writeOuter({op: 'remove', key}); paintPanel(true); }
    function setLimit(limit) { return writeOuter({op: 'limit', limit: Number(limit)}); }
    // The cards as shown: the root on screen first (its count, place and name from this page, newer than the list's),
    // then the list's order (most recently used first).
    function cards() {
      const first = root(), key = first ? rootKey(first) : null;
      const listed = outer.list.find(c => c.key === key) || null, rest = outer.list.filter(c => c.key !== key);
      if (!key) return rest;
      const here = {...(listed || {note: '', device: DEVICE[device] ? device : null}), key, root: first, count: state.entries.length, cursor: state.cursor,
        at: state.cursor ? current() : null, last: state.entries.length > 1 ? state.entries.at(-1) : null, here: true};
      return [here, ...rest];
    }

    // ---- what the cards say
    // live: the root on screen, perhaps still looking itself up; a card in Recent roots says only what it knows.
    function rootText(entry, live = true) {
      const description = middle(entry.description || '', DESC_MAX);
      if (entry.selection) return {title: middle(entry.name || entry.selection.name || entry.label?.replace(/^(Dock|Group|Estate|Parcel) · /, '') || parcelName(entry.selection.value)),
        sub: description, source: middle('picked in View · ' + (PICK[entry.pick] || 'Spec') + ' · ' + (entry.label || '') + ' · ' + parcelName(entry.selection.value), 1024)};
      const h = shortHandle(entry.handle), pos = entry.position;
      let source = [HOW[entry.how] || 'Node', h, whereText(entry)].filter(Boolean).join(' · '), fallback = h;
      if (entry.turn) source = turnText(entry.turn) + ' · ' + source;
      if (pos?.kind === 'program') {
        const p = pos.program || pos.cards[0]?.program || {};
        fallback = p.title || h;
        source += ' · Program · ' + [p.title, (p.conductors || pos.total) + ' Conductor' + ((p.conductors || pos.total) === 1 ? '' : 's')].filter(Boolean).join(' · ');
      } else if (pos) {
        const card = pos.cards.find(c => c.active) || pos.cards[0], leaf = card.chain.at(-1);
        fallback = leaf.name || card.program?.title || h;
        source += ' · ' + leaf.role + (pos.total > 1 ? ' · ' + pos.total + ' sessions' : '') + (leaf.name ? ' · ' + leaf.name : '');
      }
      if (!entry.name && live && pendingNames.has(entry.id)) source += ' · rendering…';
      return {title: middle(entry.name || fallback), sub: description, source: middle(source, 1024)};
    }
    function innerText(entry) { return {title: middle(entry.name || shortHandle(entry.handle)), sub: middle(entry.description || '', DESC_MAX), source: (HOW[entry.how] || 'Node') + ' · ' + shortHandle(entry.handle) + ' · opened in View'}; }
    function entryText(entry, index) {
      const t = index === 0 ? rootText(entry) : innerText(entry);
      return [t.title, t.sub, t.source].filter(Boolean).join(' · ');
    }
    function whereText(entry) {
      return {programs: 'tapped in ' + revealPlace, outside: 'tapped' + (entry.where ? ' in ' + entry.where : ''), pasted: 'pasted', position: 'opened from a position card', view: 'opened in View'}[entry.from] || 'opened';
    }
    // Where a root's digging stands: the entry it was left at, or (back at the root) the last one it reached.
    function digText(card) {
      const name = e => middle(e.name || shortHandle(e.handle));
      if (card.cursor > 0 && card.at) return '↳ at ' + (card.cursor + 1) + '/' + card.count + ' · ' + name(card.at);
      if (card.count > 1 && card.last) return '↳ at the root · it went on to ' + name(card.last);
      return '';
    }

    // ---- the strip: row 1 the root widget (with where and ⤢ the whole inner history), row 2 ‹ the current entry › with
    // maximize at its right (glimpse.js wires it), row 3 Recent roots; shown with a root, or row 3 alone when nothing is
    // shown yet and the list has a root to go back to
    function paint() {
      const strip = $('hist'); if (!strip) return;
      const first = root();
      paintOuter();  // row 3, and whether the strip shows at all
      $('hist-row').hidden = !first; $('hist-nav').hidden = !first;
      if (!first) { closeInner(); return; }
      const t = rootText(first), card = $('root');
      card.replaceChildren(el('span', 'g-hcard-title', t.title), el('span', 'g-hcard-sub', [t.sub, t.source].filter(Boolean).join(' · ')));
      card.dataset.kind = first.kind; card.setAttribute('aria-current', String(state.cursor === 0));
      card.title = [t.title, t.sub, t.source, 'Tap: where this root sits, and Show'].filter(Boolean).join('\n');
      $('hist-count').textContent = String(state.entries.length);
      const now = current();
      $('back').disabled = state.cursor === 0; $('fwd').disabled = state.cursor >= state.entries.length - 1;
      const label = (state.cursor + 1) + '/' + state.entries.length + ' · ' + entryText(now, state.cursor);
      $('now').textContent = label; $('now').title = label + '\nTap: this root\'s whole inner history';
      if (!$('hist-sheet').hidden) paintSheet();
      if (!$('pos').hidden) paintPosition();
    }
    // Row 3: 🕘 Recent roots · N ▾ (the list drops down); a pane server from before the list says it needs its restart.
    function paintOuter() {
      const row = $('roots'); if (!row) return;
      const first = root(), key = first ? rootKey(first) : null;
      const count = outer.list.length + (key && !outer.list.some(c => c.key === key) ? 1 : 0);
      row.hidden = outer.status === 'off' || (!first && !outer.list.length);
      row.dataset.status = outer.status;
      $('hist').hidden = !first && row.hidden;
      $('roots-count').textContent = outer.status === 'missing' ? 'need the pane server restarted once' : outer.status === 'loading' ? '· …' :
        outer.status === 'error' ? '· ⚠ unavailable' : '· ' + count;
      const open = !$('roots-panel').hidden;
      $('roots-open').setAttribute('aria-expanded', String(open));
      $('roots-open').title = outer.status === 'missing' ? 'Recent roots need the pane server restarted once (bash transcription-pane/start.sh restart); the inner history works as before.' :
        'Recent roots (the outer history): the roots you opened lately on the phone and the desktop, each with its own inner history and your note. Tap one to go back to where its digging went.';
      if (open) paintPanel();
    }
    // ---- the sheet: every entry as a card; the one showing marked, the ones ahead of it dimmed
    function paintSheet() {
      const list = $('hist-list'); list.replaceChildren();
      $('hist-title').textContent = 'Inner history · ' + state.entries.length;
      state.entries.forEach((entry, index) => {
        const b = el('button', 'g-hcard' + (index === 0 ? ' g-hroot' : ''));
        b.type = 'button'; b.dataset.index = String(index);
        const tag = index === 0 ? '⌂ root' : String(index + 1);
        const state2 = index === state.cursor ? ' · showing' : index > state.cursor ? ' · ahead' : '';
        b.append(el('span', 'g-hcard-n', tag + state2));
        const t = index === 0 ? rootText(entry) : innerText(entry);
        b.append(el('span', 'g-hcard-title', t.title));
        if (t.sub) b.append(el('span', 'g-hcard-sub', t.sub));
        b.append(el('span', 'g-hcard-source', t.source));
        b.title = [t.title, t.sub, t.source].filter(Boolean).join('\n');
        if (index === state.cursor) b.setAttribute('aria-current', 'true');
        if (index > state.cursor) b.dataset.ahead = 'true';
        list.append(b);
      });
    }
    // ---- Recent roots, dropped down from row 3 over the document: one card per root, the root widget's own words, a
    // badge for digging depth and Dan's note/editor. Source, digging and when/device share ONE truncated line beside
    // the action buttons; its complete text stays in the DOM and on hover, not several competing fine-print rows.
    function depthBadge(count) {
      const b = el('span', 'g-rdepth', String(count));
      b.dataset.depth = String(depthOf(count));
      b.title = count === 1 ? 'Just the root: no handle opened from it yet' : count + ' entries in its inner history: the root and ' + (count - 1) + ' handle' + (count === 2 ? '' : 's') + ' opened from it';
      b.setAttribute('aria-label', b.title);
      return b;
    }
    function noteEditor() {
      const box = el('div', 'g-rnote'), area = el('textarea', 'g-rnote-text'), row = el('div', 'g-rnote-row');
      area.rows = 3; area.maxLength = NOTE_MAX; area.value = outer.editing.text; area.setAttribute('aria-label', 'Your note on this root');
      area.placeholder = 'Why this root matters (the grouped screenshots, the bake-off, …)';
      area.addEventListener('input', () => { if (outer.editing) outer.editing.text = area.value; });
      const save = el('button', 'g-go', 'Save note'), cancel = el('button', null, 'Cancel');
      save.type = cancel.type = 'button'; save.dataset.act = 'save'; cancel.dataset.act = 'cancel';
      row.append(save, cancel); box.append(area, row);
      return box;
    }
    function cardNode(card) {
      const t = rootText(card.root, false), box = el('div', 'g-hcard g-hroot g-rcard');
      box.dataset.key = card.key; box.dataset.kind = card.root.kind; box.setAttribute('role', 'button'); box.tabIndex = 0;
      if (card.here) box.setAttribute('aria-current', 'true');
      if (outer.opening === card.key) box.dataset.busy = 'true';
      const head = el('div', 'g-rcard-head');
      head.append(el('span', 'g-hcard-title', t.title), depthBadge(card.count));
      box.append(head);
      if (t.sub) box.append(el('span', 'g-hcard-sub', t.sub));
      const editing = outer.editing?.key === card.key;
      if (editing) box.append(noteEditor());
      else if (card.note) box.append(el('span', 'g-rcard-note', '📝 ' + card.note));
      const foot = el('div', 'g-rcard-foot');
      const meta = [t.source, digText(card), card.here ? 'showing now' : outer.opening === card.key ? 'opening…' : null, DEVICE[card.device] || null,
        card.here ? null : whenText(Math.max(card.visited || 0, card.saved || 0))].filter(Boolean).join(' · ');
      const source = el('span', 'g-hcard-source', meta); source.title = meta; foot.append(source);
      if (!editing) {
        const edit = el('button', 'g-rcard-act', card.note ? '📝 Edit note' : '📝 Note'); edit.type = 'button'; edit.dataset.act = 'note';
        edit.title = 'A note on why this root matters; a root with a note stays in the list'; foot.append(edit);
        if (!card.here) {
          const x = el('button', 'g-rcard-act g-rcard-x', '✕'); x.type = 'button'; x.dataset.act = 'remove';
          x.title = 'Remove from Recent roots (the node itself is untouched)'; x.setAttribute('aria-label', x.title); foot.append(x);
        }
      }
      box.append(foot);
      return box;
    }
    const cardWords = c => { const t = rootText(c.root, false); return [c.root.name, c.root.description, t.title, t.sub, t.source, digText(c), c.note].join('\n').toLowerCase(); };
    // force: rebuild the cards even while a note is being typed (only Save, Cancel or the filter do that); otherwise a
    // note in progress keeps its box, and only the head, status and limit change under the finger.
    function paintPanel(force = false) {
      const list = $('roots-list'); if (!list) return;
      const all = cards(), q = outer.filter.trim().toLowerCase(), shown = q ? all.filter(c => cardWords(c).includes(q)) : all;
      $('roots-title').textContent = 'Recent roots · ' + all.length;
      const typing = outer.editing && list.querySelector?.('textarea') && !force;
      if (!typing) list.replaceChildren(...shown.map(cardNode));
      $('roots-status').textContent = outer.status === 'missing' ? 'Recent roots need the pane server restarted once (bash transcription-pane/start.sh restart). Until then View keeps only the inner history, as before.'
        : outer.status === 'loading' && !all.length ? 'Reading Recent roots…' : outer.error ? '⚠ ' + outer.error
        : outer.note ? outer.note : q && !shown.length ? 'Nothing matches “' + outer.filter.trim() + '”.' : !all.length ? 'No roots yet: whatever you open in View lands here.' : '';
      const select = $('roots-limit');
      if (select && select.dataset.limits !== outer.limits.join()) {
        select.replaceChildren(...outer.limits.map(n => { const o = el('option', null, String(n)); o.value = String(n); return o; }));
        select.dataset.limits = outer.limits.join();
      }
      if (select) select.value = String(outer.limit);
      $('roots-keep').textContent = 'roots · a root with a note stays (up to ' + outer.notedMax + ' more)';
    }
    function openRoots() {
      if (outer.status === 'off') return;
      closeInner();
      const panel = $('roots-panel'), row = $('roots').getBoundingClientRect?.();
      panel.style?.setProperty?.('--g-roots-top', Math.round((row?.bottom || 0) + 4) + 'px');
      outer.filter = ''; outer.editing = null; outer.note = ''; $('roots-filter').value = '';
      panel.hidden = false; paintOuter();
      void readOuter();  // the list again: the other device may have added to it
    }
    function closeRoots() { const panel = $('roots-panel'); if (panel) panel.hidden = true; outer.editing = null; paintOuter(); }
    // ---- the root's pop-up: what the root is; for a socket, the sidebar's tree pruned to its sessions
    function positionLine(line, depth, leaf) {
      const row = el('div', 'g-pos-line' + (leaf ? ' g-pos-leaf' : ''));
      if (line.id) row.dataset.posId = line.id;
      if (line.key && typeof reveal === 'function') { row.dataset.reveal = line.key; row.setAttribute('role', 'button'); row.tabIndex = 0; }
      row.style.setProperty('--depth', String(depth));
      row.append(el('span', 'g-pos-role', (depth ? '└ ' : '') + line.role));
      if (line.handle) row.append(' ', el('span', 'g-pos-handle', line.handle));
      if (line.name) row.append(' ', el('span', 'g-pos-name', line.name));
      if (leaf && line.status) row.append(el('span', 'g-pos-status', line.status));
      return row;
    }
    function programLine(p) {
      const row = el('div', 'g-pos-program');
      if (p.forest) { row.append(el('span', 'g-pos-role', 'Quick workers and helpers')); return row; }
      if (p.key && typeof reveal === 'function') { row.dataset.reveal = p.key; row.setAttribute('role', 'button'); row.tabIndex = 0; }
      row.append(el('span', 'g-pos-role', 'Program'), ' ', el('span', 'g-pos-handle', '[ap:' + p.core + ']'), ' ', el('span', 'g-pos-name', p.title || ''));
      const sub = [p.kind ? p.kind + ' program' : null, p.managerSession ? 'from Manager-Session-' + p.managerSession : null,
        Number.isSafeInteger(p.count) ? p.count + ' session' + (p.count === 1 ? '' : 's') : null].filter(Boolean).join(' · ');
      if (sub) row.append(el('span', 'g-pos-status', sub));
      return row;
    }
    function paintPosition() {
      const first = root(), body = $('pos-body'); if (!first || !body) return;
      body.replaceChildren();
      const h = first.selection ? null : shortHandle(first.handle), pos = first.selection ? null : first.position;
      $('pos-title').textContent = first.selection ? PICK[first.pick] || 'Spec' : pos?.kind === 'program' ? 'Program ' + h + ' · ' + pos.total + ' Conductor' + (pos.total === 1 ? '' : 's') :
        pos ? 'Where ' + h + ' sits' + (pos.total > 1 ? ' · ' + pos.total + ' sessions' : '') : h + (first.how !== 'node' ? ' · ' + HOW[first.how] : '');
      $('pos-show').textContent = 'Show ' + (first.selection ? 'it' : h);
      $('pos-turn').hidden = !(first.turn && typeof showTurn === 'function');
      if (first.turn) {
        $('pos-title').textContent = turnText(first.turn);
        body.append(el('div', 'g-pos-status', 'tapped in ' + (first.turn.session.kind === 'manager' ? 'the Manager\'s conversation' : first.turn.session.label + ': ' + (first.turn.session.title || '')) +
          ' · ' + (first.turn.kind === 'tool' ? 'a tool call' + (first.turn.part ? '\'s ' + first.turn.part : '') : first.turn.kind === 'user' ? 'a message of yours' : 'its ' + first.turn.kind)));
        body.append(el('div', 'g-pos-handle g-pos-big', h), el('div', 'g-pos-name g-pos-big', first.name || 'rendering…'));
        if (!pos) return;
      }
      if (pos) {
        let last = '';
        for (const card of pos.cards) {
          const p = card.program || {}, key = p.forest ? 'forest' : p.key || '';
          if (key !== last) { body.append(programLine(p)); last = key; }
          const box = el('div', 'g-pos-card' + (card.active ? ' g-pos-active' : ''));
          box.dataset.posCard = card.id;
          if (card.active) box.append(el('span', 'g-pos-here', '● you came from here'));
          card.chain.forEach((line, i) => box.append(positionLine(line, i, i === card.chain.length - 1)));
          if (card.manager && p.forest) box.append(el('span', 'g-pos-status', 'for Manager-Session-' + card.manager));
          body.append(box);
        }
        if (pos.total > pos.cards.length) body.append(el('div', 'g-pos-more', '… ' + (pos.total - pos.cards.length) + ' more ' + (pos.kind === 'program' ? 'Conductors' : 'sessions') + ' in ' + revealPlace));
        body.append(el('div', 'g-pos-hint', 'Tap a handle: a new root there.' + (typeof reveal === 'function' ? ' Tap a line: show it in ' + revealPlace + '.' : '')));
        return;
      }
      if (first.selection) {
        body.append(el('div', 'g-pos-name g-pos-big', first.label || ''));
        const v = first.selection.value;
        body.append(el('div', 'g-pos-status', 'chosen in View · ' + (typeof v === 'string' ? v : JSON.stringify(v)).slice(0, LINE_MAX)));
        return;
      }
      body.append(el('div', 'g-pos-handle g-pos-big', h));
      body.append(el('div', 'g-pos-name g-pos-big', first.name || 'rendering…'));
      body.append(el('div', 'g-pos-status', whereText(first) + (first.lookup === 'pending' ? ' · looking up its place in ' + revealPlace + '…' :
        typeof position === 'function' ? ' · not a listed session\'s socket' : '')));
    }
    function openRoot() {
      const first = root(); if (!first) return;
      closeRoots();
      $('pos').hidden = false; paintPosition();
      // The cards are a snapshot: the sidebar's tree may know more by now (a later page, a new leg).
      if (!first.selection && typeof position === 'function' && first.lookup !== 'pending') {
        const seq = ++state.lookups;
        Promise.resolve().then(() => position(first.handle, first.active)).then(pos => {
          if (seq !== state.lookups || state.entries[0] !== first || !cleanPosition(pos)) return;
          apply(first, pos);
        }, () => {});
      }
    }
    function openSheet() { closeRoots(); $('hist-sheet').hidden = false; paintSheet(); }
    function closeInner() { const s = $('hist-sheet'), p = $('pos'); if (s) s.hidden = true; if (p) p.hidden = true; }
    function closeAll() { closeInner(); closeRoots(); }
    function revealLine(node) {
      const key = node?.closest?.('[data-reveal]')?.dataset.reveal;
      if (!key || typeof reveal !== 'function') return false;
      closeAll(); void reveal(key); return true;
    }
    // Show in conversation: the page around shows the turn; a turn it cannot show here (tapped on the other device, or
    // its conversation no longer listed) says so instead of doing nothing.
    function showRootTurn() {
      const t = root()?.turn; if (!t || typeof showTurn !== 'function') return;
      closeAll();
      let shown;
      try { shown = showTurn(clone(t)); } catch (e) { shown = Promise.reject(e); }
      Promise.resolve(shown).then(ok => {
        if (ok === false) note?.('That conversation turn cannot be shown here (it may have been tapped on the other device, or its conversation is no longer listed).', true);
      }, () => note?.('That conversation turn could not be shown.', true));
    }
    function wire2() {
      $('root').addEventListener('click', openRoot);
      $('hist-open').addEventListener('click', openSheet);
      $('now').addEventListener('click', openSheet);
      $('back').addEventListener('click', () => void go(state.cursor - 1));
      $('fwd').addEventListener('click', () => void go(state.cursor + 1));
      $('hist-close').addEventListener('click', closeInner);
      $('pos-close').addEventListener('click', () => { $('pos').hidden = true; });
      $('pos-show').addEventListener('click', () => void go(0));
      $('pos-turn').addEventListener('click', showRootTurn);
      $('hist-list').addEventListener('click', event => {
        const card = event.target.closest?.('[data-index]'); if (!card) return;
        const index = Number(card.dataset.index);
        if (index === 0) openRoot(); else void go(index);
      });
      $('pos-body').addEventListener('click', event => revealLine(event.target));
      $('pos-body').addEventListener('keydown', event => { if ((event.key === 'Enter' || event.key === ' ') && revealLine(event.target)) event.preventDefault(); });
      for (const id of ['hist-sheet', 'pos']) $(id).addEventListener('click', event => { if (event.target === $(id)) $(id).hidden = true; });
      // Recent roots: the row drops the list down (the backdrop, Close or Escape fold it); a card goes back to that root,
      // its buttons note or remove it
      $('roots-panel').addEventListener('click', event => { if (event.target === $('roots-panel')) closeRoots(); });
      $('roots-open').addEventListener('click', () => { if ($('roots-panel').hidden) openRoots(); else closeRoots(); });
      $('roots-close').addEventListener('click', closeRoots);
      $('roots-filter').addEventListener('input', () => { outer.filter = $('roots-filter').value; paintPanel(true); });
      $('roots-limit').addEventListener('change', () => void setLimit($('roots-limit').value));
      const hit = event => {
        const card = event.target.closest?.('[data-key]'); if (!card) return null;
        const button = event.target.closest?.('[data-act]'), what = button && card.contains(button) ? button.dataset.act : null;
        if (!what && event.target.closest?.('.g-rnote')) return {card, what: 'typing'};
        return {card, what};
      };
      $('roots-list').addEventListener('click', event => {
        const h = hit(event); if (!h || h.what === 'typing') return;
        const key = h.card.dataset.key;
        if (h.what === 'note') startNote(key);
        else if (h.what === 'save') void saveNote();
        else if (h.what === 'cancel') cancelNote();
        else if (h.what === 'remove') void removeRoot(key);
        else void select(key);
      });
      $('roots-list').addEventListener('keydown', event => {
        const h = hit(event); if (!h) return;
        if (h.what === 'typing') {
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void saveNote(); }
          else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation?.(); cancelNote(); }
          return;
        }
        if (!h.what && (event.key === 'Enter' || event.key === ' ') && event.target === h.card) { event.preventDefault(); void select(h.card.dataset.key); }
      });
      doc.addEventListener?.('keydown', event => {
        if (event.key !== 'Escape') return;
        if (!$('pos').hidden) $('pos').hidden = true; else if (!$('hist-sheet').hidden) $('hist-sheet').hidden = true;
        else if (!$('roots-panel').hidden) { if (outer.editing) cancelNote(); else closeRoots(); }
      });
    }
    wire2();
    // The list once at the start (a cold start can go straight back to a recent root); the root on screen, restored from
    // this page's own store and not listed yet (the list is new, or it fell off on another device), is listed again.
    async function startOuter() {
      if (!roots) return;
      paintOuter();
      await readOuter();
      const first = root(), key = first ? rootKey(first) : null;
      if (key && outer.status === 'ready' && !outer.visiting && !outer.list.some(c => c.key === key)) { outer.pending = true; void flushSave(); }
    }
    return {state, outer, reroot, push, go, back: () => go(state.cursor - 1), forward: () => go(state.cursor + 1), rendered, restore, sync, current, root, openRoot, openSheet, closeAll,
      inPosition: node => !!node?.closest?.('#g-pos'), positionSession: node => node?.closest?.('[data-pos-id]')?.dataset.posId || null, paint,
      startOuter, readOuter, flushOuter: keepalive => flushSave(keepalive), openRoots, closeRoots, selectRoot: select, saveNote, startNote, cards, settled: () => outer.chain,
      metadataSettled: () => outer.describing || Promise.resolve()};
  }
  return Object.freeze({create, handleKey, shortHandle, rootKey, nameFrom, descriptionFrom, descriptionLine, middle, parcelName, cleanPosition, whenText, depthOf, MAX_ENTRIES, NAME_MAX, TITLE_MAX, DESC_MAX, STORE_MAX});
})();
globalThis.opcViewHistory = opcViewHistory;
