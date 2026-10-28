const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/tool-inspector.js'), 'utf8');
const readingSource = fs.readFileSync(path.join(__dirname, '../static/tool-reading.js'), 'utf8');
const shared = fs.readFileSync(path.join(__dirname, '../../chrome-extension/codenomad-session-watcher/tool-record-text.js'), 'utf8');
const selection = (partId = 'prt_a') => ({ origin: 'http://127.0.0.1:9899', instanceId: 'workspace',
  sessionId: 'ses_original', messageId: 'msg_original', partId, toolName: 'bash', sessionTitle: 'Original' });
const mail = (revision = 1, partId = 'prt_a', epoch = 'epoch') => ({ epoch, revision, selection: selection(partId) });
const record = (state, revision = 1, partId = 'prt_a') => ({ epoch: 'epoch', revision,
  part: { id: partId, type: 'tool', name: 'bash', state }, messageCompleted: false, fetchedAt: 123 });

function fixture() {
  let now = 10000, range = null, mailbox = mail(), reads = [], nextResponse = record({ status: 'running', input: { command: 'pwd' } });
  let hook = null, failClipboard = false, wire = null, artifactResponse = null, artifactHook = null;
  const elements = new Map(), listeners = new Map(), copies = [], requests = [], timers = new Map(); let timerId = 0;
  function textNode(text = '') {
    const node = { children: [], attributes: {}, append(...nodes) { this.children.push(...nodes); },
      replaceChildren(...nodes) { this.children = nodes.length === 1 && nodes[0].fragment ? nodes[0].children : nodes; }, getAttribute(k) { return this.attributes[k] ?? null; }, setAttribute(k, v) { this.attributes[k] = v; } };
    let content = text;
    Object.defineProperty(node, 'textContent', { get() { return this.children.length ? this.children.map(n => n.textContent).join('') : content; }, set(value) { content = value; this.children = []; } });
    return node;
  }
  const el = id => {
    if (!elements.has(id)) {
      const classes = new Set(), events = new Map();
      elements.set(id, Object.assign(textNode(), { id, hidden: false, scrollTop: 0, scrollLeft: 0,
        attributes: {}, events, focusCount: 0, style: { values: {}, setProperty(k, v) { this.values[k] = v; } },
        classList: { toggle(k, v) { v ? classes.add(k) : classes.delete(k); }, contains: k => classes.has(k) },
        addEventListener: (k, f) => events.set(k, f), setAttribute(k, v) { this.attributes[k] = v; },
        contains: node => node?.owner === id, getBoundingClientRect: () => ({ height: 400 }),
        setPointerCapture() {}, focus() { this.focusCount++; },
      }));
    }
    return elements.get(id);
  };
  const ctx = { document: { hidden: false, getElementById: el, getSelection: () => range,
    createDocumentFragment: () => Object.assign(textNode(), {fragment: true}), createElement: () => textNode(), createTextNode: textNode,
    addEventListener: (k, f) => listeners.set(k, f) },
    AbortSignal, AbortController, TextEncoder, TextDecoder, URLSearchParams,
    Date: class extends Date { static now() { return now; } },
    setTimeout: f => { timers.set(++timerId, f); return timerId; }, clearTimeout: id => timers.delete(id), setInterval() {},
    navigator: { clipboard: { writeText: async text => { if (failClipboard) throw new Error('denied'); copies.push(text); } } },
    fetch: async (url, opts) => {
      requests.push([url, opts]);
      let data;
      if (url === '/api/tool-selection') data = mailbox;
      else if (url.startsWith('/api/tool-record')) { reads.push(url); data = hook ? await hook(url, opts) : nextResponse; }
      else if (url === '/api/tool-selection/clear') {
        const { epoch, revision } = JSON.parse(opts.body);
        if (mailbox.epoch === epoch && mailbox.revision === revision) mailbox = { epoch, revision: revision + 1, selection: null };
        data = { ok: true };
      } else if (url.startsWith('/api/claude/artifact?')) data = artifactHook ? await artifactHook(url, opts) : artifactResponse;
      else throw new Error('Unexpected route: ' + url);
      const bytes = new TextEncoder().encode(wire && url.startsWith('/api/tool-record') ? wire : JSON.stringify(data));
      const body = new ReadableStream({ start(c) { c.enqueue(bytes); c.close(); } });
      return { ok: !data?.error, status: data?.error ? data.status || 502 : 200, body };
    },
  };
  // No production page globals: expose closure state only in this isolated fixture.
  // The shared reading view (prettyJSON, markdownLike, readingView, numberedNote …) is part of the API.
  const code = source.replace('  void pollInspection();\n})();', `  globalThis.api = { ...opcToolReading, pollInspection, selectInspection, readInspection, render,
      rawMembers, readArtifact,
     state: () => ({ selected, snapshot, terminal, disconnected, generation, nextRead, polling, cursor }) };\n})();`);
   vm.runInNewContext(shared, ctx);
   vm.runInNewContext(readingSource, ctx);
   vm.runInNewContext(code, ctx);
  return { api: ctx.api, ctx, el, listeners, copies, requests, reads, timers,
    wire: value => { wire = value; },
    artifact: value => { artifactResponse = value; }, artifactHook: value => { artifactHook = value; },
    mailbox: value => { mailbox = value; }, response: value => { nextResponse = value; wire = null; }, hook: value => { hook = value; },
    advance: (ms = 2100) => { now += ms; }, range: value => { range = value; }, denyClipboard: () => { failClipboard = true; } };
}

function nativeMail(revision = 1) {
  const identity = {storeRef:'store',sessionRef:'session',scopeRef:'scope',leafRef:'leaf',recordRef:'record',
    sessionUuid:'native-uuid',scope:'main',leafUuid:'leaf-uuid',recordUuid:'record-uuid',recordHash:'hash',
    blockIndex:0,callId:'native-call',callRef:'call-ref',fileEpoch:'file-epoch'};
  return {epoch:'epoch',revision,selection:{schemaVersion:1,backend:'claude',identity,
    callRef:identity.callRef,fileEpoch:identity.fileEpoch,toolName:'mcp__workflowy__anchor',
    sessionTitle:'Native',workspaceName:'Unknown',source:'windows-history'}};
}
function nativeRecord(input, output, revision = 1) {
  const m = nativeMail(revision), count = text => text === null ? null : new TextEncoder().encode(text).length;
  return {schemaVersion:1,backend:'claude',epoch:m.epoch,revision,selection:m.selection,identity:m.selection.identity,
    fetchedAt:123,provenance:'recorded-jsonl',snapshot:{schemaVersion:1,backend:'claude',callRef:'call-ref',fileEpoch:'file-epoch',
      provenance:'recorded-jsonl',input,output,inputBytes:count(input),outputBytes:count(output),
      inputAvailability:input === null ? 'missing' : 'stored',outputAvailability:output === null ? 'pending-or-missing' : 'stored',
      status:output === null ? 'pending-or-missing' : 'stored',artifactRef:null}};
}

test('native uses ONE mounted inspector with exact lexical raw under every pretty/readable/wrap combination', async () => {
  const f = fixture(), raw = '{"n":900719925474099312345,"n":-0,"e":1e+09,"doc":"line\\n\\tsecond 😀"}\nTAIL';
  f.mailbox(nativeMail()); f.response(nativeRecord(raw, '<img src=x>\n' + '🌈'.repeat(200000)));
  await f.api.pollInspection();
  assert.match(f.el('inspector-origin').textContent,/Claude.*native-uuid/);
  assert.ok(!f.el('inspector-origin').textContent.includes('undefined'));
  assert.equal(f.api.state().terminal,true); assert.equal(f.api.state().snapshot.input,raw);
  assert.ok(f.el('inspector-output').textContent.endsWith('🌈'.repeat(200000)));
  assert.equal(f.el('inspector-input').focusCount,0);
  const count = f.api.state().snapshot.inputBytes, reads = f.reads.length;
  for (const pretty of [false,true]) for (const readable of [false,true]) for (const wrap of [false,true]) {
    for (const kind of ['input','output']) {
      f.el('inspector-pretty-'+kind).checked=pretty; f.el('inspector-pretty-'+kind).onchange();
      f.el('inspector-readable-'+kind).checked=readable; f.el('inspector-readable-'+kind).onchange();
      f.el('inspector-wrap-'+kind).checked=wrap; f.el('inspector-wrap-'+kind).onchange();
      await f.el('inspector-copy-'+kind).onclick();
    }
    assert.equal(f.copies.at(-2),raw); assert.equal(f.copies.at(-1),f.api.state().snapshot.output);
    assert.equal(f.api.state().snapshot.inputBytes,count);
  }
  f.advance(100000); await f.api.pollInspection(); assert.equal(f.reads.length,reads);
});

test('native missing is unknown, not zero/copyable placeholders or evidence of running; empty is stored zero', async () => {
  const f = fixture(); f.mailbox(nativeMail()); f.response(nativeRecord('',null)); await f.api.pollInspection();
  assert.equal(f.api.state().snapshot.inputBytes,0); assert.equal(f.api.state().snapshot.outputBytes,null);
  assert.match(f.el('inspector-output-label').textContent,/pending-or-missing.*—/);
  assert.equal(f.el('inspector-copy-output').disabled,true);
  await f.el('inspector-copy-output').onclick(); assert.equal(f.copies.length,0);
  await f.el('inspector-copy-input').onclick(); assert.deepEqual(f.copies,['']);
  assert.equal(f.api.state().terminal,true);
});

function hostRecord(input, output, {terminal=false,inputFinal=false,live=true}={}) {
  const data=nativeRecord(input,output);
  data.selection.source='host'; data.provenance=data.snapshot.provenance='host-durable-feed';
  Object.assign(data.snapshot,{live,terminal,inputFinal});
  return data;
}
test('only explicit host-live selected call polls partial→final, preserves selection/scroll, then stops',async()=>{
  const f=fixture(), data=hostRecord('{"doc":"par',null);
  f.mailbox({...nativeMail(),selection:data.selection});f.response(data);await f.api.pollInspection();
  assert.equal(f.api.state().terminal,false);assert.match(f.el('inspector-input-label').textContent,/not-final fragment/);
  const input=f.el('inspector-input'),output=f.el('inspector-output');input.scrollTop=31;output.scrollTop=42;
  f.range({isCollapsed:false,anchorNode:{owner:input.id}});
  const exact='{"n":900719925474099312345,"doc":"real\\nnewline 😀"}';
  f.response(hostRecord(exact,'<saved offload stub>',{terminal:true,inputFinal:true}));f.advance();await f.api.pollInspection();
  assert.equal(input.textContent,'{"doc":"par');assert.equal(input.scrollTop,31);assert.equal(output.scrollTop,42);
  await f.el('inspector-copy-input').onclick();assert.equal(f.copies.at(-1),exact);
  assert.equal(f.api.state().terminal,true);f.range(null);f.api.render();assert.ok(input.textContent.includes('900719925474099312345'));
  const reads=f.reads.length;f.advance(50000);await f.api.pollInspection();assert.equal(f.reads.length,reads);
  assert.equal(f.api.state().selected.callRef,'call-ref');assert.equal(f.el('inspector-input').focusCount,0);
});
test('host inspector refuses missing honest liveness/provenance and never infers polling from absent output',async()=>{
  for(const change of [d=>delete d.snapshot.terminal,d=>delete d.snapshot.inputFinal,d=>delete d.snapshot.live,
    d=>d.snapshot.provenance='recorded-jsonl']) {
    const f=fixture(), d=hostRecord('input',null);change(d);f.mailbox({...nativeMail(),selection:d.selection});f.response(d);
    await f.api.pollInspection();assert.equal(f.api.state().snapshot,null);assert.equal(f.api.state().terminal,true);
  }
  const f=fixture(), d=hostRecord('input',null,{live:false,terminal:false,inputFinal:true});
  f.mailbox({...nativeMail(),selection:d.selection});f.response(d);await f.api.pollInspection();assert.equal(f.api.state().terminal,true);
});

test('native complete backend/record/block/call/FileEpoch/source/count discriminators reject wrong-adapter responses', async () => {
  for (const mutate of [d=>d.backend='opencode', d=>d.identity={...d.identity,recordHash:'changed'},
    d=>d.identity={...d.identity,blockIndex:2}, d=>d.snapshot.fileEpoch='new-epoch',
    d=>d.snapshot.callRef='other', d=>d.snapshot.inputBytes=0, d=>d.snapshot.provenance='preview']) {
    const f = fixture(), data = nativeRecord('stored','output'); mutate(data);
    f.mailbox(nativeMail()); f.response(data); await f.api.pollInspection();
    assert.equal(f.api.state().snapshot,null); assert.equal(f.api.state().terminal,true);
    assert.match(f.el('inspector-note').textContent,/changed|Invalid/);
  }
  const f = fixture(); f.response(nativeRecord('wrong backend','out')); await f.api.pollInspection();
  assert.equal(f.api.state().snapshot,null); assert.equal(f.api.state().terminal,true);
});

test('native delayed read cannot repaint after a newer native or another-backend selection; both orderings', async () => {
  for (const action of ['native','opencode']) {
    const f = fixture(); let resolve;
    f.mailbox(nativeMail()); f.hook(()=>new Promise(r=>resolve=r));
    const pending = f.api.pollInspection(); await new Promise(setImmediate);
    f.api.selectInspection(action==='native' ? nativeMail(2) : mail(2));
    resolve(nativeRecord('late native','late output')); await pending;
    assert.equal(f.api.state().snapshot,null); assert.ok(!f.el('inspector-input').textContent.includes('late native'));
    assert.equal(f.el('tool-inspector').classList.contains('held'),true);
  }
  const f = fixture(); f.mailbox(nativeMail()); f.response(nativeRecord('first','output')); await f.api.pollInspection();
  f.api.selectInspection(mail(2)); assert.equal(f.api.state().snapshot,null);
  f.response(record({status:'completed',input:'OpenCode kept',output:'OC'},2)); await f.api.readInspection();
  assert.equal(f.api.state().snapshot.input,'OpenCode kept');
});

test('native snapshot updates defer active selections while Copy uses NEW exact held raw and scrollers remain mounted', async () => {
  const f=fixture(); f.mailbox(nativeMail()); f.response(nativeRecord('first','old output')); await f.api.pollInspection();
  const input=f.el('inspector-input'), output=f.el('inspector-output'); input.scrollTop=35; output.scrollTop=66;
  f.range({isCollapsed:false,anchorNode:{owner:output.id}});
  f.response(nativeRecord('first','new output')); f.el('inspector-retry').onclick(); await new Promise(setImmediate);
  assert.equal(output.textContent,'old output'); assert.equal(output.scrollTop,66); assert.equal(input.scrollTop,35);
  await f.el('inspector-copy-output').onclick(); assert.equal(f.copies.at(-1),'new output');
  f.range(null); f.api.render(); assert.equal(output.textContent,'new output');
});

test('saved artifact is explicit separate raw/count/view with stale-generation guards and never substitutes recorded Copy', async () => {
  const f=fixture(), data=nativeRecord('input','recorded stub'); data.snapshot.artifactRef='artifact-ref';
  f.mailbox(nativeMail()); f.response(data); await f.api.pollInspection();
  assert.equal(f.requests.some(([url])=>url.startsWith('/api/claude/artifact')),false);
  f.artifact({schemaVersion:1,backend:'claude',artifactRef:'artifact-ref',callRef:'call-ref',fileEpoch:'file-epoch',
    text:'saved 🌈',bytes:10,availability:'stored',completeness:'limited'});
  await f.api.readArtifact();
  assert.equal(f.el('inspector-artifact-text').textContent,'saved 🌈');
  assert.match(f.el('inspector-artifact-label').textContent,/limited.*recorded result unchanged/);
  await f.el('inspector-artifact-copy').onclick(); assert.equal(f.copies.at(-1),'saved 🌈');
  await f.el('inspector-copy-output').onclick(); assert.equal(f.copies.at(-1),'recorded stub');
  let resolve; f.artifactHook(()=>new Promise(r=>resolve=r));
  const pending=f.api.readArtifact(); await new Promise(setImmediate); f.api.selectInspection(nativeMail(2));
  resolve({schemaVersion:1,backend:'claude',artifactRef:'artifact-ref',callRef:'call-ref',fileEpoch:'file-epoch',
    text:'late',bytes:4,availability:'stored',completeness:'full'}); await pending;
  assert.equal(f.el('inspector-artifact').hidden,true); assert.equal(f.el('inspector-artifact-text').textContent,'');
});

test('Word wrap is independent, CSS-only with active selections, page-local and retained across calls', async () => {
  const f = fixture(); f.response(record({status:'completed',input:'{"doc":"first\\nsecond"}',output:'line1\nline2'}));
  await f.api.pollInspection();
  const input = f.el('inspector-input'), output = f.el('inspector-output');
  assert.equal(f.el('inspector-wrap-input').checked,true); assert.equal(f.el('inspector-wrap-output').checked,true);
  const text = input.textContent, node = input.children, reads = f.reads.length;
  input.scrollTop = 31; output.scrollTop = 72;
  f.range({isCollapsed:false,anchorNode:{owner:input.id}});
  f.el('inspector-wrap-input').checked = false; f.el('inspector-wrap-input').onchange();
  assert.ok(input.classList.contains('inspector-nowrap')); assert.ok(!output.classList.contains('inspector-nowrap'));
  assert.equal(input.children,node); assert.equal(input.textContent,text);
  assert.equal(input.scrollTop,31); assert.equal(output.scrollTop,72); assert.equal(f.reads.length,reads);
  for (const mode of ['raw','pretty','readable']) {
    f.range(null); f.el('inspector-pretty-input').checked = mode !== 'raw'; f.el('inspector-pretty-input').onchange();
    f.el('inspector-readable-input').checked = mode === 'readable'; f.el('inspector-readable-input').onchange();
    assert.ok(input.classList.contains('inspector-nowrap'));
    await f.el('inspector-copy-input').onclick(); assert.equal(f.copies.at(-1),'{"doc":"first\\nsecond"}');
  }
  f.api.selectInspection(mail(2,'prt_b')); assert.ok(input.classList.contains('inspector-nowrap'));
  f.api.selectInspection(mail(3,'prt_c')); assert.equal(f.el('inspector-wrap-input').checked,false);
  assert.equal(fixture().el('inspector-wrap-input').checked,true);
});

test('Pane size emphasis resets with selection/unknown and updates within unchanged rounded KiB labels', async () => {
  const f = fixture();
  f.response(record({status:'running',input:'x'.repeat(300*1024),output:''})); await f.api.pollInspection();
  const caption = f.el('inspector-input-label'), label = caption.children[1];
  const first = label.attributes.style;
  f.response(record({status:'running',input:'x'.repeat(300*1024+20),output:''})); f.advance(); await f.api.pollInspection();
  assert.equal(caption.children[1],label); assert.notEqual(label.attributes.style,first);
  f.api.selectInspection(mail(2,'prt_b')); assert.equal(caption.textContent,'Input'); assert.equal(caption.children.length,0);
  f.response(record({status:'completed'},2,'prt_b')); await f.api.readInspection();
  assert.equal(caption.textContent,'Input · missing'); assert.equal(caption.children.length,0);
});

test('selected call is independent of Send target, heartbeats, new calls and row lifetime; shell stays mounted', async () => {
  const f = fixture(); await f.api.pollInspection();
  const input = f.el('inspector-input'), shell = f.el('tool-inspector');
  input.scrollTop = 81; input.scrollLeft = 13;
  f.ctx.sendTarget = 'ses_other'; f.ctx.activeHeartbeat = { sessionId: 'ses_new', revision: 87 };
  f.ctx.nativeRows = []; f.advance(); await f.api.pollInspection();
  assert.equal(f.api.state().selected.sessionId, 'ses_original');
  assert.equal(f.el('inspector-input'), input); assert.equal(f.el('tool-inspector'), shell);
  assert.equal(input.scrollTop, 81); assert.equal(input.scrollLeft, 13);
  assert.equal(shell.hidden, false); assert.equal(input.focusCount, 0);
  assert.ok(f.requests.every(([url]) => url.startsWith('/api/tool-')));
});

test('streaming fragments, running output and terminal/error are distinct; terminal stops regular reads', async () => {
  const f = fixture();
  f.response(record({ status: 'streaming', input: '{"command":' })); await f.api.pollInspection();
  assert.match(f.el('inspector-input-label').textContent, /streaming fragment \(not final\)/);
  assert.equal(f.el('inspector-input').textContent, '{"command":');
  assert.match(f.el('inspector-output-label').textContent, /pending/);
  f.response(record({ status: 'running', input: { command: 'pwd' }, output: 'partial\n' }));
  f.advance(); await f.api.pollInspection(); assert.equal(f.el('inspector-output').textContent, 'partial\n');
  f.response(record({ status: 'error', input: { command: 'pwd' }, error: 'failed <script>oops</script>' }));
  f.advance(); await f.api.pollInspection();
  assert.match(f.el('inspector-output').textContent, /Error:\nfailed <script>/);
  assert.equal(f.api.state().terminal, true);
  f.advance(100000); await f.api.pollInspection(); assert.equal(f.reads.length, 3);
  f.el('inspector-retry').onclick(); await new Promise(setImmediate);
  assert.equal(f.reads.length, 4);
});

test('full >10k Unicode/object/string/falsy/empty/stub data is safe and not preview-cropped', async () => {
  const f = fixture();
  const full = '<img src=x onerror=alert(1)>\n' + '😀🚀\n'.repeat(6000) + 'LAST';
  f.response(record({ status: 'completed', input: { full, empty: '', zero: 0, false: false, null: null }, output: full }));
  await f.api.pollInspection();
  // Readable text is ON by default: the multiline value is a reading block, not JSON text.
  assert.ok(f.el('inspector-input').children.some(node => node.className === 'inspector-document' && node.textContent === full));
  reading(f, 'input', false); reading(f, 'output', false);
  assert.equal(JSON.parse(f.el('inspector-input').textContent).full, full);
  assert.equal(f.el('inspector-output').textContent, full);
  assert.equal(f.el('inspector-output').innerHTML, undefined);
  for (const value of ['', false, 0, null, '{"not":"parsed"}']) {
    f.api.selectInspection(mail(2)); f.response(record({ status: 'completed', input: value, output: value }, 2));
    await f.api.readInspection();
    const raw = typeof value === 'string' ? value : JSON.stringify(value);
    assert.equal(f.api.state().snapshot.input, raw);
    assert.equal(f.api.state().snapshot.output, raw);
    assert.equal(f.el('inspector-input').textContent, f.api.prettyJSON(raw, 8 * 1024 * 1024).text);
    assert.equal(f.el('inspector-output').textContent, f.api.prettyJSON(raw, 8 * 1024 * 1024).text);
    if (value === '') assert.match(f.el('inspector-output-label').textContent, /empty string/);
    // Make the next repeated deliberate click a fresh revision even when the identity is unchanged.
    f.api.selectInspection({ epoch: 'epoch', revision: 3, selection: null });
  }
  f.api.selectInspection(mail(4));
  f.response(record({ status: 'completed', input: {}, output: '[output guard stub: original not retained]' }, 4));
  await f.api.readInspection(); assert.match(f.el('inspector-output').textContent, /original not retained/);
});

test('raw V2 completed content renders full text blocks without a legacy output field', async () => {
  const f = fixture();
  const text = 'node.read result\n<img src=x onerror=alert(1)>\n' + '😀\n'.repeat(6000) + 'END';
  f.response({ ...record({ status: 'completed', input: { verb: 'read', params: {} },
    metadata: {}, content: [{ type: 'text', text }] }), messageCompleted: true });
  await f.api.pollInspection();
  assert.equal(f.el('inspector-output').textContent, text);
  assert.equal(f.el('inspector-output').innerHTML, undefined);
  assert.match(f.el('inspector-output-label').textContent, /stored/);
  assert.equal(f.api.state().terminal, true);
});

test('raw V2 content keeps block order, literal text and non-text block data', async () => {
  const f = fixture();
  const image = { type: 'image', mediaType: 'image/png', data: 'mock-data' };
  f.response(record({ status: 'completed', input: {}, content: [
    { type: 'text', text: 'First' }, { type: 'text', text: '{"literal":"not parsed"}' }, image,
  ] }));
  await f.api.pollInspection();
  assert.equal(f.el('inspector-output').textContent,
    'First\n\n{"literal":"not parsed"}\n\n' + JSON.stringify(image));
});

test('canonical V2 empty content is available and takes precedence over legacy output', async () => {
  const f = fixture();
  for (const [revision, content] of [[1, []], [2, [{ type: 'text', text: '' }]]]) {
    f.api.selectInspection(mail(revision));
    f.response(record({ status: 'completed', input: {}, content, output: 'stale legacy value' }, revision));
    await f.api.readInspection();
    assert.equal(f.el('inspector-output').textContent, '');
    assert.match(f.el('inspector-output-label').textContent, /empty content/);
  }
});

test('raw V2 running content refreshes through completion and then stops', async () => {
  const f = fixture();
  f.response(record({ status: 'running', input: {}, content: [{ type: 'text', text: 'partial' }] }));
  await f.api.pollInspection();
  assert.equal(f.el('inspector-output').textContent, 'partial');
  f.response(record({ status: 'completed', input: {}, content: [{ type: 'text', text: 'finished' }] }));
  f.advance(); await f.api.pollInspection();
  assert.equal(f.el('inspector-output').textContent, 'finished');
  assert.equal(f.api.state().terminal, true);
  f.advance(); await f.api.pollInspection();
  assert.equal(f.reads.length, 2);
});

test('a call without canonical content or legacy output still reports unavailable output', async () => {
  const f = fixture();
  f.response(record({ status: 'completed', input: {} }));
  await f.api.pollInspection();
  assert.equal(f.el('inspector-output').textContent, '(output not available)');
  assert.match(f.el('inspector-output-label').textContent, /missing/);
});

test('delayed old response cannot overwrite a later deliberate click, including same-identity clicks', async () => {
  const f = fixture(); let release;
  f.hook(() => new Promise(resolve => { release = resolve; }));
  const pending = f.api.pollInspection(); await new Promise(setImmediate);
  f.api.selectInspection(mail(2, 'prt_b'));
  release(record({ status: 'completed', input: 'old', output: 'old' })); await pending;
  assert.equal(f.api.state().selected.partId, 'prt_b'); assert.equal(f.api.state().snapshot, null);
  f.hook(null); f.response(record({ status: 'completed', input: 'new', output: 'new' }, 2, 'prt_b'));
  await f.api.readInspection(); assert.equal(f.el('inspector-output').textContent, 'new');
  f.api.selectInspection(mail(3, 'prt_b')); assert.equal(f.api.state().snapshot, null);
});

test('round 9a: the pane never closes; it holds a call from the first click and clears nothing on the server', async () => {
  const f = fixture();
  assert.equal(f.el('tool-inspector').classList.contains('held'), false);
  await f.api.pollInspection(); assert.equal(f.el('tool-inspector').classList.contains('held'), true);
  assert.equal(f.el('inspector-outer-divider').hidden, false);
  assert.ok(!f.requests.some(([url]) => url === '/api/tool-selection/clear'));
  assert.equal(f.el('inspector-close').onclick, undefined); assert.equal(f.el('inspector-collapse').onclick, undefined);
  f.mailbox({ epoch: 'epoch', revision: 2, selection: null }); f.advance(); await f.api.pollInspection();
  assert.equal(f.el('tool-inspector').classList.contains('held'), true); assert.ok(f.api.state().snapshot);
});

test('round 9a: the head row shows the call date and time in ET and how long it took (OpenCode part start/end, native record seconds); unknown stays blank', async () => {
  const f = fixture(), start = Date.UTC(2026, 9, 15, 20, 10, 3);
  f.response(record({ status: 'completed', input: {}, output: 'x', time: { start, end: start + 83000 } })); await f.api.pollInspection();
  assert.equal(f.el('inspector-time').textContent, '· 2026-10-15 16:10:03 ET · took 1m 23s');   // EDT
  f.api.selectInspection(mail(2)); assert.equal(f.el('inspector-time').textContent, '');
  f.response(record({ status: 'completed', input: {}, output: 'x' }, 2)); await f.api.readInspection();
  assert.equal(f.el('inspector-time').textContent, '');
  const g = fixture(), data = nativeRecord('in', 'out'); data.snapshot.time = start / 1000;
  g.mailbox(nativeMail()); g.response(data); await g.api.pollInspection();
  assert.equal(g.el('inspector-time').textContent, '· 2026-10-15 16:10:03 ET', 'no result time stored: no duration');
  assert.equal(g.el('inspector-title').textContent, 'mcp__workflowy__anchor');
  const h = fixture(), timed = nativeRecord('in', 'out'); timed.snapshot.time = start / 1000; timed.snapshot.resultTime = start / 1000 + 7510;
  h.mailbox(nativeMail()); h.response(timed); await h.api.pollInspection();
  assert.equal(h.el('inspector-time').textContent, '· 2026-10-15 16:10:03 ET · took 2h 05m 10s');
});
test('an unsettled inspector record labels elapsed since call, not process liveness, then freezes its stored total', async () => {
  const f = fixture(), start = Date.UTC(2026, 9, 15, 20, 10, 3);
  f.advance(start - 10000 + 5000); // the fake clock is five seconds after the part's stored start
  f.response(record({ status: 'running', input: {}, output: null, time: { start } })); await f.api.pollInspection();
  assert.equal(f.api.state().terminal, false);
  assert.equal(f.el('inspector-time').textContent, '· 2026-10-15 16:10:03 ET · 5s since call · no result recorded');
  const reads = f.reads.length;
  f.advance(2000); f.api.render(); // the once-a-second tick repaints this one line only
  assert.equal(f.el('inspector-time').textContent, '· 2026-10-15 16:10:03 ET · 7s since call · no result recorded');
  assert.equal(f.reads.length, reads, 'the elapsed tick never reads the record again');
  // The part's own stored end freezes the total; a terminal call never ticks.
  f.response(record({ status: 'completed', input: {}, output: 'x', time: { start, end: start + 65000 } })); await f.api.readInspection();
  assert.equal(f.api.state().terminal, true);
  assert.equal(f.el('inspector-time').textContent, '· 2026-10-15 16:10:03 ET · took 1m 05s');
  f.advance(30000); f.api.render();
  assert.equal(f.el('inspector-time').textContent, '· 2026-10-15 16:10:03 ET · took 1m 05s', 'a finished call keeps its stored total');
  // An unknown start keeps the line blank, as before.
  const g = fixture(); g.response(record({ status: 'running', input: {} })); await g.api.pollInspection();
  assert.equal(g.el('inspector-time').textContent, '', 'no start is no invented time');
});
test('callTimeText: Eastern date and time (EST in winter), durations from tenths of a second to hours, no duration without a sane end', () => {
  const { api } = fixture(), t = Date.UTC(2026, 0, 2, 3, 4, 5), say = (end) => api.callTimeText(t, end);
  assert.equal(say(null), '2026-01-01 22:04:05 ET');
  assert.equal(say(t + 400), '2026-01-01 22:04:05 ET · took 0.4s');
  assert.equal(say(t + 42000), '2026-01-01 22:04:05 ET · took 42s');
  assert.equal(say(t + 65000), '2026-01-01 22:04:05 ET · took 1m 05s');
  assert.equal(say(t - 1), '2026-01-01 22:04:05 ET');
  assert.equal(api.callTimeText(null, t), '');
});

test('a newer click is always taken; polling never overlaps', async () => {
  const f = fixture(); await f.api.pollInspection();
  f.mailbox(mail(2, 'prt_b'));
  f.response(record({ status: 'completed', input: {}, output: 'B' }, 2, 'prt_b')); await f.api.pollInspection();
  assert.equal(f.api.state().selected.partId, 'prt_b');
  let release; f.hook(() => new Promise(resolve => { release = resolve; }));
  f.el('inspector-retry').onclick(); await new Promise(setImmediate);
  const count = f.reads.length;
  await f.api.pollInspection(); await f.api.readInspection(); assert.equal(f.reads.length, count);
  release(record({ status: 'completed', input: {}, output: 'B' }, 2, 'prt_b')); await new Promise(setImmediate);
});

test('offline/missing/mismatched records retain labeled snapshot, throttle retries, never select another tool', async () => {
  const f = fixture(); await f.api.pollInspection(); const initial = f.el('inspector-input').textContent;
  f.response({ error: 'record missing / unsupported backend' }); f.advance(); await f.api.pollInspection();
  assert.equal(f.el('inspector-input').textContent, initial);
  assert.match(f.el('inspector-note').textContent, /last successful snapshot retained/);
  const n = f.reads.length; await f.api.pollInspection(); assert.equal(f.reads.length, n);
  f.advance(15000); f.response(record({ status: 'running', input: 'wrong' }, 1, 'prt_wrong')); await f.api.pollInspection();
  assert.match(f.el('inspector-note').textContent, /association changed/);
  assert.equal(f.api.state().selected.partId, 'prt_a');
});

test('restart/clear does not erase held snapshot or read a different service until another click', async () => {
  const f = fixture(); await f.api.pollInspection(); const n = f.reads.length;
  f.mailbox({ epoch: 'new_epoch', revision: 0, selection: null }); f.advance(); await f.api.pollInspection();
  assert.equal(f.api.state().disconnected, true); assert.equal(f.el('tool-inspector').classList.contains('held'), true);
  assert.match(f.el('inspector-note').textContent, /restarted.*snapshot retained/);
  f.advance(); await f.api.pollInspection(); assert.equal(f.reads.length, n);
});

test('text selection, scroll and divider survive streaming updates; reading never auto-follows output', async () => {
  const f = fixture(); await f.api.pollInspection();
  const input = f.el('inspector-input'), output = f.el('inspector-output');
  input.scrollTop = 27; output.scrollTop = 41; output.scrollLeft = 9;
  f.range({ isCollapsed: false, anchorNode: { owner: 'inspector-output' }, focusNode: { owner: 'inspector-output' } });
  f.response(record({ status: 'running', input: { command: 'pwd' }, output: 'NEW OUTPUT' })); f.advance(); await f.api.pollInspection();
  assert.equal(output.textContent, '(output not available)');
  f.range(null); f.listeners.get('selectionchange')(); assert.equal(output.textContent, 'NEW OUTPUT');
  assert.equal(output.scrollTop, 41); assert.equal(output.scrollLeft, 9); assert.equal(input.scrollTop, 27);
  const divider = f.el('inspector-divider');
  const event = key => ({ key, preventDefault() {} });
  divider.events.get('keydown')(event('End')); assert.equal(divider.attributes['aria-valuenow'], 85);
  divider.events.get('keydown')(event('ArrowDown')); assert.equal(divider.attributes['aria-valuenow'], 85);
  divider.events.get('keydown')(event('Home')); assert.equal(divider.attributes['aria-valuenow'], 15);
  divider.events.get('pointerdown')({ button: 0, pointerId: 1, clientY: 100, preventDefault() {} });
  divider.events.get('pointermove')({ pointerId: 1, clientY: 180 }); assert.equal(divider.attributes['aria-valuenow'], 35);
  divider.events.get('pointercancel')(); divider.events.get('pointermove')({ pointerId: 1, clientY: 300 });
  assert.equal(divider.attributes['aria-valuenow'], 35);
  f.advance(); await f.api.pollInspection(); assert.equal(divider.attributes['aria-valuenow'], 35);
});

test('copy is explicit, keeps the selection and never modifies transcript, context or target', async () => {
  const f = fixture(); await f.api.pollInspection();
  const selection = f.api.state().selected;
  await f.el('inspector-copy-input').onclick(); assert.equal(f.copies[0], f.api.state().snapshot.input);
  f.denyClipboard(); await f.el('inspector-copy-output').onclick(); assert.match(f.el('inspector-note').textContent, /Clipboard unavailable/);
  assert.equal(f.api.state().selected, selection);
});

test('oversized snapshots fail visibly without a falsely full cropped display', async () => {
  const f = fixture(); f.response(record({ status: 'completed', input: {}, output: 'x'.repeat(8 * 1024 * 1024) }));
  await f.api.pollInspection(); assert.equal(f.api.state().snapshot, null);
  assert.match(f.el('inspector-note').textContent, /8 MiB.*nothing cropped/);
});

test('16 MiB response read bound, permanent missing-call stop and manual retry are explicit', async () => {
  const f = fixture(); f.response(record({ status: 'completed', input: {}, output: 'x'.repeat(16 * 1024 * 1024) }));
  await f.api.pollInspection(); assert.equal(f.api.state().snapshot, null);
  assert.match(f.el('inspector-note').textContent, /16 MiB.*nothing cropped/);
  f.advance(16000); f.response({ error: 'selected tool part missing', status: 404 }); await f.api.pollInspection();
  assert.equal(f.api.state().terminal, true);
  const n = f.reads.length; f.advance(16000); await f.api.pollInspection(); assert.equal(f.reads.length, n);
  f.response(record({ status: 'completed', input: {}, output: 'found on manual retry' }));
  f.el('inspector-retry').onclick(); await new Promise(setImmediate);
  assert.equal(f.el('inspector-output').textContent, 'found on manual retry');
});

test('read timeout releases one-in-flight guard with backoff; hidden pages do not download', async () => {
  const f = fixture();
  f.hook((_, options) => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('timed out')))));
  const read = f.api.pollInspection(); await new Promise(setImmediate);
  assert.equal(f.reads.length, 1); assert.equal(f.timers.size, 1);
  [...f.timers.values()][0](); await read;
  assert.equal(f.api.state().polling, false); assert.match(f.el('inspector-note').textContent, /timed out/);
  f.hook(null); f.ctx.document.hidden = true; f.advance(16000); await f.api.pollInspection(); assert.equal(f.reads.length, 1);
  f.ctx.document.hidden = false; await f.api.pollInspection(); assert.equal(f.reads.length, 2);
});

test('global Send and Refine shortcuts are blocked only while inspector/divider is focused', () => {
  const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
  const start = app.indexOf("  document.addEventListener('keydown', (e) => {");
  const end = app.indexOf('\n  });', start) + 6;
  let handler, sends = 0, refines = 0;
  vm.runInNewContext(app.slice(start, end), { document: { addEventListener: (_, f) => { handler = f; } },
    send: () => sends++, refine: () => refines++ });
  const keys = [{ key: 'Enter', ctrlKey: true }, { key: 'Enter', ctrlKey: true, altKey: true, shiftKey: true },
    { key: 'F11', shiftKey: true }, { key: 'F4' }, { key: 'r', altKey: true }];
  for (const scoped of [true, false]) for (const key of keys) {
    let prevented = false; handler({ ...key, target: { closest: () => scoped }, preventDefault: () => { prevented = true; } });
    assert.equal(prevented, true);
  }
  assert.equal(sends, 4); assert.equal(refines, 1);
});

function toggle(f, kind, checked) {
  const el = f.el('inspector-pretty-' + kind); el.checked = checked; el.onchange();
}

test('independent defaults ON, exact OFF, raw copy, no reads/target/draft/focus changes; new page resets defaults', async () => {
  const f = fixture(), raw = ' \r\n{"x":[1,{"y":"a\\nb"}]}\t\n';
  f.response(record({ status: 'completed', input: raw, output: raw })); await f.api.pollInspection();
  assert.equal(f.el('inspector-pretty-input').checked, true);
  assert.equal(f.el('inspector-pretty-output').checked, true);
  assert.notEqual(f.el('inspector-input').textContent, raw);
  const selected = f.api.state().selected, count = f.reads.length;
  f.ctx.sendTarget = 'unrelated'; f.ctx.draft = 'kept';
  f.el('inspector-input').scrollTop = 17; f.el('inspector-output').scrollLeft = 9;
  toggle(f, 'input', false);
  assert.equal(f.el('inspector-input').textContent, raw);
  assert.notEqual(f.el('inspector-output').textContent, raw);
  await f.el('inspector-copy-input').onclick(); await f.el('inspector-copy-output').onclick();
  assert.deepEqual(f.copies, [raw, raw]);
  toggle(f, 'output', false); assert.equal(f.el('inspector-output').textContent, raw);
  toggle(f, 'input', true); assert.notEqual(f.el('inspector-input').textContent, raw);
  assert.equal(f.reads.length, count); assert.equal(f.api.state().selected, selected);
  assert.equal(f.ctx.sendTarget, 'unrelated'); assert.equal(f.ctx.draft, 'kept');
  assert.equal(f.el('inspector-input').focusCount, 0);
  assert.equal(f.el('inspector-input').scrollTop, 17); assert.equal(f.el('inspector-output').scrollLeft, 9);
  f.api.selectInspection(mail(2)); f.response(record({ status: 'completed', input: {}, output: '[1,2]' }, 2));
  await f.api.readInspection(); assert.equal(f.el('inspector-output').textContent, '[1,2]');
  assert.equal(fixture().el('inspector-pretty-output').checked, true);
});

test('formatter preserves number/string/key tokens and duplicates, not parse/stringify values', () => {
  const f = fixture();
  const raw = String.raw`{"n":9007199254740993123456789,"n":-0,"exp":1.234567890123456789e+300,"overflow":1e9999,"s":"\u0041\/\\\"\n","\u006b":"😀"}`;
  const result = f.api.prettyJSON(raw, 8 * 1024 * 1024);
  assert.equal(result.reason, 'JSON');
  for (const token of ['9007199254740993123456789', '-0', '1.234567890123456789e+300', '1e9999', String.raw`"\u0041\/\\\"\n"`, String.raw`"\u006b"`]) assert.ok(result.text.includes(token));
  assert.equal((result.text.match(/"n"/g) || []).length, 2);
  assert.ok(result.text.includes('\n  '));
  for (const raw of [' \t1e9999\r\n', '  -0  ', '"\\u0061"', 'true', 'false', 'null', '[]', '{}']) {
    assert.equal(f.api.prettyJSON(raw, 1000).text, raw);
  }
});

test('wire extraction retains structured input/output/error and non-text block numbers before JS coercion', async () => {
  const f = fixture();
  const input = String.raw`{"n":900719925474099312345,"x":-0,"s":"\u0041","nest":[{"a":1e9999}]}`;
  const block = '{"type":"image","n":900719925474099399999}';
  f.wire(`{"epoch":"epoch","revision":1,"part":{"id":"prt_a","type":"tool","state":{"status":"completed","input":${input},"content":[${block}]}}}`);
  await f.api.pollInspection();
  assert.equal(f.api.state().snapshot.input, input);
  assert.equal(f.api.state().snapshot.output, block);
  toggle(f, 'input', false); toggle(f, 'output', false);
  assert.equal(f.el('inspector-input').textContent, input); assert.equal(f.el('inspector-output').textContent, block);
  await f.el('inspector-copy-input').onclick(); assert.equal(f.copies[0], input);
  f.api.selectInspection(mail(2));
  f.wire(`{"epoch":"epoch","revision":2,"part":{"id":"prt_a","type":"tool","state":{"status":"error","input":false,"error":{"n":900719925474099399999},"output":900719925474099312345}}}`);
  await f.api.readInspection();
  assert.equal(f.el('inspector-output').textContent, 'Error:\n{"n":900719925474099399999}\n\nAvailable output:\n900719925474099312345');
});

test('wire span extraction handles escaped keys, punctuation in strings, duplicate fields and nested arrays', () => {
  const f = fixture();
  const text = String.raw` { "\u0061": {"s":"} , : [ \"","b":[[],{}]}, "a":-0, "b": ["\\",null,1e9999] } `;
  const fields = f.api.rawMembers(text);
  assert.equal(fields.get('a'), '-0');
  assert.equal(fields.get('b'), String.raw`["\\",null,1e9999]`);
  assert.equal(f.api.rawMembers(fields.get('b')).get(2), '1e9999');
});

test('invalid/fragment/prose remains untouched; leading JSON containers keep suffix exactly', () => {
  const f = fixture();
  for (const raw of ['', 'plain\ntext', 'prefix {"a":1}', '[output guard stub: original not retained]', '{"a":', '[1,]', '{"a":01}', '[}', '{"x":"bad\\q"}', '{"a":1}{"b":2}', '[] true', '{} null', '{} 123', '[]]', '1 prose', '"str" prose', '\ufeff{"a":1}']) {
    assert.equal(f.api.prettyJSON(raw, 10000).text, raw, raw);
  }
  const prefix = String.raw`{"a":[1,{"s":"} ] \" \\"}],"big":900719925474099312345}`;
  for (const suffix of ['\r\n\r\nDone exactly.  \t', '\nnotes {not JSON}\n😀', 'prose without a separator']) {
    const raw = ' \t' + prefix + suffix, result = f.api.prettyJSON(raw, 10000);
    assert.equal(result.reason, 'JSON + text'); assert.ok(result.text.startsWith(' \t{\n'));
    assert.ok(result.text.endsWith(suffix)); assert.ok(result.text.includes('900719925474099312345'));
  }
  const array = f.api.prettyJSON('[1,{"x":[true,null]}]\nTAIL\r\n', 10000);
  assert.equal(array.reason, 'JSON + text'); assert.ok(array.text.endsWith('\nTAIL\r\n'));
});

test('deterministic nested JSON corpus matches ordinary pretty layout without changing token spelling', () => {
  const f = fixture(); let seed = 73421;
  const random = n => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed % n; };
  const build = depth => {
    if (!depth || random(3) === 0) return [null, false, true, 123.456, 'x,:[{}]\\"\n😀'][random(5)];
    return random(2) ? Array.from({ length: random(6) }, () => build(depth - 1))
      : Object.fromEntries(Array.from({ length: random(6) }, (_, n) => ['key' + n, build(depth - 1)]));
  };
  for (let n = 0; n < 300; n++) {
    const value = build(5), raw = JSON.stringify(value);
    const result = f.api.prettyJSON(raw, 8 * 1024 * 1024);
    assert.equal(result.text, JSON.stringify(value, null, 2));
    assert.deepEqual(JSON.parse(result.text), value);
  }
});

test('explicit size/depth/token/expansion bounds return entire raw text, no cropped fake snapshot', async () => {
  const f = fixture();
  const values = ['{"long":"' + 'x'.repeat(1024 * 1024) + '"}', '['.repeat(129) + '0' + ']'.repeat(129), '[' + '0,'.repeat(60000) + '0]'];
  for (const raw of values) {
    const result = f.api.prettyJSON(raw, 8 * 1024 * 1024);
    assert.equal(result.reason, 'format limit'); assert.equal(result.text, raw);
  }
  const raw = '[1,2,3]'; assert.equal(f.api.prettyJSON(raw, raw.length).text, raw);
  f.response(record({ status: 'completed', input: values[0], output: 'TAIL' })); await f.api.pollInspection();
  assert.equal(f.el('inspector-input').textContent, values[0]);
  assert.match(f.el('inspector-input-label').textContent, /raw \(format limit\)/);
  await f.el('inspector-copy-input').onclick(); assert.equal(f.copies[0], values[0]);
  // Near the combined snapshot cap, optional expansion falls back rather than
  // making a valid complete raw record unreadable.
  f.api.selectInspection(mail(2));
  const small = '[1,2,3,4]', big = 'x'.repeat(8 * 1024 * 1024 - small.length);
  f.response(record({ status: 'completed', input: small, output: big }, 2)); await f.api.readInspection();
  assert.equal(f.el('inspector-input').textContent, small); assert.equal(f.el('inspector-output').textContent, big);
});

test('toggle and streaming refresh defer repaint while text selected; copy uses latest raw held snapshot', async () => {
  const f = fixture(), old = '[1,2]', next = '[3,4]';
  f.response(record({ status: 'running', input: {}, output: old })); await f.api.pollInspection();
  const before = f.el('inspector-output').textContent;
  f.range({ isCollapsed: false, anchorNode: { owner: 'inspector-output' }, focusNode: { owner: 'inspector-output' } });
  toggle(f, 'output', false); assert.equal(f.el('inspector-output').textContent, before);
  f.response(record({ status: 'running', input: {}, output: next })); f.advance(); await f.api.pollInspection();
  assert.equal(f.el('inspector-output').textContent, before);
  await f.el('inspector-copy-output').onclick(); assert.equal(f.copies[0], next);
  f.range(null); f.listeners.get('selectionchange')(); assert.equal(f.el('inspector-output').textContent, next);
});

function reading(f, kind, checked) {
  const node = f.el('inspector-readable-' + kind); node.checked = checked; node.onchange();
}

test('readable values at any depth preserve structure, duplicate keys, numeric lexemes, suffix and one decoding layer', () => {
  const f = fixture();
  const raw = String.raw`{"a":{"doc":"# Heading\n\tline \"quoted\" 😀\\n literal","a":"simple"},"arr":[1,{"deep":["one\ntwo","literal\\nonly"]},"tab\ttext"],"a":"duplicate\nkept","n":900719925474099312345,"exp":1.234e+999,"\nkey":"ordinary"}` + '\r\n\r\nExact suffix.  \t';
  const view = f.api.prettyJSON(raw, 8 * 1024 * 1024);
  assert.equal(view.reason, 'JSON + text'); assert.equal(view.documents, 4);
  const documents = view.pieces.filter(piece => piece.document);
  assert.equal(documents[0].text, '# Heading\n\tline "quoted" 😀\\n literal');
  assert.equal(documents[1].text, 'one\ntwo'); assert.equal(documents[1].index, 0);
  assert.equal(documents[2].text, 'tab\ttext'); assert.equal(documents[2].index, 2);
  const shown = view.pieces.map(piece => piece.text).join('');
  for (const token of [String.raw`"literal\\nonly"`, '900719925474099312345', '1.234e+999', String.raw`"\nkey"`, '"ordinary"', '"simple"']) assert.ok(shown.includes(token), token);
  assert.equal((shown.match(/"a"/g) || []).length, 3);
  assert.ok(shown.endsWith('\r\n\r\nExact suffix.  \t'));
  assert.equal(view.text, f.api.prettyJSON(raw, 8 * 1024 * 1024).text); // Opt-in never changes ordinary pretty.
  assert.equal(view.pieces.some(piece => piece.document && piece.text === '\nkey'), false);
});

test('readable controls default ON, are per-pane/gated by Pretty, restore raw/pretty, keep raw Copy/bytes/identity', async () => {
  const f = fixture(), raw = ' \r\n' + String.raw`{"doc":"a\n\tb😀","n":900719925474099312345}` + '\nTAIL  ';
  f.response(record({ status: 'completed', input: raw, output: raw })); await f.api.pollInspection();
  assert.equal(f.el('inspector-readable-input').checked, true); assert.equal(f.el('inspector-readable-output').checked, true);
  assert.ok(f.el('inspector-input').children.some(node => node.className === 'inspector-document'));
  reading(f, 'input', false); reading(f, 'output', false);
  const selected = f.api.state().selected, count = f.reads.length, label = f.api.state().snapshot.inputLabel;
  const pretty = f.el('inspector-input').textContent;
  reading(f, 'input', true);
  assert.ok(f.el('inspector-input').textContent.includes('a\n\tb😀'));
  assert.equal(f.el('inspector-output').textContent, pretty);
  assert.ok(f.el('inspector-input').children.some(node => node.className === 'inspector-document'));
  assert.match(f.el('inspector-input-label').textContent, /reading view \(not JSON\)/);
  await f.el('inspector-copy-input').onclick(); await f.el('inspector-copy-output').onclick();
  assert.deepEqual(f.copies, [raw, raw]);
  toggle(f, 'input', false); assert.equal(f.el('inspector-readable-input').disabled, true);
  assert.equal(f.el('inspector-input').textContent, raw);
  toggle(f, 'input', true); assert.equal(f.el('inspector-readable-input').disabled, false);
  assert.ok(f.el('inspector-input').textContent.includes('a\n\tb😀'));
  reading(f, 'input', false); assert.equal(f.el('inspector-input').textContent, pretty);
  assert.equal(f.api.state().snapshot.inputLabel, label); assert.equal(f.api.state().selected, selected); assert.equal(f.reads.length, count);
  assert.equal(fixture().el('inspector-readable-input').checked, true);
  reading(f, 'output', true);
  f.api.selectInspection(mail(2)); f.response(record({ status: 'completed', input: '{}', output: raw }, 2)); await f.api.readInspection();
  assert.ok(f.el('inspector-output').textContent.includes('a\n\tb😀'));
});

test('readable rendering defers selected text updates, rejects HTML, and leaves invalid/no-document/limited payloads honest', async () => {
  const f = fixture(), old = String.raw`{"doc":"old\n<img src=x onerror=alert(1)>"}`;
  f.response(record({ status: 'running', input: {}, output: old })); await f.api.pollInspection(); reading(f, 'output', true);
  const element = f.el('inspector-output'), before = element.textContent;
  f.range({ isCollapsed: false, anchorNode: { owner: 'inspector-output' }, focusNode: { owner: 'inspector-output' } });
  const next = String.raw`{"doc":"new\n<script>still text</script>"}`;
  f.response(record({ status: 'running', input: {}, output: next })); f.advance(); await f.api.pollInspection();
  assert.equal(element.textContent, before); await f.el('inspector-copy-output').onclick(); assert.equal(f.copies[0], next);
  f.range(null); f.listeners.get('selectionchange')(); assert.ok(element.textContent.includes('new\n<script>still text</script>'));
  assert.equal(element.innerHTML, undefined);
  for (const [revision, raw, expected] of [[2, '{"bad":', /raw \(not JSON\)/], [3, '{"simple":"ordinary"}', /no document strings/],
    [4, '[' + Array.from({length:2001}, () => '"a\\nb"').join(',') + ']', /reading limit/]]) {
    f.api.selectInspection(mail(revision)); f.response(record({ status: 'completed', input: {}, output: raw }, revision)); await f.api.readInspection();
    assert.match(f.el('inspector-output-label').textContent, expected);
    assert.equal(element.textContent, f.api.prettyJSON(raw, 8 * 1024 * 1024).text);
    await f.el('inspector-copy-output').onclick(); assert.equal(f.copies.at(-1), raw);
  }
});

test('captions measure UTF-8 raw snapshots, with known empties vs missing and V2/nontext/error/stub definitions', async () => {
  const f = fixture(), text = '汉😀\n', block = { type: 'image', data: '🚀' };
  f.response(record({ status: 'completed', input: { doc: text }, content: [{ type: 'text', text }, block] })); await f.api.pollInspection();
  const snapshot = f.api.state().snapshot;
  assert.equal(snapshot.inputBytes, Buffer.byteLength(JSON.stringify({ doc: text })));
  assert.equal(snapshot.outputBytes, Buffer.byteLength(text + '\n\n' + JSON.stringify(block)));
  assert.match(f.el('inspector-input-label').textContent, /^Input stored \([0-9.]+ KiB\)/);
  assert.match(f.el('inspector-output-label').textContent, /^Output stored \([0-9.]+ KiB\)/);
  const before = snapshot.outputBytes; reading(f, 'input', true); toggle(f, 'output', false); assert.equal(snapshot.outputBytes, before);
  const cases = [ [{ status: 'completed', input: '', content: [] }, 0, 0], [{ status: 'completed' }, null, null],
    [{ status: 'error', input: false, error: '汉😀' }, 5, Buffer.byteLength('Error:\n汉😀')],
    [{ status: 'completed', input: 0, output: '[output guard stub: original not retained]' }, 1, Buffer.byteLength('[output guard stub: original not retained]')] ];
  for (let n = 0; n < cases.length; n++) {
    f.api.selectInspection(mail(n + 2)); f.response(record(cases[n][0], n + 2)); await f.api.readInspection();
    assert.equal(f.api.state().snapshot.inputBytes, cases[n][1]); assert.equal(f.api.state().snapshot.outputBytes, cases[n][2]);
  }
  assert.match(f.el('inspector-output-label').title, /Saved stubs count only the stored stub/);
});

const ANCHOR_READ = [
  '[name: not editable] [ap:K65F9I] Pane round 4 #sift-programs--expand', '',
  '## Task', '', 'Three changes to the **transcription pane**, working only in the worktree.', '',
  '### 1. The cleanup prompt', '', '- Section headings, encouraged.', '- One bounded exception: a thumbs-up.', '',
  '| Item | State |', '| --- | --- |', '| prompt | done |', '',
  'See [the README](https://example.com/readme) for `details`.'].join('\n');

test('Markdown detection is conservative: clear Markdown renders, code/logs/diffs/JSON/config/listings refuse', () => {
  const f = fixture(), like = f.api.markdownLike;
  for (const [name, text] of [
    ['Anchor Point read', ANCHOR_READ],
    ['two headings', '# One\n\nfirst part\n\n## Two\n\nsecond part'],
    ['table', '| a | b |\n|---|:--:|\n| 1 | 2 |\n| 3 | 4 |'],
    ['list and bold', 'Notes:\n\n- **first** point\n- second point'],
    ['fence and list', 'Run this:\n\n```bash\nnpm test;\n```\n\n- then check\n- the receipts'],
    ['link and heading', '# Links\n\nRead [docs](https://example.com) first.'],
  ]) assert.equal(like(text), true, name);
  for (const [name, text] of [
    ['plain prose', 'Just a sentence.\nAnd another one, nothing more.'],
    ['one heading only', '# Heading\n\nA paragraph.'],
    ['lists only', '- a\n- b\n- c\n- d'],
    ['python with comments', 'import os\n\n# Load rows\n\ndef load():\n    return [1, 2]\n\n# Save rows\n\nclass Saver:\n    pass\n- item\n- item'],
    ['shell script', '#!/bin/bash\n# Setup\n\nset -e\ncd /tmp\necho "**hi**"\n# Done\n\nfi'],
    ['JSON', '[{"a": "# x\\n- y"}]'],
    ['markup', '<div>\n# Heading\n\n## Two\n</div>'],
    ['diff', 'diff --git a/x b/x\n@@ -1,2 +1,2 @@\n-# Old\n+# New\n\n## Still'],
    ['log', '2026-10-15 10:00:01 INFO start\n2026-10-15 10:00:02 WARN slow\n# Heading\n\n## Two'],
    ['traceback', 'Traceback (most recent call last):\n  File "x.py", line 1\n# One\n\n## Two'],
    ['numbered listing', '     1\t# Title\n     2\t\n     3\t- a\n     4\t- b\n     5\t**bold**'],
    ['grep hits', 'src/a.py:12:# One\nsrc/b.py:40:## Two\nsrc/c.py:7:- item'],
    ['config', '# Config\n\nname: pane\nitems:\n  - a\n  - b\nversion: 2'],
    ['arithmetic is not bold', 'Compute x**2 + y**2 here.\n\n- a\n- b'],
    ['empty', '   \n  '],
  ]) assert.equal(like(text), false, name);
  assert.equal(like('#'.repeat(10) + 'x'.repeat(1024 * 1024)), false, 'over the format limit');
});

test('reading view renders whole Markdown text, Markdown document values and a Markdown suffix after leading JSON', () => {
  const f = fixture(), view = (raw) => { const v = f.api.prettyJSON(raw, 8 * 1024 * 1024); return f.api.readingView(v, raw); };
  const whole = view(ANCHOR_READ);
  assert.equal(whole.markdown, 1); assert.equal(whole.pieces.length, 1); assert.equal(whole.pieces[0].text, ANCHOR_READ);
  assert.equal(view('plain output\nline two'), null);
  const nested = view(JSON.stringify({ note: ANCHOR_READ, other: 'a\nb' }));
  const docs = nested.pieces.filter(piece => piece.document);
  assert.equal(docs.map(piece => piece.markdown).join(), 'true,false');
  const suffix = '\n\n## Result\n\n- one\n- two';
  const mixed = view('{"ok":true}' + suffix);
  assert.equal(mixed.markdown, 1); assert.equal(mixed.pieces.at(-1).text, suffix); assert.equal(mixed.pieces.at(-1).markdownOnly, true);
  assert.equal(mixed.pieces.map(piece => piece.text).join(''), f.api.prettyJSON('{"ok":true}' + suffix, 8 * 1024 * 1024).text);
  assert.equal(view('{"ok":true}\nplain trailing note'), null);
});

test('Markdown blocks render through the shared renderer with a per-block Rendered/Source switch; raw Copy unchanged', async () => {
  const f = fixture(), calls = [];
  f.ctx.opcMessageMarkdown = { render(node, source, markdown) { calls.push([source, markdown]); node.textContent = 'RENDERED'; } };
  f.response(record({ status: 'completed', input: { note: ANCHOR_READ }, output: ANCHOR_READ })); await f.api.pollInspection();
  const output = f.el('inspector-output'), block = output.children[0];
  assert.equal(block.className, 'inspector-document inspector-markdown');
  assert.equal(block.attributes['data-view'], 'rendered');
  assert.deepEqual(calls.at(-1), [ANCHOR_READ, true]);
  const [modes, rendered, source] = block.children;
  assert.equal(rendered.hidden, false); assert.equal(source.hidden, true); assert.equal(source.textContent, ANCHOR_READ);
  assert.match(f.el('inspector-output-label').textContent, /reading view \(Markdown\)/);
  assert.match(f.el('inspector-input-label').textContent, /reading view \(not JSON\) · Markdown/);
  modes.children[1].onclick();
  assert.equal(block.attributes['data-view'], 'source'); assert.equal(rendered.hidden, true); assert.equal(source.hidden, false);
  assert.equal(modes.children[1].attributes['aria-pressed'], 'true');
  // A repaint (Readable off and on) keeps the reader's Source choice for this call.
  reading(f, 'output', false); assert.equal(output.textContent, ANCHOR_READ);
  reading(f, 'output', true); assert.equal(output.children[0].attributes['data-view'], 'source');
  await f.el('inspector-copy-output').onclick(); assert.equal(f.copies.at(-1), ANCHOR_READ);
  // A new deliberate Inspect starts rendered again.
  f.api.selectInspection(mail(2)); f.response(record({ status: 'completed', input: {}, output: ANCHOR_READ }, 2)); await f.api.readInspection();
  assert.equal(output.children[0].attributes['data-view'], 'rendered');
  // Without the renderer, Markdown-only text stays ordinary text and claims no Markdown.
  delete f.ctx.opcMessageMarkdown;
  f.api.selectInspection(mail(3)); f.response(record({ status: 'completed', input: {}, output: ANCHOR_READ + ' ' }, 3)); await f.api.readInspection();
  assert.equal(output.textContent, ANCHOR_READ + ' '); assert.doesNotMatch(f.el('inspector-output-label').textContent, /Markdown/);
});

test('a numbered Workflowy note read renders its body without row numbers; other numbered text stays raw', async () => {
  const f = fixture(), calls = [];
  const read = ['[ap:K65F9I] Pane round 4 #sift ▸0 · desc:0', 'path: Root › Session', '', '[note: lines 1-42 of 42]',
    '  1 | ## Task', '  2 | ', '  3 | Three changes to the **transcription pane**.', '  4 | ',
    '... | ... 30 lines not shown (5-34); read them with start_line=5', ' 35 | ## Bring back', ' 36 | ', ' 37 | - a', ' 38 | - b'].join('\n') + '\n';
  const note = f.api.numberedNote(read);
  assert.equal(note.head, read.split('\n').slice(0, 4).join('\n'));
  assert.ok(note.body.startsWith('## Task\n\nThree changes')); assert.ok(note.body.includes('… 30 lines not shown'));
  assert.ok(note.body.endsWith('## Bring back\n\n- a\n- b'));
  assert.equal(f.api.numberedNote('  1 | a\n  3 | b\n  2 | c'), null, 'rows must ascend');
  assert.equal(f.api.numberedNote('  1 | a\n  2 | b\nfree text\n  3 | c'), null, 'every row after the header is numbered');
  assert.equal(f.api.numberedNote('     1\t# Title\n     2\t\n     3\t- a'), null, 'tab listings are not note reads');
  f.ctx.opcMessageMarkdown = { render(node, source) { calls.push(source); node.textContent = 'RENDERED'; } };
  f.response(record({ status: 'completed', input: {}, output: read })); await f.api.pollInspection();
  const block = f.el('inspector-output').children[0], [, rendered, source] = block.children;
  assert.equal(calls.at(-1), note.body); assert.equal(rendered.children[0].textContent, note.head);
  assert.equal(source.textContent, read);
  await f.el('inspector-copy-output').onclick(); assert.equal(f.copies.at(-1), read);
});

// A3 · the recorded diff (A1's normalized `diff`) mounted in the held inspector through the ONE shared reader (diff-view.js).
// A recorder stands in for opcDiffView: these tests pin the host adapter (what it passes, when it mounts, updates,
// wraps and destroys); tests/tool-inspector.html renders the real component's rows and line numbers.
const editDiff = () => ({files:[{path:'/fixture/code.py',status:'modified',additions:1,deletions:1,hunks:[{oldStart:124,oldLines:3,newStart:124,newLines:3,
  lines:[' before','-old','+new 雪',' after']}]}],source:'claude-structured-patch',complete:true});
const patchDiff = () => ({files:[{path:'/fixture/code.py',status:'modified',additions:1,deletions:1,hunks:[{oldStart:900,oldLines:1,newStart:900,newLines:1,
  lines:['-tail','\\ No newline at end of file','+TAIL','\\ No newline at end of file']}]},{path:'/fixture/new.txt',status:'added',additions:1,deletions:0,
  hunks:[{oldStart:0,oldLines:0,newStart:1,newLines:1,lines:['+hello']}]}],source:'opencode-unified-patch',complete:true});
const shellDiff = () => ({files:[],source:'unavailable',complete:false,reason:'Shell command; no structured edit diff recorded (it may have edited files).'});
function diffReaders(f) {
  const log = { mounts: [], updates: [], wraps: [], destroys: 0 };
  f.ctx.opcDiffView = { mount(container, diff, options) {
    const reader = { container, diff, options, alive: true, update(value) { log.updates.push(value); this.diff = value; },
      setLayout() {}, setWrap(value) { log.wraps.push(value); }, destroy() { this.alive = false; log.destroys++; } };
    log.mounts.push(reader); return reader;
  } };
  f.el('inspector-diff').hidden = true; // index.html's markup state
  return log;
}

test('A3: native snapshot.diff reaches ONE shared reader above Input/Output; raw Copy/counts unchanged under every toggle', async () => {
  const f = fixture(), log = diffReaders(f), raw = '{"file_path":"/fixture/code.py","old_string":"old","new_string":"new 雪"}';
  const data = nativeRecord(raw, 'The file has been updated.'); data.snapshot.diff = editDiff();
  f.mailbox(nativeMail()); f.response(data); await f.api.pollInspection();
  assert.equal(log.mounts.length, 1); const reader = log.mounts[0];
  assert.equal(reader.container, f.el('inspector-diff')); assert.equal(f.el('inspector-diff').hidden, false);
  assert.deepEqual(JSON.parse(JSON.stringify(reader.diff)), editDiff());
  assert.deepEqual(JSON.parse(JSON.stringify(reader.options)), { layout: 'auto', wrap: true });
  assert.equal(f.api.state().snapshot.diff, reader.diff); // carried as served, never rebuilt
  const count = f.api.state().snapshot.inputBytes, label = f.el('inspector-input-label').textContent;
  for (const pretty of [false,true]) for (const readable of [false,true]) for (const wrap of [false,true]) {
    for (const kind of ['input','output']) {
      f.el('inspector-pretty-'+kind).checked = pretty; f.el('inspector-pretty-'+kind).onchange();
      f.el('inspector-readable-'+kind).checked = readable; f.el('inspector-readable-'+kind).onchange();
      f.el('inspector-wrap-'+kind).checked = wrap; f.el('inspector-wrap-'+kind).onchange();
      await f.el('inspector-copy-'+kind).onclick();
    }
    assert.deepEqual(f.copies.slice(-2), [raw, 'The file has been updated.']);
    assert.equal(f.api.state().snapshot.inputBytes, count);
  }
  assert.equal(f.el('inspector-input-label').textContent, label);
  assert.equal(log.mounts.length, 1); assert.equal(log.updates.length, 0); assert.equal(log.destroys, 0);
  // The Diff's Wrap follows Input's Word wrap only; Output's stays independent.
  assert.deepEqual(log.wraps, [false, true, false, true, false, true, false, true]);
  assert.match(f.el('inspector-wrap-input').title, /Diff/); assert.doesNotMatch(f.el('inspector-wrap-output').title, /Diff/);
});

test('A3: OpenCode takes the diff the server put BESIDE the part, never part.diff or anything derived from the part', async () => {
  const f = fixture(), log = diffReaders(f), state = { status: 'completed', input: { patchText: '*** Begin Patch' }, output: 'Success',
    metadata: { files: [{ file: '/fixture/code.py', patch: '@@ -900 +900 @@\n-tail\n+TAIL' }] } };
  f.response({ ...record(state), diff: patchDiff() }); await f.api.pollInspection();
  assert.equal(log.mounts.length, 1); assert.deepEqual(JSON.parse(JSON.stringify(log.mounts[0].diff)), patchDiff());
  assert.equal(f.api.state().snapshot.input, JSON.stringify(state.input)); // toolSnapshotText still owns raw text
  // A part that merely carries metadata/patches (or a stray part.diff) is never a diff source.
  const g = fixture(), none = diffReaders(g), part = record(state); part.part.diff = patchDiff();
  g.response(part); await g.api.pollInspection();
  assert.equal(none.mounts.length, 0); assert.equal(g.el('inspector-diff').hidden, true); assert.equal(g.el('inspector-diff').textContent, '');
  assert.equal(g.api.state().snapshot.diff, null);
});

test('A3: the same diff data in the native and OpenCode branches reaches the reader identically', async () => {
  const seen = [];
  for (const native of [true, false]) {
    const f = fixture(), log = diffReaders(f);
    if (native) { const d = nativeRecord('{}', 'ok'); d.snapshot.diff = editDiff(); f.mailbox(nativeMail()); f.response(d); }
    else f.response({ ...record({ status: 'completed', input: {}, output: 'ok' }), diff: editDiff() });
    await f.api.pollInspection(); seen.push(JSON.stringify(log.mounts[0].diff));
  }
  assert.equal(seen[0], seen[1]); assert.equal(seen[0], JSON.stringify(editDiff()));
});

test('A3: live polls update the one mounted reader in place (no remount); unchanged renders pass nothing again', async () => {
  const f = fixture(), log = diffReaders(f), pending = { files: [], source: 'unavailable', complete: false, reason: 'pending: no result recorded yet' };
  const first = hostRecord('{"file_path":"/fixture/code.py"}', null); first.snapshot.diff = pending;
  f.mailbox({ ...nativeMail(), selection: first.selection }); f.response(first); await f.api.pollInspection();
  assert.equal(log.mounts.length, 1); assert.equal(log.mounts[0].diff.reason, 'pending: no result recorded yet');
  const again = hostRecord('{"file_path":"/fixture/code.py"}', null); again.snapshot.diff = pending;
  f.response(again); f.advance(); await f.api.pollInspection();
  assert.equal(log.mounts.length, 1); assert.equal(log.updates.length, 1); // the reader itself skips unchanged data
  for (let i = 0; i < 5; i++) { f.api.render(); f.listeners.get('selectionchange')(); }
  assert.equal(log.updates.length, 1);
  const done = hostRecord('{"file_path":"/fixture/code.py"}', 'updated', { terminal: true, inputFinal: true }); done.snapshot.diff = editDiff();
  f.response(done); f.advance(); await f.api.pollInspection();
  assert.equal(log.mounts.length, 1); assert.deepEqual(JSON.parse(JSON.stringify(log.updates.at(-1))), editDiff());
  assert.equal(log.destroys, 0); assert.equal(f.api.state().terminal, true);
});

test('A3: a new selection drops the old call\'s reader; an old read arriving late never mounts under the new call', async () => {
  const f = fixture(), log = diffReaders(f);
  f.response({ ...record({ status: 'completed', input: {}, output: 'ok' }), diff: editDiff() }); await f.api.pollInspection();
  assert.equal(log.mounts.length, 1);
  f.api.selectInspection(mail(2, 'prt_b'));
  assert.equal(log.destroys, 1); assert.equal(log.mounts[0].alive, false);
  assert.equal(f.el('inspector-diff').hidden, true); assert.equal(f.el('inspector-diff').textContent, '');
  f.response(record({ status: 'completed', input: 'plain', output: 'no diff' }, 2, 'prt_b')); await f.api.readInspection();
  assert.equal(log.mounts.length, 1); assert.equal(f.el('inspector-diff').hidden, true);
  let release; f.hook(() => new Promise(resolve => { release = resolve; }));
  f.api.selectInspection(mail(3, 'prt_c')); const late = f.api.readInspection(); await new Promise(setImmediate);
  f.api.selectInspection(mail(4, 'prt_d'));
  release({ ...record({ status: 'completed', input: {}, output: 'late' }, 3, 'prt_c'), diff: patchDiff() }); await late;
  assert.equal(log.mounts.length, 1); assert.equal(f.api.state().snapshot, null); assert.equal(f.el('inspector-diff').hidden, true);
});

test('A3: honest reasons are passed through; null hides; a vanished diff clears the region', async () => {
  const f = fixture(), log = diffReaders(f);
  f.response({ ...record({ status: 'completed', input: { command: 'sed -i s/a/b/ f' }, output: '' }), diff: shellDiff() }); await f.api.pollInspection();
  assert.deepEqual(JSON.parse(JSON.stringify(log.mounts[0].diff)), shellDiff()); assert.equal(f.el('inspector-diff').hidden, false);
  f.response({ ...record({ status: 'completed', input: {}, output: '' }), diff: null }); f.el('inspector-retry').onclick(); await new Promise(setImmediate);
  assert.equal(log.destroys, 1); assert.equal(f.el('inspector-diff').hidden, true);
});

test('A3: without the reader script, or when it throws on an odd shape, raw Input/Output stay usable under a visible note', async () => {
  const f = fixture(); f.el('inspector-diff').hidden = true;
  f.response({ ...record({ status: 'completed', input: 'kept input', output: 'kept output' }), diff: editDiff() }); await f.api.pollInspection();
  assert.match(f.el('inspector-diff').textContent, /^Diff display unavailable \(diff-view\.js did not load\)/);
  assert.equal(f.el('inspector-diff').hidden, false); assert.equal(f.el('inspector-output').textContent, 'kept output');
  await f.el('inspector-copy-input').onclick(); assert.equal(f.copies.at(-1), 'kept input');
  const g = fixture(); g.el('inspector-diff').hidden = true;
  g.ctx.opcDiffView = { mount() { throw new Error('files is not iterable'); } };
  g.response({ ...record({ status: 'completed', input: 'odd', output: 'shape' }), diff: { files: 7 } }); await g.api.pollInspection();
  assert.match(g.el('inspector-diff').textContent, /Diff display unavailable: files is not iterable/);
  assert.equal(g.el('inspector-output').textContent, 'shape'); assert.equal(g.api.state().snapshot.diff.files, 7);
});
