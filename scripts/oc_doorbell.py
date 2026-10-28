#!/usr/bin/env python3
"""oc_doorbell.py — one Manager holds a doorbell at a time (the Manager side of `oc_seat.sh wait`).

A DOORBELL is a background `oc.sh seat wait ‹id›` a conversation starts so that it is woken when that session stops.
It wakes only the conversation that started it. When Dan moves to a newer Manager Session and the earlier Manager forgot
to stop its doorbells, the earlier one would still be woken. So, for waiters started by a MANAGER only:

- A Manager arming a doorbell on a target that an EARLIER Manager conversation is waiting on closes the earlier one's
  waiter: that waiter prints a handed-over line and exits EXIT_HANDED_OVER (9), so the earlier conversation wakes
  once, reads that it was handed over, and stops. `--keep-others` leaves the earlier waiters running.
- An EARLIER Manager arming (or re-arming after exit 2) a doorbell a newer Manager holds is refused with the same exit
  code and line, unless `--force`.
- Every other waiter (Conductors, lead seats, workers, helpers, Dan's terminal) is untouched: it is never registered,
  never closed, and never closes a Manager's.

"Earlier" is the conversation's own start time (the Claude host's first_launched_at, OpenCode's session time.created),
never the Manager-Session number in .oc_config.json: that file names the NEWEST session, so an old conversation reading
it would claim the new number. The number is only a label (`--session NNN` on oc_seat, else the config).

The caller is identified from its process ancestry, never guessed: the nearest ancestor that is this Claude Code
process (CLAUDE_PID, with CC_CONTEXT_EXECUTION_ID naming its host execution) or an OpenCode server (whose shell tool
sets OPENCODE_SESSION_ID). A Claude caller is a Manager when its host record's role is `Manager`; an OpenCode caller
when its session runs a Manager agent with no parent and no seat metadata. Anything unidentifiable waits as an
ordinary doorbell (with one note line when an identity was claimed but could not be read).

Registry: one JSON file per target under ~/.local/state/opencode-companion/doorbells/ (OC_DOORBELL_DIR overrides),
read-modify-written under an flock: the claim (the newest Manager conversation that armed it), the live Manager waiters
(pid plus the kernel's process start time, so a reused pid is never mistaken for a live waiter) and the handovers
(which closed waiter was handed to whom). Waiters check their own handover on every poll lap (a file read, no signal).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oc_backend import EXIT_HANDED_OVER  # noqa: E402

MANAGER_AGENTS = ("manager", "manager-gpt")   # = oc_running.MANAGER_AGENTS: the agents a Manager conversation runs as
CLAIM_TTL_S = 14 * 86400                      # a target file untouched this long, with no live waiter, is removed
_CC_ID = re.compile(r"cc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def state_dir() -> Path:
    return Path(os.environ.get("OC_DOORBELL_DIR") or Path.home() / ".local/state/opencode-companion/doorbells")


@dataclass(frozen=True)
class Caller:
    conversation: str   # cc-… or ses_…
    backend: str        # claude · opencode
    started: float     # epoch seconds the conversation began: the order between Managers
    label: str          # 'Manager-Session-193'

    def name(self) -> str:
        return f"{self.label} ({self.conversation[:11]}…, started {_hhmm(self.started)})"


def _hhmm(t: float | None) -> str:
    try:
        return datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


# ------------------------------------------------------------------------------------ processes

def proc_start(pid: int) -> str | None:
    """The kernel's start time of `pid` (clock ticks since boot, /proc/PID/stat field 22), or None when it is gone.
    A pid plus its start time names one process for good: a reused pid has another start time."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = raw[raw.rfind(")") + 2:].split()
    return fields[19] if len(fields) > 19 else None


def _parent(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = raw[raw.rfind(")") + 2:].split()
    return int(fields[1]) if len(fields) > 1 else None


def _comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def alive(entry: dict) -> bool:
    pid, start = entry.get("pid"), entry.get("pid_start")
    return isinstance(pid, int) and pid > 0 and start is not None and proc_start(pid) == start


def nearest_runtime(env: dict, pid: int | None = None) -> str | None:
    """'claude' or 'opencode': the runtime whose process is this waiter's nearest ancestor. Environment variables alone
    cannot say it (an OpenCode server started from a Claude Code shell, or the reverse, inherits the other's)."""
    claude_pid = str(env.get("CLAUDE_PID") or "")
    pid = os.getpid() if pid is None else pid
    for _ in range(64):
        pid = _parent(pid)
        if not pid or pid <= 1:
            return None
        if str(pid) == claude_pid:
            return "claude"
        if _comm(pid).startswith("opencode"):
            return "opencode"
    return None


# ------------------------------------------------------------------------------------ who is calling

def _claude_state(eid: str) -> dict:
    import oc_claude
    return oc_claude.connect(require_seats=False).call("state", execution_id=eid)


def _opencode_session(sid: str) -> dict:
    from oc_client import oc_get
    return oc_get(f"/api/session/{sid}", timeout=20)


# @beacon[
#   id=oc-doorbell@identify_caller,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Is this wait started by a Manager conversation, and which one? Nearest runtime ancestor (CLAUDE_PID / an opencode server) → host record role Manager or an OpenCode Manager-agent session → Caller(conversation, started, label); anything else None (one note when an identity could not be read). Never guesses.,
# ]
def identify_caller(label: str, env: dict | None = None, *, runtime: str | None = None,
                    claude_state: Callable[[str], dict] = _claude_state,
                    opencode_session: Callable[[str], dict] = _opencode_session) -> tuple[Caller | None, str | None]:
    """(the calling Manager conversation, or None for every other caller; a note line when it could not be told)."""
    env = os.environ if env is None else env
    runtime = runtime or nearest_runtime(env)
    if runtime == "claude":
        eid = str(env.get("CC_CONTEXT_EXECUTION_ID") or "")
        if not _CC_ID.fullmatch(eid):
            return None, None                      # a Claude Code session the host did not launch: not a Manager of ours
        try:
            record = claude_state(eid)
        except Exception as e:  # noqa: BLE001
            if "unknown exact execution ID" in str(e):
                return None, None                  # another host's session (an isolated test host): not ours
            return None, (f"(note: doorbell registry skipped: could not read this conversation's host record "
                          f"({str(e).splitlines()[0][:160]}); waiting as an ordinary doorbell)")
        if record.get("role") != "Manager":
            return None, None
        started = record.get("first_launched_at")
        if not isinstance(started, (int, float)):
            return None, "(note: doorbell registry skipped: this Manager's host record has no launch time)"
        return Caller(eid, "claude", float(started), label), None
    if runtime == "opencode":
        sid = str(env.get("OPENCODE_SESSION_ID") or "")
        if not sid.startswith("ses_"):
            return None, None
        try:
            s = opencode_session(sid)
        except Exception as e:  # noqa: BLE001
            return None, (f"(note: doorbell registry skipped: could not read this session ({str(e)[:160]}); "
                          "waiting as an ordinary doorbell)")
        md = (s or {}).get("metadata") or {}
        if (s or {}).get("agent") not in MANAGER_AGENTS or s.get("parentID") or md.get("quick") or md.get("kind"):
            return None, None
        created = ((s.get("time") or {}).get("created"))
        if not isinstance(created, (int, float)):
            return None, "(note: doorbell registry skipped: this Manager session has no creation time)"
        return Caller(sid, "opencode", float(created) / 1000.0, label), None
    return None, None


# ------------------------------------------------------------------------------------ the registry

def _path(target: str) -> Path:
    return state_dir() / (re.sub(r"[^A-Za-z0-9_.-]", "_", target) + ".json")


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)                          # readers outside the lock always see a whole file


class _Locked:
    def __init__(self, target: str):
        self.path = _path(target)

    def __enter__(self) -> Path:
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path.with_suffix(".lock")), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self.path

    def __exit__(self, *exc) -> None:
        os.close(self.fd)                          # closing releases the flock


def _sweep(now: float) -> None:
    """Remove other targets' files untouched for CLAIM_TTL_S with no live waiter (best effort)."""
    for f in state_dir().glob("*.json"):
        try:
            if now - f.stat().st_mtime > CLAIM_TTL_S and not any(alive(w) for w in _load(f).get("waiters") or []):
                f.unlink()
                f.with_suffix(".lock").unlink(missing_ok=True)
        except OSError:
            pass


@dataclass
class Bell:
    """One Manager waiter's registration. `refused` holds the lines to print when it may not arm."""
    target: str
    me: dict
    lines: list
    refused: bool = False

    def handed_over(self) -> str | None:
        """The handed-over line when a newer Manager has closed this waiter; checked on every poll lap."""
        for h in _load(_path(self.target)).get("handovers") or []:
            if h.get("pid") == self.me["pid"] and h.get("pid_start") == self.me["pid_start"]:
                return handed_line(self.target, h.get("to_label"), h.get("to_conversation"), h.get("at"))
        return None

    def disarm(self) -> None:
        if self.refused:
            return
        try:
            with _Locked(self.target) as path:
                data = _load(path)
                mine = lambda e: e.get("pid") == self.me["pid"] and e.get("pid_start") == self.me["pid_start"]  # noqa: E731
                data["waiters"] = [w for w in data.get("waiters") or [] if not mine(w)]
                data["handovers"] = [h for h in data.get("handovers") or [] if not mine(h)]
                _save(path, data)
        except OSError:
            pass


def handed_line(target: str, to_label: str | None, to_conversation: str | None, at: float | None) -> str:
    return (f"🔕 doorbell on {target} HANDED OVER to {to_label or 'a newer Manager'} "
            f"({str(to_conversation or '?')[:11]}…) at {_hhmm(at)}: this conversation no longer holds it. "
            f"If you are an earlier Manager Session, write nothing about it and stop; the newer session owns it. "
            f"(exit {EXIT_HANDED_OVER}: handed over, not done)")


# @beacon[
#   id=oc-doorbell@arm,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Register a Manager's waiter on one target under the flock: refuse it (exit 9) when a newer Manager conversation holds the claim (unless --force or that holder kept others); otherwise close every earlier Manager's live waiter by recording a handover (unless --keep-others), take the claim and add itself.,
# ]
def arm(target: str, caller: Caller, *, keep_others: bool = False, force: bool = False, now: float | None = None,
        pid: int | None = None) -> Bell:
    now = time.time() if now is None else now
    pid = os.getpid() if pid is None else pid
    me = {"pid": pid, "pid_start": proc_start(pid), **asdict(caller), "armed_at": now}
    with _Locked(target) as path:
        data = _load(path) or {"target": target}
        waiters = [w for w in data.get("waiters") or [] if alive(w)]
        handovers = [h for h in data.get("handovers") or [] if alive(h)]
        claim = data.get("claim") or {}
        newer = (claim.get("conversation") not in (None, caller.conversation)
                 and float(claim.get("started") or 0) > caller.started)
        if newer and not force and not claim.get("keep_others"):
            data.update(waiters=waiters, handovers=handovers)
            _save(path, data)
            return Bell(target, me, [handed_line(target, claim.get("label"), claim.get("conversation"),
                                                 claim.get("claimed_at"))
                                     + " (--force arms it here anyway)"], refused=True)
        lines, closed = [], {}
        if not keep_others:
            for w in waiters:
                if w.get("conversation") != caller.conversation and float(w.get("started") or 0) < caller.started:
                    handovers.append({"pid": w["pid"], "pid_start": w.get("pid_start"),
                                      "conversation": w.get("conversation"), "label": w.get("label"),
                                      "to_conversation": caller.conversation, "to_label": caller.label, "at": now})
                    closed.setdefault(w.get("conversation"), (w.get("label"), []))[1].append(w["pid"])
            waiters = [w for w in waiters if w.get("conversation") not in closed]
        for conv, (label, pids) in closed.items():
            lines.append(f"🔔 closed {label} ({str(conv)[:11]}…)'s doorbell on {target} (pid "
                         f"{', '.join(map(str, pids))}): it rings here now; that conversation wakes once, told it was "
                         "handed over (--keep-others leaves earlier Managers' doorbells running)")
        if not newer:
            data["claim"] = {**asdict(caller), "claimed_at": now, "keep_others": bool(keep_others)}
        data.update(waiters=waiters + [me], handovers=handovers)
        _save(path, data)
    _sweep(now)
    return Bell(target, me, lines)
