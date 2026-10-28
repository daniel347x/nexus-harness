'use strict';

// View's two pickers (glimpse.html: the phone's View tab and the desktop View pane), as the Workflowy Chrome extension
// shows them: Docks (the favorites, or every Dock and/or the local ones) nested under their parent Docks (the pane server
// gives each Dock its depth and its ⭐/Ⓛ marks), and slice groups in the extension's pipe-delimited hierarchy (expanders,
// [n] ordering, dummy expander groups). One compact row per entry, never wrapped: the list scrolls sideways for a long
// name (the full-screen picker's CSS wraps them). A tap on a row picks it; a tap on an expander opens or closes it. No
// request of its own: glimpse.js hands it the catalog it read and does the render.
const opcViewPickers = (() => {
  // ---- The extension's own slice-group grammar, copied verbatim from the NEXUS extension
  // (workflowy-glimpse-cache/glimpse-cache-client.js: parseSliceGroupName, buildNestedDisplayItems). tests/
  // view-pickers.test.cjs runs the extension's copy beside this one on one corpus whenever the NEXUS checkout is present,
  // so a change on either side fails there. Pure display convention: storage is the flat list of names.

  // Parse a group NAME into { isExpander, segments (clean names), index }.
  // Leading [int] => expander/category row (index = terminal segment order).
  // Else a leaf; a trailing [int] on the last segment = that leaf's order.
  // An unparseable-integer bracket is NOT special (treated as literal text), so
  // '[foo]nexus' is just a normal group literally named '[foo]nexus'.
  function parseSliceGroupName(name) {
    const raw = (typeof name === 'string') ? name : '';
    const lead = raw.match(/^\s*\[(-?\d+)\]\s*/);
    if (lead) {
      const rest = raw.slice(lead[0].length);
      const segments = rest.split('|').map((s) => s.trim());
      if (segments.length === 0 || (segments.length === 1 && segments[0] === '')) {
        // '[2]' with no path -> not a usable expander; keep it a plain group.
        return { isExpander: false, segments: [raw.trim()], index: null };
      }
      return { isExpander: true, segments: segments, index: parseInt(lead[1], 10) };
    }
    const parts = raw.split('|').map((s) => s.trim());
    let index = null;
    const last = parts.length ? parts[parts.length - 1] : '';
    const tm = last.match(/\[(-?\d+)\]\s*$/);
    if (tm) {
      index = parseInt(tm[1], 10);
      parts[parts.length - 1] = last.slice(0, tm.index).trim();
    }
    return { isExpander: false, segments: parts, index: index };
  }

  // Returns a flat array of { kind:'leaf'|'expander', depth, name, group, ...
  // } in display order. Collapsed categories omit their descendants. Sibling
  // order: index asc (negatives first, gaps fine); unindexed last, alpha tie.
  function buildNestedDisplayItems(items, parseName, expandedPaths, expandAll) {
    const makeNode = (nm, path) => ({
      name: nm, path: path, pathKey: path.join('|'),
      expanderDefs: [], leaves: [], children: new Map(),
    });
    const root = makeNode('', []);
    const walkCreate = (segs) => {
      let node = root;
      for (let i = 0; i < segs.length; i++) {
        const seg = segs[i];
        if (!node.children.has(seg)) {
          node.children.set(seg, makeNode(seg, node.path.concat([seg])));
        }
        node = node.children.get(seg);
      }
      return node;
    };
    items.forEach((g) => {
      const p = parseName(g.name);
      if (p.isExpander) {
        walkCreate(p.segments).expanderDefs.push({ group: g, index: p.index });
      } else if (p.segments.length <= 1) {
        root.leaves.push({ group: g, index: p.index, name: p.segments[0] || g.name });
      } else {
        const parent = walkCreate(p.segments.slice(0, p.segments.length - 1));
        parent.leaves.push({ group: g, index: p.index, name: p.segments[p.segments.length - 1] });
      }
    });

    const out = [];
    const emit = (node, depth) => {
      const entries = [];
      node.leaves.forEach((lf) => {
        entries.push({ kind: 'leaf', index: lf.index, name: lf.name, group: lf.group });
      });
      node.children.forEach((child) => {
        if (child.expanderDefs.length > 0) {
          let minIdx = null;
          child.expanderDefs.forEach((d) => {
            if (d.index != null && (minIdx === null || d.index < minIdx)) minIdx = d.index;
          });
          let canonAssigned = false;
          child.expanderDefs.forEach((d) => {
            const isCanon = (!canonAssigned && d.index === minIdx);
            if (isCanon) canonAssigned = true;
            entries.push({ kind: 'expander', index: d.index, name: child.name,
              group: d.group, childNode: isCanon ? child : null, duplicate: !isCanon,
              pathStr: child.pathKey });
          });
        } else {
          entries.push({ kind: 'expander', index: null, name: child.name,
            group: null, childNode: child, synthetic: true, pathStr: child.pathKey });
        }
      });
      entries.sort((a, b) => {
        const ai = (a.index == null) ? Infinity : a.index;
        const bi = (b.index == null) ? Infinity : b.index;
        if (ai !== bi) return ai - bi;
        const an = (a.name || '').toLowerCase();
        const bn = (b.name || '').toLowerCase();
        if (an < bn) return -1;
        if (an > bn) return 1;
        return 0;
      });
      entries.forEach((e) => {
        if (e.kind === 'leaf') {
          out.push({ kind: 'leaf', group: e.group, name: e.name, depth: depth });
        } else {
          const collapsed = (!expandAll && e.childNode) ? !expandedPaths.has(e.childNode.pathKey) : false;
          out.push({ kind: 'expander', name: e.name, group: e.group, depth: depth,
            childNode: e.childNode, duplicate: !!e.duplicate, synthetic: !!e.synthetic,
            collapsed: collapsed, pathStr: e.pathStr });
          if (e.childNode && !collapsed) emit(e.childNode, depth + 1);
        }
      });
    };
    emit(root, 0);
    return out;
  }
  // ---- end of the extension's code

  // A slice group's short name, as the extension's compact surfaces show it (sliceGroupDisplayName): the last segment
  // without its [n]; the trimmed full name when none can be derived.
  function groupLabel(name) {
    const p = parseSliceGroupName(name), leaf = String(p.segments[p.segments.length - 1] || '').trim();
    return leaf || String(name || '').trim();
  }

  // ---- the rows: one button per entry in a list that scrolls both ways; the indent is the depth
  const INDENT = 16;
  function rowButton(list, cls, depth, title) {
    const b = (list.ownerDocument || document).createElement('button');
    b.type = 'button'; b.className = 'g-prow ' + cls; b.dataset.depth = String(depth);
    b.style.paddingLeft = (8 + depth * INDENT) + 'px';
    b.style.setProperty?.('--depth', String(depth));
    if (title) b.title = title;
    return b;
  }
  function span(list, cls, text) { const s = (list.ownerDocument || document).createElement('span'); s.className = cls; s.textContent = text; return s; }
  function holder(list) { const inner = (list.ownerDocument || document).createElement('div'); inner.className = 'g-plist'; return inner; }

  const depthOf = dock => Number.isInteger(dock?.depth) && dock.depth > 0 ? dock.depth : 0;
  // A filter over the Dock list (the full-screen picker): the Docks whose name holds the query, with the Docks they nest
  // under kept as context so the nesting still reads; null for an empty query.
  function dockFilter(docks, query) {
    const q = String(query || '').trim().toLowerCase();
    if (!q) return null;
    const keep = new Set(), matches = new Set(), stack = [];
    docks.forEach((dock, i) => {
      while (stack.length && depthOf(docks[stack[stack.length - 1]]) >= depthOf(dock)) stack.pop();
      if (String(dock.name || '').toLowerCase().includes(q)) { matches.add(i); keep.add(i); for (const j of stack) keep.add(j); }
      stack.push(i);
    });
    return {keep, matches};
  }

  // @beacon[
  //   id=opc-pane@paintDockPicker,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Paints View's Dock picker (compact, or full screen): one row per Dock in the daemon's visual order, indented by its depth among the listed Docks (the pane server's dock_depths; 0 from a server without it), its [ap:…] dimmed after the name (names line up by depth), then ⭐ for a favorite and Ⓛ for a local Dock where marks asks for them; a filter (dockFilter) shows only the matches and the Docks they nest under (dimmed); the current Dock marked; a tap calls onPick(index into docks).,
  // ]
  function paintDocks(list, docks, {selected = null, onPick, marks = {}, filter = null} = {}) {
    const inner = holder(list);
    docks.forEach((dock, i) => {
      if (filter && !filter.keep.has(i)) return;
      const depth = depthOf(dock), name = String(dock.name || '');
      const b = rowButton(list, 'g-pleaf' + (filter && !filter.matches.has(i) ? ' g-pctx' : ''), depth, name), ap = name.match(/^\s*(\[ap:[A-Za-z0-9]+\])\s*/);
      if (ap) b.append(span(list, 'g-pname', name.slice(ap[0].length)), span(list, 'g-pap', ap[1]));  // names line up; the AP-ID trails
      else b.append(span(list, 'g-pname', name));
      const mark = (marks.favorite && dock.favorite ? '⭐' : '') + (marks.local && dock.local ? 'Ⓛ' : '');
      if (mark) b.append(span(list, 'g-pmark', mark));
      if (selected && dock.handle === selected) b.setAttribute('aria-current', 'true');
      b.addEventListener('click', () => onPick?.(i));
      inner.append(b);
    });
    list.replaceChildren(inner);
  }

  // @beacon[
  //   id=opc-pane@paintGroupPicker,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Paints View's slice-group picker (compact, or full screen) through the extension's own buildNestedDisplayItems/parseSliceGroupName (copied verbatim): expanders (a stored [n]Path dummy group, or a category with no row of its own) open and close on a tap, collapsed by default as in the extension unless expandAll (the extension's own switch) opens every section; a filter keeps only the groups whose full name holds it, every section open; a duplicate expander shows empty with the extension's warning; a leaf shows its last segment and picks onPick(index into groups); the current group's row is marked.,
  // ]
  function paintGroups(list, groups, {selected = null, expanded = new Set(), onPick, onToggle, expandAll = false, filter = ''} = {}) {
    const inner = holder(list), q = String(filter || '').trim().toLowerCase();
    let items = groups.map((g, i) => ({name: String(g.name || ''), handle: g.handle, i}));
    if (q) items = items.filter(g => !parseSliceGroupName(g.name).isExpander && g.name.toLowerCase().includes(q));
    for (const item of buildNestedDisplayItems(items, parseSliceGroupName, expanded, expandAll || !!q)) {
      if (item.kind === 'leaf') {
        const b = rowButton(list, 'g-pleaf', item.depth, item.group.name);
        b.append(span(list, 'g-pname', item.name || item.group.name));
        if (selected && item.group.handle === selected) b.setAttribute('aria-current', 'true');
        b.addEventListener('click', () => onPick?.(item.group.i));
        inner.append(b);
        continue;
      }
      const open = !!item.childNode && !item.collapsed;
      const b = rowButton(list, 'g-pexp' + (item.synthetic ? ' g-psynth' : '') + (item.duplicate ? ' g-pdup' : ''), item.depth,
        item.group ? item.group.name : item.pathStr);
      b.append(span(list, 'g-pchev', item.duplicate ? '·' : open ? '▾' : '▸'), span(list, 'g-pname', item.name));
      if (item.duplicate) b.append(span(list, 'g-pnote', ' (duplicate expander — empty; delete or renumber)'));
      if (item.childNode) { b.setAttribute('aria-expanded', String(open)); b.addEventListener('click', () => onToggle?.(item.pathStr)); }
      else b.disabled = true;
      inner.append(b);
    }
    list.replaceChildren(inner);
  }

  return {parseSliceGroupName, buildNestedDisplayItems, groupLabel, dockFilter, paintDocks, paintGroups};
})();
globalThis.opcViewPickers = opcViewPickers;
