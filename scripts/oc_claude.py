#!/usr/bin/env python3
r"""oc_claude.py — Claude Code sessions (seats, quick workers, helpers, Conductors) through the local headless host:
the Claude half behind the oc_* scripts (overnight build U1a, plan [ap:0OP1FK]; routing and quota in oc_backend.py).

A Claude session is ONE host conversation with a non-Manager role, started through the host's own client
(claude_headless.client); its id is `cc-<uuid>`. No Claude behaviour lives in oc_client.py (OpenCode's client).

  start      launch (role worker|helper|base|conductor) → wait until `ready` with the initial settings read back → send
             the first message → wait for Claude's exact echo (acknowledged). Prints `cc-…` as soon as it exists. The
             quota guard runs first (exit 6 quota · 7 seat cap). Nothing here ever times out a Claude turn: the budgets
             below bound only how long this script watches, and an unready seat is reported, never killed. With
             fork_from (oc_seat fork --base cc-…) it starts a native FORK of a base: its whole conversation, same
             cwd, role and stored prompt; `info`/`wait` then name the base and the fork's first-turn cache HIT/MISS.
  status     the computed phase of one session: starting · running · recovering · done · attention · unknown
  wait       0 done (prints the result) · 2 still running (call again) · 4 needs attention (an escalation, a failed
             recovery, an input that never completed) · 5 UNKNOWN (ownership or the host unreachable; never "done") ·
             9 a Manager's doorbell handed over to a newer Manager (through oc_seat wait; oc_doorbell.py).
             Surfaces pending escalations, a scheduled recovery (e.g. Max quota exhausted, resets 14:00) and the quota.
  info       everything about one session: labels, model, context (previous completed call), quota, last result
  follow-up  one more message to an IDLE seat (host Send is idle-only): context check first (exit 3 at/past target),
             host `resume` (same UUID) when its process was collected or exited, then send and confirm
  socket     every Claude session ever started on one socket, newest first
  cancel     the fallback switch: cancel a scheduled recovery (it would wake the seat hours later and race the fallback)
             and close the seat's input; a busy seat is refused (interrupting seats is not wired yet)
  resolve    the way out of a stuck seat, one selector: an escalation (--incident, --all-escalations), a recovery with the
             inputs it covered (--recovery), the inputs no process can answer any more (--release-input, never
             replayed), or a durability failure once storage works again (--clear-infrastructure)
  quota      the Max quota line and the busy seats · route AGENT|MODEL → which backend runs it

From WSL: bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc.sh claude ‹verb› …
Point at another host (tests, an isolated smoke host): OC_CLAUDE_HOST_SOCKET=/short/path/host.sock.
The task itself is judged from the socket's ## Result, never from exit 0.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_backend import (_CANONICAL, EXIT_ATTENTION, EXIT_CONTEXT, EXIT_DONE, EXIT_ERROR, EXIT_NO_HOST,  # noqa: E402
                        EXIT_HANDED_OVER, EXIT_RUNNING, EXIT_UNKNOWN, LAUNCH_ROLES, HostConnection, SeatError, backend_for,
                        claude_launch_choice,
                        connect, cursor_seq, host_socket, hhmm, is_claude_handle, load_settings, quota_guard, quota_line,
                        quota_snapshot, report, resolve_claude_id, same_socket, seat_busy, start_lock)

HINT = "bash /mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion/scripts/oc.sh claude"
"""How messages name a follow-on command; a wrapping script (oc_seat, U1b) may set its own."""

RULE = "-" * 96
READY_BUDGET = 3600.0  # how long start/follow-up watch a launch become ready before handing it back (never a kill)
UNSENT_GRACE = READY_BUDGET + 300.0   # start's own observation budgets (ready + acknowledgement 120 s) plus margin


# ------------------------------------------------------------------------------------ reading a record

def describe(record: dict) -> str:
    """'worker · ⚡ quick · quick-opus55-high (claude-opus-5-5 · high)'."""
    labels = record.get("labels") or {}
    kind = labels.get("kind") or record.get("role") or "?"
    bits = [str(record.get("role") or "?")]
    if kind != record.get("role"):
        bits.append(str(kind))
    if labels.get("quick"):
        bits.append("⚡ quick")
    model = f"{record.get('requested_model') or '?'} · {record.get('requested_effort') or '?'}"
    bits.append(f"{record.get('agent') or '—'} ({model})")
    return " · ".join(bits)


def busy_reasons(record: dict) -> list[str]:
    reasons = []
    if record.get("state") in ("launching", "initializing"):
        reasons.append("starting (native initialize and settings readback)")
    if record.get("pending_message_uuids"):
        reasons.append("accepted input awaiting its result")
    if record.get("owner") in ("host", "draining"):
        if record.get("native_session_state") in ("running", "requires_action"):
            reasons.append(f"native turn {record['native_session_state']}")
        tasks = record.get("background_tasks") or {}
        if tasks or record.get("background_unsettled"):
            reasons.append(f"{len(tasks) or 'unsettled'} background task(s)")
        elif record.get("native_wake_pending"):
            # The host's marker: a background task ended while the turn was closed, so Claude Code is about to
            # start a wake-up turn for it. Without it the gap before that turn reads as done (the false doorbell).
            reasons.append("a background task ended; its wake-up turn is due")
        if record.get("native_retry_active"):
            retry = record.get("last_retry") or {}
            reasons.append(f"native API retry {retry.get('attempt', '?')}/{retry.get('max_retries', '?')} "
                           f"({retry.get('error_status') or 'error'})")
        if record.get("native_queued_turns"):
            reasons.append(f"{record['native_queued_turns']} native queued turn(s)")
    return reasons


def released(receipt: dict) -> bool:
    return bool(receipt.get("superseded_by_recovery") or receipt.get("released_by_reconnect")
                or receipt.get("released_by_operator"))


# @beacon[
#   id=oc-claude@seat_phase,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=One host record (plus its pending recovery and last input's receipt) → starting · running · recovering · done · attention · unknown with plain reasons; unknown ownership or durability is never done; a usage-limit halt (quota_halt) is attention with one line naming the reset, the Manager recovery check when the host reports one, and the way back.,
# ]
def seat_phase(record: dict, *, recovery: dict | None = None, receipt: dict | None = None,
               now: float | None = None) -> tuple[str, list[str]]:
    """The phase `wait` and `status` report. `done` needs the newest input's receipt completed, nothing busy, no
    pending recovery or escalation, and (for a live process that publishes native state) native idle. A live session
    with no input yet is `starting` for UNSENT_GRACE seconds after launch (its `start` is still sending), then
    `attention`."""
    owner, state = record.get("owner"), record.get("state")
    if record.get("infrastructure_error") or record.get("incident_unpersisted"):
        return "unknown", [f"host durability failure ({record.get('infrastructure_error') or 'incident unpersisted'}); "
                           "inspect the host's private state. Once storage works again: "
                           f"`{HINT} resolve {record.get('execution_id')} --clear-infrastructure --note \"…\"`"]
    if owner == "unknown" or state == "ownership_unknown":
        return "unknown", ["ownership unknown: the host lost sight of this Claude process (a host restart or crash); "
                           "its work may still land in Workflowy"]
    halt = record.get("quota_halt")
    if isinstance(halt, dict) and halt.get("generation") == record.get("generation"):
        return "attention", [quota_halt_text(record, halt)]
    rid = record.get("pending_recovery")
    if rid:
        status = (recovery or {}).get("status")
        if status == "blocked":
            return "attention", [f"automatic recovery {rid} is blocked ({(recovery or {}).get('failure') or 'see incidents'})"]
        if status == "scheduled":
            return "recovering", [recovery_text(recovery, record)]
        return "recovering", [f"automatic recovery {rid} {status or 'pending'}: the host is sending one `continue`"]
    if state in ("launching", "initializing"):
        return "starting", ["native initialize and settings readback"]
    if state in ("launch_blocked", "launch_failed"):
        return "attention", [f"launch {state.replace('_', ' ')}" + (" (the native initialize did not confirm Max, or "
                             "overage/API billing was observed)" if state == "launch_blocked" else "")]
    if state == "ready" and (record.get("setting_control_pending") or record.get("settings_mismatch")) \
            and not record.get("last_input_uuid"):
        if record.get("settings_mismatch"):
            return "attention", ["the initial settings readback did not verify the requested model/effort"]
        return "starting", ["initial settings readback"]
    busy = busy_reasons(record)
    if busy:
        return "running", busy
    if owner == "draining":
        return "running", ["the process exited; its output is still draining"]
    escalations = record.get("pending_escalations") or []
    if escalations:
        return "attention", [f"{len(escalations)} pending escalation(s) awaiting a disposition (a follow-up is still "
                             "accepted and records them on its receipt)"]
    if record.get("recovery_attempt_failed"):
        return "attention", ["an automatic recovery failed; inspect, then resolve it"]
    if not record.get("last_input_uuid"):
        launched = record.get("first_launched_at")
        now = time.time() if now is None else now
        if owner == "host" and isinstance(launched, (int, float)) and now - launched < UNSENT_GRACE:
            return "starting", ["ready; its first message is being sent"]
        return "attention", ["no input was ever sent to this session"]
    if receipt is not None and not receipt.get("completed"):
        if released(receipt):
            return "attention", [f"its newest input was superseded and never completed (status {receipt.get('status')})"]
        return "attention", [f"its newest input never completed (receipt {receipt.get('status')}); effects uncertain"]
    if owner == "host" and record.get("native_state_required") and record.get("native_session_state") != "idle":
        return "running", [f"waiting for the native idle state ({record.get('native_session_state') or 'unknown'})"]
    return "done", []


def quota_halt_text(record: dict, halt: dict) -> str:
    """The one line `wait`/`info`/`sessions` give a session halted for a usage-limit lockout (host quota_halt): the
    reset, the host's Manager recovery check when the record carries `quota_recovery_check`, and the follow-up that
    resumes it on a login with quota. A five_hour-family halt says no interrupted turn is replayed and the Manager
    coordinates recovery; a weekly halt keeps the plain "nothing resumes it automatically"."""
    done = {"interrupting": "the host is ending its turn", "failed": "the host could not end its turn yet (see "
            "incidents)", "closing": "its turn ended; its process is closing", "closed": "its turn ended and its "
            "process closed"}.get(halt.get("status"), "its turn ended")
    check = record.get("quota_recovery_check")
    recovery_note = ""
    if isinstance(check, dict) and check.get("status"):
        status = str(check.get("status"))
        due = check.get("due_at")
        when = f" for {hhmm(due)}" if isinstance(due, (int, float)) else ""
        if status == "delivery_problem":
            failure = str(check.get("failure") or "").strip()
            recovery_note = (" · ⚠️ the Manager's recovery check had a delivery problem"
                             + (f": {failure[:160]}" if failure else ""))
        elif status == "sent":
            recovery_note = " · ⏰ the Manager's recovery check was sent"
        else:
            recovery_note = f" · ⏰ the Manager's recovery check is {status}{when}"
    if str(halt.get("rate_limit_type") or "").startswith("five_hour"):
        resume = "No interrupted turn is replayed; the Manager coordinates five-hour recovery."
    else:
        resume = "Nothing resumes it automatically."
    return (f"⛔ HALTED: Max usage limit reached ({halt.get('rate_limit_type') or '?'}, resets "
            f"{hhmm(halt.get('resets_at'))}); {done}. {resume}{recovery_note} Once a login with quota is active, a "
            f"follow-up (`oc.sh claude follow-up {record.get('execution_id')} --prompt \"…\" --no-wait`) continues "
            "the same conversation as a new generation on that login")


def recovery_text(recovery: dict | None, record: dict) -> str:
    recovery = recovery or {}
    due = recovery.get("due_at")
    when = f"at {hhmm(due)}" if isinstance(due, (int, float)) else "when due"
    reason = recovery.get("reason") or "?"
    info = record.get("last_rate_limit_info") or {}
    if reason == "terminal_error" and info:
        kind = info.get("rateLimitType") or "quota"
        return (f"⏸️ Max quota exhausted ({kind}), resets {hhmm(info.get('resetsAt'))}; the host continues automatically "
                f"{when} (one `continue`). To switch to a fallback instead: `{HINT} cancel {record.get('execution_id')}`")
    if reason == "crash":
        return f"the process exited mid-turn; the host resumes the same conversation and sends one `continue` {when}"
    return f"a terminal capacity/quota error: the host sends one `continue` {when} ({recovery.get('delay_basis') or '?'})"


def last_result(receipt: dict | None) -> dict | None:
    """The newest primary (user-origin) result on a receipt; background task notifications are skipped."""
    from claude_headless.supervision import user_origin
    rows = [r for r in (receipt or {}).get("results") or [] if user_origin(r.get("origin"))]
    return rows[-1] if rows else None


def wake_result(record: dict) -> dict | None:
    """The session's last word when it came from a background-task wake-up (a Conductor's doorbell turn, which has no
    input of its own, so it is on no receipt): the host's `state` answers the newest such result as
    `last_synthetic_result` (an older host kept them all as `synthetic_results`), and the newest result of all is the
    one whose cursor is the record's `last_result_cursor`."""
    last = record.get("last_result_cursor")
    if not last:
        return None
    rows = [record["last_synthetic_result"]] if isinstance(record.get("last_synthetic_result"), dict) else []
    return next((r for r in reversed(rows + list(record.get("synthetic_results") or [])) if r.get("cursor") == last),
                None)


def _recovery(host: HostConnection, eid: str, record: dict) -> dict | None:
    rid = record.get("pending_recovery")
    if not rid:
        return None
    return next((r for r in host.call("recoveries", execution_id=eid) or [] if r.get("recovery_id") == rid), None)


def _receipt(host: HostConnection, eid: str, record: dict) -> dict | None:
    mid = record.get("last_input_uuid")
    return host.call("receipt", execution_id=eid, message_uuid=mid) if mid else None


def _resolve(handle: str, host: HostConnection | None) -> tuple[HostConnection, str]:
    host = host or connect(require_seats=False)
    if not is_claude_handle(handle):
        raise SeatError(f"❌ {handle!r} is not a Claude id (cc-…); OpenCode ids go through oc_seat")
    return host, resolve_claude_id(handle, host)


# @beacon[
#   id=oc-claude@status,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Pure data view of one Claude session for wrappers (oc_seat info/socket): phase, reasons, busy, labels, provenance, model, recovery and the last result.,
# ]
def status(execution_id: str, *, host: HostConnection | None = None) -> dict:
    host, eid = _resolve(execution_id, host)
    record = host.call("state", execution_id=eid)
    recovery = _recovery(host, eid, record)
    receipt = _receipt(host, eid, record) if not seat_busy(record) else None
    phase, reasons = seat_phase(record, recovery=recovery, receipt=receipt)
    return {"execution_id": eid, "phase": phase, "reasons": reasons, "busy": seat_busy(record),
            "labels": record.get("labels") or {}, "role": record.get("role"), "agent": record.get("agent"),
            "model": record.get("requested_model"), "effort": record.get("requested_effort"),
            "program": record.get("program"), "socket": record.get("socket"), "parent": record.get("parent"),
            "owning_conductor": record.get("owning_conductor"), "generation": record.get("generation"),
            "state": record.get("state"), "owner": record.get("owner"),
            "pending_escalations": record.get("pending_escalations") or [], "recovery": recovery,
            "last_input_uuid": record.get("last_input_uuid"), "last_result": last_result(receipt),
            "record": record}


# ------------------------------------------------------------------------------------ start

def _validate_launch(*, model: str, effort: str, role: str, cwd: str, prompt: str, system_prompt: str | None,
                     context_target: float | None, labels: dict | None, fork: bool = False) -> dict | None:
    from claude_headless.host import validate_labels
    from claude_headless.launch import EFFORT_LEVELS, _validated_model
    if role not in LAUNCH_ROLES:
        raise SeatError(f"❌ role {role!r}: a script starts {'|'.join(LAUNCH_ROLES)} only (Managers are the pane's)")
    try:
        _validated_model(model)
    except ValueError as e:
        raise SeatError(f"❌ {e}") from None
    if effort not in EFFORT_LEVELS:
        raise SeatError(f"❌ effort {effort!r}: one of {'|'.join(EFFORT_LEVELS)}")
    if not isinstance(prompt, str) or not prompt.strip():
        raise SeatError("❌ the first message is empty: nothing started")
    if not isinstance(system_prompt, str) and not (fork and system_prompt is None):
        raise SeatError("❌ system_prompt must be text (a fork may omit it: its base's is used)")
    if not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise SeatError(f"❌ cwd {cwd!r} must be an existing absolute directory")
    if context_target is not None and not (isinstance(context_target, (int, float)) and 0 < context_target <= 1):
        raise SeatError(f"❌ context target {context_target}: a fraction of the window above 0 and at most 1")
    try:
        return validate_labels(labels)
    except ValueError as e:
        raise SeatError(f"❌ {e}") from None


# @beacon[
#   id=oc-claude@wait_ready,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Observes one launch until ready with initial settings read back (unverifiable warns; a positive mismatch is attention). Only fork_start also waits for native input readiness; a background Manager fork needs its startup result followed by an idle push, not an early initialize readback. Existing observation budget and progress cadence; never kills.,
# ]
def wait_ready(host: HostConnection, eid: str, *, budget: float = READY_BUDGET, out: Callable = print,
               poll: float = 0.5, sleep: Callable = time.sleep, clock: Callable = time.monotonic,
               fork_start: bool = False) -> dict:
    """Host/settings readiness; only a newly created Manager fork also needs its native startup turn finished.
    The background copy's orphan notice runs a turn before input: its result and subsequent idle PUSH distinguish
    completion from an initialize readback saying idle before that turn (or before its final state push)."""
    t0 = clock()
    shown = 0
    while True:
        record = host.call("state", execution_id=eid)
        state = record.get("state")
        if (state == "ready" and not record.get("setting_control_pending") and not record.get("settings_mismatch")
                and (not fork_start or _fork_input_ready(record))):
            if record.get("settings_unverified"):
                out(f"⚠️  {eid}: {record.get('settings_unverified_disclosure') or 'settings unverified'} — sending "
                    f"anyway (nothing names a different model or effort); `{HINT} info {eid}` shows it")
            return record
        if record.get("settings_mismatch"):
            raise SeatError(f"❌ {eid}: the initial settings readback did not verify {record.get('requested_model')} · "
                            f"{record.get('requested_effort')}; nothing was sent. Inspect `{HINT} info {eid}`, then "
                            f"`{HINT} cancel {eid}`.", EXIT_ATTENTION)
        if state not in ("launching", "initializing", "ready"):
            raise SeatError(f"❌ {eid} did not become ready (state {state}, owner {record.get('owner')}); nothing was "
                            f"sent. Inspect `{HINT} info {eid}`.", EXIT_ATTENTION)
        waited = clock() - t0
        if waited >= budget:
            if fork_start:
                raise SeatError(f"❌ {eid}: its fork startup is not ready for input after {budget:.0f}s of observation "
                                f"(host state {state}, native state {record.get('native_session_state') or 'unknown'}, "
                                f"startup turn {'open' if record.get('primary_turn_open') else 'not confirmed finished'}); "
                                "nothing was delivered")
            raise SeatError(f"❌ {eid} is not ready after {budget:.0f}s of observation (state {state}); nothing was "
                            "sent and nothing was stopped. Inspect `{0} info {1}`; `{0} cancel {1}` closes it once ready."
                            .format(HINT, eid), EXIT_UNKNOWN)
        if waited >= shown + 30:
            shown += 30
            out(f"   … {eid} still {state} after {waited:.0f}s (no deadline is applied to the session itself)")
        sleep(poll)


def _fork_input_ready(record: dict) -> bool:
    """A foreground copy has no orphan-notice turn; a background copy needs result -> native idle push."""
    if record.get("native_session_state") != "idle" or record.get("primary_turn_open") is True:
        return False
    if (record.get("labels") or {}).get("fork_mode") != "background":
        return True
    result = cursor_seq(record.get("last_result_cursor"))
    return (record.get("primary_turn_open") is False and result >= 0
            and cursor_seq(record.get("native_state_push_cursor")) > result)


def _fork_input_failure(host: HostConnection, eid: str) -> Path:
    """Failure-only count-release marker, scoped to this host socket and exact fork id; never a job/context file.
    Only a definitively unsent Manager-fork start writes it. An accepted/uncertain Send never does; the live count
    consults it only while last_input_uuid is absent, so an explicit later follow-up counts normally again."""
    return host.socket_path.parent / (host.socket_path.name + ".failed-fork-inputs") / eid


# @beacon[
#   id=oc-claude@deliver,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=One exact host Send with a caller UUID, then observes Claude's exact echo (acknowledged); a lost IPC reply reads that same receipt and never resends.,
# ]
def deliver(host: HostConnection, eid: str, generation: int, text: str, *, budget: float = 120.0,
            out: Callable = print, poll: float = 0.5, sleep: Callable = time.sleep,
            clock: Callable = time.monotonic) -> dict:
    mid = str(uuid.uuid4())
    try:
        receipt = host.call("send", execution_id=eid, generation=generation, mid=mid, submitted=text, original=text)
    except SeatError as e:
        if not e.lost:
            raise SeatError(f"{e}\n   ({eid}: nothing was delivered)", e.exit_code) from None
        try:
            receipt = host.call("receipt", execution_id=eid, message_uuid=mid)
        except SeatError:
            receipt = None
        if not receipt:
            raise SeatError(f"❌ {eid}: the Send's outcome is unknown (IPC reply lost; message {mid}). It was NOT "
                            f"repeated. Check `{HINT} info {eid}` before anything else.", EXIT_UNKNOWN) from None
    t0 = clock()
    while not (receipt.get("acknowledged") or receipt.get("completed")):
        if receipt.get("status") == "uncertain":
            raise SeatError(f"❌ {eid}: message {mid} became uncertain before Claude acknowledged it (the process "
                            f"ended); inspect `{HINT} info {eid}`", EXIT_ATTENTION)
        if clock() - t0 >= budget:
            out(f"⚠️  {eid}: message {mid} is accepted and durable but not yet acknowledged by Claude after "
                f"{budget:.0f}s (observation only; nothing resent)")
            break
        sleep(poll)
        receipt = host.call("receipt", execution_id=eid, message_uuid=mid) or receipt
    return receipt


def host_steers(host: HostConnection) -> bool:
    """The host accepts a steer mid-turn (its `steer` method); a host started before it refuses one."""
    return "steer" in ((getattr(host, "health", None) or {}).get("implemented") or [])


def host_releases_inputs(host: HostConnection | None) -> bool:
    """The host has `release_input` (a pending input no process can answer is released with a disposition); a host
    started before it keeps the older refusals until Dan's next guarded host restart."""
    return "release_input" in ((getattr(host, "health", None) or {}).get("implemented") or [])


def release_unanswerable(host: HostConnection, eid: str, disposition: str, out: Callable,
                         recovery_id: str | None = None) -> dict:
    """Release every pending input no process of this seat can still answer (its process ended, or an earlier one
    accepted it), never replayed; inputs a live turn may still answer are held. Also dispositions the recovery that
    covered them. → the host's answer ({} on an older host)."""
    if not host_releases_inputs(host):
        return {}
    answer = host.call("release_input", execution_id=eid, disposition=disposition,
                       **({"recovery_id": recovery_id} if recovery_id else {})) or {}
    if answer.get("released"):
        out(f"⚠️  {eid}: {len(answer['released'])} uncertain input(s) no process can answer any more are released "
            "(never replayed; their effects are uncertain, so the next message should ask it to inspect)")
    for row in answer.get("held") or []:
        out(f"   held: input {row.get('message_uuid')}: {row.get('reason')}")
    return answer


def host_records_escalations(host: HostConnection | None) -> bool:
    """The host records a seat's open escalations on the Send's receipt instead of refusing the Send; a host started
    before that change (until Dan's next guarded host restart) still refuses."""
    return ((getattr(host, "health", None) or {}).get("escalations_at_send") == "recorded")


# ------------------------------------------------------------------------------------ switching model / effort

SWITCH_AUTHORITY = "seat-switch:"
"""claude_headless.controls.SEAT_SWITCH_AUTHORITY: the host lets a seat's setters run only under this prefix."""


def host_forks(host: HostConnection | None) -> bool:
    """Whether the host's `launch` takes `fork_from` (health `seat_fork`, from the host build that added native
    forks; an older one activates it at Dan's guarded host restart)."""
    return bool(host is not None and (host.health or {}).get("seat_fork"))


def host_manager_forks(host: HostConnection | None) -> bool:
    """Whether the host's `launch` takes `fork_self_binding` (health `manager_fork`): a Manager forking itself into a
    worker from inside its own turn (oc_seat quick --fork-self); an older host activates it at the guarded restart."""
    return bool(host is not None and (host.health or {}).get("manager_fork"))


FORK_HIT = 0.95
"""A fork's first call HITs the cache when it read at least this share of the base's last occupancy."""


def _kt(n: int | float) -> str:
    return f"{n / 1000:.1f} KT"


# @beacon[
#   id=oc-claude@fork_cache,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=A fork's first-turn cache verdict from its host record: HIT when its first completed call's cache read is ≥ 95% of the base's last occupancy (input + cache read + cache write of the base's last call when it was forked), else MISS; not yet / unknown when either call is missing.,
# ]
def fork_cache(record: dict) -> dict | None:
    """None for a session that is not a fork; else {"verdict": HIT · MISS · PENDING · UNKNOWN, "read", "wrote",
    "base", "line"}. The base's occupancy is the same count basis as context_status (input + cache read + cache
    creation of one call); the fork's first call reads that prefix back when the cache is warm (Part A: 69.7 of
    69.8 KT) and writes it again when cold or on another model."""
    base_id = record.get("forked_from")
    if not base_id:
        return None
    from claude_headless.context import INPUT_KEYS
    base = ((record.get("fork_base_call") or {}).get("usage")) or {}
    first = ((record.get("fork_first_call") or {}).get("usage")) or {}
    occupancy = sum(int(base.get(k) or 0) for k in INPUT_KEYS)
    head = f"🍴 forked from {base_id} · first-turn cache"
    if not first:
        return {"verdict": "PENDING", "line": f"{head}: not measured yet (no completed model call)"}
    read, wrote = int(first.get("cache_read_input_tokens") or 0), int(first.get("cache_creation_input_tokens") or 0)
    if not occupancy:
        return {"verdict": "UNKNOWN", "read": read, "wrote": wrote,
                "line": f"{head} UNKNOWN: read {_kt(read)}, wrote {_kt(wrote)} (the base's last call was not recorded)"}
    verdict = "HIT" if read >= FORK_HIT * occupancy else "MISS"
    why = "" if verdict == "HIT" else " (the base was written again: cold after an hour, or another model)"
    return {"verdict": verdict, "read": read, "wrote": wrote, "base": occupancy,
            "line": f"{head} {verdict}: read {_kt(read)} of the base's {_kt(occupancy)} ({read / occupancy:.0%}), "
                    f"wrote {_kt(wrote)}{why}"}


def host_switches_settings(host: HostConnection | None) -> bool:
    """The host accepts set_model/apply_effort on a conductor/worker/helper under a seat-switch authority; a host
    started before that change refuses until Dan's next guarded host restart."""
    return bool((getattr(host, "health", None) or {}).get("seat_settings_switch"))


def switch_choice(model: str | None = None, variant: str | None = None) -> dict:
    """`--model anthropic/…[#level]` and/or `--variant level` → {"model": host name | None, "effort": level | None}.
    Both None = no switch asked. A #level that differs from --variant is a conflict."""
    from claude_headless.launch import EFFORT_LEVELS, _validated_model
    from oc_backend import model_parts
    name, effort = None, variant or None
    if model:
        parts = model_parts(model)
        if parts["provider"] != "anthropic":
            raise SeatError(f"❌ --model {model}: a Claude session switches to an anthropic/* model only")
        if variant and parts["variant"] and parts["variant"] != variant:
            raise SeatError("❌ --model #thinking conflicts with --variant; choose one thinking level")
        try:
            name = _validated_model(parts["model"])
        except ValueError:
            raise SeatError(f"❌ {parts['model']}: the Claude host admits haiku/sonnet/opus/fable or "
                            "claude-(haiku|sonnet|opus|fable)-N names only") from None
        effort = effort or parts["variant"]
    if effort is not None and effort not in EFFORT_LEVELS:
        raise SeatError(f"❌ thinking level {effort!r} is not a Claude Code effort ({'|'.join(EFFORT_LEVELS)})")
    return {"model": name, "effort": effort}


def current_settings(record: dict) -> dict:
    """The session's model and effort now: the last verified native readback, else the requested launch choice."""
    eff = record.get("controls_effective") if isinstance(record.get("controls_effective"), dict) else {}
    return {"model": record.get("actual_model") or eff.get("model") or record.get("requested_model"),
            "effort": record.get("actual_effort") or eff.get("effort") or record.get("requested_effort")}


def settings_line(record: dict) -> str:
    """'model: claude-opus-5-5 · xhigh (native readback) · switched from launch claude-fable-5-1 · xhigh' for info."""
    now = current_settings(record)
    verified = "native readback" if record.get("actual_model") and record.get("actual_effort") else "requested"
    launch = record.get("launch") if isinstance(record.get("launch"), dict) else {}
    first = (launch.get("model"), launch.get("effort"))
    switched = (f" · switched from launch {first[0]} · {first[1]}"
                if all(first) and first != (record.get("requested_model"), record.get("requested_effort")) else "")
    return f"model: {now['model']} · {now['effort']} ({verified}){switched}"


def known_window(host: HostConnection, model: str, record: dict | None = None) -> int | None:
    """The native context window this host has measured for `model` (any session's `context_windows`), or None."""
    from claude_headless.controls import compare_model
    rows = [record or {}]
    try:
        rows += host.call("list", all=True) or []
    except SeatError:
        pass
    for row in rows:
        for name, window in ((row or {}).get("context_windows") or {}).items():
            if type(window) is int and window > 0 and compare_model(model, name) == "match":
                return window
    return None


# @beacon[
#   id=oc-claude@plan_switch,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Checks a requested model/effort switch BEFORE anything is resumed or sent: what differs from the session's verified settings, a host that supports seat switches, a non-base role, and the current occupancy against the target model's measured window (refused when it would not fit).,
# ]
def plan_switch(host: HostConnection, eid: str, record: dict, choice: dict, *, ctx: dict | None = None,
                out: Callable = print) -> dict:
    """→ {"model"?: …, "effort"?: …} holding only what must change ({} = nothing to switch). Raises SeatError (nothing
    resumed, nothing sent) when the host predates seat switches, the role cannot switch, or the session's current
    occupancy exceeds the target model's window."""
    from claude_headless.controls import compare_effort, compare_model
    if not (choice or {}).get("model") and not (choice or {}).get("effort"):
        return {}
    now = current_settings(record)
    changes = {}
    if choice.get("model") and compare_model(choice["model"], now["model"]) != "match":
        changes["model"] = choice["model"]
    if choice.get("effort") and compare_effort(choice["effort"], now["effort"]) != "match":
        changes["effort"] = choice["effort"]
    if not changes:
        out(f"🔀 {eid}: already on {now['model']} · {now['effort']}; no switch needed")
        return {}
    if record.get("role") not in ("conductor", "worker", "helper"):
        raise SeatError(f"❌ {eid}: a {record.get('role')!r} session cannot switch model or thinking level; nothing sent")
    if not host_switches_settings(host):
        raise SeatError(f"❌ {eid}: this Claude host was started before seat model/level switches; they go live at "
                        "Dan's next guarded host restart. Nothing resumed, nothing sent (re-run without --model/"
                        "--variant to follow up on the current model).", EXIT_ERROR)
    if "model" in changes:
        ctx = ctx if ctx is not None else context_report(host, eid, record)
        used = (ctx or {}).get("occupancy_tokens") if (ctx or {}).get("ok") else None
        window = known_window(host, changes["model"], record)
        if isinstance(used, int) and window and used >= window:
            raise SeatError(f"⛔ {eid}: its context ({used:,} tokens) does not fit {changes['model']}'s window "
                            f"({window:,}); nothing sent. Start a fresh seat on that model with the continuation.",
                            EXIT_CONTEXT)
        if not window:
            out(f"   (no {changes['model']} window measured on this host yet; the occupancy check is skipped)")
    target = {**now, **changes}
    out(f"🔀 {eid}: switching {now['model']} · {now['effort']} → {target['model']} · {target['effort']} (same "
        "conversation; the first call on a new model re-writes the prompt cache once)")
    return changes


# @beacon[
#   id=oc-claude@apply_switch,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Applies a planned switch on a READY idle seat through the host's correlated setters (set_model, then apply_effort, seat-switch: authority), each verified by the native get_settings readback; prints the readback, and refuses to send on anything but applied (a mismatch or uncertain setter leaves Send blocked).,
# ]
def apply_switch(host: HostConnection, eid: str, record: dict, changes: dict, *, why: str = "follow-up",
                 out: Callable = print) -> dict:
    """→ the fresh host record once every change is verified by readback. Raises SeatError otherwise (nothing sent);
    each setter's readback also checks that the dimension it did not name stayed as it was."""
    for method, key in (("set_model", "model"), ("apply_effort", "effort")):
        if key not in changes:
            continue
        try:
            rec = host.call(method, execution_id=eid, generation=record.get("generation"),
                            authority=f"{SWITCH_AUTHORITY}{why}", **{key: changes[key]})
        except SeatError as e:
            raise SeatError(f"❌ {eid}: {method} {changes[key]} failed: {str(e).splitlines()[0][:300]}. Nothing sent; "
                            f"inspect `{HINT} info {eid}`.", EXIT_ATTENTION if e.lost else e.exit_code) from None
        state = (rec or {}).get("state")
        read = (rec or {}).get("readback") or {}
        if state not in ("applied", "no_op_applied", "applied_unverified"):
            why_not = (rec or {}).get("disclosure") or "no disclosure"
            raise SeatError(f"❌ {eid}: {method} {changes[key]} is {state} ({why_not}); native readback {read.get('model')} · {read.get('effort')}. Nothing sent; "
                            f"inspect `{HINT} info {eid}`.", EXIT_ATTENTION)
        note = " (UNVERIFIED: the readback names nothing different)" if state == "applied_unverified" else ""
        out(f"   ✓ {method} {changes[key]}: native readback applied.model={read.get('model')} · "
            f"applied.effort={read.get('effort')}{note}")
        record = host.call("state", execution_id=eid)
    out(f"🔀 {eid}: now {record.get('requested_model')} · {record.get('requested_effort')} (verified by readback)")
    return record


# @beacon[
#   id=oc-claude@steer,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=One host steer with a caller UUID, accepted while the session's turn runs: prints queued (Claude Code holds it), then delivered (its echo: the model has it, at its next tool step); a bounded watch, never a resend; a lost IPC reply reads the same receipt.,
# ]
def steer(host: HostConnection, eid: str, generation: int, text: str, *, budget: float = 120.0,
          out: Callable = print, poll: float = 0.5, sleep: Callable = time.sleep,
          clock: Callable = time.monotonic) -> dict:
    """→ the receipt as last observed: `delivered` true once Claude echoed it, else still queued after `budget`
    seconds of watching (it lands when the command in flight finishes). Raises SeatError when the host refused it,
    or when it became uncertain (its process ended) or was cancelled (an interrupt dropped it) before delivery."""
    mid = str(uuid.uuid4())
    try:
        receipt = host.call("steer", execution_id=eid, generation=generation, mid=mid, submitted=text, original=text)
    except SeatError as e:
        if not e.lost:
            raise SeatError(f"{e}\n   ({eid}: nothing was delivered)", e.exit_code) from None
        try:
            receipt = host.call("receipt", execution_id=eid, message_uuid=mid)
        except SeatError:
            receipt = None
        if not receipt:
            raise SeatError(f"❌ {eid}: the steer's outcome is unknown (IPC reply lost; message {mid}). It was NOT "
                            f"repeated. Check `{HINT} info {eid}` before anything else.", EXIT_UNKNOWN) from None
    t0 = clock()
    said_queued = False
    while True:
        delivered = bool(receipt.get("delivered") or receipt.get("acknowledged") or receipt.get("completed"))
        if receipt.get("queued") and not said_queued:
            out(f"⏳ queued: Claude Code holds message {mid} for its next tool step")
            said_queued = True
        if delivered:
            out(f"✅ delivered: Claude received message {mid}" + (" (mid-turn)" if receipt.get("turn_open_at_send")
                                                                   else ""))
            return receipt
        if receipt.get("status") == "uncertain":
            raise SeatError(f"❌ {eid}: steer {mid} became uncertain before delivery (the process ended); it was NOT "
                            f"resent. Inspect `{HINT} info {eid}`", EXIT_ATTENTION)
        if receipt.get("status") == "cancelled":
            raise SeatError(f"❌ {eid}: steer {mid} was dropped from Claude's queue before the model saw it (an "
                            f"interrupt cancelled it); it was NOT resent", EXIT_ATTENTION)
        if clock() - t0 >= budget:
            return receipt
        sleep(poll)
        receipt = host.call("receipt", execution_id=eid, message_uuid=mid) or receipt


def new_execution_id() -> str:
    """A fresh `cc-<uuid>` for start(execution_id=…): a quick worker's first message names its own id (its helpers
    get it as --parent-seat), so the caller composes that message before the launch."""
    return "cc-" + str(uuid.uuid4())


# @beacon[
#   id=oc-claude@_start_manager_fork_input,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=First input of the one call's newly created Manager fork only: wait through native startup within one ready budget; complete one own-turn refusal once after idle only when Send confirmed nothing delivered. Never repeats a possibly accepted Send or a launch; a definite unsent failure releases this exact fork's live-count slot with a failure-only marker and reports its own state, never the Manager's.,
# ]
def _start_manager_fork_input(host: HostConnection, eid: str, prompt: str, *, ready_budget: float,
                              ack_budget: float, out: Callable, poll: float, sleep: Callable,
                              clock: Callable) -> tuple[dict, dict]:
    """Scoped startup delivery, not a general Send retry. A lost/uncertain Send keeps deliver's exact receipt rule.
    Only host-confirmed pre-admission own_turn_busy refusals can be sent once more; no receipt existed for them.
    Readiness and any post-refusal wait share READY_BUDGET, with no fixed delay. A definite unsent failure writes
    an exact-id count-release marker before reporting failure: close_input cannot close a still-running startup.
    The marker contains no prompt/job and is ignored as soon as any input is accepted."""
    deadline = clock() + ready_budget
    may_have_delivered = False
    undelivered = f"\n   ({eid}: nothing was delivered)"

    def ready():
        return wait_ready(host, eid, budget=max(0.0, deadline - clock()), out=out, poll=poll, sleep=sleep,
                          clock=clock, fork_start=True)

    def send(record):
        nonlocal may_have_delivered
        may_have_delivered = True
        try:
            return deliver(host, eid, record["generation"], prompt, budget=ack_budget, out=out, poll=poll,
                           sleep=sleep, clock=clock)
        except SeatError as error:
            # These are pre-admission RuntimeError refusals in NativeSession.send, before a receipt or stdin write.
            # Other errors (including journal/transport errors after acceptance) do not prove non-delivery.
            if (not error.lost and str(error).endswith(undelivered)
                    and str(error).startswith("❌ Claude host send: RuntimeError: ")
                    and any(str(error).splitlines()[0] == "❌ Claude host send: RuntimeError: " + reason for reason in (
                        "busy: the Manager is working on its own turn; wait or interrupt",
                        "busy: the Manager is waiting for a permission answer; wait or interrupt",
                        "busy: the Manager's current state is not known yet; wait or interrupt",
                        "native session not initialized and Max-confirmed/ready",
                        "native stdout EOF; no observed reply channel, input withheld",
                        "busy: native retry unsettled",
                        "durability unavailable: Send refused",
                        "settings pending/unverified or effective choice mismatch; Send withheld "
                        "(press Re-check settings, or apply the model/thinking again)",
                    ))):
                may_have_delivered = False
            raise

    try:
        record = ready()
        try:
            receipt = send(record)
        except SeatError as error:
            own_turn_refusals = (
                "the Manager is working on its own turn",
                "the Manager is waiting for a permission answer",
                "the Manager's current state is not known yet",
            )
            if (may_have_delivered or str(error).splitlines()[0] not in {
                    f"❌ Claude host send: RuntimeError: busy: {reason}; wait or interrupt"
                    for reason in own_turn_refusals} or clock() >= deadline):
                raise
            record = ready()
            if clock() >= deadline:
                raise error
            receipt = send(record)  # exactly one completion of a confirmed-undelivered Send, never a new launch
        return record, receipt
    except SeatError as error:
        if may_have_delivered:
            raise  # accepted/uncertain outcomes are never resent, closed or relabelled as undelivered
        reason = str(error).split(undelivered)[0].replace("the Manager", "the fork")
        marker = _fork_input_failure(host, eid)
        try:
            marker.parent.mkdir(mode=0o700, exist_ok=True)
            marker.touch(mode=0o600, exist_ok=True)
        except OSError as marker_error:
            raise SeatError(f"{reason}\n   ({eid}: nothing was delivered; its live-fork slot could not be released: "
                            f"{marker_error}. Inspect `{HINT} info {eid}`.)", EXIT_ERROR) from None
        raise SeatError(f"{reason}\n   ({eid}: nothing was delivered; its live-fork slot was released)", EXIT_ERROR) from None


# @beacon[
#   id=oc-claude@start,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Start one Claude session: validate; under the start lock run precheck then quota/cap and launch; announce at once, wait ready, Send and acknowledge. Only fork_self_binding waits through the Manager fork's native startup, completes one confirmed-undelivered own-turn refusal once and releases a definite unsent failure's live-count slot by exact id. Other starts keep their original path. A real five-hour quota refusal durably records its Manager recovery check.,
# ]
def start(*, model: str, effort: str, system_prompt: str | None, prompt: str, role: str, cwd: str,
          socket: str | None = None, program: str | None = None, parent: str | None = None, agent: str | None = None,
          owning_conductor: str | None = None, context_target: float | None = None, labels: dict | None = None,
          execution_id: str | None = None, host: HostConnection | None = None, settings: dict | None = None,
          enforce_quota: bool = True, on_id: Callable[[str, dict], None] | None = None, out: Callable = print,
          ready_budget: float = READY_BUDGET, ack_budget: float = 120.0, poll: float = 0.5,
          sleep: Callable = time.sleep, clock: Callable = time.monotonic, fork_from: str | None = None,
          fork_self_binding: str | None = None, precheck: Callable[[HostConnection], None] | None = None) -> dict:
    """→ {"execution_id", "generation", "message_uuid", "acknowledged", "receipt"}. Raises SeatError (exit codes in
    oc_backend). `execution_id` (from new_execution_id) preallocates the id when the system prompt or the first
    message must name it; omitted, one is allocated here. The id is announced (on_id, else one 🚀 line) as soon as
    the host has registered it, before the first message is sent, so a later failure still leaves it on screen.
    `fork_from` (a base's cc- id) starts a FORK of that base: a new session holding its whole conversation, in the
    base's cwd and role, from its stored snapshot (system_prompt None = the base's; the host checks the rest). The
    quota guard and the seat cap apply exactly as to any start; a real five-hour quota refusal is recorded with the
    host's durable Manager recovery check (program/socket carried along) before the unchanged refusal is raised.
    `fork_self_binding` (with fork_from = the caller's own Manager id) is its CC_CONTEXT_BINDING: the proof that a
    Manager forks itself from inside its own turn (oc_seat quick --fork-self); never logged or printed. `precheck`
    (called with the host inside the start lock, before the quota guard) is the caller's own admission check counted
    where a burst cannot overrun it (oc_seat's live Manager forks and one fork per call); a precheck that raises
    leaves nothing launched. Only `fork_self_binding` also waits for native startup completion and may finish one
    confirmed-undelivered own-turn refusal once within the same ready budget; definite unsent failures release
    this exact fork's live-count slot. An accepted or uncertain Send never takes that path."""
    labels = _validate_launch(model=model, effort=effort, role=role, cwd=cwd, prompt=prompt,
                              system_prompt=system_prompt, context_target=context_target, labels=labels,
                              fork=fork_from is not None)
    eid = new_execution_id() if execution_id is None else execution_id
    sid = eid[3:] if isinstance(eid, str) and eid.startswith("cc-") else ""
    if not _CANONICAL.fullmatch(sid):
        raise SeatError(f"❌ execution_id {execution_id!r}: a fresh cc-<canonical uuid> (oc_claude.new_execution_id)")
    host = host or connect()
    if fork_from is not None:
        if not host_forks(host):
            raise SeatError("❌ this Claude host predates native forks (its health has no `seat_fork`): they activate "
                            "at Dan's next guarded host restart. Nothing started.", EXIT_NO_HOST)
        if fork_self_binding is not None and not host_manager_forks(host):
            raise SeatError("❌ this Claude host predates Manager forks (its health has no `manager_fork`): they "
                            "activate at Dan's next guarded host restart. Nothing started.", EXIT_NO_HOST)
        fork_from = resolve_claude_id(fork_from, host)
    if parent and is_claude_handle(parent):
        parent = resolve_claude_id(parent, host)
    with start_lock(host):
        if precheck is not None:
            precheck(host)
        if enforce_quota:
            quota_guard(host, role=role, settings=settings, out=out,
                        actual_launch={"program": program, "socket_handle": socket})
        try:
            record = host.call("launch", native_uuid=sid, cwd=cwd, model=model, effort=effort,
                               system_prompt=system_prompt, role=role, agent=agent, program=program,
                               socket_handle=socket, parent=parent, owning_conductor=owning_conductor,
                               context_target=context_target, labels=labels,
                               **({"fork_from": fork_from} if fork_from is not None else {}),
                               **({"fork_self_binding": fork_self_binding} if fork_self_binding is not None else {}))
        except SeatError as e:
            if e.lost:
                raise SeatError(f"❌ {eid}: the launch's outcome is unknown (IPC reply lost). Check `{HINT} info {eid}`; "
                                "never relaunch the same id.", EXIT_UNKNOWN) from None
            raise SeatError(f"{e}\n   (nothing started)", e.exit_code) from None
    if on_id:
        on_id(eid, record)
    else:
        out(f"🚀 CLAUDE {role.upper()} {eid} · {agent or '—'} ({model} · {effort}) · socket {socket or '—'}"
            + (f" · parent {parent}" if parent else "") + (f" · fork of {fork_from}" if fork_from else ""))
    if fork_self_binding is not None:
        ready, receipt = _start_manager_fork_input(host, eid, prompt, ready_budget=ready_budget,
                                                  ack_budget=ack_budget, out=out, poll=poll, sleep=sleep, clock=clock)
    else:
        ready = wait_ready(host, eid, budget=ready_budget, out=out, poll=poll, sleep=sleep, clock=clock)
        receipt = deliver(host, eid, ready["generation"], prompt, budget=ack_budget, out=out, poll=poll, sleep=sleep,
                          clock=clock)
    return {"execution_id": eid, "generation": ready["generation"], "message_uuid": receipt["message_uuid"],
            "acknowledged": bool(receipt.get("acknowledged") or receipt.get("completed")), "receipt": receipt}


# ------------------------------------------------------------------------------------ wait / info

def _escalation_lines(host: HostConnection, eid: str, record: dict) -> list[str]:
    lines = []
    for iid in record.get("pending_escalations") or []:
        try:
            body = host.call("incident", execution_id=eid, incident_id=iid) or {}
        except SeatError:
            body = {}
        detail = body.get("detail") or {}
        what = detail.get("result") or detail.get("reason") or detail.get("failure") or json.dumps(detail)[:200]
        target = (body.get("escalation") or {}).get("target") or "?"
        lines.append(f"   ⚠️  escalation {iid[:12]}: {body.get('category', '?')} · {body.get('handler', '?')} · "
                     f"for {target} · {str(what)[:240]}")
        lines.append(f"       after inspecting: `{HINT} resolve {eid} --incident {iid[:12]} --note \"…\"`")
    return lines


def _result_lines(result: dict | None, tail: int, record: dict | None = None) -> list[str]:
    """The newest input's result; then, when the session spoke last from a background-task wake-up (a Conductor
    collecting its worker), that last word too, labelled."""
    lines = _one_result_lines(result, tail)
    wake = wake_result(record or {})
    if wake:
        lines += [RULE, "   after a background-task wake-up (its last word):"] + [
            line for line in _one_result_lines(wake, tail) if line != RULE]
    return lines


def _one_result_lines(result: dict | None, tail: int) -> list[str]:
    if not result:
        return []
    lines = []
    if result.get("terminal_error"):
        lines.append(f"   ❌ ended on an error: {result.get('category')} ({result.get('handler')}) · "
                     f"{result.get('terminal_reason') or result.get('subtype')}")
    denials = result.get("permission_denials") or []
    if denials:
        lines.append(f"   ⚠️  {len(denials)} permission denial(s) on this turn (receipts, not a task verdict)")
    text = str(result.get("result") or "").strip()
    if text:
        lines += [RULE, text[-tail:]]
    return lines


def exact_id(handle: str, host: HostConnection | None = None) -> str:
    """The exact `cc-<uuid>` `wait` observes: a canonical id as given (no host call), else the cc- router's match."""
    h = (handle or "").strip().strip("`'\"")
    body = (h[3:] if h.startswith("cc-") else h).lower()
    return "cc-" + body if _CANONICAL.fullmatch(body) else _resolve(handle, host)[1]


def _quota(host: HostConnection) -> str:
    try:
        return quota_line(quota_snapshot(host.call("list")))
    except SeatError:
        return "🔋 Max quota: unavailable"


# @beacon[
#   id=oc-claude@wait,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=`wait cc-…`: 0 done (result text, escalations, quota, a fork's first-turn cache line) · 2 still running · 4 needs attention · 5 UNKNOWN (ownership/host lost; never done) · 9 a Manager's doorbell handed over (the superseded check, first on every lap); prints recovery/quota lines as the phase changes.,
# ]
def wait(execution_id: str, *, max_s: float = 540, host: HostConnection | None = None, out: Callable = print,
         poll: float = 5.0, tail: int = 1500, sleep: Callable = time.sleep, clock: Callable = time.monotonic,
         superseded: Callable[[], str | None] | None = None) -> int:
    """Observe until done / attention, or until max_s; exit codes as above. A host that cannot be reached is UNKNOWN,
    observed again until max_s (a restarting host reconciles its records), never "done". `superseded` (a Manager's
    doorbell, oc_doorbell) returns the handed-over line once a newer Manager has taken it: printed, exit 9."""
    h = (execution_id or "").strip().strip("`'\"")
    body = (h[3:] if h.startswith("cc-") else h).lower()
    if _CANONICAL.fullmatch(body):
        eid = "cc-" + body
    else:
        host, eid = _resolve(execution_id, host)
    t0, seen = clock(), None
    while True:
        gone = superseded() if superseded else None
        if gone:
            out(gone)
            return EXIT_HANDED_OVER
        record = None
        try:
            host = host or connect(require_seats=False)
            record = host.call("state", execution_id=eid)
            recovery = _recovery(host, eid, record)
            receipt = _receipt(host, eid, record) if not seat_busy(record) else None
            phase, reasons = seat_phase(record, recovery=recovery, receipt=receipt)
        except SeatError as e:
            if "unknown exact execution ID" in str(e):
                raise SeatError(f"❌ no Claude session {eid} on the host. Copy the id from the tool output.") from None
            phase, reasons = "unknown", [f"the Claude host is unreachable: {str(e).splitlines()[0][:200]}"]
            if e.exit_code == EXIT_NO_HOST:
                host = None
        if phase == "done":
            result = last_result(receipt)
            out(f"✅ {eid} done · {describe(record)} · socket {record.get('socket') or '—'} · generation "
                f"{record.get('generation')} · Max subscription (no per-call charge)")
            out("   " + _quota(host))
            if fork := fork_cache(record):
                out("   " + fork["line"])
            for line in _escalation_lines(host, eid, record) + _result_lines(result, tail, record):
                out(line)
            return EXIT_DONE
        if phase == "attention":
            out(f"⚠️  {eid} NEEDS ATTENTION · {describe(record)} · socket {record.get('socket') or '—'}")
            for reason in reasons:
                out(f"   - {reason}")
            if record.get("pending_recovery") and (recovery or {}).get("status") == "blocked":
                out(f"   after inspecting: `{HINT} resolve {eid} --recovery {record['pending_recovery']} --note \"…\"`")
            for line in _escalation_lines(host, eid, record) + _result_lines(last_result(receipt), tail, record):
                out(line)
            return EXIT_ATTENTION
        key = (phase, reasons[0] if phase in ("recovering", "unknown") else None)
        if key != seen:
            seen = key
            icon = {"starting": "🛫", "running": "⚙️ ", "recovering": "⏸️ ", "unknown": "❓"}.get(phase, "·")
            out(f"{icon} {eid} {phase}: {'; '.join(reasons)}")
            if phase == "recovering" and host is not None:
                out("   " + _quota(host))
        if clock() - t0 >= max_s:
            if phase == "unknown":
                out(f"❓ {eid} UNKNOWN after {max_s:.0f}s: {'; '.join(reasons)}. Never treat it as done; read its socket "
                    f"for landed work, and call `{HINT} wait {eid} --max {max_s:.0f}` again.")
                return EXIT_UNKNOWN
            out(f"⏳ {eid} still {phase} after {max_s:.0f}s — call `{HINT} wait {eid} --max {max_s:.0f}` again")
            return EXIT_RUNNING
        sleep(max(0.0, min(poll, max_s - (clock() - t0))))


def _context_line(report_: dict) -> str:
    if not report_ or not report_.get("ok"):
        return f"context: UNKNOWN ({(report_ or {}).get('reason') or 'no reading'})"
    target = report_.get("target") or {}
    window = report_.get("window_tokens")
    used = report_.get("occupancy_tokens")
    pct = f" ({report_['fraction_used']:.0%})" if isinstance(report_.get("fraction_used"), float) else ""
    tgt = (f" · target {target.get('fraction'):.0%} ({target.get('source')}){' CROSSED' if target.get('crossed') else ''}"
           if target else "")
    return f"context: {used:,} / {window:,}{pct}{tgt} · {report_.get('label') or ''}".rstrip(" ·") \
        if isinstance(window, int) else f"context: {used:,} tokens (window unknown) · {report_.get('label') or ''}"


def context_report(host: HostConnection, eid: str, record: dict) -> dict:
    try:
        return host.call("get_context_usage", execution_id=eid, generation=record.get("generation"))
    except SeatError as e:
        return {"ok": False, "reason": str(e).splitlines()[0][:200]}


def context_of(execution_id: str, *, host: HostConnection | None = None) -> dict:
    """The seat's context reading (input of its previous completed model call, the role/launch target, advice) plus a
    one-line `line`: what follow_up checks first, for a wrapper's `resume --check-only`. ok False = UNKNOWN, not 0."""
    host, eid = _resolve(execution_id, host)
    report_ = context_report(host, eid, host.call("state", execution_id=eid))
    return {**report_, "line": _context_line(report_)}


# @beacon[
#   id=oc-claude@info,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=`info cc-…`: phase, labels/provenance (a fork's base and its first-turn cache HIT/MISS), model and effort, previous-call context, quota, escalations/recovery and the last result — the replacement for reading host records by hand.,
# ]
def info(execution_id: str, *, tail: int = 1500, host: HostConnection | None = None, out: Callable = print) -> dict:
    host, eid = _resolve(execution_id, host)
    s = status(eid, host=host)
    record = s["record"]
    labels = s["labels"]
    out(f"ℹ️  {eid} · {s['phase'].upper()} · {labels.get('title') or describe(record)}")
    out(f"   {describe(record)} · generation {record.get('generation')} · state {record.get('state')} · owner "
        f"{record.get('owner')}")
    out(f"   socket={record.get('socket') or '—'} · program={record.get('program') or '—'} · parent="
        f"{record.get('parent') or '—'} · conductor={record.get('owning_conductor') or '—'} · launched "
        f"{hhmm(record.get('first_launched_at'))} · last input {hhmm(record.get('last_input_at'))}")
    if fork := fork_cache(record):
        out("   " + fork["line"])
    extra = {k: v for k, v in labels.items() if k not in ("title",)}
    if extra:
        out("   labels: " + ", ".join(f"{k}={v}" for k, v in sorted(extra.items())))
    for reason in s["reasons"]:
        out(f"   - {reason}")
    out("   " + settings_line(record))
    if record.get("settings_unverified"):
        out(f"   ⚠️  {record.get('settings_unverified_disclosure') or 'settings unverified'}")
    if labels.get("prompt_drift"):
        out(f"   ⚠️  DOCTRINE DRIFT at launch: these prompt overrides were skipped (their passages kept the OpenCode "
            f"wording): {labels['prompt_drift']}")
    out("   " + _context_line(context_report(host, eid, record)))
    out("   " + _quota(host))
    for line in _escalation_lines(host, eid, record) + _result_lines(s["last_result"], tail, record):
        out(line)
    return s


# ------------------------------------------------------------------------------------ follow-up

def _await_gone(host: HostConnection, eid: str, *, budget: float, sleep: Callable, clock: Callable) -> dict:
    t0 = clock()
    while True:
        record = host.call("state", execution_id=eid)
        if record.get("owner") == "gone":
            return record
        if record.get("owner") == "unknown" or clock() - t0 >= budget:
            raise SeatError(f"❌ {eid}: its process is still closing (state {record.get('state')}, owner "
                            f"{record.get('owner')}); try the follow-up again shortly", EXIT_RUNNING)
        sleep(0.5)


# @beacon[
#   id=oc-claude@follow_up,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=A follow-up to an IDLE Claude seat: refuses busy/unknown and a recovery already sending; inputs its ended process can never answer are released (release_input, never replayed), a still-scheduled or blocked recovery is superseded, open escalations go on the receipt; context check (exit 3 at/past target), quota lines only warn; a process blocked at launch or halted for a usage limit is closed, a gone one resumed (same UUID, current login), then send + acknowledged.,
# ]
def follow_up(execution_id: str, prompt: str, *, host: HostConnection | None = None, force: bool = False,
              check_context: bool = True, settings: dict | None = None, enforce_quota: bool = True,
              switch: dict | None = None, switch_why: str = "oc_seat resume", out: Callable = print, ready_budget: float = READY_BUDGET, ack_budget: float = 120.0, poll: float = 0.5,
              sleep: Callable = time.sleep, clock: Callable = time.monotonic) -> dict:
    """→ {"execution_id", "generation", "message_uuid", "acknowledged", "resumed", "context", "superseded_recovery"}.
    A still-scheduled automatic recovery is superseded (the follow-up replaces its `continue`); one that is claimed or
    accepted is sending right now, so the follow-up waits for it (exit 2). Open escalations never refuse it: the
    Conductor writing is their addressee, and the host records them on the receipt. `switch` (switch_choice:
    {"model", "effort"}) switches the same conversation's model and/or thinking level first: planned before anything
    is resumed (plan_switch), applied once the seat is ready and verified by readback (apply_switch), then sent."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise SeatError("❌ the follow-up message is empty")
    host = host or connect()
    host, eid = _resolve(execution_id, host)
    record = host.call("state", execution_id=eid)
    if record.get("role") == "Manager":
        raise SeatError(f"❌ {eid} is a Manager conversation: Dan writes to it through the pane, never a script")
    if record.get("owner") == "unknown" or record.get("state") == "ownership_unknown":
        raise SeatError(f"❌ {eid}: ownership unknown; no second writer. Wait for the host to reconcile it.", EXIT_UNKNOWN)
    rid = record.get("pending_recovery")
    recovery = _recovery(host, eid, record) if rid else None
    if rid and (recovery or {}).get("status") in ("claimed", "accepted"):
        raise SeatError(f"❌ {eid}: automatic recovery {rid} is sending its `continue` right now; `{HINT} wait {eid}`, "
                        "then follow up", EXIT_RUNNING)
    if record.get("pending_message_uuids"):
        # An input its ended (or replaced) process can never answer is released by the follow-up itself, as the pane's
        # reconnect does for a Manager (never replayed, effects uncertain); one a live turn may still answer stays, so
        # the seat is busy below. The recovery that covered a released input is dispositioned with it.
        if release_unanswerable(host, eid, "superseded by an oc_claude follow-up (it carries the next instruction)",
                                out).get("released"):
            record = host.call("state", execution_id=eid)
            rid = record.get("pending_recovery")
            recovery = _recovery(host, eid, record) if rid else None
    # A scheduled (or blocked) recovery is superseded below, so it is not "busy" here.
    busy_view = {**record, "pending_recovery": None} if rid else record
    if seat_busy(busy_view):
        raise SeatError(f"❌ {eid} is busy ({'; '.join(busy_reasons(busy_view)) or record.get('state')}): a follow-up "
                        f"goes only to an idle seat; `{HINT} wait {eid}` first (Claude Send is idle-only).")
    escalations = record.get("pending_escalations") or []
    if escalations and not host_records_escalations(host):
        raise SeatError(f"❌ {eid} has {len(escalations)} pending escalation(s), and this Claude host (started before "
                        "escalations stopped blocking a seat's Send; that activates at Dan's next guarded host restart) "
                        f"withholds the Send until they are dispositioned (`{HINT} info {eid}`, then `{HINT} resolve …`).",
                        EXIT_ATTENTION)
    if escalations:
        out(f"⚠️  {eid}: {len(escalations)} open escalation(s) go on this follow-up's receipt (`{HINT} info {eid}`); "
            "resolve them once handled")
    if record.get("state") == "launch_failed" or not record.get("initialized") and record.get("owner") == "gone" \
            and not record.get("last_input_uuid"):
        raise SeatError(f"❌ {eid} never started (state {record.get('state')}); start a fresh seat instead.")
    ctx = context_report(host, eid, record) if check_context else None
    if ctx is not None:
        out(f"🧮 {eid} · {_context_line(ctx)}")
        if ctx.get("ok") and (ctx.get("target") or {}).get("crossed") and not force:
            raise SeatError(f"⛔ {eid} is at or past its context target: do NOT follow up. Start a fresh seat with the "
                            "continuation as its task (or pass force).", EXIT_CONTEXT)
    changes = plan_switch(host, eid, record, switch, ctx=ctx, out=out) if switch else {}
    resumed = False
    superseded = None
    with start_lock(host):
        if enforce_quota:
            quota_guard(host, role=record.get("role") or "worker", settings=settings, exclude=eid, out=out,
                        follow_up=True)
        if rid:
            # The follow-up replaces the scheduled `continue`: cancel it first (the host refuses if it has just
            # been claimed), so it can neither race the resume below nor wake the seat a second time.
            host.call("resolve_recovery", execution_id=eid, recovery_id=rid,
                      disposition="superseded by an oc_claude follow-up (the follow-up replaces its continue)")
            superseded = rid
            out(f"⏭️  {eid}: automatic recovery {rid} ({(recovery or {}).get('status') or '?'}) superseded by this "
                "follow-up")
            record = host.call("state", execution_id=eid)
        if record.get("state") == "launch_blocked" and record.get("owner") == "host":
            # Blocked at launch (its native initialize did not confirm Max, e.g. an expired login): closing it is the
            # safe direction, and the resume below checks the login again.
            out(f"↻ {eid}: its process is blocked at launch; closing it to resume the same conversation")
            host.call("close_input", execution_id=eid, generation=record.get("generation"))
            record = _await_gone(host, eid, budget=60.0, sleep=sleep, clock=clock)
        halt = record.get("quota_halt")
        if (isinstance(halt, dict) and halt.get("generation") == record.get("generation")
                and record.get("state") == "ready" and record.get("owner") == "host"):
            # Halted for a usage limit: its process still holds the login it started with. Close it so the resume
            # below starts on the current login; if the host cannot close it yet, the follow-up goes to it as before.
            out(f"↻ {eid}: halted for a usage limit; closing its process so it resumes on the current login")
            try:
                host.call("close_input", execution_id=eid, generation=record.get("generation"))
                record = _await_gone(host, eid, budget=60.0, sleep=sleep, clock=clock)
            except SeatError as e:
                out(f"⚠️  {eid}: its process could not be closed ({str(e).splitlines()[0][:200]}); following up on it")
                record = host.call("state", execution_id=eid)
        if record.get("state") == "input_closing" or record.get("owner") == "draining":
            record = _await_gone(host, eid, budget=60.0, sleep=sleep, clock=clock)
        if record.get("owner") == "gone":
            out(f"↻ {eid}: its process ended (state {record.get('state')}); resuming the same conversation")
            host.call("resume", execution_id=eid)
            resumed = True
        elif record.get("state") != "ready":
            raise SeatError(f"❌ {eid}: state {record.get('state')} (owner {record.get('owner')}); nothing sent")
    ready = wait_ready(host, eid, budget=ready_budget, out=out, poll=poll, sleep=sleep, clock=clock)
    if changes:
        ready = apply_switch(host, eid, ready, changes, why=switch_why, out=out)
    receipt = deliver(host, eid, ready["generation"], prompt, budget=ack_budget, out=out, poll=poll, sleep=sleep,
                      clock=clock)
    out(f"📨 FOLLOW-UP sent to {eid} (generation {ready['generation']}{', resumed' if resumed else ''}"
        f"{', on ' + str(ready.get('requested_model')) + ' · ' + str(ready.get('requested_effort')) if changes else ''})")
    return {"execution_id": eid, "generation": ready["generation"], "message_uuid": receipt["message_uuid"],
            "acknowledged": bool(receipt.get("acknowledged") or receipt.get("completed")), "resumed": resumed,
            "context": ctx, "superseded_recovery": superseded, "switched": changes}


# ------------------------------------------------------------------------------------ sockets

def session_row(record: dict) -> dict:
    """One host record in the shape the oc_* listings use (an OpenCode session dict's useful fields)."""
    labels = record.get("labels") or {}
    return {"id": record.get("execution_id"), "backend": "claude", "title": labels.get("title") or "",
            "kind": labels.get("kind") or record.get("role"), "quick": bool(labels.get("quick")),
            "role": record.get("role"), "agent": record.get("agent"), "program": record.get("program"),
            "socket": record.get("socket"), "parent": record.get("parent"),
            "model": record.get("requested_model"), "effort": record.get("requested_effort"),
            "created": record.get("first_launched_at") or record.get("context_generation_started_at"),
            "busy": seat_busy(record), "state": record.get("state"), "owner": record.get("owner"), "labels": labels}


# @beacon[
#   id=oc-claude@sessions_on_socket,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Every Claude session ever started on one socket (AP-core match), newest first, as normalized rows a listing can union with OpenCode's.,
# ]
def sessions_on_socket(socket: str, *, host: HostConnection | None = None) -> list[dict]:
    host = host or connect(require_seats=False)
    # "Ever started": every record on the socket, at any age (the default list is two weeks plus live/busy).
    rows = [session_row(r) for r in host.call("list", socket=socket, all=True) if same_socket(r.get("socket"), socket)]
    return sorted(rows, key=lambda r: r.get("created") or 0, reverse=True)


# @beacon[
#   id=oc-claude@busy_on_socket,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The one-session-per-socket guard's Claude half → (known, busy rows); a scheduled recovery or unknown ownership counts as busy; no host socket file at all = known and empty.,
# ]
def busy_on_socket(socket: str, *, host: HostConnection | None = None) -> tuple[bool, list[dict]]:
    """(status known?, busy Claude sessions on this socket). No host socket file at all: (True, []) — no Claude session
    can exist. A socket file that refuses: (False, []) — a crashed host may have left seats running."""
    if host is None:
        try:
            host = connect(require_seats=False)
        except SeatError as e:
            if e.exit_code == EXIT_NO_HOST and not host_socket().exists():
                return True, []
            return False, []
    try:
        rows = host.call("list")
    except SeatError:
        return False, []
    return True, [session_row(r) for r in rows if same_socket(r.get("socket"), socket)
                  and r.get("role") != "Manager" and seat_busy(r)]


# ------------------------------------------------------------------------------------ cancel / resolve

# @beacon[
#   id=oc-claude@cancel,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The fallback switch: release the inputs its ended process can never answer, cancel a still-scheduled or blocked recovery (it would wake the seat later and race the fallback) and close the seat's input; refuses a busy seat or a recovery already sending.,
# ]
def cancel(execution_id: str, note: str, *, host: HostConnection | None = None, out: Callable = print) -> dict:
    if not isinstance(note, str) or not note.strip():
        raise SeatError("❌ cancel needs a note (why: e.g. 'switched to the cast row's fallback')")
    host, eid = _resolve(execution_id, host)
    record = host.call("state", execution_id=eid)
    if record.get("role") == "Manager":
        raise SeatError(f"❌ {eid} is a Manager conversation; scripts never close it")
    actions = []
    rid = record.get("pending_recovery")
    recovery = (_recovery(host, eid, record) or {}) if rid else {}
    if recovery.get("status") in ("claimed", "accepted"):
        raise SeatError(f"❌ {eid}: recovery {rid} is sending its continue right now; `{HINT} wait {eid}`, then "
                        "cancel again", EXIT_RUNNING)
    if record.get("pending_message_uuids") or rid:
        # Inputs its ended process can never answer are abandoned with the cancel (released, never replayed), and the
        # recovery that would have continued them is cancelled with them.
        answer = release_unanswerable(host, eid, f"cancelled by oc_claude: {note}", lambda _: None)
        if answer.get("released"):
            actions.append(f"{len(answer['released'])} uncertain input(s) released (never replayed)")
        if answer.get("recovery"):
            actions.append(f"recovery {rid} ({recovery.get('status') or '?'}) cancelled")
            rid = None
        if answer:
            record = host.call("state", execution_id=eid)
    if rid:
        host.call("resolve_recovery", execution_id=eid, recovery_id=rid, disposition=f"cancelled by oc_claude: {note}")
        actions.append(f"recovery {rid} ({recovery.get('status') or '?'}) cancelled")
        record = host.call("state", execution_id=eid)
    state, owner = record.get("state"), record.get("owner")
    if owner == "host" and state in ("ready", "launch_blocked"):
        if seat_busy(record):
            raise SeatError(f"❌ {eid} is busy ({'; '.join(busy_reasons(record))}): it cannot be closed mid-turn. Wait "
                            "for it to end, then cancel again (interrupting a seat is not wired yet)."
                            + (f" Done so far: {'; '.join(actions)}." if actions else ""), EXIT_RUNNING)
        host.call("close_input", execution_id=eid, generation=record.get("generation"))
        actions.append("input closed (the process exits once Claude finishes)")
    elif owner == "gone":
        actions.append("its process had already ended")
    elif state == "input_closing":
        actions.append("its input was already closing")
    else:
        actions.append(f"process left as it is (state {state}, owner {owner})")
    out(f"🛑 {eid} cancelled: " + " · ".join(actions))
    if record.get("pending_escalations"):
        out(f"   {len(record['pending_escalations'])} escalation(s) stay open: `{HINT} info {eid}`")
    return {"execution_id": eid, "actions": actions}


# @beacon[
#   id=oc-claude@resolve,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The operator's way out of a stuck seat, one selector: an escalation (incident id or unique ≥8-char prefix), every pending one, a recovery (with the inputs it covered, through release_input), the seat's unanswerable pending inputs (--release-input), or a cleared durability failure (--clear-infrastructure).,
# ]
def resolve(execution_id: str, *, note: str, incident: str | None = None, recovery: str | None = None,
            all_escalations: bool = False, release_inputs: bool = False, clear_infrastructure: bool = False,
            host: HostConnection | None = None, out: Callable = print) -> list[dict]:
    if sum(bool(x) for x in (incident, recovery, all_escalations, release_inputs, clear_infrastructure)) != 1:
        raise SeatError("❌ resolve exactly one of --incident ID, --recovery ID, --all-escalations, --release-input "
                        "or --clear-infrastructure")
    if not isinstance(note, str) or not note.strip():
        raise SeatError("❌ resolve needs a disposition note (what you inspected and decided)")
    host, eid = _resolve(execution_id, host)
    record = host.call("state", execution_id=eid)
    done = []
    if (release_inputs or clear_infrastructure) and not host_releases_inputs(host):
        raise SeatError(f"❌ {eid}: this Claude host predates release_input / clear_infrastructure_error; they arrive "
                        "with Dan's next guarded host restart (scripts/claude_restart.sh)", EXIT_ATTENTION)
    if clear_infrastructure:
        answer = host.call("clear_infrastructure_error", execution_id=eid, disposition=note) or {}
        line = (f"✔️  {eid}: durability failure " + ("cleared, the gap journaled" if answer.get("cleared")
                                                       else "was not set") + f" (state {answer.get('state')})")
        if answer.get("pending_message_uuids"):
            line += f"; inputs the gap may have answered: `{HINT} resolve {eid} --release-input --note \"…\"`"
        out(line)
        return [answer]
    if release_inputs:
        answer = release_unanswerable(host, eid, note, out)
        if answer.get("held") and not answer.get("released"):
            raise SeatError(f"❌ {eid}: nothing released: its pending input(s) may still be answered (held, above)",
                            EXIT_RUNNING)
        out(f"✔️  {eid}: {len(answer.get('released') or [])} input(s) released"
            + (f" · recovery {answer['recovery'].get('recovery_id')} {answer['recovery'].get('status')}"
               if answer.get("recovery") else "")
            + (f" · {len(answer['acknowledged_escalations'])} escalation(s) about them answered"
               if answer.get("acknowledged_escalations") else ""))
        return [answer]
    if recovery:
        if host_releases_inputs(host):
            answer = release_unanswerable(host, eid, note, out, recovery_id=recovery)
            if answer.get("recovery") is None and answer.get("held"):
                raise SeatError(f"❌ {eid}: recovery {recovery} stays: its input(s) may still be answered (held, "
                                "above)", EXIT_RUNNING)
            done.append(answer.get("recovery") or {"recovery_id": recovery, "status": "already dispositioned"})
        else:
            done.append(host.call("resolve_recovery", execution_id=eid, recovery_id=recovery, disposition=note))
        out(f"✔️  {eid}: recovery {recovery} dispositioned ({done[-1].get('status')})")
        return done
    pending = record.get("pending_escalations") or []
    if all_escalations:
        ids = list(pending)
        if not ids:
            out(f"   {eid}: no pending escalations")
    else:
        hits = [i for i in pending if i.startswith(incident)] if len(incident) >= 8 else []
        ids = hits if len(hits) == 1 else [incident]
        if len(hits) > 1:
            raise SeatError(f"❌ incident prefix {incident!r} is ambiguous among the pending escalations; give more")
    for iid in ids:
        done.append(host.call("resolve_incident", execution_id=eid, incident_id=iid, disposition=note))
        out(f"✔️  {eid}: incident {iid[:12]} ({done[-1].get('category')}) dispositioned")
    return done


# ------------------------------------------------------------------------------------ CLI

def _text(value: str | None, path: str | None, what: str) -> str | None:
    if value is not None and path is not None:
        raise SeatError(f"❌ give --{what} or --{what}-file, not both")
    return Path(path).read_text(encoding="utf-8") if path else value


def _labels(items: list[str] | None) -> dict | None:
    if not items:
        return None
    labels = {}
    for item in items:
        key, sep, raw = item.partition("=")
        if not sep:
            raise SeatError(f"❌ --label {item!r}: key=value")
        try:
            value = json.loads(raw)
            if isinstance(value, (dict, list)):
                value = raw
        except ValueError:
            value = raw
        labels[key.strip()] = value
    return labels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="launch one Claude session and send its first message")
    s.add_argument("--agent", help="agent file whose model/level apply unless --model/--variant")
    s.add_argument("--model", help="anthropic/claude-…[#level] (or a bare host model name with --variant)")
    s.add_argument("--variant", help="thinking level (low|medium|high|xhigh|max)")
    s.add_argument("--role", required=True, choices=LAUNCH_ROLES)
    s.add_argument("--cwd", required=True, help="absolute working directory")
    s.add_argument("--prompt"); s.add_argument("--prompt-file")
    s.add_argument("--system-prompt"); s.add_argument("--system-prompt-file")
    s.add_argument("--socket"); s.add_argument("--program"); s.add_argument("--parent")
    s.add_argument("--owning-conductor"); s.add_argument("--target", type=float)
    s.add_argument("--label", action="append", help="key=value provenance label (repeatable)")
    s.add_argument("--no-wait", action="store_true"); s.add_argument("--max", type=float, default=540)
    st = sub.add_parser("status"); st.add_argument("id")
    w = sub.add_parser("wait"); w.add_argument("id"); w.add_argument("--max", type=float, default=540)
    i = sub.add_parser("info"); i.add_argument("id"); i.add_argument("--tail", type=int, default=1500)
    f = sub.add_parser("follow-up"); f.add_argument("id"); f.add_argument("--prompt"); f.add_argument("--prompt-file")
    f.add_argument("--force", action="store_true", help="send even at/past the context target")
    f.add_argument("--no-wait", action="store_true"); f.add_argument("--max", type=float, default=540)
    so = sub.add_parser("socket"); so.add_argument("socket")
    c = sub.add_parser("cancel"); c.add_argument("id"); c.add_argument("--note", required=True)
    r = sub.add_parser("resolve"); r.add_argument("id"); r.add_argument("--note", required=True)
    r.add_argument("--incident"); r.add_argument("--recovery"); r.add_argument("--all-escalations", action="store_true")
    r.add_argument("--release-input", action="store_true",
                   help="release the pending input(s) no process can answer any more (never replayed)")
    r.add_argument("--clear-infrastructure", action="store_true",
                   help="clear a host durability failure once storage works again (the gap is journaled)")
    sub.add_parser("quota")
    ro = sub.add_parser("route"); ro.add_argument("agent_or_model")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "start":
            spec = a.model
            if spec and "/" not in spec:
                spec = "anthropic/" + spec
            choice = claude_launch_choice(agent=a.agent, model=spec, variant=a.variant)
            prompt = _text(a.prompt, a.prompt_file, "prompt")
            system = _text(a.system_prompt, a.system_prompt_file, "system-prompt") or ""
            res = start(model=choice["model"], effort=choice["effort"], system_prompt=system, prompt=prompt or "",
                        role=a.role, cwd=a.cwd, socket=a.socket, program=a.program, parent=a.parent, agent=a.agent,
                        owning_conductor=a.owning_conductor, context_target=a.target, labels=_labels(a.label))
            print(f"   message {res['message_uuid']} · {'acknowledged' if res['acknowledged'] else 'accepted'}")
            if a.no_wait:
                print(f"   --no-wait: `{HINT} wait {res['execution_id']} --max 540` until it exits 0")
                return EXIT_DONE
            return wait(res["execution_id"], max_s=a.max)
        if a.cmd == "status":
            s_ = status(a.id)
            print(json.dumps({k: v for k, v in s_.items() if k != "record"}, indent=2, default=str))
            return EXIT_DONE
        if a.cmd == "wait":
            return wait(a.id, max_s=a.max)
        if a.cmd == "info":
            info(a.id, tail=a.tail)
            return EXIT_DONE
        if a.cmd == "follow-up":
            res = follow_up(a.id, _text(a.prompt, a.prompt_file, "prompt") or "", force=a.force)
            if a.no_wait:
                return EXIT_DONE
            return wait(res["execution_id"], max_s=a.max)
        if a.cmd == "socket":
            rows = sessions_on_socket(a.socket)
            if not rows:
                print(f"(no Claude session was ever started on {a.socket})")
            for row in rows:
                print(f"{row['id']} · {'BUSY' if row['busy'] else 'idle'} · {row['kind'] or '?'}"
                      f"{' · quick' if row['quick'] else ''} · created {hhmm(row['created'])} · "
                      f"{(row['title'] or row['agent'] or '')[:90]}")
            return EXIT_DONE
        if a.cmd == "cancel":
            cancel(a.id, a.note)
            return EXIT_DONE
        if a.cmd == "resolve":
            resolve(a.id, note=a.note, incident=a.incident, recovery=a.recovery, all_escalations=a.all_escalations,
                    release_inputs=a.release_input, clear_infrastructure=a.clear_infrastructure)
            return EXIT_DONE
        if a.cmd == "quota":
            host = connect(require_seats=False)
            rows = host.call("list")
            print(quota_line(quota_snapshot(rows)))
            busy = [r for r in rows if r.get("role") != "Manager" and seat_busy(r)]
            settings = load_settings()
            print(f"   busy Claude sessions (non-Manager): {len(busy)} · seat cap {settings['claude_max_seats']} · "
                  f"refuse at five-hour ≥ {settings['claude_quota_five_hour']:.0%} or seven-day ≥ "
                  f"{settings['claude_quota_seven_day']:.0%}")
            for row in busy:
                print(f"   {row.get('execution_id')} · {row.get('role')} · {row.get('socket') or '—'}")
            return EXIT_DONE
        if a.cmd == "route":
            backend = backend_for(a.agent_or_model)
            print(backend)
            if backend == "claude":
                agent = None if "/" in a.agent_or_model else a.agent_or_model
                model = a.agent_or_model if agent is None else None
                print(json.dumps(claude_launch_choice(agent=agent, model=model)))
            return EXIT_DONE
    except SeatError as e:
        return report(e)
    except KeyboardInterrupt:
        print("interrupted: observation stopped; nothing was cancelled or resent", file=sys.stderr)
        return 130
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
