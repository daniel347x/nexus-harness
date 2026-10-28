# Headless native Manager — launch, pane Send and operation

This is the pane-independent Manager seat's operator surface: a deterministic **body-only Manager
prompt** for the persistent Claude host, and a **thin launcher** that asks an already-running host to
launch or resume one native Manager session. It composes text and submits one exact host request; it
runs no inference, starts no process of its own, sends no message and installs nothing global. The
pane now provides live viewing and registered Manager Send/controls. The host provides correlated `interrupt`, `set_model`,
`apply_flag_settings`/`apply_effort` and `get_settings` with native applied readback; see
[host contracts](claude-headless-host.md). Launch alone never starts a model turn.

## The composed prompt

Source: `agents/agent/manager.md` — the body below its YAML frontmatter is the whole Manager system
prompt. `scripts/claude_headless/manager.py::compose_manager_prompt` produces the native version:

* **Body only.** The frontmatter (`description`, `mode`, `model`, `permissions`) is removed, so no
  OpenCode agent metadata reaches a native prompt.
* **Native mechanics.** Five exact doctrine passages that described OpenCode-only mechanics are
  replaced (each anchor must match exactly once, or composition refuses): the native Workflowy tool
  names and native connection reporting; `context_status` bound explicitly to native
  `mcp__context__context_status` with no arguments; OpenCode's oversized-output stub and `read` limits
  (Claude Code's own Read limit and saved-result handling instead); and OpenCode's `grep`/`glob`
  tool descriptions (Claude Code's own Grep and Glob).
* **Native runtime section.** An adapted closing section derived from the generic WSL manager
  guidance, with its terminal-only directions removed: no `/context`, no Remote Control, no pane
  history page. It keeps the OpenCode `oc.sh` dispatch bridge (conductor/worker dispatch — the
   `fresh` / `scouted` routes from this Manager; OpenCode `fork` still needs an OpenCode source) and states explicitly that a native Claude
  session UUID (`cc-…`) or native transcript id never goes through OpenCode's fuzzy `ses_`
  resolvers, and that OpenCode never drives, resumes or forks the native conversation. The one
  exception takes no id: the Manager's own `oc.sh seat quick --fork-self` (below) finds this
  conversation from its own environment; `--fork-from` stays OpenCode-only.
* **Context overlay.** `claude_headless.context.RUNTIME` follows the runtime section, preserving
  the no-argument role-tool duty with the explicit native name, native provenance and unknown/stale rule.
* **Everything else is the source doctrine, unchanged** — the Manager role, the Workflowy doctrine,
  the Toolkit and initiative, the guardrails — verified by tests that recompose the source body with
  only the declared overrides and by a doctrine-line survival check.

The composer returns the text plus its evidence: `source_path`, `source_sha256` (the doctrine file's
bytes), `prompt_sha256` (the composed text's bytes), `bytes`, and the applied override names.
Composition is deterministic. Doctrine drift (an override anchor missing or repeated) never refuses a
launch: that one override is skipped, the passage keeps its OpenCode wording, and a loud `DOCTRINE DRIFT`
warning goes to stderr and the launch record (`drifted`, `drift`; the `prompt_drift` label);
`scripts/test_claude_doctrine_drift.py` fails on it, so whoever edits the doctrine sees it at edit time.
Missing frontmatter still refuses (there is nothing to compose), as does any warning under `--strict`.

After those replacements the composer also checks a short residual watchlist: `OpenCode's own`,
`OpenCode gotcha`, the `subagent` tool, `manager-quick`, and `--fork-from`. New unsupported mechanics
can appear outside the five exact anchors. Results include `warnings`; prepare/launch/restart print
them plainly. The intentional closing native runtime is excluded, so today's composition is clean.
`compose_manager_prompt(strict=True)` or CLI `prepare --strict`, `launch --strict`,
`claude_restart.sh --strict` turns residual warnings into a refusal. This is a review watchlist,
not proof that arbitrary future OpenCode-only prose can always be recognized.

## Repository-owned Claude Code customizations (U06c)

**This repository is the whole launch configuration.** `--setting-sources ""` excludes user,
project and local settings; strict MCP loads only our generated configuration. No script edits
`~/.claude` settings, memory, skills or login. Claude Code still owns its normal native transcripts
there, and administrative/account policy remains Claude's. Never use `--bare` or an API-key fallback.

Fresh launches read the settings and role policy below and compose today's prompt/MCP snapshots.
The host imports Python when it starts: changing host-side modules requires an **idle,
guarded host restart**. Repository JSON changes need only a fresh launch. Seat prompts
(`seat_prompt.py`) are composed by each short-lived CLI invocation, so their tool sentences also
apply to fresh seats without a host restart. Resume deliberately keeps
the stored prompt/settings/MCP **and original role policy**, with only the Workflowy URL token re-resolved
at resume time and its change journaled; old generations without a role policy
keep append/default tools. Native system-prompt snapshots also persist on resume: **Session 181
must be fresh**, not a resume of the old Manager. No manual setup or home-directory copy is needed.

| Customization | Why / repository authority | Switch back for a future fresh launch |
|---|---|---|
| Manager `system_prompt_mode:"replace"` → `--system-prompt-file` | `config/claude-headless-roles.json`, read by `launch.role_policy/build_launch`. The composed Manager is the persona, not an appendix after Claude's coding/automatic-compaction instructions. Billing tag + SDK identity remain. | Set **Manager** mode to `"append"`; no other role changes. |
| Seat roles: `conductor` replace; `worker`/`helper`/`base` append; all carry `[Bash,Read,Edit,Write,Grep,Glob,WebFetch,WebSearch,TaskStop]` and `disable_skills:true` | Same role file. Explicit lists make settled seats collectable (a `"default"` tool set is never collected); no Agent (no native grandchildren), no Monitor. Editing and web tools are available to every role without changing Conductor doctrine. Started through `scripts/oc_claude.py`. | Edit a role's entry for future fresh launches; resume retains its original list. `"default"` makes that role's processes ineligible for automatic collection. |
| Manager `tools:[Bash,Read,Edit,Write,Grep,Glob,WebFetch,WebSearch,TaskStop,Agent,SendMessage]` → `--tools` | Same role file. Explicitly restores WSL Grep/Glob, retains background Bash/TaskStop + all strict MCP tools and the native Manager quick agent; SendMessage steers the Manager's own native subagents (a running one at its next tool round, a finished one is resumed); `manager-quick` children list it in `disallowedTools`, and ListAgents stays out. `crossSessionInbound` is left at its default (Dan's ruling). Excludes AskUserQuestion, plan/cron/wakeup/worktree/Workflow/NotebookEdit/Skill. This controls availability, **not** permission preapproval; no `--allowedTools`. | Edit that list; `"default"` restores the native set but WSL can omit Grep/Glob. Do not substitute an allow-list. |
| Manager `disable_skills:true` → `--disable-slash-commands` | Same role file. Drops Skill and the skills catalog cleanly; does not disable background completion notifications or file expansion globally. | Set false and add Skill to the tools list if intentionally restoring calls. |
| `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH="16384"` | `config/claude-headless-settings.json.env`; all eight Workflowy descriptions are sent whole instead of four being cut at 4096. | Remove to restore native default truncation (not recommended). |
| `ENABLE_TOOL_SEARCH="false"` | Same profile env; eager first-request schemas on Max, so the Manager's opening Workflowy availability check is meaningful. | Remove/set true only with a ToolSearch-aware runtime and a tool-set review; current opening check assumes eager tools. |
| `attribution:{commit:"",pr:""}`, `includeGitInstructions:false` | Same profile. No generated coauthor/model footer or conversation-start git-status reminder; honors the golden rule, does **not** turn off or configure cryptographic Git signing. | Remove/set native defaults to restore reminders/attribution intentionally. |
| Body-only doctrine + exact native replacements + WSL runtime + context overlay | `agents/agent/manager.md` read-only source; `scripts/claude_headless/manager.py`, `context.py`. Manager-controlled quick work uses native Agent with `manager-quick`; a job that rests on this conversation forks it with `oc.sh seat quick --fork-self` (below); Conductor-owned work that must outlive the conversation uses `oc.sh quick`. `--fork-from` names an OpenCode source only (no native-UUID `--fork-from`). Compaction stays off; handoff/baton follow doctrine under Dan's context watch. | Edit native replacements/runtime, never the installed copy; exact anchor drift refuses. Do not restore missing-tool or automatic-summary promises. |
| Launch-local `manager-quick` definition → `--agents` file | `scripts/claude_headless/quick_agent.py` reads the ONE `quick-worker/AGENTS.md` and prepends native mechanics. `build_launch` regenerates private `agents.json` on every Manager launch/resume (a fork of a Manager carries the base's stored `agents.json` byte-identically); source and generated hashes recorded. `model:inherit`, **tools omitted** to inherit shell/file and Workflowy MCP tools; `disallowedTools:[Agent]` prevents native grandchildren. No project/global agent installation. | Change the one doctrine or native preamble, then launch a fresh process; never edit a per-run snapshot. Remove Agent from role tools to disable new calls. |
| `CLAUDE_CODE_FORK_SUBAGENT="0"` (`MANAGER_REQUEST_ENV`) | `scripts/claude_headless/launch.py`; set on every Manager generation (fresh or resumed) and recorded as `request_env`. Keeps Claude Code's own native fork subagent (Agent's `subagent_type:"fork"`, on by default in 2.1.302) off: the Manager's quick work goes through its own seat fork (`oc.sh seat quick --fork-self`, below) under the quota guard and seat cap. A fork of a Manager and its resumes re-apply the base's recorded value byte-identically, so its first call still reads the base from the prompt cache. | Change `MANAGER_REQUEST_ENV` in `launch.py` and restart the host; the value changes the Agent tool description on every request. |
| Residual OpenCode watchlist, optional `--strict`; composition preflight before every restart | `manager.py`, Manager launcher, `restart.py`. Exact anchors catch drift (a warning; the drift test fails); residual warnings catch new unsupported mechanics outside them. Restart checks/publishes before any host contact; only `--launch-manager` (or `--strict`) refuses on a composition that fails outright, preserving the exact composer error; a plain restart or `--resume` only warns. | Edit the short watchlist after review; omit strict to warn only. |
| Fixed readable latest prompt | `manager.publish_manager_prompt`, called by prepare, launch/resume and Host.launch for Managers. Atomic owner-only UTF-8 preview at `~/.local/state/opencode-companion/claude-headless/manager-prompt-latest.md`. Isolated host overrides write under their own state root. | Preview is diagnostic, not configuration: never edit it to change the persona. Source files remain authority. |
| `--thinking-display summarized` | Fixed `build_launch` argv, fresh/resume; native live summaries, display-only, not Refine/context. | Remove pair in code and restart host; old omitted summaries cannot be recovered. |
| `--setting-sources ""`, strict `--mcp-config`, Workflowy HTTP + bound context stdio, timeout3600000 | `launch.py`; dynamic WSL gateway/operator endpoint and environment-only broker token, no secret in argv/files. Context binding rotates per generation. | Code change + restart; widening settings/MCP sources is a new policy decision, not home-file setup. |
| Pinned Linux CLI; stream-json/verbose/replay/partial; host/manual stdio permissions; explicit model/effort/UUID | `launch.py`, host/controls. Logged allow-by-default original input, no persisted permissions; verified applied settings gate Send; exact same-UUID ownership on resume. | Review code + restart; never replace with bypass/dontAsk/bare or silent model/provider fallback. |
| Independent Max-only environment sanitization; removes the launching Claude's parent-session identity/messaging context, API/provider/helper/simple/bare/alternate-config/turn/budget overrides and `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` | `launch.sanitize_environment`; each child owns its runtime context, subscription auth remains native, background waiting stays enabled. No new host execution deadline. | Review sanitization code + restart; API fallback and background-disable are not supported rollbacks. |
| `autoCompactEnabled:false`, `idleCompaction:false`, `DISABLE_AUTO_COMPACT:"1"`; `autoMemoryEnabled:false`, `fastMode:false` | Settings profile + `_load_profile` invariants. No automatic/idle summary, hidden memory or fast-mode billing. | Enabling any invariant requires **both** code validation and profile/runtime review + restart; not a JSON-only supported toggle. |
| `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS:"1"`, `DISABLE_CRON:"1"`; collector execution envelope | Maintained profile and `launch.py`: native state pushes include native-only completion/peer turns; native scheduler is disabled independently of offered tools. Monitor/hooks/auxiliary inference are not enabled. `launch.collector_envelope` and native effective settings/readback gate collection. | Enabling any excluded feature makes that process ineligible for automatic collection; review the native settlement contract first. |
| `bashOutputMaxChars:128000`, `MAX_MCP_OUTPUT_TOKENS:"200000"`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS:"200000"`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS:"128000"` | Settings profile, larger useful deliveries. Native 500K-character/transport/model ceilings remain; NEXUS's catalog metadata is a separate server prerequisite. | Remove/change values in profile for future fresh launches; use windows/artifacts, never pretend limits disappear. |
| `API_TIMEOUT_MS`, `CLAUDE_STREAM_IDLE_TIMEOUT_MS`, `MCP_TIMEOUT`, `MCP_CONNECT_TIMEOUT_MS`, `CLAUDE_CODE_MCP_STARTUP_WAIT_MS`, `MCP_TOOL_TIMEOUT`, `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`, `BASH_DEFAULT_TIMEOUT_MS`, `BASH_MAX_TIMEOUT_MS`, `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`, `CLAUDE_ASYNC_AGENT_STALL_TIMEOUT_MS`, `CLAUDE_CODE_WEBFETCH_DEADLINE_MS`, `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS`, `CLAUDE_CODE_USER_DIALOG_TIMEOUT_MS` all `"3600000"`; `CLAUDE_CODE_GLOB_TIMEOUT_SECONDS:"3600"` | Profile env hour clocks. Native Bash background commands **do have their own timeout** (default1h/max2h on2.1.299); bounded `seat wait --max 540` exits2 and can be waited again. Host silence never kills/interrupts/resends. | Remove/change individual profile values for fresh launches; do not confuse native limits with host execution policy. |
| `API_FORCE_IDLE_TIMEOUT:"0"`, `CLAUDE_ENABLE_BYTE_WATCHDOG:"0"`, `CLAUDE_ENABLE_STREAM_WATCHDOG:"1"`, `CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS:"0"` | Profile: separate body-idle/byte clocks off, parsed-event clock on, MCP auto-background off. **Bash** background stays on. | Remove/change individual profile values, retaining truthful deadline/output documentation. |
| `CLAUDE_CODE_RETRY_WATCHDOG:"1"`, `CLAUDE_CODE_MAX_RETRIES:"15"`; positive `CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS` absent/stripped | Profile + validation/sanitization; unbounded native capacity waiting, not infinite retry of auth/malformed/unknown errors. A usage-limit lockout (a rejected five-hour/weekly window) is never waited out: the host ends that turn and halts the session (host doc, *Usage-limit lockout*); a five-hour halt also arms the one Manager recovery check (*Five-hour recovery check*), while weekly windows arm nothing. | Positive wait cap or watchdog off requires explicit ruling and validation/sanitization change, not an inherited variable. |
| Native context qualification guard, shared role targets; silence threshold600s; one incident feed and one terminal recovery lineage | `context.py` + bound `claude_context_mcp.py`, `supervision.py`, `controls.py`; targets from `plugins/context-status.ts`. Exact tool-time occupancy UNKNOWN on2.1.299; every role reads the previous completed call (Dan's ruling D2, OpenCode parity), so seats are enabled. Silence is observation-only. | Silence threshold is operator configurable; changing context/financial/recovery/permission doctrine is a separate decision, not a cosmetic toggle. |

**Restart / Session 181:** save dictation and inspect all current receipts, controls, background
tasks, recoveries and escalations. Stop submitting Send/controls/launches during this operator
deployment; it is not a concurrent-use maintenance lock. Then, at Dan's explicit settled boundary:

```sh
C=/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion
# Refuses unsettled sessions. --close-idle explicitly authorizes EOF for idle owners only (a
# finished session held only by an unknown task level is settled by the host first; see below).
bash "$C/scripts/claude_restart.sh" --close-idle --launch-manager \
  --model claude-opus-5-5 --effort high
# Or restart without launching, then choose the model/effort explicitly:
# bash "$C/scripts/claude_restart.sh" --close-idle
# bash "$C/scripts/claude_manager_headless.sh" launch --model claude-sonnet-5-5 --effort low
# Conversation preservation ONLY (keeps old prompt/config, not Session 181 parity):
# bash "$C/scripts/claude_restart.sh" --close-idle --resume EXACT-STOPPED-UUID
```

**From inside a Manager's own turn** (the guarded restart refuses while that turn runs): queue it as the turn's
last action with `bash "$C/scripts/oc.sh" claude_restart --close-idle [--resume UUID | --launch-manager …]`.
A detached job prints **QUEUED** with its operation ID before returning. It retries every 15 s
only for typed pre-stop own-turn/settlement/transition-lock holds, within its pre-stop queue
budget; permanent store/login/config failures end promptly. Unknown output or any possible
post-stop effect never authorizes another normal restart. Viable startup stays under observation
beyond that budget. The topmost Windows popup reports actual service, Manager and notice facts,
not an assumption from `--resume`. Log: `/tmp/opencode/oc_claude_restart.log`; the per-ID durable
receipt survives log replacement/lost popups.

Choose the returned fresh live Manager row (it targets Send/Refine automatically), verify ready/applied model+effort,
then send the normal Session181 ignition. The Manager scripts' `.oc_config.json` stamp is an
OpenCode job-label setting, not a native session identity: explicitly set it to181 through the
normal Manager Session workflow when starting that numbered session (the file currently contains
`{"manager_session":"180"}`; after the181 Dock exists, set `{"manager_session":"181"}` explicitly,
or the next un-stamped steer/seal auto-syncs to the newest Dock). `oc.sh session list` manages
OpenCode Manager sub-session pages; it does not number or own native Claude sessions. Never pass the
Claude UUID to `oc.sh` session resolvers. Pane activation is independent (Dan restarts it at idle);
no OpenCode/Workflowy/production restart was performed by this unit.

After the Manager-Session-181 Dock exists, this explicit operator command preserves other config
keys and stamps future OpenCode jobs (the unit itself leaves the current180 file unchanged):

```sh
python3 - "$C/scripts/.oc_config.json" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
config = json.loads(p.read_text(encoding="utf-8"))
config["manager_session"] = "181"
p.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY
```

Verification2.1.299: isolated dummy-key loopback request (never bare/subscription capture) has
billing74B + SDK identity62B + exact composed Manager75271B, **18 tools** (nine built-ins + context
+ eight Workflowy), full descriptions, no deferred placeholder, no Skill/catalog, no attribution
or git-status reminders. Before:180943B/35 tools; after:137366B/18 tools. Real owned full-prompt
Sonnet5.5/low Max smoke performed Workflowy ping + Grep + background Bash, then received and
answered its completion notification. Fresh launches pick up this configuration; resumes do not
retrofit it. Private evidence: `__repos/oc-scratch/opencode-companion/legacy/headless-prompt-capture/u06c-parity/`, `__repos/oc-scratch/opencode-companion/legacy/hc-u06c/`.

Scratch goes in `__repos/oc-scratch/opencode-companion/`, never inside a repository. Delete your own scratch when the job is done,
keeping only receipts a record cites; browser runners delete their own Chrome profiles.

## Commands

### Personal/work Max accounts: enroll once, switch deliberately

Account selection is explicitly commanded by **Dan or the Manager under his standing ruling
[ap:4Y02WI]**, never mechanical quota-triggered failover in the host. The on-demand weekly watch
procedure is [ap:XLM018]; the Manager starts its timer, judges when to halt or let work finish,
switches only once Claude is quiet, and resumes the programs. Each login stays in its
native folder; no token copies or custom OAuth client. Identify today's default with
`~/.local/bin/claude auth status --text`, then enroll the *other* account in a WSL terminal:

```sh
bash scripts/oc.sh claude_accounts enroll work --email '<work email>'
# If work already occupies the default, enroll personal instead.
bash scripts/oc.sh claude_accounts list --json
bash scripts/oc.sh claude_accounts status --json
```

This runs native `auth login --claudeai` once with the extra `CLAUDE_CONFIG_DIR`, verifies the
approved email, and maps the other label to the observed default email. The browser must approve
the intended account; `--email` merely pre-fills. Enrollment never logs out the default, changes
the selector or restarts anything. Native authorization may eventually require renewal.

```sh
# Operator terminal, at a settled boundary:
bash scripts/claude_restart.sh --account work --close-idle --resume EXACT-MANAGER-UUID
# From inside a Manager's turn: LAST action, then end the turn:
bash scripts/oc.sh claude_restart --account personal --close-idle --resume EXACT-MANAGER-UUID
```

Desktop and phone account controls queue the same guarded operation, without a confirmation
dialog. They show exact blocking owners/tasks/reasons, switching, starting/recovering, then active
only for the requested destination with verified ready service. Restored means the switch failed
but old service is ready; recovery-required carries the same-ID command. Selection's ring is
independent. Unknown stays query-only/reconcilable; already selected is a zero-spawn receipt with
service unchecked. Manager readiness and notice delivery have their own states.
Observation-only timers and session/socket doorbells are durably listed and ended automatically; their
deadlines do not pin the old login. Genuine commands/tests/exports/edits/Agent work and uncertain
input/task/ownership evidence remain named holds, not disposable “timed work”.
Only fixed labels reach the server, never paths, commands or credentials. The durable operation
is queryable after a lost reply/page reload; do not create another operation to recover a reply.

The registry is `~/.config/opencode-companion/claude-accounts.json` (email-to-label mapping and
enrolled directory paths, no secrets); the selector is
`~/.local/state/opencode-companion/claude-account.json` (directory, account identity, epoch).
Labels resolve against each folder's **actual native auth status**, so a later manual default
login change is not mistaken for the old label. A busy/refused restart never changes selection.
The transition serializes against new launches and commits a complete before-stop recipe in the
existing recovery record. Selection changes only after all native owners and the old host are
proved gone. A caught post-stop preparation/write failure reads the actual persisted selector:
verified old selection automatically restores old service, without retrying the failed target
plan or changing the epoch; this remains a **failed switch**, not Work active. An operation-stamped
committed target continues there, even if the write reply was lost; it is never implicitly rolled
back. Same-UUID Manager resume preserves stored choices and sends no original turn. Other
conversations reconnect lazily.

Every restart (ordinary or account switch) prints an operation ID and one `claude-restart/1`
receipt. After an interrupted/stopped transition, use that ID, not a new switch request:

```sh
bash scripts/claude_restart.sh --operations
bash scripts/claude_restart.sh --status OPERATION-ID
bash scripts/claude_restart.sh --recover OPERATION-ID
```

Status/list are read-only and work with an absent or unbound host. Recover uses the stored
account, namespace and Manager policy; no hand-composed library rescue or resupplied UUID/model
is needed. Same-ID receipt replay cannot repeat a completed switch. A live exact replacement
holding `host.lock` stays **starting**, even beyond the old 120-second wait; missing socket or
elapsed time never permits a competing start. `--timeout` bounds pre-stop settlement/exit
observation, not viable reconstruction. A concrete dead/unusable start retains its obstacle and
same-ID recovery command. Selection, usable service, initialized/writable Manager, notice
delivery and publication remain separate receipt fields. Exit 0 requires ready service and
ready direct follow-up (current-Manager notice routing may remain pending); exit 1 can mean old
service restored after a failed switch or ready service with failed direct follow-up. Exit 3
means no host signal, not no partial idle/observer effects; cancellation is exit 4, observer
interruption 130, usage 2. Query exit 0 only means the query worked.

Check the other account's room without selecting it: `oc.sh usage --account work --json` (or
`personal`). This resolves the actual enrolled login, reads its own five-hour/weekly/model-specific
quota and labels it; no ChatGPT/Codex read or renewal is made. Unknown room stays unknown.

For the Manager's own watch, keep the affected program/current-Conductor and in-flight/wait list in its
ordinary session record, then queue this as the **last action of the turn**. The current host's full
account-switch path lists and retires its proven observation-only timers/doorbells itself:

```sh
bash scripts/oc.sh claude_restart --account work --close-idle --resume EXACT-MANAGER-UUID --notify-manager
```

The current observer-aware host retains a master recovery index and one report per affected program,
including nested helpers and immediate parents. Workflowy publication and missing Manager/notice delivery
never hold switching. After verified service recovery, one durable notice is eligible for the current Manager;
it resolves current Conductors on either backend, reconciles real results and re-arms wanted waits rather
than independently resuming leaves. Partial stop/failure/reload/lost reply keeps the same receipts/pages.
Publication-uncertain capture is retained locally and reconciled by its stable AP-ID, never duplicated.
The exact host API and native no-wake boundary are in `claude-headless-host.md`.

New lifecycle requests retain one operation-keyed episode/message UUID before stop. Explicit
notify and observer-switch success retain their authorized notice; plain successful restart/resume
still sends no turn by default. A post-stop failure becomes eligible for its specific labelled
failure/recovery notice once usable service returns, including old-selector restoration or later
same-ID destination recovery. Pre-stop refusal/cancellation remains not due: it cannot infer that
the old account was exhausted. No Manager is invented when none is registered.

The current-Manager reconnect/readiness owner selects again before admission; it never races a
historical UUID resume on observer switches. Once Send might have been admitted, exact recipient,
generation, submitted bytes and UUID are retained and reconciled, never rewritten/retargeted/replayed.
Previously admitted legacy episodes keep their original exact receipts; no second legacy notice
is staged for a new lifecycle request. Labels distinguish `ACCOUNT SWITCH FAILED — SERVICE RESTORED`
from completion and `AUTOMATED RESTART RECOVERY`, each followed by “not a new message from Dan.”
The notice states requested/actual account and epoch, failed phase/error, exit/readiness proofs,
Manager state, partial stop receipts, recovery/status command and publication state; quota is
optional and explicitly unavailable when unreadable. Scheduled/pending is not delivered; ACK is
transport delivery, never program success. The Manager reconciles actual work through current
Conductors on either backend, re-arms only wanted obligations and respects unrelated human HALTs.
No leaf, original turn or uncertain mutation is automatically replayed. Five-hour reset+300s
episodes remain separate with their exact defer/cancel controls.

#### Login folder is not cwd

Home, quick-worker and manager-sessions still use their original working directories. Their
project instructions and transcript buckets follow cwd. Repository-owned prompt, explicit
settings (`--setting-sources ""`), strict MCP and launch-local `--agents` files stay independent
of account selection; sanitization still strips any *inherited* `CLAUDE_CONFIG_DIR`, then injects
the trusted selection into both auth preflight and child launch. Launch records stamp account
label/identity/epoch. Usage and launch admission exclude other-account readings.

`~/.claude` is the canonical shared store. The extra directory links **every existing
non-account child**: projects (including subagents, tool results and memory), file-history,
skills, plugins, settings.json, history.jsonl, paste-cache, session-env, sessions,
shell-snapshots, downloads and installation housekeeping; plans, todos, user agents/rules and
other non-account stores are linked when present. Missing canonical stores are not created by
enrollment. Links are checked again on switching to the extra account; an existing conflicting
history/config store is never hidden or overwritten. Native atomic replacement can replace a file
symlink; the next check reports a conflict rather than discarding user data (disposable cleanup/
updater stamps and the changelog cache are simply relinked). Directory links
(notably projects/file-history/plugins) keep native atomic writes inside the canonical store.

Backup artifacts (`*.bak`, `*.bak-*`, `*.backup.*`, editor `*~`) are not active stores: each
folder keeps its own untouched copies, even when their contents differ. The restart validates
the shared-store and preference plan read-only **before closing sessions or stopping the host**,
then applies it after the old host exits. A pre-existing store conflict therefore leaves the
working host available rather than stranding the Manager after shutdown.

Separate: `.credentials.json`, native `.claude.json`, account-state `backups/`, telemetry/statsig,
remote-settings and policy-limits caches. `cache/model-catalog` remains account-local;
`cache/changelog.md` is shared by link. Default deliberately keeps `CLAUDE_CONFIG_DIR` **unset**:
setting it to `~/.claude` would move account state from `~/.claude.json` to
`~/.claude/.claude.json`. The extra folder's `.claude.json` retains its own OAuth identity and
feature/model caches. At enrollment/switch it mirrors canonical per-project trust,
allowedTools, external-include approvals and MCP preferences, plus a small whitelist of global
UI/install preferences. This is default-to-extra preference synchronization, not a shared
account-state file. The host's explicit policy remains authoritative; account/organization
managed policy can legitimately differ, and sharing history does not merge provider accounts.

#### Real two-account acceptance (Manager + Dan, after merge/activation)

1. Enroll the other account and inspect `claude_accounts status --json`; check the two actual
   emails and active label. Restart the app at a quiet moment and reload desktop and phone.
   Run `python3 scripts/claude_account_acceptance.py --live --label EXTRA_LABEL` (the label held
   in the extra folder, not the default). This read-only check verifies real links/preferences
   and all three folders' launch parity; it does not replace the real native-turn checks below.
2. Record the current Manager UUID and a distinctive existing history sentence. At idle,
   use `claude_restart.sh --account B --close-idle --resume UUID`; verify selector/launch
   label+epoch, same UUID/cwd, same sentence visible, and Send a short new sentence. Verify its
   append is in the original `~/.claude/projects/<encoded cwd>/<UUID>.jsonl`.
3. For **each** cwd (`/home/daniel347x`, quick-worker and manager-sessions absolute paths),
   launch a Manager test conversation using `claude_manager_headless.sh launch --cwd DIR
   --model claude-sonnet-5-5 --effort low`. Record `launch.json`'s cwd and
   prompt/settings/MCP/agents SHA256 values under A; after B selection, resume those exact
   UUIDs and compare (unchanged Workflowy endpoint). Send a short marker under each account;
   confirm both appends in the **same inode/canonical file**, including access through
   `<extra>/projects/<encoded cwd>/<UUID>.jsonl`. Close these idle test conversations normally.
4. `oc.sh usage --json` and the launch guard must label B, not reuse A's high/old frames. The
   first real B response must succeed under native Max auth. Switch B→A with the same main
   Manager UUID; verify original and new history remain, and quota labels return to A.
5. While a harmless real turn is busy, run the **direct** guarded B switch: expect refusal,
   unchanged selector bytes/epoch and no host signal. Do not interrupt work to force it.
6. Exercise desktop and phone buttons (A→B→A), including reloading a page while waiting;
   it must recover the same operation, show active only after completion, and preserve the
   Manager UUID. No browser OAuth ceremony, confirmation, replayed message or automatic
   host-decided quota-triggered switch. Resume real programs only on their normal explicit paths.
7. For the Manager-owned watch, use the queued command with `--notify-manager` after stopping its
   timers/waiters. With no human Send, verify exactly one labelled completion turn in the same UUID,
   the new account/quota/closed list, then reconciled Conductor resumes under [ap:4Y02WI]. Failure
   delivery is covered by isolated tests; never break a live host deliberately to test it.

The scratch parity/native loopback acceptance is not a real-Max enrollment or UI activation
receipt. Real checks above remain operator work; build/test workers never run them.

### One weekly watch, Claude and ChatGPT

The on-demand Manager procedure [ap:XLM018] covers both providers under [ap:4Y02WI] (Claude)
and [ap:PMY39V] (ChatGPT). One background timer, judged cadence and remaining-room/burn check;
recommendations are not hard thresholds. A Claude switch uses the guarded restart/wake above.
A GPT switch is `opencode auth switch openai LABEL_OR_CREDENTIAL_ID`: no OpenCode restart,
normally no program halt. New turns resolve the selected account; an in-flight request keeps
its captured login. A genuine usage-limit request stuck in proxy backoff (30 minutes, up to
2.5 hours with Retry-After) needs a targeted stop/interrupt and same-piece reconciliation, not
a fleet restart. `oc.sh seat info ses_ID` shows backoff and the last API error; the procedure
names the exact interrupt/resume path. Healthy work on either provider is not stopped for the
other provider's switch.

ChatGPT quota reads need independent file-backed Codex logins, not copied OpenCode tokens.
Map labels in `~/.config/opencode-companion/chatgpt-accounts.json`:

```json
{
  "personal": {"codex_home": "/home/daniel347x/.codex-personal", "opencode_credential_id": "PERSONAL_ID"},
  "work": {"codex_home": "/home/daniel347x/.codex-work", "opencode_credential_id": "WORK_ID"}
}
```

Dan enrolls each home with `cli_auth_credentials_store = "file"` in its `config.toml`, then
`CODEX_HOME=HOME codex login`, approving the intended ChatGPT account. Both OpenCode accounts
are separately enrolled through `opencode auth login`; `opencode auth list --format json`
shows their saved IDs. The binding is operator-supplied, not a token-identity comparison.
`oc.sh usage --provider chatgpt --list-accounts --json` shows configured entries and actual
active OpenCode ID from the secret-free integration API. `--provider chatgpt --json` reads
that active binding; add `--account work` for the other quota. Existing `--account work` alone
still means Claude. Per-home renewal state and account/binding-specific caches never pool
credentials or stale readings. Missing/unmapped/unreachable active status stays UNKNOWN.
With no registry, plain usage retains the default Codex-only reading, not a verified OpenCode
match. Full enrollment/manual commands: [ap:D8NOYW], *ChatGPT accounts*. Real A→B→A acceptance
with new turns and active-ID/quota readback remains operator work, not a synthetic-test claim.

### New Manager from the pane (no shell needed)

The pane's **＋ New Manager** runs exactly `claude_manager_headless.sh launch --model M --effort E`
through the running host (`transcription-pane/claude_launch.py` calls this launcher's `run_launch`),
names the conversation (display only) and selects it for Send once ready. If the host is not
running it is started first with `claude_host.py start`. The existing Manager keeps running
untouched and stays in the list. This is the way out whenever a conversation cannot be reconnected.

### The Manager's own quick workers

Capture a socket containing the complete bounded task as usual, then use **Agent**:
`subagent_type:"manager-quick", prompt:"Your socket is [ap:JOB]; read it in full and follow it.",
description:"Short job description", run_in_background:true`. The background option is
`run_in_background`, **not** OpenCode's `background`. Omit `model` to inherit this Manager's
current model; override only deliberately. Thinking effort is chosen by agent type (the call
cannot set it): `manager-quick` uses the session default; `manager-quick-medium`, `-high`,
`-xhigh` and `-max` are the same generated worker with the agent definition's `effort` field
(pinned 2.1.299 schema: low|medium|high|xhigh|max or an integer; a level the model lacks is
silently downgraded). An isolated loopback probe confirmed the child's requests carry
`output_config.effort: max` while the parent's carry its own `low`
(`scripts/fixtures/claude-agent-session-state-2.1.299.json`). Never use `general-purpose` or
another agent type for real work. Native subagents cannot start subagents. Authorized helpers are
`oc.sh seat quick --helper …` top-level sessions, OpenCode (`quick-ds41-flash`) or Claude Code on Max
(`quick-opus55-‹level›`, a `cc-…` id collected with `oc.sh seat wait`); never supply the native identity
as `--parent-seat`. Work that must outlive the conversation goes through the scripts (`oc.sh quick`, or
`oc.sh seat quick --agent quick-opus55-‹level›` as a top-level seat).

**A job that rests on this conversation: fork it.** For work that must carry the Manager's whole
conversation so far — its reading this turn included — the Manager forks itself with one background call
(Bash `run_in_background: true`, `timeout: 3600000`):

```sh
bash "$C/scripts/oc.sh" seat quick --fork-self [--forklift] [--doorbell none|launch|program] [--variant V] --title "‹what›"
```

Nothing else goes in: no job text, no socket, no id. The fork is the conversation, so its job is what
was asked just before the call; its first message (`manager_fork_prompt`) says it is a fork doing one
job for the Manager: a quick fork worker, or the Manager for its one forklift job. The script finds the Manager from its own environment
(`CC_CONTEXT_EXECUTION_ID`; its `CC_CONTEXT_BINDING` proves the call to the host), and finds its own call
in the Manager's main-chain transcript by `--fork-self` and its exact `--title` (`manager_fork_call`), so
a `manager-quick` or native subagent, whose calls live in its own sidechain file, is refused. The fork
is an ordinary top-level `cc-…` worker holding the whole conversation (no cut, a full prompt-cache hit),
role worker in the Manager's cwd at its model and level (`--variant` changes only the fork's level,
cache kept), with its stored system prompt and agent definitions; it works by `quick-worker/AGENTS.md`.
A quick fork worker writes its result to a local session-Dock Anchor Point (`🍴 Result · ‹title›`,
`ap_id: auto`), names that AP-ID first in its final message, and the Manager reads it and reports it.
On "forklift X", the Manager does zero preparation: one background call with a title naming its part,
a one-line "which title does what" for several, and back to Dan in under 15 s. Any check the forklift
can do, the Manager does not. The forklift quickly checks both providers' quota (`oc.sh usage`),
network / modem trouble, Claude host and OpenCode health, running programs touching the same repository
or files, Project Maps, Residue and Board for conflicts or anything urgent, then routes and launches when called for. A real
blocker means no launch and an immediate final message; minor issues are mentioned, not a launch gate.
Its writes remain its own program's records and one Board-row insert; no other existing AP, no Residue.
New APs are allowed, slightly discouraged but encouraged when of clear value: local, in the session
Dock, each opening with `Created by forklift "‹title›" (cc-…).`, all listed in its final message.
Both fork kinds read PARALLEL PROGRAMS [ap:8TRTVQ] before launching or editing code/shared documentation:
fresh `oc.sh view --running --records`, `oc.sh view --recent 30 --records` and the git overview, never conversation copies.
The opt-in cached-root records line points to SkyView, current Briefs, StageConfiguration, quick job
and seal; doubtful routing/conflict calls for reading the related SkyView/Brief and job/config as
needed, spending the throwaway fork's context rather than guessing.
Small independent overlaps ordinarily proceed; small requests prefer STEER into an already-launched,
unmerged small quick job in the same repository/area, keeping one writer for a sentence or passage.
They may relay this request once and recorded to the Conductor of a running program that already owns
its subject, instead of launching, and wait a few minutes for its answer. The clause distinguishes an
ordinary handover (that Conductor owns any declined request's fresh quick launch, even late) from a
piggyback offer (the fork retains decline/timeout fallback, so the Conductor does not also launch).
That is the only steering exception; no other management or HALT/restart authority. Residue, curation,
other steering and the rest of the Board stay the primary Manager's. The final message puts ROUTE and
one sentence of comparison after its first line; a forklift's STEERED, WAITING or ASK DAN exit-4 ring
is normal: the Manager relays it in one line without redoing reconnaissance. The call
is its doorbell. Its start (the proof, the ready wait, the first message; normally under 10 s) runs
before it waits, inside the background task's 1 h ceiling, and only the Manager's own process ending (a
guarded restart, a crash) stops it unheard, as it stops every waiter today; the fork runs on, and
`oc.sh seat info cc-… --tail 20000` reads its result. A quick fork worker's rings when it finishes,
printing its final message whole (up to 20,000 characters). A forklift's (a fork that prepares and
launches one program) rings once, when it is done: with its program's launch line, or at once (exit 4)
if it launched nothing; with `--doorbell program` it then waits on its quick program's Conductor, the
one whose `launched_by` label names the forklift, and rings when it stops. `--doorbell none` rings
nothing. Exit 2 prints the re-arm (`oc.sh seat wait cc-… --tail 20000` or `--forklift cc-…`). Refused with
`Nothing started`: a caller that is not a Manager (a fork never forks), a call the transcript does not
hold, a Manager past 55% of its window, 12 live forks of the same Manager (counted under the seat start
lock), a call that already made a fork, `--socket` (there is no socket form; a Manager meeting this
refusal has an old system prompt from before the merge, and a fresh Manager Session has the new one); then the quota guard and
seat cap. Run in the foreground by mistake, it forks and returns with a warning instead of waiting.
`--fork-from` remains OpenCode-only.

Native task completion is the doorbell; then read the socket's `## Result` and judge actual
postconditions. Do not launch a foreground waiting Agent or poll in a foreground Bash loop just
to collect it. After the Manager's launching turn ends, background work does **not** block pane
Send. Native `session_state` stays `running` (and flips to `requires_action` for the child's
auto-answered permission prompts) until the background work settles, so the host gate
(`host.own_turn_busy`) blocks only the Manager's **own** turn: a primary `system/init` opens it
(`primary_turn_open`), the primary `result` closes it; before any turn has closed the conservative
rule stays. The same probe showed native state `running` at the moment the second Send was
written, and the CLI answered it. Settings/recovery/unknown effects still retain their real
safety gates. **During the Manager's own turn, pane Send is a host steer:** a steer reaches a busy
Claude session at its next tool step; one long-running command delays it until that command ends
(receipt `queued`, then `delivered`). The pane's ■ Interrupt (beside Send) stops the Manager's
current turn.
Background work **does** block intentional stdin close and guarded host restart while the native
owner is live or unproved, which could lose it. Once exit and drains/ownership are proved settled,
stale retry/background markers are cleared, with their old values retained as incident evidence;
a resume never inherits them. Use Conductor-owned `oc.sh quick` for work that must outlive the Manager conversation.

Pinned **2.1.299** was exercised with its real CLI against a fully isolated synthetic loopback
provider/MCP (no subscription inference). It accepted the generated definition from a file,
inherited the selected model and MCP ping, removed Agent only from child tools, emitted
`system/task_started` (`task_type:local_agent,is_backgrounded:true`), `task_progress` and
`task_notification`, and automatically invoked the primary completion turn. A second parent
Send was replay-acknowledged/replied while the child call was held for six seconds. Native
`background_tasks_changed` replaces the running set. Completion turns have task-notification
origin and no human message UUID: never attribute them to another human Send. Committed selected
fixture: `scripts/fixtures/claude-agent-2.1.299.json`; private scratch receipt directory
`__repos/oc-scratch/opencode-companion/legacy/hc-manager-quick/research/probe-774d607a`. This is protocol proof, **not a real Max socket
job or production pane activation**; those are the next fresh-launch acceptance.

Child transcripts are `<native-project>/<parent-uuid>/subagents/agent-<agent-id>.jsonl` with
`.meta.json` type/description/call linkage. Child records use the **parent** sessionId plus
agentId/isSidechain, not a separate Claude UUID. The pane shows parent-labelled view-only rows
and follows transcript appends, while the Manager view shows its Agent call and native task
progress. `--forward-subagent-text` also preserves completed child frames in the host's raw feed;
the host isolates them from primary context/receipts/model. No child stream deltas were observed
in the pinned probe: live child visibility means appended messages/thinking/tool calls, not a
promise of token-by-token child output.

The child uses the same native CLI/account/inference path, not OpenCode or an API proxy. Launch
sanitization removes API-key/token/provider/base-URL overrides; the host requires native
`claude.ai/firstParty/max` authentication and initialization. Native list-price cost fields are
not paid API spend; Max quota is shared. Helpers explicitly dispatched into OpenCode are separate
and retain that runtime's billing. Refine's independent API billing is unchanged.

The inherited context callback is bound to the **primary** session, not the child. The native
preamble forbids treating it as the child's context; keep child work bounded and return a PARTIAL
early when needed. Manager-local native children do not qualify unattended Toolkit seats.

Activation: at Dan's settled boundary, `bash "$C/scripts/claude_restart.sh" --close-idle
--launch-manager --model <chosen-native-model> --effort <chosen-effort>` loads the new host and a
**fresh** Manager prompt/tool policy. Old-UUID resume preserves its old primary policy and cannot
enable missing Agent retroactively, though generated child doctrine is refreshed. Restart the
pane server at a Send/Refine-idle boundary and reload the **same** origin for child discovery/UI.
No worker performs these production actions. Small acceptance prompt after fresh launch:
**“Use Agent with manager-quick, run_in_background:true, on socket [ap:JOB]; return immediately
so we can keep talking, then read its Result when the completion notification arrives.”**

From WSL (stdlib Python only; the host socket is a POSIX Unix socket; from a Windows shell, prefix
with `wsl bash -lc "…"`):

```sh
bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_host.sh start
bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_manager_headless.sh prepare
bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_manager_headless.sh launch \
  --model claude-opus-5-5 --effort high
bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_manager_headless.sh resume \
  --uuid 3ae84e1e-0d58-47d4-97a9-6111eba2a11d
```

* **`prepare [--out FILE] [--strict]`** — compose/check and print `source_sha256`, `prompt_sha256`,
  `bytes`, `warnings` and `latest_prompt_path`; always atomically write the fixed owner-readable
  prompt at `~/.local/state/opencode-companion/claude-headless/manager-prompt-latest.md` (0600),
  and optionally write `--out` too. No host call, process or inference.
  Use it to pin or review a prompt revision before launching, and as the prompt-hash evidence for
  later units (e.g. native context qualification).
* **`launch --model M --effort E [--uuid U] [--cwd DIR] [--agent A] [--program P]
  [--socket-handle S] [--parent P] [--workflowy-url URL] [--strict]`** — compose/check/publish, then exactly one host
  `launch` with `role: Manager` and `resume: false`. Model and effort are required and explicit; a
  fresh canonical UUID is allocated unless `--uuid` gives an exact one (canonical or `cc-…`;
  prefixes, short forms and other spellings are refused). `--cwd` defaults to the manager-sessions
  folder; the provenance flags are optional. The launch is asynchronous: the host returns its
  registered record (`state: launching`/`initializing`) — readiness is read with
  `claude_host.sh call state`, never assumed from this command's exit.
* **`resume --uuid U`** — read host state for the host-owned UUID, then exactly one host `launch`
  with `resume: true`. The exact stored prompt snapshot (`prompt.md`, verified against the record's
  `prompt_sha256`) and the stored `cwd`/model/effort/role/provenance are used; nothing is
  recomposed, `--model`/`--effort` are not accepted. The Workflowy endpoint alone is re-resolved now
  (`WORKFLOWY_MCP_URL` then the WSL gateway); a recorded explicit operator URL stays explicit.
  Pre-source-marker snapshots are treated as dynamic; set `WORKFLOWY_MCP_URL` before resume if needed.
  Only that URL's JSON token may change, with old/new endpoint and hashes recorded; every other
  prompt/settings/MCP byte must match the stored snapshots. The stable preview is updated with that exact stored prompt, not today's
  newly composed prompt. All host Manager launches publish the exact launched text at the host's
  state root, including direct host API launches.

**Read the final text:** open
`~/.local/state/opencode-companion/claude-headless/manager-prompt-latest.md` in WSL (e.g. `less` or
an editor). It is the latest prepared/launched prompt, not an effective-settings receipt or proof
that launch succeeded; any later Manager prepare/launch can replace it. Per-run immutable prompt
snapshots remain the record of what each session launched with. Every restart composes/checks and
publishes **before** host contact. Plain restart/resume retains its warning-only policy;
fresh launch or explicitly requested strict mode refuses with the exact composer error.
Recover uses its stored recipe rather than recomposing an old Manager persona. Residual warnings
are shown before host contact; resume preserves the old prompt.

**Nothing is sent.** A turn is a separate explicit host action (`send`) with an exact message UUID
and generation. The launcher never sends, never re-sends and never answers for Dan, and every
success payload says `"sent": false`.

## Claude Code updates, known-good and rollback

Claude Code stays on automatic updates (Dan's ruling); `scripts/claude_version.sh` makes an update
safe to accept. From PowerShell, each a single line:

```powershell
wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_version.sh status"
wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_version.sh mark-good"
wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_version.sh rollback"
wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/claude_version.sh unfreeze"
```

* **How updating works here:** `~/.local/bin/claude` is a symlink into
  `~/.local/share/claude/versions/<version>`; the host spawns through it (`launch.CLI`), so the
  symlink decides every new launch. The background updater runs in the **interactive** `claude`
  UI (at startup and periodically), not in the host's headless processes. It obeys
  `DISABLE_AUTOUPDATER` (process environment or the `env` block of `~/.claude/settings.json`),
  `DISABLE_UPDATES` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`. `autoUpdates: false` in
  `~/.claude.json` is ignored on a native install while `autoUpdatesProtectedForNative` is true:
  the installer writes that pair itself to switch off the legacy npm updater. After an install it
  keeps the launcher's version, any version a running process locks, and the two newest others,
  and deletes the rest. `claude install <version>` re-downloads any published version.
* **`status`:** current version, versions on disk (current · known-good · running), the
  known-good record, who governs updates and the channel. (The host's idle-process collection pins
  no version: it checks each process's own native readback, see *Automatic collection*.)
* **`mark-good [VERSION] [--note …]`:** records the version (default: current) in
  `~/.local/state/opencode-companion/claude-code-known-good.json` with its sha256, and keeps a hard
  link of the binary in `claude-code-known-good/` beside it (the newest two good versions keep one),
  so the installer's pruning cannot take a known-good version away. `unmark VERSION` removes one.
* **`rollback [VERSION]`:** freezes first, then points the launcher at VERSION (default: the newest
  known-good version older than the current one; else the newest older version on disk), restoring
  it from the kept copy or with `claude install VERSION` if it is gone; it checks
  `claude --version` and prints the next step: restart the host at a settled moment
  (`claude_restart.sh --close-idle --launch-manager …`), since running processes keep the binary
  they started with, then reload the pane. `--no-freeze` switches without freezing.
* **`freeze` / `unfreeze`:** set / remove `env.DISABLE_AUTOUPDATER = "1"` in
  `~/.claude/settings.json`, nothing else (the file is backed up to
  `settings.json.bak-claude-version-<time>` first; an `env` left empty is removed). After
  `unfreeze`, the next interactive `claude` start updates to the newest release.
* Every mutating command takes `--dry-run`. Refusals exit 3 and change nothing. Tests:
  `python3 -m pytest scripts/test_claude_version.py -q` (WSL; a scratch HOME, never the live install).

## Limitations and what is not claimed

* **Controls are host operations, not launcher flags:** `interrupt`, `set_model`, `apply_effort`,
  `apply_flag_settings` and `get_settings` have durable correlated receipts. Requested choices
  are never substituted for native `get_settings.applied` proof; a mismatch/pending operation blocks Send.
* **Native context:** the supervised Manager's `mcp__context__context_status` with no arguments
  reports measured input **as of its previous completed model call**, with timestamp/age, native
  window and shared role/launch target. UNKNOWN means no completed call yet in this generation;
  output/cumulative/local estimates are not occupancy. This does not qualify exact tool-time
  occupancy or unattended Toolkit seats. The child inherits the primary-bound callback but must
  not call it as its own measurement. Old sessions keep their original prompt/MCP snapshot.
* **Seats are the scripts':** Claude Code Conductors, workers, lead seats and helpers are started and
  managed by the `oc_*` scripts (`docs/claude-headless-host.md` § Seats through the oc_* scripts); the
  Manager reaches them through `oc.sh` with their own `cc-…` ids and never passes its own.
* **Nothing global:** the host writes only its private per-run state; the Max login, global Claude
  settings and OpenCode stay untouched. `map.refresh`, restarts and global settings are never this
  tool's job.
* **A live host is required** for `launch`/`resume`; an unreachable socket fails with a transport
  message and exit 1. `prepare` needs no host at all.

## Conversation and process lifecycle contract

**The pane targets conversations; a native process is their transport.** Selecting a registered
ended Manager still permits archived Refine. **Send** first reconnects that exact conversation,
shows **Reconnecting to ‹name› …**, waits for Max initialization/applied settings, rebinds the
editor to its new generation, then journals **one new Send**. Merely browsing/selecting never
launches. A collected row says **sleeping (reconnects on Send)**; a crash/manual close says ended.
Unmanaged history and other roles never gain pane reconnect authority.

The generation is the host-side delivery identity for one process incarnation: receipts and
controls retain the generation in which they were admitted. Claude Code's own conversation file
has no such run ID; resuming the same native UUID restores its conversation, not a new persona.

Server-side `POST /api/claude/reconnect` carries only existing target authority, exact execution ID
and expected generation; the browser supplies no launch paths, prompt or endpoint. Host
`reconnect(execution_id,generation)` serializes per conversation, reuses a live owner, or resumes
through the stored snapshot only after the old PID/start witness is absent, drains are settled and
the UUID lock is free. Ten concurrent reconnect requests produce one replacement process, not ten.
A reconnect carries no message: concurrent reconnects do not queue ten messages or promise they
all run at once. The pane and the host apply one rule (`settlement.reconnect_blockers`, stamped as
`reconnect_policy` on every host record): only a live, unknown or unproved owner, an unknown PID
identity or a recovery already in flight refuses, each with a plain reason and the way out. Once
the owner is proved gone, the dead generation's protocol/settings/infrastructure markers are
released and its uncertain inputs stop gating Send (still uncertain, never replayed), logged as one
`dead_generation_holds_released` incident; resume has always ignored those markers. A host older
than this rule still refuses on them, and the pane, reading that host's records, says so and
offers **＋ New Manager** instead of a reconnect that will fail. A missing/lost Send reply is still **query the original receipt,
never replay**; reconnect is not permission to repeat an uncertain semantic input.
Native `--resume` has no file for a never-messaged UUID. Only when the complete host journal proves
**no accepted message and no native turn ever occurred**, snapshot restoration uses `--session-id`
with that same UUID and original snapshots instead. This restores an empty registered conversation,
not a fresh persona or a fallback for deleted/lost worked history; any uncertainty forbids it.

### Automatic collection: three hours of verified settlement, not silence

Every collection pass (default every **180 s**) checks every live process, each in its own task; a
process still being checked is skipped, so an eligible one closes within one cadence of reaching the
threshold however many are running. Default
settled threshold is **10,800 s / 3 h**, configurable before host start/serve with
`--collection-threshold SECONDS` and `--collection-cadence SECONDS`; health/state expose the
threshold, observed settled age and whether a check is in progress. The age uses a **monotonic**
clock and is never restored from a wall-clock timestamp after host/process restart. Native idle
with an explicitly known empty task level and all companion holds clear starts the span; native
non-idle/task work, any Send/control or native-only turn resets it. Suspend/silence is not work proof.
The first native initialize can omit the task level (2.1.299, 2.1.300): a repeat readback seeds an otherwise
idle generation's unknown level, starts its age only then, and never closes on that first observation.

At the boundary the host holds the same per-process Send gate, issues a journaled native
`initialize` on its owned stdin, and requires its correlated current-generation/PID response,
carrying a well-formed `claude_code_version`, to say **idle**, followed by its **fresh
`background_tasks_changed tasks:[]`** snapshot.
Cached emptiness, an old run's task notification or an earlier user result is insufficient.
It rechecks native activity and every shared close/restart hold immediately before EOF, and the
writer revokes an authorized close if observed work changes before actual EOF. The ordinary
stdout/stderr drains, PID witness and inherited lock prove exit; collection never signals/kills.
The durable close reason is **collected**, with settled age and both native proof cursors.

Holds include accepted inputs; native running/requires_action/queued turns; Bash/Agent/background
tasks; permission/dialog requests awaiting delivery; any native control reply; settings pending or
mismatched; native retry; recovery; infrastructure/protocol attention; and ownership uncertainty.
**Pending escalations are review requests, not native work:** they do not reset the settled age or
block collection, including incidents retained from an earlier generation. Their full evidence and
pending status survive collection in the durable incident feed; collection never resolves them.
One predicate serves direct `close_input`, collection and guarded restart: the automatic collector
keeps the other marker holds above; a deliberate operator close or guarded restart holds only on
work a close could lose and reports settings, escalation, protocol, financial and
pending-control markers instead (`settlement_advisories`, `reported_not_blocking` in the restart
report), and may close a `launch_blocked` process. Legacy
pre-state-push generations retain explicit operator-close compatibility only and are **never**
automatically collected. Missing, malformed, unsupported, wrong-PID, lost, changed or late
readback means **leave running**. A readback certificate is usable only within a 30 s observation
window; expiry abandons collection, never cancels the native request or adds a tool/reply deadline.

**No version pin; per-process capability proof** (`collection.collection_refusal`, the list is
`collection.NATIVE_PROOF`, also in host health as `collection_native_proof` with
`collection_gate: native_capabilities`). A process is collected on any Claude Code version once its
own generation shows: the qualified launch envelope; its effective-settings readback with
compaction/memory/fast mode `false`, no hooks, no auxiliary feature on, and an `env` block
reporting `DISABLE_CRON=1` and the session-state variable `1` (absent is a refusal, never a
default); at least one native `session_state_changed` push once it has run a turn
(`native_state_push_cursor`; initialize readbacks never set it); no unrecognised native system
subtype (`unknown_system_subtypes`; recorded for the collector only, never a Send/close/restart
hold); and the boundary readback above. Anything missing or malformed refuses **that process**,
leaves it running and says why: a `collection_refused` event once per change (state shows
`collection_refusal`), or a `collection_skipped` event naming the boundary failure. The
launch-time `claude --version` (`auth.cli_version`) and the readback's version
(`native_cli_version`) are recorded for diagnosis and gate nothing. Verified on 2.1.300
(scratch host, real Haiku processes) before the pin was removed.

**Execution envelope:** native cron is off (`DISABLE_CRON=1`), Monitor is not offered, setting
sources are empty and no hooks are configured, prompt suggestions/progress summaries/side questions
are not requested, automatic/idle compaction and automatic memory are off. Actual launch facts and
native effective settings are checked; any enabled/unverified excluded feature means do not collect.
Default/unknown tool policies are ineligible. Native state catches a **synthetic task-completion
reply after the task set has become empty**, not only replies to companion-accepted human UUIDs.
Native task terminals match **task_id + run_id** so an old notification cannot remove a newer run.

A host started inside a Claude tool call can inherit that caller's session context for its entire
lifetime. Every fresh launch, resume and fork strips `CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION`,
`CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_EXECPATH`, `CLAUDE_CODE_MESSAGING_SOCKET`,
`CLAUDE_CODE_MESSAGING_TOKEN`, `CLAUDE_CODE_SESSION_ATTENDED`, `CLAUDE_CODE_SESSION_ID`,
`CLAUDE_EFFORT` and `CLAUDE_PID` before computing its envelope. These belong to the parent, not the
independently owned child; native Claude generates its own tool context. Only removed **names**
are recorded, never their values. They are not newly allowed feature gates: unknown feature
enablement still refuses collection, and all native settlement/readback requirements stay intact.
This strip applies to inheritance, not deliberate profile configuration: explicit profile values
remain as configured and collector-ineligible, without a new launch/resume refusal.
Existing processes retain their original launch envelope; a code update does not retroactively
qualify them. The guarded `--close-idle` restart uses operator settlement (not collector eligibility)
to close settled old owners, then the new host launches clean generations on deliberate resume/Send.

**Tooling rule:** use registered `run_in_background` Bash (including long `oc.sh seat wait`) and
registered background Agent work. **Never detach with `cmd &` or daemonize work in a foreground
Bash call.** Such children/external services are invisible to Claude's task registry and may outlive
native idle plus empty tasks. The native two-frame query has no atomic settlement/close lease;
companion admission and observed native events are gated/rechecked, but this is deliberately the
qualified execution-envelope contract, not a universal certificate for every native feature or
arbitrary detached/external work. Do not widen it without qualification.

### Event matrix and recovery playbook

| Event | Process/state/evidence | Continuation / what Dan sees |
|---|---|---|
| Pane reload, close or second tab | No native lifetime effect; private host logs/receipts continue. Same-origin browser journals retain original generation. | Reselect/revalidate the same conversation automatically; old uncertain operations remain query-only. Abrupt close can lose typing before the draft debounce. |
| Pane server restart | Reader refs/indexes/mailbox are rebuilt, host untouched. | Same conversation/process; exact scoped receipts survive opaque-handle reconstruction. Inspector needs another deliberate selection. |
| Fresh Manager launch | New native UUID/g1, today's prompt/profile/role/MCP snapshots, no user turn. | Select ready row; fresh instructions, separate conversation. |
| Same-UUID resume / Send reconnect | Old owner gone/free lock; new generation, same prompt/settings/role/history, URL-only endpoint migration, rotated context binding and regenerated child doctrine. | Reconnecting then normal; previous completed-call context starts unknown until a new call. No original Send replay. |
| Settled automatic collection | Fresh native idle/empty tasks + envelope + all holds clear, three-hour monotonic span; EOF and normal drain, durable collected reason. | Sleeping, reconnects on next Send; transcripts/raw receipts are not deleted. |
| Explicit `close_input` | Shared settlement predicate under the Send gate; journaled manual EOF/drains. | Ended, reconnects on Send; input_close_queued alone is not exit proof. |
| Guarded host restart | Explicit operator quiescence; shared predicate, optionally authorized idle EOFs, all owner/witness checks, one witness-checked host SIGTERM/start. | Stopped conversations reconnect lazily on Send. Script is not a global concurrent-use maintenance lock. |
| Usage-limit lockout (five-hour or weekly window rejected, overage refused) | The host ends the turn sleeping in its 429 retry with its own authorized interrupt (`cancel_queued`: queued messages are cancelled, their texts stay in the receipts), records `quota_halt` and one `quota_halt` incident for Dan, schedules no blind session continuation, and closes the process (old login) once it is provably idle. A five-hour halt also arms one independent host-level Manager recovery check at the structured reset + 5 min (host doc, *Five-hour recovery check*); weekly windows arm nothing. | Manual Send/account switching and `oc_seat resume` remain available. For a five-hour window the current Manager receives one labelled automated notice due at reset + 5 min, carrying [ap:T1TNT3] and Dan's standing quota-only recovery authorization [ap:TEUN9Y]. The host reconnects that Manager if needed; the Manager coordinates Conductors and workers, never the timer independently. Generic Manager crash, interrupt and restart continuation stays banned. |
| Host loss while native owner lives | Pipes cannot be reattached; inherited UUID lock/PID witness may survive. Raw gaps and incomplete receipts remain uncertain. | Ownership unknown; no duplicate process. Periodic 60 s orphan recheck marks exited only when witness is absent AND lock free; semantic-effect incidents still require review. |
| Host loss with owners gone | Reconstruct exited records; retain raw reconciliation gaps and recovery lineage. | Idle conversations reconnect on Send. Eligible unfinished-input crash recovery is separate, uses one new generic continue, never the original text. |
| Native crash/OOM or descendant-held pipes | PID exit may precede stdout/stderr EOF; retain exited_draining/lock until drains settle. Unfinished effects/receipts uncertain. | Inspect incident/recovery. Once exit is proved, Send reconnects; uncertain inputs stay uncertain and are never replayed. |
| WSL shutdown / Windows reboot | Old boot witnesses vanish; durable host/native files remain, interrupted effects are not rolled back. | Start the independent host if absent; it never resumes every conversation at boot. First Send reconnects the selected settled conversation and re-resolves a dynamic gateway URL. |
| Sleep / suspend | No silence action; retained processes resume if WSL survived, otherwise shutdown path. | Observe native/network retry/error evidence. Monotonic settled age never replaces fresh boundary proof. |
| Workflowy daemon restart | Native MCP can disconnect/retry; active remote mutations can have unknown effects. | Reconnection/tool availability is not proof a prior write did not happen; inspect before repeating. |
| Native version/feature change | Running binary unchanged; next launch reads installed version and explicit envelope. | Any version collects only on its own processes' capability proof; a missing/malformed signal or unrecognised system subtype refuses that process (`collection_refused`/`collection_skipped` with the reason), never a whole version; unsupported initialization remains visible, no guessed protocol promotion. |
| Two programs / alternate state roots | Canonical host root + inherited UUID flock protect supported entry points. | Manual native resume/another state root is outside that namespace: never use it to bypass ownership guards. |

**The five-hour recovery check is addressed to the current Manager, not Dan.** It arrives after the
structured reset + 5 min as an ordinary user line beginning `AUTOMATED FIVE-HOUR RECOVERY CHECK — not a
new message from Dan.` — exactly once per five-hour episode, coalescing every session that hit the same
reset, and only for five-hour windows. Read the on-demand page it carries, **[ap:T1TNT3]**, then and not at
startup: inspect each affected program's *current* registry and ConductorState plus newer rulings, steer
each current quota-paused Conductor once to reconcile every in-flight assignment, recover Manager-direct
unfinished seats the same way, re-arm waits, and report what resumed and what still needs Dan. The notice is
a request to inspect current work: it replays no interrupted turn, grants no new cast, model or authority,
and lifts no unrelated human HALT. The pending alarm is delivery bookkeeping, not session work: it never marks
a session busy or blocks close, collection or the guarded restart. Once delivered, the Manager's new turn has
the ordinary busy/settlement rules; a lost Send reply is reconciled by
the notice's one durable message UUID, never sent twice. A real five-hour pause at the 95% launch guard arms
it too; preflight, dry runs and read-only usage checks do not. A weekly window, a Manager crash, an interrupt
and a host restart still get no automatic continuation.

**After reboot/host loss:** save the draft; try normal pane observation. For a recorded stopped
restart/switch, use `claude_restart.sh --operations`, `--status ID`, then `--recover ID` first.
Without such a recipe, explicitly `claude_host.sh start` then health (never restart a surviving host). Inspect ownership,
raw gaps, prior receipts/recoveries/escalations; wait for real orphan witness/lock absence, not
silence. Select the exact registered conversation and Send a deliberate new message; if it cannot
reconnect/readiness is unproved, keep the draft and inspect the stated refusal. A new persona is a
fresh Manager launch, not old-UUID resume. Never delete a lock file to manufacture ownership.

**Retention:** collection reclaims only the settled OS process, not host DB/raw generations or native
JSONL/subagent files. Claude Code (2.1.300) runs its transcript sweep (default 30 days) only from
the interactive TUI, never under `-p`, and skips it under empty setting sources unless an enabled
source names `cleanupPeriodDays`. Fresh launches carry `cleanupPeriodDays: 3650`
(`launch.FORCED_PROFILE_SETTINGS`); older snapshots resume as stored, without it (the skip case).
The sweep deletes by mtime across the whole native tree, so the protection that matters is the
interactive `claude`: `cleanupPeriodDays` in `~/.claude/settings.json`. Host evidence is not an installed native
transcript backup/importer. No evidence pruning or backup/restore policy is introduced here.

## Production activation and daily use (U06 prepared, not activated tonight)

Do this at Dan's **idle boundary**, saving unsaved dictation externally first. No worker restarted
the production pane, host, Workflowy daemon or OpenCode. The live whole-Workflowy-result check and
Dan's physical forty-input scanning/dictation review are still open; offline/browser fixtures do
not close them. The supervised Manager does not depend on native seats or a fabricated context tool.

1. **Activate U02 first, when all agents/Cartographer work is idle.** Follow [ap:51LMDW] U02 /
   deployment reference `3a35e51b`: stop Windows connector/shim FIRST, then daemon8766, broker8765.
   PowerShell: `& "E:\__daniel347x\__Dan_Root\__repos\workflowy-nexus-mcp-tools\scripts\Clear-WorkflowyNexusCache.ps1"`.
   Restart using Dan's existing private `npx @typingmind/mcp <existing-token>` command (never publish
   the token). Confirm port8766 and installed NEXUS commit `1d30db667eee47cc9c7a09c06a77cfd34d767774`.
   This tools/list change requires an idle OpenCode catalog refresh, **not during any worker run**:
   `wsl bash -lc "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/restart_oc_server.sh"`.
   Reattach with `opencode attach http://127.0.0.1:4096`. This is the separate metadata activation,
   not a normal pane/Manager restart requirement.
2. **Pane only**, WSL:

   ```sh
   C=/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion
   bash "$C/transcription-pane/start.sh" stop
   bash "$C/transcription-pane/start.sh"
   ```

   Reload the SAME `http://127.0.0.1:9911` profile/origin; `localhost` is separate storage. Never clear
   site storage. Choose Claude in a full-width tab (leave Chrome's CodeNomad split view).
3. **Start/check the independent host explicitly**, then launch a new Manager using the commands
   above with explicit model/effort, e.g. `claude-sonnet-5-5/high` for an inexpensive first check.
   If a host already owns sessions, use it; do not start a competing host or restart it blindly.
   `health` verifies readiness of IPC, not native initialization. `launch` returns an execution ID
   `cc-UUID` and generation; `state` must show Max-confirmed ready and applied settings before Send.
    Select its **live Manager** row in the integrated viewer; this automatically targets Send/Refine,
    with **Use as Send / Refine target** as a fallback. Selecting sends
   nothing. For an existing registered Manager whose old owner is proved gone, **Send reconnects**
   that conversation; the operator alternative remains
   `bash "$C/scripts/claude_manager_headless.sh" resume --uuid EXACT-UUID`. Never select an unmanaged transcript
   and assume it has been adopted. Lost/unknown owner is an incident, not permission to replace it.
4. Dictate → Refine (existing independent API billing/settings) → Send (native Max inference).
   Read the exact replay ACK separately from accepted/started/completed/uncertain. Use native
   **Apply model / Apply thinking**, then inspect **effective** readback; these are not Refine
   controls or persistent global defaults. Wait keeps draft/no queue. Explicit Interrupt preserves
   partial effects and needs result/task settlement evidence. Reload/restart the pane at idle:
   CLI stdin/lifetime belongs to the independent host, not the browser.

### Activate live thinking and activity (U06b)

At an idle pane Send/Refine boundary, save dictation externally, stop/start the pane using step 2
above, and reload the **same** `http://127.0.0.1:9911`. The selected host session now shows streamed
text, an expanded live thinking-summary disclosure above the reply (collapsible after), early tool
bars, and a ticking activity/last-event indicator. Stored summaries use the same disclosure.
The Thinking token count is the native display estimate, not measured context or billing.
Signatures, omitted and redacted bodies never become readable thinking. Result records are small
end-of-turn receipts, not another copy of the assistant reply. The left model/thinking badge says
**applied** only from native verified readback; otherwise **pending**. No OpenCode restart is needed.

Viewer changes work with the existing host feed, but an already running native process cannot gain
the new launch flag. If the production host predates U06b, it must load the changed launcher before
it can launch/resume with summaries. **Do not restart an occupied host.** At Dan's deliberate idle
boundary, inspect `list`, settle all sends/controls/background tasks/recoveries, and explicitly
`close_input` each owned session with its exact execution ID/current generation (recipe below).
Wait for each state to show native exit and `owner:"gone"`; compare every recorded PID/start witness
against `/proc`. Retain exact UUIDs and receipts. Only once **all** native owners are proved gone,
read `health` and compare the host PID/start witness before an explicit operator SIGTERM. Start the
host using step 3; confirm its new health. Never close/kill/restart because of silence or a pane outage.

After the explicit session-close review, the repository restart script refuses unsettled work on
live or unproved owners, and reports markers on proved-dead historical records without blocking
(`exited`/`launch_failed`; the PID/start witness is still checked). It checks the host PID/start witness,
waits for its exit, starts a new host and
health-confirms that exact new process. Without `--close-idle` it closes no native session.

**Finished work never blocks it.** With `--close-idle`, a live session whose **only** hold is
"native background task level is unknown" (it once ran a background command or subagent and Claude
Code never re-reported the empty level) is first handed to the host's `settle_for_restart`, under
the Send gate: the collector's own native readback (a journaled `initialize`, this PID's idle answer,
then a fresh `background_tasks_changed` snapshot). Idle with an empty level settles it, and the
normal `--close-idle` pass closes it through `close_input`. Only if that readback cannot settle it
(no answer, or no snapshot, within 30 s; or a malformed answer) does the host check the process-tree
proof: native state idle, no recorded background task, no other hold, no native work since the
readback, the exact PID witness alive, and no descendant except `python … claude_context_mcp.py`.
If it holds, the host sends that one process one SIGTERM (`close_reason: restart_process_stop`,
event `restart_process_stop_authorized` with the proof), the script awaits its proved exit and
names it under `stopped_on_process_tree_proof`; every attempt is in `settled_unknown_level`. A
readback that shows a task, a running state or new work, any other hold, or a failed proof still
refuses, naming the reason. Never a blind kill. A host older than `settle_for_restart` refuses as
before (the restart that installs it settles nothing).

```sh
bash "$C/scripts/claude_restart.sh"
# Or explicitly authorize idle session EOF as part of the restart:
# bash "$C/scripts/claude_restart.sh" --close-idle
```

Then preserve this Manager's conversation with an explicit resume:

```sh
bash "$C/scripts/claude_manager_headless.sh" resume --uuid "$UUID"
# Or deliberately start a new conversation instead:
bash "$C/scripts/claude_manager_headless.sh" launch --model claude-sonnet-5-5 --effort medium
```

Use the exact stopped host-owned UUID, not a transcript title or prefix. Resume keeps its stored
prompt/settings/non-URL MCP bytes and current verified model/effort; the new native process adds
`--thinking-display summarized`. Check the new generation's `launch.thinking_display_requested`,
ready state and applied readback, and select its live Manager row. Prior empty
thinking blocks stay unavailable; new nonempty summaries stream on subsequent turns. This switch
does not retrofit prompts/MCP, grant unattended seats, change Refine billing or enable paid overage.

Restart exit3 means refused before any host signal (some explicitly authorized idle EOFs may have
already happened); exit1 means inspect a failed restart/follow-up. `--timeout` bounds **observation**
waits only, never kills native work. If the host is genuinely absent use `claude_host.sh start`:
the restart script refuses an unreachable endpoint rather than guessing that no owner exists.

### Receipt, incident, resume and stop commands

Use private file-first JSON rather than prompt text/credentials in shell argv. Substitute only
the exact values returned by this host; example IDs elsewhere are not live authority.

```sh
C=/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion
umask 077
Q="$HOME/.local/state/opencode-companion/claude-manager-review"
mkdir -p "$Q"
ID='cc-<exact-native-uuid>'
GEN=1 # use current state generation, not an assumed value
printf '{"execution_id":"%s"}\n' "$ID" > "$Q/state.json"
bash "$C/scripts/claude_host.sh" call state --file "$Q/state.json"
printf '{"execution_id":"%s","message_uuid":"%s"}\n' "$ID" '<pane-message-uuid>' > "$Q/receipt.json"
bash "$C/scripts/claude_host.sh" call receipt --file "$Q/receipt.json"
printf '{"execution_id":"%s","request_id":"%s"}\n' "$ID" '<pane-control-request-id>' > "$Q/control.json"
bash "$C/scripts/claude_host.sh" call control_receipt --file "$Q/control.json"
bash "$C/scripts/claude_host.sh" call incidents --file "$Q/state.json"
bash "$C/scripts/claude_host.sh" call recoveries --file "$Q/state.json"
```

Page incidents using the returned cursor while `has_more` is true; raw logs are private under
`~/.local/state/opencode-companion/claude-headless/<UUID>/gNNNNNN/`. Review result.errors,
permission denials/tool errors, pending escalations, recovery and background tasks; success/exit0
is not acceptance. To acknowledge an inspected incident: `resolve_incident` with execution_id,
incident_id and a nonempty disposition in a private JSON file. To cancel a still-scheduled recovery,
or disposition a blocked/completed one after inspection: `resolve_recovery` with execution_id,
recovery_id and disposition; it holds only on work a live owner may still be doing (a proved-gone
owner never holds it) and leaves the inputs as they are (`release_input` with `recovery_id` releases
them and dispositions the recovery with them). Claimed
uncertain operations are not cancelled/replayed by acknowledgement. Preserve exact original and
submitted text/UUID; a missing receipt or lost response means **query, never resend**. Browser keys
`pane_claude_send_v1` / `pane_claude_control_v1` index execution+generation-specific journal keys
with the same prefix. Old receipts remain visible/query-only and do not block a new Manager;
an unresolved receipt still prohibits blindly repeating its original Send/control. Preserve all
these keys; no automated erase/reset.

For a deliberate native interrupt outside the pane, use `call interrupt` with a private file
containing execution_id, generation, authority (e.g. `Dan explicit operator interrupt`),
cancel_queued:true and optional reason; inspect its request_id/control_receipt and result/tasks.
There is no silence-based kill/interrupt or automatic recovery purchase/auth change.

**Close only this idle Manager:** after receipts/background work are settled, write
`{"execution_id":"cc-UUID","generation":N}` privately, then
`bash "$C/scripts/claude_host.sh" call close_input --file /absolute/private/close.json`.
Read state until explicit exit/owner gone; `input_close_queued` is not proof. Resume later with the
same UUID/stored prompt/config. There is no stop-all command; normally leave the host running.
If the host itself must be stopped, first prove *all* its native owners gone and compare the
health PID/start witness before an explicit operator SIGTERM. Never stop/restart it to repair a
slow turn or a pane outage; disappearing IPC does not prove its inherited native owner locks gone.

### Remaining live acceptance (after Dan's restart)

* New Max session: record actual CLI version, sanitized `claude.ai/firstParty/max` auth + native
  Max account, selected model/effort and applied readback/actual assistant model; verify no overage.
  Native `total_cost_usd` is a list-price estimate, **not paid API cost**. Prefer Haiku/Sonnet; one
  brief selected Opus Manager check remains open. No new U06 inference was run before activation.
* Verify all eight fresh Workflowy tools/list definitions carry integer
  `_meta["anthropic/maxResultSizeChars"]:500000`. Save an exact private baseline/hash of an
  authorized real 200–350 KB note (prior target [ap:CJM7EH]). Ask the new Manager for exactly one
  `mcp__workflowy__node` read with `max_lines:0,with_line_numbers:false`, no file/artifact/second
  reads; request INLINE/SPILLED plus unprompted latter-half/tail quotes. Pair by tool_use_id and
  compare **the entire baseline** inside native `user.message.content[]` model-facing tool_result,
  not stdout diagnostic `tool_use_result`, an inspector or an artifact. Keep private evidence,
  size/hash/auth/model/effort/catalog identity. Above native 500K chars use honest windows/artifacts.
* In the pane: Refine/Send, inspect long JSON/Readable/raw Copy, change model/thinking with readback,
  reload at idle, refuse worker/history Send, then return to OpenCode and check Send/Refine/inspection.
* Dan's short physical review: scan **forty input previews within one second**, click the standout
  call, read multiline JSON as a document, compare raw Copy, dictate while tools arrive, and check
  scroll/focus/density/3–5-line bars at his normal width. Automated fixtures are not this ergonomic proof.

## Verification (offline, no inference)

```sh
PYTHONDONTWRITEBYTECODE=1 /mnt/e/__daniel347x/__Dan_Root/__repos/workflowy-nexus-mcp-tools/.venv-wsl/bin/python -m pytest scripts/test_claude_headless_manager.py -q -p no:cacheprovider
```

The tests read the real doctrine file, exercise the launcher only through a recording fake client
and its pure builders, and start no process. They assert: no frontmatter; the composed body is the
source body with only the declared overrides; key doctrine lines survive; no terminal/Cockpit tokens
(`tmux`, `TUI`, `Arm`, `Release`, `Remote Control`, `/context`); explicitly bound native context with
unknown/stale refusal rather than a fabricated reading; the
native names and the intact `oc.sh` bridge with the no-native-UUID-fork rule; both hashes and the
override manifest; drift composes with the warning (strict refuses); that `launch` requires model/effort and calls only `launch`;
that `resume` uses the stored snapshot (and refuses a changed one or a non-Manager record) and never
substitutes today's prompt; that nothing ever calls `send`; and that `prepare` writes the prompt
without any host call. The file also passes on Windows Python (no POSIX imports), so the normal
Windows suite can collect it.
