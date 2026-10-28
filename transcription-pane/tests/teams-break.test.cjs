// Teams message break (static/teams-break.js): the exact widget delimiter and the speaker auto-toggle,
// loaded without a page (no document: nothing attaches). The real popover/keys run in teams-break.html.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/teams-break.js'), 'utf8');
const defaults = fs.readFileSync(path.join(__dirname, '../static/refine-defaults.js'), 'utf8');

function load() {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  return vm.runInContext('opcTeamsBreak', ctx);
}

test('the delimiter is the widget text exactly: no surrounding newlines, comment only when given', () => {
  const tb = load();
  assert.equal(tb.delimiter('Dan', '2026-10-19'),
    '===MESSAGE_BREAK===\nSpeaker: Dan\nDate: 2026-10-19\n===END_BREAK===');
  assert.equal(tb.delimiter('Pat Lee', '2026-10-19', 'asked about the invoice'),
    '===MESSAGE_BREAK===\nSpeaker: Pat Lee\nDate: 2026-10-19\nComment: asked about the invoice\n===END_BREAK===');
  assert.equal(tb.delimiter('Dan', '2026-10-19', 'first line\nsecond line\n\nafter a blank'),
    '===MESSAGE_BREAK===\nSpeaker: Dan\nDate: 2026-10-19\nComment:\n    first line\n    second line\n    \n    after a blank\n===END_BREAK===');
});

test('the widget key names are kept, so its saved speakers and date mean the same here', () => {
  assert.deepEqual({...load().KEYS}, {speakers: 'teams_message_speakers', active: 'teams_message_active_speakers',
    date: 'teams_message_date', last: 'teams_message_last_speaker_index', known: 'teams_message_known_speakers'});
});

test('active speakers: checked slots with a name, in slot order, a repeated name once', () => {
  const tb = load();
  const slots = ['Dan', '', 'Pat', 'Dan', 'Lee', '', '', '', '', ''];
  const checked = [true, true, true, true, false, false, false, false, false, false];
  assert.deepEqual(JSON.parse(JSON.stringify(tb.activeSpeakers(slots, checked))), [{index: 0, name: 'Dan'}, {index: 2, name: 'Pat'}]);
  assert.equal(tb.activeSpeakers(slots, checked.map(() => false)).length, 0);
});

test('auto-toggle: two speakers alternate from the last one used; three or more prefer Dan; one is itself', () => {
  const tb = load();
  const two = [{index: 0, name: 'Dan'}, {index: 3, name: 'Pat'}];
  assert.equal(tb.pickSpeaker(two, -1), 0);   // never used: the first
  assert.equal(tb.pickSpeaker(two, 0), 1);    // Dan last → Pat
  assert.equal(tb.pickSpeaker(two, 3), 0);    // Pat last → Dan
  assert.equal(tb.pickSpeaker(two, 7), 0);    // last speaker no longer active: the first
  assert.equal(tb.pickSpeaker([{index: 1, name: 'Pat'}, {index: 2, name: 'Lee'}, {index: 4, name: 'Daniel'}], 1), 2);
  assert.equal(tb.pickSpeaker([{index: 1, name: 'Pat'}, {index: 2, name: 'Lee'}, {index: 4, name: 'Kim'}], 1), 0);
  assert.equal(tb.pickSpeaker([{index: 5, name: 'Pat'}], 5), 0);
  assert.equal(tb.pickSpeaker([], 0), -1);
});

test('Refine keeps break blocks verbatim: the default system prompt says so', () => {
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(defaults, ctx);
  const system = vm.runInContext('REFINE_DEFAULT_SYSTEM_PROMPT', ctx);
  assert.match(system, /TEAMS MESSAGE BREAKS: copy every block from a `===MESSAGE_BREAK===` line through its `===END_BREAK===`\n  line EXACTLY as written/);
  assert.ok(system.indexOf('TEAMS MESSAGE BREAKS') < system.indexOf('- OUTPUT MARKDOWN'));
});

test('speaker dropdowns are legible: select and every option carry the popover colours, never transparent', () => {
  const css = fs.readFileSync(path.join(__dirname, '../static/teams-break.css'), 'utf8');
  const rule = /\.teams-break select, \.teams-break option \{([^}]*)\}/.exec(css);
  assert.ok(rule, 'one rule styles the select and its options');
  assert.match(rule[1], /background-color: var\(--tb-bg\)/);
  assert.match(rule[1], /color: var\(--tb-text\)/);
  assert.match(css, /dialog\.teams-break \{[^}]*color-scheme: dark/);
});
