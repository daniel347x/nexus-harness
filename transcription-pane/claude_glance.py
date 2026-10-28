"""One Claude Code session's glance for the sidebar (desktop and phone Programs), read from its transcript's tail.

A glance is what an expanded row shows: the session's newest text messages (the OpenCode glance's own rule,
opencode_programs.glance_messages, over what the conversation view calls a record's prose, ClaudeReader._prose) and
its running or latest tool call (the preview data and times its conversation row shows). It is read only while a row
is expanded, never by the inventory, the activity reads or telemetry, and never under the history reader's lock.

Every read is bounded. A first read takes the transcript's last TAIL_STEP bytes, and earlier steps only while too few
messages were found (at most TAIL_LIMIT bytes, READ_SECONDS). Later reads take only what the transcript grew by, the
turn ledger's byte-offset pattern (the file's identity, the offset read to, a witness of the bytes before it); a
rewrite, a replaced file or growth past TAIL_LIMIT reads a fresh tail. Display only: nothing here is authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace

from claude_reader import (ClaudeReader, ReaderError, SOURCE_LIMIT, TURN_FACT_WITNESS, UUID, open_contained,
                           parse_record, preview_data, stamp_seconds)
from opencode_programs import GLANCE_MESSAGES, GLANCE_TEXT, glance_messages

GLANCE_FILES = 64                      # transcripts whose glance state is kept (LRU)
TAIL_STEP = 1024 * 1024                # a first read takes the last MiB, then earlier MiBs while too few messages
TAIL_LIMIT = 8 * 1024 * 1024           # bytes per read at most (a first read, or what the file grew by)
READ_SECONDS = 1.5                     # a first read stops reading further back after this
LOCK_SECONDS = 3.0                     # a second read of the same transcript waits this long, then says so
CALLS_KEPT = 32                        # tool calls remembered per transcript (their results join them)
PREVIEW_LINE_LIMIT = 2 * 1024 * 1024   # a call record longer than this keeps no input to preview
TEXT_KEPT = GLANCE_TEXT * 4            # characters kept per message (its full length is kept beside them)
PATHS_KEPT = 512
PATH_MISS_SECONDS = 20                 # a session with no transcript found is looked for again after this


def _entry(line: bytes):
    """What a glance keeps of one transcript line: this session's own user/assistant records only (never a
    subagent's, never the API request copies); None for anything else."""
    if line.startswith(b'{"type":"api-request') or (b'"assistant"' not in line and b'"user"' not in line):
        return None
    try:
        record = json.loads(line)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(record, dict) or record.get("type") not in ("user", "assistant"):
        return None
    if record.get("agentId") is not None or record.get("isSidechain") is True:
        return None
    kind = record["type"]
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    content = message.get("content")
    blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
    model = message.get("model") if kind == "assistant" and isinstance(message.get("model"), str) else None
    calls = [(i, b["id"], b["name"]) for i, b in enumerate(content) if kind == "assistant" and isinstance(b, dict)
             and b.get("type") == "tool_use" and isinstance(b.get("id"), str) and isinstance(b.get("name"), str)
             ] if isinstance(content, list) else []
    results = [(b["tool_use_id"], b.get("is_error") is True) for b in blocks if kind == "user"
               and b.get("type") == "tool_result" and isinstance(b.get("tool_use_id"), str)]
    text = isinstance(content, str) or any(b.get("type") == "text" for b in blocks)
    keep = (text or calls) and len(line) <= (SOURCE_LIMIT if text else PREVIEW_LINE_LIMIT)
    return {"kind": kind, "time": stamp_seconds(record.get("timestamp")), "step": message.get("id"),
            "model": model if model and not model.startswith("<") else None,
            "text": text, "calls": calls, "results": results, "line": line if keep else None}


def _prose(entry) -> str:
    """The conversation view's own rule for a record's readable text (ClaudeReader._prose); '' when it has none."""
    if "prose" not in entry:
        entry["prose"] = ""
        if entry["text"] and entry["line"] is not None:
            try:
                text, root = parse_record(entry["line"])
                fields = root.members()
                record = SimpleNamespace(kind=entry["kind"], scope="main")
                entry["prose"] = ClaudeReader._prose(fields, fields["message"].members(), record, main_only=True).strip()
            except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, ReaderError):
                entry["prose"] = ""
    return entry["prose"]


def _settle_prose(entries, need=GLANCE_MESSAGES) -> int:
    """Reads the prose of a batch's text records newest first until `need` messages were found; older ones in the
    batch are never shown, so they are not parsed. Returns how many were found."""
    found, steps = 0, set()
    for entry in reversed(entries):
        if not entry["text"]:
            continue
        if found >= need and not (entry["kind"] == "assistant" and entry["step"] in steps):
            entry["prose"] = ""
            continue
        if _prose(entry):
            if not (entry["kind"] == "assistant" and entry["step"] in steps):
                found += 1
            if entry["kind"] == "assistant" and entry["step"]:
                steps.add(entry["step"])
    return found


def _lines(data: bytes):
    return [line for line in data.split(b"\n")[:-1] if line] if data else []


class _Transcript:
    """One transcript's glance state: the window read so far and what it found in it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset(None)

    def reset(self, signature):
        self.signature, self.start, self.offset, self.witness = signature, 0, 0, b""
        self.messages = []                 # oldest first: {role, time, text, chars, step}
        self.calls = OrderedDict()         # tool_use id -> {name, block, time, step, line | previewData, result}
        self.step = None                   # the newest assistant step that made calls
        self.last_at = self.model = None

    def apply(self, entry):
        """One record, in file order."""
        if entry["time"] is not None:
            self.last_at = entry["time"] if self.last_at is None else max(self.last_at, entry["time"])
        if entry["model"]:
            self.model = entry["model"]
        prose = _prose(entry) if entry["text"] else ""
        if prose:
            last = self.messages[-1] if self.messages else None
            if last and entry["kind"] == "assistant" and last["role"] == "assistant" and entry["step"] and last["step"] == entry["step"]:
                last["text"] = (last["text"] + "\n\n" + prose)[:TEXT_KEPT]   # one assistant message stored as several records
                last["chars"] += 2 + len(prose)
            else:
                self.messages.append({"role": entry["kind"], "time": entry["time"], "text": prose[:TEXT_KEPT],
                                      "chars": len(prose), "step": entry["step"]})
                del self.messages[:-GLANCE_MESSAGES]
        for block, call_id, name in entry["calls"]:
            if call_id in self.calls:
                continue                    # a replayed record
            if entry["step"] != self.step:
                for older in self.calls.values():
                    older.pop("line", None)   # only the newest step's calls can be shown: only their input is kept
                self.step = entry["step"]
            self.calls[call_id] = {"name": name[:256], "block": block, "time": entry["time"], "step": entry["step"],
                                   "line": entry["line"], "result": None}
            while len(self.calls) > CALLS_KEPT:
                self.calls.popitem(last=False)
        for call_id, error in entry["results"]:
            call = self.calls.get(call_id)
            if call is not None and call["result"] is None:
                call["result"] = (entry["time"], error)

    def call(self):
        """The newest step's last call still without a result, else its last call (display facts), or None."""
        calls = [c for c in self.calls.values() if c["step"] == self.step]
        if not calls:
            return None
        shown = next((c for c in reversed(calls) if c["result"] is None), calls[-1])
        if "previewData" not in shown:
            line = shown.pop("line", None)
            shown["previewData"] = {"schemaVersion": 1, "status": "unavailable", "reason": "input not kept"}
            if line is not None:
                try:
                    text, root = parse_record(line)
                    block = root.members()["message"].members()["content"].children[shown["block"]].members()
                    shown["previewData"] = preview_data(text, block.get("input"))
                except (ValueError, KeyError, IndexError, TypeError, UnicodeError, RecursionError):
                    shown["previewData"] = {"schemaVersion": 1, "status": "unavailable", "reason": "input unreadable"}
        result = shown["result"]
        return {"name": shown["name"], "status": "pending-or-missing" if result is None else "error" if result[1] else "stored",
                "previewData": shown["previewData"], "time": shown["time"], "resultTime": result[0] if result else None,
                "open": result is None}


# @beacon[
#   id=opc-pane@ClaudeGlance,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=GET /api/claude/glance?uuid=…: one Claude session's glance for an expanded sidebar row (desktop and phone Programs): its newest text messages (glance_messages, the OpenCode glance's rule, over the conversation view's prose) and its running or latest tool call (preview data and times as on its conversation row), from its transcript's tail only: a first read of the last MiB (earlier MiBs while too few messages, at most 8 MiB and 1.5 s), then only what the file grew by (signature, offset, witness). Never the inventory, never the history reader's lock, never a write.,
# ]
class ClaudeGlance:
    """The pane server's Claude glances: bounded tail reads of one session's native transcript, incremental per file."""

    def __init__(self, reader, clock=time.monotonic):
        self.reader, self.clock = reader, clock   # a ClaudeReader: its configured roots and transcript lookup only
        self.lock = threading.Lock()              # this object's maps only; never held while reading a file
        self.files: OrderedDict = OrderedDict()
        self.paths: OrderedDict = OrderedDict()

    def _path(self, uuid, cwd):
        now = self.clock()
        with self.lock:
            hit = self.paths.get(uuid)
            if hit and (hit[0] is not None or now - hit[2] < PATH_MISS_SECONDS):
                self.paths.move_to_end(uuid)
                return hit[0], hit[1]
        sid, path = self.reader.transcript_path(uuid, cwd)   # bounded: the cwd hint, else the roots' direct folders
        with self.lock:
            self.paths[uuid] = (sid, path, now)
            while len(self.paths) > PATHS_KEPT:
                self.paths.popitem(last=False)
        return sid, path

    def _transcript(self, key):
        with self.lock:
            state = self.files.get(key)
            if state is None:
                state = self.files[key] = _Transcript()
            self.files.move_to_end(key)
            while len(self.files) > GLANCE_FILES:
                self.files.popitem(last=False)
            return state

    def _tail(self, source, size, started):
        """Complete lines read backwards from the end in TAIL_STEP chunks while too few messages were found (the final
        line waits for its newline); (entries in file order, window start, window end, bytes read)."""
        chunks, hi, head, end, start, used, found, calls = [], size, b"", None, size, 0, 0, False
        while hi > 0 and used < TAIL_LIMIT:
            lo = max(0, hi - TAIL_STEP)
            source.seek(lo)
            data = source.read(hi - lo) + head   # head: the start of the newer chunk, whose first line began here
            used += hi - lo
            hi = lo
            if end is None:
                last = data.rfind(b"\n")
                if last < 0:
                    head = data              # no complete line yet (one record longer than a step): read further back
                    continue
                end, data = lo + last + 1, data[:last + 1]
            cut = data.find(b"\n") if lo > 0 else -1
            head, data = data[:cut + 1], data[cut + 1:]
            start = lo + cut + 1
            entries = [e for e in (_entry(line) for line in _lines(data) if len(line) <= SOURCE_LIMIT) if e]
            chunks.append(entries)
            found += _settle_prose(entries, GLANCE_MESSAGES - found)
            calls = calls or any(e["calls"] for e in entries)
            if (found >= GLANCE_MESSAGES and calls) or self.clock() - started > READ_SECONDS:
                break
        if end is None:
            return [], 0, 0, used           # no complete record within the bound: nothing read, nothing kept
        return [e for chunk in reversed(chunks) for e in chunk], start, end, used

    @staticmethod
    def _growth(source, offset, size):
        """What the transcript grew by, complete lines only; (entries, new end, bytes read)."""
        source.seek(offset)
        data = source.read(size - offset)
        last = data.rfind(b"\n")
        data = data[:last + 1] if last >= 0 else b""
        entries = [e for e in (_entry(line) for line in _lines(data) if len(line) <= SOURCE_LIMIT) if e]
        _settle_prose(entries)
        return entries, offset + len(data), size - offset

    def glance(self, uuid, cwd=None) -> dict:
        if not isinstance(uuid, str) or not UUID.fullmatch(uuid):
            raise ValueError("an exact session UUID is required")
        cwd = cwd if isinstance(cwd, str) and cwd.startswith("/") and len(cwd) <= 4096 else None
        started = self.clock()
        unavailable = lambda error: {"schemaVersion": 1, "backend": "claude", "status": "unavailable", "uuid": uuid,
                                     "error": error}
        sid, path = self._path(uuid, cwd)
        if sid is None:
            return unavailable("no transcript of this session found yet")
        state = self._transcript((sid, str(path)))
        if not state.lock.acquire(timeout=LOCK_SECONDS):
            return unavailable("another read of this transcript is still running")
        try:
            try:
                source = open_contained(self.reader.stores[sid]["root"], path)
            except ReaderError as e:
                with self.lock:
                    self.paths.pop(uuid, None)   # moved or gone: looked up again next time
                return unavailable(str(e))
            with source:
                st = os.fstat(source.fileno())
                signature = (st.st_dev, st.st_ino)
                fresh = (state.signature != signature or st.st_size < state.offset
                         or st.st_size - state.offset > TAIL_LIMIT)
                if not fresh and state.offset:
                    # Growth is trusted only while the bytes already read are unchanged (as the turn ledger does).
                    lo = max(0, state.offset - TURN_FACT_WITNESS)
                    source.seek(lo)
                    fresh = hashlib.sha256(source.read(state.offset - lo)).digest() != state.witness
                if fresh:
                    state.reset(signature)
                    entries, state.start, end, used = self._tail(source, st.st_size, started)
                    mode = "tail"
                elif st.st_size > state.offset:
                    entries, end, used = self._growth(source, state.offset, st.st_size)
                    mode = "growth"
                else:
                    entries, end, used, mode = [], state.offset, 0, "unchanged"
                for entry in entries:
                    state.apply(entry)
                if end != state.offset or fresh:
                    state.offset = end
                    lo = max(0, end - TURN_FACT_WITNESS)
                    source.seek(lo)
                    state.witness = hashlib.sha256(source.read(end - lo)).digest()
            messages = glance_messages((m["role"], m["time"], m["text"], m["chars"]) for m in reversed(state.messages))
            return {"schemaVersion": 1, "backend": "claude", "status": "complete", "uuid": uuid,
                    "messages": messages, "call": state.call(), "lastAt": state.last_at, "model": state.model,
                    # Fewer messages than a glance shows, and the window did not reach the transcript's start.
                    "partial": state.start > 0 and len(messages) < GLANCE_MESSAGES,
                    "read": {"mode": mode, "bytes": used, "window": state.offset - state.start, "size": st.st_size,
                             "ms": round((self.clock() - started) * 1000, 1)}}
        except OSError as e:
            state.reset(None)
            return unavailable("transcript read failed: " + str(e)[:160])
        finally:
            state.lock.release()
