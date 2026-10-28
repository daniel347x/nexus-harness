# Session history

`bash scripts/oc.sh history …` keeps Claude and OpenCode conversations together,
independently of either live store. It saves prompts, answers and a 120-Unicode-
character compact-JSON input preview for each tool call, with its status and any
child conversation link. It saves **no thinking, tool output or attachments**.
Native fork transcripts retain their copied base conversation, in file order.

Archiving and dry runs delete nothing. Only an explicit `history prune`, or a
sweep after Dan switches trimming on, removes verified live copies, and only
after writing each one's **full original**, compressed and read back, beside the
backups (§ *Full originals*). `junk`
removes only the Claude host's empty-sid derived-row residue, not
conversations. The pane's History reader is separate: no reducer is imported
into a request thread.

## Files

| File | Purpose |
|---|---|
| `~/.local/state/opencode-companion/history/archive.sqlite3` | Working SQLite archive on WSL ext4; directory 0700, file 0600; WAL, FULL synchronous commits, 10-second SQLite busy timeout |
| `E:\__daniel347x\__Files\SessionHistoryArchives\session-history-YYYY-MM.jsonl.gz` | Complete monthly backups in the Windows folder Dan backs up (`/mnt/e/__daniel347x/__Files/SessionHistoryArchives` in WSL) |
| `…\SessionHistoryArchives\full-originals\YYYY-MM\<backend>-<id>.tar.gz` | One trimmed conversation's full original each, written by the trim before it deletes anything (`originals_dir`) |
| `~/.local/state/opencode-companion/program-seals.jsonl` | Append-only local seal index, owned by `oc_seal_ledger.py`; a missing line means unsealed |
| `dirname(archive_path)/history.log` | The detached seal child's and daily sweep's output, plus seal-backfill diagnostics |

Missing archive, export and ledger paths are created by their writers; readers
treat an absent archive or ledger as empty. Two archive runs may overlap: SQLite
serializes their transactions. There is no lock file, source cache, retry loop,
retention cap or TTL. Telemetry uses `oc_telemetry`'s own existing read-only
sources and incremental cache.

## Commands

Run commands through the launcher, from the checkout you want to use:

```sh
bash scripts/oc.sh history archive --all
bash scripts/oc.sh history archive --program ABC123
bash scripts/oc.sh history archive --idle 24
bash scripts/oc.sh history archive --session claude:cc-UUID opencode:ses_ID
bash scripts/oc.sh history archive --all --dry-run
bash scripts/oc.sh history sweep
bash scripts/oc.sh history verify --all
bash scripts/oc.sh history verify --program ABC123
bash scripts/oc.sh history verify --session opencode:ses_ID
bash scripts/oc.sh history export --all
bash scripts/oc.sh history export --month 2026-10
bash scripts/oc.sh history seals --backfill
bash scripts/oc.sh history status
bash scripts/oc.sh history prune --dry-run
# On Dan's word, the Manager may perform one trim run:
bash scripts/oc.sh history prune
bash scripts/oc.sh history junk
```

`archive` without a selector uses `sweep_idle_hours`. `--session` takes archive
keys, not a display title. Output is JSON: counts, written keys, failures,
timings and export publications. `--dry-run` writes nothing and includes the
shared verification result for existing/candidate rows. It is optional, never
a prerequisite for archive, export or rebuild. A failed session is counted;
other sessions continue, and a later sweep takes it. The CLI exits nonzero
when its run has failures. Settings problems are reported by `status`, not
used to refuse work.

`junk` starts `forget {empty_sid: true}` on the Claude host and prints admission
and health's `forget` progress until done or interrupted. The host deletes only
empty-sid `row_sets`, `rows`, `row_parts`, `row_keys`, `row_diagnostics` and
`row_incidents` in small background batches on its own connection; other sids,
global event/observation/quota records and files stay untouched. Ctrl-C stops the
monitor, not deletion; an interrupted host resumes the job after its next bind.
There is no confirmation or `--yes`. The database file keeps its size (no
`VACUUM`); SQLite reuses freed pages. On an older host the CLI prints one line
saying it needs the Manager's guarded restart, and exits without a deletion.
The Manager performs that restart, then runs `history junk` once.

Claude enumeration reads `~/.claude/projects` once (not its `.claude-work`
alias), including subagents and their `.meta.json` links. The host sees only
`list` with explicit execution-ID batches of at most 200 or a program selector;
its summary is stored verbatim. An unavailable host never causes a managed
session to be recorded as unmanaged. A program archive also records launches
that never produced a transcript; a conversation archived from its transcript
keeps its archived body when that transcript is gone. OpenCode history uses only
`oc_client` GETs, unfiltered and paged oldest first; parked warm forks are archived too.

## Trimming: archive first, Dan's switch

`history prune --dry-run` prints a grouped account for Dan: the programs and
conversations that would go, estimated bytes in each store, and the first rule
that keeps everything else. Every row is in `prune-plan-<UTC stamp>.json` beside
the archive. The dry run writes only that plan, never a live store, archive,
export, seal or pin. OpenCode bytes are the archive's `raw_bytes` (message JSON,
not measured database pages); Claude estimates count transcripts, subagent and
run folders. Host database and OpenCode legacy-copy sizes are not measured.

The single `trim_plan` rule reports the first failing rule, in this order:

1. **R1:** only a program's conversations may go; freestanding history stays.
2. **R2:** the ledger must say the program was sealed at least
   `trim_sealed_days` ago, and its seal note must still say `state: sealed`.
   Reopened or unsealed programs stay; seal-node creation time is not the age.
3. **R3:** nothing in the program may be live or waiting on either backend.
   Claude uses the existing busy and owner-proved-gone predicates; OpenCode
   uses its active-session API.
4. **R4:** no star on the program or any member, including a descendant.
5. **R5:** Managers never go, with either backend's role spelling.
6. **R6:** a program carrying `#oc-persist` stays.
7. **R7:** every conversation the deletion removes must be archived, unchanged
   and backed up, with no pinned or Manager descendant. That includes recursive
   OpenCode children and the Claude subagent folder. A kept child keeps its
   ancestors, and an unarchived live member reports `not-archived`.

The report ends with the explicit Managers / unsealed / pinned / unverified
check. Cheap exclusions name known archive/OpenCode members; the host is read
only for programs that pass the ledger-age and current-seal checks.
Conversations already gone from the live stores (trimmed before, or removed by
hand) are one count, *Already gone from the live stores (in 📦 History)*, not
"Kept" lines. The report also says where the full originals go and how many
files the run would write.

`trim_enabled` stays **false**. Dan sees the dry run and opens a few archived
programs in 📦 History before he decides whether the Manager switches trimming
on in the committed config, or performs one explicit `history prune`. There is
no confirmation, force flag, batch cap or cooling-off delay.

The one executor deletes children first through OpenCode's existing API
deleter (404 means already gone), or uses the host's `forget` verb when health
advertises it. Older hosts leave Claude conversations **waiting for the host's
`forget` verb**, not failed. Immediately before each action the same plan reads
fresh live membership/status, stars, and the full verification gate for every
conversation that action removes. A changed/unarchived descendant keeps its
ancestors, with the failing key in the result. The two original Workflowy
root/seal observations are reused, not a cached verification result.

Then, still before the deletion, `write_originals` writes that action's full
original (§ *Full originals*) and reads it back. A source that changed since
the check is a skip; a write or read-back failure is a skip and a failure. Either
way that conversation and its ancestors stay live, and nothing half-written is
left behind.

`pruned_at` is written after an OpenCode DELETE returns. A Claude background
job is not completion: the executor reads `state` once by id after acceptance
and records deletion only when `archived` is present and `forget_pending` is
clear. Otherwise it reports the conversation as *forgotten on the host,
recorded on the next run*; the next run observes the tombstone again.
No polling wait delays the command. Completed changes republish their months
through the existing complete, atomic export writer. Database files do not
shrink; their freed pages are reused, with no VACUUM.

`oc.sh sessions --prune [--dry-run]` is a front end on that **same plan and
executor**, now reporting both Claude and OpenCode. `--older-than 36h` overrides
`trim_sealed_days` for that run; without it the setting wins. `--program` keeps
its explicit program scope. Unattributed history always stays;
`--keep-unattributed` remains accepted but changes nothing. A plain prune does
not build the snapshot or read its own seal dates first. Dan's explicit
`oc.sh sessions --delete H` remains separate and unchanged.

## Background operation and activation

After a successful seal capture and ledger append attempt, `oc_seal.run` starts
`nice -n 19 bash …/oc.sh history archive --program CORE` as a detached child.
It never waits for the child, and a start failure does not change the seal's
return code or undo its capture/pointer.

`server.main()` starts one `HistorySweep` daemon thread, never at module import.
It does no archive work for `sweep_start_delay_minutes`, then checks every
`sweep_check_minutes`. An absent archive or a `meta.last_sweep_at` older than
`sweep_every_hours` is due. The check reads just that one meta row read-only;
the work is a child process under `nice -n 19` and, when installed, `ionice -c3`.
Only the sweep thread waits for it. Child failures wait for the next scheduled
check, not an immediate retry.

A sweep archives changed sessions idle at least `sweep_idle_hours`, refreshes
unit seal metadata, publishes every missing/stale export, and records its
counts and completion time. Only when `trim_enabled` is true does it then call
the same trim executor; when false it adds no trim plan or host/Workflowy call.
The first sweep (or full `archive --all`) also runs
the pre-ledger seal backfill once. It reads each known program root and uses
`oc_sessions.seal_created_ms` for sealed roots: two Workflowy reads per sealed
core. Reopened roots receive no ledger line. An unavailable seal creation time
uses the program's last activity, logged explicitly. A source-census failure
keeps the first-backfill marker pending even when some cores were discovered;
`seals --backfill` can also run it explicitly.

The CLI and seal trigger activate when these scripts are merged. The daily
thread activates at the Manager App's next normal restart; its first production
backfill waits at least the configured start delay. No service is restarted by
the archiver. Trimming scripts activate on merge; `forget` activation is the
Manager's host restart, never something this command performs. Conversation
trimming (`prune`) is separate from the explicit empty-sid `junk` command.

## Settings

`scripts/oc_history_settings.py` is the **one loader**. Its `DEFAULTS` are
overridden by `config/session-history.json`, or instead by the file named in
`OC_SESSION_HISTORY_CONFIG`. Paths expand against the current HOME at each
load. Missing/unreadable files use defaults; unknown keys are ignored; wrong
types use their defaults and are listed once by `history status`.

| Key | Default |
|---|---|
| `view_window_days` / `view_wide_window_days` | 7 / 30 |
| `idle_unsealed_days` | 90 |
| `universe_idle_scan_minutes` | 10 |
| `sweep_idle_hours` / `sweep_every_hours` | 24 / 24 |
| `sweep_start_delay_minutes` / `sweep_check_minutes` | 10 / 15 |
| `export_grain` | `"month"` (`"year"` is also supported) |
| `archive_path` / `export_dir` / `seal_ledger_path` | The paths above |
| `originals_dir` | `"full-originals"`: relative = a folder under `export_dir` (so it follows the backup folder); an absolute path stands alone |
| `pins_path` | `~/.local/state/opencode-companion/transcription-pane/session-pins.json` |
| `trim_sealed_days` / `trim_enabled` | 7 / `false` (Dan's automatic-trim switch) |

**For tests or measurements, override all four output paths** (`archive_path`,
`export_dir`, `seal_ledger_path`, `pins_path`) in a scratch config; a relative
`originals_dir` follows `export_dir` there. A fresh HOME
alone does not redirect the absolute Windows backup folder. To isolate
telemetry's own cache too, set `OC_TELEMETRY_DIR` before the process imports it.

## Archive, witnesses and verification

The schema has `meta`, `sessions`, `bodies`, and `units`. A unit is a whole
program, or a freestanding conversation and its source-level descendants.
Session keys are `claude:cc-<canonical uuid>`,
`claude:cc-<parent uuid>/agent-<id>`, and `opencode:<session id>`.
Session metadata, its zlib-level-6 JSON body, and its aggregate unit commit in
one transaction. A changed attribution also removes the old aggregate if its
last member left, in that same transaction. Under that transaction the writer
rereads the stored row and writes nothing when it is trimmed (`pruned_at` set:
the stored copy is the only one left), when a record-only capture would replace
a conversation already captured from its transcript, or when an overlapping
run's capture is **older** than the stored one of the same source (L-35): a
transcript with the same inode and fewer bytes or an older `mtime_ns`; an
OpenCode capture with an older `time_updated`, or at the same `time_updated`
fewer messages or an older idle marker (a newer `time_updated` with fewer
messages is a revert, and is taken). A Claude conversation the host has
forgotten (its record carries `archived`, pending or complete) is never
captured again: a file at its old path is a re-created fragment.
Metadata retains names, source records, costs/tokens, models,
effort, telemetry and seals. Archive columns use epoch milliseconds; the
live-compatible `turn_facts_json` uses seconds and the existing `TurnLedger`
summary shape (which excludes a native fork's copied base from its own facts).
If neither records nor host supply a clock, `created_at` and `last_at` use the source file's `st_mtime_ns // 1_000_000` at archive time, not a zero sentinel; a subagent never borrows its parent's host clock. A changed witness republishes both old and new periods, including an old period now empty.

Bodies are dense source-order turns (`seq`, `role`, `ts`, `text`, then optional
assistant `tools`, `model`, `effort`, or user `origin`). The SHA-256 is of the
**uncompressed** compact UTF-8 JSON, not the compressed bytes. Unparsable lines
and unknown record types are counted in `notes_json`; readable content is still
archived. An incomplete last JSONL record waits until a later run.

The transcript witness includes inode, size, mtime_ns and last parseable record
UUID. A launch without a transcript uses generation, state and the host's
recency stamp. OpenCode uses the message count plus `time.updated` and
`time.idle` from info read before the messages. A turn-ending idle marker thus
invalidates an archive taken mid-turn even if `updated` did not advance.
OpenCode `last_at` is `max(updated, idle)`.

An equal witness at the current archive version writes **nothing** for that
session, including its body and unit. The sweep's cheap stat/clock pre-test
does not read unchanged histories or telemetry. Ledger-only seal changes are
separate unit changes and republish all the unit's member months.

`verified(key)` in `oc_history.py` is the single V1–V3 gate for `verify`, dry
runs and the trim executor:

1. The row/body exist; version/codec, decompression, canonical-body hash, turn
   shape/count and absence of unparsable lines all check.
2. The **whole** live witness recomputed now equals the stored witness.
3. The current-grain backup file contains this key with the same witness/hash.

Its fixed reasons are `ok`, `not-archived`, `body-missing`, `body-corrupt`,
`body-incomplete`, `version-unknown`, `witness-changed`, `source-gone`,
`export-missing`, `export-stale`. A source already gone is still readable in
History, but reports `source-gone`, not permission to trim anything else.

## Backup format and rebuilding

A session belongs to the UTC month of its `created_at`. At year grain the
filename is `session-history-YYYY.jsonl.gz`; changing grain leaves existing
files in place and the next run creates the new grain's missing files.
Each gzip UTF-8 JSON-lines image contains:

1. A `type: "header"` line with `archive: {format:
   "session-history-archive", format_version: 1, name: "Session history PERIOD",
   exported_at: "…Z"}`, `schema_version`, `archive_version`, `month` (the month
   **or year** label), session/unit counts, and `through` in epoch ms.
2. A `type: "unit"` line with every unit column for each unit with a member in
   the period. A unit spanning periods appears in several files.
3. A `type: "session"` line with every session column and its decoded body under
   `turns` (an array; rebuilding recovers the row's turn count from its length).

`through` is the newest `archived_at`/`pruned_at` among that period's sessions
and units. Every writer run replaces any missing or older-through image as a
whole, from one read transaction: a temp file in the same directory, gzip
closed, file fsynced, then `os.replace`. Thus the next run heals a process killed
after committing archive rows but before publishing, or an older overlapping
publication. There is no confirmation or default dry run.

To rebuild from the Windows backups alone, into a new working archive:

```sh
bash scripts/oc.sh history rebuild \
  --from-exports /mnt/e/__daniel347x/__Files/SessionHistoryArchives \
  --to /path/to/restored/archive.sqlite3
```

Rebuild uses no live source or telemetry. It verifies each body's canonical
uncompressed hash, retains the newest session image and the unit image with
greatest `archived_at`, initializes the required meta keys, and publishes the
completed database to `--to`. Session/unit rows and body hashes are restorable
from these files alone; sweep/export meta timestamps are local run metadata.

## Full originals

History keeps the reduced copy. The trim also keeps, before it deletes a
conversation, **everything that deletion removes**, compressed, in
`originals_dir` (default `export_dir/full-originals`, so on E: and in Dan's
drive backup). One gzip tar per conversation, filed by the UTC month of its
`created_at`:

- `full-originals/YYYY-MM/opencode-ses_….tar.gz`: `session.json` (the session
  info) and `messages.json` (every message with its parts), fresh unfiltered
  GETs whose witness must equal the verified stored one.
- `full-originals/YYYY-MM/claude-cc-<uuid>.tar.gz`: the transcript
  (`projects/<project>/<uuid>.jsonl`), its folder (`projects/<project>/<uuid>/`:
  subagents and their `.meta.json`), the host run folder `forget` also removes
  (`run/<uuid>/gNNNNNN/`: `prompt.md`, `launch.json`, settings, the raw
  stdout/stderr/stdin streams), and `host-record.json` (the host's `state`
  record). Transcripts must still match their stored witnesses; a subagent
  transcript the archive lacks keeps the conversation live. The host's derived
  database rows are not copied: they are rebuilt from those raw streams.
- Every file carries `manifest.json`: key, backend, program, title,
  `created_at`, `written_at`, the stored witnesses, and each member's source,
  size and SHA-256.

Each file is written to a temp file in its month folder, fsynced, atomically
replaced, then read back member by member against the SHA-256 of what was
read; a failed file is removed. Writing again (a retried action) replaces it
whole. JSON is written with ASCII escapes, so lone UTF-16 code units in cropped
tool output survive exactly. `history status` counts them (`originals`,
`originals_bytes`). `rebuild`, `status`'s export count, `export` and the V3 index
read only top-level `session-history-*` files and never see this subfolder.

To read one back by hand (no restore tool), from WSL or Windows `tar`:

```sh
tar -tzvf …/full-originals/2026-10/claude-cc-UUID.tar.gz            # list
tar -xzf  …/full-originals/2026-10/opencode-ses_ID.tar.gz -C /tmp/x  # extract
tar -xzOf …/full-originals/2026-10/opencode-ses_ID.tar.gz messages.json | jq . | less
```
