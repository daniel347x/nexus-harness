// Deliberate CodeNomad 0.20.1 selections only; independent of previews and session heartbeats.
(function () {
  'use strict';
  const origin = location.origin;
  if (!['http://127.0.0.1:9899', 'http://localhost:9899'].includes(origin)) return;
  const ENDPOINT = 'http://127.0.0.1:9911/api/tool-selection';
  const MAX_BYTES = 16 * 1024 * 1024;
  const selectorId = crypto.randomUUID();
  const ownedButtons = new WeakSet(), nativeClicks = new WeakSet(), nativeExpanded = new WeakMap();
  let sequence = 0, observedAt = 0, latest = null, draining = false, controller = null, timer = null;
  let highlighted = null, activeScope = null;

  function identity(row) {
    const ids = ['instanceId', 'sessionId', 'messageId', 'partId'].map(key => row.dataset[key]);
    if (!ids.every(id => typeof id === 'string' && id.length > 0 && id.length <= 512)) return null;
    return Object.freeze({ instanceId: ids[0], sessionId: ids[1], messageId: ids[2], partId: ids[3] });
  }

  function sameIdentity(a, b) {
    return a && b && ['instanceId', 'sessionId', 'messageId', 'partId'].every(key => a[key] === b[key]);
  }

  // The pane survives empty/loading/virtualized streams; tool rows do not.
  function currentScope() {
    const panes = [...document.querySelectorAll('.session-cache-pane[data-session-active="true"][aria-hidden="false"]')]
      .filter(pane => pane.getClientRects().length);
    if (panes.length !== 1) return null;
    const pane = panes[0], { instanceId, sessionId } = pane.dataset;
    if (![instanceId, sessionId].every(id => typeof id === 'string' && id.length > 0 && id.length <= 512)) return null;
    return { pane, instanceId, sessionId };
  }

  function reconcileScope() {
    const scope = currentScope();
    // Unknown/temporarily unmounted is not a session change. Hide, but retain identity.
    if (scope) {
      if (activeScope && (scope.instanceId !== activeScope.instanceId || scope.sessionId !== activeScope.sessionId)) {
        highlighted = null; // No per-session history; returning requires a new deliberate click.
      }
      activeScope = { instanceId: scope.instanceId, sessionId: scope.sessionId };
    }
    return scope;
  }

  function inScope(row, ids, scope) {
    return !!scope && !!ids && row.closest('.session-cache-pane') === scope.pane
      && ids.instanceId === scope.instanceId && ids.sessionId === scope.sessionId;
  }

  function attribute(element, name, value) {
    if (element.getAttribute(name) !== value) element.setAttribute(name, value);
  }

  function label(value) {
    return typeof value === 'string' ? value.replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, 160) : '';
  }

  function sourceFailure(message) {
    const error = new Error(message);
    error.sourceMessage = message;
    return error;
  }

  // Never call response.json(): enforce the byte bound even without Content-Length.
  async function readJSON(url, signal) {
    const response = await fetch(url, { credentials: 'include', cache: 'no-store', redirect: 'error', signal });
    if (!response.ok) {
      void response.body?.cancel().catch(() => {});
      throw sourceFailure(`CodeNomad source HTTP ${response.status}.`);
    }
    const length = Number(response.headers.get('content-length'));
    if (length > MAX_BYTES) {
      void response.body?.cancel().catch(() => {});
      throw sourceFailure('CodeNomad source exceeds the 16 MiB limit.');
    }
    if (!response.body) throw sourceFailure('CodeNomad source response is unsupported.');
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let bytes = 0, text = '';
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        if (bytes > MAX_BYTES) throw sourceFailure('CodeNomad source exceeds the 16 MiB limit.');
        text += decoder.decode(value, { stream: true });
      }
      text += decoder.decode();
      return JSON.parse(text);
    } finally {
      // Cancel partially consumed bodies (oversize, aborted or malformed responses).
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }

  function checkCurrent(selection) {
    if (latest !== selection) throw sourceFailure('Superseded selection.');
  }

  // @beacon[
  //   id=opc@prepareToolSelection,
  //   slice_labels=opc-codenomad-tools,
  //   kind=ast,
  //   comment=Authenticated bounded source reads establish immutable local record witnesses; retain no tool input or output.,
  // ]
  async function prepareToolSelection(selection, signal) {
    const payload = { origin, ...selection.identity, selectorId, sequence: selection.sequence, observedAt: selection.observedAt };
    try {
      const instance = encodeURIComponent(payload.instanceId), session = encodeURIComponent(payload.sessionId);
      const message = encodeURIComponent(payload.messageId), proxyPath = `/workspaces/${instance}/instance`;
      const workspace = await readJSON(`/api/workspaces/${instance}`, signal);
      checkCurrent(selection);
      if (workspace?.id !== payload.instanceId || workspace.proxyPath !== proxyPath
        || workspace.type === 'remote' || workspace.kind === 'remote' || workspace.remote === true) {
        throw sourceFailure('Unsupported or remote CodeNomad workspace.');
      }
      payload.workspaceName = label(workspace.name) || label(workspace.path);
      const sessionRecord = (await readJSON(`${proxyPath}/api/session/${session}`, signal))?.data;
      checkCurrent(selection);
      if (sessionRecord?.id !== payload.sessionId || typeof sessionRecord.location?.directory !== 'string'
        || !sessionRecord.location.directory.trim() || sessionRecord.location.directory.length > 4096
        || !Number.isSafeInteger(sessionRecord.time?.created) || sessionRecord.time.created <= 0) {
        throw sourceFailure('CodeNomad session identity or location is unavailable.');
      }
      payload.sessionTitle = label(sessionRecord.title);
      const sessionCreated = sessionRecord.time.created, directory = sessionRecord.location.directory;
      const record = (await readJSON(`${proxyPath}/api/session/${session}/message/${message}`, signal))?.data;
      checkCurrent(selection);
      if (record?.id !== payload.messageId || (Object.hasOwn(record, 'sessionID') && record.sessionID !== payload.sessionId)
        || !Number.isSafeInteger(record.time?.created) || record.time.created <= 0 || !Array.isArray(record.content)) {
        throw sourceFailure('CodeNomad message identity is unavailable.');
      }
      const matches = record.content.filter(part => part?.id === payload.partId);
      const part = matches[0];
      if (matches.length !== 1 || part.type !== 'tool' || typeof part.name !== 'string' || !part.name.trim()
        || part.name.length > 512 || (part.callID != null && (typeof part.callID !== 'string' || part.callID.length > 512))) {
        throw sourceFailure('CodeNomad tool part is missing, ambiguous or unsupported.');
      }
      payload.toolName = part.name;
      payload.association = { sessionCreated, directory, messageCreated: record.time.created,
        toolName: part.name, callId: part.callID || null };
    } catch (error) {
      checkCurrent(selection);
      payload.sourceError = error.sourceMessage || 'CodeNomad source unavailable (offline, timed out or login required).';
    }
    return payload;
  }

  // @beacon[
  //   id=opc@relayToolSelection,
  //   slice_labels=opc-codenomad-tools,
  //   kind=ast,
  //   comment=Single serial latest-click relay; stale preparation never posts and successful clicks never retry or heartbeat.,
  // ]
  async function relayToolSelection() {
    if (draining) return;
    draining = true;
    try {
      while (latest && latest.status === 'pending') {
        const selection = latest;
        controller = new AbortController();
        let timeout = setTimeout(() => controller?.abort(), 10000);
        try {
          const payload = await prepareToolSelection(selection, controller.signal);
          clearTimeout(timeout);
          checkCurrent(selection);
          // Do not abort an already posted selection on a newer click: finish it, then
          // post the newer one in order. Server also has selectorId/sequence/time guards.
          controller = null;
          const postController = new AbortController();
          // Separate post controller keeps newer clicks from cancelling the serialized write.
          timeout = setTimeout(() => postController.abort(), 10000);
          const response = await fetch(ENDPOINT, { method: 'POST', mode: 'cors', credentials: 'omit',
            headers: { 'Content-Type': 'text/plain' }, body: JSON.stringify(payload), cache: 'no-store',
            redirect: 'error', signal: postController.signal });
          if (!response.ok) throw new Error(`Inspector HTTP ${response.status}.`);
          if (latest === selection) {
            selection.status = payload.sourceError ? 'source-error' : 'selected';
            selection.detail = payload.sourceError || 'Selected in the tool inspector.';
          }
        } catch (error) {
          if (latest === selection) {
            selection.status = 'failed';
            selection.detail = 'Inspector unavailable. Click the header to retry.';
          }
        } finally {
          clearTimeout(timeout);
          controller = null;
          schedule();
        }
      }
    } finally {
      draining = false;
    }
  }

  // One capture listener also handles replacements before the next annotation scan.
  // Solid delegates native clicks at document bubble; stop before either native path.
  // @beacon[
  //   id=opc@routeToolHeaderClick,
  //   slice_labels=opc-codenomad-tools,
  //   kind=ast,
  //   comment=Capture replacement-safe row inspection; independent Toggle invokes only the guarded native transition and other controls pass through.,
  // ]
  function routeToolHeaderClick(event) {
    const target = event.target?.closest ? event.target : event.target?.parentElement;
    const header = target?.closest('.tool-call-header'), row = header?.closest('.tool-call');
    const toggle = header?.querySelector('.tool-call-header-toggle');
    if (!row?.isConnected || !toggle || toggle.closest('.tool-call') !== row) return;
    if (nativeClicks.has(toggle)) return;
    const smallToggle = target.closest('.opc-tool-toggle');
    if (smallToggle && ownedButtons.has(smallToggle) && smallToggle.parentElement === header) {
      event.preventDefault();
      event.stopImmediatePropagation();
      // Invoke the existing native state transition, never emulate its DOM/details.
      nativeClicks.add(toggle);
      try { toggle.click(); } finally { nativeClicks.delete(toggle); }
      schedule();
      return;
    }
    const control = target.closest('button, a, input, select, textarea, [role="button"], [contenteditable="true"]');
    if (control && control !== toggle) return; // Copy, speech, menus and other native actions.
    if (event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const ids = identity(row), scope = reconcileScope(); // Reused rows may have changed before a scan.
    if (!inScope(row, ids, scope)) return;
    observedAt = Math.max(Date.now(), observedAt + 1);
    latest = { identity: ids, sequence: ++sequence, observedAt, status: 'pending', detail: 'Selecting in the tool inspector…' };
    highlighted = latest;
    controller?.abort();
    scan();
    void relayToolSelection();
  }

  // @beacon[
  //   id=opc@annotateToolInspect,
  //   slice_labels=opc-codenomad-tools,
  //   kind=ast,
  //   comment=Reconcile scoped identity onto replaceable headers; large inspection target and independent native Toggle without row listeners.,
  // ]
  function annotateToolInspect(row, scope) {
    const toggle = row.querySelector('.tool-call-header-toggle');
    const header = toggle?.parentElement;
    if (!header || toggle.closest('.tool-call') !== row) return;
    let button = [...header.children].find(child => child.classList.contains('opc-tool-toggle'));
    if (!button) {
      button = document.createElement('button');
      button.type = 'button';
      button.className = 'opc-tool-toggle';
      header.insertBefore(button, toggle.nextSibling);
    }
    ownedButtons.add(button);
    const ids = identity(row), scoped = inScope(row, ids, scope);
    const selection = scoped && sameIdentity(ids, highlighted?.identity) ? highlighted : null;
    const status = selection?.status || 'idle';
    const title = !scoped ? 'Tool identity or active session unavailable.' : selection?.detail || 'Inspect this tool call in the pane.';
    if (button.textContent !== 'Toggle') button.textContent = 'Toggle';
    attribute(button, 'title', 'Toggle expanded state');
    attribute(button, 'aria-label', 'Toggle expanded state');
    // Native reactive writes remain the expansion authority. Transfer the disclosure
    // semantics to Toggle: the large button now inspects, it no longer expands.
    const expanded = toggle.getAttribute('aria-expanded') ?? nativeExpanded.get(toggle) ?? 'false';
    nativeExpanded.set(toggle, expanded);
    attribute(button, 'aria-expanded', expanded);
    attribute(header, 'data-native-expanded', expanded);
    if (toggle.hasAttribute('aria-expanded')) toggle.removeAttribute('aria-expanded');
    attribute(toggle, 'title', title);
    attribute(toggle, 'aria-label', `Inspect ${label(row.dataset.toolName) || 'this tool call'} in the pane. ${title}`);
    attribute(header, 'data-inspect-state', status);
    // A failed relay cannot claim the Pane accepted it. Keep the error on the large target.
    attribute(header, 'data-inspect-selected', String(!!selection && status !== 'failed'));
    attribute(toggle, 'aria-current', String(!!selection && status !== 'failed'));
  }

  function scan() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (document.hidden) return;
    const scope = reconcileScope();
    for (const row of document.querySelectorAll('.tool-call')) annotateToolInspect(row, scope);
  }

  function schedule() {
    if (timer === null) timer = setTimeout(scan, 80);
  }

  new MutationObserver(schedule).observe(document.documentElement, { subtree: true, childList: true,
    attributes: true, attributeFilter: ['data-instance-id', 'data-session-id', 'data-message-id', 'data-part-id',
      'data-session-active', 'data-stream-active', 'aria-hidden', 'aria-expanded', 'class', 'style'] });
  document.addEventListener('click', routeToolHeaderClick, true);
  document.addEventListener('visibilitychange', schedule);
  schedule();
})();
