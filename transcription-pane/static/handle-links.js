'use strict';

// Clickable Workflowy handles on every pane page: [ap:XXXXXX] Anchor Points, full UUIDs and their 8-hex short form.
// Nothing is rewritten. Handles are found in the page's own text, marked with the CSS Custom Highlight API (paint
// only), and hit-tested from the text under the pointer at press time, so every renderer, streaming repaint,
// selection rule and copy of a message stays exactly as it was. A tap or left-click on a handle opens it in View; a
// press-and-hold or right-click opens a small menu: Open in View · Copy. No server involved.
const handleLinks = (() => {
  const HOLD_MS = 450, SLOP = 10, SWALLOW_MS = 500;      // hold below iOS's own ~500 ms long-press; finger slop in px
  const MAX_TEXT = 1 << 20, MAX_RANGES = 5000;           // decoration caps (the gesture works past them)
  // [data-opc-no-handles]: a region whose whole card is one target (View's history strip and sheet), never a handle.
  const SKIP = 'a[href], textarea, input, select, [contenteditable]:not([contenteditable="false"]), script, style, noscript, .opc-handle-menu, [data-opc-no-handles]';
  // [ap:…] always; a lowercase-hex 8-token (digits-only too) or a whole UUID only standing alone: not glued to a
  // prefix (ses_, cc-, #, 0x, a path or URL, =, @, :) and not followed by a word character, a hyphen or a .ext.
  const SOURCE = '\\[ap:[A-Za-z0-9]{3,16}\\]|(?<![0-9A-Za-z_\\-./#=@:\\\\])[0-9a-f]{8}(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?(?![0-9A-Za-z_\\-]|\\.\\w)';

  // @beacon[
  //   id=opc-pane@findHandles,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The one matching rule for clickable handles: every [ap:XXX] (3-16 letters/digits), every whole lowercase UUID, and every standalone 8-character lowercase-hex token (digits-only included; not glued to ses_/cc-/#/0x/paths/URLs/=/@/: and not followed by a word character, hyphen or .ext). Returns {handle, start, end} in text order.,
  // ]
  function findHandles(text) {
    const out = [], re = new RegExp(SOURCE, 'g');
    let m;
    while ((m = re.exec(text))) out.push({handle: m[0], start: m.index, end: m.index + m[0].length});
    return out;
  }

  // ---- decoration: one Highlight of StaticRanges, kept current by a debounced observer (paint only, no DOM change)
  const highlight = typeof Highlight === 'function' && globalThis.CSS?.highlights ? new Highlight() : null;
  const marked = new Map();  // text node -> its ranges
  let count = 0;
  function unmark(node) {
    const ranges = marked.get(node);
    if (!ranges) return;
    for (const r of ranges) highlight.delete(r);
    count -= ranges.length; marked.delete(node);
  }
  function mark(node) {
    unmark(node);
    const text = node.data;
    if (text.length < 8 || text.length > MAX_TEXT || count >= MAX_RANGES) return;
    const ranges = [];
    for (const f of findHandles(text)) {
      if (count >= MAX_RANGES) break;
      const r = new StaticRange({startContainer: node, startOffset: f.start, endContainer: node, endOffset: f.end});
      highlight.add(r); ranges.push(r); count++;
    }
    if (ranges.length) marked.set(node, ranges);
  }
  function scan(root) {
    if (!highlight || !root?.isConnected) return;
    if (root.nodeType === 3) { if (root.parentElement && !root.parentElement.closest(SKIP)) mark(root); return; }
    if (root.nodeType !== 1 || root.closest(SKIP)) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {acceptNode: n =>
      n.nodeType === 3 ? NodeFilter.FILTER_ACCEPT : n.matches(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP});
    for (let n = walker.nextNode(); n; n = walker.nextNode()) mark(n);
  }
  const pending = new Set();
  let prune = false, timer = 0;
  function flush() {
    timer = 0;
    if (prune) { prune = false; for (const node of [...marked.keys()]) if (!node.isConnected) unmark(node); }
    const roots = [...pending]; pending.clear();
    for (const n of roots) scan(n);
  }
  const observer = highlight && new MutationObserver(records => {
    for (const r of records) {
      if (r.type === 'characterData') pending.add(r.target);
      else { for (const n of r.addedNodes) pending.add(n); if (r.removedNodes.length) prune = true; }
    }
    if (!timer) timer = setTimeout(flush, 60);
  });

  // ---- hit-test: the handle under a point, from the text alone, confirmed inside the token's own boxes
  function caretAt(x, y) {
    try {
      if (document.caretPositionFromPoint) { const p = document.caretPositionFromPoint(x, y); if (p) return {node: p.offsetNode, offset: p.offset}; }
      if (document.caretRangeFromPoint) { const r = document.caretRangeFromPoint(x, y); if (r) return {node: r.startContainer, offset: r.startOffset}; }
    } catch (_) { /* no caret here */ }
    return null;
  }
  function inside(node, start, end, x, y) {
    const range = document.createRange(); range.setStart(node, start); range.setEnd(node, end);
    for (const b of range.getClientRects()) if (x >= b.left - 1 && x <= b.right + 1 && y >= b.top - 1 && y <= b.bottom + 1) return true;
    return false;
  }
  function hitIn(node, offset, x, y) {
    if (!node || node.nodeType !== 3 || !node.parentElement || node.parentElement.closest(SKIP)) return null;
    const text = node.data, from = Math.max(0, offset - 64), to = Math.min(text.length, offset + 64), slice = text.slice(from, to);
    for (const f of findHandles(slice)) {
      if ((f.start === 0 && from > 0) || (f.end === slice.length && to < text.length)) continue;  // cut by the window
      const start = from + f.start, end = from + f.end;
      if (offset >= start && offset <= end && inside(node, start, end, x, y)) return {handle: f.handle, node, start, end};
    }
    return null;
  }
  // @beacon[
  //   id=opc-pane@handleAt,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The handle under a viewport point, or null: the caret position from the point (caretPositionFromPoint / caretRangeFromPoint), the rule matched in a window around it, and the point confirmed inside the token Range's client rects (a click past a line's end is no hit). A small element whose caret is not in text (WebKit controls) is searched text node by text node. Skips links, editable fields, script/style and the menu.,
  // ]
  function handleAt(x, y) {
    const caret = caretAt(x, y), hit = caret && hitIn(caret.node, caret.offset, x, y);
    if (hit) return hit;
    const el = document.elementFromPoint(x, y);
    if (!el || el.closest(SKIP) || (el.textContent || '').length > 4096) return null;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      for (const f of findHandles(n.data)) if (inside(n, f.start, f.end, x, y)) return {handle: f.handle, node: n, start: f.start, end: f.end};
    }
    return null;
  }

  // ---- Open in View: this page's own View opener (glimpse.js, m.js, the sidebar's sessions.js with the desktop View
  // pane, the desktop editor's app.js), else the page around this frame, else (a page with no View anywhere around it;
  // the desktop and /m never get here) one reused View tab. The opener also gets {node}: the element the handle was
  // pressed in, so it can tell where the tap came from (a session row's socket, Chat, View's own document).
  function openInView(handle, hit = null) {
    const own = globalThis.opcOpenInView, context = {node: hit?.node?.parentElement || null};
    if (typeof own === 'function') return void own(handle, context);
    try {
      const up = window.parent !== window ? window.parent.opcOpenInView : null;
      if (typeof up === 'function') return void up(handle, {node: null});
    } catch (_) { /* not our origin */ }
    window.open('/glimpse.html?handle=' + encodeURIComponent(handle), 'opc-view');
  }
  function copy(handle) {
    const fallback = () => {
      const area = document.createElement('textarea'); area.value = handle; area.setAttribute('readonly', '');
      area.style.position = 'fixed'; area.style.opacity = '0'; document.body.append(area); area.select();
      let ok = false; try { ok = document.execCommand('copy'); } catch (_) { /* reported */ }
      area.remove(); return ok;
    };
    try { if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(handle).then(() => true, fallback); } catch (_) { /* below */ }
    return Promise.resolve(fallback());
  }

  // ---- the menu: Open in View · Copy, at the finger or the pointer
  let menu = null;
  function closeMenu() { menu?.remove(); menu = null; }
  function openMenu(handle, x, y, touch = false, hit = null) {
    closeMenu();
    const box = document.createElement('div'), label = document.createElement('div');
    box.className = 'opc-handle-menu'; box.setAttribute('role', 'menu'); box.dataset.handle = handle;
    label.className = 'opc-handle-menu-label'; label.textContent = handle;
    const item = (text, act) => { const b = document.createElement('button'); b.type = 'button'; b.setAttribute('role', 'menuitem'); b.dataset.act = act; b.textContent = text; return b; };
    const view = item('Open in View', 'view'), clip = item('Copy', 'copy');
    view.addEventListener('click', () => { closeMenu(); openInView(handle, hit); });
    clip.addEventListener('click', () => void copy(handle).then(ok => {
      clip.textContent = ok ? '✓ Copied' : 'Copy failed'; setTimeout(() => { if (menu === box) closeMenu(); }, 700);
    }));
    box.append(label, view, clip);
    document.body.append(box); menu = box;
    const w = box.offsetWidth, h = box.offsetHeight, vw = document.documentElement.clientWidth, vh = window.innerHeight;
    let top = touch ? y - h - 16 : y + 4, left = touch ? x - w / 2 : x + 4;
    if (touch && top < 8) top = y + 24;
    box.style.left = Math.max(8, Math.min(left, vw - w - 8)) + 'px';
    box.style.top = Math.max(8, Math.min(top, vh - h - 8)) + 'px';
    return box;
  }

  // ---- the gesture: tap / left-click opens in View; hold or right-click opens the menu
  let press = null, swallowUntil = 0, hover = null;
  function release() {
    if (!press) return;
    clearTimeout(press.timer); press.lock?.removeAttribute('data-opc-handle-press'); press = null;
  }
  function holdFire() {
    const p = press; if (!p) return;
    p.fired = true;
    if (p.type !== 'mouse') { try { navigator.vibrate?.(10); } catch (_) { /* optional */ } getSelection()?.removeAllRanges(); }
    openMenu(p.hit.handle, p.x, p.y, p.type !== 'mouse', p.hit);
  }
  // @beacon[
  //   id=opc-pane@handlePointerDown,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Starts a handle press (capture phase, primary button, no modifier): a hit arms a 450 ms hold timer (the menu) and, for a finger, turns off text selection and the iOS callout on that one element for the press only. pointerup before the hold opens the handle in View (a mouse only with no text selected; the opener also gets the element pressed, so it knows where the tap came from) and swallows the click that follows, so the tool bar, <summary> or row under it does not also toggle; movement past 10 px or pointercancel (a scroll) cancels. A press elsewhere closes an open menu.,
  // ]
  function onPointerDown(event) {
    if (menu?.contains(event.target)) return;
    closeMenu(); release();
    if (!event.isPrimary || event.button !== 0) return;
    if (event.pointerType === 'mouse' && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)) return;
    const hit = handleAt(event.clientX, event.clientY);
    if (!hit) return;
    press = {hit, id: event.pointerId, x: event.clientX, y: event.clientY, type: event.pointerType, fired: false, lock: null};
    if (event.pointerType !== 'mouse') { press.lock = hit.node.parentElement; press.lock.setAttribute('data-opc-handle-press', ''); }
    press.timer = setTimeout(holdFire, HOLD_MS);
  }
  function onPointerMove(event) {
    if (press && event.pointerId === press.id) {
      if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > SLOP) release();
    } else if (!press && event.pointerType === 'mouse' && !event.buttons) pointerCursor(event.clientX, event.clientY);
  }
  function onPointerUp(event) {
    if (!press || event.pointerId !== press.id) return;
    const p = press; release();
    if (p.fired) { swallowUntil = performance.now() + SWALLOW_MS; if (p.type !== 'mouse') getSelection()?.removeAllRanges(); return; }
    if (p.type === 'mouse' && getSelection() && !getSelection().isCollapsed) return;  // drag-selecting stays selecting
    swallowUntil = performance.now() + SWALLOW_MS;
    openInView(p.hit.handle, p.hit);
  }
  function onClick(event) {
    if (menu?.contains(event.target) || performance.now() > swallowUntil) return;
    swallowUntil = 0; event.preventDefault(); event.stopImmediatePropagation();
  }
  function onContextMenu(event) {
    if (menu?.contains(event.target)) return;
    if (menu) { event.preventDefault(); return; }  // an Android long-press after the hold already opened it
    const hit = handleAt(event.clientX, event.clientY);
    if (!hit) return;
    event.preventDefault(); release();
    openMenu(hit.handle, event.clientX, event.clientY, false, hit);
  }
  // Desktop hover: a pointer cursor while over a handle (inline on its element, restored as soon as it leaves it).
  let hoverAt = null, hoverTimer = 0;
  function pointerCursor(x, y) {
    hoverAt = {x, y};
    if (hoverTimer) return;
    hoverTimer = setTimeout(() => {  // at most one hit-test per 40 ms, at the latest position
      hoverTimer = 0;
      const el = handleAt(hoverAt.x, hoverAt.y)?.node.parentElement || null;
      if (el === hover?.el) return;
      if (hover) hover.el.style.cursor = hover.was;
      hover = el ? {el, was: el.style.cursor} : null;
      if (el) el.style.cursor = 'pointer';
    }, 40);
  }

  const STYLE = `
::highlight(opc-handle){text-decoration:underline dotted 1px;text-underline-offset:2px;background-color:rgba(120,170,255,.14)}
:root[data-glare="on"] ::highlight(opc-handle){text-decoration:underline solid 2px;background-color:rgba(160,200,255,.26)}
[data-opc-handle-press]{-webkit-user-select:none!important;user-select:none!important;-webkit-touch-callout:none!important}
.opc-handle-menu{position:fixed;z-index:2147483000;min-width:170px;padding:6px;border:1px solid #627083;border-radius:10px;
  background:#232a36;color:#e1e5eb;box-shadow:0 8px 28px rgba(0,0,0,.5);font:15px/1.3 system-ui,sans-serif;display:flex;flex-direction:column;gap:4px}
.opc-handle-menu-label{padding:2px 8px 4px;color:#a9c9ff;font:12.5px ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}
.opc-handle-menu button{min-height:40px;padding:6px 12px;border:0;border-radius:7px;background:#303a4a;color:inherit;font:inherit;text-align:left;cursor:pointer}
.opc-handle-menu button:hover,.opc-handle-menu button:focus-visible{background:#3d4a5f}
:root[data-glare="on"] .opc-handle-menu{color:#fff;font-size:17px}`;
  // @beacon[
  //   id=opc-pane@handleLinksStart,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Starts clickable handles on this page (index, sessions, m, m-programs, glimpse): its own style (the ::highlight colours, glare included, and the menu), one scan of the body into the opc-handle Highlight and a debounced MutationObserver for every later render (skipped where the Highlight API is missing: the gesture still works), and the window capture-phase pointer/click/contextmenu listeners. Escape, a scroll or a resize closes the menu.,
  // ]
  function start() {
    const style = document.createElement('style'); style.id = 'opc-handle-links-style'; style.textContent = STYLE;
    document.head.append(style);
    if (highlight) {
      CSS.highlights.set('opc-handle', highlight);
      scan(document.body);
      observer.observe(document.body, {childList: true, subtree: true, characterData: true});
    }
    addEventListener('pointerdown', onPointerDown, true);
    addEventListener('pointermove', onPointerMove, true);
    addEventListener('pointerup', onPointerUp, true);
    addEventListener('pointercancel', release, true);
    addEventListener('click', onClick, true);
    addEventListener('contextmenu', onContextMenu, true);
    addEventListener('keydown', event => { if (event.key === 'Escape') closeMenu(); }, true);
    addEventListener('scroll', event => { if (menu && !menu.contains(event.target)) closeMenu(); }, true);
    addEventListener('resize', closeMenu);
  }
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, {once: true});
  return Object.freeze({findHandles, handleAt, openInView, openMenu, closeMenu, copy, flush,
    stats: () => ({ranges: count, nodes: marked.size, decorated: !!highlight}), HOLD_MS, MAX_RANGES, MAX_TEXT});
})();
globalThis.opcHandleLinks = handleLinks;
