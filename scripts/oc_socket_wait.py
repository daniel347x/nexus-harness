#!/usr/bin/env python3
"""oc_socket_wait.py — the socket doorbell: `oc_seat.sh wait --socket "[ap:JOB]"`, for any job that has a socket, a
Manager's own `manager-quick` subagent included (the one kind of worker with no id the scripts take).

The interface is every other doorbell's (`oc_seat.sh wait ‹id›`): --max, --keep-others, --force (the one-Manager
registry, oc_doorbell, keyed by the socket), and the exit codes 0 done · 2 still running · 4 needs attention · 5 UNKNOWN
(never done) · 1 no such socket · 9 handed over. What the socket names decides how it is watched, the first that
applies (oc_seat.socket_wait):

1. Sessions on the socket (`oc_seat socket`'s list, both backends): the id wait on the newest one (a busy one first).
2. An OpenCode `manager-quick` child: a child of an OpenCode Manager session, started since the socket was created,
   whose first prompt names the socket's AP-ID (opencode_quick_child): the id wait on it.
3. Otherwise (a Claude Code `manager-quick` child, which has no id at all): watch the socket note itself (watch). ONE
   node.read every POLL_S and nothing heavier; never a cache refresh. Done = the newest marker heading is a `## Result`
   or a PARTIAL (a `## Continuation` after it means the work resumed: not done) AND the note is unchanged across two
   consecutive reads, so a Result written in parts is not caught half-written. Liveness is the Claude host's live
   Manager records (subagent_holders): no Manager conversation left with a background subagent started since the
   socket was created, nor with its own turn open, on two consecutive reads, and still no Result = 4 needs attention.
   That signal is weak (a record does not say which subagent is which), so it may only ever say "needs attention",
   never "done": done is only ever read off the socket.

The task itself is judged from the socket's ## Result, never from exit 0.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Callable

POLL_S = 90.0           # one socket read per this many seconds (Workflowy is gentle-only: heavy calls locked Dan out)
LAP_S = 5.0             # the handed-over check between reads (a file read, oc_doorbell)
START_GRACE_S = 600.0   # a socket this young may still be waiting for its subagent to start: never "needs attention"
SINCE_SLACK_S = 600.0   # clock slack between Workflowy's `created` stamp and the host's / OpenCode's stamps
TAIL = 1500
RULE = "-" * 96
MANAGER_AGENTS = ("manager", "manager-gpt")   # = oc_doorbell.MANAGER_AGENTS

_HEADING = re.compile(r"^#{1,6}[ \t]+(.*?)[ \t#]*$")
_BOLD_PARTIAL = re.compile(r"^[ \t]*\*\*[ \t]*PARTIAL\b")      # a worker's `**PARTIAL**` line counts as its heading
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_AP = re.compile(r"\[?ap:([A-Za-z0-9]+)\]?")                   # a handle as given (fullmatch)
_AP_IN_TEXT = re.compile(r"\[ap:([A-Za-z0-9]+)\]")             # an AP-ID cited in a prompt


class SocketGone(Exception):
    """No Workflowy node carries the socket's handle (exit 1, like a session id that never existed)."""


# ------------------------------------------------------------------------------------ the socket note

def _marker(text: str) -> str | None:
    """One heading's text → 'Result' · 'PARTIAL' · 'Continuation' · None (leading emoji and emphasis ignored)."""
    words = re.sub(r"^[\W_]+", "", text)
    if re.match(r"(?i)continuation\b", words):
        return "Continuation"
    if re.match(r"(?i)(result|partial)\b", words):
        return "PARTIAL" if re.search(r"(?i)\bpartial\b", words) else "Result"
    return None


# @beacon[
#   id=oc-socket-wait@landed,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The socket doorbell's done rule on one note: the newest marker heading (outside code fences) is a ## Result or a PARTIAL → (kind, its line index); none, or a later ## Continuation (work resumed) → None.,
# ]
def landed(note: str) -> tuple[str, int] | None:
    """(kind, line index) of the newest marker heading when it is a Result or a PARTIAL; None when the note has none,
    or when a later `## Continuation` (a Conductor or Manager starting the next worker) means the work resumed."""
    newest, fenced = None, False
    for i, line in enumerate((note or "").splitlines()):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING.match(line)
        kind = _marker(m.group(1)) if m else ("PARTIAL" if not fenced and _BOLD_PARTIAL.match(line) else None)
        if kind:
            newest = (kind, i)
    return newest if newest and newest[0] != "Continuation" else None


def result_tail(note: str, line: int, tail: int = TAIL) -> str:
    """The landed section to the end of the note, its last `tail` characters."""
    return "\n".join((note or "").splitlines()[line:]).strip()[-tail:]


def _error_message(text: str) -> str | None:
    import json
    try:
        j = json.loads(text)
    except (TypeError, ValueError):
        return None
    if isinstance(j, dict) and j.get("error"):
        e = j["error"]
        return str(e.get("message") if isinstance(e, dict) else e)
    return None


def _gone(msg: str) -> bool:
    return "did not resolve" in msg or "no node carries" in msg


def read_socket(handle: str) -> str:
    """The socket's whole note text by ONE node.read (the Workflowy client). SocketGone when no node carries the
    handle; any other failure raises."""
    from oc_common import Workflowy

    async def _read() -> str:
        async with Workflowy(lightweight=True) as wf:
            return await wf.read_note_text(handle)
    text = asyncio.run(_read())
    msg = _error_message(text)
    if msg is not None:
        if _gone(msg):
            raise SocketGone(msg.splitlines()[0][:200])
        raise RuntimeError(msg.splitlines()[0][:200])
    return text


def socket_created(handle: str) -> float | None:
    """When the socket node was created (epoch seconds), from one anchor.point.read size_only (a header, no note);
    None when the header does not say. SocketGone when no node carries the handle. Once per wait call, never per poll."""
    from oc_common import Workflowy

    async def _read() -> str:
        async with Workflowy(lightweight=True) as wf:
            return await wf.call("anchor", "point.read", {"node_handle": handle, "size_only": True})
    text = asyncio.run(_read())
    msg = _error_message(text)
    if msg is not None:
        if _gone(msg):
            raise SocketGone(msg.splitlines()[0][:200])
        raise RuntimeError(msg.splitlines()[0][:200])
    m = re.search(r"(?m)^created (\d+(?:\.\d+)?)\b", text or "")
    return float(m.group(1)) if m else None


def canonical(handle: str) -> str:
    """`[ap:CORE]` for any AP form (the registry key and the label every line prints); anything else as given."""
    m = _AP.fullmatch((handle or "").strip().strip("`'\""))
    return f"[ap:{m.group(1).upper()}]" if m else (handle or "").strip()


# ------------------------------------------------------------------------------------ who could still write it

# @beacon[
#   id=oc-socket-wait@subagent_holders,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The socket doorbell's liveness (case 3): live Claude Manager records that may still run the job's subagent: a background local_agent task started since the socket was created (the Manager's own Bash waiters are local_bash, ignored), unsettled background tasks, or its own turn open (a foreground subagent). Weak: only ever 'needs attention', never 'done'.,
# ]
def subagent_holders(records: list[dict], since: float | None = None) -> list[tuple[str, str]]:
    """(Manager execution id, why) for every live Claude Manager conversation that may still be running the job's
    subagent. A record cannot say which subagent is which, so every plausible one counts."""
    holders = []
    for r in records or []:
        if r.get("role") != "Manager" or r.get("owner") not in ("host", "draining"):
            continue                                   # an ended Manager process took its subagents with it
        tasks = [t for t in (r.get("background_tasks") or {}).values() if isinstance(t, dict)]
        agents = [t for t in tasks if t.get("task_type") in (None, "local_agent")
                  and not (since is not None and isinstance(t.get("started_at"), (int, float))
                           and t["started_at"] < since - SINCE_SLACK_S)]
        eid = str(r.get("execution_id") or "?")
        if agents:
            names = ", ".join(repr(str(t.get("description") or "?")[:60]) for t in agents[:3])
            holders.append((eid, f"{len(agents)} background subagent(s) ({names})"))
        elif r.get("background_unsettled") and not tasks:
            holders.append((eid, "background tasks not yet settled"))
        elif r.get("native_session_state") in ("running", "requires_action") or r.get("primary_turn_open"):
            holders.append((eid, "its own turn is open (a foreground subagent cannot be ruled out)"))
    return holders


def opencode_quick_child(handle: str, sessions: list[dict], *, since: float | None = None,
                         first_prompt: Callable[[str], str] | None = None, limit: int = 30) -> str | None:
    """Case 2: the `ses_…` of an OpenCode Manager's `manager-quick` child whose first prompt names this socket,
    created since the socket was (less slack), newest first. A child whose first AP-ID is the socket (its job) wins
    over one that only mentions it."""
    m = _AP.fullmatch((handle or "").strip().strip("`'\""))
    needle = (re.compile(rf"(?<![A-Za-z])ap:{re.escape(m.group(1))}\b", re.I) if m
              else re.compile(re.escape(handle.strip())))
    by_id = {s.get("id"): s for s in sessions}
    created = lambda s: float((s.get("time") or {}).get("created") or 0) / 1000.0  # noqa: E731
    kids = [s for s in sessions if s.get("agent") == "manager-quick" and s.get("parentID")
            and (by_id.get(s["parentID"]) or {}).get("agent") in MANAGER_AGENTS
            and not (since is not None and created(s) < since - SINCE_SLACK_S)]
    kids.sort(key=created, reverse=True)
    if first_prompt is None:
        first_prompt = _first_prompt
    mentions = []
    for s in kids[:limit]:
        text = first_prompt(s["id"]) or ""
        hit = needle.search(text)
        if not hit:
            continue
        first = _AP_IN_TEXT.search(text)
        if first and first.start() <= hit.start() <= first.end():
            return s["id"]
        mentions.append(s["id"])
    return mentions[0] if mentions else None


def _first_prompt(sid: str) -> str:
    from oc_client import msg_text, oc_list
    first = oc_list(f"/api/session/{sid}/message", {"order": "asc", "type": "user", "limit": 1}, max_items=1)
    return msg_text(first[0]) if first else ""


# ------------------------------------------------------------------------------------ case 3: watch the note

# @beacon[
#   id=oc-socket-wait@watch,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Case 3 of `oc_seat wait --socket` (a Claude manager-quick child, no id): one socket read per 90 s → 0 when a Result/PARTIAL is the newest marker and the note is unchanged on the next read · 4 when no Result and no Manager can still hold the subagent on two reads (after a recheck for sessions) · 2 at --max · 5 at --max when the socket or the host could not be read · 9 handed over (checked every 5 s).,
# ]
def watch(socket: str, max_s: float, *, read: Callable[[], str], holders: Callable[[], list[tuple[str, str]]],
          created: float | None = None, quota: Callable[[], str | None] | None = None,
          recheck: Callable[[float], int | None] | None = None, superseded: Callable[[], str | None] | None = None,
          hint: str = "oc_seat.sh", out: Callable = print, sleep: Callable = time.sleep,
          clock: Callable = time.monotonic, wall: Callable = time.time, poll: float = POLL_S, lap: float = LAP_S,
          tail: int = TAIL) -> int:
    """Observe the socket note until done / needs attention, or until max_s; exit codes as above. `read` raises
    SocketGone (exit 1) or anything else (unknown); `holders` raises when the host cannot be read (unknown);
    `recheck(remaining)` runs once before a 4: sessions that appeared on the socket since take over (its exit code),
    None = still nothing."""
    label = canonical(socket)
    rearm = f"`{hint} wait --socket \"{label}\" --max {max_s:.0f}`"
    t0 = clock()
    next_read, prev, quiet, said, unknown, seen_by = t0, None, 0, None, None, []
    while True:
        gone = superseded() if superseded else None
        if gone:
            out(gone)
            return 9
        if clock() >= next_read:
            next_read = clock() + poll
            try:
                note, note_err = read(), None
            except SocketGone as e:
                raise SystemExit(f"❌ no such socket {label}: {e}") from None
            except Exception as e:  # noqa: BLE001
                note, note_err = None, f"the socket could not be read ({str(e).splitlines()[0][:160] if str(e) else type(e).__name__})"
            mark = landed(note) if note is not None else None
            if mark and note == prev:
                who = (f"a background subagent of {seen_by[0]}" if len(set(seen_by)) == 1
                       else "read off the socket note (no session on it)")
                out(f"✅ {label} done · {'Result' if mark[0] == 'Result' else 'PARTIAL (a continuation is owed)'} "
                    f"landed · {who}")
                line = quota() if quota else None
                if line:
                    out("   " + line)
                out(RULE)
                out(result_tail(note, mark[1], tail))
                return 0
            try:
                live, live_err = holders(), None
            except Exception as e:  # noqa: BLE001
                live, live_err = None, f"the Claude host could not be read ({str(e).splitlines()[0][:160]})"
            if live:
                seen_by = [eid for eid, _ in live]
            young = created is not None and wall() - created < START_GRACE_S
            quiet = quiet + 1 if note is not None and not mark and live == [] and not young else 0
            if quiet >= 2:
                code = recheck(max(0.0, max_s - (clock() - t0))) if recheck else None
                if code is not None:
                    return code
                out(f"⚠️  {label} NEEDS ATTENTION · no Result or PARTIAL on the socket, and no Manager conversation "
                    f"is left that could still be running its subagent (no session on the socket; read twice, "
                    f"{poll:.0f} s apart). Read the socket; never treat this as done.")
                return 4
            unknown = note_err or live_err
            if unknown:
                state = f"❓ {label} unknown: {unknown}"
            elif mark:
                state = f"📝 {label}: a {mark[0]} heading landed; confirming on the next read that it is complete"
            elif quiet:
                state = (f"⚠️  {label}: no Result yet and no Manager holds a subagent that could write one; "
                         "checking once more before saying it needs attention")
            elif live:
                state = (f"⚙️  {label} running: no Result yet · " + "; ".join(f"{eid[:11]}…: {why}" for eid, why in live)
                         + f" · the socket is read every {poll:.0f} s")
            else:
                state = f"🛫 {label}: no Result yet; the socket is young, its subagent may still be starting"
            if state != said:
                out(state)
                said = state
            prev = note
        if clock() - t0 >= max_s:
            if unknown:
                out(f"❓ {label} UNKNOWN after {max_s:.0f}s: {unknown}. Never treat it as done; read the socket for "
                    f"landed work, and call {rearm} again.")
                return 5
            out(f"⏳ {label} still running after {max_s:.0f}s — call {rearm} again")
            return 2
        sleep(max(0.0, min(lap, next_read - clock(), max_s - (clock() - t0))))
