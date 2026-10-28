'use strict';
(() => {
  const INITIAL_LINES = 2000, BATCH_LINES = 500;
  function effectiveLayout(layout, width) {
    return layout === 'auto' ? (width >= 900 ? 'split' : 'inline') : layout;
  }
  function lineModel(hunk) {
    let old = hunk.oldStart, next = hunk.newStart;
    return hunk.lines.map(raw => {
      const kind = raw[0], marker = kind === '\\';
      return {kind, text: marker ? raw : raw.slice(1),
        old: kind === ' ' || kind === '-' ? old++ : null,
        next: kind === ' ' || kind === '+' ? next++ : null};
    });
  }
  // Pair adjacent change runs by position, never by invented context or numbers.
  function pairLines(lines) {
    const rows = [];
    for (let i = 0; i < lines.length;) {
      const line = lines[i];
      if (line.kind === ' ' || line.kind === '\\') {
        rows.push({left: line, right: line.kind === ' ' ? line : null, count: line.kind === ' ' ? 1 : 0}); i++; continue;
      }
      const removed = [], added = [];
      while (i < lines.length && lines[i].kind === '-') removed.push(lines[i++]);
      while (i < lines.length && lines[i].kind === '+') added.push(lines[i++]);
      for (let j = 0; j < Math.max(removed.length, added.length); j++)
        rows.push({left: removed[j] || null, right: added[j] || null, count: Number(!!removed[j]) + Number(!!added[j])});
    }
    return rows;
  }
  function languageForPath(path, highlighter) {
    const base = String(path).split(/[\\/]/).pop().toLowerCase();
    const ext = base.split('.').pop();
    const aliases = {js:'javascript',jsx:'javascript',mjs:'javascript',cjs:'javascript',ts:'typescript',tsx:'typescript',
      py:'python',pyw:'python',rb:'ruby',rs:'rust',sh:'bash',zsh:'bash',h:'c',hpp:'cpp',cc:'cpp',cs:'csharp',
      yml:'yaml',md:'markdown',htm:'xml',html:'xml',svg:'xml',vue:'xml',svelte:'xml',kt:'kotlin',kts:'kotlin',
      dockerfile:'dockerfile',makefile:'makefile'};
    const language = aliases[ext] || ext;
    return highlighter?.getLanguage(language) ? language : null;
  }
  // Walk highlight.js's pinned token tree, carrying scopes across physical newlines.
  // Code itself is always ordinary textContent; no HTML parser or second lexer.
  function highlightSide(texts, language, highlighter) {
    const plain = () => texts.map(text => [{text, classes: ''}]);
    if (!texts.length || !language || !highlighter) return {lines: plain(), colored: false};
    try {
      const result = highlighter.highlight(texts.join('\n'), {language, ignoreIllegals: true});
      const root = result._emitter.rootNode, lines = [[]];
      function walk(node, classes) {
        if (typeof node === 'string') {
          const parts = node.split('\n');
          parts.forEach((text, index) => {
            if (index) lines.push([]);
            if (text) lines[lines.length - 1].push({text, classes});
          });
        } else {
          const scope = node.scope?.split('.').map((part, index) => index ? part + '_'.repeat(index) : 'hljs-' + part).join(' ');
          for (const child of node.children) walk(child, [classes, scope].filter(Boolean).join(' '));
        }
      }
      walk(root, '');
      return {lines, colored: true};
    } catch (_) { return {lines: plain(), colored: false}; }
  }
  // @beacon[
  //   id=opc-pane@opcDiffView.mount,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Owns the shared static inline/split recorded-diff reader with coordinate-fitted gutters, whole-hunk tokens, reversible bounds and selection-preserving updates; a no-files diff shows only its reason (no control row).,
  // ]
  function mount(container, diff, {layout = 'auto', wrap = true} = {}) {
    const doc = container.ownerDocument, win = doc.defaultView || globalThis;
    const element = (tag, className, text) => {
      const node = doc.createElement(tag); node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const root = element('section', 'opc-diff'), controls = element('div', 'opc-diff-controls');
    const choiceLabel = element('label', 'opc-diff-choice', 'Layout '), choice = element('select', 'opc-diff-layout');
    for (const [value, text] of [['auto','Auto'],['inline','Inline'],['split','Side-by-side']]) {
      const option = element('option', '', text); option.value = value; choice.append(option);
    }
    choiceLabel.append(choice);
    const wrapLabel = element('label', 'opc-diff-choice'), wrapInput = element('input', 'opc-diff-wrap');
    wrapInput.type = 'checkbox'; wrapLabel.append(wrapInput, doc.createTextNode('Wrap'));
    const counter = element('span', 'opc-diff-count'), show = element('button', 'opc-diff-show', 'Show all'); show.type = 'button';
    const reason = element('p', 'opc-diff-reason'), content = element('div', 'opc-diff-content');
    controls.append(choiceLabel, wrapLabel, counter, show); root.append(controls, reason, content); container.append(root);
    let dead = false, signature, pending = null, tasks = [], cursor = 0, shown = 0, total = 0, frame = null, expanding = false;
    // Task batches also progress when a background document isn't receiving animation frames.
    const request = fn => win.setTimeout(fn, 0);
    const cancel = id => win.clearTimeout(id);
    function selecting() {
      const selection = doc.getSelection();
      return selection && !selection.isCollapsed && (root.contains(selection.anchorNode) || root.contains(selection.focusNode));
    }
    function applyLayout() {
      if (dead) return;
      root.dataset.layout = effectiveLayout(layout, container.getBoundingClientRect().width);
      choice.value = layout;
    }
    function setLayout(value) { if (!dead) {layout = value; applyLayout();} }
    function setWrap(value) {
      if (dead) return;
      wrap = !!value; root.dataset.wrap = String(wrap); wrapInput.checked = wrap;
    }
    function updateCounter() {
      counter.textContent = `Showing ${shown.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} lines`;
      show.hidden = cursor >= tasks.length; show.disabled = expanding;
      show.textContent = expanding ? 'Showing all…' : 'Show all';
    }
    function prepare(hunk, path, note) {
      if (hunk.ready) return;
      hunk.ready = true;
      const highlighter = globalThis.hljs, language = languageForPath(path, highlighter);
      // Large hunks stay immediately readable: lexical work is optional, never a reading gate.
      const large = hunk.lines.length > INITIAL_LINES || hunk.lines.reduce((n, l) => n + l.text.length, 0) > 200000;
      let colored = !!language && !large;
      for (const side of ['old','next']) {
        const lines = hunk.lines.filter(line => line[side] !== null);
        const result = highlightSide(lines.map(line => line.text), large ? null : language, highlighter);
        colored = colored && (result.colored || lines.length === 0);
        lines.forEach((line, i) => {line[side + 'Tokens'] = result.lines[i];});
      }
      note.textContent = colored ? `${language} · syntax within recorded hunk only` :
        `Plain code · ${large ? 'large hunk (all lines remain available)' : language ? 'highlighting unavailable' : 'unknown language / highlighter unavailable'}`;
    }
    function cell(line, side) {
      const node = element('div', 'opc-diff-cell' + (!line ? ' opc-diff-blank' : line.kind === '+' ? ' opc-diff-add' : line.kind === '-' ? ' opc-diff-del' : ''));
      node.dataset.side = side;
      if (!line) {node.setAttribute('aria-label', 'No corresponding line'); return node;}
      if (line.kind === '\\') {node.className += ' opc-diff-marker'; node.textContent = line.text; return node;}
      node.append(element('span', 'opc-diff-number opc-diff-old', line.old ?? ''), element('span', 'opc-diff-number opc-diff-new', line.next ?? ''),
        element('span', 'opc-diff-sign', line.kind === '-' ? '−' : line.kind === '+' ? '+' : ' '));
      const code = element('code', 'opc-diff-code');
      for (const token of line[side + 'Tokens'] || [{text: line.text, classes: ''}])
        code.append(element('span', token.classes, token.text));
      node.append(code); return node;
    }
    function appendBatch(budget) {
      let used = 0;
      while (!dead && cursor < tasks.length) {
        const task = tasks[cursor];
        if (task.count && used + task.count > budget) break;
        task.run(); cursor++; shown += task.count; used += task.count;
      }
      updateCounter();
    }
    function stopBatch() { if (frame !== null) cancel(frame); frame = null; expanding = false; }
    function expand() {
      if (dead || expanding) return;
      expanding = true; updateCounter();
      const next = () => {
        frame = null; if (dead) return;
        appendBatch(BATCH_LINES);
        if (cursor < tasks.length) frame = request(next);
        else {expanding = false; updateCounter();}
      };
      frame = request(next);
    }
    function paint(value, key) {
      stopBatch(); signature = key; pending = null;
      let numberDigits = 4;
      tasks = []; cursor = shown = total = 0; content.replaceChildren(); root.hidden = value === null;
      controls.hidden = !value?.files?.length;  // a no-files diff (a shell call's reason) shows its reason, not inert controls
      reason.hidden = !value || (value.complete && !value.reason);
      reason.textContent = value?.reason || (value && !value.complete ? 'Incomplete recorded diff' : '');
      for (const file of value?.files || []) {
        const section = element('section', 'opc-diff-file');
        const header = element('div', 'opc-diff-file-head');
        header.append(element('strong', 'opc-diff-path', file.oldPath ? `${file.oldPath} → ${file.path}` : file.path),
          element('span', 'opc-diff-status', file.status), element('span', 'opc-diff-add-count', `+${file.additions}`),
          element('span', 'opc-diff-del-count', `−${file.deletions}`));
        section.append(header);
        tasks.push({count: 0, run: () => content.append(section)});
        if (!file.hunks.length || file.hunks.every(h => !h.lines.length)) {
          section.append(element('p', 'opc-diff-gap', 'No recorded lines (empty file / empty side)'));
        }
        let previous = null;
        for (const raw of file.hunks) {
          const hunk = {lines: lineModel(raw)}, note = element('div', 'opc-diff-syntax');
          // Include all recorded rows (also those awaiting Show all) and increments across digit boundaries.
          for (const line of hunk.lines) {
            for (const coordinate of [line.old, line.next])
              if (coordinate !== null) numberDigits = Math.max(numberDigits, String(coordinate).length);
          }
          const gap = element('div', 'opc-diff-gap', previous ?
            `Omitted context · old ${Math.max(0, raw.oldStart - previous.oldStart - previous.oldLines)} / new ${Math.max(0, raw.newStart - previous.newStart - previous.newLines)} lines` :
            'Recorded hunk · surrounding context not recorded');
          const heading = element('div', 'opc-diff-hunk', `@@ −${raw.oldStart},${raw.oldLines} +${raw.newStart},${raw.newLines} @@`);
          const rows = element('div', 'opc-diff-rows'); rows.tabIndex = 0; rows.setAttribute('aria-label', `Diff code: ${file.path}`);
          tasks.push({count: 0, run: () => {section.append(gap, heading, note, rows); prepare(hunk, file.path, note);}});
          let changeGroup = null;
          for (const row of pairLines(hunk.lines)) {
            const changed = row.left?.kind !== ' ' && row.left?.kind !== '\\';
            if (changed && !changeGroup) changeGroup = element('div', 'opc-diff-changes');
            if (!changed) changeGroup = null;
            const group = changeGroup;
            total += row.count;
            tasks.push({count: row.count, run: () => {
              const node = element('div', 'opc-diff-row');
              if (row.left?.kind === ' ') node.dataset.context = 'true';
              if (row.left?.kind === '\\') node.dataset.marker = 'true';
              node.append(cell(row.left, 'old'), cell(row.right, 'next'));
              if (group) {if (!group.parentNode) rows.append(group); group.append(node);} else rows.append(node);
            }});
          }
          previous = raw;
        }
      }
      // Keep the common-case CSS tracks byte-for-byte in charge; larger values need their digits plus padding.
      if (numberDigits > 4) root.style.setProperty('--diff-number-width', `${numberDigits + .6}ch`);
      else root.style.removeProperty('--diff-number-width');
      appendBatch(INITIAL_LINES); applyLayout();
    }
    function update(value) {
      if (dead) return;
      const key = JSON.stringify(value);
      if (key === signature) {pending = null; return;}
      if (selecting()) {pending = {value, key}; return;}
      paint(value, key);
    }
    function selectionEnded() { if (!dead && pending && !selecting()) paint(pending.value, pending.key); }
    function layoutChanged() {setLayout(choice.value);}
    function wrapChanged() {setWrap(wrapInput.checked);}
    choice.addEventListener('change', layoutChanged); wrapInput.addEventListener('change', wrapChanged); show.addEventListener('click', expand);
    doc.addEventListener('selectionchange', selectionEnded);
    const observer = win.ResizeObserver ? new win.ResizeObserver(applyLayout) : null;
    if (observer) observer.observe(container); else win.addEventListener('resize', applyLayout);
    function destroy() {
      if (dead) return;
      dead = true; stopBatch(); pending = null; tasks = [];
      observer?.disconnect(); if (!observer) win.removeEventListener('resize', applyLayout);
      doc.removeEventListener('selectionchange', selectionEnded);
      choice.removeEventListener('change', layoutChanged); wrapInput.removeEventListener('change', wrapChanged); show.removeEventListener('click', expand);
      root.remove();
    }
    setWrap(wrap); update(diff);
    return Object.freeze({update, setLayout, setWrap, destroy});
  }
  globalThis.opcDiffView = Object.freeze({mount, effectiveLayout, lineModel, pairLines, languageForPath, highlightSide,
    INITIAL_LINES, BATCH_LINES});
})();
