// Adapter checks for the whole-conversation Refine context: the shared history-mode setting, OpenCode pagination
// to exhaustion, Claude historyMode/boundaries, the Anthropic text-block and cache-key hand-off, and the measured
// cache readout. Offline VM doubles only: no key, provider, host or real network.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { pane, contentText } = require('./pane-fixture.cjs');

const ref = c => c.repeat(32), uuid = '11111111-1111-4111-8111-111111111111';
const SEPARATOR = '\n\n---\n\n';
const json = value => JSON.stringify(value); // boundary objects are made inside the page VM's realm

function hostTarget(extra = {}) {
  return {schemaVersion:1, backend:'claude', targetRef:ref('T'), storeRef:ref('S'), sessionRef:ref('J'), scopeRef:ref('M'),
    leafRef:ref('L'), fileEpoch:ref('E'), uuid, leafUuid:null, source:'host', scope:'main', title:'Manager',
    executionId:'cc-'+uuid, generation:1, role:'Manager', capabilities:{nativeInput:true, controls:true}, ...extra};
}

// One chronological message list served newest-first in one-message pages, as OpenCode's order=desc pages do.
function opencodeHistory(f, specs) {
  const descending = specs.slice().reverse(), paths = [];
  f.ctx.oc = async (_method, path) => {
    paths.push(path);
    const match = /[?&]cursor=([^&]+)/.exec(path);
    const index = match ? Number(match[1].slice(1)) : 0;
    return { data: [descending[index]], cursor: index + 1 < descending.length ? { next: 'c' + (index + 1) } : null };
  };
  return paths;
}
// The page's own filter: prose text parts only, tools excluded, both sides trimmed.
function messageText(m) { return m.type === 'user' ? (m.text || '').trim() : (m.content || []).filter(p => p && p.type === 'text' && p.text).map(p => p.text).join('\n\n').trim(); }
function expectedHistory(specs) {
  const blocks = specs.map(m => (m.type === 'user' ? '[USER]' : '[ASSISTANT]') + '\n' + messageText(m));
  return { text: blocks.join(SEPARATOR), blocks };
}
function boundariesFor(blocks, ids) {
  let end = 0;
  return blocks.map((block, i) => { end += (i ? SEPARATOR.length : 0) + block.length; return { id: ids[i], end }; });
}
function alternating(count, textFor) {
  return Array.from({length:count}, (_, i) => i % 2
    ? { id:'msg-a' + i, type:'assistant', content:[{ type:'text', text:textFor(i) }] }
    : { id:'msg-u' + i, type:'user', text:textFor(i) });
}

test('whole history pages an OpenCode conversation to exhaustion, keeps exact prose and reports one boundary per block', async () => {
  const specs = alternating(10, i => 'MESSAGE_' + i + '_' + 'x'.repeat(6000) + '_END_' + i); // beyond eight pages AND 50 KiB
  specs[3].content.push({ type:'tool', input:'EXCLUDED_TOOL_INPUT' });
  const f = pane();
  const paths = opencodeHistory(f, specs);
  const ctx = await f.api.buildContext('ses_whole');
  const expected = expectedHistory(specs);
  assert.equal(f.storage.get('refine_history_mode'), undefined, 'whole is the default and is not written');
  assert.equal(paths.length, 10, 'every page is read, not the old eight');
  assert.equal(ctx.historyMode, 'whole');
  assert.equal(ctx.text, expected.text);
  assert.equal(ctx.bytes, Buffer.byteLength(expected.text));
  assert.ok(ctx.bytes > 50 * 1024);
  assert.equal(ctx.turns, 10); assert.equal(ctx.users, 5);
  assert.equal(json(ctx.historyBoundaries), json(boundariesFor(expected.blocks, specs.map(m => m.id))));
  assert.equal(ctx.historyBoundaries.at(-1).end, expected.text.length, 'the next block owns its leading separator');
  assert.doesNotMatch(ctx.text, /EXCLUDED_TOOL_INPUT/, 'tool calls never enter Refine history');
});

test('budgeted history explicitly keeps the established byte crop and eight-page read', async () => {
  const specs = alternating(10, i => 'SHORT_' + i);
  const whole = pane(); opencodeHistory(whole, specs);
  const wholeCtx = await whole.api.buildContext('ses_whole');
  assert.equal(wholeCtx.historyBoundaries.at(-1).end, wholeCtx.text.length);

  const f = pane(); f.storage.set('refine_history_mode', 'budgeted'); f.storage.set('pane_context_kb', '400');
  const paths = opencodeHistory(f, specs);
  const ctx = await f.api.buildContext('ses_budgeted');
  assert.equal(ctx.historyMode, 'budgeted');
  assert.equal(paths.length, 8, 'the budgeted read keeps the existing eight-page bound');
  assert.equal(ctx.turns, 8);
  assert.equal(ctx.historyBoundaries.at(-1).end, ctx.text.length);

  const cropped = pane(); cropped.storage.set('refine_history_mode', 'budgeted'); cropped.storage.set('pane_context_kb', '5');
  opencodeHistory(cropped, alternating(10, i => 'LONG_' + i + '_' + 'y'.repeat(4000)));
  const small = await cropped.api.buildContext('ses_small');
  assert.ok(small.bytes <= 5 * 1024 + 7, 'the explicit budget is enforced (' + small.bytes + ')');
  assert.ok(small.text.includes('LONG_9_'), 'the newest block survives');
  assert.equal(small.historyBoundaries.at(-1).end, small.text.length);
});

test('Claude context asks for the chosen history mode, omits the budget for whole, and tolerates absent boundaries', async () => {
  const t = hostTarget(), text = '[USER]\nfirst prose\n\n[ASSISTANT]\nsecond prose';
  const context = {...t, status:'stored', targetRevision:0, contextIdentity:ref('C'), text, bytes:Buffer.byteLength(text),
    turns:2, users:1, readAt:10};
  const reads = [], f = pane();
  f.ctx.fetch = async url => { reads.push(String(url)); return {ok:true, text:async()=>JSON.stringify(context)}; };
  f.api.state.backend = 'claude'; f.api.state.claudeTarget = t;
  const whole = await f.api.buildContext(f.api.freezeRefineTarget());
  assert.equal(reads.length, 1);
  const wholeQuery = new URL(reads[0], 'http://fixture').searchParams;
  assert.equal(wholeQuery.get('historyMode'), 'whole');
  assert.equal(wholeQuery.has('budgetBytes'), false);
  assert.equal(whole.historyMode, 'whole');
  assert.equal(json(whole.historyBoundaries), '[]', 'an old reader without boundary metadata is still usable');

  f.storage.set('refine_history_mode', 'budgeted'); f.storage.set('pane_context_kb', '5');
  const budgeted = await f.api.buildContext(f.api.freezeRefineTarget());
  const budgetedQuery = new URL(reads[1], 'http://fixture').searchParams;
  assert.equal(budgetedQuery.get('historyMode'), 'budgeted');
  assert.equal(budgetedQuery.get('budgetBytes'), String(5 * 1024));
  assert.equal(budgeted.historyMode, 'budgeted');
});

test('Claude whole history carries optional cache hints without blocking text, and still validates identity', async () => {
  const t = hostTarget(), text = '[USER]\nfirst\n\n---\n\n[ASSISTANT]\nsecond', first = text.indexOf('\n\n---');
  const base = {...t, status:'stored', targetRevision:0, contextIdentity:ref('C'), text, bytes:Buffer.byteLength(text),
    turns:2, users:1, readAt:10, historyMode:'whole'};
  const f = pane(); let context = {...base, historyBoundaries:[{id:'msg-1', end:first}, {id:'msg-2', end:text.length}]};
  f.ctx.fetch = async () => ({ok:true, text:async()=>JSON.stringify(context)});
  f.api.state.backend = 'claude'; f.api.state.claudeTarget = t;
  const ctx = await f.api.buildContext(f.api.freezeRefineTarget());
  assert.equal(json(ctx.historyBoundaries), json([{id:'msg-1', end:first}, {id:'msg-2', end:text.length}]));
  assert.equal(ctx.historyBoundaries.at(-1).end, text.length);

  for (const [label, bad] of [
    ['not a list', 'nope'],
    ['not increasing', [{id:'a', end:first}, {id:'b', end:first}]],
    ['past the text', [{id:'a', end:text.length + 1}]],
    ['not an integer', [{id:'a', end:1.5}]],
  ]) {
    context = {...base, historyBoundaries:bad};
    const result = await f.api.buildContext(f.api.freezeRefineTarget());
    assert.equal(result.text, text, label + ': optional cache metadata must not prevent Refine');
    const built = f.ctx.opcRefineCore.buildRequest({ transcription:'dictation', history:result.text,
      historyBoundaries:result.historyBoundaries, parts:vm.runInContext('({system:REFINE_DEFAULT_SYSTEM_PROMPT,context:REFINE_DEFAULT_CONTEXT_PREAMBLE,transcription:REFINE_DEFAULT_TRANSCRIPTION_PREAMBLE,finalfence:REFINE_DEFAULT_FINAL_FENCE})', f.ctx) });
    assert.equal(contentText(built.anthropicUser), built.user, label);
  }
  // A response that says it applied a different mode than the one asked for is a visible mismatch.
  context = {...base, historyMode:'budgeted'};
  await assert.rejects(f.api.buildContext(f.api.freezeRefineTarget()), /history mode mismatch/);
  // Identity checks still run even though the byte budget is not.
  context = {...base, sessionRef:ref('X'), historyBoundaries:[]};
  await assert.rejects(f.api.buildContext(f.api.freezeRefineTarget()), /identity\/epoch\/schema/);
});

test('an old or invalid saved budget cannot block whole history; it still governs an explicit budgeted read', async () => {
  const t = hostTarget(), text = 'whole prose';
  const context = {...t, status:'stored', targetRevision:0, contextIdentity:ref('C'), text, bytes:Buffer.byteLength(text),
    turns:1, users:0, readAt:10, historyMode:'whole'};
  const f = pane(); f.storage.set('pane_context_kb', '401'); // an invalid saved budget
  f.ctx.fetch = async () => ({ok:true, text:async()=>JSON.stringify(context)});
  f.api.state.backend = 'claude'; f.api.state.claudeTarget = t;
  assert.equal((await f.api.buildContext(f.api.freezeRefineTarget())).text, text, 'whole history ignores the saved budget');
  f.storage.set('refine_history_mode', 'budgeted');
  await assert.rejects(f.api.buildContext(f.api.freezeRefineTarget()), /5–400 KiB/);
});

test('the desktop selector defaults to whole, imports the shared key, and switches back in one click', () => {
  const f = pane(); f.api.wireSettings();
  assert.equal(f.el('history-mode').value, 'whole');
  assert.equal(f.storage.get('refine_history_mode'), undefined);
  f.el('history-mode').onchange({target:{value:'budgeted'}});
  assert.equal(f.storage.get('refine_history_mode'), 'budgeted');
  f.el('history-mode').onchange({target:{value:'whole'}});
  assert.equal(f.storage.get('refine_history_mode'), 'whole');

  const g = pane({storage:new Map([['refine_history_mode','bogus']])}); g.api.wireSettings();
  assert.equal(g.el('history-mode').value, 'whole', 'an old/invalid value reads as the default');
  g.el('import-text').value = JSON.stringify({refine_history_mode:'budgeted'});
  g.el('import-btn').onclick();
  assert.equal(g.storage.get('refine_history_mode'), 'budgeted', 'Import settings carries the shared key');
  assert.equal(g.el('history-mode').value, 'budgeted');
});

// A settled-contract core double: it records what the adapter hands the core, then answers the call without a
// provider. The real refine-core.js is already loaded underneath, so request assembly is the production one.
function contractCore() {
  const seen = { build:[], call:[] };
  return { seen, coreHook(ctx) {
    const core = ctx.opcRefineCore;
    ctx.opcRefineCore = Object.freeze({...core,
      buildRequest: (options) => { seen.build.push(options); return core.buildRequest(options); },
      callWithRetry: async (options) => { seen.call.push(options); return {text:'cleaned', cost:null, estimated:false, cache:seen.cache}; },
    });
  } };
}

test('desktop Refine hands the core the same Anthropic text blocks, the request boundaries and a per-target cache key', async () => {
  const probe = contractCore(), f = pane({coreHook:probe.coreHook});
  const specs = alternating(2, i => 'prose ' + i);
  opencodeHistory(f, specs);
  f.api.state.target = {id:'ses_cache'};
  f.box.value = 'raw words';
  await f.api.refine();
  assert.equal(f.box.value, 'cleaned');
  const built = probe.seen.build.at(-1), call = probe.seen.call.at(-1);
  const expected = expectedHistory(specs);
  assert.equal(json(built.historyBoundaries), json(boundariesFor(expected.blocks, specs.map(m => m.id))));
  assert.equal(contentText(call.anthropicUser), call.user, 'the wire blocks carry the exact same user text');
  assert.equal(call.anthropicUser.at(-1).type, 'text');
  assert.equal(call.cacheKey, 'opencode:ses_cache');
  assert.equal(probe.seen.call.length, 1);
});

test('the cache key is stable across a host generation resume and distinct for deliberate no-context', async () => {
  const keys = [];
  for (const generation of [1, 7]) {
    const t = hostTarget({generation}), text = '[USER]\nfirst prose';
    const context = {...t, status:'stored', targetRevision:0, contextIdentity:ref('C'), text, bytes:Buffer.byteLength(text),
      turns:1, users:1, readAt:10, historyMode:'whole', historyBoundaries:[{id:'m1', end:text.length}]};
    const probe = contractCore(), f = pane({coreHook:probe.coreHook});
    f.ctx.fetch = async url => String(url).startsWith('/api/claude/context?')
      ? {ok:true, text:async()=>JSON.stringify(context)}
      : {ok:true, text:async()=>JSON.stringify({...t, contextIdentity:ref('C'), status:'complete', targetInfo:{...t, contextIdentity:ref('C')}})};
    f.api.state.backend = 'claude'; f.api.state.claudeTarget = t;
    f.box.value = 'raw words';
    await f.api.refine();
    assert.equal(f.box.value, 'cleaned', 'generation ' + generation);
    keys.push(probe.seen.call.at(-1).cacheKey);
  }
  assert.equal(json(keys), json(['claude:cc-' + uuid, 'claude:cc-' + uuid]));

  const probe = contractCore(), g = pane({coreHook:probe.coreHook});
  g.api.state.backend = 'claude'; g.storage.set('pane_claude_no_context_v1', 'true');
  g.box.value = 'raw words';
  await g.api.refine();
  assert.equal(probe.seen.call.at(-1).cacheKey, 'no-context');
  assert.equal(probe.seen.call.at(-1).anthropicUser.length, 1, 'no conversation prose is present');
});

test('the real provider-reported cache counts appear beside the desktop cost line, and nothing is shown for routers', async () => {
  const probe = contractCore(); probe.seen.cache = {read:31744, write:1434};
  const f = pane({coreHook:probe.coreHook});
  opencodeHistory(f, [{id:'msg-u1', type:'user', text:'prose'}]);
  f.api.state.target = {id:'ses_whole'};
  f.box.value = 'raw words';
  await f.api.refine();
  assert.match(f.el('cost-info').textContent, /cache: read 31\.7K · wrote 1\.4K/);
  assert.match(f.el('cost-info').textContent, /total: \$0\.00/, 'no cost was invented from cache counts');

  const router = pane({provider:'openrouter', coreHook:contractCore().coreHook});
  router.box.value = 'raw words';
  await router.api.refine();
  assert.doesNotMatch(router.el('cost-info').textContent, /cache:/);
});

test('a provider-reported unknown cache count reads as unknown, never as zero', () => {
  const f = pane();
  assert.equal(vm.runInContext('opcRefineCore.cacheSummary({read:null,write:null})', f.ctx), 'cache: read — · wrote —');
  assert.equal(vm.runInContext('opcRefineCore.cacheSummary(undefined)', f.ctx), '');
});
