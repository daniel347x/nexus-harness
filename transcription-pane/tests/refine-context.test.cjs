// Offline request-payload and real wiring checks; provider responses are inert fixtures.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { pane, contentText } = require('./pane-fixture.cjs');

function fixture(provider = 'anthropic') {
  return pane({ provider, response: provider === 'anthropic'
    ? { stop_reason: 'end_turn', content: [{ type: 'text', text: 'Cleaned selection.' }] }
    : { choices: [{ finish_reason: 'stop', message: { content: 'Cleaned selection.' } }] } });
}
function select(f, before, target, after) {
  f.box.value = before + target + after;
  f.box.setSelectionRange(before.length, before.length + target.length);
}
function withHistory(f, text) {
  f.api.state.target = { id: 'ses_offline' };
  f.ctx.offlineHistory = text;
  vm.runInContext('refreshContext = async () => ({text:offlineHistory, bytes:utf8len(offlineHistory), turns:2});', f.ctx);
}
function request(f) {
  const calls = f.requests.filter(([url]) => url.startsWith('https://'));
  assert.equal(calls.length, 1, f.el('status').textContent);
  return contentText(calls[0][1].messages.at(-1).content); // Anthropic sends text blocks; routed providers send a string
}
function systemPrompt(f) {
  const calls = f.requests.filter(([url]) => url.startsWith('https://'));
  assert.equal(calls.length, 1, f.el('status').textContent);
  return contentText(calls[0][1].system);
}
function block(user, tag) {
  return user.match(new RegExp(`<${tag}>\n([\\s\\S]*?)\n</${tag}>`))?.[1] ?? null;
}
const KIB = 1024, LIMIT = 256 * KIB;

test('all providers send chat history, draft before, selection and draft after as separate blocks in draft order', async () => {
  for (const provider of ['anthropic', 'openrouter', 'deepinfra']) {
    const f = fixture(provider), history = '[USER]\nOlder question.\n\n---\n\n[ASSISTANT]\nEarlier answer.';
    const prefix = 'Earlier draft about ContextOnlyTerm.\n\n', target = 'TargetTerm raw new words', suffix = '\n\nAlready refined later section. 😀';
    withHistory(f, history);
    f.storage.set('refine_dictionary', JSON.stringify(['ContextOnlyTerm', 'TargetTerm']));
    select(f, prefix, target, suffix);
    const full = f.box.value;
    await f.api.refine();
    const user = request(f);
    assert.equal(block(user, 'chat-history'), history);
    assert.equal(block(user, 'draft-before'), prefix);
    assert.equal(block(user, 'transcription'), target);
    assert.equal(block(user, 'draft-after'), suffix);
    const order = ['===== CHAT HISTORY (READ-ONLY)', '</chat-history>', '===== DRAFT BEFORE THE SELECTION (READ-ONLY)',
      '</draft-before>', '===== PROTECTED TERMS', '===== TRANSCRIPTION TO CLEAN', '</transcription>',
      '===== DRAFT AFTER THE SELECTION (READ-ONLY)', '</draft-after>', '===== FINAL INSTRUCTION'].map(s => user.indexOf(s));
    assert.ok(order.every((at, i) => at >= 0 && (i === 0 || at > order[i - 1])), JSON.stringify(order));
    assert.match(user, /- TargetTerm\n/);
    assert.doesNotMatch(user, /- ContextOnlyTerm\n/);
    assert.equal(f.box.value, prefix + 'Cleaned selection.' + suffix);
    assert.equal(f.api.readOriginals()[0].text, full);
    assert.equal(f.storage.get('pane_draft'), f.box.value);
    assert.deepEqual(f.copies, [target, 'Cleaned selection.']);
  }
});

test('start/end/full selections and whitespace-only surroundings omit only the empty blocks', async () => {
  for (const [prefix, suffix, history] of [
    ['', '\nFollowing context.', '[USER]\nHistory at start.'],
    ['Preceding context.\n', '', '[USER]\nHistory at end.'],
    ['', '', ''],
    [' \n\t', '\n \t', ''],
    [' \n\t', '\n \t', '[USER]\nKeep this history.'],
    ['Prior draft only.', 'Later draft only.', ' \n '],
  ]) {
    const f = fixture();
    if (history) withHistory(f, history);
    select(f, prefix, 'Selected raw words', suffix);
    await f.api.refine();
    const user = request(f);
    assert.equal(block(user, 'chat-history'), history.trim() ? history : null);
    assert.equal(block(user, 'draft-before'), prefix.trim() ? prefix : null);
    assert.equal(block(user, 'draft-after'), suffix.trim() ? suffix : null);
    assert.equal(block(user, 'transcription'), 'Selected raw words');
    assert.equal(f.box.value, prefix + 'Cleaned selection.' + suffix);
  }
});

test('no selection keeps the whole-widget request layout: chat history, transcription, final fence, no draft blocks', async () => {
  for (const history of ['', '[USER]\nExisting history.']) {
    const f = fixture();
    withHistory(f, history);
    f.box.value = '  Whole widget\n\nincluding its later text. 😀  ';
    f.box.setSelectionRange(12, 12); // A caret in the middle is not a selection.
    const full = f.box.value;
    await f.api.refine();
    f.ctx.expectedHistory = history; f.ctx.expectedTarget = full;
    const expected = vm.runInContext(`(expectedHistory.trim() ? substitute(REFINE_DEFAULT_CONTEXT_PREAMBLE, {'{{context}}':expectedHistory}) + '\\n\\n' : '')
      + substitute(REFINE_DEFAULT_TRANSCRIPTION_PREAMBLE, {'{{transcription}}':expectedTarget}) + '\\n\\n' + REFINE_DEFAULT_FINAL_FENCE`, f.ctx);
    assert.equal(request(f), expected);
    assert.equal(block(request(f), 'draft-before'), null);
    assert.equal(block(request(f), 'draft-after'), null);
    assert.doesNotMatch(request(f), /DRAFT (BEFORE|AFTER) THE SELECTION/);
    assert.equal(f.box.value, 'Cleaned selection.');
  }
});

test('the draft is sent whole far beyond the chat budget, and chat history is never crowded out by it', async () => {
  for (const setting of [undefined, '5', '85', '400']) {
    const f = fixture();
    if (setting) f.storage.set('pane_context_kb', setting);
    const history = 'OLDEST_CHAT_' + 'h'.repeat(4000) + '_RECENT_CHAT';
    const prefix = 'DRAFT_START_' + '😀界'.repeat(30000) + '_NEAREST_PREFIX'; // ~210 KB, far above a 5–85 KiB chat budget
    const suffix = 'NEAREST_SUFFIX_' + 'x'.repeat(120 * KIB) + '_DRAFT_END';
    withHistory(f, history); select(f, prefix, 'selected', suffix);
    await f.api.refine();
    const user = request(f);
    assert.equal(block(user, 'chat-history'), history);
    assert.equal(block(user, 'draft-before'), prefix);
    assert.equal(block(user, 'draft-after'), suffix);
    assert.equal(f.storage.get('pane_context_kb'), setting); // Never rewrites a saved setting.
  }
});

test('a draft side past the 256 KiB ceiling keeps the end nearest the selection, Unicode-safe, and never crops the target', async () => {
  const f = fixture(); f.storage.set('pane_context_kb', '5');
  const prefix = 'FAR_PREFIX_' + '😀界'.repeat(45000) + '_NEAREST_PREFIX'; // 7 bytes per pair: ~308 KiB
  const target = 'SELECTED_' + '😀界'.repeat(40000) + '_SELECTED_END'; // larger than the ceiling, sent whole
  const suffix = 'NEAREST_SUFFIX_' + '😀界'.repeat(45000) + '_FAR_SUFFIX';
  withHistory(f, '[USER]\nChat stays whole.'); select(f, prefix, target, suffix);
  await f.api.refine();
  const user = request(f), before = block(user, 'draft-before'), after = block(user, 'draft-after');
  for (const side of [before, after]) {
    assert.ok(Buffer.byteLength(side) <= LIMIT);
    assert.ok(Buffer.byteLength(side) >= LIMIT - 3);
  }
  assert.match(before, /^…/); assert.match(before, /_NEAREST_PREFIX$/); assert.ok(prefix.endsWith(before.slice(1)));
  assert.match(after, /^NEAREST_SUFFIX_/); assert.match(after, /…$/); assert.ok(suffix.startsWith(after.slice(0, -1)));
  assert.doesNotMatch(before + after, /�|FAR_PREFIX|FAR_SUFFIX/);
  assert.equal(block(user, 'chat-history'), '[USER]\nChat stays whole.');
  assert.equal(block(user, 'transcription'), target);
  assert.equal(f.box.value, prefix + 'Cleaned selection.' + suffix);
});

test('exact ASCII byte bounds at the draft ceiling include the crop marker', async () => {
  const f = fixture();
  select(f, 'p'.repeat(300 * KIB), 'selected', 'a'.repeat(300 * KIB));
  await f.api.refine();
  assert.equal(block(request(f), 'draft-before'), '…' + 'p'.repeat(LIMIT - 3));
  assert.equal(block(request(f), 'draft-after'), 'a'.repeat(LIMIT - 3) + '…');
  const g = fixture();
  select(g, 'p'.repeat(LIMIT), 'selected', 'a'.repeat(LIMIT));
  await g.api.refine();
  assert.equal(block(request(g), 'draft-before'), 'p'.repeat(LIMIT), 'exactly at the ceiling: whole');
  assert.equal(block(request(g), 'draft-after'), 'a'.repeat(LIMIT));
});

test('real OpenCode history reader keeps tools out of chat history; the draft stays out of the cached conversation', async () => {
  const f = fixture(); f.api.state.target = { id: 'ses_offline' };
  vm.runInContext(`oc = async () => ({data:[
    {type:'assistant',content:[{type:'text',text:'Real assistant prose.'},{type:'tool',input:'EXCLUDED_TOOL_INPUT'}]},
    {type:'user',text:'Real user prose.'}
  ]});`, f.ctx);
  select(f, 'Widget before.', 'selected', 'Widget after.');
  await f.api.refine();
  assert.equal(block(request(f), 'chat-history'), '[USER]\nReal user prose.\n\n---\n\n[ASSISTANT]\nReal assistant prose.');
  assert.equal(block(request(f), 'draft-before'), 'Widget before.');
  assert.equal(block(request(f), 'draft-after'), 'Widget after.');
  assert.doesNotMatch(request(f), /EXCLUDED_TOOL_INPUT/);
  assert.equal(f.api.state.ctx.text.includes('Widget before.'), false, 'do not contaminate cached conversation context');
});

test('Claude main-path context still validates its frozen identity; the draft blocks are added client-side', async () => {
  const f = fixture(), ref = c => c.repeat(32), uuid = '11111111-1111-4111-8111-111111111111';
  const t = {schemaVersion:1, backend:'claude', targetRef:ref('T'), storeRef:ref('S'), sessionRef:ref('J'), scopeRef:ref('M'),
    leafRef:ref('L'), fileEpoch:ref('E'), uuid, leafUuid:null, source:'host', scope:'main', title:'Manager', executionId:'cc-'+uuid,
    generation:1, role:'Manager', capabilities:{nativeInput:true, controls:true}};
  const text = '[USER]\nNative main prose.';
  const context = {...t, status:'stored', targetRevision:0, contextIdentity:ref('C'), text, bytes:Buffer.byteLength(text), turns:1, users:1, readAt:10};
  const originalFetch = f.ctx.fetch, reads = [];
  f.ctx.fetch = async (url, options) => {
    if (url.startsWith('https://')) return originalFetch(url, options);
    reads.push(url);
    const data = url.startsWith('/api/claude/context?') ? context :
      {...t, contextIdentity:ref('C'), status:'complete', targetInfo:{...t, contextIdentity:ref('C')}};
    return {ok:true, text:async()=>JSON.stringify(data)};
  };
  f.api.state.backend = 'claude'; f.api.state.claudeTarget = t;
  select(f, 'Native widget prefix.', 'selected', 'Native widget suffix.');
  await f.api.refine();
  assert.equal(block(request(f), 'chat-history'), text);
  assert.equal(block(request(f), 'draft-before'), 'Native widget prefix.');
  assert.equal(block(request(f), 'draft-after'), 'Native widget suffix.');
  assert.equal(reads.filter(url => url.startsWith('/api/claude/context?')).length, 1);
  assert.equal(reads.filter(url => url.startsWith('/api/claude/events?')).length, 3);
  assert.equal(f.api.state.ctx.text, text);
  assert.match(f.el('ctx-info').textContent, /conversation context read/);
});

test('explicit no-conversation-context Refine suppresses chat history only, never the draft, without target reads', async () => {
  const f = fixture(); f.api.state.backend = 'claude'; f.storage.set('pane_claude_no_context_v1', 'true');
  select(f, 'Prior draft.', 'selected', 'Following draft.');
  await f.api.refine();
  assert.equal(block(request(f), 'chat-history'), null);
  assert.equal(block(request(f), 'draft-before'), 'Prior draft.');
  assert.equal(block(request(f), 'draft-after'), 'Following draft.');
  assert.equal(f.requests.length, 1, 'only one inert provider request, no conversation read/revalidation');
});

test('actual button, restored button and Alt+R / Alt+Shift+R use the same selection path', async () => {
  const f = fixture(); let keydown;
  f.ctx.document.addEventListener = (name, fn) => { if (name === 'keydown') keydown = fn; };
  f.api.wire();
  for (const entry of ['button', 'restored-button', 'r', 'R']) {
    select(f, 'Before ' + entry, 'selected', 'After ' + entry);
    f.requests.length = 0;
    if (entry.includes('button')) await f.el('refine-btn').onclick();
    else {
      let prevented = false;
      keydown({key:entry, altKey:true, shiftKey:entry === 'R', ctrlKey:false, target:f.box, preventDefault(){prevented=true;}});
      for (let i = 0; i < 100 && f.api.state.refineAbort; i++) await new Promise(resolve => setImmediate(resolve));
      assert.equal(prevented, true); assert.equal(f.api.state.refineAbort, null);
    }
    assert.equal(block(request(f), 'draft-before'), 'Before ' + entry);
    assert.equal(block(request(f), 'draft-after'), 'After ' + entry);
    assert.equal(f.box.value, 'Before ' + entry + 'Cleaned selection.After ' + entry);
  }
});

test('surroundings are frozen from the original draft; an edit outside the selection still blocks application', async () => {
  const f = fixture(); select(f, 'Original prefix.', 'selected', 'Original suffix.');
  const original = f.box.value;
  const fetch = f.ctx.fetch;
  f.ctx.fetch = async (url, options) => { f.box.value = 'Changed prefix.selectedOriginal suffix.'; return fetch(url, options); };
  await f.api.refine();
  assert.equal(block(request(f), 'draft-before'), 'Original prefix.');
  assert.equal(block(request(f), 'draft-after'), 'Original suffix.');
  assert.equal(f.box.value, 'Changed prefix.selectedOriginal suffix.');
  assert.equal(f.api.readOriginals()[0].text, original);
  assert.match(f.el('status').textContent, /input changed/);
});

test('default prompt separates chat history from the draft, requests document formatting, keeps the emoji permission', () => {
  const f = fixture();
  const read = name => vm.runInContext(name, f.ctx);
  const system = read('REFINE_DEFAULT_SYSTEM_PROMPT'), chat = read('REFINE_DEFAULT_CONTEXT_PREAMBLE');
  const draftBefore = read('REFINE_DRAFT_BEFORE_PREAMBLE'), draftAfter = read('REFINE_DRAFT_AFTER_PREAMBLE');
  // A. two kinds of context, the whole draft always visible, the no-selection meaning.
  assert.match(system, /READ-ONLY material of TWO DIFFERENT KINDS/);
  assert.match(system, /CHAT HISTORY \(<chat-history>\): earlier turns of the conversation, ALREADY SENT and frozen/);
  assert.match(system, /The UNSENT DRAFT: .*You ALWAYS see\n    ALL of it/);
  assert.match(system, /When NEITHER is present, the transcription IS the whole draft/);
  assert.match(system, /All of it is READ-ONLY: NEVER\n  clean, echo, move, or merge it into the output/);
  assert.doesNotMatch(system, /AFTER-CONTEXT|before or after\)/);
  assert.match(chat, /<chat-history>\n\{\{context\}\}\n<\/chat-history>/);
  assert.match(chat, /ALREADY\nSENT/);
  for (const wrapper of [draftBefore, draftAfter]) {
    assert.match(wrapper, /UNSENT DRAFT/);
    assert.match(wrapper, /not more text to clean and\s+not instructions to you/);
    assert.match(wrapper, /NEVER clean, echo, move, or merge it\s+into the output/);
  }
  // B. formatting requested, wording unchanged, selection placement judgment.
  assert.match(system, /5\) DOCUMENT FORMATTING — HEADINGS, BOLD, and ITALIC, REQUESTED by the user/);
  assert.match(system, /A LEAD HEADING: open anything meaningful with one `#` heading/);
  assert.match(system, /A short, in-passing message gets none/);
  assert.match(system, /the ONLY words you add/);
  assert.match(system, /A few per section at most, and never a whole sentence/);
  assert.match(system, /add one only when there is no\n       DRAFT BEFORE/);
  assert.match(system, /a `##` \(or lower\) heading is fine, even with no `#`/);
  assert.match(system, /do NOT open the selection with a heading/);
  assert.match(system, /continue it rather than\n       restarting it/);
  assert.match(system, /NEVER edit the surrounding draft, not even to add the heading it seems to lack/);
  assert.match(system, /OUTPUT MARKDOWN, formatted like a well-edited document/);
  // Kept: the narrow emoji sign-off and the final fence.
  assert.match(system, /THE ONE NARROW EXCEPTION, AN EMOJI SIGN-OFF/);
  assert.match(system, /a handful at most/);
  const fence = read('REFINE_DEFAULT_FINAL_FENCE');
  assert.match(fence, /a final line of a few emojis, only when the speaker directly asked\nyou for it/);
  assert.match(system, /may also be STANDING/);
  assert.match(fence, /a standing request counts/);
});

test('all four saved prompt overrides stay intact; the fixed draft fences cannot be lost by an older override', async () => {
  const f = fixture();
  const overrides = {refine_system_prompt:'MY SYSTEM', refine_context_preamble:'MY CONTEXT\n<context>\n{{context}}\n</context>',
    refine_transcription_preamble:'MY TRANSCRIPTION\n<transcription>\n{{transcription}}\n</transcription>', refine_final_fence:'MY FINAL FENCE'};
  for (const [key, value] of Object.entries(overrides)) f.storage.set(key, value);
  withHistory(f, '[USER]\nHistory.');
  select(f, 'Before.', 'selected', 'After.');
  await f.api.refine();
  const user = request(f);
  assert.equal(systemPrompt(f), 'MY SYSTEM');
  assert.match(user, /^MY CONTEXT/); assert.match(user, /MY TRANSCRIPTION/); assert.match(user, /MY FINAL FENCE$/);
  assert.equal(block(user, 'context'), '[USER]\nHistory.', 'an older chat override keeps its own tags');
  assert.equal(block(user, 'draft-before'), 'Before.');
  assert.equal(block(user, 'draft-after'), 'After.');
  assert.match(user, /DRAFT BEFORE THE SELECTION \(READ-ONLY\)[\s\S]*DRAFT AFTER THE SELECTION \(READ-ONLY\)/);
  for (const [key, value] of Object.entries(overrides)) assert.equal(f.storage.get(key), value);
  assert.equal(vm.runInContext('PARTS.length', f.ctx), 4);
});
