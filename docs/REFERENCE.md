# Operator reference

The working technical reference for this repository (formerly its README; the front page is now [`README.md`](../README.md)):
the Manager CLI, the seat machinery, the OpenCode layer and the Claude Code host, as they run day to day.
**Canonical documentation lives in Workflowy:** the OPENCODE-COMPANION Project Map
(`ba1ea3f1`) and the 🔥🔥🔥 OpenCode Deployment/Usage Instructions Dock (`6e3035ea`).

## What lives here

| Dir | What lives here |
|---|---|
| `scripts/` | The **Manager CLI** (`oc_*.py`, all on `oc_client.py`, the one client of the OpenCode server API): the boom `oc_view`, `oc_steer`, `oc_launch`, `oc_preflight`, `oc_peek`, `oc_seal`, `oc_session`, `oc_sessions`, `oc_shelves`, and `oc_quick` (a QUICK PROGRAM in one command: root, job, worker, Board row and Conductor). The **seat machinery** (`oc_seat` on `oc_pack`, `oc_measure`) and the conductor's WSL wrappers (`oc_seat.sh`, `oc_self_resume.sh`, `oc_map_refresh.sh`). The **restart and upgrade scripts** (`restart_oc_server.sh` → `opencode_server.py`, `oc_upgrade.sh`). Live checks: `oc_cache_check.py` (the ChatGPT plan's prompt cache hits through OpenCode, in a session and on a fork's first turn) and `oc_mcp_silence_test.py` (a long, silent MCP call survives). The tests (`test_*.py`). |
| `proxy/` | The **payload proxy** in front of API-key providers (Anthropic, OpenRouter, xAI, Moonshot) and the ChatGPT plan's installed OpenAI route: cache/effort handling, 429/5xx backoff before OpenCode sees it, the request log. |
| `plugins/` | **OpenCode plugins:** the tool-output guard (`tool-output-stub.ts`), `context_status` (`context-status.ts`), the MCP fetch timeout (`mcp-fetch-timeout.ts`), the ChatGPT cache affinity (`chatgpt-cache-affinity.ts`: sends the `session-id` header that OpenAI routes the plan's prompt cache by; keep it while the plan uses the Sign in with ChatGPT login), and the OpenAI usage tap (`openai-usage-tap.ts`: one line per ChatGPT-plan call in `~/.local/state/opencode-companion/openai-usage.jsonl`, with the usage OpenAI reported, cache reads included). |
| `agents/` | **The cast** (`agents/agent/*.md`, each with `model: provider/model#variant`; the workers are `mode: subagent` and deny only native dispatch, code mode, questions and skills), **the Manager** (`agents/agent/manager.md`, `mode: primary`: its body is the whole Manager system prompt, the default agent in the `manager-sessions` project), and `AGENTS.md`, the worker instructions, installed to `~/AGENTS.md` so it reaches every session in the home folder and never the Manager. File editing and web search/fetch are allowed for every role. |
| `config/` | `opencode.json`, the master config. |
| `packages/` | `seatbases.json` (seat-base handle → parked session id) and manifests. |
| `chrome-extension/` | Home for the Chrome extension for the OpenCode web view (README only so far). |

## How it runs

- **OpenCode runs as its own background service in WSL:** 127.0.0.1:4096, managed with `opencode service …`,
  Basic auth (user `opencode`, password in `~/.config/opencode/service.json`). Plain `opencode` attaches to it.
- **Install this repo's config, prompt, plugins and agents, and restart:**
  `wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/restart_oc_server.sh"`
  (`--check` checks the candidate config only). What it does: `docs/server-lifecycle.md`.
- **Upgrade OpenCode:** `scripts/oc_upgrade.sh --check`, then without `--check` (`--rollback` undoes the last one).
- **The Manager CLI runs through one launcher**, from any folder: `bash scripts/oc.sh ‹name› …` (`oc.sh view "[ap:PROG]"`
  is the boom; from a Windows shell, put `wsl` in front). The conductor's wrappers run in WSL. Every edit is committed and pushed.
- `oc.sh session ledgers` shows each Ledger's held items as groups (name and count; an item joins a group with ` · group=‹name›` on its Curation line) and every open item still needing a fate; `items` lists a group's items by condition, `curate` appends fates or groups for chosen items or whole groups, `bundle` promotes any chosen items into one new AP. `--full` prints whole notes; repeatable `--program "[ap:X]"` scopes any of them. Details: `oc.sh session ledgers --help`.
  Ledger content, discovery and stamp checks read the daemon's existing in-memory cache, not the Workflowy API.
  For a multi-command pass, `oc.sh session ledgers batch --file curations.json --session 227` takes a JSON array of
  argument arrays, e.g. `[["curate", "--program", "[ap:P]", "--ids", "CF-01", "--fate", "forgotten · done"]]`.
  It shares one daemon connection and one invocation-local read set (never a persistent cache), runs in file order,
  updates later selections after its own successful appends, and stops at the first unsuccessful command. Existing
  single-command forms and their output are unchanged.
- **Tests:** `python -X utf8 -m pytest scripts -q --ignore=scripts/test_opencode_server.py --ignore=scripts/oc_mcp_silence_test.py`
  (Windows; `oc_mcp_silence_test.py` is a live check that pytest would collect by its name) and
  `python3 -m pytest scripts/test_opencode_server.py -q` (WSL).
- **No version gates:** `scripts/test_no_version_gates.py` fails when non-test code anywhere in the repository
  compares against, or allowlists, an exact version of an external tool (Claude Code, OpenCode, CodeNomad): a tool
  update must never silently switch a feature off. Check the capability the code relies on instead, failing closed
  per record or process, or keep working with a visible warning.

## Read once, independent seat jobs

Use a real tool-reading pre-pass on the model that will do the jobs (OpenCode `ses_…` or native Claude `cc-…`):

```bash
bash scripts/oc_seat.sh dispatch --agent sol61-xhigh --lead --pre-pass \
  --model openai/gpt-6.1-sol-fast#xhigh --socket '[ap:READING]' --program '[ap:PROG]' --no-wait
bash scripts/oc_seat.sh wait RETURNED_BASE_ID --max 540
bash scripts/oc_seat.sh fork --base RETURNED_BASE_ID --socket '[ap:JOB1]' --no-wait
bash scripts/oc_seat.sh fork --base RETURNED_BASE_ID --socket '[ap:JOB2]' --variant high --no-wait
bash scripts/oc_seat.sh resume RETURNED_BASE_ID --extend --socket '[ap:MORE_READING]' --no-wait
```

Copy every returned id. The reading socket assigns background reading and a stop, not author work. Fork the
**completed base directly for each independent job**, never the previous author. Each job has its own authoritative
socket; the inherited conversation, including settled tool results, is already-read background, not permission to
continue the base's task or write its socket. A job's socket may still require current/additional reads.
`--extend` deliberately adds reading to the **same base**, preserving its original socket. Wait for that extension
before another fork: later forks inherit it; earlier jobs do not change. Manifest-built `sb-…` snapshots/helpers
remain a separate path. One pre-pass serves one model; a fork may change thinking level before its first prompt.

`wait` and `info` show the same first **NEW model call** account (tools allowed), against the last completed call
in that fork's copied history: raw input/read/write/full-input, **new-input cached %** and **base-input coverage %**.
Coverage is approximate and can exceed 100%. OpenCode labels ≥90% coverage HIT, <10% MISS, otherwise PARTIAL;
Claude retains its separate 95% convention. Missing evidence says PENDING/UNAVAILABLE with a reason. A MISS changes
cost, never permission or inherited reading; recall alone is not a HIT, and a HIT does not prove socket obedience.
Level/date/runtime/provider retention can make a fork cold; no TTL or universal warmth is promised. Follow-up spend
and text remain follow-up-only; any displayed fork account is labelled original, not follow-up warmth.

`oc.sh cache_check` keeps its simple-turn diagnostic; its opt-in feature smoke is for explicitly owned scratch
inputs and real post-merge proof, not something ordinary dispatch/wait runs automatically. Upgrade `--check`
reports schema/SOURCE compatibility, not provider-cache retention or candidate-binary execution. Script-only seat
changes need no service restart; installed plugin/config changes still use the existing activation procedure.

## Unattended execution readiness

The separate native Claude transport foundation is `scripts/claude_host.sh` / `claude_host.py`:
a persistent private local host on the official Linux CLI/Max login, with exact send receipts,
raw replay and allow-by-default logged stdio permissions. It is independent of the pane and
OpenCode; supervision, verified native controls and guarded Manager pane Send are implemented,
but production activation and the whole-Workflowy-result check remain operator gates. Start/client/state
contracts: [Claude headless host](claude-headless-host.md); launch, activation, receipt/incident
review and daily-driver checklist: [Claude Manager](claude-headless-manager.md). Its maintained
per-run profile is `config/claude-headless-settings.json`; global Claude settings/login stay unchanged.

`bash scripts/oc.sh permission_preflight` checks actual Location policies, MCP readiness and pending
native permissions/MCP Forms without approving or activating anything. Execution certification
requires fresh stored native/SSH sentinel tool receipts; a clear queue alone is not PASS.
See [the no-click preflight and rollout guide](unattended-execution.md) for the scratch-only
payload, route matrix, held activation and rollback. Repository policy edits remain **prepared**
until the normal idle-only restart, then need post-activation certification.

## The boom

`oc.sh view` (or `oc.sh view --running`) lists server-wide active jobs/sessions once, omitting only
positively identified Manager conversations, grouped by
program/Manager ownership with task, agent and model/thinking. It reads no conversation histories or reports
and pushes no modal. By default it makes no Workflowy calls; `--verify-programs` opts into bounded
small-root registry/name verification (not with `--no-workflowy`).
Unverified/unknown ownership stays visible; unavailable live status is an error, never “zero running”.
`oc.sh view --recent 10` (or `--r 10`) selects **ten logical entries by latest observed update/activity,
not creation**: all observed conductors and owned workers/helpers of one program count once; each
independent Manager-direct job counts separately, with its lead worker's messages and its own helpers.
Manager conversations are excluded **before selecting N**, so they consume no recent slots; Manager labels
remain job provenance, not conversation summaries. Explicit `--title` / `--sid` still select Managers,
including alongside `--recent N` without consuming its slots. Grouping/deduplication happens
before limiting, across the complete session inventory. Title-only identity says **Group (unverified)**;
registry/explicit metadata establishes program ownership. Unclassified/conflicting ownership remains visible.
Recent makes only bounded small-root identity/registry checks (up to four roots); `--no-workflowy` skips
even those. It does not load reports, program state or push a modal unless program roots are explicitly named.

Running and recent share a lean entry header and one line per selected session: SID, live status,
role/agent, model/thinking and title. Recent shows active sessions, or the newest conductor/lead and
up to two newest workers when none are active. It fetches no histories. Program headers use the
same ownership/verification grammar in every mode; unavailable evidence stays unverified.
`oc.sh view "[ap:PROG]"` retains program state, registry, Ledger count and the modal, and shows
the last three registry conductors (`--conductors N`), without an always-shown -001 or ignition.
An older conductor's final is the last assistant text of the turn spanning successor creation:
the last assistant text before the first later incoming non-assistant message (a return or steer).
If the conductor was already idle when the successor was created, it falls back to the last
pre-creation assistant text; missing evidence says UNKNOWN. Later wake-ups collapse into one line.
Human report accounts retain both ends, cropping the middle.
The current conductor shows ten recent first-line messages (`--lines N`). Workers are program-wide:
five newest finished/idle workers (`--finished K`) plus every live one, with human accounts or last-assistant
fallbacks, dispatch conductor and activity age. Idle is observation, not proof of accepted completion.
Report/account reads are guarded and count/byte/deadline bounded; unavailable reads use visible fallbacks.
Accounts served from export archives remain readable and are labelled `(archived)`; the modal shows the live part only.
`--workers current|all|N` changes the recent worker selection (live workers remain included).
`--detail` restores compact conductor blocks, linked rosters and message summaries for recent/named views.
`--full` requests `--bytes` tails for every owned session, excluding parked bases.
`--sid` still selects exactly the requested session, children included;
`--title` uses the same grouping, including Manager-direct native jobs. No program identities are created by viewing.
**Both backends:** Claude Code sessions (`cc-…`, on the headless host) appear wherever OpenCode sessions do. The host's
records supply title, ownership links (program, socket, dispatching conductor) and live status (`busy`, `bg-wait` =
only background tasks, `starting`, `recovering`, `unknown`, with the host's reasons); each session's native Claude Code
transcript supplies its messages (`scripts/oc_claude_view.py`). `oc.sh peek --sid cc-…` and `oc.sh search` read the
same transcripts. An unreachable host is said in the header, never read as "no Claude sessions".
Running mode cannot be combined with these selectors or detail/modal options. Script changes apply immediately,
without a server restart. Canonical usage: Workflowy [ap:8Z49BK].
A named program's compact view ends with its newest implementer telemetry rows (below).

## One program's For-the-human pages

```bash
bash scripts/oc.sh human "Millions of events: keep"
bash scripts/oc.sh human "pre-seeded" --modal
bash scripts/oc.sh human "[ap:5CKYAT]" --no-view
```

`human` matches a literal, case-insensitive program-name fragment under Manager Sessions, or reads a root by
AP-ID, UUID or its 8-hex handle. Several matches print every candidate's title, handle and Manager Session,
then stop without pushing; use a longer fragment or a handle. Name lookup excludes exported archives.

Stdout is one copy-pasteable formal parcel (a `render_spec` for `glimpse.run`, an estate entry, or View's
**Parcel** box): the same `prog-human` + `fog-dan` slice as **📡 PROGRAMS**, rooted at this program, with full
slice postfix policy and **modified, oldest first** saved in the parcel. Only human-account notes appear;
ancestor headings keep their place in the program. Suppression above the root, or on the root itself,
does not hide those accounts. Nothing creates a named group or changes PROGRAMS.

By default it publishes a named card through the Manager App's existing `POST /api/view/roots` (`visit`).
On either phone or desktop, open **View → 🕘 Recent roots** and choose the program's **🗣️ For the human** card;
the phone opens the bodies too. This shares the root immediately, not an automatic switch of an already-open
document. `--no-view` skips publication. `--modal` additionally uses the boom's existing modal push, with the
same tags and modified order, in the daemon's connected/default Workflowy tab (`--tab ALIAS_OR_ENDPOINT` picks
a particular connected tab). Diagnostics go to
stderr; even if View is unavailable the parcel prints and a requested modal push is still attempted (exit 1
reports a failed destination; exit 2 means no unique program). Script-only activation: no restart.

## Manager forks and forklifts

A Manager on **any OpenCode model**, or on Claude Code, forks its own conversation in one call:

```bash
bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc.sh seat quick --fork-self --title "fix X"
# A sub-session's stamp goes before quick:
bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc.sh seat --session 227-a quick --fork-self --forklift --title "build X"
```

Run it with the OpenCode **`shell` tool's `background: true`** (Claude: Bash `run_in_background: true`,
`timeout: 3600000`). No socket, job file or copied session id: the fork's job is the conversation up to this call,
including this turn's reading. It retains the native agent, model, thinking level and cache affinity;
`--variant V` changes only the fork's level. A quick fork worker writes a result AP in the session Dock;
`--forklift` instead prepares and launches the one requested program, doing its own fresh PARALLEL PROGRAMS reads.
The call is its doorbell: worker completion or forklift launch/no-launch, with the final message printed.
`--doorbell program` follows a quick program's Conductor to its first stop; `--doorbell none` rings nothing.
Re-arm a forklift with `oc.sh seat wait --forklift ses_… --max 3000` (`cc-…` on Claude).
Codes: 0 done/launch, 2 still running, 4 needs attention/no launch, 5 UNKNOWN, 9 handed over.

OpenCode already sets `OPENCODE_SESSION_ID` on its shell tool. The script joins its native shell's kernel pid
to this process tree and its `shellID` to the exact history tool call (title and forklift kind included).
It waits for that background call's assistant step to settle, then uses OpenCode's own exclusive `before`
boundary at the next message: no lost current-turn reads, no copied later Manager step, no source-history edits.
An accidental foreground call copies the completed current-turn history before its still-running step and returns
the ordinary manual re-arm warning; use background for the through-call snapshot and automatic bell.
The Manager-only test, 55% context limit and 12-live-fork cap apply; a per-call receipt under
`~/.local/state/opencode-companion/manager-forks/` prevents replay even if the native fork reply is lost.
Claude's Max quota/seat guard stays on Claude launches; an OpenCode fork never requires a working Claude host.
The older `--fork-from ses_… --socket "[ap:JOB]"` route remains for quick programs and manual boundary forks.
Scripts and Workflowy instructions apply immediately; edited agent prompts activate at the next idle OpenCode restart.

## Manager doorbells

`oc.sh doorbells` lists the calling Manager's live background tasks and the forks,
forklifts and top-level quick seats it started that are still running, plus programs its
forklifts launched (both backends). Each start is **watched** or **unwatched**, with an exact
re-arm command for the latter. Nothing is registered, stopped, sent or re-armed by listing.
`--manager ses_…` or `--manager cc-…` selects another conversation, `--session NNN` (or `NNN-x`) a Manager-Session label,
and `--all` every Manager. Fresh seats carry only that numeric provenance stamp, not an
exact creating-conversation id; the output says so. Unknown task targets/liveness, queued
inbox work and unavailable backend reads are explicit. A launch-only forklift bell does not watch its running program,
and a program bell does not follow a successor Conductor. No restart required.

## Archive cut at a wave boundary

`bash scripts/oc.sh archive_cut "[ap:PROG]" [--list]` previews an earlier-record cut; `--apply` moves it.
The closing Conductor must first write `keep_writable.written`, `closed` and `nodes` in ConductorState.
The planner keeps fixtures, standing inputs, current Briefs, named writable/active/in-flight nodes and anything
created or modified since `written`. It moves whole root-child records and whole program Working-notes children,
never splits a kept subtree, and keeps a root wave (or a unit in a program without waves) until it has a direct
`⚖️ CONSOLIDATION · accepted · FINAL` child; that FINAL's review lineage may follow it.
`--list` expands the Working-notes move list. An interrupted cut resumes into the same named sibling Dock.

The script never edits the program root/state, exports or deletes. After `--apply`, the Manager adds the root's
earlier-record link and follows the printed cache-refresh/export calls; only a `verified: true` export permits
the printed delete step. Full procedure: Workflowy [ap:8Z49BK], *Archiving a live program at a wave boundary*.

## Telemetry: context and wall clock per phase

`oc.sh telemetry "[ap:PROG]"` prints one line per implementer job segment; `--sid SID` does one session
(`ses_…` prefix or `cc-` + ≥8 hex). Both backends; read-only (OpenCode's database and the Claude host's
database with `mode=ro`, native transcripts); **no Workflowy calls**.

    N07 · ses_eac38000a · Sol Fast · Leg 6 · start-up 50.3 KT · edit@57.2 KT · report@427 KT · end 481 KT · 0/0/96/14 min · ⚑2

Context is occupancy in **KT** (input + cache read + cache write of the response issuing the boundary call):
start-up = at the first source read, edit@ = the first real edit, report@ = the first write to the worker's own
socket subtree after its last file edit. Minutes are loading / source recon / implementation / finalization.
Source means contents, not glob/ls/find filenames, directory listings or empty/failed searches. Printed Python
`-c`/heredoc file windows (including guarded output), `git show REV:path` and observed `git grep` code hits
(timeout/env wrappers included) count at the issuing call. Shell-derived boundaries remain ⚑; unresolved content
searches, dynamic Python paths/conditions or unsupported output transforms get `b1_uncertain` without guessing B1.
A follow-up job in the same session shows `carried` context, never start-up. ⚑ counts review flags (a boundary
resting on shell parsing, or report writes before the last edit); `--full` shows each boundary's message index
(`oc.sh peek --sid … --at N` checks it), the phase table, growth by activity, largest items (KB), helpers and every
flag. `--json` is the same data, one record per segment plus a roll-up (schema `oc-telemetry/1`, for the pane).
Only implementers' job-context phases are classified in this default view. **The pane** shows it on every
program-tree row (desktop sidebar and phone Programs) through `pane_program` / `pane_session`, the library entry it
calls in-process: these same fields for implementer jobs, and for every other role only its context at each job
segment's first and last response and its wall clock (transcription-pane README, History page). Roles come from the session title,
the socket's title or its `session_role:` line. Each flat RESULT leg is a job socket; leg/unit labels come from its
title, and same-session follow-ups remain separate jobs.
Parsed sessions are cached in `~/.local/state/opencode-companion/telemetry/` (`OC_TELEMETRY_DIR`), so a repeat
reads only new messages: a whole program takes ~1 s uncached, around 0.5 s cached. Rules and evidence: the
feasibility study [ap:9J9VCU]; doctrine [ap:06CBK0]. Tests: `python3 -m pytest scripts/test_oc_telemetry.py -q`
(frozen golden sessions under `scripts/fixtures/telemetry/`; refreeze with `--freeze [SID ...]`, see the test's docstring).

**Stream/tool timing, for any role on either backend:** `oc.sh telemetry --sid SID --phases` reports wall-clock minutes and
shares for **wait for first output / thinking / writing tool calls / own tool execution / helper waits / idle gaps / other**. A program handle
instead of `--sid` includes every labelled program session and its descendants on either backend, with per-session and
by-role totals; `--role "PROMPT AUTHOR"` (or any recorded role) filters without asserting a different role.
This is a separate view, not the four job-context phases above. **The pane** shows it for GPT and Claude sessions, one
line per run under that run's own line, including OpenCode helpers on any model (`oc_telemetry.pane_stream_phases`): each run's window runs from its
follow-up message to the next one's, through the same selection (`phase_window`), so the pane's numbers equal
`--phases --since … --until …` over that window (the exact command is in the row's hover/tap view).

```bash
bash scripts/oc.sh telemetry --sid ses_e9fc0d8a1ffecW6yOdcvyh2Po9 --phases \
  --since '2026-10-21T18:01:25-04:00' --until '2026-10-21T20:29:05-04:00'
```

`--since`/`--until` are ISO timestamps (explicit offset recommended; otherwise local time). They select **whole
steps by request start**, inclusive/exclusive, not clipped intervals. `--full` adds each step with its stable
`peek --index` message pin; `--json` includes those details, counts and fractional wall/active shares (`oc-telemetry-phases/2`).
`peek --index` also shows stored created→completed duration in seconds, or a dash when unavailable/live.

Timing uses OpenCode's stored event clocks, not token-rate estimates: wait = step created→first item created;
thinking = union of reasoning created→completed; writing = last reasoning end (else first tool created)→last tool
ran (arguments complete); own execution = union of non-wait tool ran→completed. **Tools split into blocking tests, map refreshes and other
tools** in CLI/JSON and pane lines/hover: pure executed `run_tests.py`, pytest, `node --test`, `npm test` and the
job classifier's other test-runner forms count as tests; quoted mentions do not. Directory/env setup and output/exit
plumbing remain with the runner, including reading its own redirected log. A shell mixing tests with other work
stays wholly in other tools with `mixed_test_work`, not a guessed allocation. Tests take concurrent test/other tool
wall time; `tool_execution.overlap_ms` discloses that overlap. Executed `oc_map_refresh.sh`/`.py`, direct
`beacon.map.refresh` and that job's `system.job.status` calls count in `tool_execution.refresh`; mixtures stay
other with `mixed_refresh_work`. Tests + refresh + other equals the tool interval union.
**Background tests and refreshes** show `tests in background …m concurrent` / `refreshes in background …m concurrent`
in CLI/pane and `background_tests` / `background_refreshes` in JSON.
Pure test launches paired to their terminal OpenCode shellID or Claude generation/task/run receipt count from
dispatch to completion notification, including red exits. This is observed elapsed time, not process CPU time;
notification delivery can lag process exit. A later explicit `TaskOutput` wait or pure shell poll of the exact
task/shell output identity counts as blocking tests/refresh; just its overlap with the job lifetime is removed from
the concurrent clock. Mixed polling shells remain other and flagged, not guessed apart. The separate interval
unions disclose overlap with steps/tools and per-run witnesses, without changing execution totals, helpers, idle,
wall or active. `blocking_ms` is the within-lifetime waited portion; blocking tool calls also retain delivery tails.
Parallel background runs are unioned within a session; program totals sum session time.
Both launch and completion must be inside the selected window. Unfinished/unmatched/mixed launches stay flagged,
never extrapolated. Raw shell `&`/`nohup … &` launches without runner completion evidence remain unmatched: an
enclosing shell's exit, a PID, a later log read or a poll is not the runner's finish timestamp. `nohup` without `&`
does not itself background a process. A helper's suite counts in the helper's own tests, not again on its parent;
program phase reports include helpers through recorded parent links on either backend. Daemon refreshes use
`created_at`→terminal `updated_at` (UTC), not the later poll's arrival. A refresh wrapper's task output can supply
the exact daemon job identity; its own timeout does not finish the refresh. Missing terminal evidence stays flagged.
**Helper waits** separate pure
`oc_seat.sh wait`, `oc.sh seat wait`, `oc_seat wait` and Python/Claude-seat equivalents (including simple shell
wrappers and tail/tee pipelines). Foreground waits move out of tools; overlapping own work takes priority, so
parallel waits do not multiply time or erase real work. A shell mixing waits with tests/other work cannot be split
from one clock: it stays in tools with `mixed_helper_wait`, not a guessed allocation. Launch/setup remains tools.
For background waits, a successful synthetic shell completion (`shellID`, exit 0) must match a pure wait launch
in the selected window; only no-step gaps from registration to that notification become helpers. No matching
receipt, a timeout or an unrelated background test leaves gaps **idle**. A matched notification can extend the
observed wall beyond the last completed step; unfinished waits are never extrapolated. Windows/forks never pull
in a launch from before their boundary. The clocks measure observed waiting through notification, not exact helper
CPU time. **Other** is remaining inside-step text, tails and unknown spans. **Active** = wall minus helper waits
and idle gaps (including inside-step other); CLI shows both wall and active shares, pane rows label wall shares
and hover/tap adds active shares and the blocking/background split. A text item often has no start clock: **streamed is not its first
byte**, so that wait stays unknown. Missing clocks and incomplete steps are counted, never extrapolated.
Multiple calls with no reasoning are counted as **unseparable**: their observed wait can hide server-buffered
writing, and writing is unknown, not guessed. Tools can run while later calls stream: overlap is disclosed and
shares are non-exclusive. Program/role totals are **summed session time**, with elapsed duration shown separately;
parallel sessions are not mistaken for serial elapsed time.

**Claude** reads its own retained host `events` and exact `stdout.bin` frame ranges, read-only and incrementally;
copied fork transcript history is never timed. Request wait uses the per-attempt `message_start.ttft_ms`, **not**
the final result's `ttft_ms` (which can include a completed first tool block). Observed retry intervals stay separate.
Thinking is the native thinking-block window, excluding witnessed `narration_block_indexes`, not pure compute;
writing is observed `tool_use` start→stop; tools are permission-reply dispatch→model-facing result, or an explicitly
flagged arguments-complete fallback—not tool-body-only. Buffered input bursts, missing clocks and native-duration
versus host-arrival disagreements are flagged; native thinking duration is retained rather than stretched to the
arrival span. Host pipe timestamps include CLI/network batching. Native resume generations have separate pane
phase lines and exact CLI windows, including when a job-context segment spans several generations. Pure background
seat waits require matching generation/task/run and successful exit receipts; timeout/unmatched gaps stay idle.
Transcript-only history explicitly says `phases unavailable · no host stream recording`; no starts are invented.
Method: [ap:H8JUSO] §2 (OpenCode); the native clocks use the same `phase_measure` accounting funnel.
Tests: `python3 -m pytest scripts/test_oc_telemetry_phases.py scripts/test_oc_telemetry_claude_phases.py scripts/test_oc_telemetry.py scripts/test_oc_cli.py -q`.

## Quick-run model selection

`oc.sh quick` defaults to Sol Fast/xhigh workers and a **Claude Opus 5.5/xhigh Conductor** (Claude Code, Dan's Max
subscription; it stays awake in a background wait, which Sol Conductors have failed to do). Independently select
`--worker-speed fast|standard`, `--worker-thinking low|medium|high|xhigh|max`,
`--conductor-speed fast|standard` (a Sol Conductor instead, default xhigh), and
`--conductor-thinking low|medium|high|xhigh|max` (`--variant` still works).
Existing selectors remain; do not combine `--worker` with worker dimension flags, or `--model` with `--conductor-speed`.
Worker choices persist for scouted starts and fresh continuations; forks still inherit the Manager's model/level.
Manager-direct `subagent` uses `agent: manager-quick` (Fast/high) and its native `model` override, e.g.
`openai/gpt-6.1-sol#medium` or `openai/gpt-6.1-sol-fast#xhigh`; always specify the level when overriding.
New agents/defaults and Fast's 872K ceiling need the normal idle-only restart. Real quick launches refuse
missing agents or unsupported live models/levels before writes; dry-run reports pending agent activation.
Canonical procedure and examples: Workflowy [ap:XUWU84] and [ap:XLJKT1].

## What does NOT live here

- **Workflowy MCP tooling**: `workflowy-nexus-mcp-tools` (separate, open-source).
- **LLM Relay** (transport-only dedup proxy): **standalone repo** at
  `E:\__daniel347x\__Dan_Root\__repos\llm-relay`. Spec: `[ap:LGDT9Y]` · dock: 🔀 LLM Relay.
