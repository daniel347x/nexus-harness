// Read Aloud's listening-block headings: the prompt defaults ask for short `##`/`###` section labels even when the
// source has no heading, keep the spoken prose otherwise plain, and keep one hierarchy across a long message's
// rewrite passes. Offline: the real static scripts in a Node VM, a localStorage stub, no page, no provider key.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const staticDir = path.join(__dirname, '..', 'static');
const defaults = fs.readFileSync(path.join(staticDir, 'refine-defaults.js'), 'utf8');
const core = fs.readFileSync(path.join(staticDir, 'refine-core.js'), 'utf8');
const readAloud = fs.readFileSync(path.join(staticDir, 'read-aloud.js'), 'utf8');

function storage(saved = {}) {
  const values = new Map(Object.entries(saved));
  const writes = [];
  return {
    writes,
    getItem: name => (values.has(name) ? values.get(name) : null),
    setItem: (name, value) => { writes.push([name, String(value)]); values.set(name, String(value)); },
    removeItem: name => { values.delete(name); },
    clear: () => values.clear(),
  };
}

function boot(saved = {}) {
  const store = storage(saved);
  const context = { btoa, localStorage: store };
  vm.createContext(context);
  vm.runInContext(defaults + '\n' + core + '\n' + readAloud, context, { filename: 'read-aloud-headings.vm.js' });
  const read = JSON.parse(vm.runInContext(`JSON.stringify({
    readAloudSystem: READ_ALOUD_DEFAULT_SYSTEM_PROMPT,
    readAloudContext: READ_ALOUD_DEFAULT_CONTEXT_PREAMBLE,
    readAloudMessage: READ_ALOUD_DEFAULT_MESSAGE_PREAMBLE,
    readAloudFinalFence: READ_ALOUD_DEFAULT_FINAL_FENCE,
    spokenBefore: READ_ALOUD_SPOKEN_BEFORE_PREAMBLE,
    messageAfter: READ_ALOUD_MESSAGE_AFTER_PREAMBLE,
    readAloudParts: READ_ALOUD_PARTS.map(p => ({ id: p.id, label: p.label, storage: p.storage, requires: p.requires })),
    refineSystem: REFINE_DEFAULT_SYSTEM_PROMPT,
    refineContext: REFINE_DEFAULT_CONTEXT_PREAMBLE,
    refineTranscription: REFINE_DEFAULT_TRANSCRIPTION_PREAMBLE,
    refineFinalFence: REFINE_DEFAULT_FINAL_FENCE
  })`, context));
  return { api: context.opcReadAloud, core: context.opcRefineCore, store, ...read };
}

function block(user, tag) {
  return user.match(new RegExp(`<${tag}>\\n([\\s\\S]*?)\\n</${tag}>`))?.[1] ?? null;
}

test('the default system prompt strongly requests short ##/### section headings for a long rewrite', () => {
  const f = boot(), s = f.readAloudSystem;
  assert.match(s, /2\) SECTION HEADINGS — STRONGLY REQUESTED\./);
  assert.match(s, /USE `##` FOR SECTIONS: break a long message into its real topics/);
  assert.match(s, /Use `###` only for a genuine subsection under a `##` section/);
  assert.match(s, /single `#`, never `####` or deeper, and never any other/);
  assert.match(s, /ADD THEM EVEN WHEN NOTHING IN THE MESSAGE IS A HEADING\./);   // inferred, not only source headings
  assert.match(s, /find the real\s+topic changes yourself/);
  assert.match(s, /A LABEL IS SHORT AND PLAIN: a few words \(one to four is typical\), no sentence, no question, no ending\s+punctuation/);
  assert.match(s, /Name the topic, not the action \("The rewrite"/);
  assert.match(s, /DON'T OVERDO IT: no heading over just a sentence or two/);      // never one per paragraph
  assert.match(s, /never one for every paragraph, never a heading with\s+no section under it/);
  assert.match(s, /no heading at all on a short single-topic message/);
  assert.match(s, /A long single-topic message should\s+still carry one `##` heading for its one topic/);
  assert.match(s, /THE PLAYER SHOWS EACH `##`\/`###` LINE AS A REAL HEADING and reads its label once as a short lead-in/);
  assert.match(s, /without\s+the `#` marks and never the word "heading"/);
  assert.match(s, /do not open\s+the section by announcing or paraphrasing it in a sentence of your own \("Next, the tests\."\)/);
  assert.match(s, /After the heading\s+line, start the section's first sentence directly\./);
  assert.match(s, /HEADINGS ARE ORGANIZATION, NOT CONTENT: each label is drawn from the message's own substance/);
  assert.match(s, /Headings AND the grounded\s+explanatory additions of 4\) are allowed and encouraged; neither permits invented facts/);
  assert.match(s, /A source heading becomes one of these `##`\/`###` heading lines/);
  assert.match(s, /Within the part, head each further genuine topic change the same way, and only those\./);
});

test('spoken prose stays plain: the heading lines are the only Markdown in the output', () => {
  const f = boot(), s = f.readAloudSystem;
  assert.match(s, /Natural spoken prose in plain text\./);
  assert.match(s, /The `##`\/`###` heading lines of 2\) are the ONLY Markdown in the output/);
  assert.match(s, /no bold, italics, bullet or numbered list markers, tables, code formatting, links, block quotes, horizontal\s+rules/);
  assert.match(s, /a comfortable listening block/);
  assert.doesNotMatch(s, /Plain text only: no Markdown/);                          // the old absolute plain-text rule
  assert.doesNotMatch(s, /A heading becomes a short spoken lead-in or transition/); // the old prose-transition rule
});

test('headings and grounded explanations are encouraged consistently, without invented facts or answering', () => {
  const f = boot();
  assert.match(f.readAloudMessage, /Return ONLY its spoken version, with the short `##`\/`###` section headings your system/);
  assert.match(f.readAloudMessage, /instructions ask for and the grounded explanatory additions they encourage/);
  assert.match(f.readAloudMessage, /Explanatory asides and full-sentence reminders may draw ONLY\s+from the message, chat history or spoken-so-far text supplied to you/);
  assert.match(f.readAloudMessage, /If you do not know, add nothing and never\s+guess/);
  assert.match(f.readAloudMessage, /Do not invent facts or add opinions, greetings, commentary or answers/);
  assert.match(f.readAloudSystem, /The short heading labels of 2\) and\s+grounded explanatory additions are encouraged; unrelated new content and invented facts are not allowed/);
  assert.match(f.readAloudSystem, /DO NOT invent facts or names\. Headings and grounded explanatory additions are allowed and encouraged/);
  assert.match(f.readAloudSystem, /heading labels come from the message's own substance; explanatory asides and full-sentence reminders come\s+ONLY from the MESSAGE, CHAT HISTORY or SPOKEN SO FAR supplied to you/);
  assert.match(f.readAloudSystem, /Your entire output is\s+the spoken version of the message with its short `##`\/`###` heading lines, nothing else\./);
  assert.match(f.readAloudFinalFence, /with the short `##`\/`###` section headings\s+those instructions ask for/);
  assert.match(f.readAloudFinalFence, /Headings and grounded\s+explanatory additions are allowed and encouraged: labels from the message's own substance/);
  assert.match(f.readAloudFinalFence, /asides and even\s+full-sentence reminders ONLY from the message, chat history or spoken-so-far text supplied to you/);
  assert.match(f.readAloudFinalFence, /If you do not\s+know, stay closer to the original and add nothing\. Never invent facts or names/);
  assert.match(f.readAloudFinalFence, /no opinions, greetings, commentary\s+or answers/);
  assert.match(f.readAloudFinalFence, /Output ONLY that spoken text: no preamble, no commentary, nothing else\./);
  for (const part of [f.readAloudSystem, f.readAloudMessage, f.readAloudFinalFence]) {
    assert.doesNotMatch(part, /only new content|exception to adding no content|add no other content/i);
    assert.match(part, /not (?:an|a chat) assistant|not a request to you/i);
  }
});

test('technical completeness and confident liberal rewriting replace brevity and word-for-word preservation', () => {
  const s = boot().readAloudSystem;
  assert.match(s, /TECHNICAL AND COMPLETE, MADE SPEAKABLE/);
  assert.match(s, /Keep the technical terminology and be longer wherever that helps understanding/);
  assert.match(s, /ENCOURAGED to make\s+extensive modifications: restructure sentences, expand explanations and transform visual material into speech/);
  assert.match(s, /Where you are not confident,\s+stay closer to the original and add nothing rather than guess/);
  assert.match(s, /Keep ALL the meaning: every fact, decision, number, caveat, question and request, in the original ORDER/);
  assert.match(s, /first person stays first person, a question stays a question, informal stays\s+informal/);
  assert.match(s, /You may rewrite even clean prose extensively for listening/);
  assert.match(s, /EXPLANATORY PARENTHETICALS ARE ENCOURAGED, even when they do not exist in the original/);
  assert.match(s, /Add short asides\s+or a full-sentence reminder/);
  assert.match(s, /Draw these ONLY from the\s+MESSAGE, CHAT HISTORY or SPOKEN SO FAR supplied to you, not outside knowledge/);
  assert.match(s, /a proposal must not become a fact/);
  assert.match(s, /When you do not know, add nothing\s+and never guess/);
  assert.match(s, /Use as much text as\s+understanding needs; do not trade technical detail for brevity/);
  assert.doesNotMatch(s, /stays word for word|in a\s+sentence or two|may be grouped/);
});

test('technical references keep their kind and speakable name, never an opaque id or a friendly substitute', () => {
  const s = boot().readAloudSystem;
  assert.match(s, /say the KIND OF THING and its SPEAKABLE NAME/);
  assert.match(s, /Keep its name as well as\s+its kind/);
  for (const phrase of ['the Python script named oc view dot py', 'the Python script named server dot py',
    'the JavaScript file named refine defaults dot js', 'the function named build request',
    'the setting named refine history mode', 'the Anchor Point for the Residue page',
    'the session named Manager Session two hundred twenty-seven']) assert.ok(s.includes(phrase), phrase);
  assert.match(s, /even\s+when the original gives only a bare name such as server\.py/);
  assert.match(s, /omit the id but retain the kind and name/);
  assert.match(s, /Commands likewise\s+keep their executable and subcommand names, and meaningful options and arguments/);
  assert.match(s, /never guess a name or kind\. NEVER spell out an opaque id's letters and digits/);
  assert.doesNotMatch(s, /or simply "the server file"|simply drop the handle/);
});

test('tables and other visual structures are spoken as shape, every detail in order, then a grounded summary', () => {
  const s = boot().readAloudSystem;
  assert.match(s, /For a table, FIRST describe its shape: how many data rows and columns it has/);
  assert.match(s, /THEN go through EVERY row in its original order/);
  assert.match(s, /tying each value to its column and keeping all facts, units, qualifiers/);
  assert.match(s, /FINALLY give a short spoken summary of what the table says, grounded in those rows,\s+not a new conclusion/);
  assert.match(s, /Do not collapse rows or replace the walkthrough with the summary/);
  assert.match(s, /same shape-then-details-then-summary approach to other visual structures/);
  assert.match(s, /Explain a nested list's levels and which details belong to which parent item/);
  assert.match(s, /A diagram becomes a description of its components, connections, direction and grouping/);
  assert.match(s, /For code blocks, commands, JSON, logs, diffs and stack traces, first name the kind of block and its shape/);
  assert.match(s, /Then\s+explain its contents and operations in order/);
  assert.match(s, /a one-sentence gist is not a substitute for the details/);
});

test('the multi-pass wrappers keep the active section and one hierarchy across a seam', () => {
  const f = boot(), before = f.spokenBefore, after = f.messageAfter;
  assert.match(f.readAloudSystem, /SPOKEN SO FAR\s+\(<spoken-before>\)/);   // the existing part explanation stays
  assert.match(before, /complete spoken version of the message up to the part you rewrite now/);
  assert.match(before, /including every `##`\/`###` heading already read/);
  assert.match(before, /no new or repeated heading at the seam unless this part genuinely\s+starts a new section/);
  assert.match(before, /Keep one hierarchy across the whole message: same heading levels, no restart and no\s+renumbering/);
  assert.match(before, /no greeting, introduction, recap, "continuing" phrase/);
  assert.match(before, /or part label, nothing it already said again/);
  assert.match(before, /with a heading line only where a genuine new section begins/);
  assert.match(before, /ground names and explanatory reminders for\s+the current part, without recapping earlier content or inventing facts/);
  assert.match(before, /\{\{spoken_before\}\}/);
  assert.match(after, /raw original text of the message after the part you rewrite/);
  assert.match(after, /Do not add a heading for a section that lives in this later\s+text/);
  assert.match(after, /a heading belongs only to a section inside the part you rewrite/);
  assert.match(after, /If the part ends inside a section, leave\s+that section open for the next part/);
  assert.match(after, /NEVER rewrite, echo, summarize, or anticipate it/);
  assert.match(f.readAloudSystem, /REST OF THE MESSAGE is only for orientation, never for\s+anticipating later content/);
  assert.match(after, /\{\{message_after\}\}/);
});

test('the generated request carries the new defaults and both part wrappers unchanged', () => {
  const f = boot();
  const text = 'The middle part.\n\n## It still has a heading.';
  const spoken = 'What was said first.\n\n## Active section\n\nEarlier prose.';
  const later = 'The **raw** rest.\n\n## A later section\n\nLater prose.';
  const request = f.api.request({ text, speaker: 'the Manager', spokenBefore: spoken, messageAfter: later });
  assert.equal(request.system, f.readAloudSystem);
  assert.equal(block(request.user, 'message'), text);
  assert.equal(block(request.user, 'spoken-before'), spoken);
  assert.equal(block(request.user, 'message-after'), later);
  assert.ok(request.user.includes(f.spokenBefore.replace('{{spoken_before}}', spoken)));
  assert.ok(request.user.includes(f.messageAfter.replace('{{message_after}}', later)));
  assert.ok(request.user.endsWith(f.readAloudFinalFence));
  const order = ['===== SPOKEN SO FAR (READ-ONLY)', '===== MESSAGE TO READ ALOUD', '===== REST OF THE MESSAGE (READ-ONLY)',
    '===== FINAL INSTRUCTION'].map(marker => request.user.indexOf(marker));
  assert.ok(order.every((at, index) => at >= 0 && (index === 0 || at > order[index - 1])), JSON.stringify(order));
  assert.doesNotMatch(request.user, /\{\{(context|message|spoken_before|message_after)\}\}/);
  assert.match(request.user, /no new or repeated heading at the seam/);
  assert.match(request.user, /Do not add a heading for a section that lives in this later/);
  const one = f.api.request({ text: 'A short message.' });
  assert.doesNotMatch(one.user, /SPOKEN SO FAR|REST OF THE MESSAGE/);
  assert.equal(one.system, f.readAloudSystem);
});

test('a saved pre-heading override still wins, with no migration write', () => {
  const old = 'CUSTOM SPOKEN-WORD SYSTEM PROMPT\nNo headings; plain speech only.';
  const f = boot({ refine_read_aloud_system_prompt: old,
    refine_read_aloud_message_preamble: '===== OLD MESSAGE =====\n<message>\n{{message}}\n</message>' });
  assert.equal(f.api.parts().read_aloud_system, old);
  assert.equal(f.api.parts().read_aloud_message, '===== OLD MESSAGE =====\n<message>\n{{message}}\n</message>');
  const request = f.api.request({ text: 'Still readable.' });
  assert.equal(request.system, old);
  assert.match(request.user, /===== OLD MESSAGE =====/);
  assert.equal(block(request.user, 'message'), 'Still readable.');
  assert.deepEqual(f.store.writes, []);                                            // no default pasted over the override
  assert.match(f.readAloudSystem, /SECTION HEADINGS — STRONGLY REQUESTED/);        // the default itself is the new one
});

test('Refine\'s own prompt keeps its own heading rules and gets none of the listening rules', () => {
  const f = boot();
  assert.deepEqual(f.readAloudParts.map(p => p.id),
    ['read_aloud_system', 'read_aloud_context', 'read_aloud_message', 'read_aloud_finalfence']);
  assert.deepEqual(f.readAloudParts.map(p => p.storage),
    ['refine_read_aloud_system_prompt', 'refine_read_aloud_context_preamble', 'refine_read_aloud_message_preamble',
      'refine_read_aloud_final_fence']);
  assert.match(f.refineSystem, /5\) DOCUMENT FORMATTING — HEADINGS, BOLD, and ITALIC, REQUESTED by the user \(not merely allowed\):/);
  assert.match(f.refineSystem, /A LEAD HEADING: open anything meaningful with one `#` heading/);
  assert.match(f.refineSystem, /SECTION HEADINGS: when the text is a LONG narration/);
  assert.match(f.refineSystem, /No section heading over just a sentence or two/);
  assert.doesNotMatch(f.refineSystem, /HEADINGS ARE ORGANIZATION, NOT CONTENT/);
  assert.doesNotMatch(f.refineSystem, /THE PLAYER SHOWS EACH/);
  assert.doesNotMatch(f.refineSystem, /no new or repeated heading at the seam/);
  assert.doesNotMatch(f.refineSystem, /paragraph-sized listening blocks/);
  assert.doesNotMatch(f.refineFinalFence, /##|###|read aloud/i);
  const refine = f.core.buildRequest({ transcription: 'dictated words', parts: { system: f.refineSystem,
    context: f.refineContext, transcription: f.refineTranscription, finalfence: f.refineFinalFence } });
  assert.equal(refine.system, f.refineSystem);
  assert.ok(refine.user.endsWith(f.refineFinalFence));
  assert.doesNotMatch(refine.user, /Read aloud|`##`\/`###` section headings/);
});
