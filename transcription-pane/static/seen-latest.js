'use strict';

// What is NEW below the reading position: which rows of a conversation view have been seen (and
// whether an unseen one is a final reply), for the "Latest ↓" badge of the phone (m.js) and the
// desktop viewer (sessions.js). See createSeenTracker.
(() => {
  const PULSE_MS = 5000, SLACK = 4, pulses = new WeakMap();
  const inert = Object.freeze({enabled: false, track: () => false, seenAll() {}, below: () => 0, tally: () => ({count: 0, final: 0})});
  // @beacon[
  //   id=opc-pane@createSeenTracker,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Seen-ness per conversation row for the Latest badge: a row is seen once its END (a zero-height .seen-end sentinel kept as the row's last child) has been inside the scroll viewport, watched by ONE IntersectionObserver (the badge's recount, below(), also marks unseen rows whose end is on screen; per-scroll work touches the unseen rows only). Rows are born seen unless track() says live (arrived from the live tail while the reader is scrolled up) and their end is off screen; a seen row whose version changes and grows taller while live becomes unseen until its new end is in view. below() counts unseen, displayed rows whose end is under the viewport; tally() also counts how many of them are final replies right now (the page's isFinal(node), asked at every recount, so a row that stops or starts being final while it streams is judged as it is now); seenAll() clears (the jump, reaching the bottom). Without IntersectionObserver it is inert (count 0, the plain badge).,
  // ]
  function createSeenTracker(viewport, {onChange, isFinal} = {}) {
    const IO = viewport?.ownerDocument?.defaultView?.IntersectionObserver;
    if (typeof IO !== 'function') return inert;
    const records = new WeakMap(), owners = new WeakMap(), unseen = new Set();
    function endInView(record) {
      const end = record.end;
      if (!end.isConnected || !end.getClientRects().length) return false;
      const r = end.getBoundingClientRect(), v = viewport.getBoundingClientRect();
      return r.top <= v.bottom + SLACK && r.bottom >= v.top;
    }
    function markSeen(node) {
      records.get(node).height = node.offsetHeight;
      return unseen.delete(node);
    }
    // A queued entry can be older than the row's latest growth: the end must still be in view.
    const observer = new IO(entries => {
      let changed = false;
      for (const entry of entries) {
        const node = entry.isIntersecting ? owners.get(entry.target) : null;
        if (node && unseen.has(node) && endInView(records.get(node))) changed = markSeen(node) || changed;
      }
      if (changed) onChange?.();
    }, {root: viewport, rootMargin: `0px 0px ${SLACK}px 0px`});
    // Register a row node after it is rendered (and again after every repaint of it): keeps its
    // sentinel last and answers whether the row has just become unseen (new material arrived).
    function track(node, {live = false, version = ''} = {}) {
      let record = records.get(node);
      if (!record) {
        const end = node.ownerDocument.createElement('i');
        end.className = 'seen-end'; end.setAttribute('aria-hidden', 'true');
        record = {end, version, height: 0}; records.set(node, record); owners.set(end, node);
        node.append(end); observer.observe(end);
        if (live && !endInView(record)) { unseen.add(node); return true; }
        record.height = node.offsetHeight;
        return false;
      }
      if (node.lastChild !== record.end) node.append(record.end);
      if (version === record.version) return false;
      record.version = version;
      if (unseen.has(node)) return false;
      if (live && node.offsetHeight > record.height + 1 && !endInView(record)) { unseen.add(node); return true; }
      record.height = node.offsetHeight;
      return false;
    }
    function seenAll() {
      for (const node of unseen) if (node.isConnected) markSeen(node);
      unseen.clear();
    }
    // Unseen rows whose end is still under the viewport (count), and how many of those are final
    // replies (final: the page's own classification, read now); a hidden row (tools-only, a folded
    // group) is not counted, and a row no longer in the page is dropped. An unseen row whose end is
    // on screen right now is marked seen here too, so a frame the observer has not reported yet (or
    // a browser that throttles it) never leaves a seen row counted. Cost: the unseen rows only.
    function tally() {
      if (!unseen.size) return {count: 0, final: 0};
      const v = viewport.getBoundingClientRect(), bottom = v.bottom + SLACK;
      let count = 0, final = 0;
      for (const node of unseen) {
        if (!node.isConnected) { unseen.delete(node); continue; }
        const end = records.get(node).end;
        if (!end.getClientRects().length) continue;
        const r = end.getBoundingClientRect();
        if (r.top > bottom) { count++; if (isFinal?.(node)) final++; } else if (r.bottom >= v.top) markSeen(node);
      }
      return {count, final};
    }
    const below = () => tally().count;
    return Object.freeze({enabled: true, track, seenAll, below, tally});
  }
  // The badge's three lit states: plain ("↓ Latest") when everything below has been seen; the accent
  // "N new ↓" (data-unseen="true") while unseen rows below are only working turns (tool calls, mid-
  // round prose); the red "N new ↓" (data-unseen="final") while at least one unseen row below is a
  // final reply, whatever the newest row is. Turning red pulses on its own (a streaming reply can
  // become final without any new row). The click behaviour stays the page's own.
  function paintBadge(button, count, plain, final = 0) {
    const label = count > 0 ? count + ' new ↓' : plain, lit = count > 0 ? (final > 0 ? 'final' : 'true') : '';
    const was = button.dataset.unseen;
    if (button.textContent !== label) button.textContent = label;
    if (lit) button.dataset.unseen = lit; else { delete button.dataset.unseen; stopPulse(button); }
    if (lit === 'final' && was !== 'final') pulseBadge(button);
    button.setAttribute('aria-label', !lit ? 'Jump to the newest message' :
      count + ' new below, not yet seen' + (lit === 'final' ? ', including a final reply' : '') + ': jump to the newest');
  }
  // A short attention pulse (about 5 s), restarted each time new material arrives.
  function pulseBadge(button) {
    clearTimeout(pulses.get(button));
    delete button.dataset.pulse; void button.offsetWidth; button.dataset.pulse = 'on';
    pulses.set(button, setTimeout(() => stopPulse(button), PULSE_MS));
  }
  function stopPulse(button) { clearTimeout(pulses.get(button)); pulses.delete(button); delete button.dataset.pulse; }
  globalThis.opcSeenLatest = Object.freeze({create: createSeenTracker, paintBadge, pulseBadge, PULSE_MS});
})();
