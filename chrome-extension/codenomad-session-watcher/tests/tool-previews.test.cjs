const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../tool-previews.js'), 'utf8');
const shared = fs.readFileSync(path.join(__dirname, '../tool-record-text.js'), 'utf8');
const core = fs.readFileSync(path.join(__dirname, '../tool-preview-core.js'), 'utf8');

test('core is the sole crop/preview body and packaged scanner invokes it', async () => {
  assert.doesNotMatch(source, /function\s+(?:cropString|preview)\s*\(/);
  assert.match(source, /const \{ preview \} = opcToolPreview/);
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../manifest.json'), 'utf8'));
  const scripts = manifest.content_scripts[0].js;
  assert.ok(scripts.indexOf('tool-preview-core.js') < scripts.indexOf('tool-previews.js'));
  const rowOne = row('part');
  const h = harness([rowOne], () => record([['part', {params:{long:'x'.repeat(1000),short:1},verb:'read'}, 'mcp__workflowy__node']]));
  await h.tick();
  const preview = JSON.parse(rowOne.summary.label.textContent);
  assert.deepEqual(Object.keys(preview), ['verb','params']);
  assert.deepEqual(Object.keys(preview.params), ['short','long']);
  assert.equal(preview.params.long, 'x'.repeat(64) + '…' + 'x'.repeat(64));
});

test('pure core Unicode edges, native families, stable ties and immutable inputs', () => {
  const context = {TextEncoder}; vm.runInNewContext(core, context);
  for (const length of [64,128,129,256,200000]) {
    const text = '🌈'.repeat(length);
    assert.equal(context.opcToolPreview.cropString(text), length <= 128 ? text : '🌈'.repeat(64)+'…'+'🌈'.repeat(64));
  }
  for (const family of ['node','system','anchor','beacon','glimpse','lightning_rod','slice_group','tag']) {
    const input = {params:{large:'😀'.repeat(200),a:'aa',b:'bb',zero:0,nested:{large:1,small:0}},verb:'read'};
    const before = JSON.stringify(input), actual = JSON.parse(context.opcToolPreview.preview(input,'mcp__workflowy__'+family));
    assert.equal(JSON.stringify(input),before);
    assert.deepEqual(Object.keys(actual),['verb','params']);
    assert.deepEqual(Object.keys(actual.params).slice(0,3),['zero','a','b']);
    assert.deepEqual(Object.keys(actual.params.nested),['large','small']);
  }
  const many = Object.fromEntries(Array.from({length:400},(_,i)=>['key'+i,i]));
  assert.equal(Array.from(context.opcToolPreview.preview(many,'Bash')).length,1201);
  assert.match(context.opcToolPreview.preview({schemaVersion:1,status:'unavailable',reason:'opaque precision'},'Bash',{descriptor:true}), /preview unavailable: opaque precision/);
});

function row(partId, messageId = 'message', sessionId = 'session', instanceId = 'instance') {
  const classes = new Set();
  const summary = {
    label: null, status: {},
    classList: { add: value => classes.add(value), contains: value => classes.has(value) },
    querySelector(selector) { return selector === '.opc-tool-argument-preview' ? this.label : this.status; },
    insertBefore(label, status) { assert.equal(status, this.status); this.label = label; },
  };
  const header = { label: null, querySelector() { return this.label; }, append(label) { this.label = label; } };
  return {
    dataset: { partId, messageId, sessionId, instanceId }, summary, header,
    getClientRects: () => [1],
    getBoundingClientRect: () => ({top:0,bottom:40}),
    querySelector: selector => selector === '.tool-call-header' ? header : summary,
  };
}

function record(parts, completed = true, id = 'message') {
  return { data: { id, type: 'assistant', time: completed ? { created: 1, completed: 1 } : { created: 1 },
    content: parts.map(([id, input, name = 'bash', status = completed ? 'completed' : 'running']) => ({ id, type: 'tool', name,
      state: { status, input } })) } };
}

function harness(rows, fetcher) {
  let now = 0, timeout, interval, observer;
  const requests = [];
  const document = {
    hidden: false, documentElement: {},
    querySelectorAll: () => rows,
    createTextNode: text => ({textContent: text}),
    createElement: () => ({ content: '', childNodes: [], attributes: {}, append(node) { this.childNodes.push(node); },
      get textContent() { return this.childNodes.length ? this.childNodes.map(n => n.textContent).join('') : this.content; },
      set textContent(value) { this.content = value; this.childNodes = []; },
      setAttribute(k, v) { this.attributes[k] = v; }, getAttribute(k) { return this.attributes[k] ?? null; } }),
    addEventListener() {},
  };
  const context = {
    document, innerHeight: 1000, Date: { now: () => now }, AbortSignal, TextEncoder, TextDecoder, location: { origin: 'http://127.0.0.1:9899' },
    setTimeout: callback => { timeout = callback; return 1; },
    setInterval: callback => { interval = callback; },
    MutationObserver: class { constructor(callback) { observer = callback; } observe() {} },
    fetch: async (url, options) => {
      requests.push({ url, options });
      const body = await fetcher(url, requests.length);
      return new Response(typeof body === 'string' ? body : JSON.stringify(body));
    },
  };
  // Test-only visibility verifies originals remain retained; production exports no page globals.
  vm.runInNewContext(shared, context);
  vm.runInNewContext(core, context);
  vm.runInNewContext(source.replace('const cache = new Map();', 'const cache = new Map(); globalThis.testCache = cache;'), context);
  return {
    requests, document, cache: context.testCache,
    mutate: () => observer(),
    async tick(ms = 0) {
      now += ms;
      interval();
      for (let i = 0; i < 30; i++) {
        if (timeout) { const callback = timeout; timeout = null; callback(); }
        await Promise.resolve();
      }
    },
  };
}

test('row emphasis is independent per raw count and clears on growth/empty/unknown/reused identities', async () => {
  const rows = [row('part')]; let mode = 'heavy';
  const h = harness(rows, () => {
    if (mode === 'offline') throw new Error('offline');
    const data = record([['part', 'A😀é']], false);
    data.data.content[0].state.content = [{type:'text',text:mode === 'heavy' ? 'x'.repeat(1024*1024) : ''}];
    return data;
  });
  await h.tick();
  const labels = rows[0].header.label.childNodes;
  assert.match(labels[0].textContent, /0.007 KiB/);
  assert.match(labels[2].textContent, /1024.0 KiB/);
  assert.match(labels[2].attributes.style, /warmth:100.000%;--tool-size-lift:4.000px/);
  assert.notEqual(labels[0].attributes.style,labels[2].attributes.style);
  mode = 'empty'; await h.tick(2000);
  assert.equal(rows[0].header.label.childNodes[2],labels[2]);
  assert.equal(labels[2].textContent,'Out: 0 KiB'); assert.match(labels[2].attributes.style,/warmth:0.000%/);
  rows[0].dataset.partId = 'other'; h.mutate(); await h.tick();
  assert.equal(rows[0].header.label.textContent,'In: — · Out: —'); assert.match(labels[0].attributes.style,/warmth:0.000%/);
  rows[0].dataset.partId = 'part'; mode = 'offline'; await h.tick(2000);
  assert.equal(rows[0].header.label.textContent,'In: — · Out: —'); assert.match(labels[2].attributes.style,/warmth:0.000%/);
});

test('all tool rows in one message share one GET and preserve native controls', async () => {
  const rows = Array.from({ length: 25 }, (_, i) => row(`part-${i}`));
  const h = harness(rows, () => record(rows.map(r => [r.dataset.partId, { verb: 'read', path: 'file' }])));
  await h.tick();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, '/workspaces/instance/instance/api/session/session/message/message');
  assert.equal(h.requests[0].options.credentials, 'include');
  assert.equal(h.requests[0].options.method, undefined); // GET, never writes.
  for (const r of rows) assert.equal(r.summary.label.textContent, '{"verb":"read","path":"file"}');
  const label = rows[0].summary.label;
  await h.tick(20000);
  assert.equal(h.requests.length, 1);
  assert.equal(rows[0].summary.label, label);
});

test('pending inputs update, complete records cache, native replacement is annotated', async () => {
  const rows = [row('part')];
  const h = harness(rows, (_, call) => record([['part', call === 1 ? undefined : { command: 'pwd' }]], call > 1));
  await h.tick();
  assert.equal(rows[0].summary.label.textContent, '');
  await h.tick(2000);
  assert.equal(rows[0].summary.label.textContent, '{"command":"pwd"}');
  rows[0] = row('part');
  h.mutate();
  await h.tick(2000);
  assert.equal(rows[0].summary.label.textContent, '{"command":"pwd"}');
  assert.equal(h.requests.length, 2);
});

test('SDK streaming empty input recovers to completed point.capture with large original content', async () => {
  const input = { verb: 'point.capture', params: { parent_handle: '[ap:47K1C8]',
    name: '⚡ Worker Clause · QUICK WORKER — ' + 'long title '.repeat(20) + ' · rev 1',
    ap_id: 'auto', tags: ['pct', 'pct-manager'],
    content: '# ⚡ QUICK WORKER\n\nYou are a **quick worker** ' + 'large original content\n'.repeat(2000)
      + 'plus its handle: done, partial or failed.' } };
  const rows = [row('part')];
  const h = harness(rows, (_, call) => call === 1
    ? record([['part', '', 'workflowy_anchor', 'streaming']], false)
    : record([['part', input, 'workflowy_anchor']]));
  await h.tick();
  assert.equal(rows[0].summary.label.textContent, '');
  assert.equal(rows[0].summary.label.hidden, true);
  await h.tick(2000);
  const display = JSON.parse(rows[0].summary.label.textContent);
  assert.equal(display.verb, 'point.capture');
  assert.equal(display.params.parent_handle, '[ap:47K1C8]');
  assert.equal(Array.from(display.params.content).length, 129);
  assert.ok(display.params.content.endsWith('plus its handle: done, partial or failed.'));
   assert.equal(JSON.stringify([...h.cache.values()][0].parts.get('part').input), JSON.stringify(input));
  await h.tick(10000);
  assert.equal(h.requests.length, 2); // Stop downloading output once final inputs are known.
});

test('serialized streaming input stays pending; running rows refresh sizes as shared snapshots', async () => {
  const rows = [row('part')];
  const input = { command: 'pwd' };
  const h = harness(rows, (_, call) => record([['part', call === 1 ? '{"command":'
    : call === 2 ? JSON.stringify(input) : input, 'bash', call < 3 ? 'streaming' : 'running']], false));
  await h.tick();
  assert.equal(rows[0].summary.label.textContent, '');
  await h.tick(2000);
  assert.equal(rows[0].summary.label.textContent, '');
  await h.tick(2000);
  assert.equal(rows[0].summary.label.textContent, '{"command":"pwd"}');
  await h.tick(10000);
  assert.equal(h.requests.length, 4);
});

test('genuine final string inputs are not reinterpreted; running output size continues refreshing', async () => {
  const rows = [row('empty'), row('string'), row('json')];
  const h = harness(rows, () => record([['empty', '', 'custom_tool', 'running'],
    ['string', 'a genuine string', 'custom_tool', 'completed'],
    ['json', '{"verb":"read"}', 'workflowy_anchor', 'completed']], false));
  await h.tick();
  assert.equal(rows[0].summary.label.textContent, '""');
  assert.equal(rows[1].summary.label.textContent, '"a genuine string"');
  assert.equal(JSON.parse(rows[2].summary.label.textContent), '{"verb":"read"}');
  await h.tick(10000);
  assert.equal(h.requests.length, 2);
});

test('completed or aborted message with unfinished streaming input does not poll indefinitely', async () => {
  const rows = [row('part')];
  const h = harness(rows, () => record([['part', '', 'bash', 'streaming']], true));
  await h.tick();
  assert.equal(rows[0].summary.label.textContent, '');
  await h.tick(10000);
  assert.equal(h.requests.length, 1);
});

test('record association survives session/instance changes and row reuse', async () => {
  const rows = [row('part', 'message', 's/one', 'i one')];
  const h = harness(rows, url => record([['part', { url }]]));
  await h.tick();
  assert.match(h.requests[0].url, /i%20one\/instance\/api\/session\/s%2Fone/);
  rows[0].dataset.sessionId = 'other';
  await h.tick(2000);
  assert.match(rows[0].summary.label.textContent, /session\/other/);
  assert.equal(h.requests.length, 2);
});

test('running sizes refresh once per message; newly mounted parts share that cache', async () => {
  const rows = [row('part')];
  const h = harness(rows, (_, call) => record(call === 1 ? [['part', { command: 'pwd' }]]
    : [['part', { command: 'pwd' }], ['next', { verb: 'read' }]], false));
  await h.tick();
  await h.tick(10000);
  assert.equal(h.requests.length, 2);
  rows.push(row('next'));
  await h.tick();
  assert.equal(h.requests.length, 2);
  assert.equal(rows[1].summary.label.textContent, '{"verb":"read"}');
});

test('bounded safe JSON preview reads original input, not inline HTML', async () => {
  const rows = [row('part')];
  const h = harness(rows, () => record([['part', { verb: 'edit', note: '<img onerror=alert(1)>\n' + 'x'.repeat(12000) }]]));
  await h.tick();
  const label = rows[0].summary.label;
  assert.match(label.textContent, /^\{"verb":"edit"/);
  assert.match(label.textContent, /<img onerror=alert\(1\)>/);
  const input = JSON.parse(label.textContent);
  assert.equal(Array.from(input.note).length, 129);
  assert.ok(input.note.includes('…'));
  assert.ok(input.note.endsWith('x'.repeat(64)));
  assert.ok(!label.textContent.includes('\n'));
  assert.equal(label.innerHTML, undefined);
});

test('Workflowy params sort by original rendered lengths, stable ties, verb first; originals retained', async () => {
  const nested = Object.freeze({ z: 1, a: 2 });
  const params = Object.freeze({ longest: 'L'.repeat(500), shorter: 'S'.repeat(200),
    firstTie: 'equal', secondTie: '12345', number: 9, nested });
  const input = Object.freeze({ params, extra: true, verb: 'edit' });
  const before = JSON.stringify(input);
  const rows = [row('part')];
  const h = harness(rows, () => record([['part', input, 'workflowy_node']]));
  await h.tick();
  const display = JSON.parse(rows[0].summary.label.textContent);
  assert.equal(Object.keys(display)[0], 'verb');
  assert.deepEqual(Object.keys(display.params), ['number', 'firstTie', 'secondTie', 'nested', 'shorter', 'longest']);
  assert.deepEqual(Object.keys(display.params.nested), ['z', 'a']);
  assert.equal(display.params.shorter.length, 129);
  assert.equal(display.params.longest.length, 129); // Cropped lengths tie, originals do not.
  assert.equal(JSON.stringify(input), before);
  const retained = [...h.cache.values()][0].parts.get('part').input;
  assert.equal(JSON.stringify(retained), JSON.stringify(input));
  assert.equal(retained.params.longest.length, 500);
});

test('Workflowy family detection covers native and qualified names; other tools keep all key orders', async () => {
  const names = ['workflowy_node', 'functions.workflowy_anchor', 'mcp__workflowy__beacon',
    'workflowy.slice_group', 'workflowy_lightning_rod', 'bash', 'custom_node', 'notworkflowy_node'];
  const input = { params: { long: 'a long value', short: 1 }, verb: 'read' };
  const rows = names.map((_, i) => row(`part${i}`));
  const h = harness(rows, () => record(names.map((name, i) => [`part${i}`, input, name])));
  await h.tick();
  for (let i = 0; i < names.length; i++) {
    const display = JSON.parse(rows[i].summary.label.textContent);
    assert.deepEqual(Object.keys(display), i < 5 ? ['verb', 'params'] : ['params', 'verb'], names[i]);
    assert.deepEqual(Object.keys(display.params), i < 5 ? ['short', 'long'] : ['long', 'short'], names[i]);
  }
});

test('Workflowy ordering measures original JSON rendering, including escaped string characters', async () => {
  const input = { verb: 'edit', params: { escaped: '\n'.repeat(10), plain: 'a'.repeat(15), null: null, boolean: true } };
  const rows = [row('part')];
  const h = harness(rows, () => record([['part', input, 'workflowy_node']]));
  await h.tick();
  const display = JSON.parse(rows[0].summary.label.textContent);
  assert.deepEqual(Object.keys(display.params), ['null', 'boolean', 'plain', 'escaped']);
  assert.equal(display.params.escaped, input.params.escaped);
});

test('string crops preserve first/last 64 Unicode code points, short strings and nested key order', async () => {
  const unicode = '😀'.repeat(64) + 'discard me' + '🚀'.repeat(64);
  const input = { exact: '😀'.repeat(128), longer: 'a'.repeat(64) + 'x' + 'b'.repeat(64),
    nested: { z: unicode, a: ['short', unicode] } };
  const rows = [row('part')];
  const h = harness(rows, () => record([['part', input, 'bash']]));
  await h.tick();
  const display = JSON.parse(rows[0].summary.label.textContent);
  assert.equal(display.exact, input.exact);
  assert.equal(display.longer, 'a'.repeat(64) + '…' + 'b'.repeat(64));
  assert.equal(display.nested.z, '😀'.repeat(64) + '…' + '🚀'.repeat(64));
  assert.equal(Array.from(display.nested.z).length, 129);
  assert.equal(display.nested.a[0], 'short');
  assert.equal(display.nested.a[1], display.nested.z);
  assert.deepEqual(Object.keys(display.nested), ['z', 'a']);
  assert.equal([...h.cache.values()][0].parts.get('part').input.nested.z, unicode);
});

test('overall previews remain bounded after per-string crops without splitting surrogate pairs', async () => {
  const input = Object.fromEntries(Array.from({ length: 100 }, (_, i) => [`field${i}`, '😀'.repeat(100)]));
  const rows = [row('part')];
  const h = harness(rows, () => record([['part', input]]));
  await h.tick();
  const text = rows[0].summary.label.textContent;
  assert.equal(Array.from(text).length, 1201);
  assert.ok(text.endsWith('…'));
  assert.ok(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(text));
});

test('failures are throttled, retry and wrong-message data never gets associated', async () => {
  const rows = [row('part')];
  const h = harness(rows, (_, call) => {
    if (call === 1) throw new Error('offline');
    return record([['part', { ok: true }]], true, call === 2 ? 'wrong-message' : 'message');
  });
  await h.tick();
  assert.equal(rows[0].summary.label.textContent, 'arguments unavailable');
  await h.tick(14000);
  assert.equal(h.requests.length, 1);
  await h.tick(1000);
  assert.equal(rows[0].summary.label.textContent, 'arguments unavailable');
  await h.tick(15000);
  assert.equal(rows[0].summary.label.textContent, '{"ok":true}');
});

test('reads cap concurrency, drain queued messages, and skip hidden pages', async () => {
  const rows = Array.from({ length: 5 }, (_, i) => row('part', `m${i}`));
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const h = harness(rows, async url => { await gate; return record([['part', {}]], true, url.split('/').pop()); });
  await h.tick();
  assert.equal(h.requests.length, 3);
  release();
  await h.tick();
  assert.equal(h.requests.length, 5);
  h.document.hidden = true;
  rows.push(row('part', 'm5'));
  await h.tick(3000);
  assert.equal(h.requests.length, 5);
  h.document.hidden = false;
  await h.tick();
  assert.equal(h.requests.length, 6);
});

test('unselected row sizes use lossless raw UTF-8 wire values, ordered V2 blocks, never cropped previews', async () => {
  const rows = [row('part'), row('next')];
  const input = String.raw`{"n":900719925474099312345,"doc":"汉😀\n","n":-0,"exp":1e9999}`;
  const image = '{"type":"image","n":900719925474099312345,"data":"🚀"}';
  const wire = `{"data":{"id":"message","sessionID":"session","time":{"created":123,"completed":456},"content":[{"id":"part","type":"tool","name":"bash","callID":"call","state":{"status":"completed","input":${input},"content":[{"type":"text","text":"汉😀\\n"},${image}]}},{"id":"next","type":"tool","name":"bash","state":{"status":"completed","input":"","content":[]}}]}}`;
  const h = harness(rows, () => wire); await h.tick();
  const parts = [...h.cache.values()][0].parts;
  assert.equal(parts.get('part').inputBytes, Buffer.byteLength(input));
  assert.equal(parts.get('part').outputBytes, Buffer.byteLength('汉😀\n\n\n' + image));
  assert.equal(parts.get('part').messageCreated, 123); assert.equal(parts.get('part').callId, 'call');
  assert.equal(parts.get('next').inputBytes, 0); assert.equal(parts.get('next').outputBytes, 0);
  assert.equal(rows[1].header.label.textContent, 'In: 0 KiB · Out: 0 KiB');
  assert.ok(!Object.hasOwn(parts.get('part'), 'output'));
  assert.match(rows[0].header.label.attributes.title, /raw copy snapshot/);
});

test('live output growth updates both rows in one shared read; remount/reuse/stale response never mislabels another record', async () => {
  const rows = [row('part'), row('next')]; let release;
  const h = harness(rows, (_, call) => {
    const result = record([['part', {}, 'bash', call >= 3 ? 'completed' : 'running'], ['next', '😀', 'bash', 'completed']], call >= 3);
    result.data.content[0].state.content = [{ type: 'text', text: 'x'.repeat(call * 1024) }];
    return call === 2 ? new Promise(resolve => { release = () => resolve(result); }) : result;
  });
  await h.tick(); assert.match(rows[0].header.label.textContent, /Out: 1.0 KiB/);
  rows[0] = row('part'); await h.tick(2000); assert.match(rows[0].header.label.textContent, /Out: 1.0 KiB/);
  rows[0].dataset.sessionId = 'other'; await h.tick();
  assert.match(rows[0].header.label.textContent, /Out: 3.0 KiB/);
  release(); await h.tick(); assert.match(rows[0].header.label.textContent, /Out: 3.0 KiB/);
  assert.match(rows[1].header.label.textContent, /In: 0.004 KiB/);
  const n = h.requests.length; await h.tick(10000); assert.equal(h.requests.length, n);
});

test('unavailable/mismatched/ambiguous/oversized parts never fabricate zero, or block other rows', async () => {
  const rows = [row('part'), row('next'), row('missing')];
  const payload = record([['part', {}, 'bash'], ['next', '', 'bash']]);
  payload.data.content[0].state.output = 'x'.repeat(8 * 1024 * 1024);
  payload.data.content[1].state.content = [];
  const h = harness(rows, () => payload); await h.tick();
  assert.equal(rows[0].header.label.textContent, 'In: — · Out: —');
  assert.match(rows[0].header.label.attributes.title, /8 MiB part limit/);
  assert.equal(rows[1].header.label.textContent, 'In: 0 KiB · Out: 0 KiB');
  assert.equal(rows[2].header.label.textContent, 'In: — · Out: —');
  const n = h.requests.length; await h.tick(100000); assert.equal(h.requests.length, n);
  const duplicates = record([['part', {}, 'bash'], ['part', { different: true }, 'bash']]);
  const d = harness([row('part')], () => duplicates); await d.tick();
  assert.match([...d.cache.values()][0].parts.get('part').error, /ambiguous/);
  const wrong = harness([row('part')], () => ({ ...record([['part', {}]]), data: { ...record([['part', {}]]).data, sessionID: 'wrong' } })); await wrong.tick();
  assert.equal(wrong.cache.values().next().value.failed, true);
});

test('16 MiB stream bound blocks repeated oversized downloads; cache budgets are global and histories are never fetched', async () => {
  const rows = [row('part')], h = harness(rows, () => ' '.repeat(16 * 1024 * 1024 + 1)); await h.tick();
  assert.equal(h.cache.values().next().value.blocked, true); await h.tick(100000); assert.equal(h.requests.length, 1);
  assert.equal(rows[0].header.label.textContent, 'In: — · Out: —');
  const many = Array.from({length:125}, (_, i) => row('part', 'm' + i));
  const b = harness(many, url => record([['part', '😀'.repeat(16000)], ['second', '😀'.repeat(16000)]], true, url.split('/').pop()));
  for (let n = 0; n < 50; n++) await b.tick();
  assert.equal(b.cache.size, 120);
  assert.ok([...b.cache.values()].reduce((sum, entry) => sum + entry.bytes, 0) <= 8 * 1024 * 1024);
  assert.match(many[124].header.label.attributes.title, /cache limit/);
  assert.ok(b.requests.every(({url}) => /\/message\/m\d+$/.test(url)));
  many.splice(0, 120); await b.tick(15000);
  assert.ok(many.every(item => item.header.label.textContent.includes('KiB')));
});

test('invalid or tool-mismatched recycled rows clear previous counts and previews before any new request', async () => {
  const rows = [row('part')], h = harness(rows, () => record([['part', {}, 'bash']])); await h.tick();
  assert.match(rows[0].header.label.textContent, /KiB/);
  delete rows[0].dataset.sessionId; h.mutate(); await h.tick();
  assert.equal(rows[0].header.label.textContent, 'In: — · Out: —'); assert.match(rows[0].header.label.attributes.title, /identity unavailable/);
  assert.equal(h.requests.length, 1);
  rows[0].dataset.sessionId = 'session'; rows[0].dataset.toolName = 'different'; await h.tick();
  assert.equal(rows[0].header.label.textContent, 'In: — · Out: —'); assert.equal(rows[0].summary.label.textContent, '');
  assert.match(rows[0].header.label.attributes.title, /tool identity mismatch/);
});

test('cached immutable message creation/tool call witnesses reject same-ID replacement instead of publishing its sizes', async () => {
  for (const changed of ['message', 'call']) {
    const rows = [row('part')];
    const h = harness(rows, (_, n) => {
      const value = record([['part', {}, 'bash', 'running']], false);
      value.data.content[0].callID = changed === 'call' && n > 1 ? 'different-call' : 'original-call';
      if (changed === 'message' && n > 1) value.data.time.created = 2;
      value.data.content[0].state.output = 'x'.repeat(n * 1024); return value;
    });
    await h.tick(); assert.match(rows[0].header.label.textContent, /Out: 1.0 KiB/);
    await h.tick(2000); assert.equal(rows[0].header.label.textContent, 'In: — · Out: —');
    assert.match(rows[0].header.label.attributes.title, /witness changed/);
  }
});

test('offscreen mounted history is not inventoried; entering the viewport uses bounded shared reads', async () => {
  const offscreen = row('part', 'later'), rows = [row('part'), offscreen];
  offscreen.getBoundingClientRect = () => ({top:2000,bottom:2040});
  const h = harness(rows, url => record([['part', {}]], true, url.split('/').pop())); await h.tick();
  assert.equal(h.requests.length, 1); assert.equal(offscreen.header.label, null);
  offscreen.getBoundingClientRect = () => ({top:500,bottom:540}); await h.tick(2000);
  assert.equal(h.requests.length, 2); assert.match(offscreen.header.label.textContent, /KiB/);
});
