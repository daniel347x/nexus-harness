"""OpenCode's side of the one-call Manager fork; oc_seat owns the prompt and doorbells.

The native shell binds OPENCODE_SESSION_ID. Its public Shell.Info pid identifies this
process tree, and the history tool's shellID proves the exact call, even for identical
titles launched together. Native history forks copy only settled assistant messages:
wait for the background shell's creating step to settle, then fork BEFORE its next
message. That includes this turn's reading and the call, never the Manager's next step.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode

from oc_client import (OpenCodeError, oc_get, oc_inbox, oc_messages, oc_recent_messages, oc_request,
                       oc_sessions, oc_status, model_ref_str)


def state_dir():
    return Path(os.environ.get("OC_MANAGER_FORK_DIR") or
                Path.home() / ".local/state/opencode-companion/manager-forks")


@contextmanager
def start_lock(manager):
    """One native start/claim transaction per Manager, shared across CLI checkouts.

    The tiny receipt closes OpenCode's fork-then-metadata gap: a lost POST reply must
    not make the same tool call allocate another session. No history or job text lives here.
    """
    import fcntl
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (manager + ".json")
    with (folder / (manager + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        claims = json.loads(path.read_text()) if path.exists() else {}

        def save():
            tmp = path.with_suffix(f".tmp{os.getpid()}")
            tmp.write_text(json.dumps(claims, sort_keys=True) + "\n")
            os.replace(tmp, path)
        yield claims, save


def pending_start(session, now=None):
    """An allocated native start, using the existing Claude unsent-start grace, never a permanent cap slot.

    A native fork has no own idle timestamp until it actually runs; a completed own turn
    also settles a launch marker whose final metadata PATCH was lost.
    """
    import oc_claude
    md, stamps = session.get("metadata") or {}, session.get("time") or {}
    if not md.get("fork_launch_pending"):
        return False
    created, idle = stamps.get("created"), stamps.get("idle")
    if isinstance(created, (int, float)) and isinstance(idle, (int, float)) and idle >= created:
        return False
    return not isinstance(created, (int, float)) or (time.time() if now is None else now) - created / 1000 < oc_claude.UNSENT_GRACE


def live_forks(manager, rows=None, *, status=None):
    """Busy, queued and allocated-but-unsent native forks of exactly this Manager."""
    import oc_backend
    try:
        rows = oc_sessions() if rows is None else rows
        if status is None:
            ok, status = oc_status()
            if not ok:
                raise oc_backend.SeatError("❓ OpenCode live-fork status is unknown. Nothing started.",
                                           oc_backend.EXIT_UNKNOWN)
        found = []
        for s in rows:
            md = s.get("metadata") or {}
            if (md.get("forked_from") or (s.get("fork") or {}).get("sessionID")) != manager:
                continue
            if s["id"] in status or pending_start(s) or oc_inbox(s["id"]):
                found.append(s)
        return found
    except (OpenCodeError, OSError) as e:
        raise oc_backend.SeatError(f"❓ OpenCode live-fork inventory could not be read ({e}). Nothing started.",
                                   oc_backend.EXIT_UNKNOWN) from None


def own_shell(session, *, pid=None):
    """Find the native shell whose kernel pid is our ancestor, not a title/time guess."""
    import oc_doorbell
    ancestors = set()
    pid = os.getpid() if pid is None else pid
    while pid and pid > 1:
        ancestors.add(pid)
        pid = oc_doorbell._parent(pid)
    directory = (session.get("location") or {}).get("directory")
    query = "?" + urlencode({"location[directory]": directory}) if directory else ""
    shells = oc_get("/api/shell" + query, timeout=20)
    return next((s for s in shells if s.get("pid") in ancestors and
                 (s.get("metadata") or {}).get("sessionID") == session["id"]), None)


# @beacon[
#   id=oc-manager-fork@call_proof,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Proves the calling OpenCode shell by session-bound native pid and exact shellID in its assistant history; exact title/kind shell words; waits for the creating background step and its next message then returns that exclusive native fork boundary. No transcript reconstruction or source mutation.,
# ]
def call_proof(session, title, *, forklift=False, budget=120, clock=time.monotonic, sleep=time.sleep, seat=None):
    """(creating message id, tool id, background, exclusive next-message boundary).

    OpenCode's background shell returns immediately; only the script continues to wait.
    A foreground shell cannot settle while it waits here, so it copies the completed
    current-turn history before its own step and explains that boundary in the prompt.
    """
    if seat is None:
        import oc_seat as seat
    oc_seat = seat
    shell = own_shell(session)
    if not shell:
        raise oc_seat.fork_call_refusal("the calling OpenCode shell is not in this session's native process tree",
                                       forklift=forklift)
    words = oc_seat._shell_words(shell.get("command") or "")
    if not (words and "--fork-self" in words and ("--forklift" in words) == forklift and
            (f"--title={title}" in words or any(a == "--title" and b == title for a, b in zip(words, words[1:])))):
        raise oc_seat.fork_call_refusal("the calling shell does not hold --fork-self and exactly this title and kind",
                                       forklift=forklift)
    deadline = clock() + budget
    while True:
        # The shellID join is exact; history paging cannot confuse an older same-title call.
        messages = list(reversed(oc_recent_messages(session["id"], 20)))
        if len(messages) == 20 and not any(
                ((c.get("state") or {}).get("metadata") or {}).get("shellID") == shell["id"]
                for m in messages for c in m.get("content") or [] if c.get("type") == "tool"):
            # A delayed/replayed same shell may be older than the cheap tail. Page the
            # complete session-scoped history rather than treating a window as absence.
            messages = oc_messages(session["id"])
        for i, m in enumerate(messages):
            if m.get("type") != "assistant":
                continue
            for c in m.get("content") or []:
                state = c.get("state") or {}
                if c.get("type") != "tool" or c.get("name") != "shell":
                    continue
                inputs = state.get("input") or {}
                native_id = (state.get("metadata") or {}).get("shellID")
                # While foreground, progress has not exposed shellID in stored history.
                foreground = (inputs.get("background") is not True and
                              state.get("status") == "running" and
                              inputs.get("command") == shell.get("command") and
                              abs((c.get("time") or {}).get("ran", 0) - shell["time"]["started"]) < 2000)
                if native_id != shell["id"] and not foreground:
                    continue
                if foreground:
                    return m["id"], c["id"], False, m["id"]
                if (m.get("time") or {}).get("completed") and i + 1 < len(messages):
                    return m["id"], c["id"], True, messages[i + 1]["id"]
        if clock() >= deadline:
            raise oc_seat.fork_call_refusal("OpenCode has not settled this background call and its next history boundary",
                                           forklift=forklift)
        sleep(0.05)


def context_reading(session):
    """Same occupancy/window inputs and arithmetic as context-status.ts, no stale cumulative count."""
    from oc_common import catalog_window, context_profile
    messages = oc_get(f"/api/session/{session['id']}/context", timeout=20)
    messages = messages if isinstance(messages, list) else (messages or {}).get("messages") or []
    last, used = None, 0
    for m in reversed(messages):
        if m.get("type") != "assistant":
            continue
        t = m.get("tokens") or {}
        cache = t.get("cache") or {}
        n = sum(t.get(k) or 0 for k in ("input", "output", "reasoning")) + sum(cache.get(k) or 0 for k in ("read", "write"))
        if n > 0:
            last, used = m, n
            break
    model = (last or {}).get("model") or session.get("model") or {}
    window = (context_profile(model.get("id") or "").get("window") or
              catalog_window(model.get("providerID"), model.get("id")) or 1_000_000)
    return (used / window if last else None), used, window


# @beacon[
#   id=oc-manager-fork@start,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=OpenCode backend of oc_seat start_manager_fork: native session env and Manager authority; exact calling-shell history proof; context-status room; under one shared start lock cap and durable per-call claim then native fork before the next message. Preserve agent/model/location and shared opening doctrine; first-prompt failures retain the known fork id and claim. No Claude-host dependency.,
# ]
def start(*, title, forklift=False, doorbell=None, variant=None, environ=None, seat=None):
    import oc_running
    if seat is None:
        import oc_seat as seat
    oc_seat = seat  # CLI __main__ owns the --session override; never import a second copy of its state.
    from oc_pack import fork_session, send, set_model
    env = os.environ if environ is None else environ
    me = env.get("OPENCODE_SESSION_ID") or ""
    session = oc_get(f"/api/session/{me}", timeout=20)
    if not oc_running.is_manager_session(session):
        raise SystemExit(f"❌ --fork-self works only from an OpenCode Manager: {me} is not a Manager conversation. "
                         "A fork never forks; start a fresh quick worker instead. Nothing started.")
    entry, call, background, before = call_proof(session, title, forklift=forklift, seat=oc_seat)
    used, tokens, window = context_reading(session)
    if used is not None and used > oc_seat.MANAGER_FORK_ROOM:
        raise SystemExit(f"❌ the Manager is at {used:.0%} of its window, past the {oc_seat.MANAGER_FORK_ROOM:.0%} fork "
                         "limit: use a fresh quick worker with the context in its socket. Nothing started.")
    if used is None:
        print("⚠️ the Manager's context reading is unknown (no measured model call); forking anyway", flush=True)
    model = dict(session.get("model") or {})
    if variant:
        model["variant"] = variant
    from oc_common import context_profile, profile_target
    number = oc_seat.manager_session()
    kind = "🚜 forklift" if forklift else "⚡ quick fork worker"
    metadata = {"kind": "fork", "quick": True, "forked_from": me, "fork_source": "manager", "fork_point": "call",
                "fork_entry": entry, "fork_call": call, "fork_mode": "background" if background else "foreground",
                "manager_session": number, "composer": "manager", "fork_launch_pending": True,
                "model_ref": model_ref_str(model), "variant": model.get("variant") or "",
                "context_target": profile_target(context_profile(model.get("id") or ""), window, worker=True)}
    if forklift:
        metadata["forklift"] = True
    with start_lock(me) as (claims, save):
        rows = oc_sessions()
        made = next((s["id"] for s in rows if (s.get("metadata") or {}).get("forked_from") == me and
                     (s.get("metadata") or {}).get("fork_call") == call), None)
        if call in claims or made:
            known = made or claims[call].get("id") or "outcome unknown (inspect this source/server)"
            raise SystemExit(f"❌ this call already made a fork: {known} (tool call {call}); one call makes one fork. Nothing started.")
        live = live_forks(me, rows)
        if len(live) >= oc_seat.MAX_LIVE_MANAGER_FORKS:
            raise SystemExit(f"❌ {len(live)} forks of this Manager are still live (at most {oc_seat.MAX_LIVE_MANAGER_FORKS}):\n" +
                             "\n".join(f"   {s['id']} · {s.get('title') or ''}" for s in live) +
                             "\n   Wait for one, or use a fresh quick worker. Nothing started.")
        claims[call] = {"state": "starting", "entry": entry}
        save()
        try:
            sid = fork_session(me, f"Manager-Session-{number} ▸ {kind} · {title}", metadata=metadata, before=before)
        except BaseException:
            print(f"⚠️ native fork outcome for {me}, tool call {call}, needs inspection before another attempt; "
                  "the call's receipt is retained, never replayed.", flush=True)
            raise
        claims[call] = {"state": "allocated", "id": sid, "entry": entry}
        save()
        print(f"{'🚜 FORKLIFT' if forklift else '🍴 QUICK FORK WORKER'} {sid} · fork of {me} "
              f"(Manager-Session-{number}) · {model_ref_str(model)} · OpenCode · {title}", flush=True)
        if variant:
            set_model(sid, model)
        bell = ("none" if doorbell == "none" else "foreground" if not background else
                (doorbell or "launch") if forklift else "done")
        prompt = oc_seat.manager_fork_prompt(sid=sid, manager=me, session=number, model=model_ref_str(model),
                    effort=model.get("variant") or "default", cwd=session["location"]["directory"], title=title,
                    tool_use_id=call, forklift=forklift, bell=bell, runtime="opencode")
        if not background:
            prompt += (" OpenCode copied the completed history of this current turn before the creating foreground "
                       "step (the still-running shell itself is omitted); the creating call is identified above.")
        try:
            mid = send(sid, prompt)
        except BaseException:
            print(f"⚠️ fork {sid} exists; its first-input outcome is uncertain. Inspect `oc.sh seat info {sid}`; "
                  "never repeat the creating call.", flush=True)
            raise
        # Pending input remains live through the inbox even before OpenCode marks the fork active.
        code, reply = oc_request("PATCH", f"/api/session/{sid}", {"metadata": {**metadata, "fork_launch_pending": False,
                                                                              "fork_prompt": mid}})
        if code >= 400:
            print(f"⚠️ fork {sid} is started; clearing its launch-pending stamp failed ({code}). Inspect this id.", flush=True)
        claims[call].update(state="sent", message=mid)
        save()
    print(f"   first message {mid} · context {tokens:,}/{window:,} · boundary before {before}", flush=True)
    return oc_seat.ManagerFork(sid, call, background)
