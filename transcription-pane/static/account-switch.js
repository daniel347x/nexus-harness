/* Shared Claude account reader and deliberate switch control. Account names ONLY change the view;
 * the green ring follows the server's selected login, not the view or the requested switch target.
 * Quota caches are account-labelled; the server gives the active label and its named view one reading.
 * Only Switch and Cancel send a POST. Durable operation IDs survive reload/lost replies; a lost
 * Cancel reply reads the same receipt, never switches.
 */
'use strict';

(function () {
  const LABELS = ['personal', 'work'];
  const TERMINAL = ['active', 'restored', 'recovery_required', 'refused', 'failed', 'cancelled'];
  const STATUS_URL = '/api/claude/accounts';
  const SWITCH_URL = '/api/claude/account-switch';
  const STORE_KEY = 'pane_claude_account_switch_v1';
  const POLL_MS = 2000;
  const IDLE_MS = 60000;
  const NAMES = { personal: 'Personal', work: 'Work' };
  const mounted = new WeakSet();
  const instances = new WeakMap();

  const say = (label) => NAMES[label] || (label ? label.charAt(0).toUpperCase() + label.slice(1) : '');
  const done = (phase) => TERMINAL.indexOf(phase) !== -1;

  function newId() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
    } catch (error) { /* older browser: the timestamp form below is still unique per tab */ }
    return 'acct-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
  }

  async function readJson(url, options) {
    const response = await fetch(url, Object.assign({ cache: 'no-store' }, options || {}));
    let body = null;
    try { body = await response.json(); } catch (error) { body = null; }
    return { ok: response.ok, status: response.status, body: body };
  }

  // @beacon[
  //   id=opc-pane@mountAccountSwitch,
  //   slice_labels=opc-pane,opc-claude-restart,
  //   kind=ast,
  //   comment=Selection ring stays independent of lifecycle outcome: starting/recovering/unknown reconcile the same ID through GET only; restored is failed-switch/old-ready and Manager/notice pending stays explicit. Only Switch or waiting Cancel posts; late-reply guards and per-account quota viewing remain intact.,
  // ]
  function mount(root) {
    if (!root || mounted.has(root)) return null;
    mounted.add(root);
    const current = root.querySelector('[data-account-current]');
    const statusEl = root.querySelector('[data-account-status]');
    const identityEl = root.querySelector('[data-account-identity]');
    const viewingEl = root.querySelector('[data-account-viewing]');
    const switchButton = root.querySelector('[data-account-switch]');
    const cancelButton = root.querySelector('[data-account-cancel]');
    const quotaEl = root.querySelector('[data-account-quotas]');
    const quotaStatus = root.querySelector('[data-account-quota-status]');
    const refreshButton = root.querySelector('[data-account-refresh]');
    const details = root.querySelector('[data-account-details]');
    const buttons = Array.prototype.slice.call(root.querySelectorAll('button[data-account]'));
    const view = { status: null, operation: null, pendingId: null, error: null, timer: null, idle: null, looping: false,
      shown: null, submitting: false, cancelling: false, opRevision: 0, polling: false,
      quotas: new Map(), quotaBusy: new Set(), quotaErrors: new Map(), quotaTimers: new Map() };

    function remember(id) {
      try { sessionStorage.setItem(STORE_KEY, JSON.stringify({ id: id })); } catch (error) { /* private mode */ }
    }

    function forget() {
      view.pendingId = null;
      try { sessionStorage.removeItem(STORE_KEY); } catch (error) { /* private mode */ }
    }

    function paint() {
      const status = view.status || {};
      const accounts = Array.isArray(status.accounts) ? status.accounts : [];
      const byLabel = {};
      accounts.forEach((row) => { if (row && row.label) byLabel[row.label] = row; });
      const selected = (status.selected && status.selected.label) || null;
      const identity = (status.selected && status.selected.identity) || {};
      const op = view.operation;
      const busy = view.submitting || Boolean(view.pendingId) || Boolean(op && !done(op.phase));
      const available = status.available === true;
      if (busy && details) details.open = true; // A reload must not hide the waiting switch's Cancel.

      if (current) {
        current.textContent = selected ? say(selected) : '—';
        current.title = selected
          ? [say(selected), identity.email, identity.plan].filter(Boolean).join(' · ')
          : (view.error || 'Claude account unknown');
      }
      if (identityEl) identityEl.textContent = selected ? [identity.email, identity.plan].filter(Boolean).join(' · ') : '';
      buttons.forEach((button) => {
        const label = button.getAttribute('data-account');
        const row = byLabel[label];
        const active = selected === label;
        button.disabled = false; // Viewing is always read-only, even while a switch waits or status is unknown.
        button.textContent = say(label) + (active ? ' · active' : '');
        button.setAttribute('aria-pressed', view.shown === label ? 'true' : 'false');
        button.setAttribute('data-active', active ? 'true' : 'false');
        if (button.classList) button.classList.toggle('is-active', active);
        button.title = 'View ' + say(label) + ' quota only' + (active ? ' · currently active login' : '') +
          (!row || row.enrolled === false ? ' · not signed in/enrolled' : '');
      });
      if (viewingEl) viewingEl.textContent = view.shown ? 'Viewing ' + say(view.shown) +
        (selected === view.shown ? ' · active account' : selected ? ' · NOT active; ' + say(selected) + ' stays active' : ' · active account unknown') : 'Reading accounts…';
      if (switchButton) {
        const row = byLabel[view.shown];
        switchButton.textContent = selected === view.shown ? say(view.shown) + ' is active' : 'Switch to ' + say(view.shown);
        switchButton.disabled = busy || !available || !view.shown || selected === view.shown || !row || row.enrolled === false;
        switchButton.title = 'Restart the Claude host when running Claude work is quiet, then resume the Manager on ' + say(view.shown);
      }
      if (cancelButton) {
        cancelButton.hidden = !(op && !done(op.phase) && op.canCancel === true);
        cancelButton.disabled = view.cancelling;
        cancelButton.textContent = view.cancelling ? 'Cancelling…' : 'Cancel switch';
      }

      let phase = '';
      let text = '';
      if (view.status === null) {
        phase = 'loading';
        text = 'reading the Claude account…';
      } else if (busy) {
        phase = op?.phase || 'waiting';
        const messages = {
          switching: 'Switching to ' + say(op?.account) + ': stopping admission won; host exit and replacement readiness are not yet proved. Too late to cancel.',
          starting: 'Replacement starting; not ready yet. ' + (op?.detail || ''),
          recovering: 'Recovering the same operation. ' + (op?.detail || ''),
          unknown: 'Operation evidence pending/unknown; checking this same ID only. ' + (op?.detail || '')
        };
        text = messages[op?.phase] || (op && done(op.phase) ? op.detail : null) ||
          'Waiting for running Claude work to finish before switching to ' + say(op?.account || view.shown) +
          '. Running sessions keep their original login; Manager readiness will be reported separately.';
      } else if (op) {
        phase = op.phase;
        text = op.alreadyActive ? say(op.account) + ' already selected; service not checked' :
          (op.detail || say(op.account) + ' ' + op.phase);
        if (op.phase === 'recovery_required' && op.recoverCommand) text += ' · ' + op.recoverCommand;
      } else if (!available) {
        phase = 'unavailable';
        text = view.error || status.error || 'account switching is not available in this checkout';
      } else if (LABELS.every((label) => !byLabel[label] || byLabel[label].enrolled === false)) {
        phase = 'unconfigured';
        text = 'no Claude account is enrolled yet — run oc.sh claude_accounts enroll work';
      }
      if (view.error) text += (text ? ' · ' : '') + view.error;
      if (statusEl) statusEl.textContent = text;
      root.setAttribute('data-phase', phase || (available ? 'ready' : 'unavailable'));
      root.setAttribute('data-busy', busy ? 'true' : 'false');
      paintQuota();
    }

    function quotaAge(data) {
      // The server adds the provider's own cross-process age, so this is the endpoint reading's age.
      return Number.isFinite(data?.ageSeconds) ? data.ageSeconds + Math.max(0, (Date.now() - data.receivedAt) / 1000) : null;
    }
    function quotaRetryAt(label) {
      const data = view.quotas.get(label), reading = data?.claude;
      const at = Number.isFinite(reading?.retry_at) ? reading.retry_at : Number.isFinite(data?.retryAt) ? data.retryAt : null;
      return at !== null && at * 1000 > Date.now() ? at * 1000 : null;
    }
    function agoText(seconds) {
      return seconds < 45 ? 'just now' : seconds < 90 ? '1 min ago' : seconds < 3600 ? Math.floor(seconds / 60) + ' min ago' :
        Math.floor(seconds / 3600) + ' h ago';
    }
    function untilText(ms) {
      const seconds = Math.max(0, Math.ceil(ms / 1000));
      return seconds < 60 ? seconds + 's' : seconds < 3600 ? Math.ceil(seconds / 60) + ' min' : Math.ceil(seconds / 3600) + ' h';
    }
    function clockStamp(seconds) {
      try { return new Date(seconds * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
      catch (error) { return ''; }
    }
    function paintQuota() {
      if (!quotaEl) return;
      const label = view.shown, data = view.quotas.get(label), reading = data?.claude;
      const age = quotaAge(data);
      const retryAt = quotaRetryAt(label);
      const now = Number.isFinite(data?.now) ? data.now + Math.max(0, (Date.now() - data.receivedAt) / 1000) : Date.now() / 1000;
      const rows = [], add = (text, cls = 'account-quota-note') => { const n = document.createElement('div'); n.className = cls; n.textContent = text; rows.push(n); };
      const known = view.status?.accounts?.find(row => row.label === label);
      if (!label) add('Choose an account to view its quota.');
      else if (view.status?.available && (!known || known.enrolled === false)) add(say(label) + ' is not signed in/enrolled.');
      else if (!reading) add(view.quotaErrors.get(label) ? 'Quota unavailable: ' + view.quotaErrors.get(label) : 'Reading ' + say(label) + ' quota…');
      else if (!reading.ok && !reading.stale) add('Quota unavailable: ' + (reading.note || reading.error || data.error || 'no reading') +
        (retryAt ? ' · next read in ' + untilText(retryAt - Date.now()) : ''));
      else {
        const windows = Array.isArray(reading.windows) ? reading.windows : [];
        // Keep missing windows visible as unknown, never as zero or another account's data.
        const names = ['5-hour', 'Weekly', 'Fable weekly'];
        const matches = [/^(five[- ]hour|5[- ]hour|5h)$/i, /^(weekly|seven[- ]day)$/i, /fable/i];
        const used = new Set();
        names.forEach((name, i) => {
          const index = windows.findIndex((w, n) => !used.has(n) && matches[i].test(String(w.label)));
          if (index >= 0) used.add(index);
          quotaRow(name, index >= 0 ? windows[index] : {}, now, rows);
        });
        windows.forEach((w, i) => { if (!used.has(i)) quotaRow(String(w.label || 'Window'), w, now, rows); });
        if (reading.extra_usage_enabled) add('Extra usage is on');
        const observed = Number.isFinite(reading.observed_at) ? clockStamp(reading.observed_at)
          : Number.isFinite(data.observedAt) ? clockStamp(data.observedAt) : '';
        if (reading.stale) add('Stale: the last reading' +
          (age === null ? '' : ' (' + agoText(age) + (observed ? ', as of ' + observed : '') + ')') +
          ' · current reading unavailable: ' + (reading.note || reading.error || 'unknown'));
        if (retryAt) add('Rate limited: next read in ' + untilText(retryAt - Date.now()) +
          (reading.note ? ' · ' + reading.note : ''));
      }
      const key = rows.map(n => n.outerHTML).join('');
      if (quotaEl.renderKey !== key) { quotaEl.replaceChildren(...rows); quotaEl.renderKey = key; }
      const stale = age !== null && age > 300 || reading?.stale;
      quotaEl.setAttribute('data-stale', stale ? 'true' : 'false');
      const failed = data?.error || view.quotaErrors.get(label);
      if (quotaStatus) quotaStatus.textContent = label ? say(label) + ' quota · ' +
        (age === null ? 'not read yet' : 'read ' + agoText(age)) +
        (reading?.plan ? ' · ' + String(reading.plan).replace(/^max_(\d+)x$/i, 'Max $1×') : '') +
        (stale ? ' · stale' : '') + (view.quotaBusy.has(label) || data?.refreshing ? ' · updating…' : '') +
        (retryAt ? ' · rate limited · next read in ' + untilText(retryAt - Date.now()) : '') +
        (failed ? ' · last refresh failed: ' + failed : '') : '';
      if (refreshButton) refreshButton.textContent = retryAt ? 'Waiting · ' + untilText(retryAt - Date.now()) : 'Refresh quota';
    }

    async function readQuota(label, force = false) {
      if (!quotaEl || !LABELS.includes(label) || view.quotaBusy.has(label)) return;
      view.quotaBusy.add(label); paintQuota();
      try {
        const result = await readJson('/api/usage?account=' + encodeURIComponent(label) + (force ? '&refresh=1' : ''));
        const data = result.body;
        if (!result.ok || data?.schemaVersion !== 1 || data.backend !== 'usage') throw new Error(data?.error || 'HTTP ' + result.status);
        if (data.claude && data.claude.account?.label !== label) throw new Error('account label mismatch; quota not shown');
        view.quotas.set(label, { ...data, receivedAt: Date.now() }); view.quotaErrors.delete(label);
        clearTimeout(view.quotaTimers.get(label));
        const retry = quotaRetryAt(label);
        if (data.refreshing) view.quotaTimers.set(label, setTimeout(() => readQuota(label), POLL_MS));
        // A provider backoff is a schedule, not a failure: read again when its gate ends, never before.
        else if (retry) view.quotaTimers.set(label, setTimeout(() => readQuota(label), Math.max(1000, retry - Date.now())));
      } catch (error) { view.quotaErrors.set(label, error.message); }
      finally { view.quotaBusy.delete(label); paintQuota(); }
    }

    function show(label) {
      if (!LABELS.includes(label)) return;
      view.shown = label;
      if (details) details.open = true;
      paint();
      void readQuota(label, false); // viewing is read-only: the cached reading is enough, never a provider read
    }

    function announce() {
      const op = view.operation;
      if (!op || typeof CustomEvent !== 'function') return;
      document.dispatchEvent(new CustomEvent('oc-account-switch', { detail: { phase: op.phase, account: op.account } }));
    }

    function schedule(ms) {
      clearTimeout(view.timer);
      view.timer = setTimeout(poll, ms);
    }

    function beginLoop() {
      if (view.looping) return;
      view.looping = true;
      schedule(POLL_MS);
    }

    function endLoop() {
      view.looping = false;
      clearTimeout(view.timer);
      view.timer = null;
    }

    async function poll() {
      if (!view.pendingId) return;
      if (view.polling) return schedule(POLL_MS);
      const revision = view.opRevision;
      view.polling = true;
      let result;
      try {
        result = await readJson(SWITCH_URL + '?operationId=' + encodeURIComponent(view.pendingId));
      } catch (error) {
        if (revision !== view.opRevision) return;
        view.error = 'switch status unreachable: ' + error.message;
        paint();
        return schedule(POLL_MS);
      } finally {
        view.polling = false;
      }
      if (revision !== view.opRevision) return; // A late pre-Cancel read cannot resurrect a waiting operation.
      if (result.status === 404) {
        forget();
        return refreshStatus(false);
      }
      if (!result.ok || !result.body || !result.body.operation) {
        view.error = (result.body && result.body.error) || 'switch status failed (' + result.status + ')';
        paint();
        return schedule(POLL_MS);
      }
      view.operation = result.body.operation;
      view.error = null;
      paint();
      if (!done(view.operation.phase)) return schedule(POLL_MS);
      endLoop();
      forget();
      announce();
      return refreshStatus(true);
    }

    async function refreshStatus(force) {
      try {
        const result = await readJson(STATUS_URL + (force ? '?refresh=1' : ''));
        if (result.ok && result.body) {
          view.status = result.body;
          view.error = null;
          if (!view.shown) view.shown = LABELS.includes(result.body.selected?.label) ? result.body.selected.label : 'personal';
          if (!view.pendingId) {
            view.operation = result.body.operation || view.operation;
            if (view.operation && !done(view.operation.phase)) view.pendingId = view.operation.id;
          }
        } else {
          view.status = { available: false, accounts: [], selected: null };
          view.error = (result.body && result.body.error) || 'account status failed (' + result.status + ')';
        }
      } catch (error) {
        view.status = { available: false, accounts: [], selected: null };
        view.error = 'account status unreachable: ' + error.message;
      }
      paint();
      if (view.pendingId) beginLoop();
      if (quotaEl) for (const row of view.status?.accounts || []) {
        if (row.enrolled !== false && LABELS.includes(row.label)) void readQuota(row.label);
      }
    }

    async function start(label) {
      if (LABELS.indexOf(label) === -1) return;
      if (view.pendingId || view.submitting || view.operation && !done(view.operation.phase)) return;
      if (view.status && view.status.selected && view.status.selected.label === label) return;
      const id = newId();
      view.opRevision++;
      view.pendingId = id;
      view.submitting = true;
      view.operation = { id, account: label, phase: 'waiting', canCancel: false };
      view.error = null;
      remember(id);
      paint();
      let result;
      try {
        result = await readJson(SWITCH_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ schemaVersion: 1, backend: 'claude', account: label, operationId: id }),
        });
      } catch (error) {
        // The request may have reached the pane: poll this id rather than post a second switch.
        view.error = 'the switch request did not answer: ' + error.message;
        view.submitting = false;
        paint();
        return poll();
      }
      view.submitting = false;
      if (result.ok || result.status === 202) {
        view.operation = (result.body && result.body.operation) || null;
        if (!view.operation || !done(view.operation.phase)) { paint(); return beginLoop(); }
        forget();
        paint();
        announce();
        return refreshStatus(false);
      }
      if (result.status === 409) {
        forget();
        view.error = (result.body && result.body.error) || 'another switch is already in progress';
        await refreshStatus(false);
        if (view.pendingId) beginLoop();
        return;
      }
      forget();
      view.operation = null;
      view.error = (result.body && result.body.error) || 'switch refused (' + result.status + ')';
      paint();
    }

    async function cancel() {
      const op = view.operation;
      if (!op || op.canCancel !== true || done(op.phase) || view.cancelling) return;
      view.opRevision++;
      view.cancelling = true; paint();
      try {
        const result = await readJson(SWITCH_URL + '/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ schemaVersion: 1, backend: 'claude', operationId: op.id }) });
        if (result.body?.operation) view.operation = result.body.operation;
        if (!result.ok) view.error = result.body?.error || 'Cancel refused: the switch may have started; checking its status.';
        else view.error = null;
      } catch (error) { view.error = 'Cancel reply lost; checking the same switch: ' + error.message; }
      finally { view.cancelling = false; }
      if (view.operation && done(view.operation.phase)) {
        endLoop(); forget(); paint(); announce(); return refreshStatus(true);
      }
      paint(); return poll();
    }

    function idleRefresh() {
      clearTimeout(view.idle);
      view.idle = setTimeout(() => { refreshStatus(false).then(idleRefresh); }, IDLE_MS);
    }

    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(STORE_KEY) || 'null'); } catch (error) { saved = null; }
    if (saved && typeof saved.id === 'string') view.pendingId = saved.id;
    buttons.forEach((button) => {
      button.addEventListener('click', () => show(button.getAttribute('data-account')));
    });
    if (switchButton) switchButton.addEventListener('click', () => start(view.shown));
    if (cancelButton) cancelButton.addEventListener('click', cancel);
    if (refreshButton) refreshButton.addEventListener('click', () => readQuota(view.shown, true));
    paint();
    // Warm both read-only caches alongside account discovery, not after native auth status finishes.
    if (quotaEl) LABELS.forEach(label => { void readQuota(label); });
    refreshStatus(false).then(() => {
      if (view.pendingId) poll();
      idleRefresh();
    });
    const api = { root, refresh: (force = true) => { void readQuota(view.shown, force); return refreshStatus(force); }, paintQuota, select: show, switch: start, cancel, state: view };
    instances.set(root, api);
    return api;
  }

  function quotaRow(name, w, now, rows) {
    const node = document.createElement('div'); node.className = 'account-quota m-meter';
    const at = typeof w.resets_at === 'number' ? w.resets_at : Date.parse(w.resets_at) / 1000;
    const expired = Number.isFinite(at) && at <= now;
    const percent = Number.isFinite(w.percent) && !expired ? Math.max(0, Math.min(100, w.percent)) : null;
    const label = document.createElement('span'); label.className = 'account-quota-label m-meter-label'; label.textContent = name;
    const figure = document.createElement('strong'); figure.className = 'account-quota-percent m-meter-percent';
    figure.textContent = percent === null ? '— used' : Math.round(percent) + '% used';
    const reset = document.createElement('span'); reset.className = 'account-quota-reset m-meter-sub';
    const minutes = Number.isFinite(at) ? Math.max(1, Math.ceil((at - now) / 60)) : null;
    const duration = minutes >= 1440 ? Math.floor(minutes / 1440) + 'd ' + Math.floor(minutes % 1440 / 60) + 'h' :
      minutes >= 60 ? Math.floor(minutes / 60) + 'h ' + minutes % 60 + 'm' : minutes + 'm';
    reset.textContent = !Number.isFinite(at) ? 'Reset time unknown' : expired ? 'Reset passed · new reading needed' :
      'Resets in ' + duration + ' · ' + new Date(at * 1000).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
    node.append(label, figure, reset);
    node.title = name + ': ' + figure.textContent + ' · ' + reset.textContent;
    if (w.allowance_percent_of_weekly != null) {
      const share = document.createElement('span'); share.className = 'account-quota-reset';
      share.textContent = 'Up to ' + w.allowance_percent_of_weekly + '% of weekly · ' + (Number.isFinite(w.used_percent_of_weekly) ? w.used_percent_of_weekly + '%' : 'unknown') + ' of weekly used';
      node.append(share);
    }
    rows.push(node);
  }

  window.OCAccounts = { mount, get: root => instances.get(root), LABELS };

  function autoMount() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-oc-accounts]'), mount);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', autoMount);
  else autoMount();
})();
