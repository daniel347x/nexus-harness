// Read-only CodeNomad 0.20.1 tool arguments. No message edits or native control handlers.
(function () {
  'use strict';
  const ROW = '.tool-call'; // Also clear stale labels while recycled rows lack valid identity attributes.
  const cache = new Map();
  const LIVE_INTERVAL = 2000, RETRY_INTERVAL = 15000, MAX_CACHE = 120;
  const READ_LIMIT = 16 * 1024 * 1024, PART_LIMIT = 8 * 1024 * 1024, CACHE_BYTES = 8 * 1024 * 1024;
  const { rawMembers, toolSnapshotText, bytes, size, SIZE_NOTE } = opcToolText;
  let timer = null, activeReads = 0;

  function identity(row) {
    const ids = ['instanceId', 'sessionId', 'messageId'].map(name => row.dataset[name]);
    return [...ids, row.dataset.partId].every(id => typeof id === 'string' && id.length > 0 && id.length <= 512)
      ? { key: JSON.stringify([location.origin, ...ids]), ids } : null;
  }

  const { preview } = opcToolPreview; // Shared core; no transformation body in scanner.

  // @beacon[
  //   id=auto-beacon@readMessage-opc-tools,
  //   role=readMessage,
  //   slice_labels=opc-codenomad-tools,
  //   kind=ast,
  //   comment=Bounded shared same-origin message read: validate wire identity; cache previews/input and exact snapshot byte counts only, never output bodies.,
  // ]
  async function readMessage(entry) {
    entry.loading = true;
    activeReads++;
    try {
      const [instance, session, message] = entry.ids.map(encodeURIComponent);
      const response = await fetch(`/workspaces/${instance}/instance/api/session/${session}/message/${message}`, {
        credentials: 'include', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) { void response.body?.cancel().catch(() => {}); throw new Error(`HTTP ${response.status}`); }
      if (Number(response.headers.get('content-length')) > READ_LIMIT) {
        void response.body?.cancel().catch(() => {}); throw new Error('16 MiB read limit');
      }
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let wire = '', readBytes = 0;
      try {
        for (;;) {
          const { done, value } = await reader.read(); if (done) break;
          readBytes += value.byteLength;
          if (readBytes > READ_LIMIT) throw new Error('16 MiB read limit');
          wire += decoder.decode(value, { stream: true });
        }
        wire += decoder.decode();
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      const envelope = JSON.parse(wire);
      // The pinned OpenCode V2 client unwraps the response's data field.
      const record = envelope.data;
      if (record?.id !== entry.ids[2] || (Object.hasOwn(record, 'sessionID') && record.sessionID !== entry.ids[1])
        || !Number.isSafeInteger(record.time?.created) || record.time.created <= 0 || !Array.isArray(record.content)) throw new Error('Unexpected message record');
      if (entry.messageCreated != null && record.time.created !== entry.messageCreated) throw new Error('Message creation witness changed');
      const messageWire = rawMembers(wire).get('data'), partWires = rawMembers(rawMembers(messageWire).get('content'));
      const parts = new Map(), duplicates = new Set(); let retained = 0;
      entry.limited = false;
      for (let index = 0; index < record.content.length; index++) {
        const part = record.content[index];
        if (part?.type !== 'tool' || typeof part.id !== 'string' || !part.id || part.id.length > 512) continue;
        if (parts.has(part.id)) { duplicates.add(part.id); continue; }
        const state = part.state;
        // The pinned SDK streams serialized JSON starting at input: ""; only `called`
        // replaces it with the final input. Display text alone cannot establish readiness.
        const pending = state?.status === 'streaming' || state?.status === 'pending';
        const rawPart = partWires.get(index);
        const base = { pending, hasInput: !!state && Object.hasOwn(state, 'input'),
          live: !record.time.completed && !['completed', 'error', 'cancelled', 'aborted'].includes(state?.status),
          toolName: part.name, callId: part.callID ?? null, messageCreated: record.time.created };
        let item;
        try {
          if (typeof part.name !== 'string' || !part.name || part.name.length > 512 ||
            (part.callID != null && (typeof part.callID !== 'string' || part.callID.length > 512))) throw new Error('identity unavailable');
          const previous = entry.parts.get(part.id);
          if (previous && (previous.toolName !== part.name || previous.callId !== (part.callID ?? null))) throw new Error('Tool identity witness changed');
          if (bytes(rawPart) > PART_LIMIT) throw new Error('8 MiB part limit');
          const snapshot = toolSnapshotText(state || {}, rawMembers(rawMembers(rawPart).get('state') || '{}'));
          if (bytes(snapshot.input) + bytes(snapshot.output) > PART_LIMIT) throw new Error('8 MiB snapshot limit');
          item = { ...base, text: pending ? '' : preview(state?.input, part.name),
            inputBytes: snapshot.inputBytes, outputBytes: snapshot.outputBytes };
          // Full original inputs are optional cache data, not required to count or
          // refresh. Avoid retaining megabyte arguments for each visible row.
          if ((snapshot.inputBytes || 0) <= 64 * 1024) item.input = state?.input;
        } catch (error) {
          item = { ...base, text: '', inputBytes: null, outputBytes: null, error: error.message, live: false };
        }
        const itemBytes = bytes(JSON.stringify(item)) + bytes(part.id) + 128;
        if (parts.size >= 5000 || retained + itemBytes > CACHE_BYTES) {
          entry.limited = true; break;
        }
        retained += itemBytes; parts.set(part.id, item);
      }
      for (const id of duplicates) parts.set(id, { text: '', inputBytes: null, outputBytes: null, error: 'ambiguous part identity', live: false });
      // A response for an evicted/replaced cache entry cannot publish into its successor.
      if (cache.get(entry.key) !== entry) return;
      entry.parts = parts;
      entry.messageCreated = record.time.created;
      entry.bytes = retained;
      trimCache();
      entry.complete = !!record.time?.completed;
      entry.failed = false;
      entry.nextRead = Date.now() + LIVE_INTERVAL;
    } catch (error) {
      // Authentication/offline failures never fall back to scraped, potentially cropped input.
      entry.failed = true;
      entry.error = error.message || 'unavailable';
      entry.blocked = /16 MiB read limit|Message creation witness changed/.test(entry.error);
      entry.nextRead = Date.now() + RETRY_INTERVAL;
    } finally {
      entry.loading = false;
      activeReads--;
      schedule();
    }
  }

  function annotate(row, entry) {
    const summary = row.querySelector('.tool-call-header-toggle .tool-call-summary');
    if (!summary) return;
    let label = summary.querySelector('.opc-tool-argument-preview');
    if (!label) {
      label = document.createElement('span');
      label.className = 'opc-tool-argument-preview';
      summary.insertBefore(label, summary.querySelector('.tool-call-header-status'));
    }
    let part = entry.parts.get(row.dataset.partId);
    const mismatch = part?.toolName && row.dataset.toolName && row.dataset.toolName !== part.toolName;
    if (mismatch) part = null;
    const text = part?.text || (entry.failed ? 'arguments unavailable' : '');
    if (label.textContent !== text) label.textContent = text;
    label.hidden = !text;
    label.setAttribute('aria-label', text ? `Tool arguments: ${text}` : 'Tool arguments loading');
    if (!summary.classList.contains('opc-tool-preview-summary')) summary.classList.add('opc-tool-preview-summary');
    const header = row.querySelector('.tool-call-header');
    if (!header) return;
    let sizes = header.querySelector('.opc-tool-sizes');
    if (!sizes) {
      sizes = document.createElement('span'); sizes.className = 'opc-tool-sizes';
      for (const kind of ['input', 'output']) {
        if (kind === 'output') sizes.append(document.createTextNode(' · '));
        const label = document.createElement('span'); label.className = 'opc-tool-size'; sizes.append(label);
      }
      header.append(sizes);
    }
    const unavailable = entry.failed ? entry.error || 'unavailable' : mismatch ? 'tool identity mismatch' : part?.error || (entry.limited && !part ? 'cache limit' : part ? '' : entry.complete ? 'missing' : 'loading');
    const value = entry.failed ? 'In: — · Out: —' : `In: ${size(part?.inputBytes)} · Out: ${size(part?.outputBytes)}`;
    for (const [index, kind, prefix] of [[0, 'input', 'In'], [2, 'output', 'Out']]) {
      const count = entry.failed ? null : part?.[kind + 'Bytes'];
      const label = sizes.childNodes[index], text = `${prefix}: ${size(count)}`;
      if (label.textContent !== text) label.textContent = text;
      opcToolText.emphasizeToolSize(label, count);
    }
    const detail = SIZE_NOTE + (unavailable ? ` ${unavailable}.` : part?.pending ? ' Input is a not-final fragment.' : part?.live ? ' Live stored snapshot; may grow.' : '');
    if (sizes.getAttribute('title') !== detail) sizes.setAttribute('title', detail);
    if (sizes.getAttribute('aria-label') !== value + '. ' + detail) sizes.setAttribute('aria-label', value + '. ' + detail);
  }

  function trimCache() {
    let total = [...cache.values()].reduce((sum, entry) => sum + (entry.bytes || 0), 0);
    if (total <= CACHE_BYTES) return;
    // Inputs can be released without losing any preview or count. Counts and
    // bounded previews get priority over original-input caching.
    for (const entry of [...cache.values()].sort((a, b) => a.lastSeen - b.lastSeen)) {
      for (const part of entry.parts.values()) {
        if (Object.hasOwn(part, 'input')) { delete part.input; }
      }
      const remaining = [...entry.parts].reduce((sum, [id, part]) => sum + bytes(JSON.stringify(part)) + bytes(id) + 128, 0);
      total -= (entry.bytes || 0) - remaining; entry.bytes = remaining;
      if (total <= CACHE_BYTES) return;
    }
    for (const entry of [...cache.values()].sort((a, b) => a.lastSeen - b.lastSeen)) {
      if (total <= CACHE_BYTES) break;
      total -= entry.bytes || 0; entry.parts.clear(); entry.bytes = 0;
      entry.limited = true; entry.nextRead = Date.now() + RETRY_INTERVAL;
    }
  }

  // @beacon[
  //   id=auto-beacon@scan-opc-tools,
  //   role=scan,
  //   slice_labels=opc-codenomad-tools,
  //   kind=ast,
  //   comment=Reconcile preview/size labels by full scoped identity; bounded shared message cache and live-size refresh survive remounts without histories or row GETs.,
  // ]
  function scan() {
    timer = null;
    if (document.hidden) return;
    const groups = new Map();
    for (const row of document.querySelectorAll(ROW)) {
      if (!row.getClientRects().length) continue;
      const box = row.getBoundingClientRect();
      if (box.bottom <= 0 || box.top >= innerHeight) continue; // Do not inventory offscreen mounted history.
      const id = identity(row);
      if (!id) { annotate(row, { parts: new Map(), failed: true, error: 'identity unavailable' }); continue; }
      if (!groups.has(id.key)) groups.set(id.key, { ids: id.ids, rows: [] });
      groups.get(id.key).rows.push(row);
    }
    const now = Date.now();
    for (const [key, group] of groups) {
      let entry = cache.get(key);
      if (!entry) {
        if (cache.size >= MAX_CACHE) {
          const inactive = [...cache].filter(([id, item]) => !groups.has(id) && !item.loading).sort((a, b) => a[1].lastSeen - b[1].lastSeen);
          if (inactive.length) cache.delete(inactive[0][0]);
          else { for (const row of group.rows) annotate(row, { parts: new Map(), limited: true }); continue; }
        }
        entry = { key, ids: group.ids, parts: new Map(), bytes: 0, nextRead: 0, loading: false, complete: false, failed: false };
        cache.set(key, entry);
      }
      entry.lastSeen = now;
      for (const row of group.rows) annotate(row, entry);
      // Output sizes need actual data updates. One bounded GET per visible LIVE
      // message per interval, not one per row; terminal snapshots never poll.
      const missing = group.rows.some(row => {
        const part = entry.parts.get(row.dataset.partId);
        return (!part && !entry.complete && !entry.limited) || !!part?.live;
      });
      if (activeReads < 3 && !entry.blocked && !entry.loading && now >= entry.nextRead && (missing || entry.failed)) {
        void readMessage(entry);
      }
    }
  }

  function schedule() {
    if (timer === null) timer = setTimeout(scan, 120);
  }

  new MutationObserver(schedule).observe(document.documentElement, {
    subtree: true, childList: true, attributes: true,
    attributeFilter: ['data-instance-id', 'data-session-id', 'data-message-id', 'data-part-id', 'class'],
  });
  document.addEventListener('visibilitychange', schedule);
  document.addEventListener('scroll', schedule, true);
  setInterval(schedule, LIVE_INTERVAL);
  schedule();
})();
