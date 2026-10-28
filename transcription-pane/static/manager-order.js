/* The order of Manager conversations wherever the pane lists them together: the desktop sidebar's Manager Sessions
   (also the phone Programs pane, the same sessions.js) and the phone /m chip strip. Dan finds a Manager by its
   number, so the number orders the list, whatever the backend (Claude Code or OpenCode), model or liveness (the
   context gauge shows which are live; it never moves a row):
     1. by Manager-Session number, highest first;
     2. a primary first, then its sub-sessions right after it, a first: 218, 218-a, 218-b, 218-c (on /m the newest
        primary is always the leftmost chip, its sub-sessions to its right);
     3. a name with no Manager-Session number at the bottom; ties (no number, or the same name on two backends)
        newest created first.
   The name only orders; it never makes a session a Manager (the pages decide membership by role).
   The same names also give ↳ New sub-session its next name (nextSub) and a launch's first message (opening), and
   the /m strip its one-week cutoff (recent). */
(() => {
  'use strict';
  // Real names: Manager-Session-218, Manager-Session-217-d, and the odd ones (xxx-Manager-Session-184,
  // "Manager-Session-168 Anchor Dock instructions", "Manager Session 157-a Setup"). Any number of digits; a
  // sub-session is one or two letters right after the number, never the start of a longer word.
  const NAME = /Manager[-_ ]Session[-_ ](\d+)(?:-([a-z]{1,2})(?![a-z]))?/i;
  // The first of the names given (e.g. a Claude row's sessionLabel, then its title) that carries a number:
  // {index, sub} (sub '' for a primary), else null.
  function parse(...names) {
    for (const name of names) {
      const match = typeof name === 'string' ? NAME.exec(name) : null;
      if (match) return {index: Number(match[1]), sub: match[2] ? match[2].toLowerCase() : ''};
    }
    return null;
  }
  // a, b: {names: [...], created (any one unit for both), id (a stable last tie-break)}; any open flag is ignored.
  // @beacon[
  //   id=opc-pane@managerOrder,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The one Manager-list comparator (desktop Manager Sessions, phone Programs, /m chips): Manager-Session number highest first, each primary before its sub-sessions (a first), unnumbered names last; ties newest created. Liveness (open/live gauge), backend and model never count, so the newest primary is always first.,
  // ]
  function compare(a, b) {
    const x = parse(...(a.names || [])), y = parse(...(b.names || []));
    if (x && !y) return -1;
    if (y && !x) return 1;
    if (x && y && x.index !== y.index) return y.index - x.index;
    if (x && y && x.sub !== y.sub) {
      if (!x.sub) return -1;
      if (!y.sub) return 1;
      return x.sub.length - y.sub.length || (x.sub > y.sub ? 1 : -1);
    }
    const created = (Number(b.created) || 0) - (Number(a.created) || 0);
    return created || String(a.id ?? '').localeCompare(String(b.id ?? ''));
  }
  // rows: the names each listed Manager is known by (compare's names; a bare string is one name).
  // @beacon[
  //   id=opc-pane@managerNextSub,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=↳ New sub-session's name: the newest primary is the highest Manager-Session number listed; its letter follows the highest letter that number already has (a when none; a skipped letter stays skipped). Null with no numbered Manager or after z (oc_session.py takes one letter).,
  // ]
  function nextSub(rows) {
    const parsed = (rows || []).map(names => parse(...(Array.isArray(names) ? names : [names]))).filter(Boolean);
    if (!parsed.length) return null;
    const index = Math.max(...parsed.map(p => p.index)), subs = parsed.filter(p => p.index === index && p.sub).map(p => p.sub);
    if (subs.some(sub => sub.length > 1 || sub === 'z')) return null;
    const last = subs.sort().pop(), letter = last ? String.fromCharCode(last.charCodeAt(0) + 1) : 'a';
    const number = String(index).padStart(3, '0');  // oc_session.py's own NNN
    return {index, letter, id: number + '-' + letter, parent: 'Manager-Session-' + number, name: 'Manager-Session-' + number + '-' + letter};
  }
  // The /m chip strip's cutoff, in days of last activity (Dan: "no more than a week in the past").
  const STRIP_DAYS = 7;
  // rows: {names, lastActive (seconds; missing = unknown, never taken for old), open, keep}; returns the rows the
  // strip shows, in their given order.
  // @beacon[
  //   id=opc-pane@managerStripRecent,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The /m strip's one-week cutoff (STRIP_DAYS of last activity, not creation): an older Manager leaves the strip unless it is open, kept (the selected one), or carries the newest Manager-Session number (the newest primary and its sub-sessions). A closed recent one stays, so selecting it and pressing Send still reopens it. Order is untouched.,
  // ]
  function recent(rows, now = Date.now() / 1000, days = STRIP_DAYS) {
    const list = rows || [], parsed = list.map(row => parse(...(row.names || [])));
    const newest = Math.max(-1, ...parsed.filter(Boolean).map(p => p.index)), since = now - days * 86400;
    return list.filter((row, i) => row.open || row.keep || parsed[i]?.index === newest ||
      !(Number.isFinite(row.lastActive) && row.lastActive > 0) || row.lastActive >= since);
  }
  // The short index a chip shows beside its gauge: the first numbered name's Manager-Session number plus any
  // sub-session letter ('219', '219-a'), else null.
  function index(...names) {
    const p = parse(...names);
    return p ? String(p.index) + (p.sub ? '-' + p.sub : '') : null;
  }
  // A launch's first message: Dan's saved template (GET /api/claude/first-messages) with {N} (the name's
  // Manager-Session number as written), {letter} (its sub-session letter, '' for a primary) and {name} filled in; a
  // name without a number fills {N} with the whole name.
  function opening(template, name) {
    const text = String(name ?? ''), match = NAME.exec(text);
    const number = match ? match[1] : text, letter = match?.[2] ? match[2].toLowerCase() : '';
    return String(template ?? '').replace(/\{N\}/g, () => number).replace(/\{letter\}/g, () => letter).replace(/\{name\}/g, () => text);
  }
  globalThis.opcManagerOrder = Object.freeze({parse, compare, nextSub, opening, recent, index, STRIP_DAYS});
})();
