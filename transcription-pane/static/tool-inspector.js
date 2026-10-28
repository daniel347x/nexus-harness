// Memory-only selected-call reader. Deliberate Inspect clicks, not Send's target, own this state.
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const panel = $('tool-inspector'), input = $('inspector-input'), output = $('inspector-output');
  const LIMIT = 16 * 1024 * 1024, SNAPSHOT_LIMIT = 8 * 1024 * 1024;
  const pretty = { input: true, output: true };
  const readable = { input: true, output: true };
  const wrap = { input: true, output: true };
  // Block ordinals the reader switched to Source; cleared by each new deliberate Inspect.
  const sourceChoice = { input: new Set(), output: new Set() };
  const { rawMembers, toolSnapshotText, bytes, size, SIZE_NOTE, emphasizeToolSize } = opcToolText;
  // Pretty JSON, Readable text, Markdown and the painter, shared with the phone page.
  const { snapshotViews, regionView, paint, forget, callTimeText, elapsedText, isRunningStatus } = opcToolReading;
  const sizeLabels = new WeakMap();
  let cursor = null, selected = null, snapshot = null, generation = 0;
  let polling = false, nextRead = 0, failures = 0, terminal = false, disconnected = false;
  let recordController = null, artifactController = null, artifactSnapshot = null;
  // The held call's ONE shared recorded-diff reader (diff-view.js) and the snapshot diff it was last given.
  let diffReader = null, diffValue = null;

  async function json(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options,
      signal: options.signal || AbortSignal.timeout(12000) });
    // The server already bounds upstream reads; also bound retained browser response bytes.
    const reader = response.body.getReader();
    const decoder = new TextDecoder(); let text = '', bytes = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        if (bytes > LIMIT) throw new Error('Tool response exceeds the 16 MiB read limit; nothing cropped');
        text += decoder.decode(value, { stream: true });
      }
      text += decoder.decode();
    } catch (error) { await reader.cancel().catch(() => {}); throw error; }
    finally { reader.releaseLock(); }
    const data = JSON.parse(text);
    if (!response.ok) {
      const error = new Error(data.error || `HTTP ${response.status}`);
      error.status = response.status; throw error;
    }
    if (url.startsWith('/api/tool-record') && data.backend !== 'claude') {
      // JSON.parse is for envelope/provenance only. Never stringify its rounded
      // numeric tool values: retain the validated wire lexemes for presentation.
      const part = rawMembers(text).get('part');
      const state = part && rawMembers(part).get('state');
      data.rawState = state ? rawMembers(state) : new Map();
    }
    return data;
  }

  function note(text, error = false) {
    $('inspector-note').textContent = text;
    $('inspector-note').classList.toggle('error', error);
  }
  // The call's own time (the time the conversation shows) in Eastern time, then how long it took when
  // the stored records carry its result's time. An unsettled record gets elapsed-since-call, explicitly
  // not a process-liveness claim: a crash can leave a stored running part without its terminal event.
  function paintTime(now = Date.now()) {
    if (!snapshot) return;
    const stamp = callTimeText(snapshot.time, NaN);
    const running = !terminal && !disconnected && !failures && now-snapshot.observedAt<=15000 && isRunningStatus(snapshot.status);
    const elapsed = running ? elapsedText(snapshot.time, now) : '';
    const line = stamp ? (running ? stamp + (elapsed ? ' · ' + elapsed + ' since call · no result recorded' : '') :
      callTimeText(snapshot.time, snapshot.endTime)) : '';
    const shown = line && '· ' + line;
    if ($('inspector-time').textContent !== shown) $('inspector-time').textContent = shown;
  }
  function render() {
    if (!snapshot) return;
    for (const kind of ['input', 'output']) {
      const view = snapshot.views[kind];
      const shown = regionView(view, snapshot[kind], pretty[kind], readable[kind], snapshot[kind + 'Bytes']);
      paint(kind === 'input' ? input : output, shown.text, shown.pieces, sourceChoice[kind]);
      const reason = view.reason;
      const caption = $('inspector-' + kind + '-label');
      const text = snapshot[kind + 'Label'] + shown.caption;
      if (caption.textContent !== text) {
        const count = snapshot[kind + 'Bytes'], formatted = size(count), start = count == null ? -1 : text.indexOf(formatted);
        if (start < 0) { caption.textContent = text; sizeLabels.delete(caption); }
        else {
          const label = document.createElement('span'); label.className = 'inspector-size';
          label.textContent = formatted; sizeLabels.set(caption, label);
          caption.replaceChildren(document.createTextNode(text.slice(0, start)), label, document.createTextNode(text.slice(start + formatted.length)));
        }
      }
      if (sizeLabels.has(caption)) emphasizeToolSize(sizeLabels.get(caption), snapshot[kind + 'Bytes']);
      $('inspector-copy-' + kind).disabled = snapshot.backend === 'claude' && !snapshot[kind + 'Available'];
      caption.title = SIZE_NOTE;
      $('inspector-pretty-' + kind).title = pretty[kind] ? `Showing ${reason}; copy always uses original text` : 'Showing original text; copy always uses original text';
    }
    $('inspector-artifact-read').hidden = snapshot.backend !== 'claude' || !snapshot.artifactRef;
    renderDiff();
    paintTime();
  }
  // A1's normalized `diff`, exactly as the snapshot carries it, goes to the shared reader above Input/Output: mounted
  // once per held call, then updated in place (the reader keeps unchanged code, its layout choice and a live text
  // selection). A new snapshot object is the only reason to pass it again; null hides the region.
  function renderDiff() {
    const value = snapshot.diff ?? null, region = $('inspector-diff');
    if (value === diffValue) return;
    if (value === null) { clearDiff(); return; }
    diffValue = value; region.hidden = false;
    const view = globalThis.opcDiffView;
    if (!view) { region.textContent = 'Diff display unavailable (diff-view.js did not load) · Input and Output below are unchanged'; return; }
    try {
      if (diffReader) diffReader.update(value);
      else { region.textContent = ''; diffReader = view.mount(region, value, { layout: 'auto', wrap: wrap.input }); }
    } catch (error) {
      // An odd diff shape never costs the raw readers: say so in place of the diff.
      diffReader?.destroy(); diffReader = null;
      region.textContent = `Diff display unavailable: ${error.message} · Input and Output below are unchanged`;
    }
  }
  function clearDiff() {
    diffReader?.destroy(); diffReader = null; diffValue = null;
    $('inspector-diff').hidden = true; $('inspector-diff').textContent = '';
  }

  function sameNativeIdentity(a, b) {
    const keys = ['storeRef', 'sessionRef', 'scopeRef', 'leafRef', 'recordRef', 'sessionUuid', 'scope',
      'leafUuid', 'recordUuid', 'recordHash', 'blockIndex', 'callId', 'callRef', 'fileEpoch'];
    return !!a && !!b && Object.keys(a).length === keys.length && Object.keys(b).length === keys.length &&
      keys.every(key => Object.hasOwn(a, key) && Object.hasOwn(b, key) && a[key] === b[key]);
  }

  function nativeSnapshot(data, held) {
    const raw = data.snapshot;
    const host = held.source === 'host', provenance = host ? 'host-durable-feed' : 'recorded-jsonl';
    if (data.schemaVersion !== 1 || data.backend !== 'claude' || data.selection?.backend !== 'claude' ||
        !sameNativeIdentity(data.identity, held.identity) || !sameNativeIdentity(data.selection.identity, held.identity) ||
        !raw || raw.schemaVersion !== 1 || raw.backend !== 'claude' || raw.callRef !== held.callRef ||
        raw.fileEpoch !== held.fileEpoch || data.provenance !== provenance || raw.provenance !== provenance ||
        (host && (typeof raw.live !== 'boolean' || typeof raw.terminal !== 'boolean' || typeof raw.inputFinal !== 'boolean'))) {
      throw Object.assign(new Error('Selected native source/identity changed'), { status: 409 });
    }
    const result = { backend: 'claude', status: raw.status, artifactRef: raw.artifactRef,
      live:host && raw.live, terminal:!host || raw.terminal,
      time: typeof raw.time === 'number' ? raw.time * 1000 : null,
      endTime: typeof raw.resultTime === 'number' ? raw.resultTime * 1000 : null };
    for (const kind of ['input', 'output']) {
      const available = raw[kind + 'Availability'] === 'stored', count = raw[kind + 'Bytes'];
      if (available ? typeof raw[kind] !== 'string' || !Number.isSafeInteger(count) || count < 0 || bytes(raw[kind]) !== count :
          raw[kind] !== null || count !== null) {
        throw Object.assign(new Error('Invalid native raw snapshot availability/count'), { status: 409 });
      }
      result[kind] = available ? raw[kind] : '';
      result[kind + 'Available'] = available;
      result[kind + 'Bytes'] = count;
      result[kind + 'Label'] = available ? `${kind === 'input' ? 'Input' : 'Output'} stored (${size(count)})` :
        `${kind === 'input' ? 'Input' : 'Output'} · ${raw[kind + 'Availability'] || 'unavailable'} · —`;
      if (host && kind === 'input' && available && !raw.inputFinal) result.inputLabel += ' · not-final fragment';
    }
    // Only after identity and counts validate: the server's normalized diff, carried as is (never derived here).
    result.diff = raw.diff ?? null;
    return result;
  }

  function resetArtifact() {
    artifactController?.abort(); artifactSnapshot = null;
    $('inspector-artifact').hidden = true; $('inspector-artifact-read').hidden = true;
    $('inspector-artifact-text').textContent = ''; $('inspector-artifact-copy').disabled = true;
  }

  // @beacon[
  //   id=opc-pane@readArtifact,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Explicit contained saved-artifact view with held call/epoch/generation guards; separate raw Copy/count never replaces the recorded result.,
  // ]
  async function readArtifact() {
    if (!selected || snapshot?.backend !== 'claude' || !snapshot.artifactRef || artifactController) return;
    const held = selected, version = generation, ref = snapshot.artifactRef;
    const controller = new AbortController(); artifactController = controller;
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const query = new URLSearchParams({ artifactRef: ref, callRef: held.callRef, fileEpoch: held.fileEpoch });
      const data = await json('/api/claude/artifact?' + query, { signal: controller.signal });
      if (version !== generation || selected !== held) return;
      if (data.schemaVersion !== 1 || data.backend !== 'claude' || data.artifactRef !== ref ||
          data.callRef !== held.callRef || data.fileEpoch !== held.fileEpoch || data.availability !== 'stored' ||
          !['full', 'limited'].includes(data.completeness) || typeof data.text !== 'string' ||
          data.bytes !== bytes(data.text) || data.bytes > SNAPSHOT_LIMIT) throw new Error('Saved artifact witness/count changed');
      artifactSnapshot = { text: data.text, bytes: data.bytes };
      $('inspector-artifact-label').textContent = `Saved ${data.completeness} artifact (${size(data.bytes)}) · recorded result unchanged`;
      paint($('inspector-artifact-text'), data.text);
      $('inspector-artifact').hidden = false; $('inspector-artifact').open = true;
      $('inspector-artifact-copy').disabled = false;
    } catch (error) {
      if (version === generation && selected === held) note(`Saved artifact unavailable: ${error.message} · recorded result retained`, true);
    } finally {
      clearTimeout(timeout); if (artifactController === controller) artifactController = null;
    }
  }

  // @beacon[
  //   id=opc-pane@selectInspection,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=Independent epoch/revision cursor: only deliberate Inspect selections replace the held call (and drop the old call's diff reader); stale reads cannot resurrect an old one. The panel never closes.,
  // ]
  function selectInspection(mail) {
    const key = `${mail.epoch}:${mail.revision}`;
    if (cursor === key) return;
    cursor = key;
    if (!mail.selection) {
      if (selected) {
        generation++; recordController?.abort(); disconnected = true;
        note('Selection relay cleared or restarted. Last snapshot retained; click Inspect again to reconnect.', true);
      }
      return;
    }
    generation++; recordController?.abort(); resetArtifact(); clearDiff();
    selected = { epoch: mail.epoch, revision: mail.revision, ...mail.selection };
    snapshot = null; terminal = false; disconnected = false; failures = 0; nextRead = 0;
    forget(input); forget(output);
    sourceChoice.input.clear(); sourceChoice.output.clear();
    panel.classList.toggle('held', true);
    $('inspector-outer-divider').hidden = false;
    $('inspector-title').textContent = selected.toolName || selected.association?.toolName || 'Tool inspector';
    $('inspector-origin').textContent = selected.backend === 'claude' ?
      `Claude · ${selected.sessionTitle || 'Session'} · ${selected.identity?.sessionUuid}\n${selected.workspaceName} · ${selected.source} · read-only\n${selected.identity?.scope} · record ${selected.identity?.recordUuid} · block ${selected.identity?.blockIndex} · call ${selected.identity?.callId}` :
      `OpenCode · ${selected.sessionTitle || 'Session'} · ${selected.sessionId}\n${selected.workspaceName || 'Workspace'} · ${selected.origin} · ${selected.instanceId}\nmessage ${selected.messageId} · part ${selected.partId}`;
    input.textContent = ''; output.textContent = ''; input.scrollTop = output.scrollTop = 0;
    input.scrollLeft = output.scrollLeft = 0;
    $('inspector-input-label').textContent = 'Input'; $('inspector-output-label').textContent = 'Output';
    sizeLabels.delete($('inspector-input-label')); sizeLabels.delete($('inspector-output-label'));
    $('inspector-time').textContent = '';
    note('Reading selected stored call…'); // Incoming selections never focus an element.
  }

  // @beacon[
  //   id=opc-pane@readInspection,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=Read exact snapshots/shared UTF-8 sizes with cached views; explicit host-live calls poll through partial/final and history/settled calls stop. Carries the server's normalized diff (native snapshot.diff; OpenCode data.diff beside part) for render's one shared diff reader above Input/Output. Timestamp each good read for the qualified elapsed-since-call caption: an unsettled record is not proof of live process execution.,
  // ]
  async function readInspection() {
    if (!selected || disconnected || terminal || Date.now() < nextRead || recordController) return;
    const held = selected, version = generation;
    const controller = new AbortController(); recordController = controller;
    const timeout = setTimeout(() => controller.abort(), 12000);
    try {
      const data = await json(`/api/tool-record?epoch=${encodeURIComponent(held.epoch)}&revision=${held.revision}`, { signal: controller.signal });
      if (version !== generation || selected !== held) return;
      if (data.epoch !== held.epoch || data.revision !== held.revision) throw Object.assign(new Error('Selected tool epoch/revision changed'), {status:409});
      const native = held.backend === 'claude';
      if (!native && (data.backend === 'claude' || data.part?.id !== held.partId || data.part?.type !== 'tool'))
        throw Object.assign(new Error('Selected tool association changed'), {status:409});
      // OpenCode: the server normalizes the diff beside the raw part (data.diff); raw text and counts stay toolSnapshotText's.
      const rawSnapshot = native ? nativeSnapshot(data, held) : { ...toolSnapshotText(data.part.state || {}, data.rawState),
        time: data.part.state?.time?.start ?? null, endTime: data.part.state?.time?.end ?? null, diff: data.diff ?? null }, status = rawSnapshot.status;
      const inputText = rawSnapshot.input, outputText = rawSnapshot.output;
      if (new TextEncoder().encode(inputText + outputText).length > SNAPSHOT_LIMIT) throw new Error('Selected input/output exceeds the 8 MiB snapshot limit; nothing cropped');
      // Views budgeted for every pretty/readable/raw combination, computed once per snapshot.
      snapshot = { ...rawSnapshot, observedAt:Date.now(), views: snapshotViews(inputText, outputText) };
      // Only explicit host projection permits polling a selected live call.
      // Missing transcript output never implies native activity; history stops.
      terminal = native ? (!rawSnapshot.live || rawSnapshot.terminal) :
        ['completed', 'error', 'cancelled', 'aborted'].includes(status) || !!data.messageCompleted;
      failures = 0; nextRead = Date.now() + 2000;
      render();
      note(`${status}${data.messageCompleted && !['completed', 'error'].includes(status) ? ' · message ended' : ''} · stored snapshot ${new Date(data.fetchedAt || Date.now()).toLocaleTimeString()}${terminal ? ' · automatic refresh stopped' : ''}`);
    } catch (error) {
      if (version !== generation || selected !== held) return;
      failures++; nextRead = Date.now() + Math.min(15000, 3000 * 2 ** Math.min(failures - 1, 3));
      terminal = [400, 403, 404, 409, 413].includes(error.status);
      note(`${error.message || 'Read timed out'}${snapshot ? ' · last successful snapshot retained' : ''} · ${held.sourceError ? 'click Inspect again to recheck the source' : 'refresh to retry'}${terminal ? ' · automatic refresh stopped' : ''}`, true);
    } finally {
      clearTimeout(timeout);
      if (recordController === controller) recordController = null;
    }
  }

  async function pollInspection() {
    if (polling || document.hidden) return;
    polling = true;
    try {
      const mail = await json('/api/tool-selection');
      if (typeof mail.epoch !== 'string' || !Number.isInteger(mail.revision)) throw new Error('Invalid selection relay');
      selectInspection(mail);
      render();
      await readInspection();
    } catch (error) {
      if (selected) note(`Selection relay unavailable: ${error.message} · held call retained`, true);
    } finally { polling = false; }
  }
  function resize(divider, container, property, initial, minimum, maximum) {
    let percentage = initial, dragging = null;
    const apply = value => {
      percentage = Math.max(minimum, Math.min(maximum, value));
      container.style.setProperty(property, `${percentage}%`);
      divider.setAttribute('aria-valuemin', minimum); divider.setAttribute('aria-valuemax', maximum);
      divider.setAttribute('aria-valuenow', Math.round(percentage));
    };
    apply(initial);
    divider.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault(); divider.focus(); divider.setPointerCapture(event.pointerId);
      dragging = { id: event.pointerId, y: event.clientY, percentage };
    });
    divider.addEventListener('pointermove', event => {
      if (!dragging || dragging.id !== event.pointerId) return;
      const height = container.getBoundingClientRect().height;
      if (height) apply(dragging.percentage + (event.clientY - dragging.y) / height * 100 * (property === '--inspector-height' ? -1 : 1));
    });
    const finish = () => { dragging = null; };
    divider.addEventListener('pointerup', finish); divider.addEventListener('pointercancel', finish);
    divider.addEventListener('lostpointercapture', finish);
    divider.addEventListener('keydown', event => {
      const direction = property === '--inspector-height' ? -1 : 1;
      if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
        event.preventDefault(); apply(percentage + (event.key === 'ArrowUp' ? -5 : 5) * direction);
      } else if (event.key === 'Home' || event.key === 'End') {
        event.preventDefault(); apply(event.key === 'Home' ? minimum : maximum);
      }
    });
  }
  resize($('inspector-divider'), $('inspector-content'), '--inspector-input-height', 45, 15, 85);
  resize($('inspector-outer-divider'), $('work-area'), '--inspector-height', 55, 25, 70);
  $('inspector-artifact-read').onclick = readArtifact;
  $('inspector-artifact-copy').onclick = async () => {
    if (!artifactSnapshot) return;
    const version = generation;
    try { await navigator.clipboard.writeText(artifactSnapshot.text); }
    catch { if (version === generation) note('Saved-artifact clipboard unavailable; select the separate saved text manually.', true); }
  };
  $('inspector-retry').onclick = () => { terminal = false; nextRead = 0; failures = 0; void pollInspection(); };
  for (const kind of ['input', 'output']) {
    const wrapToggle = $('inspector-wrap-' + kind), region = kind === 'input' ? input : output;
    wrapToggle.checked = true;
    wrapToggle.title = kind === 'input' ?
      'Wrap long lines in Input and in the Diff above it. Off preserves line breaks and scrolls horizontally; raw Copy is unchanged.' :
      'Wrap long lines within this region. Off preserves line breaks and scrolls horizontally; raw Copy is unchanged.';
    wrapToggle.onchange = () => {
      wrap[kind] = wrapToggle.checked;
      // CSS only: no repaint/parser work, even with an active text selection.
      region.classList.toggle('inspector-nowrap', !wrap[kind]);
      if (kind === 'input') diffReader?.setWrap(wrap.input);
    };
    const toggle = $('inspector-pretty-' + kind);
    const readingToggle = $('inspector-readable-' + kind);
    readingToggle.checked = true; // ON after every page load, like Pretty JSON.
    readingToggle.title = 'Reading view, not valid JSON: decode multiline/tabbed string values once and show Markdown-looking text rendered (each block has a Rendered/Source switch). Keys and ordinary values stay JSON; raw Copy is unchanged.';
    readingToggle.onchange = () => { readable[kind] = readingToggle.checked; render(); };
    toggle.checked = true; // Page-local defaults, not persistent settings.
    toggle.onchange = () => { pretty[kind] = toggle.checked; readingToggle.disabled = !toggle.checked; render(); };
    $('inspector-copy-' + kind).onclick = async () => {
      if (!snapshot || snapshot.backend === 'claude' && !snapshot[kind + 'Available']) return;
      const version = generation;
      // Always the held raw snapshot, even when presentation is pretty or repaint
      // is deferred by a text selection. Manual browser selection copies the view.
      try { await navigator.clipboard.writeText(snapshot[kind]); }
      catch { if (version === generation) note('Clipboard unavailable; turn Pretty JSON off, then select and copy the original text manually.', true); }
    };
  }
  // The held call's callRef, read-only: a handle clicked in the inspector roots View at that call's turn (app.js).
  globalThis.opcToolInspector = Object.freeze({ heldCallRef: () => selected?.callRef || null });
  document.addEventListener('selectionchange', render);
  document.addEventListener('visibilitychange', () => { void pollInspection(); });
  setInterval(() => { void pollInspection(); }, 700);
  // The head row's live elapsed time ticks once a second in place, independent of reads.
  setInterval(() => { if (!document.hidden) paintTime(); }, 1000);
  void pollInspection();
})();
