/* Subscription allotments at a glance: one quiet row in the desktop sidebar (sessions.html, under
   ＋ New Manager, above the Show filter). It reads the pane's cached GET /api/usage, the same reading
   the phone's Context view shows: the server gives each Claude login one bounded cache (beside an
   independent GPT cache), so this page only polls the cache (every 60 s while the page is visible,
   sooner while a refresh is under way); clicking the row asks for a fresh reading (refresh=1, still
   server-bounded and never before a provider's retry time). Each gauge shows the share USED (fills
   and turns green → orange → red) and, in brackets, the time until that window resets; unknown is a
   grey "?", never zero. The phone page never loads this file. */
(() => {
  'use strict';
  const POLL_MS = 60000, PENDING_MS = 5000, RETRY_MS = 20000, TIMEOUT_MS = 12000, STALE_SECONDS = 300;
  // Shown as grey "?" in place while a provider has no reading, so the row keeps its shape.
  const EXPECTED = {claude: ['five-hour', 'weekly', 'Fable weekly'], chatgpt: ['weekly']};
  const PROVIDERS = {claude: 'Claude', chatgpt: 'GPT'};

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const percentText = value => Math.round(value) + '%';
  // "five-hour" -> 5h, "weekly" -> wk, "Fable weekly" -> Fable, anything else keeps its own words.
  function shortLabel(label) {
    const text = String(label || '').trim();
    if (/^five-hour$/i.test(text)) return '5h';
    if (/^weekly$/i.test(text)) return 'wk';
    return text.replace(/\s+weekly$/i, '').replace(/^five-hour\b/i, '5h') || '?';
  }
  function planLabel(plan) { const max = /max_(\d+)x/i.exec(plan || ''); return max ? 'Max ' + max[1] + '×' : String(plan || ''); }
  function ago(seconds) {
    return seconds < 45 ? 'just now' : seconds < 90 ? '1 min ago' : seconds < 3600 ? Math.round(seconds / 60) + ' min ago' :
      seconds < 86400 ? (Math.round(seconds / 360) / 10) + ' h ago' : Math.round(seconds / 86400) + ' d ago';
  }
  function span(seconds) {
    const m = Math.max(0, Math.round(seconds / 60)), d = Math.floor(m / 1440), h = Math.floor(m % 1440 / 60);
    return d ? d + 'd ' + h + 'h' : h ? h + 'h' + (m % 60 ? ' ' + (m % 60) + 'm' : '') : (m % 60) + 'm';
  }
  // The row's bracketed time to a window's reset: "4h 50m" under a day, "5d 3h" beyond (a zero minor unit
  // is left out); minor is the part narrow rows drop. A passed or unknown reset shows nothing, never 0 or less.
  function leftText(at, now) {
    if (!Number.isFinite(at) || at <= now) return null;
    const m = Math.ceil((at - now) / 60), d = Math.floor(m / 1440), h = Math.floor(m % 1440 / 60);
    return d ? {major: d + 'd', minor: h ? h + 'h' : ''} : h ? {major: h + 'h', minor: m % 60 ? m % 60 + 'm' : ''} : {major: m + 'm', minor: ''};
  }
  const epoch = value => typeof value === 'number' ? value : typeof value === 'string' ? Date.parse(value) / 1000 : NaN;
  // "resets 9:00 PM · in 2h 31m" (a weekday or date when it is not today), on the PC's clock.
  function resetText(at, now) {
    if (!Number.isFinite(at)) return 'reset time unknown';
    const date = new Date(at * 1000), today = new Date(now * 1000).toDateString() === date.toDateString();
    const when = date.toLocaleString([], today ? {hour: 'numeric', minute: '2-digit'} : (at - now) / 86400 < 6 ?
      {weekday: 'short', hour: 'numeric', minute: '2-digit'} : {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
    return 'resets ' + when + (at > now ? ' · in ' + span(at - now) : '');
  }

  // @beacon[
  //   id=opc-pane@quotaGauges,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Sidebar quota gauges retain provider age stale/reset-passed unknown and backoff semantics; GPT tooltips name the current native/mapped login and distinguish the legacy UNVERIFIED Codex reading from active-subscription proof. Viewing or refresh never selects.,
  // ]
  function quotaGauges(u, now, fetchError = null) {
    const age = u && finite(u.ageSeconds) ? u.ageSeconds : null;
    const fresh = seconds => seconds === null ? 'not read yet' : 'read ' + ago(seconds);
    const refreshing = u?.refreshing ? ' · updating…' : '';
    const failed = u?.error || (u && fetchError) ? ' · last refresh failed: ' + String(u?.error || fetchError).slice(0, 160) : '';
    const placeholders = (provider, why) => EXPECTED[provider].map(label => ({provider, key: label, label: shortLabel(label), percent: null, left: null,
      state: 'unknown', title: PROVIDERS[provider] + ' · ' + label + '\nunknown: ' + why}));
    if (!u) {
      const why = fetchError ? 'allotments unavailable: ' + fetchError : 'reading the allotments…';
      return [...placeholders('claude', why), ...placeholders('chatgpt', why)];
    }
    const gauge = (provider, w, {plan, readAge, stale, extra = []}) => {
      const label = String(w?.label || 'window'), resets = epoch(w?.resets_at);
      const head = PROVIDERS[provider] + (plan ? ' ' + plan : '') + ' · ' + label + ' window';
      const tail = [fresh(readAge) + refreshing + failed, 'Click the row to read the providers now.'];
      const base = {provider, key: label, label: shortLabel(label), left: null};
      if (!finite(w?.percent)) return {...base, percent: null, state: 'unknown', title: [head, 'unknown: the reading has no figure for this window', ...extra, ...tail].join('\n')};
      const used = Math.max(0, Math.min(100, w.percent));
      if (Number.isFinite(resets) && resets <= now)
        return {...base, percent: null, state: 'unknown', title: [head, 'unknown: this window reset after the reading (' + percentText(used) +
          ' used before the reset); a fresh figure is on its way', ...extra, ...tail].join('\n')};
      const old = readAge !== null && readAge > STALE_SECONDS;
      return {...base, percent: used, left: leftText(resets, now), state: stale || old ? 'stale' : 'known', title: [head,
        percentText(100 - used) + ' left · ' + percentText(used) + ' used', resetText(resets, now), ...extra,
        ...(stale ? ['stale: the current reading is unavailable, this is the last one'] : old ? ['stale: this reading is old'] : []), ...tail].join('\n')};
    };
    const out = [], claude = u.claude, chatgpt = u.chatgpt;
    const claudeNote = claude ? String(claude.note || claude.error || '').slice(0, 200) : '';
    const claudeRetry = claude && finite(claude.retry_at) && claude.retry_at > now ? claude.retry_at - now : null;
    if (!claude) out.push(...placeholders('claude', 'not read yet'));
    else if (!claude.ok && !claude.stale) {
      const why = String(claude.note || claude.error || 'unknown').slice(0, 200);
      out.push(...placeholders('claude', 'Claude allotments unavailable: ' + why +
        (claudeRetry !== null ? ' · next read in ' + span(claudeRetry) : '') + failed));
    } else {
      const extra = [...(claude.stale && claudeNote ? ['stale: last good reading · ' + claudeNote] : claudeNote && !claude.ok ? [claudeNote] : []),
        ...(claudeRetry !== null ? ['rate limited: next read in ' + span(claudeRetry)] : [])];
      for (const w of claude.windows || []) out.push(gauge('claude', w, {plan: planLabel(claude.plan), readAge: age, stale: !!claude.stale,
        extra: [...extra,
          ...(w.allowance_percent_of_weekly != null ? ['up to ' + w.allowance_percent_of_weekly + '% of weekly · ' +
            (finite(w.used_percent_of_weekly) ? percentText(w.used_percent_of_weekly) : '?') + ' of weekly used'] : []),
          ...(claude.extra_usage_enabled ? ['extra usage is on'] : [])]}));
      if (!(claude.windows || []).length) out.push(...placeholders('claude', claudeNote || 'the last reading has no windows'));
    }
    const gptAge = finite(u.chatgptAgeSeconds) ? u.chatgptAgeSeconds : age;
    if (!chatgpt) out.push(...placeholders('chatgpt', 'not read yet'));
    else if (!chatgpt.ok && !chatgpt.stale) out.push(...placeholders('chatgpt', 'ChatGPT allotments unavailable: ' + String(chatgpt.error || 'unknown').slice(0, 200) + failed));
    else {
      const models = Object.entries(chatgpt.models || {}).map(([name, m]) => name + ': ' + (chatgpt.stale ? 'availability unknown (stale)' :
        m?.available === true ? 'available now' : m?.available === false ? 'not available' +
          (m.available_at ? ' until ' + resetText(epoch(m.available_at), now).replace(/^resets /, '') : '') : 'availability unknown'));
      const binding = chatgpt.account;
      const provenance = binding ? binding.opencode_credential_id ? [
        'Native login: ' + (binding.active_credential_label || binding.opencode_credential_id) +
        (binding.label ? ' · quota label: ' + binding.label : ''), binding.binding || 'operator-configured quota binding'] :
        ['Default Codex quota · UNVERIFIED against the active OpenCode subscription'] : [];
      const extra = [...provenance, ...(chatgpt.limit_reached ? ['limit reached'] : []), ...models];
      for (const w of chatgpt.windows || []) out.push(gauge('chatgpt', w, {plan: chatgpt.plan, readAge: gptAge, stale: !!chatgpt.stale, extra}));
    }
    return out;
  }

  function el(doc, tag, cls, text) { const node = doc.createElement(tag); if (cls) node.className = cls; if (text) node.textContent = text; return node; }
  function paintGauge(node, g) {
    node.dataset.state = g.state; node.title = g.title;
    if (g.percent === null) node.style.removeProperty('--quota-fill'); else node.style.setProperty('--quota-fill', g.percent.toFixed(1));
    node.querySelector('.quota-label').textContent = g.label;
    node.querySelector('.quota-pct').textContent = g.percent === null ? '?' : percentText(g.percent);
    const left = node.querySelector('.quota-left'); left.hidden = !g.left;
    left.firstChild.textContent = g.left ? g.left.major : ''; left.lastChild.textContent = g.left?.minor ? ' ' + g.left.minor : '';
    node.setAttribute('aria-label', g.title.split('\n').slice(0, 2).join(': '));
  }
  // Updated in place while the same windows are shown (a hovered tooltip survives a poll);
  // rebuilt only when the set of windows changes.
  function paint(bar, gauges) {
    const doc = bar.ownerDocument, key = gauges.map(g => g.provider + ':' + g.key).join('|');
    if (bar.dataset.layout !== key) {
      const groups = [];
      for (const provider of Object.keys(PROVIDERS)) {
        const own = gauges.filter(g => g.provider === provider); if (!own.length) continue;
        const group = el(doc, 'span', 'quota-group'); group.dataset.provider = provider;
        group.append(el(doc, 'span', 'quota-provider', PROVIDERS[provider]));
        for (const g of own) {
          const node = el(doc, 'span', 'quota-gauge'); node.setAttribute('role', 'img'); node.dataset.key = g.key;
          const left = el(doc, 'span', 'quota-left'); left.append(el(doc, 'span', 'quota-left-major'), el(doc, 'span', 'quota-left-minor'));
          node.append(el(doc, 'span', 'quota-pie'), el(doc, 'span', 'quota-label'), el(doc, 'span', 'quota-pct'), left);
          group.append(node);
        }
        groups.push(group);
      }
      bar.replaceChildren(...groups); bar.dataset.layout = key;
    }
    const nodes = bar.querySelectorAll('.quota-gauge');
    gauges.forEach((g, i) => paintGauge(nodes[i], g));
  }

  // @beacon[
  //   id=opc-pane@createQuotaBar,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Desktop sidebar quota row (sessions.html #quota-bar, never the phone): polls the cached GET /api/usage every 60 s while visible (5 s while the server is refreshing, 20 s after a failure), a click or Enter asks refresh=1 (server-bounded and waits out a provider backoff), repaints every 30 s so freshness, the bracketed time left and passed resets stay true.,
  // ]
  function createQuotaBar(bar, {fetch = globalThis.fetch, doc = bar.ownerDocument} = {}) {
    const state = {usage: null, receivedAt: 0, error: null, busy: false, timer: 0, due: 0};
    const nowSeconds = () => state.usage ? state.usage.now + (Date.now() - state.receivedAt) / 1000 : Date.now() / 1000;
    function render() {
      const u = state.usage, drift = (Date.now() - state.receivedAt) / 1000;
      const aged = u && {...u, ageSeconds: finite(u.ageSeconds) ? u.ageSeconds + drift : null,
        chatgptAgeSeconds: finite(u.chatgptAgeSeconds) ? u.chatgptAgeSeconds + drift : null};
      paint(bar, quotaGauges(aged, nowSeconds(), state.error));
      bar.dataset.busy = String(state.busy || !!u?.refreshing);
    }
    function schedule(ms) { clearTimeout(state.timer); state.due = Date.now() + ms; state.timer = setTimeout(() => void read(false), ms); }
    async function read(force) {
      if (state.busy) return;
      if (!force && doc.hidden) { schedule(POLL_MS); state.due = 0; return; } // nobody is looking: no reads; read the moment the page shows
      state.busy = true; render();
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
      try {
        const response = await fetch('/api/usage' + (force ? '?refresh=1' : ''), {cache: 'no-store', signal: controller.signal});
        const data = JSON.parse(await response.text());
        if (!response.ok || data?.schemaVersion !== 1 || data.backend !== 'usage') throw new Error(data?.error || 'HTTP ' + response.status);
        state.usage = data; state.receivedAt = Date.now(); state.error = null;
      } catch (e) { state.error = e.name === 'AbortError' ? 'the pane did not answer' : String(e.message || e); }
      finally { clearTimeout(timer); state.busy = false; }
      schedule(state.usage?.refreshing ? PENDING_MS : state.error ? RETRY_MS : POLL_MS);
      render();
    }
    bar.addEventListener('click', () => void read(true));
    bar.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); void read(true); } });
    doc.addEventListener('visibilitychange', () => { if (!doc.hidden && Date.now() >= state.due) void read(false); });
    setInterval(render, 30000);
    render(); void read(false);
    return {read, render};
  }

  globalThis.opcQuotaBar = Object.freeze({quotaGauges, shortLabel, leftText});
  if (typeof document !== 'undefined' && document.getElementById('quota-bar')) createQuotaBar(document.getElementById('quota-bar'));
})();
