"""Configured-root Claude history. Bodies stay in JSONL; opaque refs never contain paths.

Records of any Claude Code version are read by their own shape (fixtures: Windows 2.1.185,
native 2.1.296, headless 2.1.299 and synthetic-v1; real 2.1.300 transcripts checked). A record
that departs from that shape is refused alone, with a diagnostic, never a whole version.
History is never native input authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from tool_diff import claude_diff, recorded_value

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from claude_headless.lexical import (  # noqa: E402,F401 -- compatibility re-exports
    BODY_LIMIT, SOURCE_LIMIT, PREVIEW_DATA_LIMIT, PREVIEW_DATA_NODES, PREVIEW_DATA_DEPTH,
    PASTE_WRAPPER, SLASH_COMMAND, MODEL_ID, EFFORT_LEVEL, ReaderError, digest, Span,
    parse_record, scalar, projection, preview_data, result_projection, thinking_projection,
    unwrap_paste, call_facts,
)

PAGE_LIMIT = 8 * 1024 * 1024
PAGE_EVENTS = 100
SCAN_BYTES = 1024 * 1024
SCAN_LINES = 1000
INDEX_RECORDS = 100_000
INDEX_FILES = 32
INDEX_SUMMARY_BYTES = 256 * 1024 * 1024
HANDLE_LIMIT = 50_000
DISCOVERY_STEPS = 1000
INVENTORY_FILES = 4096
SUMMARY_FILES_PER_PAGE = 8
INVENTORY_SCANS = 8  # concurrent native inventory reads, each paging its own walk
# A record's `version` names the Claude Code release that wrote it (e.g. 2.1.300), or our own
# synthetic policy shape. It is diagnosis only: nothing here gates on WHICH release wrote a record.
# Each record is admitted by its own shape (identity fields and types, known record and block
# kinds, provenance fields), so a Claude Code update never silently switches history, prose or
# Refine context off; only a missing or malformed version string is refused, per record.
SYNTHETIC_VERSION = "synthetic-v1"
VERSION_SHAPE = re.compile(r"\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]{1,64})?\Z")


def well_formed_version(value):
    """A transcript `version` of the recorded shape: any release number, or synthetic-v1."""
    return isinstance(value, str) and (value == SYNTHETIC_VERSION or VERSION_SHAPE.fullmatch(value) is not None)


# Record kinds that copy conversation without carrying its identity (no uuid, no parentUuid):
# Claude Code 2.1.300's hash-addressed `api-request-blob` copies of each API request. They are
# counted on the history page, never listed one diagnostics row each.
QUIET_COPY_KINDS = frozenset(("api-request-blob",))
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
AGENT_FILE = re.compile(r"agent-([A-Za-z0-9_-]{1,256})\.jsonl\Z")
OPAQUE = re.compile(r"[A-Za-z0-9_-]{24,64}\Z")
# Display-only session names: the Manager-Session number Dan's launch prompt carries.
MANAGER_SESSION = re.compile(r"Manager-Session-[0-9]{1,6}(?:-[a-z])?(?![A-Za-z0-9])")
LABEL_SCAN_BYTES = 512 * 1024
LABEL_PROSE_MESSAGES = 3
LABEL_CACHE_FILES = 4096
LABEL_MISS_SECONDS = 60
CREATED_SCAN_BYTES = 64 * 1024
# Model/thinking labels (display only): one incremental ledger per transcript, bounded.
TURN_FACT_FILES = 4096
TURN_FACT_ENTRIES = 200_000
TURN_FACT_BUDGET = 32 * 1024 * 1024
TURN_FACT_WITNESS = 4096
_INVALID = object()


def _json_fact(record, key):
    value = record.get(key)
    return value if value is None or isinstance(value, str) else _INVALID


def _span_fact(fields, key):
    node = fields.get(key)
    return None if node is None or node.kind == "null" else node.scalar if node.kind == "string" else _INVALID


def _turn_stamp(value):
    """A record's own ISO-8601 timestamp with an explicit zone -> epoch seconds; else None."""
    if not isinstance(value, str) or not re.search(r"(?:Z|[+-]\d\d:?\d\d)\Z", value):
        return None
    return stamp_seconds(value)


# A native fork's own first message (oc_seat's fork_claude_prompt): 'You are a FORK (session
# cc-…) of cc-…'. It names the fork's own session, so an ancestor fork's copied prompt never matches.
FORK_PROMPT = re.compile(r"You are a FORK \(session cc-([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\) "
                         r"of cc-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")


def _fork_prompt_session(record):
    """The session a record's own FORK prompt names ('You are a FORK (session cc-…) of cc-…'), else None.

    A fork's transcript carries the text twice: as the launch's queue-operation at the head, before
    the copied conversation, and as the input's user record at the end of that copy. Only the session
    the text itself names can match, so a fork-of-fork's copied ancestor prompts never do.
    """
    kind = record.get("type")
    if kind == "user":
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
    elif kind == "queue-operation":
        content = record.get("content")
    else:
        return None
    found = FORK_PROMPT.match(content) if isinstance(content, str) else None
    return found.group(1) if found else None


def _transcript_session(path):
    """The session a transcript file's own name carries (a main transcript), else None."""
    stem = Path(str(path)).stem
    return stem if UUID.fullmatch(stem) else None


class TurnLedger:
    """One transcript's recorded per-call model/thinking facts, scanned incrementally (display only).

    `entries` maps the transcript's own assistant records (main records of a primary
    transcript, the agent's records of a subagent transcript) by UUID to (message id, model,
    level); `models`/`levels` keep first-use order. The aggregate is complete only when the
    whole file was read and every own record stated its model (and, for levels, its level).
    `first`/`last` are the earliest and latest timestamps on the session's own turn records
    (user and assistant: prompts, answers, tool calls and results). A record that could not
    be read before the first (or after the last) known one makes that end unknown.

    A native fork's transcript carries the base's whole conversation above the fork's own
    first message: from that message on (`fork_at`, the fixed FORK prompt naming this
    transcript's own session; a queue-operation of it at the head fixes the same boundary
    before the copy is read) only its own stamped records count, so copied base history never
    becomes the fork's own facts. A transcript without that prompt keeps today's facts.
    """

    def __init__(self, session=None):
        self.signature = None
        self.stat = None
        self.offset = 0
        self.witness = b""
        self.skipping = False
        self.entries = {}
        self.models, self.levels = {}, {}
        self.records = self.unknown_models = self.unknown_levels = self.unreadable = 0
        self.first = self.last = None
        self.session = session if isinstance(session, str) and UUID.fullmatch(session) else None
        self.fork_at = None
        self.fork_started = False
        self.head_unknown = self.tail_unknown = False
        self.status = "scanning"

    def unreadable_line(self, call=True):
        """An oversize or unparseable record: it may hide a turn (and, when `call`, a model call),
        so the ends it could hide are unknown. Inside a fork's copied conversation, before its own
        first stamped turn, it hides nothing of the session's own work."""
        if self.fork_at is not None and self.first is None:
            return
        self.unreadable += 1 if call else 0
        self.tail_unknown = True
        if self.first is None:
            self.head_unknown = True

    def own_fork_prompt(self, record):
        """Latch the boundary when `record` is this session's own FORK prompt; answer its record kind.

        The user record re-anchors the boundary after the copied conversation even if a queue-operation
        already supplied a launch stamp at the head. Repeated user echoes and ancestor prompts do not reset it.
        """
        if self.session is None or self.fork_started:
            return None
        named = _fork_prompt_session(record)
        if named is None or named != self.session:
            return None
        stamp = _turn_stamp(record.get("timestamp"))
        if stamp is not None:
            self.fork_at = stamp
        if record.get("type") == "user":
            self.fork_started = True
        return record.get("type")

    def copied_region(self):
        """Start the session's own facts at the fork boundary: everything read so far is the base's copy.

        `entries` keep the copied records' per-message facts: a message's own model is a label, not a count."""
        self.records = self.unknown_models = self.unknown_levels = self.unreadable = 0
        self.models, self.levels = {}, {}
        self.first = self.last = None
        self.head_unknown = self.tail_unknown = False

    def _stamp(self, record):
        stamp = _turn_stamp(record.get("timestamp"))
        if stamp is None:
            self.tail_unknown = True
            if self.first is None:
                self.head_unknown = True
            return
        self.first = stamp if self.first is None else min(self.first, stamp)
        self.last = stamp if self.last is None else max(self.last, stamp)
        self.tail_unknown = False

    def observe(self, line, agent):
        if line.startswith(b'{"type":"api-request') or (b'"assistant"' not in line and b'"user"' not in line
                                                        and b'"queue-operation"' not in line):
            return
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            if b'"type":"assistant"' in line or b'"type":"user"' in line:
                self.unreadable_line(call=b'"type":"assistant"' in line)
            return
        if not isinstance(record, dict):
            return
        if self.own_fork_prompt(record) == "user":
            self.copied_region()  # the copy above the fork's own first message was read as its own
        if record.get("type") not in ("assistant", "user"):
            return
        side = record.get("agentId")
        own = side in (None, agent) if agent else side is None and record.get("isSidechain") is not True
        stamp = _turn_stamp(record.get("timestamp"))
        # A fork counts only its own stamped records, at or after the boundary; copied base history
        # before it (and a record the boundary cannot place) is not one of the session's turns.
        counted = own and (self.fork_at is None or (stamp is not None and stamp >= self.fork_at))
        if counted:
            self._stamp(record)
        if record.get("type") != "assistant":
            return
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        stated, model, level = call_facts(message.get("model"), _json_fact(record, "effort"),
                                          _json_fact(record, "perTurnEffort"), record.get("isApiErrorMessage") is True)
        if not own or not stated:
            return
        if counted:
            self.records += 1
            if model is None:
                self.unknown_models += 1
            else:
                self.models[model] = self.models.get(model, 0) + 1
            if level is None:
                self.unknown_levels += 1
            else:
                self.levels[level] = self.levels.get(level, 0) + 1
        uuid = record.get("uuid")
        if isinstance(uuid, str) and UUID.fullmatch(uuid):
            message_id = message.get("id") if isinstance(message.get("id"), str) else None
            entry = (message_id, model, level)
            if uuid in self.entries and self.entries[uuid] != entry:
                entry = (message_id, None, None)  # one UUID, two different records: no certain fact
            self.entries[uuid] = entry

    def summary(self):
        complete = self.status == "complete" and not self.unreadable
        out = {"status": self.status, "records": self.records,
               "models": list(self.models), "levels": list(self.levels),
               "modelsComplete": complete and not self.unknown_models,
               "levelsComplete": complete and not self.unknown_models and not self.unknown_levels}
        # Turn times (epoch seconds): the first once seen, the last only once the whole file is read.
        if self.first is not None and not self.head_unknown:
            out["firstAt"] = self.first
        if self.status == "complete" and self.last is not None and not self.tail_unknown:
            out["lastAt"] = self.last
        return out




def envelope(**values):
    return {"schemaVersion": 1, "backend": "claude", **values}


def check_from_seq(from_seq, target_ref, leaf_ref, cursor):
    """fromSeq starts a first page at a row position (seq); it never mixes with a cursor, a leaf or a target."""
    if from_seq is None:
        return
    if type(from_seq) is not int or not 0 <= from_seq <= 2**53 - 1:
        raise ReaderError(400, "schema", "fromSeq must be a non-negative integer")
    if target_ref or leaf_ref or cursor:
        raise ReaderError(400, "schema", "fromSeq starts a first page; it cannot be combined with cursor, leafRef or targetRef")


def folder_role(cwd, manager_label=False):
    """A transcript with no host record: its role from its working folder (display only).

    The quick-worker folder holds quick workers and helpers. The manager-sessions folder also
    holds probes and smokes, so only a transcript whose first messages carry a Manager-Session
    label counts as a Manager there. Anything else stays unknown-role history."""
    leaf = re.split(r"[\\/]", str(cwd or "").rstrip("/\\"))[-1].lower()
    if leaf == "quick-worker":
        return "quick-worker"
    if leaf == "manager-sessions" and manager_label:
        return "manager"
    return None




def utf8_tail(text, size):
    return text.encode("utf-8")[-size:].decode("utf-8", errors="ignore") if size > 0 else ""


# Refine history scope. "budgeted" is the long-standing newest-first byte budget;
# "whole" is the same eligible rows with no budget and no crop, so the provider's
# cached prefix can grow instead of restarting at a ~50 KiB tail.
HISTORY_MODES = ("budgeted", "whole")
HISTORY_SEPARATOR = "\n\n---\n\n"


def check_history_mode(value):
    if type(value) is not str or value not in HISTORY_MODES:
        raise ReaderError(400, "schema", "historyMode must be 'budgeted' or 'whole'")
    return value


def utf16_units(text):
    """Length in UTF-16 code units, the unit JavaScript `String.length` and slice use."""
    return len(text.encode("utf-16-le")) // 2


# @beacon[
#   id=opc-pane@HistoryBlocks,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Shared newest-first eligible-prose accumulator: preserve the legacy UTF8 tail crop when budgeted or serialize every whole-history block, with stable IDs and UTF16 boundaries without duplicating prose in the response.,
# ]
class HistoryBlocks:
    """One exact Refine history serialization from newest-first eligible prose blocks.

    Budgeted preserves the existing accounting byte for byte: newest first,
    7-byte separators between blocks, and at most one UTF-8-safe tail of the
    oldest block that does not fit. Whole takes every eligible block, ignoring the
    budget. `finish` renders the historical `[USER]\\nprose\\n\\n---\\n\\n[ASSISTANT]\\nprose`
    text plus one cumulative UTF-16 end boundary per included block (the separator
    after a block is excluded, so `text.slice(previousEnd, end)` is exactly that block,
    with its leading separator for every block after the first).
    """

    def __init__(self, mode, budget_bytes):
        self.mode = check_history_mode(mode)
        self.budget_bytes = budget_bytes
        self.blocks = []  # (stable id, formatted block), newest first
        self.users = 0
        self._bytes = 0

    def add(self, block_id, label, prose):
        """Admit one block newest-first: 'fits', 'truncated' (oldest crop) or 'dropped'."""
        block = label + "\n" + prose
        if self.mode == "whole":
            self.blocks.append((block_id, block))
            return "fits"
        separator = len(HISTORY_SEPARATOR.encode("utf-8")) if self.blocks else 0
        room = self.budget_bytes - self._bytes - separator
        size = len(block.encode("utf-8"))
        if size > room:
            remaining = room - len((label + "\n…").encode("utf-8"))
            if remaining > 0:
                self.blocks.append((block_id, label + "\n…" + utf8_tail(prose, remaining)))
                return "truncated"
            return "dropped"
        self.blocks.append((block_id, block))
        self._bytes += separator + size
        return "fits"

    def finish(self):
        ordered = list(reversed(self.blocks))
        text = HISTORY_SEPARATOR.join(block for _, block in ordered)
        boundaries, end = [], 0
        for index, (block_id, block) in enumerate(ordered):
            end += (utf16_units(HISTORY_SEPARATOR) if index else 0) + utf16_units(block)
            boundaries.append({"id": block_id, "end": end})
        return text, boundaries


def open_contained(root, path):
    """No symlink components, no FIFO blocking, canonical containment at every open.

    dir_fd/O_NOFOLLOW traversal also closes directory replacement races on Linux.
    Root is pinned canonically by startup configuration, never browser input.
    """
    root, path = Path(root), Path(path)
    try:
        relative = path.relative_to(root)
        if not relative.parts or any(p in ("", ".", "..") or "%" in p for p in relative.parts):
            raise ValueError("invalid contained path")
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in relative.parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            body = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        finally:
            os.close(fd)
        if not stat.S_ISREG(os.fstat(body).st_mode):
            os.close(body)
            raise ValueError("not a regular file")
        return os.fdopen(body, "rb")
    except FileNotFoundError as e:
        raise ReaderError(404, "missing", "source or artifact missing") from e
    except (OSError, ValueError) as e:
        raise ReaderError(403, "containment", "source containment refused") from e


# The pane's context line reads the LAST completed model call through the shared host-context
# module (scripts/claude_headless/context.py): one bounded reverse witness scan and one pure
# report function, so the pane and the bound tool can never disagree about the number. No
# local arithmetic duplicates the shared report: a missing module is an honest unknown.
CONTEXT_CACHE_FILES = 8
CONTEXT_DISCOVERY_DIRS = 4096


def _shared_context():
    """The ONE shared context module, imported through the pane's existing scripts/ seam.

    server.py inserts ../scripts; a standalone reader import installs the same seam once.
    Availability is explicit, so a missing module can never produce a lookalike number.
    """
    try:
        import claude_headless.context as shared
    except ImportError:
        scripts = str(Path(__file__).resolve().parents[1] / "scripts")
        try:
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            import claude_headless.context as shared
        except ImportError:
            return None
    return shared


class InventoryScan:
    """One native inventory read: its own directory walk, the files it found but has not returned
    yet ((store, path) -> (sequence, session UUID)) and every file it has seen."""

    def __init__(self):
        self.walk, self.done, self.revision = None, False, 0
        self.found, self.seen = {}, set()

    def close(self):
        if self.walk is not None:
            self.walk.close()
            self.walk = None


class LeafPath(list):
    """A selected main/agent path; ``leaf`` is the walked leaf UUID (its identity), which
    is not always the last element once parallel-call result side records are absorbed."""
    leaf = None


def leaf_uuid(selected):
    return getattr(selected, "leaf", None) or (selected[-1].uuid if selected else None)


def stamp_seconds(value):
    """Native ISO-8601 record timestamp -> epoch seconds for display; anything else None."""
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Record:
    offset: int
    length: int
    hash: str
    uuid: str | None = None
    parent: str | None = None
    scope: str = "main"
    kind: str = "diagnostic"
    reason: str | None = None
    version: str | None = None
    calls: list = field(default_factory=list)
    results: list = field(default_factory=list)
    prose_bytes: int = 0
    thinking: list = field(default_factory=list)
    replay: int = 0
    content_hash: str | None = None  # exact lexical content witness for conservative host/history overlap
    time: float | None = None  # display-only: the record's native ISO timestamp as epoch seconds


@dataclass
class FileIndex:
    path: Path
    root: Path
    session: str
    store: str
    epoch: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    signature: tuple | None = None
    offset: int = 0
    records: list = field(default_factory=list)
    by_uuid: dict = field(default_factory=dict)
    ambiguous: set = field(default_factory=set)
    cwds: set = field(default_factory=set)
    title: str | None = None
    revision: int = 0
    status: str = "indexing"
    summary_bytes: int = 0
    # QUIET_COPY_KINDS records seen, by kind: counted, not listed.
    quiet: dict = field(default_factory=dict)
    # Rolling digest verifies preexisting bytes on a detected rewrite; not body storage.
    prefix_hash: object = field(default_factory=hashlib.sha256)


# @beacon[
#   id=opc-pane@ClaudeReader,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Contained main and parent-labelled native subagent transcripts; isolated scopes and lexical tool snapshots with separate recorded diff evidence never confer child Send/context authority.,
# ]
class ClaudeReader:
    def __init__(self, stores=None, *, scan_bytes=SCAN_BYTES, index_files=INDEX_FILES,
                 index_records=INDEX_RECORDS, summary_limit=INDEX_SUMMARY_BYTES):
        stores = stores if stores is not None else [{"id": "native", "root": "/home/daniel347x/.claude/projects", "native": True}]
        self.stores = {}
        for config in stores:
            if set(config) - {"id", "root", "native"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", config["id"]):
                raise ValueError("invalid configured store")
            root = Path(config["root"]).expanduser().resolve()
            self.stores[config["id"]] = {**config, "root": root, "ref": secrets.token_urlsafe(24)}
        self.scan_bytes, self.index_files, self.index_records = scan_bytes, index_files, index_records
        self.summary_limit = summary_limit
        self.files, self.handles, self.reverse = OrderedDict(), OrderedDict(), {}
        self.inventory = OrderedDict()
        # One directory walk per inventory read (token -> InventoryScan, LRU): a fresh read by one
        # reader (a second tab, the sidebar's 10-second refresh) never resets another's position.
        self.scans = OrderedDict()
        self.context_cache = OrderedDict()
        self.subagent_scans = OrderedDict()
        self.label_cache = OrderedDict()
        self.label_paths = OrderedDict()
        self.created_cache = OrderedDict()
        self.turn_ledgers = OrderedDict()
        # model -> context window the CLI itself reported (host records' context_windows);
        # display fallback for transcripts with no host record of their own (subagents, history).
        self.known_windows = {}
        self.lock = threading.RLock()

    def _agent(self, sid, path):
        parts = path.relative_to(self.stores[sid]["root"]).parts
        match = AGENT_FILE.fullmatch(path.name)
        if len(parts) in (4, 5) and parts[2] == "subagents" and UUID.fullmatch(parts[1]) and match:
            return parts[1], match.group(1)
        return None

    def _agent_labels(self, sid, path, parent_title=None):
        agent = self._agent(sid, path)
        if not agent:
            return {}
        parent, identity = agent
        metadata = {}
        try:
            with open_contained(self.stores[sid]["root"], path.with_suffix(".meta.json")) as source:
                raw = source.read(16 * 1024 + 1)
            if len(raw) <= 16 * 1024:
                value = json.loads(raw)
                if isinstance(value, dict):
                    metadata = {k: value[k][:512] for k in ("agentType", "description")
                                if isinstance(value.get(k), str)}
        except (ReaderError, ValueError, UnicodeError):
            pass  # Optional native sidecar labels are never routing or acceptance evidence.
        label = metadata.get("agentType") or ("agent-" + identity)
        title = (parent_title[:4096] if isinstance(parent_title, str) and parent_title else parent) + " ▸ " + label
        if metadata.get("description"):
            title += " · " + metadata["description"]
        return {"parentUuid": parent, "agentId": identity, "agentType": metadata.get("agentType"),
                "description": metadata.get("description"), "title": title, "role": "subagent"}

    def _ref(self, kind, identity):
        key = (kind, identity)
        existing = self.reverse.get(key)
        if existing in self.handles:
            self.handles.move_to_end(existing)
            return existing
        ref = secrets.token_urlsafe(24)
        self.handles[ref] = key
        self.reverse[key] = ref
        while len(self.handles) > HANDLE_LIMIT:
            old, value = self.handles.popitem(last=False)
            self.reverse.pop(value, None)
        return ref

    def _get(self, ref, kind):
        if not isinstance(ref, str) or not OPAQUE.fullmatch(ref):
            raise ReaderError(400, "schema", "invalid opaque " + kind + " handle")
        value = self.handles.get(ref)
        if value is None:
            raise ReaderError(409, "stale", "handle expired; reselect")
        if value[0] != kind:
            raise ReaderError(400, "schema", "wrong handle kind")
        return value[1]

    def _walk(self):
        for sid, config in self.stores.items():
            root = config["root"]
            if not root.is_dir():
                yield ("diagnostic", {"storeRef": config["ref"], "status": "no-history"})
                continue
            # scandir is incremental; no recursively materialized full file inventory.
            stack = [os.scandir(root)]
            try:
                while stack:
                    try:
                        entry = next(stack[-1])
                    except StopIteration:
                        stack.pop().close()
                        continue
                    yield ("progress", None)
                    if entry.is_symlink():
                        continue
                    relative = Path(entry.path).relative_to(root)
                    if entry.is_dir(follow_symlinks=False):
                        if len(relative.parts) <= 4:
                            stack.append(os.scandir(entry.path))
                    elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".jsonl"):
                        if UUID.fullmatch(Path(entry.name).stem) or ("subagents" in relative.parts and entry.name.startswith("agent-")):
                            yield ("file", (sid, Path(entry.path)))
            finally:
                for iterator in stack:
                    iterator.close()

    def _discover(self, scan):
        if scan.done:
            return [], True
        if scan.walk is None:
            scan.walk = self._walk()
        diagnostics, complete = [], False
        for _ in range(DISCOVERY_STEPS):
            try:
                kind, value = next(scan.walk)
            except StopIteration:
                scan.walk = None
                scan.done = True
                complete = True
                break
            except OSError:
                scan.walk = None  # the next page walks again; files already seen are not listed twice
                diagnostics.append({"status": "read-failure"})
                break
            if kind == "diagnostic":
                diagnostics.append(value)
            if kind == "file" and value not in scan.seen:
                scan.seen.add(value)
                sid, path = value
                session = path.stem
                if "subagents" in path.relative_to(self.stores[sid]["root"]).parts:
                    # Native layout: <UUID>/subagents/agent-<ID>.jsonl. No project-name decoding.
                    agent = self._agent(sid, path)
                    if not agent:
                        diagnostics.append({"status": "unsupported-trace", "label": path.name})
                        continue
                    session = agent[0]
                scan.revision += 1
                scan.found[value] = (scan.revision, session)
                # Never discover more unreturned summaries than one page can consume.
                # The directory iterator is the continuation, not an all-store database.
                if len(scan.found) >= SUMMARY_FILES_PER_PAGE:
                    break
        return diagnostics, complete

    def _index(self, sid, path):
        key = (sid, path)
        if key not in self.files:
            self.files[key] = FileIndex(path, self.stores[sid]["root"], self.inventory[key]["session"], sid)
        self.files.move_to_end(key)
        while len(self.files) > self.index_files:
            self.files.popitem(last=False)
        index = self.files[key]
        self._refresh(index)
        self.inventory[key]["status"] = index.status
        while len(self.files) > 1 and sum(i.summary_bytes for i in self.files.values()) > self.summary_limit:
            self.files.popitem(last=False)
        return index

    def _reset(self, index):
        fresh = FileIndex(index.path, index.root, index.session, index.store)
        index.__dict__.clear()
        index.__dict__.update(fresh.__dict__)

    @staticmethod
    def _signature(source):
        st = os.fstat(source.fileno())
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

    def _stable(self, index):
        with open_contained(index.root, index.path) as source:
            current = self._signature(source)
        if current != index.signature:
            raise ReaderError(409, "changed-index", "source changed during read; refresh with original epoch")

    def _refresh(self, index):
        with open_contained(index.root, index.path) as source:
            st = os.fstat(source.fileno())
            signature = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            if index.signature and (signature[:2] != index.signature[:2] or st.st_size < index.offset):
                self._reset(index)
            elif index.signature and signature != index.signature and index.offset:
                # A detected change can combine rewrite + append. Verify the indexed prefix,
                # never assume growth means append-only. Work is bounded per index operation:
                # large-prefix validation reports indexing and continues on the next request.
                witness = getattr(index, "validation", None)
                if witness is None or witness[0] != signature:
                    witness = [signature, 0, hashlib.sha256()]
                    index.validation = witness
                source.seek(witness[1])
                chunk = source.read(min(self.scan_bytes, index.offset - witness[1]))
                witness[2].update(chunk)
                witness[1] += len(chunk)
                if witness[1] < index.offset:
                    index.status = "validating"
                    return
                del index.validation
                if witness[2].digest() != index.prefix_hash.digest():
                    self._reset(index)
            source.seek(index.offset)
            work = lines = 0
            if getattr(index, "skip", None):
                start, length = index.skip
                chunk = source.readline(self.scan_bytes)
                index.prefix_hash.update(chunk)
                index.offset = source.tell()
                index.skip = (start, length + len(chunk))
                if chunk.endswith(b"\n"):
                    index.records.append(Record(start, index.skip[1], "", reason="source-limit"))
                    index.summary_bytes += 640
                    index.revision += 1
                    del index.skip
                index.signature = signature
                index.status = "source-limit" if getattr(index, "skip", None) else "indexing"
                return
            while work < self.scan_bytes and lines < SCAN_LINES:
                if len(index.records) >= self.index_records or index.summary_bytes >= self.summary_limit:
                    index.status = "index-limit"
                    break
                at = source.tell()
                raw = source.readline(SOURCE_LIMIT + 1)
                if not raw:
                    index.status = "complete"
                    break
                if len(raw) > SOURCE_LIMIT:
                    index.prefix_hash.update(raw)
                    index.offset = source.tell()
                    if raw.endswith(b"\n"):
                        index.records.append(Record(at, len(raw), "", reason="source-limit"))
                        index.summary_bytes += 640
                        index.revision += 1
                        index.status = "indexing"
                    else:
                        index.skip = (at, len(raw))
                        index.status = "source-limit"
                    break
                if not raw.endswith(b"\n"):
                    index.status = "incomplete-tail"
                    break
                record = self._summarize(index, raw, at)
                footprint = 640 + 2 * len(repr(record).encode("utf-8"))
                if index.summary_bytes + footprint > self.summary_limit:
                    if index.by_uuid.get((record.scope, record.uuid)) is record:
                        del index.by_uuid[(record.scope, record.uuid)]
                    index.status = "index-limit"
                    break
                index.records.append(record)
                index.summary_bytes += footprint
                index.prefix_hash.update(raw)
                index.offset = source.tell()
                work += len(raw)
                lines += 1
                index.revision += 1
                index.status = "indexing"
            if source.tell() == st.st_size and index.offset == st.st_size:
                index.status = "complete"
            index.signature = signature
        key = (index.store, index.path)
        sequence = self.inventory.get(key, {}).get("sequence", 0)
        self.inventory[key] = {
            "sequence": sequence,
            "session": index.session, "status": index.status,
            "title": index.title or index.session,
            "cwd": next(iter(index.cwds)) if len(index.cwds) == 1 else "mixed" if index.cwds else "unknown"}
        self.inventory.move_to_end(key)
        while len(self.inventory) > INVENTORY_FILES:
            self.inventory.popitem(last=False)

    def _summarize(self, index, raw, offset):
        record = Record(offset, len(raw), digest(raw))
        trace = self._agent(index.store, index.path)
        if trace:
            record.scope = "agent:" + trace[1]
        try:
            text, root = parse_record(raw)
            fields = root.members()
            kind = scalar(fields, "type", "string")
            session = scalar(fields, "sessionId", "string")
            if session is not None and session != index.session:
                raise ValueError("session identity mismatch")
            if kind in ("ai-title", "custom-title"):
                title = scalar(fields, "aiTitle" if kind == "ai-title" else "customTitle", "string")
                if title is not None:
                    index.title = title[:4096]
            cwd = scalar(fields, "cwd", "string")
            if cwd is not None:
                if len(index.cwds) < 2:
                    index.cwds.add(cwd if len(cwd) <= 4096 else "unknown")
            uuid = scalar(fields, "uuid", "string")
            if "parentUuid" in fields and fields["parentUuid"].kind not in ("string", "null"):
                raise ValueError("invalid parent UUID type")
            parent = scalar(fields, "parentUuid")
            if parent is not None and (not isinstance(parent, str) or not UUID.fullmatch(parent)):
                raise ValueError("invalid parent UUID")
            if uuid is not None and not UUID.fullmatch(uuid):
                raise ValueError("invalid record UUID")
            record.uuid, record.parent = uuid, parent
            record.time = stamp_seconds(scalar(fields, "timestamp", "string"))
            agent = scalar(fields, "agentId", "string")
            if agent is not None and (not agent or len(agent) > 256):
                raise ValueError("invalid agent identity")
            if trace and agent is not None and agent != trace[1]:
                raise ValueError("agent identity mismatch")
            side = scalar(fields, "isSidechain", default=False)
            if type(side) is not bool:
                raise ValueError("invalid sidechain flag")
            record.scope = ("agent:" + (trace[1] if trace else agent)) if trace or agent else "sidechain" if side else "main"
            version = scalar(fields, "version", "string")
            record.version = version
            if trace and kind == "progress" and "message" not in fields:
                # Native F3 excludes progress from conversational parent linkage. It is
                # telemetry, not a second leaf or a child/parent prose/context message.
                record.kind, record.uuid, record.parent = "metadata", None, None
                return record
            if version is not None and not well_formed_version(version):
                record.reason = "unsupported-version:" + version[:64]
            if uuid:
                key = (record.scope, uuid)
                prior = index.by_uuid.get(key)
                if prior:
                    if prior.hash == record.hash:
                        prior.replay += 1
                        record.kind = "replay"
                        return record
                    index.ambiguous.add(key)
                    record.reason = "conflicting-uuid"
                else:
                    index.by_uuid[key] = record
            record.kind = kind if kind in ("user", "assistant", "attachment", "system") else "metadata"
            if record.kind in ("user", "assistant"):
                if not uuid or session is None or "parentUuid" not in fields:
                    raise ValueError("missing conversation identity")
                if version is None:
                    record.reason = "unsupported-version:missing"
                if "isMeta" in fields and fields["isMeta"].kind not in ("true", "false"):
                    raise ValueError("invalid isMeta")
                message = fields["message"].members()
                if scalar(message, "role", "string") != kind:
                    raise ValueError("message role mismatch")
                if "origin" in fields:
                    scalar(fields["origin"].members(), "kind", "string")
                if "sourceToolAssistantUUID" in fields:
                    source_uuid = scalar(fields, "sourceToolAssistantUUID", "string")
                    if not source_uuid or not UUID.fullmatch(source_uuid):
                        raise ValueError("invalid source assistant UUID")
                blocks = self._blocks(message)
                record.content_hash = digest(projection(text, message.get("content")).encode("utf-8"))
                prose = self._prose(fields, message, record, main_only=False)
                record.prose_bytes = len(prose.encode("utf-8"))
                for number, block in blocks:
                    b = block.members()
                    typ = scalar(b, "type", "string")
                    if typ == "text":
                        if "text" not in b:
                            raise ValueError("missing text block body")
                        scalar(b, "text", "string")
                    elif typ == "tool_use":
                        callid = scalar(b, "id", "string")
                        name = scalar(b, "name", "string")
                        if kind != "assistant" or not callid or not name or len(callid) > 256 or len(name) > 256:
                            raise ValueError("invalid tool use")
                        inp = projection(text, b.get("input"))
                        record.calls.append({"block": number, "id": callid, "name": name[:256],
                                             "inputBytes": None if inp is None else len(inp.encode("utf-8")),
                                              "previewData": preview_data(text, b.get("input"))})
                    elif typ == "tool_result":
                        callid = scalar(b, "tool_use_id", "string")
                        error = scalar(b, "is_error", default=False)
                        if kind != "user" or not callid or len(callid) > 256 or type(error) is not bool:
                            raise ValueError("invalid tool result")
                        out = result_projection(text, b.get("content"))
                        if "savedArtifact" in b and version == "synthetic-v1":
                            metadata = b["savedArtifact"].members()
                            if (not scalar(metadata, "relativePath", "string") or
                                    scalar(metadata, "completeness", "string") not in ("full", "limited")):
                                raise ValueError("unsupported saved artifact metadata")
                        if "savedArtifact" in b and version != "synthetic-v1":
                            record.reason = "unsupported-artifact-reference"
                        record.results.append({"block": number, "id": callid, "error": error,
                                               "source": scalar(fields, "sourceToolAssistantUUID", "string"),
                                               "outputBytes": None if out is None else len(out.encode("utf-8"))})
                    elif typ in ("thinking", "redacted_thinking"):
                        summary, _ = thinking_projection(b, number, 0)
                        record.thinking.append({k: v for k, v in summary.items() if k != "text"})
                    elif typ != "text":
                        record.reason = "unsupported-block:" + str(typ)
            elif kind in QUIET_COPY_KINDS and uuid is None and "parentUuid" not in fields:
                # A copy of an API request, not conversation: counted on the page, never a row each.
                index.quiet[kind] = index.quiet.get(kind, 0) + 1
            elif kind not in ("attachment", "system", "ai-title", "custom-title", "last-prompt", "mode",
                              "permission-mode", "bridge-session", "queue-operation", "file-history-snapshot") and (
                    uuid is not None or "parentUuid" in fields or "message" in fields):
                # Unknown kinds carrying conversation identity may be prose: refuse. Pure session
                # bookkeeping (agent-name, atis-latch, cost-state, ...) cannot be, and every Claude Code
                # minor release adds some, so it is ignored rather than blocking the reader.
                record.reason = "unknown-metadata:" + str(kind)
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError) as e:
            record.kind, record.reason = "diagnostic", "malformed:" + str(e)[:256]
        return record

    @staticmethod
    def _blocks(message):
        content = message.get("content")
        if content is None or content.kind not in ("string", "array"):
            raise ValueError("unsupported message content")
        return list(enumerate(content.children)) if content.kind == "array" else []

    @staticmethod
    def _prose(fields, message, record, *, main_only=True):
        if (main_only and record.scope != "main") or scalar(fields, "isMeta", default=False):
            return ""
        content = message.get("content")
        if record.kind == "user":
            origin = fields.get("origin")
            # Headless stream-json input (seen on 2.1.299 and 2.1.300) has no origin
            # field. Admit only that exact external SDK string-message provenance, on any
            # version (the fields are the capability, not the release), never tool results,
            # queued/unknown/internal records or an inferred newest conversation.
            sdk_input = (origin is None and content.kind == "string" and
                         scalar(fields, "promptSource", "string") == "sdk" and
                         scalar(fields, "turnOrigin", "string") == "sdk" and
                         scalar(fields, "entrypoint", "string") == "sdk-cli" and
                         scalar(fields, "userType", "string") == "external" and
                         "sourceToolAssistantUUID" not in fields)
            child_prompt = (not main_only and record.scope.startswith("agent:") and
                            content.kind == "string" and
                            scalar(fields, "isSidechain", default=False) is True and
                            scalar(fields, "agentId", "string") == record.scope[6:])
            if not sdk_input and not child_prompt and (origin is None or scalar(origin.members(), "kind", "string") != "human"):
                return ""
        def permitted(value):
            if not isinstance(value, str):
                raise ValueError("invalid text block")
            markers = ("<command-", "<local-command-", "<system-reminder>", "<bash-input>", "<bash-stdout>")
            # A path (`/mnt/e/…`) or a leading `!` is Dan's prose; only a slash-command-shaped word is a command.
            return not value.lstrip().startswith(markers) and not (record.kind == "user" and SLASH_COMMAND.match(value.lstrip()))
        if content.kind == "string":
            prose = content.scalar if permitted(content.scalar) else ""
            if record.kind == "user":
                prose = unwrap_paste(prose)
        else:
            pieces = []
            for block in content.children:
                b = block.members()
                if scalar(b, "type", "string") == "text":
                    value = scalar(b, "text", "string")
                    if permitted(value):
                        pieces.append(value)
            prose = "\n\n".join(pieces)
        # Native command/internal output strings are not human conversation context (a path is).
        if record.kind == "user" and (SLASH_COMMAND.match(prose.lstrip()) or
                                      prose.lstrip().startswith(("<command-", "<local-command-", "<system-reminder>"))):
            return ""
        return prose

    def _read(self, index, record):
        if record.length > SOURCE_LIMIT:
            raise ReaderError(413, "source-limit", "record exceeds source read limit")
        with open_contained(index.root, index.path) as source:
            st = os.fstat(source.fileno())
            if index.signature and (st.st_dev, st.st_ino) != index.signature[:2]:
                self._reset(index)
                raise ReaderError(409, "stale-epoch", "file replaced; reselect")
            source.seek(record.offset)
            raw = source.read(record.length)
        if digest(raw) != record.hash:
            self._reset(index)
            raise ReaderError(409, "stale-epoch", "indexed record changed; reselect")
        self._stable(index)
        return parse_record(raw)

    def _session(self, ref):
        sid, path = self._get(ref, "session")
        if (sid, path) not in self.inventory:
            # The opaque handle already authorizes this exact discovered path. No
            # browser-provided path; summary eviction is not source reinterpretation.
            session = self._session_uuid(sid, path)
            self.inventory[(sid, path)] = {"session": session, "sequence": 0, "title": session,
                                          "cwd": "unknown", "status": "indexing"}
        return self._index(sid, path)

    def _session_uuid(self, sid, path):
        agent = self._agent(sid, path)
        if agent:
            return agent[0]
        return path.stem

    @staticmethod
    def _context_unavailable(record, source, reason):
        record = record if isinstance(record, dict) else {}
        return {"state": "unavailable", "occupancy_tokens": None, "window_tokens": None,
                "fraction_used": None, "target": None, "advice": None, "as_of": None,
                "age_seconds": None, "source": source, "reason": reason,
                "label": "UNKNOWN: " + reason, "report_source": "pane",
                "identity": {"execution_id": record.get("execution_id"),
                             "generation": record.get("generation"),
                             "uuid": record.get("native_uuid"), "role": record.get("role")}}

    def transcript_path(self, uuid, cwd=None):
        """Locate one session's native transcript under the configured roots only.

        A cwd hint checks the exact expected project directory first; otherwise each
        configured root's direct children are scanned (bounded) for <uuid>.jsonl. Only a
        real regular file is returned; open_contained re-verifies containment at open.
        """
        if not isinstance(uuid, str) or not UUID.fullmatch(uuid):
            return None, None
        stores = [config for config in self.stores.values() if config.get("native")] or list(self.stores.values())
        for config in stores:
            root = config["root"]
            candidates = []
            if isinstance(cwd, str) and cwd.startswith("/"):
                candidates.append(root / re.sub(r"[^a-zA-Z0-9]", "-", cwd) / (uuid + ".jsonl"))
            for candidate in candidates:
                if candidate.is_file():
                    return config["id"], candidate
            try:
                with os.scandir(root) as entries:
                    for count, entry in enumerate(entries):
                        if count >= CONTEXT_DISCOVERY_DIRS:
                            break
                        if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                            continue
                        candidate = Path(entry.path) / (uuid + ".jsonl")
                        if candidate.is_file():
                            return config["id"], candidate
            except OSError:
                continue
        return None, None

    # @beacon[
    #   id=opc-pane@ClaudeReader.session_label,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Display-only sidebar name: the Manager-Session-NNN token from a transcript's first human messages, one bounded contained head read cached per file size; never identity or authority.,
    # ]
    def session_label(self, sid, path):
        """The `Manager-Session-NNN[-x]` token of a transcript's first human messages, or None.

        Display only. One bounded head read (LABEL_SCAN_BYTES) through open_contained; a
        found label is cached for the file, a miss is cached until the file grows.
        """
        with self.lock:
            try:
                size = path.stat().st_size
            except OSError:
                return None
            cached = self.label_cache.get(path)
            if cached is not None and (cached[1] is not None or cached[0] == size):
                self.label_cache.move_to_end(path)
                return cached[1]
            try:
                with open_contained(self.stores[sid]["root"], path) as source:
                    head = source.read(LABEL_SCAN_BYTES)
            except ReaderError:
                return None
            label, seen = None, 0
            for line in head.split(b"\n"):
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    continue  # the bounded head may end mid-record
                if not isinstance(record, dict) or record.get("type") != "user" or record.get("isMeta") is True:
                    continue
                message = record.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, str):
                    prose = content
                elif isinstance(content, list):
                    prose = "\n".join(b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"
                                      and isinstance(b.get("text"), str))
                else:
                    continue
                if not prose.strip() or prose.lstrip().startswith(("<command-", "<local-command-", "<system-reminder>")):
                    continue  # tool results and command echoes are not the launch message
                seen += 1
                match = MANAGER_SESSION.search(prose)
                if match:
                    label = match.group(0)
                    break
                if seen >= LABEL_PROSE_MESSAGES:
                    break
            self.label_cache[path] = (size, label)
            self.label_cache.move_to_end(path)
            while len(self.label_cache) > LABEL_CACHE_FILES:
                self.label_cache.popitem(last=False)
            return label

    # @beacon[
    #   id=opc-pane@ClaudeReader.transcript_created,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Sidebar order: a session's creation time is its own transcript's first timestamped record (a native fork's own FORK launch message outranks the base records it copied above), one bounded contained head read cached per file; never identity or authority.,
    # ]
    def transcript_created(self, sid, path):
        """Epoch seconds of a transcript's own first timestamped record, or None (display order only).

        The first record never changes, so a found stamp is cached for the file; a miss is
        cached until the file grows. One bounded head read through open_contained. A native
        fork's own launch message (its FORK prompt, naming this transcript's session) outranks
        the copied records above it: its stamp is the creation, never the base's first record.
        """
        with self.lock:
            try:
                size = path.stat().st_size
            except OSError:
                return None
            cached = self.created_cache.get(path)
            if cached is not None and (cached[1] is not None or cached[0] == size):
                self.created_cache.move_to_end(path)
                return cached[1]
            try:
                with open_contained(self.stores[sid]["root"], path) as source:
                    head = source.read(CREATED_SCAN_BYTES)
            except ReaderError:
                return None
            session = _transcript_session(path)
            stamp = None
            shared = _shared_context()
            for line in head.split(b"\n"):
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    continue  # the bounded head may end mid-record
                if not isinstance(record, dict) or shared is None:
                    continue
                if session is not None and _fork_prompt_session(record) == session:
                    found = shared.timestamp_seconds(record["timestamp"]) if isinstance(record.get("timestamp"), str) else None
                    if found is not None:
                        stamp = found
                        break  # the fork's own launch message is its creation
                elif stamp is None and isinstance(record.get("timestamp"), str):
                    stamp = shared.timestamp_seconds(record["timestamp"])
            self.created_cache[path] = (size, stamp)
            self.created_cache.move_to_end(path)
            while len(self.created_cache) > LABEL_CACHE_FILES:
                self.created_cache.popitem(last=False)
            return stamp

    def _host_transcript(self, uuid, cwd):
        """(store, path) of a host session's native transcript, cached; a miss retries after a minute."""
        if not isinstance(uuid, str) or not UUID.fullmatch(uuid):
            return None, None
        found = self.label_paths.get(uuid)
        if found is None or (found[0] is None and time.monotonic() - found[2] > LABEL_MISS_SECONDS):
            sid, path = self.transcript_path(uuid, cwd)
            found = (sid, path, time.monotonic())
            self.label_paths[uuid] = found
            while len(self.label_paths) > LABEL_CACHE_FILES:
                self.label_paths.popitem(last=False)
        return found[0], found[1]

    def created_for_uuid(self, uuid, cwd=None):
        """A host session's creation time from its own native transcript, or None."""
        with self.lock:
            sid, path = self._host_transcript(uuid, cwd)
            return self.transcript_created(sid, path) if sid is not None else None

    def label_sessions(self, rows):
        """Attach `sessionLabel` and `createdAt` to primary inventory rows in place (display only).

        Native rows use their own contained transcript; host rows locate the native
        transcript of their UUID (cwd hint first; a miss is retried after a minute).
        Children are named by their parent in the sidebar and are skipped. A host row that
        already carries `createdAt` (the host facade's ordering stamp) keeps it.
        """
        with self.lock:
            for row in rows:
                if not isinstance(row, dict) or row.get("parentUuid") or row.get("role") == "subagent":
                    continue  # children are named and placed by their parent
                try:
                    if row.get("source") == "host":
                        sid, path = self._host_transcript(row.get("uuid"), row.get("cwd"))
                        if sid is None:
                            continue
                    else:
                        sid, path = self._get(row.get("sessionRef"), "session")
                    if row.get("createdAt") is None:
                        created = self.transcript_created(sid, path)
                        if created is not None:
                            row["createdAt"] = created
                    # A worker's or Conductor's first message often names the Manager Session it
                    # serves: that token names Managers (and unknown-role history) only.
                    if not row.get("sessionLabel") and row.get("displayRole") in (None, "manager", "unknown"):
                        label = self.session_label(sid, path)  # a launch name already wins
                        if label:
                            row["sessionLabel"] = label
                    if row.get("displayRole") is None:
                        role = folder_role(row.get("cwd"), bool(row.get("sessionLabel")))
                        if role:
                            row["displayRole"], row["roleSource"] = role, "folder"
                except (ReaderError, OSError, KeyError, TypeError):
                    continue  # a name is cosmetic: never fail the inventory page for it
            return rows

    # @beacon[
    #   id=opc-pane@ClaudeReader.turn_ledger,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Model/thinking labels and first/last turn times: one transcript's recorded per-call facts (message.model, effort/perTurnEffort) and its own user/assistant record timestamps, read incrementally within a byte budget; a rewrite or replaced file starts over, an incomplete tail waits for its newline; a native fork counts only its own work from its own FORK launch message (a queue-operation of it at the head fixes the boundary before the copy is read).,
    # ]
    def turn_ledger(self, sid, path, budget=TURN_FACT_BUDGET):
        """Advance one transcript's TurnLedger by at most `budget` bytes; (ledger, bytes read)."""
        with self.lock:
            key = (sid, str(path))
            session = _transcript_session(path)
            ledger = self.turn_ledgers.get(key)
            if ledger is None:
                ledger = self.turn_ledgers[key] = TurnLedger(session)
            self.turn_ledgers.move_to_end(key)
            while len(self.turn_ledgers) > TURN_FACT_FILES:
                self.turn_ledgers.popitem(last=False)
            agent = self._agent(sid, path)
            agent = agent[1] if agent else None
            used = 0
            with open_contained(self.stores[sid]["root"], path) as source:
                st = os.fstat(source.fileno())
                if ledger.status == "limit" or (ledger.stat == (st.st_size, st.st_mtime_ns) and
                                                ledger.signature == (st.st_dev, st.st_ino)):
                    return ledger, 0
                if ledger.signature != (st.st_dev, st.st_ino) or st.st_size < ledger.offset:
                    ledger.__init__(session)
                elif ledger.offset:
                    # Growth is trusted only while the bytes already read are unchanged.
                    start = max(0, ledger.offset - TURN_FACT_WITNESS)
                    source.seek(start)
                    if hashlib.sha256(source.read(ledger.offset - start)).digest() != ledger.witness:
                        ledger.__init__(session)
                ledger.signature = (st.st_dev, st.st_ino)
                source.seek(ledger.offset)
                while used < budget:
                    if len(ledger.entries) >= TURN_FACT_ENTRIES:
                        ledger.status = "limit"
                        break
                    line = source.readline(SOURCE_LIMIT + 1)
                    if not line:
                        break
                    if not line.endswith(b"\n") and len(line) <= SOURCE_LIMIT:
                        break  # an incomplete final record waits for its newline
                    used += len(line)
                    ledger.offset += len(line)
                    if ledger.skipping or len(line) > SOURCE_LIMIT:
                        if not ledger.skipping:
                            ledger.unreadable_line()  # an oversize record may hide a call or a turn
                        ledger.skipping = not line.endswith(b"\n")
                        continue
                    ledger.observe(line, agent)
                start = max(0, ledger.offset - TURN_FACT_WITNESS)
                source.seek(start)
                ledger.witness = hashlib.sha256(source.read(ledger.offset - start)).digest()
                if ledger.status != "limit":
                    ledger.status = "complete" if ledger.offset == st.st_size else "scanning"
                    ledger.stat = (st.st_size, st.st_mtime_ns) if ledger.status == "complete" else None
            return ledger, used

    def host_turn_ledger(self, uuid, cwd=None, budget=TURN_FACT_BUDGET):
        """A host session's ledger, from its own native transcript; None when there is none yet."""
        with self.lock:
            sid, path = self._host_transcript(uuid, cwd)
            return self.turn_ledger(sid, path, budget)[0] if sid is not None else None

    def attach_turn_facts(self, rows, budget=TURN_FACT_BUDGET):
        """Attach `turnFacts` (the models, thinking levels and first/last turn times a session's own turns recorded) in place.

        Display only, within one shared byte budget per request: a row whose transcript is not
        read to its end yet says so (`status: scanning`) and the sidebar shows nothing for it.
        """
        with self.lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    if row.get("source") == "host":
                        sid, path = self._host_transcript(row.get("uuid"), row.get("cwd"))
                        if sid is None:
                            continue
                    else:
                        sid, path = self._get(row.get("sessionRef"), "session")
                    ledger, used = self.turn_ledger(sid, path, budget)
                    budget = max(0, budget - used)
                    row["turnFacts"] = ledger.summary()
                except (ReaderError, OSError, KeyError, TypeError):
                    continue  # a label is cosmetic: never fail the inventory page for it
            return rows

    def _compose_context(self, record, usage, measured_at, *, source, window=None, windows=None,
                         model=None, message_id=None, timestamp=None, now=None, witness=None):
        """One honest report from a completed model call's usage (shared function only).

        Composed fresh on every call, so age/as-of/target/window/role can never be stale;
        only the raw witness may be cached by the caller.
        """
        shared = _shared_context()
        if shared is None:
            return self._context_unavailable(record, source, "shared context report unavailable")
        record = dict(record) if isinstance(record, dict) else {}
        model = model if isinstance(model, str) else None
        if model:
            record["context_model"] = model
        if window is None and isinstance(windows, dict) and model in windows:
            candidate = windows.get(model)
            window = candidate if type(candidate) is int and candidate > 0 else None
        report = shared.previous_call_report(record, usage, measured_at, window=window, source=source, now=now)
        if not isinstance(report, dict):
            return self._context_unavailable(record, source, "shared context report unavailable")
        report.setdefault("report_source", "shared")
        report["source"] = source
        report["occupancy_basis"] = "input + cache_read + cache_creation (no output)"
        report["identity"] = {"execution_id": record.get("execution_id"), "generation": record.get("generation"),
                              "uuid": record.get("native_uuid"), "role": record.get("role")}
        report["model"] = model
        report["message_id"] = message_id if isinstance(message_id, str) else None
        if isinstance(timestamp, str):
            report["timestamp"] = timestamp
        if witness is not None:
            report["witness"] = witness
        report["measured_at"] = shared.timestamp_seconds(report.get("as_of"))
        return report

    def context_report(self, usage, measured_at, *, record=None, source="host completed model call",
                       window=None, windows=None, model=None, message_id=None, now=None):
        """A report from a host-recorded completed model call (no transcript read)."""
        return self._compose_context(record, usage, measured_at, source=source, window=window,
                                     windows=windows, model=model, message_id=message_id, now=now)

    # @beacon[
    #   id=opc-pane@ClaudeReader.transcript_context,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=The pane's context line: one shared bounded witness scan (stat-cached) plus a pure report recomposed every read, so the figure never waits for the viewer index and never goes stale.,
    # ]
    def transcript_context(self, root, path, *, uuid=None, record=None, source="native-transcript",
                           window=None, windows=None, now=None, not_before=None, agent_id=None):
        """One transcript's context report from the SHARED witness scan and report function.

        Only the raw witness (shared `latest_transcript_call` result + file signature) is
        cached, so a poll never rereads an unchanged file; the report itself is recomposed
        on every call. The witness read uses the contained opener, never a check-then-reopen.
        `windows` may supply the native observed window for the witness model; without one
        the measured tokens are still reported with an unavailable window. A missing or
        unreadable file, or a missing shared module, is an honest unknown.
        """
        shared = _shared_context()
        if shared is None:
            return self._context_unavailable(record, source, "shared context report unavailable")
        key = (str(root), str(path), uuid, repr(not_before), agent_id)
        if window is None and not windows and self.known_windows:
            windows = dict(self.known_windows)
        try:
            with open_contained(root, path) as source_file:
                st = os.fstat(source_file.fileno())
            signature = (st.st_ino, st.st_size, st.st_mtime_ns)
            cached = self.context_cache.get(key)
            if cached is not None and cached[0] == signature:
                self.context_cache.move_to_end(key)
                witness = copy.deepcopy(cached[1])
            else:
                witness = shared.latest_transcript_call(str(path), uuid, not_before,
                                                        opener=lambda: open_contained(root, path),
                                                        **({"agent_id": agent_id} if agent_id else {}))
                self.context_cache[key] = (signature, copy.deepcopy(witness))
                self.context_cache.move_to_end(key)
                while len(self.context_cache) > CONTEXT_CACHE_FILES:
                    self.context_cache.popitem(last=False)
        except (ReaderError, OSError, ValueError) as error:
            return self._context_unavailable(record, source, "transcript unavailable: " + str(error)[:120])
        if not isinstance(witness, dict) or not witness.get("usage"):
            return self._context_unavailable(record, source, "no completed model call yet in this generation"
                                             if not_before is not None else
                                             "no completed model call in the transcript tail yet")
        record = dict(record) if isinstance(record, dict) else {}
        record.setdefault("native_uuid", uuid)
        stamp = witness.get("measured_at") if isinstance(witness.get("measured_at"), str) else None
        return self._compose_context(record, witness["usage"], witness.get("measured_at"), source=source,
                                     window=window, windows=windows, model=witness.get("model"),
                                     message_id=witness.get("message_id"), timestamp=stamp, now=now,
                                     witness={"sizeBytes": signature[1]})

    def context_readout_for_uuid(self, uuid, *, cwd=None, record=None, window=None, windows=None, now=None,
                                 not_before=None):
        """A host session's context report from its exact native transcript.

        No viewer index participates: the transcript is located under the configured roots
        and its tail is read only, so the figure appears within seconds of a cold load.
        """
        store, path = self.transcript_path(uuid, cwd)
        if store is None:
            return self._context_unavailable(record, "native-transcript",
                                             "no native transcript found for this session yet")
        # A host session's transcript is the CLI's own native transcript wherever the
        # configured root that contains it sits; the store flag governs history rows.
        return self.transcript_context(self.stores[store]["root"], path, uuid=uuid, record=record,
                                       source="native-transcript",
                                       window=window, windows=windows, now=now, not_before=not_before)

    def _scope_ref(self, index, scope):
        return self._ref("scope", (index.store, index.path, scope))

    # @beacon[
    #   id=opc-pane@ClaudeReader.sessions,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Native inventory paging: every fresh read walks the history folders as its own scan (LRU of INVENTORY_SCANS), so two readers (a second tab, the sidebar's refresh beside a Reconnect) never reset each other's cursor.,
    # ]
    def sessions(self, cursor=None, storeRef=None, workspace=None):
        with self.lock:
            if cursor:
                token, start, old_filter, old_workspace = self._get(cursor, "inventory-cursor")
                scan = self.scans.get(token)
                if scan is None:
                    raise ReaderError(409, "stale", "inventory scan expired; reselect")
                if (old_filter, old_workspace) != (storeRef, workspace):
                    raise ReaderError(400, "schema", "cursor filter mismatch")
                self.scans.move_to_end(token)
                # Returned summaries leave the scan; index bodies were never cached.
                for key in [k for k, (sequence, _) in scan.found.items() if sequence <= start]:
                    del scan.found[key]
            else:
                token, start, scan = secrets.token_urlsafe(24), 0, InventoryScan()
                self.scans[token] = scan
                while len(self.scans) > INVENTORY_SCANS:
                    self.scans.popitem(last=False)[1].close()
            diagnostics, discovery_done = self._discover(scan)
            sid_filter = None
            if storeRef:
                matches = [sid for sid, config in self.stores.items() if config["ref"] == storeRef]
                if not matches:
                    raise ReaderError(409, "stale", "unknown store; reselect")
                sid_filter = matches[0]
            rows, work = [], 0
            end = start
            for (sid, path), (sequence, session) in sorted(scan.found.items(), key=lambda item: item[1][0]):
                if sequence <= start:
                    continue
                if work >= SUMMARY_FILES_PER_PAGE:
                    break
                end = max(end, sequence)
                if sid_filter and sid != sid_filter:
                    continue
                work += 1
                # The shared summary cache may have dropped this file since the walk found it.
                summary = self.inventory.setdefault((sid, path), {"session": session, "sequence": 0, "status": "indexing",
                                                                  "cwd": "unknown", "title": session})
                try:
                    index = self._index(sid, path)
                    summary = self.inventory[(sid, path)]
                    scopes = sorted({r.scope for r in index.records}) or ["main"]
                    epoch = index.epoch
                except ReaderError as e:
                    summary = {**summary, "status": e.status}
                    scopes, epoch = [], None
                if workspace is not None and summary["cwd"] != workspace:
                    continue
                config = self.stores[sid]
                rows.append({"storeRef": config["ref"], "sessionRef": self._ref("session", (sid, path)),
                             "uuid": summary["session"], "title": summary["title"], "cwd": summary["cwd"],
                             "status": summary["status"], "fileEpoch": epoch,
                             "scopes": [{"label": s, "scopeRef": self._scope_ref(index, s)} for s in scopes],
                              "capabilities": {"history": True, "nativeInput": False, "subagents": True},
                             "source": "native" if config.get("native") else "windows-history",
                             **self._agent_labels(sid, path)})
            more = any(sequence > end for sequence, _ in scan.found.values()) or not discovery_done
            if not more:
                self.scans.pop(token, None)
            return envelope(sessions=rows, diagnostics=diagnostics, hasMore=more,
                            nextCursor=self._ref("inventory-cursor", (token, end, storeRef, workspace)) if more else None,
                             inventoryRevision=scan.revision, status="complete" if discovery_done else "discovering")

    # @beacon[
    #   id=opc-pane@ClaudeReader.subagents,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Bounded parent-scoped native child discovery for the live flat session list; opaque replay-checked paging and contained sidecar labels only, never process or Send authority.,
    # ]
    def subagents(self, sessionRef, cursor=None, *, parent_title=None):
        with self.lock:
            sid, path = self._get(sessionRef, "session")
            agent = self._agent(sid, path)
            if agent:
                path = self.stores[sid]["root"] / path.relative_to(self.stores[sid]["root"]).parts[0] / (agent[0] + ".jsonl")
            parent = path.stem
            if not UUID.fullmatch(parent):
                raise ReaderError(409, "ambiguous", "subagent discovery requires a primary session")
            if cursor:
                token, position, owner = self._get(cursor, "subagent-cursor")
                scan = self.subagent_scans.get(token)
                if scan is None or scan[1] != position or owner != (sid, path):
                    raise ReaderError(409, "stale", "subagent inventory changed; refresh")
            else:
                token = secrets.token_urlsafe(24)
                directory = path.with_suffix("") / "subagents"
                try:
                    if directory.is_symlink():
                        raise ReaderError(403, "containment", "subagent directory symlink refused")
                    iterator = os.scandir(directory)
                except FileNotFoundError:
                    return envelope(sessions=[], diagnostics=[], hasMore=False, nextCursor=None, status="complete")
                scan = [iterator, 0]
                self.subagent_scans[token] = scan
                while len(self.subagent_scans) > 8:
                    _, old = self.subagent_scans.popitem(last=False)
                    old[0].close()
            rows, done = [], False
            try:
                for _ in range(DISCOVERY_STEPS):
                    try:
                        entry = next(scan[0])
                    except StopIteration:
                        done = True
                        break
                    scan[1] += 1
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False) or not AGENT_FILE.fullmatch(entry.name):
                        continue
                    child = Path(entry.path)
                    self.inventory.setdefault((sid, child), {"session": parent, "sequence": 0})
                    index = self._index(sid, child)
                    scope = "agent:" + self._agent(sid, child)[1]
                    rows.append({"storeRef": self.stores[sid]["ref"],
                        "sessionRef": self._ref("session", (sid, child)), "uuid": parent,
                        "cwd": next(iter(index.cwds)) if len(index.cwds) == 1 else "unknown",
                        "status": index.status, "fileEpoch": index.epoch,
                        "scopes": [{"label": scope, "scopeRef": self._scope_ref(index, scope)}],
                        "source": "native", "state": "transcript-observed",
                        "capabilities": {"history": True, "live": True, "inspect": True,
                                         "nativeInput": False, "controls": False, "context": False, "subagents": True},
                        **self._agent_labels(sid, child, parent_title)})
                    if len(rows) >= SUMMARY_FILES_PER_PAGE:
                        break
            except OSError as error:
                raise ReaderError(502, "read-failure", "subagent discovery unavailable") from error
            if done:
                self.subagent_scans.pop(token, None)
                scan[0].close()
            return envelope(sessions=rows, diagnostics=[], hasMore=not done,
                nextCursor=self._ref("subagent-cursor", (token, scan[1], (sid, path))) if not done else None,
                status="complete" if done else "discovering")

    def _path(self, index, scope, leafRef=None):
        records = {r.uuid: r for r in index.records if r.scope == scope and r.uuid and r.kind != "replay"}
        parents = {r.parent for r in records.values() if r.parent in records}
        leaves = [r for uid, r in records.items() if uid not in parents]
        # Parallel tool calls: native 2.1.299 writes one assistant record per tool_use
        # block (chained), parents each tool_result on ITS OWN tool_use record, and the
        # turn continues from one of those results. The other tool_use/tool_result
        # records form dead-end "plumbing" side chains hanging off an assistant tool_use
        # record of the main chain. They are not conversation branches: they never count
        # as leaves and join the selected path below in file order, so every call shows
        # its result. Anything else (a re-roll/edit fork) stays an explicit choice.
        def plumbing(r):
            return ((r.kind == "user" and r.results and not r.prose_bytes and not r.calls) or
                    (r.kind == "assistant" and r.calls and not r.prose_bytes))
        side = {}
        if len(leaves) > 1:
            open_leaves = [r for r in leaves if not plumbing(r)]
            main = open_leaves[0] if len(open_leaves) == 1 else None
            if not open_leaves:
                depth = {}
                for uid, r in records.items():
                    depth[uid] = depth.get(r.parent, 0) + 1
                main = max(leaves, key=lambda r: (depth.get(r.uuid, 0), r.offset))
            if main is not None:
                chain, at = set(), main
                while at is not None and at.uuid not in chain:
                    chain.add(at.uuid)
                    at = records.get(at.parent)
                for r in leaves:
                    if r is main:
                        continue
                    at, walked = r, []
                    while at is not None and at.uuid not in chain and plumbing(at) and at not in walked:
                        walked.append(at)
                        at = records.get(at.parent)
                    if at is not None and at.uuid in chain and at.kind == "assistant" and at.calls and not at.prose_bytes:
                        side[r.uuid] = (at.uuid, walked)
        real = [r for r in leaves if r.uuid not in side]
        choices = [{"leafRef": self._ref("leaf", (index.store, index.path, scope, r.uuid, index.epoch)),
                    "uuid": r.uuid} for r in real]
        if leafRef:
            sid, path, sc, uid, epoch = self._get(leafRef, "leaf")
            if (sid, path, sc, epoch) != (index.store, index.path, scope, index.epoch) or uid not in records:
                raise ReaderError(409, "stale-epoch", "leaf identity changed; reselect")
            leaf = records[uid]
        elif len(real) == 1:
            leaf = real[0]
        else:
            return None, choices
        path, seen = [], set()
        at = leaf
        while at:
            if at.uuid in seen or (scope, at.uuid) in index.ambiguous or at.kind == "diagnostic":
                raise ReaderError(409, "ambiguous", "unsupported or conflicting parent chain")
            # Prefix witnesses validated by _refresh and stable file signature cover
            # metadata ancestry; only returned/selected bodies require full rereads.
            seen.add(at.uuid)
            path.append(at)
            if at.parent is not None and at.parent not in records:
                raise ReaderError(409, "ambiguous", "missing parent linkage")
            at = records.get(at.parent)
        self._stable(index)
        result = LeafPath(reversed(path))
        if side:
            # Absorb the plumbing side chains attached to this path, in file order
            # (append-only, so later arrivals extend the path, never reorder it).
            on_path = {r.uuid for r in path}
            extra = {r.uuid: r for attach, walked in side.values() if attach in on_path
                     for r in walked if r.uuid not in on_path and (scope, r.uuid) not in index.ambiguous}
            if extra:
                result = LeafPath(sorted(path + list(extra.values()), key=lambda r: r.offset))
        result.leaf = leaf.uuid  # the path's identity: the walked leaf, not a side record
        return result, choices

    def _extension(self, index, scope, selected):
        current, choices = self._path(index, scope)
        if not selected:
            if current is None:
                raise ReaderError(409, "ambiguous", "multiple descendant leaves; explicitly reselect")
            return current, choices
        if current is None:
            descendants = []
            for choice in choices:
                path, _ = self._path(index, scope, choice["leafRef"])
                if any(r.uuid == selected[-1].uuid for r in path):
                    descendants.append(path)
            if len(descendants) != 1:
                raise ReaderError(409, "ambiguous", "multiple descendant leaves; explicitly reselect")
            current = descendants[0]
        boundary = selected[-1].uuid
        if not any(r.uuid == boundary for r in current):
            raise ReaderError(409, "stale", "selected path no longer extends boundary")
        return current, choices

    def _target(self, targetRef):
        sid, path, scope, uid, epoch = self._get(targetRef, "target")
        if self._agent(sid, path) or scope != "main":
            raise ReaderError(409, "ambiguous", "subagents never authorize a main target")
        index = self._index(sid, path)
        if index.epoch != epoch:
            raise ReaderError(409, "stale-epoch", "target epoch changed; reselect")
        leaf = self._ref("leaf", (sid, path, scope, uid, epoch))
        selected, choices = self._path(index, scope, leaf)
        if index.status not in ("complete", "incomplete-tail"):
            raise ReaderError(409, "indexing", "target validation requires complete index")
        return index, scope, selected, choices

    # @beacon[
    #   id=opc-pane@events,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Whole-event chronological snapshot/delta paging; saturated indexes retain the indexed prefix and truthful later-history limit metadata without enabling incomplete targets.,
    # ]
    def events(self, *, sessionRef=None, scopeRef=None, leafRef=None, fileEpoch=None, targetRef=None, cursor=None,
               fromSeq=None):
        check_from_seq(fromSeq, targetRef, leafRef, cursor)
        with self.lock:
            if targetRef:
                if any((sessionRef, scopeRef, leafRef, fileEpoch)):
                    raise ReaderError(400, "schema", "mixed addressing forms")
                index, scope, selected, choices = self._target(targetRef)
            else:
                if not sessionRef or not scopeRef:
                    raise ReaderError(400, "schema", "sessionRef and scopeRef required")
                index = self._session(sessionRef)
                sid, path, scope = self._get(scopeRef, "scope")
                if (sid, path) != (index.store, index.path):
                    raise ReaderError(409, "ambiguous", "scope/session mismatch")
                if fileEpoch and fileEpoch != index.epoch:
                    raise ReaderError(409, "stale-epoch", "epoch changed; reselect")
                if cursor and not leafRef:
                    expected = self._get(cursor, "event-cursor")[0]
                    if expected[:3] != (index.store, index.path, scope) or expected[4] != index.epoch:
                        raise ReaderError(409, "stale-epoch", "cursor scope/epoch changed")
                    leafRef = self._ref("leaf", expected) if expected[3] is not None else None
                selected, choices = self._path(index, scope, leafRef) if index.status in ("complete", "incomplete-tail", "index-limit") else (None, [])
                if cursor and selected is None and index.status in ("indexing", "validating"):
                    # A growing transcript re-verifies its indexed prefix in bounded steps
                    # (a large running subagent takes a few requests per append). That is
                    # not an identity change: answer "nothing new yet" with the same cursor
                    # so the viewer simply asks again, instead of a hard reselect refusal.
                    expected = self._get(cursor, "event-cursor")[0]
                    return envelope(sessionRef=self._ref("session", (index.store, index.path)),
                                    scopeRef=self._scope_ref(index, scope), leafRef=self._ref("leaf", expected),
                                    fileEpoch=index.epoch, targetRef=self._ref("target", expected)
                                    if scope == "main" and expected[3] is not None and not self._agent(index.store, index.path) else None,
                                    events=[], branches=[], traces=[], diagnostics=[], indexRevision=index.revision,
                                    snapshotRevision=index.revision, status=index.status, hasMore=False,
                                    change="append", nextCursor=None, deltaCursor=cursor,
                                    sourceProgress={"indexedBytes": index.offset, "indexedRecords": len(index.records),
                                                    "sourceBytes": index.signature[2] if index.signature else None,
                                                    "status": index.status, "kind": "native-transcript"})
            path_status = "index-limit" if index.status == "index-limit" else (
                "complete" if selected is not None else "ambiguous" if index.status == "complete" else index.status)
            selected = selected or []
            uid = leaf_uuid(selected)
            identity = (index.store, index.path, scope, uid, index.epoch)
            # fromSeq: this first page starts at that position on the path (seq), with fresh cursors.
            start = min(fromSeq or 0, len(selected))
            diagnostic_start = 0
            snapshot_revision = index.revision
            if cursor:
                value = self._get(cursor, "event-cursor")
                expected, start, direction, boundary_revision = value[:4]
                diagnostic_start = value[4] if len(value) > 4 else 0
                if expected != identity:
                    raise ReaderError(409, "stale-epoch", "cursor scope/leaf/epoch mismatch")
                if boundary_revision > index.revision:
                    raise ReaderError(409, "stale", "index revision reset")
                snapshot_revision = boundary_revision
                if direction == "delta":
                    selected, choices = self._extension(index, scope, selected)
                    uid = leaf_uuid(selected)
                    identity = (index.store, index.path, scope, uid, index.epoch)
                    snapshot_revision = index.revision
            # Incomplete history remains browseable, never a complete context/input
            # target. Keep the existing _target/snapshot refusals intact.
            target = self._ref("target", identity) if scope == "main" and selected and index.status != "index-limit" else None
            rows, diagnostics, used, end = [], [], 0, start
            for number in range(start, len(selected)):
                record = selected[number]
                row = self._event(index, record, selected, identity)
                size = len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
                if size > PAGE_LIMIT - 32 * 1024:
                    row = {"recordRef": row["recordRef"], "kind": record.kind, "status": "response-limit",
                           "bodyBytes": size, "text": None, "limitBytes": PAGE_LIMIT,
                           "thinking": [], "thinkingStatus": "response-limit",
                           "thinkingBlocks": len(row.get("thinking", [])), "partial": False}
                    size = len(json.dumps(row).encode())
                if rows and (len(rows) >= PAGE_EVENTS or used + size > PAGE_LIMIT - 32 * 1024):
                    break
                row["seq"] = number  # position on this path: the viewer keeps one contiguous run
                rows.append(row)
                used += size
                end = number + 1
            # Malformed/unknown boundaries share scope/epoch/snapshot paging;
            # exhaustion of prose does not mean exhaustion of diagnostics.
            boundaries = [r for r in index.records[:snapshot_revision]
                          if r.reason and (r.scope == scope or r.kind == "diagnostic")]
            diagnostic_end = min(diagnostic_start + 100, len(boundaries))
            if diagnostic_start == 0:
                for kind, number in sorted(index.quiet.items()):
                    diagnostics.append({"offset": "counted", "status": f"{number:,} {kind} record(s): copies of API "
                                        "requests, not conversation; counted here, not listed one by one",
                                        "quiet": kind, "count": number})
            for record in boundaries[diagnostic_start:diagnostic_end]:
                diagnostics.append({"offset": record.offset, "status": record.reason,
                                    "recordRef": self._ref("record", (index.store, index.path, record.scope, index.epoch, record.uuid, record.offset))})
            more = end < len(selected) or diagnostic_end < len(boundaries)
            # The context line is auxiliary: its bounded tail read must never break
            # history paging, so any failure is an honest unknown, not a failed page.
            try:
                # A subagent's context is its OWN last call, from its own transcript
                # (sidechain records of that agent only), never the parent's figure.
                agent = self._agent(index.store, index.path)
                context_readout = self.transcript_context(
                    index.root, index.path, uuid=agent[0] if agent else index.session,
                    record={"role": "subagent" if agent else "history", "execution_id": None, "generation": None,
                            "native_uuid": agent[0] if agent else index.session},
                    source="subagent-transcript" if agent else
                    "native-transcript" if self.stores[index.store].get("native") else "windows-history",
                    agent_id=agent[1] if agent else None)
            except Exception as error:  # noqa: BLE001
                context_readout = self._context_unavailable({}, "native-transcript", "context read failed: " + str(error)[:120])
            source_progress = {"indexedBytes": index.offset, "indexedRecords": len(index.records),
                               "sourceBytes": index.signature[2] if index.signature else None,
                               "status": index.status, "kind": "native-transcript"}
            return envelope(sessionRef=self._ref("session", (index.store, index.path)),
                            scopeRef=self._scope_ref(index, scope), leafRef=self._ref("leaf", identity),
                            fileEpoch=index.epoch, targetRef=target, events=rows, branches=choices,
                            traces=sorted({r.scope for r in index.records if r.scope != "main"}),
                            diagnostics=diagnostics, indexRevision=index.revision, snapshotRevision=snapshot_revision,
                            status=path_status, hasMore=more,
                            contextReadout=context_readout, sourceProgress=source_progress,
                            indexLimit={"status": "index-limit", "message": "Later history not indexed; only the indexed prefix is available.",
                                        "indexedRecords": len(index.records), "indexedBytes": index.offset,
                                        "sourceBytes": index.signature[2], "summaryBytes": index.summary_bytes,
                                        "summaryLimitBytes": self.summary_limit, "recordLimit": self.index_records}
                                       if index.status == "index-limit" else None,
                            change="append" if cursor and direction == "delta" else "snapshot",
                            nextCursor=self._ref("event-cursor", (identity, end, "forward", snapshot_revision, diagnostic_end)) if more else None,
                            deltaCursor=self._ref("event-cursor", (identity, len(selected), "delta", snapshot_revision, len(boundaries))) if selected else None)

    def _join(self, index, call_record, call, selected):
        candidates = [(r, c) for r in selected for c in r.calls if c["id"] == call["id"]]
        results = [(r, b) for r in selected for b in r.results if b["id"] == call["id"]]
        if len(candidates) != 1 or len(results) > 1:
            return "ambiguous", None
        if not results:
            return "pending-or-missing", None
        result, block = results[0]
        if block["source"] is not None and block["source"] != call_record.uuid:
            return "ambiguous", None
        return "error" if block["error"] else "stored", (result, block)

    def _event(self, index, record, selected, identity):
        if record.kind not in ("user", "assistant"):
            self._stable(index)
            return {"recordRef": self._ref("record", (index.store, index.path, record.scope, index.epoch, record.uuid, record.offset)),
                    "uuid": record.uuid, "kind": record.kind, "scope": record.scope,
                    "status": record.reason or "metadata", "text": "", "textBytes": 0,
                    "calls": [], "thinking": [], "partial": False,
                    "orphanResults": [], "replays": record.replay, "time": record.time}
        text, root = self._read(index, record)
        fields = root.members()
        prose = self._prose(fields, fields["message"].members(), record, main_only=False) if record.kind in ("user", "assistant") else ""
        size = len(prose.encode("utf-8"))
        thinking, remaining = [], max(0, BODY_LIMIT - size)
        content = fields["message"].members()["content"]
        for summary in record.thinking:
            item, charged = thinking_projection(content.children[summary["blockIndex"]].members(),
                                                summary["blockIndex"], remaining)
            thinking.append(item)
            remaining -= charged
        calls = []
        for call in record.calls:
            status, result = self._join(index, record, call, selected)
            cref = self._ref("call", (identity, record.uuid, call["block"], call["id"]))
            # time/resultTime as the call's snapshot states them (display only: how long it took, on its row).
            calls.append({**call, "callRef": cref, "status": status,
                          "outputBytes": result[1]["outputBytes"] if result else None,
                          "time": record.time, "resultTime": getattr(result[0], "time", None) if result else None})
        orphans = [b["id"] for b in record.results if not any(c["id"] == b["id"] for r in selected for c in r.calls)]
        facts = {}
        if record.kind == "assistant":
            # The block's model and thinking level exactly as this record states them (display only).
            message = fields["message"].members()
            api_error = "isApiErrorMessage" in fields and fields["isApiErrorMessage"].kind == "true"
            _, model, level = call_facts(_span_fact(message, "model"), _span_fact(fields, "effort"),
                                         _span_fact(fields, "perTurnEffort"), api_error)
            facts = {"model": model, "effort": level}
        return {**facts, "recordRef": self._ref("record", (index.store, index.path, record.scope, index.epoch, record.uuid, record.offset)),
                "uuid": record.uuid, "kind": record.kind, "scope": record.scope,
                "status": "body-limit" if size > BODY_LIMIT else record.reason or "stored",
                "text": prose if size <= BODY_LIMIT else None, "textBytes": size,
                "calls": calls, "thinking": thinking, "partial": False,
                "orphanResults": orphans, "replays": record.replay, "time": record.time,
                # Display-only record facts: a user record that only carries tool
                # results (already shown under their calls) can be folded away.
                "toolResults": len(record.results),
                "isMeta": scalar(fields, "isMeta", default=False) is True,
                "originKind": (scalar(fields["origin"].members(), "kind", "string") if "origin" in fields
                               and fields["origin"].kind == "object" else None)}

    def validate_target(self, targetRef):
        with self.lock:
            index, scope, selected, _ = self._target(targetRef)
            return envelope(targetRef=targetRef, fileEpoch=index.epoch, scopeRef=self._scope_ref(index, scope),
                            leafUuid=leaf_uuid(selected), indexRevision=index.revision, status="valid")

    # @beacon[
    #   id=opc-pane@context,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Frozen Refine history: the same eligible main prose in the historical byte-budgeted tail or the whole uncropped conversation, plus one UTF-16 end boundary per included block for the provider's cached prefix; Read Aloud's `before` (a record UUID) keeps only the history before that record.,
    # ]
    def context(self, targetRef, targetRevision, budgetBytes=50 * 1024, historyMode="budgeted", before=None):
        mode = check_history_mode(historyMode)
        if mode == "budgeted" and (type(budgetBytes) is not int or not 5 * 1024 <= budgetBytes <= 400 * 1024):
            raise ReaderError(400, "schema", "native context budget must be 5–400 KiB")
        if type(targetRevision) is not int or not 0 <= targetRevision <= 2**53 - 1:
            raise ReaderError(400, "schema", "invalid targetRevision")
        with self.lock:
            index, scope, selected, _ = self._target(targetRef)
            if scope != "main":
                raise ReaderError(409, "ambiguous", "context requires main path")
            pinned_uid = leaf_uuid(selected)
            history, diagnostics = HistoryBlocks(mode, budgetBytes), []
            found = before is None
            for record in reversed(selected):
                if not found:  # Read Aloud: everything up to, not including, the clicked record
                    found = record.uuid == before
                    continue
                if record.kind not in ("user", "assistant"):
                    diagnostics.append({"uuid": record.uuid, "reason": "control-or-metadata"})
                    continue
                text, root = self._read(index, record)
                fields = root.members()
                prose = self._prose(fields, fields["message"].members(), record)
                if not prose or record.reason or record.prose_bytes > BODY_LIMIT:
                    diagnostics.append({"uuid": record.uuid, "reason": record.reason or "excluded-or-body-limit"})
                    continue
                label = "[USER]" if record.kind == "user" else "[ASSISTANT]"
                status = history.add(record.uuid or "record:" + record.hash, label, prose)
                if status != "fits":
                    if status == "truncated":
                        history.users += record.kind == "user"
                    break
                history.users += record.kind == "user"
            if not found:
                raise ReaderError(409, "missing", "that message is not on this conversation's main path")
            result, boundaries = history.finish()
            self._stable(index)  # replacement during projection must not publish a usable context witness
            return envelope(targetRef=targetRef, targetRevision=targetRevision,
                            storeRef=self.stores[index.store]["ref"],
                            leafRef=self._ref("leaf", (index.store, index.path, scope, pinned_uid, index.epoch)),
                            sessionRef=self._ref("session", (index.store, index.path)),
                            scopeRef=self._scope_ref(index, scope), fileEpoch=index.epoch,
                            leafUuid=pinned_uid, indexRevision=index.revision,
                            text=result, bytes=len(result.encode("utf-8")), turns=len(boundaries),
                            users=history.users, historyMode=mode, historyBoundaries=boundaries,
                            readAt=int(time.time() * 1000), exclusions=diagnostics[:100], status="stored")

    def snapshot(self, callRef, fileEpoch):
        with self.lock:
            identity, uid, number, callid = self._get(callRef, "call")
            sid, path, scope, leaf, epoch = identity
            index = self._index(sid, path)
            if epoch != fileEpoch or epoch != index.epoch:
                raise ReaderError(409, "stale-epoch", "call witness changed; reselect")
            selected, _ = self._path(index, scope, self._ref("leaf", identity))
            selected, _ = self._extension(index, scope, selected)
            if index.status not in ("complete", "incomplete-tail"):
                raise ReaderError(409, "indexing", "selected snapshot unavailable during indexing")
            matches = [(r, c) for r in selected for c in r.calls if (r.uuid, c["block"], c["id"]) == (uid, number, callid)]
            if len(matches) != 1:
                raise ReaderError(409, "ambiguous", "call witness unavailable")
            record, call = matches[0]
            text, root = self._read(index, record)
            block = root.members()["message"].members()["content"].children[number].members()
            inp = projection(text, block.get("input"))
            status, result = self._join(index, record, call, selected)
            out, artifact, result_time, diff_metadata = None, None, None, None
            if result:
                rr, rb = result
                result_time = getattr(rr, "time", None)  # display only: how long the call took
                wire, tree = self._read(index, rr)
                diff_metadata = recorded_value(wire, tree.members().get("toolUseResult"))
                resultblock = tree.members()["message"].members()["content"].children[rb["block"]].members()
                out = result_projection(wire, resultblock.get("content"))
                # Synthetic-v1 metadata is policy evidence only; no undocumented native regex.
                saved = resultblock.get("savedArtifact")
                if saved and scalar(tree.members(), "version", "string") == "synthetic-v1":
                    metadata = saved.members()
                    rel = scalar(metadata, "relativePath", "string")
                    completeness = scalar(metadata, "completeness", "string")
                    if rel and completeness in ("full", "limited"):
                        artifact_path = index.path.parent / "tool-results" / rel
                        artifact = self._ref("artifact", (callRef, epoch, artifact_path, completeness))
            counts = [None if value is None else len(value.encode("utf-8")) for value in (inp, out)]
            if sum(v or 0 for v in counts) > BODY_LIMIT:
                raise ReaderError(413, "body-limit", "combined stored snapshot exceeds 8 MiB; not cropped")
            return envelope(callRef=callRef, fileEpoch=epoch, input=inp, output=out,
                            diff=claude_diff(call["name"], recorded_value(text, block.get("input")), diff_metadata, status),
                            inputBytes=counts[0], outputBytes=counts[1], status=status,
                            inputAvailability="stored" if inp is not None else "missing",
                            outputAvailability="stored" if out is not None else status,
                            caption="stored", artifactRef=artifact, provenance="recorded-jsonl",
                            time=getattr(record, "time", None), resultTime=result_time)

    def artifact(self, artifactRef, callRef, fileEpoch):
        with self.lock:
            expected, epoch, path, completeness = self._get(artifactRef, "artifact")
            if (expected, epoch) != (callRef, fileEpoch):
                raise ReaderError(409, "ambiguous", "artifact call/epoch witness mismatch")
            self.snapshot(callRef, fileEpoch)
            identity = self._get(callRef, "call")[0]
            index = self.files[(identity[0], identity[1])]
            approved = index.path.parent / "tool-results"
            # Root walk must also validate the project/tool-results directory components.
            if approved not in path.parents:
                raise ReaderError(403, "containment", "artifact outside approved tool-results")
            with open_contained(index.root, path) as source:
                if os.fstat(source.fileno()).st_size > SOURCE_LIMIT:
                    raise ReaderError(413, "source-limit", "artifact source exceeds 16 MiB")
                raw = source.read(BODY_LIMIT + 1)
            if len(raw) > BODY_LIMIT:
                raise ReaderError(413, "body-limit", "saved artifact exceeds 8 MiB; not cropped")
            try:
                text = raw.decode("utf-8")
            except UnicodeError as e:
                raise ReaderError(409, "unsupported", "artifact is not UTF-8 text") from e
            return envelope(artifactRef=artifactRef, callRef=callRef, fileEpoch=fileEpoch,
                            text=text, bytes=len(raw), availability="stored", completeness=completeness,
                            caption="saved " + completeness + " artifact; recorded snapshot unchanged")
