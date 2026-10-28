#!/usr/bin/env python3
"""oc_steer.py — send a `Dan rules:` / `Dan asks:` relay into a program's live Conductor session.

    oc.sh steer "[ap:PROG]" --file steer.md (--rules | --asks)
                               [--sid SID] [--session NNN [--remember]] [--record] [--dry-run]
                               [--model anthropic/M] [--variant LEVEL]   (Claude Code targets: switch first)

What it does (the mechanics the Manager used to re-write as a one-off .py every time):
  1. reads the steer text from --file (or `--file -` for stdin) — never JSON on the command line
  2. resolves the live Conductor: the newest row of the program root's `sessions` registry whose bound session is
     live (an OpenCode `ses_…` on the server, or a Claude Code `cc-…` the host knows; --sid overrides; a unique sid
     prefix is accepted)
  3. prints its status: busy → 'read at the worker's return' · idle → 'read now'
  4. composes  `Dan rules: ‹text›`  or  `Dan asks: ‹text›`  + ` (relayed by Manager-Session-NNN)`
     NNN from --session, else scripts/.oc_config.json → manager_session, else a prompt (never a guess)
  5. POST /api/session/{sid}/prompt, delivery `steer`: taken at the session's next step boundary, so a busy conductor
     reads it when its running tool call (a worker, say) returns; --queue: only after its current turn ends
  6. confirms by the prompt's own msg_ id: in the history (delivered) or in the session's inbox (accepted, pending)
  --record  also captures the steer as an AP under the program root:
            `📨 Steer · ‹program› · for -NNN · ‹date›`, tagged #prog-steer--expand, so the ruling is not chat-only
  --dry-run prints everything (resolved sid, status, the exact message) and sends/writes nothing

  WORKER targets (direct steers — ruled Manager-Session-033 · doctrine [ap:UP7YVO] · [ap:Y4DHSH] check 14):
  when --sid resolves to a WORKER the steer is a direct steer into it. A worker is a seat the scripts started (session
  metadata `kind: seat|helper`: `oc_seat dispatch`, fork or quick; a top-level session with no parentID) or a child
  session (it has a parentID):
    · an IDLE worker is refused unless --force — nobody is awaiting it, so the message would go nowhere
    · the message gains one reminder line ("list this under steers_received …") unless --no-reminder
    · --record titles the AP `📨 Steer · ‹program› · for worker ‹socket› · ‹date›` and places it BESIDE the
      worker's socket: --socket "[ap:X]" names it; else the seat's own metadata `socket`; else the program's
      ConductorState `in_flight` rows are matched to the worker's @agent; else the program root, with a printed warning

  CLAUDE CODE targets (a registry row bound `execution: {backend: claude, id: cc-…}`, or --sid cc-…): the same command,
  through the Claude host. A steer reaches a busy Claude session at its next tool step; one long-running command delays
  it until that command ends:
    · mid-turn (a Conductor, worker, lead seat or helper) → steered at once through the host's steer: prints `queued`
      (Claude Code holds it), then `delivered` when Claude's echo shows the model received it; watched up to
      --deliver-wait seconds (default 120), else "queued, not yet delivered" with its receipt id, exit 0 (never resent)
    · its own turn closed (idle, or a Conductor waiting on its workers in background Bash) → sent now, then confirmed by
      Claude's exact echo of the message (acknowledged); a still-scheduled automatic recovery is superseded by it
    · its process ended (collected, or closed) → the host resumes the same conversation (same id), then the send
    · --queue: deliver only after its current turn ends instead: watches HERE (no hidden host queue) and sends the
      moment its own turn ends, printing as it waits (--max seconds, default 3000; exit 2 = still mid-turn, nothing
      sent); a worker's turn end is its run's end, so a queued worker steer also needs --force
    · starting, closing, a settings readback or a recovery's `continue` in flight → not sent (the host withholds
      every message then); a Conductor's --queue waits it out. A native API retry inside the open turn is no blocker:
      the steer is queued through it
    · an idle Claude worker is refused unless --force, as on OpenCode; a Manager conversation, unknown ownership or an
      escalation awaiting a disposition → refused (nothing a wait fixes)
    · --model anthropic/M and/or --variant LEVEL switch the SAME conversation's model/thinking level before the message
      is sent (oc_claude.plan_switch / apply_switch: verified by the native readback, else nothing is sent). A switch
      needs its own turn closed: mid-turn it is refused unless --queue. On an OpenCode target: not implemented yet.

Doctrine: [ap:8Z49BK] §Control (busy Conductors CAN be steered; a question is not authority — the
prefix is how the Conductor knows which it received) · [ap:8U33GB] §2 (confirm by reading the session back, not by the
POST's status).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

from oc_common import (
    CONFIG_SOURCE,
    Workflowy,
    claude_live_checker,
    find_yaml_key,
    row_backend,
    row_session_id,
    load_config,
    newest_live_row,
    note_json,
    confirm_prompt,
    oc_prompt,
    oc_sessions,
    oc_status,
    program_short_name,
    read_text_source,
    registry_rows,
    resolve_sid_prefix,
    save_config,
    sessions_by_id,
    today,
    verify_manager_session,
    normalize_session_id,
    row_binding,
)
import oc_backend
import oc_claude
from oc_backend import is_claude_handle

STEER_TAG = "prog-steer--expand"   # decision 1: a new prog-family row, not the brief family
CLAUDE_QUEUE_MAX = 3000.0   # --max default for --queue on a Claude target: well under a background Bash's 1 h ceiling
CLAUDE_POLL = 3.0           # seconds between host observations while --queue waits
CLAUDE_DELIVER_WAIT = 120.0  # --deliver-wait default: how long a steer's delivery is watched (observation only)
WORKER_REMINDER = (
    "(Direct steer — list it in one summary line under `steers_received` in your gate record; "
    "if it conflicts with your contract, follow it and record a forced deviation. See [ap:UP7YVO].)"
)


def manager_session_number(args: argparse.Namespace) -> str:
    """--session → config file → prompt. Zero-padded to three digits. Never guessed."""
    return manager_session_and_source(args)[0]


# @beacon[
#   id=auto-beacon@manager_session_and_source-qsus,
#   role=manager_session_and_source,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
def manager_session_and_source(args: argparse.Namespace) -> tuple[str, str]:
    """(NNN, where it came from: '--session' · CONFIG_SOURCE · 'prompt'). The source decides how a
    disagreement with the newest Manager session Dock is treated (config → refuse; deliberate → warn)."""
    raw = args.session
    source = "--session"
    if raw is None:
        cfg = load_config()
        raw = cfg.get("manager_session")
        source = CONFIG_SOURCE
    if raw is None:
        if not sys.stdin.isatty():
            raise SystemExit("Manager session number unknown: give --session NNN (or write it once with --remember); "
                             "stdin is not a terminal so I will not prompt")
        raw = input("Manager-Session number (e.g. 029): ").strip()
        source = "prompt"
        if not raw:
            raise SystemExit("no Manager session number given")
    try:
        nnn = normalize_session_id(raw)   # '084' · a sub-session '084-b' (never rewritten; see oc_common)
    except ValueError:
        raise SystemExit(f"Manager session number '{raw}' ({source}) is not NNN or NNN-x (e.g. 084, 084-b)")
    if args.remember:
        if "-" in nnn:
            raise SystemExit("--remember refuses a sub-session number: .oc_config.json belongs to the primary session; "
                             "a sub-session passes --session NNN-x on every call")
        cfg = load_config()
        cfg["manager_session"] = nnn
        save_config(cfg)
        print(f"remembered manager_session={nnn} in scripts/.oc_config.json")
    return nnn, source


def compose(text: str, rules: bool, nnn: str, worker_reminder: bool = False) -> str:
    prefix = "Dan rules: " if rules else "Dan asks: "
    suffix = f"(relayed by Manager-Session-{nnn})"
    if worker_reminder:
        # a worker dispatched before check 14 existed has no clause telling it what to do with a steer;
        # the reminder makes the message self-describing at the cost of one line
        return f"{prefix}{text}\n\n{WORKER_REMINDER}\n{suffix}"
    if "\n" in text:
        return f"{prefix}{text}\n\n{suffix}"
    return f"{prefix}{text} {suffix}"


SEAT_KINDS = ("seat", "helper")   # session metadata `kind` of every seat the scripts start (oc_seat dispatch/fork/quick)


# @beacon[
#   id=oc_steer@is_worker_session,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Who gets a direct (worker) steer on OpenCode: a child session, or a top-level seat the scripts started (oc_seat dispatch/fork/quick: metadata kind seat|helper). Idle → refused unless --force; reminder line; --record beside its socket.,
# ]
def is_worker_session(session: dict) -> bool:
    """A direct-steer target: a child session (parentID), or a seat the scripts started as a TOP-LEVEL session
    (`oc_seat dispatch`, fork, quick: metadata kind seat|helper), which has no parentID. A Conductor is neither."""
    md = session.get("metadata") or {}
    return bool(session.get("parentID")) or str(md.get("kind") or "") in SEAT_KINDS


def seat_socket(session: dict) -> str | None:
    """The socket a script-started seat was given (its metadata `socket`), as an [ap:X] handle; None when absent."""
    raw = str((session.get("metadata") or {}).get("socket") or "").strip()
    if not raw:
        return None
    if raw.startswith("[ap:"):
        return raw
    import re
    return f"[ap:{raw}]" if re.fullmatch(r"[A-Z0-9]{4,}", raw) else raw   # a bare AP core; a UUID stays as given


def worker_agent(session: dict) -> str | None:
    """A worker session's agent: the session's own `agent` field, else the `@agent` in its title
    (e.g. 'Implement U7 … (@qwen3-max subagent)' → 'qwen3-max')."""
    if session.get("agent"):
        return str(session["agent"])
    import re
    m = re.search(r"@([A-Za-z0-9._-]+)", session.get("title") or "")
    return m.group(1) if m else None


# @beacon[
#   id=auto-beacon@find_worker_socket-88kk,
#   role=find_worker_socket,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def find_worker_socket(program: str, session: dict) -> tuple[str | None, str]:
    """Best-effort: the socket AP the worker is filling, from ConductorState `in_flight` rows.

    → (socket handle or None, one-line explanation). Never raises: a failed lookup means root placement.
    Matching: exactly one in_flight row → it; several → the one whose `agent:` equals the worker's @agent;
    otherwise None (ambiguous — say so, place at the root).
    """
    import re
    try:
        async with Workflowy() as wf:
            kids = await wf.children(program)
            cs = next((k for k in kids if "conductorstate" in (k.get("name") or "").lower()), None)
            if not cs:
                return None, "no ConductorState among the root's children"
            _, note = await wf.read_note(cs["uuid"])
    except Exception as e:  # noqa: BLE001
        return None, f"ConductorState lookup failed: {str(e)[:120]}"
    block = re.search(r"^\s*in_flight\s*:\s*\n((?:[ \t]+.*\n?)+)", note, flags=re.M)
    if not block:
        return None, "ConductorState has no in_flight rows"
    rows: list[tuple[str, str | None]] = []
    for line in block.group(1).splitlines():
        sm = re.search(r"socket\s*:\s*\"?(\[ap:[A-Z0-9]+\])", line)
        if not sm:
            continue
        am = re.search(r"agent\s*:\s*\"?([A-Za-z0-9._-]+)", line)
        rows.append((sm.group(1), am.group(1) if am else None))
    if not rows:
        return None, "in_flight rows carry no socket handles"
    if len(rows) == 1:
        return rows[0][0], f"the one in_flight socket ({rows[0][1] or 'agent unknown'})"
    agent = worker_agent(session)
    hits = [s for s, a in rows if agent and a == agent]
    if len(hits) == 1:
        return hits[0], f"in_flight row whose agent matches the worker's @{agent}"
    return None, f"{len(rows)} in_flight sockets and no unique agent match (worker @{agent or '?'})"


# @beacon[
#   id=auto-beacon@resolve_conductor-0hda,
#   role=resolve_conductor,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def resolve_conductor(program: str, explicit_sid: str | None, sessions: list[dict] | None,
                            oc_error: str | None = None) -> tuple[dict, str, str, dict | None]:
    """→ (session, root_name_line, program short name, registry row or None). The session is an OpenCode session
    dict, or for a Claude Code target the dict claude_target returns (`backend: claude`, with its host record).
    sessions None = OpenCode did not answer (oc_error says why): only a Claude target can still be steered, and only
    when it is the program's NEWEST bound row (an OpenCode row above it may be live: never steer past it)."""
    by_id = sessions_by_id(sessions) if sessions is not None else {}
    root_name = ""
    row = None
    if explicit_sid:
        if is_claude_handle(explicit_sid):
            s = claude_target(explicit_sid)
        elif sessions is None:
            raise SystemExit(f"OpenCode unavailable: {oc_error}")
        else:
            s = resolve_sid_prefix(explicit_sid, sessions)
        # still read the root (for --record naming); tolerate Workflowy being down
        try:
            async with Workflowy() as wf:
                root_name, _ = await wf.read_note(program)
        except Exception as e:  # noqa: BLE001
            print(f"(note: Workflowy root read failed, naming falls back to the handle: {str(e)[:120]})")
        return s, root_name, program_short_name(root_name, program), None
    async with Workflowy() as wf:
        root_name, root_note = await wf.read_note(program)
    rows = registry_rows(root_note)
    if not rows:
        raise SystemExit(f"{program}: no `sessions` registry rows in the root note — give --sid")
    live = claude_live_checker()
    if sessions is None:
        bound = sorted((r for r in rows if row_binding(r)), key=lambda r: int(r.get("number") or 0), reverse=True)
        row = bound[0] if bound and row_backend(bound[0]) == "claude" and live(row_session_id(bound[0])) else None
        if row is None:
            raise SystemExit(f"OpenCode unavailable: {oc_error}")
    else:
        row = newest_live_row(rows, by_id, claude_live=live)
    if not row:
        listing = "\n".join(
            f"  -{int(r.get('number') or 0):03d} sid={row_session_id(r) or 'null'} state={r.get('state')}"
            for r in rows)
        raise SystemExit(f"{program}: no registry row has a LIVE session:\n{listing}\n(give --sid to override)")
    if row_backend(row) == "claude":
        # the program's current Conductor runs on Claude Code: never fall back to an older OpenCode row
        return claude_target(row_session_id(row)), root_name, program_short_name(root_name, program), row
    return by_id[row_session_id(row)], root_name, program_short_name(root_name, program), row


# ----------------------------------------------------------------------------- Claude Code targets

def claude_target(handle: str) -> dict:
    """A Claude Code session to steer: {id, backend, title, host, record}. The id goes through the exact `cc-` router
    (oc_backend.resolve_claude_id), never OpenCode's resolver; a host that does not answer stops here, nothing sent."""
    try:
        host = oc_claude.connect(require_seats=False)
        eid = oc_backend.resolve_claude_id(handle, host)
        record = host.call("state", execution_id=eid)
    except oc_backend.SeatError as e:
        raise SystemExit(f"{e}\n(nothing sent: the Claude host must answer before a Claude session can be steered)") \
            from None
    return {"id": eid, "backend": "claude", "title": (record.get("labels") or {}).get("title") or "",
            "host": host, "record": record}


def claude_recovery(host, eid: str, record: dict) -> dict | None:
    """The record's pending automatic recovery as the host stores it (status scheduled · claimed · accepted …), or None."""
    rid = record.get("pending_recovery")
    if not rid:
        return None
    return next((r for r in host.call("recoveries", execution_id=eid) or [] if r.get("recovery_id") == rid), None)


def _turn_reason(record: dict) -> str | None:
    """The host's own-turn gate (claude_headless.host.own_turn_busy, imported so the two never drift) in a steer's
    words, or None when its own turn is closed. Background tasks never count: they never block a message."""
    from claude_headless.host import own_turn_busy
    if not own_turn_busy(record):
        return None
    native = record.get("native_session_state")
    if native == "requires_action":
        return "mid-turn: a permission request inside its turn is being answered"
    if native == "running":
        return "mid-turn: working on its own turn"
    return "its turn state is not known yet (no native state published)"


# @beacon[
#   id=oc_steer@claude_gate,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Can a steer reach a Claude session now? The host's Send gate read off one record: open (idle or waiting in background; supersedes a scheduled recovery) · resume (process ended) · wait (own turn open: --queue) · refuse (Manager, unknown ownership, failed launch). An open escalation is noted, never a refusal (the host records it on the receipt).,
# ]
def claude_gate(record: dict, recovery: dict | None = None, *, steers: bool = True,
                records_escalations: bool = True) -> tuple[str, str]:
    """Whether a message can reach this Claude Code session now (see _claude_gate). Open escalations never refuse: the
    sender is their addressee, so the host records them on the message's receipt; the reason says so.
    `records_escalations=False` (oc_claude.host_records_escalations: a host started before that change) keeps the old
    host's refusal until Dan's next guarded host restart."""
    verdict, reason = _claude_gate(record, recovery, steers=steers)
    escalations = record.get("pending_escalations") or []
    if escalations and verdict in ("open", "steer", "resume") and not records_escalations:
        return "refuse", (f"{len(escalations)} escalation(s) await a disposition, and this Claude host (started before "
                          "escalations stopped blocking a seat's Send; that activates at Dan's next guarded host "
                          f"restart) withholds every message until then: `oc.sh seat info {record.get('execution_id')}`, "
                          "then `oc.sh seat resolve … --incident … --note …`")
    if escalations and verdict in ("open", "steer", "resume"):
        reason += (f" · {len(escalations)} open escalation(s) go on this message's receipt, not a block "
                   f"(`oc.sh seat info {record.get('execution_id')}`, then `oc.sh seat resolve …`)")
    return verdict, reason


def _claude_gate(record: dict, recovery: dict | None = None, *, steers: bool = True) -> tuple[str, str]:
    """Whether a message can reach this Claude Code session now: the host's own Send gate (NativeSession.send) read
    off one host record, plus what waiting would or would not fix. → (verdict, plain reason):
      open    the host accepts a message now: idle, or its own turn closed while it waits on background tasks (a
              Conductor's `oc_seat.sh wait`; they never block a message), or a still-scheduled automatic recovery,
              which the message supersedes (the host cancels that `continue`)
      resume  its process has ended: the host resumes the same conversation (same id), then the message is sent
      steer   its own turn is open (an input is being answered): the host's steer queues the message for its next
              tool step (`steers=False`, a host that predates the steer, turns this into `wait`)
      wait    it is starting, closing, reading back settings or sending a recovery's `continue` (or, its turn
              closed or the host predating the steer, inside a native API retry): the host withholds the message
              until that clears; --queue waits for it
      refuse  nothing a wait fixes: a Manager, unknown ownership, a failed launch or a positive settings mismatch."""
    eid = record.get("execution_id")
    owner, state = record.get("owner"), record.get("state")
    if record.get("role") == "Manager":
        return "refuse", "a Manager conversation: Dan writes to it through the pane, never a script"
    if record.get("infrastructure_error") or record.get("incident_unpersisted"):
        return "refuse", f"the host reports a durability failure for it (`oc.sh seat info {eid}`)"
    if owner == "unknown" or state == "ownership_unknown":
        return "refuse", ("ownership unknown: the host lost sight of its process (a host restart or crash); no second "
                          "writer until the host reconciles it")
    if state in ("launch_failed", "launch_blocked") or record.get("settings_mismatch"):
        return "refuse", f"its launch did not complete ({state}{', settings mismatch' if record.get('settings_mismatch') else ''})"
    rid = record.get("pending_recovery")
    if owner == "gone":
        if rid:
            return "wait", (f"the host is recovering it (automatic recovery {rid}, "
                            f"{(recovery or {}).get('status') or 'pending'}): it resumes and sends one `continue` first")
        if not record.get("last_input_uuid"):
            return "refuse", "it never received a first message (never started)"
        return "resume", f"its process has ended ({state}): the host resumes the same conversation first"
    if owner == "draining" or state == "input_closing" or record.get("stdout_eof"):
        return "wait", "its process is closing (it can be resumed once closed)"
    if state in ("launching", "initializing"):
        return "wait", "starting (native initialize and settings readback)"
    if state != "ready":
        return "refuse", f"state {state}, owner {owner}"
    if record.get("setting_control_pending"):
        return "wait", "a settings readback is pending"
    status = (recovery or {}).get("status")
    if rid and status in ("claimed", "accepted"):
        return "wait", f"an automatic recovery ({rid}) is sending its `continue` right now"
    turn = "mid-turn: answering an accepted message" if record.get("pending_message_uuids") else _turn_reason(record)
    # A native API retry withholds an ordinary send, never a steer into the open turn: Claude Code holds the steer
    # through the retry and hands it to the model at its next tool step.
    if record.get("native_retry_active") and not (turn and steers):
        return "wait", "a native API retry is unsettled (the host withholds an ordinary message until its turn ends)"
    if turn:
        if not steers:
            return "wait", (f"{turn} (this Claude host predates mid-turn steering: it activates at Dan's next guarded "
                            "host restart)")
        return "steer", turn
    if rid:
        return "open", (f"an automatic recovery ({rid}, {status or '?'}) is scheduled: this message supersedes it (the "
                        "host cancels that `continue`), and the session reads the message instead")
    tasks = record.get("background_tasks") or {}
    if tasks or record.get("background_unsettled"):
        return "open", (f"its own turn is closed, waiting in the background on {len(tasks) or 'unsettled'} task(s): "
                        "it reads the message now, and its background waits keep running")
    return "open", "idle: it reads the message now"


def claude_waiting_in_background(record: dict) -> bool:
    return bool(record.get("background_tasks") or record.get("background_unsettled"))


def claude_queue(host, eid: str, *, deadline: float, out=print, poll: float | None = None, sleep=None,
                 clock=None) -> tuple[str, str, dict] | None:
    """--queue on a Claude target: observe the host until the gate opens → (verdict, reason, record); None when the
    deadline (a time.monotonic() value) passes first: nothing sent. The message is held HERE, in this process, never
    in a hidden host queue. A gate that turns to `refuse` while waiting stops with SystemExit."""
    poll = CLAUDE_POLL if poll is None else poll
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    seen = None
    t0 = clock()
    while True:
        record = host.call("state", execution_id=eid)
        verdict, reason = claude_gate(record, claude_recovery(host, eid, record),   # `steer` = still mid-turn here
                                      records_escalations=oc_claude.host_records_escalations(host))
        if verdict in ("open", "resume"):
            out(f"   ▶ {time.strftime('%H:%M:%S')} its turn is over after {clock() - t0:.0f}s of watching: {reason}")
            return verdict, reason, record
        if verdict == "refuse":
            raise SystemExit(f"REFUSED while waiting: {eid}: {reason}. Nothing sent.")
        if reason != seen:
            seen = reason
            out(f"   ⏳ {time.strftime('%H:%M:%S')} still {reason}; sending the moment it ends")
        if clock() >= deadline:
            return None
        sleep(max(0.0, min(poll, deadline - clock())))


# @beacon[
#   id=oc_steer@claude_send,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Delivers one steer to a Claude session whose gate is open: host resume first when its process ended, a planned --model/--variant switch (verified by readback), then one exact Send confirmed by Claude's echo (oc_claude.deliver; a lost reply reads the receipt, never resends).,
# ]
def claude_send(host, eid: str, verdict: str, record: dict, message: str, *, changes: dict | None = None,
                out=print) -> dict:
    """One delivery: `resume` → host resume (same conversation, same id) and wait until ready; `changes` (a planned
    model/effort switch) → oc_claude.apply_switch, verified by readback; then oc_claude.deliver (one host Send with a
    fresh message UUID, observed until Claude echoes it). Raises oc_backend.SeatError."""
    if verdict == "resume":
        out(f"↻ {eid}: its process had ended; the host resumes the same conversation (same id) …")
        host.call("resume", execution_id=eid)
        record = oc_claude.wait_ready(host, eid, out=out)
    if changes:
        record = oc_claude.apply_switch(host, eid, record, changes, why="oc_steer", out=out)
    return oc_claude.deliver(host, eid, record["generation"], message, out=out)


def _host_refused_busy(e: oc_backend.SeatError) -> bool:
    """The host's Send refused because a turn began between the observation and the send: nothing was delivered."""
    return not e.lost and "busy" in str(e)


def claude_quota(host) -> str:
    try:
        return oc_backend.quota_line(oc_backend.quota_snapshot(host.call("list")))
    except oc_backend.SeatError:
        return "🔋 Max quota: unavailable"


# @beacon[
#   id=oc_steer@run_claude,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=oc_steer to a Claude Code target: the same composition and --record; mid-turn it steers at once through the host steer (queued, then delivered at the next tool step); idle → send now, collected → resume first; --queue holds it here until the turn ends.,
# ]
async def run_claude(args: argparse.Namespace, target: dict, short: str, row: dict | None, text: str, nnn: str) -> int:
    host, eid, record = target["host"], target["id"], target["record"]
    role = str(record.get("role") or "?")
    is_worker = role in oc_backend.SEAT_ROLES or str((record.get("labels") or {}).get("kind") or "") in SEAT_KINDS
    message = compose(text, args.rules, nnn, worker_reminder=is_worker and not args.no_reminder)
    steers = oc_claude.host_steers(host)
    verdict, reason = claude_gate(record, claude_recovery(host, eid, record), steers=steers,
                                  records_escalations=oc_claude.host_records_escalations(host))
    in_background = claude_waiting_in_background(record)
    worker_socket, socket_note = None, ""
    if is_worker:
        if args.socket:
            worker_socket, socket_note = args.socket, "--socket"
        elif record.get("socket"):
            worker_socket, socket_note = str(record["socket"]), "its host record"
    status = {"open": "waiting in background" if in_background else "idle", "resume": "process ended",
              "steer": "mid-turn", "wait": "not taking messages yet", "refuse": "refused"}[verdict]
    if record.get("pending_recovery") and verdict == "open":
        status = "recovery scheduled"

    print("=" * 96)
    print(f"STEER → {target['title'] or oc_claude.describe(record)}   [Claude Code]")
    print(f"  program: {args.program}  ({short})" + (f"  registry row -{int(row.get('number') or 0):03d}" if row
                                                     else "  [explicit --sid]"))
    if record.get("program") and oc_backend.ap_core(str(record["program"])) != oc_backend.ap_core(args.program):
        print(f"  ⚠️  its host record names program {record['program']}, not {args.program}")
    if is_worker:
        print(f"  target: {role.upper()} (@{record.get('agent') or '?'}) — a Claude Code seat; its Conductor "
              f"{record.get('owning_conductor') or record.get('parent') or '?'}  [direct steer, doctrine [ap:UP7YVO]]")
        if args.record or args.dry_run:
            print(f"  record: beside socket {worker_socket} ({socket_note})" if worker_socket
                  else "  record: ⚠️ at the program root — socket unknown; pass --socket \"[ap:X]\" to place it")
    print(f"  sid: {eid}  status: {reason}")
    print(f"  {claude_quota(host)}")
    print(f"  kind: {'Dan rules' if args.rules else 'Dan asks'} · relayed by Manager-Session-{nnn}"
          + (" · --record" if args.record else ""))
    print("-" * 96)
    print(message)
    print("-" * 96)

    if verdict == "refuse":
        print(f"REFUSED: {reason}. Nothing sent.")
        return 1
    try:
        switch = oc_claude.switch_choice(getattr(args, "model", None), getattr(args, "variant", None))
    except oc_backend.SeatError as e:
        print(f"{e}")
        return 1
    changes = {}
    if switch["model"] or switch["effort"]:
        if verdict == "steer" and not args.queue:
            print("REFUSED: a model/thinking-level switch needs the session's own turn closed, and it is mid-turn. "
                  "Re-run with --queue (it sends, after the switch, the moment the turn ends). Nothing sent.")
            return 1
        try:
            changes = oc_claude.plan_switch(host, eid, record, switch)
        except oc_backend.SeatError as e:
            print(f"{e}")
            return e.exit_code or 1
    resume_hint = f"`oc.sh seat resume {eid} --socket \"{record.get('socket') or '[ap:SOCKET]'}\"`"
    if verdict == "steer" and args.queue:
        verdict, status = "wait", "mid-turn (--queue)"   # the explicit option: deliver after its turn ends
    if is_worker and verdict == "wait" and not (args.queue and args.force):
        if args.queue:
            print(f"REFUSED: --queue holds the message until this {role}'s turn ends, which is its run's end: nobody "
                  "would be awaiting it. Drop --queue to steer it now (it lands at its next tool step), or add --force "
                  "if its Conductor will wait on it again. Nothing sent.")
        else:
            print(f"NOT SENT: this Claude Code {role} is {reason}. Try again in a moment, follow it up once it lands "
                  f"({resume_hint}), or steer its Conductor. Nothing sent.")
        return 1
    worker_idle = is_worker and (verdict == "resume" or (verdict == "open" and not in_background
                                                          and not record.get("pending_recovery")))
    if worker_idle and not args.force:
        print("REFUSED: the worker is idle — its run has landed (or its process ended), so no one is awaiting it. Steer "
              f"the Conductor instead, follow it up with {resume_hint}, or pass --force if you know its Conductor "
              "will wait on it again.")
        return 1
    if verdict == "wait" and not args.queue:
        print(f"NOT SENT: the Conductor is {reason}. Re-run with --queue: the command then watches and sends the moment "
              f"this clears, printing as it waits (up to --max {args.max:.0f}s).")
        return 1

    if args.dry_run:
        print("DRY RUN — nothing sent, nothing written. A real run would "
              + {"open": "send now and confirm Claude's acknowledgement.",
                 "resume": "resume the same conversation through the host, then send and confirm.",
                 "steer": "steer it now: queued at once, delivered at its next tool step (watched up to "
                          f"{args.deliver_wait:.0f}s).",
                 "wait": f"wait here up to {args.max:.0f}s for its own turn to end, then send and confirm."}[verdict])
        return 0

    deadline = time.monotonic() + args.max
    waited_from = time.monotonic()
    if verdict == "wait":
        print(f"⏳ --queue: held HERE (never in a hidden host queue) until its own turn ends; watching up to "
              f"{args.max:.0f}s. Ctrl-C = nothing sent.")
    while verdict != "steer":
        if verdict == "wait":
            got = claude_queue(host, eid, deadline=deadline)
            if got is None:
                print(f"⏳ STILL MID-TURN after {args.max:.0f}s: NOTHING SENT. Run the same command again (or a longer "
                      "--max).")
                return 2
            verdict, reason, record = got
            status = f"mid-turn → sent when its turn ended (--queue, {time.monotonic() - waited_from:.0f}s)"
        try:
            receipt = claude_send(host, eid, verdict, record, message, changes=changes)
            break
        except oc_backend.SeatError as e:
            if _host_refused_busy(e) and args.queue:
                print(f"   ↺ it began a new turn just before the send (the host refused it: nothing delivered); "
                      "watching again")
                verdict = "wait"
                continue
            if _host_refused_busy(e) and steers and not changes:
                print("   ↺ it began a new turn just before the send (nothing delivered); steering it instead")
                verdict, status = "steer", "mid-turn (a turn began as it was sent)"
                record = host.call("state", execution_id=eid)
                break
            if _host_refused_busy(e):
                print(f"NOT SENT: it began a new turn just before the send ({str(e).splitlines()[0][:200]}). Nothing "
                      "was delivered. Re-run with --queue.")
                return 1
            print(f"❌ {e}")
            print("NOT confirmed. Inspect `oc.sh seat info " + eid + "` before retrying; do not send twice blindly.")
            return 1

    if verdict == "steer":
        try:
            receipt = oc_claude.steer(host, eid, record["generation"], message, budget=args.deliver_wait)
        except oc_backend.SeatError as e:
            print(f"❌ {e}")
            print("NOT confirmed. Inspect `oc.sh seat info " + eid + "` before retrying; do not send twice blindly.")
            return 1
        mid = receipt.get("message_uuid")
        if receipt.get("delivered") or receipt.get("acknowledged") or receipt.get("completed"):
            detail = f"delivered: Claude received message {mid} at its next tool step"
        else:
            detail = (f"queued, not yet delivered: it lands when the current command finishes (receipt {mid}; never "
                      "resent)")
            print(f"⏳ {detail}. `oc.sh seat info {eid}` shows it later.")
        status = f"{status} → steered"
    else:
        acknowledged = bool(receipt.get("acknowledged") or receipt.get("completed"))
        mid = receipt.get("message_uuid")
        if acknowledged:
            detail = f"acknowledged: Claude echoed message {mid}; it is reading it now"
            print(f"✅ {detail}")
        else:
            detail = (f"accepted and durable on the host (message {mid}) but not yet acknowledged by Claude; it was "
                      "NOT resent")
            print(f"⚠️  {detail}. Check `oc.sh seat info {eid}` before anything else.")

    if args.record:
        try:
            reply = await record_steer(args.program, short, row, eid, message, status, detail,
                                       worker_socket=worker_socket, is_worker=is_worker)
            j = note_json(reply)
            if j and j.get("success") is False:
                print(f"❌ --record failed: {json.dumps(j.get('error'))[:300]}")
                return 1
            handle = ""
            if j:
                handle = find_yaml_key(j, "ap_id") or find_yaml_key(j, "uuid") or ""
                if isinstance(handle, str) and handle and not handle.startswith("[ap:") and len(handle) <= 8:
                    handle = f"[ap:{handle}]"
            where = worker_socket if (is_worker and worker_socket) else args.program
            print(f"📨 recorded under {where} as #{STEER_TAG}" + (f" → {handle}" if handle else ""))
            if not handle:
                print(f"   (capture reply: {reply[:300]})")
        except Exception as e:  # noqa: BLE001
            print(f"❌ --record failed: {str(e)[:300]}  (the steer WAS delivered)")
            return 1
    return 0


# @beacon[
#   id=auto-beacon@record_steer-sgar,
#   role=record_steer,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def record_steer(program: str, short: str, row: dict | None, sid: str, message: str, status: str,
                       delivered: str, *, worker_socket: str | None = None, is_worker: bool = False) -> str:
    """Capture the steer as an AP. Conductor steer → program root, `for -NNN`.
    Worker steer → beside the worker's socket when known (`for worker [ap:X]`), else the root (`for worker ‹sid›`).
    Returns the tool's reply text (JSON)."""
    if is_worker:
        for_whom = f"for worker {worker_socket}" if worker_socket else f"for worker {sid[:12]}"
        parent = worker_socket or program
        kind = "direct steer into a WORKER session"
    else:
        for_whom = f"for -{int(row.get('number') or 0):03d}" if row else f"for {sid[:12]}"
        parent = program
        kind = "steer to the Conductor"
    name = f"📨 Steer · {short} · {for_whom} · {today()}"
    body = (
        f"# {name}\n\n"
        f"*Relayed by oc_steer.py · {kind} · session `{sid}` · status at send: **{status}** · {delivered}*\n\n"
        f"{message}\n"
    )
    async with Workflowy() as wf:
        txt = await wf.call("anchor", "point.capture", {
            "parent_handle": parent, "name": name, "content": body, "ap_id": "auto", "tags": [STEER_TAG],
        })
    return txt


# @beacon[
#   id=auto-beacon@run-07ys,
#   role=run,
#   slice_labels=opc-basic,
#   kind=ast,
# ]
async def run(args: argparse.Namespace) -> int:
    switching = bool(getattr(args, "model", None) or getattr(args, "variant", None))
    if switching and args.sid and not is_claude_handle(args.sid):
        print("❌ --model/--variant are not implemented yet for OpenCode sessions; nothing sent.")
        return 1
    text = read_text_source(args.file)
    nnn, source = manager_session_and_source(args)
    _, nnn = await verify_manager_session(nnn, source)   # a stale config number is synced here (file rewritten), before anything is resolved or sent

    oc_error = None
    try:
        sessions = oc_sessions()
    except Exception as e:  # noqa: BLE001
        sessions, oc_error = None, str(e)   # only a Claude target can still be steered (resolve_conductor decides)
    session, root_name, short, row = await resolve_conductor(args.program, args.sid, sessions, oc_error)
    if session.get("backend") == "claude":
        return await run_claude(args, session, short, row, text, nnn)
    if switching:
        print(f"❌ --model/--variant are not implemented yet for OpenCode sessions ({session.get('id')}); nothing sent.")
        return 1
    sid = session["id"]
    is_worker = is_worker_session(session)
    md = session.get("metadata") or {}
    message = compose(text, args.rules, nnn, worker_reminder=is_worker and not args.no_reminder)
    status_ok, status = oc_status()
    st = status.get(sid, "idle") if status_ok else "unknown"
    delivery = "queue" if args.queue else "steer"
    busy_when = ("taken at its next step boundary (when its running tool call, e.g. a worker, returns)"
                 if delivery == "steer" else "taken only after its current turn ends (--queue)")
    if is_worker:
        when = {"busy": f"worker running: {busy_when}",
                "idle": "⚠️ NOBODY is awaiting this worker — a steer into it goes nowhere",
                "unknown": "status endpoint unavailable"}.get(st, f"{st} — {busy_when}")
    else:
        when = {"busy": busy_when,
                "idle": "read now (the prompt wakes it)",
                "unknown": "status endpoint unavailable"}.get(st, f"{st} — {busy_when}")

    worker_socket: str | None = None
    socket_note = ""
    if is_worker:
        if args.socket:
            worker_socket, socket_note = args.socket, "--socket"
        elif seat_socket(session):
            worker_socket, socket_note = seat_socket(session), "its session metadata"
        elif args.record or args.dry_run:
            worker_socket, socket_note = await find_worker_socket(args.program, session)

    print("=" * 96)
    print(f"STEER → {session.get('title') or '(untitled)'}")
    print(f"  program: {args.program}  ({short})" + (f"  registry row -{int(row.get('number') or 0):03d}" if row else "  [explicit --sid]"))
    if is_worker:
        parent_sid = session.get("parentID") or ""
        if parent_sid:
            origin = f"child of conductor session {parent_sid[:12]}…"
        else:
            cond = md.get("conductor_sid")
            origin = (f"a {md.get('kind') or 'seat'} the scripts started (top-level); its Conductor "
                      f"{str(cond)[:12] + '…' if cond else '?'}")
        print(f"  target: {'HELPER' if md.get('kind') == 'helper' else 'WORKER'} (@{worker_agent(session) or '?'}) — "
              f"{origin}  [direct steer, doctrine [ap:UP7YVO]]")
        if args.record or args.dry_run:
            print(f"  record: beside socket {worker_socket} ({socket_note})" if worker_socket
                  else f"  record: ⚠️ at the program root — socket unknown ({socket_note}); pass --socket \"[ap:X]\" to place it")
    print(f"  sid: {sid}  status={st} → {when}")
    print(f"  kind: {'Dan rules' if args.rules else 'Dan asks'} · relayed by Manager-Session-{nnn}" + (" · --record" if args.record else ""))
    print("-" * 96)
    print(message)
    print("-" * 96)

    if is_worker and st == "idle" and not args.force:
        print("REFUSED: the worker is idle — its run has landed (or never started), so no one is awaiting it and no one "
              "would read this. Steer the Conductor instead, follow it up with "
              f"`oc.sh seat resume {sid} --socket \"{worker_socket or '[ap:SOCKET]'}\"`, or pass --force if you know "
              "its Conductor will wait on it again.")
        return 1

    if args.dry_run:
        print("DRY RUN — nothing sent, nothing written.")
        return 0

    code, item = oc_prompt(sid, message, delivery=delivery)
    mid = item.get("id") if isinstance(item, dict) else None
    print(f"POST /api/session/{sid[:12]}…/prompt ({delivery}) → {code}" + (f"  {mid}" if mid else f"  {str(item)[:300]}"))
    if code != 200 or not mid:
        print("NOT confirmed — the server did not accept the send. Inspect before retrying; do not send twice blindly.")
        return 1
    state, detail = confirm_prompt(sid, mid)
    ok = state != "missing"
    print((f"✅ {state}: " if ok else "❌ NOT confirmed: ") + detail)
    if not ok:
        return 1

    if args.record:
        try:
            reply = await record_steer(args.program, short, row, sid, message, st, detail,
                                       worker_socket=worker_socket, is_worker=is_worker)
            j = note_json(reply)
            if j and j.get("success") is False:
                print(f"❌ --record failed: {json.dumps(j.get('error'))[:300]}")
                return 1
            handle = ""
            if j:
                handle = find_yaml_key(j, "ap_id") or find_yaml_key(j, "uuid") or ""
                if isinstance(handle, str) and handle and not handle.startswith("[ap:") and len(handle) <= 8:
                    handle = f"[ap:{handle}]"
            where = worker_socket if (is_worker and worker_socket) else args.program
            print(f"📨 recorded under {where} as #{STEER_TAG}" + (f" → {handle}" if handle else ""))
            if not handle:
                print(f"   (capture reply: {reply[:300]})")
        except Exception as e:  # noqa: BLE001
            print(f"❌ --record failed: {str(e)[:300]}  (the steer WAS delivered)")
            return 1
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="send a Dan rules / Dan asks relay to a program's live Conductor")
    ap.add_argument("program", help="program root handle, e.g. [ap:Y3DWY2]")
    ap.add_argument("--file", required=True, help="UTF-8 file holding the steer text; '-' = stdin")
    kind = ap.add_mutually_exclusive_group(required=True)
    kind.add_argument("--rules", action="store_true", help="prefix 'Dan rules:' — a ruling (authority)")
    kind.add_argument("--asks", action="store_true", help="prefix 'Dan asks:' — a question (not authority)")
    ap.add_argument("--sid", help="explicit session id: OpenCode `ses_…` (or a unique prefix) or Claude Code `cc-…` "
                                  "(exact, or cc- plus 8+ hex); overrides the registry lookup")
    ap.add_argument("--session", help="Manager session number NNN for '(relayed by Manager-Session-NNN)' (a sub-session passes NNN-x, e.g. 084-b); else scripts/.oc_config.json → manager_session; else a prompt")
    ap.add_argument("--remember", action="store_true", help="write the Manager session number into scripts/.oc_config.json")
    ap.add_argument("--record", action="store_true", help="also capture the steer as `📨 Steer · …` (#prog-steer--expand) under the program root (a worker steer: beside the worker's socket)")
    ap.add_argument("--socket", help="WORKER steers: the socket AP the worker is filling, e.g. [ap:9A0VAU] — where --record places the Steer AP (else looked up in ConductorState in_flight; else the root)")
    ap.add_argument("--force", action="store_true", help="WORKER steers: send even if the worker is idle (normally refused — nobody is awaiting an idle worker)")
    ap.add_argument("--no-reminder", action="store_true", help="WORKER steers: omit the one-line steers_received reminder appended to the message")
    ap.add_argument("--queue", action="store_true", help="deliver only after the session's current turn ends (default: steer, taken at its next step boundary). "
                                                          "A Claude Code session mid-turn: watch here and send the moment its own turn ends (a worker also needs --force)")
    ap.add_argument("--max", type=float, default=CLAUDE_QUEUE_MAX,
                    help=f"Claude Code targets with --queue: seconds to watch for the turn to end (default {CLAUDE_QUEUE_MAX:.0f}); "
                         "exit 2 = still mid-turn, nothing sent")
    ap.add_argument("--deliver-wait", type=float, default=CLAUDE_DELIVER_WAIT,
                    help=f"Claude Code targets steered mid-turn: seconds to watch for delivery (default "
                         f"{CLAUDE_DELIVER_WAIT:.0f}); still queued after it = exit 0 with the receipt id, never resent")
    ap.add_argument("--dry-run", action="store_true", help="print the resolved session, status and exact message; send nothing")
    ap.add_argument("--model", help="Claude Code targets: switch the same conversation to anthropic/M[#level] first "
                                    "(verified by readback; needs its turn closed, else --queue). OpenCode: not implemented yet")
    ap.add_argument("--variant", choices=("low", "medium", "high", "xhigh", "max"),
                    help="Claude Code targets: switch its thinking level first (as --model). OpenCode: not implemented yet")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
