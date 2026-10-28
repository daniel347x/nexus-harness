#!/usr/bin/env python3
"""oc_claude_view.py — Claude Code sessions for the observers (the boom, oc_peek, oc_search), read-only.

The observers read three things from OpenCode's server: the session list, the live status map and one session's
message history. This module gives them the same three for Claude Code sessions (`cc-…`, run by the local headless
host on Dan's Max subscription), in OpenCode's own shapes, so one renderer serves both backends:

  inventory()          every host record (one `list` call) as an OpenCode-shaped session dict: title, agent, model,
                       times and metadata from the host's immutable launch labels (program, socket, kind, quick,
                       parent_seat, conductor_sid …), plus the status of the live ones, computed by oc_claude.seat_phase
                       from the same record: busy · bg-wait (only background tasks, or a wake-up turn due) · starting ·
                       recovering · unknown. A session that is not live is absent from the map (= idle, as OpenCode's).
  messages(cc-…)       the history from the session's native Claude Code transcript (the path the host records as
                       `native_transcript_path_expected`), as OpenCode-shaped messages, oldest first: one `assistant`
                       message per model response (text · reasoning · tool parts, each tool call carrying its result),
                       prompts and steers as `user`, background-task notifications as `synthetic`. The transcript is
                       append-only, so a message index is a stable pin, as in OpenCode.
  session_messages()   either backend: OpenCode's API for `ses_…`, the transcript for `cc-…`.

Observation only: nothing here sends, launches, resumes or writes. The host is never started (no host socket file at
all = no Claude session exists); a host that does not answer is said loudly, never read as "no Claude sessions".
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PROJECTS = Path.home() / ".claude" / "projects"
_CANONICAL = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
FINISH = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool-calls", "max_tokens": "length"}
BACKGROUND_REASONS = ("background task", "wake-up turn is due")

_records: dict[str, dict] = {}   # the newest inventory's host records by id, for messages() and phase notes


def is_claude_id(sid) -> bool:
    return isinstance(sid, str) and sid.startswith("cc-")


def _ms(epoch) -> int:
    return int(epoch * 1000) if isinstance(epoch, (int, float)) and not isinstance(epoch, bool) and epoch > 0 else 0


# ------------------------------------------------------------------------------------ sessions and status

def session_of(record: dict) -> dict:
    """One host record → an OpenCode-shaped session dict (the fields the observers read)."""
    labels = record.get("labels") if isinstance(record.get("labels"), dict) else {}
    role = record.get("role")
    program = record.get("program") or (f"[ap:{labels['program_core']}]" if labels.get("program_core") else None)
    md = {
        "backend": "claude", "program": program, "socket": record.get("socket"),
        "kind": labels.get("kind") or (role if role in ("helper", "base") else None), "quick": labels.get("quick"),
        "parent_seat": record.get("parent") or labels.get("parent_seat"),
        "conductor_sid": labels.get("conductor_id") or record.get("owning_conductor"),
        "conductor_n": labels.get("conductor_n"), "manager_session": labels.get("manager_session"),
        "seat_role": labels.get("seat_role"), "variant": record.get("requested_effort"),
        # A fork's base: a Manager's quick fork worker is grouped under that Manager (oc_running, "fork source").
        "forked_from": record.get("forked_from"),
    }
    created = _ms(record.get("first_launched_at") or record.get("context_generation_started_at"))
    model = {"providerID": "anthropic", "id": record.get("requested_model") or "?"}
    if record.get("requested_effort"):
        model["variant"] = record["requested_effort"]
    return {
        "id": record.get("execution_id"), "backend": "claude",
        "title": labels.get("title") or ("Claude Manager conversation" if role == "Manager" else ""),
        "agent": record.get("agent") or ("manager" if role == "Manager" else None), "model": model,
        "time": {"created": created, "updated": _ms(record.get("last_activity_at")) or created},
        "metadata": {k: v for k, v in md.items() if v not in (None, "")},
    }


def phase_of(record: dict) -> tuple[str, list[str]]:
    """oc_claude.seat_phase on the list record alone (no receipt/recovery reads): the phase `oc.sh seat wait` uses."""
    from oc_claude import seat_phase
    try:
        return seat_phase(record)
    except Exception as e:  # noqa: BLE001 — one malformed record never hides the others
        return "unknown", [f"phase unreadable ({type(e).__name__})"]


def status_token(phase: str, reasons: list[str]) -> str | None:
    """The status-map value for a live phase (None = not live, i.e. idle): busy · bg-wait · starting · recovering ·
    unknown. bg-wait = running only because of background tasks (a Conductor waiting on its workers)."""
    if phase == "running":
        background = reasons and all(any(b in r for b in BACKGROUND_REASONS) for r in reasons)
        return "bg-wait" if background else "busy"
    return phase if phase in ("starting", "recovering", "unknown") else None


# @beacon[
#   id=oc-claude-view@inventory,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Every Claude host record as an OpenCode-shaped session plus the live-status map (busy · bg-wait · starting · recovering · unknown via seat_phase), from one `list` call; an unreachable host is ok False with a note, never "no Claude sessions".,
# ]
def inventory(host=None) -> dict:
    """{"ok", "sessions", "status", "note"} for every Claude session the host knows (any age).

    ok False = the host did not answer (its sessions are UNKNOWN, not absent); no host socket file at all = ok True and
    empty (no Claude session can exist). Each session dict also carries `claude: {phase, reasons}`."""
    import oc_backend
    try:
        host = host or oc_backend.connect(require_seats=False)
        rows = host.call("list", summary=True, all=True)
    except oc_backend.SeatError as e:
        if e.exit_code == oc_backend.EXIT_NO_HOST and not oc_backend.host_socket().exists():
            return {"ok": True, "sessions": [], "status": {}, "note": ""}
        return {"ok": False, "sessions": [], "status": {},
                "note": f"Claude host unavailable ({str(e).splitlines()[0][:160]}); Claude sessions UNKNOWN"}
    sessions, status = [], {}
    for record in rows if isinstance(rows, list) else []:
        if not isinstance(record, dict) or not is_claude_id(record.get("execution_id")):
            continue
        _records[record["execution_id"]] = record
        s = session_of(record)
        phase, reasons = phase_of(record)
        s["claude"] = {"phase": phase, "reasons": list(reasons)}
        token = status_token(phase, reasons)
        if token:
            status[s["id"]] = token
        sessions.append(s)
    return {"ok": True, "sessions": sessions, "status": status, "note": ""}


def phase_note(session: dict) -> str:
    """'' for an OpenCode session or a done Claude one; else the host's plain reasons, e.g.
    'claude: 2 background task(s)' or '⚠ needs attention: 1 pending escalation(s) …'."""
    info = (session or {}).get("claude") if isinstance(session, dict) else None
    if not isinstance(info, dict) or info.get("phase") in (None, "done"):
        return ""
    reasons = "; ".join(info.get("reasons") or []) or info["phase"]
    return ("⚠ needs attention: " if info["phase"] == "attention" else "claude: ") + reasons


def resolve_claude_session(handle: str, sessions: list[dict]) -> dict:
    """`cc-<uuid>`, a bare canonical UUID, or `cc-` + a unique ≥8-hex prefix among the listed Claude sessions → the
    session dict. Never fuzzy (oc_backend's cc- rule); SystemExit on no or several matches."""
    h = (handle or "").strip().strip("`'\"")
    body = (h[3:] if h.startswith("cc-") else h).lower()
    claude = {s["id"]: s for s in sessions if is_claude_id(s.get("id"))}
    if _CANONICAL.fullmatch(body) and "cc-" + body in claude:
        return claude["cc-" + body]
    flat = body.replace("-", "")
    if h.startswith("cc-") and len(flat) >= 8 and re.fullmatch(r"[0-9a-f]+", flat):
        hits = [s for sid, s in claude.items() if sid[3:].replace("-", "").startswith(flat)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise SystemExit(f"❌ {handle!r} is ambiguous ({len(hits)} Claude sessions start with it); give more "
                             "characters:\n" + "\n".join(f"  {s['id']}  {(s.get('title') or '')[:70]}" for s in hits[:8]))
    raise SystemExit(f"❌ no Claude session on the host matches {handle!r} (exact cc-… id, a bare UUID, or cc- plus at "
                     "least 8 hex characters). Copy the id from the tool output rather than retyping it.")


# ------------------------------------------------------------------------------------ history

def _stamp_ms(stamp) -> int:
    if not isinstance(stamp, str):
        return 0
    try:
        return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return 0


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    return ""


# @beacon[
#   id=oc-claude-view@transcript_messages,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Native Claude Code transcript records → OpenCode-shaped messages: one assistant per model response (blocks merged by message id, tool results attached to their calls, finish/completed from stop_reason), prompts/steers as user, task notifications as synthetic; meta and sidechain records skipped.,
# ]
def transcript_messages(records, agent: str | None = None) -> list[dict]:
    """Parsed transcript records (oldest first) → OpenCode-shaped messages that oc_client's msg_* helpers read.

    Claude Code writes one record per content block; blocks of one model response share `message.id` and may be
    interleaved with the results of tools already running, so they are merged by id at the response's first position.
    A tool_result record is not a message of its own (OpenCode keeps a tool's output in its call): it completes its
    call. A user record that is not a tool result is a prompt or a steer (`user`), or a background-task notification
    (`synthetic`: an incoming return, like a worker's in OpenCode). Attachments, API request records, queue
    bookkeeping, meta and sidechain records are not conversation."""
    out: list[dict] = []
    responses: dict[str, dict] = {}
    calls: dict[str, dict] = {}
    for r in records:
        if not isinstance(r, dict) or r.get("isSidechain") or r.get("isMeta"):
            continue
        kind, at = r.get("type"), _stamp_ms(r.get("timestamp"))
        msg = r.get("message") if isinstance(r.get("message"), dict) else {}
        content = msg.get("content")
        if kind == "assistant":
            mid = str(msg.get("id") or r.get("uuid") or len(out))
            m = responses.get(mid)
            if m is None:
                m = {"id": mid, "type": "assistant", "time": {"created": at}, "content": [],
                     "model": {"providerID": "anthropic", "id": msg.get("model") or "?"}}
                if agent:
                    m["agent"] = agent
                responses[mid] = m
                out.append(m)
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    m["content"].append({"type": "text", "text": b.get("text") or ""})
                elif b.get("type") == "thinking":
                    m["content"].append({"type": "reasoning", "text": b.get("thinking") or ""})
                elif b.get("type") == "tool_use":
                    part = {"type": "tool", "name": b.get("name") or "?", "callID": b.get("id"),
                            "state": {"status": "running", "input": b.get("input")}}
                    m["content"].append(part)
                    if b.get("id"):
                        calls[b["id"]] = part
            stop = msg.get("stop_reason")
            if stop:
                m["finish"] = FINISH.get(stop, stop)
                if m["finish"] == "stop":
                    m["time"]["completed"] = at
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
            if usage:
                m["tokens"] = {"input": usage.get("input_tokens") or 0, "output": usage.get("output_tokens") or 0,
                               "reasoning": 0, "cache": {"read": usage.get("cache_read_input_tokens") or 0,
                                                         "write": usage.get("cache_creation_input_tokens") or 0}}
        elif kind == "user":
            blocks = content if isinstance(content, list) else []
            results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            for b in results:
                part = calls.get(b.get("tool_use_id"))
                if part is None:
                    continue
                text = _result_text(b.get("content"))
                part["state"].update(status="error" if b.get("is_error") else "completed",
                                     content=[{"type": "text", "text": text}])
                if b.get("is_error"):
                    part["state"]["error"] = text[:2000]
            if results:
                continue
            text = content if isinstance(content, str) else _result_text(blocks)
            origin = r.get("origin") if isinstance(r.get("origin"), dict) else {}
            notice = origin.get("kind") == "task-notification" or r.get("turnOrigin") == "task_notification"
            out.append({"id": r.get("uuid"), "type": "synthetic" if notice else "user", "time": {"created": at},
                        "text": text})
    return out


def transcript_path(execution_id: str, record: dict | None = None) -> Path | None:
    """The session's native transcript: the host record's expected path, else <uuid>.jsonl under one project folder."""
    expected = (record or {}).get("native_transcript_path_expected")
    if expected and Path(expected).is_file():
        return Path(expected)
    uuid = execution_id[3:] if is_claude_id(execution_id) else ""
    if not _CANONICAL.fullmatch(uuid):
        return None
    try:
        for folder in PROJECTS.iterdir():
            candidate = folder / (uuid + ".jsonl")
            if candidate.is_file():
                return candidate
    except OSError:
        return None
    return None


def _record(execution_id: str) -> dict | None:
    if execution_id in _records:
        return _records[execution_id]
    try:
        import oc_backend
        record = oc_backend.connect(require_seats=False).call("state", execution_id=execution_id)
    except Exception:  # noqa: BLE001 — the transcript may still be found without the record
        return None
    if isinstance(record, dict):
        _records[execution_id] = record
        return record
    return None


def messages(execution_id: str) -> list[dict]:
    """The whole history of one Claude session, oldest first (see transcript_messages). Raises when the transcript
    cannot be found or read: never an empty history passed off as 'nothing said'."""
    record = _record(execution_id)
    path = transcript_path(execution_id, record)
    if path is None:
        raise RuntimeError(f"{execution_id}: native transcript not found (expected "
                           f"{(record or {}).get('native_transcript_path_expected') or 'under ~/.claude/projects'})")
    records = []
    with open(path, "rb") as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except ValueError:
                continue   # the newest line may be mid-write
    return transcript_messages(records, agent=(record or {}).get("agent"))


def session_messages(sid: str) -> list[dict]:
    """Either backend's history, oldest first: the native transcript for `cc-…`, OpenCode's API for `ses_…`."""
    if is_claude_id(sid):
        return messages(sid)
    from oc_client import oc_messages
    return oc_messages(sid)
