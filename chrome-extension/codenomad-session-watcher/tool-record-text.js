// Shared snapshot definition for CodeNomad rows and the Pane. Functions only; no records/credentials.
// The Pane serves this exact file at /tool-record-text.js (no second implementation).
(function () {
  'use strict';
  function valueEnd(text, start) {
    let quoted = false, depth = 0;
    for (let i = start; i < text.length; i++) {
      const c = text[i];
      if (quoted) {
        if (c === '\\') i++;
        else if (c === '"') { quoted = false; if (!depth) return i + 1; }
      } else if (c === '"') quoted = true;
      else if (c === '{' || c === '[') depth++;
      else if (c === '}' || c === ']') { if (!depth) return i; if (!--depth) return i + 1; }
      else if (!depth && (c === ',' || /[\x20\t\r\n]/.test(c))) return i;
    }
    return text.length;
  }
  // Call only on JSON.parse-validated wire text. Values retain their literal wire lexemes.
  function rawMembers(text) {
    const fields = new Map();
    let i = 0;
    const whitespace = () => { while (/[\x20\t\r\n]/.test(text[i] || '\0')) i++; };
    whitespace(); const object = text[i] === '{';
    if (!object && text[i] !== '[') return fields;
    i++; let index = 0;
    while (i < text.length) {
      whitespace(); if (text[i] === '}' || text[i] === ']') break;
      let key = index++;
      if (object) {
        const end = valueEnd(text, i); key = JSON.parse(text.slice(i, end));
        i = end; whitespace(); i++; whitespace();
      }
      const end = valueEnd(text, i); fields.set(key, text.slice(i, end)); i = end;
      whitespace(); if (text[i] !== ',') break; i++;
    }
    return fields;
  }
  const storedText = raw => raw?.startsWith('"') ? JSON.parse(raw) : raw;
  const bytes = text => new TextEncoder().encode(text).length;
  const size = value => value == null ? '—' : `${value === 0 ? '0' : value < 1024 ? (value / 1024).toFixed(3) : (value / 1024).toFixed(1)} KiB`;
  const SIZE_NOTE = 'UTF-8 bytes of the raw copy snapshot (1 KiB = 1024 bytes), not tokens or pretty text. Text blocks joined with two newlines; non-text blocks use wire JSON. Error headings/separators count. Saved stubs count only the stored stub, never its lost original.';

  // @beacon[
  //   id=opc@emphasizeToolSize,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=Presentation only: shared gray-to-red log curve and capped three-point size lift; idempotent writes keep native-row observers quiet.,
  // ]
  function emphasizeToolSize(element, value) {
    const known = Number.isFinite(value) && value >= 0;
    const capped = known ? Math.min(value, 1024 * 1024) : 0;
    const warmth = Math.log1p(capped / 16384) / Math.log1p(64);
    const lift = Math.max(0, Math.min(1, (capped - 256 * 1024) / (768 * 1024))) * 4;
    const style = `--tool-size-warmth:${(warmth * 100).toFixed(3)}%;--tool-size-lift:${lift.toFixed(3)}px`;
    if (element.getAttribute('style') !== style) element.setAttribute('style', style);
  }

  // @beacon[
  //   id=opc@toolSnapshotText,
  //   slice_labels=opc-pane,opc-codenomad-tools,
  //   kind=ast,
  //   comment=One raw-copy and UTF-8 size definition for both surfaces; retain wire JSON values and ordered V2 text/nontext blocks without numeric coercion.,
  // ]
  function toolSnapshotText(state, raw) {
    const status = state.status || 'unknown', pending = status === 'streaming' || status === 'pending';
    const hasInput = Object.hasOwn(state, 'input'), hasContent = Object.hasOwn(state, 'content');
    const hasOutput = hasContent || Object.hasOwn(state, 'output'), hasError = Object.hasOwn(state, 'error');
    const input = hasInput ? storedText(raw.get('input')) : '(input not available)';
    const storedOutput = hasContent ? (Array.isArray(state.content)
      ? [...rawMembers(raw.get('content')).values()].map((block, index) =>
        state.content[index]?.type === 'text' && typeof state.content[index].text === 'string'
          ? state.content[index].text : block).join('\n\n')
      : storedText(raw.get('content'))) : storedText(raw.get('output'));
    let output = hasOutput ? storedOutput : '(output not available)';
    if (hasError) output = `Error:\n${storedText(raw.get('error'))}` + (hasOutput ? `\n\nAvailable output:\n${output}` : '');
    const inputBytes = hasInput ? bytes(input) : null, outputBytes = hasOutput || hasError ? bytes(output) : null;
    return { input, output, status, pending, hasInput, hasOutput, hasError,
      inputBytes, outputBytes,
      inputLabel: hasInput ? `Input stored (${size(inputBytes)})${pending ? ` · ${status} fragment (not final)` : ''}` : `Input · ${pending ? status + ' fragment (not final) · ' : ''}missing`,
      outputLabel: hasError ? `Output stored (${size(outputBytes)}) · error` : !hasOutput ? `Output · ${pending || status === 'running' ? 'pending' : 'missing'}`
        : `Output stored (${size(outputBytes)})${storedOutput === '' ? (hasContent ? ' · empty content' : ' · empty string') : ''}` };
  }
  globalThis.opcToolText = Object.freeze({ valueEnd, rawMembers, storedText, bytes, size, SIZE_NOTE, toolSnapshotText, emphasizeToolSize });
})();
