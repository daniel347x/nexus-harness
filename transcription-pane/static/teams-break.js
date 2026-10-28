/* Teams message break (Ctrl+M), ported from the TypingMind widget's "Insert Teams Message Break".
 *
 * Dan pastes or dictates Teams messages into the box and marks each one, at the cursor, with its speaker
 * and date, for the Manager to log:
 *
 *   ===MESSAGE_BREAK===
 *   Speaker: ‹name›
 *   Date: ‹date›
 *   Comment: ‹one line›        (only with a comment; several lines: "Comment:", then each line indented 4)
 *   ===END_BREAK===
 *
 * No newline is added before or after it (Dan handles spacing); the cursor lands just after it. Ctrl+M (in
 * the box) opens the popover with the speaker already chosen: of two active speakers, the one NOT used last,
 * so a two-person thread alternates with Ctrl+M, Enter. The date persists (today when unset); the comment
 * always starts empty. Settings keep the widget's localStorage key names.
 *
 * Self-contained: a page marks its box with data-teams-break and any opener with
 * data-teams-break-for="‹box id›". The insertion is the box's ordinary input (undoable where the browser
 * allows), so the page's own draft handling (revision, save) runs as for typing. While the popover is open
 * no key reaches the page's own shortcuts (Send, Refine).
 */
'use strict';

const opcTeamsBreak = (() => {
  const SLOTS = 10;
  const KEYS = {speakers:'teams_message_speakers', active:'teams_message_active_speakers', date:'teams_message_date',
    last:'teams_message_last_speaker_index', known:'teams_message_known_speakers'};
  const ADD = ' add';  // the "＋ Add new…" option of a slot's dropdown (names are trimmed, so never this)
  const ui = {};        // the one dialog and its parts, built on first open
  // chosen: position in `active`; chosenSlot: its slot, so a re-render keeps the same speaker.
  let target = null, cursor = 0, settings = null, active = [], chosen = -1, chosenSlot = -1;

  // ---------------------------------------------------------------- the delimiter and the speaker choice (pure)
  function delimiter(speaker, date, comment = '') {
    let text = '===MESSAGE_BREAK===\nSpeaker: ' + speaker + '\nDate: ' + date;
    if (comment) {
      const lines = comment.split('\n');
      text += lines.length > 1 ? '\nComment:\n' + lines.map(line => '    ' + line).join('\n') : '\nComment: ' + comment;
    }
    return text + '\n===END_BREAK===';
  }
  // The checked slots with a name, in slot order; a name in two slots counts once (its first slot).
  function activeSpeakers(slots, checked) {
    const out = [];
    for (let i = 0; i < SLOTS; i++) {
      const name = (slots[i] || '').trim();
      if (checked[i] && name && !out.some(s => s.name === name)) out.push({index: i, name});
    }
    return out;
  }
  // Which active speaker the popover preselects (the widget's rule): two → the one not used last;
  // three or more → the first whose name contains "dan", else the first; one → it; none → -1.
  function pickSpeaker(list, lastIndex) {
    if (!list.length) return -1;
    if (list.length === 2) { const at = list.findIndex(s => s.index === lastIndex); return at === -1 ? 0 : (at + 1) % 2; }
    if (list.length >= 3) { const at = list.findIndex(s => s.name.toLowerCase().includes('dan')); return at === -1 ? 0 : at; }
    return 0;
  }
  function today() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  // ---------------------------------------------------------------- settings (the widget's key names)
  function readJSON(key) {
    try { const value = JSON.parse(localStorage.getItem(key) || 'null'); return Array.isArray(value) ? value : []; }
    catch (_) { return []; }
  }
  function load() {
    const slots = readJSON(KEYS.speakers).slice(0, SLOTS).map(v => typeof v === 'string' ? v.trim() : '');
    const checked = readJSON(KEYS.active).slice(0, SLOTS).map(v => v === true);
    while (slots.length < SLOTS) slots.push('');
    while (checked.length < SLOTS) checked.push(false);
    const known = readJSON(KEYS.known).filter(v => typeof v === 'string' && v.trim()).map(v => v.trim());
    for (const name of slots) if (name && !known.includes(name)) known.push(name);  // a slot always shows its name
    let date = null, last = -1;
    try { date = localStorage.getItem(KEYS.date); last = parseInt(localStorage.getItem(KEYS.last) || '-1', 10); } catch (_) {}
    return {slots, checked, known, date: date || today(), last: Number.isInteger(last) ? last : -1};
  }
  function store(pairs) {
    try { for (const [key, value] of pairs) localStorage.setItem(key, value); return true; }
    catch (e) { note('Speaker settings not saved (browser storage unavailable): ' + e.message, true); return false; }
  }
  function saveSettings() {
    store([[KEYS.speakers, JSON.stringify(settings.slots)], [KEYS.active, JSON.stringify(settings.checked)],
      [KEYS.known, JSON.stringify(settings.known)]]);
  }

  // ---------------------------------------------------------------- the popover
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (key === 'class') node.className = value; else if (key === 'text') node.textContent = value;
      else if (key in node && typeof value !== 'string') node[key] = value; else node.setAttribute(key, value);
    }
    node.append(...children);
    return node;
  }
  function build() {
    ui.auto = el('span', {class: 'tb-auto', 'aria-live': 'polite'});
    ui.radios = el('div', {class: 'tb-radios', role: 'radiogroup', 'aria-label': 'Speaker of this message'});
    ui.date = el('input', {id: 'teams-break-date', type: 'text', autocomplete: 'off', spellcheck: 'false', placeholder: 'e.g., 2025-11-07'});
    ui.comment = el('textarea', {id: 'teams-break-comment', rows: '2', placeholder: 'Optional note about this message (always starts empty)'});
    ui.note = el('div', {class: 'tb-note', role: 'status'});
    ui.insert = el('button', {type: 'button', class: 'tb-insert', text: 'Insert break'});
    ui.cancel = el('button', {type: 'button', class: 'tb-cancel', text: 'Cancel'});
    ui.slots = el('div', {class: 'tb-slots'});
    ui.known = el('div', {class: 'tb-known'});
    ui.speakers = el('details', {class: 'tb-speakers'}, el('summary', {text: 'Speakers · tick the ones in this thread'}), ui.slots, ui.known);
    ui.dialog = el('dialog', {class: 'teams-break', 'aria-labelledby': 'teams-break-title'},
      el('div', {class: 'tb-head'}, el('strong', {id: 'teams-break-title', text: 'Insert Teams message break'}), ui.auto),
      ui.radios,
      el('label', {class: 'tb-field', for: 'teams-break-date', text: 'Date'}), ui.date,
      el('label', {class: 'tb-field', for: 'teams-break-comment', text: 'Comment (optional) · Enter = new line, Ctrl+Enter = insert'}), ui.comment,
      ui.note,
      el('div', {class: 'tb-buttons'}, ui.insert, ui.cancel),
      ui.speakers);
    ui.insert.addEventListener('click', insert);
    ui.cancel.addEventListener('click', () => ui.dialog.close());
    ui.date.addEventListener('input', () => store([[KEYS.date, ui.date.value]]));
    ui.dialog.addEventListener('keydown', onKey);
    ui.dialog.addEventListener('close', () => { if (target) target.focus(); target = null; });
    document.body.append(ui.dialog);
  }
  function note(text, error = false) {
    if (!ui.note) return;
    ui.note.textContent = text; ui.note.dataset.error = String(error);
  }
  function renderSlots() {
    ui.slots.replaceChildren();
    for (let i = 0; i < SLOTS; i++) {
      const check = el('input', {type: 'checkbox', checked: settings.checked[i], 'aria-label': 'Speaker slot ' + (i + 1) + ' active'});
      const pick = el('select', {'aria-label': 'Speaker slot ' + (i + 1)}, el('option', {value: '', text: '—'}),
        ...settings.known.map(name => el('option', {value: name, text: name})), el('option', {value: ADD, text: '＋ Add new…'}));
      pick.value = settings.slots[i];
      check.addEventListener('change', () => { settings.checked[i] = check.checked; saveSettings(); renderRadios(); });
      pick.addEventListener('change', () => {
        if (pick.value === ADD) {
          const name = (prompt('New speaker name:') || '').trim();
          if (!name) { pick.value = settings.slots[i]; return; }
          if (!settings.known.includes(name)) settings.known.push(name);
          settings.slots[i] = name;
        } else settings.slots[i] = pick.value;
        saveSettings(); renderSlots(); renderKnown(); renderRadios();
        ui.slots.querySelectorAll('select')[i].focus();
      });
      ui.slots.append(el('div', {class: 'tb-slot'}, check, pick));
    }
  }
  function renderKnown() {
    ui.known.replaceChildren(...settings.known.map(name => {
      const forget = el('button', {type: 'button', class: 'tb-forget', text: '×', title: 'Delete “' + name + '” from saved names', 'aria-label': 'Delete ' + name});
      forget.addEventListener('click', () => {
        if (!confirm('Delete "' + name + '" from saved names?')) return;
        settings.known = settings.known.filter(n => n !== name);
        settings.slots = settings.slots.map(n => n === name ? '' : n);
        saveSettings(); renderSlots(); renderKnown(); renderRadios();
      });
      return el('span', {class: 'tb-chip'}, name, forget);
    }));
  }
  function renderRadios(repick = false) {
    active = activeSpeakers(settings.slots, settings.checked);
    const kept = active.findIndex(s => s.index === chosenSlot);
    const start = repick || kept === -1 ? pickSpeaker(active, settings.last) : kept;
    ui.radios.replaceChildren(...active.map((speaker, at) => {
      const radio = el('button', {type: 'button', class: 'tb-radio', role: 'radio'},
        ...(at < 9 ? [el('span', {class: 'tb-key', text: String(at + 1), 'aria-hidden': 'true'})] : []), speaker.name);
      radio.addEventListener('click', () => choose(at));
      return radio;
    }));
    if (!active.length) ui.radios.append(el('span', {class: 'tb-empty', text: 'No active speaker: tick one under Speakers below.'}));
    choose(start, false);
    ui.auto.textContent = active.length === 2 ? 'Auto-toggling between 2 speakers'
      : active.length >= 3 && active[chosen] ? 'Auto-selected: ' + active[chosen].name : '';
    ui.insert.disabled = !active.length;
  }
  function choose(at, focus = true) {
    chosen = active[at] ? at : -1; chosenSlot = chosen === -1 ? -1 : active[chosen].index;
    [...ui.radios.querySelectorAll('.tb-radio')].forEach((radio, i) => {
      radio.setAttribute('aria-checked', String(i === chosen)); radio.tabIndex = i === chosen ? 0 : -1;
      if (focus && i === chosen) radio.focus();
    });
  }
  // Where the widget had it: top-centre over the dictation box, kept inside the window.
  function place(box) {
    const r = box.getBoundingClientRect(), d = ui.dialog.getBoundingClientRect();
    const left = Math.max(8, Math.min(r.left + r.width / 2 - d.width / 2, innerWidth - d.width - 8));
    const top = Math.max(8, Math.min(r.top + 8, innerHeight - d.height - 8));
    ui.dialog.style.left = Math.round(left) + 'px'; ui.dialog.style.top = Math.round(top) + 'px';
  }

  // @beacon[
  //   id=opc-pane@teamsBreakOpen,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Ctrl+M / the Teams break opener: saves the box's cursor, reloads the widget-compatible speaker settings, preselects the speaker (two active: the one not used last), clears the comment and opens the modal popover focused on that speaker.,
  // ]
  function open(box) {
    if (!box || ui.dialog?.open) return;
    if (!ui.dialog) build();
    target = box; cursor = box.selectionStart ?? box.value.length;
    settings = load();
    ui.date.value = settings.date; ui.comment.value = ''; note('');
    renderSlots(); renderKnown(); renderRadios(true);
    ui.speakers.open = !active.length;
    ui.dialog.showModal(); place(box);
    if (active[chosen]) choose(chosen); else ui.slots.querySelector('select')?.focus();
  }

  // @beacon[
  //   id=opc-pane@teamsBreakInsert,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Inserts the exact widget delimiter (===MESSAGE_BREAK=== / Speaker / Date / optional Comment / ===END_BREAK===, no surrounding newlines) at the saved cursor as ordinary undoable input, cursor after it; remembers the speaker for the next auto-toggle.,
  // ]
  function insert() {
    const speaker = active[chosen];
    if (!speaker) return note('Choose a speaker first (tick one under Speakers).', true);
    const box = target, text = delimiter(speaker.name, ui.date.value.trim(), ui.comment.value.trim());
    settings.last = speaker.index;
    store([[KEYS.last, String(speaker.index)], [KEYS.date, ui.date.value]]);
    ui.dialog.close();
    const at = Math.min(cursor, box.value.length), expected = box.value.slice(0, at) + text + box.value.slice(at);
    box.focus(); box.setSelectionRange(at, at);
    let typed = false;
    try { typed = document.execCommand('insertText', false, text); } catch (_) {}
    if (!typed || box.value !== expected) { box.value = expected; box.dispatchEvent(new Event('input', {bubbles: true})); }
    box.setSelectionRange(at + text.length, at + text.length);
  }

  // Enter inserts (in the comment: Ctrl+Enter; a plain button keeps its own Enter); 1–9 pick a speaker,
  // arrows move between speakers, Escape cancels. Nothing reaches the page's own shortcuts underneath.
  function onKey(e) {
    e.stopPropagation();
    const t = e.target, typing = t.matches('textarea, select, input:not([type=checkbox])'), radio = t.matches('.tb-radio');
    if (e.isComposing) return;
    if (e.key === 'Enter' && !e.altKey && (e.ctrlKey || !(t.matches('textarea') || (t.matches('button, summary') && !radio)))) {
      e.preventDefault(); insert(); return;
    }
    if (e.ctrlKey && (e.key === 'm' || e.key === 'M')) { e.preventDefault(); return; }
    if (!typing && !e.ctrlKey && !e.altKey && !e.metaKey && /^[1-9]$/.test(e.key) && active[Number(e.key) - 1]) {
      e.preventDefault(); choose(Number(e.key) - 1); return;
    }
    if (radio && ['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown'].includes(e.key) && active.length) {
      e.preventDefault();
      const step = e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 1;
      choose((Math.max(chosen, 0) + step + active.length) % active.length);
    }
  }

  function attach(box) {
    box.addEventListener('keydown', e => {
      if (e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey && (e.key === 'm' || e.key === 'M')) { e.preventDefault(); open(box); }
    });
  }
  if (typeof document !== 'undefined') {
    for (const box of document.querySelectorAll('textarea[data-teams-break]')) attach(box);
    for (const opener of document.querySelectorAll('[data-teams-break-for]'))
      opener.addEventListener('click', () => open(document.getElementById(opener.dataset.teamsBreakFor)));
  }
  return {open, attach, delimiter, activeSpeakers, pickSpeaker, KEYS};
})();
