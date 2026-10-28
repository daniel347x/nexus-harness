const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../session-focus.js'), 'utf8');
const PANE = 'http://127.0.0.1:9911/api/focus-request';

// session-focus.js in a VM with a scripted pane and a page that has the given workspace-tab folders (no DOM clicks
// here: tests/session-focus.html drives a real CodeNomad-shaped DOM).
function harness({ folders = [], request = null, claimed = true, visibility = 'visible' } = {}) {
  const calls = [], loops = [];
  const tabs = folders.map((title) => ({ getAttribute: (k) => (k === 'title' ? title : k === 'aria-selected' ? 'false' : null), getClientRects: () => [1], click() {} }));
  const context = {
    document: { visibilityState: visibility, querySelectorAll: (q) => (q === 'button.tab-base[role="tab"][title]' ? tabs : []) },
    crypto: { randomUUID: () => 'fixture' }, CSS: { escape: (s) => s }, AbortSignal: { timeout: () => ({}) }, Event: class {},
    Date, setTimeout: (cb) => { loops.push(cb); return 1; },
    fetch: async (url, options) => {
      const body = options?.body ? JSON.parse(options.body) : undefined;
      calls.push({ url, body });
      if (url.startsWith(PANE + '?')) return { ok: true, json: async () => ({ schemaVersion: 1, request }) };
      if (url === PANE + '/claim') return { ok: true, json: async () => ({ claimed }) };
      return { ok: true, json: async () => ({ accepted: true }) };
    },
  };
  context.globalThis = context;
  vm.createContext(context); vm.runInContext(source, context);
  return { api: context.opcSessionFocus, calls, tabs, next: async () => { await new Promise((r) => setImmediate(r)); const cb = loops.shift(); if (cb) cb(); await new Promise((r) => setImmediate(r)); } };
}
const settle = () => new Promise((r) => setImmediate(r));
const req = (over) => ({ nonce: 'nonce-0001', sessionId: 'ses_abcdefgh12', directory: '/mnt/e/repos/quick-worker', title: 't', state: 'pending', ...over });

test('folders are compared in one spelling; the most specific tab holding the session folder wins', () => {
  const { api } = harness();
  assert.equal(api.normalizeFolder('E:\\__daniel347x\\repos\\x\\'), '/mnt/e/__daniel347x/repos/x');
  assert.equal(api.normalizeFolder('/home/dan//work/'), '/home/dan/work');
  const tab = (title) => ({ getAttribute: (k) => (k === 'title' ? title : null) });
  const home = tab('/home/dan'), repo = tab('/mnt/e/repos/quick-worker'), win = tab('E:\\repos\\manager');
  assert.equal(api.matchTab([home, repo, win], '/mnt/e/repos/quick-worker'), repo);
  assert.equal(api.matchTab([home, repo, win], '/mnt/e/repos/quick-worker/.worktrees/a'), repo, 'a worktree under the folder');
  assert.equal(api.matchTab([home, repo, win], '/mnt/e/repos/manager'), win, 'a Windows-spelled tab folder');
  assert.equal(api.matchTab([home, repo, win], '/home/dan'), home);
  assert.equal(api.matchTab([home, repo], '/mnt/e/repos/quick-worker-2'), null, 'a sibling with a longer name is not inside');
  assert.equal(api.matchTab([home], 'relative/path'), null);
});

test('the selectors are pinned to CodeNomad 0.20.1 and the contract never reaches a prompt or model control', () => {
  const { api } = harness();
  assert.equal(api.CODENOMAD, '0.20.1');
  assert.equal(api.SELECTORS.tab, 'button.tab-base[role="tab"][title]');
  assert.equal(api.SELECTORS.rowSelect, 'button.session-item-select');
  assert.doesNotMatch(source, /prompt-input|data-model-selector|data-thinking-selector|\/api\/session\/[^'"]*\/prompt|\bsend\(/i);
});

test('a page without the folder says no-tab once and never claims; the request stays for a page that has it', async () => {
  const h = harness({ folders: ['/home/dan'], request: req() });
  await settle(); await h.next(); await h.next();
  const posted = h.calls.filter((c) => c.body);
  assert.deepEqual(posted.map((c) => [c.url, c.body.code]), [[PANE + '/report', 'no-tab']]);
  assert.deepEqual(posted[0].body.folders, ['/home/dan']);
  assert.ok(h.calls[0].url.startsWith(PANE + '?watcherId=focus-'), 'polls with its own watcher id');
});

test('a page with the folder claims (saying whether it is visible) and acts once; a claim lost to another page does nothing', async () => {
  const hidden = harness({ folders: ['/mnt/e/repos/quick-worker'], request: req(), claimed: false, visibility: 'hidden' });
  await settle(); await hidden.next();
  const claims = hidden.calls.filter((c) => c.url === PANE + '/claim');
  assert.ok(claims.length >= 1 && claims.every((c) => c.body.visible === false && c.body.nonce === 'nonce-0001'));
  assert.equal(hidden.calls.filter((c) => c.url === PANE + '/report').length, 0, 'not claimed: no clicks, no report');
  const done = harness({ folders: ['/mnt/e/repos/quick-worker'], request: req({ state: 'claimed' }) });
  await settle();
  assert.equal(done.calls.filter((c) => c.body).length, 0, 'only a pending request is taken');
});
