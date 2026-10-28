// Browser-side removal regression: the abandoned Claude terminal-input machinery must not
// come back. U06 reuses Send/control URL names for the independent registered Manager host,
// not terminal input; unregistered Claude targets still never fall through to OpenCode.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pane} = require('./pane-fixture.cjs');
const staticDir = path.join(__dirname, '..', 'static');
const app = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const style = fs.readFileSync(path.join(staticDir, 'style.css'), 'utf8');
const sessionsHtml = fs.readFileSync(path.join(staticDir, 'sessions.html'), 'utf8');

const REMOVED_APP_SYMBOLS = ['sendNative', 'nativeHash', 'nativeProse', 'nativeSendBlocked', 'nativeUnresolved',
  'renderNativeDelivery', 'pollNativeDelivery', 'initializeNativeDelivery', 'applyNativeDelivery', 'releaseNativeInput',
  'controlNative', 'applyNativeControl', 'nativeControlSupported', 'saveNativeReceipt', 'sameNativeSubmission',
  'checkedNativeOutcome', 'renderNativeControls', 'saveNativeControl', 'nativePersistence', 'nativeEffortNoop',
  'nativeControlValue', 'noteNativeConversation', 'useCurrentClaudeConversation'];
const REMOVED_STATE_FIELDS = ['nativeBinding', 'nativeReceipt', 'nativeBusy', 'nativePolling', 'nativeArm', 'nativeReadError',
  'nativeStorageError', 'nativePollAfter', 'nativeBackoff', 'nativeControlReceipt', 'activeNativeSubmission'];
const REMOVED_ROUTES = ['/api/claude/arm', '/api/claude/release',
  '/api/claude/binding', '/api/claude/delivery'];
const REMOVED_ELEMENT_IDS = ['native-input', 'native-arm-send', 'native-release', 'native-current-conversation',
  'native-read-status', 'native-recover-text', 'native-model', 'native-effort', 'native-model-apply', 'native-effort-apply',
  'native-control-consent', 'native-state', 'native-blocked', 'native-delivery', 'native-controls', 'native-requested',
  'native-effective', 'native-control-state'];

test('no terminal-input code, controller-only routes, terminal polling or arm/control state remains in app.js', () => {
  for (const name of REMOVED_APP_SYMBOLS) assert.doesNotMatch(app, new RegExp(`\\b${name}\\b`), name);
  for (const field of REMOVED_STATE_FIELDS) assert.doesNotMatch(app, new RegExp(`state\\.${field}\\b`), field);
  for (const route of REMOVED_ROUTES) assert.equal(app.includes(route), false, route);
  assert.doesNotMatch(app, /setInterval\([^\n]*pollNativeDelivery/);
  assert.doesNotMatch(app, /pane_claude_receipt_v1|pane_claude_control_receipt_v1/);
  // New semantic entry points are separately tested against role/UUID/generation/receipt guards.
  assert.match(app, /async function sendClaude\(/); assert.match(app, /async function controlClaude\(/);
  // Retained Claude history surfaces stay.
  for (const kept of ['acceptClaudeTarget', 'validatedClaudeTarget', 'fetchClaudeTarget', 'handleTargetMessage',
    'initializeTargetState', 'pane_claude_target_v1', 'pane_claude_no_context_v1'])
    assert.equal(app.includes(kept), true, kept);
});

test('removed native markup and styles are gone from the real pane documents while retained markup stays', () => {
  for (const id of REMOVED_ELEMENT_IDS) assert.equal(indexHtml.includes(`id="${id}"`), false, id);
  for (const kept of ['backend-select', 'backend-status', 'target-select', 'target-pill', 'native-context-choice',
    'native-no-context', 'transcript', 'refine-btn', 'send-btn', 'restore-btn', 'recovery-btn', 'tool-inspector'])
    assert.equal(indexHtml.includes(`id="${kept}"`), true, kept);
  assert.doesNotMatch(style, /#native-input|native-actions|#native-controls|#native-requested|#native-blocked|#native-delivery/);
  assert.match(style, /#native-context-choice/);
  assert.doesNotMatch(sessionsHtml, /N1\/N2|cockpit|U3/);
  assert.match(sessionsHtml, /tool-record-text\.js.*tool-preview-core\.js.*sessions\.js/s);
});

test('Claude Send refuses explicitly with zero transport on a direct call, and the UI shows it unavailable', async () => {
  const f = pane();
  f.api.state.backend = 'claude';
  f.box.value = '  retained draft 😀  ';
  f.api.renderTarget();
  assert.equal(f.el('send-btn').disabled, true); // unqualified: no OpenCode fall-through possible
  assert.match(f.el('backend-status').textContent, /unavailable/);
  await f.api.send();
  assert.equal(f.requests.length, 0);
  assert.equal(f.box.value, '  retained draft 😀  ');
  assert.equal(f.storage.has('pane_originals_v1'), false);
  assert.match(f.el('status').textContent, /Claude Send unavailable/);
});

test('the real Send hotkey refuses in Claude mode with no OpenCode, controller or provider request', async () => {
  const f = pane();
  f.api.state.backend = 'claude';
  let handler = null;
  f.ctx.document.addEventListener = (name, fn) => { if (name === 'keydown') handler = fn; };
  f.api.wire();
  assert.ok(handler, 'wire() must still install the global keydown handler');
  f.box.value = 'hotkey draft';
  for (const key of [{key: 'Enter', ctrlKey: true}, {key: 'Enter', ctrlKey: true, altKey: true, shiftKey: true}, {key: 'F11', shiftKey: true}, {key: 'F4'}]) {
    handler({...key, preventDefault() {}, target: null});
    await Promise.resolve();
    assert.equal(f.requests.length, 0, JSON.stringify(key));
    assert.equal(f.box.value, 'hotkey draft', JSON.stringify(key));
    assert.match(f.el('status').textContent, /unavailable/, JSON.stringify(key));
  }
});

test('OpenCode Send still posts through the ordinary path after the removal', async () => {
  const f = pane();
  f.api.state.backend = 'opencode';
  f.api.state.target = {id: 'ses_manager_gpt', title: 'GPT Manager', agent: 'manager-gpt'};
  f.box.value = 'draft to OpenCode';
  f.ctx.confirm = () => { throw new Error('unexpected confirmation'); };
  await f.api.send();
  assert.equal(f.requests.some(([url]) => url === '/api/send'), true);
  assert.equal(f.box.value, '');
});
