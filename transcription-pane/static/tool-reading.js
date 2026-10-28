// The tool inspector's reading view, shared by the desktop inspector (tool-inspector.js) and the phone
// page (m.js): bounded lossless Pretty JSON, Readable document blocks, conservative Markdown detection,
// Rendered/Source blocks and the selection-safe painter. Display only; raw snapshots never change.
(function () {
  'use strict';
  const SNAPSHOT_LIMIT = 8 * 1024 * 1024;
  // Formatting is optional: large/deep/token-dense text stays complete and raw.
  const FORMAT_LIMIT = 1024 * 1024, FORMAT_DEPTH = 128, FORMAT_TOKENS = 100000;
  // Markdown display inside reading blocks is bounded like the other optional views.
  const MARKDOWN_BLOCKS = 200, MARKDOWN_BYTES = 1024 * 1024;
  // The one shared UTF-8 count (tool-record-text.js), looked up when used so load order is free.
  const bytes = text => opcToolText.bytes(text);
  const painted = new WeakMap();

  // @beacon[
  //   id=opc-pane@prettyJSON,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=Bounded lossless JSON plus opt-in document-value blocks: one-layer string decoding only; preserve keys/numbers/duplicates/order/suffix and immutable raw Copy.,
  // ]
  function prettyJSON(raw, budget) {
    const unchanged = reason => ({ text: raw, reason });
    if (raw.length > FORMAT_LIMIT) return unchanged('format limit');
    // One lexical pass finds tokens and the first balanced container boundary.
    // Validate that completed value once, never a series of guessed prefixes.
    const tokens = []; let i = 0, depth = 0, end = 0, lastEnd = 0;
    while (/[\x20\t\r\n]/.test(raw[i] || '\0')) i++;
    const start = i, container = raw[i] === '{' || raw[i] === '[';
    while (i < raw.length) {
      const c = raw[i];
      if (/[\x20\t\r\n]/.test(c)) { i++; continue; }
      const begin = i;
      if (c === '"') {
        i++; let closed = false;
        while (i < raw.length) {
          if (raw[i] === '\\') i += 2;
          else if (raw[i++] === '"') { closed = true; break; }
        }
        if (!closed) return unchanged('non-JSON');
      } else if ('{}[],:'.includes(c)) i++;
      else { while (i < raw.length && !/[\x20\t\r\n{}\[\],:"]/.test(raw[i])) i++; }
      tokens.push(raw.slice(begin, i));
      lastEnd = i;
      if (tokens.length > FORMAT_TOKENS) return unchanged('format limit');
      if (c === '{' || c === '[') { if (++depth > FORMAT_DEPTH) return unchanged('format limit'); }
      else if (c === '}' || c === ']') {
        depth--;
        if (container && depth === 0) { end = i; break; }
      }
    }
    if (!end) end = tokens.length ? lastEnd : raw.length;
    try { JSON.parse(raw.slice(start, end)); } catch { return unchanged('non-JSON'); }
    // Scalars are accepted only as whole content. Containers may carry an exact
    // prose suffix; no searching for JSON in the middle or at the end of text.
    const suffix = raw.slice(end);
    const mixed = /[^\x20\t\r\n]/.test(suffix);
    if (mixed && !container) return unchanged('non-JSON');
    // Concatenated containers / unmatched JSON delimiters are ambiguous rather
    // than prose. Conservatively leave them untouched (no second-prefix search).
    if (mixed && /^[\x20\t\r\n]*[{}\[\],:"]/.test(suffix)) return unchanged('non-JSON');
    if (mixed) {
      try { JSON.parse(suffix); return unchanged('non-JSON'); }
      catch { /* Non-JSON prose, not a second concatenated JSON value. */ }
    }
    const chunks = [raw.slice(0, start)], pieces = [], stack = [];
    let level = 0, length = chunks[0].length, documents = 0, plain = [chunks[0]];
    const append = chunk => { chunks.push(chunk); length += chunk.length; return length <= budget; };
    const flush = () => { const text = plain.join(''); if (text) pieces.push({ text }); plain = []; };
    const newline = () => '\n' + '  '.repeat(level);
    for (let n = 0; n < tokens.length; n++) {
      const token = tokens[n]; let chunk = token;
      if (token === '{' || token === '[') { stack.push({ type: token, index: 0 }); level++; if (tokens[n + 1] !== '}' && tokens[n + 1] !== ']') chunk += newline(); }
      else if (token === '}' || token === ']') { stack.pop(); level--; if (tokens[n - 1] !== '{' && tokens[n - 1] !== '[') chunk = newline() + token; }
      else if (token === ',') { if (stack.at(-1)?.type === '[') stack.at(-1).index++; chunk += newline(); }
      else if (token === ':') chunk += ' ';
      if (!append(chunk)) return unchanged('format limit');
      // Keys remain their original JSON tokens. Decode exactly ONE value layer;
      // a decoded literal backslash+n is not another invitation to unescape.
      const decoded = token.startsWith('"') && tokens[n + 1] !== ':' ? JSON.parse(token) : null;
      if (decoded !== null && /[\r\n\t]/.test(decoded)) {
        flush(); documents++;
        pieces.push({ text: decoded, document: true, index: stack.at(-1)?.type === '[' ? stack.at(-1).index : null });
      } else plain.push(chunk);
    }
    if (!append(suffix)) return unchanged('format limit');
    // The suffix stays its own piece so the reading view can test it for Markdown.
    flush(); if (suffix) pieces.push({ text: suffix, suffix: true });
    const text = chunks.join('');
    if (bytes(text) > budget) return unchanged('format limit');
    // Limit DOM work as well as text bytes. Keep ordinary Pretty JSON intact if
    // the optional document view is too costly; never drop an individual value.
    const readingBytes = pieces.reduce((total, piece) => total + bytes(piece.text) + (piece.index != null ? bytes(`[${piece.index}]\n`) : 0), 0);
    const readingLimit = documents > 2000 || readingBytes > budget;
    return { text, reason: mixed ? 'JSON + text' : 'JSON', pieces: readingLimit ? null : pieces,
      documents, readingLimit, displayBytes: Math.max(bytes(text), readingLimit ? 0 : readingBytes) };
  }
  // Lines that read as source code, shell or config rather than prose.
  const CODE_LINE = /[;{]\s*$|^\s*[}\])][;,)]*\s*$|=>\s*\{?\s*$|^\s*(def|class|function|import|export|const|let|var|return|async|await|elif|except|package|public|private|protected|fn|pub|func|echo|fi|done|then|esac)\b|^\s*from\s+\S+\s+import\b|^\s*#include\b|^\s*(if|for|while|switch)\s*\(|^\s*(else|try|finally):\s*$|^\s*\$ \S/;
  const LOG_LINE = /^\s*\[?\d{4}-\d\d-\d\d[T ]\d\d:\d\d|^\s*\[?(ERROR|WARN(ING)?|INFO|DEBUG|TRACE|FATAL)\b[\]:]|^[\w.\/\\-]+:\d+[:-]/;
  const LIST_LINE = /^\s{0,6}([-*+]|\d{1,3}[.)])\s+\S/;
  // @beacon[
  //   id=opc-pane@markdownLike,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=Conservative Markdown test for inspector reading blocks: needs two kinds of Markdown signal (or several headings / a real table) and refuses JSON, markup, diffs, logs, tracebacks, numbered listings, config and code.,
  // ]
  function markdownLike(text) {
    if (typeof text !== 'string' || text.length > FORMAT_LIMIT) return false;
    const trimmed = text.trim();
    if (!trimmed || /^[<{]/.test(trimmed)) return false;
    if (trimmed[0] === '[') { try { JSON.parse(trimmed); return false; } catch { /* prose may open with [ */ } }
    if (/^(diff --git |@@ -\d+(,\d+)? \+\d+|\+\+\+ [ab]\/|--- a\/|index [0-9a-f]{7,}\.\.[0-9a-f]{7,})/m.test(text)) return false;
    if (/^Traceback \(most recent call last\):/m.test(text)) return false;
    const lines = text.split(/\r?\n/);
    let fence = null, fences = 0, considered = 0, code = 0, log = 0, numbered = 0, config = 0;
    let headings = 0, lists = 0, tableRows = 0, delimiter = false, bold = 0, links = 0;
    for (let n = 0; n < lines.length; n++) {
      const line = lines[n], open = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
      if (fence) {
        if (open && open[1][0] === fence[0] && open[1].length >= fence.length && !line.slice(open[0].length).trim()) { fence = null; fences++; }
        continue;
      }
      if (open) { fence = open[1]; continue; }
      if (!line.trim()) continue;
      considered++;
      if (/^\s*\d+(\t|→| \| )/.test(line)) numbered++;
      if (LOG_LINE.test(line)) log++;
      if (/^\s*[\w.-]+:(\s|$)/.test(line) && !/^\s*(https?|mailto):/.test(line)) config++;
      if (/(^|[\s(])\*\*[^\s*][^*\n]*?\*\*(?=$|[\s.,;:!?)])/.test(line)) bold++;
      if (/\[[^\]\n]+\]\((https?:\/\/|mailto:|#|\.{0,2}\/)[^)\s]*\)/.test(line)) links++;
      if (/^#{1,6}\s+\S/.test(line)) {
        // A code comment sits on top of code; a heading is followed by a blank line, list or table.
        const next = lines[n + 1];
        if (next === undefined || !next.trim() || LIST_LINE.test(next) || /^\s*\|/.test(next)) headings++;
      } else if (LIST_LINE.test(line)) lists++;
      else if (/^\s*\|.*\|\s*$/.test(line)) {
        tableRows++;
        if (/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(line)) delimiter = true;
      } else if (CODE_LINE.test(line)) code++;
    }
    // A share of code/log/listing/config lines refuses; one stray line in short prose does not.
    if (!considered || (code >= 2 && code > considered * 0.2) || (log >= 2 && log > considered * 0.2) ||
        (numbered >= 2 && numbered > considered * 0.3) || (config >= 3 && config > considered * 0.3)) return false;
    const table = delimiter && tableRows >= 3;
    const kinds = [headings > 0, lists >= 2, fences > 0, table, bold > 0, links > 0].filter(Boolean).length;
    return kinds >= 2 || headings >= 2 || table;
  }
  // Workflowy note reads number their rows ("  12 | text", elisions "... | ..."). When EVERY row after
  // the header has that exact form, the note body is the rows without their prefixes; else null.
  function numberedNote(text) {
    const lines = text.replace(/\s+$/, '').split(/\r?\n/), row = /^\s*(\d+|\.\.\.) \|(?: |$)/;
    const first = lines.findIndex(line => /^\s*\d+ \|(?: |$)/.test(line));
    if (first < 0 || lines.length - first < 3) return null;
    let last = 0; const body = [];
    for (const line of lines.slice(first)) {
      const match = row.exec(line);
      if (!match) return null;
      if (match[1] === '...') { body.push('', line.slice(match[0].length).replace(/^\.\.\.\s*/, '… '), ''); continue; }
      if (Number(match[1]) <= last) return null;
      last = Number(match[1]); body.push(line.slice(match[0].length));
    }
    return { head: lines.slice(0, first).join('\n').replace(/\s+$/, ''), body: body.join('\n') };
  }
  // Reading pieces for one cached view: decoded JSON document values, a Markdown-looking suffix after
  // leading JSON, or a whole non-JSON text that looks like Markdown. Null when nothing reads better.
  function readingView(view, raw) {
    if (view.readingLimit) return null;
    let markdown = 0, markdownBytes = 0;
    const mark = text => {
      if (markdown >= MARKDOWN_BLOCKS || markdownBytes + bytes(text) > MARKDOWN_BYTES || !markdownLike(text)) return false;
      markdown++; markdownBytes += bytes(text); return true;
    };
    if (view.reason === 'non-JSON') {
      if (mark(raw)) return { pieces: [{ text: raw, document: true, markdown: true, markdownOnly: true }], markdown };
      const note = raw.length <= FORMAT_LIMIT ? numberedNote(raw) : null;
      return note && mark(note.body) ? { pieces: [{ text: raw, document: true, markdown: true, markdownOnly: true,
        head: note.head, body: note.body }], markdown } : null;
    }
    if (!view.pieces) return null;
    const pieces = view.pieces.map(piece => piece.document ? { ...piece, markdown: mark(piece.text) } :
      piece.suffix && mark(piece.text) ? { text: piece.text, document: true, markdown: true, markdownOnly: true } : piece);
    return view.documents || markdown ? { pieces, markdown } : null;
  }
  function markdownBlock(piece, ordinal, choices) {
    const block = document.createElement('span');
    block.className = 'inspector-document inspector-markdown';
    const rendered = document.createElement('div'); rendered.className = 'inspector-md-rendered';
    const source = document.createElement('span'); source.className = 'inspector-md-source';
    source.append(document.createTextNode(piece.text));
    if (piece.body == null) opcMessageMarkdown.render(rendered, piece.text, true);
    else {
      // Numbered note read: its header stays plain text; the body renders without row numbers.
      const head = document.createElement('div'); head.className = 'inspector-md-head';
      head.append(document.createTextNode(piece.head));
      const body = document.createElement('div'); body.className = 'inspector-md-body';
      opcMessageMarkdown.render(body, piece.body, true);
      rendered.append(head, body);
    }
    const modes = document.createElement('span'); modes.className = 'inspector-md-modes';
    modes.setAttribute('role', 'group'); modes.setAttribute('aria-label', 'Markdown display');
    const buttons = ['Rendered', 'Source'].map(label => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'inspector-md-mode'; button.textContent = label;
      button.title = label === 'Rendered' ? 'Show this block as rendered Markdown (display only)' : 'Show the exact source text of this block';
      modes.append(button); return button;
    });
    const show = asSource => {
      rendered.hidden = asSource; source.hidden = !asSource;
      block.setAttribute('data-view', asSource ? 'source' : 'rendered');
      buttons[0].setAttribute('aria-pressed', String(!asSource)); buttons[1].setAttribute('aria-pressed', String(asSource));
      if (asSource) choices?.add(ordinal); else choices?.delete(ordinal);
    };
    buttons[0].onclick = () => show(false); buttons[1].onclick = () => show(true);
    if (piece.index != null) {
      const index = document.createElement('span'); index.className = 'inspector-document-index';
      index.textContent = `[${piece.index}]\n`; block.append(index);
      block.setAttribute('aria-label', `Document text at array index ${piece.index}`);
    }
    block.append(modes, rendered, source);
    show(!!choices?.has(ordinal));
    return block;
  }
  function readerSelecting(element) {
    const range = document.getSelection();
    return range && !range.isCollapsed && (element.contains(range.anchorNode) || element.contains(range.focusNode));
  }
  // @beacon[
  //   id=opc-pane@paintReading,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=Paints one reader as safe plain text or reading pieces (document blocks, Rendered/Source Markdown blocks), keeping its scroll; an unchanged view is left alone and an active text selection defers the repaint (returns false).,
  // ]
  function paint(element, text, pieces = null, choices = null) {
    const previous = painted.get(element);
    if (previous?.text === text && !!previous?.pieces === !!pieces) return true;
    if (readerSelecting(element)) return false;
    const top = element.scrollTop, left = element.scrollLeft;
    if (pieces) {
      const fragment = document.createDocumentFragment();
      // Markdown is display only, through the pane's sanitizing renderer; without it, plain blocks.
      const markdown = !!globalThis.opcMessageMarkdown?.render; let ordinal = 0;
      for (const piece of pieces) {
        // A block that exists only for Markdown stays ordinary text when the renderer is unavailable.
        if (!piece.document || (piece.markdownOnly && !markdown)) fragment.append(document.createTextNode(piece.text));
        else if (piece.markdown && markdown) fragment.append(markdownBlock(piece, ordinal++, choices));
        else {
          const block = document.createElement('span'); block.className = 'inspector-document';
          if (piece.index != null) {
            const index = document.createElement('span'); index.className = 'inspector-document-index';
            index.textContent = `[${piece.index}]\n`; block.append(index);
            block.setAttribute('aria-label', `Document text at array index ${piece.index}`);
          }
          block.append(document.createTextNode(piece.text)); fragment.append(block);
        }
      }
      element.replaceChildren(fragment);
    } else element.textContent = text; // Untrusted plain text only, never HTML.
    painted.set(element, { text, pieces });
    element.scrollTop = top; element.scrollLeft = left;
    return true;
  }
  // Views are computed once per fetched snapshot. Budget for the largest combination of independent
  // ON/OFF states; optional expansion never makes an otherwise-readable raw snapshot unavailable.
  function snapshotViews(inputText, outputText) {
    const rawInputBytes = new TextEncoder().encode(inputText).length;
    const rawOutputBytes = new TextEncoder().encode(outputText).length;
    const inputView = prettyJSON(inputText, SNAPSHOT_LIMIT - rawOutputBytes);
    const outputView = prettyJSON(outputText, SNAPSHOT_LIMIT - Math.max(rawInputBytes, inputView.displayBytes || bytes(inputView.text)));
    inputView.reading = readingView(inputView, inputText); outputView.reading = readingView(outputView, outputText);
    return { input: inputView, output: outputView };
  }
  // @beacon[
  //   id=opc-pane@regionView,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=What one input/output region shows for its Pretty JSON and Readable text choices: the text, the reading pieces (null = plain text) and the caption after its size label; desktop inspector and phone tool view alike.,
  // ]
  function regionView(view, raw, pretty, readable, count) {
    const reading = pretty && readable, reason = view.reason;
    // Markdown blocks count only when the sanitizing renderer is loaded.
    const markdown = globalThis.opcMessageMarkdown?.render && view.reading?.markdown || 0;
    return { text: pretty ? view.text : raw,
      pieces: reading && view.reading && (view.documents || markdown) ? view.reading.pieces : null,
      caption: (pretty && reason === 'format limit' ? ' · raw (format limit)' : '') +
        // Nothing to read in an empty or unavailable text: no reading caption.
        (reading && count ? view.readingLimit ? ' · Pretty JSON (reading limit)' : view.documents ? ' · reading view (not JSON)' + (markdown ? ' · Markdown' : '') : markdown ? ' · reading view (Markdown)' : reason === 'non-JSON' ? ' · raw (not JSON)' : reason === 'format limit' ? ' · reading unavailable' : ' · no document strings' : '') };
  }
  // @beacon[
  //   id=opc-pane@callTimeText,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=An opened tool call's time line, desktop inspector and phone tool view alike: "2026-10-18 23:52:07 ET · took 1m 23s" (Eastern, DST-correct, 24-hour); the duration (callDuration, also on the collapsed rows) only when the stored records carry both the call's time and its result's, never for a call still running; unknown start → empty.,
  // ]
  function callTimeText(startMs, endMs) {
    if (!Number.isFinite(startMs) || startMs <= 0) return '';
    let stamp;
    try {
      const p = {};
      for (const x of new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(startMs))) p[x.type] = x.value;
      stamp = `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} ET`;
    } catch (_) { return ''; }
    const took = callDuration(startMs, endMs);
    return took ? stamp + ' · took ' + took : stamp;
  }
  // How long a call took, as its opened view says it ("8.3s", "1m 23s"; the collapsed rows show it too): only for a
  // sane stored start and end, never for a call still running.
  function callDuration(startMs, endMs) {
    if (!Number.isFinite(startMs) || startMs <= 0 || !Number.isFinite(endMs) || endMs < startMs) return '';
    const ms = endMs - startMs, s = Math.round(ms / 1000), two = n => String(n).padStart(2, '0');
    return ms < 10000 ? (ms / 1000).toFixed(1) + 's' : s < 60 ? s + 's' : s < 3600 ? Math.floor(s / 60) + 'm ' + two(s % 60) + 's' :
      Math.floor(s / 3600) + 'h ' + two(Math.floor(s / 60) % 60) + 'm ' + two(s % 60) + 's';
  }
  // @beacon[
  //   id=opc-pane@elapsedText,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=A call still running's elapsed time at whole-second resolution ("0s", "12s", "1m 05s", "1h 02m 03s"), never negative or invented: an unknown start or a clock reading before it is empty. A finished call's duration stays callDuration's.,
  // ]
  function elapsedText(startMs, nowMs) {
    if (!Number.isFinite(startMs) || startMs <= 0 || !Number.isFinite(nowMs) || nowMs < startMs) return '';
    const s = Math.floor((nowMs - startMs) / 1000), two = n => String(n).padStart(2, '0');
    return s < 60 ? s + 's' : s < 3600 ? Math.floor(s / 60) + 'm ' + two(s % 60) + 's' :
      Math.floor(s / 3600) + 'h ' + two(Math.floor(s / 60) % 60) + 'm ' + two(s % 60) + 's';
  }
  // Call states that may still be running: the conversation's own V2 running/streaming states and a live
  // record's plain "running". A terminal status (stored/error/completed/…) never qualifies.
  const RUNNING_STATUS = new Set(['running', 'pending', 'pending-or-missing', 'not-final-fragment', 'streaming']);
  function isRunningStatus(status) { return RUNNING_STATUS.has(status); }
  // @beacon[
  //   id=opc-pane@runningTools,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=The desktop and phone's current-tool strip above history: every open parallel call stays visible even above the reading window; keyed rows tick in place, source freshness stops stale timers, and terminal calls never inherit a session spinner.,
  // ]
  function runningTools(container, doc=container?.ownerDocument) {
    const rows=new Map();let title;
    function update(calls,busy,observedAt,error,now=Date.now()) {
      if(!container || !doc)return;
      const fresh=Number.isFinite(observedAt) && now-observedAt<=15000 && !error;
      container.hidden=busy!==true || !fresh;
      if(container.hidden){rows.clear();container.replaceChildren();title=null;return;}
      if(!title){title=doc.createElement('div');title.className='running-tools-title';container.append(title);}
      const known=Array.isArray(calls),open=(known?calls:[]).filter(c=>c && isRunningStatus(c.status) && c.resultTime==null);
      const heading=!known?'Busy · running tool details unavailable':open.length?'Running tools · '+open.length:'Busy · no running tool recorded';
      if(title.textContent!==heading)title.textContent=heading;
      const keep=new Set();
      for(const c of open) {
        const key=c.callRef || c.id;if(!key)continue;keep.add(key);
        let row=rows.get(key);
        if(!row) {
          const node=doc.createElement('div'),name=doc.createElement('span'),time=doc.createElement('span'),preview=doc.createElement('span');
          node.className='running-tool';name.className='running-tool-name';time.className='running-tool-time';preview.className='running-tool-preview';
          node.append(name,time,preview);container.append(node);row={node,name,time,preview};rows.set(key,row);
        }
        const name=String(c.name || 'tool');if(row.name.textContent!==name)row.name.textContent=name;
        const signature=JSON.stringify(c.previewData);
        if(signature!==row.signature) {
          row.signature=signature;
          try{row.preview.textContent=globalThis.opcToolPreview?.preview?.(c.previewData,c.name,{descriptor:true}) || '';}
          catch(_){row.preview.textContent='Preview unavailable';}
        }
        const start=Number.isFinite(c.time)?c.time*1000:NaN,elapsed=elapsedText(start,now),value=elapsed?elapsed+' so far':'start time unknown';
        if(row.time.textContent!==value)row.time.textContent=value;
        row.node.title='Current in-flight call, independent of the history scroll position. '+(elapsed?'Elapsed '+elapsed:'Start time unavailable');
      }
      for(const [key,row] of rows)if(!keep.has(key)){row.node.remove();rows.delete(key);}
    }
    return {update};
  }
  globalThis.opcToolReading = Object.freeze({ prettyJSON, markdownLike, numberedNote, readingView, snapshotViews, regionView,
    paint, forget: element => painted.delete(element), readerSelecting, callTimeText, callDuration, elapsedText, isRunningStatus, runningTools });
})();
