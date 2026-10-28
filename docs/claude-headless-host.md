# Persistent Claude host — transport, supervision and native controls

This is a private local owner of the **official Linux Claude Code CLI**, not an inference proxy,
terminal driver, pane server or replacement OpenCode client. Claude handles subscription login,
inference, native tools/retries and transcripts. The host adds **no execution/control/initialize
deadline**. It neither purchases usage nor logs in/upgrades. U04 adds one journaled recovery
`continue` only after an explicit terminal capacity error or proved missing-result process
exit, never during native retries or because of silence, and never after a usage-limit lockout
([halt](#usage-limit-lockout-halted-never-resumed-automatically)). The U05/U06 viewer/guarded Manager Send
are implemented separately; operator activation/whole-result/ergonomics remain gates. Seats are started
by the oc_* scripts through `scripts/oc_claude.py` ([Seats](#seats-through-the-oc_-scripts-u1a)).

## Start explicitly, independently of the pane

From WSL (stdlib Python only):

```sh
bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_host.sh start
printf '{}' | bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_host.sh call health
```

`start` acquires the existing root `host.lock` **before Popen** and passes that same open lock FD
to `serve`; the child adopts it before Store open/reconstruction and holds it for its lifetime.
The launcher closes its own copy without unlocking the child's. It reports `start_requested`,
**not readiness**, with operation/start-attempt identity and the private receipt location.
Before reconstruction the child atomically writes `host-start-receipt.json` (root/socket,
PID/start/boot witness, selected account observation, initializing); it advances to bound and
ready, or retains a terminal initialization error when possible. These timestamps are diagnostic,
never a lease/TTL. A failed optional receipt update never kills usable service. Socket binding
still follows reconstruction. After binding an asynchronous probe of the **ordinary endpoint**
health/list calls the same factual ready finalizer as the restart coordinator; merely binding
is not health confirmation. Duplicate hosts cannot compete. For foreground operation use `serve`. Reloading a browser, closing
a client or restarting the pane does not close CLI stdin. Nothing starts at boot or on history browsing.

Defaults:

* IPC: `/tmp/opencode/cc-<unix-uid>/host.sock` (private 0700 parent/0600 socket; same-UID peer check).
* State: `~/.local/state/opencode-companion/claude-headless/` (0700).
* Index/projections/receipts: `host.sqlite3`, FULL-synchronous WAL, private 0600; everything one native
  read produces is one transaction ([Durability](#durability-feed-and-next-unit-seams)).
* Native run: `<state>/<native-uuid>/g000001/`, then increasing generations on explicit resume.
  `stdout.bin`, `stderr.bin`, `stdin.bin` are append-only exact bytes; `launch.json` is secret-safe;
  `prompt.md`, `settings.json`, `mcp.json` are private immutable-per-run snapshots.
* `host.lock` plus `<uuid>/owner.lock`: exclusive `flock`; the native child inherits its UUID lock.
  Records include PID + Linux start ticks + boot ID. Restart and the periodic orphan recheck reconcile witnesses/locks, expose
  lost live/held owners as `ownership_unknown`, and indexes retained but previously unindexed bytes
  as `raw_reconciliation_gap`. Neither permits a second writer or semantic replay. A proved gone
  initialized owner with a missing result can resume the same UUID after its durable recovery claim.
  Startup and the recheck take one record at a time: a pruned `<uuid>/` directory marks that record
  `session_directory_missing` (owner gone once its witness is; never resumed, its scheduled recovery
  cancelled, an unfinished input escalated for `release_input`), and a record that cannot be
   reconstructed is marked `reconstruction_error`. Each non-archived record's gaps, receipts, incidents,
   schedules and final record commit once in one `Store.batch`; a failure rolls back only that record,
   then its failure mark is recorded outside the rolled-back batch. Neither stops the host from starting.

Operator-only test overrides: `--socket-path /short/private/host.sock --state-root /private/state`
go **before** `start`, `serve` or `call`. Use one canonical state root in normal operation. These
path/CWD/argv choices must not be exposed directly to a browser. Native transcripts remain under
Claude's normal `~/.claude/projects/`; `native_transcript_path_expected` is a prediction, not a verified
artifact or authority to send. Never pass `cc-UUID` through OpenCode's fuzzy `ses_` resolver.

## Exact local client contract (protocol version 1)

Python import: add the repository's `scripts/` to `sys.path`, then:

```python
from claude_headless.client import Client
client = Client()                         # default endpoint
result = client.call("state", execution_id="cc-<native-uuid>")
# In an asyncio pane/server: await client.acall("state", execution_id=...)
```

Each call opens one private Unix connection, sends an 8-byte unsigned big-endian byte length plus
UTF-8 JSON `{"method":...,"params":{...}}`, and receives the same framing with
`{"ok":true,"result":...}` or `{"ok":false,"error":...,"message":...}`. Explicit IPC limit: 64 MiB;
oversize is an error, never a cropped success. No client retries or timeouts. A lost Send response
means **query the same message receipt**, not allocate/repeat another semantic Send. CLI `call METHOD
--file /absolute/private/params.json` reads params from a UTF-8 file (stdin is also supported); avoid
placing private prompt/tool payloads on the shell command line. CLI output is private data too.

| Method | Exact params / result |
|---|---|
| `health` | `{}` → protocol version, host PID/start witness, paths, implemented/pending-unit capabilities (the two fork paths are `seat_fork` and `manager_fork`), `seat_interface` (1: the fields below that `oc_claude` needs; absent on an older host), `unattended_roles_enabled` (true under D2), `root_search_hook` (the root-search hook's witness by Claude Code version, flagged generations, the guard's extra roots), the unchanged five-key `rows_backfill`, and additive `load` observations (row debt, loop lag, startup and disk facts; Durable conversation rows below). |
| `launch` | Required `native_uuid` (caller-preallocated canonical UUID), absolute `cwd`, explicit `model`, `effort`, `system_prompt` text. Optional `resume:false`, `role:"worker"` (`Manager`, `conductor`, `worker`, `helper`, `base`), `agent`, `program`, `socket_handle`, `parent`, operator `workflowy_url`, `context_target` (finite fraction in (0,1], immutable on resume), `labels` (seat provenance: at most 32 lowercase identifier keys with string/number/boolean/null values, e.g. title, kind, quick, manager_session, conductor id/n, seat_base; immutable: resume keeps them and refuses different ones). Returns registered `execution_id:"cc-UUID"`, generation, launch/state/provenance record; records also carry `first_launched_at` (kept across resumes) and `last_input_uuid`/`last_input_at` (the newest accepted Send, a recovery `continue` included). Initialize is asynchronous: query `state`/`control_receipt`; no timed wait/abort. |
| `launch` with `resume:true` / `resume` | Host-owned UUID only, old PID/start witness gone and lock acquired. Same current explicit choices/role/CWD and byte-identical stored prompt/settings/role/MCP apart from the re-resolved Workflowy URL token, whose change is journaled; provenance retained. `resume` takes only `execution_id`. Ordinary unsettled inputs stay uncertain; automatic crash continuation explicitly links/supersedes them without calling them completed or replaying their text. |
| `launch` with `fork_from` | A **fork** of the base `fork_from` (`cc-` id): a new session holding the base's whole conversation (`--resume BASE --fork-session --session-id NEW`; the base is untouched), built from the base's stored snapshot (prompt, settings and role policy byte-identical, MCP with only the Workflowy URL migrated; a fork of a Manager also carries the base's stored `agents.json` and recorded `request_env` byte-identically), so its first call reads the base from the prompt cache. The base must be host-owned, have a completed turn and its own turn closed now (`fork_blockers`: not starting, no input awaiting its result, no open turn, no pending recovery, ownership known; an idle live base or an ended one is fine) — except a **Manager base**, admitted only by that Manager's own self-fork: `fork_self_binding` is the context binding of the Manager's live generation (which only its own process tree holds), the call must ask for `role: worker` (a fork is never a second Manager), and the Manager's open turn is the conversation being copied, its transcript on disk standing for a completed turn; a self-fork of any other role refuses (`a fork never forks`). The fork has the base's role and cwd (Claude Code finds the base's transcript by cwd); `system_prompt` may be omitted (the base's is used), given it must equal it; model and effort are the caller's (a level change keeps the cache, a model change writes the base again). Records carry `forked_from`, `fork_base_call` (the base's last completed call when forked) and `fork_first_call` (the fork's own first completed call, the evidence for `oc_seat info`'s HIT/MISS line). A fork has no transcript before its first message: resuming one whose process ended first forks it from its base again (`resume_strategy: re-fork`), never an empty `--session-id` history; after its first turn it resumes its own conversation. Health `seat_fork: 1`; a Manager self-fork also needs `manager_fork: 1`. |
| `reconnect` | Registered Manager only: exact `execution_id`, expected integer `generation`. Per-conversation serialized; reuse a live owner or resume the stored conversation only after the old witness/drains/lock are proved gone. Ten concurrent old-generation requests return one new process/generation. Returns current record, not readiness or a Send; never a message replay. One rule with the pane, `settlement.reconnect_blockers` (plain-words reasons naming the way out): refuses only a live/unknown/unproved owner, an unknown PID identity, a still-alive witness or a recovery already claimed. Once the owner is proved gone, the dead generation's `protocol_attention`, `settings_mismatch`/pending settings and infrastructure markers are released, uncertain inputs are marked `released_by_reconnect` (still uncertain, never replayed, no longer gating Send) and a still-scheduled recovery is cancelled as superseded, all in one `dead_generation_holds_released` incident. Every `state`/`list` record carries `reconnect_policy` so a reader applies exactly this host's rule. |
| `send` | `execution_id`, integer `generation`, caller-preallocated `mid` (canonical message UUID), exact `submitted` nonempty text, exact `original` text. Persists and reads back both texts/hashes before journaling stdin, then returns `accepted` receipt. Same ID/same texts returns the existing receipt without writing again; different text/original refuses. Wrong generation, uninitialized/unconfirmed Max, logging failure or another unsettled input refuses. No invisible input queue. |
| `steer` | Same params and idempotency as `send`, accepted **while the session's own turn runs**: the ordinary user line (no `priority`, the native default `"next"`), which Claude Code queues and hands to the model at its next tool step in the same turn (if the turn ends first, it starts the next turn). A steer reaches a busy Claude session at its next tool step; one long-running command delays it until that command ends. Only the open-turn refusal is waived, and with it a native API retry inside that turn (Claude Code holds the queued line through the retry; the turn's `last_retry`/`last_rate_limit_info` evidence is kept): generation, ready, stdout EOF, settings and recovery gates all hold (an open escalation is recorded on the receipt, never a refusal), and an ordinary `send` still waits out a retry; a pending permission request is not a blocker. Receipt `kind:"steer"`, `turn_open_at_send`, and two moments: `queued` (native `command_lifecycle queued`) and `delivered` (its exact replay echo, which arrives only when the model receives it; `acknowledged` too). One result may name it beside the input whose turn it joined (`completed`). An interrupt's `cancel_queued` before delivery makes it `cancelled` and releases it; a process that ends first makes it `uncertain`. Never resent: a lost reply is answered from this receipt. `priority:"now"` is deliberately not used (it ends the turn and cancels a pending permission request). |
| `receipt` | `execution_id`, `message_uuid` → exact durable receipt or null. `written`, `acknowledged`, `started`, `completed`, status and result records are separate. Native replay must have both matching UUID and **exact submitted content** to acknowledge. |
| `quota_pause` | Real launch-guard pause only: `rate_limit_type:"five_hour"`, structured numeric `resets_at`, optional `program`, `socket_handle`, `role` → coalesced durable recovery-check episode. Never called by preflight/dry-run/usage reads. |
| `quota_checks` | `{}` → durable five-hour episodes and their exact notification UUID, recipient, deadline and delivery status; read-only inspection, no Send/replay. |
| `quota_check_control` | `episode` (exact retained ID), `action:"defer"|"cancel"`, nonempty `authority` → durable operator status, null due time, original deadline, history and actual admitted receipt. `oc.sh claude_notices list`, `defer EPISODE`, `cancel EPISODE` are the supported operator CLI. Other episodes are unchanged. |
| `observer_inventory`, `observations` | Inventory full executed native Bash inputs into typed timer/session/socket/read-only-poll obligations, with exact owner/backend/generation/task/run/tool-use/raw/process witnesses and immediate ancestry. Task/run and tool-use cursor indexes select the exact raw frames without repeated generation-wide scans; ownership uses cached session records. `observations` reads/reconciles the durable records (optional exact `observation_id`). Display summaries/descriptions never authorize stopping. |
| `stop_task` | Exact `execution_id`, integer `generation`, inventoried `observation_id`, nonempty `authority`. Revalidates the full task incarnation/input/process witness, commits intent, issues at most one native `stop_task` for that run and returns pending. Native empty success is not cancellation; reconcile terminal, later task-level and original-process receipts. Never stops the watched job. |
| `observer_switch_inventory`, `prepare_observer_switch` | Whole-host exact blocker disclosure; prepare takes retained `operation_id`, `requested_account`, `authority`, stages the recovery index and fences new Send admissions only when every owner is settled or observer-only. Genuine work, ambiguous inputs/retries/ownership remain holds. |
| `retire_observer_owner`, `observer_retirement` | Exact switch `operation_id`, `execution_id`, `generation` (retire also requires `authority`). Only an entirely proven observer-only native owner qualifies. Durable intent → freeze its exact native callbacks → drain/revalidate task/process population → exact native shutdown → original observer process exit and durable owner/drain/lock proof. Query retirement after a lost reply; no new stop is issued. |
| `observer_recoveries`, `finish_observer_switch` | Read the independent master/per-program report queue (optional `operation_id`); finish retains actual outcome/receipts and releases the host admission fence. Reports/publication/notice delivery never hold native settlement or switching. |
| `state`, `list` | `state`: `execution_id`, any session at any age; it also answers `last_synthetic_result`, the newest background result (a wake-up turn's last word, which may also name an absorbed input; null when none), read from its own row. `list` answers by default the sessions whose most recent turn (last output, input, generation start or first launch) is within the last **two weeks** (`LIST_WINDOW_DAYS`, health `list_window_days`), plus every live or busy one regardless of age (owner not proved gone, an input awaiting its result, a pending recovery); a record with no timestamp is never taken for old. `all: true` answers everything, `window_days: N` another window. Three selectors answer at any age instead of a window, together every record any of them names: `execution_ids` exactly those ids (ask for an older session by id), `programs: [CORE…]` every member of those program cores (`program` exactly `[ap:CORE]`, else the `program_core` launch label: the pane's own rule; `[ap:CORE]` is accepted too) and `live: true` every record that is not ended (whatever the pane's `_ended` does not call ended, plus every live or busy one: a superset of the pane's live rows). `summary: true` omits the bulky detail fields `LIST_SUMMARY_OMITS` (launch argv/paths, tool/MCP lists, effective-settings dumps; the row says `summary: true`; records carry no result copies); `socket` (same AP core) narrows. Unknown parameters and malformed values are ignored and callers re-filter (an older host answers every full record). `list` reads an in-memory copy of the session records (`Store.cached_sessions`, below) and copies and completes only the rows it answers, so a narrow answer costs what it returns. Whole-inventory checks pass `all` (the guarded restart, prefix resolution, registry/seal/launch checks, `socket` listings); the pane sidebar passes its view window and separate exact-id/program selectors (`all` only for Everything live), while the quota/busy lines use the default. Owner/role/provenance/choices/auth, pending IDs, `controls_effective`, setting gate, retry/background tasks, recovery/escalation and silence observations. `ready` means **transport initialized/Max confirmed**, not task acceptance; initial applied-settings readback must also finish before Send. |
| `program_activity` | Read-only `since` (epoch **ms**) → `{programs: [{core, last_at, sessions}], as_of}` from `Store.cached_sessions`, no SQL/decode/record copy. Uses the one `list_program` rule and `list_recency` (seconds → ms). `last_at` is the newest activity of any member; `sessions` counts **all** members, any age. Answers cores whose known `last_at >= since`, newest first, ties by core; undated records alone supply no activity evidence. Missing/malformed `since` means 0. `as_of` is epoch ms on the host clock. No cap or expiry. Feature-detect through health `implemented`; an older host's unknown method falls back to the slow windowed list. |
| `forget` | Exactly one of `execution_ids: ["cc-UUID", …]` or `empty_sid: true` → immediate `{job, accepted: [ids], skipped: [{id, reason}]}`, then background deletion. The only session admission test is `settlement.owner_proved_gone`; skip reasons are `unknown`, `not-ended`, `already-archived`. Unknown parameters/malformed selectors are a `ValueError`. No confirmation, ID cap, archive verification or trim-policy decision on the host; the archive-first trim planner owns eligibility. |
| `control_receipt` | `execution_id`, `request_id` → durable correlated native/operation receipt; no reply stays pending, setter reply alone is unverified. Interrupt settlement is refreshed from result/task evidence. |
| `get_context_usage` | Operator read: exact `execution_id` + current integer `generation`; asks that owned native process for context and applies its role/profile/launch target. Python `await client.context(execution_id,generation)` is the same call. No transcript/catalog/cumulative fallback. |
| `context_status` | Private MCP callback: exact launch `execution_id` + generation capability `binding`, injected by the host, never model-supplied. No other params. Same measurement as `get_context_usage`; invalid/foreign/old-generation capability refuses. |
| `begin_control` | U06 Manager-only nonblocking caller-ID form: `execution_id`, current integer `generation`, canonical caller `request_id`, `operation` (`set_model`, `apply_effort`, `interrupt`) plus the operation's existing params/authority. Durably binds the exact operation/params to that native request UUID; returns pending without awaiting native reply. Same ID/same request returns its receipt, changed request refuses. Detached native reply/readback reconciliation survives observer disconnect; query `control_receipt`, never repeat a lost operation. |
| `permission_receipt` | `execution_id`, `generation`, `request_id` → full original lexical request/input, all reason fields, allow decision and exact response. |
| `replay`, `subscribe` | Optional `execution_id`, opaque `cursor` (null starts at beginning), `limit` (1–500, default100). Bounded metadata page: `events`, next `cursor`, `has_more`, `gap:false`. `subscribe` waits for a nonempty page; client `async for page in client.subscribe(...)` pulls pages. Foreign/ahead cursors refuse visibly. Disconnect cancels only that reader. |
| `raw` | Event `cursor`, optional byte `start:0`, `length:1048576` (max4 MiB). Returns `base64`, `next_start`, `total`, `has_more`. Exact bytes, including malformed UTF-8/newline spelling. Read all slices to reconstruct a large frame. No caller-selected file paths. |
| `raw_frames` | `cursors:[…]`, optional `budget:4194304` (1 byte–4 MiB, clamped). Read-only whole frames, at most 1000 cursors and `budget` raw bytes per call, one open per stream. Returns `{frames:{cursor:{base64,total}}, remaining:[…], skipped:[{cursor,reason}]}`. Continue with `remaining` until empty; it is the unprocessed suffix, never a history cutoff. Skips (`oversize`, `no-raw`, `invalid-cursor`, `foreign-cursor`, `short-read`, `raw-unavailable`) use ordinary paged `raw`, preserving its errors and large-frame support. Health advertises the verb; older hosts keep the per-frame path. |
| `rows` | `execution_id`, optional `scope` (default main for ordinary paging), `from_seq`, `by_origin`, `agent_window`, `incident_offset:0`, `limit:100` (1–500). Read-only durable row page; selectors and continuations below. Every reply includes stored `agent_info: [{generation, call, …stored info fields}]`, never re-derived from frames. |
| `rows_changed` | `execution_id`, optional `scope:"main"`, `since_changed:0`, `after_seq:-1`, `limit:100` (1–500) → rows strictly after `(since_changed,after_seq)`, ordered by `(changed,seq)`, `has_more`, and when more, `next:[changed,seq]` from the last returned row. Pass both coordinates back; a tie group may span pages. |
| `row_witnesses` | `execution_id` → `witnesses`, the whole exact-mirror list of `[kind,scope,uuid,fingerprint]` keys, not bodies or an event replay. |
| `row_diagnostics` | `execution_id`, optional `scope`, `offset:0`, `limit:100` (1–500) → `diagnostics` in projection order, `has_more`, `next_offset` (null at end). An absent scope includes every scope. |
| `tool_snapshot` | `execution_id`, `generation`, `tool_use_id`, optional input `frame_cursor` for ambiguity. Returns scoped final input (`input_raw`, lexical JSON) and model-facing tool-result `block_raw` records paired by tool_use_id. Identity includes execution/generation/event UUID/provider message ID/block index/tool ID/cursor; repeated native event identities are deduplicated in the snapshot, not erased from raw logs. Missing/offload data is not fabricated. Partial stream projection belongs to U05. |
| `close_input` | `execution_id`, `generation`; explicit operator action under the Send gate using the single shared native/companion settlement predicate (also collector/restart). Queues EOF behind the single writer; observed new work before actual EOF revokes authorization. `input_close_queued` is not settlement; read `process_exit`/state and native task evidence. Never use this as a silence policy. |
| `release_input` | `execution_id`, nonempty `disposition`, optional `message_uuids` (list) or `recovery_id` (its source inputs); default every unfinished, unreleased input. The general way out of an input no process can answer: an owner proved gone, an input an earlier generation accepted, one the writer never wrote (`withheld`), or a live process natively idle with its turn closed (its result was lost). Each is marked `released_by_operator` (status `uncertain`, never completed, **never replayed**) and stops gating Send/close; one a process may still answer is returned under `held` with the reason (ownership unknown, a draining exit, a running turn), never released. The recovery that covered the released inputs is dispositioned (`resolve_recovery`), escalations that exist only because those inputs never got a result (`recovery_failed`, `startup_failure`, `ownership_unknown`, …) are acknowledged with the same disposition; auth, billing and model alerts stay open. A recovery claimed or sending refuses the call. Returns `released`, `held`, `completed`, `already_released`, `recovery`, `acknowledged_escalations`, `pending_escalations`, `message_replayed:false`; journaled as `input_released`. |
| `clear_infrastructure_error` | `execution_id`, nonempty `disposition`: once storage works again. Journals the clearing first (the probe), counts the bytes drained but not retained and the inputs never written (marked `withheld`), indexes retained-but-unindexed bytes as `raw_reconciliation_gap`, restores the state and resyncs the stdout parser (at a frame boundary it parses on; mid-frame, the rest of that frame is kept as one `raw_resync_fragment`); the record keeps a `retention_gap` and `protocol_attention`, and a live process gets one native readback (fresh session state, redelivered permission requests). Inputs the gap may have answered stay pending for `release_input`; a gap never schedules an automatic `continue` (its exit escalates `reconciliation_uncertain`). A stored record just drops the marker, journaled. Returns the state plus `cleared`. |
| `settle_for_restart` | `execution_id`, `generation`, optional `allow_stop` (bool, default false); the guarded restart's settle for a session whose only operator hold is "native background task level is unknown", under the Send gate. Runs the collector's native readback (`collection.observe_readback`): idle with a fresh empty level → `settled` (nothing closed; the restart then uses `close_input`). Task, running state, new work or another hold → `held`. No answer/snapshot within 30 s or a malformed answer: without `allow_stop` → `refused`; with it, only if the process-tree proof holds (idle, no recorded task, no other hold, no work since the readback, PID witness alive, no descendant but `claude_context_mcp.py`) → one SIGTERM, `stopped` with the proof (`close_reason: restart_process_stop`, no startup-failure incident). Anything else → `refused` with the reason. |

Forgotten sessions remain reachable: one committed record replacement keeps
`state: "exited"`, `owner: "gone"` and adds
`archived: {at: <epoch ms>, key: "claude:cc-UUID", by: "forget"}` plus
`forget_pending: true`. `state`, `list all` and `list execution_ids` keep answering
that tombstone at any age. The default list also shows a newly archived tombstone
for its normal window, without rewriting its historical activity timestamps.
`resume`, `launch resume:true`, a fork from it and `reconnect` refuse with
`archived on ‹UTC date›; its conversation is in 📦 History (‹key›)`.

The host uses one background worker and its existing SQLite connection. Each
exact-sid deletion batch commits at most 2,000 rows, checks elapsed work in small
SQL steps (2 ms work budget, 3 ms adaptive transaction target, 20 ms slice ceiling)
and gives pending socket I/O eight zero-delay loop turns before the next batch.
The sid-equality subquery selects a bounded rowid set; the outer delete uses only
primary-key lookups, never a scan of that sid's entire range. It deletes from
`events`, `receipts`, `permissions`, `controls`, `incidents`, `recoveries`,
`results`, `task_inputs`, `rows`, `row_parts`, `row_keys`, `row_diagnostics`, `row_incidents`,
`row_sets` and `stream_ends`, then removes `<state>/<uuid>/`, the recorded transcript and its
sibling `<uuid>/` subagent folder in an executor. Missing paths are already gone.
Only afterwards does one committed record write clear `forget_pending`.
The tombstone is never deleted; no `VACUUM` runs, so the database file keeps its
size and SQLite reuses the freed pages.

`empty_sid: true` uses the same worker for exactly `row_sets`, `rows`, `row_parts`,
`row_keys`, `row_diagnostics` and `row_incidents` with SQL `sid = ''`. It leaves
empty-sid `events`, `incidents`, `quota_checks`, `observations` and
`observer_recoveries` untouched, and removes no files or session records.
Its durable `meta.forget_empty_sid` marker is `started` before admission and
`complete` after deletion; `started` resumes after bind. Its accepted/current ID
is `""`. The Manager runs `bash scripts/oc.sh history junk` once after the guarded
restart; the CLI prints admission and health progress until completion or Ctrl-C.
Stopping that monitor never cancels deletion. An older host gets one line saying
it needs the Manager's restart, with no `forget` call or fallback deletion.

Health advertises `forget` in `implemented` and reports exactly
`forget: {status, queued, current, rows_deleted, done, failed}` from Host attributes:
`queued` and `done` are job counts, `current` is an execution ID, `""` for junk, or null,
`rows_deleted` is the committed row count, and `failed` is a list of `{id, error}`
(exception type). Status is `idle`, `running`, `complete`, `failed` or `interrupted`.
Further calls join the worker, never wait for it. Reconstruction skips tombstones
without probes, incidents or record writes; after the socket binds it resumes
any `forget_pending` work. Shutdown cancels the worker, never waits for deletion
or refuses a restart. Activation is the Manager's guarded host restart.

### U04 controls (exact `execution_id` + current integer `generation` required)

`set_model(model)` and `apply_flag_settings(settings:{effortLevel:LEVEL})` / `apply_effort(effort)`
are settled-turn-boundary operations serialized with Send, for the Manager or for a conductor/worker/helper
whose caller passes a `seat-switch:…` authority (health `seat_settings_switch`; `oc_seat resume` / `oc_steer`
`--model`/`--variant`); a base never switches. Unique native request IDs, authority/intents/replies/readback are durable. A success
response is only `replied`, not applied; subsequent `get_settings.applied` must match both model
and effort. Same-value requests are verified `no_op_applied`. Missing reply stays pending forever;
refused/unsupported/mismatched/uncertain settings keep Send blocked. Initial launch also requires
applied readback before Send. Model switching may invalidate cache; no simulated confirmation dialog
or global-default change. `get_settings()` is read-only and allowed busy, and can reconcile a lost
caller without inventing missing applied evidence.

`interrupt(authority:TEXT,cancel_queued:true,reason?:TEXT)` requires explicit operator/script authority,
is allowed busy, and uses native control, not signals. Native `still_queued`/`cancelled` receipt is
not settlement. Query `control_receipt` again for result/task evidence. U06 `begin_control` returns
at durable issuance, not at applied readback; the existing synchronous script controls remain.
Partial effects are not
rolled back; explicit interrupt never schedules a continue. Control receipts preserve native `status`
separately from operation `state`, `authority_cursor`, requested/readback/effective values,
applied/blocks_send/disclosure, verified generation and interrupt settlement. A lost response means
query receipt/state, not another semantic setter. Seat construction remains separate later work.

### Native measured context (U07)

The installed **2.1.299** supports the captured SDK's exact control request:
`{"type":"control_request","request_id":"<unique UUID>","request":{"subtype":"get_context_usage","detail":"summary"}}`.
Its correlated success payload supplies `totalTokens`, `rawMaxTokens`, `maxTokens`, `model`,
`isAutoCompactEnabled`, categories and optional `apiUsage`. **Do not use `totalTokens` as measured
occupancy:** installed CLI `XPn` combines the latest API input count with `ajt`'s locally estimated
tail; before an API it uses per-category counts with an unobservable local-estimate fallback
(`_Zo`/`kZo`/`Zsn`). Full detail does not certify that no fallback happened. The native rounded
percentage and diagnostic categories are likewise not our measurement.

Instead the guard qualifies a **clean completed API boundary**: native `apiUsage` must exactly
match this generation's final `stream_event/message_delta.usage`, followed by `message_stop`,
with no subsequent observed semantic message/partial change. Multi-iteration usage refuses.
Native `totalTokens` must equal that response's measured input+cache-creation+cache-read count;
any difference is an unmeasured tail/category drift and refuses. Occupancy is then the **one
response's measured input + cache creation + cache read + output**, never a sum across turns,
messages, copied history or result.usage/modelUsage aggregates. Output is counted once, not
reserved/subtracted. The pending tool result and future output are not yet included. `summary`
avoids per-category count-API calls; its estimates are diagnostic only, never occupancy.

Window is native `rawMaxTokens`, with `maxTokens` separately disclosed, only when
`autocompactSource:"auto"` proves no configured compaction-window override. Model must match native
applied readback and auto-compaction must explicitly be off. There is no catalog/1M fallback.
Initial/operator reads before any API and same-UUID resume before a new generation API are
**unverified/null**, not fresh copies of old usage. Fresh native inference is never secretly sent
to make a reading available. U09 must refuse an unverified pre-follow-up resume check, not bypass it.

**U07 qualification BLOCKER (2.1.299):** real native MCP execution can publish the tool result
**before** the final `message_delta.usage`/`message_stop`. The provisional assistant block had
output_tokens23, final usage32 only after the context tool returned. Awaiting that final usage
inside the callback would make a dependency cycle; returning a provisional count or `totalTokens`
would violate measured-current-context policy. Three real MCP calls across two UUIDs and a same-UUID
resume therefore correctly returned UNKNOWN/null; one later idle operator boundary measured21635/1M.
Summary category drift also produced unverified idle reads. The stdio/IPC/native-control plumbing
is qualified, **the mandatory no-argument measured tool-time guard is NOT**. Host health explicitly
reports `native_tool_time_context_qualified:false`. That exact native mechanism (a tool-call-safe
current token count with declared provenance and an entitled window) is still absent upstream.
**The different decision has since been taken (D2, 2026-10-15, [ap:0OP1FK]):** every role measures
as the Manager does, the input of the previous completed model call, which is also what
OpenCode's own `context_status` measures. Health therefore reports `unattended_roles_enabled:true`
and the shared runtime text says so; the tool-time qualification above stays false and honest.

Strict per-run `mcp.json` explicitly contains **both** `workflowy` and `context` (stdio,
the host's Python + `scripts/claude_context_mcp.py`). The model-facing tool is
**`mcp__context__context_status`**, schema `{"type":"object","properties":{},"additionalProperties":false}`.
The shared native runtime overlay binds every doctrine reference to bare `context_status` to this
name, called with **no arguments**. No caller session/model/window/threshold lookup is exposed.
The adapter snapshots `CC_CONTEXT_EXECUTION_ID`, `CC_CONTEXT_BINDING`, `CC_CONTEXT_HOST_SOCKET` at
startup. Host-injected random capability is private environment-only; records keep its SHA-256,
not the value. Literal env references keep MCP snapshots immutable on resume, while the capability
rotates each generation: an old adapter cannot read a newly resumed generation, even with the same
UUID. Never log environment values. This is same-UID local binding, not a sandbox against malicious
code already running as Dan.

Every call issues a new native control, without a control deadline. The independent stdio process
awaits IPC while the host event loop keeps draining CLI stdout/stderr and approving permissions.
No callback is awaited from `NativeSession.project/stdout`. Reports carry request/response cursors,
generation, revision, request/response-arrival time, age and CLI version. Native measurement time
is explicitly null (the CLI supplies none); host arrival is not mislabeled as a provider timestamp.
A semantic message/model change
while awaiting reply, replaced generation/owner, aged reply (>30s), unavailable channel, missing
native fields, unverified API boundary/model/window or auto-compaction not confirmed off returns `ok:false`,
`fresh:false`, **null occupancy/window/target** and UNKNOWN advice; never reuse a prior measurement
as fresh. The 30s validity rule is observation-only, not an execution timer or cancellation.

Targets use **`oc_common.context_profile/profile_target`**, reading the one JSON authority in
`plugins/context-status.ts`. Native explicit model names feed that loader unchanged. Opus5.5 maps
to its own row; Haiku/Sonnet currently honestly report the authority's fallback. Manager/conductor
use handoff; worker/helper/base use land_by; an explicit host launch `context_target` wins and is
preserved on same-UUID resume. No copied target table or OpenCode behavior change. `LAND`/`HANDOFF`
means finish the current thing then output/PARTIAL or baton, not kill/compact. Unknown/stale means
no unattended continuation based on that reading. Seats use this same reading (D2) and are started
through `scripts/oc_claude.py`. Existing
pre-U07 sessions retain their original prompt/MCP snapshots; do not retrofit or restart them.

### One incident feed and journaled recovery

`incidents(execution_id?:ID,cursor?:CURSOR,limit?:100)` returns `incidents`, durable next `cursor`,
`has_more`, `gap:false`; page until complete. Same cursor vocabulary as replay/raw. Each record:

```json
{"incident_id":"stable-key","execution_id":"cc-UUID","native_uuid":"UUID","generation":1,
 "time":0,"category":"unknown","handler":"escalate","source_cursor":"store:seq",
 "message_uuids":["input-UUID"],"detail":{},"task_acceptance":"unjudged",
 "escalation":{"status":"pending","target":"Dan|owning_conductor","owning_conductor":"explicit-handle-or-null",
 "parent":"parent-execution-or-null","owning_conductor_binding":"explicit|unbound; adapter must resolve program owner",
 "program":"program-handle","socket":"socket-handle","delivery":"local_incident_feed; adapter_delivery_pending"}}
```

`incident(execution_id,incident_id)` reads current projection/disposition. Unknown Manager errors route
to Dan; seats carry explicit `owning_conductor` launch metadata, parent, program/socket and
pending adapter delivery. Auth/financial problems always alert Dan. This is a **durable local alert**,
not a delivered Workflowy/browser notification; U05/U09 consume it. Operator
`resolve_incident(execution_id,incident_id,disposition:TEXT)` acknowledges, never accepts the task,
and appends a disposition event. Pending escalations never block a Send: a seat's are addressed to its owning Conductor and a Manager's to Dan, and the sender is the addressee, so the Send proceeds and the receipt records `escalations_open_at_send` (health `escalations_at_send: recorded`). An automatic recovery waits only on escalations about it (its own inputs, auth, billing, a failed recovery, a model mismatch), never on an unrelated one. A still-scheduled recovery yields to an explicit Send (`superseded_recovery` on the receipt, `recovery_superseded` incident); a claimed one still refuses. Every permission request
(full lexical request/reasons), denial, tool error, native retry, terminal error, crash, silence,
proved-exit marker clearing (`native_markers_cleared`),
recovery and infrastructure failure joins this feed. If the store itself fails, live
`incident_unpersisted` says so honestly; new input is withheld until `clear_infrastructure_error`.
The one-second supervisor scopes every failure to the session that raised it: a bad record, a
missing directory or a bug in one session's part of a tick is that session's `supervision_attention`
(its state/list record, health `supervision_attention`, one `supervision_failure` incident; the type
only, never the text), cleared by its next clean pass, and never marks any session `failed_storage`.
Only a failure no session explains (the recovery journal unreadable) sets health `supervision_error`
(with `supervision_error_detail`), which the next clean tick clears (`last_supervision_error` keeps it);
the guarded restart reports it, never refuses on it. A collection that fails on storage marks only its
own session; any other collection failure is its supervision attention.

Registry (`supervision.classify_result`), applied only to terminal result error envelopes:

| Match | Handler |
|---|---|
| authorized interruption + interrupted/aborted/`aborted_*` native reason (2.1.300 ends an interrupted retry with `aborted_streaming`) | record; no continue |
| not-logged-in/auth/401/OAuth | alert Dan; no auth changes/respawn |
| entitlement/credits/billing/402/403/spend | alert Dan; no purchase/overage/fallback |
| context/prompt too long; max turns; budget; invalid config/flag | continuation/configuration needed; escalate, never auto-compact |
| exhausted 429/529/overload/quota/session limit | wait, one new UUID `continue`; a usage-limit lockout (below) halts instead |
| observed 500/timeout/malformed response, or unknown terminal error | durable escalation |

Both `result` text and `errors[]` matter; `subtype:success` may have `is_error:true`. Denials/tool errors
remain beside results, never algorithmic task failure (negative tests may expect them).
`origin.kind:task-notification` synthetic results are separate; first main result is not background
settlement. Every result has `task_acceptance:unjudged`.

Native Agent child frames (`parent_tool_use_id` non-null or `isSidechain:true`) remain in the
lossless raw feed, but never update the primary's context/model witness, Send receipts, retry
state or parent tool snapshots. Child permission requests still receive the same logged broker
decision. `task_started/task_progress/task_notification` retain background task observations;
a backgrounded task also keeps its tool's name, command (up to 1024 characters) and description
(up to 256), joined by tool-use id, plus the host-observed start time. Same-run level snapshots
preserve these fields; task membership and settlement rules are unchanged.
Background tasks do not block ordinary Manager Send after its primary turn completes, but native-only
completion/peer turns in running/requires_action state do. Tasks do
block intentional `close_input` and guarded restart while the owner is live or unproved:
closing the native conversation could lose them. After proved exit and settled drains, or host
reconstruction with the PID/start witness absent and UUID lock free, stale retry/background
markers clear automatically. Their exact old values remain in `native_markers_cleared` incidents;
`background_exit_unknown` stays a non-blocking observation. Resumes never inherit those markers.
A result that an authorized interrupt ended clears `native_retry_active` whatever its origin (a
background or task-notification wake-up turn included), so it no longer holds Send, `close_input` or
the setters until process exit.

### A Manager is never continued automatically

Dan's rule: after a crash, a lockout, an interrupt or a host restart, a **Manager** gets no automatic
`continue`. Only Dan knows what changed meanwhile (another Manager, a sub-session, a ruling), so an automatic
continuation acts on stale context. `Supervisor.schedule` withholds it (`recovery_withheld`, escalated to Dan)
and `Supervisor.consider_recovery` cancels one scheduled earlier. Dan's next Send reconnects the Manager.
Conductors, seats and workers keep the one journaled automatic continuation.

Labelled notifications are exceptions, not continuations: for a **five-hour** usage-limit episode the host
sends the current Manager one labelled automated recovery check at the structured reset + 300 s
(*Five-hour recovery check* below). It carries no original input, reconnects only the current Manager if needed,
and carries Dan's standing quota-only recovery authorization [ap:TEUN9Y]; it asks the Manager to inspect current
work and coordinate Conductors at existing program authority.
The lifecycle's observer-switch/explicit-notify outcome and specific post-stop failure/recovery
notice use that same current-Manager reconnect/exact-receipt path (below). Generic crash, interrupt, restart and
weekly-window Manager continuations stay banned; the host never decides to switch accounts.

### Usage-limit lockout: halted, never resumed automatically

Dan's rule (2026-10-22): when the account is out of its usage window, no session sleeps until the reset
and no session is scheduled to wake or continue there. The only scheduled action is the separate five-hour
Manager recovery check below — one new Manager message, never a blind session continuation; weekly windows have no such
notice. The signature (`supervision.usage_lockout`) is Claude Code's own
`rate_limit_event` `rate_limit_info`: `status:"rejected"`, a usage-window `rateLimitType` (`five_hour`,
`seven_day`, `seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included`, `overage`, or a newer
`five_hour…`/`seven_day…` window), no overage in use (`isUsingOverage` not true) or available
(`overageStatus` not `allowed`/`allowed_warning`), and a `resetsAt` still ahead. It counts during a 429
`api_retry` (either frame order) or on a terminal capacity error. An ordinary capacity 429 or 529
carries no rejected window and keeps the unbounded native retry of the launch profile. Then:

1. The host records `quota_halt` on the session (`status`, `rate_limit_type`, `resets_at`, `authority`,
   `attempts`) and raises one `quota_halt` incident (`alert_no_retry`, escalation target Dan; its
   detail names the reset and the way back). A still-scheduled recovery of the session is cancelled.
   A `five_hour` halt also arms the Manager recovery check below, coalesced by that structured reset.
2. Mid-retry it ends the turn through its own correlated `interrupt` control (no signal), authority
   `usage-limit lockout: <type>, resets <time> …`, `cancel_queued:true` (queued inputs are cancelled;
   their texts stay in their receipts). A failed interrupt (`quota_halt_interrupt_failed`) is tried
   again on a later retry frame, at most 3 times; any operator interrupt also ends it.
3. The interrupted result marks it `halted`. No recovery, crash recovery or `continue` is ever
   scheduled for a halted generation (`recovery_withheld` / `quota_halt_missing_result` incidents);
   the Manager recovery check is a notification, not one.
4. Every few seconds the supervisor closes a halted process's input once it is provably idle (the
   operator-close holds, plus no wake-up due; an unknown task level is read back first exactly as
   the guarded restart does): `close_reason:"quota_halt"`, status `closing` → `closed`. A background
    task keeps it open until the task ends or an explicit account switch retires its proven observation mechanism (below); a wake-up turn that hits the limit again is halted the same way,
   and so is a Send that reaches it while still open: a rejected window still ahead survives the Send (the
   process keeps the login it started with, and Claude Code repeats an unchanged rejection at most every
   30 s, so that turn may bring only the 429 retry).
5. The way back is any ordinary operator action: the Manager's pane Send reconnects as a new
   generation, a seat's `oc_seat resume` / `oc.sh claude follow-up` resumes it (closing a still-open
   halted process first), both on whatever login is current. A new generation carries no
   `quota_halt`; the next accepted input moves it to `last_quota_halt` and dispositions the
   `quota_halt` escalation. Nothing here refuses Send, interrupt, close, resume or the guarded restart.

`oc_seat wait`/`oc.sh claude wait` report a halted session as needs attention (exit 4) with one
`⛔ HALTED: Max usage limit reached (…, resets HH:MM)` line, naming the five-hour Manager check's state
when the record carries one and the valid follow-up command; `info` and `oc.sh sessions` show the same.

`recoveries(execution_id?:ID)` returns durable `recovery_id,incident_cursor,source_message_uuids,
reason,due_at,delay_basis,action_uuid,submitted,status,original_replay:false,effects`.
Status scheduled → claimed **before launch/write** → accepted → completed/blocked. Explicit
numeric/ISO reset/retry metadata governs waiting when available; otherwise conservative fallback
**one hour** (human reset prose is not guessed). `system/api_retry` never causes a host Send and
blocks a scheduled continuation while the native process is live; proved exit clears the stale
retry flag. Recovery text starts `continue`, instructs inspection of native
interrupted-tool/history receipts/postconditions, and contains no original input. Uncertain source
receipts keep `effects:uncertain` and `superseded_by_recovery`, not completed. One automatic action per
lineage; repeated failure or restart finding an uncertain claimed action escalates, never storms.
`resolve_recovery(execution_id,recovery_id,disposition:TEXT)` explicitly cancels a still-scheduled
action or dispositions a blocked/completed one after inspection. Claimed/accepted actions refuse, and
so does work a live owner may still be doing (an input its own turn may still answer, unsettled
background tasks, a native retry); a proved-gone owner, or inputs an earlier generation accepted,
never do. It does not release the inputs (`release_input` does, and dispositions the recovery with
them); acknowledgement never blindly cancels/replays an uncertain send.

### Five-hour recovery check: one Manager notice, not a session wake

Dan's 2026-10-24 ruling adds one narrow exception to "a Manager is never continued": after a **five-hour**
usage-limit episode the host sends the current registered Manager one clearly labelled automated recovery
check at the structured reset + **300 s**. It is a new request to inspect current work and coordinate
Conductors, not a replay of the interrupted turn; only its Manager recipient may be reconnected automatically.
No Conductor or worker is independently resumed by the timer. Weekly (`seven_day…`) windows,
any other window type, an ordinary capacity 429/529, a crash, an interrupt and a host restart keep the
existing rule: no Manager continuation.

`quota_wake.QuotaWake` owns one durable episode per reported five-hour reset, normalized to its epoch second
so seconds/milliseconds fields coalesce (`quota_checks`, independent
of any native session). `Supervisor.quota_halt` arms it for an observed `five_hour…` halt and coalesces every
session reporting that same reset, recording each one's program/execution/socket/role and source
(`quota_halt`). A **real** five-hour refusal from `oc_backend.quota_guard` during a genuine launch arms the
same episode (`quota_pause`; source `launch_guard`), so a program paused at the 95% guard also gets the
check. Reading usage, `prepare`, `info`, a dry run, a preflight or any read-only admission arms nothing. A
structured reset that is genuinely absent is reported, never guessed: the halt stands and no deadline is
invented.

The episode is `scheduled` when armed and its only deadline is `due_at = resets_at + 300` s. At due time the
supervisor selects the current Manager by actual input recency (then launch time), reuses a live owner or
reconnects that exact conversation when its process had ended, waits for native initialization with no
pending settings control, and steers the notice through the ordinary journaled Send path (an open turn
receives it at its next tool step; an idle one starts a turn). Statuses are `scheduled` → `preparing` →
`sending` → `sent`, or `delivery_problem` with the reason; preparation is re-checked each tick and a failed
pre-admission delivery attempt is retried on a later tick after 30 s. An admitted but uncertain Send is never replayed. The message starts
`AUTOMATED FIVE-HOUR RECOVERY CHECK — not a new message from Dan.`,
names the affected sessions and carries **[ap:T1TNT3]**, to be read only on this notification: reconcile
current registries, ConductorState, successors and newer rulings; leave completed work and unrelated human
HALTs alone; steer each current quota-paused Conductor once to reconcile every in-flight assignment; recover
Manager-direct unfinished work; never duplicate a worker or repeat an uncertain mutation without inspecting
its live postcondition; report briefly. It carries no original input and changes no cast, model or program authority.

The alarm is bookkeeping, not session work: it is not a `pending_recovery`, escalation, busy marker,
settlement hold or close/restart refusal, it keeps no session busy, and it survives closing every halted
process and a host restart with its status and recipient. Delivery uses one retained `message_uuid` through
the ordinary durable receipt: after a lost reply the host reads that receipt again
(acknowledged/started/completed → `sent`; uncertain, cancelled, withheld or a proved-gone owner →
`delivery_problem`) and never makes a second semantic Send. `state`/`list` expose the read-only
`quota_recovery_check` of a record's own `five_hour` halt — `episode_id`, `resets_at`, `due_at`, `status`,
`recipient`, `message_uuid`, `failure?`, `sent_at?`, plus operator/admitted-delivery facts below — associated
by that halt's reset; `quota_checks` lists
episodes and health reports `five_hour_recovery_check: 1` (a guard-only pause has no halted row to carry
it). The pane appends the notice's own line to the halt text (banner, row hover and glance): *Manager
recovery check due at …*, *sent*, or *delivery problem …*. Ordinary Send, `oc_seat resume`, interrupt,
close and the guarded restart are unchanged; the notice adds no gate.

`QuotaWake.control_episode(episode, action, authority)` synchronously defers or cancels **that exact episode**
(`action: defer|cancel`, nonempty operator authority). It commits `deferred_by_operator` or
`cancelled_by_operator`, `due_at:null`, the retained `original_due_at`, operator authority/time and a
`quota_episode_control` event cursor before stopping any pending notice admission. Its structured reply
includes the exact retained `record`, a `display` projection and the existing exact `receipt` if one was
already admitted. Repeating the same action/authority is idempotent. Neither action erases affected work,
changes the message UUID, resumes work, or changes any other episode's default reset + 300 s alarm.
Same-episode halts may append affected records but never re-arm a controlled alarm, including after restart.

An in-flight reconnect finishes normally and rechecks the durable control before Send. If notice Send is
still waiting for its command lock and has no receipt, only that private pre-admission task is stopped;
unrelated inputs and watched work are untouched. An already admitted Send may complete: operator status
and the null deadline remain, while `delivery_status` separately retains `sending`, `sent` or
`delivery_problem` and `display.send_admitted`/`receipt_status` show the actual receipt. A cancellation
request is not a claim that admitted input was unsent; late acknowledgements remain visible without replay.

### Manager-requested account-switch outcome

#### Observation-only account switches

An explicit `--account LABEL --close-idle` switch on the current host automatically inventories what holds the
old login. Pure `sleep N` / `sleep N; date -u`, `oc.sh timer`, the current `oc_seat.sh`/Python and
`oc.sh seat|claude wait` session/socket doorbells, and finite recognised read-only polling bodies are typed
observations. The **whole executed input** is inspected from scoped raw tool records, including child Bash;
the cropped 1024-character command and task description are never evidence. A timeout does not make a
test/export/edit/Agent disposable. Mixed effects, an unknown command/run, an open turn, accepted input,
native retry, permission/queue ambiguity or broken ownership identify the exact blocking session/task/reason.

For an owner containing only proven observers, the switch commits every obligation and its master/per-program
report identities before stopping anything. It freezes **that exact native PID/start witness**, drains and
revalidates output and descendant ownership, then shuts down that one observer-only native owner. It never
signals a process group, another owner or the watched worker. This is deliberately not “ignore background
tasks and EOF”: real no-inference native probes showed that EOF, a late `stop_task`, a notification UUID and
an interrupt sweep do **not** reliably prevent naturally completed tasks from opening another old-login turn.
Exact frozen-owner shutdown does. A model request already dispatched before the freeze cannot be recalled;
its real receipts remain a pre-boundary race. Genuine work discovered during revalidation is left alone.

The normal guarded host/PID/exit/drain/account-selection/replacement-health checks still run. Switch retry,
timeout, cancellation or failure keeps actual stop outcomes rather than pretending waits remain armed.
Native `stopped` is not watched-job completion; a shell that exited while frozen may have an unobserved
completion/failure and is explicitly marked for reconciliation. A host crash retains exact retirement intent;
restart may finish a still-frozen exact observer-only shutdown after revalidation, never signal a reused PID.

`observer_recoveries` retains one master index and one report per affected **program**, including the
Conductor's own waits, direct workers and helpers with immediate parents. Manager, standalone and unresolved
ownership have separate index sections. Reports resolve current extant Conductors on either backend at
publication/recovery time; a historical Conductor SID is provenance, not the destination. Independent
background publication never holds the switch. A verified switch stages one exact-UUID index notice to the
current registered Manager through the existing receipt/reconnect path; it does not wake/resume every leaf.
The index and notice can remain locally pending while Workflowy or a Manager is unavailable. Pre-stop
refusal/cancellation retains partial effects but does not trigger exhausted-account recovery inference.
A post-stop failure is deliberately eligible once verified service returns: old-selector restoration
is a failed switch with old service ready, not completion; committed-target recovery continues there.
The shared finalizer handles normal/recover/restore/bound-host evidence without waiting for publication.
The Manager coordinates
current Conductors, inspects actual results/errors/sockets and re-arms only obligations still wanted.

Page AP-IDs and create intent are durable before publication. Lost create replies reconcile the same AP-ID;
the Workflowy capture API supplies no caller idempotency token, so an ambiguous missing cached read is **not**
permission to create a duplicate. Such a create stays explicitly uncertain with its local report available.
This is not a promise of automatic exactly-once page creation after a request whose admission cannot be
determined. No account/credential or OpenCode in-flight Authorization migration is inferred from these records.

New restart/switch recipes and action evidence extend `observer_recoveries.transition`; there
is no sidecar selector or parallel notice authority. `facts` is its sanitized derived summary.
One `observer-switch:<operation ID>` episode is retained before stop, not yet due. Observer-switch
success or explicit notify authorizes completion delivery; a failed post-stop lifecycle authorizes
its narrow labelled recovery outcome once service is usable. Plain successful restart/resume
sends no turn by default. Previously admitted legacy `account-switch` episodes keep their exact
UUID/recipient receipts, never migrate into a second semantic notice.

`QuotaWake.advance` owns current-Manager reconnect and initialized/writable/settings observation.
No registered Manager means pending, not a newly invented conversation. The exact recipient,
generation, text and message UUID become immutable before possible Send admission; every existing
receipt, uncertain included, reconciles first and prevents semantic replay/retarget. Labels are
`AUTOMATED ACCOUNT SWITCH COMPLETE`, `AUTOMATED ACCOUNT SWITCH FAILED — SERVICE RESTORED`, or
`AUTOMATED RESTART RECOVERY`, all followed by “not a new message from Dan.” They retain actual
account/epoch, original failures, exit/service proofs, Manager state, partial obligations and local
status/recover/publication facts; fresh quota is optional, not a recovery dependency. Scheduled is
not delivered, and ACK/started/completed is transport delivery, never accepted task completion.
Exact defer/cancel survives finalization and late admitted receipts. Five-hour reset+300s behavior
is separate and unchanged; the host never decides to switch accounts or resume all watched work.

Launch/control models are `haiku`, `sonnet`, `opus`, `fable` or explicit
`claude-{haiku,sonnet,opus,fable}-VERSION` names (Claude Code 2.1.299 offers Fable 5.1 on Max as
`fable` → `claude-fable-5-1`, effort low…max). Provider aliases and special paid-context/entitlement routes such as `[1m]` refuse until
separately qualified; this is no automatic billing/fallback promise. Context uses the live native window.

Crash = accepted unfinished input + proved PID exit, **even exit0**. Drain buffered results before
judging; keep lock until both pipes EOF, reacquire and resume **same UUID** with stored snapshots and
current verified choices. EOF while possibly live, unknown owner, unindexed raw gaps, deterministic
startup failure or unresolved setting/interrupt effects never authorize replacement. Intentional
idle exit is not task failure.

Default silence observation threshold **600 seconds**, configurable via host `--silence-threshold`
before `start`/`serve`. Active turn/initialize/control/native-retry/background silence records
onset/duration/recovery. **Zero kill, signal, interrupt, close-stdin, abort, resend or relaunch effects**.
One-second observer cadence and control polling cadence are not deadlines.

## Launch and permission policy

`scripts/claude_headless/launch.py::build_launch` returns fixed `argv`, sanitized private `env`,
secret-free `safe` record and maintained `profile`. It pins `/home/daniel347x/.local/bin/claude`;
stream-json both directions, verbose/replay/partial output, fixed `--thinking-display summarized`
immediately after `--include-partial-messages` for **fresh launches and same-UUID resumes**, stdio permission host, **host/manual**,
`--setting-sources ""`, explicit model/effort/UUID or same-UUID resume (or a fork, `fork_from`: `--resume BASE
--fork-session --session-id NEW` from the base's stored snapshot, the only launch carrying `--fork-session`), private settings/prompt files,
and strict explicit Workflowy + session-bound context MCP. No shell string, arbitrary extra flags, allow-list, bypass,
`dontAsk`, API fallback, turn/dollar/global wall-clock cap. Login is unchanged and must be native
`claude.ai` / `firstParty` / `max`; initialize must corroborate Claude Max before Send. Version is
read without upgrading. No credentials, email/account IDs or inherited env values enter launch logs.

Every Manager generation (fresh or resumed) also sets the request-shaping environment `MANAGER_REQUEST_ENV` (`CLAUDE_CODE_FORK_SUBAGENT=0`) and stores it in the record as `request_env`: Claude Code's own native fork subagent (Agent's `subagent_type: "fork"`, on by default in 2.1.302) stays off, and the Manager's quick work goes through its own seat fork (`oc_seat quick --fork-self`, below). The gate changes the Agent tool description every request carries, so a fork of a Manager and its resumes re-apply the base's recorded `request_env` byte-identically, and the fork's first call still reads the base from the prompt cache.

Fresh role persona/tool choices come from `config/claude-headless-roles.json`: Manager replaces the
coding prompt, explicitly selects eleven built-ins (including Grep/Glob, background Bash/TaskStop, Agent
and SendMessage for its own native subagents), and disables skills; MCP tools remain strict/eager. Seat roles name their tools too, so settled seats
are collectable, and every seat role carries the same nine built-ins (Dan's all-or-nothing ruling)
`[Bash,Read,Edit,Write,Grep,Glob,WebFetch,WebSearch,TaskStop]`: `conductor` replaces the coding prompt,
`worker`/`helper`/`base` append to it; none has Agent or Monitor, and all disable skills. That is
availability, not authority: a Conductor still never writes code. `config/claude-headless-settings.json` carries
the description cap16384, eager tool setting and empty attribution/no-git-instructions policy.
The single inventory, rationale and rollback switches are in
[Repository-owned Claude Code customizations](claude-headless-manager.md#repository-owned-claude-code-customizations-u06c).
Resume retains the original role policy from `launch.json` (pre-policy runs use append/default),
as well as immutable prompt/settings/MCP except the audited Workflowy URL token migration. Python code changes require an idle guarded host restart;
use `scripts/claude_restart.sh`, not an occupied-host stop. The script sends no turn unless its
explicit account-switch `--notify-manager` option requests the labelled outcome notice above.

Every Manager launch/resume also generates `agents.json` from `quick-worker/AGENTS.md` via
`claude_headless.quick_agent.manager_quick_definition`, supplying its file with native `--agents`.
The private snapshot/source hashes are recorded; no inline doctrine enters argv. This is
launch-local, never a `~/.claude` or project agent install. The child's `model:inherit`, omitted
`tools` and `disallowedTools:[Agent,SendMessage]` inherit native model/MCP tools while preventing native
grandchildren and keeping SendMessage the Manager's own. Inherited child-model override/force environment variables are sanitized. Primary
prompt/settings/role policy and non-URL MCP bytes still remain immutable on resume; refreshed child doctrine does
not retrofit Agent availability or the primary overlay into an old Manager. Restart composition
preflight checks the quick doctrine before host contact, and actual launch regenerates it again.

`launch.thinking_display_requested:"summarized"` records the requested display mode, not verified
settings readback. The hidden native CLI seam is pinned/probed on **2.1.299**; the public Agent SDK
equivalent is `thinking:{type:"adaptive",display:"summarized"}`. `showThinkingSummaries:true` alone
does **not** request summaries in headless mode on that version. Summaries are display-only,
not raw reasoning, assistant prose or Refine context. Old omitted blocks cannot be recovered from
their signatures. Resume leaves old prompt/settings/MCP snapshots byte-identical and requests
summaries only in the new process; no immutable old launch record is rewritten.

The maintained `config/claude-headless-settings.json` materializes the scratch hour profile with
compaction/memory/fast-mode off, byte watchdog/body-idle off and parsed-event/API/MCP timers at one
hour. **Positive `CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS` is absent and inherited values are removed.**
`CLAUDE_CODE_RETRY_WATCHDOG=1` stays on; MaxRetries15 is not a promise every failure retries forever.
Capacity retries stay unbounded; a usage-limit lockout's retry is ended by the host
([halt](#usage-limit-lockout-halted-never-resumed-automatically)).
Sanitization also removes all `ANTHROPIC_*`, `CLAUDE_CODE_USE_*`, injected OAuth/helper/simple/bare/
fast-mode/provider/model-remap/alternate-config-dir settings and inherited turn/budget caps.
Native model, artifact, transport and managed-policy limits still exist.

Workflowy URL: explicit operator override → `WORKFLOWY_MCP_URL` → dynamic WSL default gateway,
port8766 `/mcp`. Userinfo/query/fragment endpoints refuse (no credential-in-URL launch log).
`mcp.json` contains `timeout:3600000` and literal `Bearer ${WORKFLOWY_MCP_BEARER}`, not OpenCode's
`{file:...}`. The bearer is read from the existing broker file or supplied environment and lives
only in the child environment; it is not extracted from subscription auth or stored in argv/config.

On resume, re-resolve the current environment/gateway endpoint unless the stored launch records an
explicit operator URL. Only `mcpServers.workflowy.url`'s JSON string token may change; every other
snapshot byte remains exact. `workflowy_endpoint_reresolved` records prior/current URL and generation.
Process collection is native idle/empty-level proof, not silence: see the single
[lifecycle contract](claude-headless-manager.md#conversation-and-process-lifecycle-contract).
Health/state expose the default 3 h threshold and the pass cadence (each pass checks every live process,
health `collection_scope`); unknown evidence stays running.
No Claude Code version is pinned: each process must prove the collector's capabilities in its own
native output (health `collection_gate: native_capabilities` and `collection_native_proof`; state
`collection_refusal` names what a refused process lacks).

**ALLOW BY DEFAULT:** every native `can_use_tool` is immediately journaled and answered:

```json
{"type":"control_response","response":{"subtype":"success","request_id":"<same-id>","response":{"behavior":"allow","updatedInput":"<original input JSON value, not this placeholder>"}}}
```

The one exception is a **root search**: a Bash, Grep or Glob call that walks a whole tree from a
repository root or above, or searches across the repositories under a folder above them, is answered
`{"behavior":"deny","message":"…"}` instead, its permission record carrying `behavior: "deny"` and a
`root_search` record ([root-search guard](root-search-guard.md); any doubt allows). A permission
request carries no folder, so it is judged with the folder unknown (never the launch folder). Each
generation's record says whether its settings env makes Glob honour `.gitignore`
(`glob_honours_gitignore`: `CLAUDE_CODE_GLOB_NO_IGNORE=false`, forced on every fresh launch; a resume
keeps its stored settings), and Glob is judged by it.

**The root-search hook.** Every launch's `initialize` registers one SDK hook,
`{"PreToolUse":[{"matcher":"Bash|Grep|Glob","hookCallbackIds":["root-search-guard"],"timeout":30}]}`
(`INITIALIZE_HOOKS`), so Claude Code sends every Bash, Grep and Glob call, including those it would
approve itself, as a `hook_callback` before any permission check, carrying the folder its shell
stands in (`cwd`). The host answers each one at once: a root search gets
`{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"…"}}`,
which Claude Code shows the model as the tool's error; anything else gets `{}` (no opinion: the
normal permission path follows, and does not judge that call again). Each answer is journaled in the
permissions table (`kind: "hook_callback"`, `behavior: "deny"` or `"no_opinion"`, `hook_cwd`,
`cwd_known`) with a `hook_decision` event; duplicate request IDs replay. Callback hooks are not
settings: the effective-settings readback carries none, and the collector's readback `initialize`
(no `hooks` key) keeps them. Nothing hangs on the hook: a host that stopped answering for its 30 s
`timeout` would make each Bash, Grep and Glob call fail with Claude Code's "PreToolUse hook did not
respond before its timeout" error (the call does not run; the turn goes on).

**Its witness.** The hook rests on undocumented Claude Code behaviour, so each generation checks it
as it runs (`NativeSession.root_search_witness`; record `root_search_hook`, reset at each launch): a
main-thread Bash/Grep/Glob call whose result is not an error but which the hook never answered
(`missing_callback`), a hook call with no `cwd` while no other main-thread Bash runs (`cwd_missing`),
and a denied call that ran anyway (`deny_ignored`). Each is flagged once per generation as a
`root_search_hook` incident with an escalation naming the Claude Code version (no input named, so no
recovery waits on it; nothing is refused or delayed). `state` carries the counts; health
`root_search_hook` sums them by Claude Code version (`generations`, `seen_answering`,
`results_checked`, `flagged`), names flagged generations and shows the guard's `extra_roots`. After
a Claude Code update, one health read after any session's first Bash/Grep/Glob call says whether the
hook works on that version ([root-search guard](root-search-guard.md), "Is it still working?").

The real `updatedInput` is the input's **original lexical JSON slice**, avoiding float/Unicode/
escape roundtrip changes. No `updatedPermissions` is ever emitted. The full input/request and all
optional fields (workingDir, safetyCheck, consent, matched rules, suggestions, etc.) remain private
in the raw frame/permission receipt. Missing reason is `unknown`. Duplicate request IDs receive
the cached exact decision/response (also across resume generations) with no second decision mutation;
changed duplicate content is flagged. An absent input member is logged/flagged and allowed without
an `updatedInput` override—no missing original is fabricated as null or an empty object. Initialize's
pending-permission redelivery uses the same broker. MCP elicitation,
user-dialog and unknown controls are retained/escalation-needed, **not blindly approved**; output
drains continue. Intrinsically allowed tools and managed/hard policies may resolve natively;
no allow-list does **not** prove every tool call asks. Native denials/tool errors remain receipts.

## Durability, feed and next-unit seams

One async writer owns stdin. Independent byte readers continuously drain both stdout and stderr,
even with zero subscribers. Complete stdout frames are indexed with sequence, generation, arrival
time and raw byte offsets; malformed/unknown frames remain visible and do not stop later approvals.
Frames up to64 MiB are parsed (33 MiB fixture proven); larger frames are disk-spooled **in full**
and visibly `unsupported_frame`, never a prefix mislabeled complete (65 MiB fixture proven).
EOF retains an unterminated tail. Stdin intent, pipe-write evidence, exact native echo, lifecycle,
results, EOF and exit are separate durable records. Native PID exit is published even if a descendant
still holds an output pipe: state/owner are `exited_draining`/`draining` and the UUID lock remains held
until both drains reach EOF, then owner becomes `gone`. No timer kills the descendant. The host does
not import an SDK's 1 MiB buffer
or 60-second control wait. Subscribers pull the committed index; no viewer is a blocking tee and
there is no bounded raw-record eviction. On disk/index failure, state reports `infrastructure_error`,
withholds new input/durable-acceptance claims and keeps draining where possible; lost bytes are
counted, not called silence or retried. Once storage works again, `clear_infrastructure_error`
releases it with the gap journaled (above). Raw records contain **full private tool inputs**; do not publish them.

**One transaction per read.** Each stdout or stderr read is appended and fsynced first; then everything
it produces (its frames' events and cursors, receipt/control/permission/incident updates, the session
record written once) is one SQLite transaction, committed before the next read (`Store.batch`); one
stdin write's evidence is one transaction too. A host crash before that commit leaves the read's bytes
retained and unindexed: the next start indexes them as one `raw_reconciliation_gap`. A storage failure
inside a read rolls all of it back (never half a frame's projection), marks `infrastructure_error`, and
its bytes become the gap `clear_infrastructure_error` indexes; a cursor handed out inside a rolled-back
read never names a different event later. The host keeps an in-memory copy of the committed session
records (`Store.cached_sessions`: filled once at start, after the store's migration and before
reconstruction): a record write enters it only when it commits (a batch's records at its commit), a
failed record write or any rollback drops it (the next read decodes the table again), and readers get
fresh copies, never the cached objects. `list`, the 60 s orphan recheck and the five-hour notice's
choice of Manager, and the observation ownership/switch inventories read it; `state` and other readers
still read the table. Result copies are rows
of their own (`results`: each native result's identity for duplicate detection, and a background
result's summary), written once and never
part of the session record. An ended session leaves memory once its exit is journaled (owner gone, no
durability failure): `state`, `list`, `replay`, `raw`, `receipt`, `control_receipt`, `release_input`,
`resolve_*`, `resume`/`reconnect` and follow-ups read its stored record, as after a host restart. That
release still runs if final Supervisor bookkeeping raises, so a later forget is visible immediately.
The `events_by_session` index lets per-session replay/incidents and a missing stream-end seed read
only that session's events. `stream_ends(sid,generation,stream,end)` is a rowid table, forgotten with
the other per-session tables. Each offset-bearing event advances its nondecreasing end in that event's
transaction. A small `meta.stream_ends_through` event watermark detects a pre-U4.1 writer: a later event
head invalidates only these derived ends, so the next gap check seeds them again rather than inventing
a gap over already indexed bytes. A lowered head after forget does not invalidate other sessions' ends.
Startup checks each stream by primary key; only a missing row runs the old per-session stream aggregate,
then persists even a zero end. First activation (or the first start after an older writer) fills that
small table once; subsequent starts do not revisit already indexed stream history.
An older store without the session index builds it once and moves old records' result
copies into rows (fresh-copy timings below), journaled in `meta` and `service.log`.

`task_inputs` retains every matching assistant tool-use and task/run lifecycle frame's sequence,
including sidechains and Agent scopes, in that frame's native-read batch. A per-generation coverage
sentinel makes a missing key authoritative only after complete coverage: new generations start covered;
an old or partially indexed generation back-fills lazily in one batch on its first lookup, never at
startup. An unindexed append (including an older host's) invalidates coverage; an unavailable index
uses the original scan. Raw-input deduplication, first-frame cursors and all task-proof reasons stay
unchanged. `forget` removes both the frame pointers and coverage with the session's other rows.

### Durable conversation rows (prepared; operator activation remains gated)

The same private `host.sqlite3` also holds display-row structure and raw-frame witnesses, not prose
bodies: `rows`, append-only `row_parts`, `row_keys`, `row_diagnostics`, `row_incidents`, `row_sets`.
Streamed text/thinking/tool-input witness lists live in row_parts, not growing copies in rows.body;
row reads hydrate only that row's still-active lists. Superseded parts remain append-only witnesses.
One shared `RowProjector`/`project_through` interprets the log for both the live writer and backfill.
Every host append passes the synchronous rows hook, inside the existing native-read batch (or one
append batch outside reads); rows derived there commit with its raw events. A storage failure rolls
the whole read back. A projection-only failure rolls back to its SAVEPOINT, reloads the backend's
in-memory state and marks that set `unreadable` in the same transaction, so raw events still commit.
Row `seq` is dense from zero **within each scope**, permanent across process generations; `ordinal`
is arrival order across scopes. `changed`/`body_changed` are durable global **event** seqs.

Each row-family response includes `epoch`, `status`, `built_through`, `counts` (scope → row count),
and `as_of`. The last two watermarks are numeric global event seqs, not cursor strings: built_through
is the consumed prefix, as_of the newest committed event of this session when the synchronous read
answered. No await splits a reply's reads. The backend's internal built_through property converts its
stored number to `identity:seq` for replay; this does not change event cursor semantics.
Epochs are durable `rows-…` identities; `complete|queued|running|unreadable` records ownership/availability.
Absent rows answer `epoch:null,status:"queued",built_through:0,counts:{}`; a consumer falls back.
Only a complete set is current. An append projects a complete set only when an indexed range of at
most 251 entries proves at most 250 events remain (global seq differences are not event counts).
Otherwise it queues the set in that same batch and wakes backfill; no old-log catch-up runs inside
the append. While queued/running the live writer appends raw events but never projects them, and
only backfill does. An unreadable set retains a short `reason` and `at` (epoch seconds) in its own row;
appends still commit raw events, and backfill never retries it. A new session starts complete and empty.

`rows` returns `{rows,epoch,status,built_through,counts,incidents,incident_count,as_of,has_more,
next_from_seq}`. Every row includes `scope`, `seq`, `ordinal` and the projector's structure/witnesses.
With no selector, it returns the last `limit` rows of the named scope (main when absent).
`from_seq` is an inclusive scope position, with forward pages; if has_more, next_from_seq is the next
scope position, otherwise null. `by_origin` selects the exact creating event cursor (optional scope
filters it). The three selectors `from_seq`, `by_origin`, `agent_window` are mutually exclusive.
`incidents` is independently paged, at most 100 from incident_offset, with the full incident_count;
advance incident_offset by the returned count until that total is reached, even if rows are empty.

**Agent windows use another coordinate, never scope seq.** `agent_window:[lo,hi]` selects every
`agent:*` scope in global ordinal order with **exclusive** bounds `lo < ordinal < hi`. `lo` may be
-1, `hi` may be null (to the tail). Absent scope includes all agents; one explicit `agent:…` scope
filters to it; explicit `scope:"main"` is contradictory and refused. Window replies have
next_from_seq:null; when has_more, `next_after_ordinal` is the last returned ordinal. Continue with
`agent_window:[next_after_ordinal,hi]`. This loses or repeats no rows across interleaved scopes.
The `(sid,ordinal)` index bounds the read to that window, rather than replaying the conversation.

On first open, migration registers `row_schema:1`, seeds queued sets using the session-events index,
and journals `migration_rows` plus one service-log line; it does not build rows on the startup path.
Startup inventories `sessions` plus `row_sets`, excluding sid `''`, and seeks `MAX(seq)` once per known
conversation instead of grouping every event. Tombstones and conversations without events seed nothing.
It requeues a complete set left behind its log by an older host, without changing epoch or positions.
Unparseable or unusable row-set metadata becomes that set's persisted `unreadable` status with a short
reason and timestamp in the detecting batch, using the full empty metadata shape. Raw events and stored
row witnesses are retained. Requeue never turns `unreadable` back into `queued`. There is no rebuild verb:
removing that sid's `row_sets` row lets the next start seed a fresh queued set. A full offline re-projection
also clears that sid's other five derived row tables (not events or raw files), so old keys/positions
are not reused.

`Host.backfill_rows` starts after the supervisor and stays alive, waking whenever an append queues a
set. Its in-memory queue refreshes on wake, not by rescanning event history each round. It walks sets
round-robin, live sessions first, one page per set per round: at most 250 events and a named host
`ROW_SLICE_MS = 20` elapsed target, checked before each page/event with 10 ms reserved for the FULL-sync
commit/checkpoint (`ROW_COMMIT_HEADROOM_MS`), not an assumption that committing is free. At least one event advances
even if that event or the page read alone spends the work budget. The task freezes the retained startup heap
into cyclic GC's permanent generation after bind: reference counts still free replaced acyclic records and
new cycles collect normally, without re-walking the old record cache during every full GC. Task exit unfreezes
it; no full collection or startup scan is added. Each page is one transaction; eight
zero-delay loop turns **between** pages let socket accept/read/dispatch/write finish so list/state/Send
keep running. Tombstones are rechecked before every page; a deleted set is never recreated. The page
that reaches the tail marks complete with its rows and watermark. Interruption resumes at built_through.
A failed page rolls its projection back to a SAVEPOINT and persists `unreadable` in that same batch;
other sets continue. This status survives restart; there is no automatic rebuild/retry path.

Health advertises the four methods, `row_schema:1`, and `rows_backfill:{status,error,remaining_sessions,
built_through,unreadable}`. Backfill status is idle/running/complete/failed; a task-wide failure names
its exception class and message (cropped to 200 characters) in error, otherwise null. Complete means
no queued/running set remains, not that unreadable sessions were silently accepted or the task ended.
remaining_sessions includes those not complete;
built_through maps execution IDs to numeric watermarks; unreadable lists execution IDs. These answers
come from in-memory counters/maps filled once after startup requeue and updated only after an outer
`Store.batch` commits, including row-set replacements and forget/junk deletions. A rolled-back batch
never publishes its changes; health does not decode every row-set body on every call. The pane reads
complete rows first, cold-opening at the newest page; missing/building/unreadable rows or an older host
use the same full forward replay from event zero. That fallback retains its two-second budget and
continuations, 32-index LRU and aggregate summary-byte eviction, but no per-conversation refusing bound.
Native transcripts retain their own reader limits. Operator activation/restarts remain gated;
never start a Host on a copy containing real records (reconstruction can schedule real recoveries).
The offline proof tool reads an already-backfilled backup copy and raw files read-only:
`python3 scripts/claude_rows_compare.py dump --store COPY --raw-root RAW --source replay --out REPLAY`
and the same command with `--source rows --out ROWS`, then `compare REPLAY ROWS`.
Both dumps must use the same copy; they contain private previews and belong under `/tmp/opencode/`.
The rows source loads stored structure, parts, diagnostics and agent attribution through the same pane
wire/body renderers, without replaying the projector. It refuses absent/building/behind row sets.

Health also adds `load:{loop_lag_ms_max_60s,rows_behind_sets,rows_behind_events,startup,wal_bytes,free_bytes}`.
`rows_behind_sets` counts queued/running sets only, never unreadable sets; `rows_behind_events` is the
exact number of their session events after each set's watermark. It counts indexed per-session suffixes
once at open, then updates over newly appended or projected ranges and bounded forget deletions, never
subtracting global sequence numbers or recounting history on each health call/backfill page.
The serve-owned 1-second ticker measures how late each wake fires; the lag field is the maximum over
the last 60 seconds (zero before a sample). It observes only, never kills, restarts or gates work.
`startup` carries this process's exclusive `store_open_ms`, `migrate_ms`, `reconstruct_ms`, and
`stream_ends_seeded` (committed new stream-end entries in reconstruction: zero on an ordinary start).
`bound_at` is exactly the existing bound start receipt's `observed_at`, in epoch seconds; a later ready
receipt does not replace that instant. These are phase timings, not a claim that they sum to all launch
overhead. The first start after the stream-end schema/older writer may seed it once; later starts do not.
WAL size comes from a file stat (zero if absent), free bytes from the root filesystem's available blocks;
an unavailable filesystem stat is null, never a new health failure. Older consumers ignore the new block.

U04 extends `NativeSession.project/control`, `Host.dispatch/launch`, Store projections and one
`Supervisor`; existing raw/result/control evidence and same-UUID locks remain authoritative.
Results retain input UUID(s), origin, result_index, text/errors/denials, generation and raw cursor.
Any primary result naming a Send or steer completes that input, including a task-notification
wake turn that absorbed it; origin still governs background incidents and recoveries, unchanged.
Internal recovery-action receipts keep their existing user-origin-only completion rule and are
not released by stale-input reconciliation.
Each completion journals `input_settled` with the result cursor. At native idle with the primary
turn closed, no queued turn, pending native request or wake turn due, a model-delivered steer's
exact echo or a native `started`
cursor before that same generation's closed result proves an unmatched hold stale, not completed:
`input_stale` journals those cursors, the receipt says `status:stale`, `effects:uncertain`, and it
no longer counts as pending/busy or blocks a guarded restart. Automatic reconciliation never
releases a just-sent/not-yet-started input on mere idle, an ordinary Send's acknowledgement or
elapsed time. Reconstruction applies these same pending-receipt/evidence
rules to already-stuck entries, without replay or resend; no manual receipt repair is needed.
Exit0 without a matching result or stale proof becomes
`uncertain`; unknown ownership never authorizes a second writer. **A receipt is not task acceptance.**
The pane consumes `replay/subscribe/raw/tool_snapshot` and deliberately reconciles partial/final/history
identities; host diagnostics are not evidence of model-facing output. Its independent server enforces
exact registered Manager role/UUID/current-generation before any browser mutation. Receipt reads can
recover old scoped generation/UUID after pane restart without adopting a writer or requiring an old
opaque view handle to survive. Post-acceptance response/size failures are uncertain, never pre-write
refusals; reconcile that same operation ID.

To stop an owned fixture, explicitly `close_input`, verify native `owner:"gone"` and exit evidence,
then end that host using its verified PID/start witness. There is no automatic service restart/stop
or safe "kill everything" command. Stopping a host with live CLI processes can leave uncertain
ownership/effects; never treat a disappearing pane/socket/PID alone as permission to replace them.

## Seats through the oc_* scripts

Plan and decisions: [ap:0OP1FK]. A seat is one host conversation with role `conductor`, `worker`,
`helper` or `base`, started by a script through `claude_headless.client`; nothing Claude-specific
lives in `oc_client.py`. Two modules:

* `scripts/oc_backend.py`: **routing** `backend_for(agent | model ref)`: `anthropic/*` → Claude
  Code on Max, everything else (also `openrouter/anthropic/*`) → OpenCode; one loud global switch,
  `.oc_config.json` `"claude_backend": "opencode"` or env `OC_CLAUDE_BACKEND=opencode` (env wins); an
  agent file with front-matter `backend: claude-code` refuses that switch. `claude_launch_choice`
  maps an agent/`--model`/`--variant` to host model + effort (Opus and Fable alike).
  **Ids** `resolve_claude_id`: exact `cc-UUID`, a bare UUID, or `cc-` + a unique ≥8-hex prefix;
  never OpenCode's `resolve_sid`. **Host** `connect()`: `OC_CLAUDE_HOST_SOCKET` overrides the
  default socket; an absent host or one without `seat_interface` refuses (exit 8); it never starts
  a host. **Quota guard** `quota_guard` before every new seat or Conductor: the freshest native
  rate-limit frame across records must show five-hour < 0.95 and seven-day < 0.97 (a window past
  its reset counts as reset), else exit 6; at most 5 busy seats (worker/helper/base), else exit 7.
  Only a known exhausted status counts as exhausted (an unrecognised one warns). A follow-up to an
  existing seat is never refused for quota, only warned; overage in use refuses both.
  Thresholds: `claude_quota_five_hour`, `claude_quota_seven_day`, `claude_max_seats` in
  `.oc_config.json`, or `OC_CLAUDE_QUOTA_FIVE_HOUR`, `OC_CLAUDE_QUOTA_SEVEN_DAY`, `OC_CLAUDE_MAX_SEATS`.
* `scripts/oc_claude.py` (`oc.sh claude …`): `start` (guard under a cross-process lock → `launch`
  with labels → print `cc-…` → wait `ready` with settings read back → `send` → Claude's exact echo),
  `status`, `wait`, `info`, `follow-up` (idle only; context check first; a still-scheduled recovery is
  superseded, one already claimed waits; host `resume` of the same UUID when the process ended),
  `socket`, `cancel` (release the inputs its ended process can never answer, cancel a still-scheduled
  or blocked recovery, then `close_input`; a busy seat is refused), `resolve` (one selector: an
  escalation `--incident`/`--all-escalations`, a recovery with the inputs it covered `--recovery`,
  the unanswerable inputs `--release-input`, a durability failure `--clear-infrastructure`), `quota`,
  `route`. On a host with `release_input`, `follow-up` and `cancel` release a pending input no
  process can answer themselves (never replayed; a line says so), as the pane's reconnect does for a
  Manager, and `follow-up` closes a process blocked at launch before resuming it: an overnight crash
  whose recovery failed on an expired login takes a follow-up, a cancel or a resolve once the login is
  fixed, with no forced replacement.

**`oc_seat` and `oc_quick` on top (callers unchanged).** `oc_seat quick --agent quick-opus55-‹level›`
or `quick-fable51-‹level›` (medium · high · xhigh · max), or any quick agent with `--model anthropic/…`, starts a Claude quick
worker or helper: role `worker`/`helper`, cwd the quick-worker folder, labels mirroring OpenCode's
metadata (`title`, `kind`, `quick`, `manager_session`, `parent_seat`, `conductor_id`/`_n`,
`variant`), and it prints `⚡ QUICK WORKER cc-…` the moment the host registers it. On both backends,
program worker/lead starts (quick, dispatch and forks) keep the existing ownership prefix, then
`[ap:SOCKET] · ROLE · existing detail`. The worker's **own** top-level `dispatch.role` supplies the
marker (underscores become spaces), never a nested `prompt_for` role; a no-dispatch quick prompt
puts `session_role: ROLE` beside its opening SkyView line. This is display metadata, not a dispatch
or write grant. A SPEC_AUTHOR's strictness-pass socket says `STRICTNESS`; a Toolkit `Chain N`
ancestor adds `REMEDIATION` (also implied by FOCUSED_AUDITOR), including for split findings and
adjudication. Role alone cannot distinguish a first implementer from a remediation implementer.
No parallel role roster is stored in the scripts. Legacy named role/Prompt/stage-0 socket shapes
are fallbacks; no usable role says `QUICK` for a quick worker (normal: a quick job need declare no role) or `WORKER` with a warning for a dispatched one, never a guessed role.
Automatic detail is the existing socket-name hint (twelve words, 120 characters for the task
phrase); `--title` supplies verbatim detail but cannot remove the socket/role, even when empty.
Two read-only calls at start share one Workflowy connection: a guarded 80-line socket read
(long notes: head/tail; role headers belong at the top) and cached ancestry, never a subtree render.
Unavailable evidence warns and never refuses a launch; ancestry failure leaves remediation context
unknown. Helpers and non-program starts retain the old name-only/explicit-title policy unchanged.
Existing sessions are not renamed; no host or pane restart is needed. `--parent-seat`
takes `ses_…` or `cc-…` on either backend (a helper never starts helpers). `wait`, `info`, `socket`,
`resume` (`--model anthropic/M`/`--variant V` switch the same conversation first, verified by readback;
`--window`/`--land-by` refuse: the target is the host's), `cancel` and `resolve` take `cc-` ids; the one-session-per-socket guard and `socket` list
both backends (a Claude host that does not answer is said loudly and never blocks an OpenCode start).
`oc_quick --worker quick-opus55-‹level›` (or `quick-fable51-‹level›`) admits the worker against the host and the quota guard
before any write, and an `anthropic/*` Conductor `--model` likewise (level, composed prompt, host,
quota). `create` and `quick --fork-from` stay OpenCode: a `cc-` fork-from source refuses, and so does an
`anthropic/*` agent or model for `create` and for a `fork --base sb-…` (parked and forked in OpenCode it would bill
the API). A Claude Manager's own fork is `quick --fork-self` (below), which names no id: `--fork-from` stays OpenCode-only.

**Quick-program Project Maps.** `oc.sh quick --map "[ap:A]" --map "[ap:B]"` also accepts
`--map "[ap:A]" "[ap:B]"` or `--map "[ap:A],[ap:B]"` (forms may be mixed). Every handle resolves
before writes; duplicate nodes collapse in first-seen order. `policy.maps` records the full list;
`policy.map` keeps the first map, or null. Readers fall back to an old scalar `policy.map` when
`maps` is absent. Every fresh worker's first message names its maps and tells it to render each in full before
acting, on both backends; the scouted Conductor also renders each map, and OpenCode fork workers cross-check each.
`oc.sh seat quick --map …` accepts the same forms without `--fork-from`; with `--program` it defaults to the program's
maps. Without supplied/program maps, a top-level fresh seat infers them from the repositories named in its socket.
`oc.sh quick` infers on every route when `--map` is omitted, before writes, and records the result in the policy
(`--dry-run` shows it). Inference reads `system.config.get` → `project_maps_location`, then searches each direct map
under that location for the exact repository root path, in WSL or Windows form; paths to files/subdirectories and
similarly named repositories do not count as a map naming that root. Each inference prints one outcome line;
missing/unreadable settings, maps or a disconnected tab never prevent a start. `--no-map` (exclusive with `--map`)
skips defaults/inference and records `maps: []`, `map: null` in a quick program. Helpers get only explicit `--map`.

**Native Claude forks (`oc_seat fork --base cc-…`).** `fork --base cc-… --socket "[ap:S]" [--variant V]
[--model anthropic/M] [--target F] [--parent-seat …] [--no-wait]` (`fork_claude`) forks a Claude seat through
the host's `launch` `fork_from`: an ordinary new record (`wait`, `info`, `steer`, `resume`, the quota guard and
the seat cap apply unchanged) holding the base's whole conversation, in the base's role and cwd, from its stored
prompt and configuration, with the base's model and level unless `--variant` (on the launch line; the cache is
kept) or `--model` (warned: the cache is per model, so the first call writes the base again). Its one message
names its own id, its job socket and that the base's socket is not its to write; the base's own socket refuses,
and so does a Conductor or Manager base (a Manager's only fork is its own `oc_seat quick --fork-self`,
below). Labels `kind: fork`, `seat_base: cc-…`. `dispatch --lead --pre-pass`
labels a Claude lead seat `pre_pass: true` (an OpenCode agent refuses); `resume` refuses a pre-pass unless
`--extend`. Author jobs fork the completed pre-pass; resume --extend deliberately extends its reading.
`info` and `wait`'s done line print `🍴 forked from cc-… · first-turn cache HIT|MISS: read X KT of the
base's Y KT (Z%), wrote W KT`: HIT when the fork's first completed call read at least 95% of the base's last
occupancy (input + cache read + cache write of the base's last completed call when it was forked), else MISS
(cold after an hour, or another model); `not measured yet` before that call. A host without `seat_fork` refuses
(exit 8) until Dan's guarded host restart.

**A Claude Manager forks itself (`oc_seat quick --fork-self`).** `quick --fork-self [--forklift]
[--doorbell none|launch|program] [--variant V] --title T`, one background call by a Claude Manager, forks that
Manager's whole conversation — this turn's reading included, no cut, a full prompt-cache hit — into a
top-level quick fork worker: an ordinary `cc-…` seat (role `worker`, the Manager's cwd, model and level
unless `--variant` changes the fork's launch effort, cache kept,
its stored system prompt and agent definitions) with no socket, whose job is the conversation. A quick
fork worker writes its result to a local session-Dock AP (`🍴 Result · ‹title›`, `ap_id: auto`) and names
that AP-ID first in its final message; the doorbell prints it for the Manager to read and report.
`wait`, `info`, `steer` and `oc.sh claude follow-up` apply unchanged. The
Manager is found from the script's own environment (`CC_CONTEXT_EXECUTION_ID`, on its own host), never
typed, and its `CC_CONTEXT_BINDING` proves the call to the host (`fork_self_binding`). Refused: a caller
that is not a Manager (a fork never forks), a call the Manager's own main chain does not hold (a Bash
`tool_use` holding `--fork-self` and exactly this `--title`; a `manager-quick` or native subagent shares
its environment but writes its own transcript), a call that already made a fork (`fork_call`), a
Manager past 55% of its window (`MANAGER_FORK_ROOM`: use a fresh quick worker instead), 12 live forks of
that Manager already (`MAX_LIVE_MANAGER_FORKS`, counted inside the seat start lock); then the quota
guard and seat cap. Title `Manager-Session-NNN ▸ ⚡ quick fork worker · T` (a forklift's
`Manager-Session-NNN ▸ 🚜 forklift · T`, label `forklift: true`); labels `kind: fork`, `quick: true`,
`forked_from`, `fork_source: manager`, `fork_point: call`, `manager_session`, `fork_call` (the creating
Bash `tool_use` id), `fork_mode`. Its first message says it is a fork doing one job for the Manager, that the
call above created it (shown interrupted, or stopped in its copy) and must never run again, that its
job is what was asked just before that call, and that the quick worker's `AGENTS.md` is its working
rules. A forklift is the Manager for its one job: quickly check both providers' quota (`oc.sh usage`),
network / modem trouble, Claude host and OpenCode health, running programs touching the same repository
or files, Project Maps, Residue and Board for conflicts or anything urgent, then route and launch when called for. These are
reads in its background time, not a new scripted gate: real blockers mean no launch and an immediate
final message; minor issues are mentioned there. It writes only its own program records and one Board-row
insert, edits no other existing AP and never Residue. Useful new local APs in the session Dock are allowed,
slightly discouraged but encouraged when of clear value, each opening with `Created by forklift "‹title›"
(cc-…).` and all listed in its final message. Both fork kinds read PARALLEL PROGRAMS [ap:8TRTVQ]
before launching or editing code/shared documentation, running `oc.sh view --running --records`,
`oc.sh view --recent 30 --records` and the clause's git overview fresh, not reusing conversation copies.
The opt-in records line gives SkyView, current Briefs, StageConfiguration, quick job and seal from a
bounded cached root-note read; doubtful routing/conflict calls for reading the related purpose/Brief
and job/config as needed. The throwaway fork is meant to spend that context.
Small independent overlaps usually proceed; small requests prefer piggybacking into an already-launched,
unmerged small quick job in the same repository/area, with one writer for a sentence or passage.
The narrow steering exception is one recorded relay of this request to a running program's Conductor
that already owns its subject, instead of launching, and a few minutes' wait for its answer. The clause's
ordinary handover gives that Conductor ownership of a declined request's fresh quick launch, even late;
its separate piggyback offer leaves decline/timeout fallback with the fork, not both. No other program
management, HALT answer or restart is granted. Residue, curation, other steering and the rest
of the Board remain the primary Manager's. The final message has a ROUTE line and one-sentence comparison
after its first line; a forklift's STEERED, WAITING or ASK DAN exit-4 ring is normal, relayed by the
Manager in one line without its own reconnaissance. On "forklift X", that Manager does zero preparation: one
background call titled for its part, a one-line "which title does what" for several, and back to Dan
in under 15 s; any check the forklift can do, the Manager does not.
A host without `manager_fork` refuses (exit 8). Only this Manager-fork start also waits for
native input readiness: a background copy's orphan-notice startup result must be followed by an idle
state push (an initialize readback saying idle is not that witness). Within the same `READY_BUDGET`,
one own-turn refusal explicitly confirmed undelivered may be completed once after idle; an accepted
or uncertain Send is never repeated. A definite unsent failure names the fork's own state and releases
its live-fork slot before returning exit 1. The failure-only, empty exact-id marker is beside this
host's socket, in `‹socket filename›.failed-fork-inputs/`; it holds no job/context, is not read by the
fork, and is ignored once any input is accepted. The host record and its one-fork-per-call claim stay
intact. This releases the count rather than closing a possibly still-running startup turn (the host
refuses that close); ordinary seats, base forks and follow-ups retain their existing behavior.

**Fresh seats (`oc_seat dispatch`).** Inside a program every worker is a top-level session started
by a script, never a subagent (Dan's ruling). `oc_seat dispatch --agent ‹cast agent› --socket
"[ap:S]" --program "[ap:PROG]" [--lead] [--variant V] [--target F] [--no-wait]` starts one program
worker, or with `--lead` a lead seat with no seat base, and sends it the one worker line (who it is
and its id, then *read the Primary Worker Clause, then the socket*). An `anthropic/*` cast agent
(`opus55*`, `opus5`) runs on Claude Code: the cast prompt below, role `worker`, cwd `$HOME`, labels
`kind: seat`, `fresh: true`, `seat_role: worker|lead`, `title`, `conductor_id`/`_n`, the quota guard and seat cap.
Every other agent runs on OpenCode: a new session for the installed cast agent with its model and
level, no directory (the server's home folder, so it reads `~/AGENTS.md` as a subagent worker did),
metadata `kind: seat` (OpenCode's `context_status` then gives it the worker's landing target), `fresh: true`,
`seat_role: worker|lead`. Quick agents, the Manager's and the conductor refuse;
`--program` is required. `wait`, `info`, `socket`, `resume` (and `cancel`/`resolve` for `cc-`) work
on what it starts; the socket guard counts it on both backends; `wait` makes no cache claim for it.
A fresh lead seat's helpers fork from an OpenCode helper base or start quick, `--parent-seat` its id.

**The Claude Conductor (`oc_launch`, callers unchanged).** `oc.sh launch "[ap:PROG]" --seeded|--resume
--model anthropic/claude-opus-5-5 --variant ‹level› --authority …` (and `oc.sh quick --model
anthropic/… --variant …`) seats the Conductor on Claude Code: the same reservation, title, fixed
ignition and one-active rule as OpenCode; before any write the level (required), the composed
prompt, the host and the quota guard are checked; then role `conductor` (prompt **replaces** Claude
Code's: `seat_prompt.compose_conductor_prompt` = preamble, the `conductor` agent body, `agents/AGENTS.md`
adapted, the native Conductor runtime, the context binding), cwd `$HOME`, immutable labels (`title`,
`kind: conductor`, `registry_n`, `program_core`, `model_ref`, …). The launch record is verified
(title, role, model, level, program) before the ignition is sent; Claude's echo acknowledges it. The
registry row is bound with `execution: {backend: claude, id: "cc-…"}` **in place of** the null
`opencode_session_id` line, with the dated comment naming model and level; every reader goes through
`scripts/oc_binding.py` (`row_binding`, `row_session_id`, `is_reservation`: a reservation has no
binding of either kind). `--self-resume` (`oc_self_resume.sh`, unchanged) reads model, level and
target back (comment, else the host record) and seats a new Claude Conductor under the next number
and a new id; a crash is the host's own same-id recovery, and the row stays bound. A busy, recovering,
background-waiting or ownership-unknown Claude Conductor, or one whose host does not answer, refuses
a second launch on either backend (`--force` overrides); title clashes are checked on the host's
labels too. The runtime tells the Conductor to dispatch every worker by script (`oc_seat.sh dispatch`
for ordinary workers and fresh lead seats, `quick`, OpenCode seat bases; no `subagent`), to wait with
background `oc_seat.sh wait … --max
3000` (Bash `run_in_background`, timeout one hour), confirm a waiter for every in-flight worker and end
its turn. `oc_seat.sh wait cc-…` on a Conductor says done only once its turn has ended, no
background wait remains and no wake-up turn is due (the host's `native_wake_pending`: a background
task that ends `completed`/`failed` while the session's own turn is closed makes Claude Code start a
turn for it, and the gap before that turn's `init` is not idle; the wake turn's `init` or `result`,
or a proved exit, clears it; an operator close is not held by it), and prints its last word from a background wake-up (the
host `state`'s `last_synthetic_result`, which belongs to no input) after the ignition's result. `oc_steer` reaches a
Claude target through the host (`oc_steer.claude_gate`): a steer reaches a busy Claude session at
its next tool step; one long-running command delays it until that command ends. A Conductor, worker,
lead seat or helper mid-turn is steered at once through the host `steer` (prints `queued`, then
`delivered` when Claude's echo arrives; still queued after `--deliver-wait`, default 120 s, it says
so and exits 0 with the receipt id, never resent); an idle Conductor, or one waiting in background,
gets an ordinary Send and Claude's echo confirms it; a collected one is resumed first; a
still-scheduled recovery is superseded. `--queue` instead watches in the script and sends when its
own turn ends (`--max`, default 3000 s; exit 2 = still mid-turn, nothing sent; a worker also needs
`--force`). Starting, closing, a settings readback or a recovery's `continue` in flight still
withhold every message (a native retry inside the open turn does not: the steer is queued through it); an idle Claude worker is refused unless `--force`; a host
started before the steer keeps the between-turns rule until its guarded restart. `oc_seal` counts every Claude session of the program on the host (its
program label, or started by one of its sessions): running, background waits, a scheduled recovery,
unknown ownership or a silent host block the seal. `oc_preflight` checks the host and the quota guard
when the Conductor (`--model`, else a Claude newest row) or a cast/seats row runs on Claude, requires
a Claude row's level, and refuses while a Conductor of either backend still runs. The boom (`oc_view`),
`oc_peek` and `oc_search` read Claude sessions as they read OpenCode ones (`scripts/oc_claude_view.py`): one
`list` gives every record as a session with its launch-label links and the host's live status (`busy`, `bg-wait`
for background tasks only, `starting`, `recovering`, `unknown`), and each session's native Claude Code transcript
(`native_transcript_path_expected`) gives its history in OpenCode's message shapes.

**Seat prompts** (`claude_headless/seat_prompt.py`, appended to Claude Code's own prompt): the quick
worker prompt is a short top-level preamble, the agent body, the quick-worker folder's `AGENTS.md`
and the fresh-seat prompt (`compose_cast_worker_prompt`) a short preamble, the cast agent body and
`agents/AGENTS.md`, each with its OpenCode-only passages replaced by exact anchors (a drifted anchor
skips that override with a loud `DOCTRINE DRIFT` warning and the seat's `prompt_drift` label, never a
refused start; `test_claude_doctrine_drift.py` fails on it), then the native runtime (the `cc-` id rule, helpers, tool names, Bash, background
waits, never `cmd &`; a fresh seat's names `fork --base … --parent-seat cc-…` and quick helpers) and
the context binding. The seat's own id is only in its first message, so seats of one agent share one
system prompt. **D6:** agent files marked `backend: claude-code` are never installed into OpenCode
(`opencode_server.claude_code_only`; an installed copy is removed) and the config check does not
expect them: every `anthropic/*` agent file carries it (the cast's `opus55`, `opus55-xhigh`,
`opus55-max`, `opus5`, `fable51`, `fable51-xhigh`, `fable51-max` and the quick `quick-opus55-*` and
`quick-fable51-*`; not the `-or` twins), so a stray `subagent(opus55)` in OpenCode fails ("agent not
found") instead of billing the Anthropic API; per-token Claude through OpenCode is the `-or` twin. The `conductor`
agent itself denies `subagent` (one permission line in `agents/agent/conductor.md`, removable), so an
OpenCode Conductor cannot start any subagent at all: every program worker starts by `oc_seat.sh`.

Exit codes: 0 done · 1 error · 2 still running · 3 follow-up at/past the context target · 4 needs
attention (escalation, blocked recovery, launch never ready, input never completed, halted for a usage limit) · 5 UNKNOWN
(ownership or host unreachable; never "done") · 6 Max quota high · 7 seat cap · 8 no usable host ·
9 a Manager's doorbell handed over. `oc_seat.sh wait` is one interface for both backends: an OpenCode
target gives 0 / 2 / 5 (the server cannot be read: never "still running") / 1 (no such session, or
deleted) the same way; 4 is Claude-only (OpenCode has no escalations or host recoveries), and a
socket doorbell's (below).

**One Manager holds a doorbell at a time** (`scripts/oc_doorbell.py`). `wait` tells its caller from
its process ancestry: the nearest ancestor is this Claude Code process (`CLAUDE_PID`, its host
execution in `CC_CONTEXT_EXECUTION_ID`, role `Manager` on the host record) or an OpenCode server
(`OPENCODE_SESSION_ID`, a Manager-agent session with no parent and no seat metadata). Only a Manager's
waiter is registered (`~/.local/state/opencode-companion/doorbells/‹target›.json`, under an flock:
the claim, live waiters by pid plus process start time, handovers). A Manager's wait closes every
earlier Manager conversation's live waiter on the same target: that waiter prints `🔕 doorbell on …
HANDED OVER to Manager-Session-NNN …` and exits 9 on its next poll lap (≤ 5 s), so the earlier
conversation wakes once and is told it no longer holds it; `--keep-others` leaves them. An earlier
Manager arming or re-arming under a newer Manager's claim is refused with the same line and exit 9
(`--force` arms anyway, never taking the newer claim). Earlier and newer are the conversations' own
start times (`first_launched_at`, `time.created`), not the `.oc_config.json` number, which names the
newest session; that number (or `--session NNN`) is only the label. Conductors, seats, workers,
helpers and terminals are never registered, never closed and never close a Manager's.

**A doorbell on a socket** (`scripts/oc_socket_wait.py`): `oc_seat.sh wait --socket "[ap:JOB]"
[--max N] [--keep-others] [--force]` waits on whatever job a socket names, so any session can ring
one even on an earlier Manager's own `manager-quick` subagent, which has no id. Same exit codes and
output shape as an id wait (1 = no such socket); the one-Manager registry is keyed by the socket.
The first case that applies: (1) sessions on the socket (`oc_seat.sh socket`): the id wait on the
newest one, a busy one first; (2) an OpenCode Manager's `manager-quick` child started since the
socket was created whose first prompt names the socket: the id wait on it; (3) otherwise (a Claude
Code `manager-quick` child) the socket note itself, **one `node.read` every 90 s**, nothing heavier.
Done (0) = the newest marker heading is a `## Result` or a PARTIAL (a `## Continuation` after it
means the work resumed) and the note is unchanged on the next read, then a done line, the Max quota
line, a rule and the section's last 1500 characters. Liveness comes from the host's live Manager
records: a background `local_agent` task started since the socket was created (Bash waiters are
`local_bash` and ignored), unsettled background tasks, or the Manager's own turn open. No Result and
none of those on two reads 90 s apart (and still no session on the socket; a socket under 10
minutes old is still starting) = 4 needs attention. That signal cannot tell one subagent from
another, so it only ever says "needs attention", never "done". A socket or host that cannot be
read (or an OpenCode server that cannot be, while an OpenCode child is possible) = 5 at `--max`.

`seat_busy` counts a scheduled recovery and unknown ownership as busy. The observation budgets in
`start`/`follow-up` (ready 3600 s, echo 120 s) bound only how long the script watches; nothing is
killed or resent. **Activation:** the host fields above (`labels`, `last_input_uuid`,
`seat_interface`, D2 health, `seat_fork`, `manager_fork`, `MANAGER_REQUEST_ENV` and the fork fields) need Dan's
guarded host restart; the role-policy JSON applies to the next fresh launch of each seat role.

## Verification and activation

### Durable conversation history — HALT for the Manager

These are operator instructions, **not actions performed by this program**. No host, pane or
OpenCode restart, live-store write or live comparison was performed. Keep dictation saved and
choose a settled moment. Commands below run in WSL from
`/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion`; `<Manager UUID>` is the exact Manager
conversation to resume. Use the state root reported by health (normally
`$HOME/.local/state/opencode-companion/claude-headless`).

1. **Guarded host restart.** Run
   `bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_restart.sh --close-idle --resume <Manager UUID>`
   (or queue `oc.sh claude_restart`). Respect a refusal: never force a host replacement or kill a
    session. First activation seeds missing stream ends before serving; later starts use primary-key
    stream lookups and indexed per-conversation event heads, with one reconstruction transaction per
    record. On an isolated **1,070-record / 18,788,580-event** backup (14.70 GB, private empty run
    folders, empty-sid junk retained), process start to socket bound was **16.57 s first / 2.30 s
    second**, versus **18.72 s / 21.10 s** before U4.1. Standalone requeue took **90 ms** (five
    samples, maximum 90.2 ms). This measures host startup, not the entire guarded account-switch,
    native resume or history loading; the first fill and a later older-writer invalidation can still
    pay the old per-stream scan cost. No earlier bind, second writer/connection, TTL or startup
    event-index build is introduced.
2. **Wait for backfill; allow its disk growth.** Read health with
   `printf '{}' | bash scripts/claude_host.sh call health`. Poll `rows_backfill.status` until `complete`, or until
   every set still not complete is named in `rows_backfill.unreadable`; report those execution IDs.
   The U4 in-process copy walk took **244.546 s (about 4 min 5 s)**; the largest session
   (56,855 events, 5,083 rows across scopes) took **11.621 s**, including interrupt/reopen/resume.
   All 106 sets completed: **24,243 rows / 980,159 row_parts, zero unreadable**.
   Checkpointed DB bytes were **550,690,816 before migration → 622,796,800 after migration →
   1,048,047,616 after backfill**: **+425,250,816 bytes (425.3 MB)** for backfill alone,
   **+497,356,800 bytes** including migration. Allow free room for that growth plus SQLite WAL and
   continued event writes; no pruning or compaction is implied. Compared with U2's smaller copy
   (98 sessions / 1,398,046 events), its first open was 1.606 s, rows seed 0.098 s, backfill
   195.850 s, largest 10.843 s, backfill growth 378,707,968 bytes to 934,719,488, second open 0.114 s.
   The pane continues its full replay fallback for any session still building: no flag day.
3. **Read-only live check — before restarting the pane.** Run
   `python3 -B scripts/claude_rows_compare.py --live-read-only --raw-root "$HOME/.local/state/opencode-companion/claude-headless" --sessions all --out-dir /tmp/opencode/dch-activation-$(date +%Y%m%d-%H%M%S)`
   (substitute health's state root if different). This opens exactly one `mode=ro` connection,
   never constructs/migrates `Store`, and holds one `BEGIN … ROLLBACK` around both dumps and their
   comparison. It reports session/scope/row counts, mismatch paths (not values), hashes and a
   private `snapshot-report.json`. Dumps contain private previews: keep them under `/tmp/opencode/`.
   The final U4 **copy-only** check took **274 s**, compared **106 sessions / 285 scopes / 24,243
   rows**, and reported **zero mismatches and zero exclusions**; its DB sha256 was unchanged.
   Exclusions are named with reasons from the same snapshot's row-set status/watermark, read-only
   indexed stdout extents, or an unreadable row/body render; both dumps omit the same named session.
   Reconcile every exclusion with health's unreadable list and report it. Exit 3 fails exclusions
   health cannot name: replay/rows render failures, unreadable row state, rows behind or absent
   (an already-failed comparison keeps its nonzero exit). An unexpectedly building
   set means wait, not an equality claim. Require exit 0 and zero mismatches; a check with no
   comparable sessions fails. On any mismatch or failed check, **do not restart the pane**: use step 6
   and report to the conductor. This live command is for the Manager at activation, not the implementer.
4. **Only after a clean check, restart the pane and reload.** Run
   `bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/transcription-pane/start.sh restart`,
   then reload the browser tab to pick up U3/U4's `sessions.js`. The quiet gate waits; do not bypass it.
   Existing tabs resync by position, but reload is needed for the new static files.
5. **Cold-open check.** Open the largest Manager (`50a5f467-19b1-4971-9b0a-afa8ac8819e4` on this
   copy) cold. Its first `/api/claude/events` response must say `rowSource: 'host'` and arrive within
   seconds; scrolling up and ⤒ First must still reach the beginning. Live latency and this final
   acceptance remain **unverified until the Manager performs them**.
6. **Failure branch: guarded rollback to the pre-rows host.** Do not restart the pane on a mismatch.
   Use the known pre-rows host commit **`ad938be`**, not U4's `c41cee2` base (which already has rows).
   Without changing the working checkout, prepare its code with
   `mkdir -p /tmp/opencode/dch-pre-rows-ad938be && git archive ad938be | tar -x -C /tmp/opencode/dch-pre-rows-ad938be`,
   then run
   `bash /tmp/opencode/dch-pre-rows-ad938be/scripts/claude_restart.sh --close-idle --resume <Manager UUID>`
   at a settled moment; honor its guard and keep that exported tree while its host runs. The older
   host ignores the additive row tables and health lacks `rows`, so every pane build automatically
   uses replay. Report the mismatch/check failure and rollback outcome; the conductor decides next.
   A later guarded re-upgrade from the current repository re-queues complete sets left behind their
   event logs, without changing their epoch/positions. Never delete the row tables or hand-edit the
   live store. Unit 2's existing rollback safeguards still apply.

**The program now HALTs for activation.** The measured copy proves equality, not that these live
operator steps have happened. The native reader and its warning remain unchanged; the host fallback
has no per-conversation event or memory refusal and requires no manual indexing action.

Offline (no inference):

```sh
PYTHONDONTWRITEBYTECODE=1 /mnt/e/__daniel347x/__Dan_Root/__repos/workflowy-nexus-mcp-tools/.venv-wsl/bin/python -m pytest scripts/test_claude_headless_host.py scripts/test_claude_headless_launch.py -q -p no:cacheprovider
```

Owned U03 native smoke on CLI2.1.299: three short Haiku/low turns, two-turn recall, exact echo,
Write approval/sentinel, genuine model-facing Workflowy ping, Max account and no overage. A
no-user-input same-UUID resume/read-only native get_settings smoke verified Haiku/low and effective
compaction/memory/fast off, watchdog on and absent positive capacity-wait cap. Public controls remain
U04. Both native generations exited0 with owners gone; no production service was restarted.

Workflowy ping does **not** activate/prove U02's 500K-character catalog metadata. Dan's separate idle
daemon/catalog deployment and model-facing large-read gate remain pending per [ap:51LMDW] U02.
U06's Manager UI is prepared; production pane/host activation and Dan's final live acceptance remain
explicit gates in `docs/claude-headless-manager.md`, not a claim made by host health.
