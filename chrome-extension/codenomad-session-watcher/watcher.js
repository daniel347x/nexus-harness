// Read-only CodeNomad 0.20.1 adapter. No clicks, prompt writes or credentials.
(function () {
  'use strict';
  const PANE = 'http://127.0.0.1:9911/api/active-session';
  const watcherId = crypto.randomUUID();
  let last = null, timer = null, pending = null, sending = false, sequence = 0;
  const visible = (el) => !!el && el.getClientRects().length > 0;

  // @beacon[
  //   id=opc-pane@currentThinkingReport,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Reads only the visible active session pane and its sibling model/thinking controls; unknown is not Default. Pinned to CodeNomad 0.20.1 English labels.,
  // ]
  function currentThinkingReport() {
    const streams = [...document.querySelectorAll('.message-stream-container[data-stream-active="true"][data-session-id]')].filter(visible);
    const rows = [...document.querySelectorAll('.session-item-active[data-session-id]')].filter(visible);
    const candidates = streams.length ? streams : rows;
    if (candidates.length !== 1) return { sessionId: null, title: null, thinking: null };
    const el = candidates[0], sessionId = el.getAttribute('data-session-id');
    const row = document.querySelector('[data-session-id="' + CSS.escape(sessionId) + '"] .session-item-title');
    const report = { sessionId, title: row ? row.textContent.trim() : null, thinking: null };
    // Never associate a global selector with a sidebar row or a hidden workspace.
    const pane = el.closest('.session-cache-pane[data-session-active="true"][aria-hidden="false"]');
    if (!visible(pane) || pane.getAttribute('data-session-id') !== sessionId ||
        pane.getAttribute('data-instance-id') !== el.getAttribute('data-instance-id')) return report;
    const controls = [...pane.querySelectorAll('.prompt-context-controls')].filter(visible);
    if (controls.length !== 1) return report;
    const models = controls[0].querySelectorAll('[data-model-selector-control]');
    const thinkers = controls[0].querySelectorAll('[data-thinking-selector-control]');
    if (models.length !== 1 || thinkers.length !== 1 || !visible(models[0]) || !visible(thinkers[0])) return report;
    // The sr-only inputs have no useful value/visible rect. aria-label is derived
    // from currentModel/currentValue, not a search box, assistant label or popup.
    const modelLabel = models[0].querySelector('[data-model-selector]')?.getAttribute('aria-label') || '';
    const thinkingLabel = thinkers[0].querySelector('[data-thinking-selector]')?.getAttribute('aria-label') || '';
    const model = /^Model: .+, provider: .+, ID: ([^\s/]+)\/(\S+)$/.exec(modelLabel);
    const choice = /^Thinking: (\S+)$/.exec(thinkingLabel);
    if (model && choice) report.thinking = {
      providerID: model[1], id: model[2], variant: choice[1] === 'Default' ? null : choice[1],
    };
    return report;
  }

  async function drain() {
    if (sending) return;
    sending = true;
    try {
      while (pending) {
        const body = pending; pending = null;
        try {
          await fetch(PANE, { method: 'POST', headers: { 'Content-Type': 'text/plain' }, body: JSON.stringify(body), signal: AbortSignal.timeout(5000) });
        } catch (e) { /* offline: next change or heartbeat retries */ }
      }
    } finally { sending = false; }
  }

  function report(force) {
    const cur = currentThinkingReport(), key = JSON.stringify(cur);
    if (!force && last === key) return;
    last = key;
    // Coalesce pending reports and serialize HTTP writes. The server also rejects
    // older observations, including across reloads/tabs; receipt time alone is unsafe.
    pending = { ...cur, watcherId, sequence: ++sequence, observedAt: Date.now() };
    void drain();
  }

  new MutationObserver(() => { if (!timer) timer = setTimeout(() => { timer = null; report(false); }, 120); })
    .observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true,
      attributeFilter: ['data-stream-active', 'data-session-id', 'data-session-active', 'data-instance-id', 'aria-hidden', 'aria-label', 'class', 'style'] });
  setInterval(() => report(true), 15000);
  report(true);
})();
