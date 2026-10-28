// A stand-in for the pane server's Recent roots (view_roots.py) in the Node and browser fixtures: the same changes
// (visit, open, save, note, remove, limit), cards, order and replies, kept in memory (test_view_roots.py checks the real
// store). handle(method, body) answers {status, body} as /api/view/roots would; log keeps every read and change.
(function (global) {
  function viewRootsFake({clock = null, metadata = null} = {}) {
    let tick = 1000;
    const now = clock || (() => ++tick);
    const db = {limit: 30, roots: []}, log = [];
    const copy = v => JSON.parse(JSON.stringify(v));
    const used = r => Math.max(r.visited || 0, r.saved || 0);
    const brief = e => e ? {handle: e.handle, how: e.how, name: e.name} : null;
    const card = r => ({key: r.key, root: copy(r.entries[0]), count: r.entries.length, cursor: r.cursor, at: r.cursor ? brief(r.entries[r.cursor]) : null,
      last: r.entries.length > 1 ? brief(r.entries.at(-1)) : null, note: r.note, device: r.device || null, created: r.created, visited: r.visited, saved: r.saved});
    function keep() {
      const ordered = [...db.roots].sort((a, b) => used(b) - used(a));
      db.roots = ordered.slice(0, db.limit).concat(ordered.slice(db.limit).filter(r => r.note).slice(0, 100));
    }
    const list = () => ({schemaVersion: 1, limit: db.limit, limits: [10, 20, 30, 50, 100], notedMax: 100, roots: [...db.roots].sort((a, b) => used(b) - used(a)).map(card)});
    function handle(method, body) {
      log.push(method === 'GET' ? {op: 'read'} : copy(body));
      if (method === 'GET') return {status: 200, body: list()};
      if (body.op === 'describe') {
        for (const r of db.roots.filter(r => body.keys.includes(r.key))) {
          if (r.entries[0].metadataVersion === 1) continue;
          const fields = metadata?.(copy(r.entries[0]), r.key);
          if (fields) Object.assign(r.entries[0], fields, {metadataVersion: 1});
        }
        return {status: 200, body: list()};
      }
      const keepMetadata = (before, after) => {
        if (before?.metadataVersion === 1 && after.metadataVersion !== 1)
          for (const k of ['name', 'description', 'metadataVersion']) if (k in before) after[k] = before[k];
        return after;
      };
      const t = now(), found = db.roots.find(r => r.key === body.key);
      let opened = null;
      if (body.op === 'visit') {
        opened = found || {key: body.key, entries: [], cursor: 0, note: '', created: t};
        if (found) opened.entries[0] = keepMetadata(found.entries[0], copy(body.root)); else { opened.entries = [copy(body.root)]; db.roots.push(opened); }
        opened.visited = t; opened.device = body.device || opened.device;
      } else if (body.op === 'open') {
        if (!found) return {status: 404, body: {...list(), status: 'missing', error: 'that root is no longer in the list'}};
        found.visited = t; found.device = body.device || found.device; opened = found;
      } else if (body.op === 'save') {
        const r = found || (db.roots.push({key: body.key, note: '', created: t, visited: t}), db.roots.at(-1));
        const entries = copy(body.entries); entries[0] = keepMetadata(r.entries?.[0], entries[0]);
        Object.assign(r, {entries, cursor: body.cursor, saved: t, device: body.device || r.device});
      } else if (body.op === 'note') {
        if (!found) return {status: 404, body: {...list(), status: 'missing', error: 'that root is no longer in the list'}};
        found.note = String(body.note).trim();
      } else if (body.op === 'remove') db.roots = db.roots.filter(r => r.key !== body.key);
      else if (body.op === 'limit') db.limit = body.limit;
      else return {status: 400, body: {schemaVersion: 1, status: 'schema', error: 'op is visit, open, save, note, remove or limit'}};
      keep();
      const out = list();
      if (opened && db.roots.includes(opened)) out.opened = {key: opened.key, entries: copy(opened.entries), cursor: opened.cursor, note: opened.note};
      return {status: 200, body: out};
    }
    return {db, log, handle, list};
  }
  global.viewRootsFake = viewRootsFake;
  if (typeof module !== 'undefined' && module.exports) module.exports = viewRootsFake;
})(typeof globalThis !== 'undefined' ? globalThis : this);
