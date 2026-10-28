# CodeNomad Session Watcher

## Packaged 0.1.12: one shared preview core (U2 read-only cockpit)

`tool-preview-core.js` now owns the only `preview`/`cropString` implementation;
the manifest and affected fixtures load it before `tool-previews.js`. Scanner
record association, bounded caches, shared row requests/counts, native controls,
watcher and inspector behavior are unchanged. Native `mcp__workflowy__<family>`
and existing Workflowy names retain verb-first, original-rendered-value short-first
params with stable ties, nested Unicode first/last64 crops and total1200-point cap.

The same pure module accepts the pane reader's bounded structural previewData
descriptors. This is presentation-only, never snapshot/raw Copy/count authority.
The served `/sessions` page now calls this same core with descriptor mode; it loads
only pure helpers, never the CodeNomad scanner/observer/cache. Both production
consumers have direct and real-DOM/offline checks. Native calls use the pane's ONE
mailbox/held inspector, separate from explicit target ACK and browsing. This is
submitted U2 source, not Conductor acceptance or native client qualification.
Packaged0.1.12 is not activation.
Nothing was reloaded/restarted; final U2 checks/handoff and Manager/Dan idle
authorization must precede activation. Extension reload + CodeNomad reload is
distinct from a pane-server restart/page reload, with no OpenCode/proxy restart.

Loaded unpacked in Chrome, this extension adds five small features to CodeNomad's loopback page:

- `watcher.js` reads the visible session labels and reports the open session to the transcription pane.
  In 0.1.7 it also reports that session's scoped model identity and thinking-selector choice.
  It only reads: it never clicks, types, switches models or sends prompts.
- `session-focus.js` (0.1.13) is **"Open in CodeNomad"**, the extension's only clicks. See below.
- `message-format.css` gives user and assistant Markdown paragraphs and top-level lists/quotes a full text-line
  gap. Double newlines become paragraph boundaries in CodeNomad; this makes that separation clearly visible.
  It does not rewrite message text or alter nested lists and code blocks.
- `tool-previews.js` / `tool-previews.css` put compact JSON input previews in every tool header,
  not just Workflowy calls. Native titles, status, actions and expand/collapse remain intact.
  Inputs come from CodeNomad's authenticated, same-origin message GET, not its cropped inline panel.
  Reads and independent **In / Out** size labels are shared by rows in each message (at most three concurrent).
  Visible live calls refresh at most every two seconds for output growth; terminal snapshots stop polling.
  The cache retains bounded previews, byte counts and optional original inputs up to 64 KiB each, never output bodies.
  Limits: 16 MiB per response, 8 MiB per part/raw snapshot, 120 messages and an 8 MiB serialized cache-data budget;
  at most 5,000 tool parts per message. Inputs are released first under cache pressure, without losing counts.
  Missing/offline/oversize/cache-limited data shows **—**, not a fabricated zero; a failed/oversized part does not
  block other parts within a readable message. A whole message over the read bound cannot supply its rows' counts.
  Previews use a font exactly 1pt smaller than the native header and wrap to at most three lines.
  String values keep the first/last 64 Unicode code points (128 original characters plus a middle `…`);
  short strings are unchanged. Workflowy tool families keep `verb` first and sort only immediate
  `params` keys short-value-first, measured from original JSON values before cropping, with stable ties.
  Other tools keep their key order; nested objects are never reordered.
  Streaming inputs are serialized JSON fragments (initially an empty string), not final arguments.
  They remain hidden and refresh until the SDK reports parsed running/completed input; genuine final
  string inputs are still shown as strings, without speculative JSON parsing. This fixes previews
  getting stuck at `""`. Completed/aborted messages do not poll indefinitely for unfinished input.
  Offline/login failures retry after 15 seconds and show `arguments unavailable`; no credentials are
   stored and preview reads remain inside CodeNomad. No session-history inventory is fetched.
- `tool-inspect.js` / `tool-inspect.css` make an ordinary tool-header click (including the argument preview)
   inspect that call in the Transcription Pane. Extension **0.1.11** emphasizes right-side input/output sizes; the
  small **Toggle** button invokes native expand/collapse;
  its tooltip and accessible name are **Toggle expanded state**. The two buttons retain native keyboard
  activation; copy, status and other actions stay independent. New calls, remounts and scrolling never select anything.
  The extension sends only full origin/instance/session/message/part identity, source labels and an immutable
  record-association witness to the existing pane server; never tool bodies or credentials. It reads the local
  workspace descriptor and authenticated same-origin session/message to prepare that witness. Source failures
  open a visible error in the pane; relay failures give the header an error outline and retry tooltip.
  Only another deliberate header click retries. The selected header has a subtle theme-aware neutral background
  and left-edge marker. Its ephemeral full call identity survives same-session row replacement or scrolling away
  and back; no DOM node is the selection authority. Missing/ambiguous active panes hide the marker but retain
  identity; a confirmed session/instance switch clears it (returning requires another click). The Pane's held
  inspection is independent and is not cleared by session switching. Pending selection is marked immediately;
  an accepted source error stays marked, but a failed relay cannot claim acceptance and removes the highlight.
  This is the current shared local OpenCode service / single-CodeNomad-tab workflow, not remote routing.

## Open in CodeNomad (0.1.13): the only clicks

The pane's OpenCode glance card has an **Open in CodeNomad** button. It posts one request (session id, the
session's folder, its title) to the pane server. `session-focus.js` polls `GET /api/focus-request` about once a
second (every 2 s while the page is hidden). A page whose workspace tab matches the folder claims the request
(nonce + its own watcher id, compare-and-claim; a hidden page waits 1.5 s so a visible one acts first), then:

1. clicks that workspace tab, `button.tab-base[role=tab]` whose `title` is the folder. The folder match is
   normalized (`E:\…` = `/mnt/e/…`, no trailing slash), and a parent folder matches a subfolder or worktree
   (the most specific tab wins);
2. clicks the session row, `.session-item-base[data-session-id=‹id›] button.session-item-select`. A subagent's
   row first needs its parent's expander. A row the list has not loaded is found with CodeNomad's own session
   search (the title is typed into "Search sessions", and the box is put back as it was afterwards);
3. confirms the session is shown (the same visible stream / active row the watcher reports) and reports
   `done`, or `failed` with a message naming what was missing.

A page with no matching tab says `no-tab` without claiming. The pane then says "Open the CodeNomad tab for
‹folder›", and the request waits up to a minute, completing by itself once that tab is open. The extension
never opens a workspace itself.

**The contract.** The extension clicks only to select a workspace tab and a session (plus a subagent's parent
expander and the session search, to find its row), only when the user asked from the pane. It never types or
sends a prompt, never switches models or thinking levels, and never changes stored messages. Selectors are
pinned to CodeNomad 0.20.1 (`SELECTORS` in `session-focus.js`, English "Search sessions" label). After an
upgrade, a changed control fails visibly in the pane, never silently.

## Activate an update

Reload the unpacked extension in `chrome://extensions`, then reload the CodeNomad tab.
No OpenCode or transcription-pane server restart is needed. A display update also applies to existing messages.
When also updating **thinking bridge or persistent inspector pane-server code**, restart the pane server and reload its page
(see its README); do not restart during Send/Refine. No OpenCode/proxy restart is needed.

## Persistent tool inspector

Keep CodeNomad and `http://127.0.0.1:9911` side by side. Click the tool header or argument preview to inspect;
click **Toggle** for native expansion without changing the Pane selection. The pane
holds the latest deliberately clicked call, with stored input above output, separate scrolling, a draggable
and keyboard-resizable divider, plus a dictation/inspector separator and collapse/close controls. Inspector
selection is independent of Send/Refine target and session heartbeats. Close releases selected tool data;
only another deliberate click reopens it. A page reload may reread the current memory-only server selection;
there is no durable tool history, pinned list or remote-instance router. A server restart loses the mailbox;
an already-open pane labels its retained snapshot instead of silently choosing a different tool.

Previews retain final inputs; shared row-size reads and the pane's **separate reader** refresh running calls
every two seconds until terminal state/message completion, then stops. Stored results bypass the native
10,000-character inline crop, but upstream guard stubs remain stubs. Plain text/pretty JSON only. Source reads
are bounded to 16 MiB; the selected part and rendered input/output are bounded to 8 MiB, with a visible refusal,
never a silently cropped "full" result. Missing, mismatched, oversized and unsupported sources fail visibly.
Witness validation compares session creation time/location, message creation time and tool identity/call ID
against the pane's fixed authenticated backend; there is no arbitrary destination or browser service password.

### Raw snapshot sizes and readable documents

**KiB = 1024 UTF-8 bytes**, measured before Pretty JSON or Readable text. Rows show `In: X KiB · Out: Y KiB`;
loaded inspector captions say `Input stored (X KiB)` / `Output stored (Y KiB)`. `tool-record-text.js` is the
single browser snapshot/counting definition, packaged here and served by the Pane at `/tool-record-text.js`.
String input/legacy output is its decoded stored string, not a JSON-quoted wrapper. Structured values keep
their original backend wire JSON. V2 `state.content` takes precedence: text blocks are joined in order with
two newlines; non-text blocks use their complete wire JSON (including encoded image data). Error headings
and separators are included exactly as raw Copy sees them. Counts are **stored snapshot bytes**, not token
counts, original file/binary sizes, or proof of what a provider received. Saved stubs count only their retained
stub, not the unrecoverable pre-guard output. Tooltips explain this; only a known empty snapshot is zero.
Live snapshots can grow; unavailable data stays unknown. Reused/remounted rows are reconciled by scoped
origin/instance/session/message/part and validated record witnesses, never by rendered argument text.

Size labels have a **15px baseline**, up from 10px in rows and 12px in Pane captions. A continuous
gray-to-red foreground curve uses `log1p(bytes / 16384) / log1p(64)`, capped at **1 MiB (1024 KiB)**.
Thus tens/hundreds of KiB are visibly warmer; known zero is gray, unknown stays gray with **—**/missing/loading,
never zero. From **256 KiB to 1 MiB**, the font smoothly increases to **19px** (a capped 3pt lift), with no
animation. Each In/Out value is independent. Light-theme gray/red are `#59616e`/`#b91c2c`, dark-theme
`#a6adb9`/`#ff8080`; the gradient maintains ≥4.5:1 contrast on the pinned native surfaces, including selected
headers. Exact numeric KiB remains visible; color is supplementary and forced-colors uses system text.
The reserved 30px row strip keeps controls from shifting as counts grow. No measurement/fetch/cache changes.

The Pane's independent **Readable text** options default OFF and are enabled only with **Pretty JSON** ON.
They decode multiline/tabbed JSON string values once into inset safe plain-text blocks, keeping all surrounding
keys, nesting, array positions, other values and trailing prose. This is visibly a **reading view, not valid JSON**;
keys/simple strings remain escaped JSON. Literal backslash+n after decoding is not recursively unescaped.
Raw Copy stays exact. Independent Pane **Word wrap** checkboxes default ON and survive selections within
the page; OFF preserves physical newlines/tabs and scrolls horizontally inside that reader in raw, Pretty JSON
and Readable text modes. Wrap changes are CSS-only, retaining selected text and mounted document blocks.
Activate **0.1.11** with extension reload + CodeNomad reload + Pane page reload. If the 0.1.10
wire-preserving/shared-script server is already active, **no server restart** is needed. A first-time inspector
installation still needs the updated Pane server. No OpenCode/proxy restart; tests never activate production.

## Thinking selector → pane Send (0.1.7)

The adapter is pinned to **CodeNomad 0.20.1, English UI**. It reads the sr-only combobox inputs' `aria-label`
under `.prompt-context-controls`, inside the visible active `.session-cache-pane` belonging to the stream's
same session **and instance**. `[data-model-selector]` provides the exact provider/model ID;
`[data-thinking-selector]` provides the selected variant. It never uses assistant labels, search inputs,
popup text, global selectors or arbitrary page text. Missing/ambiguous/hidden controls or other-language
labels report **unknown**, not Default. Explicit `Thinking: Default` is a known choice with `variant: null`.

Thinking/model/session label changes are observed (including `aria-label` mutations); unchanged reports
heartbeat every 15 seconds. Writes are serialized/coalesced, with observation time and sequence to reject
out-of-order arrival, including old-tab reports. The pane relay remains one latest visible tab, not a tab
picker: use one CodeNomad tab for reliable following. There is a short observation/relay delay (normally
120 ms plus the pane's 700 ms poll); let the selector settle before Send. A detected revision/model change
refuses Send rather than applying another selection, retaining the draft for retry.

The **pane server**, not this extension, validates the supported variant in the session-location's OpenCode
catalog and applies it immediately before a normal followed Send. It preserves provider/model, awaits the
switch and checks acknowledgement. Manual/stale/unknown-selector sends keep the existing level and say so;
queued Send deliberately does not apply the selector or change a generation already running.
No new extension permissions, credentials or npm bundle edits are involved.

## Browser regression check

Open `tests/message-format.html` in Chrome, or use Chrome's headless `--dump-dom` on its file URL with an
isolated temporary `--user-data-dir`. The fixture uses CodeNomad 0.20.1's relevant baseline CSS and the
extension stylesheet. `document.body.dataset.testPassed` must be `"true"`; `testResults` records all ten checks:
full-line user and assistant paragraph/list gaps, unchanged nested-list/other-UI spacing, no trailing gaps,
and unchanged literal blank lines in code.

Run `node --test tests/tool-previews.test.cjs` for record lookup/cache/lifecycle checks.
Run `node --test tests/watcher.test.cjs` for session/thinking observation and ordered-report checks.
Run `node --test tests/session-focus.test.cjs` for Open in CodeNomad's folder matching and pinned selectors;
`tests/session-focus.html` is a CodeNomad 0.20.1 DOM fixture (tabs, delegated clicks, a virtualized row found
by search, a missing tab, a changed UI) with mocked pane requests. Headless Chrome
`--virtual-time-budget=60000 --dump-dom` must report `data-test-passed="true"`.
Run `node --test tests/tool-inspect.test.cjs` for deliberate selection, stale work, bounded source reads,
association validation, whole-row replacement/new arrivals, selected identity rehydration, session/instance
switching, Toggle isolation and native-control preservation. `tests/tool-inspect.html`
is an isolated real-DOM fixture with mocked requests; headless Chrome with `--allow-file-access-from-files`
and `--virtual-time-budget=15000 --dump-dom` must report `data-test-passed="true"`.
`tests/watcher.html` is an isolated real-DOM fixture with mocked fetch (no live browser injection or prompts);
headless Chrome `--virtual-time-budget=5000 --dump-dom` must report `data-test-passed="true"` (10 checks).
Open `tests/tool-previews.html` for a narrow/wide header layout fixture; its `data-test-passed`
must be `true`. After activation, check real Workflowy and shell rows, an in-flight call,
session switching, native row actions/expansion and the existing paragraph spacing.
For inspection, also hold a running call through new arrivals/completion, virtual scrolling and session
switching; confirm the pane remains on that call and its dictation focus/draft/Send target stay unchanged.
The adapter targets the installed CodeNomad 0.20.1 DOM and pinned OpenCode V2 response shape.

## Known upstream limitation: long ordinary Markdown

CodeNomad 0.20.1 applies a 10,000-character tool-output display cap to ordinary user/assistant
Markdown too. Extension 0.1.7 does **not** fix that cap. See
[LONG-MARKDOWN-INVESTIGATION.md](LONG-MARKDOWN-INVESTIGATION.md) for the tested native-renderer
reuse hazards, the concrete maintained source-patch alternative, and an isolated diagnostic fixture.
The diagnostic/test files are not loaded by the extension; this investigation needs no activation.
