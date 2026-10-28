const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../tool-inspect.js'), 'utf8');
const ENDPOINT = 'http://127.0.0.1:9911/api/tool-selection';

class Element {
  constructor(className = '') {
    this.className = className; this.children = []; this.dataset = {}; this.listeners = {}; this.attributes = {};
    this.textContent = ''; this.title = ''; this.isConnected = true;
    this.classList = { contains: value => this.className.split(' ').includes(value) };
  }
  insertBefore(element, before) {
    const index = before ? this.children.indexOf(before) : this.children.length;
    this.children.splice(index, 0, element); element.parentElement = this;
  }
  get nextSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  getClientRects() { return this.hidden ? [] : [{}]; }
  getAttribute(name) { return name.startsWith('data-') ? this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] ?? null : this.attributes[name] ?? null; }
  hasAttribute(name) { return this.getAttribute(name) !== null; }
  removeAttribute(name) { delete this.attributes[name]; }
  setAttribute(name, value) {
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
    else this.attributes[name] = value;
    if (name === 'title') this.title = value;
  }
  matches(selector) {
    return selector.split(',').some(s => {
      s = s.trim();
      if (s.startsWith('.')) return this.classList.contains(s.slice(1));
      if (s.startsWith('[')) { const [, name, value] = s.match(/^\[([^=]+)="([^"]*)"\]$/) || []; return name && this.getAttribute(name) === value; }
      return this.tagName === s;
    });
  }
  closest(selector) {
    return this.matches(selector) ? this : this.parentElement?.closest(selector) || null;
  }
  querySelector(selector) {
    for (const child of this.children) {
      if (child.matches(selector)) return child;
      const descendant = child.querySelector(selector);
      if (descendant) return descendant;
    }
    return null;
  }
  click(options = {}) {
    if (this.disabled) return;
    const event = { target: this, currentTarget: this, button: 0, ...options,
      preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() { this.stopped = true; },
      stopPropagation() { this.stopped = true; } };
    let root = this;
    while (root.parentElement) root = root.parentElement;
    root.captureClick?.(event);
    if (!event.stopped) this.listeners.click?.(event);
    return event;
  }
}

function row(partId = 'part', messageId = 'message', sessionId = 'session', instanceId = 'instance') {
  const r = new Element('tool-call'), header = new Element('tool-call-header');
  r.dataset = { partId, messageId, sessionId, instanceId };
  r.insertBefore(header, null);
  const toggle = new Element('tool-call-header-toggle'); toggle.tagName = 'button'; toggle.setAttribute('aria-expanded', 'false');
  toggle.insertBefore(new Element('opc-tool-argument-preview'), null);
  header.insertBefore(toggle, null);
  const action = new Element('native-action'); action.tagName = 'button'; header.insertBefore(action, null);
  r.header = header;
  r.output = '<img src=x onerror=alert(1)>\n' + 'literal full output\n'.repeat(2000);
  return r;
}

function records({ instanceId = 'instance', sessionId = 'session', messageId = 'message', partId = 'part' } = {}) {
  return {
    workspace: { id: instanceId, proxyPath: `/workspaces/${encodeURIComponent(instanceId)}/instance`, name: 'Fixture workspace' },
    session: { data: { id: sessionId, title: 'Fixture session', time: { created: 101 }, location: { directory: '/local/project' } } },
    message: { data: { id: messageId, time: { created: 202 }, content: [{ id: partId, type: 'tool', name: 'bash', callID: 'call',
      state: { input: { command: '<img src=x>' }, output: 'unsafe'.repeat(4000) } }] } },
  };
}

function json(value, headers) {
  return new Response(JSON.stringify(value), { headers });
}

function harness(rows = [row()], fetcher, origin = 'http://127.0.0.1:9899') {
  const requests = [], timers = new Map();
  let serial = 0, observer, now = 1000;
  const pane = new Element('session-cache-pane'); Object.assign(pane.dataset, { instanceId: 'instance', sessionId: 'session' });
  let captureClick;
  const attach = () => { for (const r of rows) { if (!r.parentElement) r.parentElement = pane; r.parentElement.captureClick = captureClick; } };
  const panes = [pane];
  const document = { hidden: false, documentElement: {}, querySelectorAll: selector => selector === '.tool-call' ? rows : panes.filter(p => !p.hidden),
    createElement: () => { const el = new Element(); el.tagName = 'button'; return el; },
    addEventListener(name, fn) { if (name === 'click') captureClick = fn; } };
  const context = { document, location: { origin }, crypto: { randomUUID: () => 'fixture-selector' },
    Date: { now: () => now }, TextDecoder, AbortController,
    setTimeout: (fn, delay) => { const id = ++serial; timers.set(id, { fn, delay }); return id; },
    clearTimeout: id => timers.delete(id),
    MutationObserver: class { constructor(fn) { observer = fn; } observe() {} },
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (fetcher) return fetcher(url, options, requests.length);
      if (url === ENDPOINT) return new Response('{}');
      const r = records(rows.find(r => url.includes(encodeURIComponent(r.dataset.messageId)))?.dataset || rows[0].dataset);
      return json(url.startsWith('/api/') ? r.workspace : url.includes('/message/') ? r.message : r.session);
    },
  };
  vm.runInNewContext(source, context);
  attach();
  return { rows, requests, document, timers, pane, panes,
    mutate: () => observer?.(), now: value => { now = value; },
    posts: () => requests.filter(r => r.url === ENDPOINT).map(r => JSON.parse(r.options.body)),
    button: (index = 0) => rows[index].querySelector('.opc-tool-toggle'),
    inspect: (index = 0) => rows[index].querySelector('.tool-call-header-toggle'),
    state: (index = 0) => rows[index].header.dataset.inspectState,
    selected: (index = 0) => rows[index].header.dataset.inspectSelected === 'true',
    async tick() {
      for (let i = 0; i < 8; i++) {
        attach();
        for (const [id, t] of [...timers]) if (t.delay < 1000) { timers.delete(id); t.fn(); }
        await new Promise(resolve => setImmediate(resolve));
      }
    },
    expire() { for (const [id, t] of [...timers]) if (t.delay === 10000) { timers.delete(id); t.fn(); } },
  };
}

test('idempotent Toggle sibling, native state transition and actions independent, no relay on mounts', async () => {
  const h = harness(); await h.tick();
  const button = h.button(), toggle = h.rows[0].querySelector('.tool-call-header-toggle');
  assert.equal(button.parentElement, toggle.parentElement);
  assert.equal(toggle.nextSibling, button);
  assert.equal(button.type, 'button');
  assert.equal(button.textContent, 'Toggle'); assert.equal(button.title, 'Toggle expanded state');
  assert.equal(button.getAttribute('aria-label'), 'Toggle expanded state');
  assert.equal(toggle.hasAttribute('aria-expanded'), false);
  let toggled = 0, copied = 0;
  toggle.addEventListener('click', () => { toggled++; toggle.setAttribute('aria-expanded', 'true'); });
  h.rows[0].querySelector('.native-action').addEventListener('click', () => copied++);
  button.click(); h.rows[0].querySelector('.native-action').click(); await h.tick();
  assert.equal(toggled, 1); assert.equal(copied, 1);
  assert.equal(button.getAttribute('aria-expanded'), 'true');
  for (let i = 0; i < 5; i++) { h.mutate(); await h.tick(); }
  assert.equal(h.button(), button);
  h.rows.push(row('next')); h.rows[0] = row(); h.mutate(); await h.tick();
  assert.ok(h.button()); assert.ok(h.button(1));
  assert.equal(h.requests.length, 0);
});

test('deliberate click sends validated witness only, authenticated source and credential-free fixed relay', async () => {
  const h = harness(); await h.tick();
  const original = h.rows[0].output;
  assert.equal(h.inspect().click().stopped, true); await h.tick();
  assert.equal(h.posts().length, 1);
  assert.deepEqual(h.posts()[0], { origin: 'http://127.0.0.1:9899', instanceId: 'instance', sessionId: 'session',
    messageId: 'message', partId: 'part', selectorId: 'fixture-selector', sequence: 1, observedAt: 1000,
    workspaceName: 'Fixture workspace', sessionTitle: 'Fixture session', toolName: 'bash',
    association: { sessionCreated: 101, directory: '/local/project', messageCreated: 202, toolName: 'bash', callId: 'call' } });
  assert.equal(h.rows[0].output, original); assert.ok(original.length > 10000);
  assert.equal(h.state(), 'selected'); assert.equal(h.selected(), true);
  assert.equal(h.requests.length, 4);
  for (const request of h.requests.slice(0, 3)) {
    assert.equal(request.options.credentials, 'include'); assert.equal(request.options.redirect, 'error');
    assert.equal(request.options.method, undefined);
  }
  const options = h.requests[3].options;
  assert.equal(options.method, 'POST'); assert.equal(options.headers['Content-Type'], 'text/plain');
  assert.equal(options.credentials, 'omit'); assert.ok(!options.body.includes('state'));
  for (let i = 0; i < 4; i++) { h.mutate(); await h.tick(); }
  assert.equal(h.requests.length, 4); // Successful click cannot reopen a subsequently closed inspector.
});

test('row reuse reads fresh click identity before a scan and resets old selected receipt', async () => {
  const r = row(), h = harness([r]); await h.tick();
  h.inspect().click(); await h.tick();
  r.dataset = { instanceId: 'i space', sessionId: 's/other', messageId: 'new-message', partId: 'new-part' };
  Object.assign(h.pane.dataset, { instanceId: 'i space', sessionId: 's/other' });
  h.inspect().click(); await h.tick();
  assert.equal(h.posts()[1].instanceId, 'i space'); assert.equal(h.posts()[1].partId, 'new-part');
  assert.ok(h.requests.some(r => r.url === '/api/workspaces/i%20space'));
  assert.ok(h.requests.some(r => r.url.includes('/session/s%2Fother/message/new-message')));
  r.dataset.partId = 'third'; h.mutate(); await h.tick();
  assert.equal(h.state(), 'idle'); assert.equal(h.selected(), false); assert.equal(h.posts().length, 2);
});

test('selector sequence and deliberate timestamp are monotonic even with wall-clock rollback', async () => {
  const h = harness(undefined, undefined, 'http://localhost:9899'); await h.tick();
  h.inspect().click(); await h.tick(); h.now(500); h.inspect().click(); await h.tick();
  assert.equal(h.posts()[1].observedAt, 1001); assert.equal(h.posts()[1].sequence, 2);
  assert.equal(h.posts()[1].selectorId, h.posts()[0].selectorId);
  assert.equal(h.posts()[1].origin, 'http://localhost:9899');
});

test('latest wins stale asynchronous preparation, one active preparation, intermediate clicks coalesce', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const r = row(), h = harness([r], async (url, options, count) => {
    if (count === 1) await gate; // Deliberately ignore abort, exercising generation guard.
    if (url === ENDPOINT) return new Response('{}');
    const data = records(r.dataset);
    return json(url.startsWith('/api/') ? data.workspace : url.includes('/message/') ? data.message : data.session);
  });
  await h.tick(); h.inspect().click(); await h.tick();
  r.dataset.partId = 'middle'; h.inspect().click();
  r.dataset.partId = 'last'; h.inspect().click(); await h.tick();
  assert.equal(h.requests.length, 1); assert.equal(h.posts().length, 0);
  release(); await h.tick();
  assert.equal(h.posts().length, 1); assert.equal(h.posts()[0].partId, 'last'); assert.equal(h.posts()[0].sequence, 3);
});

test('in-flight older POST finishes before latest POST, old success never becomes selected receipt', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let writes = 0;
  const r = row(), h = harness([r], async url => {
    if (url === ENDPOINT) { if (++writes === 1) await gate; return new Response('{}'); }
    const data = records(r.dataset);
    return json(url.startsWith('/api/') ? data.workspace : url.includes('/message/') ? data.message : data.session);
  });
  await h.tick(); h.inspect().click(); await h.tick();
  assert.equal(h.posts().length, 1);
  r.dataset.partId = 'last'; h.inspect().click(); await h.tick();
  assert.equal(h.posts().length, 1); assert.equal(h.state(), 'pending');
  release(); await h.tick();
  assert.deepEqual(h.posts().map(p => p.sequence), [1, 2]);
  assert.equal(h.posts()[1].partId, 'last'); assert.equal(h.state(), 'selected');
});

for (const origin of ['https://127.0.0.1:9899', 'http://evil:9899', 'http://127.0.0.1:9898', 'http://localhost.evil:9899']) {
  test(`unsupported origin does nothing: ${origin}`, async () => {
    const h = harness(undefined, undefined, origin); await h.tick();
    assert.equal(h.button(), null); assert.equal(h.requests.length, 0);
  });
}

for (const [name, mutate] of [
  ['wrong workspace ID', r => { r.workspace.id = 'other'; }],
  ['external proxy', r => { r.workspace.proxyPath = 'https://evil.example'; }],
  ['remote workspace', r => { r.workspace.type = 'remote'; }],
  ['wrong session ID', r => { r.session.data.id = 'other'; }],
  ['absent directory', r => { r.session.data.location = {}; }],
  ['nonnumeric session time', r => { r.session.data.time.created = '101'; }],
  ['negative session time', r => { r.session.data.time.created = -101; }],
  ['wrong message ID', r => { r.message.data.id = 'other'; }],
  ['wrong message session', r => { r.message.data.sessionID = 'ses_other'; }],
  ['absent message time', r => { delete r.message.data.time.created; }],
  ['fractional message time', r => { r.message.data.time.created = 202.5; }],
  ['wrong part type', r => { r.message.data.content[0].type = 'text'; }],
  ['duplicate part', r => { r.message.data.content.push(r.message.data.content[0]); }],
  ['missing tool name', r => { delete r.message.data.content[0].name; }],
  ['unsupported call ID', r => { r.message.data.content[0].callID = {}; }],
]) {
  test(`invalid witness reports bounded sourceError to pane, never identity-only: ${name}`, async () => {
    const data = records(); mutate(data);
    const h = harness(undefined, async url => url === ENDPOINT ? new Response('{}')
      : json(url.startsWith('/api/') ? data.workspace : url.includes('/message/') ? data.message : data.session));
    await h.tick(); h.inspect().click(); await h.tick();
    const payload = h.posts()[0];
    assert.equal(h.posts().length, 1); assert.ok(payload.sourceError.length < 240);
    assert.equal(payload.association, undefined); assert.equal(payload.partId, 'part');
    assert.equal(h.state(), 'source-error'); assert.equal(h.selected(), true);
    assert.ok(h.requests.every(r => r.url.startsWith('/') || r.url === ENDPOINT));
  });
}

test('missing callID is explicit null and unavailable optional labels are harmless', async () => {
  const data = records(); delete data.message.data.content[0].callID; delete data.workspace.name; delete data.session.data.title;
  const h = harness(undefined, async url => url === ENDPOINT ? new Response('{}')
    : json(url.startsWith('/api/') ? data.workspace : url.includes('/message/') ? data.message : data.session));
  await h.tick(); h.inspect().click(); await h.tick();
  assert.equal(h.posts()[0].association.callId, null); assert.equal(h.posts()[0].sourceError, undefined);
});

test('source login/offline failures still post click; relay failures visibly fail and only a new click retries', async () => {
  let failed = true;
  const h = harness(undefined, async url => {
    if (url === ENDPOINT) { if (failed) throw new Error('offline'); return new Response('{}'); }
    return new Response('login', { status: 401 });
  });
  await h.tick(); h.inspect().click(); await h.tick();
  assert.equal(h.posts().length, 1); assert.match(h.posts()[0].sourceError, /401/);
  assert.equal(h.state(), 'failed'); assert.match(h.inspect().title, /retry/); assert.equal(h.selected(), false);
  h.mutate(); await h.tick(); assert.equal(h.posts().length, 1);
  failed = false; h.inspect().click(); await h.tick();
  assert.equal(h.posts().length, 2); assert.equal(h.posts()[1].sequence, 2);
  assert.equal(h.state(), 'source-error');
});

for (const useHeader of [true, false]) {
  test(`source read 16 MiB bound enforced ${useHeader ? 'before body' : 'on chunked body'}`, async () => {
    let cancelled = false;
    const h = harness(undefined, async url => {
      if (url === ENDPOINT) return new Response('{}');
      return new Response(new ReadableStream({ start(c) { c.enqueue(new Uint8Array(16 * 1024 * 1024 + 1)); },
        cancel() { cancelled = true; } }), useHeader ? { headers: { 'content-length': String(16 * 1024 * 1024 + 1) } } : {});
    });
    await h.tick(); h.inspect().click(); await h.tick();
    assert.match(h.posts()[0].sourceError, /16 MiB/); assert.equal(h.requests.length, 2);
    assert.equal(cancelled, true);
  });
}

test('source timeout becomes sourceError and relay timeout is visible with no automatic retry', async () => {
  let blockPost = false;
  const h = harness(undefined, (url, options) => {
    if (url === ENDPOINT && !blockPost) return Promise.resolve(new Response('{}'));
    return new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('timeout'))));
  });
  await h.tick(); h.inspect().click(); await h.tick(); h.expire(); await h.tick();
  assert.match(h.posts()[0].sourceError, /timed out/); assert.equal(h.state(), 'source-error');
  blockPost = true; h.inspect().click(); await h.tick(); h.expire(); await h.tick(); h.expire(); await h.tick();
  assert.equal(h.state(), 'failed');
  const count = h.requests.length; h.mutate(); await h.tick(); assert.equal(h.requests.length, count);
});

test('empty identities and detached headers do not relay; Toggle remains an independent native action', async () => {
  const h = harness(); await h.tick();
  h.rows[0].dataset.partId = ''; h.mutate(); await h.tick();
  h.inspect().click(); await h.tick(); assert.equal(h.requests.length, 0);
  h.rows[0].dataset.partId = 'part'; h.mutate(); await h.tick();
  h.rows[0].isConnected = false; h.inspect().click(); await h.tick(); assert.equal(h.requests.length, 0);
});

test('preview and header gap inspect once, keyboard click inspects, Toggle never inspects', async () => {
  const h = harness(); await h.tick();
  let native = 0;
  h.inspect().addEventListener('click', () => native++);
  h.inspect().querySelector('.opc-tool-argument-preview').click(); await h.tick();
  assert.equal(h.posts().length, 1); assert.equal(native, 0);
  h.rows[0].header.click(); await h.tick();
  assert.equal(h.posts().length, 2); assert.equal(native, 0);
  h.inspect().click({ detail: 0 }); await h.tick();
  assert.equal(h.posts().length, 3); assert.equal(native, 0);
  h.button().click({ detail: 0 }); await h.tick();
  assert.equal(h.posts().length, 3); assert.equal(native, 1);
});

test('selected identity survives whole-row replacement, new arrival and same-session absence without relay', async () => {
  const h = harness([row(), row('next')]); await h.tick();
  h.inspect().click(); await h.tick();
  assert.equal(h.selected(), true); assert.equal(h.selected(1), false);
  h.rows.splice(0, 2, row('part'), row('next'), row('arrival')); h.mutate(); await h.tick();
  assert.equal(h.selected(), true); assert.equal(h.selected(1), false); assert.equal(h.selected(2), false);
  assert.equal(h.posts().length, 1);
  h.rows.splice(0); h.mutate(); await h.tick();
  h.rows.push(row('next')); h.mutate(); await h.tick(); assert.equal(h.selected(), false);
  h.rows.push(row()); h.mutate(); await h.tick(); assert.equal(h.selected(1), true);
  assert.equal(h.posts().length, 1);
  h.inspect().click(); await h.tick();
  assert.equal(h.selected(), true); assert.equal(h.selected(1), false);
  h.inspect().click(); await h.tick(); assert.equal(h.posts().length, 3);
});

test('full identity prevents part-ID collisions; reused rows clear highlight before the next click', async () => {
  const h = harness([row(), row('part', 'other-message'), row('part', 'message', 'other-session'),
    row('part', 'message', 'session', 'other-instance')]); await h.tick();
  h.inspect().click(); await h.tick();
  assert.equal(h.selected(), true);
  for (let i = 1; i < h.rows.length; i++) assert.equal(h.selected(i), false);
  h.rows[0].dataset.messageId = 'other-message'; h.mutate(); await h.tick();
  assert.equal(h.selected(), false); assert.equal(h.posts().length, 1);
});

test('unknown or ambiguous active scope hides but retains; confirmed empty session switch clears permanently', async () => {
  const h = harness(); await h.tick(); h.inspect().click(); await h.tick();
  h.pane.hidden = true; h.mutate(); await h.tick(); assert.equal(h.selected(), false);
  h.inspect().click(); await h.tick(); assert.equal(h.posts().length, 1);
  h.pane.hidden = false; h.mutate(); await h.tick(); assert.equal(h.selected(), true);
  h.panes.push(new Element('session-cache-pane')); h.mutate(); await h.tick();
  assert.equal(h.selected(), false); h.inspect().click(); await h.tick(); assert.equal(h.posts().length, 1);
  h.panes.pop(); h.mutate(); await h.tick(); assert.equal(h.selected(), true);
  const original = h.rows.pop(); h.pane.dataset.sessionId = 'other-session'; h.mutate(); await h.tick();
  h.pane.dataset.sessionId = 'session'; h.rows.push(original); h.mutate(); await h.tick();
  assert.equal(h.selected(), false); assert.equal(h.state(), 'idle'); assert.equal(h.posts().length, 1);
  h.inspect().click(); await h.tick(); assert.equal(h.selected(), true);
});

test('instance switch clears visual selection, even when IDs otherwise collide', async () => {
  const h = harness(); await h.tick(); h.inspect().click(); await h.tick();
  h.pane.dataset.instanceId = 'different'; h.rows[0].dataset.instanceId = 'different'; h.mutate(); await h.tick();
  assert.equal(h.selected(), false);
  h.pane.dataset.instanceId = 'instance'; h.rows[0].dataset.instanceId = 'instance'; h.mutate(); await h.tick();
  assert.equal(h.selected(), false); assert.equal(h.posts().length, 1);
});

test('session switch during preparation leaves deliberate relay ordered but cannot resurrect a highlight', async () => {
  let release; const gate = new Promise(resolve => { release = resolve; });
  const h = harness(undefined, async (url, options, count) => {
    if (count === 1) await gate;
    if (url === ENDPOINT) return new Response('{}');
    const r = records(); return json(url.startsWith('/api/') ? r.workspace : url.includes('/message/') ? r.message : r.session);
  });
  await h.tick(); h.inspect().click(); await h.tick(); assert.equal(h.state(), 'pending');
  h.pane.dataset.sessionId = 'other-session'; h.mutate(); await h.tick(); assert.equal(h.selected(), false);
  release(); await h.tick(); assert.equal(h.posts().length, 1);
  h.pane.dataset.sessionId = 'session'; h.mutate(); await h.tick();
  assert.equal(h.state(), 'idle'); assert.equal(h.selected(), false);
});

test('new native headers route inspection before annotation; no per-row listeners are installed', async () => {
  const h = harness(); await h.tick();
  const replacement = row('replacement'); replacement.parentElement = h.pane; h.rows[0] = replacement;
  replacement.querySelector('.tool-call-header-toggle').click(); await h.tick();
  assert.equal(h.posts()[0].partId, 'replacement'); assert.equal(h.selected(), true);
  assert.deepEqual(h.button().listeners, {}); assert.deepEqual(h.inspect().listeners, {});
});
