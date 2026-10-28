# No-click execution readiness

**Prepared is not active; active is not certified.** Repository configuration takes effect through
the normal idle-only OpenCode restart. A preflight against the live service tests its current policy,
not an uninstalled candidate. Do not launch an unattended night on FAIL or UNKNOWN.

## Policy and independent gates

The master config has an explicit host `*:* allow`, then `question:* deny`. OpenCode v2.0.24
appends document permissions before agent permissions; runtime appends session permissions last.
The last matching action/resource rule wins. Worker/conductor tool denials therefore survive the
host baseline. **Never put a wildcard allow in a session**: it would override the role denials.
Forks and native children inherit session permissions; a restart does not clear stored overrides.

Every role has file-editing and native web search/fetch permissions. Search uses the fixed existing `exa` integration. Pinning a provider
avoids the native first-use provider-selection Form; it does not guarantee future network/auth/rate
availability. Worker/conductor denials cover only `subagent`, `execute`, `question` and `skill`.
OpenCode selects `patch` for GPT models and `edit`/`write` for other models; all three actions are allowed.
No new account, paid service or credential
is provisioned by this config. Interactive native questions are denied rather than autoanswered;
a substantive decision still belongs in a visible human-authority HALT.

Host allow does **not** approve MCP-internal Forms. The existing SSH command must retain
`--disableApproval --group=dev`: installed ssh-mcp 2.8.1 uses these for auto approval and the
privileged class independently. A TOML profile, if introduced later, can supersede quick-start flags.
Keep host verification, built-in SSH denials and role/resource controls. New MCPs/OAuth/semantic
Forms need their own readiness decision, never a generic form-acceptance robot. CodeNomad Yolo
is neither the policy baseline nor a universal MCP Form answerer.

## Cheap pre-bedtime check (roughly 90 seconds)

`bash scripts/oc.sh permission_preflight --help` documents the receipt options. The command
uses `oc_client` and observes the actual OpenCode **Location**, not the shell workdir. By default
it inspects home, Manager and quick-worker Locations, native permission queues and Location-global
MCP Forms. Extra project/worktree Locations must be supplied explicitly. It never approves,
interrupts, resumes, reconnects, reloads or creates a session.
The Python command is usable immediately; only the host/search configuration needs activation.

Snapshot-only inspection is useful for finding policy/queue/MCP problems, but cannot certify
execution: missing execution evidence is UNKNOWN. A PASS needs successful stored native tool
records, not an assistant's claim that everything worked. Unavailable/malformed observations,
timeouts, stale/inherited history, failed tools and missing output markers never become PASS.
Output is restricted to readiness metadata; raw configs, Forms, tool outputs and API errors are
not printed, because they may contain secrets. JSON receipts can be retained privately.
Provider authentication is outside the ordinary native/SSH sentinel's certificate, not an assumed
PASS; if the actual workload needs search, include an owner-controlled native search smoke check.
Registered/pinned provider availability still must be observable. Historical receipt inspection
(`--historical`) never certifies current execution, and default receipt age is at most 90 seconds.

Use the existing normal launch route with a scratch socket carrying this bounded assignment
(replace `<SID>` with the probe's own actual session id):

> You are a quick helper. Probe CURRENT live execution policy only. Immediately run native shell
> `printf 'LOCAL_PERMISSION_SENTINEL\n'` with a 10s timeout and absolute companion workdir.
> Native-read companion `README.md` by absolute path outside your execution Location (first five
> lines suffice). Create only `/tmp/opencode/permission-preflight/<SID>/` with bounded shell;
> native patch/write/edit `marker.txt` there to contain `NATIVE_EDIT_SENTINEL`, native-read it,
> then native-delete that marker and `rmdir` only your empty directory. Use SSH MCP
> `run-command` with exactly `python3 -c 'print("SSH_INLINE_SENTINEL")'` after list-connections.
> No retries, approvals, real files, secrets, production/DB effects, service actions or helpers.
> If an ask/Form/refusal appears, report FAIL and stop. Finish within 90 seconds; record tool
> receipts and cleanup in the socket, not just PASS prose. Never auto-resume an authority HALT.

Record the launch epoch in milliseconds before the normal launcher, copy its returned session id,
then observe it with `--probe-session SID --since-ms EPOCH`. `--session SID` additionally audits
specific existing launch/base/continuation sessions without steering them. Use a **new** approved
scratch socket/session for a new night's run, not an old PASS receipt. Queue checks cover all
supplied Locations, including global MCP Forms; a session-only permission endpoint misses those.
The session must finish with all markers and scoped cleanup; permission queues clear at one instant
are not proof that a later command will not prompt. The tool record time range is printed separately
from the observer's runtime. Slow provider/model startup is a failed time budget, not a reason to
invent no-click readiness. The script does not kill a tool/server on timeout: reconcile that specific
scratch session and any residue before retrying.

Normal quick-helper example, from the companion repository (the owner first creates the socket):

```bash
START_MS=$(date +%s%3N)
bash scripts/oc_seat.sh quick --agent quick-sol61-fast-high --socket '[ap:SCRATCH]' \
  --parent-seat ses_ACTUAL_LEAD --no-wait
# Copy SID from the launcher output; do not substitute another session's receipt.
bash scripts/oc.sh permission_preflight --probe-session ses_RETURNED --since-ms "$START_MS" --json
```

The same payload belongs in the route-specific native/seat dispatch, under that route's doctrine.
Do not create a second conductor/program or test by self-resuming an existing held program.
Nightly only test the routes the job will use; the larger matrix below is rollout work.

## Initial rollout certification (owner/Manager-coordinated, not nightly overhead)

| Route | Existing primitive / actual Location | What distinguishes certification from inspection |
|---|---|---|
| Manager | Attended bounded assignment / `manager-sessions` | Native + SSH markers, question denied, search works without first-use Form |
| Fresh conductor | `oc.sh launch` in an approved scratch program / home | Read back conductor agent/model/Location; execute shared payload |
| Program worker / fresh lead seat | `oc_seat.sh dispatch` (never `subagent`) / home | Top-level, no ParentID: agent + model/level + `fresh` metadata; execute payload |
| Fresh quick worker / quick helper | `oc_seat.sh quick`, helper with real `--parent-seat` / quick-worker | Both fresh top-level routes, connected MCPs before tool turn; execute payload |
| Lead / ordinary helper seat | Normal manifested `seat create` + `seat fork` / home | Inspect parked base and fork policy; execute new, not inherited, records |
| Manager-fork quick worker / forklift | One background `seat quick --fork-self [--forklift] --title …` / inherited Manager Location (`--fork-from` remains the socket route) | Inspect native source/fork policy, current-turn call boundary and completion doorbell; no assumption of quick-worker folder or Yolo inheritance |
| Seat continuation | Normal `seat resume` on scratch seat / retained Location | Observe a newly executed payload after continuation cutoff |
| Self-resumed conductor | Normal `oc_self_resume.sh` only in authorized scratch program / home | New conductor session, not fork; execute payload; preserve real HALTs |

Record each as EXECUTED / STATIC ONLY / DEFERRED, with session id, Location, timestamps and receipt.
Close CodeNomad browser/TUI approval clients for owner-controlled acceptance; no UI approval is
permitted. Do not stop CodeNomad/OpenCode services during an active program to simulate headless
operation. A worker cannot certify that someone else's browser was closed.

At rollout only, if required by the work, separately test `sudo -n true` (no provisioning), unique
interactive/background SSH sessions and inert SFTP transfer in a unique `/tmp/permission-preflight-*`
directory, with cleanup. Signal classification can be checked offline; never signal an existing
production process as a probe. Background completion/reaping is a lifecycle issue, not approval.

## Idle-boundary activation and rollback

Do not hand-edit installed settings or run location reload/MCP reconnect. Finish all active work,
including the Manager's own turn. From WSL (prefix with `wsl` from PowerShell):

```bash
bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc.sh view --running --no-workflowy
bash -lc 'bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/restart_oc_server.sh --check'
# ONLY once idle and explicitly released by the Manager:
bash -lc 'bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/restart_oc_server.sh'
```

Wait for READY, inspect live per-Location config/agent/MCP/queues with this preflight, then run the
fresh payload and coordinated matrix. `--check` is an isolated candidate load, not rollout
certification; it can regenerate the repo's generated Manager quick agent before probing.
Preserve any extra installed plugins: installation is copy-only. Record prepared commit and live
version; compare the live installed policy before calling it active. Failed/previously refused
commands are not automatically rerun by restart. Held programs remain held until their own owner
issues a separate authorized ruling.

Rollback: revert only the owned hardening commit(s), commit/push sequentially, then repeat the
same idle-only restart and verification. Retain the prior config backup; do not revert another
worker's commit, reset/stash concurrent work, or silently resume held tasks. Rollback restores
approval/question risks, so do not call the rolled-back setup unattended-ready.

Permissions cannot repair stdin prompts, `sudo` authentication, Git editors/credential helpers,
package-manager consent, provider login/limits, network loss, missing resources or real authority
HALTs. Use closed stdin/noninteractive flags and bounded commands when the assignment permits;
fail early on unavailable authentication/resources rather than autoanswering semantic decisions.
