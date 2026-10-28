/* Saved OpenAI account views and deliberate native selection, desktop + phone.
 * Native public readback alone owns the active badge. Quota is optional, never a switch gate.
 * This controller deliberately does NOT inherit Claude's restart/wait/cancel protocol.
 */
'use strict';
(function () {
  const instances = new WeakMap(), STORE = 'pane_chatgpt_account_switch_v1';
  const STATUS = '/api/chatgpt/accounts', SWITCH = '/api/chatgpt/account-switch', USAGE = '/api/chatgpt/usage';
  const pending = op => op && ['pending', 'attempting'].includes(op.phase);
  const epoch = value => typeof value === 'number' ? value : typeof value === 'string' ? Date.parse(value) / 1000 : NaN;
  const duration = seconds => {
    const m = Math.max(0, Math.ceil(seconds / 60)), d = Math.floor(m / 1440), h = Math.floor(m % 1440 / 60);
    return d ? d + 'd ' + h + 'h' : h ? h + 'h ' + m % 60 + 'm' : m + 'm';
  };
  async function json(url, options) {
    const response = await fetch(url, Object.assign({cache: 'no-store'}, options || {}));
    const data = await response.json();
    if (!response.ok) throw new Error(data?.error || 'HTTP ' + response.status);
    if (data?.schemaVersion !== 1) throw new Error('Unexpected GPT account reply');
    return data;
  }

  // @beacon[
  //   id=opc-pane@mountChatGptAccounts,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Desktop and phone native GPT account owner: arbitrary saved labels with separate viewed active and pending states; optional account-bound quota and banked resets never gate Switch. Immutable IDs recover lost replies by reads only; default phone polls never paint this view and historical receipts never set active badges.,
  // ]
  function mount(root) {
    if (!root) return null;
    if (instances.has(root)) return instances.get(root);
    const doc = root.ownerDocument || document, part = name => root.querySelector('[data-gpt-' + name + ']');
    const state = {status: null, shown: null, quota: null, quotaError: null, quotaBusy: false,
      operation: null, intent: null, submitting: false, statusBusy: false, statusRevision: 0,
      quotaRevision: 0, error: null, timer: null, quotaTimer: null};
    const node = (tag, value, cls) => { const n = doc.createElement(tag); if (value != null) n.textContent = value; if (cls) n.className = cls; return n; };
    const rows = () => state.status?.accounts || [];
    const name = id => rows().find(r => r.id === id)?.label || id || '—';
    const active = () => state.status?.active?.known ? state.status.active.credential_id : null;
    const detail = () => { const d = part('details'); if (d) d.open = true; };
    function remember(intent) {
      state.intent = intent;
      try { if (intent) sessionStorage.setItem(STORE, JSON.stringify(intent)); else sessionStorage.removeItem(STORE); }
      catch (error) { /* Server receipt still exists; loss of browser storage does not replay a selection. */ }
    }
    function paint() {
      const selected = active(), accounts = rows(), busy = state.submitting || !!state.intent || pending(state.operation);
      if (part('current')) part('current').textContent = selected ? name(selected) : 'Active unknown';
      if (part('current')) part('current').title = selected || state.status?.active?.detail || 'Reading native OpenAI accounts';
      const buttons = accounts.map(row => {
        const b = node('button', row.label + (row.id === selected ? ' · active' : ''), 'mini');
        b.type = 'button'; b.setAttribute('data-gpt-account', row.id);
        b.setAttribute('aria-pressed', String(row.id === state.shown));
        b.setAttribute('data-active', String(row.id === selected));
        b.title = 'View quota only · ' + row.id + (row.method ? ' · ' + row.method : '');
        b.addEventListener('click', () => viewAccount(row.id));
        return b;
      });
      part('accounts')?.replaceChildren(...buttons);
      if (part('viewing')) part('viewing').textContent = state.shown ? 'Viewing ' + name(state.shown) +
        (selected === state.shown ? ' · active account' : selected ? ' · NOT active; ' + name(selected) + ' stays active' : ' · active account unknown') :
        state.status ? 'No saved OpenAI login' : 'Reading saved OpenAI logins…';
      const button = part('switch');
      if (button) {
        button.textContent = busy ? 'Checking native selection…' : selected === state.shown && selected ? name(selected) + ' is active' : 'Switch to ' + name(state.shown);
        button.disabled = busy || !state.status?.available || !accounts.some(r => r.id === state.shown) || selected === state.shown;
        button.title = 'Select this saved OpenAI login for new requests. No restart; in-flight requests keep their captured login.';
      }
      if (busy) detail();
      const op = state.operation;
      let message = state.error || state.status?.error || '';
      if (state.intent || state.submitting) message = 'Checking the outcome of this exact Switch; it will not be sent again.' + (message ? ' ' + message : '');
      else if (op) message = (op.label || name(op.credentialId)) + ' · ' + op.phase + ': ' + op.detail +
        (op.phase === 'confirmed' && active() !== op.credentialId ? ' This is a historical receipt; that login is NOT currently confirmed active.' : '') +
        (op.nativeProblem ? ' ' + op.nativeProblem + '; current selection was verified separately.' : '') + (message ? ' ' + message : '');
      if (part('status')) part('status').textContent = message;
      root.setAttribute('data-phase', state.intent ? 'pending' : op?.phase || (state.status?.available ? 'ready' : 'unavailable'));
      root.setAttribute('data-busy', String(busy));
      if (part('enrollment')) part('enrollment').hidden = accounts.length > 1;
      paintQuota();
    }
    function resetText(value, now) {
      const at = epoch(value);
      return !Number.isFinite(at) ? 'reset time unknown' : (at <= now ? 'recorded reset passed · ' : 'resets ') +
        new Date(at * 1000).toLocaleString() + (at > now ? ' · in ' + duration(at - now) : ' · current quota unknown');
    }
    function paintQuota() {
      const data = state.quota, reading = data?.chatgpt;
      const now = data ? data.now + Math.max(0, (Date.now() - data.receivedAt) / 1000) : Date.now() / 1000;
      const age = Number.isFinite(data?.ageSeconds) ? data.ageSeconds + Math.max(0, (Date.now() - data.receivedAt) / 1000) : null;
      const stale = !!reading?.stale || age !== null && age > 300 || !!data?.error || !!state.quotaError;
      const out = [], addNote = value => out.push(node('div', value, 'account-quota-note'));
      if (part('plan')) part('plan').textContent = reading?.plan || '';
      if (!data) addNote(state.quotaError || (state.shown ? 'Reading this login’s quota…' : 'Quota unknown until a saved login is viewed.'));
      else if (!reading?.ok && !reading?.stale) addNote('Quota unknown · ' + (reading?.error || data.error || 'no reading yet') + ' · Native switching remains available.');
      else {
        if (stale) addNote('STALE / historical reading · current room is not confirmed.');
        if (reading?.limit_reached) addNote('Provider reported limit reached' + (stale ? ' in the historical reading' : ''));
        for (const w of reading?.windows || []) {
          const at = epoch(w.resets_at), passed = Number.isFinite(at) && at <= now;
          const row = node('div', null, 'account-quota');
          const known = Number.isFinite(w.percent) && !passed;
          row.append(node('span', w.label || 'Quota window', 'account-quota-label'),
            node('span', known ? Math.round(w.percent) + '% used' : '? used', 'account-quota-percent'),
            node('span', resetText(w.resets_at, now), 'account-quota-reset'));
          out.push(row);
        }
        for (const [model, m] of Object.entries(reading?.models || {})) {
          const expired = Number.isFinite(epoch(m.available_at)) && epoch(m.available_at) <= now;
          addNote(model + ' · ' + (stale ? 'historical availability: ' : '') + (expired ? 'availability unknown after recorded reset' :
            m.available === true ? 'available' : m.available === false ? 'not available' + (m.available_at ? ' · ' + resetText(m.available_at, now) : '') : 'availability unknown'));
        }
        for (const note of reading?.notes || []) addNote(note);
      }
      if (reading?.account?.binding) addNote(reading.account.binding);
      if (data?.error || state.quotaError) addNote('Last refresh: ' + (state.quotaError || data.error));
      const quotas = part('quotas');
      if (quotas) { quotas.setAttribute('data-stale', String(stale)); quotas.replaceChildren(...out); }
      const retry = epoch(data?.retryAt || reading?.retry_at), wait = Number.isFinite(retry) && retry > now;
      if (part('quota-status')) part('quota-status').textContent = name(state.shown) + ' · ' +
        (age === null ? 'no provider reading yet' : 'read ' + duration(age) + ' ago') +
        (state.quotaBusy || data?.refreshing ? ' · updating…' : '') + (wait ? ' · next read in ' + duration(retry - now) : '');
      if (part('refresh')) part('refresh').disabled = !state.shown || state.quotaBusy;
      const banked = part('banked');
      if (banked) {
        const credits = reading?.reset_credits, bankedNodes = [];
        bankedNodes.push(node('strong', 'Banked resets · ' + name(state.shown)));
        if (!Array.isArray(credits)) bankedNodes.push(node('div', 'Banked resets unknown · not a verified zero', 'account-quota-note'));
        else {
          const live = credits.filter(c => !Number.isFinite(epoch(c.expires_at)) || epoch(c.expires_at) > now)
            .sort((a, b) => (epoch(a.expires_at) || Infinity) - (epoch(b.expires_at) || Infinity));
          bankedNodes.push(node('div', (live.length ? live.length + ' available' : 'none banked') + (stale ? ' · as of the historical reading' : ''), 'account-quota-note'));
          for (const c of live) {
            const at = epoch(c.expires_at), urgent = at - now < 86400;
            const n = node('div', Number.isFinite(at) ? 'Expires ' + new Date(at * 1000).toLocaleString() + ' · in ' + duration(at - now) +
              (urgent ? ' · use within 24h or lose it' : '') : 'Expiry unknown', 'account-quota-note');
            if (urgent) n.setAttribute('data-urgent', 'true');
            bankedNodes.push(n);
          }
        }
        banked.replaceChildren(...bankedNodes);
      }
    }
    async function readQuota(id = state.shown, force = false) {
      if (!id) return;
      const revision = ++state.quotaRevision, statusRevision = state.statusRevision;
      state.quotaBusy = true; paintQuota();
      try {
        const data = await json(USAGE + '?credentialId=' + encodeURIComponent(id) + (force ? '&refresh=1' : ''));
        if (revision !== state.quotaRevision || statusRevision !== state.statusRevision || state.shown !== id) return;
        if (data.backend !== 'chatgpt-usage' || data.credentialId !== id ||
            data.chatgpt?.account?.opencode_credential_id && data.chatgpt.account.opencode_credential_id !== id) throw new Error('Foreign GPT quota reply rejected');
        state.quota = {...data, receivedAt: Date.now()}; state.quotaError = null;
      } catch (error) {
        if (revision === state.quotaRevision && state.shown === id) state.quotaError = error.message;
      } finally {
        if (revision === state.quotaRevision) { state.quotaBusy = false; paintQuota(); }
      }
      clearTimeout(state.quotaTimer);
      if (state.quota?.refreshing && state.shown === id) state.quotaTimer = setTimeout(() => void readQuota(id), 2000);
    }
    function viewAccount(id) {
      if (!rows().some(r => r.id === id)) return;
      state.shown = id; state.quota = null; state.quotaError = null;
      detail(); paint(); void readQuota(id);
    }
    function apply(data, exactId = null) {
      if (data.backend !== 'chatgpt-accounts') throw new Error('Unexpected GPT account status');
      if (exactId && state.intent?.id === exactId && data.operation &&
          (data.operation.id !== exactId || data.operation.credentialId !== state.intent.credentialId)) throw new Error('Different GPT intent receipt rejected');
      state.status = data; state.error = null;
      state.operation = data.operation || null;
      if (exactId && state.intent?.id === exactId) {
        if (!data.operation) {
          state.error = 'No retained receipt for that Switch; its outcome is unknown. Nothing was replayed. Read the active login before a new explicit Switch.';
          remember(null); state.submitting = false;
        }
        else if (!pending(data.operation)) { remember(null); state.submitting = false; }
      }
      if (!rows().some(r => r.id === state.shown)) { state.shown = active() || rows()[0]?.id || null; state.quota = null; }
      paint();
    }
    async function refresh(forceQuota = false) {
      if (state.statusBusy) return;
      state.statusBusy = true;
      const revision = ++state.statusRevision, intent = state.intent;
      try {
        const data = await json(intent ? SWITCH + '?operationId=' + encodeURIComponent(intent.id) : STATUS);
        if (revision !== state.statusRevision) return;
        apply(data, intent?.id);
        if (state.shown) void readQuota(state.shown, forceQuota);
      } catch (error) {
        if (revision !== state.statusRevision) return;
        state.error = error.message;
        // An old active badge must not survive a failed current public read.
        if (state.status) state.status = {...state.status, available: false, active: {known: false, detail: error.message}};
        paint();
      } finally {
        state.statusBusy = false; schedule();
      }
    }
    function schedule() {
      clearTimeout(state.timer);
      state.timer = setTimeout(() => { if (!doc.hidden) void refresh(); else schedule(); }, state.intent || pending(state.operation) ? 2000 : 60000);
    }
    async function switchAccount() {
      if (part('switch')?.disabled || state.submitting || state.intent || !state.shown) return;
      const id = window.crypto?.randomUUID?.() || 'gpt-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
      const intent = {id, credentialId: state.shown, expectedActiveCredentialId: active()};
      remember(intent); state.submitting = true; state.error = null;
      const revision = ++state.statusRevision;
      paint(); schedule(); // Read-only recovery runs even if the POST response never arrives.
      try {
        const data = await json(SWITCH, {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({schemaVersion: 1, backend: 'chatgpt', credentialId: intent.credentialId,
            operationId: intent.id, expectedActiveCredentialId: intent.expectedActiveCredentialId})});
        if (state.intent?.id !== intent.id || revision !== state.statusRevision) return;
        apply(data, intent.id);
        doc.dispatchEvent(new CustomEvent('oc-chatgpt-account-switch', {detail: {provider: 'openai', operation: data.operation, active: data.active}}));
      } catch (error) {
        if (state.intent?.id === intent.id) state.error = error.message + ' · querying this intent only, never repeating the POST.';
      } finally {
        if (state.intent?.id === intent.id) state.submitting = false;
        paint(); void refresh();
      }
    }
    part('switch')?.addEventListener('click', () => void switchAccount());
    part('refresh')?.addEventListener('click', () => void refresh(true));
    part('details')?.addEventListener('toggle', () => { if (part('details').open) void refresh(); });
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORE) || 'null');
      if (saved?.id && saved?.credentialId) state.intent = saved;
    } catch (error) { state.error = 'Stored GPT intent could not be read; nothing was replayed.'; }
    const api = {refresh, viewAccount, readQuota, switchAccount, paintQuota, snapshot: () => state};
    instances.set(root, api); paint(); void refresh();
    return api;
  }
  window.OCChatGPTAccounts = {mount, get: root => instances.get(root) || null};
  function boot() { document.querySelectorAll('[data-chatgpt-accounts]').forEach(mount); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
