# transcription-pane: Transcription Control for OpenCode / Claude

## Claude Manager / live history — the terminal cockpit is gone

Choose **Claude / live and history** on the same pane origin. In a full-width tab
(leave Chrome's CodeNomad split view for Claude), dictation and the existing
inspector stay on the left; the **existing `/sessions` component** is embedded on
the right, not a second viewer or editor. At narrow widths it stacks below the
editor. OpenCode mode hides the embed and preserves the original split-view route.
Switches retain drafts, provider settings, the held inspector and browser scroll;
CodeNomad heartbeats never retarget Claude. The embed is lazy and its hidden polls
pause. `/sessions` remains an optional standalone read-only convenience.
**Layout (round 5):** `[transcription column | tool column] | conversation`. One draggable
divider sets the transcription/conversation split, another the tool column's width (pointer or
Left/Right arrows, Home/End; both remembered in this browser). The tool inspector lives in
its own full-height column right of the transcription column, present from page load (round 9a:
"No tool call selected" until a tool is clicked; input above output) and moves back under
dictation in OpenCode mode, keeping its reading position. Its top is one thin row, the tool name
and the call's own date and time (the record time the conversation shows); clicking it unfolds
the source details, the read status and **refresh**. A read error always shows. There is no
Close or Collapse: the pane cannot be hidden. Above the edit area only the backend, the session
badge (on the backend row: role and name only, two points smaller, its status text only while
not plain "Ready", UUID/source on hover), model/thinking pill, Manager model/thinking/Apply and
the context line stay visible, in smaller type; the OpenCode session dropdown is hidden; receipts,
status lines, Refresh/Wait/Re-check settings and Refine-without-context sit behind one
**▸ details** toggle (collapsed by default, remembered). **＋ New Manager** sits at the top of the
conversation view's session list, always visible (round 6). The only Interrupt is the
one beside Send. The context **view** dialog opens scrolled to the newest context; Escape
closes it (native dialog). The buttons under the editor (Refine, Send, Interrupt, last sent,
originals) are compact single-line buttons; the cost totals wrap to their own line when narrow.
A status set while a Send or control is uncertain names its
subject; the confirming receipt replaces it (accepted, then delivered, saying whether the
retained draft was sent) unless a newer line already did.
Send/Refine statuses are layout-free toasts on both editor pages (`static/status-toast.js/.css`):
success/progress fade after three seconds; errors are red and stay until × or a replacement.
**Status** in the desktop header, or **⋯ → Status messages** on the phone, retains the full notes
for this page load, including the thinking reminder. **History** on a toast opens the same log.
This changes presentation only, not receipt ownership, draft clearing or Send/Refine behavior.

Host-registered Manager/Conductor/worker/helper/base sessions use the independent
headless host's **durable conversation rows** through `claude_live.py` when complete; missing,
building or unreadable rows (and an older host) automatically use today's durable-event replay.
When the host explicitly reports unreadable stored rows, only that conversation adds one quiet line:
*stored rows for this conversation are damaged; showing what the raw log allows*.
The raw path, messages already on screen, Send and controls are unchanged; ordinary conversations
and older hosts show no new line.
The page's diagnostic `rowSource` is `host` or `fallback`; the browser does not branch on it. Unmanaged sessions
still use contained JSONL history through `claude_reader.py`. Source, role,
generation and capabilities are explicit, with honest incidents,
native retries, silence, background tasks and unknown ownership—not inferred
busy/idle or task acceptance. Browsing cannot launch/resume/control a process; only an
Send reconnects a collected/ended registered Manager conversation (including the host's scheduled five-hour recovery notice).
The desktop ribbon names the **conversation being viewed** immediately after its context pie,
in blue, with a maximum width, ellipsis and the whole name on hover. It keeps the ticking
last-event age (OpenCode: read age), context and as-of time, without activity words or
background counts. Offline/observation failures, sleeping/ended states and settings-unverified
warnings remain because they describe a real limitation, not routine activity. The phone's
Chat ribbon is unchanged. It is the only status line always shown: the
session details, scope/leaf, target, paging controls, Tools only, preview lines, status
lines and evidence sit under the tiny **more info & options** expander (a red dot on it
means one of those lines reports a problem). **⛔ Usage-limit halt** (`static/quota-halt.js`, the host's `quota_halt` of the session's own generation): a red line under that banner (and under the phone Chat's state line) reads *⛔ Halted: Max usage limit reached (five-hour), resets 14:50.* The host's five-hour `quota_recovery_check` adds one line: *Manager recovery check due at … / sent / delivery problem …*. The current Manager receives one labelled notice at the structured reset + 5 min and coordinates Conductors; no interrupted turn is replayed. Manual Send/account switching and resume remain available. Other windows have no automatic notice. The row (desktop sidebar, phone Programs, the phone's Manager chips) carries a ⛔ with that text on hover and in its glance; the next accepted input clears it. A thin ribbon above the conversation holds
the **Following / Paused** toggle, **⤒ First** (the conversation's very first message), jumps
between your messages and between final answers, a thinking open/close switch, Tools only, Subagents,
**Reload** (just after Subagents; the same newest-turn reload) and, at its right end,
**⟳ Reconnect** (round 9b, below).
"Earlier" is one mechanism (round 6): scrolling to the top, ↑ past the oldest message on screen and
**Load earlier messages** all load the page before the oldest one shown, above a stable reading
position, and ↑ keeps loading until it finds the previous message or reaches the session's start;
the same holds forward (scrolling to the bottom, ↓, **Load later messages**). ↑/↓ clicks queue:
five quick clicks are five moves, in order. ⤒ First reopens the conversation at its first page
(paused) and lands on its first message; Following resumes only at the true newest turn. **Following lit always means pinned to
the last pixel**: every growth path pins, and a one-second check re-pins anything else.
**One contiguous run, never a silent gap (round 9b).** Every page row carries its position (`seq`) and
the viewer keeps exactly one contiguous run of rows: a row is mounted only where it extends either end,
and over the 200-event bound rows leave from an end only (following: the oldest; paused: the oldest above
the reading position, else the newest, and the view is then *behind*). It used to keep the row being read
as an island above the live tail, which is how Dan read a 16:18 message directly above 17:47 with nothing
marked between. A line under the newest message says what is pending: *Loading older messages above …*
for an older read, *Loading newer messages …* only for rows ahead (never metadata-only paging),
or *Still loading. Feel free to send messages while the load completes.* for a writable Manager's first
page/indexing wait (*Still loading …* for view-only history), with the known MB source progress.
Transient failures during that wait stay calm, with the actual retry delay. **A slow or failed read never
blanks the rows on screen (U1.1):** a read still unanswered after 4 s, or one that timed out or failed
without a refusal (a 5xx, the 12 s observer deadline, the network), reads *Catching up … the rows on screen
stay; newer messages follow by themselves.* (plus *Feel free to send messages meanwhile.* for a writable
Manager), retries on the usual backoff and clears itself with the next good read; the detail status line
keeps its T5.6 wording. The phone's `/m` chat keeps the same rule with *Catching up … the messages on screen
stay; newer ones follow by themselves.*, and its resync (void handles, `stale-epoch`) keeps the messages on
screen until the same conversation's fresh first page arrives, instead of clearing them first. Otherwise it reads
*Newer messages are not loaded below this point · ↓ Load them · Jump to latest* (the toggle
reads *Paused · newer below*), *Later history is not indexed* (native transcripts only), or **⚠ Not updating since ‹time›: ‹reason›**
with Reconnect; a view that stopped updating shows **Not updating** on the toggle (never a lit Following)
and clicking it reconnects. **⟳ Reconnect** (always available) reads the whole list again (fresh handles),
finds the same conversation by its stable identity (host execution, a newer generation included; else the
transcript; a subagent through its parent) or, with nothing selected, the newest Manager (a live one first),
clears the fold's stale Inspect/history lines and reloads it at its newest turn, Following. The orange
"Subagent discovery unavailable …" line stays until its discovery succeeds again. Handles are tokens in the
pane server's memory: every pane-server restart re-issues them all; the sidebar keeps the selected row
highlighted by identity.
**Resync in place (FMPZO7).** When a read is refused because its handles no longer name anything (a
pane-server restart, an evicted or rebuilt index, a newer generation: statuses `stale`, `stale-epoch`,
`stale-generation`, `gap`, `reload-required`, `changed-index`), the view does not stop: it reads the list
again, finds the same conversation by identity, and reads its window again from the oldest row on screen by
position (`fromSeq`), checking each returned row against the row at that position (its kind, and its UUID
wherever both have one). A match takes the fresh handle in place, so a reply cut off mid-stream completes;
nothing is lost or doubled, Following and the reading position stay as they were, and the wait reads
*Reconnecting … the rows on screen stay.* (the toggle reads *Following · reconnecting*, also while the restarted server rebuilds its index). Rows that do not match
(new code built them differently) fall back to the full Reconnect reload with a neutral notice. The orange
**⚠ Not updating …** line appears only when re-acquiring really fails (the conversation is no longer listed,
or about 30 s of refused reads); a resync whose reads are only slow or unanswered (a slow host's
read deadline included) stays *Reconnecting …* however long it
takes, never ⚠. It keeps retrying by itself and clears on success. "Load earlier" reaches back by
position once the old checkpoints are gone.
Selecting text in the conversation pauses it visibly (**Paused · text selected**); clearing the
selection or leaving the conversation frame resumes it (a selection used to hold the view
silently behind a lit Following). Opening a different conversation lands Following. Hovering
a chat block shows a small **⧉** left of its date that copies only that message's own
Markdown source (no thinking or tools); rendered code blocks have their own ⧉. A small
**context pie** at the left of the status bar shows the share of the window used by the
session's last completed call — for Managers, every subagent (its own sidechain calls from
its own transcript) and ended sessions — green, then orange, then red by the thresholds in
the `sessions.css` theme block, exact figures on hover; an unknown newer read keeps the last
known figure for the same conversation even when a reconnect or pane-server restart reissues
its handles. The hover keeps that figure's as-of time and says why the newest read is unknown;
it never borrows another session's or subagent's figure. Following (the default) lands a
selection on the newest turn (it traverses later pages like Jump to latest) and pins
every arrival or growth into view (new rows, streaming rows, subagent groups gaining entries);
scrolling up by hand pauses it, and scrolling back to the
bottom resumes it. Paused never moves the reading position and counts new turns.
While paused with the last turn wholly off screen (or newer turns not loaded), a floating **↓ Latest**
badge sits at the bottom right of the conversation, as on the phone (same seen rule and states, below);
clicking it does what the toggle does: the newest turn, Following. The Paused label's *N new ↓* is
the same unseen count.
Turns are told apart: your messages green, final answers (assistant prose with no tool
call before the next user turn) red, prose inside a tool-using round an orange bar on a
dim red ground, tool calls a gentle blue. Hovering a block lights its left bar bright blue. Claude Code stores every tool result as a `user` record whose
content is already shown under its call; those records fold away. Other records without
prose (meta messages, attachments, system records) collapse to a one-line flag whose
disclosure lists their metadata; native task observations (a background command finished) are such
one-line flags too. Every turn's header is always shown (a little fainter on tool and thinking
turns) with its full **date and time** (`Mon, Dec 4th, 2026 3:07 PM ET`, browser time zone)
where its UUID used to be; the UUID stays on hover. Every Claude block's header names, right after its role and
dimmer, the **model and thinking level recorded on that message** — `Claude · answer (Opus 5.5 · high)`, or
`(Opus 5.5)` when no level is recorded for it, nothing when no model is. The model is the message's own `message.model`
(host stream or transcript); the level is its transcript record's `effort` / `perTurnEffort` (the request's
`output_config.effort`; two different values mean none), joined to a host block by its exact record UUID and message
id, and a level that lands after its stream frame follows as a display update. Never a session default, an agent
name or a guess; the raw id and level are on hover. **Next/previous** in You and Answers walk every
message (next used to stick on the first). **Subagent activity** in a host Manager's view: between two
primary turns (your message, an assistant message or a Manager tool call) each subagent's work is one
tiny group in that subagent's own color (index among the Manager's subagents, ten colors cycling):
a header row (marker, the run's description, number of calls, time span), expanded by default
(a group you fold or reopen by hand keeps that choice in this browser), and one
row per call (time, tool, one-line preview), text or background command. Clicking a call opens it in
the tool inspector. The ribbon's **Subagents** chip folds every group to its header row (this page view
only; a reload starts expanded). The data is the host's own stream: forwarded child frames (`parent_tool_use_id`) and
native task events attributed by `tool_use_id` / `owned_by_subagent` + `parent_task_id`; never main
rows, leaf, cursor or Refine context. **Running now** (host signals only): the live Manager's
result-less call in its still-open turn and a running subagent's result-less calls are yellow; the
sidebar row of a working Manager (its own turn open, however it started, or a Send unanswered) and of a
running subagent (in the Manager's native background-task registry) gets a yellow spinner; pending
background tasks show as a secondary `◌ N in background` on the sidebar row, not the desktop ribbon. Missing evidence/history/offline is explicitly unknown in the detailed evidence; silence
never triggers an action. Host assistant text and tool starts update the same keyed
rows until their final replacements. A complete stored conversation opens at its newest ≤100 rows;
scrolling up, Load earlier and ⤒ First still reach position zero. Its epoch survives a pane-server restart;
process-local handles are re-adopted by position through the existing resync. Row bodies, tool witnesses,
subagent groups and Refine exclusions use the same renderer as replay. Activity is observed through a
throwaway shared-projector tail starting at the newest main row, within the existing two-second budget;
a permission request predating that row, an older-generation seed or an unfinished tail may stay unknown.
The fallback alone drains 500 raw events per host
page within a two-second refresh budget; tail-only indexing continuations follow
back to back with UI yields and a neutral loading/event-count label. They automatically
finish scanning the indexed tail, including an already-completed last reply on
first opening; later pages carry replacements for earlier streaming rows too.
While paused, later history loads as you scroll down (or with ↓ / Jump to latest). Thinking summaries are separate safe-text
disclosures above the reply, initially expanded while streaming and collapsed for
stored history; updates retain your disclosure state, selection and viewport.
Omitted/redacted/body-limit summaries are a small dim parenthetical on the header, not reconstructed. Summaries are
display-only and never enter assistant prose, Refine or conversation context.
User messages and final assistant reply text render as Markdown by default:
headings, emphasis, lists, tables, code, links and blockquotes. Streaming replies
stay complete plain text until final. Locally vendored Marked + DOMPurify escape
source HTML and sanitize rendered markup; images stay text and links open in a
new tab with noopener/noreferrer. Thinking and tool previews are unchanged; the
inspector's own Markdown reading view is described with Readable text below. Display formatting never changes stored text or Refine context.
**Game boards:** a fenced block in the language `fen` (one FEN line; an optional second line is
the last move, e.g. `e7e5`, which is highlighted) renders as a static chessboard, White at the
bottom, with a "Black to move" caption, in the desktop viewer and on the phone alike. The board is
built after sanitization from DOM nodes and text only (CSS grid, Unicode pieces, no library); a
block that does not parse stays the ordinary code block. Another game is one more renderer in the
`boards` map of `message-markdown.js` (e.g. `sgf` for Go); a `pgn` block would need a move
generator and is not drawn.
Unchanged prose is not reparsed on polling/metadata updates. Activate with a pane
page reload only (save unsaved dictation first); no pane/Claude host restart.
The same banner carries the session's context figure as of the **last completed model
call** — its input only (`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`;
output is never occupancy) with an honest time, e.g. `context 348K / 1M (35%) · as of 15:29`.
It rides the first events page (read-only; no separate request), from the host-recorded
completed call when this generation has one and otherwise from a bounded tail scan of the
session's native transcript — never after the whole viewer index. A resumed generation
never borrows the call before its start: the exact cutoff is the record's launch stamp or
`launch.json`'s mtime (never the run directory's st_ctime), and an unknown cutoff says why.
The window is the CLI's own `modelUsage[contextWindow]` (a host-recorded `context_windows`
entry first); without one the measured tokens still show, without a fraction. Absent,
foreign-UUID, sidechain, unterminated and no-stop_reason records never answer, and only
the shared `scripts/claude_headless/context.py` report is used — no local arithmetic.
The window line also reports the viewer's source progress (`indexed X MB / Y MB`, decimal MB)
so a slow first load explains itself, and the footer's resident-row accounting is relabeled
**viewer memory** — bytes of mounted rows, explicitly not context.
Activating the readout needs a pane server stop/start (server-side Python) and a pane page
reload; the live Claude host needs no fresh launch, because a record without the new fields
falls back to the native transcript and the raw stream's result tail.
Native result receipts are small end-of-turn markers (outcome, available token
usage/permission denials and **list-price estimated cost, not billed Max/API
spend**), never a second copy of the final reply. Selected host views observe at
600ms cadence; stored history retains 3s, hidden views pause, and failures retain
the existing 3–15s backoff and no-overlap/read limits; indexing retries remain
neutral and capped at three seconds. A streaming `not-final-fragment` call is
labeled streaming; unknown cosmetic statuses/counts/disclosures are labeled
unknown rather than rejecting a whole page. Identity/epoch/schema and resource
boundaries still refuse. Resident events deduplicate by UUID plus scope even if
a later page changes their opaque record handle; native task observations also include their process generation,
so a resumed task notification cannot swallow an earlier row and strand all later messages behind a sequence gap.
Replayed prose UUIDs still deduplicate across generations; no text-only deduplication.
Only an explicitly **host-registered Manager** can Send or change native model/thinking/interrupt
from the editor. Selecting its live, sleeping or reconnectable ended row in the integrated viewer
targets that exact conversation automatically, replacing the previous editor target;
**Use as Send / Refine target** remains a fallback. Browsing never wakes it: only Send reconnects.
Validation runs alongside the history load;
the server checks role, exact UUID and current generation.
A **collected** (settled, closed on purpose) or proved-ended registered Manager keeps a
read-only archived authority for Refine and stays selectable as a Send target: the row reads
**sleeping (reconnects on Send)**, and Send reconnects that exact conversation first (one
idempotent host reconnect, no browser launch arguments) and then delivers one new journaled
Send to the reborn generation. Nothing is lost by the close; a resume restores the
conversation exactly.
Workers, Conductors and unmanaged history remain view-only, including direct hotkeys/API calls,
and **never fall through to OpenCode Send** or provider context. All OpenCode
Send/Refine/inspection behavior in this pane is unchanged.

Native Claude subagents appear nested under their parent session in the same list,
with a ▸/▾ toggle **only when children are known to exist**, never for the capability alone.
Visible capable rows discover their children once through the optional-detail queue; the
selected primary session expands and discovers new
children every three seconds (paused while hidden), each shown as its description
(bright) over its agent type (the `.meta.json` `agentType`, e.g. `manager-quick-high`) with the model and level its own
turns recorded (dim: `manager-quick · Opus 5.5 · high`; the type's level suffix is dropped only when it is exactly the
one level recorded; nothing recorded yet: the type alone; the full `Manager-Session-181 ▸ manager-quick · description` on hover);
expanding another session reads its children once.
Click one to follow its recorded messages, thinking summaries and tool calls at
600ms cadence. This is the contained JSONL reader, not another process or viewer;
native transcripts record completed message blocks, not every streaming token.
More subagents pages through every discovered child; a later page stays put until
Refresh subagents rather than being replaced by the automatic first-page watch. **More** sits
at the start of the parent's metadata line only when another page exists; **Refresh** appears
there after paging or a discovery failure (return to page one/resume polling, or retry).
Ordinary first-page polling needs neither a Refresh button nor a footer. Empty discovery
draws no “No subagents yet · view-only” line; a genuine failed discovery keeps its warning.
Child browsing cannot change the editor's existing Manager target, grant Send or
controls, or supply Refine context. Parent history/leaf/context and child tool
snapshots remain file-and-scope isolated, even when native tool IDs are reused.
The Manager view retains its Agent call; the subagent's own start/finish, its forwarded calls and
its background commands appear as that subagent's activity group (above), display-only, not parent
prose or context, and completion is not task acceptance. Native 2.1.299 writes parallel tool calls
as one record per call with each result parented on its own call, so a transcript has dead-end
"plumbing" side chains: they join the conversation path (they are not branches), so long
subagents no longer open empty. A growing transcript larger than one scan step re-verifies its
prefix over a few reads; meanwhile the viewer gets "nothing new yet" with the same cursor instead
of an identity refusal.

Claude Code's native serializer (read on 2.1.299; unchanged on 2.1.300) places children at
`<projects>/<project>/<parentUUID>/subagents/agent-<agentID>.jsonl`, carrying the
parent `sessionId`, child `agentId` and `isSidechain:true`. Optional adjacent
`agent-<agentID>.meta.json` supplies only `agentType`/`description` labels (bounded
16 KiB, contained/no symlinks); missing metadata falls back to the agent ID.
This layout and metadata contract were checked read-only in the installed CLI, not by
submitting a live job. Offline Python/Node/actual-markup browser fixtures
cover discovery, append, paging, labels, messages/thinking/tools and authority
isolation; next fresh Manager launch remains the production proof. Activate with
an idle pane-server stop/start and page reload; do not restart a running Claude
host for the viewer change, and save unsaved dictation before reloading.

The terminal cockpit remains removed—controller, tmux launch, Arm/Send/control,
qualification and generation-specific hooks. The headless host is separate;
Manager launch/resume remains an explicit operator action. Refine keeps its existing
independent provider/API billing, not the native Max subscription.

Transcripts of **any Claude Code version** are read by each record's own shape, never by a
list of releases, so an update never switches history, prose or Refine context off. Fixtures:
Windows `2.1.185`, the synthetic policy shape `synthetic-v1`, native `2.1.296` and headless
`2.1.299`; the installed `2.1.300` was checked on real transcripts. A record that departs from
the shape (an unknown block kind, an unknown record kind carrying conversation identity, a
missing or malformed `version` string) is refused alone: it shows its reason in the block's
meta line and the diagnostics, and stays out of prose/context. Exact independently sourced
fixture hashes and sanitization are in `tests/fixtures/claude/provenance.json`. Absent-origin
external SDK string inputs (headless Sends, seen on 2.1.299 and 2.1.300) require the observed
`promptSource: sdk`, `turnOrigin: sdk`, `entrypoint: sdk-cli`, `userType: external`
combination; unknown/mixed provenance and tool results are not user context. A pasted prompt stored as
`<pasted_content id=X>`…`</pasted_content id=X>` is unwrapped exactly
(`unwrap_paste`) when the prompt is exactly one wrapper; session bookkeeping
records with no conversation identity (`agent-name`, `atis-latch`, `cost-state`)
are ignored, while an unknown record carrying a uuid, parentUuid or message is
still refused. Claude Code 2.1.300's `api-request-blob` records (hash-addressed copies of each API
request, no uuid or parent) are counted in one diagnostics line per page, never listed one row each.
A message of yours is left out of Refine context only when it is a slash command (`/compact`,
`/model opus`); one that starts with a path (`/mnt/e/…`) or `!` is prose and stays.

`CLAUDE_HISTORY_ROOT` defaults to `/home/daniel347x/.claude/projects`;
`CLAUDE_WINDOWS_HISTORY_ROOT` is history-only. No browser request adds roots or
file paths. Handles are opaque (`storeRef`, `sessionRef`, `scopeRef`, `leafRef`,
`recordRef`, `callRef`, `targetRef`, `artifactRef`, plus `fileEpoch`); UUID/cwd/
title/source are labels, not routing handles. Known empty is zero;
missing/unavailable is unknown (`—`), never a fabricated zero.

### History page and API

`sessions.html`/`sessions.js` are same-origin, no-store, shared by the
embedded and standalone views. Browsing is read-only; stars only change the local pin store.
**Sidebar:** opening reads the bounded **host summary** inventory, not native transcripts.
The server's one universe keeps whole programs (both backends) and freestanding sessions with
their children: live/waiting, starred, active within the view window, or an unsealed program
not truly idle for `idle_unsealed_days` (90 by default). Managers never enter by that last rule.
Busy/waiting sessions and their launch ancestors lead the host pages;
the OpenCode inventory is read alongside it.
**Last 7 days · 30 days · Everything live** selects the live scope, desktop and phone Programs.
The same sidebar top bar has one quiet host caption while work is behind: **host catching up · N conversations**
for queued/running row sets, otherwise **host busy · lag X s** when the host's last-60-second maximum
one-second wake-up lag exceeds one second. It disappears on the next clear inventory read (the existing
10-second list cadence), on an unavailable first page, or in 📦 History. Unreadable sets do not keep the
catch-up count on forever; their per-conversation damaged-rows line stays. The first inventory page's
optional `hostLoad` forwards only `rows_behind_sets` and `loop_lag_ms_max_60s` from that read's existing
health reply; Everything live also makes this optional health read, while the fast live-activity lane
stays list-only. There is no new browser poll, control/history lock, setting, dialog or work gate.
An older host/server without load simply shows no caption. Host and pane activate independently:
a guarded host restart, then the guarded pane-server restart and a desktop/phone page reload.
The live-scope selector is page-local (no URL/storage preference); reloading returns to 7 days. A **☆/★** on every
row and program header keeps that whole unit in the live universe, without a confirmation,
limit or expiry; it is distinct from the selected Manager's placement at the top.
In the every-session live view, a **program header's ★** also puts it in **Favorite programs**,
the expandable section directly below **Running now**, above Manager Sessions and the other homes,
on desktop and phone Programs. It is one flat list: full programs first, then quick programs, each
in its home order, with every favorite shown (no Show more). The existing `/api/pins` program key
is the only favorite store; session, worker and helper stars keep their existing meaning and never
add favorites here. Every new program is starred automatically at birth: `oc.sh quick` stars its root
as soon as its AP-ID is assigned (fork, fresh or scouted, forklifted or not); a full program's first
`oc.sh launch --seeded` stars it when reserving or adopting row 001. The scripts write this same
`SessionPins` store directly, without a running Manager App. A failed write warns and the launch
continues; `--dry-run` only says it would star. Unstarring is Dan's alone: resumes, continuation legs,
promotions, re-launches and seals never re-star, and nothing automatically unstars a program.
A running favorite's full tree stays in Running now, with its yellow stub at
the top of Favorite programs; an idle favorite's full tree is in Favorite programs. Both leave a
yellow stub at home in their newest-created position (starred programs do not get the live-first
boost there); an idle stub says **in Favorite programs**. Home paging still applies to positional
stubs. Unstar returns the program to the ordinary home/Running now rule. Favorite programs remembers
its fold and uses the same active-only and global fold controls as the other home sections; the
role filter stays flattened and 📦 History stays unchanged. Activate with a desktop and `/m` page
reload only, no server restart.
An old pane server's `view` 400 retries the legacy list; its pins 404 simply hides stars.
**📦 History**, the selector's fourth entry, is the permanent log: every archived conversation (the daily sweep's
archive, below), whole programs newest first, drawn by the same tree, rows and glances. Nothing reads the archive
until it is picked, and while it is shown no live list, activity, glance, turns or telemetry read runs; each pick
reads page 1 again, *Show more* loads the next page of whole units until the whole archive is listed, and any other
entry (or a reload) returns to the live list. An archived conversation opens read-only in the same viewer (desktop)
or in Chat through Read › (phone): its prompts, answers and a one-line preview of each tool call (no thinking), its
older turns as Dan scrolls, a call's **↳ Open child conversation** opening the archived child; it is never a Send,
Refine, Inspect or context target. Before the first sweep the list says the archive is empty.
Activate with the Manager's guarded host restart (`program_activity` in health `implemented`),
`transcription-pane/start.sh restart` (view/pins routes and independent caches), then a desktop
and phone page reload. Before the host restart, a slow settings-window list supplies idle
program evidence and members; neither the list nor Send waits for a missing new verb.
**Stored native history** is collapsed above the list:
opening it (or its Load / refresh button) reads unmanaged transcripts once, retaining them through
later host refreshes. It never starts a repeating history walk. A refresh swaps the finished host
list at once; a refused read keeps the last list. Groups are **Manager Sessions** first among the home groups (true Managers only), then
**Full programs** and **Quick programs** (round 7, below), then **Quick workers and helpers · quick-worker**,
**manager-sessions folder · not Managers**, and one **Other** for all remaining non-program sessions,
without per-folder subsections. The role decides a home section (Manager → Manager Sessions; quick worker or
helper → Quick workers and helpers); the working folder only places a session with no such role (label-less
history in the quick-worker folder, the manager-sessions folder, else Other). Their larger blue-purple section headers keep the same expanders
and active-only controls (program headers and the rows inside are unchanged). These home sections stay in that order, each listing every
session of the group, newest created first (`createdAt`: the transcript's first record, else the generation start), 30 at a
time behind a **Show N more** at the bottom of that group. **Manager Sessions** alone is ordered by its names
(`static/manager-order.js`, shared with the phone's chips): by Manager-Session number, highest first,
Claude Code and OpenCode together, live or not (liveness never moves a row), each primary followed by its sub-sessions, `a` first
(`218`, `218-a`, `218-b`, `218-c`). The number is read from the label, else the title, anywhere in the name
(`Manager Session 157-a Setup` counts); a name without one goes to the bottom, newest first, as do two
rows with the same name. The name only orders: it never makes a session a Manager. A Claude Manager row carries a small **context gauge** where the green live dot was (`managerGauge`): while live, the status bar's pie in a green ring (a plain green dot until a share is known); once closed, a gray ring, filled gray with its last share when the list row already carries it (`lastContext`: the host record's last completed call of that generation over its window, computed with the list, never a transcript read).
In the every-session view, selecting any Manager pins its **whole numbered family** above the scrolling list:
the primary first, then `a`, `b`, `c` …, Claude and OpenCode together, every closed sibling included.
Desktop list refreshes read the existing unwindowed Manager summaries so a sibling outside the view's age window
is not lost; phone Programs uses `/m`'s already-read full Manager picker. With no chosen family (including a fresh
page load), the highest Manager-Session index's whole group is pinned; if no name is numbered, the newest Manager
is used. Native subagent expanders stay with their
rows; only the clicked conversation is selected, and drafts, stars and saved folds are unchanged. Selecting a
non-Manager conversation **keeps the last chosen Manager family at the top**, on desktop and phone.
Only choosing another Manager or one of its sub-sessions swaps that family; the previous group returns to its normal
home/Running now placement. This is list placement only, never a Send target change. Line 1 of every row starts with the session's real
**role word** (`displayRole`: Manager · Conductor · Lead seat · Quick worker · Helper · Worker ·
History), then its name on one line (ellipsis; the whole name, UUID, folder and role source on
hover); the host registry role, refined by the oc_* seat labels — `labels.quick` → quick worker,
`seat_role lead` (or, before it was stamped, a fresh `kind seat`) → lead seat, `kind`/role helper → helper; a host
session's native transcript takes its host row's role and is shown as **↳ its native transcript**
under that row; a transcript with no host record: quick-worker folder → quick worker,
manager-sessions folder + a Manager-Session label → Manager, otherwise History). A name that
mentions a Manager Session never makes a session a Manager; a worker or Conductor shows its seat
title (without the scripts' `[ap:CORE]-001 ▸ ⚡ quick worker ·` prefix) and, outside a program,
"for Manager-Session-NNN". A Claude row's status line ends with the models and thinking levels its own turns
recorded, e.g. `(Opus 5.5 · high)` or `(Opus 5.5, Fable 5.1 · medium, high)` (up to three models, else
`multiple models`; levels only when every turn recorded one), from `turnFacts`. Host inventory/activity
requests use `details=0`: only a row in the viewport asks `/api/claude/turns` for its own incremental
native-transcript ledger (`ClaudeReader.turn_ledger`, 32 MiB per request), shown only once read to its end.
Unseen rows' facts and aggregates that need them remain unknown rather than scanning hidden transcripts.
Every row (Claude rows, native subagents, OpenCode rows; desktop and phone Programs) also shows its first and
latest turn as a second parenthetical right of the models (an OpenCode row: right of its status; a subagent: its own
line), `(first 2026-10-08 21:14 ET · last 2026-10-18 23:52 ET)`, 24-hour Eastern time (DST-correct). It wraps
only between its two halves, so a narrow list moves it down rather than cutting the models or a time; an end not
known yet reads `—`, and nothing is shown while neither is known.
Hovering any row shows the same two times on two lines,
with `First: Thu Oct 15, 7:02 PM ET` on line 1 and `Last: Thu Oct 15, 9:48 PM ET` on line 2, before all the existing details (DST-correct; a past year is named). A turn is
any own user or assistant record (prompt, answer, tool call or result). Claude rows take them from the same ledger
(`turnFacts.firstAt`/`lastAt`, lazily read for visible host rows); OpenCode rows read `/api/opencode/turns`
when the row enters the viewport. Turn facts and telemetry share **two optional reads at most**, with active
work and newest activity first; a collapse or redraw drops queued hidden rows. Closed programs, offscreen and
unpaged rows read nothing. Times are kept while the row's updated/idle stamps stand (a busy row re-reads after 10 s, a failed
read after 30 s). Forks exclude copied base turns from these times and Claude's session-wide model/level facts;
their own launch message names the boundary (OpenCode also uses launch metadata and creation time).
An end not recorded, not read yet, or behind an unreadable record says `unknown`.
**Telemetry** (`oc.sh telemetry`) starts on its own line directly under that times line on every program-tree row and
every freestanding quick worker or helper, both backends, desktop and phone, in the same gray and size: **one line per
run** (job segment: the fresh start, then each follow-up message that resumed the session), oldest first, so the live
run is last; each line opens with its mark in one cell, `▶️` the first run, `↪️` a resumed one. An implementer run shows the chain
`50.3 (0) → 57.2 (0) → 427 (96) → 481 KT (14 min) ⚑2` (`oc_telemetry.pane_chain`; pane only): context in KT at the
first source read, first edit, first own-socket write after the last edit and end; column k always carries phase k's
minutes in brackets (loading, reading the code, making the changes, writing the report) and the last bracket carries
`min`. A live run's running phase is one parenthesized cell, its context and minutes so far, with a dash for the end
(`343 (0) → 376 (3) → (419 KT, 8 min) → —`: making changes for 8 minutes, at 419 KT, no report yet); a finished run's
moment that never happened is a dash with its stretch's minutes beside it (`62 (0) → — (11 min) → — → 186 KT`: read
code, never edited or reported); ⚑ review flags. A resumed run's moments
are its own, its first bracket the time from the follow-up's arrival to its first source read. **A row's lines line up
in columns:** its runs are one CSS grid (`teleSpan`: a column for the mark, each moment, each arrow and the ⚑, tabular
figures), each column as wide as its widest cell in whatever font the device uses. A row too narrow for its grid wraps
instead (`teleFit`, on every redraw and whenever the list's width changes; a list drawn while hidden, the phone's
Programs frame behind Chat, is fitted on its first frame on screen): each run's moments flow onto the lines they need,
between moments, never inside one and never dropping a bracket, every cell keeping its column's width, so the runs
still line up. Any other role (Conductor,
helper, recon, auditor, or a session whose records name no role) shows `context 35.6 → 522 KT · 126 min` per run, its
context at the run's first and last model response and its wall clock: phases are never guessed for a role the script
does not classify. Forks start at their own launch message (or original launch time): copied base history contributes
neither runs nor minutes. Their first context includes the inherited material already in memory; their growth and
phases describe only their own work. Missing fork provenance keeps the legacy calculation. **A GPT session**
(OpenCode, model `openai/…`/`…gpt…`, else a Sol/Astra agent name; `oc_telemetry.is_gpt`), whatever its role, has a
second line under each run: where that run's time went, `wall · wait 2.4m 7% · think 1.6m 5% · write 2.5m 8% · tools 1.4m 4% · helpers 24m 75%`
(minutes, shares of wall: first-output wait, thinking, writing tool calls, own tool execution, helper waits).
Helpers separate pure foreground seat waits and idle gaps tied by shell ID to a successful background seat-wait
completion; concurrent own work never becomes helper idle. Unmatched gaps stay `idle`; `idle`/`other` (inside-step
text, tails, unknown time) show from 5%. Mixed wait/work commands stay tools with a flag, not a guessed split.
Hover/tap adds shares of **active** time (wall minus helpers and idle, including inside-step other), the
blocking/background split and the exact CLI command. These are
the run's own `oc.sh telemetry --sid … --phases` totals (`oc_telemetry.pane_stream_phases`, the CLI's own selection over
the run's window; the hover/tap view adds the long form, step counts and the exact command). It spans from the moments'
column (`.tele-phases`), wrapping between its parts. A step still running is not extrapolated; a matched background
completion can add observed helper idle through its notification even after the last completed step, so a live
run's line grows with each telemetry refresh. **A Claude session** has the same second line, measured from the host's
retained stream records (`host.sqlite3`'s `events` and the `stdout.bin` byte ranges they name) over that run's own
window, through the same `pane_stream_phases` funnel: **wait** is each attempt's own first output (`message_start`
`ttft_ms`, retries kept apart), **think** is the native thinking window between a thinking block's open and close
(narration blocks excluded, so it is a span and not pure compute), **write** is observed `tool_use` block start to
input stop (a buffered burst can hide earlier generation, and is flagged rather than guessed), **tools** is
permission-reply dispatch to result, with a flagged arguments-complete fallback when no dispatch witness exists—not
tool-body-only. Helpers/idle/other and active time follow the GPT rules above. Where a clock is buffered, missing,
or the native thinking duration disagrees with its host-arrival span, the hover/tap view names it rather
than guessing, and a transcript-only run (no host recording) shows
`phases unavailable · no host stream recording` in place of its line. Fork generations count only their own events;
resume generations have individual phase lines even inside one job-context segment, each with its own CLI window.
On **both** backends (and OpenCode helper rows on any model), tools retain their total and add
`(tests …m; refresh …m; other …m)`: tests are blocking pure executed test-runner calls; refresh is blocking map
refresh wrappers/direct launches/job-status calls. Only executed argv count; quoted mentions do not.
Test/work mixtures stay wholly in other tools with `mixed_test_work`; refresh/work mixtures do too, with `mixed_refresh_work`.
Tests own concurrent test/other elapsed time (the hover discloses the overlap), so the sub-buckets sum to the tools
union, not CPU/process time (tests + refresh + other). Helper waits remain separate. Paired runs add
`tests in background …m concurrent` and `refreshes in background …m concurrent`, separate from blocking cost.
Explicit task joins/pure identified output polls move just their waited portion back into blocking tools; mixed
polls stay other with a flag. Tests use dispatch→completion notification (including red exits); daemon refreshes
use created→terminal updated UTC timestamps, not poll arrival or the wrapper's own timeout. These are elapsed,
not CPU, clocks. Hover discloses overlap with own steps/tools and unmatched runs; JSON includes per-run witnesses. Nothing is
extrapolated for unfinished/missing completions. Raw `&`/`nohup … &` without a runner finish witness stays flagged,
not timed from a later poll/log read or the enclosing shell's exit. Helper suites count on their own session.
Other models' non-helper sessions still show none. `so far` ends the live run's line alone (after the ⚑, in the running color), while the row is busy or waiting or the session was
active in the last 15 minutes. Hover (desktop) or a tap on it (phone) gives every run's labelled long form, the CLI's
own fields (`start-up 50.3 KT · edit@57.2 KT · … · 0/0/96/14 min · ⚑2`; a follow-up's `carried … · src@…`), with its ⚑
flags and notes. Each page's collapsed **list info · key to the telemetry** block (top of the list) holds a
plain-words key to all of this (`TELE_KEY` in `sessions.js`). Viewport rows read `GET /api/telemetry?program=CORE` (one per program with a visible row) or `?sid=…`
(one per freestanding tree, up to 40): again when the drawn rows' own stamps move, every 15 s while something in it is
busy or waiting, 5 s after `computing`, 30 s after a failure (the last numbers stay). The server
(`pane_telemetry.PaneTelemetry`) computes in-process with `oc_telemetry.pane_program`/`pane_session`, one worker
thread, local records only and no Workflowy call; it probes the program's sessions first (OpenCode rows/last
seq/last update, a Claude transcript's size) and reuses its last answer while none moved, else reclassifies the
implementer candidates (parsed messages from the tool's incremental disk cache) and re-reads another role's session
only if its own probe moved. A request waits up to 8 s, else answers `computing`. MCI (338 sessions): about 0.6 s the
first time, 0.15 s after a change, a few milliseconds unchanged. A telemetry rule change in `oc_telemetry.py`
reaches the pane at its next restart.

**History sweep.** One more background thread (`history_sweep.HistorySweep`, started by `server.main`) runs the
archiver: it waits `sweep_start_delay_minutes` after a pane start, then every `sweep_check_minutes` runs
`bash scripts/oc.sh history sweep` as a child at `nice 19` (`ionice -c3` when present) whenever the archive is absent
or its `meta.last_sweep_at` is older than `sweep_every_hours`; the child's output appends to
`dirname(archive_path)/history.log`, and no request thread waits on it.

The **Show** filter covers the whole list. `live`/`sleeping
(reconnects on Send)`/`ended` labels stay. A session is named by its chosen name (New Manager or a
sidebar rename), else the `Manager-Session-NNN` (or `-NNN-a`) token in its first human messages
(Managers and unknown-role history only; a display-only bounded head read of its transcript),
otherwise its recorded or seat title, otherwise a short UUID; the full UUID is on hover and behind a
small copy button. **✎ Rename** (on hover and on the selected row) turns the name into an input:
Enter or leaving it saves, Escape cancels, an empty name restores the automatic one; it writes the
same display-only `session-names.json` as New Manager. The role word and metadata share one left edge for Claude
and OpenCode rows, with the expander in a reserved gutter. The second line is one point larger and a little brighter
(phone Programs and glare follow). **✎ · ⧉ · 💬** sit side by side at the right, larger targets spanning the first two
text lines without adding a control-only third line; ✎ remains Claude-only, ⧉ and 💬 are on both backends.
A thin outline marks the entry (session or
subagent) whose conversation is on screen. The host list refreshes ten seconds after its preceding
scan finishes while visible, and after an observed launch/readiness transition. Claude running/waiting
marks refresh independently every three seconds through transcript-free `live=1` host-only summaries:
only nonterminal host records travel, not hundreds of ended sessions. Newly discovered live work is
added immediately; a disappeared live row becomes unknown until the host inventory confirms its end.
OpenCode activity retains its two-second lane; neither uses the optional-detail queue. A visible caption
says **Updating current activity…** until both sources have been checked, then **Current activity checked**.
A slow scan finishing later cannot overwrite newer activity. Failed, missing or older-than-15-second
observations say **activity unknown**, never idle or a still-current running mark. This is shared by the
desktop sidebar and phone Programs view. A **divider** between the list and the
conversation sets the list's width (drag it, or focus it and use Left/Right, Home/End; 16–70 % of
the page, default 34 %, remembered in this browser).

Fresh worker and seat launches (`oc_seat quick`, `dispatch`, and worker/lead forks) append **` · #2`,
` · #3`, … per socket** to the stored title; the first run stays unnumbered. OpenCode and Claude runs
share the series across Conductors. The launcher takes the highest stored suffix, counting legacy
unnumbered runs in creation order, from both all-time histories—not the sidebar's age-filtered rows.
It prefetches during existing launch preparation and never waits or retries for numbering: an unreadable
or still-pending history warns and launches unnumbered. Helpers and warm bases are unchanged. Existing
titles are not backfilled or renamed; same-session follow-ups and Claude process resumes keep their title.
Both the desktop sidebar and phone Programs show the stored suffix without new browser/server code;
no restart or page reload is needed for future launches. This is best-effort sequential-launch numbering,
not a reservation registry for concurrent forced launches or completely deleted history.

**Programs nested (round 7).** Every program session sits in one tree per program: the program row
(its newest Conductor's stored label; `[ap:CORE] · from
Manager-Session-NNN · N sessions`; a green dot while any member runs) → its Conductor sessions by
registry number (`-001`, `-002 · resumed` …) → the seats each one started (workers, lead seats, quick
workers) → their helpers. Edges come only from the immutable launch labels (`ClaudeFacade._lineage`:
program core, `conductor_n`/`registry_n`, `conductor_id`, a helper's `parent_seat`/`parent`), keyed on
(program core, Conductor number); the helper → lead edge is the only one by session id. The
`[ap:CORE]-NNN` title prefix is read only when no number was stamped; an older oc_quick worker with
no number (started before its Conductor) belongs to row 001. **A session whose parent is not listed
sits one level higher with a dim "started by …, not listed" note: never hidden, never under the
wrong parent.**

**Full program names:** program headers use the newest Conductor's stored session label directly, without
a Workflowy lookup, name cache or delayed enrichment. The launcher stores the full program name; the pane
strips its launch prefix, not its length. A different explicit Conductor name stays as chosen; older cut
labels stay cut until their stored titles are corrected. An unlisted Conductor keeps the existing orphan
session-label/core fallback. Desktop keeps the exact one-line layout: the name uses the available width,
busy/waiting beside it, existing widgets on the right, ellipsis only when space runs out. Phone Programs
wraps the whole name onto as many lines as needed. Activate with the guarded pane-server restart, then
reload desktop and `/m`; no host/OpenCode/daemon restart.

**Freestanding quick workers.** A quick worker a Manager starts outside any program (`oc_seat quick` without
`--program`), and its helpers, on either backend, sit in **Quick workers and helpers** as one forest built by
the same `lineageTree`: each helper under its lead by its immutable `parent_seat` (a Claude lead's OpenCode
helpers too), its subagents under it, the same never-hide rule (a helper whose lead is not listed stays at
section level with the dim note). Edges come from the stamped launch facts only: a Claude row's `lineage` without a
`program` (`labels.quick`, no program), an OpenCode row without `program` (metadata `quick`, `kind` seat or helper);
no title is parsed. Trees and the label-less history rows of that section are ordered together, newest created
first (a tree by its root); rows carry their backend flag and, having no program row above them, "for
Manager-Session-NNN". A busy or waiting tree moves to Running now whole, with a stub at home, like a program; the
Show filter flattens it with a `Quick workers ▸ …` breadcrumb; selecting a session opens its lead.

Every full/quick program root, in Running now or its home section, also has three fixed-width,
right-aligned cells: blue count blocks (1–3 / 4–7 / 8–15 / 16–31 / 32–63 / 64+) plus the same session
count, a yellow duration bar (square-root scale capped at four days, at least 2px of the 18px track
for a known duration) with a short label (`40m`, `9.3h`, `1.6d`), and age from the start (`today`,
`1d`–`6d`, then whole weeks; Eastern calendar days). The root name also fades linearly with this
first-turn age, from today's title colour to the dim gray `#7d8594` (`--program-name-faded`; the second line
itself is a step brighter, `#9ba3b1`) at the host's list window
(`health.list_window_days`, carried on the first inventory page as `listWindowDays`); it stays at
that readable floor beyond the window. A missing/invalid window or unknown start leaves the name's
original colour. The age label is unchanged. The dim second line ends with the program's first/latest
times in the session rows' parenthetical, `(first … ET · last … ET)`, wrapping the same way (desktop and
phone; nothing while neither is known), and the root hover starts with them on two lines, keeping its
old text below. These are recomputed
once per inventory from listed members: Claude's own `turnFacts.firstAt`/`lastAt` (creation stamp
estimates a missing first), and OpenCode's session `createdAt` to `max(updatedAt,idleAt)` as a turn-time
estimate, disclosed on the meters. Any member's unknown/unread endpoint keeps that program endpoint
and duration unknown (`—`), including a still-scanning Claude transcript; no partial prefix is presented
as a short run. Duration ends at the latest turn, never the seal or now, and a later follow-up does
not reset age or brighten the name. The pane reads health once per fresh host inventory, never per
program or continuation; an older/unavailable health reply disables fading without blocking rows.
No browser request, timer or cache is added. Activate with a pane-server restart and page reload.

Expanders per program, Conductor and seat are remembered in this browser
(`pane_lineage_open`, keyed by AP core and session id); a program is open while it runs and closed
once it has ended, unless Dan chose otherwise; selecting a session opens its ancestors. The **Show**
filter flattens the programs into one list with a one-line breadcrumb (`[ap:CORE] ▸ -001 ▸ Lead seat
…`) under each match; a program group's "Show more" counts programs (12 at a time). **Live programs
first** (round 8): within a home group unstarred live programs lead, then newest;
starred programs keep their chronological stub positions. **Running now** (round 9b) leads the whole list, with **Favorite programs** immediately after it:
every *active* root entry — full or quick program, Manager Session or other session — except the pinned Manager. A session is active while its turn is open (the spinner:
`primary_turn_open` or an unanswered Send) or while it is **waiting** (turn closed, a background task still
open in `background_tasks`: it is woken when one completes); a program is active when any session in it is,
so the whole program moves there. Each lifted or pinned entry leaves a dim, non-clickable stub in its home group,
under that group's normal paging; home counts still include it. Running now lists Managers first, then programs,
then other sessions. While the pointer is over the list, lifted entries stay there until it leaves; new activity
still appears. A program's header sticks at the top of the scrolling list while its tree is on screen.
The list holds the row being read through late sibling/descendant arrivals, telemetry growth and refreshes,
including inside a saved-expanded program or one reopened mid-load. Its explicit `listAnchor` prefers row
starts **below the sticky header**, not an earlier row partly hidden behind it: pinning that predecessor's
top did not prevent its growing bottom from pushing the reader away. A tapped row/head still stays under
the finger. The anchor is captured before a redraw's layout changes and also around asynchronous turn-time
updates; it does not rely on CSS scroll anchoring. Whole-pixel positions and carried rounding slack prevent
WebKit creep. Every active program carries one complete tan rectangle, including its own root row when
collapsed or expanded; it paints above the sticky header, so an old dim name still visibly runs
(the row outlines when the Show filter flattens the tree are unchanged). Waiting Claude rows, Managers included, show a small **waiting** flag:
hover for each background task's shared compact command preview and “waiting, not blocked”. Recorded
`oc_seat.sh wait` / `oc.sh seat wait` commands show **🔔 Doorbell** (target name, role, program and socket;
“not blocking: wakes when that session stops”); `oc_map_refresh.sh` shows **🗺️ Map refresh** (folder's first
eight characters and observed start time). Mixed waits keep **waiting**; missing commands or provenance
say not recorded / not listed. Commands begin with tasks started after the host's guarded activation restart.
**OpenCode foreground waits:** every two seconds, `/api/opencode/activity` reads busy/idle and running
tools independently of the slower inventory. A running `oc_seat.sh wait SID` / `oc.sh seat wait SID`
(quoted paths/IDs included; OpenCode or Claude target) shows **🔔 Waiting on helper** beside the busy
spinner, with each helper's name, role, program and socket on hover and a read-only conversation link
below the row. This is a **blocking foreground wait**, not the nonblocking Claude background **Doorbell**.
Map-refresh commands have their own flag. Failed activity reads or observations older than 15 seconds
say **activity unknown**, never idle or an indefinitely spinning old fact. Refresh asks for this fast
activity read immediately (and repeats a click made during an in-flight read); it need not wait for the
full inventory refresh. A known-idle OpenCode parent with active children is
flagged waiting as a best guess, and its tooltip says so. A collapsed program or session shows a small dim busy/waiting count for activity hidden beneath it, including a Claude row's collapsed native-subagent list. **⊟** beside the Show filter is the phone's two-state Collapse / Show running control, its state read from what is drawn open: while any program row or freestanding tree is open, a click closes those top-level rows (inner open states kept) and every home section, keeping **Running now** open; with nothing open, a click opens only what is busy or waiting (each program's fold-to-active rule) and only the sections that draw it. Its tooltip says which comes next. **⊞** still opens programs, tree nodes and quiet-Conductor folds without reopening sections (choices remembered like the expanders). Each program's dim ⊟ / ⊞ folds to the minimal subtree showing everything busy or waiting, or opens everything; quiet runs of two or more numbered Conductors become a thin clickable `Conductors -001 – -008` line, except selected or pointer-held rows, and an open fold keeps its ribbon available to close immediately on click even under the pointer. Clicking anywhere on a section header collapses or expands the group; its independent **active only** control (except in Running now) keeps only entries with busy or waiting work, opened minimally on first appearance and then kept until turned off; its page-only expanders leave saved choices untouched, while section choices survive reloads. **OpenCode
sessions** of a program (Conductors, seats, helpers, and the subagent children under them) join the
same tree, and freestanding ones the quick-worker forest, with an **OpenCode** flag (Claude rows in a tree say **Claude Code**), a busy spinner,
outcome, model and cost. On the desktop, clicking one opens its **whole conversation** in the same
viewer as a Claude session, at its newest rows, Following: prompts, reasoning, each tool call with its
preview and In/Out sizes, prose and end-of-turn receipts (outcome, the turn's cost, the last step's
tokens including reasoning); empty or encrypted reasoning is one quiet line, and unusual rows
(synthetic, system, skill, shell, an unknown type) are labelled, never hidden. Text and reasoning
follow per stored block (no token streaming) and tool states live, about every second while busy and
every 3 s idle. **Running tools** above the history on desktop and phone lists every current open call,
even an earlier parallel call above the reading window while the last sibling has finished. Each has its
preview and a live `12s so far` timer. Tool rows also tick in place once a second; completion freezes their
stored duration. The strip and busy banner also read the fast activity snapshot independently, so a slow
or failed history-index read cannot hide current tools on either page. Failed/stale/idle activity evidence
stops the running claim. This is a display clock, not telemetry.
⤒ First reads its first row and every row in between pages in. It is view only: never a
Send, Refine or control target, and a tool click uses the shared inspector. A `subagent` call's **Open
child conversation** link opens that child the same way, listed or not. The conversation's header
shows OpenCode, its title (else the bare id), the `ses_…` id with copy and **Open in CodeNomad**. The
row's **💬** chip alone opens a **glance** under the row — busy/idle, outcome, model,
cost, its last five text messages (each clipped), then its **tool call**, and its `ses_…` id with a copy button — and never
selects, browses or targets anything in the conversation view. On the phone's Programs page a row tap
is still the glance and no conversation opens there. **Claude rows have the same glance** (their 💬 sits beside
the row's ✎ and ⧉; on the phone a Claude row with nothing under it also opens it with a tap on the row): the same five newest text
messages by the same rule (`opencode_programs.glance_messages`, over what the conversation view calls a record's prose:
human prompts and assistant text, never tool results, reminders, meta or subagent records), the row's busy/idle,
model and newest record, its UUID with copy, and on the phone **Read conversation ›**. The server reads only the
transcript's **tail** (`claude_glance.ClaudeGlance`, `/api/claude/glance`): the last MiB first, earlier MiBs only while
too few messages were found (at most 8 MiB and 1.5 s), then only what the file grew by (its identity, offset and a
witness of the bytes before it); never the history reader's lock and never the inventory's history walk, so a glance
cannot slow the list. An OpenCode glance shows **all current running calls**, using the fast activity
observation when available; once none are open it shows the latest call. The **tool call** on Claude is its running call if one is running, else its
latest (the newest step's call still without a result, else its last call): its name, the conversation tool row's
own compact preview (`opcToolPreview`) and its time as the row says it (`opcToolReading.callDuration`): the total for
a finished call, `… so far` for one running now (no result yet while the session is busy), `no result yet` when the
session is not busy. A glance is read when it opens and again every 5 s while it stays open (at most eight open
glances, one read at a time, the list redrawn only when what a glance shows changed); a closed glance is never read,
and no glance is ever part of the inventory, activity or telemetry reads. The glance's (and the header's) **Open in CodeNomad** (round 8) shows that session in the CodeNomad page
beside the pane: the pane server holds one request (`codenomad_focus.FocusRequests`), and our Chrome
extension (`session-focus.js`, 0.1.13) on the page whose workspace tab holds the session's folder
claims it, clicks that tab and the session's row, and reports back. The button reads pending →
"✓ shown in CodeNomad" or "✗ ‹what was missing›". With no tab for that folder it says "Open the
CodeNomad tab for ‹folder›", and the request waits up to a minute, completing by itself once the tab
is open. With no CodeNomad page answering, it says so. OpenCode uses the same whole-unit universe as Claude;
Everything live lists every nonparked session without an age or row cap. OpenCode is read by the pane server (below), beside the
Claude list and never in front of it: OpenCode down or slow leaves the Claude rows as they are,
with an "OpenCode unavailable" line in the program sections and Quick workers and helpers (and earlier OpenCode rows marked "last known").
An ended Manager identifies and links the newest ready Manager in the same workspace, and a
sleeping one says nothing is lost: Send reconnects it. Selecting a live or initializing Manager targets only the integrated panel's actual parent;
history/subagent browsing and Inspect never retarget it. **Use as Send / Refine target** addresses only the integrated panel's
actual same-origin parent editor: hello → parent presence → metadata target →
validation with an explicit applied/rejected ACK. BroadcastChannel discovers
other pane tabs but never carries target authority; multiple answers produce
**another pane tab is open; target applied here only** only when another tab actually answers.
Otherwise the plain confirmation is **Ready: sending to …**. A standalone `/sessions`
view explicitly refuses targeting. Absent parent says not applied; timeout
withholds an applied claim. Manual target actions do not retry automatically; automatic live-row
selection retries only validation of that same deliberate choice, neutrally, never Send/control.
While connecting, the old destination cannot receive a hotkey Send. Paging: complete host rows open at
the tail; native history and the replay fallback start chronologically (later pages continue their cursor, Jump to latest
exhausts later pages, earlier pages reload a checkpoint/prepend while preserving an
event/pixel anchor, and the start is always page one); a unique append while scrolled up retains the viewport. A
unique append may update earlier tool status/counts through reader-owned
`summaryUpdates` metadata only — no body/snapshot fetch to update a bar.

Viewport limits: 200 whole events / 16 MiB view accounting, 256 page checkpoints,
pages ≤8 MiB/100 events, 12 s read deadline, no overlapping reads, 3–15 s transient backoff.
When host health advertises `raw_frames`, page bodies, changed/late-label rows, child activity,
Refine context and selected tool snapshots prefetch whole raw frames in byte-bounded batches
(≤4 MiB per host call, with continuations until every requested frame is considered). The same
renderer/parser and exact raw hash witnesses still read every body; nothing is cropped and whole
Refine context is unchanged. Bytes live only for this reader operation. An older host is never sent
the verb; a refusal, skipped large frame or incomplete hint uses the existing paged per-frame `raw`
path, so the pane works before the host's next guarded restart too.
A read deadline returns 502 `read-failure` (*pane host read deadline; native execution untouched*):
it publishes nothing, discards no stored row set and never starts replay from event zero. Last-good
history stays on screen with the quiet catching-up line; the next poll tries stored rows again.
Only a genuine rows availability/integrity failure keeps the one replay retry with a renewed
observer budget. Neither path changes native execution or Refine's frozen-prose checks.
History has its own lane, separate from Manager Send, state, selection and controls. Each operation
owns its observer deadline and row/frame staging; the host transport receives a per-call budget,
never shared mutable deadline state. Shared handle/index maps use only short sections, never IPC.
Inventory and target metadata hold neither lane across host calls. Identical in-flight events pages
share one host read sequence, with a separate answer for each caller (no answer cache or expiry).
A queued viewer whose client has disconnected is skipped before its history work starts; no native
Send or control is ever cancelled to retire a viewer.
The sidebar has no inventory page or row cap; its existing 12-program/30-entry DOM pages bound what is drawn.
Whole far-edge events are evicted; earlier history stays reachable by checkpoint
reload or an explicit forward traversal. Branch/FileEpoch/stale/unsupported
boundaries are visible and require Reload/reselect, never spliced offsets.

Endpoints (fixed listener-port loopback/same-origin only; strict query/method
handling; no cross-origin OPTIONS grant; no-store):

- `GET /api/claude/sessions` — `cursor`, `storeRef`, `workspace`, optional `native=0` (host only),
  `details=0` (no host transcript ledger), `live=1` (nonterminal host records only, before paging),
  `view=week|month|all` (default `week`; settings windows 7/30 days, or no rule).
  `role=Manager` and `live=1` ignore the view. Cursors bind it; `hasMore`/`nextCursor`
  reaches every member with no first-N cutoff. Week/month read the host window separately
  from exact-id/program selectors, never `all`. `listWindowDays` is the view's days or null.
  Rows carry display-only
  `displayRole`/`roleSource`, `managerSession`, `hostTwin` and a chosen name's `nameSource`.
  Program rows also carry `lineage` (program core, kind, `conductorN`, `conductorId`, `parentSeat`,
  `seatRole`, quick/fresh/quickProgram, `entryAction`, `managerSession`, label title); a freestanding quick
  worker or helper carries the same `lineage` without `program`. Rows here and from
  `/api/claude/subagents` carry display-only `turnFacts` (`status`, `records`, first-use `models`/`levels`,
  `modelsComplete`, `levelsComplete`; `firstAt`/`lastAt` epoch seconds of its own first and last turn
  record, `lastAt` only once read to its end; absent when unknown).
- `GET /api/claude/turns` — one canonical `uuid`, optional `cwd` lookup hint (same contained
  transcript roots as the glance); the visible host row's `turnFacts`, or null when none is recorded.
  This cosmetic read never confers conversation, Send or control authority.
- `GET /api/opencode/programs` — only optional `view=week|month|all`; **no query means all**
  for the phone's Manager chip picker. OpenCode's session list and busy map read server-side
  (Basic auth stays in the server), cached 8 s, returned as tree rows (`opencode_programs.program_rows`):
  whole program sessions with their `program`, freestanding sessions and children without one,
  and display-only `socket` where stamped. No age filter or row cap remains in `program_rows`;
  the unpaged inventory response has no conversation-page byte ceiling either.
  Incremental reads stop at a page containing an unchanged `time.updated`; a full uncapped
  enumeration every `universe_idle_scan_minutes` (10 by default) catches deletions and old members.
  Universe activity is `max(time.updated, time.idle)` in ms; existing display-row clocks stay seconds.
  Past 8 s the last answer is served at once while one background
  refresh (up to 120 s) runs; `observedAt` stays the original busy-read timestamp, never the cache's
  delivery time, so stale running state cannot look current. Only the very first read waits
  (10 s): it publishes the first source page and catches up old members in the background.
  Neither backend waits on the other's cold read. OpenCode down answers `status:"unavailable"` with the reason.
- `GET /api/opencode/activity` — no query; independent fast `active` session IDs, source `observedAt`
  (epoch seconds), and each active session's `runningCalls` plus `toolStatus`. One-second cache, single
  refresh in flight; cached replies disclose `refreshing`/`stale`, never reset the observation clock.
  An active-map failure is unavailable, not an empty/idle map; a tool-detail failure keeps busy known
  and labels the details unavailable. Details read newest message pages, paging past notifications to
  the latest assistant/idle boundary, eight reads in parallel within one nine-second observer deadline.
  Full shell commands reach the wait recogniser (not cropped preview strings); no tool output does.
  This read never waits for the full inventory. It neither controls a session nor changes telemetry.
- `GET /api/opencode/glance` — exactly one `id=ses_…`; one session's status, outcome, model, cost,
  its last five text messages (text parts only, 1200 characters each) and `call` (its running or latest
  tool call: `name`, `status`, `previewData`, `time`, `resultTime`, `open`, as its conversation row has them;
  `null` when none), plus `runningCalls`/`toolStatus`, all from one page of its newest 24 messages; never a write. Rows and
  glances carry the session's `directory` (OpenCode's `location.directory`).
- `GET /api/claude/glance` — exactly one `uuid` (a Claude session UUID) and optionally one absolute `cwd`
  (only a hint for finding the transcript); the same glance for a Claude session: `messages`, `call`,
  `model`, `lastAt` (its newest record), `partial` (fewer messages than a glance shows, and the read did not
  reach the transcript's start) and `read` (mode `tail` / `growth` / `unchanged`, bytes, ms), from the
  transcript's tail only (bounded, incremental, display only); no transcript yet answers `status:"unavailable"`.
- `GET /api/opencode/turns` — exactly one `id=ses_…`; the session's `firstAt` (its oldest own user/assistant/
  synthetic message's `created`) and `lastAt` (its newest such message's latest stamp), idle and switch markers
  skipped. A fork's own launch message starts its span; otherwise a labelled fork starts at its first turn at/after
  creation (creation itself while only the copied prefix is on the oldest page). A copied-only tail has no own
  `lastAt` yet; missing fork provenance keeps the legacy times. `null` when unknown; cached 8 s;
  OpenCode down answers `status:"unavailable"`.
- `GET /api/opencode/events` — `id=ses_…`, optional `fileEpoch`, and either a server `cursor` (needs the
  epoch) or `fromSeq` (a position; `0` is the first row); neither reads the newest ≤100 rows. Schema-1
  pages of whole rows (≤100 events, 8 MiB) with `busy`, `observedAt`, `runningCalls` and `sessionInfo`; the
  current-tool summary is independent of the requested history window. A larger page or session is a
  visible 413 (`response-limit` / `session-limit`), an oversized row a placeholder at its own position.
  The epoch is index-local: after a pane-server restart or cache eviction an old epoch answers 409
  `stale-epoch` and the viewer re-reads the same `ses_…` by position (rows that no longer match reload that
  conversation's tail with a notice); 502 `read-failure` retries with the usual backoff.
- `POST /api/opencode/inspect` — same-origin JSON naming exactly one viewer call (`id`, `messageId`,
  `partIndex`, `callRef`, `fileEpoch`, selector, sequence); the server checks that part against its own read
  and admits it to the shared `/api/tool-selection` mailbox (below); never Send or control.
- `POST /api/focus-request` — same-origin JSON only: `sessionId` (`ses_…`), absolute `directory`,
  optional `title`/`parentId`, a fresh `nonce`; replaces any earlier request (`superseded`).
  `GET /api/focus-request` returns it (`pending` → `claimed` → `done`/`failed`; `expired` after 60 s
  unclaimed or 25 s claimed but silent; `missingTab`, `noPage`). The CodeNomad page polls the same GET
  with `?watcherId=` (exact CodeNomad origin; CORS to that origin only) and posts text/plain
  `/api/focus-request/claim` (nonce + watcherId compare-and-claim; a hidden page waits 1.5 s for a
  visible one) and `/api/focus-request/report` (`done`/`failed` from the claimer only; `no-tab` from any
  page, without claiming). Memory only, 8 KiB bodies, no preflight; no prompt, model or OpenCode call.
- `POST /api/claude/rename` — exactly `schemaVersion` 1, `backend:"claude"`, canonical `uuid`,
  `name` (≤80 printable characters; empty forgets the chosen name); same-origin JSON; writes the
  display-only name store, never identity, Send or a process.
- `GET /api/pins` — `{schemaVersion:1,pins:{key:{pinned_at:<epoch seconds>}}}` from the one
  `SessionPins` store (settings `pins_path`); a missing file means empty. `POST /api/pins` takes
  exactly `{schemaVersion:1,key,pinned:<Boolean>}`, same-origin JSON under rename's origin rules,
  and returns the same envelope. Keys are `program:CORE` or `session:claude:cc-UUID`, its
  `/agent-ID` subagent, or `session:opencode:ses_…`. A malformed key is 400. No cap or expiry.
- `GET /api/pane-quiet` (loopback, no query) — `{quiet, busy}`: mutation requests in flight here (Send,
  control, reconnect, launch, resolve, OpenCode prompt) and the page's leases. `POST /api/pane-activity` —
  same-origin JSON `{schemaVersion:1, id, kind, active}`: the editor holds a lease (180 s, renewed) while a
  Send, Refine, control, launch or incident resolve runs, so `start.sh restart` waits for it. Memory only.
- `GET /api/claude/subagents` — required opaque primary/child `sessionRef`, optional
  `cursor`; parent-scoped discovery independent of the main inventory's cursors.
  Eight child summaries/index refreshes and 1000 directory entries per page, up to
  eight resident directory continuations; stale/replayed cursors require Refresh.
  Browser paths, roots, titles and process control are never accepted.
- `GET /api/claude/events` — `sessionRef`+`scopeRef` plus optional `leafRef`,
  `fileEpoch`, `cursor` or `fromSeq` (a first page starting at that row position with fresh cursors; never
  with `cursor`, `leafRef` or `targetRef`; past the end an empty page), or `targetRef`+optional cursor (never mixed with session/scope); every event
  carries `seq`, its position in the scope's rows (host) or on the selected path (native), and every assistant
  event the display-only `model` and `effort` recorded for that message (null when not recorded);
  host target reads may additionally pin `leafRef`+`fileEpoch` for frozen Refine context; explicit
  leaves, separate trace labels, chronological supported prose and bounded call
  summaries, with server/reader-owned `targetInfo`. Host pages also carry Boolean `rowsUnreadable`:
  only an explicit host `rows` status of `unreadable` sets it, and the existing raw fallback still reads
  the conversation; desktop and phone show its one quiet damaged-rows line beside any catching-up note.
- `GET /api/claude/target` — a host target's **authority without its history**: `targetRef`, or
  `sessionRef`+`scopeRef` (main only; the answer mints the live or archived target ref the fresh state
  proves). One exact host `state` read, never a page of rows: `status:"authority"` and a `targetInfo`
  with the same identity, role, capabilities, lifecycle and refs as a history read's, its `leafRef`,
  `fileEpoch`, `leafUuid` and `contextIdentity` null. A handle the host facade does not own answers 409
  `history-target`, and the editor validates that native target through `GET /api/claude/events` as before.
- `GET /api/claude/context` — `targetRef` plus required integer `targetRevision`,
  optional `historyMode=whole|budgeted` and `budgetBytes`. Refine explicitly requests
  `whole` by default: every eligible main-path prose turn, without a byte crop.
  Omitting `historyMode` retains the legacy `budgeted` API behaviour (5–400 KiB,
  default 50 KiB, newest-first selection with a UTF-8-safe oldest tail). Both modes
  preserve the same prose exclusions and return `historyMode` plus `historyBoundaries`:
  stable message IDs and cumulative UTF-16 end offsets in `text`, excluding the following separator.
- `GET /api/claude/artifact` — `artifactRef` plus originating `callRef`,
  `fileEpoch`; contained explicit saved view, never replacing recorded-stub
  Copy/count.
- `POST /api/claude/inspect` — exactly `schemaVersion` 1, `backend:"claude"`,
  `callRef`, `fileEpoch`, `selectorId`, positive `sequence`, `observedAt`; bounded
  same-origin JSON (32 KiB, no CORS), validated against the reader's original
  page/path/record/block/call/hash witness — never browser bodies or fake OpenCode
  IDs.

`/api/tool-selection` and `/api/tool-record` dispatch by source; the OpenCode
legacy schema/wire witnesses stay separate and intact. Native raw strings/counts
come only from the reader; display options never reserialize them.

Host pages use these same endpoints and opaque handles; labels are not routing
authority. Inventory/target metadata adds `source:"host"`, `role`, `executionId`
(`cc-<native-uuid>`), integer `generation`, role-bound `capabilities` (including
`reconnect`: a registered Manager whose Send may wake the conversation), `lifecycle`
(`live` / `sleeping` / `ended` / `pending`), and
`liveState` observations (`close_reason` is the host's durable reason, e.g. `collected`;
the Manager's **own turn** — `native_session_state` `running`/`requires_action` while
`primary_turn_open` is not false, an unknown required state, `native_queued_turns` or a Manager
(not subagent, `agent_id`) permission request — visibly blocks an ordinary Send just as the host's
gate does, with a plain-words reason. Native state stays `running` after the primary `result` while
background agents/tasks work; the host's `primary_turn_open` (primary `system/init` opens a turn, its
`result` closes it) tells them apart, so a background subagent never blocks Send).
Pages add resident-only `eventUpdates` for stable
partial→final replacements alongside legacy `summaryUpdates`, and bounded
incident evidence. Reconnect cursors are opaque server witnesses to the durable
host cursor; gap, generation or epoch boundaries refuse rather than splice.
The selected host call alone may refresh until explicit settlement; unmanaged
history still stops automatic inspection reads. Neither missing output nor a
successful result/exit proves task acceptance or an idle process.

History/live overlap is conservative: a complete supported native main path is
hidden as a mirror only when **every** conversation record matches a durable
UUID/scope/kind/exact lexical-content witness and a freshly verified prefix within
the reader's scan budget. Larger prefixes or unproved overlap remain explicitly
labeled **Native history boundary**, not another live target. Native-only prior
history is not guessed into the host's Refine context; select that history source
deliberately if needed. No text-only or provider-message-ID-only deduplication.

### 📦 History archive API (`/api/history/*`)

`history_archive.py` (`HistoryArchive`) reads the session archive (MasterSpec §3; written only by
`scripts/oc_history.py`) behind `server.py`'s `_history_route`: GET only (405), the same origin guard as
`/api/claude/*` (so the Tailscale Serve policy covers it), no cross-origin preflight (403). The archive path comes
from `oc_history_settings.load().archive_path` at every request; the file is opened read-only (`mode=ro`, one
connection behind one lock, one read transaction per answer) by the first `/api/history/*` request and never before.
An absent file is a 200 with `status: "empty"` and empty lists, checked again by the next request; a file that cannot
be read or lacks §3.3's tables is 503 `unavailable`. No live route ever reads it. Every answer carries
`schemaVersion: 1, backend: "history"`; times are epoch seconds (the archive stores milliseconds). Each parameter at
most once, non-empty, ≤ 4096 characters; an unknown or malformed one is 400 `bad-request`.

- **`programs?before=&limit=`** (`limit` 1–100, default 24): `{units, claude, opencode, hasMore, nextBefore, total,
  archive: {lastSweepAt}}`. Units newest first by `last_at`, ties by unit key descending, whole: every drawn member of
  each unit is in `claude` or `opencode`. A unit holding only parked warm forks is skipped and the page still fills.
  Each unit is `{unit, kind, program, firstAt, lastAt, sessions, sealedAt, costUsd, telemetry: null}`: its member rows
  carry their own telemetry, so the page never carries a second copy. `nextBefore` is an opaque keyset cursor (`[A-Za-z0-9_-]`); a page followed by more is full. `total` counts the rows
  of the `units` and `sessions` tables (warm forks and warm-only units included).
- **`sessions?unit=` or `?keys=k1,…`** (exactly one; at most 80 distinct keys): `{claude, opencode, missing}`. A key
  read alone keeps the attribution its unit gives it. `missing` lists keys (or the unit) the archive lacks; a parked
  warm fork has no row and is not missing.
- **`glance?key=`**: `/api/claude/glance`'s shape, `{key, messages, call, lastAt, model, partial: false}`: the newest
  five turns with text (the live glance rule, 1,200 characters each), the last tool of the newest assistant turn that
  made calls (`ok → stored`, `error → error`, `unknown → pending-or-missing`). An unknown key is 404 `missing`.
- **`events?key=[&fileEpoch=][&fromSeq=|&cursor=]`**: a schema-1 page shaped like `/api/opencode/events`
  (`source: "history"`, `deltaCursor: null`, `busy: false`, `sessionInfo`), one row per turn, dense `seq`, at most 100
  rows and 8 MiB (a turn too large is 413 `response-limit`, never cropped). No position = the newest page
  (`hasMore: false`); `fromSeq` reads forward from that row; `cursor` (needs `fileEpoch`, excludes `fromSeq`)
  continues forward from where the previous page ended. A `fileEpoch` other than the body's is 409 `stale-epoch`.
  Rows are read-only: `thinking: []`, no Inspect; each call carries `previewData` built from the archived
  120-character preview (a cut preview shows its whole members and the cut string ending in `…`) and `childKey`, the
  archived key of the conversation it started.

Member rows keep the live shapes, so the sidebar's renderers draw them unchanged: a host session through
`host_row_fields` (`/api/claude/sessions`' row), an unmanaged transcript as a native row, a subagent as a
native-subagent row, an OpenCode session through `opencode_row` with `busy: false`. Every row adds History's fixed
values: `storeRef: "history"`, `sessionRef: "h_" + b64u(key)`, `scopes: [{label: "main", scopeRef: "h_" +
b64u(key + ":main")}]`, `fileEpoch: "h_" + body_sha256[:16]`, `targetRef`, `liveState`, `lastContext` and `successor`
null, `lifecycle: "ended"`, history-only `capabilities` (`subagents` when archived children exist), and
`archived: {key, archivedAt, prunedAt}` and `telemetry` (stored at archive time). `sessionLabel`/`nameSource` come
from the stored chosen name, `turnFacts` from `turn_facts_json`. `programs` and `sessions` never decode a
conversation body; `glance` and `events` parse only the turns they show.

### Reader and index limits

Read record 16 MiB; combined selected input/output and prose/page bodies 8 MiB;
pages ≤100 whole events; scan 1 MiB/1000 lines per refresh (one whole bounded
record may cross scan bytes); per-file 100,000 records; aggregate 256 MiB
summary-accounting units across ≤32 resident indexes; 50,000 opaque handles;
native inventory 4096 summaries with ≤8 newly discovered files/index refreshes per page;
discovery 1000 directory entries per call; one page performs ≤8 bounded scan
operations. Every fresh native inventory read walks the folders as its own scan (up to 8 at once), so two
readers (a second tab, the sidebar refresh beside a Reconnect) never reset each other's cursor. The host inventory has no record cap: it reads the host's summary `list` once per
refresh and pages it 64 rows at a time. Complete host rows read the addressed page plus its activity window and metadata, looking up each missing activity anchor at most once per read. A host
conversation's **replay fallback** drains forward from event zero without an event, row-count or
per-conversation summary-byte refusal. Its existing two-second budget and continuations stay;
the 32-index LRU and aggregate summary-byte eviction bound total pane memory, not one conversation.
Refine waits within its existing observer budget for a complete fallback index, refusing with
`indexing` if it is still catching up. Native transcripts retain their own per-file limits and
visible later-history warning. Activation and rollback: `docs/claude-headless-host.md` §Verification and activation.
Detected rewrite/append validates old-prefix witnesses (including same-length
rewrites) and each referenced record hash at open; index eviction invalidates
epoch refs; oversize lines drain to their newline in bounded continuations; an
incomplete final UTF-8/JSONL record waits for newline. Bodies are released after
indexing and reread only for returned prose or deliberately selected bodies.

Shared previews: `/tool-preview-core.js` serves the packaged extension's pure
preview module — the single crop/short-first renderer; the reader supplies
`calls[].previewData` descriptors (ordered values, literal numeric tokens, original
string code-point/JSON-rendered lengths, first/last-128-code-point transport edges)
and never sorts or renders. Descriptor ceilings: 64 KiB per call, 2048 value
nodes, depth 32, keys ≤4096 UTF-8 bytes, numeric lexemes ≤128 chars; missing or
over-bound descriptors are labeled unavailable, never arbitrary first-N
substitutes.

An ended host Manager has separate **archived Refine authority**, never Send/control authority.
Its fully witnessed main prose can still be read and revalidated; an incomplete index still refuses
context. A replay diagnostic (an unknown block, an overlapping stream, a skipped or oversized frame)
leaves out only the rows its frame touched, listed in the context's `exclusions` and counted on the
context line ("N left out (replay diagnostic)"); it never refuses the conversation, and never
removes an otherwise writable session's Send target. Inspector, subagent and context-readout notices are independent observations, not Send gates.
A collected/ended registered Manager additionally carries **reconnect authority**: the row stays
selectable as a Send target and Send reconnects the conversation (see below). Ownership-unknown
records carry neither, and no reconnect is offered while the old owner is unproved.

### History Refine

Select a validated main history target (the backend/target pill is routing
authority; saved targets are revalidated on reload and never trusted). ✨ Refine
(Alt+R) uses the reader's genuine human/assistant prose with your existing
independent provider/API-key/model/prompts — never tools or thinking text; native Claude
targets never borrow OpenCode records. Backend, full target/main path, FileEpoch, target/draft/page
revisions and selection are frozen before awaits and revalidated before the
provider call, after inference and after clipboard completion; a file replacement
or edit/restore/retarget-away-and-back cancels application even when the final
bytes match. **Refine without conversation context** is an explicit, reload-stable
choice (`pane_claude_no_context_v1`, default OFF); a failed required read never
switches it ON. Explicit no-context Refine does not read or revalidate any conversation target.
Refine context defaults to **Whole conversation**. The desktop settings' **Refine context** selector
can switch to **Budgeted history**, which retains the saved 5–400 KiB budget (default 50 KiB;
outside values are refused, never clamped). Whole mode does not consult that budget.
The native conversation cache row is `—`, not OpenCode tokens; Refine's own per-call cache
result appears with its cost instead. Refine/provider settings and OpenCode routing are independent. Historical/worker targets
stay view-only; registered Manager Send is the separately guarded native path below.
Host sessions freeze Refine's own context path at the context read, separately
from Send authority; `leafRef`/`fileEpoch`/`contextIdentity` pin that path during provider/clipboard
awaits. **A Manager mid-turn never blocks Refine:** later transcript growth is excluded from that
frozen context and tolerated without any bound (new rows after it, tool calls and results, intermediate
and final assistant text, the reply that was still streaming at the read and sent nothing, a message
that was read gaining further blocks), during the read itself and at the desktop's pinned re-checks
after inference and clipboard (`ClaudeFacade._frozen_changes`: the read remembers the prose each row
sent, per frozen leaf). Refine still refuses, with `changed-context`, a message it read being
**rewritten** rather than appended (its prose no longer begins with what was sent), and as before a
different conversation (another execution, target or session), a new generation (`stale-generation`:
the Manager's process was replaced; reselect), a changed epoch (`stale-epoch`) or an incomplete index.
The desktop's pinned re-check no longer compares `leafUuid`, because the frozen path's last row can
be the reply still in flight, which takes a record UUID as each block lands. Native/windows-history
retain their strict selected-path behavior.

### OpenCode Managers on desktop and phone

In the desktop **Managers · Claude / OpenCode** surface, or directly on the phone **`/m`**,
**＋ New Manager → OpenCode** offers the real workspace catalog (provider/model and that model's
thinking variants) and the installed `manager` or `manager-gpt` persona. The chosen model is
explicitly pinned; changing models never secretly changes persona. Native Claude remains the
default runtime with its existing launch defaults. **Launch & select / Start** creates a named
conversation in the existing `manager-sessions` workspace; once it is selected, its first message
goes out (*New Manager, way-out controls and honest refusals*). Its MCP
connection status is information, not a launch gate or proof that a first turn has its tools ready.
No CodeNomad tab, extension, PC visit or TUI is needed on the phone.

Existing genuine OpenCode Managers share **Manager Sessions** on desktop and the phone's chip
strip with Claude Managers. Select one to read and target that same Manager. Selection is GET-only:
it never sends, resumes or wakes a process, and keeps your draft. A sleeping/ended native Claude
Manager with reconnect authority also replaces the previous editor target when selected; only its
later Send reconnects it. OpenCode Manager identity is its full `ses_` id plus the server-observed
creation/folder witness. Actual root position, Manager agent and non-seat ownership decide authority,
never a title or folder name. The Manager chip picker is unaffected by the sidebar's view selector;
a saved full id can always be revalidated directly after reload. Workers, Conductors and subagents remain
read-only in the same viewer (including Programs → Read on the phone).

**Send** goes directly through the guarded pane OpenCode routes. While the Manager is busy it is
a native **steer**, taken at its next step boundary; no confirmation or idle-only refusal is added.
The browser persists and reads back the complete original, exact boundary-trimmed submitted text,
backend-qualified target and operation before requesting its one execution. The pane durably binds
that intent to a server-clock native `msg_` id before its one upstream POST. Browser/phone clock skew
cannot change native message ordering. A native id is not by itself an ACK: the pane compares the
actual first-admitted target/type/text (and inbox delivery), since OpenCode's first admission wins
without body equality.

The receipt keeps **pane accepted**, **durable inbox pending**, **history delivered**, native turn
**completed/failed**, and **uncertain/conflict** distinct. A pending inbox is not yet history delivery;
a completed turn is not task acceptance or proof that every background effect stopped. Reply loss,
page reload or pane restart only queries that same intent/id, never posts it again. A missing receipt
stays uncertain. **I checked — release it** records your deliberate disposition after looking at the
conversation; it keeps the original and never resends it. Exact native evidence can clear only the
unchanged same target/draft and persisted original. Desktop reload keeps its draft; the phone retains
its existing exact-restored-draft recovery rule. Edits, retargets and away-and-back changes invalidate
older work monotonically. Another target's receipt cannot clear or hold the current draft.

OpenCode original/journal slots retain full text without the native legacy recovery's rolling bounds.
Browser storage failure prevents an unjournaled execution, never accepts a crop. Legacy Claude keys,
originals and unresolved receipts remain; no site-storage clear is a recovery step. PC and phone
drafts remain independent. OpenCode browser slots are `pane_manager_v1:opencode:*` on desktop and
`pane_m_v1:manager:opencode:*` on phone; the pane's private durable journal is
`~/.local/state/opencode-companion/transcription-pane/opencode-managers.sqlite3`.

**Refine** and **What Refine sends** use the selected OpenCode Manager's genuine main user/assistant
prose through a guarded server read, on both devices. Whole mode pages to exhaustion; Budgeted
history explicitly takes the tail. Tools, reasoning, synthetic/system text, glance clips and only-loaded
viewer rows are never substituted. Explicit no-context Refine reads no conversation. Refine's own
provider, key, model and billing are unchanged; it does not run through Manager inference.

Desktop model/thinking controls and the phone's **hold-chip** sheet use the real per-Manager catalog
and native session readback. They work while busy and affect **future calls**, not a call already
running. **■ Interrupt / ⋯ → Interrupt** records one intent and makes one native interrupt; a lost
reply reads back state and never repeats that interrupt against a later turn. Acceptance is not cleanup
settlement. Unknown settings/context never block Send. OpenCode context is the **newest measured
assistant call's input + cache read + cache write + output + reasoning**, labelled with model window,
basis and as-of time; native Claude's existing input-only figure and global account quota are separate.
A missing measured call/window stays unknown, not zero and not an assumed million-token window.

The new finite `/api/opencode/{manager,manager-models,launch-defaults,intent,launch-manager,send,
control,receipt,context}` routes reuse `/m`'s exact origin/Tailscale-login guard. OpenCode's service
password stays on the server. `/oc/*` stays loopback-only, and the original desktop follow/manual/
queue/thinking bridge remains `/api/send`, unchanged. New intents and browser leases participate in
the existing pane restart quiet check.

**Activation checklist (operator work, not performed by this build):**
1. Integrate the reviewed update with other pending changes and run the combined-tree checks,
   preserving draft-safe selection of sleeping/reconnectable native Managers.
2. Save any unsaved dictation externally. Dan or his Manager runs the existing guarded pane-only
   `bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/transcription-pane/start.sh restart`
   (background it from a Manager's own turn). No OpenCode, proxy or Claude-host restart is expected.
3. Reload desktop and phone `/m` pages. On `/m` alone choose ＋ → OpenCode, choose model/thinking
   and persona, Start, then supply the usual opening Manager prompt yourself. Try Send/steer, Refine,
   hold-chip model/thinking, Interrupt and Context. Reopen that same Manager after reload; retain
   browser recovery keys. Check native Claude and the old desktop follow/manual workflow too.

### Native Claude Manager Send and controls (U06 prepared; production activation pending)

The editor's ordinary Send button/hotkeys dispatch by backend; no native identifier enters
OpenCode. For a registered Manager, Send first observes state and freezes UUID/generation plus
target/draft/page revisions. **Send while the Manager is busy with its own turn** goes through the
host's steer (`steer: true` on `POST /api/claude/send`): a steer reaches a busy Claude session at its
next tool step; one long-running command delays it until that command ends. The status and receipt
line say **queued** (Claude Code holds it; the unchanged draft clears then) and **delivered** (its
replay echo: the Manager has it). One steer is in flight at a time: a second Send waits until the
first is delivered, draft kept. A native retry inside the turn is no blocker (the steer is queued
through it). Neither is a scheduled automatic recovery (after a capacity/quota error or a crash; a
capacity wait can be an hour): the panel says so, and your Send replaces its `continue` (the host
cancels it). Only while a recovery is sending its `continue` right now does the host refuse, in its
own words, with the draft kept. **■ Interrupt** is shown beside Send for a live Manager (lit while
its own turn runs; also under the Manager controls); interrupt never rolls back tool effects. A host
started before the steer refuses it in plain words until its guarded restart. `priority: "now"`
(which ends the turn and cancels a pending permission request) is not used.

Host Send binds to the registered **executionId + generation, role and current
capabilities**, not a selection-time history leaf. New records and per-block
partial→final replacements cannot invalidate it; incomplete/bounded history may
block Refine, not session Send authority. **Selecting a Manager never reads its history:** the
editor validates the target (and the reconnect, reselect and New Manager adoption paths validate their
row) through `GET /api/claude/target`, one host `state` read, independent of completing the conversation
history or its lock: target authority uses the independent control lane. Refine's frozen pin is unchanged: its context
read mints `leafRef`/`fileEpoch`/`contextIdentity`, and its pinned re-checks read the history with them
(`_frozen_changes` still refuses a rewritten message). After pane reconstruction, handle expiry, transient
validation errors or a generation change, the same registered execution is automatically
rediscovered and validated; frozen work is cancelled and drafts/old receipts are retained.
A selection during initialization is polled until writable. An ended or **sleeping** Manager
stays selectable as a Send target with a named live successor offered explicitly; it can
separately supply archived Refine context. No automatic recovery picks another execution,
launches/resumes a process or repeats an intent.

**Reconnect on Send.** When the selected target is a registered Manager with no live process —
a collected conversation (**sleeping (reconnects on Send)**) or a proved-ended one — Send first
calls `POST /api/claude/reconnect` with the exact identity (`targetRef`, `executionId`,
expected old `generation`) and nothing else: no text, no message UUID, no model/CWD/UUID launch
arguments. The server refuses every other role, unregistered history and unproved ownership.
The host reuses a live generation and launches at most one replacement generation, only when
the old owner is proved gone and its UUID lock is free; ten concurrent reconnects (tabs, races)
still produce one process, because the operation is idempotent and **not a Send**. The pane then
observes readiness through the ordinary `state` read, rebinds the editor to the returned
generation (a fresh target ref), and only then writes one **new** journaled Send (`messageUuid`,
exact original/submitted) to that generation. A reconnect alone never carries a message; a lost
reconnect reply is idempotent (re-read state or Send again), never an uncertain Send retry. The
pane passes the **original expected generation** through: a live newer owner is reused for it,
while a stale expectation whose transport already ended refuses with the host's own reason
instead of resuming behind the pane's back. An earlier unacknowledged Send on the **same
conversation** (any generation) is never repeated by a new UUID: the reconnect may stand, but
the pane first queries that exact receipt — the generation it was sitting on needs the exact
replay ACK, an earlier one its exact durable receipt — and only then sends; a different
conversation's journal stays independent, and the earlier generation's receipt stays visible
and query-only. Every await around the reconnect, readiness and the new Send is bracketed by
the frozen-then-rebound target revision, so an edit, retarget or away/back retarget cancels
with the draft retained. The visible status reads **Reconnecting to ‹name› …**.

Before one POST, the full untrimmed original is saved/read back in existing browser recovery, then
an exact operation journal (original, submitted boundary-trimmed text, caller UUID, frozen identity)
is saved/read back. The host journals both strings/UUID durably before stdin. Browser quota,
corrupt recovery or oversize storage can legitimately refuse; save externally, never accept a crop.
A journal this browser cannot read holds Send and the controls and shows **Export & clear journal**:
every journal key (originals included) is downloaded as a file and kept in this browser as
`pane_claude_journal_quarantine_v1:‹time›` when there is room, the disposition is recorded in
`pane_claude_journal_disposition_v1`, and only then is the journal cleared; with no copy possible it
refuses. Nothing is resent.
Each execution+generation has its own journal bounded to 4 MiB serialized UTF-16, with space
reserved for compact receipt metadata; origin-wide reference indexes merge other same-profile tabs' stored slots. Receipt text is
referenced, not duplicated. Legacy single-operation journals are preserved/migrated, not cleared.
The existing originals bound remains 2 MiB.

**Accepted, exact replay-acknowledged, started, completed and uncertain are separate labels.**
POST success or pipe write never clears the editor. Only the exact durable UUID/text/original
replay ACK (for a steer: its durable native `queued` receipt) can clear the unchanged draft on the
same target/revisions/page. An edit (even away
and back), retarget, backend change or reload keeps it. Last-sent/full originals remain available.
Send flushes this tab's pending draft save before journaling; a failed flush retains the box without blocking Send.
Draft clear requires the stored draft still to equal the sent original, and is itself persisted/read back before clearing the visible box. Completion is a turn
receipt, not task acceptance or proof that background tools stopped.

A lost response, invalid reply or observation timeout queries the **same receipt** only, never
repeats Send. Reload recovers its UUID, but cannot clear the new page's draft. A missing receipt
stays uncertain for that execution+generation, not permission to resend it. A Send still unconfirmed
after 30 s holds the conversation's next Send with that reason in view and the way through,
**I checked — release it**: your deliberate disposition after looking at the conversation, recorded on
the journal entry (`released`); the original stays in ↩ originals and its receipt line, it is never
resent, and the next Send is a new message. Old-target receipts
remain visible and query-only, but never block a deliberate Send or Interrupt to a new Manager.
Use the host receipt/incident/raw log review in
`docs/claude-headless-manager.md`. Preserve browser keys; no blanket storage clear or blind resend.

Native model/thinking controls are separate from Refine provider/model/settings. They affect
subsequent turns of this Manager only, with caller-allocated durable control IDs and native
`get_settings.applied` readback. Requested/replied is not applied. Pending or positively mismatched
settings block host Send. **Settings the host cannot verify** (`settings_unverified`: a readback that names
no usable model/effort, nothing naming a different one) leave Send open: the badge reads **⚠ settings
unverified** (orange, the host's disclosure on hover), the observation line and the conversation banner say
so, and a Send's status line adds the warning; Re-check settings verifies. Effective values and the
correlated control receipt are shown.
The model/thinking badge says **applied** only when `controls_effective` and
`setting_control_state: applied` agree with a verified observation; otherwise it
says **pending**, never treating the dropdown's requested selection as applied. Idle dropdowns follow changed
verified applied readbacks; an unapplied local choice or in-flight/unsettled control is preserved across polls.
Model switches can invalidate cache; no simulated TUI dialog/global default writes. Polls only observe
host state/receipts; their 12 s observer budget never cancels host work or native control waits.
Measured previous-call context is available under supervision; unattended seats remain later work.

Fixed same-origin JSON mutations: `POST /api/claude/send` requires schemaVersion:1, backend:'claude',
targetRef, executionId, generation, messageUuid, original, submitted; `POST /api/claude/control`
uses the same identity plus requestId, operation (`set_model`+model, `apply_effort`+effort, or
`interrupt`+optional reason); `POST /api/claude/reconnect` carries the identity alone
(targetRef, executionId, expected generation) — never text, a message UUID or launch arguments —
and answers the current generation with a fresh target reference for the rebind. Read-only GET `state`, `receipt` (+messageUuid), `control-receipt`
(+requestId) take exact targetRef/executionId/generation. Receipts can reconcile their safely
scoped original generation; mutations always require current registration. No browser launch,
path/argv/CWD/endpoint control. Strict schemas/origin checks; 8 MiB request bound, never cropping.

The pre-removal U1–U3 milestone records live in Git history; this README states
current behavior only.

### New Manager, way-out controls and honest refusals (guard sweep)

* **＋ New Manager** (Manager surface, at the top of the conversation view's session list, always
  visible; the editor drives its form in the same-origin frame): name (default one above the highest
  `Manager-Session-NNN` the pane knows: `scripts/.oc_config.json`, earlier pane names, transcript
  labels), **runtime** (Native Claude, the default, or catalog-backed OpenCode as above), model (for
  Native Claude, free entry with suggestions: any model the host's own launch rule accepts, so a new
  release needs no pane change; paid-context/provider routes stay refused by the host) and thinking
  (default Opus 5.5 / xhigh; an explicit choice is kept). **Launch & select** posts
  `POST /api/claude/launch-manager` `{schemaVersion, backend, nativeUuid, name, model, effort}`;
  the server (`claude_launch.py`) runs the operator launcher's exact `run_launch` (compose, publish,
  one host `launch` with role Manager, nothing sent), starting a stopped host first with
  `claude_host.py start` (its own lock refuses a second host). The browser allocates the UUID, so a
  repeat or lost reply answers the first launch, never a second conversation. The current Manager
  is never touched; the new one becomes the Send target when ready (Dan picking another target
  first wins) and the right pane shows it. A reload keeps watching for it. Names live in
  `~/.local/state/opencode-companion/transcription-pane/session-names.json` (display only).
* **The first message.** Once the new Manager is the Send target, the pane sends it Dan's default
  first message (his Manager Sessions opener, `Manager-Session-{N}` filled in from the name) as one
  journaled Send, exactly as Send journals one (OpenCode: one journaled intent), never the draft; its
  ACK clears, copies and records nothing. Picking another target first cancels it; a refusal puts
  the text in an empty draft for a deliberate Send. Untick *send its first message* in the form to
  launch without one. **Default first messages** (folded in the form; the phone's New Manager form
  has the same) edits the two templates, one saved copy for desktop and phone (`GET`/`POST
  /api/claude/first-messages`, `first-messages.json` beside `session-names.json`; an empty box or
  **Restore defaults** returns to the built-in text): `{N}` is the Manager-Session number as written,
  `{letter}` the sub-session letter, `{name}` the whole name.
* **↳ Sub-session** (beside ＋ New Manager; on the phone the green ↳ beside ＋) starts the next
  sub-session of the newest primary in Manager Sessions: its number is the highest listed, its
  letter the one after the highest letter that number already has (`a` when none; a skipped letter
  stays skipped; `static/manager-order.js` `nextSub`). It opens a box with the sub-session default
  filled in (`sub-session 219-b of Manager-Session-219`, the [ap:M8T0EO] instruction and the
  `oc.sh session sub --parent 219 --letter b` line), ending `Reason for this sub-session:`. Under
  the text sit **New Manager's own runtime, model and thinking controls** (one set of controls that
  moves into whichever box is open, desktop and phone alike, so the two never differ; Native Claude
  and Opus 5.5 / xhigh by default). Dan adds the reason, picks the runtime if Claude is
  not the one that works (OpenCode → persona, catalog model such as `openai/gpt-6.1-sol-fast`,
  thinking such as `xhigh`), and presses **Send**, which launches `Manager-Session-219-b` through
  New Manager's own path and sends that text as its first message (an OpenCode sub-session gets
  the same journaled first-message intent an OpenCode New Manager gets). The OpenCode route never
  touches the Claude host, so it is the crisis route while that host is down. The pane never runs
  `oc.sh session sub` itself: the new sub-session does, per [ap:M8T0EO], and stamps
  `--session 219-b` from then on.
* **Quota row** (desktop sidebar only, under ＋ New Manager, above the Show filter; `static/sessions-quota.js`,
  never loaded by `/m` or `/m-programs.html`): one small gauge per allotment window the phone's Context
  view shows (Claude five-hour `5h`, `wk`, `Fable`; every ChatGPT window), each a pie, short label and
  the share **used** with the time until that window resets in brackets (`(4h 50m)` under a day,
  `(5d 3h)` beyond; nothing once passed or unknown), turning green → orange → red (`--quota-*` thresholds in the `sessions.css`
  theme block); hover gives left/used, reset time, freshness (Fable's weekly share, ChatGPT model
  availability). No reading, a failed provider without a last-good reading, a missing figure or a window that reset after the
  reading is a grey `?`, never zero; a reading over 5 minutes old stays visible, dimmed, and Claude's or
  ChatGPT's own stale last-good snapshot stays visible too, marked stale with its provider age; a Claude
  rate-limit backoff is a quiet countdown in the tooltip and no click reads before its `retry_at`. It
  polls the same cached `GET /api/usage` every 60 s while the page is
  visible (5 s while the server refreshes, 20 s after a failure); a click asks `refresh=1`, still
  bounded by the server. Narrow lists shorten the brackets to `(4h)`/`(5d)`, then drop them, then the
  provider words, then the window names (container widths 490/405/320/255 px, measured).
* **Re-check settings** asks Claude Code for the applied model/thinking (one read-only
  `get_settings`): a matching readback clears a stale settings hold; nothing is set or sent.
  Apply model/thinking can also supersede an uncertain setter or one from an ended process.
* **Reconnect refusals say why.** The pane offers Send-reconnect exactly when the answering host
  would accept it (`reconnect_blockers`, under the host's stamped `reconnect_policy`); otherwise the
  ended row and status line show the host's reason in plain words plus the way out (usually ＋ New
  Manager). A refused reconnect keeps that reason on screen and never marks the target broken.
* **Spinner.** A Manager's sidebar spinner means its **own turn** is running: the host's
  `primary_turn_open` (primary `system/init` opens a turn, its primary `result` closes it, so a turn
  started by a background-task notification counts too) or a Send still unanswered, read from the
  newer of the listed and the selected view's polled host record. Streamed activity (thinking,
  writing, tool) never lights it, so the selected and listed rows always agree; a running background
  subagent spins on its own row and never on the Manager's. A task-notification `result` also
  returns the activity line to Idle.
* A completed native result for an earlier Send settles it for the next Send even when its echo
  never matched byte for byte (it is still never resent).
* **Open incidents, each with Resolve.** The Manager panel lists the targeted Manager's open incidents
  (escalations addressed to Dan: category, time, what happened, from `GET /api/claude/state`'s
  `openIncidents`) always in view, each with **Resolve…**: Dan's typed disposition goes through
  `POST /api/claude/resolve-incident` (`targetRef`, `executionId`, `generation`, `incidentId`, `disposition`)
  to the host's own `resolve_incident` verb, which records it on the incident and closes it. Never a Send or
  a native control.

### Removed transport and old private receipts

`POST /api/claude/send` now targets the independent host's registered Manager only; browsing
cannot start/resume a Manager. The old binding/delivery/Arm/Release terminal routes
and `CLAUDE_CONTROLLER_RUN` adoption no longer exist. The interim interactive WSL
launcher (`scripts/claude_manager_interim.sh`) and independent Windows launcher
remain; the replacement headless host is an independent component under `scripts/`
(see `docs/claude-headless-host.md`), never started or stopped by this viewer.

Local cleanup is held for two unresolved preflight receipts in old runs
`Manager-20261013-174727-ee1f86-exit` and `Manager-20261013-180029-b87f2e-exit`.
All original run/hook/socket state and transcripts remain in place, with privately
verified preserved originals/ownership ledger; Dan must disposition uncertainty
before cleanup. Old browser receipt keys can also contain originals: do not clear
site storage or blindly resend. Save unsaved dictation before an idle pane-only
stop/start and same-origin reload; no OpenCode/proxy/Workflowy restart is needed.

## The pane

Dan's dictation pane, beside CodeNomad in a Chrome **split view** (right-click the CodeNomad tab → New split
view → open `http://127.0.0.1:9911` on the other side). He works only in this pane:

1. **Dictate** into the box. Wispr Flow does the listening and types into whatever box has focus, so the pane
   needs no microphone.
2. **✨ Refine** (Alt+R) cleans the selection, or everything, with a frontier model, using the open
   conversation as context.
3. **📤 Send** (Ctrl+Enter · Ctrl+Alt+Shift+Enter · Shift+F11 · F4) posts it to the OpenCode session, and
   CodeNomad shows it. Nothing is typed into CodeNomad.

### Claude accounts: view quota, switch deliberately, cancel a waiting switch

Dan's two Claude Max accounts live in two native login folders; `scripts/oc.sh claude_accounts` owns the
label → folder mapping and the selector (its `status --json` supplies the active label, the epoch and the
public identity). The app only offers the two fixed labels and one durable operation:

- **Phone → Context → Claude Max:** tap **Personal** or **Work** to view that account's own 5-hour,
  weekly and Fable-weekly percentages used and the countdown/time to each reset. The **green ring**
  stays on the active login; the **blue selection** and **Viewing Work · NOT active; Personal stays
  active** line identify what is shown. Viewing never switches, even while another switch waits.
- **Desktop header:** the same active label/email and read-only account buttons. Tapping an account
  opens **Quota & switching**, with that account's readings and the separate action buttons.
- Only **Switch to Work** (or Personal) requests a switch. No confirmation dialog. It restarts the
  host once genuine Claude work is quiet. Proven observation-only timers, quota sleeps and
  session/socket doorbells are listed durably and ended rather than waiting out their deadlines.
  Unknown/mixed tasks, real commands/tests/exports/edits/Agent work and input/ownership ambiguity
  remain holds; the waiting detail names the actual owner/task/reason.
- **Cancel switch** is available throughout the wait, including after a page reload. It ends that
  queued operation as **cancelled**, leaves the login unchanged and immediately re-enables Switch.
  Once old-process stopping/closing begins, cancellation is unavailable and the UI says it is too late.
   Running Claude sessions retain their original login; genuine work therefore still needs a quiet restart.

The explicit Switch posts `{schemaVersion: 1, backend: "claude", account: "personal"|"work", operationId}` to
`POST /api/claude/account-switch` — a label and this tab's operation id, never a path, credential or
command. The pane starts the existing queued guarded restart
(`oc_claude_restart.py --_worker --account ‹label› --close-idle --resume ‹current Manager UUID›`) and
reports the canonical `claude-restart/1` receipt: **waiting** for work to settle (only typed pre-stop
transients retry every 15 s) → **switching** → **starting**/**recovering** → **‹Work› active** only
with the requested destination and exact ready service. **Restored** means failed switch/verified
old service; **recovery_required** gives `claude_restart.sh --recover ID`; refused/failed/cancelled
retain their real partial effects. A label, exit code or log alone never proves success. The selected
ring, usable host, Manager readiness and notice delivery are separate. Already selected is a
zero-spawn shortcut with service unchecked, not a healthy restart receipt. The newest host-registered Manager
receives one exact-receipt recovery-index notice after a verified switch, reconnecting if needed. The index
has one report per affected program with current-Conductor routing on either backend, plus separate Manager,
standalone and unresolved sections. Watched workers are untouched; waiter exit0/idle is not accepted completion.
Missing Manager or Workflowy publication never holds switching: the local queue/notice retains pending versus
confirmed status and stable page/message identities. No leaf is independently resumed. Partial stop/failure
receipts survive timeout/reload/retry. Post-stop failure notices become due only after service is
verified back; pending is not delivered. Plain successful restart/resume sends no turn by default.

The operation is written to
`~/.local/state/opencode-companion/transcription-pane/claude-account-switch.json`, so a lost reply, a second
tab or a pane-server restart reads that same operation instead of starting another. Lifecycle truth
stays in the engine's existing `observer_recoveries` row, not this pane projection. Unknown evidence
remains query-only/reconcilable even if the pane died after spawn before saving its PID. GET never
starts recovery, selection or retry; CLI recovery by the displayed ID is supported, with no new
recovery button or confirmation. Earlier IDs retain
their receipts after subsequent switches, so an old retry cannot create another restart.
`GET /api/claude/accounts` reports the labels, the public identity (email/plan) and the operation;
`GET /api/claude/account-switch[?operationId=…]` reads it. Cancel posts
`{schemaVersion:1, backend:"claude", operationId}` to `/api/claude/account-switch/cancel`.
A per-operation durable gate and interprocess lock arbitrate **cancelled** versus **stopping** before
the first old-process close/stop; cancellation does not race a process signal inferred from log text.
The waiting worker observes cancellation and exits quietly. Earlier operation IDs cannot cancel a
newer operation. All routes use the same origin policy as the
rest of the pane (`_claude_origin`), so the phone through the Tailscale Serve origin and the desktop on
loopback behave the same, and no login-folder path or secret ever reaches a browser. The phases are also
dispatched as a `oc-account-switch` document event for a page that wants to refresh its Claude rows.

`GET /api/usage?account=personal|work[&refresh=1]` reads that label's own native login without selecting
it or reading/renewing GPT credentials. There is **one bounded background cache per Claude login**: the
default `/api/usage` of the active label and that label's named view share it, so the active account's
endpoint is read once, while GPT has its own independent cache (a Claude backoff never holds GPT back,
and a GPT read never reads Claude). A primary quota 429 keeps the last good windows visible with their
provider age, a quiet note and `retry_at`; no poll — `refresh=1` included — starts a read before that time.
A banked-only 429 has its own shared retry time: the last banked list stays labelled historical while
healthy quota can still refresh. The
browser preloads both enrolled labels without forcing, labels the age and rejects another account's
response. Missing windows/reset times stay unknown; a passed reset does not show an old percentage as
current. Phone banked Claude resets are explicitly labelled **active account**, independent of the
quota view toggle.
`scripts/oc_usage.py` also shares that endpoint snapshot/backoff with CLI readers under an identity-keyed
interprocess lock (`$XDG_STATE_HOME/opencode-companion/claude-usage/`, default `~/.local/state/…`). Ordinary
quota TTL is 120 s, banked resets 1 h (their own age is shown); deliberate Refresh / `oc.sh usage --refresh`
can refresh both after the existing 30 s minimum. A 429 honours `Retry-After` (seconds or HTTP date), else
waits 2 min, growing to 30 min on repeats; no reader bypasses the failing endpoint's gate. Quota is read
first, so banked-reset throttling cannot starve it. `requests.jsonl` there records actual
endpoint attempts, account label, parent caller and status only (bounded 1 MiB, no credentials or bodies).
Activation: guarded pane-only restart, then reload desktop and phone pages. No Claude host restart
is needed for view/cancel controls alone. Observer retirement/recovery notices require a separately authorized
guarded Claude-host restart to load the new host Python; these repository changes do not activate it themselves.
The independent five-hour notice can be controlled exactly with `oc.sh claude_notices defer EPISODE` or
`cancel EPISODE` (inspect with `list --json`); the halt display says **not armed**, retaining any already admitted
notice receipt honestly. Other episodes keep reset+five-minutes behavior; same-episode halts/restarts do not rearm it.

## Teams message breaks (Ctrl+M)

Ported from the TypingMind widget, for marking pasted or dictated Teams messages before Refine and Send.
**Ctrl+M** in the box (or **🗨 Teams break** under it; on the phone page, ⋯ → **Teams message break**) opens a
small popover over the box with the speaker already chosen; **Enter** inserts, at the cursor:

    ===MESSAGE_BREAK===
    Speaker: ‹name›
    Date: ‹date›
    Comment: ‹one line›          (only with a comment; several lines: "Comment:", then each line indented four spaces)
    ===END_BREAK===

No newline is added before or after it; the cursor lands just after it, and Ctrl+Z takes it back. With two
active speakers the popover preselects the one **not** used last, so a two-person thread is Ctrl+M, Enter each
time; with three or more, the first whose name contains "dan". **1–9** or the arrows pick another speaker,
**Ctrl+Enter** inserts from the comment (plain Enter is a new line there), **Esc**/Cancel closes without
inserting; while the popover is open no key reaches Send or Refine. The comment always starts empty; the date
persists (today when unset). **Speakers** (in the popover, open by itself while none is active): ten slots, each
a checkbox (active in this thread) and a dropdown of known names with **＋ Add new…**; × deletes a known name.
Settings are browser-local under the widget's key names (`teams_message_speakers`, `teams_message_active_speakers`,
`teams_message_date`, `teams_message_last_speaker_index`, `teams_message_known_speakers`), per origin (the phone
over Tailscale keeps its own), and are not part of ⚙ Import settings. Refine's default system prompt keeps these
blocks verbatim; a saved system-prompt override needs **Restore default** (or the same rule) to get it.

`static/teams-break.js` / `teams-break.css` are self-contained: they attach to any `data-teams-break` box and
`data-teams-break-for="‹box id›"` opener, and insert as the box's ordinary input, so the page's own draft
saving runs. Activation: a page reload.

## Which session

The **CodeNomad Session Watcher** extension (`../chrome-extension/codenomad-session-watcher/`) reports which
session CodeNomad has open; the pane follows it (green). Pick a session by hand from the dropdown to pin it
(amber). If the watcher goes quiet for 45 s, the pane keeps the last known session and shows it red, and Send
asks first. A session whose agent isn't `manager` also asks first.

## Persistent selected-tool inspector

With CodeNomad Session Watcher **0.1.11**, click a CodeNomad tool header/argument preview to inspect it;
the separate **Toggle** keeps native expand/collapse independent. Right-side **In / Out** labels also show
raw stored snapshot sizes for unselected rows.
The pane shows the latest deliberately selected call while CodeNomad's live history stays visible. Full
available stored input is above output, with two independent scrollers and a movable internal divider.
Drag the separators, or focus one and use Up/Down (5% steps), Home/End. The outer separator shares height with
dictation. The inspector appears with the first deliberate click and is never closed or collapsed (round 9a):
the next click replaces the call. Its thin head row names the tool and the call's start time in Eastern time,
`2026-10-18 23:52:07 ET` (OpenCode `state.time.start`, Claude record time), then `· took 1m 23s` when the
stored records carry its end too (OpenCode `state.time.end`; Claude: its result's record time, on the host
feed the arrival of the frame carrying its result, so measured from the call's shown time, which on the host is
its message's start); an unsettled selected call shows a ticking `… since call · no result recorded`
instead (stored state alone is not proof that its process still runs). The stored duration
(`opcToolReading.callDuration`) stands on every completed collapsed tool bar, desktop and phone, just left of
its In/Out sizes: each call in a conversation page carries `time`/`resultTime` exactly as its snapshot states
them (Claude reader, host feed from the rows in hand, OpenCode part times), so no bar reads a snapshot for it.
Unfolding it shows the source details, read status and **refresh**.
On small windows the page can scroll rather than squash dictation or the reading regions to nothing.
No incoming selection steals focus.

**Diff.** A held source edit (Claude Edit/Write/MultiEdit, OpenCode edit/patch/write) opens on its recorded
change, above Input and Output, in the shared IDE-style diff reader (`static/diff-view.js` with the vendored
highlight.js; the phone uses the same one): line numbers exactly as recorded, red/green −/+ rows, syntax colors
within the recorded hunks, each file's path, status and +N −N. Its own controls carry **Layout** (Auto is side by
side when the Diff is at least 900 px wide, inline when narrower; Inline and Side-by-side override it until the next
deliberate Inspect) and, past the first 2,000 lines, **Show all**. The data is the snapshot's own `diff` field,
normalized by the pane server from what the backend recorded (Claude `structuredPatch`, OpenCode
`metadata.files[].patch`; on the OpenCode mailbox read it sits beside the raw part): the browser parses nothing,
adds no request and costs no tokens. Instead of a diff it says what is true: a pending, failed or unreadable record
is labelled; an OpenCode write shows its new content under *new content; previous content not recorded*; a shell
call says *Shell command; no structured edit diff recorded (it may have edited files).* (alone: a diff with no files
shows no Layout/Wrap/count row); other tools show no Diff
region. Raw Input/Output, their Copy and sizes are unchanged. The Diff scrolls inside its own share of the panel
(at most 55%); a changed diff waits while text in it is selected. It arrives with a page reload once the pane
server is serving the `diff` field.

Raw OpenCode V2 results are stored in `state.content`, not CodeNomad's normalized `state.output` field.
The reader preserves text blocks in order as plain text, retains non-text blocks as readable JSON,
distinguishes empty content from unavailable output, and uses legacy `output` only when `content` is absent.
Browser-reader updates activate with a pane page reload; they do not require a server or extension restart.

New calls, native row remounts, virtual scrolling, completion, heartbeats and followed/manual session changes
do **not** close or retarget it. Only another deliberate Inspect click replaces the held identity. It retains
origin, workspace/instance, session, message and part IDs separately from Send's target. Inspection never
changes model/thinking, transcript/draft/originals, Refine prompts or conversation context. Send/Refine hotkeys
are suppressed while focus is in the inspector or its outer divider: return to dictation for hotkeys, or
use the deliberate Refine/Send buttons. **Pretty JSON** is independent for input/output and defaults ON
after each page reload; preferences are page-local and survive new selections. OFF shows the exact held raw
text. **copy raw input/output** always copies that original snapshot, not display formatting (even while a
text selection defers repaint). Manual browser selection/copy uses the displayed text.

Input/output also have independent **Readable text** checkboxes, default ON after every reload (page-local,
never saved) and enabled only while that region's **Pretty JSON** is ON. Clear multiline/tabbed JSON string **values** become bordered,
lightly shaded, pre-wrapped plain-text document blocks at any nesting depth, with array indexes shown.
All keys, structure, duplicate keys, ordinary scalars, numeric lexemes and exact trailing prose remain;
keys/simple strings are not decoded. Exactly one string layer is decoded: an intentional literal `\\n` after
decoding stays literal. The caption visibly says **reading view (not JSON)**. Empty or unavailable text gets
no reading caption. Raw snapshots/Copy never change. Toggling either option restores pretty/raw without
fetching or retargeting.

Readable text also renders **Markdown**, display only, through the same local Marked + DOMPurify path as the
history viewer (source HTML escaped, images as text, safe links). Three places qualify: a decoded JSON string
value, the prose suffix after a leading JSON value, and a whole non-JSON input/output (e.g. a Workflowy node
read or Dock render). A Workflowy read with numbered rows (`  12 | text`, `... | ...` elisions) qualifies when
every row after its header has that form: the header stays plain text and the body renders without row
numbers. Detection is conservative: it needs two kinds of Markdown signal (a heading followed by a blank
line/list/table, two or more list lines, a closed ``` fence, `**bold**`, a link) or two such headings or a
pipe table with its delimiter row, and refuses text that starts with `{`/`<` or is JSON, contains diff or
traceback markers, or has a real share of code, log/timestamp, `path:line:` hit, tab/arrow-numbered listing or
`key:` config lines. Captions say **reading view (Markdown)** or add **· Markdown**. Every rendered block has
a small **Rendered / Source** switch (Rendered by default); Source shows the block's exact text and the choice
survives repaints until the next deliberate Inspect. Rendered prose always wraps (code blocks scroll); Word wrap
governs Source. Rendering is bounded (1 MiB per text, 200 blocks / 1 MiB per region) and falls back to plain
text if the renderer is unavailable. Pretty JSON, Readable text, Markdown and the painter live in
`static/tool-reading.js`, shared with the phone page's tool view.

Independent **Word wrap** checkboxes default ON after page reload and retain their choices across selections and
refresh within that page; Input's also wraps the Diff above it (Output's does not). OFF preserves physical line breaks/tabs, disables soft wrapping
and provides a horizontal scrollbar **inside that input/output reader**, for raw, Pretty JSON and Readable
text blocks alike. The other reader's wrapping/scrolling/divider is untouched. Native labeled checkboxes work
with Tab/Space; focused readers support keyboard scrolling. Wrap changes are CSS-only, without reparsing,
repainting or disturbing selected text/raw Copy. At split-view widths ≤440px, the inspector has a 450px
minimum height so wrapped controls leave reading space; the page may scroll vertically, never horizontally
because of unwrapped payloads. Preferences are page-local, not saved to browser storage.

Only complete valid JSON is formatted, preserving number/string/key tokens, escapes and duplicate keys:
large integers, negative zero and exponents are never parse/stringify-rounded for display. A valid leading
object/array followed by non-JSON prose can also be formatted, with its suffix unchanged. Invalid/truncated
input, middle/trailing JSON and ambiguous concatenated JSON/delimiters stay raw. Tool strings remain literal
text, including input fragments; structured values use their server-response JSON representation, with
wire lexemes retained before browser numeric coercion. This cannot recover syntax/precision already lost
upstream before the snapshot, or any saved guard stub.

Formatting work is bounded to 1 Mi UTF-16 code units, 128 nested containers and 100,000 tokens per region,
and the combined rendered input/output remains within 8 MiB UTF-8 in any pretty/readable/raw combination. Above a
format/expansion bound, the entire original text remains readable/copyable and the label says
**raw (format limit)**; there is no cosmetic crop or failed raw read. Views are computed once per fetched
snapshot, not on every toggle/poll repaint. Formatting is safe plain text only and changes no stored data,
identity, selection/polling, Send/Refine target, draft, context or recovery behavior. Readable text additionally
limits document blocks to 2,000 per region; a reading limit leaves complete ordinary Pretty JSON with an
explicit label. Invalid/unrecognised JSON stays raw. Unchanged views do not rebuild their DOM blocks.

### Size definition shared with CodeNomad

Loaded captions say **Input stored (X KiB)** / **Output stored (Y KiB)**; **KiB = 1024 UTF-8 bytes** of the
exact raw Copy snapshot, before either display option. A known empty value is zero; missing/loading/oversize
data never becomes a fabricated zero. String values use their decoded stored text. Structured input/non-text
blocks retain original backend wire JSON tokens and whitespace before numeric coercion or Python float
reserialization. V2 text blocks keep order with two-newline separators; non-text blocks count their complete
wire JSON, including encoded image data. Error headings/separators count as raw Copy sees them. These are
stored snapshot sizes, not tokens, original binary/file sizes, or proof of provider context consumption.
Saved guard stubs count only their retained stub, not a lost pre-guard original; the caption tooltip explains.

Row and heading size labels use a readable **15px baseline** and a continuous log-ish gray-to-red foreground:
`log1p(bytes / 16384) / log1p(64)`, capped at **1 MiB / 1024 KiB**. Known zero is neutral gray; missing/loading
remains distinct, not zero. From **256 KiB to 1 MiB**, numeric labels increase smoothly to **19px** (3pt extra),
then stop growing. Pane dark gray/red endpoints are `#a6adb9`/`#ff8080`, with ≥4.5:1 contrast on its panel;
CodeNomad also has a contrast-tested light palette. No animation; forced-colors uses system text. Exact KiB
numbers stay visible and color is supplementary. The same browser helper applies emphasis to existing byte
counts without changing snapshot serialization, association, request/cache bounds or backend machinery.

One shared `../chrome-extension/codenomad-session-watcher/tool-record-text.js` defines snapshots/counting
for both surfaces and is served by the Pane at `/tool-record-text.js`. CodeNomad row reads reuse the bounded
preview message cache (three concurrent, at most one per live visible message every two seconds), never
per-row/session-history requests. Terminal reads stop; rows remount/reuse with scoped identity and stale
responses cannot relabel another record. Cache limits and unavailable counts are documented in the extension
README; message/part over-limit records show **—** rather than inferred DOM-preview sizes.

The independent `/api/tool-selection` mailbox is memory-only and epoch/revision ordered, separate from
`/api/active-session`. Compare-and-clear and stale-response checks prevent old clicks/reads or unrelated
heartbeats from reopening a closed call. The extension supplies metadata only, not tool bodies: it checks
the local CodeNomad workspace/proxy and reads its authenticated same-origin session/message once per click.
The pane server validates immutable session creation time/location, message creation time, unique tool part,
tool name/call ID against its **fixed local OpenCode service**, with auth held server-side. Unsupported/remote,
missing or mismatched records fail visibly; there is no arbitrary URL router or password in browser code.
This is record association for the current shared-local-service, single-CodeNomad-tab/single-pane workflow,
not generalized remote routing or proof against deliberately cloned record identities.
**The latest deliberate click wins across CodeNomad, Claude and OpenCode viewer calls; a viewer click
replaces a CodeNomad-held call and vice versa.** An OpenCode viewer click (`POST /api/opencode/inspect`) is
associated by the pane server from its own read of that part (`callId` null on the V2 wire) and shows the
same raw sizes as the row; browsing, polling and child links never select a call or change the target.

Only the selected message is fetched, one in-flight read at a time, with timeout and 2 s live throttling,
3–15 s transient-failure backoff and stale-generation rejection. Terminal state/message completion stops
regular record fetching; **refresh** rereads it. Missing/mismatch/oversize errors stop automatic reads; transient
backend outages can recover on retry. If source preparation failed, click Inspect again to recheck CodeNomad.
The shell, divider, unchanged text and scroll positions stay mounted; active text selections defer repaint,
and output never auto-scrolls to the end. An unavailable call shows an error and labeled last snapshot when
one exists, never a substitute call. Tool text is untrusted and rendered as safe plain text/pretty JSON only.

Native inline 10,000-character caps do not crop this stored-record view; upstream saved guard stubs remain the
available result, not recoverable originals. Streaming input is explicitly a **not-final fragment**; final
empty/falsy/string values, running output, error and missing output remain distinct. Response reads are bounded
to 16 MiB, selected parts to 8 MiB UTF-8, and pretty-rendered input/output to 8 MiB combined. Oversize is refused,
never silently cropped. Only one selected snapshot is retained; no pins, durable history or all-history polling.
A pane reload can reread the server's current selection (scroll state is page-local); server restart
loses the mailbox, and an already-open pane labels any last snapshot until another deliberate Inspect click.

**Activate:** reload the unpacked extension in `chrome://extensions`, reload CodeNomad, then (when no
Send/Refine is in flight):

    bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/transcription-pane/start.sh stop
    bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/transcription-pane/start.sh

Reload `http://127.0.0.1:9911`. No OpenCode/proxy restart is needed. These activation/live-browser steps have
not been performed by the implementation job; isolated fixtures are not a live CodeNomad confirmation.

For **Readable text and raw snapshot sizes**, activate extension **0.1.10** with extension + CodeNomad reload,
then **stop/start the Pane server when no Send/Refine is in flight**, and reload the Pane page. Server code now
preserves the validated backend wire part once (without duplicating its body) and serves the shared helper.
No OpenCode/proxy restart. The older Pretty JSON-only update needed only a Pane page reload.

For **Word wrap and size emphasis (0.1.11)**, reload the unpacked extension, CodeNomad and the Pane page.
The 0.1.10 inspector server/shared-helper route must already be installed; if active, **no Pane server restart
is required**. Only browser assets changed in this follow-up. Save any unsaved dictation externally before reload.

## CodeNomad thinking level on Send

With watcher **0.1.7** and CodeNomad **0.20.1 English UI**, select the thinking level in CodeNomad, let it
settle, then use this pane's normal **Send**. A fresh (under 45 s), same-session, same-provider/model report
is validated against OpenCode's catalog for that session's location. The pane server awaits the native
model switch and verifies the acknowledged level **before** submitting the text. Provider/model do not
change. Explicit Default removes the variant (provider/model default); an unavailable selector is not Default.

The Send status says `thinking xhigh`, `thinking medium`, `thinking Default`, or why thinking stayed unchanged.
Manual targets, stale reports and unknown/missing selectors preserve the session's existing level.
Queued Send **never applies the selector**, matching CodeNomad's queue semantics; its confirmation/status
say so. This does not change the level of the ongoing generation or add deferred level-switch machinery.

If an applicable selection is unsupported, mismatched, changed during Send, expired, or cannot be switched,
Send fails visibly, keeps the transcript, and does **not** submit a prompt at a different level. Check the
target/selector and retry. An acknowledged switch can remain in effect if a later recheck/prompt fails;
there is no speculative rollback. Existing Refine models/settings, target warnings and Send shortcuts remain.

The relay is memory-only, ordered, and single-latest-tab: use one CodeNomad tab. Selection observation
normally takes 120 ms plus the pane's 700 ms poll. Changed revisions are refused rather than silently reused.
The server serializes pane Sends and rechecks target/model/busy state; OpenCode has no atomic switch-and-prompt
API, so simultaneous changes from another client cannot be transactionally excluded. Avoid changing
models/targets in another client during Send. Nothing edits CodeNomad's installed npm files or the payload proxy.

## Refine

Ported from the TypingMind widget (`deepgram-typingmind-extension.js`, the "Transcription Control" widget):
the four editable prompt parts with their anti-injection order (`static/refine-defaults.js`, now the source of truth, edited
directly; it began as a verbatim copy of the widget's prompts), the dictionary protect-list, Anthropic / OpenRouter / DeepInfra, thinking
levels, retry, cancel and +30 s. Settings use the **same localStorage key names** as the widget, so they import
1:1 (⚙ → Import settings; the panel shows the one-line console snippet to run in TypingMind). The shared
provider/request engine is `static/refine-core.js` (`opcRefineCore`); `index.html` loads it after the defaults
and before the PC page adapter, `app.js`, and other pages can load defaults + core without the PC page.

The default system prompt **requests document formatting** (section 5): a `#` lead heading summarizing anything
meaningful (none for a short, in-passing message), `##`/`###` section headings where a long narration, brain dump,
brainstorm, list or discussion separates into topics, and light bold/italic at key points. Headings are the only
words it adds; the wording is otherwise unchanged. For a selection it formats the selected region fully in the light
of the whole draft, gives a lead heading only when the selection starts the draft, continues the heading hierarchy
or paragraph above rather than restarting it, and never edits the surrounding draft. It also allows **one bounded
exception** to the anti-injection rule: when the speaker directly asks the cleanup for an emoji sign-off, a final
line of a few emojis (a handful at most) after the unchanged cleaned text. The final fence names that same exception.
A saved override of a prompt part (Prompt editor, shown as "your override") keeps replacing that part's default;
**Restore default** picks up the new text. *Import settings* from the widget also imports its system prompt, which
then counts as an override of the pane's default.

The **context** is read from the selected Claude or OpenCode conversation instead of hand-copied into ten slots:
the whole earlier conversation by default, Dan's messages and the assistant's prose only. **Tool calls are never
included**: Anthropic flags tool inputs sent back inside a user message.

**Chat history and the unsent draft are separate.** The request carries up to four read-only/target blocks in
draft order: `<chat-history>` (earlier turns, already sent; the editable chat-history preamble), then for a
selection `<draft-before>` (the edit box from its start up to the selection), the protected terms and the
selected `<transcription>`, and `<draft-after>` (from the selection to the end of the box), then the final
instruction fence. The two draft blocks have fixed wrappers (not editable parts), so they stay labelled even with
older overrides; all of it is background only, never cleaned, echoed, moved or merged into the output. Only the
selected text is returned and spliced back. With no selection (the whole box) there are no draft blocks and the
prompt tells the agent the transcription **is** the whole draft; that request layout is otherwise unchanged. Both
Refine entry points (the button and Alt+R / Alt+Shift+R) take this path. Whitespace-only sides are omitted, and
**Refine without conversation context** suppresses chat history only, never the draft.

**History choice:** ⚙ → **Refine context** defaults to **Whole conversation**, with no history crop on desktop,
phone or the OpenCode path. **Budgeted history** switches back in one selection to today's newest-turn crop,
using the desktop's saved budget (default 50 KiB). The shared/importable `refine_history_mode` setting defaults
to `whole`; an older saved byte budget does not change that default. The phone honours the same choice in its
own browser storage and uses its existing 50 KiB budget when explicitly set to `budgeted`. The history budget
never includes the unsent draft. Each draft side is sent **whole** up to a **256 KiB** UTF-8 ceiling; past it, the side keeps
the end nearest the selection (the before-side's tail, the after-side's head) with a `…` marker, counted in the
bound and never splitting a Unicode code point. The selected transcription is always sent whole. The context row
describes conversation history; the running Refine status reports the chat-history and both draft sizes.

The context row's **view** button opens the **Refine-context View**, which shows what Refine would send without
sending anything. It reads the context the way Refine does and opens scrolled to the end. A switch at its top offers
two modes:
- **Context:** the chat history (with its size, turns and read time), then **Draft before the selection** and
  **Draft after the selection**, each in its own pane, exactly as Refine sends them (cropped the same way). An
  empty side is one short line, and with no selection one line says the whole box is the passage.
- **Full prompt:** the system prompt and the user message, start to finish, with the provider, model and thinking
  level above them. The user message holds the filled-in context, the protected terms, the selected passage and
  the final fence. They come from `refineRequest`, the same function whose output `refine()` sends, so the view
  cannot drift from the request.

The View reopens in the mode you last chose. When a required Claude context cannot be read, it shows why and
offers no prompt, because Refine would not run. The phone page draws the same view through the shared
`static/refine-view.js` (⋯ → **What Refine sends**). Four editable prompt parts and saved overrides remain unchanged; restore the system default (and the
chat-history preamble, if overridden) to adopt the new rules. The whole-history reader/API change needs a
quiet pane-server restart through `start.sh restart`, then a pane page reload; no Claude host or OpenCode restart.

The `cache` row shows the last assistant turn's tokens and cache reads, straight from OpenCode's records.

**Anthropic Refine prompt caching** is independent of that conversation metric. Direct API requests use
one-hour cache breakpoints on the system prompt and the current history end; a small in-memory per-target
memo also marks the previous Refine's history end, so it stays findable after more than 20 new prose blocks.
Equal boundaries are deduplicated (at most three breakpoints). The history closing wrapper and changing
dictionary protect-list, draft, transcription and final fence remain uncached at the end. Full prompt still
shows exactly the concatenated system/user text. The memo stores a boundary/checksum, not prose or keys;
reload, expiry, edited history or changed model/thinking/prompts may cause a normal cache miss, never an
automatic crop, extra paid request or refusal. OpenRouter/DeepInfra retain their string-message request shapes
without these Anthropic cache controls.

The desktop's existing cost line now includes the call's measured **`cache: read 31K · wrote 1.4K`**; the phone
includes it in Refine's completion status. A cold call normally writes, the next warm call reads the stable
prefix and writes only new history. Zero means the API reported zero; `—` means no counter was supplied.
Prefixes below a model's cache minimum are simply not cached. Cost estimates use direct-model list prices,
separate cache reads and five-minute/one-hour write buckets (1.25×/2× base input; never charge their aggregate
again). In particular Opus 5.5 uses $4/$20/$0.20 per million input/output/cache-read tokens, Sonnet 5 $2/$10/$0.20.
The estimate is not an invoice; thinking/output and cold one-hour writes can dominate an individual call.

### Long inputs and Refine completion safety

Refine now **requires a complete provider response before replacing any text**: Anthropic `stop_reason=end_turn`,
OpenRouter/DeepInfra `finish_reason=stop`. Limit stops (`max_tokens`/`length`), incomplete/cancelled/refusal/tool
responses, empty output, and missing/unknown completion statuses fail visibly with the original intact. These
semantic failures are **not automatically retried** (they are not network errors). All Anthropic text blocks are
concatenated in order; thinking is not treated as transcription. A provider can still omit material while claiming
a normal stop: completion status is not proof of semantic fidelity. Keep/check the recovered original.

**Output cap** is model-specific: **Anthropic direct `claude-opus-5-5` defaults to its supported 128,000 tokens**
(Manager verified 1,000,000 context / 128,000 output in the live catalogue). Medium adaptive thinking is unchanged.
Its input accepts 1024–128000, not the former artificial 65536 ceiling. Other providers/models keep the existing
8192 default / 65536 editable ceiling; no inference that aliases/routed models support the same allowance.
An **explicit saved override is preserved**, even an old 8192 value. Click **Use model default** to remove it;
changing provider/model updates the displayed default and allowed maximum, not the override. An override outside
the newly selected model's allowed range fails visibly, never silently clamps or changes the model.

The cap is a maximum, not a request to generate that many tokens; actual generated tokens cost money. It is not
guaranteed usable text: adaptive Claude/OpenRouter/DeepInfra reasoning can share it. For
older fixed-budget Claude thinking, the selected text allowance is added to the existing thinking budget
(4096/10240/20480/32768/49152 for low…max). Providers may reject unsupported budgets; that preserves the original. There is no automatic
limit-escalation/retry/continuation spending. Rejected/incomplete provider output can still incur a charge.

**Thinking levels** (desktop ⚙ `thinking`, phone Setup `Thinking`) are the API's names: low, medium, high, xhigh,
max; the default stays medium. Anthropic adaptive models get `output_config.effort` as chosen (Opus/Sonnet 4.6 have
no xhigh, so it is sent as high). OpenRouter `reasoning.effort` and DeepInfra `reasoning_effort` both accept xhigh and
max (OpenRouter maps a level a model lacks to its nearest); the per-family clamp only reshapes low/medium/high. On
OpenRouter, xhigh/max reserve about 95% of the output cap for reasoning, so raise the cap there or expect a visible
limit failure. The PC's initial Refine deadline is 2 min, 4 min at xhigh and 6 min at max (+30s still adds); the phone
has no deadline. Higher levels think longer and cost more: thinking tokens bill as output. So a Refine that completes at
xhigh or max adds a small red line beneath its completion status, *Reminder: you are currently set to max* (or *… extra
high*), on the desktop and the phone alike (`opcRefineCore.thinkingReminder`); the next status replaces it, and high or
lower adds nothing.

**Anthropic direct now requests `stream: true` internally**, including older Claude models. SSE is buffered in
memory, **never streamed into the box or result clipboard**. Only a completed message with closed content blocks,
final `stop_reason=end_turn`, cumulative usage, terminal `message_stop`, and clean stream end can replace input.
The parser handles UTF-8 split boundaries, LF/CRLF/CR framing, multi-line data, comments/pings, and multiple indexed
text blocks; thinking/signatures/citations are not transcription text. It merges usage by overwriting cumulative
counters, not summing them, for the existing cost estimate. Unknown future event names are ignored; unsupported
content/deltas, malformed known events, refusals/tools, stream errors, read failure, EOF without completion,
max_tokens, empty output, or cancel/timeout leave the original intact. A started stream failure is not automatically
retried or continued. HTTP pre-stream/network retry behavior remains as before. OpenRouter/DeepInfra remain JSON
completion calls with their existing finish checks, not Anthropic SSE.

Cancel / **+30s** remain active during stream reads. The existing two-minute deadline still applies; extend it for
slow/long generations or a timeout will safely retain the original. Native limits can still be exhausted and the
provider can still reject unsupported allowances. No automatic chunking, continuation or extra paid requests.
Protocol references: [Anthropic streaming](https://docs.anthropic.com/en/api/messages-streaming), and the official
`anthropics/anthropic-sdk-typescript` `src/core/streaming.ts`, `src/lib/MessageStream.ts`, and
`src/resources/messages/messages.ts` (SSE events, block deltas, cumulative usage and terminal message_stop).

Conversation context is whole by default (user/assistant prose only, no tools). Selection Refine sends the same
chat history beside its separately bounded draft blocks. Explicit Budgeted history retains the UTF-8-safe
partial-oldest-block crop; the transcription target never shares that budget: a selection is sent whole,
or the entire box if no selection.

### Recovery (not just the clipboard)

**↩ originals** opens full, read-only previews and restores recent originals into the box. Before each Refine
request and Send submission, the full **untrimmed box** is saved and read back from browser-local storage. This
also preserves unselected text when refining a selection. Clipboard before/after remains supplemental; denied
clipboard access is shown, but does not jeopardize recovery, and a clipboard write the browser never settles
gives up after 2.5 s, so it can never hold Refine (or the read-only editor) open. Restoring over a nonempty box first saves that box
too. Restore is disabled operationally while Send/Refine runs. Draft storage failures are visible.

History retains **up to 10 distinct originals / 2 MiB of serialized UTF-16 data**, whichever is smaller, newest
first. Re-saving identical text promotes it without a duplicate. Oldest entries are evicted only by a successful
atomic storage write. Quota, unavailable/corrupt storage, failed verification, or a single original too large for
the recovery bound **blocks Refine/Send before the request**, with text unchanged; no prefix/tail-only backup.
Save an oversized original externally and work in smaller pieces. Settings/API keys are not copied into history.

This is browser-profile/origin-local recovery, **not an archive or server backup**: `localhost:9911` and
`127.0.0.1:9911` have separate storage, browser-data clearing/private-mode shutdown can remove it, and repeated
work evicts older entries. Alternating same-profile pane tabs retain each other's journal references, and an old
Send acknowledgement cannot erase another tab's changed stored draft; originals are still bounded, not a
transactional multi-tab archive or synchronized unsent edits. Copy/export important
brain dumps to a file. Unsaved dictation before any Refine/Send still depends on the ordinary debounced draft save.
No earlier lost originals can be reconstructed by this update.

### Send integrity and evidence boundaries

The textarea has no `maxlength`. Send intentionally trims **boundary whitespace only**; internal newlines,
Unicode and the entire body/tail pass via `JSON.stringify` to `/api/send`, server `Content-Length` read, and the
OpenCode JSON text prompt. The old `/oc/api/session/:id/prompt` proxy forwards the entire raw body too. Neither
pane route imposes a character limit or slices text. Offline tests exercise 25K/200K ASCII and long Unicode,
both the app and actual ephemeral HTTP pane handler → stub upstream, including queued Send and legacy proxy.

Version-matched **OpenCode 2.0.24 source review** (not a live provider proof): `server/src/handlers/session.ts`
forwards `ctx.payload.text`; protocol/schema prompt input is `Schema.String` with no length cap; `SessionPrompt.prepare`
clones the full text through the prompt hook; durable inbox/projector retain the payload; runner `to-llm-message.ts`
uses `Message.text(message.text)` without slicing. OpenAI Responses lowering (`ai/src/protocols/open-responses.ts`)
also uses `input_text: part.text` whole. Its optional provider `truncation=auto` setting is a separate history-drop
feature, not selected by the pane's text-only prompt; this review is not proof of all existing provider settings.
`model-request.ts` fits the **output** allowance to context room
(catalog output, capped at 256K; fallback 32K, floor 1024), not a crop of user text. With automatic compaction off,
source refuses automatic/overflow compaction; provider context overflow is an error, not intentional silent input
cropping. Manual compaction/history boundaries and third-party hooks are separate mechanisms. Framework dependency
body limits and all downstream provider adapters were not runtime-tested here; no live prompts were submitted.

A Refine limit stop previously could silently replace the original; that is a confirmed code defect, **not proof
of the cause of any particular missing brain dump** without its original/provider response. CodeNomad rendering
clipping, stored-message integrity, model context windows, and explicit saved tool-output stubs are distinct.
These tests prove the offline pane transport, not what a dictation tool typed or a provider saw in a past incident.

**Activate this safety update:** reload the pane page after saving any unsaved text externally. Only static assets
changed; no pane/OpenCode/proxy server restart or extension reload is required for these changes.

## Read Aloud (🔊 on a conversation turn)

The TypingMind widget's Read Aloud, rebuilt for the conversation viewer: a turn is **rewritten for listening**, saved,
and read by ElevenLabs in a pop-over player.

- **Where:** 🔊 just before the ⧉ copy button on every user and assistant message (shown on hover, like the copy; not on
  tool rows or a message still streaming), on the desktop viewer for both backends, and on each thinking block's header
  there; on the phone `/m`, beside the message's Copy (Manager chats and viewed sessions; no thinking button).
- **The pop-over** opens at once over the conversation: a status line (looking for a saved reading · reading the
  conversation before this message · rewriting · synthesizing part N of M · playing), the elapsed time, then the spoken
  text in bordered **paragraph-sized tap blocks**, with real `##`/`###` section headings (the playing block highlighted),
  and the player: ⏮ back to the
  beginning, **−10s / +10s**, ▶/⏸, one seek bar over the whole text, speed 0.75–2× (a native select, live from the moment the pop-over opens and
  applied to the playing audio at once; default `playback_rate`, 1.35×; a choice is kept in this browser as
  `pane_read_aloud_rate_v1`). **−10s / +10s** (and ←/→) move 10 s of the audio itself, whatever the speed, across
  part boundaries; taps add up (two taps = 20 s, counted from the pending target), paused stays paused, before the
  start is 0 and past the end finishes; forward into a part not synthesized yet waits for it while the current one
  plays on. A long turn's text and audio arrive **pass by pass** (below) and play while the rest is rewritten: the line
  beneath the status says which pass is being rewritten (or why the rest stopped), the time shows `+` while more is
  coming, and playback that reaches the end of what is ready waits there and carries on by itself. ⚙ in its head opens the 🔊 Read Aloud settings below. Space, Home, ←/→, Esc. **Closing stops playback**; a rewrite still
  running carries on and is saved, and reopening the same turn attaches to it (what is already rewritten plays at once). ↻ Regenerate (a new rewrite and new
  audio), Cancel (while rewriting: the passes left are not sent; what already plays stays), Read without the conversation (offered when the context read fails), Copy text. On a
  phone-sized screen the pop-over is a full-screen sheet.
- **Tap a block to start there:** playback of the old block stops immediately. Ready audio plays at once; an
  unsynthesized block is requested directly, ahead of the intervening parts, even while an earlier prefetch is still
  in flight. The most recent tap wins late replies and errors. Each block is one synthesis part, so its start is exact,
  not an estimated time inside another part; only its next neighbour is prefetched while it plays. A pass not yet
  rewritten has no blocks to show: the progress line says more blocks become tappable when it arrives, and they append
  as soon as it is saved. Taps never start another rewrite. Targets are at least 44 px high, support Enter/Space, and
  leave scrolling, press-and-hold and text selection alone. Browsing the blocks stops playback's automatic scrolling;
  tapping a block or ⏮ resumes it. Existing saved readings keep their old, possibly coarser parts; **↻ Regenerate**
  gives them the new blocks and headings.
- **The rewrite is Refine's call** (`refine-core.js`: provider, model, key, thinking, output cap, retry, Anthropic prompt
  caching under its own cache key `read-aloud:<backend>:<conversation>`), with the desktop editor's own Refine settings
  (`opcRefineSettings` in `app.js`; the phone uses its own) and the same whole/budgeted history choice. **Context:** the
  conversation's prose **before** the clicked message (`GET /api/read-aloud/context`: Refine's own context readers with
  a `before` cut: `ClaudeReader.context`, `ClaudeFacade.context`, `OpenCodeManagers.history`), for any Claude main path
  (Managers, workers, quick workers, stored history) and any OpenCode session; a subagent's view has none and says so.
  No dictionary or draft blocks.
- **A long turn is rewritten in passes** (`read-aloud.js` `splitPasses`, `rewrite`), so the voice starts after the first
  pass instead of the whole rewrite. Up to 1,200 characters it is one pass, exactly the single rewrite. Longer: about 700
  characters first, then about 2,000, doubling up to 6,000, cut between Markdown blocks (never inside a fenced code
  block, a table or a list item; a heading or a line ending in a colon stays with what follows; an over-long unit is cut
  only between list items or at a sentence end; a short tail joins the pass before it). Each pass is one provider call:
  the same chat history first (its cache breakpoint, so every later pass reads it from the cache), then **what was
  already spoken** (`<spoken-before>`: the earlier passes' rewrites), **the part to rewrite** (`<message>`) and **the rest
  of the message, raw** (`<message-after>`), the way Refine frames a selection with its draft before and after. Each
  pass is saved the moment it lands and goes straight into the player's queue; the next pass is sent at once (not when
  the audio ends). A failure names the pass and keeps what was saved and plays.
- **Prompt caching per provider** (measured 2026-10-26): **Anthropic direct** puts the one-hour breakpoints on the system
  prompt and the history end, as Refine does; a later pass reads the whole history from the cache (36,595 of 39,051
  input tokens on a 100 KB conversation) and writes nothing. **OpenRouter** caches a Claude model only at Anthropic's own
  breakpoints, which Refine's plain string prompt does not carry (0 cached tokens on a repeat); Read Aloud's calls
  (`routerCache` in `refine-core.js` `callOnce`) send the same one-hour breakpoints in content parts there, so a
  repeat read 10,459 of 10,828 tokens from the cache ($0.0038 instead of $0.0435). Refine's own OpenRouter call is
  unchanged. **DeepInfra** caches a repeated prefix by itself (GLM-5.2: 7,424 of 7,547 tokens on a repeat). Read Aloud
  records the routers' cache counts with its cost; Refine's cost line shows none for routers, as before.
- **The prompt** has Refine's four-part structure and anti-injection order (`<chat-history>` · the `<message>` with its
  speaker · the final fence) and lives beside Refine's in `static/refine-defaults.js` (`READ_ALOUD_*`,
  `READ_ALOUD_PARTS`). Edit it in ⚙ Refine settings → 📜 Prompt, the four **Read aloud ·** parts; overrides are saved as
  `refine_read_aloud_*` (imported/exported with the other `refine_*` settings), and Restore default deletes one. It turns
  handles, hashes, paths, code, tables, lists and emoji into spoken English while keeping every point, the order and the
  speaker's voice. **Section headings are strongly requested**, even when the original has none: short `##` topic
  labels, `###` only for useful subsections, not a heading over every paragraph or a sentence or two. The player displays
  them as headings and voices each label once as a short lead-in, without the hash marks or the word “heading”; other
  Markdown is stripped before synthesis too. A pass's two extra blocks have fixed wrappers (`READ_ALOUD_SPOKEN_BEFORE_PREAMBLE`,
  `READ_ALOUD_MESSAGE_AFTER_PREAMBLE`; not editable parts, so they stay labelled under older overrides), and the default
  system prompt explains parts and seams (continue the active section and its heading hierarchy, nothing said twice,
  no restarted headings or wrap-up before the last pass). Identical active headings echoed at a pass's start are removed
  without editing the saved prefix or prose. Saved prompt overrides still win: **⚙ → Restore Read Aloud prompt defaults**
  in the pop-over (also in phone Refine setup and desktop settings) removes only this browser's four listening-prompt
  overrides, not Refine's. Then **↻ Regenerate**; a page reload alone does not update an overridden prompt or saved reading.
  The desktop Prompt editor can still restore/edit each part separately.
- **Saved results:** on the pane server, `~/.local/state/opencode-companion/read-aloud/` (`items/<id>.json`: the text,
  its parts, the voice and how it was made; `audio/<id>/<n>.mp3`), keyed by backend + conversation + message + part
  (`text` or `thinking:N`). A long turn's reading is saved pass by pass (`POST` with `run`, `segment`, `done`: an
  incomplete item that grows; each pass gets its own parts, the first one short, and parts never move, so a replay reuses
  every MP3); one whose rewrite stopped part-way (a closed page, a failure, Cancel) is rewritten again on the next click,
  and a pass from a run that was replaced meanwhile (another tab) is refused. A second click replays at once; a message that changed since is flagged. Bounded: least
  recently used items lose their audio past `cache_max_mb` (512 MB), and at most 3,000 items are kept; delete the folder
  to clear it.
- **ElevenLabs is called by the server only** (`read_aloud.py`), with the widget's request: Multilingual v2,
  stability 0.5, similarity 0.75, speed 1.2. New parts never merge paragraphs; a long paragraph splits at sentence/word
  boundaries up to 600 characters (or `chunk_chars` if smaller), with its headings attached to its first piece. A short
  first part (`first_chunk_chars`) is retained for every pass, including when paragraph separation would otherwise
  remove that fast opening cut. Display headings and markup-free speech are saved separately; existing items are not
  migrated or rechunked. The next part synthesizes while one plays, network/429/5xx retried with
  backoff. Neighbouring text goes along as `previous_text`/`next_text` (not billed: the `character-cost` header counts
  the part alone). **Settings:** `~/.config/opencode-companion/elevenlabs.json` (mode 600, never in the repository):
  `api_key`, `voice_id`, `voice_name`, `model_id`, `voice_settings`, `chunk_chars` (1000), `first_chunk_chars` (350),
  `continuity_chars` (500), `playback_rate` (1.35), `output_format` (`mp3_44100_128`), `cache_max_mb`. It is re-read when
  it changes. Without it the rewrite is still saved and the player says how to set it up.
- **🔊 Read Aloud settings** (one section, `read-aloud.js` `settingsPanel`): on the phone in **Refine setup**, on the
  desktop in **⚙ Refine settings** under 📜 Prompt, and behind **⚙** in the pop-over. Stored on the pane server, so the
  phone and the desktop show the same values; nothing is read until the section is opened. **Rewrite model:** *Same as
  Refine* (the default), or a provider's model from Refine's model lists in this browser (or *Other … model id*), saved
  in `~/.config/opencode-companion/read-aloud.json`; it uses that provider's Refine API key in this browser, while
  history stays Refine's. **Rewrite thinking:** *Same as Refine* (the default), or one of Refine's own levels (low,
  medium, high, xhigh, max), chosen independently of the model and saved in the same file; each provider receives it
  exactly as Refine sends that level, and the rewrite's time limit scales with it. **ElevenLabs API key:** paste to set or replace it; it is written to
  `elevenlabs.json` (mode 600) and only its last four characters come back; **Test** makes one subscription read (no
  characters billed) and says OK with the usage, or why not. **Voice:** your account's voices, read only on **↻ Load
  voices** (with the account's speech models; cached per key in the state folder's `elevenlabs-catalog.json`).
  **Speech model:** the account's TTS models, else a fixed list; default `eleven_multilingual_v2`. A new voice or model
  applies to readings made from then on (a saved reading keeps the voice it was made with; ↻ Regenerate re-reads it).
  ElevenLabs is called only on Test, ↻ Load voices and playback.
- **Routes** (the `/m` origin guard): `GET /api/read-aloud/context`, `GET`/`POST /api/read-aloud/item`,
  `POST /api/read-aloud/audio` (one part's MP3), `GET`/`POST /api/read-aloud/settings`, `POST
  /api/read-aloud/settings/test` and `/settings/catalog`. A pane restart waits for the POSTs and for the page's `read-aloud`
  activity lease while a rewrite runs.
- **Measured** (2026-10-26, a 2,946-character Manager reply with two Anchor Point handles, 20 KB of context, Opus 5.5
  at medium): rewrite 9.0 s (~$0.10, cold cache); its 322-character first part synthesized in 4.5 s (a 786-character
  one took 8.6 s), so the first audio about 13.5 s after the click, the next part ready long before the first ends;
  2 min 32 s of audio in all; a replay's first part from the cache in 23 ms.
- **Measured in passes** (2026-10-26, Opus 5.5 at medium, real turns): a 5,356-character Manager reply with 100 KB of
  context, first audio **21.7 s → 7.8 s** (warm cache; 11.7 s cold), 3 passes all rewritten by 18 s, before the first
  part had finished playing, every later part ready 3.5 s or more before it was due; a 12,557-character message of Dan's
  with 54 KB of context, cold, **30.7 s → 9.7 s**, 4 passes done by 31 s. First audio is now the first pass (about 3.3 s
  warm) plus the first part's synthesis (4.3–6.1 s). Cost: a few cents more per long turn (each pass re-reads the cached
  history and sees the message once more): $0.090 vs $0.087 warm, $0.32 vs $0.25 cold.
- **Activation:** the guarded pane restart (`start.sh restart`, new routes), then a page reload, desktop and phone.

## Running it

    bash transcription-pane/start.sh            # start (WSL, 127.0.0.1:9911)
    bash transcription-pane/start.sh stop|status
    bash transcription-pane/start.sh restart [--max SECONDS]

**`start.sh restart` is how the pane server is restarted.** It waits for a quiet moment
(`tools/pane_quiet.py`): no Send, Refine, control, reconnect or launch in flight in the pane
(`/api/pane-quiet`), and every live Manager's reply finished (host records: turn closed, no Send
unanswered; a Manager only waiting on a background task is quiet), two checks a second apart. Then it stops,
starts and checks that the page answers. Open pane tabs resync by themselves; reload the page only for
browser-side changes. From a Manager's own turn, run it in the background: it restarts once that turn ends.
`--max` gives up (exit 3, nothing restarted). A pane server older than the quiet check is waited on only
for the Managers' replies, once.

`server.py` serves the page, proxies `/oc/*` to the OpenCode service with its Basic auth (the password never
reaches the page; base URL and password come from `scripts/oc_client.py`), and relays the watcher's reports
(`/api/active-session`).
Normal Send goes through pane-only `/api/send`, which performs the guarded thinking bridge and then submits
OpenCode's text prompt. The watcher origin is restricted to the two loopback CodeNomad URLs; the Send bridge
accepts same-origin pane JSON only and does not expose a general browser-control interface or credentials.

**Activate this bridge after an update:** reload the unpacked extension in `chrome://extensions`, reload
CodeNomad, restart the pane server with `start.sh restart` (it waits until no Send/Refine is in flight), and
reload the pane. No OpenCode/proxy restart is needed.

### NEXUS document view bridge

The pane's `/api/glimpse/render` reads through the existing NEXUS daemon render funnels,
without a model, conversation, SDK installation or daemon restart. POST JSON
`{"kind":"parcel","value":{"node":"<handle>"}}` for a node, or use a group,
subtree, Dock, tags/symbols or saved formal render specification. `kind:"estate"`
uses the daemon's estate output, including its headings. Optional view overrides are
`headings_only`, `sort_by`, `sort_direction`, `recent`, `recent_by`, `ancestor_notes` and
`include_archives` (a boolean, forwarded to `glimpse.run`, `glimpse.estate` and `anchor.dock.render`);
direction requires explicit `created`/`modified` sorting. A parcel may also ask
`include_rows: true` (never an estate): the reply then adds the daemon's own `rows` (its modal
row authority's canonical rows: `uuid`, `ancestor_path`, `occurrence`, `depth`, `name`, times)
and the resolved `render_spec`, forwarded unchanged beside the same Markdown; `bytes` still counts
the Markdown only, and the 16 MiB wire limit covers the whole reply. A daemon from before
`include_rows` refuses it with a structured `forbidden_parameter` naming it: the bridge renders once
more without only that option and answers document-only (no rows), with no remembered capability,
so the daemon's upgrade shows on the next render. Other errors (a stale row focus included) stay
errors. Returned Markdown is unchanged; the daemon's own status lines on that envelope (the
⚠️ ARCHIVED NODE banner, each `resolved: … (corrected from …)` handle correction, the *📦 N archived
subtree(s) … left out* footer) come back as `notices`, never spliced into the Markdown (a plain
Markdown reply already carries them in its text), and the footer's count as `archivesLeftOut`. A daemon fault's text is its own `message`, never a
dict's repr. `renderedAt` is render completion time, **not** the age of the daemon's cache export.
`GET /api/glimpse/catalog` returns groups/favorite Docks in the daemon's order, keeping partial
results if an inventory fails; each Dock row carries its `depth` among the listed Docks
(`dock_depths`: `dock.list` rows come depth-first with their ancestor paths, so a Dock nests under the
nearest earlier Dock whose path prefixes its own, as the extension's Dock tree nests them) and its
`favorite` / `local` marks (`dock.list`'s `is_favorite` / `is_local`). `POST /api/glimpse/docks` with
`{"all": bool, "local": bool}` re-reads the Docks alone: `all` is `dock.list favorites_only: false`,
`local` adds `include_local: true`, `{}` is exactly the catalog's favorites call; a failure comes back
as `errors` beside an empty list. `POST /api/glimpse/ancestors` with `{"handle": "…", "uuids":
"leaf"|"every-level"|"none"}` (default `leaf`) returns `node.ancestors`' Markdown (the F3 chain) as
`markdown`. Manual handles remain usable with an empty catalog. Only an explicit
`POST /api/glimpse/cache-refresh` with `{}` refreshes that cache; attempts (including
failed ones) are limited to one per 30 seconds. Ordinary Render/Refresh never does it, and View
has no button for it (refresh the cache in Workflowy, or ask an agent).

Keep a Workflowy tab with the NEXUS extension open on the PC for groups, Docks and
symbols. Without it the diagnostic says: “Open a Workflowy tab with the NEXUS extension
on the PC; groups/Docks/symbols need its settings. Raw node/subtree works without it.”
Raw node/subtree reads have no tab-health or catalog prerequisite. Connection URL and
bearer-token lookup come from `scripts/oc_view.py`; credentials remain on the server.
These APIs, `/glimpse.html`, `/glimpse.js`, `/glimpse.css`, all `/nexus-md/` files and View's image routes
(`/files/…`, `POST /api/files/meta`, `POST /api/files/list`; below, under *View: images, the lightbox and side by
side*) use the same loopback/opt-in Serve origin policy described below, with no cross-origin preflight.

`/nexus-md/` serves the extension's **actual files**, not copies or the pane chat's marked18:
`glimpse-md-render.js/.css`, marked15, the KaTeX initializer/overrides and KaTeX JS/CSS/fonts, and beside them the
image lightbox (`glimpse-lightbox.js/.css` and the vendored `photoswipe/photoswipe.umd.min.js/.css`).
At startup, `~/.config/opencode-companion/pane-glimpse.json` may select their checkout:

```json
{"nexus_checkout":"/mnt/e/__daniel347x/__Dan_Root/__repos/workflowy-nexus-mcp-tools-wt-paneviews"}
```

That is tonight's B1 worktree setting; merging NEXUS first is unnecessary. **Absent file**
defaults to `/mnt/e/__daniel347x/__Dan_Root/__repos/workflowy-nexus-mcp-tools`.
Invalid configuration lets the pane start, while View's renderer files report the settings file and reason without using another checkout. Missing renderer assets name the checkout/file and say
“set nexus_checkout in ~/.config/opencode-companion/pane-glimpse.json, then restart pane”.
There is no local renderer or marked18 fallback. After NEXUS is merged, the setting can
point to main (or be removed) before another pane restart.

**Activation:** set this local, uncommitted JSON setting, then run
`bash transcription-pane/start.sh restart` from the companion checkout, **in the background**
from an active Manager turn so the existing quiet gate can proceed when that turn ends.
Reload the View page after its browser files are available. This restarts only the pane;
do not restart the daemon, OpenCode, Claude host or extension for this bridge.

### View: NEXUS documents on the desktop and the phone

**View** shows a Dock, slice group, node or saved render specification exactly as the Workflowy
extension's Markdown dock does, by the same code: `static/glimpse.html` loads the extension's own
`/nexus-md/` files (marked 15, KaTeX and its initializer, `glimpse-md-render.js/.css`) and paints
`GlimpseMd.toHtml`'s HTML straight into the page (raw note HTML stays live; no sanitizer, no frame
around the document). `glimpse.js` holds only the request, the returned Markdown with the daemon's
rows and render spec, and the view/find state; it reads only the bridge routes above. No key, setup
or model call.
On the desktop it opens in place, never in a new tab: in Claude mode as the conversation column's **📄 View** pane (the
**💬 Conversation | 📄 View** switch at the column's top, or the header's **📄 View**), in OpenCode mode as a sheet over the
pane with **Close** (both below, under *View history*); dictation stays put. `http://127.0.0.1:9911/glimpse.html` still
opens it on its own, and on the phone it is `/m`'s **View** tab (below).

- **Layout:** at the very top, always, the two history rows (below, under *View history*); under them one thin
  **Controls** bar (a hairline with ▸ Controls) that folds everything else above the document: **Choose**
  (itself still foldable), the toolbar (Rendered | Source, Copy MD, Refresh, Find; on the page opened on its own
  also the ☀) and the as-of line. Open or closed is remembered on the device (`localStorage`
  `pane_view_controls_open_v1:‹phone|desktop|page|framed›`); it stands open by itself while nothing is shown yet,
  a new root chosen in Choose (a picker row, a pasted handle, a spec) folds it, and a root from outside View never
  opens it. Outside it, never folded: *Compact view* with **Full**, errors, the daemon's notices, the archives
  line, the catalog's errors and short notes such as *Copied*. Phone and desktop share this; the desktop is denser.
- **Dividers:** two horizontal grips (a hairline with an iOS-style pill, 26 px tall for a thumb): one under the open
  Controls (how tall they may get; past that they scroll inside themselves, 62% of the page until dragged), one between
  the row list and the document. Drag either with a thumb or a mouse (pointer capture, `touch-action: none` on the
  grip), or focus it and press Up/Down (5% of the page) or Home/End. Each sets a maximum height: a drag stops at the
  content's own height and always leaves the document 64 px, so a short list or a folded chooser takes only its own
  height. Remembered per device as a fraction of the page height (`localStorage` `pane_view_splits_v1:‹embed›`), so a
  rotation keeps the proportion; a grip shows only beside what it sizes (open Controls, an unfolded row list).
- **Choose:** two pickers, as the Workflowy Chrome extension shows them (`static/view-pickers.js`,
  from one catalog read when the page opens; one open at a time). **Favorite Docks**: every favorite
  Dock nested under its parent Docks with a faint guide per level, its `[ap:…]` dimmed after the name.
  **Slice groups**: the extension's pipe-delimited hierarchy by its own code (`parseSliceGroupName`,
  `buildNestedDisplayItems`, copied verbatim; `tests/view-pickers.test.cjs` runs the extension's copy
  beside it): `A | B | leaf` nests under the sections A › B, a trailing `[n]` orders a group among its
  siblings, a leading `[n]` names a dummy expander (a section kept even when empty, ordered by its `[n]`),
  unindexed entries last and alphabetical; a section without an expander of its own still shows, a
  duplicate expander shows empty with the extension's warning. Sections start closed and stay as left
  on this device; a group shows its short name. Both lists use one compact row per entry, never
  wrapped (a long name scrolls sideways), the chooser's full width; **tap a row** to render it (the
  current one is marked). **Full screen:** **⚓ Docks** and **Groups** on the Choose line (there even while Choose is
  folded) and **⤢** on each picker open the same list over the whole frame (the whole phone screen): big rows wrapping
  to two lines, scrolled to the current one, a **Filter by name** box (a Dock keeps the Docks it nests under, dimmed;
  groups open every section), and a tap renders and closes it (**Close**, the backdrop or Escape close it too). The
  Docks there have two switches, as `anchor.dock.list` has them: **All Docks** (not just ⭐ favorites:
  `favorites_only: false`) and **Local Docks Ⓛ** (`include_local`); a list with them marks ⭐ and Ⓛ after each name,
  the compact picker follows (its title says *All + local Docks*), and the choice is remembered per device
  (`pane_view_dock_list_v1:‹embed›`), read again after the catalog on the next load. A failed read keeps the list on
  screen and says so. Slice groups have **Open every section** (the extension's expand-all). **Paste a handle** reads it as **Node** (its note), **Subtree** or **Dock**;
  **Spec** sends a parcel or formal specification (JSON or a group name) as **Parcel**, or a named
  estate / estate payload as **Estate**, as chosen. An odd spec goes to the pane as typed, and its
  refusal is shown. After a render the chooser folds to one line naming the document.
- **Compact on a phone:** in `/m` or on a narrow screen a Dock, group, subtree or parcel spec
  starts with **Headings only** (the daemon's own option), labelled *Compact view* with **Full**,
  which renders the same scope without headings-only or a recent window. A node or symbols render
  their body. An estate (a spec sent as **Estate**, or a named estate opened from its handle, below) sends
  `headings_only` only when it is ticked, on the phone too, so each parcel keeps its own read (`{node}` its body,
  `{dock}` headings); the as-of line says *each parcel's own read*. A row tap on the phone's default compact view
  also shows the note's full text.
  The desktop opens full. A ticked/unticked **Headings only** holds for later choices.
  **Options** (Headings only; **Include archives 📦**; order: **Saved order** = the scope's own, a slice group's saved sort else the
  outline, **Document order** = the outline whatever was saved, or By created / By modified with a
  direction; recent) re-render the current document at once. **Include archives** (off by default; sent only when on)
  renders the current request again with `include_archives: true` (a tapped row stays that row) and holds for later
  choices; the export archives' subtrees then appear read-only, marked 📦. **Full** also drops a pasted
  specification's own `since` window (the history keeps the spec as pasted); a pasted `content_format:
  'raw_text'` specification is painted as plain text, as the dock paints it.
- **Rendered | Source**, **Find in source** (the dock's grammar: words, "quoted phrases", `*`/`?`
  globs; ‹ › wrap; Enter / Shift+Enter; typing in Rendered switches to Source), **Copy MD** (the whole
  Markdown as returned, whatever is mounted) and **Refresh** (the same request again) never touch
  Workflowy's cache.
- The as-of line (in Controls) names the document and says *Rendered at ‹time› · daemon cache snapshot*
  with the applied options and its size: the render time, not the cache's age. The daemon's status lines
  for the document (an archived node, a corrected handle) show above the document; its archives-left-out footer
  becomes one plain line, *📦 N archived subtrees were left out* with **Include them** (one tap turns Include archives
  on), or *📦 Archives included* with **Leave them out** (from the reply's `archivesLeftOut`, its notices, or the end
  of a plain Markdown reply such as an estate's). A
  failed render keeps the previous document, labelled *Last successful*, with the error in plain words
  (and *Still showing the last successful: …*), in a band that scrolls inside a fifth of the screen.
- **Rows** (above the document): the dock's row list, its table of contents, painted by the
  extension's own `GlimpseMd.renderRowList` from the daemon's rows: the same names, indent, guides,
  order, hover times and day lines (under a date sort) as the dock. **Tap a row** to read that
  occurrence alone: the held render spec goes back to the daemon with its focus set to the row;
  on the phone's default compact view it also sets `headings_only:false` to read the body. A hand-ticked
  **Headings only** or a wide page keeps the spec's setting. A repeated node's other occurrence is its
  own row, and the whole list returns with it, the tapped
  row marked. Options and Full then apply to the chosen document again; Refresh repeats the focused
  view. The region folds to one line; it scrolls inside a band (a fifth of a phone's screen, a third of the page
  elsewhere, until the divider under it is dragged) so the document stays readable. Ctrl/Cmd/Shift multi-row
  selection is not wired here: a modified tap reads that one row like a plain tap. Past 200 rows it shows the first 200 with *Showing 200 of N rows* and **Show all** (and
  back), display only: every row is painted and held, nothing is fetched. The phone shows no
  📘/bullet/halo icons: those come from Workflowy's live page, which the daemon's rows do not carry.
  An estate has no list; a daemon or renderer checkout from before the row list shows the document
  alone, as before.
- **Where it lives:** while one node is on screen (a Node, Subtree or Dock handle, a picked Dock, a tapped row, a
  formal spec's raw root; never a slice group, tag/symbol selection or estate) a **where** button (a small tree icon)
  sits between the root card and ⤢. It opens a pop-up with that node's ancestors, `node.ancestors`' own text (the F3
  chain: H1 = the Workflowy root, one heading per level, the node as the last • line, ⚓ ⭐ Ⓛ ✓ 🗺️ 📄 markers and the
  size metrics), painted by the shared renderer; **Leaf UUID** (F3, the default) or **Every level** (a UUID under each
  ancestor), **Copy** (the Markdown), **Close** / backdrop / Escape. A handle in it opens in View as a new entry and
  closes the pop-up. One read per chain and mode.
- A document past 200 top-level blocks mounts the first 200 with *Showing 200 of N blocks* and
  **Show all**, which adds the rest in batches; Source holds every byte; tables, code and formulas
  scroll inside the document, never the page.
- Groups, Docks and symbols need a Workflowy tab with the NEXUS extension open on the PC (the
  catalog and the error say so); a pasted Node or Subtree works without one. A missing renderer file
  is named at the top with the `pane-glimpse.json` setting and the pane's own diagnostic; there is
  no fallback renderer.
- **Activation:** a page reload once the pane serves the landed files; `pane-glimpse.json` must
  point at a NEXUS checkout that has `glimpse-md-render.js` (tonight the B1 worktree, a local
  setting only). After Dan merges NEXUS, point it back at main (or remove it) and restart the pane.
  The row list needs a daemon with `include_rows` (Dan merges the NEXUS branch and restarts the
  Workflowy MCP daemon; the extension reload is for the dock itself) and a pane serving this page
  (the guarded pane restart, then a page reload); nothing to configure. Until the daemon is restarted,
  View is the document alone, without an error.
  Evidence so far is offline Chrome fixtures; the iPhone and the live daemon are Dan's first try.

### View: images, the lightbox and side by side

A note can show Dan images: standard Markdown image lines with the file's absolute path, a heading per group
(`## Opus` then `![card · dark](/mnt/e/…/card-dark.png)`, one line per file). View finds them in whatever it renders
(a node, a Dock, a group, an estate, a handle tapped in chat). The lightbox is the extension's
`workflowy-glimpse-cache/glimpse-lightbox.js/.css` (`GlimpseLightbox`, host-neutral, over the vendored PhotoSwipe
5.4.4), served at `/nexus-md/` and not in the extension's manifest yet; `glimpse.js` supplies the URLs, sizes and
listings.

- **Map before mount:** in Rendered, an image whose source is a local path (`/mnt/e/…`, `E:/…` with forward slashes,
  `file://…`) points at the pane's `/files/…` before the HTML goes live (an inert template parse, so nothing is ever
  requested at the raw path); `http(s):`, `data:` and `blob:` stay. Write a space as `%20`, never the angle-bracket
  form (the daemon's render drops it). Source and Copy MD keep the exact Markdown.
- **Contact sheet and chip:** a paragraph of two or more images becomes a grid of captioned tiles (two across a phone
  held upright, about 220 px wide on the desktop); a single image or one inside a sentence stays inline; a missing file
  is a dim tile with its path. The caption is the alt text (a `"title"` is a second line; the file name shows small).
  **Every heading starts a group**, labelled by the heading up to its first ` · ` (a leading `[ap:…]` and trailing
  `#tags` dropped); images before any heading belong to the document's title. One line outside Controls says
  **🖼️ 145 images · 3 groups**: a tap opens the lightbox at the first image, a tap on any image or tile opens it there.
  The whole document is collected, past the 200-block bound too. One `POST /api/files/meta` per document gives the
  sizes (every path again on Refresh, so a regenerated screenshot shows). A compact (headings-only) render has no notes,
  so no images: **Full** brings them.
- **A gallery opens straight away:** a new document that is nothing but images (at least one; besides headings, rules
  and `render_scope` lines, at most one paragraph of up to 400 characters per heading, plus one) opens straight into
  the lightbox once its sizes are in (or after 1.5 s); **Back** lands on its contact sheet with the last image seen in
  view. Refresh or the archives switch on a gallery already shown stays on the sheet. An estate opens straight away
  only when every entry holds images and none failed.
- **The lightbox:** black, the image fitted clear of the chrome; the whole document is one sequence (swiping past a
  group's last image goes on into the next; it stops at the very last). Top: the position within the group (`12 / 42`),
  the group chips with counts (a tap jumps there), **1:1**, **⛶** where the Fullscreen API is allowed (the View frames
  carry `allow="fullscreen"`), zoom on a mouse screen, **✕**. Foot: caption, title line, the file name with the whole
  heading, and the group's filmstrip (tap to jump). A missing file is its own slide saying so with its path; neighbours
  (±2) preload. **Phone:** swipe; pinch to about 4×; double-tap to zoom at that point and back; drag to pan while
  zoomed; drag down at fit to close; a tap hides or shows the chrome. On `/m` the View frame goes edge to edge (top bar
  and tabs away, `opcViewImmersive`). **Desktop keys:** ← →; PageUp/PageDown (↑ ↓ at fit) the previous/next group;
  Home/End the group's first/last; + − zoom, 0 fit, 1 actual pixels; Space hides the chrome; F fullscreen; Esc. A click
  on the image zooms (PhotoSwipe's own), a click beside it closes, the wheel zooms at the pointer. **Back** closes it
  everywhere (it pushes one history entry). Glare brightens its captions.
- **Live folders and globs:** an image line whose path is a folder (ending in `/`) or a glob (`…/shots/*.png`; `*` and
  `?` in any segment, `**` reaches into subfolders) is listed when View renders it: one `POST /api/files/list` per
  paint, every live line again on a fresh reply or Refresh (a folder an agent just added to shows up). Each listed file
  is one image captioned by its file name without the extension, `-`/`_` read as spaces (`card-dark.png` → *card
  dark*). A live line **with** alt text is a group of its own named by it (inside the heading around it, until the next
  heading); one **without** alt joins the heading's group; fixed and live lines mix. Order: natural name order
  (`shot-2` before `shot-10`, case ignored; a glob by whole path). A folder lists its own files only, image files only
  (png, jpg/jpeg/jfif, gif, webp, avif, apng, svg, bmp, ico, heic/heif, tif/tiff), dot-files skipped. An empty folder
  or a glob with no match is a labelled tile (*📁 no images in this folder · path*, *🔎 no image matches · pattern*),
  a path it cannot list says why (*⚠ no such folder: …*); past 4,096 files the line under the chip says how many show.
  A live gallery opens straight away too, once its lines are listed.
- **Side by side (desktop only):** **⧉ Side by side** beside the chip, on a wide page (never on a phone or in `/m`'s
  frame) for a document of two groups or more, live folders and estates included: any A/B or A/B/C comparison
  (bake-offs, before/after screenshots). It is its own simple viewer over the pane (`GlimpseLightbox.compare`), two or
  three panels, the first two or three groups at first; the bar's group chips swap one in or out (lit and numbered when
  shown; three at most, another then takes the right-most panel's place; one always stays). Each panel: its group, its
  own position (`7 / 42`) between nudge **‹ ›** with the nudge beside it (`+1`), the image fitted, its caption, title
  and file name. **← →** (or the bar's ‹ ›) step every panel together by position (image 7 of each), Home/End the first
  and last step; a panel past its group's last image says so; a panel's own nudge moves it alone for sets that do not
  line up one for one, and **A** (⟲ Line up) drops the nudges. One shared zoom: + − 0 (fit) 1 (actual pixels), the
  wheel at the pointer, a drag pans every panel together, so same-size screenshots stay pixel-aligned. A click on a
  panel's image opens it in the ordinary lightbox over the panels (its keys then; Esc or Back return to them). Space
  hides the chrome, F fullscreen; Esc, ✕ or Back close it; a new document closes it.
- **Named estates from a tapped handle:** a handle read as **Node** (tapped in chat, opened from outside, pasted, tapped
  in View, or from history) whose note is wholly parcel lines renders as that estate (`kind: estate` on the node's
  uuid), labelled *🏛️ Estate · ‹name› · N parcels* with **Show the node**; on the raw node, *This node is a named estate
  (its note lists N parcels)* with **Show the estate**. Parcel lines are read as the daemon's estate grammar reads them,
  one per line: blank lines are fine, a leading *↩ moved from / derived from* line is ignored, the whole note may be one
  fenced block or one JSON value (a Saved-Tabs export); every other line must be a qualified parcel (short or long
  form), `{lens}`, `{spec}`, a render_spec blob, a handle, or a slice-group name the catalog lists; a path, URI or
  settings key never is, and one other line makes it a plain node. The history entry keeps the node's name, **where**
  shows where the estate node lives, Refresh repeats what is on screen; if the daemon refuses the whole estate, View
  shows the node with that error above it. An estate of gallery nodes shows its images, chip and lightbox as one gallery
  does, all its groups in one sequence.
- **The routes** (`server.py`), behind the same guard as `/m` (`_claude_origin`: loopback same-origin, or the configured
  Serve origin with its login); no folder allowlist and no type filter, since the pane already sends prompts to Managers
  that run shells, and a list would only stop Dan's own agents from showing him a file:
  - `GET|HEAD /files/‹absolute path›` (the leading `/` dropped, each segment percent-encoded; `X:/` maps to `/mnt/x/`):
    the file streamed in 256 KiB chunks with its type, `Cache-Control: no-cache`, an mtime+size `ETag` and
    `Last-Modified`, 304 for `If-None-Match` / `If-Modified-Since`; a missing file, a folder or an unreadable path is a
    404 JSON naming it.
  - `POST /api/files/meta {"paths": […]}` (up to 4,096): one row per path, in order: the path as sent, `url`, `exists`,
    `bytes`, `mtime`, `type`, `width`/`height` read from the header (PNG, GIF, WebP, BMP, JPEG turned by its EXIF
    orientation; cached per file version), or why it cannot be shown.
  - `POST /api/files/list {"paths": […]}` (up to 256 paths, 4,096 files per call): one row per path: `path`, `kind`
    (`folder` | `glob`), `total`, `files` (meta rows), `truncated` when cut short, or `error` (*no such folder*, *not a
    folder*, *not readable*, *not a folder or a glob*, *not an absolute path*); an empty listing is `files: []`.
- **Limits:** with **Headings only** ticked by hand a tapped handle is read compact, so its note (and an estate in it)
  does not come back: **Full** brings it. A bare slice-group name counts as a parcel line only when the catalog lists it
  (a Workflowy tab on the PC). Two galleries in one estate with the same heading names show repeated chip names (the
  chip's tooltip has the whole heading). Files on another machine are copied to a local folder first. **The Markdown
  modal can learn the same images, lightbox, side by side and live expansion later** (pseudo-spec [ap:7YRA8U] §6):
  every piece of `GlimpseLightbox` takes its URLs, sizes and listings from its host; the modal would map local paths to
  `http://127.0.0.1:9911/files/…` (a loopback rule on `/files/` for workflowy.com's image requests, or the daemon's
  bytes as `blob:` URLs) and needs a listing source for live lines (this pane's `/api/files/list` under the same rule,
  or the daemon).
- **Activation:** the guarded pane restart (the routes and the `/nexus-md/` allowlist are server-side), then a View
  reload on the phone and the desktop. No daemon or extension change.

### Clickable handles: open any Anchor Point or short UUID in View

Every Workflowy handle the pane shows is clickable, on all five pages (`index.html`, `sessions.html`, `m.html`,
`m-programs.html`, `glimpse.html`): conversation prose, thinking, tool bars and opened tools (Readable, Pretty, Raw,
Diff), the sidebar and Programs, the inspector and View itself. `static/handle-links.js` does it in the browser only:
it never rewrites a renderer's DOM. Handles get a faint tint and a dotted underline (CSS Custom Highlight API,
`::highlight(opc-handle)`, brighter in glare mode), and the handle under a press is found from the text at that
point, so selection, copying a whole message, streaming repaints and the readers' selection rules are unchanged.

- **What counts:** `[ap:XXXXXX]` always; a whole lowercase UUID; any standalone 8-character lowercase-hex token,
  digits-only included (so a date like `20261022` lights up too; opening a non-handle shows View's "did not resolve").
  Not when glued to `ses_`, `cc-`, `#`, `0x`, a path, URL, `=`, `@` or `:`, nor followed by a word character, hyphen
  or `.ext`; never inside a link, a text box, uppercase hex or a 7- or 40-character SHA.
- **Desktop:** a left-click opens the handle in View, in place: the conversation column's View pane in Claude mode, the
  sheet in OpenCode mode (read as Node; View's Node / Subtree / Dock switch changes it). The click does not also open the
  tool bar or row under it;
  a drag-selection or a Ctrl/Cmd/Shift/Alt-click is left alone. **Right-click** or a **press-and-hold** (450 ms)
  opens a small menu: **Open in View** · **Copy**. The pointer turns into a hand over a handle.
- **Phone (`/m`):** a tap opens it in the View tab (from Chat or Programs; Chat and the draft stay as they are);
  inside View it renders in place. A **press-and-hold** opens the same menu; for that press only the text under the
  finger has selection and the iOS callout turned off (nothing permanent, nothing blocked on `touchstart`), so a
  scroll that starts on a handle still scrolls.
- **Limits:** decoration stops at 5,000 handles per page and skips single text nodes over 1 MiB (the gesture still
  works there); a handle split across syntax-highlight tokens is not found; browsers without the Highlight API
  (before Chrome 105 / iOS 17.2) show no tint but the gesture works.

### View history and the root widget

View keeps its history in two layers (`static/view-history.js`), named on screen **inner history** and **Recent roots**;
neither is the sidebar's **📦 History** (the archive of ended conversations).

The **inner history**, browser-style:
- **A fresh root:** any handle opened from **outside** View (a session row's socket in Programs or the sidebar, a handle
  tapped in Chat or a conversation) or chosen in View's own pickers (a favorite Dock, a slice group, a pasted handle, a
  spec) starts a fresh inner history, one entry, that root.
- **Entries:** a handle tapped **inside** the rendered document adds an entry, named by the node's full Workflowy name,
  tags and all (the reply's first heading, or the daemon's first row).
- **Moving around:** ‹ › and the inner-history sheet re-render an earlier entry from the daemon without dropping the later
  ones; only a new handle opened from there cuts them. The handle already on screen is never a second entry.
- **Storage:** up to 50 entries, the root kept, in `localStorage` under `pane_view_history_v1:‹phone|desktop|page|framed›`
  (one per page context). A reload shows the same entry again, with one render.

**Recent roots** (the outer history): the roots View showed lately, each kept with its own inner history and a note.
- **One list for the phone and the desktop**, on the pane server (`view_roots.py`, `GET|POST /api/view/roots`, the file
  `~/.local/state/opencode-companion/transcription-pane/view-roots.json`), most recently used first (shown, or dug into):
  the last 30 by default (**Keep the last** 10/20/30/50/100 at the list's foot); a root with a note stays past that (up to
  100 more). Each change is one op (visit, open, save, note, remove, limit, or metadata-only describe) answered with the list.
- **Every fresh root is listed.** Opening a listed root again (a chat link, a pick) shows the root with its earlier digging
  back **ahead** of it (› walks it); a **card picked from the list** goes back to where that root stood (its whole inner
  history at its cursor). Each change to the inner history is saved there (after a pause, in order; before a new root and
  on leaving the page).
- **A card** leads with the root's real node/Dock name, full slice-group name, or named estate's node name (other parcels
  show the telling spec). Names and descriptions display up to **256 Unicode characters each**, with a middle ellipsis
  keeping both ends and cutting at a nearby word boundary where possible. The name may wrap; the smaller gray description
  (the first meaningful note line, or the group's description) is **one line**. Cards grow to contain their text and your
  whole 📝 note, never shrink to fit the list; scroll for the rest. The badge still shows how many entries its inner history
  holds (brighter from 5, brighter still from 10). Source (conversation/session/turn, pick kind, program position, handle),
  *↳ at 3/7 · ‹the entry›* (or where the digging went on to), when and device share **one truncated line beside the buttons**,
  with its full text on hover; opening the root still gives its existing source/pop-up. The root on screen comes first. **📝 Note** edits the note in the card (Save,
  Cancel; Ctrl/Cmd+Enter, Escape), **✕** takes a root off the list (never the node), **Filter** matches names, descriptions,
  sources and notes, including the stored text omitted by the display cut (stored names remain 2,048 characters and
  description metadata 320; no migration). Inner-history sheet cards share the caps, wrapping names and one-line
  description/source; strip rows stay their old height, name first, with description/source secondary and details on hover.
- **Metadata costs:** reuse the render reply and picker catalog; no extra read when those already supply the facts.
  Old entries are backfilled without opening them, in asynchronous batches of at most 12 (`op: describe`); the route reads
  only the root from the daemon's in-memory cache (`node.scry`, depth 0, size_limit 1, allow_anchor_dock), or the slice-group
  configuration (`slice_group.get`). No live Workflowy API read, whole-account sizing, subtree metrics or cache refresh.
  GET stays store-only; metadata reads start within a five-second batch budget and do not block rendering or saves.
  Successful metadata is persisted once; missing roots keep their existing text. Only name/description/version change:
  notes, inner entries, cursors, counts, devices and use times stay intact. A guarded pane-server restart and page reload
  activate this metadata route and the name-first cards on desktop and phone.
- A pane server from before the list says *Recent roots need the pane server restarted once*; the inner history works as
  before. A conversation-turn root opened on the other device keeps its card; its *Show in conversation* says so when that
  page cannot show the turn.

**The strip** sits at the very top of View, above the Controls bar, and never folds:
- **row 1** is the root widget, with where and ⤢ (the whole inner history, near full screen);
- **row 2** is ‹ the current entry › (whenever there is a root), with **maximize** at its right;
- **row 3** is **🕘 Recent roots · N ▾**: one tap drops the list down over the document. With nothing shown yet it is the
  strip's only row, so a cold start can go straight back to a root.

**Three modes:** **Full** (the Controls open), **Collapsed** (the Controls folded: the three rows, then the document) and
**Maximized**: the maximize button keeps only row 2 (naming the root itself while the root is showing) and the document,
hiding the row list and the status lines too (a render error still shows); in `/m` the View frame takes the whole screen,
top bar and tabs away (`opcViewMaximized`, as the image lightbox's `opcViewImmersive` does; asked again when the View tab
is shown or the lightbox closes). The same button, in the same place, restores. Kept per device (`pane_view_max_v1:‹embed›`),
applied while a document is shown.

Strip and sheet cards are whole targets: handles inside them are not decorated (`[data-opc-no-handles]`).

**The root widget** names the root first, whatever route opened it. Its secondary line keeps the description then source:
- **A session's socket** (from Programs, or found to be one): its handle, role, session count and session's name.
- **A program's root:** `[ap:CORE]`, program title and Conductor count; the Conductor rows show `[ap:CORE]-NNN`.
- **A plain handle:** its id (a UUID as its 8 hex) and whether it was tapped or pasted.
- **A pick:** what was chosen (Dock, Slice group, Parcel or Estate), the pick label and handle/spec.
- **A conversation turn** (a handle tapped in Chat, the Manager's or a read-only viewed session's, or in the desktop
  conversation): `💬 ‹the session's name› · assistant · 11:02` (or your message, thinking, a tool's input or output), the
  secondary, never in place of the node's name. `turnAt` records it at tap time (`m.js` on the phone, `sessions.js` on the desktop): the
  session, the row's position (`seq`) and UUID, the kind and, in a tool, input or output (the phone) or the call (a
  desktop tool bar). Its pop-up's **Show in conversation** (`opcShowTurn`) brings the conversation back on that session:
  - **phone** (`phoneShowTurn`): Chat on its Manager chip, or the viewed session reopened; a tool turn opens at the input
    or output it came from;
  - **desktop** (`showTurn`): 💬 comes forward, on that session (found in the list by its identity, a subagent under its
    parent, an OpenCode conversation by its `ses_` id); a thinking turn opens its thinking, a tool turn flashes its bar
    (never an Inspect).

  Either way the page around the turn is read from its position when it is not on screen (⤒ First's paging, starting 20
  rows before it, Paused; ↓ Latest returns to the newest), and the turn is scrolled into view and flashed. Kept like the
  other roots.

**Tapping the root widget opens its pop-up.**
- **Show** returns to the root, history kept.
- **For a socket,** the pop-up is the sidebar's own tree pruned to the sessions on that socket: the program line once,
  then one card per session, each its branch (Conductor → seat → helper …). The session tapped in Programs comes first,
  outlined, "you came from here".
- **A handle in a card** is a new root there, with that card's session active; the root's own handle just shows the root.
- **A line elsewhere** shows that session (or program) in Programs (phone) or the sidebar: the Show filter back to every
  session, only its ancestors opened, scrolled to the middle and flashed, never its glance. On the desktop View stays on
  screen beside it.

**Where the cards come from:** View asks the page around it, and nothing is read for them.
- The page answers through `opcProgramPosition` and `opcRevealSession` (`m.js` on the phone; on the desktop the sidebar
  page itself, `sessions.js`); the sidebar's `programPosition`, `positionWhenReady` and `revealInList` (`opcSidebar`)
  answer from the tree the list already draws, matching `socket` (an AP-ID by its core, a UUID by its 8 hex).
- On the phone, a lookup loads the Programs frame in the background if Programs was never opened.
- A session past the inventory's list window is not listed; its socket reads as a plain handle.

**How a tap carries its origin:**
- `handle-links.js` passes the pressed element to the page's opener.
- `sessions.js` (`originOf`) turns a row's own handle into `{from:'programs', active, position}`, and a handle in the
  desktop conversation into `{from:'outside', where, turn}`.
- `/m` and the desktop pane hand it to View, through `?handle=…&from=…&active=…&where=…&turn=…` on View's first load,
  else `opcGlimpseView.openHandle`.

**Every tree row shows its own handle at its left:** its socket, or `[ap:CORE]-NNN` on a Conductor, where the scripts'
title did not already show it.

**The desktop View pane** (Claude mode): the sidebar page's conversation column has a switch at its top,
**💬 Conversation | 📄 View** (`sessions.html`, `showPane` in `sessions.js`).
- **📄 View** lies over the conversation, from just below the switch: `glimpse.html` in `desktop-view-frame`, loaded on
  first use, denser than on the phone (13 px reading type, mouse-sized controls). The conversation stays laid out
  underneath, out of reach (`inert`), its reads and Following going on, so **💬** brings it back exactly as it was left.
- **Any handle click on the desktop** opens there as a fresh root, and View comes forward: the conversation and the
  sidebar through `openInView` → `openView`; the editor page through `app.js`'s `desktopOpenInView` → the sessions
  frame's `opcDesktopView`. A handle in the inspector roots View at the held call's conversation turn, as one in the
  conversation does (`callTurn`: its bar on screen, else the turn remembered at Inspect), else "tapped in the inspector". The header's **📄 View** (a button)
  shows it. Nothing opens a new tab; `handle-links.js` keeps one reused View tab only for a page with no View around it.
- **Opening a session from the sidebar** (its row, a subagent, a twin, a "waits on" link) brings 💬 forward; **Select in
  sidebar** from View's pop-up never does.
- **OpenCode mode** (CodeNomad's split, no conversation column): View is a sheet over the whole editor page
  (`index.html` `#view-sheet`, the same `glimpse.html` in its own `desktop-view-frame`), with **Close** (or Escape);
  dictation keeps its text and gets the focus back. No sidebar is on screen there, so its roots show no program position.
- **One desktop history:** both frames key it `pane_view_history_v1:desktop`. Whichever is shown again takes up a history
  the other changed meanwhile (`sync` in `view-history.js`, `resume` in `glimpse.js`) and renders its current entry.
- A page reload starts on 💬; the history survives it.

### ChatGPT accounts: view quota and switch new OpenCode requests

**Desktop header → Quota & switching · GPT**, and **phone → Context → ChatGPT**, show the saved
OpenAI connections using their actual native labels, not assumed Personal/Work identities. Tap a
name to **view only**: quota windows, model availability, reset times, age/provenance and banked
resets belong to that viewed login. The blue choice is the view; the green ring and **GPT · active**
come from OpenCode's current public integration list. Unknown or stale quota is labelled, a passed
reset becomes **?**, and an unmapped native login never borrows the default Codex login's quota.
**Refresh quota / active** is a deliberate read; it never selects. A missing or broken optional quota
mapping does **not** disable a valid saved-native Switch.

Only the separate **Switch to ‹saved label›** selects that login. No additional confirmation,
OpenCode/Claude/proxy restart, quiet-fleet wait, Manager resume or unrelated-work halt is involved.
New OpenCode requests resolve the new saved login. **An already captured in-flight request,
including its proxy retries, keeps its old Authorization**; it has not migrated. Recovering a
confirmed stuck old-login request is a separate exact-session operator action (runbook [ap:D8NOYW]),
not part of this switch.

The pane strictly resolves the requested ID against fresh **saved OpenAI** entries, calls native
`Credential.activate` once (`opencode auth switch openai EXACT_SAVED_ID` is the CLI equivalent),
then verifies the actual active ID through public `GET /api/integration`. A 204 alone is not success.
Pending, confirmed, failed, conflict/changed and uncertain outcomes stay visible. Lost replies and
reloads **read the same caller-ID receipt**, never replay selection. A historical confirmed receipt
does not move the active badge after a later CLI/device switch. If the active login changed since
the view, no different selection is blindly replayed: review the fresh login and deliberately Switch
again. Native activation has no compare-and-set/epoch, so an external CLI race cannot be atomically
excluded; readback mismatch/unknown is reported, not called success. Failures never trigger a
rollback selection or restart.

#### One-time operator enrollment (not performed by the app)

One saved login is still useful; the UI explains how to add another rather than inventing it.
In WSL, Dan runs:

```bash
opencode auth login openai --method chatgpt-token-sharing  # Sign in with ChatGPT; approve the intended subscription
opencode auth list --format json                        # public labels / opaque saved IDs; first OpenAI connection is active
```

Keep the first login saved—no logout or credential copying. There is no `--label` login flag; native
labels may be `OpenAI`, `OpenAI 2`, etc. Enrollment can select the newly created login, so reread the
actual active ID and choose the intended one. This is the native operator browser flow, not a new app
login flow. No native secret-list endpoint or token store is read by these controls.

**Optional quota enrollment is separate.** Install Codex CLI/on PATH if needed (official package:
`npm install -g @openai/codex`). Use a different file-backed `CODEX_HOME` for each intended login;
set `cli_auth_credentials_store = "file"` in each home's `config.toml`, then approve the matching
subscription in each Codex browser login:

```bash
CODEX_HOME="$HOME/.codex-account-a" codex login
CODEX_HOME="$HOME/.codex-account-b" codex login
```

Do not infer identity from the existence of `~/.codex/auth.json`; reuse that home only after identifying
its login. Never copy OpenCode credentials. Do not race a native Codex login/logout against a reader
renewing that same home's credentials. Create the secret-free mapping
`~/.config/opencode-companion/chatgpt-accounts.json`, replacing these example labels/paths/IDs:

```json
{
  "quota-a": {"codex_home": "/home/daniel347x/.codex-account-a", "opencode_credential_id": "ACTUAL_SAVED_ID_A"},
  "quota-b": {"codex_home": "/home/daniel347x/.codex-account-b", "opencode_credential_id": "ACTUAL_SAVED_ID_B"}
}
```

The binding is operator-supplied, **not an independent cross-client provider-identity check**. Check
the intended browser approvals once. `bash scripts/oc.sh usage --provider chatgpt --list-accounts --json`
lists quota enrollments **and** the separate public native inventory; `--account quota-a --json` or
`--account quota-b --json` reads only that quota binding, without selection. With no mapping, the
default `/api/usage` retains legacy Codex quota explicitly **UNVERIFIED against OpenCode**; it is not
shown as a selected native account's quota. Native controls work before any of this optional setup.

#### Reusable GPT route/controller contract

All GPT routes use the unchanged `_claude_origin` loopback / configured HTTPS Tailscale Serve policy,
with OpenCode service authentication kept server-side. `/oc/*` stays remote-blocked. Inputs are
fixed IDs, never provider/URL/argv/path overrides:

- `GET /api/chatgpt/accounts` (no query): `{schemaVersion:1, backend:"chatgpt-accounts", available,
  accounts:[{id,label,method,active,quota:{state,label,authPresent}}], active:{known,credential_id,
  credential_label,detail}, mapping,error,operation}`. `quota.state` is mapped/missing/invalid/unmapped;
  mapping failure never removes a saved-native row. Only `active.known` plus its actual ID determines
  active badges; array order is native order, not alphabetic.
- `POST /api/chatgpt/account-switch`: exactly `{schemaVersion:1, backend:"chatgpt", credentialId,
  operationId, expectedActiveCredentialId:ID|null}`. The intent (target + expected prior ID) is immutable
  for that caller ID. Returns the account envelope with its receipt, 202 while pending/attempting,
  200 after a terminal outcome. Changed intent under the same ID is 409, not another activation.
- `GET /api/chatgpt/account-switch[?operationId=…]`: fresh account truth plus that historical receipt
  (or null), never a mutation. Receipt fields include `id,credentialId,label,phase,detail,attempted,
  startedAt,updatedAt,finishedAt,activeAtFinish`; phases end in confirmed/failed/changed/conflict/uncertain.
  Native has no idempotency receipt: the pane's private SQLite journal records attempts/outcomes only,
  never a second selector. After a pane crash an abandoned pending intent becomes uncertain, not replayed.
- `GET /api/chatgpt/usage?credentialId=ID[&refresh=1]`: `{schemaVersion:1,backend:"chatgpt-usage",
  credentialId,quotaState,now,observedAt,ageSeconds,refreshing,retryAt,error,chatgpt}`. The existing
  `ChatGptUsage`/`UsageReadings` owners share default-active and named caches by full native/mapping/home
  binding, check remaps before/after asynchronous reads and recheck active selection before default
  publication. Provider age is retained; private paths, account/cache hashes and token metadata are not
  browser fields. Named GPT reads never touch Claude or another GPT account. Existing Claude
  `/api/usage?account=personal|work` meanings and independent caches/backoff are unchanged.

`static/chatgpt-accounts.js` auto-mounts `[data-chatgpt-accounts]`; `window.OCChatGPTAccounts.mount/get(root)`
returns `refresh(forceQuota=false)`, `viewAccount(id)`, `readQuota(id,force=false)`, `switchAccount()`,
`paintQuota()` and a diagnostic `snapshot()`. The provider-specific controller shares quota CSS, not
Claude's wait/restart/Cancel machinery. Its browser intent key is `pane_chatgpt_account_switch_v1`;
the provider-scoped document event is `oc-chatgpt-account-switch` (`detail.provider:"openai"`), never
Claude's reconnect event. On the phone this owner alone paints GPT quota, plan and banked captions:
late default `/api/usage` polls cannot overwrite the viewed account. Generic OpenCode Managers/Send/
Refine/model controls remain a separate integration, reusable through these fixed account contracts.

**Activation after integration:** one guarded **pane-only** `transcription-pane/start.sh restart`, then
reload desktop and phone. No OpenCode/proxy/Claude restart is needed to select accounts. Operator
acceptance still needs the real second login/mapping if desired, desktop and physical phone/Tailscale
viewing, A → B → A actual active-ID checks and a short new call per login. Offline fixtures do not prove
real credentials, quota binding, billing or physical-phone behavior.

### Remote Claude access through Tailscale Serve (optional; OFF by default)

The pane still binds only to `127.0.0.1:9911`. To reach its guarded Claude APIs, `/sessions`
viewer and [phone Manager page `/m`](#phone-manager-page-m) from your own phone, use
**HTTPS Tailscale Serve, not Funnel**. This is a server access setting, not a login page. Loopback clients work exactly as
before, without a Tailscale identity. Nothing installs Tailscale or restarts services automatically.

1. **Windows PC and iPhone:** install Tailscale, sign into the same account/tailnet and turn on
   the phone's Tailscale connection. In the Tailscale admin console, keep MagicDNS ON and enable
   HTTPS certificates. On the admin console's **Users** page, copy **your exact full login**
   (usually the account email, including its domain and original case). Do not use a display name
   or a shortened `tailscale status` label such as `dan@`: the comparison is exact.
2. **On Windows, in PowerShell/Windows Terminal:**

   ```powershell
   tailscale serve --bg http://127.0.0.1:9911
   tailscale serve status
   ```

   Copy the exact `https://<pc>.<tailnet>.ts.net` origin shown by status, not an HTTP URL or
   bare tailnet IP. Serve reaches the existing WSL localhost pane through Windows localhost
   forwarding and preserves that external Host.
3. **In the WSL account running the pane:** replace both placeholders below with the origin
   from step 2 and the full login from step 1, then create the setting:

   ```bash
   mkdir -p "$HOME/.config/opencode-companion"
   cat > "$HOME/.config/opencode-companion/pane-remote.json" <<'JSON'
   {"origin":"https://<pc>.<tailnet>.ts.net","login":"<your exact Tailscale login>"}
   JSON
   ```

   No API key goes in this file. An absent file, empty file or `{}` means OFF. One valid
   nonempty `origin` + `login` enables one HTTPS origin; this is not a list. The origin may
   have an explicit port, but no credentials, URL path, query or fragment (a trailing `/`
   is normalized away, as are host/scheme case and HTTPS's default port). Invalid, partial
   or unreadable configuration leaves remote access OFF with a startup-log diagnostic;
   loopback still starts.
4. **Dan or his Manager activates it with the existing guarded pane restart, in WSL:**

   ```bash
   bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/transcription-pane/start.sh restart
   ```

   It waits for the quiet conditions described above; from a Manager's own turn, run it in
   the background so it can proceed after that turn ends. No Claude host or OpenCode restart.
   The policy is read once at server startup: editing the file alone does not change the
    running server. Reload pages for changed browser code. This supplies access for `/m`;
    it does not make the PC's OpenCode or tool-preview paths remote-capable.

On the configured remote authority only, every guarded request needs exactly one
`Tailscale-User-Login` equal to the configured login. Serve strips client-supplied identity
headers and supplies this header; no browser form or extra token is needed. Origin, when
present, must exactly equal the serialized HTTPS origin; page navigations may omit it.
Foreign/duplicate Host or Origin, missing/wrong/duplicate identity, and `cross-site` or
`same-site` fetches are refused. `Sec-Fetch-Site` may be absent, `same-origin` or `none`.
Forwarded headers alone authorize nothing; this adds no cross-origin CORS grant.

**Turn guarded remote access OFF:** in that WSL account, rename/remove the setting, then
Dan or his Manager runs the same guarded restart:

```bash
mv "$HOME/.config/opencode-companion/pane-remote.json" "$HOME/.config/opencode-companion/pane-remote.json.disabled"
bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/transcription-pane/start.sh restart
```

To stop this specific Serve mapping too, in Windows (append `off` with the original flags;
do not reset unrelated mappings):

```powershell
tailscale serve --bg http://127.0.0.1:9911 off
tailscale serve status
```

**The `/oc/*` OpenCode proxy is loopback same-origin only:** it forwards to OpenCode with the
server-side Basic auth only for a single `127.0.0.1`/`localhost` Host on the pane's port, an
absent or matching Origin, and no cross-site fetch (and grants no preflight). A Serve request
carries the tailnet Host, so it is refused with 403 and never reaches OpenCode, whether or not
`pane-remote.json` is ON. Generic static files keep their existing policy; the remote switch is
not an all-path firewall, and stopping the Serve mapping removes the remote mapping entirely.
Serve reference: <https://tailscale.com/kb/1242/tailscale-serve>.

### Phone Manager page `/m`

**This update needs a guarded pane-server restart, then a phone page reload; neither was
performed by this implementation.** After Dan or his Manager does the guarded restart in the
remote-access steps above, open **`https://<pc>.<tailnet>.ts.net/m`**
on the iPhone with Tailscale connected. Use the exact HTTPS origin from `tailscale serve status`,
not a bare tailnet IP over HTTP (Send needs a secure browser UUID). `/m/` works too.
Optionally choose Safari **Share → Add to Home Screen**. Enter/import Refine settings where
you will actually use it: a Home Screen app or another browser may have separate storage and
need its own import. Do not clear site data to troubleshoot a Send.

The page lists **Claude Code host-registered Managers**, live or sleeping, and genuine root
**OpenCode Managers** in the desktop Manager Sessions' order (by Manager-Session
number, highest first, each primary followed by its sub-sessions, so the newest primary is always the leftmost chip; *History page
and API* above), and keeps refreshing. Other sessions (Conductors,
seats, workers, helpers and subagents on either backend) open read-only from **Programs** (see
*Reading other sessions* below). It is one screen: **Chat · Programs · Context · View** at
the top (on a phone the four shrink to their words, so all four are in view; wider, each keeps a thumb-sized
minimum width and the row may slide sideways) with the **☀** outdoor glare switch at the row's end, a one-line strip of Manager chips (the last week only: a chip whose last activity is older than seven days (`STRIP_DAYS`, `manager-order.js recent`) leaves the strip unless it is open, selected, or carries the newest Manager-Session number; a closed recent one stays, so selecting it and pressing Send reopens it. Right after the gauge each chip shows its Manager-Session index in bold (`219`, `219-a`; `manager-order.js index`), never elided, so on an iPhone the number survives and the name after it elides instead; a name without a number shows no index. Each chip's small context gauge says open or closed and how full: live, the Context view's pie in a green ring, "live · 42% context" on hover; closed, a gray ring, filled gray with the last share when the list carries it, "closed · last 42%" or "closed". Its background shades as the subscription nears its limits, the same for every chip of a model family: top half the weekly window (crimson), bottom half the five-hour window (orange-red), fading in from 65% used to full at 95%; Claude Code chips from the Claude reading, OpenCode `openai/*` chips from the ChatGPT plan's, any other model none. It takes the same cached `GET /api/usage` reading the Context view uses: Chat reads it itself once on load and then every 90 s while the page is visible (`STRIP_USAGE_MS`; Context reads it every 15 s), one request per read and never one per chip, and never `refresh=1`. That costs no provider calls of its own: the pane's one shared cache per login (`ReadingCache`) lets an ordinary read start a provider read only once its reading is 2 minutes old, one at a time, and none inside a 429 backoff, and `oc_usage.py`'s cross-process snapshot, shared with `oc.sh usage`, gates it again. The selected chip is the
conversation's title, and the line under it says *Live · idle*, *Working* or *Sleeping*), the
conversation filling the middle, and the text box with its four buttons at the bottom. One scroll
below, **Refine setup** and **Last original** share one row of toggles; each opens its panel below the row.

**Outdoor glare mode: tap ☀.** For reading in the sun: every dim grey on every view turns bright,
near white, and the small print grows (role labels and times, tool names and previews, status lines,
the Programs rows' meta lines, telemetry and glances); backgrounds, layout and colour meanings
stay. The button turns yellow while it is on; tap it again for the normal look. The choice is kept
on the phone (`pane_m_v1:glare`), so a reload or a new tab opens the same way, and the Programs
frame follows it at once without storing anything itself. It is one switch over the pages' own
theme variables: `static/glare.js` sets `data-glare="on"` on `<html>` before the first paint, and
`static/glare.css` (loaded after `m.css`, and after `sessions.css` + `m-programs.css` in the frame)
re-points the colour variables of both sheets, then lifts the small print `m.css` sets by hand and
the sidebar's type scale. A conversation following its newest message keeps following when the
text grows. New phone widgets that use the theme variables follow automatically; a grey or a small
size written literally needs its line in `glare.css`.

Tap a chip to read that Manager's latest messages, rendered as Markdown, including tables, in
the desktop viewer's turn colours (copied exactly from `sessions.css`): your messages green
(`#3fcf7a`), the Manager's final answer red (`#ff6b61`), Manager prose inside a working round
(a tool call follows it before your next message) the desktop's dim orange, and each tool call a
compact gentle-blue bar (amber while pending, red-bordered on error). A bar shows the tool name,
the call's total time (as below), the shared In/Out stored sizes and the desktop's shared argument preview as `key: value` pairs.
Tap it to read the stored input and output in small scrollers, each with **Copy** (the exact
stored text), through the desktop inspector's own reading view (the same `tool-reading.js`; see
*Persistent selected-tool inspector*). A source edit opens on its recorded **Diff** first, above Input and
Output: the desktop's Diff reader, inline at phone width (Layout and Show all in its own controls, the same honest
labels for shell, pending and new-content calls), taken from the same snapshot read, so it adds no request; it
scrolls inside at most 70% of the screen height, never the page sideways. Each region has a **Readable · Pretty · Raw** switch, the
desktop's Pretty JSON and Readable text checkboxes as one control: **Readable** (default) shows
lossless Pretty JSON with multiline string values as document blocks, a leading JSON value
prettified with the text after it rendered as Markdown when it looks like Markdown, and a
Markdown-looking body without JSON rendered; each rendered block has **Rendered / Source**.
**Pretty** is the indented JSON alone; **Raw** the exact stored text. The switch appears only where
it changes something (plain text has none), and the desktop's caption says what is shown. One
**Wrap** switch, on by default, covers every tool view, the Diff included: off keeps lines whole and scrolls sideways
inside the reader. The choices are page-wide and reset on reload, as on the desktop, and a text
selection defers a repaint until it ends. Its status line is the call's state as one emoji in a fixed-width slot
(📖 reading · ⏳ pending · ✍️ input still streaming · ✅ complete · ❌ error · ⚠️ read failed, the word as its title),
then the call's date and time and, when stored, how long it took, exactly as the desktop inspector does
(`✅ 2026-10-18 23:52:07 ET · took 1m 23s`). A live call's view rereads about once a second and its emoji flips
📖 ↔ ⏳ while the time beside it stays, so the line never rewraps and the panes under it never jump.
**Refresh** rereads the call; an open live call updates until settled; closing it stops reads. Thinking summaries with stored text are quiet
**Thinking** disclosures; blocks with no stored text are not shown. **🧠**, beside ⤒ First in the chat's chip row,
switches between **current round** (lit, ON by default) and **keep open** (OFF), saved per device.
A round runs from your message through every assistant/tool step and the final answer, until your next message.
Current round opens all its thinking, streaming or finished, and folds all earlier rounds when your next message appears.
You can open or close any block; those hand choices last until the next round. Keep open starts every block expanded,
never folds it automatically, and remembers your hand collapses across updates, row re-creation and ⤒ First / ↓ Latest.
Both modes retain choices while this conversation is open (including same-conversation reconnects); another conversation
or a page reload starts fresh. Switching modes reapplies its defaults. Paging back keeps the newest known round boundary,
so earlier history does not reopen itself. The rule is shared by Manager chats and viewed sessions on both backends.
This browser-only change activates with a `/m` reload, no server restart. Each message shows its time
and a ⧉ that copies its own Markdown. Scrolling up pauses following; **↓ Latest** returns to
the newest message, and is offered only while every part of the last item (message, thinking or
tool call) is off screen. It also says whether anything below is **new**: a row counts as seen once
its end has been on screen (a zero-height `.seen-end` marker at each row's end, watched by one
IntersectionObserver; `static/seen-latest.js`). Rows that arrive from the live tail while you are
scrolled up are new; everything the view loads otherwise (the opening page after a load, a
conversation switch or a reconnect, which opens at the newest; continuation pages; earlier
history filled in above) counts as seen. A seen row that grows past the view while you are scrolled
up (a streaming reply, a tool result filling in) is new again until its new end has been on screen.
All of it seen: the plain blue **↓ Latest**. Something unseen below: amber **N new ↓**, with a ~5 s
pulse each time new material arrives; it stays amber until you have seen it, scroll to the end or
tap it. It turns **red** (the final-answer red) while at least one unseen row below is a final reply
(the row classified final: settled prose with no tool call after it before your next message),
whatever the newest row is, and pulses as it turns; tool turns alone keep it amber, so a doorbell
round still at work reads amber and a finished one red. Final-ness is judged at every repaint, so a
reply still streaming, or prose a tool call then follows, never holds it red. The same on the desktop viewer, in ☀ glare mode, and with no layout shift. **⤒ First** (just left of ＋) is the desktop's ⤒ First: it reads the
conversation's opening page (`/api/claude/events` with `fromSeq=0`, one bounded page) and holds it
still at the top, so the startup prompt can be copied with its ⧉; **↓ Latest** reopens the live
newest window. **Scrolling past either end of what is loaded reads the next batch that way**, as
on the desktop, so no stretch of a long conversation is out of reach (`phonePage` in `m.js`; the
readers already page by position, so the server is unchanged). Near the top: the 100 rows just
above the oldest one on screen, by position (`fromSeq`, stepping on when a byte-bounded page stops
short), placed above without moving what you are reading, on back to the very first message. Near
the bottom of ⤒ First's window (or of a turn opened from View): the rows after the newest loaded;
at the conversation's last row the view becomes the live tail again (⤒ First released, new rows
arriving below). Only your own scrolling pages (a touch, the wheel, keys, the thumb strip), never
the page's own scroll writes. The live tail keeps its newest 80 prose turns while it follows; while
you read above the newest nothing above is trimmed, and the earlier pages you loaded stay until
you are back at the newest (then they may go, and scrolling up reads them again). Rows loaded this
way count as seen. Opened views keep their state while polling. Neither tool bodies nor
thinking enter Refine context. Task/result carrier rows are not duplicate replies.

The phone loads the existing **`/tool-preview-core.js`**, now served under the **same origin policy
as `/m`**, including the configured Serve origin (no inline fork), the shared size helper
`/tool-record-text.js` and the shared reading view `/tool-reading.js` (an ordinary static file). Read-only
**`GET /api/claude/tool-record?callRef=…&fileEpoch=…`** delegates directly to the desktop's existing
reader snapshot method, with its same witness checks and response bounds. It does not select or
replace the PC's held inspector, and no collapsed tool fetches a body. The existing `/oc/*` guard
and all phone/Claude origin refusals are unchanged.

Reading never wakes a Manager and needs no key or setup. There is no login, wizard or session expiry.
It is not the desktop pane: no full held-tool inspector, push notifications or offline/service-worker mode.

**Model and thinking: press and hold a Manager chip.** OpenCode uses the location-specific catalog
and that model's variants, with native readback and no busy-only refusal (see *OpenCode Managers*
above). Native Claude retains the following host behavior. Holding a chip still for about half a
second (a scroll or an early lift cancels; a right-click or Android long-press does it too) opens a
bottom sheet for that Manager without selecting it; the tap that ends the hold is swallowed, and
the chips allow no iOS text selection or callout. The sheet shows the applied readback in the
desktop badge's words (*Opus 5.5 · thinking high · applied*, *pending* or *⚠ settings
unverified*), the thinking levels as one large row (a green dot marks the verified current value),
the model folded below, and one **Apply** naming what it will do (*Apply thinking · xhigh*). The
option lists are the desktop's (`index.html` `claude-model` / `claude-effort`). Apply is the
desktop's Apply model / Apply thinking (`controlClaude`) exactly: a fresh `state` read refuses a
Manager busy with its own turn (try again when it finishes), then the control's caller
`requestId` is saved and read back (`pane_m_v1:control:‹executionId›`) before one
`POST /api/claude/control` (`set_model`+model, `apply_effort`+effort). Model and thinking together
go model first, thinking only once the model is verified applied. The sheet says **applied and
verified** only on the host's applied receipt; a pending setter shows as requested, holds Apply and
is polled through `control-receipt` by its `requestId` (after a reload too), never repeated; a
refusal keeps the choice and says why. A sleeping or still-starting Manager's sheet explains and
offers no Apply. Close, Escape or a touch outside closes it; the draft and Sends are untouched.

**＋ New Manager** (the green ＋ at the right end of the Manager chips, beside maximize) is the
desktop's New Manager at phone size, with **Native Claude | OpenCode** runtime choice. OpenCode
offers catalog model/thinking/persona, entirely from `/m`. Native Claude's form
is pre-filled from the same
`GET /api/claude/launch-defaults`: the next `Manager-Session-NNN` name and Opus 5.5 / xhigh, with
model and thinking folded under *Model and thinking*. **Start** posts the desktop's exact
`POST /api/claude/launch-manager` with a UUID the phone allocates (see *New Manager, way-out
controls and honest refusals*). Both routes already sat behind the same origin policy as `/m`
and its Send. The current Manager keeps running and the draft is untouched.
The new Manager is selected in the chip row as soon as the host lists it, before it has any
message, and its first message (the desktop's, above) goes out as soon as it is ready; if you chose
another Manager meanwhile, your choice wins and nothing is sent. **↳** beside ＋ is the desktop's
↳ Sub-session: the next letter of the newest primary chip, its first-message box with room for
the reason, ＋'s own Runtime and *Model and thinking* under it (Claude by default), then **Send**.
A page loaded while the Claude host is down lists no Claude chip, so ↳ also counts the Claude
Managers' names from this phone's last good Claude read (`pane_m_v1:claude_manager_names`, display
names only) and says so. Its state line reads *Getting
ready · Send waits for it*. A Send while it is still starting waits until it is ready, as a Send
to a sleeping Manager waits after its reconnect; editing the text or choosing another Manager
cancels that Send. A refused launch keeps the form open with the host's reason. A lost reply
keeps watching the list for that launch.

**Programs** switches to the desktop's actual sidebar renderer, phone-sized and read-only:
Running now, Favorite programs (the same program ★ and stub rules as the desktop), program → Conductors → workers → helpers, freestanding quick-worker trees, role
filters, fold/expand, busy/waiting states, native-subagent expanders and glances (both backends).
The sidebar loads only on the first tap and stays mounted when you return to **Chat**; switching
views never changes your Manager or draft. It reads `/api/claude/{sessions,subagents,glance,turns}`
and `/api/opencode/{programs,glance,turns}` routes, already covered by
the phone's origin policy. The small `/m-programs.html` shell uses `sessions.js` in sidebar-only
mode, not a copied program renderer: no targeting, rename, launch or CodeNomad focus actions are
wired there, and a conversation opens only in the phone's own Chat (**Read ›**, below). Only local
display fold/filter choices change. On the phone a tap
anywhere on a row with sessions under it (a program-name row or any session row) opens or closes
them; reopening restores each inner row's own saved open state. Rows are tap targets, not
selectable text, and the desktop's hold-rows-under-the-pointer applies there to a mouse only (a
touch lift is a pointer leaving, and rebuilding the list then swallowed the tap). It holds its reading position through refreshes like the desktop list, and a refresh waits while a finger is on the list or a flick is still moving (Chrome on iOS is WebKit too). A Claude row with no tree under
it toggles its native subagents (with neither, it opens its glance); a row of either backend with something under it keeps
its glance on a small 💬 chip. An open glance's **⤢ Large** (its top right) shows that same glance as a sheet over the whole
Programs view, in large bright type with every message whole (the inline glance shows a few lines of each) and the tool call's
preview unclipped, opened at its newest end; it follows the glance's own 15-second reads (and keeps your place if you scrolled
up), reads nothing itself, and closes with **Close** at the bottom, ✕, Read conversation › or closing the glance. One two-tap button replaces the desktop's ⊟ ⊞ (the same fold as the desktop ⊟, stepped
by tap count rather than read from what is open): **Collapse** closes every top-level
row (programs, freestanding trees, root rows' subagent lists; inner states kept) and folds the
home sections, keeping Running now open; **Show running** then opens only what is busy
or waiting, by the desktop's own per-program fold-to-active rule, applied to every program and
freestanding tree, with only the sections that draw that work left open.

**Reading other sessions (read-only).** **Read ›** on a Claude row in Programs (beside its ⧉; an
expanded native subagent has its own) and **Read conversation ›** inside an OpenCode row's 💬 glance
open that session in **Chat**, in the same conversation view as a Manager: Markdown turns in the
desktop colours, labelled with the session's role (*Conductor*, *Quick worker*, *Helper*, *Subagent* …;
a subagent's prompt reads *Prompt*), tool bars that open their stored input and output, thinking,
⤒ First and ↓ Latest. It gets its own chip at the start of the chip strip (role, name, ✕), and the
state line names it in full, e.g. *Read-only · Conductor: ⚡ Phone page round 2 · Working*. The text box gives way to one line, *Read-only ·
Send goes to Managers*: these sessions have no Send, Refine or Re-check, by design (they take their
instructions through their sockets, and the host refuses a Send to anything but a Manager). Your
Manager stays selected underneath, its chip dashed, with its draft, Send receipts and Refine untouched;
tapping that chip or ✕ brings it back as it was, and another Manager chip selects that Manager.
Read › on a Manager simply selects it. A Claude session is read through `/api/claude/events` (checked by
its host execution, else by the transcript handle it was opened with) and its tools through
`/api/claude/tool-record`. An OpenCode session is read through `/api/opencode/events` by its `ses_` id,
and its tools through **`GET /api/opencode/tool-record?id=…&callRef=…&fileEpoch=…`**, which re-checks
the call's session, message and unique tool slot and returns the same text and byte counts the desktop
inspector shows, without touching the PC's held inspector. Both routes sit behind the same origin policy
as `/m`; the `/oc/*` proxy stays loopback-only. After a pane restart the same session is found again
(a Claude session by one scan of the session list, an OpenCode session by its id). Only the Programs
frame of the same page and origin can open a session. Not included: a session's other scopes,
following a subagent call to its child, a Context gauge for viewed sessions, and keeping the viewed
session across a page reload.

**View** is the View page (*View: NEXUS documents*, above) in a frame of its own, loaded on the
first tap and kept while you switch tabs (its document, scroll and choices stay); the frame is a
page container, not a sandbox. It follows ☀ at once, and reads nothing until you choose and nothing
while hidden; meanwhile the phone page polls only Send receipts and controls, as for Programs. On a
390 or 375 px phone all four tabs, **View** included, are in view at once.

**Context** shows four sections, in this order. **Manager context**: one line each, the desktop's
context gauge, percent and `current / allowed` (e.g. `412K / 1M`) for every live Claude Manager and
the selected one (marked). Then Dan's subscription allotments, each window with its percent and when
it resets: **Claude Max** (five-hour, weekly and Fable weekly windows, with Fable's share of the
weekly total) and **ChatGPT** (its windows and model availability, Astra). Each provider's block
carries its own banked resets at its bottom: Claude's are the active account's, followed by the
allotment reading's age and its **Refresh**; ChatGPT's belong to its viewed login. Last, **OpenCode
Managers**, each one's own measured context. Each
reading's `reset_credits` (Anthropic's limit resets from `/api/oauth/usage?cedar_ember=1`, sent with
Claude Code's CLI User-Agent, and GPT's `wham/rate-limit-reset-credits`) shows how many are available and one
line each, soonest first, with its expiry in the phone's local time to the minute (`Thu 10-22
15:24`) and a countdown (`in 21h 40m`); one expiring within 24 h turns red with *use it within
24 h or lose it*. A Claude grant also names what it clears (`clears 5-hour + weekly`), its count
when it holds several (`2× …`) and *held · not usable right now* when paused. A failed read says
so, with its reason (never zero): for Claude, only `no_grant` is a verified *none banked*; a
`surface` refusal or a missing block is *could not be read*. A stale reading's list is marked *as
of the last reading*, and a list from the provider's 1 h banked-reset cache names its own read age;
a 429 with no list yet is a quiet *not read yet* note with the provider's retry time, never a
verified zero. Claude's source is `BANKED_SOURCES` in `m.js`; GPT's viewed-account list is in
`chatgpt-accounts.js`. A Manager's figure is the desktop's own `contextReadout` (the last
completed model call, never a guess); the view reads it every 10 s from an exact-execution
`/api/claude/events` page that starts past the last row, so no rows travel, and the open
conversation's own pages keep the selected Manager's figure fresh. A newer unknown read keeps the
last measured figure, marked as such. Active-Claude allotments come from read-only **`GET /api/usage`**;
the GPT control reads **`GET /api/chatgpt/usage?credentialId=…`** for the viewed native login,
under the same origin policy as `/m`: each Claude login has one cached reading (the active label's
default view and its named view share it) beside independent GPT binding caches, refreshed single-flight
in the background (an ordinary poll after 2 minutes; an explicit **Refresh** after 30 seconds and only
outside a provider's retry time; never more often than every 30 seconds, failures included), so the
page never hammers the providers. Opening Context or toggling an account only reads that cache; it
never forces a provider read. The account hash is not sent. The footer
gives the reading's age, turning amber with the failure when it is over 5 minutes old or a refresh
failed; a provider's own stale snapshot says so, keeps its last windows and names the provider's
retry time rather than a red error.

Chat, the text box and Programs each have their own **maximize** icon (four outward corners).
The text box fills the visible phone screen with just its compact button row
below; the Manager chips, setup and chat stay hidden until you restore. Chat can independently
fill the screen, as can Programs. The same icon (now four inward corners) returns to the normal
combined view. Nothing is remounted or cleared, and maximizing never opens the keyboard by
itself. The maximized text box follows the keyboard-visible viewport, keeping its buttons above
the iPhone keyboard. Beside it, on the right edge, is a thumb-wide margin with a grip line that is
not part of the editor: a tap there hides the keyboard, and a drag there scrolls the maximized
view, which has extra room below its buttons. That is the way out when something iOS floats over
the page covers the buttons, such as the iPad's bar for a minimized floating keyboard. Outside
maximize, a phone-sized layout (either **layout viewport** dimension below 500 CSS px, portrait or
landscape; no user-agent sniffing) keeps the box at **three scrolling lines**, however long the draft.
An iPad Mini or larger layout keeps growing with its text up to about a third of the screen as before.
The keyboard's smaller **visual viewport** does not decide this breakpoint. Maximize is still the way
to see more of a long draft; there is no text-length limit.

**Status never takes space from the conversation or editor.** Send/Refine success and progress notes
appear over the visible conversation, fading after about three seconds; repeated identical polling
notes do not keep them alive. Red errors stay until × or a replacement. The full text is scrollable
inside the toast, and **History** or **⋯ → Status messages** opens every note from this page load,
including a high-thinking reminder. The log is page-local, not saved across a reload. With the text
box maximized, the toast is an **i** (or red **!**) in its non-editable thumb margin; tap it for the full
notes, so an automatic notice never covers dictated text or the action buttons. Other phone views
do not pop composer notices over their own controls; the notes remain in Status messages on return.
These browser-only changes activate with a page reload after merge, no server restart.

The combined view has the same way out over the whole screen. While the text box has focus (the
keyboard is up), the page stops scrolling as a document: it becomes a frame over exactly the
visible screen that scrolls inside (the views, the chat one screen tall, the text box and its
buttons, the setup below and some extra room), and a thumb-wide strip runs down its right edge; the
content narrows so the strip covers nothing. The frame moves nothing under the finger of the tap
that opens it, whenever iOS places that tap's caret (after the focus event, at the finger's point
in the layout as it is then): the text box's text keeps its width, because outside the frame the
box's right padding already holds the room the strip takes (the cost: the text wraps a strip's
width, about 42 px, short of the box's right edge even with the keyboard down), and the box stays
exactly where it was on the screen. Once the keyboard resizes the frame, the text box and its
buttons go to the bottom of the screen and stay there as the keyboard settles or the box grows.
The draft's own inner scroll is kept separately: if its end was visible before focus (within one
CSS pixel of the bottom), viewport/box resize and the shell's existing layout fits keep that end
in view, in normal and maximized composers. Focus itself never re-scrolls the text under the tap,
and nothing changes the caret, raises the keyboard or retries on a timer. A draft already scrolled
up stays alone; scrolling it yourself, navigating with keys, a second tap, selecting a range, typing
earlier in the text or blurring releases the end pin. A tall/maximized editor also leaves a tap's
caret before nonblank trailing text to the browser's own scrolling rather than pinning it off screen.
Real iOS keyboard behavior still needs an iPhone try; if it jumps,
send the Strip diagnostics below rather than adding timing-based scroll corrections.
Through all of it the line at the top of what you see of the conversation stays the top line, as
the keyboard comes and as it goes, many turns up included, the same line every time whether or not
you touched the conversation in between: the narrower page rewraps every message taller and no scroll
anchoring makes up for it (Safari before 27 has none, and none acted on an iPhone with iOS 27), so at
every opening and every closing the page takes that line afresh and puts it back after each change
that follows (the shell's own, each keyboard resize or pan, iOS scrolling the page back as the
keyboard leaves). It measures the screen as `visualViewport.pageTop` less `window.scrollY`, right
both where positions count from the visible screen (iOS WebKit) and where they count from the
page's layout (Chrome). Your own touch, press or wheel lets the line go, and a conversation
following its newest message keeps following. A
finger on one of the text box's buttons (Refine, After ---, ⋯ and its items, Send, maximize) holds the
frame still until that tap's click: the keyboard resizing or the box losing
focus waits until the click has landed (or the touch was cancelled, or the next touch lands
elsewhere), so a tap on Refine with the keyboard up lands on Refine. The action row itself
(Refine, After ---, ⋯, Send) acts when the touch **lifts** on a button (within 12 px of where it
went down, inside the button as it was then), not on the click iOS sends afterwards: between the
two, focus moves (Send leaves the box) and the frame and its strip can come or go, shifting the row
before iOS hit-tests the click, which then landed elsewhere or nowhere. That later click, wherever it
lands, is swallowed once (the next touch, or a second, clears it); a mouse, the keyboard and
VoiceOver keep the ordinary click, and the touch guard that keeps the box's selection is unchanged. A drag on
the strip is one continuous scroll through the whole view: toward the top it brings the chat up to
fill the screen, then scrolls the conversation's history up to its oldest loaded message, then
shows the rows above; toward the bottom the same in reverse, back to the newest message and the
text box and its buttons. A flick glides on, and one toward the bottom comes to rest with the
buttons in view. While the strip moves the conversation only its very end counts as following, so
a poll never pulls a drag back. One grip line, centred, never moves, and nothing a drag scrolls can
move the strip: under the iOS keyboard a scrolling document shifts the viewport, which is what made
a strip fixed to the screen shake. Touching the conversation or the rest of the view still scrolls
it directly. A tap on the strip hides the keyboard; the strip and the frame go with it and the page
is an ordinary document again. Pressing the strip never moves the cursor or the selection. The maximized text box keeps its own margin instead, and maximized chat or Programs
has no strip (the keyboard is down there).

**Strip diagnostics**, for a strip that misbehaves on a real phone: ⋯ → **Strip diagnostics** starts
recording for this page load (or open `/m?thumbdebug=1`); use the strip; then ⋯ → **Copy strip
diagnostics** copies the last 10 seconds to paste into the Manager chat: every strip event and drag
frame, each move that keeps the reading line still (`hold:N`) and each draft-end correction (`draft-pin:N`), with
the view/conversation/draft scrollers, the caret range, the window and visual-viewport offsets and the frame's and strip's
screen positions. The log is also shown under *Strip diagnostics* below the setup. It sends and
stores nothing.

Dictate/type in the box, then use the button row, **Refine · After --- · ⋯ · Send**, with the text
box's maximize icon after it. Send is last, and the small ⋯ keeps it away from the Refine buttons. ⋯ opens a
small menu above the row with the rarely used actions, **Paste**, **What Refine sends**, **Re-check
settings**, **Teams message break**, **Strip diagnostics** and **Status messages**. It closes on a choice or a tap elsewhere.
**Interrupt** is first in that menu for emergencies.

- **Paste** (in ⋯) reads the clipboard directly from your tap and selects exactly the pasted passage for
  Refine, without opening the keyboard. At a cursor it pastes there. When text is selected (the
  passage you just pasted or refined), it never replaces it: the new passage goes right after it
  as its own paragraph, so a paste → Refine → paste the next thought → Refine loop keeps
  every refined passage. Safari may show its own small Paste bubble; the page adds no
  confirmation. A denied clipboard read leaves the text intact and you can still use the
  iPhone's native Paste menu, which also auto-selects the passage (and, being a normal paste,
  replaces a selection).
- **Refine** cleans the selected passage, or the whole box when nothing is selected, using that
  Manager's own whole main prose conversation by default, your dictionary and the shared browser Refine engine.
  It uses the default prompts, or imported
  prompt-part overrides with the PC's placeholder fallback rule; there is no phone prompt editor.
  **Pasting automatically selects exactly the newly pasted text**, including multiline text or a
  replacement, so the next Refine acts only on it. The surrounding unsent draft is read-only
  `<draft-before>` / `<draft-after>` context, and only the selection is replaced. If the replacement
  reaches the end (the whole box or a selected tail), the caret lands at the end and the box scrolls
  to its bottom, with no selection or green ring and without opening the keyboard. Only a selection
  in the middle stays selected and gets the green Refine ring; tapping Refine again or Send clears
  that ring. The maximized phone box follows the same rule; desktop behavior is unchanged.
  **Last original** holds the full untrimmed original as read-only text for recovery; its **Copy** copies all of it.
  A failed context read is shown; tap Refine again to retry, never silently run without it.
  The same button turns amber **Cancel** while running; no phone inference deadline or message-length cap.
  Completed at xhigh or max, it adds the red thinking reminder under its status line (see *Thinking levels*).
  Incomplete/failed/cancelled output does not replace text, nor does output arriving after an edit.
- **After ---** refines only the text after the last line that is just `---` (one you typed to mark
  where the last Refine ended; a `---` inside a ``` or ~~~ code block does not count), sending
  everything above it as `<draft-before>` context exactly as a selection would; only that block is
  replaced, surrounding blank lines kept. Like a selected tail, it leaves an unhighlighted caret
  at the actual box's end (past any preserved trailing blank space) and scrolls there, keyboard down
  unless already open. With no `---` it is Refine on the whole box. A selection still wins, the way
  to clean a block in the middle and keep that replacement selected.
  While the box holds a selection After --- is dimmed (a tap would refine the selection, as Refine
  does); it is never truly disabled, so it can't get stuck. It turns into **Cancel** while its
  Refine runs, and either button cancels.
- **Send** needs no Refine key. A native Claude sleeping Manager reconnects on this deliberate
  press; a busy Claude Manager receives a host steer. OpenCode Managers use direct native steer,
  without a reconnect or watcher. Both paths journal once and query exact receipts on reply loss.
  Settings-unverified is a note, not a Send blocker. The phone saves and reads back its exact
  UUID/original/submitted/identity journal before one POST. A lost reply queries that receipt
  only, never resends. Only an exact ACK (or durable queued steer) clears unchanged text for
  the same Manager; exact delivered text restored after a reload can clear too. The button stays
  disabled from the press until that Send is settled (its exact ACK, a busy Manager's steer
  delivered, a refusal, or a failure before the POST), so a second tap can never send it twice; a
  reload restores the lock from the journal. If still stuck after about 30 seconds, look at the
  conversation, then use **I checked — release it** below the action row, the lock's way out.
  This conditional link records your disposition and preserves the original, making no POST; any
  later press of Send is a genuinely new message. Unreadable journal records are shown as
  copyable raw text and set aside without holding Send.
- **⋯ → What Refine sends** is the desktop's Refine-context View on a full-screen sheet. It reads
  this Manager's conversation context as Refine does (the same whole/budgeted choice and checks) and opens
  scrolled to the bottom. **Context** shows the chat history, then the unselected draft before
  and after the selection, each in its own pane; an empty side is one line. **Full prompt** shows
  the system prompt and user message exactly as Refine would send them, built by the same
  function as the real request. The keyboard closes while you read, and **Close** (or Escape)
  returns to the same draft and selection. It sends nothing and needs no key. A failed context
  read is shown, with no prompt, since Refine would not run.
- **⋯ → Re-check settings** makes one read-only `get_settings` request, even while busy or held on
  settings, to show the applied model/thinking and clear a matching stale host hold. It sets
  nothing. The status says Send is open, still held (the host's reason), or did not verify.
- **⋯ → Interrupt** requests interruption only of the selected Manager. OpenCode uses one
  persisted native interrupt intent, with no confirmation or idle-only gate; acceptance is not proof
  of cleanup, and a lost reply never repeats it. Native Claude stops its current turn through the host's native
  interrupt, with no confirmation. It is enabled while the page says Working and says *Nothing to
  interrupt: the Manager is idle* otherwise; a fresh state read checks again before the request.
  The draft and selection stay intact, so Send can immediately explain what happened. The status
  gives the interruption time or the host's failure; an uncertain reply is checked by its saved
  control request ID, never repeated (also after reload). Its journal slot is separate from settings
  controls, so an uncertain setter does not hold the emergency action. No session is closed or
  restarted, and background tasks are not stopped. Desktop already has **■ Interrupt** beside Send
  (no equivalent ⋯ menu); its existing confirmation is unchanged. Non-Manager OpenCode sessions
  remain read-only; the finite Manager control route is not an abort proxy for arbitrary sessions.

A phone Send appears in the PC's existing polled conversation about a second later while that
view is visible; drafts and keys do not sync between devices. The phone's draft, selection,
last original, per-message journals and glare choice use only **`pane_m_v1:*`** keys, separate from the PC
pane even when `/m` is opened on the same PC origin. Refine's **`refine_*`** settings are
deliberately compatible. The reused Programs sidebar shares only its local display preferences
(`pane_lineage_open` / `pane_sidebar_sections`) with that browser's desktop sidebar.
Browser/profile/origin-local storage is not a server backup; quota
failure prevents an unjournaled Send but keeps its full visible text. Refine can still run if
its original backup fails, with the original retained on the page and a visible note.

**Bring your existing PC Refine key, model and dictionary with one paste:**

1. On the **PC pane tab where your working Refine settings live**, open **⚙ → Import settings**
   and copy its one-line export snippet. Open that same tab's DevTools Console (F12 → Console),
   paste/run the snippet, and copy the resulting JSON (the snippet copies it to the clipboard).
   Alternatively run this equivalent in that PC pane Console:

   ```js
   copy(JSON.stringify(Object.fromEntries(Object.keys(localStorage).filter(k=>k.startsWith('refine_')).map(k=>[k,localStorage.getItem(k)]))))
   ```

2. Transfer that JSON privately to your phone: **it contains your API key**. In `/m`, unfold
   **Refine setup → Import settings JSON**, paste once and leave the field. It imports/persists
   provider, API keys, model/model lists, thinking, **`refine_dictionary`** (include this for your
   AP-ID/symbol spellings), and prompt overrides; cost and all `pane_*` entries are ignored.
   It never rewrites an override you did not import. No confirmation is needed.
3. Or enter the provider, key, model and dictionary directly in the same disclosure; fields
   save when changed. With no saved choice the default is Anthropic `claude-opus-5-5`, medium
   thinking, model-default output allowance. A rejected key can be replaced there without
   clearing other settings. Reading and Send already work before any of this.

To switch off, use **Turn guarded remote access OFF** and the exact Serve-off command above.
The `/oc/*` proxy caveat there still applies: the Claude switch is not an all-path firewall.
Live iPhone, Tailscale and paid-provider acceptance remain Dan's post-activation try.

Documentation lives in Workflowy (🐫 CodeNomad DEPLOYMENT AND USAGE INSTRUCTIONS Dock).

## Checks

Scratch goes in `__repos/oc-scratch/opencode-companion/`, never inside a repository.
Delete your own scratch when the job is done, keeping only receipts a record cites; browser runners delete their own Chrome profiles.
`tests/browser_scratch.py` creates this shared Windows-accessible root (also from a worktree); `OC_SCRATCH` may override it.
Its `profiles/` holds disposable Chrome state, `receipts/` holds DOM/PNG/JSON evidence, and `fixtures/` holds temporary test parents.
Every browser runner cleans its profile in `finally`, retries briefly for Windows locks without failing the run,
and at startup sweeps profile leftovers older than 24 hours. Receipts are not swept.

Run every retained actual-markup browser suite plus the two-editor target regression
through an ephemeral HTTP origin (real same-origin WindowProxy/postMessage, inert
API transport, isolated Chrome profiles, never production 9911):

    python3 transcription-pane/tests/run_browser_checks.py --real-time-markdown --receipt-dir /mnt/e/__daniel347x/__Dan_Root/__repos/oc-scratch/opencode-companion/receipts/pane-browser-receipt

`--suite NAME` (repeatable) runs only those suites, for a targeted check. The receipt retains every `SUITES` Chrome exit code, per-suite check counts and full DOM/
stderr. `phone-caret` always runs through Windows Node and Chrome CDP (`run_tap_browser.cjs`): only a real click places a caret. `claude-mode.html` and `claude-target-stability.html` require HTTP rather
than a `file://` opaque origin for their direct parent handshake.

**New Manager / ↳ acceptance also needs the real pane.** The fixture suites substitute small catalogs;
the desktop OpenCode fixture even places the frame's form in the editor document. Their green receipts
do not prove real catalog requests, production startup/script loading or the actual sessions-frame wiring.
After integration, run the read-only cold-page gate (Windows Node + isolated headless Chrome; WSL paths
passed to Windows Node must use `E:/…`). From the checkout, in Windows:

    node transcription-pane/tests/run_manager_choices_live.cjs http://127.0.0.1:9911 E:/__daniel347x/__Dan_Root/__repos/oc-scratch/opencode-companion/receipts/manager-choices-live

It opens `/` and `/m`, opens ↳ first with Native Claude, chooses OpenCode · manager-gpt ·
`openai/gpt-6.1-sol-fast` · xhigh, then checks New Manager has the identical populated choices and
switches back to Claude. It never presses Send/Start/Launch, selects a conversation or permits any
non-GET/HEAD request. `live.json` retains every option/value, console/exception, failed request and
choice-ready latency; PNGs show both boxes. A green fake-server run is not a substitute. A real
launch, first reply and working tools still need Dan's deliberate live click.
`message-markdown.html` loads the actual local renderer/sanitizer and viewer. It
covers user/assistant tables/lists/code, malicious HTML/links, plain streaming to
final Markdown, selection deferral, unchanged-body parse caching, narrow layout,
`fen` game boards (and unreadable `fen` blocks staying code) and automatic cold-tail replay. `--real-time-markdown` uses installed Windows Node
and isolated Chrome CDP to retain meaningful cold-load/large-message timings;
the other twenty-six retained suites keep their existing virtual-time runner. `teams-break.html` drives
the actual pane and phone pages with real keyboard events (Ctrl+M, Enter, Ctrl+Enter, 1–9, arrows, undo) and
inert storage; `teams-break.test.cjs` pins the exact delimiter, the auto-toggle and Refine's verbatim rule. `phone-manager.html`
loads the actual `/m` HTML/CSS/scripts, renderer/sanitizer and shared Refine core, with inert
host/provider transports. It checks the cold Managers-only list and table, the desktop turn
colours, tool bars (sizes, preview, Copy, live refresh; Readable/Pretty/Raw input, a JSON-then-Markdown
output and a Markdown body with Rendered/Source, sanitizing, a selection deferring a repaint, and Wrap
scrolling inside the reader, never the page; the recorded Diff first in the opened bar with its line numbers,
colors, Wrap, layout, shell/pending/none states, Show all and its reader stopped when the bar goes), thinking, ↓ Latest,
the compact phone text box and growing tablet text box, all four real buttons (⋯ → Paste placement and selection, and After --- refining only the text after the last `---` line, included), selection
Refine → journaled Send → exact clear, end-reaching Refine (whole box, selected tail and After --- with trailing blank
lines) leaving no selection/ring, the caret at the actual end and long text scrolled to the bottom without focus, and
middle-selection Refine retaining its selection/ring in normal and maximized composers; the red thinking reminder at max and xhigh (none at high or below), Send locked
from the press through the POST to the exact ACK (a second tap sending nothing; a refusal, the release and a reload's
receipt unlocking it), failure/cancel/edit safety, uncertain receipt-only
recovery/reload, sleeping reconnect, busy steer, settings-unverified, conditional release,
the ⋯ menu with Interrupt (busy/idle, exact selected target, saved request, refusal, lost reply/reload,
no confirmation or draft changes), read-only Re-check and What Refine sends (opened at the bottom, the draft panes,
a Full prompt equal to the request Refine then sends, a failed context read), the three maximize
views, the combined view's keyboard shell and thumb strip (shown only with the text box focused: the document locked under a
fixed frame that follows the visual viewport and opens at home, the chat one screen tall, the strip covering nothing, one fixed
grip; one continuous drag from home up through the conversation's history to its oldest message and the rows above, and back,
each part handing over to the next exactly; a small drag into the history ending following so a poll leaves it there; history
scrolled by touch carried on from where it was; the strip, the frame and the document never moving; a viewport shift mid-drag
not feeding back; a flick's glide, and a downward flick coming to rest at home; a tap blurring; a finger on a text-box
button holding the shell still (status toasts taking no layout, a keyboard resize and a blur waiting for its click, which lands; a
cancelled touch or a touch elsewhere letting go); ⋯ → Strip diagnostics recording
and copying its log), the View tab (lazy, unsandboxed, loaded once,
Chat and draft kept, only receipts/controls polled while shown) and the Context view (the sliding four-tab row, gauges
from past-the-end events pages, both providers' windows and resets, stale/failed readings, the
last-known context rule), and ＋ New Manager (its place beside maximize, the defaults form, a
refused launch, the exact launch request, the new Manager selected before any message, a first
Send waiting for it to be ready, another Manager chosen mid-launch winning), and the press-and-hold
model/thinking sheet (the desktop's option lists, a moved finger cancelling, the swallowed tap,
contextmenu, the exact saved-then-posted `apply_effort`, a busy refusal with nothing posted, a pending
setter polled by receipt across a reload and never repeated, model then thinking, a refusal, a
sleeping Manager). `phone-programs.html` loads the actual `/m-programs.html` sidebar with
inert reads, including row taps with saved inner states and the two-tap Collapse / Show running,
also as replayed iPhone touch taps (touch pointer events, then the pointer leaving every ancestor,
with the click delivered only if the tapped row is still the one under the finger), a glance's ⤢ Large sheet (over the whole
frame, every message whole in large white type, no read of its own, following the glance's refresh at its newest end or
at the place scrolled to, Close, closing with the glance) and the glance in glare mode (bright, larger, and back), with the
frame storing nothing. `phone-glare.html` loads the actual `/m` and `/m-programs.html` markup and styles with `glare.js`
where they load it, in `<head>`: off at the first paint by default, the ☀ at the end of the views row with the four views
keeping their thumb width and sliding on 390 and 375 px phones, the normal colours and sizes untouched, one tap on (kept as `pane_m_v1:glare`,
greys near white, small print larger), a Programs frame and a View frame opened then starting lit with no storage of their own, a tap off
reaching the frames at once, another tab's change followed, and a reload starting lit before the first paint.
`phone-viewer.html` loads the actual `/m` page with the actual `/m-programs.html` sidebar in its frame:
real Read › / Read conversation › taps open a Conductor, a native subagent and an OpenCode session
read-only (their readers and role labels, tool bodies from each backend's tool-record, ⤒ First /
↓ Latest, a refused other-execution page, the resync after re-issued handles, a message from anywhere
else ignored; a Claude Write's and an OpenCode multi-file patch's Diff, an OpenCode write's label), while the Manager's draft, selection and Send stay untouched until its chip brings it back; a handle tapped in Chat
and one opened from the Programs frame open the View tab (first load by `?handle=`, a loading or loaded View page asked to render it).
`phone-thinking.html` uses the actual `/m` page with inert Claude/OpenCode readers and real phone device metrics
(`run_phone_device_browser.cjs`): default current-round expansion, every assistant/tool/final block, streaming and
hand collapses, folding prior rounds on the next user message, First/Latest and same-conversation resync, keep-open
memory across repaint and re-created disclosures/rows, conversation isolation, saved mode after reload, paused anchors
and Following at the end. `phone-paging.html` loads the actual `/m` page against a positional reader (a 450-row conversation, ≤100-row pages, deltas):
the cold tail; a scroll the page made itself reading nothing; a touch near the top reading the 100 rows above, the row being
read kept in place; pushing on to the very first message with every row on screen and nothing read past it; a live row
arriving meanwhile (earlier pages kept, the badge new); ↓ Latest letting them go and scrolling up reading them again; a
byte-bounded page stepping on without a gap; ⤒ First paging down through the middle to the end and joining the live tail
(its delta read next); and a turn opened from View paging both ways. `phone-caret.html` taps the actual `/m` text box for
real at the end of a long draft scrolled to its end: the caret lands in the last paragraph, and nothing under the finger
ever changes (iOS places the caret after the focus event, possibly later still; Chrome resolves the click earlier and cannot
show the jump itself, so these are the decisive checks): the text column's width, the text's height and scroll and the box's
place on the screen are the same at the focus (the shell already on), after it, after a real tap on the strip hides the
keyboard, and for a real tap into the maximized editor; a ResizeObserver sees exactly two text widths in all (the combined
view's and the maximized editor's). Chrome's scroll anchoring is off on the box and the page scrollbar takes no width there,
as on iOS. `?app=`/`?css=` load another `m.js`/`m.css`: the code before this rule fails it.
`phone-composer` uses actual markup/modules with isolated Chrome CDP device metrics and touch emulation:
390×844, rotated 844×390 and iPad Mini 744×1133. A mocked 450px visual viewport stands in for the keyboard,
without shrinking the layout viewport. It checks the compact phone versus growing tablet draft, keyboard-shell
home and visible conversation room, maximize/restore, layout-free toasts, three-second fading, sticky errors and
the full-note log, retaining numbered JSON checks. Normal and maximized draft checks also model a stale inner
scroll offset during focus: keyboard resizes restore the previously visible end without a focus-time scroll,
while an already scrolled-up draft, a deliberate inner scroll, range selection or mid-text edit releases/avoids
the pin. A compact box's opening tap may land short of the final character; a tall box's earlier caret is left native.
Run just that check with `--suite phone-composer`.
`phone-keyboard-reading` runs the same way at Dan's iPhone page (428×751) with scroll anchoring off on the whole page,
as on Safari ≤ 26 and as on his iPhone with iOS 27 (Chrome's anchoring hid the keyboard shell's jump from every other
suite), and with iOS WebKit's coordinates (every box's client position counted from the visual viewport, `window.scrollY`
where the visual viewport sits; Chrome's own in one scenario): the actual `/m` page, a 70-turn conversation read ten
turns up, and the line at the top of what is on screen of the conversation staying the top line within 8 px (the word
there within a line, the turn at the top unchanged) at every step of a keyboard cycle (the tap; the keyboard as on his
iPhone, one step to 411 px visible with the page panned 340 px, and as Safari's resizes and pans; 1.5 s of polls; the
blur with the keyboard still up; the keyboard gone), then back where it was on the screen; three cycles in a row (the
second untouched, the third after a small touch scroll); Dan's own scroll with the keyboard up kept through a keyboard
resize and the close; the same with anchoring on (no double correction); and a conversation following its newest
message following throughout. `?app=PATH` loads another `m.js`: the code before this rule fails it (on opening the
line lands near the bottom, on closing it jumps about 2,000 px up, and a second untouched cycle carries the stale hold).
`handle-links.html` runs the actual `handle-links.js` on pane markup (prose from the actual message renderer under
`sessions.css`, a tool-bar `<button>`, a `<summary>`, a `<pre>`, highlight.js tokens, a pressable row, a link, a text box):
exactly the handles decorated, the rendered DOM unchanged, hits at each handle and none past a line's end, a click/tap
opening View with the bar, `<summary>` or row under it not acting, a plain tap unchanged, drag/scroll/selection/modifier
left alone, the hold menu (press-only selection lock, the swallowed click, Copy, Open in View), right-click and Escape, the
hover cursor, re-renders and pruning, the three Open-in-View routes, and an 8 MiB output capped at 5,000 ranges.
`handle-links.test.cjs` pins the matching rule.
`view-history.html` loads the actual View page in a frame named as /m's, with a scripted page around it. It covers:
- **roots:** a Programs root with its cards; a root from Chat looked up; a plain handle; a pasted UUID; a Dock; a spec;
  a program root;
- **entries:** added by real taps in the document, never a duplicate; ‹ › and the sheet keep the later ones; a new
  handle cuts them;
- **the strip and the sheet:** no handles inside them;
- **the pop-up:** two cards, the tapped one first; a live handle rerooting with its session active; a line asking to be
  shown in Programs; Show;
- **the turn root:** its card, Show in conversation handing the turn back, and a reload keeping it;
- **storage and reload:** a reload restoring the same entry; a `?handle=…&from=…&active=…` start (its earlier digging back
  ahead of it from Recent roots); the 50-entry bound; a corrupt store set aside;
- **Recent roots and maximize** (over `tests/view-roots-fake.js`, the store in memory): row 3's count, the list dropped down
  under it, the root on screen first with its badge, a card picked, a note typed and saved, the filter; maximize keeping
  only ‹ › and the document and asking the page around for the whole screen, and the same button restoring;
- **layout:** 390 px (three rows, no sideways page) and glare.

`view-history.test.cjs` pins its handle identity, entry names, position cleaning, Recent-roots keys, depth badges and
card times; `glimpse-view.test.cjs` the visits, saves, re-opens, picks, notes, a pane server from before the list and the
three modes (with `/m`'s whole screen); `test_view_roots.py` the store and its route. `phone-programs.html` and
`phone-viewer.html` add each row's handle at its left, `programPosition` (two sessions on one socket, a program root,
the forest), where a tap came from, the reveal (filter reset, ancestors, in view, flash, no glance), and `/m` answering
both through its Programs frame. `phone-viewer.html` also taps a handle in a Manager's message (its turn travels with
it) and shows a turn again, on screen and in another session read from its position.
`desktop-view.html` loads, at 1400 px, the actual editor page in Claude mode, the actual sidebar page in its frame and
the actual View page in that page's `desktop-view-frame` (each View frame given `src` gets the same page as `srcdoc`, its
query inlined), with an inert transport and a 140-row conversation. Real presses on handles cover:
- **the switch:** 💬 first, View loaded only on first use; View over the conversation from below the switch, the
  conversation inert; 💬 back at the same scroll, Paused, and Following kept through a reply that arrived meanwhile;
- **the turn root:** the first handle's URL (handle, where, turn); Show in conversation after ⤒ First (read from
  `fromSeq=111`, in view, flashed); a thinking turn opening its thinking;
- **the sidebar:** a session row's socket as a Programs root, its pop-up's card, Select in sidebar flashing the row with
  View still on screen; a row opened from the sidebar bringing 💬 forward; an entry added in View;
- **the editor around it:** a handle in the inspector (no held call; then the held call's turn, its row paged away and
  on screen, Show in conversation flashing its bar with no new Inspect); the header's 📄 View; OpenCode mode's sheet (loaded once, the
  stored history taken up, a reroot, Close, Escape); the pane taking up the history the sheet changed;
- **layout:** the history rows on top and the thin Controls bar folded under them (a root from outside never opens
  it); opened, mouse-sized View controls; 13 px reading type, no sideways page; no page errors.

`?shot=turn|view-open|popup|socket|reveal|sheet` stops the run at that state for a Chrome screenshot (`--window-size=1400,1000`).
`glimpse-view.html` loads the actual View page (`static/glimpse.html`/`.js`/`.css`) with the extension's actual
marked 15, KaTeX, initializer and `glimpse-md-render.js/.css`, which the fixture server's test-only `/nexus-md/` alias
serves from the NEXUS checkout in `NEXUS_MD_CHECKOUT`, else `pane-glimpse.json`, else NEXUS main (it says so loudly
when `glimpse-md-render.js` is absent there), with an inert bridge transport: the exact requests (Dock/group picks,
Node/Subtree/Dock presets, Parcel/Estate specs, options), the compact phone default and Full, painted HTML equal to
`GlimpseMd.toHtml`, raw HTML (an inline handler included) straight in, table/code/math/money/entities, Source find
(count, wrap, the grammar's errors), Copy MD whole, Refresh (never a cache refresh), last successful after a no-tab error,
the newest choice winning, the 200-block bound and Show all, 390/375 px and wide, glare following, a partial or failed
catalog and a missing renderer file; and the row list from the daemon's real `include_rows` replies
(`tests/fixtures/glimpse-rows/`, recorded from S1's code): painted by the real `renderRowList` (spied, never replaced),
its row DOM equal to the extension's row oracle (case `canonical-no-icons`, America/New_York), no icons, a tap's exact
focused spec, a repeated node's two occurrences, taps A/B answered in both orders, a failed tap's labelled snapshot, an
old daemon's late reply, old→new daemon, the 200-row bound and Show all without a request, fold, 390/375 px and wide,
touch-only taller rows and glare on the painter's inline day labels; the layout (no header, the history rows on top,
the thin Controls bar open while empty, folded by a pick, opening above a pushed-down document; `?shot=layout-collapsed|
layout-open|layout-open-chooser`); then the two pickers with Dan's real favorite Docks
and slice-group names (`tests/fixtures/view-pickers-catalog.json`) at 390/375 px and in the desktop embed: one unwrapped
row each, the chooser's width, sideways scroll, the Docks' nesting, closed sections, a section's [n] order and a pick
(`?shot=pickers-docks|pickers-groups|pickers-desktop` stops there for a screenshot; `capture_phone_shots.py` has
`glimpse-docks`/`glimpse-groups`). `glimpse-view.test.cjs` checks the same controller offline in a VM, the row list and
its race matrix, the pickers, Saved/Document order, Full without a pasted `since`, raw_text and the notices included;
`view-pickers.test.cjs` runs the extension's own `parseSliceGroupName`/`buildNestedDisplayItems` (taken from the NEXUS
checkout the pane serves) beside the pane's copy, code and output, and skips that comparison, saying so, without one.
The image suites (`glimpse-lightbox-fixture.js`) load the actual View page with the extension's actual renderer,
`glimpse-lightbox.js/.css` and the vendored PhotoSwipe, real PNGs (`tests/fixtures/lightbox/`, written by `make.py`) at
`/files/mnt/z/lightbox-fixture/…` through the fixture server's test-only alias, and an inert transport for the render,
`/api/files/meta` and `/api/files/list`: `glimpse-lightbox` (390 px in `/m`'s frame: auto-open, immersive, synthetic
swipe, pinch, pan and double-tap, the chrome, the missing slide, chips, filmstrip, a `%20` path, Back),
`glimpse-lightbox-sheet` (the contact sheet, a missing tile, taps, Esc, a fast reopen, Refresh, a mixed document, live
tiles, inline images, Source, no meta reply), `glimpse-lightbox-desktop` (1400 px: keys, 1:1, + − 0, Space, ⛶, a click
outside, the fit clearing the chrome), `glimpse-lightbox-live` (live folders and globs, then a tapped estate of
galleries, at 390 and 1400 px) and `glimpse-lightbox-compare` (side by side: not offered at 390 px; at 1400 px two and
three groups, stepping, a nudge, Line up, the shared zoom and pan, chips, the lightbox over it, Back, an estate's live
groups). Two fixture-only shims: headless Chrome under virtual time runs no animation frames in the frame, so a frame is
a 16 ms timer, and reduced motion is on. `?shot=` names for screenshots are listed at the fixture's top.
`glimpse-view.test.cjs` also covers the image wiring (one meta read, auto-open, listings, tapped estates, side by side
offered only on a wide page). `test_files_routes.py` covers `/files/` (exact bytes, HEAD, chunks, the 304s, a new ETag
after a rewrite, `E:/` and `%20`, named 404s, 400/405/403, the guard), `/api/files/meta` (each format's size, EXIF
rotation, reasons in order, the request shape, the real bake-off PNGs) and `/api/files/list` (natural order, `*` `?` `**`
`*/` and a literal `[`, empty and missing paths and wrong kinds in order, `E:/`, `%20` urls, truncation, the guard, the
real bake-off folder). The module's pure parts (paths, live kinds, captions, labels, groups, the gallery test, keys, side
by side's picks, stepping, nudges and geometry) are `node tests/js/test_glimpse_lightbox.js` in the NEXUS checkout.
`test_phone_remote.py` covers `/m`, `/m/`, `/m-programs.html`, `/api/usage` and
`/api/opencode/{events,tool-record}` accepted with the configured Serve headers and refused with remote
OFF or foreign headers, plus each provider cache's single-flight, gap and provider `retry_at` gate (a
forced read included), the roster of two independent provider reads, and the provider age riding beside
the pane age; `test_opencode_conversation_api.py`
checks the OpenCode tool-record against the desktop inspector's own reading of the same bytes.
`test_pane_chatgpt_switch.py` covers the native GPT adapter and immutable receipts, including process-lock
serialization, lost replies/reload, external selection, fixed saved-provider resolution and actual-wire
loopback/Serve/schema guards. `test_pane_chatgpt_usage.py` covers native/mapping/home-bound caches,
late/remapped answers, current-active versus viewed provenance, unknown/legacy readings and independent
single-flight/refresh/backoff. `scripts/test_chatgpt_accounts.py` covers the public native/mapping reader;
`chatgpt-accounts.test.cjs` covers the real controller. The focused `--suite chatgpt-accounts` browser
check loads real desktop and phone markup/controller/CSS at 1400/420 and 390/375 px, including view-only,
unmapped Switch, pending/lost/reload outcomes and default-poll isolation. No fixture activates a real login.
`test_pane_accounts.py` covers the Claude account switch offline: the CLI status normalization (labels,
epoch and the public identity only — never a login folder path or a token), the one durable operation's
phases (waiting → switching → active, a refusal that leaves the account alone, a failure after the signal),
the same-operation-id and busy refusals, the one-live-Manager resume, a pane restart reading the same
record, and both routes' same-origin/Serve guards; `account-switch.test.cjs` drives the actual
`static/account-switch.js` in a VM: account-name taps never POST, separate active/viewed state,
account-labelled percentages/reset countdowns, foreign/late-response isolation, passed-reset unknown,
a stale rate-limited reading keeping its windows with the provider age and a waiting Refresh, a 429
before any reading as a quiet note with its retry countdown, ordinary
taps never forcing a read (only Refresh does),
the explicit label-only Switch, waiting → switching → active, waiting Cancel (also a lost reply),
same-ID reload recovery, and unavailable-selector/refusal text. The actual-markup phone/desktop
suites also exercise these controls with inert account/status/usage transports.
`test_claude_launch.py` does the same for New Manager's routes (the saved first messages included), then reads the new Manager
back from `/api/claude/sessions?role=Manager` before it has any message.
For a look at the phone page itself, `python3 transcription-pane/tests/capture_phone_shots.py
--out /mnt/e/__daniel347x/__Dan_Root/__repos/oc-scratch/opencode-companion/receipts/phone-shots` writes iPhone-sized (390×844) PNGs of chat, a chess move with its `fen` board, an open tool (an Edit's Diff and
Readable input), its Diff alone (`tool-diff`) and a large one at its first bound (`tool-diff-large`), a Write whose Markdown content renders inside its JSON, a JSON-then-Markdown output, Wrap off, thinking, a
refined passage, each maximize view, the ⋯ menu, What Refine sends in both modes, the ＋ New Manager
form (folded and open) and the new Manager selected, the model/thinking sheet, Context, Programs before, after one and after
two taps of its control, and three sessions viewed read-only (a Conductor, a worker's open tool, an
OpenCode helper), a busy worker's open glance and its ⤢ Large sheet, and the View tab (`glimpse`: a Dock after Full;
`glimpse-compact`: its cold headings-only default; `glimpse-rows`: its row list by modified with a row tapped;
`glimpse-source`: Source with find; `glimpse-large`: a large group at its
200-block bound; `glimpse-wide`: the page on its own, run with `--size 1280,900`) on the actual View page and the extension's
shared renderer, from the offline fixture
`phone-ui-demo.html`. Any of them also runs as `glare-‹view›` (the ☀ tapped first): the default set adds thirteen, and every
glare view is swept for text left dim (luminance under 0.55, or 0.3 for a meaning-coloured red or green; any faded
opacity), failing the shot if any is found; `--sweep` counts the same in the normal views (the before picture). On the desktop, `claude-voice.html` checks the Refine-context View's
draft panes and its Full prompt against the request Refine then sends, and `claude-mode.html`
checks that it opens at the newest context.

History checks: `node --test transcription-pane/tests/claude-sessions.test.cjs`
and isolated `tests/claude-sessions.html` at16000ms virtual time. The real-DOM fixture
loads actual history HTML/CSS/scripts with mocked transport; require process exit0,
`data-test-passed="true"` and zero failed checks. It covers safe complete >200K prose,
shared preview/counts, density/filter, keyed mount/pixel anchor/append/Jump latest,
ACK highlighting/Close, absent editor/target ACK and420px layout. Node/actual ephemeral
HTTP also connect production history → Handler → one mailbox → reader → existing
inspector with exact raw Copy/counts, plus actual sender/receiver channel owners.
Named wrong-adapter/guard mutations must fail real assertions, not just import errors.
Use the runners' unique disposable profiles under `oc-scratch/opencode-companion/profiles/`, never Dan's profile/live9911/9899.

Live-mode checks additionally use `tests/test_claude_live.py`,
`tests/claude-mode.test.cjs`, `tests/claude-live-sessions.test.cjs`, and the actual
markup `tests/claude-mode.html` / `tests/claude-live-sessions.html` at16000ms.
They cover dense40-call pages, host/history overlap, durable reconnect/gaps,
partial→final/replayed/result pairing, old paging, scoped context exclusions,
live selected inspector settlement, real DOM selection/focus/scroll, view-only
Claude refusal, hidden-embed polling and narrow/full-width layout. `claude-manager.test.cjs`
and `claude-manager.html` add registered Manager receipt/control/recovery/race checks.
`claude-reconnect.test.cjs` adds the batch B pane flow: Send to a sleeping Manager reconnects,
waits for readiness and delivers one new journaled Send to the rebounded generation; a refused
reconnect, an edited draft, an edit away/back, a retarget (including away/back and after the
rebind), a never-writable generation, a pending-recovery busy refusal and an
unreconciled earlier Send each leave nothing sent and the draft intact (a native-origin turn,
queued turn, permission or native retry takes the message as one host steer); ownership-unknown,
worker and history targets gain no reconnect. `test_claude_reconnect.py` adds the identity-only
facade and HTTP contract, the sleeping/ended lifecycle, expected-generation pass-through (live
newer reuse; stale expectation against an ended transport refuses) and ten-concurrent-reconnect
idempotency (one launch, no Send mutation). `claude-sessions.test.cjs` adds the sleeping
row/label and the no-reconnect rows; the actual-markup `claude-manager.html` fixture adds
sleeping → reconnect → one new-generation Send, and `claude-sessions.html` the sleeping row. These are inert
transport fixtures; Dan's actual forty-input scanning/dictation ergonomics and
production activation remain unperformed.

    node --test transcription-pane/tests/*.test.cjs   # from the repository root; no browser or server needed
    scratch=$(mktemp -d /tmp/opencode/pane-offline.XXXXXX)   # fresh short isolated HOME for the test run
    HOME="$scratch" python3 -m unittest discover -s transcription-pane/tests -p 'test_*.py' -v
    python3 -m unittest discover -s ../manager-sessions/claude/tests -p 'test_*.py' -v   # independent launcher tests: text/composition only

Use a fresh, short isolated HOME so test writes to private state stay out of a real
user's paths and cannot overlap anything live; delete that isolated HOME after the run.
Inspector and route tests create their own parents under external `fixtures/` and clean their temporary children. The launcher suite
needs no HOME, no native launch and no inference.

First-class OpenCode Manager coverage is in `test_opencode_managers.py`,
`test_opencode_manager_inventory.py` and `scripts/test_oc_client_ids.py`; browser-side journal,
desktop target/race and phone rules are in `opencode-manager-journal.test.cjs`,
`opencode-manager-target.test.cjs`, `opencode-managers.test.cjs` and
`phone-opencode-managers.test.cjs`. The actual-markup suites `opencode-managers` and
`phone-opencode-managers` use the real pages with inert protocol/provider fixtures; native Claude
and old OpenCode suites remain regression checks. Branch receipts retain exact runner selection,
commit/tree/dirty state and limitations; only a clean matching tree qualifies a whole commit.

Python tests mock OpenCode or use ephemeral loopback HTTP stub servers; no live prompts, credentials or paid calls
are involved. Node tests run offline VM/DOM doubles, not a live browser. Provider safety tests cover limit/incomplete/
refusal/empty/missing statuses, cancellation, full-original retention with denied clipboard and quota failures,
multi-block/normal output, model-specific budgets/explicit overrides, recovery restore/eviction, and Send
text/target/queue/thinking regressions. Anthropic stream fixtures cover output beyond both former caps (70,000 mock
output tokens / over 420K characters), split Unicode/SSE framing, errors/EOF/malformed streams, cumulative usage,
terminal completion, cancellation/pending reads/+30s, and no partial input or result-clipboard replacement.
The extension's `tests/watcher.html` checks the installed-version selector structure in isolated headless Chrome.

Selection-context regressions: `tests/refine-context.test.cjs` covers all three providers, real OpenCode/native
context reads, separate chat-history and draft blocks in draft order, the draft sent whole beyond the chat budget,
the 256 KiB per-side ceiling with Unicode-safe nearest-end cropping, selection edges/whitespace, original
recovery/edit guards, saved prompt overrides, the formatting and emoji rules of the default prompt and both Refine
entry points. The retained actual-markup `tests/refine-context.html` suite runs through `run_browser_checks.py` with
inert history/provider transport and checks real textarea ranges, button focus, Alt+R/Alt+Shift+R, block order,
replacement and recovery, and the red thinking reminder under the status at max and xhigh (none at medium or high).

Inspector checks: `tests/tool-inspector.test.cjs` covers independent state, last-click/close/stale responses,
streaming/terminal/error, full Unicode/falsy/empty/stub data, safe rendering, text selection/scroll/resize/copy,
shortcuts and memory limits. `tests/test_tool_inspector.py` adds mailbox and ephemeral authenticated HTTP tests
for origins/schema/CORS, fixed-backend association, ordering/close/heartbeat, missing/offline/oversize records
and concurrent reads. `tests/tool-inspector.html` runs the real pane markup/CSS/inspector plus real shortcut
wiring with mocked transport and inert Send/Refine spies: use isolated headless Chrome with
`--allow-file-access-from-files --virtual-time-budget=16000 --dump-dom` on its file URL; require
`data-test-passed="true"`. It checks narrow/wide layout, dividers, scrollers/text selection, focus, copy,
shortcuts, collapse/close and terminal polling, and the Diff with the real reader (recorded line numbers and
colors, Auto split/inline, the layout choice, Input's Wrap, raw Copy/captions unchanged, a selection holding a
change, replacement, the shell label, Show all, both backends). `diff-view.html` checks the shared reader alone.
`python3 transcription-pane/tests/run_browser_suite.py --receipt-dir /mnt/e/__daniel347x/__Dan_Root/__repos/oc-scratch/opencode-companion/receipts/‹dir› ‹suite›…` runs only the named
`SUITES` with the same runner and pass rule. Real pointer capture is VM-tested; physical drag ergonomics,
real authenticated association and live remount/virtual-scroll/session-switch behavior still need Dan's check.
JSON tests also cover exact raw OFF/copy, independent defaults/selection changes, numeric/string lexeme
fidelity including structured wire values, mixed-prefix suffix preservation, invalid/ambiguous input,
format/expansion bounds, selected-text repaint deferral and a deterministic nested JSON corpus. Browser
fixtures exercise the actual labeled controls and narrow/wide layout with mocked requests; physical keyboard
activation and real tool formatting/copy remain live checks.
Readable/size regressions additionally cover nested documents/tabs, one-layer backslash literals, keys vs values,
array indexes, duplicate keys/numeric/suffix fidelity, exact raw Copy and UTF-8 byte captions across toggles,
safe document DOM, selected-text repaint deferral, reading limits and narrow split-view controls. Python HTTP
fixtures prove exact backend part-wire forwarding and the fixed shared-script route without real credentials.
