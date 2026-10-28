#!/usr/bin/env python3
"""Permanent, reduced Claude/OpenCode history. Run through ``oc.sh history``.

Archiving sources are read-only; the explicit prune executor alone removes
verified live copies. This module alone writes the history archive; the pane
does not import it. A session, its body and its unit are committed together.
There are deliberately no locks outside SQLite, retries, retention limits or
source caches. Automatic trimming is Dan's trim_enabled switch; manual prune
is the Manager's one-run act on Dan's word. The host owns forget and junk:
``junk`` asks the host to remove only its empty-sid row residue; it never
deletes a conversation or decides trim eligibility.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import time
import zlib

# The archiver's only persistent outputs are its configured data/logs, not
# import caches beside source files (also when launched outside the test env).
sys.dont_write_bytecode = True

import oc_client
import oc_history_settings
import oc_seal_ledger
from claude_headless.host import list_program, list_recency
from claude_headless.settlement import owner_proved_gone
from oc_pins import SessionPins

PANE = Path(__file__).resolve().parents[1] / 'transcription-pane'
if str(PANE) not in sys.path:
    sys.path.insert(0, str(PANE))
from claude_reader import TurnLedger
from opencode_programs import opencode_row, _core, SEAT_TITLE, SESSION_ID

SCHEMA_VERSION = ARCHIVE_VERSION = 1
SESSION_TYPES = (
    ('key', 'TEXT PRIMARY KEY NOT NULL'), ('unit_key', 'TEXT NOT NULL'),
    ('backend', 'TEXT NOT NULL'), ('id', 'TEXT NOT NULL'), ('native_uuid', 'TEXT'),
    ('program_core', 'TEXT'), ('parent_key', 'TEXT'), ('kind', 'TEXT NOT NULL'),
    ('role', 'TEXT'), ('title', 'TEXT'), ('chosen_name', 'TEXT'), ('socket', 'TEXT'),
    ('manager_session', 'TEXT'), ('cwd', 'TEXT'), ('created_at', 'INTEGER NOT NULL'),
    ('first_at', 'INTEGER'), ('last_at', 'INTEGER NOT NULL'), ('close_reason', 'TEXT'),
    ('outcome', 'TEXT'), ('cost_usd', 'REAL'), ('tokens_json', 'TEXT'),
    ('models_json', 'TEXT NOT NULL'), ('levels_json', 'TEXT NOT NULL'),
    ('turns', 'INTEGER NOT NULL'), ('turn_facts_json', 'TEXT NOT NULL'),
    ('telemetry_json', 'TEXT'), ('source_json', 'TEXT NOT NULL'), ('notes_json', 'TEXT'),
    ('raw_bytes', 'INTEGER NOT NULL'), ('witness', 'TEXT NOT NULL'),
    ('body_sha256', 'TEXT NOT NULL'), ('body_bytes', 'INTEGER NOT NULL'),
    ('archived_at', 'INTEGER NOT NULL'), ('pruned_at', 'INTEGER'),
    ('archive_version', 'INTEGER NOT NULL'),
)
SESSION_COLUMNS = tuple(name for name, _ in SESSION_TYPES)
UNIT_COLUMNS = ('unit_key', 'kind', 'program_core', 'first_at', 'last_at', 'sessions',
                'cost_usd', 'backends_json', 'sealed_at', 'seal', 'telemetry_json', 'archived_at')
DDL = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions ({','.join(n + ' ' + t for n, t in SESSION_TYPES)},
    UNIQUE(backend,id));
CREATE INDEX IF NOT EXISTS sessions_unit_last ON sessions(unit_key,last_at);
CREATE INDEX IF NOT EXISTS sessions_program ON sessions(program_core);
CREATE INDEX IF NOT EXISTS sessions_last ON sessions(last_at);
CREATE TABLE IF NOT EXISTS bodies (key TEXT PRIMARY KEY REFERENCES sessions(key),
    codec TEXT NOT NULL CHECK(codec = 'zlib-json'), body BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS units (unit_key TEXT PRIMARY KEY NOT NULL, kind TEXT NOT NULL,
    program_core TEXT, first_at INTEGER NOT NULL, last_at INTEGER NOT NULL,
    sessions INTEGER NOT NULL, cost_usd REAL, backends_json TEXT NOT NULL,
    sealed_at INTEGER, seal TEXT, telemetry_json TEXT, archived_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS units_last ON units(last_at DESC,unit_key DESC);
"""


def compact(value, *, sort_keys=False):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=sort_keys)


def now_ms():
    return int(time.time() * 1000)


def stamp_ms(value):
    """Claude ISO/seconds -> archive milliseconds, never OpenCode's already-ms clock."""
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)
        except (ValueError, OverflowError):
            return None
    if type(value) in (int, float):
        return int(value * 1000)
    return None


def open_archive(path, *, readonly=False):
    """Open the sole writer's schema, or an existing archive read-only (absent = None)."""
    path = Path(path) if str(path) != ':memory:' else ':memory:'
    if readonly:
        if not path.exists():
            return None
        con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, isolation_level=None)
    else:
        if path != ':memory:':
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(path.parent, 0o700)
        con = sqlite3.connect(str(path), isolation_level=None, timeout=10)
        con.execute('PRAGMA busy_timeout=10000')
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA synchronous=FULL')
        con.execute('PRAGMA foreign_keys=ON')
        try:
            con.executescript('BEGIN IMMEDIATE;\n' + DDL)
            for key, value in (('schema_version', SCHEMA_VERSION), ('archive_version', ARCHIVE_VERSION),
                               ('created_at', now_ms())):
                con.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', (key, str(value)))
            con.commit()
        except BaseException:
            con.rollback()
            con.close()
            raise
        if path != ':memory:':
            os.chmod(path, 0o600)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=10000')
    return con


def meta_get(con, key):
    row = con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone() if con else None
    return row[0] if row else None


def meta_set(con, key, value):
    con.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                (key, str(value)))


class IncompleteTranscript(ValueError):
    """The writer has not finished the last JSONL record; take it on the next run."""


def transcript_records(path):
    """Stream records and parse failures, with no line/size cap or on-disk parse cache."""
    with Path(path).open('rb') as source:
        for line in source:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    record = None
            except (ValueError, UnicodeError, RecursionError):
                if not line.endswith(b'\n'):
                    raise IncompleteTranscript('incomplete-last-line') from None
                record = None
            yield line, record


# @beacon[
#   id=opc-history@claude_transcript_witness,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The sole transcript witness: inode size mtime_ns and last parseable record UUID in canonical sorted JSON; verification scans the whole file while archiving may pass its same-scan snapshot. An incomplete final JSONL record waits for the next sweep.,
# ]
def claude_transcript_witness(path, *, snapshot=None):
    if snapshot is None:
        stat = Path(path).stat()
        last_uuid = None
        for _line, record in transcript_records(path):
            if record and isinstance(record.get('uuid'), str):
                last_uuid = record['uuid']
    else:
        stat, last_uuid = snapshot
    return compact({'ino': stat.st_ino, 'kind': 'claude-transcript', 'last_uuid': last_uuid,
                    'mtime_ns': stat.st_mtime_ns, 'size': stat.st_size}, sort_keys=True)


# @beacon[
#   id=opc-history@claude_record_witness,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The sole witness for a host launch with no transcript: generation state and the host's own list_recency clock, without copying the host's attribution or recency rules.,
# ]
def claude_record_witness(record):
    return compact({'kind': 'claude-record', 'generation': record.get('generation'),
                    'last_activity_at': list_recency(record), 'state': record.get('state')}, sort_keys=True)


# @beacon[
#   id=opc-history@opencode_witness,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The sole OpenCode witness: all-message count plus updated and idle clocks from info read BEFORE messages; the idle marker detects assistant rewrites at a turn's end.,
# ]
def opencode_witness(info, messages):
    times = info.get('time') or {}
    return compact({'kind': 'opencode-session', 'messages': len(messages),
                    'time_idle': times.get('idle'), 'time_updated': times.get('updated')}, sort_keys=True)


@dataclass
class Reduced:
    turns: list = field(default_factory=list)
    created_at: int | None = None
    last_at: int | None = None
    cost: float | None = None
    tokens: dict | None = None
    cwd: str | None = None
    facts: dict = field(default_factory=dict)
    notes: dict = field(default_factory=lambda: {'unparsable_lines': 0, 'unknown_record_types': {}})
    witness: str = ''
    raw_bytes: int = 0
    fork: bool = False


def turn(role, ts, text):
    return {'seq': 0, 'role': role, 'ts': ts, 'text': text}


def add_turn(reduced, value):
    value['seq'] = len(reduced.turns)
    reduced.turns.append(value)


def tool_entry(name, status, value, child=None):
    return {'name': name, 'status': status, 'preview': compact(value)[:120], 'child': child}


def note_unknown(reduced, kind):
    key = str(kind or '<missing>')
    counts = reduced.notes['unknown_record_types']
    counts[key] = counts.get(key, 0) + 1


# @beacon[
#   id=opc-history@reduce_claude,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The one archive Claude reducer: file-order prompts and answers, coalesced split assistant message IDs, ordered input previews with tool-result status and subagent links; no thinking or outputs. Parse failures and unknown types are counted, native fork copies retained, live TurnLedger supplies the facts shape.,
# ]
def reduce_claude(path, *, subagent=False, children=None, native_uuid=None):
    """Reduce one JSONL transcript; TurnLedger is used only for its existing display facts."""
    reduced, children = Reduced(), children or {}
    stat = Path(path).stat()
    ledger = TurnLedger(native_uuid if not subagent else None)
    agent = Path(path).stem.removeprefix('agent-') if subagent else None
    pending, pending_id, last_uuid = [], None, None
    results, calls = {}, {}

    def flush():
        nonlocal pending, pending_id
        if not pending:
            return
        ordered = sorted(pending, key=lambda r: r['apiBlockIndex']) if all(
            type(r.get('apiBlockIndex')) is int for r in pending) else pending
        first, text, tools = pending[0], [], []
        for record in ordered:
            message = record.get('message') if isinstance(record.get('message'), dict) else {}
            content = message.get('content') or []
            if isinstance(content, str):
                content = [{'type': 'text', 'text': content}]
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'text' and isinstance(block.get('text'), str):
                    text.append(block['text'])
                elif block.get('type') == 'tool_use':
                    ident = block.get('id')
                    entry = tool_entry(str(block.get('name') or '?'), results.get(ident, 'unknown'),
                                       block.get('input'), children.get(ident))
                    tools.append(entry)
                    if isinstance(ident, str):
                        calls.setdefault(ident, []).append(entry)
        if any(text) or tools:
            value = turn('assistant', stamp_ms(first.get('timestamp')), '\n'.join(text))
            if tools:
                value['tools'] = tools
            model = (first.get('message') or {}).get('model')
            effort = next((r['effort'] for r in pending if isinstance(r.get('effort'), str)), None)
            if isinstance(model, str):
                value['model'] = model
            if effort is not None:
                value['effort'] = effort
            add_turn(reduced, value)
        pending, pending_id = [], None

    known_drops = {'attachment', 'queue-operation', 'last-prompt', 'atis-latch', 'cost-state', 'mode', 'system'}
    for line, record in transcript_records(path):
        ledger.observe(line, agent)
        if record is None:
            flush()
            reduced.notes['unparsable_lines'] += 1
            continue
        if isinstance(record.get('uuid'), str):
            last_uuid = record['uuid']
        stamp = stamp_ms(record.get('timestamp'))
        if stamp is not None:
            if reduced.created_at is None:
                reduced.created_at = stamp
            reduced.last_at = stamp
        if reduced.cwd is None and isinstance(record.get('cwd'), str):
            reduced.cwd = record['cwd']
        kind = record.get('type')
        if record.get('isMeta') is True or (not subagent and record.get('isSidechain') is True):
            flush()
            continue
        message = record.get('message') if isinstance(record.get('message'), dict) else {}
        ident = message.get('id')
        if kind == 'assistant':
            if pending and (ident is None or pending_id != ident):
                flush()
            pending_id = ident
            pending.append(record)
            continue
        flush()
        if kind == 'user':
            content, text = message.get('content'), []
            if isinstance(content, str):
                content = [{'type': 'text', 'text': content}]
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_result':
                    tool_id = block.get('tool_use_id')
                    status = 'error' if block.get('is_error') is True else 'ok'
                    if results.get(tool_id) == 'error':
                        status = 'error'
                    results[tool_id] = status
                    for entry in calls.get(tool_id, []):
                        entry['status'] = status
                elif block.get('type') == 'text' and isinstance(block.get('text'), str):
                    if not block['text'].strip().startswith('<system-reminder>'):
                        text.append(block['text'])
            if any(text):
                value = turn('user', stamp, '\n'.join(text))
                origin = record.get('origin') if isinstance(record.get('origin'), dict) else {}
                if record.get('turnOrigin') == 'task_notification' or origin.get('kind') == 'task-notification':
                    value['origin'] = 'task-notification'
                elif '[Request interrupted by user]' in value['text']:
                    value['origin'] = 'interrupt'
                add_turn(reduced, value)
        elif kind == 'cost-state':
            cost = record.get('totalCostUSD')
            reduced.cost = cost if type(cost) in (int, float) else None
            reduced.tokens = record.get('modelUsage')
        elif kind not in known_drops and not (isinstance(kind, str) and kind.startswith('api-request')):
            note_unknown(reduced, kind)
    flush()
    ledger.status = 'complete'
    ledger.unreadable = max(ledger.unreadable, reduced.notes['unparsable_lines'])
    reduced.facts, reduced.fork = ledger.summary(), ledger.fork_at is not None
    reduced.raw_bytes = stat.st_size
    reduced.witness = claude_transcript_witness(path, snapshot=(stat, last_uuid))
    return reduced


# @beacon[
#   id=opc-history@reduce_opencode,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The one archive OpenCode reducer over unfiltered API messages: prompts shell commands answers compact input previews child links model/effort costs and tokens, never reasoning or outputs. Raw-size accounting uses JSON escapes for lone UTF-16 output code units; canonical reduced-body UTF-8 is unchanged.,
# ]
def reduce_opencode(info, messages):
    reduced = Reduced(created_at=int((info.get('time') or {}).get('created') or 0),
                      last_at=max(int((info.get('time') or {}).get(k) or 0) for k in ('updated', 'idle')))
    costs, tokens = [], {'input': 0, 'output': 0, 'reasoning': 0, 'cache': {'read': 0, 'write': 0}}
    dropped = {'synthetic', 'system', 'skill', 'compaction', 'idle'}
    for message in messages:
        # JSON's wire form escapes lone UTF-16 code units in cropped tool output.
        # Those outputs are discarded below; do not require them to be UTF-8 text
        # merely to count source bytes. The kept body still uses strict §3.4 UTF-8.
        reduced.raw_bytes += len(compact(message).encode('utf-8', errors='backslashreplace'))
        if not isinstance(message, dict):
            reduced.notes['unparsable_lines'] += 1
            continue
        kind = oc_client.msg_type(message)
        stamp = oc_client.msg_created(message) or None
        if kind in ('user', 'shell'):
            text = message.get('command') if kind == 'shell' else oc_client.msg_text(message)
            if isinstance(text, str) and text:
                value = turn('user', stamp, text)
                if kind == 'shell':
                    value['origin'] = 'shell'
                add_turn(reduced, value)
        elif kind == 'assistant':
            text = oc_client.msg_text(message)
            tools = []
            for tool in oc_client.msg_tools(message):
                child = tool['metadata'].get('sessionID') if tool['name'] == 'subagent' else None
                child = 'opencode:' + child if isinstance(child, str) and SESSION_ID.fullmatch(child) else None
                tools.append(tool_entry(tool['name'], {'completed': 'ok', 'error': 'error'}.get(
                    tool['status'], 'unknown'), tool['input'], child))
            if text or tools:
                value = turn('assistant', stamp, text)
                if tools:
                    value['tools'] = tools
                model = message.get('model') if isinstance(message.get('model'), dict) else {}
                if isinstance(model.get('id'), str):
                    value['model'] = model['id']
                if isinstance(model.get('variant'), str):
                    value['effort'] = model['variant']
                add_turn(reduced, value)
            if type(message.get('cost')) in (int, float):
                costs.append(message['cost'])
            counts = oc_client.msg_tokens(message)
            for key in ('input', 'output', 'reasoning'):
                tokens[key] += counts[key]
            for key in ('read', 'write'):
                tokens['cache'][key] += counts['cache'][key]
        elif kind not in dropped and not kind.endswith('-switched'):
            note_unknown(reduced, kind)
    reduced.cost, reduced.tokens = sum(costs) if costs else None, tokens
    # Reuse the live facts serializer, rather than defining its public shape twice.
    ledger = TurnLedger()
    ledger.status, ledger.records = 'complete', len(reduced.turns)
    ledger.models = dict.fromkeys(t['model'] for t in reduced.turns if 'model' in t)
    ledger.levels = dict.fromkeys(t['effort'] for t in reduced.turns if 'effort' in t)
    stamps = [t['ts'] / 1000 for t in reduced.turns if t['ts'] is not None]
    ledger.first, ledger.last = (min(stamps), max(stamps)) if stamps else (None, None)
    ledger.unreadable = reduced.notes['unparsable_lines']
    reduced.facts = ledger.summary()
    reduced.witness = opencode_witness(info, messages)
    return reduced


@dataclass
class Transcript:
    key: str
    path: Path
    native_uuid: str
    parent_key: str | None = None
    labels: dict | None = None


@dataclass
class Metrics:
    host_calls: list = field(default_factory=list)
    host_seconds: float = 0.0
    opencode_gets: int = 0
    opencode_seconds: float = 0.0
    telemetry_seconds: float = 0.0
    telemetry_errors: list = field(default_factory=list)


class Sources:
    """Read-only source access, injectable in tests; all host calls are selected lists."""

    def __init__(self, projects=None, *, metrics=None):
        self.projects = Path(projects) if projects else Path.home() / '.claude/projects'
        self.metrics = metrics or Metrics()
        self._transcripts = None
        self._host = None

    def transcripts(self, *, refresh=False):
        if self._transcripts is None or refresh:
            import uuid
            found = {}
            for path in sorted(self.projects.glob('*/*.jsonl')):
                try:
                    native = str(uuid.UUID(path.stem))
                except ValueError:
                    continue
                key = 'claude:cc-' + native
                found[key] = Transcript(key, path, native)
            for path in sorted(self.projects.glob('*/*/subagents/agent-*.jsonl')):
                try:
                    native = str(uuid.UUID(path.parent.parent.name))
                except ValueError:
                    continue
                parent = 'claude:cc-' + native
                agent = path.stem.removeprefix('agent-')
                try:
                    labels = json.loads(path.with_suffix('.meta.json').read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    labels = {}
                labels = labels if isinstance(labels, dict) else {}
                labels = {**labels, 'parentUuid': native, 'agentId': agent,
                          'agentType': labels.get('agentType'), 'description': labels.get('description')}
                key = parent + '/agent-' + agent
                found[key] = Transcript(key, path, native, parent, labels)
            self._transcripts = found
        return self._transcripts

    def child_links(self):
        """All meta-named tool children, even a launch with no child transcript yet."""
        links = {}
        for parent in self.transcripts().values():
            if parent.parent_key:
                continue
            for path in sorted((parent.path.with_suffix('') / 'subagents').glob('agent-*.meta.json')):
                try:
                    labels = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                if isinstance(labels, dict) and isinstance(labels.get('toolUseId'), str):
                    links.setdefault(parent.key, {})[labels['toolUseId']] = (
                        parent.key + '/' + path.name.removesuffix('.meta.json'))
        return links

    def host_list(self, **selector):
        start = time.perf_counter()
        self.metrics.host_calls.append(selector)
        try:
            if self._host is None:
                from oc_backend import HostConnection, host_socket
                # connect() additionally calls health. This reader's contract is LIST ONLY.
                self._host = HostConnection(host_socket())
            rows = self._host.call('list', summary=True, **selector)
            if not isinstance(rows, list):
                raise ValueError('host list did not return rows')
            return rows
        finally:
            self.metrics.host_seconds += time.perf_counter() - start

    def _oc(self, operation, *args):
        # Count actual GET pages, while keeping oc_client's one paging implementation.
        original = oc_client.oc_raw

        def measured(method, *a, **kw):
            assert method == 'GET'
            start = time.perf_counter()
            self.metrics.opencode_gets += 1
            try:
                return original(method, *a, **kw)
            finally:
                self.metrics.opencode_seconds += time.perf_counter() - start

        oc_client.oc_raw = measured
        try:
            return operation(*args)
        finally:
            oc_client.oc_raw = original

    def opencode_sessions(self):
        return self._oc(oc_client.oc_sessions)

    def opencode_session(self, sid):
        return self._oc(oc_client.oc_session, sid)

    def opencode_messages(self, sid):
        return self._oc(oc_client.oc_messages, sid)

    def opencode_status(self):
        return self._oc(oc_client.oc_status)

    def live_witness(self, row):
        if row['backend'] == 'opencode':
            info = self.opencode_session(row['id'])  # clocks BEFORE message read
            return opencode_witness(info, self.opencode_messages(row['id']))
        kind = json.loads(row['witness']).get('kind')
        # A no-transcript launch may have produced its first file since this
        # operation's census. V2 must see it, not reuse an earlier absence.
        transcript = self.transcripts(refresh=kind == 'claude-record').get(row['key'])
        if transcript:
            return claude_transcript_witness(transcript.path)
        if kind == 'claude-transcript':
            raise FileNotFoundError(row['key'])
        records = self.host_list(execution_ids=[row['id']])
        record = next((r for r in records if r.get('execution_id') == row['id']), None)
        if record is None:
            raise FileNotFoundError(row['key'])
        return claude_record_witness(record)


class Telemetry:
    """The pane's own answers, with one lazy Inventory and per-run unit memo (no TTL)."""

    def __init__(self, metrics):
        self.metrics, self.lib, self.inv = metrics, None, None
        self.programs, self.memo = {}, {}

    def _read(self, fn, name):
        start = time.perf_counter()
        try:
            if self.lib is None:
                import oc_telemetry
                self.lib = oc_telemetry
                self.inv = self.lib.Inventory()
            return fn()
        except (Exception, SystemExit) as error:  # telemetry is optional, never a session failure
            self.metrics.telemetry_errors.append({'key': name, 'error': str(error)})
            return None
        finally:
            self.metrics.telemetry_seconds += time.perf_counter() - start

    def session(self, sid):
        def read():
            entry = self.lib.pane_session(self.inv, sid)
            return self.lib.mark_live(entry, now_ms()) if entry is not None else None
        return self._read(read, sid)

    def program(self, core):
        if core not in self.programs:
            self.programs[core] = self._read(
                lambda: self.lib.pane_program(self.inv, f'[ap:{core}]', True, self.memo), core)
        return self.programs[core]

    def close(self):
        if self.inv is not None and self.inv.con is not None:
            self.inv.con.close()


def program_for_opencode(info, by_id):
    row = opencode_row(info, by_id)
    if row is not None:
        return row.get('program'), row
    # Parked warm forks are hidden by the row builder, not by the archive (T9).
    md = info.get('metadata') if isinstance(info.get('metadata'), dict) else {}
    title = SEAT_TITLE.match(info.get('title') or '')
    return _core(md.get('program')) or (title.group(1) if title else None), None


def unit_key(key, core, parents, cores):
    current, seen = key, set()
    while current not in seen:
        seen.add(current)
        core = core or cores.get(current)
        parent = parents.get(current)
        if not parent:
            break
        current = parent
    return 'program:' + core if core else 'session:' + current


def _upsert(con, table, columns, row):
    key, *rest = columns
    con.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                f"ON CONFLICT({key}) DO UPDATE SET " + ','.join(f'{n}=excluded.{n}' for n in rest),
                tuple(row.get(n) for n in columns))


def refresh_unit(con, key, ledger, telemetry, at):
    members = con.execute('SELECT created_at,last_at,cost_usd,backend FROM sessions WHERE unit_key=?', (key,)).fetchall()
    if not members:
        con.execute('DELETE FROM units WHERE unit_key=?', (key,))
        return
    core = key.removeprefix('program:') if key.startswith('program:') else None
    seal = ledger.get(core, {})
    costs = [m['cost_usd'] for m in members if m['cost_usd'] is not None]
    value = {'unit_key': key, 'kind': 'program' if core else 'session', 'program_core': core,
             'first_at': min(m['created_at'] for m in members), 'last_at': max(m['last_at'] for m in members),
             'sessions': len(members), 'cost_usd': sum(costs) if costs else None,
             'backends_json': compact(sorted({m['backend'] for m in members})),
             'sealed_at': seal.get('sealed_at'), 'seal': seal.get('seal'),
             'telemetry_json': compact(telemetry) if telemetry is not None else None, 'archived_at': at}
    _upsert(con, 'units', UNIT_COLUMNS, value)


def _witness_kind(value):
    try:
        witness = json.loads(value)
        return witness.get('kind') if isinstance(witness, dict) else None
    except (ValueError, TypeError, RecursionError):
        return None


def _older_capture(new, old):
    """True when witness new is an earlier reading of the same source than witness old (L-35).

    A transcript is append-only: the same inode with fewer bytes, or an older mtime_ns,
    is an earlier reading. OpenCode's clocks only advance: an older time_updated is
    earlier; at the same time_updated, fewer messages or an older idle marker is. A newer
    time_updated with fewer messages is a revert, a later reading. Other kinds never compare.
    """
    try:
        new, old = json.loads(new), json.loads(old)
    except (ValueError, TypeError, RecursionError):
        return False
    if not isinstance(new, dict) or not isinstance(old, dict) or new.get('kind') != old.get('kind'):
        return False

    def clocks(witness, *names):
        return [witness.get(n) if type(witness.get(n)) in (int, float) else -1 for n in names]
    if new['kind'] == 'claude-transcript':
        return clocks(new, 'mtime_ns') < clocks(old, 'mtime_ns') or (
            new.get('ino') == old.get('ino') and clocks(new, 'size') < clocks(old, 'size'))
    if new['kind'] == 'opencode-session':
        names = ('time_updated', 'messages', 'time_idle')
        return clocks(new, *names) < clocks(old, *names)
    return False


# @beacon[
#   id=opc-history@write_session,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The one session writer: row, body and unit in one BEGIN IMMEDIATE transaction that rereads the stored row first and writes nothing when it is trimmed (pruned_at set), when a record-only capture would replace a transcript one (L-33), when an overlapping run's capture is older than the stored one of the same source (L-35), or when the witness is equal.,
# ]
def write_session(con, row, reduced, *, ledger, unit_telemetry):
    body = compact(reduced.turns).encode('utf-8')
    compressed = zlib.compress(body, 6)
    row.update(first_at=next((t['ts'] for t in reduced.turns if t['ts'] is not None), None),
               cost_usd=reduced.cost, tokens_json=compact(reduced.tokens) if reduced.tokens is not None else None,
               models_json=compact(list(dict.fromkeys(t['model'] for t in reduced.turns if 'model' in t))),
               levels_json=compact(list(dict.fromkeys(t['effort'] for t in reduced.turns if 'effort' in t))),
               turns=len(reduced.turns), turn_facts_json=compact(reduced.facts), notes_json=compact(reduced.notes),
               raw_bytes=reduced.raw_bytes, witness=reduced.witness, body_sha256=hashlib.sha256(body).hexdigest(),
               body_bytes=len(compressed), archive_version=ARCHIVE_VERSION)
    con.execute('BEGIN IMMEDIATE')
    try:
        old = con.execute('SELECT * FROM sessions WHERE key=?', (row['key'],)).fetchone()
        # A trimmed conversation's stored copy is the only one left: nothing
        # replaces it, not even a transcript re-created at its old path.
        if old and old['pruned_at'] is not None:
            con.rollback()
            return False
        # A concurrent transcript capture may have landed after this run's
        # census. A record-only launch must never replace that conversation.
        if old and _witness_kind(row['witness']) == 'claude-record' and _witness_kind(old['witness']) != 'claude-record':
            con.rollback()
            return False
        # An overlapping run may have landed a newer capture (and a prune may
        # have verified it) while this run held an older one.
        if old and _older_capture(row['witness'], old['witness']):
            con.rollback()
            return False
        # Recheck under SQLite's transaction: overlapping runs may already have landed it.
        if old and old['witness'] == row['witness'] and old['archive_version'] == ARCHIVE_VERSION:
            con.rollback()
            return False
        unit = con.execute('SELECT max(archived_at) FROM units WHERE unit_key IN (?,?)',
                           (row['unit_key'], old['unit_key'] if old else row['unit_key'])).fetchone()
        row['archived_at'] = max(now_ms(), old['archived_at'] + 1 if old else 0, (unit[0] or 0) + 1)
        row['pruned_at'] = None
        if old and period_for(old['created_at']) != period_for(row['created_at']):
            _remember_export_periods(con, [period_for(value, grain)
                                     for value in (old['created_at'], row['created_at'])
                                     for grain in ('month', 'year')])
        _upsert(con, 'sessions', SESSION_COLUMNS, row)
        _upsert(con, 'bodies', ('key', 'codec', 'body'), {'key': row['key'], 'codec': 'zlib-json', 'body': compressed})
        refresh_unit(con, row['unit_key'], ledger, unit_telemetry, row['archived_at'])
        if old and old['unit_key'] != row['unit_key']:
            previous = con.execute('SELECT telemetry_json FROM units WHERE unit_key=?', (old['unit_key'],)).fetchone()
            refresh_unit(con, old['unit_key'], ledger, json.loads(previous[0]) if previous and previous[0] else None,
                         row['archived_at'])
        con.commit()
        return True
    except BaseException:
        con.rollback()
        raise


def cheap_match(old, *, path=None, info=None):
    if not old or old['archive_version'] != ARCHIVE_VERSION:
        return False
    witness = json.loads(old['witness'])
    if path is not None:
        stat = Path(path).stat()
        return witness.get('kind') == 'claude-transcript' and all(
            witness.get(k) == v for k, v in (('ino', stat.st_ino), ('size', stat.st_size), ('mtime_ns', stat.st_mtime_ns)))
    times = info.get('time') or {}
    return witness.get('kind') == 'opencode-session' and witness.get('time_updated') == times.get('updated') and (
        witness.get('time_idle') == times.get('idle'))


@dataclass
class Selection:
    program: str | None = None
    idle: float | None = None
    sessions: set | None = None
    all: bool = False
    dry_run: bool = False


# @beacon[
#   id=opc-history@session_times,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The last-resort clock rule: when both record and host clocks are absent use the source file's mtime_ns floored to epoch milliseconds for created_at and last_at; observed source clocks retain their existing precedence and no unknown-date sentinel is invented.,
# ]
def session_times(created_at, last_at, source_file):
    """Keep source clocks; only a wholly clockless source takes its file mtime."""
    if created_at is None and last_at is None:
        modified_at = int(os.stat(source_file).st_mtime_ns // 1_000_000)
        return modified_at, modified_at
    return (created_at if created_at is not None else 0,
            last_at if last_at is not None else 0)


# @beacon[
#   id=opc-history@archive,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=Archive selected conversations through host selected LISTs and OpenCode GETs; source-census failures stay explicit for seal backfill. Record-only launches, older overlapping captures and anything over a trimmed row cannot replace stored conversations (write_session rechecks inside its transaction); a transcript the host has forgotten is never re-captured. Preserve each transcript's own clocks (subagents never borrow the host's); wholly clockless files take mtime. Equal witnesses read no histories/telemetry; session/body/unit changes and empty-old-unit removal commit together.,
# ]
def archive(con, selection, *, sources=None, telemetry=None):
    sources = sources or Sources()
    metrics, start = sources.metrics, time.perf_counter()
    owned_telemetry = telemetry is None
    telemetry = telemetry or Telemetry(metrics)
    report = {'archived': 0, 'unchanged': 0, 'failed': 0, 'skipped': 0, 'written': [], 'failures': [],
              'source_counts': {'claude': 0, 'subagents': 0, 'opencode': 0}, 'source_failures': [], 'forks': []}
    old = {r['key']: dict(r) for r in con.execute('SELECT * FROM sessions')}
    transcripts, records, unavailable = sources.transcripts(), {}, set()
    wanted = selection.sessions
    main_ids = sorted({t.parent_key.removeprefix('claude:') if t.parent_key else t.key.removeprefix('claude:')
                       for t in transcripts.values() if not wanted or t.key in wanted})

    def failed(key, error):
        report['failed'] += 1
        report['failures'].append({'key': key, 'reason': str(error) or type(error).__name__})

    if selection.program:
        try:
            records = {r['execution_id']: r for r in sources.host_list(programs=[selection.program])
                       if list_program(r) == selection.program}
        except Exception as error:
            failed('claude:program:' + selection.program, error)
            report['source_failures'].append({'source': 'claude', 'program': selection.program, 'reason': str(error)})
            unavailable.update(main_ids)
    else:
        for offset in range(0, len(main_ids), 200):
            ids = main_ids[offset:offset + 200]
            try:
                records.update({r['execution_id']: r for r in sources.host_list(execution_ids=ids)
                                if r.get('execution_id') in ids})
            except Exception as error:
                unavailable.update(ids)
                report['source_failures'].append({'source': 'claude', 'execution_ids': ids, 'reason': str(error)})
                for t in transcripts.values():
                    if (t.parent_key or t.key).removeprefix('claude:') in ids and (not wanted or t.key in wanted):
                        failed(t.key, error)
    try:
        infos = sources.opencode_sessions()
    except Exception as error:
        infos = []
        failed('opencode:*', error)
        report['source_failures'].append({'source': 'opencode', 'reason': str(error)})
    by_id = {s['id']: s for s in infos}
    attributed = {sid: program_for_opencode(s, by_id) for sid, s in by_id.items()}
    parents = {t.key: t.parent_key for t in transcripts.values()}
    cores = {t.key: list_program(records.get('cc-' + t.native_uuid, {})) for t in transcripts.values()}
    for sid, info in by_id.items():
        key = 'opencode:' + sid
        parents[key] = 'opencode:' + info['parentID'] if info.get('parentID') else None
        cores[key] = attributed[sid][0]
    ledger = oc_seal_ledger.read()
    cutoff = now_ms() - selection.idle * 3600000 if selection.idle is not None else None
    from claude_launch import SessionNames
    names = SessionNames().all()  # read only; never set/forget
    from claude_live import host_row_fields
    child_links = sources.child_links()

    def save(row, reduced):
        if cutoff is not None and row['last_at'] > cutoff:
            report['skipped'] += 1
            return
        previous = old.get(row['key'])
        if previous and previous['witness'] == reduced.witness and previous['archive_version'] == ARCHIVE_VERSION:
            report['unchanged'] += 1
            return
        if selection.dry_run:
            report['written'].append(row['key'])
            report['archived'] += 1
            return
        entry = telemetry.session(row['id'])
        row['telemetry_json'] = compact(entry) if entry is not None else None
        unit_telemetry = telemetry.program(row['program_core']) if row['program_core'] else None
        if write_session(con, row, reduced, ledger=ledger, unit_telemetry=unit_telemetry):
            report['archived'] += 1
            report['written'].append(row['key'])
        else:
            report['unchanged'] += 1

    try:
        for t in transcripts.values():
            if wanted and t.key not in wanted:
                continue
            eid, key = 'cc-' + t.native_uuid, t.key
            core = cores[key]
            if selection.program and core != selection.program:
                continue
            report['source_counts']['claude'] += 1
            report['source_counts']['subagents'] += int(t.parent_key is not None)
            if eid in unavailable:
                continue
            try:
                if key in old and (records.get(eid) or {}).get('archived'):
                    # Forgotten by a trim (completed or still pending): a file at the
                    # old path is a re-created fragment, never a newer capture (L-35).
                    report['unchanged'] += 1
                    report.setdefault('source_gone', []).append(key)
                    continue
                if cheap_match(old.get(key), path=t.path):
                    report['unchanged'] += 1
                    continue
                reduced = reduce_claude(t.path, subagent=bool(t.parent_key), children=child_links.get(key),
                                        native_uuid=t.native_uuid)
                record = records.get(eid)
                fields = host_row_fields(record) if record else {}
                if t.parent_key:
                    source, role, title = t.labels, 'subagent', t.labels.get('description') or t.path.stem
                elif record:
                    source, role, title = record, record.get('role'), fields.get('title')
                else:
                    source, role, title = {'native': True, 'cwd': reduced.cwd}, None, t.native_uuid
                clock_record = (record or {}) if not t.parent_key else {}
                created_at, last_at = session_times(
                    reduced.created_at if reduced.created_at is not None else stamp_ms(clock_record.get('first_launched_at')),
                    reduced.last_at if reduced.last_at is not None else stamp_ms(list_recency(clock_record)),
                    t.path)
                row = {'key': key, 'unit_key': unit_key(key, core, parents, cores), 'backend': 'claude',
                       'id': key.removeprefix('claude:'), 'native_uuid': t.native_uuid, 'program_core': core,
                       'parent_key': t.parent_key, 'kind': 'subagent' if t.parent_key else 'session', 'role': role,
                       'title': title, 'chosen_name': names.get(t.native_uuid) if not t.parent_key else None,
                       'socket': fields.get('socket') if not t.parent_key else None,
                       'manager_session': fields.get('managerSession'), 'cwd': reduced.cwd or fields.get('cwd'),
                       'created_at': created_at, 'last_at': last_at,
                       'close_reason': (record or {}).get('close_reason'), 'outcome': (record or {}).get('outcome'),
                       'source_json': compact(source)}
                if reduced.fork:
                    report['forks'].append(key)
                save(row, reduced)
            except Exception as error:
                failed(key, error)
        if selection.program:
            for eid, record in records.items():
                key = 'claude:' + eid
                if key in transcripts:
                    continue
                report['source_counts']['claude'] += 1
                previous = old.get(key)
                if previous and _witness_kind(previous['witness']) != 'claude-record':
                    # A missing transcript is not evidence of a never-written launch.
                    report['unchanged'] += 1
                    report.setdefault('source_gone', []).append(key)
                    continue
                try:
                    reduced = Reduced(created_at=int((record.get('first_launched_at') or 0) * 1000),
                                      last_at=int((list_recency(record) or 0) * 1000),
                                      witness=claude_record_witness(record))
                    ledger_facts = TurnLedger()
                    ledger_facts.status = 'complete'
                    reduced.facts = ledger_facts.summary()
                    fields = host_row_fields(record)
                    save({'key': key, 'unit_key': 'program:' + selection.program, 'backend': 'claude', 'id': eid,
                          'native_uuid': record.get('native_uuid') or eid.removeprefix('cc-'),
                          'program_core': selection.program, 'kind': 'session', 'role': record.get('role'),
                          'title': fields.get('title'), 'chosen_name': names.get(record.get('native_uuid')),
                          'socket': fields.get('socket'), 'manager_session': fields.get('managerSession'),
                          'cwd': fields.get('cwd'), 'created_at': reduced.created_at, 'last_at': reduced.last_at,
                          'close_reason': record.get('close_reason'), 'outcome': record.get('outcome'),
                          'source_json': compact(record)}, reduced)
                except Exception as error:
                    failed(key, error)
        for sid, info in by_id.items():
            key, (core, fields) = 'opencode:' + sid, attributed[sid]
            if wanted and key not in wanted or selection.program and core != selection.program:
                continue
            report['source_counts']['opencode'] += 1
            try:
                times = info.get('time') or {}
                last = max(int(times.get(k) or 0) for k in ('updated', 'idle'))
                if cutoff is not None and last > cutoff:
                    report['skipped'] += 1
                    continue
                if cheap_match(old.get(key), info=info):
                    report['unchanged'] += 1
                    continue
                # The list is the session-info snapshot BEFORE its messages, as §3.5 requires.
                reduced = reduce_opencode(info, sources.opencode_messages(sid))
                fields = fields or {}
                save({'key': key, 'unit_key': unit_key(key, core, parents, cores), 'backend': 'opencode', 'id': sid,
                      'program_core': core, 'parent_key': parents[key],
                      'kind': 'subagent' if info.get('parentID') else 'session', 'role': fields.get('kind'),
                      'title': info.get('title'), 'socket': fields.get('socket'),
                      'manager_session': fields.get('managerSession'), 'cwd': fields.get('directory'),
                      'created_at': reduced.created_at, 'last_at': reduced.last_at,
                      'close_reason': info.get('close_reason'), 'outcome': info.get('outcome'),
                      'source_json': compact(info)}, reduced)
            except Exception as error:
                failed(key, error)
        if wanted:
            found = set(transcripts) | {'opencode:' + sid for sid in by_id}
            for key in sorted(wanted - found):
                failed(key, 'source-gone')
    finally:
        if owned_telemetry:
            telemetry.close()
    report['duration_seconds'] = time.perf_counter() - start
    programs = {}
    for record in records.values():
        core = list_program(record)
        if core:
            programs[core] = max(programs.get(core, 0), int((list_recency(record) or 0) * 1000))
    for sid, info in by_id.items():
        core = attributed[sid][0]
        if core:
            programs[core] = max(programs.get(core, 0), *(int((info.get('time') or {}).get(k) or 0)
                                                        for k in ('updated', 'idle')))
    report['programs'] = programs
    report['metrics'] = vars(metrics)
    return report


def history_log(message, settings=None):
    settings = settings or oc_history_settings.load()
    path = Path(settings.archive_path).parent / 'history.log'
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'a', encoding='utf-8') as out:
            out.write(datetime.now(timezone.utc).isoformat() + ' history: ' + message + '\n')
    except OSError:
        pass


def source_programs(sources):
    """Metadata-only census for an explicit seals --backfill (archive already has its census)."""
    programs, failures = {}, []
    ids = sorted({'cc-' + t.native_uuid for t in sources.transcripts().values()})
    for offset in range(0, len(ids), 200):
        batch = ids[offset:offset + 200]
        try:
            for record in sources.host_list(execution_ids=batch):
                core = list_program(record)
                if core:
                    programs[core] = max(programs.get(core, 0), int((list_recency(record) or 0) * 1000))
        except Exception as error:
            failures.append({'source': 'claude', 'reason': str(error)})
    try:
        by_id = {s['id']: s for s in sources.opencode_sessions()}
        for info in by_id.values():
            core, _row = program_for_opencode(info, by_id)
            if core:
                programs[core] = max(programs.get(core, 0), *(int((info.get('time') or {}).get(k) or 0)
                                                            for k in ('updated', 'idle')))
    except Exception as error:
        failures.append({'source': 'opencode', 'reason': str(error)})
    return programs, failures


# @beacon[
#   id=opc-history@backfill_seals,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The one-time seals --backfill path: discovered/archive cores without a ledger line, root registry then oc_sessions.seal_created_ms; reopened roots stay unsealed and zero creation time uses logged last activity. Source-census failures from the archive run or this census keep the completion marker pending; complete reads alone close it. The existing ledger module writes the index; Workflowy is read only.,
# ]
def backfill_seals(con, *, programs=None, sources=None, workflowy_factory=None, settings=None, source_failures=None):
    settings, sources = settings or oc_history_settings.load(), sources or Sources()
    start, discovered, failures = time.perf_counter(), {}, list(source_failures or [])
    if programs is None:
        discovered, census_failures = source_programs(sources)
        failures.extend(census_failures)
    else:
        discovered.update(programs)
    for row in con.execute('SELECT program_core,max(last_at) FROM sessions WHERE program_core IS NOT NULL GROUP BY program_core'):
        discovered[row[0]] = max(discovered.get(row[0], 0), row[1])
    ledger = oc_seal_ledger.read()
    cores = sorted(set(discovered) - set(ledger))
    report = {'cores': len(cores), 'sealed': 0, 'workflowy_reads': 0, 'workflowy_seconds': 0.0,
              'fallbacks': [], 'failures': failures}
    if cores:
        from oc_common import Workflowy, registry_from_note
        from oc_sessions import READ_CONCURRENCY, seal_created_ms

        async def work():
            sem = asyncio.Semaphore(READ_CONCURRENCY)
            async with (workflowy_factory or Workflowy)() as wf:
                async def one(core):
                    try:
                        async with sem:
                            report['workflowy_reads'] += 1
                            before = time.perf_counter()
                            try:
                                _name, note = await wf.read_note(f'[ap:{core}]')
                            finally:
                                report['workflowy_seconds'] += time.perf_counter() - before
                        iface, _registry = registry_from_note(note)
                        if not iface or not iface.get('seal') or ('state' in iface and iface['state'] != 'sealed'):
                            return
                        seal = str(iface['seal'])
                        async with sem:
                            report['workflowy_reads'] += 1
                            before = time.perf_counter()
                            try:
                                sealed_at = await seal_created_ms(wf, core, seal)
                            finally:
                                report['workflowy_seconds'] += time.perf_counter() - before
                        if not sealed_at:
                            sealed_at = discovered[core]
                            report['fallbacks'].append(core)
                            history_log(f'seal backfill {core}: creation time unavailable; using last activity {sealed_at}', settings)
                        oc_seal_ledger.append(core, sealed_at, seal, 'backfill')
                        report['sealed'] += 1
                    except Exception as error:
                        report['failures'].append({'core': core, 'reason': str(error)})
                await asyncio.gather(*(one(core) for core in cores))
        try:
            asyncio.run(work())
        except Exception as error:
            report['failures'].append({'source': 'workflowy', 'reason': str(error)})
    if not report['failures']:
        meta_set(con, 'seals_backfilled_at', now_ms())
    report['duration_seconds'] = time.perf_counter() - start
    history_log('seal backfill ' + compact(report), settings)
    return report


def refresh_seals(con):
    """Ledger-only unit metadata changes are export changes, never session rewrites."""
    ledger, changed = oc_seal_ledger.read(), []
    con.execute('BEGIN IMMEDIATE')
    try:
        for row in con.execute('SELECT unit_key,program_core,sealed_at,seal,archived_at FROM units WHERE kind=\'program\''):
            entry = ledger.get(row['program_core'], {})
            if (row['sealed_at'], row['seal']) == (entry.get('sealed_at'), entry.get('seal')):
                continue
            con.execute('UPDATE units SET sealed_at=?,seal=?,archived_at=? WHERE unit_key=?',
                        (entry.get('sealed_at'), entry.get('seal'), max(now_ms(), row['archived_at'] + 1), row['unit_key']))
            changed.append(row['unit_key'])
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return changed


def finish_archive_run(con, settings, result, *, sources=None, backfill=False, workflowy_factory=None):
    if backfill and not meta_get(con, 'seals_backfilled_at'):
        result['seals'] = backfill_seals(con, programs=result['programs'], sources=sources,
                                        workflowy_factory=workflowy_factory, settings=settings,
                                        source_failures=result.get('source_failures', []))
    result['seal_units_updated'] = refresh_seals(con)
    result['exports'] = export_archive(con, settings)
    return result


# @beacon[
#   id=opc-history@sweep,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=Daily child-process sweep: archive changed idle sessions, complete the one-time seal census, refresh ledger metadata and publish backups; only Dan's trim_enabled switch then calls the one prune executor. With the switch off no trim plan or extra host/Workflowy call runs; persist last_sweep counts and duration.,
# ]
def sweep(con, settings=None, *, sources=None, telemetry=None, workflowy_factory=None, host=None, pins=None):
    settings, sources = settings or oc_history_settings.load(), sources or Sources()
    start = time.perf_counter()
    result = archive(con, Selection(idle=settings.sweep_idle_hours), sources=sources, telemetry=telemetry)
    finish_archive_run(con, settings, result, sources=sources, backfill=True, workflowy_factory=workflowy_factory)
    if settings.trim_enabled:
        result['trim'] = prune(con, settings, sources=sources, host=host, pins=pins,
                               workflowy_factory=workflowy_factory, print_report=False)
    result['duration_seconds'] = time.perf_counter() - start
    con.execute('BEGIN IMMEDIATE')
    try:
        meta_set(con, 'last_sweep_at', now_ms())
        meta_set(con, 'last_sweep', compact({k: result[k] for k in ('archived', 'unchanged', 'failed', 'duration_seconds')}))
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return result


def period_for(created_at, grain='month'):
    return datetime.fromtimestamp(created_at / 1000, timezone.utc).strftime('%Y' if grain == 'year' else '%Y-%m')


def export_path(settings, created_at):
    return Path(settings.export_dir) / f'session-history-{period_for(created_at, settings.export_grain)}.jsonl.gz'


def export_header(path):
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as source:
            header = json.loads(source.readline())
        if header.get('type') == 'header' and type(header.get('through')) is int:
            return header
    except (OSError, ValueError, EOFError, UnicodeError, AttributeError):
        pass
    return None


def _remember_export_periods(con, labels):
    """Remember departed periods transactionally, including ones now containing no rows."""
    known = set(json.loads(meta_get(con, 'export_periods') or '[]'))
    updated = known | set(labels)
    if updated != known:
        meta_set(con, 'export_periods', compact(sorted(updated)))


# @beacon[
#   id=opc-history@export_archive,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=One atomic month/year export writer from one read transaction: missing or stale whole-period images include source-clock relocations and remembered periods now empty. Header counts detect departures as well as through advances; gzip to same-folder temp then fsync/replace heals interrupted and overlapping publications.,
# ]
def export_archive(con, settings=None, *, month=None):
    """Publish complete periods, including seals and clock moves out of an old period."""
    settings = settings or oc_history_settings.load()
    folder, exported, failures = Path(settings.export_dir), [], []
    folder.mkdir(parents=True, exist_ok=True)
    grain_sql = '%Y' if settings.export_grain == 'year' else '%Y-%m'
    period_sql = f"strftime('{grain_sql}',s.created_at / 1000.0,'unixepoch')"
    con.execute('BEGIN')
    try:
        periods = {row['period']: dict(row) for row in con.execute(f"""SELECT {period_sql} AS period, count(*) AS sessions,
            count(DISTINCT s.unit_key) AS units,
            max(max(s.archived_at,coalesce(s.pruned_at,0),coalesce(u.archived_at,0))) AS through
            FROM sessions s LEFT JOIN units u ON u.unit_key=s.unit_key GROUP BY period ORDER BY period""")}
        for label in json.loads(meta_get(con, 'export_periods') or '[]'):
            if len(label) == (4 if settings.export_grain == 'year' else 7):
                periods.setdefault(label, {'period': label, 'sessions': 0, 'units': 0, 'through': 0})
        for period in sorted(periods.values(), key=lambda row: row['period']):
            label = period['period']
            if month and label != (month[:4] if settings.export_grain == 'year' else month):
                continue
            path = folder / f'session-history-{label}.jsonl.gz'
            old = export_header(path)
            if old and old['through'] >= period['through'] and (
                    old.get('sessions'), old.get('units')) == (period['sessions'], period['units']):
                continue
            try:
                header = {'type': 'header', 'archive': {'format': 'session-history-archive', 'format_version': 1,
                          'name': f'Session history {label}',
                          'exported_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')},
                          'schema_version': SCHEMA_VERSION, 'archive_version': ARCHIVE_VERSION,
                          'month': label, 'sessions': period['sessions'], 'units': period['units'],
                          'through': period['through']}
                fd, temp = tempfile.mkstemp(prefix=f'.session-history-{label}-', suffix='.tmp', dir=folder)
                with os.fdopen(fd, 'wb') as raw:
                    with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as output:
                        def emit(value):
                            output.write((compact(value) + '\n').encode('utf-8'))
                        emit(header)
                        for unit in con.execute(f"""SELECT u.* FROM units u WHERE u.unit_key IN
                            (SELECT s.unit_key FROM sessions s WHERE {period_sql}=?) ORDER BY u.unit_key""", (label,)):
                            emit({'type': 'unit', **dict(unit)})
                        for session in con.execute(f"""SELECT s.*,b.body FROM sessions s JOIN bodies b ON b.key=s.key
                            WHERE {period_sql}=? ORDER BY s.key""", (label,)):
                            row = dict(session)
                            turns = json.loads(zlib.decompress(row.pop('body')))
                            # L-07: the decoded body is called turns; its row count is recovered by len on rebuild.
                            row['turns'] = turns
                            emit({'type': 'session', **row})
                    raw.flush()
                    os.fsync(raw.fileno())
                os.replace(temp, path)
                exported.append({'period': label, 'path': str(path), 'through': period['through'],
                                 'sessions': period['sessions'], 'units': period['units'], 'bytes': path.stat().st_size})
            except Exception as error:
                # The old image stays in place; no retry and no source/session rollback.
                failures.append({'period': label, 'reason': str(error)})
        con.commit()
    except BaseException:
        con.rollback()
        raise
    if exported:
        meta_set(con, 'last_export_at', now_ms())
    return {'exported': exported, 'failures': failures}


class ExportIndex:
    """Per-run V3 index; an atomic replacement changes its inode/mtime/size signature.

    The index retains only keys, witnesses and hashes, never decoded conversations.
    It has no TTL or cap. verified still checks the current file signature each time.
    """

    def __init__(self, settings):
        self.settings, self.images = settings, {}

    def session(self, row):
        path = export_path(self.settings, row['created_at'])
        try:
            stat = path.stat()
        except FileNotFoundError:
            return 'export-missing', None
        except OSError:
            return 'export-stale', None
        signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        hit = self.images.get(path)
        if hit is None or hit[0] != signature:
            entries = {}
            try:
                with gzip.open(path, 'rt', encoding='utf-8') as source:
                    for line in source:
                        value = json.loads(line)
                        if value.get('type') == 'session':
                            entries[value['key']] = (value.get('witness'), value.get('body_sha256'))
            except (OSError, ValueError, KeyError, EOFError, UnicodeError, AttributeError):
                entries = {}
            self.images[path] = signature, entries
        return 'export-stale', self.images[path][1].get(row['key'])


def valid_turns(turns):
    """V1's §3.4 shape, not a second reducer: dense ordered turns and tool entries."""
    if not isinstance(turns, list):
        return False
    for seq, value in enumerate(turns):
        if not isinstance(value, dict) or list(value)[:4] != ['seq', 'role', 'ts', 'text']:
            return False
        if type(value['seq']) is not int or value['seq'] != seq or value['role'] not in ('user', 'assistant'):
            return False
        if value['ts'] is not None and type(value['ts']) is not int or not isinstance(value['text'], str):
            return False
        optional = ('tools', 'model', 'effort') if value['role'] == 'assistant' else ('origin',)
        if list(value)[4:] != [key for key in optional if key in value]:
            return False
        if 'origin' in value and value['origin'] not in ('task-notification', 'interrupt', 'shell'):
            return False
        if any(key in value and not isinstance(value[key], str) for key in ('model', 'effort')):
            return False
        if 'tools' in value:
            if not isinstance(value['tools'], list) or not value['tools']:
                return False
            for tool in value['tools']:
                if not isinstance(tool, dict) or list(tool) != ['name', 'status', 'preview', 'child']:
                    return False
                if not isinstance(tool['name'], str) or tool['status'] not in ('ok', 'error', 'unknown'):
                    return False
                if not isinstance(tool['preview'], str) or len(tool['preview']) > 120:
                    return False
                if tool['child'] is not None and not isinstance(tool['child'], str):
                    return False
    return True


# @beacon[
#   id=opc-history@verified,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The ONE trim gate: V1 row/body/known version/codec/hash/turn shape/count and parse completeness (malformed or overdeep body JSON is corrupt), V2 freshly recomputed WHOLE source witness, then V3 current-grain backup witness/hash. Fixed §3.9 reasons only, with no source error producing ok.,
# ]
def verified(key, *, con=None, settings=None, sources=None, exports=None):
    """The §3.9 (ok, reason) gate: validate the whole body and recompute the whole live witness."""
    settings = settings or oc_history_settings.load()
    owned = con is None
    if owned:
        con = open_archive(settings.archive_path, readonly=True)
    try:
        row = con.execute('SELECT * FROM sessions WHERE key=?', (key,)).fetchone() if con else None
        if row is None:
            return False, 'not-archived'
        stored = con.execute('SELECT codec,body FROM bodies WHERE key=?', (key,)).fetchone()
        if stored is None:
            return False, 'body-missing'
        if type(row['archive_version']) is not int or not 1 <= row['archive_version'] <= ARCHIVE_VERSION:
            return False, 'version-unknown'
        if stored['codec'] != 'zlib-json':
            return False, 'body-corrupt'
        try:
            raw = zlib.decompress(stored['body'])
            if hashlib.sha256(raw).hexdigest() != row['body_sha256']:
                return False, 'body-corrupt'
            turns = json.loads(raw)
            if not valid_turns(turns) or len(turns) != row['turns']:
                return False, 'body-corrupt'
            notes = json.loads(row['notes_json']) if row['notes_json'] else {}
            if notes.get('unparsable_lines', 0):
                return False, 'body-incomplete'
        except (ValueError, zlib.error, TypeError, UnicodeError, AttributeError, RecursionError):
            return False, 'body-corrupt'
        sources = sources or Sources()
        try:
            witness = sources.live_witness(row)
        except FileNotFoundError:
            return False, 'source-gone'
        except oc_client.OpenCodeError as error:
            return False, 'source-gone' if error.status == 404 else 'witness-changed'
        except Exception:
            return False, 'witness-changed'
        if witness != row['witness']:
            return False, 'witness-changed'
        reason, exported = (exports or ExportIndex(settings)).session(row)
        if exported != (row['witness'], row['body_sha256']):
            return False, reason
        return True, 'ok'
    finally:
        if owned and con is not None:
            con.close()


def verify_archive(con, settings, *, sources=None, program=None, keys=None):
    sources, exports = sources or Sources(), ExportIndex(settings)
    selected = list(keys) if keys else [r['key'] for r in con.execute(
        'SELECT key FROM sessions' + (' WHERE program_core=?' if program else '') + ' ORDER BY key',
        (program,) if program else ())] if con else []
    rows, reasons = [], {}
    for key in selected:
        ok, reason = verified(key, con=con, settings=settings, sources=sources, exports=exports)
        rows.append({'key': key, 'ok': ok, 'reason': reason})
        reasons[reason] = reasons.get(reason, 0) + 1
    return {'sessions': rows, 'reasons': reasons, 'total': len(rows), 'metrics': vars(sources.metrics)}


def _trim_descendants(rows, key):
    """Live removal geometry only, children first; no eligibility rule lives here."""
    children = {}
    for row in rows.values():
        children.setdefault(row.get('parent_key'), []).append(row['key'])
    order, seen = [], set()

    def visit(current):
        if current in seen:
            return
        seen.add(current)
        for child in sorted(children.get(current, [])):
            visit(child)
        order.append(current)

    visit(key)
    return order


def _trim_program_notes(cores, ledger, workflowy_factory):
    """R2/R6 evidence: one root read and one seal-note read per old-enough core."""
    from oc_common import Workflowy, extract_yaml_block, program_short_name, registry_from_note
    from oc_sessions import PERSIST_RE
    facts = {}

    async def read():
        async with (workflowy_factory or Workflowy)() as wf:
            for core in cores:
                fact = facts[core] = {'name': core, 'seal': ledger[core].get('seal'), 'sealed': False,
                                      'persisted': False, 'root_error': None, 'seal_error': None}
                try:
                    name, note = await wf.read_note(f'[ap:{core}]')
                    fact['name'] = program_short_name(name, core)
                    fact['persisted'] = bool(PERSIST_RE.search(name or ''))
                    # seal_created_ms takes this same root-registry handle, but its
                    # creation clock is NOT a trim input (L-16). No third peek.
                    iface, _ = registry_from_note(note)
                    fact['seal'] = fact['seal'] or (iface or {}).get('seal')
                except Exception as error:
                    fact['root_error'] = str(error)
                if fact['seal']:
                    try:
                        _name, note = await wf.read_note(fact['seal'])
                        block = extract_yaml_block(note, 'program_seal') or {}
                        fact['sealed'] = (block.get('program_seal') or {}).get('state') == 'sealed'
                    except Exception as error:
                        fact['seal_error'] = str(error)
    try:
        asyncio.run(read())
    except Exception as error:
        for core in cores:
            facts.setdefault(core, {'name': core, 'sealed': False, 'seal_error': str(error)})
    return facts


def _trim_file_bytes(path):
    if not path:
        return 0
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.is_dir() else 0


def _claude_run_folder(record, state_root, eid):
    """The host run folder forget removes; the trim estimate and the full original share it."""
    if record.get('run_dir'):
        return Path(record['run_dir']).parent
    return Path(state_root) / eid.removeprefix('cc-') if state_root else None


class OriginalChanged(Exception):
    """The live source no longer matches the verified capture: keep it for the next run."""


def _original_json(value):
    # ASCII escapes keep lone UTF-16 code units from cropped tool output lossless.
    return json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def _read_unchanged(path, witness=None):
    """Whole file bytes, refusing a file that moved while read or differs from its stored witness."""
    try:
        source = open(path, 'rb')
    except FileNotFoundError:
        raise OriginalChanged(f'{path} is gone since it was verified') from None
    with source:
        before = os.fstat(source.fileno())
        data = source.read()
        after = os.fstat(source.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != after.st_size:
        raise OriginalChanged(f'{path} changed while it was read')
    if witness is not None and (after.st_ino, after.st_size, after.st_mtime_ns) != (
            witness.get('ino'), witness.get('size'), witness.get('mtime_ns')):
        raise OriginalChanged(f'{path} changed since it was verified')
    return data, int(after.st_mtime)


def _publish_original(path, members):
    """Temp file, fsync, atomic replace, then read the published file back member by member."""
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = {name: hashlib.sha256(data).hexdigest() for name, data, _ in members}
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as raw:
            with tarfile.open(fileobj=raw, mode='w:gz', compresslevel=6) as tar:
                for name, data, mtime in members:
                    info = tarfile.TarInfo(name)
                    info.size, info.mtime, info.mode = len(data), mtime, 0o600
                    tar.addfile(info, io.BytesIO(data))
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise
    try:
        found = {}
        with tarfile.open(path, 'r:gz') as tar:
            for member in tar:
                if not member.isfile() or member.name in found:
                    raise ValueError(f'{path}: unexpected member {member.name}')
                found[member.name] = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
        if found != expected:
            raise ValueError(f'{path}: read-back does not match what was written')
    except BaseException:
        path.unlink(missing_ok=True)  # never leave a file that looks like a good original
        raise
    return path.stat().st_size


# @beacon[
#   id=opc-history@write_originals,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=Full originals before a trim deletes anything: one verified .tar.gz per conversation under originals_dir/YYYY-MM/ (the session's created_at month). OpenCode: fresh unfiltered info and messages GETs whose witness must equal the stored one. Claude: the transcript, its <uuid>/ folder (subagents), the host run folder and the host's state record, transcripts checked against their stored witnesses. Temp, fsync, replace, read-back hashes; any failure or change raises so prune keeps the conversation live.,
# ]
def write_originals(con, settings, row, removed, *, sources, host, state_root=None):
    """Everything one deletion removes, compressed beside the backups; returns the verified files.

    OpenCode: one file per removed session (session.json, messages.json). Claude: one file for
    the main conversation, holding its transcript, its folder and run folder, and host-record.json.
    Each file carries manifest.json (keys, stored witnesses, sources, sizes, SHA-256).
    """
    stored = {r['key']: dict(r) for r in con.execute(
        f"SELECT key,backend,id,program_core,title,created_at,witness FROM sessions WHERE key IN "
        f"({','.join('?' for _ in removed)})", tuple(removed))}
    missing = sorted(set(removed) - set(stored))
    if missing:
        raise OriginalChanged('not archived: ' + ', '.join(missing))
    groups = [[key] for key in removed] if row['backend'] == 'opencode' else [[row['key']] + [
        key for key in removed if key != row['key']]]
    written = []
    for keys in groups:
        main, files, now = stored[keys[0]], [], int(time.time())
        witnesses = {key: json.loads(stored[key]['witness']) for key in keys}
        if main['backend'] == 'opencode':
            try:
                info = sources.opencode_session(main['id'])  # clocks BEFORE messages, as archived
                messages = sources.opencode_messages(main['id'])
            except oc_client.OpenCodeError as error:
                if error.status == 404:
                    raise OriginalChanged(main['key'] + ' is gone since it was verified') from None
                raise
            if opencode_witness(info, messages) != main['witness']:
                raise OriginalChanged(main['key'] + ' changed since it was verified')
            files += [('session.json', 'GET /api/session/' + main['id'], _original_json(info), now),
                      ('messages.json', 'GET /api/session/' + main['id'] + '/message', _original_json(messages), now)]
        else:
            record = host.call('state', execution_id=main['id'])
            transcript = Path(row['transcript_path']) if row.get('transcript_path') else None
            folders, seen = [], set()
            if transcript:
                prefix = 'projects/' + transcript.parent.name + '/'
                if transcript.exists() or witnesses[main['key']].get('kind') == 'claude-transcript':
                    if witnesses[main['key']].get('kind') != 'claude-transcript':
                        raise OriginalChanged(f'{transcript} appeared since {main["key"]} was verified')
                    data, mtime = _read_unchanged(transcript, witnesses[main['key']])
                    files.append((prefix + transcript.name, str(transcript), data, mtime))
                seen.add(main['key'])
                folders.append((transcript.with_suffix(''), prefix + transcript.stem + '/'))
            elif witnesses[main['key']].get('kind') != 'claude-transcript':
                seen.add(main['key'])
            run = _claude_run_folder(record, state_root, main['id'])
            folders += [(run, 'run/' + run.name + '/')] if run else []
            for folder, name in folders:
                for path in sorted(folder.rglob('*')) if folder.is_dir() else []:
                    if path.is_symlink() or not path.is_file():
                        continue
                    key = None
                    if path.parent == folder / 'subagents' and path.suffix == '.jsonl' and name.startswith('projects/'):
                        key = main['key'] + '/' + path.stem
                        if key not in witnesses:  # forget would delete a conversation History lacks
                            raise OriginalChanged(f'{path} appeared since {main["key"]} was verified')
                        seen.add(key)
                    data, mtime = _read_unchanged(path, witnesses.get(key))
                    files.append((name + path.relative_to(folder).as_posix(), str(path), data, mtime))
            if set(keys) - seen:
                raise OriginalChanged('no transcript for ' + ', '.join(sorted(set(keys) - seen)))
            files.append(('host-record.json', 'host state ' + main['id'], _original_json(record), now))
        manifest = {'format': 'session-history-original', 'format_version': 1, 'key': main['key'],
                    'backend': main['backend'], 'id': main['id'], 'program_core': main['program_core'],
                    'title': main['title'], 'created_at': main['created_at'], 'written_at': now_ms(),
                    'witnesses': witnesses, 'files': [{'name': name, 'source': source, 'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest()} for name, source, data, _ in files]}
        members = [('manifest.json', json.dumps(manifest, ensure_ascii=True, indent=1).encode('ascii'), now)]
        members += [(name, data, mtime) for name, _source, data, mtime in files]
        path = Path(settings.originals_dir) / period_for(main['created_at']) / (main['key'].replace(':', '-') + '.tar.gz')
        written.append({'key': main['key'], 'path': str(path), 'bytes': _publish_original(path, members),
                        'files': len(files)})
    return written


# @beacon[
#   id=opc-history@trim_plan,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The ONE R1-R7 trim rule, optionally scoped to named programs: live membership and deletion subtrees join the archive; ledger age precedes the two root/seal reads, then liveness/pins and Manager/persist/whole verified checks. First exclusions propagate to ancestors; last-moment R3/R4/R7 reuses only root/seal observations and the signature-checked export index, never a verified result. Reports where the full originals go and how many the run would write.,
# ]
def trim_plan(now=None, *, con=None, settings=None, sources=None, host=None, workflowy_factory=None,
              pins=None, older_than_days=None, ledger=None, program_facts=None, recheck_key=None, exports=None,
              programs=None):
    """Plan R1..R7 without writes. Times are milliseconds; every verification is fresh.

    Cheap excluded programs use the archive/OC census: their Claude members are
    labelled archive-only, not claimed to be a live enumeration. recheck_key is
    the executor's projection: R3/R4 still see the whole program, while R7 reads
    only the conversations that this deletion would remove. program_facts holds
    only the original two Workflowy observations, never a cached verified result.
    """
    from oc_backend import HostConnection, host_socket, seat_busy
    start = time.perf_counter()
    now, settings, sources = now if now is not None else now_ms(), settings or oc_history_settings.load(), sources or Sources()
    days = settings.trim_sealed_days if older_than_days is None else older_than_days
    ledger = oc_seal_ledger.read() if ledger is None else ledger
    pins = SessionPins(settings.pins_path) if pins is None else pins
    pinned = pins.all() if hasattr(pins, 'all') else pins
    owned = con is None
    if owned:
        con = open_archive(settings.archive_path, readonly=True)
    try:
        columns = 'key,unit_key,backend,id,native_uuid,program_core,parent_key,kind,role,title,source_json,raw_bytes,pruned_at'
        archived = {r['key']: dict(r) for r in con.execute('SELECT ' + columns + ' FROM sessions')} if con else {}
        rows = {key: {**row, 'evidence': 'archive-only'} for key, row in archived.items()}
        failures, infos, oc_error = [], [], None
        try:
            infos = sources.opencode_sessions()
        except Exception as error:
            oc_error = str(error)
            failures.append({'source': 'opencode', 'reason': oc_error})
        by_id = {s['id']: s for s in infos}
        parents = {'opencode:' + sid: 'opencode:' + s['parentID'] if s.get('parentID') else None
                   for sid, s in by_id.items()}
        attributed = {sid: program_for_opencode(s, by_id) for sid, s in by_id.items()}
        cores = {'opencode:' + sid: value[0] for sid, value in attributed.items()}
        for sid, info in by_id.items():
            key, (core, fields) = 'opencode:' + sid, attributed[sid]
            unit = unit_key(key, core, parents, cores)
            core = unit.removeprefix('program:') if unit.startswith('program:') else None
            rows[key] = {**archived.get(key, {}), 'key': key, 'id': sid, 'backend': 'opencode',
                         'unit_key': unit, 'program_core': core, 'parent_key': parents[key],
                         'kind': 'subagent' if info.get('parentID') else 'session',
                         'role': (fields or {}).get('kind'), 'title': info.get('title'),
                         'manager': bool((fields or {}).get('manager')), 'evidence': 'live'}
        all_cores = set(ledger) | {r['program_core'] for r in rows.values() if r.get('program_core')}
        if programs is not None:
            all_cores &= {_core(handle) or handle for handle in programs}
        if recheck_key:
            target = rows.get(recheck_key) or archived.get(recheck_key) or {}
            all_cores = {target.get('program_core')} - {None}
        old = sorted(core for core in all_cores if core in ledger and ledger[core]['sealed_at'] <= now - days * 86400000)
        facts = (program_facts if program_facts is not None else _trim_program_notes(old, ledger, workflowy_factory)) if old else {}
        viable = [core for core in old if facts.get(core, {}).get('sealed')]
        records, host_errors, health = {}, {}, {}
        status_ok, status = True, {}
        if viable:
            status_ok, status = sources.opencode_status() if oc_error is None else (False, {})
            for core in viable:
                try:
                    for record in sources.host_list(programs=[core]):
                        if list_program(record) == core:  # old hosts may ignore the selector
                            records[record['execution_id']] = record
                except Exception as error:
                    host_errors[core] = str(error)
                    failures.append({'source': 'claude', 'core': core, 'reason': str(error)})
            try:
                host = host or HostConnection(host_socket())
                health = host.call('health')
            except Exception as error:
                failures.append({'source': 'host-health', 'reason': str(error)})
        for eid, record in records.items():
            core, key = list_program(record), 'claude:' + eid
            path = record.get('native_transcript_path_expected')
            if not path:
                transcript = sources.transcripts().get(key)
                path = str(transcript.path) if transcript else None
            rows[key] = {**archived.get(key, {}), 'key': key, 'id': eid, 'backend': 'claude',
                         'unit_key': 'program:' + core, 'program_core': core, 'parent_key': None,
                         'kind': 'session', 'role': record.get('role'),
                         'title': (record.get('labels') or {}).get('title') or eid, 'evidence': 'live',
                         'transcript_path': path, 'already_archived': bool(record.get('archived'))}
            if path:
                for child in sorted((Path(path).with_suffix('') / 'subagents').glob('agent-*.jsonl')):
                    child_key = key + '/' + child.stem
                    rows[child_key] = {**archived.get(child_key, {}), 'key': child_key,
                        'id': child_key.removeprefix('claude:'), 'backend': 'claude', 'unit_key': 'program:' + core,
                        'program_core': core, 'parent_key': key, 'kind': 'subagent', 'role': 'subagent',
                        'title': child.stem, 'evidence': 'live', 'delete_with': key, 'transcript_path': str(child)}
        live = {key: r for key, r in rows.items() if r['evidence'] == 'live'}
        selected = set(_trim_descendants(live, recheck_key)) if recheck_key else set(rows)
        groups = []
        exports = exports or ExportIndex(settings)

        def exclude(row, rule, reason, blocked_by=None):
            row.update(eligible=False, rule=rule, reason=reason, blocked_by=blocked_by)

        for core in sorted(all_cores):
            members = [r for r in rows.values() if r.get('program_core') == core]
            fact, entry = facts.get(core, {}), ledger.get(core)
            group = {'core': core, 'name': fact.get('name', core), 'sealed_at': (entry or {}).get('sealed_at'),
                     'seal': fact.get('seal') or (entry or {}).get('seal'), 'sealed': fact.get('sealed', False),
                     'persisted': fact.get('persisted', False), 'sessions': members, 'exclusion': None}
            groups.append(group)
            problem = None
            if not entry:
                problem = ('R2', 'program has no seal-ledger line', None)
            elif core not in old:
                problem = ('R2', f'program was sealed less than {days:g} days ago', None)
            elif not fact.get('sealed'):
                problem = ('R2', 'seal is not in force (state is not sealed)' + (
                    ': ' + fact['seal_error'] if fact.get('seal_error') else ''), None)
            elif oc_error or not status_ok:
                problem = ('R3', 'OpenCode live status is unavailable' + (': ' + oc_error if oc_error else ''), 'opencode:*')
            elif core in host_errors:
                problem = ('R3', 'Claude live membership is unavailable: ' + host_errors[core], 'claude:program:' + core)
            else:
                for row in members:
                    record = records.get(row['id']) if row['backend'] == 'claude' and row['kind'] != 'subagent' else None
                    if row['evidence'] == 'live' and (row['backend'] == 'opencode' and status.get(row['id'], 'idle') != 'idle'
                            or record is not None and (seat_busy(record) or not owner_proved_gone(record))):
                        problem = ('R3', 'program has live or waiting work' if record is None or seat_busy(record)
                                   else 'not ended on the host', row['key'])
                        break
            if problem is None:
                pin = next((key for key in ['program:' + core] + ['session:' + r['key'] for r in members] if key in pinned), None)
                if pin:
                    problem = ('R4', 'program or a member is starred', pin.removeprefix('session:'))
            if problem:
                group['exclusion'] = dict(zip(('rule', 'reason', 'blocked_by'), problem))
            for row in members:
                if problem:
                    exclude(row, *problem)
                elif row.get('manager') or str(row.get('role') or '').lower() == 'manager' or str(archived.get(row['key'], {}).get('role') or '').lower() == 'manager':
                    exclude(row, 'R5', 'Manager conversations are never trimmed')
                elif fact.get('persisted') or fact.get('root_error'):
                    exclude(row, 'R6', '#oc-persist program' if fact.get('persisted') else 'program root unavailable: ' + fact['root_error'])
                elif row['evidence'] != 'live' or row.get('already_archived'):
                    exclude(row, 'R7', 'source-gone' if core in viable else 'live membership not enumerated')
                elif row['key'] in selected:
                    ok, reason = verified(row['key'], con=con, settings=settings, sources=sources, exports=exports)
                    row['verification'] = reason
                    if ok:
                        row.update(eligible=True, rule=None, reason='ok', blocked_by=None)
                    else:
                        exclude(row, 'R7', reason)
            if recheck_key:
                group['sessions'] = [r for r in members if r['key'] in selected]
        freestanding = [r for r in rows.values() if not r.get('program_core')] if not recheck_key and programs is None else []
        for row in freestanding:
            exclude(row, 'R1', 'not part of a program; freestanding history stays live')
        for row in live.values():
            if row['key'] not in selected or not row.get('eligible'):
                continue
            row['removes'] = _trim_descendants(live, row['key'])
            for key in row['removes']:
                child = rows[key]
                if not child.get('eligible'):
                    exclude(row, 'R7', f"deletion would remove a kept conversation ({child.get('rule')}: {child.get('reason')})", key)
                    break
        # A Claude child is removed only by its main conversation's forget, not
        # through a second file deleter. Do not advertise an undeliverable row.
        for row in live.values():
            if row.get('eligible') and row.get('delete_with') and not rows[row['delete_with']].get('eligible'):
                exclude(row, 'R7', 'main Claude conversation is kept', row['delete_with'])
        offered = [r for group in groups for r in group['sessions']] + freestanding
        totals = dict.fromkeys(('opencode', 'claude_transcripts', 'claude_subagents', 'claude_runs'), 0)
        for row in offered:
            if not row.get('eligible'):
                continue
            sizes = dict.fromkeys(totals, 0)
            try:
                if row['backend'] == 'opencode':
                    sizes['opencode'] = archived[row['key']]['raw_bytes']
                elif row['kind'] != 'subagent':
                    sizes['claude_transcripts'] = _trim_file_bytes(row.get('transcript_path'))
                    sizes['claude_subagents'] = _trim_file_bytes(Path(row['transcript_path']).with_suffix('')) if row.get('transcript_path') else 0
                    sizes['claude_runs'] = _trim_file_bytes(_claude_run_folder(records[row['id']], health.get('state_root'), row['id']))
            except OSError as error:
                row['bytes_error'] = str(error)  # an estimate failure never prevents a plan
            row['bytes'] = sizes
            for store, size in sizes.items():
                totals[store] += size
        eligible = [r for r in offered if r.get('eligible')]
        reasons = Counter(r['rule'] for r in offered if not r.get('eligible'))
        forget = 'forget' in (health.get('implemented') or [])
        originals = sum(not r.get('delete_with') and (r['backend'] == 'opencode' or forget) for r in eligible)
        return {'created_at': now, 'trim_sealed_days': days, 'trim_enabled': settings.trim_enabled,
                'programs': groups, 'freestanding': freestanding, 'eligible': [r['key'] for r in eligible],
                'summary': {'programs': sum(any(r.get('eligible') for r in g['sessions']) for g in groups),
                    'sessions': len(eligible), 'backends': dict(Counter(r['backend'] for r in eligible)), 'bytes': totals,
                    'checks': {'managers': sum(str(r.get('role') or '').lower() == 'manager' or r.get('manager', False) for r in eligible),
                               'unsealed_programs': sum(any(r.get('eligible') for r in g['sessions']) and not g['sealed'] for g in groups),
                               'pinned_units': sum(('program:' + g['core'] in pinned or any('session:' + r['key'] in pinned for r in g['sessions']))
                                                   and any(r.get('eligible') for r in g['sessions']) for g in groups),
                               'unverified_sessions': sum(r.get('verification') != 'ok' for r in eligible)},
                    'excluded_by_rule': dict(reasons)},
                'host': {'forget': forget, 'state_root': health.get('state_root')},
                'originals': {'dir': settings.originals_dir, 'files': originals},
                'program_facts': facts, 'source_failures': failures, 'metrics': json.loads(compact(vars(sources.metrics))),
                'duration_seconds': time.perf_counter() - start,
                'legacy_copies': "not measured by the archiver (OpenCode's database is not read; §5 D9)"}
    finally:
        if owned and con is not None:
            con.close()


def print_trim_plan(plan, *, dry_run=True):
    """One readable account, grouped rather than a thousand individual skip lines."""
    summary, sizes = plan['summary'], plan['summary']['bytes']
    print(('DRY RUN — nothing deleted. ' if dry_run else '') + f"{summary['sessions']} conversations in {summary['programs']} programs would leave the live stores.")
    print(f"Only programs still sealed for at least {plan['trim_sealed_days']:g} days, with nothing live, waiting or starred; never Managers or #oc-persist work. Every removed conversation must be archived, unchanged and backed up.")
    print(f"Automatic trimming is {'on' if plan['trim_enabled'] else 'OFF'}. Switching it on is Dan's decision, carried out by the Manager.")
    print(f"OpenCode: {summary['backends'].get('opencode', 0)} conversations; estimated {sizes['opencode']:,} bytes of message JSON (archive raw_bytes, not measured database pages).")
    print(f"Claude: {summary['backends'].get('claude', 0)} conversations; {sizes['claude_transcripts']:,} transcript bytes + {sizes['claude_subagents']:,} subagent-folder bytes + {sizes['claude_runs']:,} run-folder bytes. Host database space is not measured.")
    print('Database files do not shrink: deleted pages are reused; no VACUUM.')
    originals = plan['originals']
    print(f"Full originals: {originals['files']} compressed files would be written to {originals['dir']}/YYYY-MM/ and read back before their conversations are deleted; a conversation whose original cannot be written stays live.")
    for group in plan['programs']:
        counts = Counter(r['backend'] for r in group['sessions'] if r.get('eligible'))
        if counts:
            print(f"  Would trim {group['name']} [ap:{group['core']}]: " + ', '.join(f'{n} {backend}' for backend, n in sorted(counts.items())))
    gone = Counter()
    for rule in ('R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7'):
        grouped = {}
        for group in plan['programs'] + [{'name': 'Freestanding', 'core': None, 'sessions': plan['freestanding']}]:
            for row in group['sessions']:
                if row.get('rule') == rule:
                    name = group['name'] + (f" [ap:{group['core']}]" if group['core'] else '')
                    if (rule, row['reason']) == ('R7', 'source-gone'):
                        gone[name] += 1  # trimmed earlier (or removed by hand): nothing is kept live
                        continue
                    grouped.setdefault(row['reason'], Counter())[name] += 1
            if not group['sessions'] and (group.get('exclusion') or {}).get('rule') == rule:
                grouped.setdefault(group['exclusion']['reason'], Counter())[group['name'] + f" [ap:{group['core']}] (no known members)"] += 0
        for reason, programs in grouped.items():
            print(f"  Kept — {rule}: {reason}: {sum(programs.values())} known conversations; " + '; '.join(f'{name} ({n})' for name, n in programs.items()))
    if gone:
        print(f"  Already gone from the live stores (in 📦 History): {sum(gone.values())} conversations "
              f"({len(gone)} program{'' if len(gone) == 1 else 's'}).")
    print('Cheap exclusions use known archive/OpenCode members; Claude is enumerated only after the ledger-age and seal-state checks.')
    print('OpenCode legacy copies: ' + plan['legacy_copies'] + '.')
    if not plan['host']['forget']:
        waiting = [r['key'] for g in plan['programs'] for r in g['sessions'] if r.get('eligible') and r['backend'] == 'claude']
        print(f"Claude: {len(waiting)} eligible conversations waiting for the host's `forget` verb.")
    for failure in plan['source_failures']:
        print(f"  Source unavailable — {failure['source']}: {failure['reason']}; affected conversations stay.")
    check = summary['checks']
    print(f"Check: {check['managers']} Managers · {check['unsealed_programs']} unsealed programs · {check['pinned_units']} pinned units · {check['unverified_sessions']} unverified sessions among the eligible.")


def write_trim_plan(plan, settings):
    folder = Path(settings.archive_path).parent
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.fromtimestamp(plan['created_at'] / 1000, timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    path = folder / f'prune-plan-{stamp}.json'
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as output:
        output.write(compact(plan) + '\n')
    return str(path)


def _trim_rows(plan):
    return {r['key']: r for g in plan['programs'] for r in g['sessions']} | {
        r['key']: r for r in plan['freestanding']}


def _mark_pruned(con, keys):
    """Record an observed completed deletion, advancing the existing export clock."""
    changed = []
    con.execute('BEGIN IMMEDIATE')
    try:
        latest = con.execute('''SELECT max(max(s.archived_at,coalesce(s.pruned_at,0),coalesce(u.archived_at,0)))
            FROM sessions s LEFT JOIN units u ON u.unit_key=s.unit_key''').fetchone()[0]
        at = max(now_ms(), (latest or 0) + 1)
        for key in keys:
            if con.execute('UPDATE sessions SET pruned_at=? WHERE key=? AND pruned_at IS NULL', (at, key)).rowcount:
                changed.append(key)
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return changed


# @beacon[
#   id=opc-history@prune,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The ONE trim executor for history prune, sweep and sessions --prune, with an optional explicit program scope: dry run writes only JSON/report; children-first execution uses the existing OpenCode deleter or feature-detected forget. Each action gets fresh trim_plan R3/R4/R7, no cached ok, then write_originals' verified full original; a changed source or failed original skips the action, never deleting. Skips keep ancestors; pruned_at follows DELETE or observed completed tombstone, then existing exports republish.,
# ]
def prune(con=None, settings=None, *, sources=None, host=None, workflowy_factory=None, pins=None,
          older_than_days=None, dry_run=False, print_report=True, programs=None):
    """Execute the one plan, or write only its dry run. No confirmation, cap or wait.

    Claude completion is observed once through state after forget; an unfinished
    background job stays pending and the next run observes its tombstone again.
    Accepting a job is never recorded as a completed deletion (L-04).
    """
    from oc_backend import HostConnection, host_socket
    from oc_sessions import _delete
    settings = settings or oc_history_settings.load()
    sources, host = sources or Sources(), host or HostConnection(host_socket())
    pins = SessionPins(settings.pins_path) if pins is None else pins
    owned = con is None
    if owned:
        con = open_archive(settings.archive_path, readonly=dry_run)
    try:
        exports = ExportIndex(settings)  # signatures rechecked by verified; never a cached ok
        plan = trim_plan(con=con, settings=settings, sources=sources, host=host,
                         workflowy_factory=workflowy_factory, pins=pins, older_than_days=older_than_days,
                         exports=exports, programs=programs)
        path = write_trim_plan(plan, settings)
        result = {'plan': plan, 'plan_path': path, 'deleted': [], 'skipped': [], 'pending': [],
                  'waiting': [], 'failures': [], 'originals': [],
                  'pruned_at_policy': 'OpenCode: DELETE returned; Claude: state by id shows archived and no forget_pending (this run or the next)',
                  'exports': {'exported': [], 'failures': []}}
        if print_report:
            print_trim_plan(plan, dry_run=dry_run)
            print('Every conversation and exclusion: ' + path)
        if dry_run:
            return result
        rows, blocked = _trim_rows(plan), {}

        def skip(key, reason, failing=None):
            failing = failing or key
            result['skipped'].append({'key': key, 'reason': reason, 'blocked_by': failing})
            current, seen = key, set()
            while current and current not in seen:
                seen.add(current)
                blocked[current] = {'reason': reason, 'blocked_by': failing}
                current = rows.get(current, {}).get('parent_key')

        def observe(row, removed):
            record = host.call('state', execution_id=row['id'])
            if not record.get('archived') or record.get('forget_pending'):
                return False
            result['deleted'].extend(_mark_pruned(con, removed))
            return True

        # A previous run may have returned while forget was still working.
        # Only a completed tombstone, freshly observed by exact id, closes it.
        for row in rows.values():
            if row['backend'] == 'claude' and row['kind'] != 'subagent' and row.get('already_archived') and row.get('pruned_at') is None:
                try:
                    removed = _trim_descendants({key: dict(r) for key, r in rows.items() if r['backend'] == 'claude'}, row['key'])
                    if not observe(row, removed):
                        result['pending'].append({'key': row['key'], 'reason': 'forget is still pending'})
                except Exception as error:
                    result['failures'].append({'key': row['key'], 'reason': str(error)})
        order, seen = [], set()
        for key in plan['eligible']:
            for member in _trim_descendants(rows, key):
                if member in plan['eligible'] and member not in seen:
                    seen.add(member)
                    order.append(member)
        for key in order:
            row = rows[key]
            if row.get('delete_with'):
                continue
            if key in blocked:
                skip(key, **{'reason': blocked[key]['reason'], 'failing': blocked[key]['blocked_by']})
                continue
            if row['backend'] == 'claude' and not plan['host']['forget']:
                result['waiting'].extend({'key': member, 'reason': "waiting for the host's `forget` verb"}
                    for member in _trim_descendants(rows, key) if member in plan['eligible'])
                continue
            fresh = trim_plan(con=con, settings=settings, sources=sources, host=host, pins=pins,
                              older_than_days=older_than_days, program_facts=plan['program_facts'], recheck_key=key, exports=exports)
            current = _trim_rows(fresh)
            rows.update(current)
            decision = current.get(key, {})
            if not decision.get('eligible'):
                skip(key, decision.get('reason', 'source-gone'), decision.get('blocked_by'))
                continue
            removed = decision['removes']
            try:
                result['originals'].extend(write_originals(con, settings, decision, removed, sources=sources,
                                                           host=host, state_root=plan['host'].get('state_root')))
            except OriginalChanged as error:
                skip(key, str(error))
                continue
            except Exception as error:
                result['failures'].append({'key': key, 'reason': 'full original not written: ' + str(error)})
                skip(key, 'full original not written: ' + str(error))
                continue
            try:
                if row['backend'] == 'opencode':
                    done, failed = _delete([row['id']])
                    if failed or not done:
                        result['failures'].append({'key': key, 'reason': 'OpenCode DELETE failed'})
                        skip(key, 'OpenCode DELETE failed')
                    else:
                        result['deleted'].extend(_mark_pruned(con, removed))
                else:
                    try:
                        answer = host.call('forget', execution_ids=[row['id']])
                    except Exception as error:
                        if 'unknown method' not in str(error).lower():
                            raise
                        plan['host']['forget'] = False
                        result['waiting'].append({'key': key, 'reason': "waiting for the host's `forget` verb"})
                        continue
                    refusal = next((r for r in answer.get('skipped', []) if r['id'] == row['id']), None)
                    if refusal:
                        skip(key, refusal['reason'])  # not-ended/unknown/already-archived are skips, not failures
                    elif row['id'] in answer.get('accepted', []):
                        if not observe(row, removed):
                            result['pending'].append({'key': key, 'job': answer.get('job'), 'reason': 'forget accepted; completion not yet observed'})
                    else:
                        skip(key, 'forget did not accept this id')
            except Exception as error:
                result['failures'].append({'key': key, 'reason': str(error)})
                skip(key, str(error))
        # The existing publisher also heals an earlier run interrupted after
        # pruned_at committed. It owns the format and atomic replacement.
        if con is not None:
            result['exports'] = export_archive(con, settings)
            result['failures'].extend({'source': 'export', **failure} for failure in result['exports']['failures'])
        history_log('prune ' + compact({k: v for k, v in result.items() if k != 'plan'}), settings)
        if print_report:
            print(f"Trim result: {len(result['deleted'])} conversations deleted; {len(result['skipped'])} skipped; {len(result['pending'])} forgotten on the host, recorded on the next run; {len(result['waiting'])} waiting for forget; {len(result['failures'])} failures.")
            print(f"Full originals: {len(result['originals'])} files written and read back ({sum(o['bytes'] for o in result['originals']):,} bytes) in {plan['originals']['dir']}.")
            for item in result['skipped']:
                print(f"  Kept {item['key']}: {item['reason']} (because of {item['blocked_by']}).")
        return result
    finally:
        if owned and con is not None:
            con.close()


# @beacon[
#   id=opc-history@rebuild,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=Rebuild from backups alone: verify canonical body hashes, recover counts from turns arrays and retain newest session/unit images across periods; restore the period inventory including empty images so later exports still heal them. Required meta is initialized and a complete temporary database replaces the target.,
# ]
def rebuild(folder, target):
    """Restore rows and period inventory from exports alone, with no live source reads."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix='.history-rebuild-', suffix='.sqlite3', dir=target.parent)
    os.close(fd)
    con, files = open_archive(temp), 0
    try:
        con.execute('BEGIN IMMEDIATE')
        for path in sorted(Path(folder).glob('session-history-*.jsonl.gz')):
            files += 1
            with gzip.open(path, 'rt', encoding='utf-8') as source:
                for line in source:
                    value = json.loads(line)
                    kind = value.pop('type')
                    if kind == 'header':
                        _remember_export_periods(con, [value['month']])
                        continue
                    if kind == 'unit':
                        previous = con.execute('SELECT archived_at FROM units WHERE unit_key=?',
                                               (value['unit_key'],)).fetchone()
                        if previous is None or previous[0] < value['archived_at']:
                            _upsert(con, 'units', UNIT_COLUMNS, value)
                    elif kind == 'session':
                        turns = value['turns']
                        raw = compact(turns).encode('utf-8')
                        if not valid_turns(turns) or hashlib.sha256(raw).hexdigest() != value['body_sha256']:
                            raise ValueError(f"{path.name}: body hash/shape mismatch for {value['key']}")
                        previous = con.execute('SELECT archived_at,pruned_at FROM sessions WHERE key=?',
                                               (value['key'],)).fetchone()
                        incoming = max(value['archived_at'], value.get('pruned_at') or 0)
                        if previous and max(previous['archived_at'], previous['pruned_at'] or 0) >= incoming:
                            continue
                        compressed = zlib.compress(raw, 6)
                        value['turns'], value['body_bytes'] = len(turns), len(compressed)
                        _upsert(con, 'sessions', SESSION_COLUMNS, value)
                        _upsert(con, 'bodies', ('key', 'codec', 'body'),
                                {'key': value['key'], 'codec': 'zlib-json', 'body': compressed})
        con.commit()
        counts = {table: con.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                  for table in ('sessions', 'units', 'bodies')}
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
    with open(temp, 'rb') as source:
        os.fsync(source.fileno())
    os.replace(temp, target)
    return {'files': files, **counts, 'path': str(target), 'bytes': target.stat().st_size}


def status(settings):
    con = open_archive(settings.archive_path, readonly=True)
    try:
        files = sorted(Path(settings.export_dir).glob('session-history-*.jsonl.gz'))
        originals = sorted(Path(settings.originals_dir).glob('*/*.tar.gz'))
        return {'status': 'complete' if con else 'empty',
                'settings': {k: v for k, v in vars(settings).items() if k != 'problems'},
                'problems': list(settings.problems), 'archive_path': settings.archive_path,
                'log': str(Path(settings.archive_path).parent / 'history.log'),
                'archive_bytes': Path(settings.archive_path).stat().st_size if con else 0,
                'sessions': con.execute('SELECT count(*) FROM sessions').fetchone()[0] if con else 0,
                'units': con.execute('SELECT count(*) FROM units').fetchone()[0] if con else 0,
                'raw_bytes': con.execute('SELECT coalesce(sum(raw_bytes),0) FROM sessions').fetchone()[0] if con else 0,
                'body_bytes': con.execute('SELECT coalesce(sum(body_bytes),0) FROM sessions').fetchone()[0] if con else 0,
                'exports': len(files), 'export_bytes': sum(p.stat().st_size for p in files),
                'originals': len(originals), 'originals_bytes': sum(p.stat().st_size for p in originals),
                'last_sweep_at': meta_get(con, 'last_sweep_at'), 'last_sweep': meta_get(con, 'last_sweep'),
                'last_export_at': meta_get(con, 'last_export_at'),
                'seals_backfilled_at': meta_get(con, 'seals_backfilled_at')}
    finally:
        if con:
            con.close()


# @beacon[
#   id=opc-history@junk,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=oc.sh history junk feature-detects the host forget verb, starts exact empty-sid row cleanup and prints the six health progress fields until complete or interrupted; an older host gets one Manager-restart line, never a restart or a fallback deletion.,
# ]
def junk():
    """Monitor host-owned cleanup; interruption stops this reader, never the job."""
    from oc_backend import HostConnection, SeatError, host_socket, report
    needs_restart = "Claude host needs its guarded restart (by the Manager) before history junk."
    try:
        host = HostConnection(host_socket())
        if 'forget' not in host.call('health').get('implemented', []):
            print(needs_restart, flush=True)
            return 1
        try:
            admission = host.call('forget', empty_sid=True)
        except SeatError as error:
            if 'unknown method' not in str(error) or error.lost:
                raise
            print(needs_restart, flush=True)
            return 1
        print(compact(admission), flush=True)
        while True:
            progress = host.call('health')['forget']
            print(compact({'forget': progress}), flush=True)
            if progress['status'] in ('complete', 'failed'):
                return int(progress['status'] == 'failed')
            time.sleep(.5)  # progress display cadence only; never throttles deletion on the host
    except KeyboardInterrupt:
        print('Monitoring interrupted; deletion continues on the host.', flush=True)
        return 130
    except SeatError as error:
        return report(error)


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    verbs = cli.add_subparsers(dest='verb', required=True)
    arch = verbs.add_parser('archive', help='archive selected conversations (sources are read only)')
    select = arch.add_mutually_exclusive_group()
    select.add_argument('--program')
    select.add_argument('--idle', type=float, metavar='HOURS')
    select.add_argument('--all', action='store_true')
    select.add_argument('--session', nargs='+', action='extend', dest='sessions')
    arch.add_argument('--dry-run', action='store_true')
    verify = verbs.add_parser('verify', help='the V1-V3 gate, with a reason for every selected session')
    select = verify.add_mutually_exclusive_group()
    select.add_argument('--all', action='store_true')
    select.add_argument('--program')
    select.add_argument('--session', nargs='+', action='extend', dest='sessions')
    export = verbs.add_parser('export', help='publish missing or stale complete backup periods')
    select = export.add_mutually_exclusive_group()
    select.add_argument('--all', action='store_true')
    select.add_argument('--month', help='UTC YYYY-MM (the whole year at year grain)')
    restore = verbs.add_parser('rebuild', help='rebuild from the backup files alone')
    restore.add_argument('--from-exports', required=True)
    restore.add_argument('--to', required=True)
    verbs.add_parser('status', help='counts, sizes, last runs and settings problems; no source reads')
    verbs.add_parser('sweep', help='archive changed idle conversations and publish backups in the background')
    verbs.add_parser('junk', help='remove the Claude host empty-sid row residue; monitor background progress')
    trim = verbs.add_parser('prune', help='trim verified sealed-program history (the Manager acts on Dan\'s word)')
    trim.add_argument('--dry-run', action='store_true', help='report only; JSON beside the archive')
    seals = verbs.add_parser('seals', help='one-time pre-ledger seal index backfill (Workflowy reads only)')
    seals.add_argument('--backfill', action='store_true', required=True)
    return cli


# @beacon[
#   id=opc-history@main,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=oc.sh history archive/sweep/seals/export/rebuild/verify/status/prune/junk dispatch; one settings loader, sole V1-V3 verified gate and one trim plan/executor. Prune reports in plain words with complete JSON beside the archive; only the configured sweep switch or the Manager's explicit prune executes trimming. junk monitors the host's feature-detected empty-sid cleanup without opening the archive. Complete missing/stale backups healed after writer runs and first-backfill seals indexed.,
# ]
def main(argv=None):
    args, settings = parser().parse_args(argv), oc_history_settings.load()
    if args.verb == 'junk':
        return junk()
    if args.verb == 'prune':
        return int(bool(prune(settings=settings, dry_run=args.dry_run)['failures']))
    if args.verb == 'status':
        print(compact(status(settings)))
        return 0
    if args.verb == 'rebuild':
        print(compact(rebuild(args.from_exports, args.to)))
        return 0
    if args.verb in ('sweep', 'seals'):
        con = open_archive(settings.archive_path)
        try:
            if args.verb == 'sweep':
                result = sweep(con, settings)
                failed = result['failed'] or result['exports']['failures'] or result.get('seals', {}).get('failures') or result.get('trim', {}).get('failures')
            else:
                result = backfill_seals(con, settings=settings)
                result['seal_units_updated'] = refresh_seals(con)
                result['exports'] = export_archive(con, settings)
                failed = result['failures'] or result['exports']['failures']
            print(compact(result))
            return int(bool(failed))
        finally:
            con.close()
    if args.verb == 'verify':
        con = open_archive(settings.archive_path, readonly=True)
        try:
            result = verify_archive(con, settings, program=(_core(args.program) or args.program) if args.program else None,
                                    keys=args.sessions)
            print(compact(result))
            return int(any(not r['ok'] for r in result['sessions']))
        finally:
            if con:
                con.close()
    if args.verb == 'export':
        con = open_archive(settings.archive_path)
        try:
            result = export_archive(con, settings, month=args.month)
            print(compact(result))
            return int(bool(result['failures']))
        finally:
            con.close()
    selection = Selection(program=_core(args.program) or args.program if args.program else None,
                          idle=args.idle, all=args.all, sessions=set(args.sessions) if args.sessions else None,
                          dry_run=args.dry_run)
    if not selection.all and not selection.program and not selection.sessions and selection.idle is None:
        selection.idle = settings.sweep_idle_hours
    con = open_archive(settings.archive_path, readonly=True) if selection.dry_run else open_archive(settings.archive_path)
    if con is None:
        con = open_archive(':memory:')
    try:
        sources = Sources()
        result = archive(con, selection, sources=sources)
        if selection.dry_run:
            keys = set(result['written']) | {r['key'] for r in con.execute('SELECT key,program_core FROM sessions')
                    if not selection.program or r['program_core'] == selection.program}
            if selection.sessions:
                keys &= selection.sessions
            result['verification'] = verify_archive(con, settings, sources=sources, keys=sorted(keys))
        else:
            finish_archive_run(con, settings, result, sources=sources, backfill=selection.all)
        print(compact(result))
        return int(bool(result['failed'] or result.get('exports', {}).get('failures') or result.get('seals', {}).get('failures')))
    finally:
        con.close()


if __name__ == '__main__':
    raise SystemExit(main())
