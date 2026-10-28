"""Read-only OpenCode V2 conversations projected onto the pane's existing event rows.

Wire text is the snapshot authority. The LRU is rebuildable, never a history store;
epochs live only as long as their index. Nothing here polls, renders, or sends.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import pathlib
import re
import sys
import threading
import time
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from opencode_programs import SESSION_ID, Unavailable, default_request
from tool_diff import opencode_diff

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from claude_headless.lexical import ReaderError, parse_record, preview_data  # noqa: E402

REF = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
SAFE_INTEGER = 2**53 - 1
UPSTREAM_LIMIT = 16 * 1024 * 1024
RESPONSE_LIMIT = 8 * 1024 * 1024
SESSION_LIMIT = 64 * 1024 * 1024
CACHE_LIMIT = 128 * 1024 * 1024
CACHE_SESSIONS = 4
PAGE_ROWS = 100
RUNNING_FIELDS = ("callRef", "id", "messageId", "partIndex", "name", "status",
                  "previewData", "inputBytes", "outputBytes", "time", "resultTime")
PROVIDER_FIELDS = frozenset(("reasoningEncryptedContent", "providerState", "providerResultState", "providerContext"))
FLAGS = frozenset(("compaction", "agent-switched", "model-switched", "location-switched"))


def encoded(value):
    """Compact UTF-8 JSON; escape lone surrogates without changing stored strings."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8", "backslashreplace")


def text_bytes(text):
    """TextEncoder's UTF-8 count: each unpaired UTF-16 surrogate counts as U+FFFD."""
    return len(text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace").encode("utf-8"))


def raw_members(text):
    """Direct wire lexemes of validated JSON, with last-key semantics like JS rawMembers.

    The stdlib decoder finds spans without a preview parser's node/depth ceilings.
    Opaque payload duplicate keys and numeric spellings remain in each raw value.
    """
    text = text.strip()
    if not text or text[0] not in "[{":
        return {}
    decoder, i, index, fields = json.JSONDecoder(), 1, 0, {}
    while i < len(text):
        while text[i] in " \t\r\n":
            i += 1
        if text[i] in "}]":
            break
        key = index
        index += 1
        if text[0] == "{":
            key, i = decoder.raw_decode(text, i)
            while text[i] in " \t\r\n":
                i += 1
            i += 1
            while text[i] in " \t\r\n":
                i += 1
        _, end = decoder.raw_decode(text, i)
        fields[key] = text[i:end]
        i = end
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if i == len(text) or text[i] != ",":
            break
        i += 1
    return fields


def stored_text(wire):
    return json.loads(wire) if wire.startswith('"') else wire


# @beacon[
#   id=opc-pane@snapshot_text,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Server port of packaged toolSnapshotText: retain raw lexemes and decoded strings including lone surrogates; count UTF-8 like TextEncoder; Node differential tests own parity.,
# ]
def snapshot_text(state_wire):
    """Keep JS snapshot text verbatim; TextEncoder counts a lone surrogate as 3 bytes."""
    state = json.loads(state_wire)
    state = state if isinstance(state, dict) else {}
    raw = raw_members(state_wire)
    has_input, has_content = "input" in state, "content" in state
    has_output, has_error = has_content or "output" in state, "error" in state
    input_text = stored_text(raw["input"]) if has_input else "(input not available)"
    output = "(output not available)"
    if has_content:
        if isinstance(state["content"], list):
            blocks = raw_members(raw["content"])
            output = "\n\n".join(
                block["text"] if isinstance(block, dict) and block.get("type") == "text" and
                isinstance(block.get("text"), str) else blocks[i]
                for i, block in enumerate(state["content"]))
        else:
            output = stored_text(raw["content"])
    elif has_output:
        output = stored_text(raw["output"])
    if has_error:
        output = "Error:\n" + stored_text(raw["error"]) + ("\n\nAvailable output:\n" + output if has_output else "")
    status = state.get("status") or "unknown"
    return {"input": input_text, "output": output, "status": status,
            "pending": status in ("streaming", "pending"), "hasInput": has_input,
            "hasOutput": has_output, "hasError": has_error,
            "inputBytes": text_bytes(input_text) if has_input else None,
            "outputBytes": text_bytes(output) if has_output or has_error else None}


def safe_ref(value):
    return isinstance(value, str) and REF.fullmatch(value) is not None


def row_ref(message_id, slot):
    value = message_id + "_" + slot
    # Hash bytes only, never an HTTP body: preserve distinct stored identifiers.
    return value if safe_ref(value) else "ocrow_" + hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def call_ref(session_id, message_id, index, part_id):
    return "occall_" + hashlib.sha256(encoded([1, session_id, message_id, index, part_id])).hexdigest()


def object_value(value):
    return value if isinstance(value, dict) else {}


def string_value(value):
    return value if isinstance(value, str) else None


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def integer(value):
    return value if type(value) is int and 0 <= value <= SAFE_INTEGER else None


def settled(message):
    times = object_value(message.get("time"))
    return bool(message.get("finish")) or number(times.get("completed")) is not None


def display_facts(value):
    """Exclude continuation envelopes, not operational tool input/output strings or keys."""
    if isinstance(value, list):
        return [display_facts(v) for v in value]
    if not isinstance(value, dict):
        return value
    return {k: (v if k == "state" and value.get("type") == "tool" else display_facts(v))
            for k, v in value.items() if k not in PROVIDER_FIELDS and
            not (k == "state" and value.get("type") in ("text", "reasoning"))}


def detail(value):
    return json.dumps(display_facts(value), ensure_ascii=False, indent=2, allow_nan=False)


def encrypted(value):
    if isinstance(value, dict):
        return bool(value.get("reasoningEncryptedContent")) or any(encrypted(v) for v in value.values())
    return isinstance(value, list) and any(encrypted(v) for v in value)


@dataclass
class Message:
    data: dict
    wire: str

    @classmethod
    def read(cls, wire):
        data = json.loads(wire)
        if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
            raise ValueError("message id unavailable")
        return cls(data, wire)


def page_messages(raw, page, enveloped):
    """Message objects from a raw message page, keeping each row's exact wire; None when rows do not line up.

    The page's parsed `data` rows and the raw lexemes must be one-for-one: a mismatch is an unreadable page, never a
    guessed mapping (the glance and the activity detail call this, and both are display only)."""
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        wire = raw_members(text).get("data") if enveloped else text
        wires = list(raw_members(wire).values()) if isinstance(wire, str) else []
        if (not isinstance(page, list) or len(wires) != len(page) or
                any(not isinstance(message, dict) for message in page)):
            return None
        return [Message(message, source) for message, source in zip(page, wires)]
    except (ValueError, UnicodeError, RecursionError, IndexError, TypeError, AttributeError):
        return None


def event(message, slot, kind, text="", *, part=None, **facts):
    mid = message["id"]
    times = object_value(message.get("time"))
    part_time = object_value(object_value(part).get("time"))
    stamp = number(part_time.get("created"))
    if stamp is None:
        stamp = number(times.get("created"))
    model = object_value(message.get("model"))
    ref = row_ref(mid, slot)
    return {"recordRef": ref, "uuid": ref, "seq": 0, "kind": kind, "scope": "main",
            "status": "stored", "text": text, "textBytes": text_bytes(text),
            "partial": False, "time": stamp / 1000 if stamp is not None else None,
            "thinking": [], "calls": [], "orphanResults": [], "toolResults": 0,
            "isMeta": kind not in ("user", "assistant"), "originKind": None,
            "model": string_value(model.get("id")), "effort": string_value(model.get("variant")),
            "agent": string_value(message.get("agent")), "receipt": None, **facts}


def call_status(state):
    """A tool part's state as the event rows' call status (shared with the phone's tool snapshot)."""
    status = string_value(state.get("status"))
    return {"streaming": "not-final-fragment", "running": "pending-or-missing",
            "completed": "stored", "error": "error"}.get(status, "unknown · " + str(status or "missing"))


def call_times(message, part, status):
    """A tool part's (time, resultTime) in seconds, shared by its row and its snapshot (display only: how long the
    call took): the part's creation, else its message's; its completion only once the call is over."""
    times = object_value(part.get("time"))
    started, ended = number(times.get("created")), number(times.get("completed"))
    if started is None:
        started = number(object_value(message.get("time")).get("created"))
    terminal = status in ("stored", "error") or settled(message)
    return (started / 1000 if started is not None else None,
            ended / 1000 if ended is not None and terminal else None)


def call_summary(session_id, message, part, index, state_wire):
    """One tool part's compact call facts, shared by the conversation row, a running call and the glance.

    `state_wire` is that part's exact stored state lexeme, so the preview and byte counts are the row's own."""
    state = object_value(part.get("state"))
    snapshot = snapshot_text(state_wire)
    status = call_status(state)
    child = object_value(state.get("metadata")).get("sessionID") if part.get("name") == "subagent" else None
    started, ended = call_times(message, part, status)
    return {"callRef": call_ref(session_id, message["id"], index, part["id"]), "block": index, "id": part["id"],
            "name": string_value(part.get("name")) or "unknown tool", "status": status,
            "previewData": input_preview(state_wire), "inputBytes": snapshot["inputBytes"],
            "outputBytes": snapshot["outputBytes"], "time": started, "resultTime": ended,
            "messageId": message["id"], "partIndex": index,
            "childSessionId": child if isinstance(child, str) and SESSION_ID.fullmatch(child) else None}


def shell_command(part):
    """The full stored shell command for wait recognition, not a cropped preview; None for other calls."""
    if string_value(part.get("name")) != "shell":
        return None
    command = object_value(object_value(part.get("state")).get("input")).get("command")
    return command if isinstance(command, str) and command else None


def input_preview(state_wire):
    raw = raw_members(state_wire).get("input")
    if raw is None:
        return preview_data("", None)
    try:
        text, root = parse_record(('{"input":' + raw + '}').encode("utf-8"))
        return preview_data(text, root.members()["input"])
    except (ValueError, UnicodeError, RecursionError):
        return {"schemaVersion": 1, "status": "unavailable", "reason": "input lexical preview limit"}


def message_rows(session_id, source):
    m = source.data
    kind, mid = m.get("type"), m["id"]
    if kind == "assistant":
        content = m.get("content") if isinstance(m.get("content"), list) else []
        wire_parts = raw_members(raw_members(source.wire).get("content", "[]"))
        ids = Counter(p.get("id") for p in content if isinstance(p, dict) and isinstance(p.get("id"), str))
        rows = []
        for i, item in enumerate(content):
            p = object_value(item)
            typ = p.get("type")
            row = event(m, "p" + str(i), "assistant", part=p)
            if typ == "text":
                text = p.get("text") if isinstance(p.get("text"), str) else "OpenCode · text unavailable\n" + detail(p)
                row.update(text=text, textBytes=text_bytes(text),
                           partial=not (settled(m) or number(object_value(m.get("time")).get("streamed")) is not None))
            elif typ == "reasoning":
                text = p.get("text") if isinstance(p.get("text"), str) else ""
                times = object_value(p.get("time"))
                streaming = number(times.get("completed")) is None if times else not settled(m)
                redacted = not text and not streaming and encrypted(p.get("state"))
                origin = "encrypted-reasoning" if redacted else "empty-reasoning" if not text else None
                row.update(partial=streaming, originKind=origin, thinking=[{
                    "blockIndex": i, "availability": "redacted" if redacted else "stored",
                    "streaming": streaming, "text": None if redacted else text}])
            elif typ == "tool" and isinstance(p.get("id"), str) and p["id"] and ids[p["id"]] == 1:
                state = object_value(p.get("state"))
                state_wire = raw_members(wire_parts[i]).get("state", "{}")
                row.update(partial=state.get("status") in ("streaming", "running"),
                           calls=[call_summary(session_id, m, p, i, state_wire)])
            else:
                label = "malformed tool · no unique part id" if typ == "tool" else "unknown part · " + str(typ or "unknown")
                text = "OpenCode · " + label + "\n" + detail(item)
                row.update(originKind=str(typ or "unknown"), text=text, textBytes=text_bytes(text), isMeta=True)
            rows.append(row)
        if not content:
            text = "Assistant empty step · no content recorded" if settled(m) else "Assistant step started · no content yet"
            rows.append(event(m, "p0", "assistant", text, originKind="assistant-empty", partial=not settled(m)))
        if "error" in m or "retry" in m:
            text = "OpenCode · assistant state\n" + detail({k: m[k] for k in ("error", "retry") if k in m})
            rows.append(event(m, "state", "task", "description: OpenCode · assistant state\n" + text,
                              originKind="assistant-state"))
        return rows
    if kind == "idle":
        return [event(m, "m", "result", originKind="idle")]
    if kind in FLAGS:
        text = "description: OpenCode · " + kind + "\n" + detail({k: v for k, v in m.items() if k not in ("id", "type", "time")})
        return [event(m, "m", "task", text, originKind=kind)]
    if kind in ("user", "synthetic", "system", "skill"):
        text = m.get("text") if isinstance(m.get("text"), str) else ""
        extras = {k: v for k, v in m.items() if k not in ("id", "type", "time", "text") and k not in PROVIDER_FIELDS}
        if extras:
            text += ("\n\n" if text else "") + "OpenCode · recorded facts\n" + detail(extras)
        if kind != "user":
            text = "OpenCode · " + kind + "\n" + text
        return [event(m, "m", kind, text, originKind=None if kind == "user" else kind)]
    if kind == "shell":
        lines = ["OpenCode · shell"]
        for key in ("command", "status", "exit"):
            if key in m:
                lines.append(key + ": " + (m[key] if isinstance(m[key], str) else detail(m[key])))
        output = m.get("output")
        body = output.get("output") if isinstance(output, dict) else output
        if "output" in m:
            lines.append("output:\n" + (body if isinstance(body, str) else detail(body)))
        extras = {k: v for k, v in m.items() if k not in ("id", "type", "time", "command", "status", "exit", "output")}
        if isinstance(output, dict):
            extras["output facts"] = {k: v for k, v in output.items() if k != "output"}
        if extras:
            lines.append(detail(extras))
        return [event(m, "m", "shell", "\n".join(lines), originKind="shell", partial=m.get("status") == "running")]
    label = "unknown message · " + str(kind or "unknown")
    text = "OpenCode · " + label + "\n" + detail({k: v for k, v in m.items() if k not in ("id", "time", "type")})
    return [event(m, "m", string_value(kind) or "unknown", text, originKind=string_value(kind) or "unknown",
                  partial=False)]


# @beacon[
#   id=opc-pane@running_calls,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Current-turn running/streaming tool parts only, after the latest idle receipt in unsettled steps and only while busy is positively true; all parallel siblings retain their row facts and full shell command for wait recognition, independent of the history window, while unknown/terminal states and prior-turn orphans never claim running.,
# ]
def running_calls(session_id, messages, busy, *, newest_first=False):
    """Every still-open tool call in the turn after the latest idle message, or [] unless busy is true."""
    if busy is not True:
        return []
    if newest_first:
        messages = list(reversed(messages))
    last_idle = max((i for i, source in enumerate(messages) if source.data.get("type") == "idle"), default=-1)
    calls = []
    for source in messages[last_idle + 1:]:
        m = source.data
        if m.get("type") != "assistant" or settled(m):
            continue
        content = m.get("content") if isinstance(m.get("content"), list) else []
        wire_parts = raw_members(raw_members(source.wire).get("content", "[]"))
        ids = Counter(p.get("id") for p in content if isinstance(p, dict) and isinstance(p.get("id"), str))
        for i, item in enumerate(content):
            p = object_value(item)
            pid = p.get("id")
            if p.get("type") != "tool" or not isinstance(pid, str) or not pid or ids[pid] != 1:
                continue
            status = call_status(object_value(p.get("state")))
            if status not in ("pending-or-missing", "not-final-fragment"):
                continue
            state_wire = raw_members(wire_parts.get(i, "{}")).get("state", "{}")
            call = call_summary(session_id, m, p, i, state_wire)
            summary = {key: call[key] for key in RUNNING_FIELDS}
            command = shell_command(p)
            if command is not None:
                summary["command"] = command
            calls.append(summary)
    return calls


# @beacon[
#   id=opc-pane@glance_call,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=The glance's tool call (OpenCode): from a session's newest messages, newest first, the newest assistant step that made calls; its last call still open (no stored result, the step not settled) if any, else its last call; the same name, status, preview data and times as that call's conversation row (message_rows). Display only.,
# ]
def glance_call(session_id, sources):
    """The running-or-latest tool call of a session from its newest messages (newest first), or None."""
    for source in sources:
        if source.data.get("type") != "assistant":
            continue
        calls = [call for row in message_rows(session_id, source) for call in row["calls"]]
        if not calls:
            continue
        settled_step = settled(source.data)
        for call in calls:
            call["open"] = call["status"] not in ("stored", "error") and not settled_step
        shown = next((c for c in reversed(calls) if c["open"]), calls[-1])
        return {k: shown[k] for k in ("name", "status", "previewData", "time", "resultTime", "open")}
    return None


def receipt(steps, outcome):
    costs = [number(step.get("cost")) for step in steps if step.get("type") == "assistant" or
             step.get("type") == "compaction" and ("model" in step or "cost" in step or step.get("status") in ("completed", "failed"))]
    last = next((s for s in reversed(steps) if s.get("type") == "assistant"), {})
    tokens = object_value(last.get("tokens"))
    cache = object_value(tokens.get("cache"))
    return {"subtype": outcome if isinstance(outcome, str) and outcome else "unknown",
            "is_error": True if outcome == "failed" else False if outcome == "succeeded" else None,
            "total_cost_usd": sum(costs) if costs and all(c is not None for c in costs) else None,
            "usage": {"input_tokens": integer(tokens.get("input")), "output_tokens": integer(tokens.get("output")),
                      "reasoning_tokens": integer(tokens.get("reasoning")), "cache_read_input_tokens": integer(cache.get("read")),
                      "cache_creation_input_tokens": integer(cache.get("write"))}}


# @beacon[
#   id=opc-pane@build_rows,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Pure complete V2 message/part mapping: stable slots, visible unknowns and encrypted/empty blocks, exact tool snapshots, idle/legacy receipts and dense positions.,
# ]
def build_rows(session_id, messages):
    """Every message and assistant slot is represented; no glance/valuable-text filter."""
    rows, turn = [], []
    last_idle = max((i for i, s in enumerate(messages) if s.data.get("type") == "idle"), default=-1)
    for i, source in enumerate(messages):
        m = source.data
        mapped = message_rows(session_id, source)
        turn.append(m)
        if m.get("type") == "idle":
            mapped[0]["receipt"] = receipt(turn, m.get("outcome"))
            turn = []
        elif i > last_idle and m.get("type") == "assistant" and m.get("finish") == "stop" and settled(m):
            mapped.append(event(m, "r", "result", receipt=receipt(turn, "failed" if "error" in m else "succeeded"),
                                originKind="legacy-stop"))
            turn = []
        rows.extend(mapped)
    for seq, row in enumerate(rows):
        row["seq"] = seq
    return rows


@dataclass
class Index:
    session: dict
    messages: list
    rows: list
    stamps: list
    epoch: str
    revision: int
    retained: int
    raw_bytes: int
    busy: bool | None
    running: list
    observed_at: float | None = None


def topology_same(old, messages, rows):
    old_ids = [m.data["id"] for m in old.messages]
    if [m.data["id"] for m in messages[:len(old_ids)]] != old_ids:
        return False
    if [r["uuid"] for r in rows[:len(old.rows)]] != [r["uuid"] for r in old.rows]:
        return False
    for before, after in zip(old.messages, messages):
        a, b = before.data, after.data
        if a.get("type") != b.get("type"):
            return False
        if a.get("type") != "assistant":
            continue
        def slots(m):
            return [(object_value(p).get("type"), object_value(p).get("id") if object_value(p).get("type") == "tool" else None)
                    for p in (m.get("content") or [])]
        left, right = slots(a), slots(b)
        if right[:len(left)] != left or len(right) < len(left):
            return False
    return True


# @beacon[
#   id=opc-pane@OpenCodeConversation,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Authenticated GET-only conversation reader with exact wire snapshots, tail revalidation and reader-owned Inspect witnesses; every page also carries all current in-flight tool summaries and the actual busy-observation timestamp, independent of the history window.,
# ]
class OpenCodeConversation:
    """Read stored rows and current open calls with exact wire sizes, Inspect facts and observation provenance."""

    def __init__(self, request=None, *, response_limit=RESPONSE_LIMIT, upstream_limit=UPSTREAM_LIMIT,
                 session_limit=SESSION_LIMIT, cache_limit=CACHE_LIMIT, cache_sessions=CACHE_SESSIONS):
        self.request = request or default_request
        self.response_limit, self.upstream_limit = response_limit, upstream_limit
        self.session_limit, self.cache_limit, self.cache_sessions = session_limit, cache_limit, cache_sessions
        self.cache = OrderedDict()
        # Stage and publish refreshes serially; no old overlapping acquisition can
        # overwrite a newer index. The mailbox never holds its lock over this work.
        self.lock = threading.RLock()
        self.stats = {"requests": 0, "rawBytes": 0, "messagePages": 0}

    def _get(self, path):
        try:
            raw = self.request(path, 10.0, raw=True)
            self.stats["requests"] += 1
            if not isinstance(raw, bytes):
                raise ValueError("raw transport required")
            self.stats["rawBytes"] += len(raw)
            if len(raw) > self.upstream_limit:
                raise ReaderError(413, "response-limit", f"OpenCode response exceeds {self.upstream_limit} byte read bound; not cropped")
            text = raw.decode("utf-8")
            def invalid_constant(value):
                raise ValueError("non-finite JSON")
            parsed = json.loads(text, parse_constant=invalid_constant)
            if not isinstance(parsed, dict) or "data" not in parsed:
                raise ValueError("expected data envelope")
            return parsed["data"], raw_members(text)["data"], parsed.get("cursor")
        except Unavailable as error:
            raise ReaderError(error.code, error.status, str(error)) from None
        except ReaderError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError, TypeError, KeyError):
            raise ReaderError(502, "read-failure", "OpenCode response unavailable or unreadable") from None

    def _metadata(self, sid):
        data, wire, _ = self._get("/api/session/" + quote(sid, safe=""))
        if not isinstance(data, dict) or data.get("id") != sid:
            raise ReaderError(502, "read-failure", "OpenCode session metadata unavailable")
        return data, len(wire.encode("utf-8"))

    def _busy(self, sid):
        try:
            data, _, _ = self._get("/api/session/active")
            return sid in data if isinstance(data, dict) and all(isinstance(key, str) and SESSION_ID.fullmatch(key) for key in data) else None
        except ReaderError:
            return None

    def _pages(self, sid, order, limit, *, overlap=None):
        path = "/api/session/" + quote(sid, safe="") + "/message?"
        query, result, seen_cursors, retained = {"order": order, "limit": limit}, [], set(), 0
        while True:
            data, wire, cursors = self._get(path + urlencode(query))
            self.stats["messagePages"] += 1
            if not isinstance(data, list) or len(data) > limit or not isinstance(cursors, dict):
                raise ReaderError(502, "read-failure", "OpenCode message page invalid")
            try:
                page = [Message.read(v) for v in raw_members(wire).values()]
            except (ValueError, UnicodeError, RecursionError):
                raise ReaderError(502, "read-failure", "OpenCode message page unreadable") from None
            result.extend(page)
            retained += sum(len(s.wire.encode("utf-8")) for s in page)
            if retained > self.session_limit:
                raise ReaderError(413, "session-limit", f"OpenCode session exceeds {self.session_limit} retained bytes; not cropped")
            nxt = cursors.get("next")
            if overlap is not None and any(s.data["id"] in overlap for s in page):
                break
            if nxt is None:
                break
            if not isinstance(nxt, str) or not nxt or nxt in seen_cursors:
                raise ReaderError(502, "read-failure", "OpenCode pagination cursor invalid")
            seen_cursors.add(nxt)
            query = {"cursor": nxt, "limit": limit}
        ids = [s.data["id"] for s in result]
        if len(set(ids)) != len(ids):
            raise ReaderError(502, "read-failure", "OpenCode page ranges overlap inconsistently")
        return result if order == "asc" else list(reversed(result))

    def _project(self, session, messages, busy, meta_bytes, old=None, observed_at=None):
        rows = build_rows(session["id"], messages)
        running = running_calls(session["id"], messages, busy)
        same = old is not None and topology_same(old, messages, rows)
        changed = old is not None and not same
        epoch = old.epoch if same else "oc_" + uuid.uuid4().hex
        # Use the actual envelope, including its real cursor lengths. There is
        # no separate prose cap or guessed reserve that could hide a fitting row.
        revision = old.revision + 1 if same else 1
        sizing_index = Index(session, messages, rows, [], epoch, revision, 0, 0, busy, running, observed_at)
        for i, row in enumerate(rows):
            size = len(encoded(row))
            envelope_bytes = len(encoded(self._envelope(session["id"], sizing_index, [], [], i + 1, False)))
            if size + envelope_bytes > self.response_limit:
                rows[i] = {**row, "status": "response-limit", "text": None, "bodyBytes": size,
                           "limitBytes": self.response_limit, "thinking": [], "calls": [], "partial": False}
                rows[i]["receipt"] = None
        raw_bytes = meta_bytes + sum(len(m.wire.encode("utf-8")) for m in messages)
        # Serialized accounting units include the retained parsed records too,
        # not just their wire and browser projection. No middle-record eviction.
        retained = raw_bytes + meta_bytes + sum(len(encoded(m.data)) for m in messages) + sum(len(encoded(row)) for row in rows) + len(encoded(running))
        if retained > self.session_limit or retained > self.cache_limit:
            raise ReaderError(413, "session-limit", f"OpenCode session exceeds {min(self.session_limit, self.cache_limit)} retained wire-plus-projection bytes; not cropped")
        revision = old.revision + 1 if same and rows != old.rows else old.revision if same else 1
        stamps = [old.stamps[i] if same and i < len(old.rows) and old.rows[i] == row else revision for i, row in enumerate(rows)]
        return Index(session, messages, rows, stamps, epoch, revision, retained, raw_bytes, busy, running, observed_at), changed

    def _publish(self, sid, index):
        self.cache[sid] = index
        self.cache.move_to_end(sid)
        while len(self.cache) > self.cache_sessions or sum(i.retained for i in self.cache.values()) > self.cache_limit:
            self.cache.popitem(last=False)

    def _refresh(self, sid, *, position=None, full=False):
        old = self.cache.get(sid)
        session, meta_bytes = self._metadata(sid)
        busy = self._busy(sid)
        observed_at = time.time() if busy is not None else None
        if old is None or full:
            messages = self._pages(sid, "asc", 100)
        else:
            old_ids = {s.data["id"]: i for i, s in enumerate(old.messages)}
            tail = self._pages(sid, "desc", 20, overlap=old_ids)
            first = next((old_ids[m.data["id"]] for m in tail if m.data["id"] in old_ids), len(old.messages))
            covered = [m.data["id"] for m in tail]
            expected = [m.data["id"] for m in old.messages[first:]]
            # Ordered coverage, NOT a union: a missing old tail/middle ID is a
            # topology boundary, even when every remaining ID still exists.
            mismatch = (covered[:len(expected)] != expected or any(mid in old_ids for mid in covered[len(expected):]) or
                        (old.messages and first == len(old.messages)))
            boundary = any(m.data.get("type") == "compaction" and
                           (m.data["id"] not in old_ids or m.wire != old.messages[old_ids[m.data["id"]]].wire) for m in tail)
            tail_row = next((r["seq"] for r in old.rows if tail and r["uuid"] == row_ref(tail[0].data["id"],
                                 "p0" if tail[0].data.get("type") == "assistant" else "m")), len(old.rows))
            if mismatch or boundary or position is not None and position < tail_row:
                messages = self._pages(sid, "asc", 100)
            else:
                messages = old.messages[:first] + tail
                for i, cached in enumerate(messages[:first]):
                    m = cached.data
                    provisional = m.get("type") == "assistant" and (not settled(m) or any(
                        r["partial"] for r in message_rows(sid, cached))) or m.get("type") == "shell" and m.get("status") == "running"
                    if provisional:
                        try:
                            data, wire, _ = self._get("/api/session/" + quote(sid, safe="") + "/message/" + quote(m["id"], safe=""))
                            if not isinstance(data, dict) or data.get("id") != m["id"]:
                                raise ReaderError(502, "read-failure", "OpenCode message identity changed")
                            messages[i] = Message.read(wire)
                        except ReaderError as error:
                            if error.code != 404:
                                raise
                            messages = self._pages(sid, "asc", 100)
                            break
        index, changed = self._project(session, messages, busy, meta_bytes, old, observed_at)
        self._publish(sid, index)
        if changed:
            raise ReaderError(409, "changed-index", "OpenCode conversation order/slots changed; resync this session")
        return index

    def _cursor(self, sid, epoch, position, mode, revision):
        identity = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:24]
        return base64.urlsafe_b64encode(encoded([1, identity, epoch, position, mode, revision])).decode("ascii").rstrip("=")

    def _decode(self, sid, cursor, epoch):
        try:
            if not safe_ref(cursor):
                raise ValueError()
            data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if (not isinstance(data, list) or len(data) != 6 or type(data[0]) is not int or data[0] != 1 or
                    data[1] != hashlib.sha256(sid.encode("utf-8")).hexdigest()[:24] or data[2] != epoch or
                    not safe_ref(data[2]) or integer(data[3]) is None or data[4] not in ("f", "d") or
                    integer(data[5]) is None):
                raise ValueError()
            return data[3], data[4], data[5]
        except (ValueError, TypeError, UnicodeError):
            raise ReaderError(400, "schema", "invalid OpenCode cursor identity/schema") from None

    def _envelope(self, sid, index, events, updates, boundary, delta):
        session = index.session
        has_more = boundary < len(index.rows)
        return {"schemaVersion": 1, "backend": "opencode", "source": "opencode", "sessionRef": sid,
                "scopeRef": "main", "fileEpoch": index.epoch, "events": events, "hasMore": has_more,
                "nextCursor": self._cursor(sid, index.epoch, boundary, "f", index.revision) if has_more else None,
                "deltaCursor": self._cursor(sid, index.epoch, boundary, "d", index.revision), "rowsAhead": has_more,
                "leafRef": None, "targetRef": None, "indexLimit": None, "branches": [], "traces": [],
                "summaryUpdates": [], "subagentActivity": [], "eventUpdates": updates, "diagnostics": [],
                "status": "complete", "change": "append" if delta else "snapshot", "busy": index.busy,
                "runningCalls": index.running, "observedAt": index.observed_at,
                "sessionInfo": {"id": sid, "title": string_value(session.get("title")) or sid,
                    "agent": string_value(session.get("agent")), "model": string_value(object_value(session.get("model")).get("id")),
                    "directory": string_value(object_value(session.get("location")).get("directory")),
                    "parentId": string_value(session.get("parentID"))}}

    def events(self, *, id, fileEpoch=None, cursor=None, fromSeq=None):
        if not isinstance(id, str) or SESSION_ID.fullmatch(id) is None:
            raise ReaderError(400, "schema", "invalid OpenCode session id")
        if fileEpoch is not None and not safe_ref(fileEpoch) or cursor is not None and (fromSeq is not None or fileEpoch is None):
            raise ReaderError(400, "schema", "cursor requires epoch and excludes fromSeq")
        if fromSeq is not None and integer(fromSeq) is None:
            raise ReaderError(400, "schema", "fromSeq must be a nonnegative safe integer")
        position, mode, observed = self._decode(id, cursor, fileEpoch) if cursor is not None else (fromSeq, "f", None)
        with self.lock:
            index = self._refresh(id, position=position)
            if fileEpoch is not None and fileEpoch != index.epoch:
                raise ReaderError(409, "stale-epoch", "OpenCode index discarded/rebuilt; resync the same session by position")
            if observed is not None and observed > index.revision:
                raise ReaderError(400, "schema", "OpenCode cursor revision is not an observed index revision")
            updates = [row for row, stamp in zip(index.rows, index.stamps) if observed is not None and
                       stamp > observed and row["seq"] < position]
            if len(updates) > PAGE_ROWS:
                raise ReaderError(409, "reload-required", "OpenCode replacement rows exceed update page; resync by position")
            start = len(index.rows) if position is None else min(position, len(index.rows))
            delta = mode == "d"
            update_bytes = sum(len(encoded(row)) for row in updates) + max(0, len(updates) - 1)
            def overhead(boundary):
                return len(encoded(self._envelope(id, index, [], [], boundary, delta))) + update_bytes
            if overhead(start) > self.response_limit:
                raise ReaderError(409 if updates else 413, "reload-required" if updates else "response-limit",
                                  f"OpenCode response exceeds {self.response_limit} bytes; not cropped; resync by position")
            selected, used = [], 0
            candidates = reversed(index.rows[max(0, start - PAGE_ROWS):start]) if position is None else iter(index.rows[start:start + PAGE_ROWS])
            for row in candidates:
                size = len(encoded(row)) + (1 if selected else 0)
                boundary = len(index.rows) if position is None else start + len(selected) + 1
                if overhead(boundary) + used + size > self.response_limit:
                    break
                selected.append(row)
                used += size
            if position is None:
                selected.reverse()
                boundary = len(index.rows)
            else:
                boundary = start + len(selected)
            if not selected and not updates and start < len(index.rows) or position is None and index.rows and not selected:
                raise ReaderError(413, "response-limit", f"OpenCode row/envelope exceeds {self.response_limit} response bytes; not cropped")
            result = self._envelope(id, index, selected, updates, boundary, delta)
            if len(encoded(result)) > self.response_limit:
                raise ReaderError(409 if updates else 413, "reload-required" if updates else "response-limit", f"OpenCode response exceeds {self.response_limit} bytes; not cropped")
            return copy.deepcopy(result)

    # @beacon[
    #   id=opc-pane@OpenCodeConversation.witness,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Revalidate the actual session/message/unique tool slot before admission; construct only server-owned association facts with V2 callId null.,
    # ]
    def witness(self, *, id, messageId, partIndex, callRef, fileEpoch):
        with self.lock:
            index = self._refresh(id, full=True)
            if fileEpoch != index.epoch:
                raise ReaderError(409, "stale-epoch", "OpenCode tool index changed; click again")
            m = next((s.data for s in index.messages if s.data["id"] == messageId), None)
            if m is None:
                raise ReaderError(404, "missing", "OpenCode message missing")
            content = m.get("content") if isinstance(m.get("content"), list) else []
            p = object_value(content[partIndex]) if partIndex < len(content) else {}
            pid = p.get("id")
            if m.get("type") != "assistant" or p.get("type") != "tool" or not isinstance(pid, str) or not pid:
                raise ReaderError(404, "missing", "OpenCode addressed part is not an inspectable tool")
            if sum(isinstance(v, dict) and v.get("id") == pid for v in content) != 1 or call_ref(id, messageId, partIndex, pid) != callRef:
                raise ReaderError(404, "missing", "OpenCode call identity missing or mismatched")
            session = index.session
            created = integer(object_value(session.get("time")).get("created"))
            message_created = integer(object_value(m.get("time")).get("created"))
            directory = string_value(object_value(session.get("location")).get("directory"))
            if not created or not message_created or not directory:
                raise ReaderError(502, "read-failure", "OpenCode association facts unreadable")
            return {"schemaVersion": 1, "backend": "opencode", "source": "opencode", "origin": "pane",
                    "instanceId": "opencode-local", "sessionId": id, "messageId": messageId, "partId": pid,
                    "partIndex": partIndex, "callRef": callRef, "fileEpoch": index.epoch, "toolName": p.get("name"),
                    "sessionTitle": string_value(session.get("title")) or id, "workspaceName": directory,
                    "association": {"sessionCreated": created, "directory": directory,
                        "messageCreated": message_created, "toolName": p.get("name"), "callId": None}}

    # @beacon[
    #   id=opc-pane@OpenCodeConversation.snapshot,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=The phone's direct read of one OpenCode tool body (GET /api/opencode/tool-record): revalidates callRef and its session/message/unique tool witness then returns exact snapshot_text with separate recorded diff evidence and unchanged byte counts/status/times; never the inspector mailbox.,
    # ]
    def snapshot(self, *, id, callRef, fileEpoch):
        if not isinstance(id, str) or SESSION_ID.fullmatch(id) is None:
            raise ReaderError(400, "schema", "invalid OpenCode session id")
        if not safe_ref(callRef) or not safe_ref(fileEpoch):
            raise ReaderError(400, "schema", "invalid OpenCode call identity")
        with self.lock:
            index = self._refresh(id)
            if fileEpoch != index.epoch:
                raise ReaderError(409, "stale-epoch", "OpenCode index discarded/rebuilt; read the conversation again")
            call = next((c for row in index.rows for c in row["calls"] if c["callRef"] == callRef), None)
            source = call and next((s for s in index.messages if s.data["id"] == call["messageId"]), None)
            if source is None:
                raise ReaderError(404, "missing", "OpenCode call missing from this conversation")
            m, i = source.data, call["partIndex"]
            content = m.get("content") if isinstance(m.get("content"), list) else []
            p = object_value(content[i]) if i < len(content) else {}
            pid = p.get("id")
            if (m.get("type") != "assistant" or p.get("type") != "tool" or not isinstance(pid, str) or not pid or
                    sum(isinstance(v, dict) and v.get("id") == pid for v in content) != 1 or call_ref(id, m["id"], i, pid) != callRef):
                raise ReaderError(404, "missing", "OpenCode call identity missing or mismatched")
            state_wire = raw_members(raw_members(raw_members(source.wire).get("content", "[]"))[i]).get("state", "{}")
            snap = snapshot_text(state_wire)
            if (snap["inputBytes"] or 0) + (snap["outputBytes"] or 0) > self.response_limit:
                raise ReaderError(413, "body-limit", f"OpenCode tool snapshot exceeds {self.response_limit} bytes; not cropped")
            state = object_value(p.get("state"))
            status, stored = call_status(state), snap["hasOutput"] or snap["hasError"]
            started, ended = call_times(m, p, status)
            terminal = status in ("stored", "error") or settled(m)
            return {"schemaVersion": 1, "backend": "opencode", "source": "opencode", "sessionRef": id,
                    "diff": opencode_diff(p.get("name"), state),
                    "callRef": callRef, "fileEpoch": index.epoch, "toolName": string_value(p.get("name")) or "unknown tool",
                    "input": snap["input"] if snap["hasInput"] else None, "output": snap["output"] if stored else None,
                    "inputBytes": snap["inputBytes"], "outputBytes": snap["outputBytes"], "status": status,
                    "inputAvailability": "stored" if snap["hasInput"] else "missing",
                    "outputAvailability": "stored" if stored else status, "caption": "stored", "artifactRef": None,
                    "provenance": "opencode-v2", "inputFinal": state.get("status") != "streaming", "live": True,
                    "terminal": terminal, "time": started, "resultTime": ended}
