'use strict';

// The View page (glimpse.html, also /m's View tab): a NEXUS document through the pane's /api/glimpse routes,
// painted by the extension's shared renderer (GlimpseMd, served at /nexus-md/). This page holds only the request,
// the Markdown it got back, the daemon's rows and render spec with it, and the view/find state; it has no parser,
// escaper, search compiler, row builder or row painter of its own (the row list is GlimpseMd.renderRowList).
// What is shown is one entry of its history (view-history.js): a root from outside View or its pickers, then the
// handles tapped in the document.
const glimpseView = (() => {
  const BOUND = 200, BATCH = 200;   // top-level rendered blocks mounted at first, and per Show all step
  const ROW_BOUND = 200;            // rows shown at first in the row list (display only; every row is painted and held)
  const $ = id => document.getElementById('g-' + id);
  const state = {view: 'rendered', how: 'node', specKind: 'parcel', headingsChoice: null, selection: null, current: null, held: null,
    error: null, seq: 0, busy: false, find: {query: '', regex: null, error: '', count: 0, current: -1},
    painted: '', paintMs: 0, rest: null, total: 0, mountToken: null, catalog: null, rowsAll: false,
    // the rendered document's images (GlimpseLightbox.collect's groups), whether it is a gallery, what /api/files/meta said
    // of each local path, the open lightbox and the open side by side; its live gallery lines (folders, globs), what
    // /api/files/list said of each, and the files a fresh listing already sized
    images: null, imageOnly: false, imagesUnavailable: false, imageMeta: new Map(), lightbox: null, compare: null,
    live: [], listings: new Map(), listedNow: new Set()};
  let history = null;  // view-history.js, created at start
  const fmt = n => Number(n || 0).toLocaleString('en-US');
  const clock = iso => { const t = new Date(iso); return Number.isNaN(t.getTime()) ? String(iso) : t.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}); };
  const pause = () => new Promise(resolve => setTimeout(resolve, 0));
  function note(text, error = false) { const n = $('note'); n.textContent = text || ''; n.dataset.error = String(!!error); }
  // A phone (narrow screen) or the /m frame: broad scopes open compact. The desktop View pane is a frame too, but wide.
  const embed = document.documentElement?.dataset?.viewEmbed || (window.parent !== window ? 'framed' : 'page');
  function narrowPage() { return (window.parent !== window && embed !== 'desktop') || !!window.matchMedia?.('(max-width: 700px)').matches; }
  // The page around this frame (/m, the desktop sessions page) answers where a socket sits and shows a session in its list.
  const around = (() => { try { return window.parent !== window ? window.parent : null; } catch (_) { return null; } })();
  const aroundFn = name => { try { return typeof around?.[name] === 'function' ? around[name].bind(around) : null; } catch (_) { return null; } };

  // ---- the request: one of B2's {kind, value, options}; the daemon resolves and composes it
  // Order: Saved order sends nothing (the scope's own: a group's saved sort, else the outline); Document order sends
  // sort_by 'none' (the outline, whatever was saved); a date sort carries its direction.
  const dated = sort => sort === 'created' || sort === 'modified';
  // Include archives (Options, off by default) is sent only when on: include_archives true, never a false.
  function options() {
    const o = {headings_only: $('headings').checked}, sort = $('sort').value, recent = $('recent').value.trim();
    if (dated(sort)) { o.sort_by = sort; o.sort_direction = $('direction').value; }
    else if (sort === 'none') o.sort_by = 'none';
    if (recent) o.recent = recent;
    if ($('archives').checked) o.include_archives = true;
    return o;
  }
  // A parcel also asks for its row list (the daemon's rows and resolved spec, beside the same Markdown); an estate never,
  // and an estate sends headings_only only when Dan ticked it: each parcel keeps its own default read ({node} its body,
  // {dock} headings), as the estate grammar defines.
  function requestOptions(kind) {
    if (kind === 'parcel') return {...options(), include_rows: true};
    const o = options();
    if (!o.headings_only) delete o.headings_only;
    return o;
  }
  function applied(o, archives = o.include_archives) {
    const parts = [o.headings_only === undefined ? 'each parcel\'s own read' : o.headings_only ? 'headings only' : 'full'];
    if (o.recent) parts.push('recent ' + o.recent);
    if (dated(o.sort_by)) parts.push('by ' + o.sort_by + ', ' + (o.sort_direction === 'asc' ? 'oldest first' : 'newest first'));
    else if (o.sort_by === 'none') parts.push('document order');
    if (archives) parts.push('archives included');
    return parts.join(' · ');
  }
  // The daemon's footer for archived subtrees it left out ("📦 9 archived subtree(s) under this scope were left out;
  // include_archives=true includes them …"): from the reply's count, its notices, or the end of a plain Markdown reply.
  const ARCHIVES_LINE = /📦\s*(\d+)\s+archived subtree\(s\)[^\n]*left out/;
  function archivesLeftOut(data, notices) {
    if (Number.isSafeInteger(data.archivesLeftOut) && data.archivesLeftOut > 0) return data.archivesLeftOut;
    const line = notices.find(n => ARCHIVES_LINE.test(n)) || ARCHIVES_LINE.exec(String(data.markdown).slice(-2000))?.[0];
    const count = line ? Number(ARCHIVES_LINE.exec(line)[1]) : 0;
    return count > 0 ? count : 0;
  }
  // ---- Recent roots' store: the pane server's one list for the phone and the desktop (/api/view/roots, view_roots.py),
  // handed to the history. Every answer is {ok, status, data}: data only when it is the store's own (schemaVersion 1),
  // so a pane server from before the list (its static 404, a 405 for a POST) reads as missing, never as an error.
  async function rootsCall(method, body = null, {keepalive = false} = {}) {
    try {
      const response = await fetch('/api/view/roots', method === 'GET' ? {} :
        {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), ...(keepalive ? {keepalive: true} : {})});
      const data = await response.json().catch(() => null);
      return {ok: response.ok, status: response.status, data: data?.schemaVersion === 1 ? data : null, error: data?.error || ''};
    } catch (e) { return {ok: false, status: 0, data: null, error: e.message}; }
  }
  const rootsStore = {read: () => rootsCall('GET'), write: (body, options) => rootsCall('POST', body, options),
    describe: keys => rootsCall('POST', {schemaVersion: 1, op: 'describe', keys})};
  // A choice from the catalog, the handle presets or the spec box. Its default (compact for broad scopes on a
  // phone, the body for a node or symbols, each parcel's own read for an estate) applies unless Headings only was set
  // by hand; then it renders.
  function choose(selection) {
    if (state.headingsChoice === null) $('headings').checked = selection.kind !== 'estate' && narrowPage() && !selection.body;
    const done = render(selection);
    paintPickers();  // the chosen Dock or group is marked in its picker (a handle marks neither)
    return done;
  }
  // @beacon[
  //   id=opc-pane@glimpseRender,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=View's one render request: newest reply wins, preserves daemon Markdown/rows/spec/notices and archives, reuses its root name and first note line in both histories (never a focused descendant's name), detects named estates and opens galleries in the lightbox. Refresh/options repeat the scope without a cache refresh; failures retain the last document. Picker names/descriptions stay authoritative for groups; missing old-card metadata loads independently through the cache-only roots route.,
  // ]
  async function render(selection, request = {kind: selection.kind, value: selection.value, options: requestOptions(selection.kind)}) {
    const seq = ++state.seq;
    state.selection = selection; state.current = {selection, request}; state.busy = true;
    $('doc').dataset.busy = 'true'; $('rows').dataset.busy = 'true'; note('Rendering ' + selection.label + '…'); showStatus();
    let data;
    try {
      const response = await fetch('/api/glimpse/render', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(request)});
      data = await response.json().catch(() => ({error: 'HTTP ' + response.status + ' with no readable reply'}));
      if (!response.ok) throw new Error(data.error || 'HTTP ' + response.status);
      if (typeof data.markdown !== 'string') throw new Error('the reply carried no Markdown');
    } catch (e) {
      if (seq !== state.seq) return false;  // a newer choice is in flight or already shown
      state.busy = false; state.error = {label: selection.label, message: e.message};
      $('doc').dataset.busy = 'false'; $('rows').dataset.busy = 'false'; note(''); showStatus();
      return false;
    }
    if (seq !== state.seq) return false;
    // One reply, held whole: its Markdown, its rows (absent from an old daemon: no list; [] is an empty list), its spec
    // and the daemon's own status lines for it (shown beside the document, never spliced into the Markdown).
    const held = {markdown: data.markdown, bytes: data.bytes, renderedAt: data.renderedAt, request: data.request || request, label: selection.label, selection,
      rows: Array.isArray(data.rows) ? data.rows : null, spec: data.render_spec && typeof data.render_spec === 'object' ? data.render_spec : null,
      notices: Array.isArray(data.notices) ? data.notices.filter(line => typeof line === 'string' && line.trim()) : []};
    held.view = selection.view || held.request.options || {};
    held.archives = held.request.options?.include_archives === true;
    held.archivesLeftOut = held.archives ? 0 : archivesLeftOut(data, held.notices);
    // A handle read as Node that is a named estate shows that estate (its history entry keeps the node's own name); asked
    // for the node itself (raw), it says it is one.
    held.parcels = selection.bare && !selection.focus ? estateParcels(held) : 0;
    if (held.parcels && !selection.raw) {
      if (selection.entry) history?.rendered(selection.entry, held);
      const shown = await render(estateSelection(selection, held, held.parcels));
      if (shown || state.seq !== seq + 1 || !state.error) return shown;
      // The estate itself could not be rendered: the node, with that error kept above it (its line offers the estate again)
      const failed = state.error, again = await render({...selection, raw: true});
      if (again && !state.error) { state.error = failed; showStatus(); }
      return again;
    }
    // The same gallery again (Refresh, the archives switch) stays on its contact sheet; a new one opens by itself.
    const shownAgain = state.held?.selection === selection && (state.images?.length || 0) > 0;
    state.held = held; state.error = null; state.busy = false;
    if (selection.entry && !selection.estateOf && !selection.focus) history?.rendered(selection.entry, held);
    if (data.markdown.length > 512 * 1024) { note('Painting ' + fmt(data.bytes) + ' bytes…'); await pause(); if (seq !== state.seq) return false; }
    note('');
    $('doc').dataset.busy = 'false'; $('rows').dataset.busy = 'false';
    $('scroll').scrollTop = 0; $('scroll').scrollLeft = 0;
    state.lightbox?.close(); state.compare?.close();
    const sized = paintHeld(true);
    if (!shownAgain) void autoOpen(seq, sized);
    paintRows(!!selection.base);  // a tap keeps the list where it was; a new document starts it at the top
    $('pick').open = false;  // the chooser folds to its one-line summary (the current document); a tap reopens it
    return true;
  }
  // Refresh: the current request again, exactly (no cache refresh, no new options).
  function refresh() { return state.current ? render(state.current.selection, state.current.request) : Promise.resolve(false); }
  // Full: this scope without headings-only or a recent window (a one-off; the next choice takes its own default).
  // After a row tap, Full and the options apply to the chosen document itself (its whole list), not to the tapped row.
  // A pasted render specification's own `since` window goes too (the request without it; the history entry keeps it).
  function full() {
    const current = state.held?.selection || state.selection, selection = current?.base || current; if (!selection) return Promise.resolve(false);
    $('headings').checked = false; $('recent').value = ''; state.headingsChoice = null;
    const value = selection.value;
    if (value && typeof value === 'object' && !Array.isArray(value) && value.since != null) {
      const {since, ...whole} = value;
      return render({...selection, value: whole});
    }
    return render(selection);
  }
  function applyOptions() { $('direction').hidden = !dated($('sort').value); if (state.selection) void render(state.selection.base || state.selection); }
  // Include archives on or off (the Options switch, or the one tap beside the daemon's footer): the current request again,
  // exactly, with only include_archives changed (a tapped row stays the tapped row); later choices keep the switch.
  function setArchives(on) {
    $('archives').checked = !!on;
    const now = state.current;
    if (!now) { showStatus(); return Promise.resolve(false); }
    const request = JSON.parse(JSON.stringify(now.request));
    request.options = {...(request.options || {})};
    if (on) request.options.include_archives = true; else delete request.options.include_archives;
    return render(now.selection, request);
  }
  // The held reply's own content format: a pasted raw_text specification is painted as plain text, as the dock does.
  function contentFormat(held) {
    const value = held.request?.value, asked = value && typeof value === 'object' ? value.content_format : null;
    return (held.spec?.content_format || asked) === 'raw_text' ? 'raw_text' : 'markdown';
  }

  // ---- paint: the shared renderer's HTML, straight into the document (no sanitizer, no escaping, no frame)
  // @beacon[
  //   id=opc-pane@glimpsePaint,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Paints the held Markdown through GlimpseMd.toHtml (Rendered, or Source with the shared find marks and count; a raw_text spec as plain text) and mounts that HTML from an inert parse: in Rendered its local image paths become /files/ URLs before any request, live folder/glob lines are expanded from their listings, paragraphs of images become contact sheets and the images are collected in heading groups for the lightbox (prepareImages); a document past 200 top-level blocks mounts its first 200 with a counted Show all, never cutting the held Markdown. Then settleImages: the live lines listed (/api/files/list: all again on a fresh reply, else those not listed yet) and the document painted again if that changed it, then the images' sizes read (readImageMeta; every path a listing did not size on a fresh reply, else only unknown ones); returns that promise, its .listed once the listing is painted. The row list is not repainted here (paintRows, once per reply).,
  // ]
  function paintHeld(fresh = false) {
    return paintDocument() ? settleImages(fresh) : Promise.resolve(false);
  }
  function paintDocument() {
    const held = state.held, doc = $('doc');
    if (!held) return false;
    if (!globalThis.GlimpseMd) { doc.textContent = 'Cannot paint: the shared renderer did not load (see above). Copy MD still copies the Markdown.'; showStatus(); return false; }
    const started = performance.now();
    const html = GlimpseMd.toHtml(held.markdown, {view: state.view, contentFormat: contentFormat(held), findRegex: state.find.regex,
      onSearchState: s => { state.find.count = s.count; state.find.current = s.current; }});
    state.painted = html;
    mountHtml(html);
    state.paintMs = Math.round(performance.now() - started);
    showFind(); showStatus(); showImages();
    return true;
  }
  // After a paint: the live gallery lines listed (on a fresh reply every one again, a folder an agent has just added to;
  // else only those not listed yet), the document painted again when that changed it (its scroll kept), then the sizes.
  // The promise resolves with the size read; its .listed once the listing is painted.
  function settleImages(fresh) {
    const held = state.held, current = () => state.held === held;
    if (fresh) state.listedNow = new Set();
    const asked = (state.live || []).filter(path => fresh || !state.listings.has(path)).slice(0, 256);
    const listed = (asked.length ? readListings(asked) : Promise.resolve(false)).then(changed => {
      if (changed && current() && state.view === 'rendered') { const box = $('scroll'), top = box.scrollTop; paintDocument(); box.scrollTop = top; }
      return changed;
    });
    const sized = listed.then(() => (current() ? readImageMeta(fresh) : false));
    sized.listed = listed;
    return sized;
  }
  function take(holder, count) {
    const part = document.createDocumentFragment(); let n = 0;
    while (holder.firstChild && (n < count || holder.firstChild.nodeType !== 1)) { if (holder.firstChild.nodeType === 1) n++; part.append(holder.firstChild); }
    return part;
  }
  function mountHtml(html) {
    const doc = $('doc'), parse = document.createElement('template');
    state.mountToken = {};  // stops an unfinished Show all of the previous paint
    // An inert parse (a template's content): nothing in it loads until it is mounted, so no image is ever requested at
    // the raw path its note names.
    parse.innerHTML = html;
    const holder = parse.content || parse;
    prepareImages(holder);
    state.total = holder.children.length;
    doc.replaceChildren(take(holder, BOUND));
    state.rest = holder.firstChild ? holder : null;
    showMore();
  }

  // ---- images: the lightbox module beside the shared renderer (GlimpseLightbox, /nexus-md/glimpse-lightbox.js, with the
  // vendored PhotoSwipe) maps, groups and shows them; this page supplies the /files/ URLs, the sizes and the chip
  const lightboxApi = () => globalThis.GlimpseLightbox || null;
  const filesUrl = path => '/files' + path.split('/').map(encodeURIComponent).join('/');
  // The whole document (the part past the 200-block bound included), before it goes live: local paths to /files/…,
  // each live folder/glob line as the files it listed (a line not listed yet waits as its labelled tile), contact sheets,
  // the heading groups, and whether it is nothing but images. Source and raw_text show no images.
  function prepareImages(holder) {
    const lb = lightboxApi();
    state.images = null; state.imageOnly = false; state.imagesUnavailable = false; state.live = [];
    if (state.view !== 'rendered' || !state.held || contentFormat(state.held) !== 'markdown') return;
    if (!lb) { state.imagesUnavailable = !!holder.querySelector?.('img'); return; }
    lb.mapImages(holder, filesUrl);
    if (typeof lb.livePaths === 'function') {
      state.live = lb.livePaths(holder);
      if (state.live.length) lb.expandLive(holder, path => state.listings.get(path) || null, filesUrl);
    }
    lb.sheets(holder);
    state.images = lb.collect(holder);
    state.imageOnly = state.images.length > 0 && lb.imageOnly(holder);
    if (state.imageMeta.size) lb.applySizes(holder, path => state.imageMeta.get(path));
  }
  const imageCount = () => (state.images || []).reduce((n, group) => n + group.images.length, 0);
  // The chip beside the status lines (never folded with the Controls): 🖼️ 145 images · 3 groups; a tap opens the lightbox.
  // Beside it on a wide page, ⧉ Side by side for a document with two groups or more.
  function showImages() {
    const count = imageCount(), groups = state.images?.length || 0;
    $('images').hidden = !count && !state.imagesUnavailable;
    $('images-open').hidden = !count;
    $('images-compare').hidden = !compareOffered();
    $('images-text').textContent = count ? fmt(count) + (count === 1 ? ' image' : ' images') + (groups > 1 ? ' · ' + fmt(groups) + ' groups' : '') : '';
    const cut = state.live.map(path => state.listings.get(path)).filter(Boolean), more = cut.some(listing => listing.truncated);
    $('images-note').textContent = state.imagesUnavailable ? '🖼️ The image lightbox did not load (/nexus-md/glimpse-lightbox.js): the pane server needs its restart once.'
      : more ? ' The folders and globs list ' + fmt(cut.reduce((n, l) => n + (l.total || l.files.length), 0)) + ' images; the first ' + fmt(cut.reduce((n, l) => n + l.files.length, 0)) + ' are shown.' : '';
  }
  // Each local image's existence and size (POST /api/files/meta, one call per document): sizes the lightbox before a
  // slide opens and turns a missing file into a labelled tile. A failed read leaves the lightbox to measure each image.
  // On a fresh reply every path is read again, except the files its live listings have just sized.
  async function readImageMeta(all) {
    const paths = [...new Set((state.images || []).flatMap(group => group.images.map(image => image.path)).filter(Boolean))]
      .filter(path => all ? !state.listedNow.has(path) : !state.imageMeta.has(path));
    if (!paths.length) return true;
    try {
      const response = await fetch('/api/files/meta', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths})});
      const data = await response.json().catch(() => null);
      if (!response.ok || !Array.isArray(data?.files)) return false;
      for (const row of data.files) if (row && typeof row.path === 'string') state.imageMeta.set(row.path, row);
    } catch (_) { return false; }
    const lb = lightboxApi(), lookup = path => state.imageMeta.get(path);
    lb?.applySizes($('doc'), lookup);
    if (state.rest) lb?.applySizes(state.rest, lookup);
    state.lightbox?.refresh();
    return true;
  }
  // @beacon[
  //   id=opc-pane@glimpseReadListings,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Lists the document's live gallery lines (a folder ending in /, or a glob such as …/shots/*.png) in one POST /api/files/list: each line's listing is held by its path (the image files in the pane's natural name order, an empty list, or the error its tile shows; a pane server without the route says it needs its restart once), each listed file's row is its size for the lightbox; resolves whether what the document shows changed, so settleImages paints it again only then.,
  // ]
  async function readListings(paths) {
    let rows = null, failure = '';
    try {
      const response = await fetch('/api/files/list', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths})});
      const data = await response.json().catch(() => null);
      if (response.ok && Array.isArray(data?.lists)) rows = data.lists;
      else failure = response.status === 404 ? 'could not list it: the pane server needs its restart once' : 'could not list it: ' + (data?.error || 'HTTP ' + response.status);
    } catch (e) { failure = 'could not list it: ' + e.message; }
    let changed = false;
    paths.forEach((path, i) => {
      const row = rows ? rows.find(r => r?.path === path) || rows[i] : null;
      const files = Array.isArray(row?.files) ? row.files.filter(f => f && typeof f.path === 'string') : [];
      const listing = row ? {files, total: Number(row.total) || files.length, truncated: row.truncated === true, error: row.error || ''} : {files: [], total: 0, truncated: false, error: failure || 'could not list it'};
      for (const file of files) { state.imageMeta.set(file.path, file); state.listedNow.add(file.path); }
      const before = state.listings.get(path), names = l => l.files.map(f => f.path).join('\n') + '\n' + l.error;
      if (!before || names(before) !== names(listing)) changed = true;
      state.listings.set(path, listing);
    });
    return changed;
  }
  // A new document that is nothing but images (a gallery) opens straight into the lightbox, once its live folders are
  // listed and its sizes are in (or after a moment without them); Back then lands on its contact sheet. Not while this
  // page is out of sight.
  async function autoOpen(seq, sized) {
    if (state.view !== 'rendered') return false;
    await sized.listed;  // a live folder's images are part of what the gallery holds
    if (seq !== state.seq || !state.imageOnly) return false;
    await Promise.race([sized, new Promise(resolve => setTimeout(resolve, 1500))]);
    const visible = !document.hidden && (document.documentElement?.clientHeight || 0) > 0;
    if (seq !== state.seq || !state.imageOnly || state.lightbox?.isOpen() || !visible) return false;
    return openLightbox(0);
  }
  // @beacon[
  //   id=opc-pane@glimpseOpenLightbox,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Opens View's image lightbox (GlimpseLightbox.open, PhotoSwipe) at image index on the document's collected groups as one sequence (from the chip, a tap on an image or tile, or a gallery's automatic opening), sized from /api/files/meta; on /m it asks the page around for the whole screen (opcViewImmersive true, then false; a maximized View then asks for it again, opcViewMaximized), Back closes it (its own history entry) and the last image seen is scrolled into view on the contact sheet; an open lightbox just moves there. Without the library it says so.,
  // ]
  function openLightbox(index = 0) {
    const lb = lightboxApi(), groups = state.images;
    if (!lb || !groups?.length) return false;
    if (state.lightbox?.isOpen()) { state.lightbox.goTo(index); return true; }
    const immersive = aroundFn('opcViewImmersive'), whole = embed === 'phone' ? aroundFn('opcViewMaximized') : null;
    // Closing gives /m's screen back; a maximized View takes it again at once.
    state.lightbox = lb.open(groups, {start: index, sizes: image => (image.path ? state.imageMeta.get(image.path) : null),
      onImmersive: on => { immersive?.(on); if (!on && maximizedNow()) whole?.(true); }, onClose: (_last, image) => revealImage(image)});
    if (!state.lightbox) note('The image lightbox could not open: its library (/nexus-md/photoswipe/photoswipe.umd.min.js) did not load; the pane server needs its restart once.', true);
    return !!state.lightbox;
  }
  // Side by side is a wide screen's: offered on the desktop (never on a phone or in /m's frame) for two groups or more.
  function compareOffered() { return typeof lightboxApi()?.compare === 'function' && (state.images?.length || 0) >= 2 && !narrowPage(); }
  // @beacon[
  //   id=opc-pane@glimpseOpenCompare,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Opens View's side-by-side viewer (GlimpseLightbox.compare; the ⧉ Side by side button beside the 🖼️ chip, offered on a wide page only, never on a phone or in /m's frame, for a document of two groups or more, live folders and estates included) on the document's collected groups, the first two or three shown, sized from /api/files/meta; a click on a panel's image opens it in View's own lightbox over it (openLightbox), whose Back and Esc return to the panels; a new document closes it.,
  // ]
  function openCompare() {
    if (!compareOffered()) return false;
    if (state.compare?.isOpen()) return true;
    state.lightbox?.close();
    const viewer = lightboxApi().compare(state.images, {sizes: image => (image.path ? state.imageMeta.get(image.path) : null),
      onOpen: index => openLightbox(index), onClose: () => { if (state.compare === viewer) state.compare = null; }});
    state.compare = viewer;
    return !!viewer;
  }
  // Back on the contact sheet, the last image seen is on screen (scrolled inside the document only, never the page).
  function revealImage(image) {
    const el = image?.element, box = $('scroll');
    if (!el?.isConnected || !box.contains?.(el)) return;
    const r = el.getBoundingClientRect(), b = box.getBoundingClientRect();
    if (r.top < b.top || r.bottom > b.bottom) box.scrollTop += r.top - b.top - (box.clientHeight - r.height) / 2;
  }
  async function showAll() {
    const token = state.mountToken, doc = $('doc');
    while (state.rest?.firstChild && state.mountToken === token) {
      doc.append(take(state.rest, BATCH)); showMore(true);
      await pause();  // yield between batches: the page stays responsive
    }
    if (state.mountToken === token) { state.rest = null; showMore(); }
  }
  function showMore(working = false) {
    const more = $('more'), shown = $('doc').children.length;
    more.hidden = !state.rest;
    $('more-text').textContent = 'Showing ' + fmt(shown) + ' of ' + fmt(state.total) + ' blocks' + (working ? '…' : ' · the whole document is held; Copy MD copies all of it');
    $('show-all').disabled = working;
  }

  // ---- the row list: the daemon's rows painted by the extension's own painter; a tap renders that row's focused view
  // @beacon[
  //   id=opc-pane@glimpseRows,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Paints the held reply's daemon rows above the document with GlimpseMd.renderRowList only (the held spec's sort for timestamps and day lines; the rendered focus selected by its full uuid/ancestor_path/occurrence identity); no list without rows or without the shared export. Every row is painted; past 200 the later rows are hidden with a counted Show all (display only, no fetch). A tap renders that row's focus through the same render and asks for its body on a phone's default compact view; hand-ticked Headings only or a wide page keeps the spec's setting.,
  // ]
  function paintRows(keepScroll = false) {
    const held = state.held, box = $('rows-box'), list = $('rows'), painter = globalThis.GlimpseMd?.renderRowList;
    if (!held?.rows || typeof painter !== 'function') { box.hidden = true; list.replaceChildren(); $('rows-more').hidden = true; paintSplits(); return; }
    const top = keepScroll ? list.scrollTop : 0, focus = held.selection.focus, rows = held.rows;
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    const index = focus ? rows.findIndex(r => r.uuid === focus.uuid && r.occurrence === focus.occurrence && same(r.ancestor_path, focus.ancestor_path)) : -1;
    painter(list, rows, {sortBy: held.spec?.sort_by || held.request.options?.sort_by || 'none',
      sortDir: held.spec?.sort_direction || held.request.options?.sort_direction || 'asc',
      selected: new Set(index < 0 ? [] : [index]), primary: index, onClick: i => tapRow(held, i)});
    box.hidden = false;
    $('rows-count').textContent = fmt(rows.length) + (rows.length === 1 ? ' row' : ' rows');
    list.scrollTop = top;
    boundRows();
    paintSplits();
  }
  // The first ROW_BOUND rows show; the later painted rows and day lines are hidden, never removed or left unpainted.
  function boundRows() {
    const rows = state.held?.rows || [], over = rows.length > ROW_BOUND, cut = over && !state.rowsAll ? rows[ROW_BOUND - 1]._rowEl : null;
    let after = false;
    for (const node of $('rows').children) { node.hidden = after; if (node === cut) after = true; }
    $('rows-more').hidden = !over;
    $('rows-more-text').textContent = state.rowsAll ? 'Showing all ' + fmt(rows.length) + ' rows' : 'Showing ' + fmt(ROW_BOUND) + ' of ' + fmt(rows.length) + ' rows';
    $('rows-all').textContent = state.rowsAll ? 'Show first ' + fmt(ROW_BOUND) : 'Show all';
  }
  function toggleAllRows() { state.rowsAll = !state.rowsAll; boundRows(); }
  // A tap: deep-copy the held spec and replace focus with the daemon's occurrence triple. A phone's default compact
  // view also asks for the body; hand-ticked Headings only or a wide page keeps the spec's setting. Same render and list.
  function tapRow(held, index) {
    const row = held.rows[index];
    if (!row) return;
    if (!held.spec) { note('This list came without a render specification, so a row cannot be opened from it.', true); return; }
    const focus = JSON.parse(JSON.stringify({mode: 'row', uuid: row.uuid, ancestor_path: row.ancestor_path, occurrence: row.occurrence}));
    const value = JSON.parse(JSON.stringify(held.spec)); value.focus = focus;
    if (narrowPage() && state.headingsChoice !== true) value.headings_only = false;
    // Labels describe the focused request; the checkbox remains the chosen base document's option.
    const view = {...held.view, headings_only: value.headings_only};
    const base = held.selection.base || held.selection, name = String(row.name ?? row.text ?? '').trim() || '(untitled)';
    void render({label: base.label + ' › ' + name, kind: 'parcel', value: base.value, body: base.body, base, focus, view},
      {kind: 'parcel', value, options: {include_rows: true, ...($('archives').checked ? {include_archives: true} : {})}});
  }

  // ---- Rendered | Source and Source find (the shared compiler's grammar; marks and count from sourceHtml)
  function setView(view) {
    if (view !== 'rendered' && view !== 'source') return;
    state.view = view;
    $('rendered').setAttribute('aria-pressed', String(view === 'rendered')); $('source').setAttribute('aria-pressed', String(view === 'source'));
    $('scroll').scrollTop = 0;
    paintHeld(); showFind();
    if (view === 'source') revealCurrent();
  }
  function setFind(query) {
    const compiled = globalThis.GlimpseMd ? GlimpseMd.compileSourceSearch(query) : {ok: false, error: 'renderer missing'};
    state.find.query = query;
    state.find.error = compiled.ok ? '' : (compiled.error || 'bad query');
    state.find.regex = compiled.ok ? compiled.regex : null;
    state.find.count = 0; state.find.current = -1;
    if (state.view !== 'source' && query.trim()) setView('source');  // find is Source find
    else { paintHeld(); revealCurrent(); }
    showFind();
  }
  function navigate(dir) {
    const f = state.find;
    if (state.view !== 'source') { if (!f.regex) return; setView('source'); }
    if (!f.regex || f.count <= 0) return;
    let i = f.current + (dir < 0 ? -1 : 1);
    if (i < 0) i = f.count - 1;
    if (i >= f.count) i = 0;
    f.current = i;
    const marks = $('doc').querySelectorAll('mark.glimpse-dock-src-hit');
    marks.forEach(m => m.classList.remove('glimpse-dock-src-hit--current'));
    marks[i]?.classList.add('glimpse-dock-src-hit--current');
    reveal(marks[i]); showFind();
  }
  function revealCurrent() { if (state.view === 'source' && state.find.current >= 0) reveal($('doc').querySelectorAll('mark.glimpse-dock-src-hit')[state.find.current]); }
  // Scroll the match into the middle of the document (and of its own sideways scroller), never the page.
  function reveal(node) {
    if (!node) return;
    const box = $('scroll'), r = node.getBoundingClientRect(), b = box.getBoundingClientRect();
    box.scrollTop += r.top - b.top - (box.clientHeight - r.height) / 2;
    // Sideways only when the match lies past the block's first screen width; otherwise lines keep their start.
    const pre = node.closest('pre');
    if (pre && pre.scrollWidth > pre.clientWidth) {
      const x = r.left - pre.getBoundingClientRect().left + pre.scrollLeft;
      pre.scrollLeft = x + r.width < pre.clientWidth - 16 ? 0 : x - pre.clientWidth / 3;
    }
  }
  function showFind() {
    const f = state.find, count = $('count');
    count.dataset.error = String(!!f.error);
    count.textContent = f.error ? f.error : !f.query.trim() ? '' : state.view !== 'source' ? 'Source' :
      f.count ? (f.current + 1) + ' / ' + fmt(f.count) : f.regex ? 'no matches' : '';
    $('prev').disabled = $('next').disabled = !(f.regex && (state.view !== 'source' || f.count > 0));
  }

  // ---- status: what is shown, from which request, and when; never a stale document passed off as new
  function showStatus() {
    const held = state.held, asof = $('asof');
    if (held) {
      const o = held.view || held.request.options || {};
      asof.textContent = (state.error ? 'Last successful: ' : '') + held.label + ' · Rendered at ' + clock(held.renderedAt) +
        ' · daemon cache snapshot · ' + applied(o) + ' · ' + fmt(held.bytes) + ' bytes';
      asof.dataset.last = String(!!state.error);
      const compact = [o.headings_only && 'headings only', o.recent && 'recent ' + o.recent].filter(Boolean);
      $('compact').hidden = !compact.length;
      $('compact-text').textContent = 'Compact view: ' + compact.join(' · ') + ', not the whole document.';
      $('current').textContent = held.label;
    } else if (!state.error && !state.busy) asof.textContent = 'Choose a Dock or a slice group, or paste a handle.';
    else asof.textContent = '';
    // The as-of line sits in the controls; with them closed, the error itself says the document is the last successful one.
    $('error').hidden = !state.error;
    $('error').textContent = state.error ? '⚠ ' + state.error.label + ' · ' + state.error.message + (held ? '\nStill showing the last successful: ' + held.label : '') : '';
    // The daemon's status lines, but its agent-facing archives footer becomes the plain line with its one tap below.
    const notices = (held?.notices || []).filter(line => !ARCHIVES_LINE.test(line));
    $('notices').hidden = !notices.length;
    $('notices').textContent = notices.join('\n');
    const left = held?.archivesLeftOut || 0, included = !!held?.archives;
    $('arch').hidden = !(left || included);
    $('arch-text').textContent = included ? '📦 Archives included (read-only, marked 📦)' : '📦 ' + fmt(left) + ' archived subtree' + (left === 1 ? ' was' : 's were') + ' left out';
    $('arch-toggle').textContent = included ? 'Leave them out' : 'Include them';
    // A named estate opened from its handle: what it is, and one tap to the node itself (or back to the estate)
    const estate = held?.selection?.estateOf, rawEstate = held?.selection?.raw && held.parcels;
    const parcels = n => fmt(n) + (n === 1 ? ' parcel' : ' parcels');
    $('estate').hidden = !(estate || rawEstate);
    $('estate-text').textContent = estate ? '🏛️ Estate · ' + estate.name + ' · ' + parcels(estate.parcels)
      : rawEstate ? '🏛️ This node is a named estate (its note lists ' + parcels(held.parcels) + ')' : '';
    $('estate-toggle').textContent = estate ? 'Show the node' : 'Show the estate';
    $('where').hidden = !nodeHandle();
    $('copy').disabled = !held;
    $('refresh').disabled = !state.current;
    paintControls();
    paintMax();
  }

  // ---- Copy MD: the whole held Markdown, exactly as the daemon returned it
  function copyText(text, done, failed) {
    const fallback = () => {
      const area = document.createElement('textarea'); area.value = text; area.setAttribute('readonly', ''); area.style.position = 'fixed'; area.style.opacity = '0';
      document.body.append(area); area.select(); let ok = false;
      try { ok = document.execCommand('copy'); } catch (_) { /* reported below */ }
      area.remove(); if (ok) done(); else failed();
    };
    try { navigator.clipboard.writeText(text).then(done, fallback); } catch (_) { fallback(); }
  }
  function copy() {
    const held = state.held;
    if (!held) return note('Nothing rendered yet.');
    copyText(held.markdown, () => note('Copied the whole Markdown · ' + fmt(held.bytes) + ' bytes'),
      () => note('Copy unavailable in this browser · use Source and select the text', true));
  }

  // ---- the controls: one thin bar over the chooser, the toolbar and the as-of line (the history rows above it never
  // fold). Open or closed is remembered on this device; while nothing is shown (and no render is on its way) it stands
  // open by itself, until a tap says otherwise. A root from outside View never opens it.
  const CTL_KEY = 'pane_view_controls_open_v1:' + embed;
  const ctl = {open: (() => { try { return globalThis.localStorage?.getItem(CTL_KEY) === '1'; } catch (_) { return false; } })(), tappedEmpty: false};
  const empty = () => !state.held && !state.busy;
  function controlsShown() { return ctl.open || (empty() && !ctl.tappedEmpty); }
  function paintControls() {
    const shown = controlsShown();
    $('ctl-body').hidden = !shown;
    $('ctl-toggle').setAttribute('aria-expanded', String(shown));
    paintSplits();
  }
  function toggleControls(open = !controlsShown()) {
    ctl.open = !!open;
    if (empty()) ctl.tappedEmpty = true;
    try { globalThis.localStorage?.setItem(CTL_KEY, ctl.open ? '1' : '0'); } catch (_) { /* not remembered */ }
    paintControls();
    return ctl.open;
  }
  // A new root chosen in the chooser (a picker row, a pasted handle, a spec): what is rendered was changed by hand, so
  // the bar folds away and the document takes the room (the chooser itself folds after every render, as before).
  function chosenHere(rendering) { if (ctl.open) toggleControls(false); else paintControls(); return rendering; }

  // ---- the three modes: Full (the Controls open), Collapsed (the Controls folded) and Maximized (the document fills View;
  // only ‹ the current entry › stays, with its restore button). Maximize sits at the right of the ‹ › row in every mode,
  // kept on this device; it applies while a document is shown (an empty View keeps its chooser in reach). In /m it asks
  // the page around for the whole screen (opcViewMaximized), as the image lightbox does (opcViewImmersive).
  const MAX_KEY = 'pane_view_max_v1:' + embed;
  const MAXIMIZE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const RESTORE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const maxed = {on: (() => { try { return globalThis.localStorage?.getItem(MAX_KEY) === '1'; } catch (_) { return false; } })(), shown: null};
  const maximizedNow = () => maxed.on && !!state.held;
  function paintMax() {
    const now = maximizedNow(), button = $('max'), page = document.documentElement;
    if (page?.dataset) page.dataset.viewMax = String(now);
    const label = maxed.on ? 'Restore: the root, Recent roots and the Controls again' : 'Maximize: only the document and ‹ ›';
    if (button.getAttribute('aria-pressed') !== String(maxed.on)) button.innerHTML = maxed.on ? RESTORE : MAXIMIZE;
    button.setAttribute('aria-pressed', String(maxed.on)); button.setAttribute('aria-label', label);
    button.title = label + (maxed.on ? '' : ' (the same button brings everything back)');
    if (now === maxed.shown) return;
    maxed.shown = now;
    if (embed === 'phone') aroundFn('opcViewMaximized')?.(now);
    history?.paint();  // row 2 names the root itself while the root widget is away
    applySplits();
  }
  // @beacon[
  //   id=opc-pane@glimpseToggleMaximized,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=View's maximize button (at the right of the ‹ current entry › row, in every mode): Maximized hides the root widget, Recent roots, the Controls, the row list and the status lines (a render error still shows) so the document fills View, the ‹ › row naming the root itself; the same button restores Full or Collapsed as the Controls were. Kept per device (pane_view_max_v1:‹embed›), applied while a document is shown; in /m the page around takes the whole screen (opcViewMaximized: top bar and tabs away), asked again when the View tab is shown (shown) and after the image lightbox gives the screen back.,
  // ]
  function toggleMaximized(on = !maxed.on) {
    maxed.on = !!on;
    try { globalThis.localStorage?.setItem(MAX_KEY, maxed.on ? '1' : '0'); } catch (_) { /* this page only */ }
    history?.closeAll();
    paintMax();
    return maxed.on;
  }
  // /m shows the View tab again (its whole-screen frame may have been given back meanwhile): a maximized View asks again.
  function shown() { maxed.shown = null; paintMax(); }

  // ---- the two dividers: under the open Controls (how tall they may get before scrolling inside themselves) and between
  // the row list and the document. A thumb or a mouse drags each (pointer capture; touch-action none on the handle), the
  // keys move it (Up/Down 5%, Home/End). Each sets a max-height, so a short list or folded chooser takes only its own
  // height and the document the rest; a drag stops at the content's own height and keeps the document 64 px. Remembered
  // on this device as a fraction of the page's height (localStorage pane_view_splits_v1:‹embed›).
  const SPLIT_KEY = 'pane_view_splits_v1:' + embed, MIN_PART = 40, MIN_DOC = 64;
  const SPLITS = {ctl: {target: 'ctl-body', prop: '--g-ctl-max'}, rows: {target: 'rows', prop: '--g-rows-max'}};
  const splits = (() => { try { const v = JSON.parse(globalThis.localStorage?.getItem(SPLIT_KEY) || '{}'); return v && typeof v === 'object' && !Array.isArray(v) ? v : {}; } catch (_) { return {}; } })();
  const pageHeight = () => $('page').clientHeight || 0;
  const boxHeight = node => node.getBoundingClientRect?.().height || 0;
  function setSplit(name, px) { $('page').style.setProperty?.(SPLITS[name].prop, Math.round(px) + 'px'); }
  // The stored fractions, as px of this page's height; then the document keeps its 64 px (a rotation, a smaller frame).
  function applySplits() {
    const h = pageHeight(); if (!h) return;
    for (const name of Object.keys(SPLITS)) { const f = Number(splits[name]); if (f > 0 && f < 1) setSplit(name, Math.max(MIN_PART, f * h)); }
    fitSplits();
  }
  // The row list gives way first, then the controls; nothing stored changes.
  function fitSplits() {
    let deficit = MIN_DOC - boxHeight($('scroll'));
    if (!pageHeight() || deficit <= 0) return;
    for (const name of ['rows', 'ctl']) {
      const target = $(SPLITS[name].target), h = boxHeight(target), cut = Math.min(deficit, Math.max(0, h - MIN_PART));
      if (target.hidden || cut <= 0) continue;
      setSplit(name, h - cut); deficit -= cut;
      if (deficit <= 0) break;
    }
  }
  // A divider shows only beside what it sizes: the open Controls; a row list shown and unfolded.
  function paintSplits() {
    $('ctl-split').hidden = $('ctl-body').hidden;
    $('rows-split').hidden = $('rows-box').hidden || !$('rows-box').open;
    fitSplits();
  }
  // @beacon[
  //   id=opc-pane@glimpseSplitDivider,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=One of View's two horizontal dividers (below the open Controls, between the row list and the document) for a thumb on iOS WebKit or a mouse: pointer capture on a 26 px handle with touch-action none, the drag setting its target's max-height in px (at least 40, at most the content's own height and what keeps the document 64 px), Up/Down 5% steps and Home/End; the result is remembered per device as a fraction of the page height.,
  // ]
  function splitDivider(name) {
    const divider = $(name + '-split'), target = () => $(SPLITS[name].target);
    let drag = null;
    // How tall the target may get now: its content's own height, and what leaves the document MIN_DOC.
    const natural = node => (node.scrollHeight || 0) + Math.max(0, (node.offsetHeight || 0) - (node.clientHeight || 0));
    const limit = () => { const node = target(), h = boxHeight(node); return Math.max(MIN_PART, Math.min(natural(node) || h, h + Math.max(0, boxHeight($('scroll')) - MIN_DOC))); };
    const set = px => { const value = Math.max(MIN_PART, Math.min(limit(), px)); setSplit(name, value); divider.setAttribute('aria-valuenow', String(Math.round(value))); return value; };
    const save = px => {
      const h = pageHeight(); if (!h) return;
      splits[name] = Math.round(px / h * 1000) / 1000;
      try { globalThis.localStorage?.setItem(SPLIT_KEY, JSON.stringify(splits)); } catch (_) { /* this page only */ }
    };
    const finish = () => {
      if (!drag) return;
      const px = drag.px; drag = null;
      divider.classList.remove('dragging'); document.body?.classList?.remove('g-dragging');
      if (px !== null) save(px);
    };
    divider.addEventListener('pointerdown', event => {
      if (event.button) return;  // the main button; a touch or a pen reports 0
      event.preventDefault(); divider.focus?.({preventScroll: true});
      try { divider.setPointerCapture?.(event.pointerId); } catch (_) { /* the drag still works over the handle */ }
      drag = {id: event.pointerId, y: event.clientY, start: boxHeight(target()), max: limit(), px: null};
      divider.classList.add('dragging'); document.body?.classList?.add('g-dragging');
    });
    divider.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      event.preventDefault();
      drag.px = Math.max(MIN_PART, Math.min(drag.max, drag.start + event.clientY - drag.y));
      setSplit(name, drag.px); divider.setAttribute('aria-valuenow', String(Math.round(drag.px)));
    });
    for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) divider.addEventListener(type, finish);
    divider.addEventListener('keydown', event => {
      const h = pageHeight(), now = boxHeight(target());
      let px = null;
      if (event.key === 'ArrowUp' || event.key === 'ArrowDown') px = now + (event.key === 'ArrowUp' ? -1 : 1) * h * 0.05;
      else if (event.key === 'Home') px = MIN_PART;
      else if (event.key === 'End') px = limit();
      if (px === null) return;
      event.preventDefault(); save(set(px));
    });
  }

  // ---- the catalog (favorite Docks, slice groups): read once; a failure never blocks a pasted handle. Its errors show
  // under the document's status lines, outside the controls bar, so a closed bar never hides them. The Dock list's two
  // switches (All Docks, Local Docks; the full-screen picker) are remembered on this device and re-read the Docks alone.
  const DOCKS_KEY = 'pane_view_dock_list_v1:' + embed;
  const dockList = (() => { try { const v = JSON.parse(globalThis.localStorage?.getItem(DOCKS_KEY) || '{}'); return {all: v?.all === true, local: v?.local === true}; } catch (_) { return {all: false, local: false}; } })();
  state.docksShown = {all: false, local: false};  // what the Dock list on screen is (the catalog's: the favorites)
  const dockLabel = shown => (shown.all ? 'All' : 'Favorite') + (shown.local ? ' + local' : '') + ' Docks';
  async function readCatalog() {
    const errors = $('catalog-errors');
    try {
      const response = await fetch('/api/glimpse/catalog');
      const data = await response.json().catch(() => ({error: 'HTTP ' + response.status + ' with no readable reply'}));
      if (!response.ok) throw new Error(data.error || 'HTTP ' + response.status);
      state.catalog = {docks: data.docks || [], groups: data.groups || []};
      errors.textContent = (data.errors || []).map(e => (e.source === 'groups' ? 'Slice groups' : 'Docks') + ' unavailable: ' + e.error).join('\n');
    } catch (e) {
      state.catalog = {docks: [], groups: []};
      errors.textContent = 'Docks and groups unavailable (a pasted handle still works) · ' + e.message;
    }
    paintCounts();
    paintPickers();
    if (dockList.all || dockList.local) await readDocks();
  }
  function paintCounts() {
    $('dock-count').textContent = fmt(state.catalog.docks.length);
    $('dock-label').textContent = dockLabel(state.docksShown); $('dock').setAttribute('aria-label', dockLabel(state.docksShown));
    $('group-count').textContent = fmt(state.catalog.groups.filter(g => !opcViewPickers.parseSliceGroupName(g.name).isExpander).length);
  }
  // @beacon[
  //   id=opc-pane@glimpseReadDocks,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Re-reads View's Dock list as the full-screen picker's switches ask (POST /api/glimpse/docks {all, local}: dock.list favorites_only false and/or include_local, rows with depth and ⭐/Ⓛ marks) and repaints both Dock pickers; the newest read wins; a failure keeps the list on screen, puts the switches back to what it is and says so (a 404 means the pane server still needs its restart).,
  // ]
  async function readDocks() {
    const seq = ++sheet.reads, asked = {...dockList};
    sheet.status = 'Reading ' + dockLabel(asked) + '…'; paintSheet();
    let error = '';
    try {
      const response = await fetch('/api/glimpse/docks', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(asked)});
      const data = await response.json().catch(() => ({error: 'HTTP ' + response.status + ' with no readable reply'}));
      if (!response.ok) throw new Error((data.error || 'HTTP ' + response.status) + (response.status === 404 ? ' (this list needs the pane server restarted once)' : ''));
      if (seq !== sheet.reads) return false;
      if (data.errors?.length) error = data.errors.map(e => e.error).join('\n');
      else { state.catalog = {...(state.catalog || {groups: []}), docks: Array.isArray(data.docks) ? data.docks : []}; state.docksShown = asked; }
    } catch (e) {
      if (seq !== sheet.reads) return false;
      error = e.message;
    }
    if (error) { Object.assign(dockList, state.docksShown); saveDockList(); }
    sheet.status = error ? '⚠ ' + dockLabel(asked) + ' unavailable, still showing ' + dockLabel(state.docksShown) + ': ' + error : '';
    if (state.catalog) { paintCounts(); paintPickers(); } else paintSheet();
    return !error;
  }
  function saveDockList() { try { globalThis.localStorage?.setItem(DOCKS_KEY, JSON.stringify(dockList)); } catch (_) { /* this page only */ } }
  function setDockList(key, on) { dockList[key] = !!on; saveDockList(); return readDocks(); }

  // ---- the two pickers (view-pickers.js): Docks nested under their parent Docks, slice groups in the extension's pipe
  // hierarchy; the open slice-group sections are remembered on this device, as the extension remembers them per session
  const OPEN_KEY = 'pane_view_group_sections_v1';
  const openSections = (() => { try { const v = JSON.parse(globalThis.localStorage?.getItem(OPEN_KEY) || '[]'); return new Set(Array.isArray(v) ? v.filter(p => typeof p === 'string') : []); } catch (_) { return new Set(); } })();
  function toggleSection(path) {
    if (openSections.has(path)) openSections.delete(path); else openSections.add(path);
    try { globalThis.localStorage?.setItem(OPEN_KEY, JSON.stringify([...openSections])); } catch (_) { /* not remembered */ }
    paintPickers();
  }
  // The chosen document's Dock or group (its base, after a row tap), marked in its picker.
  function chosen(key) { const s = state.selection?.base || state.selection, v = s?.value; return v && typeof v === 'object' && typeof v[key] === 'string' ? v[key] : null; }
  // @beacon[
  //   id=opc-pane@glimpsePaintPickers,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Repaints View's two pickers, and the full-screen one when open, from the catalog (view-pickers.js paintDocks/paintGroups; the Docks as the switches last read them, ⭐ marked on an all-Docks list and Ⓛ on a local one): the current Dock or group marked, the remembered open slice-group sections; a row tap picks (pick), an expander tap opens or closes its section (toggleSection, kept in localStorage). Opening one compact picker closes the other.,
  // ]
  function paintPickers() {
    if (!state.catalog || !globalThis.opcViewPickers) return;
    opcViewPickers.paintDocks($('dock'), state.catalog.docks, {selected: chosen('dock'), marks: {favorite: state.docksShown.all, local: state.docksShown.local}, onPick: i => pick('dock', i)});
    opcViewPickers.paintGroups($('group'), state.catalog.groups, {selected: chosen('group'), expanded: openSections, onPick: i => pick('group', i), onToggle: toggleSection});
    paintSheet();
  }

  // ---- a picker full screen (the whole frame, so the whole phone screen): the same list in big rows, a filter, for the
  // Docks the two switches; a tap renders it and closes the sheet (Close, the backdrop or Escape close it too)
  const sheet = {kind: 'dock', filter: '', expandAll: false, reads: 0, status: ''};
  // @beacon[
  //   id=opc-pane@glimpsePickerSheet,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Opens View's Dock or slice-group picker over the whole frame (⚓ Docks / Groups on the Choose line, ⤢ on each compact picker): the catalog's list in big wrapped rows scrolled to the current one, a name filter (a Dock keeps the Docks it nests under; groups open every section), the Dock switches All Docks and Local Docks (readDocks) and Open every section for groups; a row tap closes it and renders that pick as a fresh root.,
  // ]
  function openSheet(kind) {
    sheet.kind = kind === 'group' ? 'group' : 'dock'; sheet.filter = ''; $('psheet-filter').value = '';
    $('psheet').hidden = false;
    paintSheet();
    $('psheet-list').querySelector?.('[aria-current="true"]')?.scrollIntoView?.({block: 'center'});
    if (!globalThis.matchMedia?.('(pointer: coarse)').matches) $('psheet-filter').focus?.();  // a keyboard at hand; never a phone's
  }
  function closeSheet() { $('psheet').hidden = true; }
  function paintSheet() {
    if ($('psheet').hidden || !globalThis.opcViewPickers) return;
    const dock = sheet.kind === 'dock', list = $('psheet-list'), catalog = state.catalog || {docks: [], groups: []}, q = sheet.filter.trim();
    $('psheet-docks').hidden = !dock; $('psheet-groups').hidden = dock;
    $('docks-all').checked = dockList.all; $('docks-local').checked = dockList.local; $('groups-open').checked = sheet.expandAll;
    let shown, total;
    if (dock) {
      const filter = opcViewPickers.dockFilter(catalog.docks, q);
      opcViewPickers.paintDocks(list, catalog.docks, {selected: chosen('dock'), marks: {favorite: state.docksShown.all, local: state.docksShown.local}, filter,
        onPick: i => { closeSheet(); void pick('dock', i); }});
      $('psheet-title').textContent = dockLabel(state.docksShown);
      total = catalog.docks.length; shown = filter ? filter.matches.size : total;
    } else {
      opcViewPickers.paintGroups(list, catalog.groups, {selected: chosen('group'), expanded: openSections, expandAll: sheet.expandAll, filter: q,
        onPick: i => { closeSheet(); void pick('group', i); }, onToggle: toggleSection});
      $('psheet-title').textContent = 'Slice groups';
      const leaves = catalog.groups.filter(g => !opcViewPickers.parseSliceGroupName(g.name).isExpander);
      total = leaves.length; shown = q ? leaves.filter(g => String(g.name || '').toLowerCase().includes(q.toLowerCase())).length : total;
    }
    $('psheet-count').textContent = (q ? fmt(shown) + ' of ' : '') + fmt(total);
    $('psheet-status').textContent = sheet.status || (!state.catalog ? 'Reading the catalog…' : q && !shown ? 'Nothing matches “' + q + '”.' : '');
  }
  // Keep the full real name and description already read by the picker; its short group label is source information.
  function pick(kind, index) {
    const row = (kind === 'dock' ? state.catalog?.docks : state.catalog?.groups)?.[Number(index)];
    if (!row || !history) return Promise.resolve(false);
    const metadata = {name: opcViewHistory.middle(row.name, opcViewHistory.NAME_MAX), description: opcViewHistory.descriptionLine(row.description),
      ...(typeof row.description === 'string' ? {metadataVersion: 1} : {})};
    return chosenHere(history.reroot({pick: kind, selection: kind === 'dock' ? {...metadata, label: 'Dock · ' + row.name, kind: 'parcel', value: {dock: row.handle}, body: false}
      : {...metadata, label: 'Group · ' + opcViewPickers.groupLabel(row.name), kind: 'parcel', value: {group: row.handle}, body: false}}));
  }

  // ---- the shared renderer's files: a plain message naming what failed, never a substitute renderer
  function checkAssets() {
    const failed = [...(window.glimpseAssetFailures || [])];
    if (!globalThis.GlimpseMd && !failed.some(u => u.includes('glimpse-md-render.js'))) failed.push('/nexus-md/glimpse-md-render.js (GlimpseMd missing)');
    if (!globalThis.marked && !failed.some(u => u.includes('marked.min.js'))) failed.push('/nexus-md/marked.min.js (marked missing)');
    if (!failed.length) return;
    const box = $('assets'); box.hidden = false;
    box.textContent = 'The shared renderer files did not all load: ' + failed.join(', ') + '.\nThe pane serves them from the NEXUS checkout set as ' +
      'nexus_checkout in ~/.config/opencode-companion/pane-glimpse.json; fix that, then restart the pane (bash transcription-pane/start.sh restart).';
    for (const url of failed) {
      if (!url.startsWith('/nexus-md/') || url.includes(' ')) continue;
      fetch(url).then(r => (r.ok ? null : r.json())).then(d => { if (d?.error) box.textContent += '\n' + url + ': ' + d.error; }).catch(() => {});
    }
  }

  // ---- the chooser: catalog picks render at once; a pasted handle reads as Node, Subtree or Dock; a spec is
  // sent as the Parcel or the Estate chosen here (never guessed)
  // One history entry on screen: a pick's own selection, or a handle read as Node, Subtree or Dock (into the handle box).
  // A handle read as Node is bare: its node decides, so a named estate renders as that estate (render).
  function renderEntry(entry) {
    if (entry.selection) return choose({...entry.selection, entry: entry.id});
    const how = ['node', 'subtree', 'dock'].includes(entry.how) ? entry.how : 'node';
    state.how = how; $('handle').value = entry.handle; radio($('handle-form'), 'how', how);
    return choose({label: {node: 'Node', subtree: 'Subtree', dock: 'Dock'}[how] + ' · ' + entry.handle, kind: 'parcel', value: {[how]: entry.handle}, body: how === 'node',
      entry: entry.id, ...(how === 'node' ? {bare: true} : {})});
  }
  // The handle box (a pasted handle): a fresh history rooted at it.
  function renderHandle() {
    const handle = $('handle').value.trim();
    if (!handle) { note('Paste a handle first.'); return Promise.resolve(false); }
    return chosenHere(history.reroot({handle, how: state.how, origin: {from: 'pasted'}}));
  }
  // @beacon[
  //   id=opc-pane@glimpseOpenHandle,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=A handle opened from outside View (handle-links.js through /m or the desktop pane, or ?handle=…&how=…&from=…&active=…&turn=…): a fresh history rooted at it, read as Node unless told otherwise (a node whose note lists parcels then renders as that named estate); origin says where it came from ({from:'programs', active: the tapped session's id} for a session row's socket; {from:'outside', where, turn} for a conversation, turn being the conversation turn it was tapped in, which then roots the history; a socket found by the page around shows its program position either way). origin.from 'view' (a handle tapped in this page's own document) adds an entry instead.,
  // ]
  function openHandle(handle, how = 'node', origin = null) {
    const h = String(handle || '').trim();
    if (!h) return Promise.resolve(false);
    const read = ['node', 'subtree', 'dock'].includes(how) ? how : 'node';
    if (origin?.from === 'view') return history.push(h, read);
    return history.reroot({handle: h, how: read, origin: origin || {from: 'outside'}});
  }
  // This View shown again (the desktop's 📄 View switch or link): a history another View of this page context changed
  // since (the desktop pane and its OpenCode-mode sheet share one) is taken up and its current entry rendered.
  function resume() {
    void history?.readOuter();  // Recent roots again: the other device may have added to them
    const again = history?.sync();
    return again ? renderEntry(again) : Promise.resolve(false);
  }
  // A handle tapped on this page (handle-links.js): in the root's pop-up a new root (its card's session the active one;
  // the root's own handle just shows the root again), anywhere else in View a new entry.
  function openFromPage(handle, context = null) {
    const node = context?.node || null;
    if (node?.closest?.('#g-anc')) { closeAncestors(); return history.push(handle, 'node'); }  // an ancestor, opened in View
    if (history.inPosition(node)) {
      const root = history.root(), key = opcViewHistory.handleKey;
      if (root && !root.selection && key(root.handle) && key(root.handle) === key(handle)) return history.go(0);
      return history.reroot({handle, how: 'node', origin: {from: 'position', active: history.positionSession(node)}});
    }
    return history.push(handle, 'node');
  }
  function bodyParcel(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    if (value.ref && value.kind === 'node' && value.how !== 'subtree') return true;
    const keys = Object.keys(value).filter(k => !['alias', 'title', 'root'].includes(k));
    return keys.length === 1 && (keys[0] === 'node' || keys[0] === 'symbols');
  }

  // ---- named estates: a node whose note is wholly parcel lines (estates [ap:D2XQXO], skill [ap:CDE6YO] §8), recognised
  // as the daemon's estate grammar recognises one parcel per line (glimpse_estate_payload's _parse_note_lines with
  // simple_form's validity): blank lines are fine, a leading "↩ moved from" / "↩ derived from" provenance is inert, the
  // note may be one fenced block or one whole JSON payload (a Saved-Tabs export); a bare name must be a slice group the
  // catalog lists (else, without a catalog, the node stays a node). One other line, and it is a plain node: never the
  // daemon's tolerant scan for JSON inside prose.
  const PARCEL_FORMS = ['group', 'dock', 'tags', 'symbols', 'node', 'subtree'], BLOB_KEYS = ['kind', 'group_id', 'root_uuid', 'lightning', 'selection'];
  const PARCEL_SETTINGS = ['roles', 'read', 'load', 'note', 'view', 'entries'], SPEC_KINDS = ['raw_root', 'slice', 'slice_override', 'lightning_rod'];
  const PARCEL_HANDLE = /^(?:\[ap:[A-Za-z0-9]+\]|\[[0-9a-f]{8}(?:[0-9a-f-]*[0-9a-f])?\]|[0-9a-f]{8}(?:[0-9a-f-]*[0-9a-f])?)$/i;
  const isObject = v => !!v && typeof v === 'object' && !Array.isArray(v), filled = v => typeof v === 'string' && !!v.trim();
  function looseJson(text) {
    try { return {ok: true, value: JSON.parse(text)}; } catch (_) { /* below */ }
    if (text.includes('\u00a0')) { try { return {ok: true, value: JSON.parse(text.replace(/\u00a0/g, ' '))}; } catch (_) { /* not JSON */ } }
    return {ok: false};
  }
  // A bare string: a handle (in a note it can only be a Dock; the daemon says so if it is not), else a slice-group name
  // the catalog lists (its id, its whole name, or its short name after the last |); a path or URI is never a parcel.
  function bareParcel(text, groups) {
    const s = String(text).trim();
    if (!s || s.includes('://') || /^[/~]/.test(s) || s.includes('\\')) return false;
    if (PARCEL_HANDLE.test(s)) return true;
    const low = s.toLowerCase(), short = name => String(name || '').split('|').pop().trim().toLowerCase();
    return (groups || []).some(g => g.handle === s || String(g.name || '').trim().toLowerCase() === low || short(g.name) === low);
  }
  // One decoded entry, valid as the daemon's _coerce_entry takes it: a qualified parcel (short, or the long {ref, kind}),
  // {lens}, a {spec} wrapper, a bare render_spec blob, or a string.
  function parcelEntry(v, groups) {
    if (typeof v === 'string') return bareParcel(v, groups);
    if (!isObject(v)) return false;
    const has = k => Object.prototype.hasOwnProperty.call(v, k), keys = Object.keys(v), kind = String(v.kind ?? '').trim().toLowerCase();
    if (has('ref') || kind === 'file' || kind === 'uri') {
      if (PARCEL_SETTINGS.some(has) || !['slice_group', 'dock', 'node'].includes(kind) || !filled(v.ref) || keys.some(k => !['ref', 'kind', 'how', 'descend', 'alias'].includes(k))) return false;
      if (has('how') && (kind !== 'node' || !['note', 'subtree'].includes(String(v.how).trim().toLowerCase()))) return false;
      return !has('descend') || kind === 'dock' && typeof v.descend === 'boolean';
    }
    if (has('lens')) return isObject(v.lens) && keys.every(k => ['lens', 'alias', 'title'].includes(k));
    const forms = PARCEL_FORMS.filter(has);
    if (has('descend') && !has('dock')) return false;
    if (forms.length && BLOB_KEYS.some(has)) return false;
    if (isObject(v.spec)) return true;
    if (!forms.length) return SPEC_KINDS.includes(kind) || ['group_id', 'root_uuid', 'lightning', 'selection'].some(k => v[k] != null);
    if (forms.length > 1 || PARCEL_SETTINGS.some(has)) return false;
    const form = forms[0], value = v[form];
    const allowed = form === 'group' ? ['group', 'alias', 'title', 'url'] : form === 'node' || form === 'subtree' ? [form, 'alias']
      : form === 'dock' ? ['dock', 'alias', 'descend', 'recursive'] : [form, 'alias', 'root'];
    if (keys.some(k => !allowed.includes(k))) return false;
    if (form === 'tags' || form === 'symbols') return Array.isArray(value) && value.length > 0 && value.every(filled) && filled(v.root);
    if (form === 'dock' && ['descend', 'recursive'].some(k => has(k) && typeof v[k] !== 'boolean')) return false;
    return filled(value);
  }
  // How many parcels a note lists, or 0 when it is not wholly a named estate.
  // @beacon[
  //   id=opc-pane@glimpseEstateLines,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Whether a node's note is wholly a named estate, as the daemon's one-parcel-per-line grammar reads it: blank lines and a leading ↩ provenance skipped, one whole fenced block or one whole JSON payload (a Saved-Tabs export) allowed, then every line a parcel (a qualified short or long form, {lens}, {spec}, a render_spec blob, a handle, or a slice-group name the catalog lists; never a path or URI); returns the parcel count, 0 for a plain node (one other line is enough; no tolerant scan for JSON inside prose).,
  // ]
  function estateLines(note, groups) {
    const lines = String(note || '').split(/\r?\n/);
    let start = 0;
    while (start < lines.length && (!lines[start].trim() || /^↩ (?:moved|derived) from /.test(lines[start]))) start++;
    let text = lines.slice(start).join('\n').trim();
    const fence = /^```[ \t]*[A-Za-z0-9_+.-]*[ \t]*\r?\n([\s\S]*?)```$/.exec(text);
    if (fence) text = fence[1].trim();
    if (!text) return 0;
    const whole = looseJson(text);  // one JSON value, a pretty-printed payload (a Saved-Tabs export) included, is not lines
    if (whole.ok && Array.isArray(whole.value)) return whole.value.length && whole.value.some(e => parcelEntry(e, groups)) ? whole.value.length : 0;
    if (whole.ok && isObject(whole.value)) return parcelEntry(whole.value, groups) ? 1 : 0;
    let count = 0;
    for (const raw of text.split('\n')) {
      const line = raw.trim();
      if (!line) continue;
      const json = looseJson(line);
      if (!json.ok && /[{[]/.test(line) && !PARCEL_HANDLE.test(line)) return 0;  // braces that are not JSON: prose
      if (Array.isArray(json.value) || !parcelEntry(json.ok ? json.value : line, groups)) return 0;
      count++;
    }
    return count;
  }
  // The parcels a Node read's note lists (the reply is "# ‹its name›" then the note), or 0.
  function estateParcels(held) {
    const markdown = String(held?.markdown || ''), name = /^#[ \t][^\n]*(?:\n|$)/.exec(markdown);
    return estateLines(name ? markdown.slice(name[0].length) : markdown, state.catalog?.groups || []);
  }
  // The estate a Node read named: the daemon's glimpse.estate on that node (its uuid, from the reply's row), labelled
  // Estate · ‹its name›; the node read stays at hand for one tap back (estateOf.node).
  // @beacon[
  //   id=opc-pane@glimpseEstateSelection,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The re-render of a tapped or pasted handle whose Node read is a named estate (render calls it when estateParcels counts parcels and Dan did not ask for the node itself): kind estate on the node's uuid, so the daemon renders every parcel with its own default read (no headings_only unless ticked), labelled Estate · ‹the node's name› with the parcel count; the history entry keeps the node's name, and the estate line's one tap (toggleEstate) shows the raw node or comes back. A gallery estate opens straight into the lightbox like a gallery node.,
  // ]
  function estateSelection(node, held, parcels) {
    const name = opcViewHistory.nameFrom(held) || node.value?.node || '';
    const handle = held.rows?.[0]?.uuid || node.value?.node;
    const {raw: _raw, ...plain} = node;
    return {label: 'Estate · ' + name, kind: 'estate', value: handle, body: false, entry: node.entry,
      estateOf: {node: plain, name, parcels, handle}};
  }
  // One tap: from the estate to the node itself (its note as written), or back.
  function toggleEstate() {
    const held = state.held, selection = held?.selection;
    if (selection?.estateOf) return choose({...selection.estateOf.node, raw: true});
    if (selection?.raw && held.parcels) return choose(estateSelection(selection, held, held.parcels));
    return Promise.resolve(false);
  }
  function renderSpec() {
    const text = $('spec').value.trim(), kind = state.specKind;
    if (!text) { note('Type a parcel or an estate first.'); return Promise.resolve(false); }
    let value = text;
    if (text.startsWith('{')) { try { const parsed = JSON.parse(text); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) value = parsed; } catch (_) { /* sent as typed; the pane says what is wrong */ } }
    const short = opcViewHistory.middle(text);
    return chosenHere(history.reroot({pick: kind === 'estate' ? 'estate' : 'parcel',
      selection: {label: (kind === 'estate' ? 'Estate · ' : 'Parcel · ') + short, kind, value, body: kind === 'parcel' && bodyParcel(value)}}));
  }
  function radio(group, attr, value) { for (const b of group.querySelectorAll('[role="radio"]')) b.setAttribute('aria-checked', String(b.dataset[attr] === value)); }

  // ---- where the node on screen lives: its ancestors, the daemon's node.ancestors chain (F3: H1 = the root, ⚓ ⭐ Ⓛ 🗺️ 📄
  // markers, the leaf UUID), painted by the shared renderer in a pop-up over View
  // The one node the document is about: a tapped row, else a Node / Subtree / Dock handle (picked, pasted or opened from
  // outside, or a parcel naming one), else a formal spec's raw root, else a named estate's own node; a slice group, a tag
  // or symbol selection or an estate typed as a spec has none.
  function nodeHandle(held = state.held) {
    const selection = held?.selection;
    if (!selection) return null;
    if (typeof selection.focus?.uuid === 'string' && selection.focus.uuid) return selection.focus.uuid;
    if (selection.estateOf) return selection.estateOf.handle || null;  // a named estate shown: where its node lives
    const value = (selection.base || selection).value;
    if (!value || typeof value !== 'object' || Array.isArray(value) || selection.kind === 'estate') return null;
    for (const key of ['node', 'subtree', 'dock']) if (typeof value[key] === 'string' && value[key].trim()) return value[key].trim();
    if (typeof value.ref === 'string' && value.ref.trim() && (value.kind === 'node' || value.kind === 'dock')) return value.ref.trim();
    if (typeof value.root_uuid === 'string' && value.root_uuid && !value.group_id && !value.group_name && !value.lightning) return value.root_uuid;
    return null;
  }
  const anc = {seq: 0, key: null, markdown: '', uuids: 'leaf'};
  // @beacon[
  //   id=opc-pane@glimpseAncestors,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The where button (beside the root card, shown while one node is rendered: nodeHandle) opens View's ancestors pop-up: POST /api/glimpse/ancestors {handle, uuids} reads the daemon's node.ancestors F3 Markdown (Leaf UUID, or Every level for a UUID under each ancestor) and paints it with GlimpseMd.toHtml; the newest read wins, the same chain is not read twice, a failure says why (a 404: the pane server still needs its restart); Copy copies the Markdown; a handle tapped in it opens in View and closes it.,
  // ]
  async function showAncestors(uuids = anc.uuids) {
    const handle = nodeHandle();
    if (!handle) return false;
    $('anc').hidden = false;
    anc.uuids = uuids === 'every-level' ? 'every-level' : 'leaf'; radio($('anc'), 'uuids', anc.uuids);
    $('anc-title').textContent = 'Where ' + opcViewHistory.shortHandle(handle) + ' lives';
    const key = handle + ' ' + anc.uuids;
    if (key === anc.key && anc.markdown) return true;  // already on screen
    const seq = ++anc.seq, doc = $('anc-doc');
    anc.key = key; anc.markdown = '';
    $('anc-status').textContent = 'Reading where it lives…'; doc.dataset.busy = 'true';
    try {
      const response = await fetch('/api/glimpse/ancestors', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({handle, uuids: anc.uuids})});
      const data = await response.json().catch(() => ({error: 'HTTP ' + response.status + ' with no readable reply'}));
      if (!response.ok) throw new Error((data.error || 'HTTP ' + response.status) + (response.status === 404 ? ' (this needs the pane server restarted once)' : ''));
      if (typeof data.markdown !== 'string') throw new Error('the reply carried no Markdown');
      if (seq !== anc.seq) return false;
      anc.markdown = data.markdown;
    } catch (e) {
      if (seq !== anc.seq) return false;
      anc.key = null; doc.dataset.busy = 'false';
      $('anc-status').textContent = '⚠ Could not read where it lives: ' + e.message;
      return false;
    }
    $('anc-status').textContent = ''; doc.dataset.busy = 'false';
    if (globalThis.GlimpseMd) doc.innerHTML = GlimpseMd.toHtml(anc.markdown, {view: 'rendered', contentFormat: 'markdown', findRegex: null});
    else doc.textContent = anc.markdown;
    $('anc-scroll').scrollTop = 0;
    return true;
  }
  function closeAncestors() { $('anc').hidden = true; }
  function copyAncestors() {
    if (!anc.markdown) return;
    copyText(anc.markdown, () => { $('anc-status').textContent = 'Copied the ancestor chain'; },
      () => { $('anc-status').textContent = 'Copy unavailable in this browser · select the text'; });
  }

  // @beacon[
  //   id=opc-pane@glimpseStart,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Starts the View page: names any shared renderer file that failed to load, reads the Dock/group catalog once (a failure leaves the pasted-handle and spec inputs working), creates its history (view-history.js, keyed by the page around: phone, desktop or the page itself, with the page around's position and reveal answers and Recent roots' store, /api/view/roots, read once at the start and flushed on leaving the page; its three rows always at the top) and the maximize button at the right of its ‹ › row (toggleMaximized: Full, Collapsed or Maximized), and wires the thin Controls bar folding the chooser, toolbar and as-of line (remembered per device, open by itself while nothing is shown), the two draggable dividers (below the open Controls, between the row list and the document; remembered per device), the chooser (the two pickers, one open at a time, each also full screen with the Dock switches and a filter), options (Include archives too, and the one tap beside the archives footer), the where button's ancestors pop-up, Rendered/Source, Source find, Copy MD, Refresh, Full, the document's Show all and the row list's Show all, the images (a tap on one opens the lightbox there, the 🖼️ chip at the first, ⧉ Side by side on a wide page) and the named-estate line's one tap (the node itself, or back to the estate); a ?handle=…[&how=node|subtree|dock][&from=…&active=…&where=…] in the URL (a handle tapped before the View page loaded) is rendered at once as a fresh root, as Node by default; otherwise the stored history's current entry is shown again (one render). No polling, no other automatic render and no cache refresh.,
  // ]
  function glimpseStart() {
    checkAssets();
    const position = aroundFn('opcProgramPosition'), reveal = aroundFn('opcRevealSession'), showTurn = aroundFn('opcShowTurn');
    history = opcViewHistory.create({doc: document, storageKey: 'pane_view_history_v1:' + embed, render: renderEntry, position, reveal, showTurn,
      revealPlace: embed === 'desktop' ? 'the sidebar' : 'Programs', roots: rootsStore, device: embed, note});
    $('max').addEventListener('click', () => toggleMaximized());
    // Leaving the page (a reload, the tab closed or hidden): the inner history's last change still reaches Recent roots.
    globalThis.addEventListener?.('pagehide', () => void history.flushOuter(true));
    document.addEventListener?.('visibilitychange', () => { if (document.hidden) void history.flushOuter(true); });
    // One picker open at a time: the list gets the room.
    for (const [box, other] of [['dock-box', 'group-box'], ['group-box', 'dock-box']])
      $(box).addEventListener('toggle', () => { if ($(box).open) $(other).open = false; });
    $('handle-form').addEventListener('submit', event => { event.preventDefault(); void renderHandle(); });
    $('handle-form').querySelector('.g-seg').addEventListener('click', event => {
      const how = event.target.closest?.('[data-how]')?.dataset.how; if (how) { state.how = how; radio($('handle-form'), 'how', how); }
    });
    $('spec-box').querySelector('.g-seg').addEventListener('click', event => {
      const kind = event.target.closest?.('[data-kind]')?.dataset.kind; if (kind) { state.specKind = kind; radio($('spec-box'), 'kind', kind); }
    });
    $('spec-render').addEventListener('click', () => void renderSpec());
    $('headings').addEventListener('change', () => { state.headingsChoice = $('headings').checked; applyOptions(); });
    $('sort').addEventListener('change', applyOptions);
    $('direction').addEventListener('change', applyOptions);
    $('recent').addEventListener('change', applyOptions);
    $('rendered').addEventListener('click', () => setView('rendered'));
    $('source').addEventListener('click', () => setView('source'));
    $('find').addEventListener('input', () => setFind($('find').value));
    $('find').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); navigate(event.shiftKey ? -1 : 1); } });
    $('prev').addEventListener('click', () => navigate(-1));
    $('next').addEventListener('click', () => navigate(1));
    $('copy').addEventListener('click', copy);
    $('refresh').addEventListener('click', () => void refresh());
    $('ctl-toggle').addEventListener('click', () => toggleControls());
    $('full').addEventListener('click', () => void full());
    $('show-all').addEventListener('click', () => void showAll());
    $('rows-all').addEventListener('click', toggleAllRows);
    $('archives').addEventListener('change', () => void setArchives($('archives').checked));
    $('arch-toggle').addEventListener('click', () => void setArchives(!state.held?.archives));
    // The two dividers, kept in their place when the frame changes size (a rotation, the desktop pane's column)
    $('rows-box').addEventListener('toggle', paintSplits);
    splitDivider('ctl'); splitDivider('rows');
    globalThis.addEventListener?.('resize', () => { applySplits(); showImages(); });  // ⧉ only while the page is wide
    // The pickers full screen: from the Choose line (even folded) and from each compact picker's ⤢ (never its toggle)
    for (const [id, kind] of [['pick-docks', 'dock'], ['pick-groups', 'group'], ['dock-full', 'dock'], ['group-full', 'group']])
      $(id).addEventListener('click', event => { event.preventDefault(); event.stopPropagation?.(); openSheet(kind); });
    $('psheet-close').addEventListener('click', closeSheet);
    $('psheet').addEventListener('click', event => { if (event.target === $('psheet')) closeSheet(); });
    $('psheet-filter').addEventListener('input', () => { sheet.filter = $('psheet-filter').value; paintSheet(); });
    $('docks-all').addEventListener('change', () => void setDockList('all', $('docks-all').checked));
    $('docks-local').addEventListener('change', () => void setDockList('local', $('docks-local').checked));
    $('groups-open').addEventListener('change', () => { sheet.expandAll = $('groups-open').checked; paintSheet(); });
    // Where the node on screen lives
    // Images: a tap on one (or its tile) opens the lightbox there, the chip at the first; a broken one becomes a labelled tile
    lightboxApi()?.wire($('doc'), {onOpen: index => openLightbox(index)});
    $('images-open').addEventListener('click', () => openLightbox(0));
    $('images-compare').addEventListener('click', () => openCompare());
    // A named estate: one tap to the node itself, or back
    $('estate-toggle').addEventListener('click', () => void toggleEstate());
    $('where').addEventListener('click', () => void showAncestors());
    $('anc-close').addEventListener('click', closeAncestors);
    $('anc-copy').addEventListener('click', copyAncestors);
    $('anc').addEventListener('click', event => { if (event.target === $('anc')) closeAncestors(); });
    $('anc').querySelector('.g-seg').addEventListener('click', event => { const uuids = event.target.closest?.('[data-uuids]')?.dataset.uuids; if (uuids) void showAncestors(uuids); });
    document.addEventListener?.('keydown', event => { if (event.key !== 'Escape') return; if (!$('anc').hidden) closeAncestors(); else if (!$('psheet').hidden) closeSheet(); });
    showFind(); showStatus(); applySplits();
    const search = globalThis.location?.search || '', asked = search ? new URLSearchParams(search) : null;
    if (asked?.get('handle')) {
      const origin = {from: asked.get('from') || 'outside'};
      for (const k of ['active', 'where']) if (asked.get(k)) origin[k] = asked.get(k);
      try { const turn = JSON.parse(asked.get('turn') || 'null'); if (turn && typeof turn === 'object') origin.turn = turn; } catch (_) { /* no turn */ }
      void openHandle(asked.get('handle'), asked.get('how') || 'node', origin);
    } else { const again = history.restore(); if (again) void renderEntry(again); }
    void history.startOuter();  // Recent roots, read once: a cold start can go straight back to one
    return readCatalog();
  }
  return {start: glimpseStart, state, choose, render, refresh, full, toggleControls, controlsShown, toggleMaximized, maximized: () => maxed.on, shown, copy, setView, setFind, navigate, showAll, readCatalog, pick, paintPickers,
    renderHandle, openHandle, openFromPage, resume, renderSpec, toggleAllRows, setArchives, openSheet, closeSheet, readDocks, setDockList, showAncestors, closeAncestors,
    nodeHandle, applySplits, openLightbox, openCompare, toggleEstate, estateLines, history: () => history, BOUND, BATCH, ROW_BOUND};
})();
globalThis.opcGlimpseView = glimpseView;
globalThis.opcOpenInView = (handle, context) => glimpseView.openFromPage(handle, context);  // a handle tapped on this page: an entry, or a new root from the pop-up
glimpseView.start();
