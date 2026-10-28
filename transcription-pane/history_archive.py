"""📦 History: the pane's read-only reader of the session archive, behind GET /api/history/* (MasterSpec §3.8).

The archive (one SQLite file; scripts/oc_history.py is its only writer) is opened read-only (`mode=ro`) by the
first /api/history/* request and never before. Its path comes from the one settings loader at every request, so
an archive that appears, moves or is rebuilt is seen by the next request; an absent file is an ordinary empty
answer, never an error. One read-only connection serves every pane thread behind one lock.

Member rows come out in the two shapes the sidebar already draws: Claude rows through host_row_fields (the live
inventory's own field builder) plus stored columns, OpenCode rows through opencode_row (the live attribution).
programs and sessions read stored columns only and never decode a conversation body; their stored telemetry is
carried into the answer as its stored text (encode_answer), never decoded and re-encoded. A programs page's units
carry `telemetry: null` (the page-weight ruling, [ap:NF5UM1]): the unit column repeats every member row's entry,
and each member row carries its own. glance and events read one
body and parse only the turns they show. The archive stores epoch milliseconds; answers carry epoch seconds.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
from urllib.parse import parse_qs, quote
import zlib

_SCRIPTS = str(Path(__file__).resolve().parent.parent / 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import oc_history_settings  # noqa: E402  (the one settings loader: archive_path)

import claude_launch  # noqa: E402  (apply_names: the one chosen-name rule)
from claude_live import host_row_fields  # noqa: E402
from claude_reader import ReaderError, folder_role, parse_record, preview_data  # noqa: E402
from opencode_conversation import encoded, text_bytes  # noqa: E402
from opencode_programs import GLANCE_MESSAGES, SESSION_ID, glance_messages, opencode_row  # noqa: E402

TABLES = ('meta', 'sessions', 'bodies', 'units')   # §3.3; later schema versions only add (§3.7)
PAGE_UNITS, MAX_UNITS = 24, 100
MAX_KEYS = 80
PARAM_LIMIT = 4096
PAGE_ROWS = 100
RESPONSE_LIMIT = 8 * 1024 * 1024       # one events answer, as /api/opencode/events (never cropped: 413)
SCAN_UNITS = 16                        # units examined per round while warm-only units are skipped
READ_TIMEOUT = 2.0                     # seconds a read waits on the archiver's lock before answering 503
SAFE_INTEGER = 2**53 - 1
REF = re.compile(r'[A-Za-z0-9_-]{1,256}\Z')
_UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
_KEY = rf'(?:claude:cc-{_UUID}(?:/agent-[A-Za-z0-9_-]{{1,256}})?|opencode:ses_[A-Za-z0-9]{{8,64}})'
SESSION_KEY = re.compile(_KEY + r'\Z')
UNIT_KEY = re.compile(rf'(?:program:[A-Z0-9]{{6}}|session:{_KEY})\Z')
SUBAGENT_ID = re.compile(rf'cc-({_UUID})/agent-([A-Za-z0-9_-]{{1,256}})\Z')
CORE = re.compile(r'[A-Z0-9]{6}\Z')
NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
TOOL_STATUS = {'ok': 'stored', 'error': 'error', 'unknown': 'pending-or-missing'}
ROUTES = {'/api/history/programs': ('before', 'limit'), '/api/history/sessions': ('unit', 'keys'),
          '/api/history/glance': ('key',), '/api/history/events': ('key', 'fileEpoch', 'fromSeq', 'cursor')}
UNIT_COLUMNS = 'unit_key, kind, program_core, first_at, last_at, sessions, cost_usd, sealed_at'
SESSION_COLUMNS = ('key, unit_key, backend, id, native_uuid, parent_key, kind, title, chosen_name, cwd, created_at, '
                   'last_at, models_json, turn_facts_json, telemetry_json, source_json, body_sha256, archived_at, pruned_at')
_DECODER = json.JSONDecoder()


def _bad(message):
    return ReaderError(400, 'bad-request', message)


def _answer(status, **fields):
    return {'schemaVersion': 1, 'backend': 'history', 'status': status, **fields}


def b64u(data) -> str:
    """Unpadded base64url (§3.8's refs and this module's cursors): always matches [A-Za-z0-9_-]."""
    return base64.urlsafe_b64encode(data.encode('utf-8') if isinstance(data, str) else data).decode('ascii').rstrip('=')


def _unb64u(token):
    return base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))


def refs(key, body_sha256) -> dict:
    """§3.8's fixed handles for one archived session: sessionRef, its main scopeRef and the body's fileEpoch."""
    return {'sessionRef': 'h_' + b64u(key), 'scopeRef': 'h_' + b64u(key + ':main'),
            'fileEpoch': 'h_' + str(body_sha256)[:16]}


def _seconds(ms):
    """Archive milliseconds as the renderer's epoch seconds (None stays None)."""
    return ms / 1000 if type(ms) in (int, float) else None


def _json_value(text):
    """A stored JSON column's value, or None when absent or unreadable (display data never fails a page); a stored
    NaN or Infinity, which no browser parses, reads as null."""
    try:
        return json.loads(text, parse_constant=lambda _: None) if isinstance(text, str) else None
    except ValueError:
        return None


class StoredJSON:
    """A stored JSON object column carried into an answer as its stored text; encode_answer splices it in."""
    __slots__ = ('text',)

    def __init__(self, text):
        self.text = text


def _stored_object(text):
    """A stored JSON object column (telemetry_json) for an answer, without decoding it: the archive's one writer
    (scripts/oc_history.py) stores compact JSON, so its text is carried verbatim. Text that is not an object's (absent,
    cut, garbage) is null, as an unreadable column always was; text naming NaN or Infinity is decoded instead."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text[:1] != '{' or text[-1:] != '}':
        return None
    if 'NaN' in text or 'Infinity' in text:
        value = _json_value(text)
        return value if isinstance(value, dict) else None
    return StoredJSON(text)


# @beacon[
#   id=opc-history@encode_answer,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=One History answer as compact UTF-8 JSON (opencode_conversation.encoded's form) with every StoredJSON value spliced in as its stored text, so a page carrying megabytes of stored telemetry never decodes and re-encodes it; the same JSON value as decoding would give.,
# ]
def encode_answer(answer) -> bytes:
    """Compact UTF-8 JSON of one answer, each StoredJSON value spliced in verbatim behind a per-answer nonce."""
    stored, nonce = [], secrets.token_hex(8)

    def splice(value):
        if isinstance(value, StoredJSON):
            stored.append(value.text)
            return '\x00' + nonce + str(len(stored) - 1) + '\x00'
        raise TypeError(f'{type(value).__name__} is not JSON serializable')

    body = json.dumps(answer, ensure_ascii=False, separators=(',', ':'), allow_nan=False,
                      default=splice).encode('utf-8', 'backslashreplace')
    if not stored:
        return body
    marker = re.compile(rb'"\\u0000' + nonce.encode('ascii') + rb'([0-9]+)\\u0000"')
    return marker.sub(lambda m: stored[int(m.group(1))].encode('utf-8', 'backslashreplace'), body)


def _capabilities(subagents):
    return {'history': True, 'live': False, 'context': False, 'inspect': False, 'nativeInput': False,
            'controls': False, 'subagents': subagents, 'reconnect': False}


class _Cut(Exception):
    """The preview ended before this value began."""


def _prefix_value(text):
    """The JSON value a cut compact-JSON preview began: whole members kept, a cut string ending in '…', the rest
    (a cut key, number or literal) dropped; None when no value began."""
    pos = 0

    def skip():
        nonlocal pos
        while pos < len(text) and text[pos] in ' \t\r\n':
            pos += 1

    def string():
        nonlocal pos
        try:
            value, pos = _DECODER.raw_decode(text, pos)
            return value, True
        except ValueError:
            body, pos = text[pos + 1:], len(text)
        for drop in range(min(len(body), 6) + 1):        # a dangling escape (\, \u12…) is dropped
            try:
                value = json.loads('"' + body[:len(body) - drop] + '"')
                break
            except ValueError:
                continue
        else:
            value = ''
        while value and '\ud800' <= value[-1] <= '\udbff':
            value = value[:-1]
        return value + '…', False

    def value():
        nonlocal pos
        skip()
        if pos >= len(text):
            raise _Cut
        c = text[pos]
        if c in '{[':
            return container(c)
        if c == '"':
            return string()
        for token, literal in (('true', True), ('false', False), ('null', None)):
            if text.startswith(token, pos):
                pos += len(token)
                return literal, True
        found = NUMBER.match(text, pos)
        if not found or found.end() >= len(text):
            raise _Cut                                     # a number or literal the cut may have shortened
        pos = found.end()
        return json.loads(found.group()), True

    def container(opening):
        nonlocal pos
        pos += 1
        items = {} if opening == '{' else []
        while True:
            skip()
            if pos >= len(text):
                return items, False
            if text[pos] in '}]':
                pos += 1
                return items, True
            if text[pos] == ',':
                pos += 1
                continue
            if opening == '{':
                if text[pos] != '"':
                    raise ValueError('object key is not a string')
                name, whole = string()
                skip()
                if not whole or pos >= len(text) or text[pos] != ':':
                    return items, False
                pos += 1
            try:
                member, whole = value()
            except _Cut:
                return items, False
            if opening == '{':
                items[name] = member
            else:
                items.append(member)
            if not whole:
                return items, False

    try:
        return value()[0]
    except _Cut:
        return None


# @beacon[
#   id=opc-history@archived_preview_data,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=A History tool row's previewData from its archived 120-code-point preview: whole JSON as is, a cut preview repaired to the value it began (whole members, the cut string ending in …), then the one preview_data descriptor builder the live rows use; never a second renderer.,
# ]
def archived_preview_data(preview) -> dict:
    """previewData (the descriptor the shared preview core draws) for one archived tool entry's `preview`."""
    if not isinstance(preview, str) or not preview.strip():
        return {'schemaVersion': 1, 'status': 'unavailable', 'reason': 'no archived preview'}
    try:
        json.loads(preview)
        wire = preview
    except ValueError:
        try:
            value = _prefix_value(preview)
        except (ValueError, RecursionError):
            value = None
        if value is None:
            return {'schemaVersion': 1, 'status': 'unavailable', 'reason': 'archived preview cut before its first value'}
        wire = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    try:
        text, root = parse_record(('{"input":' + wire + '}').encode('utf-8'))
        return preview_data(text, root.members()['input'])
    except (ValueError, UnicodeError, RecursionError):
        return {'schemaVersion': 1, 'status': 'unavailable', 'reason': 'archived preview unreadable'}


class _Turns:
    """One decoded body (§3.4: the canonical JSON array of turns, each opening with `{"seq":`), read by position.

    Only the windows asked for are parsed: a turn opens with `{"seq":` and no JSON string can hold that text
    unescaped. A body not in that canonical form is parsed whole instead, so the answer never depends on it."""

    def __init__(self, raw: bytes):
        self.raw, self.whole = raw, None
        starts, at = [], raw.find(b'{"seq":')
        while at >= 0:
            starts.append(at)
            at = raw.find(b'{"seq":', at + 7)
        self.starts = starts
        canonical = (raw == b'[]') if not starts else (raw[:1] == b'[' and starts[0] == 1 and raw[-1:] == b']')
        if not canonical or (starts and self._window(len(starts) - 1, len(starts)) is None):
            self._parse_whole()

    def _parse_whole(self):
        value = json.loads(self.raw)
        if not isinstance(value, list) or not all(isinstance(turn, dict) for turn in value):
            raise ValueError('the body is not an array of turns')
        self.whole = value

    def __len__(self):
        return len(self.whole) if self.whole is not None else len(self.starts)

    def _window(self, a, b):
        end = self.starts[b] - 1 if b < len(self.starts) else len(self.raw) - 1
        try:
            value = json.loads(b'[' + self.raw[self.starts[a]:end] + b']')
        except ValueError:
            return None
        if (not isinstance(value, list) or len(value) != b - a or
                any(not isinstance(turn, dict) or turn.get('seq') != a + i for i, turn in enumerate(value))):
            return None
        return value

    def window(self, a, b) -> list:
        """Turns a..b-1 (positions are the dense seq)."""
        a, b = max(0, a), min(b, len(self))
        if a >= b:
            return []
        if self.whole is None:
            found = self._window(a, b)
            if found is not None:
                return found
            self._parse_whole()
        return self.whole[a:b]

    def newest_first(self, step=32):
        end = len(self)
        while end > 0:
            start = max(0, end - step)
            yield from reversed(self.window(start, end))
            end = start


def _call(ref, index, tool) -> dict:
    tool = tool if isinstance(tool, dict) else {}
    call_ref = f'{ref}_{index}'
    child = tool.get('child') if isinstance(tool.get('child'), str) else None
    return {'callRef': call_ref, 'block': index, 'id': call_ref,
            'name': tool.get('name') if isinstance(tool.get('name'), str) and tool['name'] else 'unknown tool',
            'status': TOOL_STATUS.get(tool.get('status'), 'pending-or-missing'),
            'previewData': archived_preview_data(tool.get('preview')), 'inputBytes': None, 'outputBytes': None,
            'time': None, 'resultTime': None, 'messageId': None, 'partIndex': None, 'childSessionId': None,
            'childKey': child}


def _event(identity, seq, turn) -> dict:
    """One turn as a schema-1 conversation row (§3.8): read-only, its tools as preview-only calls."""
    ref = f'h_{identity}_{seq}'
    role = turn.get('role') if isinstance(turn.get('role'), str) else 'unknown'
    origin = turn.get('origin') if isinstance(turn.get('origin'), str) else None
    text = turn.get('text') if isinstance(turn.get('text'), str) else ''
    tools = turn.get('tools') if isinstance(turn.get('tools'), list) else []
    return {'recordRef': ref, 'uuid': ref, 'seq': seq, 'kind': 'task' if origin == 'task-notification' else role,
            'scope': 'main', 'status': 'stored', 'text': text, 'textBytes': text_bytes(text), 'partial': False,
            'time': _seconds(turn.get('ts')), 'thinking': [], 'calls': [_call(ref, i, t) for i, t in enumerate(tools)],
            'orphanResults': [], 'toolResults': 0, 'isMeta': False, 'originKind': origin or role,
            'model': turn.get('model') if isinstance(turn.get('model'), str) else None,
            'effort': turn.get('effort') if isinstance(turn.get('effort'), str) else None,
            'agent': None, 'receipt': None}


def _opencode_info(member) -> dict:
    """The archived OpenCode session info (source_json verbatim), its id pinned to the stored one."""
    info = _json_value(member['source_json'])
    info = dict(info) if isinstance(info, dict) else {}
    if info.get('id') != member['id']:
        info['id'] = member['id']
    return info


class _Rows:
    """Sidebar rows for archived sessions, given every member of their units (the context): which sessions have
    archived children, and the OpenCode by_id that opencode_row walks (the context plus archived ancestors)."""

    def __init__(self, db, context):
        self.parents = {m['parent_key'] for m in context if m['parent_key']}
        self.by_id = {m['id']: _opencode_info(m) for m in context if m['backend'] == 'opencode'}
        missing, seen = self._parents(self.by_id.values()), set()
        while missing:
            seen |= missing
            marks = ','.join('?' * len(missing))
            found = [_opencode_info(m) for m in db.execute(
                f"SELECT id, source_json FROM sessions WHERE backend = 'opencode' AND id IN ({marks})",
                sorted(missing)).fetchall()]
            self.by_id.update((info['id'], info) for info in found)
            missing = self._parents(found) - seen
        self.by_id = {sid: info for sid, info in self.by_id.items() if SESSION_ID.fullmatch(sid)}

    def _parents(self, infos):
        return {i['parentID'] for i in infos if isinstance(i.get('parentID'), str)
                and SESSION_ID.fullmatch(i['parentID']) and i['parentID'] not in self.by_id}

    def row(self, m):
        """('claude' | 'opencode', row); ('opencode', None) for a parked warm fork, which History never draws."""
        handles = refs(m['key'], m['body_sha256'])
        fixed = {'storeRef': 'history', 'sessionRef': handles['sessionRef'], 'targetRef': None,
                 'fileEpoch': handles['fileEpoch'], 'scopes': [{'label': 'main', 'scopeRef': handles['scopeRef']}],
                 'lifecycle': 'ended', 'liveState': None, 'lastContext': None, 'successor': None,
                 'capabilities': _capabilities(m['key'] in self.parents)}
        if m['backend'] == 'opencode':
            info = self.by_id.get(m['id']) or _opencode_info(m)
            row = opencode_row(info, self.by_id)
            if row is None:
                return 'opencode', None
            row['busy'] = False
            row.update(fixed)
            # The row's own turn times, as the live sidebar reads them per row (/api/opencode/turns): History
            # carries them on the row so nothing live is read for an archived session.
            row['turnFacts'] = _json_value(m['turn_facts_json'])
        else:
            row = self._claude(m, fixed)
        row['archived'] = {'key': m['key'], 'archivedAt': _seconds(m['archived_at']), 'prunedAt': _seconds(m['pruned_at'])}
        row['telemetry'] = _stored_object(m['telemetry_json'])
        return m['backend'], row

    @staticmethod
    def _claude(m, fixed):
        source = _json_value(m['source_json'])
        source = source if isinstance(source, dict) else {}
        created = _seconds(m['created_at'])
        agent = SUBAGENT_ID.fullmatch(m['id']) if m['kind'] == 'subagent' else None
        if m['kind'] != 'subagent' and source.get('native') is not True and isinstance(source.get('execution_id'), str):
            # A host session: ClaudeFacade.sessions' row, its record fields from the same builder.
            fields = host_row_fields(source)
            row = {'storeRef': fixed['storeRef'], 'sessionRef': fixed['sessionRef'], 'targetRef': None,
                   'uuid': fields['uuid'], 'title': fields['title'], 'cwd': fields['cwd'],
                   'status': source.get('state', 'unknown'), 'fileEpoch': fixed['fileEpoch'], 'scopes': fixed['scopes'],
                   'source': 'host', 'role': fields['role'], 'executionId': fields['executionId'],
                   'generation': fields['generation'], 'createdAt': created, 'state': source.get('state', 'unknown'),
                   'liveState': None, 'capabilities': fixed['capabilities'], 'lifecycle': 'ended'}
            for name in ('displayRole', 'roleSource', 'managerSession', 'lineage', 'socket'):
                if name in fields:
                    row[name] = fields[name]
            row.update(successor=None, lastContext=None)
        else:
            # An unmanaged transcript (native row) or a subagent transcript (native-subagent row).
            uuid = m['native_uuid'] or (agent.group(1) if agent else m['id'][3:] if m['id'].startswith('cc-') else m['id'])
            cwd = source.get('cwd') if isinstance(source.get('cwd'), str) else m['cwd']
            row = {'storeRef': fixed['storeRef'], 'sessionRef': fixed['sessionRef'], 'targetRef': None, 'uuid': uuid,
                   'title': m['title'] or (m['id'] if agent else uuid), 'cwd': cwd or 'unknown', 'status': 'complete',
                   'fileEpoch': fixed['fileEpoch'], 'scopes': fixed['scopes'], 'capabilities': fixed['capabilities'],
                   'source': 'native'}
            if agent:
                row.update(parentUuid=source.get('parentUuid') or agent.group(1), agentId=source.get('agentId') or agent.group(2),
                           agentType=source.get('agentType'), description=source.get('description'), role='subagent')
            else:
                row['role'] = 'history'
            row.update(executionId=None, generation=None, state='unmanaged-history', createdAt=created, liveState=None,
                       lifecycle='ended', successor=None, lastContext=None)
        claude_launch.apply_names([row], {row['uuid']: m['chosen_name']} if m['chosen_name'] else {})
        if row['source'] == 'native' and not agent and 'displayRole' not in row:
            role = folder_role(row['cwd'], bool(row.get('sessionLabel')))
            if role:
                row['displayRole'], row['roleSource'] = role, 'folder'
        row['turnFacts'] = _json_value(m['turn_facts_json'])
        return row


def _unit(u) -> dict:
    """One programs-page unit; `telemetry` is always null there ([ap:NF5UM1]: the member rows carry their own)."""
    core = u['program_core'] if isinstance(u['program_core'], str) and CORE.fullmatch(u['program_core']) else None
    return {'unit': u['unit_key'], 'kind': u['kind'], 'program': core, 'firstAt': _seconds(u['first_at']),
            'lastAt': _seconds(u['last_at']), 'sessions': u['sessions'], 'sealedAt': _seconds(u['sealed_at']),
            'costUsd': u['cost_usd'], 'telemetry': None}


def _members(db, unit_keys):
    marks = ','.join('?' * len(unit_keys))
    return db.execute(f'SELECT {SESSION_COLUMNS} FROM sessions WHERE unit_key IN ({marks}) '
                      'ORDER BY created_at DESC, key DESC', list(unit_keys)).fetchall()


def _encode_before(last_at, unit_key):
    return b64u(json.dumps([1, last_at, unit_key], separators=(',', ':')))


def _decode_before(token):
    try:
        data = json.loads(_unb64u(token)) if REF.fullmatch(token) else None
    except (ValueError, binascii.Error):
        data = None
    if (not isinstance(data, list) or len(data) != 3 or data[0] != 1 or type(data[1]) not in (int, float)
            or not isinstance(data[2], str)):
        raise _bad('before is not a History page cursor')
    return data[1], data[2]


def _query(path, query):
    names = ROUTES.get(path)
    if names is None:
        raise ReaderError(404, 'missing', 'unknown History route')
    try:
        q = parse_qs(query, strict_parsing=bool(query), keep_blank_values=True, max_num_fields=8)
    except ValueError:
        raise _bad('malformed History query') from None
    for name, values in q.items():
        if name not in names:
            raise _bad('unknown History parameter')
        if len(values) != 1 or not values[0] or len(values[0]) > PARAM_LIMIT:
            raise _bad('each History parameter at most once, non-empty, at most 4096 characters')
    return {name: values[0] for name, values in q.items()}


def _integer(value, name, low, high):
    if not re.fullmatch(r'[0-9]{1,16}', value) or not low <= int(value) <= high:
        raise _bad(f'{name} must be an integer {low}–{high}')
    return int(value)


# @beacon[
#   id=opc-history@HistoryArchive,
#   slice_labels=opc-history,
#   kind=ast,
#   comment=The /api/history/ reader (§3.8): opens the archive read-only on the first request (path from oc_history_settings at every request; absent = empty, never cached; unreadable or tableless = 503), one connection behind one lock, one read transaction per answer; rows from host_row_fields/opencode_row plus stored columns; bodies only for glance/events.,
# ]
class HistoryArchive:
    """The pane's one History reader. Nothing is opened until a /api/history/* request asks."""

    def __init__(self, settings=None):
        self._settings = settings or oc_history_settings.load
        self.lock = threading.Lock()
        self.connection = None
        self.opened = None                     # (path, st_dev, st_ino) of the file the connection reads
        self.stats = {'opens': 0, 'bodies': 0}

    # @beacon[
    #   id=opc-history@HistoryArchive.route,
    #   slice_labels=opc-history,
    #   kind=ast,
    #   comment=One strict query per History route (each parameter at most once, non-empty, ≤ 4096 characters, unknown = 400 bad-request; sessions takes exactly one of unit or keys ≤ 80; an events cursor needs fileEpoch and excludes fromSeq), then the route's answer as a dict.,
    # ]
    def route(self, path, query) -> dict:
        """Answer one GET /api/history/* request (the server applies the origin guard and GET-only first)."""
        args = _query(path, query)
        if path == '/api/history/programs':
            before = args.get('before')
            limit = _integer(args['limit'], 'limit', 1, MAX_UNITS) if 'limit' in args else PAGE_UNITS
            return self.programs(_decode_before(before) if before is not None else None, limit)
        if path == '/api/history/sessions':
            if ('unit' in args) == ('keys' in args):
                raise _bad('sessions takes exactly one of unit or keys')
            if 'unit' in args:
                if not UNIT_KEY.fullmatch(args['unit']):
                    raise _bad('unit is not a History unit key')
                return self.sessions(unit=args['unit'])
            keys = args['keys'].split(',')
            if len(keys) > MAX_KEYS or len(set(keys)) != len(keys) or not all(SESSION_KEY.fullmatch(k) for k in keys):
                raise _bad(f'keys takes 1–{MAX_KEYS} distinct session keys')
            return self.sessions(keys=keys)
        key = args.get('key')
        if key is None or not SESSION_KEY.fullmatch(key):
            raise _bad('key is not a History session key')
        if path == '/api/history/glance':
            return self.glance(key)
        epoch, cursor = args.get('fileEpoch'), args.get('cursor')
        if (epoch is not None and not REF.fullmatch(epoch)) or (cursor is not None and not REF.fullmatch(cursor)):
            raise _bad('fileEpoch and cursor are opaque base64url handles')
        if cursor is not None and (epoch is None or 'fromSeq' in args):
            raise _bad('cursor requires fileEpoch and excludes fromSeq')
        from_seq = _integer(args['fromSeq'], 'fromSeq', 0, SAFE_INTEGER) if 'fromSeq' in args else None
        return self.events(key, fileEpoch=epoch, fromSeq=from_seq, cursor=cursor)

    # ---- the archive file

    def _close(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except sqlite3.Error:
                pass
        self.connection, self.opened = None, None

    def _open(self):
        """The read-only connection to the configured archive, (re)opened when the file changed; None when absent."""
        path = self._settings().archive_path
        try:
            st = os.stat(path)
        except FileNotFoundError:
            self._close()
            return None
        except OSError as error:
            self._close()
            raise ReaderError(503, 'unavailable', 'the History archive cannot be read: ' + str(error)[:160]) from None
        identity = (path, st.st_dev, st.st_ino)
        if self.connection is not None and self.opened == identity:
            return self.connection
        self._close()
        try:
            connection = sqlite3.connect('file:' + quote(path) + '?mode=ro', uri=True, timeout=READ_TIMEOUT,
                                         check_same_thread=False, isolation_level=None)
            try:
                names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            except sqlite3.Error:
                connection.close()
                raise
        except sqlite3.Error as error:
            raise ReaderError(503, 'unavailable', 'the History archive cannot be read: ' + str(error)[:160]) from None
        missing = [name for name in TABLES if name not in names]
        if missing:
            connection.close()
            raise ReaderError(503, 'unavailable', 'the History archive lacks its tables: ' + ', '.join(missing))
        connection.row_factory = sqlite3.Row
        self.connection, self.opened = connection, identity
        self.stats['opens'] += 1
        return connection

    @contextmanager
    def _read(self):
        """One read transaction on the archive under the lock (None when the archive file is absent)."""
        with self.lock:
            db = self._open()
            if db is None:
                yield None
                return
            try:
                db.execute('BEGIN')
                yield db
                db.execute('COMMIT')
            except sqlite3.Error as error:
                self._close()
                raise ReaderError(503, 'unavailable', 'the History archive cannot be read: ' + str(error)[:160]) from None
            finally:
                if self.connection is not None and self.connection.in_transaction:
                    try:
                        self.connection.execute('ROLLBACK')
                    except sqlite3.Error:
                        self._close()

    def _turns(self, blob, codec):
        """Decode one stored body (§3.4: zlib of canonical JSON); an unreadable one is the archive's 503."""
        self.stats['bodies'] += 1
        try:
            if codec != 'zlib-json':
                raise ValueError('unknown body codec')
            return _Turns(zlib.decompress(blob))
        except (zlib.error, ValueError, TypeError, RecursionError):
            raise ReaderError(503, 'unavailable', 'this archived conversation cannot be read') from None

    @staticmethod
    def _session(db, key):
        """The session's stored row and its body; 404 missing when the archive has no such key."""
        member = db.execute(f'SELECT {SESSION_COLUMNS} FROM sessions WHERE key = ?', (key,)).fetchone()
        if member is None:
            raise ReaderError(404, 'missing', 'no archived session has this key')
        body = db.execute('SELECT codec, body FROM bodies WHERE key = ?', (key,)).fetchone()
        if body is None:
            raise ReaderError(503, 'unavailable', 'this archived conversation has no stored body')
        return member, body['codec'], body['body']

    # ---- the four routes

    # @beacon[
    #   id=opc-history@HistoryArchive.programs,
    #   slice_labels=opc-history,
    #   kind=ast,
    #   comment=GET /api/history/programs: one page of whole units newest first by (last_at DESC, unit_key DESC) through §3.3's index, a keyset nextBefore; every drawn member of each unit in claude/opencode, units holding only parked warm forks skipped while the page still fills; stored columns only, no body; each unit's telemetry is null ([ap:NF5UM1]) and each member row's stored telemetry passes through undecoded (encode_answer); total counts the units and sessions tables.,
    # ]
    def programs(self, after=None, limit=PAGE_UNITS) -> dict:
        """A page of units after the keyset position `after` = (last_at, unit_key)."""
        with self._read() as db:
            if db is None:
                return _answer('empty', units=[], claude=[], opencode=[], hasMore=False, nextBefore=None,
                               total={'units': 0, 'sessions': 0}, archive={'lastSweepAt': None})
            units, claude, opencode, more, last = [], [], [], False, None
            while len(units) < limit:
                scan = max(limit - len(units), SCAN_UNITS)
                if after is None:
                    batch = db.execute(f'SELECT {UNIT_COLUMNS} FROM units ORDER BY last_at DESC, unit_key DESC LIMIT ?',
                                       (scan + 1,)).fetchall()
                else:
                    batch = db.execute(f'SELECT {UNIT_COLUMNS} FROM units WHERE (last_at, unit_key) < (?, ?) '
                                       'ORDER BY last_at DESC, unit_key DESC LIMIT ?', (*after, scan + 1)).fetchall()
                examine, more = batch[:scan], len(batch) > scan
                if not examine:
                    break
                members = _members(db, [u['unit_key'] for u in examine])
                rows, grouped = _Rows(db, members), {}
                for m in members:
                    grouped.setdefault(m['unit_key'], []).append(m)
                for i, u in enumerate(examine):
                    after = (u['last_at'], u['unit_key'])
                    drawn = {'claude': [], 'opencode': []}
                    for m in grouped.get(u['unit_key'], ()):
                        backend, row = rows.row(m)
                        if row is not None:
                            drawn[backend].append(row)
                    if drawn['claude'] or drawn['opencode']:
                        units.append(_unit(u))
                        claude += drawn['claude']
                        opencode += drawn['opencode']
                        last = after
                        if len(units) == limit:
                            more = more or i < len(examine) - 1
                            break
                if not more:
                    break
            counts = db.execute('SELECT (SELECT COUNT(*) FROM units), (SELECT COUNT(*) FROM sessions)').fetchone()
            sweep = db.execute("SELECT value FROM meta WHERE key = 'last_sweep_at'").fetchone()
        sweep_ms = int(sweep['value']) if sweep and re.fullmatch(r'[0-9]{1,16}', str(sweep['value'])) else None
        more = more and len(units) == limit
        return _answer('complete', units=units, claude=claude, opencode=opencode, hasMore=more,
                       nextBefore=_encode_before(*last) if more else None,
                       total={'units': counts[0], 'sessions': counts[1]}, archive={'lastSweepAt': _seconds(sweep_ms)})

    # @beacon[
    #   id=opc-history@HistoryArchive.sessions,
    #   slice_labels=opc-history,
    #   kind=ast,
    #   comment=GET /api/history/sessions?unit= or ?keys=: one whole unit's rows, or up to 80 sessions' rows (built with their units as context so attribution matches the page), plus missing for keys (or the unit) the archive lacks; parked warm forks have no row and are not missing; never a body.,
    # ]
    def sessions(self, unit=None, keys=None) -> dict:
        with self._read() as db:
            if db is None:
                return _answer('empty', claude=[], opencode=[], missing=[])
            if unit is not None:
                wanted = context = _members(db, [unit])
                missing = [] if wanted or db.execute('SELECT 1 FROM units WHERE unit_key = ?', (unit,)).fetchone() else [unit]
            else:
                marks = ','.join('?' * len(keys))
                found = {m['key']: m for m in db.execute(
                    f'SELECT {SESSION_COLUMNS} FROM sessions WHERE key IN ({marks})', keys).fetchall()}
                wanted = [found[k] for k in keys if k in found]
                missing = [k for k in keys if k not in found]
                context = _members(db, sorted({m['unit_key'] for m in wanted})) if wanted else []
            rows, drawn = _Rows(db, context), {'claude': [], 'opencode': []}
            for m in wanted:
                backend, row = rows.row(m)
                if row is not None:
                    drawn[backend].append(row)
        return _answer('complete', claude=drawn['claude'], opencode=drawn['opencode'], missing=missing)

    # @beacon[
    #   id=opc-history@HistoryArchive.glance,
    #   slice_labels=opc-history,
    #   kind=ast,
    #   comment=GET /api/history/glance?key=: the /api/claude/glance shape from one archived body: the newest five turns with text (glance_messages, the live rule, 1,200 characters each), the last tool of the newest assistant turn that made calls (status stored/error/pending-or-missing, archived previewData), the newest model; 404 missing for an unknown key.,
    # ]
    def glance(self, key) -> dict:
        with self._read() as db:
            if db is None:
                return _answer('empty', key=key, messages=[], call=None, lastAt=None, model=None, partial=False)
            member, codec, blob = self._session(db, key)
        texts, call, model = [], None, None
        for turn in self._turns(blob, codec).newest_first():
            text, role = turn.get('text'), turn.get('role')
            if len(texts) < GLANCE_MESSAGES and isinstance(text, str) and text.strip():
                texts.append((role, _seconds(turn.get('ts')), text))
            if role == 'assistant':
                if model is None and isinstance(turn.get('model'), str):
                    model = turn['model']
                tools = turn.get('tools') if isinstance(turn.get('tools'), list) else []
                if call is None and tools:
                    last = _call('', 0, tools[-1])
                    call = {'name': last['name'], 'status': last['status'], 'previewData': last['previewData'],
                            'time': _seconds(turn.get('ts')), 'resultTime': None, 'open': False}
            if len(texts) >= GLANCE_MESSAGES and call is not None and model is not None:
                break
        return _answer('complete', key=key, messages=glance_messages(texts), call=call,
                       lastAt=_seconds(member['last_at']), model=model, partial=False)

    # @beacon[
    #   id=opc-history@HistoryArchive.events,
    #   slice_labels=opc-history,
    #   kind=ast,
    #   comment=GET /api/history/events?key=[&fileEpoch=][&fromSeq=|&cursor=]: a schema-1 conversation page shaped like /api/opencode/events: one row per turn, dense seq, ≤ 100 rows and 8 MiB (413, never cropped); no parameter = the newest page, fromSeq/cursor read forward from a position; a fileEpoch other than the body's is 409 stale-epoch.,
    # ]
    def events(self, key, fileEpoch=None, fromSeq=None, cursor=None) -> dict:
        handles = refs(key, '')
        with self._read() as db:
            if db is None:
                return self._envelope(handles, None, [], False, None, status='empty')
            member, codec, blob = self._session(db, key)
        handles = refs(key, member['body_sha256'])
        epoch, identity = handles['fileEpoch'], hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]
        if fileEpoch is not None and fileEpoch != epoch:
            raise ReaderError(409, 'stale-epoch', 'this conversation was archived again; resync it')
        position = fromSeq
        if cursor is not None:
            try:
                data = json.loads(_unb64u(cursor))
            except (ValueError, binascii.Error):
                data = None
            if (not isinstance(data, list) or len(data) != 4 or data[0] != 1 or data[1] != identity
                    or data[2] != fileEpoch or type(data[3]) is not int or data[3] < 0):
                raise _bad('cursor is not a History cursor of this conversation and fileEpoch')
            position = data[3]
        turns = self._turns(blob, codec)
        total = len(turns)
        if cursor is not None and position > total:
            raise _bad('cursor is beyond this conversation')
        info = self._session_info(member)
        widest = self._envelope(handles, info, [], True, self._cursor(identity, epoch, total))
        used = len(encoded(widest))
        start = max(0, total - PAGE_ROWS) if position is None else min(position, total)
        window = turns.window(start, min(total, start + PAGE_ROWS))
        rows = [_event(identity, start + i, turn) for i, turn in enumerate(window)]
        selected = []
        for row in (reversed(rows) if position is None else rows):
            size = len(encoded(row)) + (1 if selected else 0)
            if used + size > RESPONSE_LIMIT:
                break
            selected.append(row)
            used += size
        if rows and not selected:
            raise ReaderError(413, 'response-limit', 'an archived turn exceeds the 8 MiB response; not cropped')
        if position is None:
            selected.reverse()
            boundary = total
        else:
            boundary = start + len(selected)
        more = boundary < total
        return self._envelope(handles, info, selected, more, self._cursor(identity, epoch, boundary) if more else None)

    @staticmethod
    def _cursor(identity, epoch, position):
        return b64u(json.dumps([1, identity, epoch, position], separators=(',', ':')))

    @staticmethod
    def _session_info(member):
        source = _json_value(member['source_json'])
        source = source if isinstance(source, dict) else {}
        parent = member['parent_key'].split(':', 1)[1] if isinstance(member['parent_key'], str) and ':' in member['parent_key'] else None
        if member['backend'] == 'opencode':
            model = source.get('model').get('id') if isinstance(source.get('model'), dict) else None
            location = source.get('location') if isinstance(source.get('location'), dict) else {}
            title, agent = source.get('title'), source.get('agent')
            directory = location.get('directory', source.get('directory'))
            parent = source.get('parentID') if isinstance(source.get('parentID'), str) else parent
        else:
            models = _json_value(member['models_json'])
            model = models[-1] if isinstance(models, list) and models and isinstance(models[-1], str) else None
            title, agent, directory = member['title'], None, member['cwd']
        def text(value):
            return value if isinstance(value, str) else None
        return {'id': member['id'], 'title': text(title) or member['id'], 'agent': text(agent), 'model': text(model),
                'directory': text(directory), 'parentId': text(parent)}

    @staticmethod
    def _envelope(handles, info, events, more, next_cursor, status='complete'):
        return {'schemaVersion': 1, 'backend': 'history', 'source': 'history', 'sessionRef': handles['sessionRef'],
                'scopeRef': handles['scopeRef'], 'fileEpoch': handles['fileEpoch'] if info is not None else None,
                'events': events, 'hasMore': more, 'nextCursor': next_cursor, 'deltaCursor': None, 'rowsAhead': 0,
                'leafRef': None, 'targetRef': None, 'indexLimit': None, 'branches': [], 'traces': [],
                'summaryUpdates': [], 'subagentActivity': [], 'diagnostics': [], 'eventUpdates': [], 'status': status,
                'change': 'snapshot', 'busy': False, 'runningCalls': [], 'observedAt': None, 'sessionInfo': info}
