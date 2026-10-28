const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = app.indexOf('async function loadSessions() {');
const end = app.indexOf('\nfunction renderSelect()', start);
assert.ok(start >= 0 && end > start, 'loadSessions must be available for the regression test');
const source = app.slice(start, end);

function pane(responses) {
  const banner = { textContent: '', className: '' };
  const context = {
    state: { sessions: [] },
    oc: async (method, route) => {
      assert.equal(method, 'GET');
      assert.equal(route, '/api/session?limit=100');
      const response = responses.shift();
      if (response instanceof Error) throw response;
      return { data: response };
    },
    $: (id) => {
      assert.equal(id, 'status');
      return banner;
    },
    status: (message, kind = '') => {
      banner.textContent = message;
      banner.className = 'status ' + kind;
    },
    renderSelect: () => {},
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, banner };
}

test('a successful session-list retry clears the earlier fetch failure', async () => {
  const { context, banner } = pane([new Error('Failed to fetch'), []]);
  await context.loadSessions();
  assert.equal(banner.textContent, 'session list failed: Failed to fetch');
  assert.equal(banner.className, 'status err');
  await context.loadSessions();
  assert.equal(banner.textContent, '');
  assert.equal(banner.className, 'status ');
});

test('a successful session-list retry preserves a newer action message', async () => {
  const { context, banner } = pane([new Error('Failed to fetch'), []]);
  await context.loadSessions();
  context.status('Refine failed: provider unavailable', 'err');
  await context.loadSessions();
  assert.equal(banner.textContent, 'Refine failed: provider unavailable');
  assert.equal(banner.className, 'status err');
});

test('a successful initial load preserves other status messages', async () => {
  const { context, banner } = pane([[]]);
  context.status('Sent', 'ok');
  await context.loadSessions();
  assert.equal(banner.textContent, 'Sent');
  assert.equal(banner.className, 'status ok');
});
