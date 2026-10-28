// The Claude account control, offline: a VM/DOM double of the actual static/account-switch.js.
// No fetch, no credentials, no real timer, no browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/account-switch.js'), 'utf8');
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test('a historical successful switch never overrides the current selected label', async () => {
  const app = boot({ body: accounts({ selected: 'personal', operation: operation('active', 'work') }) });
  await tick();
  assert.equal(app.ui.current.textContent, 'Personal');
  assert.match(app.ui.status.textContent, /work active/i); // historical outcome, never today's selection ring
  assert.equal(app.ui.personal.getAttribute('data-active'), 'true');
  assert.equal(app.ui.work.getAttribute('data-active'), 'false');
});

function element(attrs = {}) {
  return {
    attrs: { ...attrs }, textContent: '', title: '', disabled: false, hidden: false, listeners: {}, children: [], className: '',
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    addEventListener(name, handler) { this.listeners[name] = handler; },
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = nodes; },
    get outerHTML() { return this.className + this.textContent + this.children.map(n=>n.outerHTML).join(''); },
    classList: { values: new Set(), toggle(name, on) { if(on)this.values.add(name);else this.values.delete(name); }, contains(name) { return this.values.has(name); } },
  };
}

function panel() {
  const current = element();
  const identity = element();
  const status = element();
  const personal = element({ 'data-account': 'personal' });
  const work = element({ 'data-account': 'work' });
  const buttons = [personal, work];
  const viewing = element(), switchButton = element(), cancel = element(), quotas = element(), quotaStatus = element(), refresh = element();
  const parts = {
    '[data-account-current]': current,
    '[data-account-identity]': identity,
    '[data-account-status]': status,
    '[data-account-viewing]': viewing,
    '[data-account-switch]': switchButton,
    '[data-account-cancel]': cancel,
    '[data-account-quotas]': quotas,
    '[data-account-quota-status]': quotaStatus,
    '[data-account-refresh]': refresh,
  };
  const root = {
    attrs: {},
    querySelector(selector) { return parts[selector] || null; },
    querySelectorAll(selector) { return selector === 'button[data-account]' ? buttons : []; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name] ?? null; },
  };
  return { root, current, identity, status, personal, work, buttons, viewing, switchButton, cancel, quotas, quotaStatus, refresh };
}

function accounts({ selected = 'personal', epoch = 4, available = true, installed = true, operation = null, error = null } = {}) {
  return {
    schemaVersion: 1, backend: 'claude-accounts', status: available ? 'ready' : 'unavailable',
    installed, available, labels: ['personal', 'work'],
    selected: available ? { label: selected, epoch, identity: { email: selected + '@example.test', plan: 'max' } } : null,
    accounts: [
      { label: 'personal', enrolled: true, identity: { email: 'personal@example.test', plan: 'max' }, active: selected === 'personal' },
      { label: 'work', enrolled: true, identity: { email: 'work@example.test', plan: 'max' }, active: selected === 'work' },
    ],
    operation, error,
  };
}

function operation(phase, account = 'work', detail = '') {
  return { id: 'op-1', account, fromAccount: 'personal', phase, detail,
    startedAt: 1, updatedAt: 2, finishedAt: phase === 'active' ? 3 : null, durationSeconds: 2, canCancel: phase === 'waiting' };
}

function quota(label, percent = label === 'work' ? 12 : 76) {
  return { schemaVersion: 1, backend: 'usage', now: Date.now()/1000, ageSeconds: 120, refreshing: false,
    claude: { ok:true, account:{label}, windows:[{label:'five-hour',percent,resets_at:Date.now()/1000+7200},
      {label:'weekly',percent:34,resets_at:Date.now()/1000+172800},{label:'Fable weekly',percent:56,resets_at:Date.now()/1000+172800}] } };
}

function quotaLimited(label) {
  const now = Date.now()/1000;
  return { schemaVersion: 1, backend: 'usage', now, ageSeconds: 700, refreshing: false, retryAt: now + 600,
    claude: { ok:false, stale:true, rate_limited:true, retry_at: now + 600, observed_at: now - 700,
      note:'Claude quota refresh is waiting for the provider\'s retry time; last reading kept.',
      error:'usage endpoint: HTTP 429', account:{label},
      windows:[{label:'five-hour',percent:88,resets_at:now+3600},{label:'weekly',percent:12,resets_at:now+86400}] } };
}

function quotaNoGood(label) {
  const now = Date.now()/1000;
  return { schemaVersion: 1, backend: 'usage', now, ageSeconds: null, refreshing: false, retryAt: now + 600,
    claude: { ok:false, stale:false, rate_limited:true, retry_at: now + 600,
      note:'Claude quota refresh is waiting for the provider\'s retry time. No reading yet.',
      error:'usage endpoint: HTTP 429', account:{label} } };
}

function boot({ routes, storage = new Map(), body = accounts(), quotaRoute } = {}) {
  const ui = panel();
  const requests = [], events = [], timers = [];
  let nextTimer = 1;
  const replies = routes ? [...routes] : [];
  const document = {
    readyState: 'complete',
    querySelectorAll(selector) { return selector === '[data-oc-accounts]' ? [ui.root] : []; },
    querySelector() { return null; },
    createElement() { return element(); },
    addEventListener() {},
    dispatchEvent(event) { events.push(event); return true; },
  };
  const sessionStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  };
  const context = {
    window: { crypto: { randomUUID: () => 'op-1' } },
    document,
    sessionStorage,
    CustomEvent: function CustomEvent(type, init) { this.type = type; this.detail = init && init.detail; },
    fetch: async (url, options) => {
      requests.push({ url, method: (options && options.method) || 'GET',
        body: options && options.body ? JSON.parse(options.body) : null });
      if (url.startsWith('/api/usage')) {
        const label = new URL(url, 'http://fixture').searchParams.get('account');
        const reply = quotaRoute ? await quotaRoute(label, url) : {status:200,body:quota(label)};
        return {ok:reply.status===200,status:reply.status,json:async()=>reply.body};
      }
      const next = replies.shift();
      const answer = await (typeof next === 'function' ? next(url, options) : next);
      if (answer instanceof Error) throw answer;
      const reply = answer || { status: 200, body: body };
      return { ok: reply.status >= 200 && reply.status < 300, status: reply.status, json: async () => reply.body };
    },
    setTimeout: (fn, ms) => { const id = nextTimer++; timers.push({ id, fn, ms }); return id; },
    clearTimeout: (id) => {
      const index = timers.findIndex((entry) => entry && entry.id === id);
      if (index !== -1) timers[index] = null;
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { ui, requests, events, storage, timers, api: context.window.OCAccounts,
    // The control's 2 s switch poll only: the 60 s idle refresh never runs in these tests.
    fire: async (maxMs = 5000) => {
      const index = timers.findIndex((entry) => entry && entry.ms <= maxMs);
      const entry = index === -1 ? null : timers.splice(index, 1)[0];
      if (entry) await entry.fn();
      await tick();
    } };
}

test('the mount rings the active login, selects its view and keeps both view buttons enabled', async () => {
  const page = boot();
  await tick();
  assert.equal(page.ui.current.textContent, 'Personal');
  assert.equal(page.ui.identity.textContent, 'personal@example.test · max');
  assert.equal(page.ui.personal.getAttribute('aria-pressed'), 'true');
  assert.equal(page.ui.work.getAttribute('aria-pressed'), 'false');
  assert.equal(page.ui.personal.disabled, false, 'the active account can be viewed');
  assert.equal(page.ui.work.disabled, false);
  assert.equal(page.ui.personal.getAttribute('data-active'), 'true');
  assert.equal(page.ui.switchButton.disabled, true);
  assert.equal(page.ui.status.textContent, '');
});

test('only explicit Switch posts the label and ID; active ring stays through waiting and switching', async () => {
  const page = boot({ routes: [
    { status: 200, body: accounts() },
    { status: 202, body: { schemaVersion: 1, backend: 'claude-accounts', operation: operation('waiting') } },
    { status: 200, body: { status: 'operation', operation: operation('switching') } },
    { status: 200, body: { status: 'operation', operation: operation('active') } },
    { status: 200, body: accounts({ selected: 'work', epoch: 5, operation: operation('active') }) },
  ] });
  await tick();
  page.ui.work.listeners.click();
  await tick();
  assert.equal(page.requests.filter(r=>r.method==='POST').length,0);
  assert.match(page.ui.viewing.textContent,/Viewing Work · NOT active; Personal stays active/);
  page.ui.switchButton.listeners.click();
  await tick();
  const post = page.requests.find((request) => request.method === 'POST');
  assert.deepEqual(post.body, { schemaVersion: 1, backend: 'claude', account: 'work', operationId: 'op-1' });
  assert.deepEqual(Object.keys(post.body).sort(), ['account', 'backend', 'operationId', 'schemaVersion']);
  assert.equal(page.ui.personal.disabled, false);
  assert.equal(page.ui.work.disabled, false);
  assert.equal(page.ui.switchButton.disabled, true);
  assert.equal(page.ui.personal.getAttribute('data-active'), 'true');
  assert.equal(page.ui.cancel.hidden, false);
  assert.match(page.ui.status.textContent, /Waiting for running Claude work to finish/);

  await page.fire();
  assert.match(page.ui.status.textContent, /Switching to Work/);
  assert.match(page.ui.status.textContent,/Too late to cancel/);
  assert.equal(page.ui.cancel.hidden,true);
  await page.fire();
  assert.equal(page.ui.status.textContent, 'Work active');
  assert.equal(page.events.at(-1).type, 'oc-account-switch');
  assert.equal(page.events.at(-1).detail.phase, 'active');
  assert.equal(page.events.at(-1).detail.account, 'work');
  assert.equal(page.storage.has('pane_claude_account_switch_v1'), false);
  assert.equal(page.ui.work.getAttribute('aria-pressed'), 'true');
});

test('a lost POST reply polls the same operation instead of posting twice', async () => {
  const page = boot({ routes: [
    { status: 200, body: accounts() },
    new Error('socket closed'),
    { status: 200, body: { status: 'operation', operation: operation('active') } },
    { status: 200, body: accounts({ selected: 'work', epoch: 5 }) },
  ] });
  await tick();
  page.ui.work.listeners.click();
  page.ui.switchButton.listeners.click();
  await tick();
  const posts = page.requests.filter((request) => request.method === 'POST');
  assert.equal(posts.length, 1);
  const poll = page.requests.find((request) => request.url.includes('operationId='));
  assert.match(poll.url, /operationId=op-1$/);
  await tick();
  assert.equal(page.ui.status.textContent, 'Work active');
});

test('a reload reads the operation its tab remembered instead of posting again', async () => {
  const storage = new Map([['pane_claude_account_switch_v1', JSON.stringify({ id: 'op-1' })]]);
  const page = boot({ storage, routes: [
    { status: 200, body: accounts() },
    { status: 200, body: { status: 'operation', operation: operation('switching') } },
  ] });
  await tick();
  assert.equal(page.requests.filter((request) => request.method === 'POST').length, 0);
  assert.ok(page.requests.some(r=>r.url==='/api/claude/account-switch?operationId=op-1'));
  assert.match(page.ui.status.textContent, /Switching to Work/);
});

test('an unavailable selector disables switching, not viewing, and says why', async () => {
  const page = boot({ routes: [
    { status: 200, body: accounts({ available: false, installed: false, error: 'the claude_accounts command is not installed in this checkout' }) },
  ] });
  await tick();
  assert.equal(page.ui.personal.disabled, false);
  assert.equal(page.ui.work.disabled, false);
  assert.equal(page.ui.switchButton.disabled,true);
  assert.equal(page.ui.current.textContent, '—');
  assert.match(page.ui.status.textContent, /not installed/);
  assert.equal(page.ui.root.getAttribute('data-phase'), 'unavailable');
});

test('before the one-time enrollment it says how to enrol, and starts nothing', async () => {
  const page = boot({ routes: [{ status: 200, body: {
    schemaVersion: 1, backend: 'claude-accounts', status: 'ready', installed: true, available: true,
    labels: ['personal', 'work'], selected: { label: 'default', epoch: 0, identity: { email: 'dan@example.test', plan: 'max' } },
    accounts: [{ label: 'default', enrolled: true, identity: { email: 'dan@example.test', plan: 'max' }, active: true }],
    operation: null, error: null } }] });
  await tick();
  assert.equal(page.ui.current.textContent, 'Default');
  assert.equal(page.ui.personal.disabled, false);
  assert.equal(page.ui.work.disabled, false);
  assert.equal(page.ui.switchButton.disabled,true);
  assert.match(page.ui.status.textContent, /enroll work/);
  assert.equal(page.ui.root.getAttribute('data-phase'), 'unconfigured');
  assert.equal(page.requests.filter((request) => request.method === 'POST').length, 0);
});

test('a refused switch says nothing was signalled', async () => {
  const page = boot({ routes: [
    { status: 200, body: accounts() },
    { status: 202, body: { operation: operation('waiting') } },
    { status: 200, body: { operation: operation('refused', 'work',
      'the host stayed busy and nothing was signalled; personal is still active') } },
    { status: 200, body: accounts() },
  ] });
  await tick();
  page.ui.work.listeners.click();
  page.ui.switchButton.listeners.click();
  await tick();
  await page.fire();
  assert.match(page.ui.status.textContent, /nothing was signalled/);
  assert.equal(page.ui.personal.getAttribute('data-active'), 'true');
  assert.equal(page.ui.root.getAttribute('data-phase'), 'refused');
  assert.equal(page.storage.has('pane_claude_account_switch_v1'), false);
});

test('viewing either account uses its own quota and reset; green and blue remain independent', async () => {
  const page = boot(); await tick();
  page.ui.work.listeners.click(); await tick();
  assert.match(page.ui.quotas.outerHTML,/12% used/);
  assert.match(page.ui.quotas.outerHTML,/Resets in 2h/);
  assert.match(page.ui.quotaStatus.textContent,/Work quota · read 2 min ago/);
  assert.equal(page.ui.work.getAttribute('aria-pressed'),'true');
  assert.equal(page.ui.work.getAttribute('data-active'),'false');
  assert.equal(page.ui.personal.getAttribute('data-active'),'true');
  page.ui.personal.listeners.click(); await tick();
  assert.match(page.ui.quotas.outerHTML,/76% used/);
  assert.equal(page.requests.filter(r=>r.method==='POST').length,0);
});

test('late quota for the other account never replaces the viewed account', async () => {
  let release;
  const page=boot({quotaRoute:label=>label==='work'?new Promise(resolve=>release=()=>resolve({status:200,body:quota(label)})):
    Promise.resolve({status:200,body:quota(label)})});
  await tick();page.ui.work.listeners.click();page.ui.personal.listeners.click();
  release();await tick();
  assert.match(page.ui.quotas.outerHTML,/76% used/);
  assert.doesNotMatch(page.ui.quotas.outerHTML,/12% used/);
});

test('viewing either account never forces a provider read; only the Refresh button asks', async () => {
  const page = boot();
  await tick();
  page.ui.personal.listeners.click(); await tick();
  page.ui.work.listeners.click(); await tick();
  const usage = page.requests.filter(r => r.url.startsWith('/api/usage'));
  assert.ok(usage.length >= 2);
  assert.ok(usage.every(r => !r.url.includes('refresh=1')), 'opening or toggling only reads the cache');
  page.ui.refresh.listeners.click(); await tick();
  assert.ok(page.requests.some(r => r.url === '/api/usage?account=work&refresh=1'), 'the one deliberate Refresh asks');
});

test('a rate-limited Claude reading keeps its stale windows, says why and waits before asking again', async () => {
  const page = boot({ quotaRoute: label => ({ status: 200, body: quotaLimited(label) }) });
  await tick();
  const text = page.ui.quotas.outerHTML;
  assert.match(text, /88% used/);
  assert.match(text, /12% used/);
  assert.match(text, /Stale: the last reading/);
  assert.match(text, /Rate limited: next read in/);
  assert.match(page.ui.quotaStatus.textContent, /rate limited · next read in/);
  assert.match(page.ui.refresh.textContent, /^Waiting · /);
  page.ui.refresh.listeners.click(); await tick();
  assert.match(page.ui.refresh.textContent, /^Waiting · /, 'a manual Refresh during a live backoff says it is waiting');
  assert.doesNotMatch(page.ui.quotas.outerHTML, /Quota unavailable/);
});

test('a 429 before any Claude reading is a quiet note with the retry countdown, never unknown windows', async () => {
  const page = boot({ quotaRoute: label => ({ status: 200, body: quotaNoGood(label) }) });
  await tick();
  const text = page.ui.quotas.outerHTML;
  assert.match(text, /Quota unavailable: Claude quota refresh is waiting for the provider's retry time\. No reading yet\./);
  assert.match(text, /next read in/);
  assert.doesNotMatch(text, /0% used/);
  assert.match(page.ui.quotaStatus.textContent, /rate limited · next read in/);
  assert.match(page.ui.refresh.textContent, /^Waiting · /);
});

test('a foreign quota label is never displayed and a passed reset never shows its old percentage', async () => {
  const page=boot({quotaRoute:label=>({status:200,body:quota(label==='work'?'personal':label)})});
  await tick();page.ui.work.listeners.click();await tick();
  assert.match(page.ui.quotas.outerHTML,/account label mismatch/);
  const old=quota('personal');old.claude.windows[0].resets_at=old.now-1;
  const expired=boot({quotaRoute:()=>({status:200,body:old})});await tick();
  assert.match(expired.ui.quotas.outerHTML,/Reset passed · new reading needed/);
  assert.doesNotMatch(expired.ui.quotas.outerHTML,/76%/);
});

test('Cancel while waiting is terminal, leaves Personal ringed and immediately re-enables Switch', async () => {
  const cancelled=operation('cancelled','work','Switch cancelled; personal is still active');
  const page=boot({routes:[{status:200,body:accounts({operation:operation('waiting')})},
    {status:200,body:{operation:operation('waiting')}},{status:200,body:{operation:cancelled}},
    {status:200,body:accounts({operation:cancelled})}]});
  await tick();page.ui.work.listeners.click();await tick();
  assert.equal(page.ui.cancel.hidden,false);
  page.ui.cancel.listeners.click();await tick();
  const posts=page.requests.filter(r=>r.method==='POST');
  assert.equal(posts.length,1);assert.equal(posts[0].url,'/api/claude/account-switch/cancel');
  assert.deepEqual(posts[0].body,{schemaVersion:1,backend:'claude',operationId:'op-1'});
  assert.equal(page.ui.personal.getAttribute('data-active'),'true');
  assert.equal(page.ui.switchButton.disabled,false);assert.equal(page.ui.cancel.hidden,true);
  assert.match(page.ui.status.textContent,/cancelled/);
});

test('a lost Cancel reply recovers that operation, never posting a new switch', async () => {
  const page=boot({routes:[{status:200,body:accounts({operation:operation('waiting')})},
    {status:200,body:{operation:operation('waiting')}},new Error('reply lost'),
    {status:200,body:{operation:operation('cancelled','work','Cancelled')}},
    {status:200,body:accounts({operation:operation('cancelled','work','Cancelled')})}]});
  await tick();page.ui.cancel.listeners.click();await tick();
  assert.equal(page.requests.filter(r=>r.method==='POST').length,1);
  assert.equal(page.ui.root.getAttribute('data-phase'),'cancelled');
  assert.equal(page.storage.has('pane_claude_account_switch_v1'),false);
});

test('a slow pre-Cancel status read cannot resurrect a cancelled wait', async () => {
  let release;
  const cancelled=operation('cancelled','work','Cancelled');
  const page=boot({routes:[{status:200,body:accounts({operation:operation('waiting')})},
    ()=>new Promise(resolve=>release=()=>resolve({status:200,body:{operation:operation('waiting')}})),
    {status:200,body:{operation:cancelled}},{status:200,body:accounts({operation:cancelled})}]});
  // Route answers can themselves be asynchronous, like a real slow GET.
  await tick();page.ui.cancel.listeners.click();await tick();
  if(release)release();await tick();
  assert.equal(page.ui.root.getAttribute('data-phase'),'cancelled');
  assert.equal(page.ui.cancel.hidden,true);
});

test('recovery receipts keep unknown/starting query-only and show failed switch/old restored without a target ring', async () => {
  const storage = new Map([['pane_claude_account_switch_v1', JSON.stringify({ id: 'op-1' })]]);
  const restored = { ...operation('restored', 'work', 'Switch failed; personal service restored. Manager pending; notice scheduled.'), canCancel: false };
  const page = boot({ storage, routes: [
    { status: 200, body: accounts({ operation: operation('unknown', 'work', 'evidence pending') }) },
    { status: 200, body: { operation: operation('starting', 'work', 'Old personal replacement starting; Manager pending; notice pending.') } },
    { status: 200, body: { operation: restored } },
    { status: 200, body: accounts({ operation: restored }) },
  ] });
  await tick();
  assert.equal(page.ui.root.getAttribute('data-phase'), 'starting');
  assert.match(page.ui.status.textContent, /Replacement starting; not ready yet/);
  assert.equal(storage.has('pane_claude_account_switch_v1'), true);
  assert.equal(page.ui.cancel.hidden, true);
  await page.fire();
  assert.equal(page.ui.root.getAttribute('data-phase'), 'restored');
  assert.match(page.ui.status.textContent, /Switch failed; personal service restored/);
  assert.match(page.ui.status.textContent, /Manager pending; notice scheduled/);
  assert.equal(page.ui.personal.getAttribute('data-active'), 'true');
  assert.equal(page.ui.work.getAttribute('data-active'), 'false');
  assert.equal(page.requests.filter(r => r.method === 'POST').length, 0);
});

test('already selected says service unchecked and recovery-required exposes the same-ID command', async () => {
  const selected = boot({ body: accounts({ operation: { ...operation('active', 'personal'), alreadyActive: true,
    service: { state: 'unchecked' } } }) });
  await tick();
  assert.match(selected.ui.status.textContent, /already selected; service not checked/);
  const required = boot({ body: accounts({ operation: { ...operation('recovery_required', 'work', 'Work selected; startup failed.'),
    recoverCommand: 'bash scripts/claude_restart.sh --recover op-1' } }) });
  await tick();
  assert.match(required.ui.status.textContent, /--recover op-1/);
  assert.equal(required.requests.filter(r => r.method === 'POST').length, 0);
});
