/* Shared Refine-context View body, drawn the same way in the desktop dialog and the phone sheet.
   Context shows what Refine sends as context: the chat history, then the unselected draft before and
   after the selection (each its own read-only block of the request; usually empty, then one line).
   Full prompt shows the system prompt and the user message exactly as opcRefineCore.buildRequest
   assembled them through the page's own Refine assembly. The page reads the context, builds the
   request and opens/closes the view; this file only draws. */
(() => {
  'use strict';
  const encoder = new TextEncoder();
  function size(text) {
    const n = encoder.encode(text).length;
    return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(2) + ' MB';
  }
  function node(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  }
  function caption(title, detail) {
    const cap = node('div', 'rv-cap');
    cap.append(node('strong', null, title));
    if (detail) cap.append(node('span', 'rv-detail', detail));
    return cap;
  }
  function textBox(text, note) {
    const pre = node('pre', note ? 'rv-text rv-note' : 'rv-text', text);
    pre.tabIndex = 0;
    return pre;
  }
  function draftPane(cls, title, text) {
    text = text && text.trim() ? text : ''; // buildRequest's rule: a whitespace-only side is not sent
    const pane = node('section', 'rv-pane rv-draft ' + cls + (text ? '' : ' rv-empty'));
    pane.append(caption(title, text ? size(text) : 'none'));
    if (text) pane.append(textBox(text));
    return pane;
  }
  const toEnd = (el) => { if (el) el.scrollTop = el.scrollHeight || 0; };

  // Each mode opens on its end: the newest chat history (and the draft just before the selection),
  // or the prompt's final lines. The draft after the selection opens on its start, next to it.
  function showMode(host, mode) {
    host.dataset.mode = mode;
    for (const b of host.querySelectorAll('.rv-switch button')) b.setAttribute('aria-checked', String(b.dataset.mode === mode));
    const context = host.querySelector('.rv-context'), prompt = host.querySelector('.rv-prompt');
    context.hidden = mode !== 'context';
    prompt.hidden = mode !== 'prompt';
    if (mode === 'context') {
      toEnd(host.querySelector('.rv-history .rv-text'));
      toEnd(host.querySelector('.rv-before .rv-text'));
    } else toEnd(prompt);
  }

  // view: { mode: 'context'|'prompt', history: string|null (null = not read; historyNote says why),
  //   historyDetail, usingSelection, draftBefore, draftAfter, request: {system, user}|null
  //   (null: promptNote says why), promptDetail }. options: { historyId, onMode(mode) }.
  // Call it with the host visible, so each scroller can open on its end.
  // @beacon[
  //   id=opc-pane@renderRefineView,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Draws the shared Refine-context View (desktop dialog and phone sheet): a Context | Full prompt switch; chat history plus draft-before/draft-after panes, or the system prompt and user message of the page's real Refine request; opens scrolled to the end.,
  // ]
  function render(host, view, options = {}) {
    const mode = view.mode === 'prompt' ? 'prompt' : 'context';
    host.classList.add('rv');
    host.replaceChildren();

    const toggle = node('div', 'rv-switch');
    toggle.setAttribute('role', 'radiogroup');
    toggle.setAttribute('aria-label', 'What to show');
    for (const [m, label] of [['context', 'Context'], ['prompt', 'Full prompt']]) {
      const b = node('button', null, label);
      b.type = 'button'; b.dataset.mode = m; b.setAttribute('role', 'radio');
      b.addEventListener('click', () => { showMode(host, m); if (options.onMode) options.onMode(m); });
      toggle.append(b);
    }

    const context = node('div', 'rv-context');
    const history = node('section', 'rv-pane rv-history');
    const read = typeof view.history === 'string', text = read && view.history.trim() ? view.history : '';
    history.append(caption('Chat history', [read ? (text ? size(text) : 'empty') : '', view.historyDetail || '']
      .filter(Boolean).join(' · ')));
    const pre = text ? textBox(text) :
      textBox(read ? 'No chat history: the conversation has no turns to send yet.' : view.historyNote || '', true);
    if (options.historyId) pre.id = options.historyId;
    history.append(pre);
    context.append(history);
    if (view.usingSelection) {
      context.append(draftPane('rv-before', 'Draft before the selection', view.draftBefore || ''),
        draftPane('rv-after', 'Draft after the selection', view.draftAfter || ''));
    } else context.append(node('div', 'rv-cap rv-whole', 'No selection: Refine cleans the whole box, so no draft goes before or after it.'));

    const prompt = node('div', 'rv-prompt');
    if (view.request) {
      if (view.promptDetail) prompt.append(node('div', 'rv-cap rv-whole', view.promptDetail));
      prompt.append(caption('System prompt', size(view.request.system)), textBox(view.request.system),
        caption('User message', size(view.request.user)), textBox(view.request.user));
    } else prompt.append(textBox(view.promptNote || '', true));

    host.append(toggle, context, prompt);
    showMode(host, mode);
  }

  globalThis.opcRefineView = Object.freeze({ render, size });
})();
