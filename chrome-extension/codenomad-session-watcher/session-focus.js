// "Open in CodeNomad" (CodeNomad 0.20.1 adapter). Only on a request the user made in the Transcription Pane, this
// page selects a workspace tab and a session in CodeNomad's own controls: it clicks the tab whose folder holds the
// session, then that session's row. It never types or sends a prompt and never switches models; the session search
// box is filled only to find a row the list has not loaded, and is put back as it was.
(function () {
  'use strict';
  const PANE = 'http://127.0.0.1:9911/api/focus-request';
  const CODENOMAD = '0.20.1';
  // Pinned to CodeNomad 0.20.1's UI (components/instance-tab.tsx, session-list.tsx,
  // instance/shell/SessionSidebar.tsx, message-section; English labels). After a CodeNomad upgrade, check these
  // first: a missing one fails visibly in the pane, naming it, never silently.
  const SELECTORS = Object.freeze({
    tab: 'button.tab-base[role="tab"][title]',
    row: '.session-item-base[data-session-id]',
    rowSelect: 'button.session-item-select',
    rowExpander: 'button.session-item-expander[aria-expanded="false"]',
    list: '.session-list-container',
    searchInput: '.session-list-container input.form-input[type="text"]',
    searchToggle: 'button[aria-label="Search sessions"][aria-pressed]',
    stream: '.message-stream-container[data-stream-active="true"][data-session-id]',
    activeRow: '.session-item-active[data-session-id]',
  });
  const watcherId = 'focus-' + crypto.randomUUID();
  const handled = new Set(), missingSent = new Set();
  let busy = false;
  const visible = (el) => !!el && el.isConnected !== false && el.getClientRects().length > 0;
  const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const pinned = (what) => what + ' (selectors pinned to CodeNomad ' + CODENOMAD + '; CodeNomad may have changed)';

  // One folder spelling for comparing a session's directory with a tab's folder: forward slashes, E:\ → /mnt/e/,
  // no doubled or trailing slash.
  function normalizeFolder(path) {
    let s = String(path || '').trim().replace(/\\/g, '/');
    const drive = /^([A-Za-z]):\/(.*)$/.exec(s);
    if (drive) s = '/mnt/' + drive[1].toLowerCase() + '/' + drive[2];
    s = s.replace(/\/{2,}/g, '/');
    return s.length > 1 ? s.replace(/\/+$/, '') : s;
  }
  // The tab whose folder is the session's folder or holds it (a subfolder or worktree); the most specific wins.
  function matchTab(tabs, directory) {
    const dir = normalizeFolder(directory);
    let best = null, bestLength = -1;
    if (!dir.startsWith('/')) return null;
    for (const tab of tabs) {
      const folder = normalizeFolder(tab.getAttribute('title'));
      if (!folder.startsWith('/')) continue;
      const holds = folder === '/' || dir === folder || dir.startsWith(folder + '/');
      if (holds && folder.length > bestLength) { best = tab; bestLength = folder.length; }
    }
    return best;
  }
  const workspaceTabs = () => [...document.querySelectorAll(SELECTORS.tab)];
  const rowButton = (sid) => [...document.querySelectorAll(SELECTORS.row)]
    .filter((row) => row.getAttribute('data-session-id') === sid)
    .map((row) => row.querySelector(SELECTORS.rowSelect)).find(visible) || null;
  const shown = (sid) => [...document.querySelectorAll(SELECTORS.stream + ',' + SELECTORS.activeRow)]
    .some((el) => el.getAttribute('data-session-id') === sid && visible(el));
  async function until(test, ms) {
    for (const end = Date.now() + ms; ; await pause(100)) {
      const value = test();
      if (value || Date.now() >= end) return value;
    }
  }
  function setQuery(input, value) {
    input.value = value;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // @beacon[
  //   id=opc-pane@focusCodeNomadSession,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=Selects one session in CodeNomad 0.20.1 by its own controls: the workspace tab for its folder, then its session row (its parent's expander for a subagent, the session search for a row not loaded), and confirms it is shown; every failure names what was missing.,
  // ]
  async function focusSession(req) {
    const folder = normalizeFolder(req.directory), sid = req.sessionId;
    let tab = matchTab(workspaceTabs(), folder);
    if (!tab) return { code: 'failed', message: 'The CodeNomad tab for ' + folder + ' closed before it could be selected' };
    const tabFolder = tab.getAttribute('title');
    if (tab.getAttribute('aria-selected') !== 'true') {
      tab.click();
      const active = await until(() => workspaceTabs().some((t) => t.getAttribute('title') === tabFolder && t.getAttribute('aria-selected') === 'true'), 3000);
      if (!active) return { code: 'failed', message: pinned('Clicked the CodeNomad tab for ' + tabFolder + ', but it did not become active') };
    }
    if (shown(sid)) return { code: 'done', message: 'already open in CodeNomad' };
    if (!(await until(() => [...document.querySelectorAll(SELECTORS.list)].some(visible), 2500)))
      return { code: 'failed', message: "CodeNomad's session list is not open for " + tabFolder + ': open its left panel, then try again (or ' + pinned('the list was not found') + ')' };
    let button = await until(() => rowButton(sid), 2000);
    if (!button && req.parentId) {
      // A subagent's row shows only under its expanded parent.
      const expander = rowButton(req.parentId)?.closest(SELECTORS.row)?.querySelector(SELECTORS.rowExpander);
      if (expander) { expander.click(); button = await until(() => rowButton(sid), 2000); }
    }
    let restore = null;
    if (!button) {
      // Not loaded yet (the list pages in as it scrolls): CodeNomad's own session search finds it by title.
      let input = [...document.querySelectorAll(SELECTORS.searchInput)].find(visible), toggle = null;
      if (!input) {
        toggle = [...document.querySelectorAll(SELECTORS.searchToggle)].find(visible);
        if (!toggle) return { code: 'failed', message: pinned('The session is not in the loaded list, and the session search button was not found') };
        toggle.click();
        input = await until(() => [...document.querySelectorAll(SELECTORS.searchInput)].find(visible), 1500);
        if (!input) { toggle.click(); return { code: 'failed', message: pinned('The session search box did not open') }; }
      }
      const prior = input.value, opened = !!toggle;
      // Read the controls again: the list may have re-rendered them meanwhile.
      restore = () => {
        const now = [...document.querySelectorAll(SELECTORS.searchInput)].find(visible);
        if (now) setQuery(now, prior);
        const pressed = opened && [...document.querySelectorAll(SELECTORS.searchToggle)].find((t) => visible(t) && t.getAttribute('aria-pressed') === 'true');
        if (pressed) pressed.click();
      };
      setQuery(input, String(req.title || sid).trim().slice(0, 400));
      button = await until(() => rowButton(sid), 6000);
    }
    try {
      if (!button) return { code: 'failed', message: 'This session is not in the CodeNomad session list for ' + tabFolder +
        ' (not found by its title either' + (req.parentId ? ', nor under its parent' : '') + '). Find it there by hand: ' + String(req.title || sid).slice(0, 160) +
        '. If it is listed there, ' + pinned('its row was not recognised') };
      button.click();
      if (await until(() => shown(sid), 4000)) return { code: 'done', message: 'shown in CodeNomad' };
      return { code: 'failed', message: pinned('Clicked the session row in CodeNomad, but the session did not open') };
    } finally {
      if (restore) restore();
    }
  }

  async function call(url, body) {
    const response = await fetch(url, body === undefined ? { cache: 'no-store', signal: AbortSignal.timeout(5000) } :
      { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: JSON.stringify(body), signal: AbortSignal.timeout(5000) });
    return response.ok ? response.json() : null;
  }
  async function tick() {
    if (busy) return;
    busy = true;
    try {
      const req = (await call(PANE + '?watcherId=' + encodeURIComponent(watcherId)))?.request;
      if (!req || req.state !== 'pending' || handled.has(req.nonce)) return;
      const tab = matchTab(workspaceTabs(), req.directory);
      if (!tab) {
        // Not this page's folder (yet): say so once, without claiming; the request stays pending for a page,
        // or a tab Dan opens now, that has it.
        if (!missingSent.has(req.nonce)) {
          missingSent.add(req.nonce);
          const folders = workspaceTabs().map((t) => t.getAttribute('title')).filter(Boolean).slice(0, 32);
          // Tabs on the page that the pinned selector does not recognise: CodeNomad changed, not a missing tab.
          const unrecognised = !folders.length && document.querySelectorAll('[role="tab"]').length > 0;
          await call(PANE + '/report', { nonce: req.nonce, watcherId, code: 'no-tab', folders,
            ...(unrecognised ? { message: pinned('This CodeNomad page has tabs, but none is a workspace tab the extension recognises') } : {}) });
        }
        return;
      }
      const claim = await call(PANE + '/claim', { nonce: req.nonce, watcherId, visible: document.visibilityState === 'visible' });
      if (!claim?.claimed) return;
      handled.add(req.nonce);
      let outcome;
      try { outcome = await focusSession(req); } catch (e) { outcome = { code: 'failed', message: 'CodeNomad page error: ' + String(e?.message || e).slice(0, 300) }; }
      await call(PANE + '/report', { nonce: req.nonce, watcherId, code: outcome.code, message: String(outcome.message).slice(0, 600) });
    } catch (e) { /* pane offline: the next poll retries */ } finally { busy = false; }
  }
  (function loop() { void tick().finally(() => setTimeout(loop, document.visibilityState === 'visible' ? 1000 : 2000)); })();
  globalThis.opcSessionFocus = Object.freeze({ normalizeFolder, matchTab, focusSession, SELECTORS, CODENOMAD });
})();
