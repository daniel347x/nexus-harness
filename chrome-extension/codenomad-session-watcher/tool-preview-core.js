// One pure preview renderer, shared by the packaged scanner and /sessions.
(function () {
  'use strict';
  const MAX_PREVIEW = 1200, MAX_NODES = 2048, MAX_DEPTH = 32, MAX_DATA = 64 * 1024;
  const workflowyFamily = /(?:^|[.:/_-])workflowy[.:/_-]+(?:node|anchor|beacon|glimpse|lightning_rod|slice_group|system|tag)$/i;

  function cropString(value) {
    if (value.length <= 128) return value;
    const head = [];
    for (const character of value) { head.push(character); if (head.length === 129) break; }
    if (head.length <= 128) return value;
    let end = value.length, count = 0;
    while (end > 0 && count++ < 64) {
      end--;
      const code = value.charCodeAt(end);
      if (code >= 0xdc00 && code <= 0xdfff && end > 0) {
        const previous = value.charCodeAt(end - 1);
        if (previous >= 0xd800 && previous <= 0xdbff) end--;
      }
    }
    return head.slice(0, 64).join('') + '…' + value.slice(end);
  }

  function bounded(text) {
    const characters = Array.from(text.replace(/\s+/g, ' '));
    return characters.slice(0, MAX_PREVIEW).join('') + (characters.length > MAX_PREVIEW ? '…' : '');
  }

  function descriptorValue(data) {
    if (!data || data.schemaVersion !== 1 || data.status !== 'available') throw new Error(data?.reason || 'input unavailable');
    if (new TextEncoder().encode(JSON.stringify(data)).length > MAX_DATA) throw new Error('descriptor byte limit');
    let nodes = 0;
    function read(node, depth = 0) {
      if (++nodes > MAX_NODES || depth > MAX_DEPTH || !node || typeof node !== 'object') throw new Error('descriptor structure limit');
      if (node.kind === 'string') {
        if (!Number.isSafeInteger(node.characters) || node.characters < 0 || !Number.isSafeInteger(node.jsonCharacters) ||
            node.jsonCharacters < node.characters + 2 || typeof node.head !== 'string' || typeof node.tail !== 'string' ||
            Array.from(node.head).length !== Math.min(128, node.characters) || Array.from(node.tail).length !== Math.min(128, node.characters)) throw new Error('invalid string descriptor');
        // Two TRANSPORT edges, fed into the same cropString as scanner values.
        return { value: node.characters <= 128 ? node.head : node.head + node.tail, length: node.jsonCharacters };
      }
      if (node.kind === 'number') {
        if (typeof node.lexeme !== 'string' || node.lexeme.length > 128 || !/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/.test(node.lexeme)) throw new Error('invalid numeric descriptor');
        const value = Number(node.lexeme);
        if (!Number.isFinite(value) || (/^-?\d+$/.test(node.lexeme) && !Number.isSafeInteger(value))) throw new Error('opaque numeric precision');
        return { value, length: Array.from(JSON.stringify(value)).length };
      }
      if (['true', 'false', 'null'].includes(node.kind)) {
        const value = node.kind === 'null' ? null : node.kind === 'true';
        return { value, length: JSON.stringify(value).length };
      }
      if (node.kind === 'array' && Array.isArray(node.items)) {
        const items = node.items.map(child => read(child, depth + 1));
        return { value: items.map(item => item.value), length: 2 + Math.max(0, items.length - 1) + items.reduce((n, item) => n + item.length, 0) };
      }
      if (node.kind === 'object' && Array.isArray(node.entries)) {
        const value = Object.create(null), lengths = new Map(); let length = 2;
        for (const entry of node.entries) {
          if (!Array.isArray(entry) || entry.length !== 2 || typeof entry[0] !== 'string' || lengths.has(entry[0])) throw new Error('invalid object descriptor');
          const child = read(entry[1], depth + 1), key = entry[0];
          value[key] = child.value; lengths.set(key, child.length);
          length += Array.from(JSON.stringify(key)).length + 1 + child.length;
        }
        length += Math.max(0, lengths.size - 1);
        return { value, length, lengths };
      }
      throw new Error('unsupported preview descriptor');
    }
    const root = read(data.value);
    function originalLength(node) {
      if (node.kind === 'string') return node.jsonCharacters;
      if (node.kind === 'number') return JSON.stringify(Number(node.lexeme)).length;
      if (node.kind === 'array') return 2 + Math.max(0, node.items.length - 1) + node.items.reduce((n, child) => n + originalLength(child), 0);
      if (node.kind === 'object') return 2 + Math.max(0, node.entries.length - 1) + node.entries.reduce((n, [key, child]) => n + Array.from(JSON.stringify(key)).length + 1 + originalLength(child), 0);
      return node.kind.length;
    }
    if (data.value.kind === 'object') {
      const params = data.value.entries.find(([key]) => key === 'params');
      if (params?.[1].kind === 'object') root.paramsLengths = new Map(params[1].entries.map(([key, child]) => [key, originalLength(child)]));
    }
    return root;
  }

  // @beacon[
  //   id=opc-codenomad-tools@preview-core,
  //   slice_labels=opc-codenomad-tools,opc-pane,
  //   kind=ast,
  //   comment=One pure renderer for native bounded descriptors and scanner values: original-length Workflowy ordering, Unicode string edges and bounded presentation; never raw Copy authority.,
  // ]
  function preview(input, toolName, options = {}) {
    if (input === undefined) return '';
    try {
      const descriptor = options.descriptor ? descriptorValue(input) : null;
      if (descriptor) input = descriptor.value;
      let display = input;
      if (workflowyFamily.test(toolName || '') && input && typeof input === 'object' && !Array.isArray(input)) {
        const params = input.params;
        display = { ...(Object.hasOwn(input, 'verb') ? { verb: input.verb } : {}), ...input };
        if (params && typeof params === 'object' && !Array.isArray(params)) {
          const entries = Object.entries(params).map(([key, value], index) => ({ key, value, index,
            length: descriptor?.paramsLengths?.get(key) ?? Array.from(JSON.stringify(value) ?? '').length }));
          entries.sort((a, b) => a.length - b.length || a.index - b.index);
          display.params = Object.fromEntries(entries.map(({ key, value }) => [key, value]));
        }
      }
      const text = JSON.stringify(display, (_, value) => typeof value === 'string' ? cropString(value) : value);
      return text ? bounded(text) : '';
    } catch (error) {
      return bounded(`[preview unavailable: ${String(error.message || 'unsupported input').slice(0, 128)}]`);
    }
  }

  globalThis.opcToolPreview = Object.freeze({ preview, cropString });
})();
