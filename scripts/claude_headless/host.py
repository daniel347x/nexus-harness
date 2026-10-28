"""One native owner/writer; durable supervision, never an execution/silence deadline."""
import asyncio
from collections import deque
from datetime import datetime, timezone
import fcntl
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import struct
import time
import uuid

from .store import FORGET_TABLES, JUNK_TABLES, Store, private_dir, private_file
from .wire import PARSE_LIMIT, array_items_raw, member_raw, read_packet, write_packet
from .supervision import Supervisor, clear_stale_native_markers, usage_lockout, user_origin
from .settlement import RECONNECT_POLICY, native_turn_over, owner_proved_gone
from .rows import SqliteRows, RowProjector, RowsSource, project_through, backfill_rows_page, ROW_PAGE_EVENTS
from .root_search import judge as judge_root_search

STATE_ROOT = Path.home() / '.local/state/opencode-companion/claude-headless'
SOCKET_PATH = Path(f'/tmp/opencode/cc-{os.getuid()}/host.sock')
ROW_SLICE_MS = 20
ROW_COMMIT_HEADROOM_MS = 10  # FULL-sync commits/checkpoints are part of the measured page, not free work.
ROW_YIELD_TURNS = 8  # Let socket accept/read/dispatch/write finish before another projection slice.
LOOP_LAG_TICK_SECONDS = 1
LOOP_LAG_WINDOW_SECONDS = 60
KNOWN_TYPES = {'user', 'assistant', 'result', 'system', 'stream_event', 'control_request',
               'control_response', 'control_cancel_request', 'command_lifecycle',
               'rate_limit_event', 'tool_progress', 'tool_use_summary', 'auth_status'}
ROOT_SEARCH_HOOK = 'root-search-guard'
INITIALIZE_HOOKS = {'PreToolUse': [{'matcher': 'Bash|Grep|Glob', 'hookCallbackIds': [ROOT_SEARCH_HOOK],
                                    'timeout': 30}]}
"""The host's own SDK hook, registered by every launch's initialize: Claude Code then sends every Bash, Grep
and Glob call (auto-approved ones included) as a hook_callback with the folder its shell stands in, before
any permission check (NativeSession.hook_callback, docs/root-search-guard.md). A later initialize without
`hooks` (the collector's readback) keeps it; one with `hooks` replaces it. Callback hooks are not settings:
the effective-settings readback carries none. The host answers at once, so nothing waits on it; a host that
stopped answering for `timeout` (30 s) would make each such call fail with Claude Code's "PreToolUse hook did
not respond before its timeout" error (the call does not run; the turn goes on). Nothing hangs either way.
NativeSession.root_search_witness checks on every generation that the hook is really called."""
WITNESSED_TOOLS = ('Bash', 'Grep', 'Glob')
GLOB_IGNORE_ENV = 'CLAUDE_CODE_GLOB_NO_IGNORE'  # 'false' in a generation's settings env: its Glob honours .gitignore
ROOT_SEARCH_FLAGS = {
    'missing_callback': 'the root-search hook was not called on Claude Code {version}: a {tool} call ran unseen by '
                        'the guard, so searches Claude Code approves itself are unguarded. Nothing was refused; tell Dan',
    'cwd_missing': 'the root-search hook got no folder (cwd) on Claude Code {version}, so relative searches are not '
                   'judged. Nothing was refused; tell Dan',
    'deny_ignored': "the root-search hook's deny was ignored on Claude Code {version}: a refused {tool} call ran "
                    'anyway. Tell Dan',
}
"""What each witness flag tells its reader (incident detail `reason`, which `oc_seat wait` prints)."""


def fresh_root_search_witness(generation):
    """A generation's root-search hook witness before its first Bash/Grep/Glob call (NativeSession.root_search_witness)."""
    return {'generation': generation, 'seen_answering': False, 'answered': 0, 'denied': 0, 'results_checked': 0,
            **{name: 0 for name in ROOT_SEARCH_FLAGS}, 'flags': {}}


def native_id(value):
    if value.startswith('cc-'):
        value = value[3:]
    canonical = str(uuid.UUID(value))
    if canonical != value:
        raise ValueError('use an exact canonical UUID or cc-UUID')
    return canonical


def refuse_archived(record):
    """One refusal for every way to resume or fork a forgotten conversation."""
    archived = record.get('archived') if record else None
    if archived:
        date = datetime.fromtimestamp(archived['at'] / 1000, timezone.utc).isoformat(timespec='seconds')
        raise ValueError(f"archived on {date}; its conversation is in 📦 History ({archived['key']})")


LABEL_KEY = re.compile(r'[a-z][a-z0-9_]{0,63}')


# @beacon[
#   id=opc@validate_labels,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The optional immutable provenance a seat script stamps at launch (title, kind, quick, manager_session, conductor id/n, seat_base): lowercase keys, JSON scalars; resume never changes it.,
# ]
def validate_labels(labels):
    """Optional launch provenance for seats started by the oc_* scripts. None means none. At most 32
    lowercase identifier keys; values are strings (at most 2000 characters), numbers, booleans or null.
    Immutable: a resume keeps the stored labels and refuses different ones."""
    if labels is None:
        return None
    if not isinstance(labels, dict) or len(labels) > 32:
        raise ValueError('labels must be an object with at most 32 entries')
    clean = {}
    for key, value in labels.items():
        if not isinstance(key, str) or not LABEL_KEY.fullmatch(key):
            raise ValueError('label keys must be lowercase identifiers (a-z, 0-9, _; at most 64 characters)')
        if isinstance(value, str):
            if len(value) > 2000:
                raise ValueError('label values are at most 2000 characters')
        elif not (value is None or type(value) in (bool, int) or
                  (type(value) is float and math.isfinite(value))):
            raise ValueError('label values must be strings, finite numbers, booleans or null')
        clean[key] = value
    return clean


def released_receipt(receipt):
    """An unfinished input that no longer gates Send: a recovery continue superseded it, a
    reconnect proved its process gone (a new process can never answer it), or an operator released
    it with a disposition (release_input), or closed-turn evidence proved it stale. Its effects
    stay uncertain and it is never replayed."""
    return bool(receipt.get('superseded_by_recovery') or receipt.get('released_by_reconnect')
                or receipt.get('released_by_operator') or receipt.get('released_as_stale'))


def witness(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
        fields = stat[stat.rfind(')') + 2:].split()
        if fields[0] == 'Z':
            return None
        return {'pid': pid, 'start_ticks': fields[19],
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    except (FileNotFoundError, ProcessLookupError):
        return None


def owner_lock(path, inherited_fd=None):
    """The existing singleton/UUID lock, optionally adopting the launcher's same open FD."""
    if inherited_fd is not None:
        actual, expected = os.fstat(inherited_fd), Path(path).stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError('inherited owner FD is not this namespace lock')
        fcntl.flock(inherited_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return inherited_fd
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise RuntimeError('native UUID already owned (owner lock held)') from None
    return fd


def safe_auth(value):
    """The auth facts the preflight judges and reports; no email, account id or credential value."""
    auth = {key: value.get(key) for key in ('loggedIn', 'authMethod', 'apiProvider', 'subscriptionType')}
    auth['apiKey'] = bool(value.get('apiKeySource') or value.get('apiKey'))
    return auth


def subscription_problems(auth):
    """What stands between this native login and a Max-only launch (an empty list = admitted).

    The property is real: no API billing. So exactly what matters is checked: logged in, the
    first-party provider, no API key in play, and a Max subscription (``'max' in subscriptionType``,
    as initialize confirms it). Any other field, or a new spelling of one (a renamed auth method, a
    ``max_20x`` tier), is not a refusal."""
    problems = []
    if auth.get('loggedIn') is not True:
        problems.append('not logged in')
    if auth.get('apiProvider') != 'firstParty':
        problems.append('API provider is not firstParty')
    method = str(auth.get('authMethod') or '').lower()
    if auth.get('apiKey') or ('api' in method and 'key' in method):
        problems.append('an API key is in play (API billing)')
    if 'max' not in str(auth.get('subscriptionType') or '').lower():
        problems.append('the subscription is not Max')
    return problems


# @beacon[
#   id=opc@subscription_preflight,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Check native Max auth using the same immutable selected login folder as child launch after stripping inherited auth overrides; retains no credentials or email.,
# ]
async def subscription_preflight(account=None):
    """Read only native auth status; retain no email, account IDs, credentials or env values."""
    from .launch import CLI, sanitize_environment
    sanitized = sanitize_environment(dict(os.environ))
    env = sanitized[0] if isinstance(sanitized, tuple) else sanitized
    from .accounts import selected_account, account_environment
    env = account_environment(env, selected_account() if account is None else account)
    process = await asyncio.create_subprocess_exec(CLI, 'auth', 'status', '--json', env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await process.communicate()  # no host deadline
    try:
        auth = safe_auth(json.loads(out))
    except (ValueError, UnicodeError, AttributeError):
        raise RuntimeError(f'native auth status unavailable (exit {process.returncode})') from None
    problems = subscription_problems(auth)
    if problems:
        raise RuntimeError('Max-only launch refused: ' + '; '.join(problems) + ' (observed: '
                           + ', '.join(f'{key}={value!r}' for key, value in auth.items())
                           + f', exit {process.returncode})')
    version = await asyncio.create_subprocess_exec(CLI, '--version', env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await version.communicate()
    auth['cli_version'] = out.decode('utf-8', errors='replace').strip() if not version.returncode else 'unknown'
    return auth


# @beacon[
#   id=opc@own_turn_busy,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Ordinary Send gate on the session's OWN turn only: native running/requires_action blocks unless the primary result already closed the turn (then it is background agents/tasks, which never block Send). Unknown state still withholds. A steer waives this gate (Claude Code queues it for the next tool step).,
# ]
def own_turn_busy(record):
    """Plain-words reason the Manager's own turn blocks a new message, or None.

    Native ``session_state`` stays ``running`` (and flips to ``requires_action`` for a
    child's auto-answered permission prompts) for as long as background agents or tasks
    work, even after the primary turn's ``result``. ``primary_turn_open`` (primary
    ``system/init`` opens a turn, primary ``result`` closes it) is the witness that tells
    them apart; a generation that has not closed a turn yet keeps the conservative gate."""
    native = record.get('native_session_state')
    closed = record.get('primary_turn_open') is False
    if native in ('running', 'requires_action') and not closed:
        return ('the Manager is waiting for a permission answer' if native == 'requires_action'
                else 'the Manager is working on its own turn')
    if record.get('native_state_required') and native not in ('idle', 'running', 'requires_action'):
        return "the Manager's current state is not known yet"
    return None


def fork_blockers(record, *, self_call=False):
    """Plain-words reasons a base's conversation cannot be forked now (empty = its own turn is closed).

    A fork copies the base's transcript as it stands, so the base must not be starting, working on its own
    turn, holding an input that awaits its result, recovering, or of unknown ownership. A base whose
    process has ended (collected, closed) is forkable; background tasks after its turn closed are not a
    blocker. An idle live base is forkable too (Part A, T10).

    ``self_call``: the base is a Manager forking itself from inside its own turn (Host.fork_base has proved
    it). That open turn, and the input it answers, are the conversation being copied, so they are not
    blockers; starting, a pending recovery and unknown ownership still are."""
    reasons = []
    if record.get('owner') == 'unknown' or record.get('state') == 'ownership_unknown':
        reasons.append("the base's ownership is unknown")
    if record.get('state') in ('launching', 'initializing'):
        reasons.append('the base is still starting')
    if not self_call:
        if record.get('pending_message_uuids'):
            reasons.append('the base has an input awaiting its result')
        if record.get('primary_turn_open'):
            reasons.append('the base is working on its own turn')
        elif (record.get('owner') in ('host', 'draining') and record.get('primary_turn_open') is not False
              and record.get('native_session_state') in ('running', 'requires_action')):
            reasons.append('the base is working')
    if record.get('pending_recovery'):
        reasons.append('the base has a pending recovery')
    return reasons


def fork_call(record):
    """The base's last completed primary model call, kept on a fork's record at launch: what the fork's first
    call should read back from the prompt cache (oc_claude's HIT/MISS line)."""
    call = record.get('last_completed_model_call') or {}
    if not call.get('usage'):
        return None
    return {'usage': dict(call['usage']), 'model': call.get('model'), 'measured_at': call.get('measured_at'),
            'generation': call.get('generation')}


LIST_SUMMARY_OMITS = ('launch', 'synthetic_results', 'native_effective_settings', 'tools', 'mcp_servers',
                      'result_identities')
"""The bulky detail fields `list(summary=True)` leaves out: launch argv and paths, the native tool and MCP lists
and effective-settings dumps (result copies no longer live in records: they are rows of their own, Store.results;
the two names stay for a record an older host wrote). State, settlement, routing, labels, quota, rate-limit and
context fields all stay; `state` always answers the full record."""

_AP_CORE = re.compile(r'\[ap:([A-Za-z0-9]+)\]')

LIST_WINDOW_DAYS = 14
"""Dan's ruling ([ap:WPWZ14]): `list` answers sessions whose most recent turn is within the last two weeks, plus
anything live or busy regardless of age. `all: true` answers everything, `window_days: N` another window, and
`execution_ids` (asking by id) every named session; `state` always answers any one session."""


def list_recency(record):
    """The newest moment the record knows of: its last output, input, generation start or first launch (None = no
    timestamp at all, which is never taken for old)."""
    stamps = [record.get(key) for key in ('last_activity_at', 'last_input_at', 'context_generation_started_at',
                                          'first_launched_at')]
    stamps = [value for value in stamps if type(value) in (int, float) and math.isfinite(value)]
    return max(stamps) if stamps else None


def list_kept_regardless_of_age(record):
    """Live or busy: an owner not proved gone, an input awaiting its result, or a pending recovery."""
    from .settlement import owner_proved_gone
    return bool(not owner_proved_gone(record) or record.get('pending_message_uuids') or record.get('pending_recovery'))


LIST_ENDED_STATES = ('exited', 'exited_draining', 'ownership_unknown', 'launch_failed', 'collected')
"""The states the pane's ClaudeFacade._ended calls ended (beside an owner gone or draining, or an exit code)."""


def list_live(record):
    """``list(live=True)``'s rule: every record the pane does not call ended (ClaudeFacade._ended: an ended state, an
    owner gone or draining, or an exit code), plus every one :func:`list_kept_regardless_of_age` keeps. A superset of
    the pane's live rows, never a subset: the pane re-filters, and a subset would hide a row."""
    ended = (record.get('state') in LIST_ENDED_STATES or record.get('owner') in ('gone', 'draining')
             or record.get('exit_code') is not None)
    return not ended or list_kept_regardless_of_age(record)


_PROGRAM_HANDLE = re.compile(r'\[ap:([A-Z0-9]{6})\]')
_PROGRAM_CORE = re.compile(r'[A-Z0-9]{6}')


def list_program(record):
    """The record's program core by the pane's own rule (ClaudeFacade._lineage): ``program`` that is exactly
    ``[ap:CORE]`` (six capitals or digits), else the launch label ``program_core``; None when neither is a core."""
    labels = record.get('labels') if isinstance(record.get('labels'), dict) else {}
    program = record.get('program') if isinstance(record.get('program'), str) else ''
    found = _PROGRAM_HANDLE.fullmatch(program.strip())
    core = found.group(1) if found else labels.get('program_core')
    return core if isinstance(core, str) and _PROGRAM_CORE.fullmatch(core) else None


# @beacon[
#   id=opc@list_view,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The host list's ONE window and its modes: by default the last two weeks (LIST_WINDOW_DAYS) plus anything live or busy; a newly archived tombstone is visible for that window without changing its historical activity; all or window_days widen it; execution_ids and programs and live select at any age instead; summary drops bulky fields and socket narrows. Unknown parameters are ignored, so callers still filter.,
# ]
def list_view(records, params, now=None, answer=None):
    """``list``'s window and optional modes over the records it would return.

    By default only records whose most recent turn (:func:`list_recency`) is within ``LIST_WINDOW_DAYS``, plus every
    live or busy one (:func:`list_kept_regardless_of_age`). A tombstone's archive time also keeps it visible for
    that window, without changing the conversation's activity timestamps. ``all: true`` keeps every record; ``window_days`` (a
    positive number) sets another window. Three selectors answer at any age instead of a window, together every
    record any of them names: ``execution_ids`` (list of cc-ids) exactly those; ``programs`` (list of program cores,
    ``CORE`` or ``[ap:CORE]``) every member of those programs (:func:`list_program`); ``live: true`` every record
    that is not ended (:func:`list_live`). ``summary`` (bool) omits LIST_SUMMARY_OMITS and marks the row ``summary:
    True``; ``socket`` (an AP handle; same AP core, else the same text) narrows the answer. Parameters this host does
    not know are ignored, and so is a malformed value (the default applies): callers re-filter, and an older host
    answered every full record. ``answer`` (the host's) turns each record kept into the row answered, so a narrow
    answer builds only its own rows."""
    wanted = params.get('execution_ids')
    wanted = {w for w in wanted if isinstance(w, str)} if isinstance(wanted, list) else None
    programs = params.get('programs')
    programs = ({found.group(1) if (found := _PROGRAM_HANDLE.fullmatch(p.strip())) else p.strip()
                 for p in programs if isinstance(p, str)} if isinstance(programs, list) else None)
    live = params.get('live') is True
    if wanted is not None or programs is not None or live:
        records = [r for r in records if (wanted is not None and r.get('execution_id') in wanted)
                   or (programs is not None and list_program(r) in programs) or (live and list_live(r))]
    elif params.get('all') is not True:
        days = params.get('window_days')
        if not (type(days) in (int, float) and math.isfinite(days) and days > 0):
            days = LIST_WINDOW_DAYS
        cutoff = (time.time() if now is None else now) - days * 86400
        records = [r for r in records if list_kept_regardless_of_age(r)
                   or (stamp := list_recency(r)) is None or stamp >= cutoff
                   or (r.get('archived') or {}).get('at', 0) >= cutoff * 1000]
    socket_handle = params.get('socket')
    if isinstance(socket_handle, str) and socket_handle.strip():
        core = _AP_CORE.search(socket_handle)
        key = core.group(1) if core else socket_handle.strip()
        records = [r for r in records if isinstance(r.get('socket'), str) and
                   ((m.group(1) if (m := _AP_CORE.search(r['socket'])) else r['socket'].strip()) == key)]
    if answer is not None:
        records = [answer(r) for r in records]
    if params.get('summary') is True:
        records = [{**{k: v for k, v in r.items() if k not in LIST_SUMMARY_OMITS}, 'summary': True} for r in records]
    return records


# @beacon[
#   id=opc@program_activity,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Read-only activity by program core over the records the host already holds: its newest moment (ms) and whole-session count, bounded by since; no SQL, no decode, no copy, nothing capped or refused.,
# ]
def program_activity(records, params=None, now=None):
    """``program_activity``'s answer: which program cores were active at or after ``since``.

    ``records`` is :meth:`Store.cached_sessions` (``(sid, record, committed JSON)``): each record is read as the cache
    holds it, never decoded or copied, so the answer costs one pass over what the host already has in memory and no
    SQL. A record's program core is :func:`list_program` (no core: skipped) and its newest moment
    :func:`list_recency` (epoch seconds, converted to whole milliseconds). A program's ``last_at`` is the newest
    moment of any of its sessions and ``sessions`` counts every member, whatever its age; the answer holds one row
    per program whose ``last_at`` is at or after ``since`` (epoch milliseconds), most recent first, ties by core. A
    program with no known moment supplies no activity evidence and is not answered (liveness is a separate read).
    ``since`` absent or malformed is ``0`` (every program with a moment);
    no parameter is ever refused. ``as_of`` is this answer's own moment, in milliseconds."""
    params = params if isinstance(params, dict) else {}
    since = params.get('since')
    if not (type(since) in (int, float) and math.isfinite(since)):
        since = 0
    programs = {}
    for _sid, record, _text in records:
        core = list_program(record)
        if core is None:
            continue
        row = programs.setdefault(core, {'core': core, 'last_at': None, 'sessions': 0})
        row['sessions'] += 1
        stamp = list_recency(record)
        if stamp is not None:
            last_at = int(stamp * 1000)
            if row['last_at'] is None or last_at > row['last_at']:
                row['last_at'] = last_at
    kept = [row for row in programs.values() if row['last_at'] is not None and row['last_at'] >= since]
    kept.sort(key=lambda row: (-row['last_at'], row['core']))
    return {'programs': kept, 'as_of': int((time.time() if now is None else now) * 1000)}


# @beacon[
#   id=opc@ClaudeNativeSession,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Owns one journaled CLI writer with native state/task projection, exact observation terminal receipts, account-labelled quota frames and proof-gated EOF; forwarded child records stay separate from primary context models and receipts.,
# ]
class NativeSession:
    def __init__(self, host, record, process, lock_fd):
        self.host, self.store = host, host.store
        self.record, self.process, self.lock_fd = record, process, lock_fd
        self.sid, self.generation = record['native_uuid'], record['generation']
        self.rows = SqliteRows(self.store, self.sid)
        self.writes = asyncio.Queue()  # intents are also durable; no subscriber queue
        self.command_lock = asyncio.Lock()
        self.control_start_lock = asyncio.Lock()
        self.tasks = []
        self.pending = set(record.get('pending_message_uuids', []))
        self.background_inputs = {}  # bounded tool-use correlation only; background_tasks owns task membership
        # Root-search guard: main-thread Bash calls between their tool_use and tool_result (any of them may
        # still move the shell's folder), the tool uses the PreToolUse hook already answered (deny | no_opinion,
        # bounded), and for the witness the main-thread Bash/Grep/Glob calls awaiting their tool_result.
        self.bash_in_flight = set()
        self.bash_done = set()  # this turn's finished ones, so a late assistant frame never re-opens one
        self.root_search_hooked = {}
        self.root_search_calls = {}
        self.infrastructure_error = None
        # While storage fails: bytes read and not retained, inputs never written, and whether the stdout parser
        # lost its place (clear_infrastructure_error journals all three and resyncs the parser).
        self.lost_bytes = {'stdout': 0, 'stderr': 0}
        self.withheld = set()
        self.stdout_broken = False
        self.stdout_resync = False  # False · 'boundary' (restart at the file end) · 'fragment' (and skip to a newline)
        self.stdout_at_boundary = True  # the last stdout chunk read, retained or not, ended with a newline
        self.control_tasks = set()
        self.context_revision = 0
        self.context_stream = None
        self.settled_since = None  # monotonic, never restored across host/process generations
        self.work_revision = 0
        self.frame_number = 0
        self.collection_in_progress = False
        self.settlement_probe = None
        self.close_revision = None
        self.close_settled_since = None

    def update(self, **values):
        self.record.update(values)
        from .settlement import settlement_blockers
        blockers = settlement_blockers(self.record, require_native=True,
            ignore_control=(self.settlement_probe or {}).get('request_id'))
        if blockers:
            self.settled_since = None
        elif self.settled_since is None:
            self.settled_since = self.host.monotonic()
        self.record['settled_age_seconds'] = (max(0, self.host.monotonic() - self.settled_since)
            if self.settled_since is not None else None)
        self.store.put('sessions', self.sid, self.record)

    def completed_call(self, call):
        """One completed primary model call: the context reading's basis; a fork's FIRST one is also kept
        (`fork_first_call`), the evidence its cache read is judged by against the base's last call."""
        self.update(last_completed_model_call=call)
        if self.record.get('forked_from') and not self.record.get('fork_first_call'):
            self.update(fork_first_call=call)

    def reset_settlement(self):
        self.work_revision += 1
        self.settled_since = None

    # @beacon[
    #   id=opc@NativeSession.close_input,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=The shared-predicate EOF admission point; durable escalation review survives collection, and native work arriving before the writer closes input revokes the authorization.,
    # ]
    def close_input(self, *, reason='manual', settled_age=None):
        from .settlement import settlement_blockers
        # Operator close waives advisory markers; collection also ignores durable escalation review.
        blockers = settlement_blockers(self.record, ignore_control=(self.settlement_probe or {}).get('request_id'),
                                       automatic=reason == 'collected')
        if blockers:
            raise RuntimeError('cannot close input with unsettled work: ' + '; '.join(blockers))
        self.event('input_close_authorized', {'reason': reason, 'settled_age_seconds': settled_age})
        self.close_revision = self.work_revision
        self.close_settled_since = self.settled_since
        self.update(state='input_closing', close_reason=reason, collected_settled_age_seconds=settled_age,
                    collection_proof=None)
        self.writes.put_nowait('close_input')

    def event(self, kind, detail=None, **raw):
        result = self.store.event(self.sid, self.generation, kind, detail, **raw)
        self.host.changed.set()
        return result

    def restore_committed(self):
        """After a rolled-back read the live record returns to the last committed one, so memory and the store agree
        again: nothing that read set (a cursor, an escalation key, a recovery id, an input marked answered) outlives
        its rollback, and inputs its lost frames may have answered stay pending, as clear_infrastructure_error says."""
        try:
            stored = self.store.get('sessions', self.sid)
            self.rows.reload()
        except Exception:
            return  # the store cannot even be read: the live state stays the only truthful surface
        if stored is not None:
            self.record.clear()
            self.record.update(stored)
            self.pending = set(stored.get('pending_message_uuids') or [])

    def failed_storage(self, error):
        if not self.infrastructure_error:
            self.record['state_before_infrastructure_error'] = self.record.get('state')
            self.record['infrastructure_error_at'] = time.time()
        self.infrastructure_error = type(error).__name__
        self.record['infrastructure_error'] = self.infrastructure_error
        self.record['state'] = 'infrastructure_error'
        try:
            self.host.supervisor.incident(self.record, 'infrastructure_error', 'escalate',
                detail={'failure': self.infrastructure_error, 'durable_acceptance': 'withheld'}, attention=True)
        except Exception:
            # When the store itself is down, the live state is the only truthful surface.
            self.record['incident_unpersisted'] = True
        self.host.changed.set()

    # @beacon[
    #   id=opc@NativeSession.clear_infrastructure_error,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=The release of a durability failure once storage works again: the clearing is journaled first (the probe), lost bytes and never-written inputs are counted, retained-but-unindexed bytes become a raw_reconciliation_gap, the stdout parser resyncs at the next frame and a live process gets one native readback. No automatic continue after a gap.,
    # ]
    def clear_infrastructure_error(self, disposition):
        """Resume durable retention after a storage failure, with the gap journaled, never hidden.

        Inputs the gap may have answered stay pending (uncertain): ``release_input`` dispositions them. An input
        the writer never wrote is marked ``withheld`` (the process never saw it; it is never sent later)."""
        prior = {'infrastructure_error': self.infrastructure_error or self.record.get('infrastructure_error'),
                 'since': self.record.get('infrastructure_error_at'),
                 'incident_unpersisted': bool(self.record.get('incident_unpersisted')),
                 'lost_bytes': dict(self.lost_bytes), 'withheld_message_uuids': sorted(self.withheld)}
        # The probe: storage must commit this record before anything is cleared.
        cursor = self.store.event(self.sid, self.generation, 'infrastructure_error_cleared',
                                  {**prior, 'disposition': disposition})
        gaps = self.host.index_raw_gaps(self.record)
        alive = self.process.returncode is None
        resync = ((('boundary' if self.stdout_at_boundary else 'fragment')
                   if self.stdout_broken or self.lost_bytes['stdout'] > 0 else False) if alive else False)
        for mid in sorted(self.withheld):
            receipt = self.store.get('receipts', self.sid, mid)
            if receipt and not receipt.get('written') and not receipt.get('completed'):
                receipt.update(status='withheld', withheld=True)
                self.store.put('receipts', self.sid, receipt, mid)
        gap = {**prior, 'cursor': cursor, 'raw_gaps': gaps, 'disposition': disposition}
        values = {'infrastructure_error': None, 'incident_unpersisted': None, 'infrastructure_error_at': None,
                  'state_before_infrastructure_error': None, 'retention_gap': gap}
        if gaps or resync or any(prior['lost_bytes'].values()):
            values['protocol_attention'] = True
        exit_finished = len(self.tasks) > 3 and self.tasks[3].done()  # exited() ran, skipping its completion
        if alive:
            values['state'] = self.record.get('state_before_infrastructure_error') or 'ready'
        elif not exit_finished:
            values.update(state='exited_draining', owner='draining')  # exited() completes it once drained
        self.infrastructure_error = None
        self.stdout_broken, self.stdout_resync = False, resync
        self.lost_bytes, self.withheld = {'stdout': 0, 'stderr': 0}, set()
        self.update(**values)
        if not alive and exit_finished:
            for mid in self.pending:
                receipt = self.store.get('receipts', self.sid, mid)
                if receipt and not receipt.get('completed'):
                    receipt['status'] = 'uncertain'
                    self.store.put('receipts', self.sid, receipt, mid)
            self.update(state='exited', owner='gone', exit_code=self.process.returncode,
                        pending_message_uuids=sorted(self.pending))
            self.event('process_drains_settled', {'exit_code': self.process.returncode})
            self.host.supervisor.exited(self, cursor)
        self.host.supervisor.incident(self.record, 'infrastructure_error_cleared', 'retain', cursor,
            sorted(self.pending), {**gap, 'message_replayed': False, 'automatic_recovery':
                'none; inputs the gap may have answered stay pending: inspect, then release_input or follow up'})
        if not alive and exit_finished:
            self.host.release_ended(self)  # its exit is journaled now; the stored record answers from here on
        if alive and self.record.get('initialized') and not self.collection_in_progress:
            task = asyncio.create_task(self.gap_readback())
            self.control_tasks.add(task)
            task.add_done_callback(self.control_tasks.discard)
        return gap

    async def gap_readback(self):
        """One native readback after a durability gap: its answer refreshes the session state the gap may have
        hidden and redelivers a permission request the process still waits on (the same broker answers it).
        Observation only, under the Send gate; never a close."""
        from .collection import READBACK_FRESH_SECONDS, start_readback
        async with self.command_lock:
            if self.collection_in_progress or self.process.returncode is not None:
                return
            try:
                probe = start_readback(self.host, self, 'gap_readback_requested')
                while (self.process.returncode is None and not probe.get('snapshot_cursor')
                       and self.host.monotonic() - probe['started'] <= READBACK_FRESH_SECONDS
                       and not (probe.get('response_at') and self.host.monotonic() - probe['response_at'] > 2)):
                    await asyncio.sleep(.05)  # observer cadence; no deadline on the native process
                self.event('gap_readback_observed', {'request_id': probe['request_id'],
                    'answered': bool(probe.get('response_frame')), 'valid': probe.get('valid'),
                    'session_state': probe.get('session_state'), 'snapshot': bool(probe.get('snapshot_cursor'))})
            except Exception as error:
                try:
                    self.event('gap_readback_failed', {'failure': type(error).__name__})
                except Exception:
                    pass
            finally:
                self.collection_in_progress = False
                self.settlement_probe = None
                self.update()

    def start(self):
        self.tasks = [asyncio.create_task(self.writer()), asyncio.create_task(self.stdout()),
                      asyncio.create_task(self.stderr()), asyncio.create_task(self.exited())]

    def enqueue(self, data, kind, detail=None):
        if self.infrastructure_error:
            raise RuntimeError('durability unavailable: input withheld')
        offset = self.store.append_bytes(self.sid, self.generation, 'stdin', data)
        cursor = self.event('stdin_intent', {'intent_kind': kind, **(detail or {})},
                            stream='stdin', offset=offset, length=len(data))
        self.writes.put_nowait((data, cursor, kind, detail or {}))
        return cursor

    async def writer(self):
        while True:
            item = await self.writes.get()
            if item is None:
                return
            if item == 'close_input':
                from .settlement import settlement_blockers
                blockers = settlement_blockers({**self.record, 'state': 'ready'},
                    ignore_control=(self.settlement_probe or {}).get('request_id'),
                    automatic=self.record.get('close_reason') == 'collected')
                if blockers or self.close_revision != self.work_revision:
                    self.event('input_close_revoked', {'reason': 'work changed before EOF', 'holds': blockers})
                    self.update(state='ready', close_reason=None, collected_settled_age_seconds=None,
                                collection_proof=None)
                    continue
                self.process.stdin.close()
                self.event('stdin_closed')
                if self.record.get('close_reason') == 'collected':
                    age = (max(0, self.host.monotonic() - self.close_settled_since)
                        if self.close_settled_since is not None else self.record.get('collected_settled_age_seconds'))
                    proof = {**(self.record.get('collection_proof') or {}), 'settled_age_seconds': age}
                    self.update(collection_proof=proof, collected_settled_age_seconds=age)
                    self.event('process_collected', proof)
                continue
            data, cursor, kind, detail = item
            if self.infrastructure_error:
                if kind == 'user':
                    self.withheld.add(detail['message_uuid'])  # never written: the process never saw it
                continue  # no input with false durable acceptance
            try:
                if kind == 'control':
                    control = self.store.get('controls', self.sid, detail['request_id']) or {}
                    if control.get('subtype') == 'stop_task':
                        binding = control.get('qualified_owner') or {}
                        task = (self.record.get('background_tasks') or {}).get(binding.get('task_id')) or {}
                        tool_use_id = task.get('tool_use_id')
                        if task and not tool_use_id:
                            from .observations import full_task_input
                            source, _ = full_task_input(self.host, self, {**task, 'task_id': binding.get('task_id')})
                            tool_use_id = source.get('tool_use_id') if source else None
                        if (binding.get('generation') != self.generation or binding.get('session') != self.record['execution_id']
                                or task.get('run_id') != binding.get('run_id') or tool_use_id != binding.get('tool_use_id')):
                            control.update(status='withheld', reason='exact task/run/tool-use changed before native stdin write')
                            self.store.put('controls', self.sid, control, detail['request_id'])
                            self.update(pending_control_requests=[rid for rid in self.record.get('pending_control_requests', [])
                                                                 if rid != detail['request_id']])
                            self.event('observer_stop_withheld', {'request_id': detail['request_id'], 'reason': control['reason']})
                            continue
                self.process.stdin.write(data)
                await self.process.stdin.drain()  # no control/send deadline
                with self.store.batch():  # the write's evidence is one transaction
                    self.event('stdin_written', {'intent_cursor': cursor, 'intent_kind': kind, **detail})
                    if kind == 'user':
                        receipt = self.store.get('receipts', self.sid, detail['message_uuid'])
                        receipt['written'] = True
                        self.store.put('receipts', self.sid, receipt, detail['message_uuid'])
                    elif kind == 'permission':
                        pending = dict(self.record.get('pending_native_requests') or {})
                        pending.pop(detail['request_id'], None)
                        self.update(pending_native_requests=pending)
            except (BrokenPipeError, ConnectionResetError):
                self.event('stdin_uncertain', {'intent_cursor': cursor, 'intent_kind': kind, **detail})
                if kind == 'user':
                    receipt = self.store.get('receipts', self.sid, detail['message_uuid'])
                    receipt['status'] = 'uncertain'
                    self.store.put('receipts', self.sid, receipt, detail['message_uuid'])
            except Exception as error:
                self.restore_committed()
                self.failed_storage(error)

    # @beacon[
    #   id=opc@NativeSession.stdout,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=The stdout drain: each read is appended and fsynced first, then every frame it completes is indexed and projected in ONE store transaction (Store.batch); a failure rolls that read's index back and its retained bytes become a gap once storage works again.,
    # ]
    async def stdout(self):
        # Bytes are disk-spooled before parsing. No readline/StreamReader limit, no UTF-8
        # decoding until a complete frame. Even >64 MiB frames are retained in full.
        # Everything one read produces (its frames' events, receipts, projections, the session record once) is
        # one transaction, committed after the raw append's fsync and before the next read.
        frame_start = 0
        tail = bytearray()
        frame_size = 0
        oversize = False
        skipping = False  # after a durability gap: the bytes up to the next newline belong to a broken frame
        while True:
            chunk = await self.process.stdout.read(65536)
            arrival = time.time()
            if not chunk:
                break
            self.stdout_at_boundary = chunk.endswith(b'\n')
            if self.infrastructure_error:
                self.lost_bytes['stdout'] += len(chunk)
                continue  # drain even after logging failure; visible data-loss state
            try:
                with self.store.batch():
                    offset = self.store.append_bytes(self.sid, self.generation, 'stdout', chunk)
                    if self.stdout_resync:
                        # Storage works again (clear_infrastructure_error): parse from the file's true end; when the
                        # gap ended mid-frame, the rest of that frame is retained as one unparsed fragment.
                        skipping, self.stdout_resync = self.stdout_resync == 'fragment', False
                        frame_start, frame_size, oversize = offset, 0, False
                        tail.clear()
                    self.host.supervisor.activity(self)
                    parts = chunk.split(b'\n')
                    for index, part in enumerate(parts):
                        complete = index < len(parts) - 1
                        segment = part + (b'\n' if complete else b'')
                        frame_size += len(segment)
                        if skipping:
                            if complete:
                                self.event('raw_resync_fragment', {'reason': 'the rest of a frame a durability gap '
                                    'broke; retained, never parsed', 'semantic_replay': False},
                                    stream='stdout', offset=frame_start, length=frame_size)
                                frame_start += frame_size
                                frame_size, skipping = 0, False
                            continue
                        if not oversize and frame_size <= PARSE_LIMIT:
                            tail.extend(segment)
                        else:
                            oversize = True
                            tail.clear()
                        if complete:
                            self.frame(bytes(tail) if not oversize else None, frame_start, frame_size,
                                       unsupported=oversize, arrival_time=arrival)
                            frame_start += frame_size
                            tail.clear()
                            frame_size = 0
                            oversize = False
            except Exception as error:
                self.stdout_broken = True
                self.restore_committed()
                self.failed_storage(error)
        if not self.infrastructure_error:
            try:
                with self.store.batch():
                    if frame_size and skipping:
                        self.event('raw_resync_fragment', {'reason': 'the unterminated rest of a frame a durability '
                            'gap broke; retained, never parsed', 'semantic_replay': False},
                            stream='stdout', offset=frame_start, length=frame_size)
                    elif frame_size:
                        self.frame(bytes(tail) if not oversize else None, frame_start, frame_size,
                                   unsupported=oversize, unterminated=True)
                    eof_cursor = self.event('stdout_eof')
                    self.update(stdout_eof=True)
                    if self.pending:
                        self.host.supervisor.incident(self.record, 'stdout_eof_unsettled', source_cursor=eof_cursor,
                            mids=sorted(self.pending), detail={'process_exit_proved': self.process.returncode is not None,
                                'execution_action': 'none; await proved exit/drains, never EOF-only respawn'})
            except Exception as error:
                self.restore_committed()
                self.failed_storage(error)

    def frame(self, data, offset, length, unsupported=False, unterminated=False, arrival_time=None):
        raw_ref = {'stream': 'stdout', 'offset': offset, 'length': length,
                   'arrival_time': time.time() if arrival_time is None else arrival_time}
        if unsupported:
            self.event('unsupported_frame', {'reason': 'over 64 MiB parsing limit; raw retained',
                                             'unterminated': unterminated}, **raw_ref)
            self.update(protocol_attention=True)
            return
        try:
            text = data.decode('utf-8')
            event = json.loads(text)
            if not isinstance(event, dict):
                raise ValueError('frame must be object')
        except (UnicodeError, ValueError, RecursionError):
            self.event('malformed_frame', {'unterminated': unterminated}, **raw_ref)
            self.update(protocol_attention=True)
            return
        typ = event.get('type')
        kind = 'native_frame' if typ in KNOWN_TYPES else 'unknown_frame'
        cursor = self.event(kind, {key: event.get(key) for key in
            ('type', 'subtype', 'uuid', 'session_id', 'parent_tool_use_id')},
            _frame=data, _task_frame=event, **raw_ref)
        self.update(last_native_cursor=cursor)
        self.frame_number += 1
        if kind == 'unknown_frame' or unterminated:
            self.update(protocol_attention=True)
        if unterminated:
            self.event('unterminated_frame', {'frame_cursor': cursor})
        try:
            self.project(event, text, cursor)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
            self.event('protocol_attention', {'frame_cursor': cursor, 'error': type(error).__name__})
            self.update(protocol_attention=True)

    def track_tool_use(self, block):
        """One main-thread tool_use (stream start or assistant frame): a Bash may move the shell until its result,
        and every Bash/Grep/Glob call's result is checked against the root-search hook's answers (bounded)."""
        tool_id, name = block.get('id'), block.get('name')
        if not isinstance(tool_id, str) or name not in WITNESSED_TOOLS or tool_id in self.bash_done:
            return
        if name == 'Bash':
            self.bash_in_flight.add(tool_id)
        self.root_search_calls[tool_id] = name
        if len(self.root_search_calls) > 256:
            self.root_search_calls.pop(next(iter(self.root_search_calls)))

    # @beacon[
    #   id=opc@NativeSession.project,
    #   slice_labels=opc-claude-seats,
    #   kind=ast,
    #   comment=Projects exact primary turn and task/run lifecycles while isolating Agent scopes; retains observation terminal evidence independently of registry removal, and never equates waiter exit with watched-worker completion. Tracks main-thread Bash calls between tool_use and tool_result for the root-search hook, and checks every main-thread Bash/Grep/Glob result against the hook's answers (root_search_witness).,
    # ]
    def project(self, event, text, cursor):
        typ = event.get('type')
        # Native Agent emits child frames on the PRIMARY stdout too. Retain them in
        # the lossless feed, but never let child usage/model/results/echoes mutate the
        # Manager's own context, receipt, history leaf or applied-model witness.
        task_lifecycle = typ == 'system' and event.get('subtype') in (
            'task_started', 'task_updated', 'task_progress', 'task_notification', 'background_tasks_changed')
        if (typ == 'system' and event.get('subtype') == 'background_tasks_changed' and
                (event.get('parent_tool_use_id') is not None or event.get('isSidechain') is True)):
            # A child-scoped empty registry is never initialize's PRIMARY snapshot.
            self.reset_settlement()
            self.update(background_level_known=False, last_subagent_cursor=cursor)
            return
        if (event.get('parent_tool_use_id') is not None or event.get('isSidechain') is True) and not task_lifecycle:
            if typ == 'control_request':
                self.permission(event, text, cursor)
            self.update(last_subagent_cursor=cursor)
            return
        if typ in ('user', 'assistant', 'command_lifecycle', 'stream_event'):
            self.reset_settlement()
        # The Manager's OWN turn: every primary turn opens with a primary system/init frame
        # and closes with its primary result (strictly alternating on 2.1.299 and 2.1.300). Native
        # session_state stays 'running' while background agents/tasks work after that
        # result, so the Send gate needs this witness to tell the two apart.
        if typ == 'system' and event.get('subtype') == 'init':
            self.update(primary_turn_open=True, primary_turn_cursor=cursor, native_wake_pending=False)
        elif typ == 'result':
            self.update(primary_turn_open=False, primary_turn_cursor=cursor, native_wake_pending=False)
            self.bash_in_flight.clear()
            self.bash_done.clear()
            self.root_search_calls.clear()
        if typ == 'system' and event.get('subtype') == 'session_state_changed':
            state = event.get('state')
            if state not in ('idle', 'running', 'requires_action'):
                self.update(protocol_attention=True, native_session_state='unknown')
            else:
                if state != self.record.get('native_session_state') or state != 'idle':
                    self.reset_settlement()
                # native_state_push_cursor witnesses the push capability itself (the collector
                # requires it once a turn ran); initialize readbacks never set it.
                self.update(native_session_state=state, native_state_cursor=cursor,
                            native_state_push_cursor=cursor)
                if state == 'idle':
                    self.host.reconcile_pending_inputs(self.record)
        if (typ in ('user', 'assistant')
                or (typ == 'system' and event.get('subtype') == 'compact_boundary')
                or (typ == 'stream_event' and (event.get('event') or {}).get('type') in
                    ('message_start', 'content_block_start', 'content_block_delta',
                     'content_block_stop', 'message_delta', 'message_stop'))):
            self.context_revision += 1
        if typ == 'stream_event' and not event.get('parent_tool_use_id'):
            stream = event.get('event') or {}
            block = stream.get('content_block') or {}
            if stream.get('type') == 'content_block_start' and block.get('type') == 'tool_use':
                self.track_tool_use(block)
            if stream.get('type') == 'message_start':
                message = stream.get('message') or {}
                self.context_stream = {'usage': dict(message.get('usage') or {}),
                    'model': message.get('model'), 'message_id': message.get('id')}
                self.update(native_api_usage_complete=False, native_api_usage=None,
                    native_api_message_id=(stream.get('message') or {}).get('id'))
            elif stream.get('type') == 'message_delta':
                if self.context_stream is None:
                    self.context_stream = {'usage': {}, 'model': self.record.get('last_assistant_model')
                        or (self.record.get('controls_effective') or {}).get('model')}
                self.context_stream['usage'].update(stream.get('usage') or {})
                self.update(native_api_usage=stream.get('usage'), native_api_usage_complete=False)
            elif stream.get('type') == 'message_stop':
                from .context import input_tokens
                call = self.context_stream or {}
                if not event.get('parent_tool_use_id') and input_tokens(call.get('usage')) is not None:
                    self.completed_call({**call, 'generation': self.generation,
                        'measured_at': time.time(), 'cursor': cursor,
                        'source': 'host stream message_stop completed model-call usage'})
                self.context_stream = None
                self.update(native_api_usage_complete=True, native_api_usage_generation=self.generation,
                    native_api_usage_revision=self.context_revision, native_api_usage_cursor=cursor)
        if typ == 'control_request':
            self.permission(event, text, cursor)
        elif typ == 'control_response':
            response = event.get('response', {})
            rid = response.get('request_id')
            control = self.store.get('controls', self.sid, rid)
            if control:
                control.update(status=response.get('subtype', 'unknown'), response=response,
                               response_cursor=cursor, response_at=time.time())
                self.store.put('controls', self.sid, control, rid)
                pending = [key for key in self.record.get('pending_control_requests', []) if key != rid]
                self.update(pending_control_requests=pending)
                if control['subtype'] == 'initialize' and control.get('settlement_readback'):
                    probe = self.settlement_probe
                    body = response.get('response') or {}
                    if probe and probe['request_id'] == rid:
                        from .collection import well_formed_version
                        # Any Claude Code version: the readback itself must carry every field
                        # the collector reads; a missing or malformed one refuses this process.
                        valid = (response.get('subtype') == 'success' and
                            type(body.get('pid')) is int and body['pid'] == self.process.pid and
                            probe['generation'] == self.generation and
                            body.get('session_state') in ('idle', 'running', 'requires_action') and
                            well_formed_version(body.get('claude_code_version')) and
                            not any(response.get(key) or body.get(key) for key in
                                ('pending_permission_requests', 'pending_user_dialog_requests')))
                        probe.update(response_frame=self.frame_number, response_cursor=cursor,
                            response_at=self.host.monotonic(), valid=valid,
                            session_state=body.get('session_state'))
                        if valid:
                            self.update(native_session_state=body['session_state'],
                                        native_cli_version=body['claude_code_version'])
                    for key in ('pending_permission_requests', 'pending_user_dialog_requests'):
                        for envelope, path in ((response, ('response', key)),
                                (body, ('response', 'response', key))):
                            if envelope.get(key):
                                for pending_request, pending_raw in array_items_raw(member_raw(text, *path)):
                                    self.permission(pending_request, pending_raw, cursor)
                elif control['subtype'] == 'initialize':
                    account = response.get('response', {}).get('account', {})
                    native_state = response.get('response', {}).get('session_state')
                    if native_state in ('idle', 'running', 'requires_action'):
                        self.update(native_session_state=native_state, native_state_cursor=cursor)
                    # The running binary's own version (display/diagnosis; never a collection gate).
                    self.update(native_cli_version=response.get('response', {}).get('claude_code_version'))
                    confirmed = 'max' in str(account.get('subscriptionType', '')).lower()
                    self.update(initialized=response.get('subtype') == 'success',
                                initialize_max_confirmed=confirmed,
                                state='ready' if confirmed and response.get('subtype') == 'success'
                                 else 'launch_blocked')
                    if confirmed and response.get('subtype') == 'success':
                        task = asyncio.create_task(self.verify_initial_settings())
                        self.control_tasks.add(task)
                        task.add_done_callback(self.control_tasks.discard)
                    else:
                        self.host.supervisor.incident(self.record, 'auth', 'alert_no_retry', cursor,
                            detail={'reason': 'native initialize did not confirm Max'}, attention=True)
                    # Official initialize redelivery envelopes use the same request
                    # shape. Transplant their lexical inputs, not decoded float values.
                    for key in ('pending_permission_requests', 'pending_user_dialog_requests'):
                        if response.get(key):
                            for pending, pending_raw in array_items_raw(member_raw(text, 'response', key)):
                                self.permission(pending, pending_raw, cursor)
        elif typ == 'user' and event.get('isReplay'):
            mid = event.get('uuid')
            receipt = self.store.get('receipts', self.sid, mid)
            if receipt:
                content = event.get('message', {}).get('content')
                # Do not acknowledge an ID-only echo or a differing submission.
                if content == receipt['submitted']:
                    receipt.update(acknowledged=True, acknowledgement_cursor=cursor)
                    if receipt.get('kind') == 'steer':
                        # A queued message is echoed only when the model receives it: delivery.
                        receipt.update(delivered=True, delivered_cursor=cursor, delivered_at=time.time())
                    if receipt['status'] not in ('completed', 'started'):
                        receipt['status'] = 'delivered' if receipt.get('kind') == 'steer' else 'acknowledged'
                else:
                    receipt['echo_mismatch'] = True
                    self.event('receipt_mismatch', {'message_uuid': mid, 'frame_cursor': cursor})
                self.store.put('receipts', self.sid, receipt, mid)
        elif typ == 'command_lifecycle':
            mid = event.get('command_uuid') or event.get('user_message_uuid') or event.get('uuid')
            receipt = self.store.get('receipts', self.sid, mid)
            if receipt:
                state = event.get('state')
                receipt.setdefault('lifecycle', []).append({'state': state, 'cursor': cursor})
                if state == 'queued' and not receipt.get('queued'):
                    receipt.update(queued=True, queued_cursor=cursor, queued_at=time.time())
                    if receipt.get('kind') == 'steer' and receipt['status'] == 'accepted':
                        receipt['status'] = 'queued'
                if state == 'started':
                    receipt.update(started=True, status='started')
                if (state == 'cancelled' and receipt.get('kind') == 'steer' and not receipt.get('started')
                        and not receipt.get('delivered') and not receipt.get('completed')):
                    # Dropped from the native queue before the model saw it (an interrupt's
                    # cancel_queued): no result will ever name it, so it stops gating input.
                    receipt.update(status='cancelled', cancelled=True, cancelled_cursor=cursor)
                    self.pending.discard(mid)
                    self.update(pending_message_uuids=sorted(self.pending))
                    self.event('steer_cancelled', {'message_uuid': mid, 'frame_cursor': cursor})
                self.store.put('receipts', self.sid, receipt, mid)
        elif typ == 'result':
            self.reset_settlement()
            queued = event.get('queued_turn_count')
            if queued is not None and (type(queued) is not int or queued < 0):
                self.update(protocol_attention=True)
            self.update(native_queued_turns=queued or 0)
            windows = dict(self.record.get('context_windows') or {})
            for model, usage in (event.get('modelUsage') or {}).items():
                if isinstance(usage, dict) and type(usage.get('contextWindow')) is int and usage['contextWindow'] > 0:
                    windows[model] = usage['contextWindow']
            self.update(context_windows=windows)
            mids = event.get('user_message_uuids') or [event.get('user_message_uuid')]
            origin = event.get('origin')
            background = not user_origin(origin)
            identity_key = hashlib.sha256(json.dumps([self.generation, event.get('uuid') or cursor,
                event.get('result_index'), origin, mids], sort_keys=True).encode()).hexdigest()
            # Result copies live in their own rows (Store.results), written once, never in the session record:
            # the identity for duplicate detection, and a background result's summary (it may also name an input;
            # `state` answers the newest as last_synthetic_result).
            if self.store.result_seen(self.sid, identity_key):
                self.event('result_duplicate', {'frame_cursor': cursor, 'identity_key': identity_key})
                return
            summary = {key: event.get(key) for key in ('subtype', 'is_error', 'terminal_reason',
                'result', 'errors', 'permission_denials', 'origin', 'result_index',
                'user_message_uuid', 'user_message_uuids', 'queued_turn_count')}
            summary['cursor'] = cursor
            self.store.put_result(self.sid, identity_key, cursor, background)
            self.host.supervisor.result(self, event, cursor, [mid for mid in mids if mid], summary)
            if background:
                self.store.put_result(self.sid, identity_key, cursor, True, summary)  # classified by result()
            for mid in mids:
                receipt = self.store.get('receipts', self.sid, mid)
                if receipt:
                    # Multiple result indexes/origins remain separate receipts, never task success.
                    existing = receipt.setdefault('results', [])
                    identity = (event.get('uuid'), event.get('result_index'), origin, self.generation)
                    if not any(tuple(row['identity']) == identity for row in existing):
                        existing.append({'identity': identity, **summary})
                    self.store.put('receipts', self.sid, receipt, mid)
            self.update(pending_message_uuids=sorted(self.pending), last_result_cursor=cursor,
                        last_terminal_reason=event.get('terminal_reason'))
            # Origin classifies supervision, not which explicitly named user inputs this turn answered.
            self.host.reconcile_pending_inputs(self.record, message_uuids=mids)
            if not background:
                for mid in mids:
                    receipt = self.store.get('receipts', self.sid, mid)
                    if receipt:
                        self.host.supervisor.recovery_result(self, receipt, summary)
        elif typ == 'system' and event.get('subtype') == 'init':
            if event.get('apiKeySource') not in (None, 'none'):
                self.update(state='launch_blocked', financial_route_attention=True)
                self.host.supervisor.incident(self.record, 'financial_entitlement', 'alert_no_retry', cursor,
                    detail={'reason': 'native init apiKeySource was not none'}, attention=True)
            self.update(actual_model=event.get('model'), mcp_servers=event.get('mcp_servers'),
                        tools=event.get('tools'), native_init_cursor=cursor)
            self.verify_model_witness(event.get('model'), cursor)
        elif typ == 'assistant':
            from .context import input_tokens
            message = event.get('message') or {}
            # Remember only explicit background tool inputs; attach in either native frame order.
            for block in message.get('content') or []:
                if not isinstance(block, dict) or block.get('type') != 'tool_use':
                    continue
                self.track_tool_use(block)
                tool_input = block.get('input')
                if not isinstance(tool_input, dict) or tool_input.get('run_in_background') is not True:
                    continue
                tool_id = block.get('id')
                if not isinstance(tool_id, str):
                    continue
                tool_input = {'name': block.get('name') if isinstance(block.get('name'), str) else '',
                    'command': tool_input['command'][:1024] if isinstance(tool_input.get('command'), str) else '',
                    'description': tool_input['description'][:256] if isinstance(tool_input.get('description'), str) else ''}
                self.background_inputs[tool_id] = tool_input
                if len(self.background_inputs) > 64:
                    self.background_inputs.pop(next(iter(self.background_inputs)))
                tasks = self.record.get('background_tasks') or {}
                if any(t.get('tool_use_id') == tool_id and 'tool_input' not in t for t in tasks.values()):
                    self.update(background_tasks={task_id: {**task, 'tool_input': tool_input}
                        if task.get('tool_use_id') == tool_id and 'tool_input' not in task else task
                        for task_id, task in tasks.items()})
            if (message.get('stop_reason') and not event.get('parent_tool_use_id') and not event.get('isSidechain')
                    and input_tokens(message.get('usage')) is not None):
                prior = self.record.get('last_completed_model_call') or {}
                same = message.get('id') and prior.get('message_id') == message['id']
                self.completed_call({'usage': dict(message['usage']),
                    'model': message.get('model'), 'message_id': message.get('id'),
                    'generation': self.generation,
                    'measured_at': prior.get('measured_at') if same else time.time(),
                    'cursor': cursor, 'source': 'host completed assistant model-call usage'})
            self.update(last_assistant_model=event.get('message', {}).get('model'),
                        last_assistant_cursor=cursor)
            self.verify_model_witness(event.get('message', {}).get('model'), cursor)
        elif typ == 'rate_limit_event':
            info = event.get('rate_limit_info', {})
            self.update(last_rate_limit_info=info, last_rate_limit_cursor=cursor,
                        last_rate_limit_at=time.time(), last_rate_limit_account=self.record.get('account'))
            if info.get('isUsingOverage'):
                self.update(state='launch_blocked', financial_route_attention=True)
                self.event('financial_route_attention', {'frame_cursor': cursor, 'reason': 'native overage observed'})
                self.host.supervisor.incident(self.record, 'financial_entitlement', 'alert_no_retry', cursor,
                                              detail={'reason': 'native overage observed'}, attention=True)
            if self.record.get('native_retry_active'):
                # Either order: a rejected usage window during a 429 retry is a lockout, never a sleep to the reset.
                self.host.supervisor.quota_lockout(self, cursor)
        if typ == 'system' and event.get('subtype') == 'api_retry':
            self.update(native_retry_active=True, last_retry={'cursor': cursor,
                **{k: event.get(k) for k in ('attempt', 'max_retries', 'retry_delay_ms', 'error_status', 'error')}})
            self.host.supervisor.incident(self.record, 'native_retry', source_cursor=cursor,
                                          detail=self.record['last_retry'])
            self.host.supervisor.quota_lockout(self, cursor)
        if typ == 'system' and event.get('subtype') in ('task_started', 'task_updated', 'task_progress', 'task_notification', 'background_tasks_changed'):
            if getattr(self.host, 'observations', None) is not None:
                self.host.observations.task_event(self, event, cursor)
            tasks = dict(self.record.get('background_tasks') or {})
            sub = event['subtype']
            task_id = event.get('task_id')
            if sub != 'background_tasks_changed':
                self.reset_settlement()
            if sub == 'background_tasks_changed':
                level = event.get('tasks')
                valid_level = (event.get('session_id') in (None, self.sid) and isinstance(level, list) and all(isinstance(t, dict) and
                    isinstance(t.get('task_id'), str) and isinstance(t.get('run_id'), str) for t in level))
                if not valid_level:
                    self.update(background_level_known=False, protocol_attention=True)
                    return
                prior_tasks = tasks
                tasks = {t['task_id']: {**(tasks[t['task_id']] if t['task_id'] in tasks and
                    tasks[t['task_id']].get('run_id') == t['run_id'] else {}), **t, 'cursor': cursor} for t in level}
                # Null/omitted level fields never erase a task_started tool-use binding.
                for tid, item in tasks.items():
                    prior = prior_tasks.get(tid) or {}
                    if not item.get('tool_use_id') and prior.get('run_id') == item['run_id'] and prior.get('tool_use_id'):
                        item['tool_use_id'] = prior['tool_use_id']
                self.update(background_level_known=True, background_level_cursor=cursor)
                probe = self.settlement_probe
                if probe and probe.get('response_frame') and self.frame_number > probe['response_frame']:
                    # initialize's fresh task snapshot is the first subsequent level, never a
                    # cached empty map or a terminal edge. It has no native request_id.
                    if not probe.get('snapshot_cursor'):
                        probe.update(snapshot_cursor=cursor, tasks=level,
                            snapshot_revision=self.work_revision, snapshot_at=self.host.monotonic())
            elif sub == 'task_started' and task_id and event.get('is_backgrounded'):
                if task_id in tasks and tasks[task_id].get('run_id') != event.get('run_id'):
                    self.update(background_level_known=False)
                else:
                    tasks[task_id] = {**event, 'cursor': cursor}
                    tasks[task_id]['started_at'] = time.time()
                    if event.get('tool_use_id') in self.background_inputs:
                        tasks[task_id]['tool_input'] = self.background_inputs[event['tool_use_id']]
            elif sub in ('task_progress', 'task_updated') and task_id:
                previous = tasks.get(task_id)
                patch = event.get('patch') or {}
                same_run = (previous is not None and isinstance(event.get('run_id'), str) and
                    previous.get('run_id') == event.get('run_id'))
                if same_run:
                    if patch.get('status') in ('completed', 'failed', 'killed', 'stopped'):
                        tasks.pop(task_id, None)
                    else:
                        tasks[task_id] = {**previous, **event, **patch, 'cursor': cursor}
                        if not tasks[task_id].get('tool_use_id') and previous.get('tool_use_id'):
                            tasks[task_id]['tool_use_id'] = previous['tool_use_id']
                elif sub == 'task_updated' and patch.get('is_backgrounded') is True:
                    if previous is not None:
                        self.update(background_level_known=False)
                    else:
                        tasks[task_id] = {**event, **patch, 'cursor': cursor}
            elif sub == 'task_notification' and task_id:
                if event.get('status') in ('completed', 'failed', 'stopped'):
                    if (task_id in tasks and isinstance(event.get('run_id'), str) and
                            tasks[task_id].get('run_id') == event.get('run_id')):
                        tasks.pop(task_id, None)
                else:
                    if task_id in tasks and tasks[task_id].get('run_id') != event.get('run_id'):
                        self.update(background_level_known=False)
                    else:
                        tasks[task_id] = {**event, 'cursor': cursor}
            if tasks:
                self.reset_settlement()
            self.update(background_tasks=tasks, background_unsettled=bool(tasks))
            # A background task that ends while the session's own turn is closed WAKES the session: Claude Code
            # queues its notification and starts a turn for it (observed order: task_updated completed ->
            # task_notification -> background_tasks_changed -> session_state_changed running -> init). Between
            # those frames the level is empty and the state idle, so without this marker a reader sees "done"
            # for a few milliseconds while a wake-up is due. The wake turn's own init (or result) clears it; a
            # deliberate stop (killed/stopped) queues no notification and never sets it.
            ended = ((event.get('patch') or {}).get('status') if sub in ('task_updated', 'task_progress')
                     else event.get('status') if sub == 'task_notification' else None)
            if (ended in ('completed', 'failed') and event.get('parent_tool_use_id') is None
                    and event.get('isSidechain') is not True
                    and (self.record.get('native_session_state') == 'idle'
                         or self.record.get('primary_turn_open') is False)):
                self.update(native_wake_pending=True, native_wake_cursor=cursor)
        if typ == 'system' and event.get('subtype') not in (
                'init', 'status', 'session_state_changed', 'api_retry', 'task_started', 'task_updated',
                'task_progress', 'task_notification', 'background_tasks_changed', 'permission_denied',
                'compact_boundary', 'local_command_output', 'turn_duration',
                'thinking_tokens', 'vcs_state_changed'):
            # A system frame is a status notice: it carries no input, result or effect. An unknown
            # subtype is retained and logged, never a settlement hold (nothing can be lost by it).
            # Only the automatic collector refuses this generation: a new native notice may
            # announce work its readback does not cover (collection.collection_refusal).
            self.event('unknown_system_subtype', {'frame_cursor': cursor, 'subtype': event.get('subtype')})
            seen = list(self.record.get('unknown_system_subtypes') or [])
            if str(event.get('subtype')) not in seen:
                self.update(unknown_system_subtypes=seen + [str(event.get('subtype'))])
        if typ == 'system' and event.get('subtype') in ('task_started', 'task_notification', 'permission_denied'):
            if event.get('subtype') == 'permission_denied':
                self.host.supervisor.incident(self.record, 'permission_denied', source_cursor=cursor,
                                              mids=sorted(self.pending), detail=event)
            field = 'denial_cursors' if event.get('subtype') == 'permission_denied' else 'task_cursors'
            for mid in self.pending:
                receipt = self.store.get('receipts', self.sid, mid)
                receipt.setdefault(field, []).append(cursor)
                self.store.put('receipts', self.sid, receipt, mid)
        if typ == 'user':
            blocks = event.get('message', {}).get('content', [])
            if isinstance(blocks, list):
                for block in blocks:
                     if (isinstance(block, dict) and block.get('type') == 'tool_result'
                             and block.get('tool_use_id') in self.bash_in_flight):
                        self.bash_in_flight.discard(block['tool_use_id'])
                        self.bash_done.add(block['tool_use_id'])
                     if (isinstance(block, dict) and block.get('type') == 'tool_result'
                             and block.get('tool_use_id') in self.root_search_calls):
                        tool = self.root_search_calls.pop(block['tool_use_id'])
                        answer = self.root_search_hooked.pop(block['tool_use_id'], None)
                        # Only a call that ran says anything: an error result may be a validation failure
                        # (no hook runs then), an interrupt, a refusal or the hook's own timeout.
                        condition = (None if block.get('is_error') else 'missing_callback' if answer is None
                                     else 'deny_ignored' if answer == 'deny' else None)
                        self.root_search_witness(cursor, checked=True, condition=condition, tool=tool,
                                                 tool_use_id=block['tool_use_id'])
                     if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('is_error'):
                        self.host.supervisor.incident(self.record, 'tool_error_receipt', source_cursor=cursor,
                            mids=sorted(self.pending), detail={'tool_use_id': block.get('tool_use_id'),
                                'task_verdict': 'not_inferred; may be expected negative test'},
                            key=cursor + ':tool:' + str(block.get('tool_use_id')))
                        for mid in self.pending:
                            receipt = self.store.get('receipts', self.sid, mid)
                            receipt.setdefault('tool_errors', []).append({'tool_use_id': block.get('tool_use_id'),
                                                                        'cursor': cursor})
                            self.store.put('receipts', self.sid, receipt, mid)

    def verify_model_witness(self, model, cursor):
        from .controls import compare_model
        expected = (self.record.get('controls_effective') or {}).get('model')
        # Only a model the native positively names differently blocks (cost and billing); one the
        # comparison cannot place (an alias, an empty value) is not a difference.
        if isinstance(model, str) and expected and compare_model(expected, model) == 'differs':
            self.update(setting_control_pending=True, settings_mismatch=True,
                setting_control_state='mismatch', setting_control_request_id=None,
                setting_control_disclosure=f'native execution model {model} differs from applied readback {expected}')
            self.host.supervisor.incident(self.record, 'execution_model_mismatch', 'escalate', cursor,
                sorted(self.pending), {'expected_applied_model': expected, 'execution_model': model}, True)

    # @beacon[
    #   id=opc@NativeSession.permission,
    #   slice_labels=opc-claude-seats,
    #   kind=ast,
    #   comment=Answers every native can_use_tool at once, journaled in the permissions table: allow with the input's exact lexical JSON, or deny with a message for a root search (root_search.judge, with the folder unknown: a request carries none) the PreToolUse hook did not already judge. Hook callbacks go to hook_callback; duplicate request IDs replay the cached decision; other control requests escalate.,
    # ]
    def permission(self, event, text, cursor):
        request = event.get('request', {})
        if request.get('subtype') == 'hook_callback':
            return self.hook_callback(event, text, cursor)
        rid = event.get('request_id')
        self.reset_settlement()
        pending = dict(self.record.get('pending_native_requests') or {})
        pending[rid or 'unknown'] = {'subtype': request.get('subtype'), 'cursor': cursor,
            # A subagent's own tool permission (native agent_id) is not the Manager's turn.
            **({'agent_id': request['agent_id']} if isinstance(request.get('agent_id'), str) else {})}
        self.update(pending_native_requests=pending)
        # A root search (docs/root-search-guard.md) is refused; the check allows on any doubt. A call the
        # PreToolUse hook judged (with the folder Claude Code reported) is not judged again here. A request
        # carries no folder and the shell may have moved since launch, so the folder is unknown: a relative
        # or missing Grep/Glob path is never judged against the launch folder.
        refusal = (judge_root_search(request.get('tool_name'), request.get('input'), None,
                                     glob_honours_gitignore=self.record.get('glob_honours_gitignore') is True)
                   if request.get('subtype') == 'can_use_tool'
                   and request.get('tool_use_id') not in self.root_search_hooked else None)
        behavior = 'deny' if refusal else 'allow'
        self.host.supervisor.incident(self.record, 'permission_request', source_cursor=cursor,
            mids=sorted(self.pending), detail={'request_id': rid, 'request_raw': member_raw(text, 'request'),
                'tool_name': request.get('tool_name'), 'tool_use_id': request.get('tool_use_id'),
                'behavior': behavior if request.get('subtype') == 'can_use_tool' else 'not_automatically_answered'})
        if request.get('subtype') != 'can_use_tool' or not isinstance(rid, str):
            self.event('unsupported_control_request', {'frame_cursor': cursor,
                       'request_id': rid, 'subtype': request.get('subtype')})
            self.update(protocol_attention=True)
            self.host.supervisor.incident(self.record, 'unsupported_control_request', 'escalate', cursor,
                sorted(self.pending), {'request_id': rid, 'subtype': request.get('subtype')}, True)
            return
        previous = self.store.permission(self.sid, self.generation, rid)
        if previous:
            self.event('permission_duplicate', {'request_id': rid, 'frame_cursor': cursor,
                       'original_cursor': previous['request_cursor'],
                       'changed_request': previous['request_raw'] != member_raw(text, 'request')})
            self.enqueue(previous['response_raw'].encode('utf-8') + b'\n', 'permission',
                         {'request_id': rid, 'duplicate': True})
            return
        # No float roundtrip: input's exact lexical JSON is transplanted into updatedInput.
        input_present = 'input' in request
        input_raw = member_raw(text, 'request', 'input') if input_present else None
        if refusal:
            response_raw = ('{"type":"control_response","response":{"subtype":"success","request_id":' +
                json.dumps(rid) + ',"response":{"behavior":"deny","message":' + json.dumps(refusal.message) + '}}}')
        else:
            response_raw = ('{"type":"control_response","response":{"subtype":"success",'
                '"request_id":' + json.dumps(rid) + ',"response":{"behavior":"allow",'
                '"updatedInput":' + input_raw + '}}}') if input_present else (
                '{"type":"control_response","response":{"subtype":"success","request_id":' +
                json.dumps(rid) + ',"response":{"behavior":"allow"}}}')
        body = {'request_id': rid, 'request_cursor': cursor,
                'decision_generation': self.generation,
                'request_raw': member_raw(text, 'request'), 'input_raw': input_raw,
                'input_present': input_present,
                'tool_name': request.get('tool_name'), 'tool_use_id': request.get('tool_use_id'),
                'reason_fields': {key: value for key, value in request.items()
                                  if key not in ('input', 'subtype', 'tool_name', 'tool_use_id')},
                'reason': 'unknown' if not any(key not in ('input', 'subtype', 'tool_name', 'tool_use_id')
                                             for key in request) else 'native fields retained',
                'behavior': behavior, 'response_raw': response_raw,
                **({'root_search': refusal.record()} if refusal else {})}
        self.store.permission(self.sid, self.generation, rid, body)
        if not input_present:
            self.event('permission_input_absent', {'request_id': rid, 'frame_cursor': cursor,
                       'behavior': 'allow', 'updated_input': 'omitted; no original input supplied'})
            self.update(protocol_attention=True)
        self.event('permission_decision', {'request_id': rid, 'behavior': behavior,
                    'tool_name': body['tool_name'], 'tool_use_id': body['tool_use_id'],
                    'request_cursor': cursor, 'reason': body['reason'],
                    **({'root_search': refusal.record()} if refusal else {})})
        self.enqueue(response_raw.encode('utf-8') + b'\n', 'permission', {'request_id': rid})

    # @beacon[
    #   id=opc@NativeSession.hook_callback,
    #   slice_labels=opc-claude-seats,
    #   kind=ast,
    #   comment=Answers every native hook_callback at once (the PreToolUse hook the host registers at initialize, so every Bash/Grep/Glob call reaches it, auto-approved ones included): denies a root search with its message, judged from the folder Claude Code reports (unknown while another main-thread Bash runs); anything else gets no opinion. Journaled with the permissions; duplicate request IDs replay. Each answer feeds the generation's witness; a judged callback with no folder is flagged.,
    # ]
    def hook_callback(self, event, text, cursor):
        request = event.get('request', {})
        rid = event.get('request_id')
        hook_input = request.get('input') if isinstance(request.get('input'), dict) else {}
        self.reset_settlement()
        pending = dict(self.record.get('pending_native_requests') or {})
        pending[rid or 'unknown'] = {'subtype': 'hook_callback', 'cursor': cursor,
            **({'agent_id': hook_input['agent_id']} if isinstance(hook_input.get('agent_id'), str) else {})}
        self.update(pending_native_requests=pending)
        if not isinstance(rid, str):
            self.event('unsupported_control_request', {'frame_cursor': cursor, 'request_id': rid,
                       'subtype': 'hook_callback'})
            self.update(protocol_attention=True)
            self.host.supervisor.incident(self.record, 'unsupported_control_request', 'escalate', cursor,
                sorted(self.pending), {'request_id': rid, 'subtype': 'hook_callback'}, True)
            return
        previous = self.store.permission(self.sid, self.generation, rid)
        if previous:
            self.event('permission_duplicate', {'request_id': rid, 'frame_cursor': cursor,
                       'original_cursor': previous['request_cursor'],
                       'changed_request': previous['request_raw'] != member_raw(text, 'request')})
            self.enqueue(previous['response_raw'].encode('utf-8') + b'\n', 'permission',
                         {'request_id': rid, 'duplicate': True})
            return
        tool_use_id = request.get('tool_use_id') or hook_input.get('tool_use_id')
        refusal = cwd = None
        judged = (request.get('callback_id') == ROOT_SEARCH_HOOK
                  and hook_input.get('hook_event_name') == 'PreToolUse')
        others_running = bool(self.bash_in_flight - {tool_use_id})
        if judged:
            # Claude Code reports the folder its shell stands in. Another main-thread Bash still running may
            # move it before this call starts, so the folder is known only while none is (docs/root-search-guard.md).
            cwd = hook_input.get('cwd') if isinstance(hook_input.get('cwd'), str) else None
            if others_running:
                cwd = None
            refusal = judge_root_search(hook_input.get('tool_name'), hook_input.get('tool_input'), cwd, shell_cwd=cwd,
                                        glob_honours_gitignore=self.record.get('glob_honours_gitignore') is True)
        behavior = 'deny' if refusal else 'no_opinion'
        if judged and isinstance(tool_use_id, str):
            self.root_search_hooked[tool_use_id] = behavior
            if len(self.root_search_hooked) > 1024:
                self.root_search_hooked.pop(next(iter(self.root_search_hooked)))
        answer = ({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                          'permissionDecisionReason': refusal.message}} if refusal else {})
        response_raw = ('{"type":"control_response","response":{"subtype":"success","request_id":' +
                        json.dumps(rid) + ',"response":' + json.dumps(answer) + '}}')
        body = {'request_id': rid, 'request_cursor': cursor, 'decision_generation': self.generation,
                'request_raw': member_raw(text, 'request'), 'kind': 'hook_callback',
                'callback_id': request.get('callback_id'), 'tool_name': hook_input.get('tool_name'),
                'tool_use_id': tool_use_id, 'hook_cwd': hook_input.get('cwd'), 'cwd_known': cwd is not None,
                'behavior': behavior, 'response_raw': response_raw,
                **({'root_search': refusal.record()} if refusal else {})}
        self.store.permission(self.sid, self.generation, rid, body)
        self.event('hook_decision', {'request_id': rid, 'behavior': behavior, 'callback_id': body['callback_id'],
                   'tool_name': body['tool_name'], 'tool_use_id': tool_use_id, 'request_cursor': cursor,
                   'cwd_known': body['cwd_known'], **({'root_search': refusal.record()} if refusal else {})})
        if not judged:
            self.event('hook_callback_unknown', {'request_id': rid, 'frame_cursor': cursor,
                       'callback_id': request.get('callback_id')})
        else:
            self.root_search_witness(cursor, answered=behavior, tool=hook_input.get('tool_name'),
                                     tool_use_id=tool_use_id, condition='cwd_missing' if (
                                         not isinstance(hook_input.get('cwd'), str) and not others_running) else None)
        self.enqueue(response_raw.encode('utf-8') + b'\n', 'permission', {'request_id': rid})

    # @beacon[
    #   id=opc@NativeSession.root_search_witness,
    #   slice_labels=opc-claude-seats,
    #   kind=ast,
    #   comment=The root-search hook's witness on this generation (record root_search_hook, so state and host health show it): counts hook answers and checked main-thread Bash/Grep/Glob results, and flags a missing callback, a callback with no folder, or an ignored deny once per generation as a root_search_hook escalation naming the Claude Code version. Never refuses or delays anything.,
    # ]
    def root_search_witness(self, cursor, *, answered=None, checked=False, condition=None, tool=None,
                            tool_use_id=None):
        """Record one hook answer (`answered`: deny | no_opinion) or one checked main-thread result, and flag
        `condition` (a ROOT_SEARCH_FLAGS key) the first time it occurs on this generation
        (docs/root-search-guard.md, "Is it still working?")."""
        witness = dict(self.record.get('root_search_hook') or {})
        if witness.get('generation') != self.generation:
            witness = fresh_root_search_witness(self.generation)
        if answered:
            witness.update(seen_answering=True, answered=witness['answered'] + 1,
                           denied=witness['denied'] + (answered == 'deny'))
        if checked:
            witness['results_checked'] += 1
        version = self.record.get('native_cli_version') or 'unknown'
        witness['claude_code_version'] = version
        if condition:
            witness[condition] += 1
            if condition not in witness['flags']:
                # Durable and addressed (Dan for a Manager, else the owning Conductor), never a refusal, a Send
                # hold or a recovery block: no input is named.
                incident = self.host.supervisor.incident(self.record, 'root_search_hook', 'escalate', cursor,
                    detail={'condition': condition, 'claude_code_version': version, 'tool_name': tool,
                            'tool_use_id': tool_use_id,
                            'reason': ROOT_SEARCH_FLAGS[condition].format(version=version, tool=tool or 'tool'),
                            'check': 'docs/root-search-guard.md, "Is it still working?"'},
                    attention=True, key=f'root-search-hook-{self.generation}-{condition}')
                witness['flags'] = {**witness['flags'], condition: incident['incident_id']}
                self.event('root_search_hook_flag', {'condition': condition, 'claude_code_version': version,
                           'tool_name': tool, 'tool_use_id': tool_use_id, 'frame_cursor': cursor})
        self.update(root_search_hook=witness)

    async def stderr(self):
        while True:
            data = await self.process.stderr.read(65536)
            if not data:
                break
            if self.infrastructure_error:
                self.lost_bytes['stderr'] += len(data)
                continue
            try:
                with self.store.batch():  # one transaction per read, after the raw append's fsync
                    offset = self.store.append_bytes(self.sid, self.generation, 'stderr', data)
                    self.host.supervisor.activity(self)
                    stderr_cursor = self.event('stderr_bytes', stream='stderr', offset=offset, length=len(data))
                    self.update(last_stderr_cursor=stderr_cursor)
            except Exception as error:
                self.restore_committed()
                self.failed_storage(error)
        if not self.infrastructure_error:
            try:
                self.event('stderr_eof')
            except Exception as error:
                self.failed_storage(error)

    # @beacon[
    #   id=opc@NativeSession.exited,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Journal PID exit before waiting for drain EOF, then release the settled owner even if Supervisor exit bookkeeping raises; a durability failure keeps its owner resident for reconciliation, never a timer-driven kill.,
    # ]
    async def exited(self):
        # asyncio Process.wait can wait for pipe EOF as well as PID exit. A tool
        # descendant may retain a pipe: observe the actual native returncode without
        # a deadline, publish exit now, and keep both drains/UUID lock alive separately.
        while self.process.returncode is None:
            await asyncio.sleep(.05)
        code = self.process.returncode
        # Release the already-dead native process's stdin descriptor; NEVER silence-driven.
        self.process.stdin.close()
        try:
            await self.process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass
        exit_cursor = None
        try:
            exit_cursor = self.event('process_exit', {'exit_code': code, 'pending_message_uuids': sorted(self.pending)})
            before = self.record['state']
            self.update(state='exited_draining', exit_code=code, owner='draining',
                        pending_message_uuids=sorted(self.pending), state_before_exit=before)
            for mid in self.pending:
                receipt = self.store.get('receipts', self.sid, mid)
                if receipt and not receipt.get('completed'):
                    receipt['status'] = 'uncertain'
                    self.store.put('receipts', self.sid, receipt, mid)
        except Exception as error:
            self.failed_storage(error)
        finally:
            await asyncio.gather(self.tasks[1], self.tasks[2])
            if not self.infrastructure_error:
                try:
                    self.update(state='exited', owner='gone')
                    self.event('process_drains_settled', {'exit_code': code})
                except Exception as error:
                    self.failed_storage(error)
            os.close(self.lock_fd)
            self.writes.put_nowait(None)
            if not self.infrastructure_error:
                try:
                    self.host.supervisor.exited(self, exit_cursor)
                finally:
                    self.host.release_ended(self)

    # @beacon[
    #   id=opc@NativeSession.send,
    #   slice_labels=opc-basic,opc-claude-seats,
    #   kind=ast,
#   comment=One journaled user line per caller UUID (never resent), gated during exact observer-owner retirement. Ordinary Send needs the own turn closed; steer joins native next-step queuing. Accepted input ends a quota halt but keeps a still-rejected window; native wake and task receipts never imply worker completion.,
    # ]
    async def send(self, mid, submitted, original, recovery_id=None, steer=False):
        mid = native_id(mid)
        if not isinstance(submitted, str) or not submitted or not isinstance(original, str):
            raise ValueError('nonempty submitted and exact original text required')
        async with self.command_lock:
            previous = self.store.get('receipts', self.sid, mid)
            if previous:
                if previous['submitted'] != submitted or previous['original'] != original:
                    raise ValueError('message UUID already bound to different original/submission')
                return previous  # no second semantic write, including uncertain receipts
            if self.record.get('observation_transition') or getattr(self.host, 'observer_switch', None):
                raise RuntimeError('account transition is retiring this exact observer-only owner; reconnect after its exit')
            if self.record['state'] != 'ready' or self.process.returncode is not None:
                raise RuntimeError('native session not initialized and Max-confirmed/ready')
            if self.record.get('stdout_eof'):
                raise RuntimeError('native stdout EOF; no observed reply channel, input withheld')
            # A steer is the native queued message (no priority field = "next"): written while the
            # turn runs, Claude Code hands it to the model at its next tool step in the same turn, or
            # it starts the next turn if this one ends first. Only the open turn, and a native retry
            # inside it, are waived for it.
            busy = own_turn_busy(self.record)
            turn_open = bool(self.pending or busy)
            if self.pending and not steer:
                raise RuntimeError('busy: the Manager is still answering the previous message; wait or interrupt')
            if busy and not steer:
                raise RuntimeError('busy: ' + busy + '; wait or interrupt')
            if self.record.get('setting_control_pending') or self.record.get('settings_mismatch'):
                raise RuntimeError('settings pending/unverified or effective choice mismatch; Send withheld '
                                   '(press Re-check settings, or apply the model/thinking again)')
            # Background tasks (e.g. a Manager's background `oc.sh seat wait`) run independently of the
            # conversation and are its doorbell: they never block Dan's next message. An active native
            # retry blocks an ordinary Send, but never a steer into the open turn: Claude Code holds the
            # queued line through the retry and hands it to the model at its next tool step.
            steer_joins_turn = steer and turn_open
            if self.record.get('native_retry_active') and not steer_joins_turn:
                raise RuntimeError('busy: native retry unsettled')
            superseded = None
            if self.record.get('pending_recovery') and recovery_id != self.record['pending_recovery']:
                # Dan's own message replaces a still-scheduled automatic `continue` (two inputs
                # would race); a recovery already claimed or sending is in flight and still waits.
                rid = self.record['pending_recovery']
                action = self.host.supervisor.actions.get((self.record['execution_id'], rid))
                recovery = self.store.get('recoveries', self.sid, rid)
                if (action is not None and not action.done()) or (recovery or {}).get('status') in ('claimed', 'accepted'):
                    raise RuntimeError('busy: an automatic recovery is sending its continue right now; wait a moment')
                if recovery and recovery.get('status') == 'scheduled':
                    recovery.update(status='cancelled', operator_disposition="superseded by Dan's explicit Send " + mid)
                    self.store.put('recoveries', self.sid, recovery, rid)
                superseded = rid
            receipt = {'execution_id': self.record['execution_id'], 'generation': self.generation,
                'message_uuid': mid, 'original': original, 'submitted': submitted,
                'original_sha256': hashlib.sha256(original.encode()).hexdigest(),
                'submitted_sha256': hashlib.sha256(submitted.encode()).hexdigest(),
                'status': 'accepted', 'durable': True, 'written': False, 'acknowledged': False,
                 'started': False, 'completed': False, 'results': []}
            if steer:
                # Two moments of its own: `queued` (native command_lifecycle queued: Claude Code
                # holds it) and `delivered` (its replay echo, which arrives only when the model
                # receives it). One result of any origin may name it beside the input whose turn it joined.
                receipt.update(kind='steer', turn_open_at_send=turn_open, queued=False, delivered=False)
            if recovery_id:
                receipt['recovery_id'] = recovery_id
            if self.record.get('pending_escalations'):
                # An escalation is addressed to whoever writes next: Dan for a Manager, the owning
                # Conductor (or the Manager) for a seat. Their message is the disposition, so open
                # escalations are recorded on this receipt and stay listed until resolved, never a
                # refusal. What really must stop input (launch_blocked, financial attention, a positive
                # settings mismatch) blocks through state above.
                receipt['escalations_open_at_send'] = list(self.record['pending_escalations'])
            if superseded:
                receipt['superseded_recovery'] = superseded
            if self.infrastructure_error:
                raise RuntimeError('durability unavailable: Send refused')
            self.store.put('receipts', self.sid, receipt, mid)
            # Read back before attempting stdin. This receipt never authorizes blind replay.
            if self.store.get('receipts', self.sid, mid) != receipt:
                raise OSError('exact original receipt readback failed')
            self.pending.add(mid)
            self.reset_settlement()
            self.context_revision += 1
            # last_input_uuid names the newest accepted input (a recovery continue included), so a
            # seat script's wait can read that input's receipt and result without local state.
            # A steer that joins the open turn keeps that turn's retry/rate-limit evidence: a terminal
            # error of the same turn still schedules its recovery from it. A rejected usage window whose
            # reset is still ahead is kept across any input: this process keeps the login it started with,
            # and Claude Code repeats an unchanged rejection at most every 30 s, so a Send to a halted live
            # process may get only the 429 retry, which must halt again rather than sleep to the reset.
            rate_info = self.record.get('last_rate_limit_info')
            if not usage_lockout(rate_info, self.host.supervisor.clock()):
                rate_info = None
            self.update(pending_message_uuids=sorted(self.pending), last_activity_at=self.host.supervisor.clock(),
                        **({} if steer_joins_turn else {'last_retry': None, 'last_rate_limit_info': rate_info}),
                        last_input_uuid=mid, last_input_at=time.time(),
                        **({'pending_recovery': None} if superseded else {}))
            if superseded:
                self.host.supervisor.incident(self.record, 'recovery_superseded', 'retain', mids=[mid],
                    detail={'recovery_id': superseded, 'by': 'explicit Send', 'original_replay': False})
            if not steer_joins_turn and not recovery_id:
                self.clear_quota_halt(mid)
            payload = {'type': 'user', 'uuid': mid, 'session_id': self.sid,
                       'message': {'role': 'user', 'content': submitted}, 'parent_tool_use_id': None}
            receipt['intent_cursor'] = self.enqueue((json.dumps(payload, ensure_ascii=False) + '\n').encode(),
                                                    'user', {'message_uuid': mid})
            self.store.put('receipts', self.sid, receipt, mid)
            return receipt

    # @beacon[
    #   id=opc@NativeSession.clear_quota_halt,
    #   slice_labels=opc-basic,opc-claude-seats,
    #   kind=ast,
    #   comment=The operator's next accepted input ends a usage-limit halt: quota_halt moves to last_quota_halt and the quota_halt escalation (carried into a resumed or reconnected generation) is dispositioned by that input. Never a refusal.,
    # ]
    def clear_quota_halt(self, mid):
        halt = self.record.get('quota_halt')
        if halt:
            self.update(quota_halt=None, last_quota_halt={**halt, 'cleared_by_input': mid, 'cleared_at': time.time()})
        for iid in list(self.record.get('pending_escalations') or []):
            if (self.store.get('incidents', self.sid, iid) or {}).get('category') == 'quota_halt':
                self.host.supervisor.resolve_incident(self.sid, iid, f'usage-limit halt answered by the next input {mid}')

    def control(self, subtype, request=None, request_id=None, *, settlement_readback=False):
        if self.collection_in_progress and not settlement_readback:
            raise RuntimeError('process settlement check in progress; wait or reconnect')
        if self.record.get('state') in ('input_closing', 'exited', 'exited_draining'):
            raise RuntimeError('native input is closing or closed; reconnect first')
        if not settlement_readback:
            self.reset_settlement()
        if subtype in ('set_model', 'apply_flag_settings'):
            self.context_revision += 1
        rid = native_id(request_id) if request_id is not None else str(uuid.uuid4())
        if self.store.get('controls', self.sid, rid) is not None:
            raise ValueError('control request UUID already bound')
        payload = {'type': 'control_request', 'request_id': rid,
                   'request': {'subtype': subtype, **(request or {})}}
        body = {'request_id': rid, 'subtype': subtype, 'generation': self.generation, 'status': 'pending'}
        if settlement_readback:
            body['settlement_readback'] = True
        self.record['pending_control_requests'] = [*self.record.get('pending_control_requests', []), rid]
        self.update(last_activity_at=self.host.supervisor.clock())
        self.store.put('controls', self.sid, body, rid)
        body['intent_cursor'] = self.enqueue((json.dumps(payload) + '\n').encode(), 'control', {'request_id': rid})
        self.store.put('controls', self.sid, body, rid)
        return body

    async def verify_initial_settings(self):
        from .controls import dispatch_control, open_unverified
        try:
            await dispatch_control(self, 'get_settings', {})
        except Exception as error:
            if isinstance(error, (OSError, sqlite3.Error)):
                self.failed_storage(error)
            elif self.process.returncode is None:
                # The readback could not run (an unexpected failure, not a different choice): the
                # launch-fixed model/effort are unverified, never "mismatched". Send opens with a loud
                # settings_unverified warning; a readback that names a different choice still blocks.
                open_unverified(self, None, None, ['model', 'effort'], source='initial',
                                disclosure=f'the initial settings readback failed ({type(error).__name__}: '
                                           f'{str(error)[:300]})')


# @beacon[
#   id=opc@ClaudeHost,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Independent native owner service: adopts existing host.lock before Store/reconstruction and publishes launch/bound receipts; after bind an ordinary-endpoint probe finalizes lifecycle readiness beside resumable background forget, without early readiness, deletion waits, semantic replay or independent process recovery.,
# ]
class Host:
    # @beacon[
    #   id=opc@Host.__init__,
    #   slice_labels=opc-basic,opc-rows,
    #   kind=ast,
    #   comment=Time the one Store's open/migrate and per-record reconstruction, expose this start's committed stream-end seeds, then reconcile frozen retirements; initialize cheap loop/debt health without binding early or changing reconstruction failures.,
    # ]
    def __init__(self, root=STATE_ROOT, socket_path=SOCKET_PATH, launch_builder=None, auth_check=None,
                 clock=time.time, silence_threshold=600, monotonic=time.monotonic,
                 collection_threshold=10800, collection_cadence=180, host_lock_fd=None,
                 operation_id=None, start_token=None, ready_probe=None):
        import math
        if (not math.isfinite(collection_threshold) or collection_threshold < 0 or
                not math.isfinite(collection_cadence) or collection_cadence <= 0):
            raise ValueError('collection threshold must be finite nonnegative; cadence positive')
        self.monotonic = monotonic
        self.collection_threshold, self.collection_cadence = collection_threshold, collection_cadence
        self.reconnect_locks = {}
        self.collection_tasks = {}  # sid -> its running collection check (at most one per session)
        self.next_collection = monotonic() + collection_cadence
        self.next_orphan_check = monotonic() + 60
        self.root, self.socket_path = Path(root), Path(socket_path)
        private_dir(self.root)
        from .host_start import start_admission
        with start_admission(self.root, operation_id, inherited=host_lock_fd is not None):
            self.host_lock = owner_lock(self.root / 'host.lock', inherited_fd=host_lock_fd)
        self.start_operation, self.start_token = operation_id, start_token
        self.ready_probe, self.ready_task = ready_probe, None
        self.start_receipt = None
        self.start_receipt_error = None
        self.startup = {'store_open_ms': None, 'migrate_ms': None, 'reconstruct_ms': None,
                        'bound_at': None, 'stream_ends_seeded': 0}
        self.loop_lag_samples = deque()
        self.loop_lag_task = None
        self.publish_start_receipt('initializing')  # BEFORE Store open/reconstruction, never an early endpoint
        self.store = Store(self.root)
        self.startup.update(self.store.startup_timings)
        self.store.cached_sessions()  # the record cache list/orphan recheck/quota wake read: filled once, at open
        self.sessions = {}
        self.rows_backfill_wake = asyncio.Event()
        self.rows_backfill_wake.set()  # Startup's queued sets join the long-lived worker after bind.
        self.store.rows_writer = self.append_rows
        self.changed = asyncio.Event()
        self.launch_builder, self.auth_check = launch_builder, auth_check
        self.launch_lock = asyncio.Lock()
        self.server = None
        self.supervisor = Supervisor(self, clock, silence_threshold)
        from .observations import ObservationLedger
        self.observations = ObservationLedger(self)
        from .observer_recovery import ObserverRecovery
        self.observer_recovery = ObserverRecovery(self.store)
        self.observer_switch = None
        self.observer_publications = {}
        self.supervisor_task = None
        self.rows_backfill_task = None
        self.forget_task = None
        self.forget_queue = deque()
        self.forget_job = None
        self.forget_admitting = 0
        self.forget_status, self.forget_current = 'idle', None
        self.forget_rows_deleted = self.forget_done = self.forget_batches = 0
        self.forget_failed = []
        self.forget_max_transaction_ms = 0
        self.rows_backfill_status = 'idle'
        # Set by the Supervisor only for a tick failure no single session explains; the next clean tick clears it.
        self.supervision_error = self.supervision_error_detail = self.last_supervision_error = None
        # A lost writer is NOT permission to spawn another process. Inherited flock
        # plus PID/start witness survives host death. Uncertain claims NEVER replay.
        # reconstruct owns one batch per record. Its failure has already rolled back before the
        # separate reconstruction_failed marker; the next record never shares that transaction.
        stream_ends_before = self.store.db.execute('SELECT COUNT(*) FROM stream_ends').fetchone()[0]
        reconstruct_at = time.monotonic()
        for _, stored, text in self.store.cached_sessions():
            record = self.store.fresh(stored, text)  # never mutate the committed record cache before commit
            try:
                self.reconstruct(record)
            except Exception as error:
                self.reconstruction_failed(record, error)
        self.startup['reconstruct_ms'] = round((time.monotonic() - reconstruct_at) * 1000, 3)
        self.startup['stream_ends_seeded'] = self.store.db.execute(
            'SELECT COUNT(*) FROM stream_ends').fetchone()[0] - stream_ends_before
        self.observations.recover_frozen_retirements()

    def publish_start_receipt(self, phase):
        """Optional ownership diagnostics; load's bound time is this receipt's exact observation."""
        from .host_start import write_start_receipt
        try:
            self.start_receipt = write_start_receipt(self.root, self.socket_path,
                operation_id=self.start_operation, start_token=self.start_token, phase=phase,
                account=(self.start_receipt or {}).get('account'))
            if phase == 'bound':
                self.startup['bound_at'] = self.start_receipt['observed_at']
            self.start_receipt_error = None
        except Exception as error:
            self.start_receipt_error = type(error).__name__

    # @beacon[
    #   id=opc@Host.finalize_start_ready,
    #   slice_labels=opc-claude-restart,
    #   kind=ast,
    #   comment=After late binding, asynchronously probes ordinary health/list and shares the operation's exact factual ready/notice finalizer; no early endpoint, physical recovery coordinator, native launch or semantic replay.,
    # ]
    async def finalize_start_ready(self):
        """Probe the EXISTING bound endpoint, then share the coordinator's factual finalizer.

        No selection, stop, native launch or physical-driver ownership here. Readiness can
        finish after a coordinator dies; optional publication/notice errors stay independent.
        """
        from .accounts import selector_snapshot
        from .restart_state import finalize_service
        from .client import Client
        if not self.start_operation:
            return
        while True:
            try:
                probe = self.ready_probe or Client(self.socket_path).acall
                health = await probe('health')
                await probe('list', all=True, summary=True)
                record = self.observer_recovery.read(self.start_operation)
                if (not record or not record.get('transition') or
                        record['transition'].get('start_token') != self.start_token):
                    return
                if self.start_receipt is None:
                    self.publish_start_receipt('bound')
                if self.start_receipt is None:
                    raise RuntimeError('host start/selection binding is unconfirmed')
                self.observer_recovery.transition_update(self.start_operation, fields={
                    'candidate': {k: self.start_receipt.get(k) for k in (
                        'host_pid', 'process_witness', 'operation_id', 'start_token', 'account')}})
                finalize_service(self.store, self.start_operation, health, selector_snapshot(), witness,
                    source='host_local_endpoint_probe')
                self.publish_start_receipt('ready')
                return
            except Exception as error:
                self.start_receipt_error = type(error).__name__
                await asyncio.sleep(.25)  # no expiry/usable-startup-aborting timeout

    # @beacon[
    #   id=opc@Host.append_rows,
    #   slice_labels=opc-rows,
    #   kind=ast,
    #   comment=Atomic bounded append projection: an index range of at most 251 events admits one 250-event page, otherwise queue and wake background catch-up; a SAVEPOINT rolls failed derived work back, reloads its cache and persists unreadable while raw events commit.,
    # ]
    def append_rows(self, sid, frames=None):
        """Project at most one page in the append batch; queued history never delays raw evidence."""
        if not sid:
            # A host-wide event (sid '', e.g. a quota-check deferral) has no conversation rows. A row set
            # for '' would replay EVERY session's events, since replay treats an empty sid as "all".
            return
        session = self.sessions.get(sid)
        backend = getattr(session, 'rows', None) or SqliteRows(self.store, sid)
        backend.sync()
        if backend.data['status'] == 'complete':
            # seq is global: its difference is NOT this conversation's event count.
            pending = self.store.db.execute('SELECT seq FROM events WHERE sid=? AND seq>? ORDER BY seq LIMIT ?',
                (sid, backend.data['built_through'], ROW_PAGE_EVENTS + 1)).fetchall()
            if len(pending) > ROW_PAGE_EVENTS:
                backend.data['status'] = 'queued'
                backend.save()
                self.rows_backfill_wake.set()
                return
            self.store.db.execute('SAVEPOINT rows_append')
            try:
                project_through(RowsSource(self.store), sid, RowProjector(backend), frames, limit=ROW_PAGE_EVENTS)
            except Exception as error:
                try:
                    self.store.db.execute('ROLLBACK TO rows_append')
                    self.store.db.execute('RELEASE rows_append')
                    # Projection writes row tables only, never Store.dirty. Preserve the caller's pending records.
                    backend.reload()
                    backend.mark_unreadable(error)
                except Exception:
                    backend.cache_tag = object()  # Storage failure rolls the caller's whole batch back.
                    raise
            else:
                self.store.db.execute('RELEASE rows_append')
        # An already queued/running set is owned by the worker. Its raw appends must not trigger another inventory.

    # @beacon[
    #   id=opc@Host.forget,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Admit exact ended conversations using only owner_proved_gone: commit each exited/gone tombstone through Store.batch before queueing the single background deleter; empty_sid instead persists its restart marker and queues exactly the six junk row tables. Unknown and already-archived ids are reported, never an eligibility policy or bulk deletion on the request lane.,
    # ]
    async def forget(self, params):
        """Answer with admissions, not deletion completion; no cap on requested IDs.

        The empty-sid job is represented by '' in accepted/current, never a session
        ID, tombstone or file path. Its meta marker is committed before admission.
        """
        if set(params) == {'empty_sid'} and params['empty_sid'] is True:
            with self.store.batch():
                self.store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('forget_empty_sid',
                    json.dumps({'status': 'started', 'at': int(self.supervisor.clock() * 1000)})))
            return {'job': self.queue_forget(''), 'accepted': [''], 'skipped': []}
        if set(params) != {'execution_ids'} or not isinstance(params['execution_ids'], list):
            raise ValueError('forget requires exactly one of execution_ids (a list of exact IDs) or empty_sid: true')
        ids = params['execution_ids']
        if not all(isinstance(eid, str) for eid in ids):
            raise ValueError('execution_ids must be a list of exact IDs')
        sids = [native_id(eid) for eid in ids]  # validate the whole request before writing anything
        accepted, skipped = [], []
        job = self.forget_job
        self.forget_admitting += 1
        try:
            for sid in sids:
                eid = 'cc-' + sid
                record = self.store.get('sessions', sid)
                reason = ('unknown' if record is None else 'already-archived' if record.get('archived')
                          else 'not-ended' if not owner_proved_gone(record) else None)
                if reason:
                    skipped.append({'id': eid, 'reason': reason})
                    await asyncio.sleep(0)
                    continue
                record.update(state='exited', owner='gone', archived={
                    'at': int(self.supervisor.clock() * 1000), 'key': 'claude:' + eid, 'by': 'forget'},
                    forget_pending=True)
                with self.store.batch():
                    self.store.put('sessions', sid, record)
                job = self.queue_forget(sid)
                accepted.append(eid)
                await asyncio.sleep(0)
        finally:
            self.forget_admitting -= 1
        return {'job': job, 'accepted': accepted, 'skipped': skipped}

    def queue_forget(self, sid):
        """One worker, with further calls joining its queue; tombstones are its durable work list."""
        if self.forget_task is None or self.forget_task.done():
            self.forget_job = 'forget-' + uuid.uuid4().hex
            self.forget_rows_deleted = self.forget_done = self.forget_batches = 0
            self.forget_failed = []
            self.forget_max_transaction_ms = 0
            self.forget_status = 'running'
            self.forget_task = asyncio.create_task(self.run_forgets())
        if sid not in self.forget_queue and self.forget_current != ('cc-' + sid if sid else ''):
            self.forget_queue.append(sid)
        return self.forget_job

    def forget_health(self):
        """Progress is already in memory; health never counts the rows being deleted."""
        return {'status': self.forget_status, 'queued': len(self.forget_queue), 'current': self.forget_current,
                'rows_deleted': self.forget_rows_deleted, 'done': self.forget_done,
                'failed': [dict(item) for item in self.forget_failed]}

    def remove_forget_files(self, record):
        """Run only in the executor: the complete run folder, transcript and native subagents."""
        sid = record['native_uuid']
        paths = [self.root / sid]
        if record.get('native_transcript_path_expected'):
            transcript = Path(record['native_transcript_path_expected'])
            paths.extend((transcript, transcript.parent / sid))
        for path in paths:
            try:
                if path.is_symlink() or not path.is_dir():
                    path.unlink()
                else:
                    shutil.rmtree(path)
            except FileNotFoundError:
                pass  # already gone is success, including a restart after removing just some paths

    # @beacon[
    #   id=opc@Host.run_forgets,
    #   slice_labels=opc-basic,opc-rows,
    #   kind=ast,
    #   comment=The single background forget worker: one measured exact-sid Store.batch, at most 2000 rows with a 3 ms adaptive transaction target, then eight zero-delay loop turns so socket accept/read/dispatch/write can finish before another slice; the per-session table list then executor files and clear forget_pending for a session, exactly six row tables and complete the meta marker for empty sid. Failures keep the durable pending marker for restart.,
    # ]
    async def run_forgets(self):
        """No transaction crosses a yield; both session and junk markers survive cancellation.

        Session deletion follows FORGET_TABLES, including task-input pointers and their coverage.
        Several zero-delay turns let a pending socket request traverse accept/read/
        dispatch/write without waiting behind several deletion slices. This is
        cooperative I/O priority, not a timer, rate limit or cooling-off delay.
        """
        try:
            while self.forget_queue or self.forget_admitting:
                if not self.forget_queue:
                    await asyncio.sleep(0)
                    continue
                sid = self.forget_queue.popleft()
                self.forget_current = 'cc-' + sid if sid else ''
                try:
                    limit = 2000
                    for table in (FORGET_TABLES if sid else JUNK_TABLES):
                        while True:
                            deleted, elapsed = self.store.forget_batch(table, sid, limit)
                            self.forget_rows_deleted += deleted
                            self.forget_batches += 1
                            self.forget_max_transaction_ms = max(self.forget_max_transaction_ms, elapsed)
                            limit = min(2000, max(1, int(limit * min(2, 3 / max(elapsed, .01)))))
                            for _ in range(8):
                                await asyncio.sleep(0)
                            if not deleted:
                                break
                    if sid:
                        record = self.store.get('sessions', sid)
                        await asyncio.get_running_loop().run_in_executor(None, self.remove_forget_files, record)
                        record = self.store.get('sessions', sid)
                        with self.store.batch():
                            record.pop('forget_pending', None)
                            self.store.put('sessions', sid, record)
                    else:
                        with self.store.batch():
                            self.store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('forget_empty_sid',
                                json.dumps({'status': 'complete', 'at': int(self.supervisor.clock() * 1000)})))
                    self.forget_done += 1
                except Exception as error:
                    self.forget_failed.append({'id': self.forget_current, 'error': type(error).__name__})
                finally:
                    self.forget_current = None
                await asyncio.sleep(0)
            self.forget_status = 'failed' if self.forget_failed else 'complete'
        except asyncio.CancelledError:
            self.forget_status = 'interrupted'
            raise

    def resume_forgets(self):
        """After bind only: pending tombstones and the junk marker resume on the one worker."""
        for sid, record, _ in self.store.cached_sessions():
            if record.get('archived') and record.get('forget_pending'):
                self.queue_forget(sid)
        junk = self.store.db.execute("SELECT value FROM meta WHERE key='forget_empty_sid'").fetchone()
        if junk and json.loads(junk[0]).get('status') == 'started':
            self.queue_forget('')

    # @beacon[
    #   id=opc@Host.backfill_rows,
    #   slice_labels=opc-rows,
    #   kind=ast,
    #   comment=Long-lived wake-driven round-robin catch-up, live first: one 250-event slice per set per round with commit headroom under the 20 ms target and socket loop turns outside batches; freeze the retained startup heap against GC history walks until shutdown, refresh the queue only on new ownership, recheck tombstones, and skip persisted unreadable sets.,
    # ]
    async def backfill_rows(self):
        """Share elapsed-work slices fairly for the process's life; never hold a batch across a yield."""
        # This task starts after bind. The retained record cache is a large, long-lived graph: repeatedly
        # walking it in a full cyclic-GC pass made even tiny projected frames take 20-28 ms on the real copy.
        # freeze() moves the existing heap to GC's permanent generation without a heap walk. Refcounts still
        # free replaced acyclic records, and new allocations/cycles keep ordinary GC. One Host owns this
        # process; unfreeze on task exit so fixture/embedded lifetimes do not retain that graph afterwards.
        try:
            self.rows_backfill_status = 'running'
            self.rows_backfill_error = None
            gc.freeze()
            self.rows_backfill_wake.set()
            pending = {}
            while True:
                if self.rows_backfill_wake.is_set():
                    self.rows_backfill_wake.clear()
                    # Small metadata inventory on wake only, never an events aggregate or a scan per page/round.
                    queued = [sid for sid, body in self.store.db.execute("SELECT sid, body FROM row_sets WHERE sid!=''")
                              if json.loads(body)['status'] in ('queued', 'running')]
                    pending = {sid: pending.get(sid) for sid in queued}
                if not pending:
                    self.rows_backfill_status = 'complete'  # No queued set; unreadable is a separate persisted fact.
                    await self.rows_backfill_wake.wait()
                    continue
                self.rows_backfill_status = 'running'
                for sid in sorted(pending, key=lambda sid: sid not in self.sessions):
                    if (self.store.get('sessions', sid) or {}).get('archived'):
                        del pending[sid]
                    else:
                        deadline = self.monotonic() + (ROW_SLICE_MS - ROW_COMMIT_HEADROOM_MS) / 1000
                        backend = backfill_rows_page(self.store, sid, pending[sid],
                            stop=lambda event: self.monotonic() >= deadline)
                        if backend.data['status'] in ('queued', 'running') and backend.cache_tag is not None:
                            pending[sid] = backend
                        else:
                            del pending[sid]
                    for _ in range(ROW_YIELD_TURNS):
                        await asyncio.sleep(0)  # Commit first; let the socket pipeline finish before the next slice.
        except Exception as error:
            self.rows_backfill_status = 'failed'
            self.rows_backfill_error = f'{type(error).__name__}: {str(error)[:200]}'
        finally:
            gc.unfreeze()

    # @beacon[
    #   id=opc@Host.rows_health,
    #   slice_labels=opc-rows,
    #   kind=ast,
    #   comment=The existing five-key rows-health answer from committed in-memory counters and maps, never decoding row-set bodies per call; unreadable stays in remaining_sessions and its own list, while complete still means no queued worker debt.,
    # ]
    def rows_health(self):
        """Report committed progress without row-set reads; unreadable still counts as not complete."""
        counters = self.store.row_counters()
        return {'status': self.rows_backfill_status,
                'error': getattr(self, 'rows_backfill_error', None),
                'remaining_sessions': counters.remaining_sessions,
                'built_through': dict(counters.built_through),
                'unreadable': list(counters.unreadable)}

    # @beacon[
    #   id=opc@Host.measure_loop_lag,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Serve-owned one-second ticker records wake-up lateness over the last 60 seconds; reschedule from each observed wake rather than busy-looping missed ticks, with no control, restart or recovery action.,
    # ]
    async def measure_loop_lag(self):
        """Observe the loop only; a delayed wake never authorizes an action or a work limit."""
        due = self.monotonic() + LOOP_LAG_TICK_SECONDS
        while True:
            await asyncio.sleep(max(0, due - self.monotonic()))
            observed = self.monotonic()
            self.loop_lag_samples.append((observed, max(0, observed - due) * 1000))
            while self.loop_lag_samples and self.loop_lag_samples[0][0] < observed - LOOP_LAG_WINDOW_SECONDS:
                self.loop_lag_samples.popleft()
            due = observed + LOOP_LAG_TICK_SECONDS

    # @beacon[
    #   id=opc@Host.load_health,
    #   slice_labels=opc-basic,opc-rows,
    #   kind=ast,
    #   comment=Additive cheap load facts: committed queued/running set and exact event debt, 60-second ticker lag maximum, this start's timings/seed count and existing bound-receipt instant, plus WAL stat and available filesystem bytes; unreadable sets are not perpetual catch-up debt.,
    # ]
    def load_health(self):
        """Small counters and filesystem facts; unknown stat failures stay unknown, never gate health."""
        now = self.monotonic()
        while self.loop_lag_samples and self.loop_lag_samples[0][0] < now - LOOP_LAG_WINDOW_SECONDS:
            self.loop_lag_samples.popleft()
        counters = self.store.row_counters()
        try:
            wal_bytes = (self.root / 'host.sqlite3-wal').stat().st_size
        except FileNotFoundError:
            wal_bytes = 0
        except OSError:
            wal_bytes = None
        try:
            volume = os.statvfs(self.root)
            free_bytes = volume.f_bavail * volume.f_frsize
        except OSError:
            free_bytes = None
        return {'loop_lag_ms_max_60s': round(max((lag for _, lag in self.loop_lag_samples), default=0), 3),
                'rows_behind_sets': counters.behind_sets, 'rows_behind_events': counters.behind_events,
                'startup': dict(self.startup), 'wal_bytes': wal_bytes, 'free_bytes': free_bytes}

    def row_state(self, execution_id):
        sid = native_id(execution_id)
        state = SqliteRows(self.store, sid).state()
        newest = self.store.db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE sid=?', (sid,)).fetchone()[0]
        return sid, {'epoch': state['epoch'] if state else None, 'status': state['status'] if state else 'queued',
                     'built_through': state['built_through'] if state else 0,
                     'counts': state['counts'] if state else {}, 'as_of': newest}

    # @beacon[
    #   id=opc@Host.rows,
    #   slice_labels=opc-rows,
    #   kind=ast,
    #   comment=Read-only durable row pages: scope tail/forward position, exact origin or exclusive agent ordinal window; lossless ordinal continuation across scopes, stable epoch/status/watermark and a bounded incidents page.,
    # ]
    def rows(self, execution_id, scope=None, from_seq=None, by_origin=None, agent_window=None,
             incident_offset=0, limit=100):
        sid, result = self.row_state(execution_id)
        limit = min(max(int(limit), 1), 500)
        if sum(value is not None for value in (from_seq, by_origin, agent_window)) > 1:
            raise ValueError('row selectors are mutually exclusive')
        args, sql, order = [sid], 'SELECT body FROM rows WHERE sid=?', 'seq'
        if agent_window is not None:
            if scope is not None and (not isinstance(scope, str) or not scope.startswith('agent:')):
                raise ValueError('agent_window requires absent scope or one agent: scope, never main')
            if (not isinstance(agent_window, list) or len(agent_window) != 2 or
                    type(agent_window[0]) is not int or agent_window[0] < -1 or
                    (agent_window[1] is not None and
                     (type(agent_window[1]) is not int or agent_window[1] < agent_window[0]))):
                raise ValueError('agent_window must be exclusive [lo,hi], lo>=-1 and hi integer or null')
            sql += " AND scope LIKE 'agent:%' AND ordinal>?"
            args.append(agent_window[0])
            if agent_window[1] is not None:
                sql += ' AND ordinal<?'
                args.append(agent_window[1])
            if scope is not None:
                sql += ' AND scope=?'
                args.append(scope)
            order = 'ordinal'
        elif by_origin is not None:
            sql += ' AND origin=?'
            args.append(by_origin)
            if scope is not None:
                sql += ' AND scope=?'
                args.append(scope)
            order = 'ordinal'
        else:
            scope = scope or 'main'
            if from_seq is None:
                from_seq = max(0, result['counts'].get(scope, 0) - limit)
            if type(from_seq) is not int or from_seq < 0:
                raise ValueError('from_seq must be a nonnegative row position')
            sql += ' AND scope=? AND seq>=?'
            args.extend((scope, from_seq))
        found = self.store.db.execute(sql + f' ORDER BY {order} LIMIT ?', (*args, limit + 1)).fetchall()
        values = [SqliteRows.decode(row[0], self.store, sid) for row in found[:limit]]
        offset = max(0, int(incident_offset))
        incidents = [json.loads(r[0]) for r in self.store.db.execute(
            'SELECT body FROM row_incidents WHERE sid=? AND n>=? ORDER BY n LIMIT 100', (sid, offset))]
        count = self.store.db.execute('SELECT COUNT(*) FROM row_incidents WHERE sid=?', (sid,)).fetchone()[0]
        result.update(rows=values, incidents=incidents, incident_count=count, has_more=len(found) > limit,
                      next_from_seq=(values[-1]['seq'] + 1 if values and len(found) > limit and agent_window is None else None))
        state = SqliteRows(self.store, sid).state()
        result['agent_info'] = [{'generation': key[0], 'call': key[1], **info}
                                for key, info in (state['agent_info'] if state else [])]
        if agent_window is not None and result['has_more']:
            result['next_after_ordinal'] = values[-1]['ordinal']
        return result

    # @beacon[
    #   id=opc@Host.rows_changed,
    #   slice_labels=opc-rows,
    #   kind=ast,
    #   comment=Read-only event-seq change pages in (changed,seq) order with an exact continuation, so capped tie groups cannot lose rows; every answer includes the committed session as_of.,
    # ]
    def rows_changed(self, execution_id, scope='main', since_changed=0, after_seq=-1, limit=100):
        sid, result = self.row_state(execution_id)
        limit = min(max(int(limit), 1), 500)
        found = self.store.db.execute('SELECT body FROM rows WHERE sid=? AND scope=? AND '
            '(changed,seq)>(?,?) ORDER BY changed,seq LIMIT ?',
            (sid, scope, int(since_changed), int(after_seq), limit + 1)).fetchall()
        values = [SqliteRows.decode(r[0], self.store, sid) for r in found[:limit]]
        result.update(rows=values, has_more=len(found) > limit)
        if result['has_more']:
            result['next'] = [values[-1]['changed'], values[-1]['seq']]
        return result

    # @beacon[
    #   id=opc@Host.row_witnesses,
    #   slice_labels=opc-rows,
    #   kind=ast,
    #   comment=Read-only exact-mirror content witness keys for one conversation, never an event-history replay.,
    # ]
    def row_witnesses(self, execution_id):
        sid, result = self.row_state(execution_id)
        result['witnesses'] = [json.loads(r[0]) for r in self.store.db.execute(
            "SELECT k FROM row_keys WHERE sid=? AND kind='witness' ORDER BY k", (sid,))]
        return result

    # @beacon[
    #   id=opc@Host.row_diagnostics,
    #   slice_labels=opc-rows,
    #   kind=ast,
    #   comment=Read-only diagnostic pages, optionally scoped, in projection order; raw bodies and exception payloads are never logged.,
    # ]
    def row_diagnostics(self, execution_id, scope=None, offset=0, limit=100):
        sid, result = self.row_state(execution_id)
        offset, limit = max(0, int(offset)), min(max(int(limit), 1), 500)
        sql, args = 'SELECT body FROM row_diagnostics WHERE sid=?', [sid]
        if scope is not None:
            sql += ' AND scope=?'
            args.append(scope)
        found = self.store.db.execute(sql + " ORDER BY json_extract(body,'$.revision'),scope,n LIMIT ? OFFSET ?",
                                      (*args, limit + 1, offset)).fetchall()
        result.update(diagnostics=[json.loads(r[0]) for r in found[:limit]], has_more=len(found) > limit,
                      next_offset=offset + limit if len(found) > limit else None)
        return result

    # @beacon[
    #   id=opc@Host.reconstruct,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Startup reconciliation in one record batch: tombstones return before probes/writes; witness/UUID-lock ownership, proved-no-process launch_failed, stream ends, raw gaps, receipts, incidents and schedules commit once or roll back together. Uncertain claims never replay or turn missing worked history into empty genesis; the caller marks a failure after rollback.,
    # ]
    def reconstruct(self, record):
        if record.get('archived'):
            return
        with self.store.batch():
            sid = record['native_uuid']
            alive = bool(record.get('process_witness') and witness(record['pid']) == record['process_witness'])
            missing = not (self.root / sid).is_dir()
            held = False
            if not missing:
                try:
                    os.close(owner_lock(self.root / sid / 'owner.lock'))
                except RuntimeError:
                    held = True
            no_process = record.get('state') == 'launch_failed' and not record.get('process_witness')
            record.update(owner='unknown' if alive or held else 'gone',
                          state='ownership_unknown' if alive or held else 'launch_failed' if no_process else 'exited')
            if missing:
                # Pruned by hand (or never created): no lock to hold and no snapshot to resume from.
                record['session_directory_missing'] = True
            if not alive and not held:
                # The old owner is proved absent and the UUID lock is free: the retry/background
                # clearing events can no longer arrive, so those stale markers are dropped here
                # too (values kept as evidence), never left to withhold Send/recovery/restart.
                stale = clear_stale_native_markers(record)
                if stale:
                    self.supervisor.incident(record, 'native_markers_cleared', 'retain',
                        detail={'reason': 'host reconstruction observed the recorded PID/start witness '
                                         'absent and the UUID lock free; the retry/background clearing '
                                         'events can no longer arrive',
                                'cleared': stale})
            # A host crash between raw fsync and index commit leaves recoverable bytes,
            # not permission to silently drop them or replay an uncertain intent.
            if self.index_raw_gaps(record, reason='host stopped before indexing these retained bytes'):
                record['protocol_attention'] = True
            self.store.put('sessions', sid, record)
            self.reconcile_pending_inputs(record)
            for receipt in self.store.receipts(sid):
                if not receipt.get('completed') and not released_receipt(receipt):
                    receipt['status'] = 'uncertain'
                    self.store.put('receipts', sid, receipt, receipt['message_uuid'])
            for action in self.store.records('recoveries', sid):
                if action['status'] in ('claimed', 'accepted'):
                    receipt = self.store.get('receipts', sid, action['action_uuid'])
                    if receipt and receipt.get('completed'):
                        action['status'] = 'completed'
                    else:
                        action['status'] = 'blocked'
                        record['recovery_attempt_failed'] = True
                        self.supervisor.incident(record, 'recovery_uncertain', 'escalate',
                            action['incident_cursor'], action['source_message_uuids'],
                            {'action_uuid': action['action_uuid'], 'semantic_replay': False}, True)
                    self.store.put('recoveries', sid, action, action['recovery_id'])
                elif missing and action['status'] == 'scheduled':
                    action.update(status='cancelled', operator_disposition='session directory missing; nothing can resume it')
                    self.store.put('recoveries', sid, action, action['recovery_id'])
                    if record.get('pending_recovery') == action['recovery_id']:
                        record['pending_recovery'] = None
                        self.store.put('sessions', sid, record)
            incomplete = [r['message_uuid'] for r in self.store.receipts(sid)
                          if not r.get('completed') and not released_receipt(r)]
            if incomplete and record.get('execution_id'):
                if alive or held:
                    self.supervisor.incident(record, 'ownership_unknown', 'escalate', mids=incomplete,
                        detail={'reason': 'old owner witness or inherited lock still held; no second writer'}, attention=True)
                elif missing:
                    self.supervisor.incident(record, 'session_directory_missing', 'escalate', mids=incomplete,
                        detail={'reason': 'its session directory is gone, so nothing can resume it; release the inputs '
                                          '(release_input) or start a fresh session', 'semantic_replay': False},
                        attention=True)
                elif record.get('protocol_attention'):
                    self.supervisor.incident(record, 'reconciliation_uncertain', 'escalate', mids=incomplete,
                        detail={'reason': 'retained unindexed/protocol evidence; never replay'}, attention=True)
                elif record.get('initialized') and record.get('process_witness'):
                    incident = self.supervisor.incident(record, 'crash_missing_result', 'resume_same_uuid',
                        mids=incomplete, detail={'reason': 'restart proved old PID/start witness absent and lock free'})
                    self.supervisor.schedule(record, incident, 'crash', incomplete,
                                              self.supervisor.clock(), 'restart_proved_exit')
                else:
                    self.supervisor.incident(record, 'startup_failure', 'alert_no_retry', mids=incomplete,
                        detail={'reason': 'no initialized owner with proved missing-result exit'}, attention=True)
            elif missing:
                self.supervisor.incident(record, 'session_directory_missing', 'retain',
                    detail={'reason': 'its session directory is gone; the record stays as history and is never resumed'})
            SqliteRows.requeue(self.store, sid)

    def reconstruction_failed(self, record, error):
        """A record startup could not reconstruct: marked, never a host that does not start. Its owner stays
        unknown while the recorded PID/start witness could still be alive (the orphan recheck proves it gone
        later); a record that never started a process is history."""
        try:
            alive = bool(record.get('process_witness') and witness(record.get('pid')) == record['process_witness'])
        except Exception:
            alive = True
        unproved = alive or bool(record.get('process_witness'))
        record.update(reconstruction_error=type(error).__name__,
                      owner='unknown' if unproved else 'gone', state='ownership_unknown' if unproved else 'exited')
        try:
            self.store.put('sessions', record['native_uuid'], record)
            self.supervisor.incident(record, 'reconstruction_failed', 'escalate', attention=True,
                detail={'failure': type(error).__name__, 'execution_action': 'none; the orphan recheck looks again'})
        except Exception:
            pass  # a record too malformed to store or journal is skipped; the host still starts

    # @beacon[
    #   id=opc@Host.index_raw_gaps,
    #   slice_labels=opc-basic,opc-rows,
    #   kind=ast,
    #   comment=One primary-key stream-end lookup per stdout/stderr/stdin; only a missing end seeds from that sid's events, then retained unindexed bytes become visible non-replayed gap events in the same batch.,
    # ]
    def index_raw_gaps(self, record, reason='retained bytes the index never recorded'):
        """Index retained bytes the event log never recorded (a stop or a storage failure between the raw fsync
        and the index commit) as raw_reconciliation_gap events: kept and visible, never parsed or replayed.
        Cached stream ends are exact for this writer; migrate_rows invalidates them after an older writer.
        A missing stream is aggregated once, then even a zero end is persisted in this batch.
        """
        gaps = []
        sid, generation = record['native_uuid'], record['generation']
        with self.store.batch():
            for stream in ('stdout', 'stderr', 'stdin'):
                path = self.store.path(sid, generation, stream)
                cached = self.store.db.execute('SELECT end FROM stream_ends WHERE sid=? AND generation=? AND stream=?',
                                               (sid, generation, stream)).fetchone()
                if cached is None:
                    end = self.store.db.execute('SELECT COALESCE(MAX(offset+length),0) FROM events '
                        'WHERE sid=? AND generation=? AND stream=?', (sid, generation, stream)).fetchone()[0]
                    self.store.db.execute('INSERT INTO stream_ends(sid,generation,stream,end) VALUES (?,?,?,?)',
                                          (sid, generation, stream, end))
                else:
                    end = cached[0]
                size = path.stat().st_size if path.exists() else 0
                if size > end:
                    self.store.event(sid, generation, 'raw_reconciliation_gap',
                        {'reason': reason, 'semantic_replay': False}, stream=stream, offset=end, length=size - end)
                    gaps.append({'stream': stream, 'offset': end, 'length': size - end})
        return gaps

    # @beacon[
    #   id=opc@Host.fork_base,
    #   slice_labels=opc-claude-seats,
    #   kind=ast,
    #   comment=The base a fork starts from: a host-owned non-archived session in the fork's role and cwd with a completed turn, its own turn closed now (fork_blockers), its stored snapshot present. A tombstone names its History key before any path probe. A Manager base only through its own self-fork: a worker fork, the binding of the Manager's live generation, its open turn admitted.,
    # ]
    def fork_base(self, fork_from, *, sid, cwd, role, self_binding=None):
        """The base record a fork of ``fork_from`` starts from (its live record when its process runs), or a refusal.

        A Manager is forked only by itself: ``self_binding`` is the context binding of the Manager's running
        generation (CC_CONTEXT_BINDING, which only its own process tree holds), so the call provably comes from
        inside that Manager; the fork's role is ``worker``, never a second Manager. Its open turn is the
        conversation being copied, so it is admitted, and its transcript on disk stands for a completed turn.
        A self-fork of anything but a Manager is refused: a fork never forks. A tombstone
        names its archive instead of probing the removed snapshot."""
        base_sid = native_id(fork_from)
        if base_sid == sid:
            raise ValueError('a session cannot fork itself')
        stored = self.store.get('sessions', base_sid)
        if not stored:
            raise ValueError(f'fork_from cc-{base_sid}: no such host-owned session')
        refuse_archived(stored)
        live = base_sid in self.sessions and self.sessions[base_sid].process.returncode is None
        base = self.sessions[base_sid].record if base_sid in self.sessions else stored
        manager = base.get('role') == 'Manager'
        if manager or self_binding is not None:
            from .context import binding_matches
            if not manager:
                raise ValueError(f"a self-fork (oc_seat quick --fork-self) is a Manager's only: cc-{base_sid} is a "
                                 f"{base.get('role')} session, and a fork never forks")
            if role != 'worker':
                raise ValueError(f'a Manager is forked only into a worker (its quick fork worker), never a {role}')
            if not (live and binding_matches(base, self_binding)):
                raise ValueError('a Manager conversation is forked only from inside its own running turn '
                                 "(oc_seat quick --fork-self, proved by the context binding of the Manager's live "
                                 'generation)')
        elif role != base.get('role'):
            raise ValueError(f"a fork keeps its base's role ({base.get('role')}), not {role}")
        if cwd != base.get('cwd'):
            raise ValueError(f"a fork runs in its base's cwd ({base.get('cwd')}): Claude Code finds the base's "
                             'transcript by cwd')
        if manager:
            transcript = base.get('native_transcript_path_expected')
            if not transcript or not Path(transcript).is_file():
                raise ValueError("the Manager's transcript is not on disk yet: there is no conversation to fork")
        elif not base.get('last_result_cursor'):
            raise ValueError('the base has no completed turn yet: there is no conversation to fork')
        blockers = fork_blockers(base, self_call=manager)
        if blockers:
            raise RuntimeError('the base cannot be forked now: ' + '; '.join(blockers) +
                               ' (a fork copies its conversation as it stands; wait for its turn to end)')
        if not base.get('run_dir') or not (Path(base['run_dir']) / 'prompt.md').is_file():
            raise ValueError("the base's stored run directory is gone (pruned?), so it cannot be forked")
        return base

    # @beacon[
    #   id=opc@Host.launch,
    #   slice_labels=opc-claude-seats,
    #   kind=ast,
    #   comment=Registers/spawns under transition/UUID gates with one selected account and stored snapshot parity; resumes/forks refuse tombstones, rechecked after auth yields. Fresh generations start complete task-input coverage before stdout. A failed admitted launch is launch_failed only after no process and a free UUID lock are proved, preserving same-Manager intent. Manager self-fork, INITIALIZE_HOOKS and root_search_hook witnesses remain unchanged.,
    # ]
    async def launch(self, *, native_uuid, cwd, model, effort, system_prompt=None, resume=False,
                     role='worker', agent=None, program=None, socket_handle=None, parent=None,
                     workflowy_url=None, owning_conductor=None, context_target=None, labels=None,
                     fork_from=None, fork_self_binding=None):
        """``fork_from`` (a ``cc-`` id) launches a fork of that base: a new session with the base's whole
        conversation (``--resume BASE --fork-session --session-id NEW``), built from the base's stored
        snapshot so its prompt, settings and MCP bytes are identical (the prompt cache hit). ``system_prompt``
        may then be omitted (the base's is used); given, it must be the base's. ``fork_self_binding`` (the
        caller's CC_CONTEXT_BINDING) is a Manager forking itself into a worker from inside its own turn
        (``oc_seat quick --fork-self``; fork_base checks it); the fork carries the Manager's stored agent
        definitions and request environment (build_launch). Tombstones are refused before
        path probes and again after asynchronous auth, never resurrected by an in-flight resume.
        Each new process generation starts task-input coverage before its first stdout frame; old
        generations are never swept here. A pre-process failure preserves
        its registered snapshot/UUID; only a free owner lock proves launch_failed. A possible
        child keeps uncertain ownership, never permission to create a second native owner."""
        from .launch import build_launch
        from .context import validate_target
        from .accounts import launch_gate, selected_account
        validate_target(context_target)
        labels = validate_labels(labels)
        sid = native_id(native_uuid)
        if role not in ('Manager', 'conductor', 'worker', 'helper', 'base'):
            raise ValueError('explicit supported role required')
        if not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise ValueError('operator CWD must be an existing absolute directory')
        if fork_from is not None and resume:
            raise ValueError('a fork is a new session: fork_from never resumes')
        if fork_self_binding is not None and fork_from is None:
            raise ValueError('fork_self_binding belongs to a self-fork (with fork_from)')
        if system_prompt is None and fork_from is None:
            raise ValueError('system_prompt required')
        async with self.launch_lock, launch_gate(self.root):
            old = self.store.get('sessions', sid)
            if resume:
                refuse_archived(old)
            if old and not resume:
                raise RuntimeError('UUID already registered; explicit resume required')
            if resume and not old:
                raise ValueError('resume requires an existing host-owned session snapshot')
            if sid in self.sessions and self.sessions[sid].process.returncode is None:
                raise RuntimeError('UUID already has a live owner')
            if old and old.get('process_witness') and witness(old['pid']) == old['process_witness']:
                raise RuntimeError('old PID/start witness still alive; ownership unknown')
            base = None
            if fork_from is not None:
                base = self.fork_base(fork_from, sid=sid, cwd=cwd, role=role, self_binding=fork_self_binding)
                base_prompt = (Path(base['run_dir']) / 'prompt.md').read_bytes()
                if system_prompt is None:
                    system_prompt = base_prompt.decode('utf-8')
                elif system_prompt.encode() != base_prompt:
                    raise ValueError("a fork keeps its base's system prompt (the prompt cache hit needs it)")
            elif old and old.get('forked_from') and self.empty_conversation_proved(sid, old):
                # A fork has no transcript before its first message (Part A, T9): an unstarted fork whose
                # process ended is forked from its base again, never restarted with an empty history.
                base = self.fork_base(old['forked_from'], sid=sid, cwd=cwd, role=role)
            directory = private_dir(self.root / sid)
            lock_fd = owner_lock(directory / 'owner.lock')
            process = None
            try:
                if base is not None and not self.launch_builder:
                    for filename, hash_key in (('settings.json', 'settings_sha256'), ('mcp.json', 'mcp_sha256')):
                        expected = base.get('launch', {}).get(hash_key)
                        if not expected or hashlib.sha256((Path(base['run_dir']) / filename).read_bytes()).hexdigest() != expected:
                            raise ValueError("the base's stored configuration hash differs; nothing forked")
                if old:
                    # U03 resumes only the exact launch snapshot; no new prompt/config.
                    for key, value in {'cwd': cwd, 'requested_model': model,
                                       'requested_effort': effort, 'role': role}.items():
                        if old.get(key) != value:
                            raise ValueError('resume requires same launch choices')
                    if hashlib.sha256(system_prompt.encode()).hexdigest() != old['prompt_sha256']:
                        raise ValueError('resume requires original system prompt')
                    if not self.launch_builder:
                        for filename, hash_key in (('settings.json', 'settings_sha256'), ('mcp.json', 'mcp_sha256')):
                            expected = old.get('launch', {}).get(hash_key)
                            if not expected or hashlib.sha256((Path(old['run_dir']) / filename).read_bytes()).hexdigest() != expected:
                                raise ValueError('stored resume configuration hash differs; input withheld')
                    for key, supplied in {'agent': agent, 'program': program, 'socket': socket_handle,
                                            'parent': parent, 'owning_conductor': owning_conductor}.items():
                        if supplied is not None and supplied != old.get(key):
                            raise ValueError('resume cannot change execution provenance')
                    agent, program = old.get('agent'), old.get('program')
                    socket_handle, parent = old.get('socket'), old.get('parent')
                    owning_conductor = old.get('owning_conductor')
                    if context_target is not None and context_target != old.get('context_target'):
                        raise ValueError('resume cannot change context target')
                    context_target = old.get('context_target')
                    if labels is not None and labels != old.get('labels'):
                        raise ValueError('resume cannot change execution labels')
                    labels = old.get('labels')
                account = selected_account()
                auth = await self.auth_check() if self.auth_check else await subscription_preflight(account)
                if old:
                    refuse_archived(self.store.get('sessions', sid))
                if base:
                    refuse_archived(self.store.get('sessions', base['native_uuid']))
                generation = old['generation'] + 1 if old else 1
                run_dir = private_dir(directory / f'g{generation:06d}')
                # A fork (or a re-fork) is built from the stored snapshot: the base's, or its own unstarted one.
                fork_kwargs = ({'fork_from': base['native_uuid'], 'snapshot_source': (old or base)['run_dir']}
                               if base is not None else {})
                spec = (self.launch_builder or build_launch)(run_dir, native_uuid=sid, cwd=cwd,
                    model=model, effort=effort, system_prompt=system_prompt, resume=resume and base is None,
                    workflowy_url=workflowy_url,
                    **({'role': role, 'account': account, **({'snapshot_source': old['run_dir']} if old else {}), **fork_kwargs}
                       if not self.launch_builder else {}))
                if base is not None and old:
                    spec['safe']['resume_strategy'] = 're-fork: an unstarted fork forked from its base again'
                elif (old and not self.launch_builder and '--resume' in spec['argv'] and
                        self.empty_conversation_proved(sid, old)):
                    # Native --resume refuses a UUID that never had a turn (no transcript file).
                    # Restore that provably EMPTY registered conversation with the same UUID,
                    # original snapshots and new host generation, never a history-loss fallback.
                    index = spec['argv'].index('--resume')
                    spec['argv'][index] = '--session-id'
                    spec['safe']['argv'] = list(spec['argv'])
                    spec['safe']['resume_strategy'] = 'same-uuid empty registered conversation'
                # Config carries literal env references so it stays byte-identical on resume;
                # the callback capability rotates each generation and never enters raw launch logs.
                context_binding = secrets.token_urlsafe(32)
                spec['env'].update(CC_CONTEXT_EXECUTION_ID='cc-' + sid,
                    CC_CONTEXT_BINDING=context_binding, CC_CONTEXT_HOST_SOCKET=str(self.socket_path))
                snapshot = old or base   # a resume's own previous generation, or a fresh fork's base
                if snapshot:
                    endpoint_change = None
                    for name in ('settings.json', 'mcp.json', 'prompt.md'):
                        prior = Path(snapshot['run_dir']) / name
                        current = run_dir / name
                        if prior.read_bytes() != current.read_bytes():
                            if name == 'mcp.json' and not self.launch_builder:
                                from .launch import mcp_url_only_change
                                migration = mcp_url_only_change(prior.read_bytes(), current.read_bytes())
                                if (migration.get('url_only') and
                                        migration.get('current_url') == spec['safe'].get('workflowy_url')):
                                    endpoint_change = migration
                                    continue
                            raise ValueError("fork configuration differs from the base's snapshot" if not old else
                                             'resume configuration differs; explicit U04 correction required')
                    if endpoint_change:
                        self.store.event(sid, generation, 'workflowy_endpoint_reresolved',
                            {**endpoint_change, 'source': spec['safe'].get('workflowy_url_source'),
                                'all_other_snapshot_bytes': 'identical',
                                'prior_generation': old['generation'] if old else None,
                                **({} if old else {'fork_base': 'cc-' + base['native_uuid']})})
                private_file(run_dir / 'launch.json', json.dumps(spec['safe'], indent=2).encode())
                if role == 'Manager':
                    from .manager import publish_manager_prompt
                    publish_manager_prompt(system_prompt, self.root / 'manager-prompt-latest.md')
                record = {'execution_id': 'cc-' + sid, 'native_uuid': sid, 'backend': 'claude',
                    'generation': generation, 'role': role, 'agent': agent, 'program': program,
                    'socket': socket_handle, 'parent': parent, 'cwd': cwd,
                    'labels': labels, 'account': {key: account.get(key) for key in
                        ('label', 'config_dir', 'identity', 'epoch')},
                    # A fork's provenance: its base, and the base's last completed call when it was forked (what
                    # its first call should read from the cache); its own first call is kept as it completes.
                    'forked_from': 'cc-' + base['native_uuid'] if base is not None else (old or {}).get('forked_from'),
                    'fork_base_call': fork_call(base) if base is not None else (old or {}).get('fork_base_call'),
                    'fork_first_call': None if base is not None else (old or {}).get('fork_first_call'),
                    'first_launched_at': (old or {}).get('first_launched_at') or time.time(),
                    'last_input_uuid': (old or {}).get('last_input_uuid'),
                    'last_input_at': (old or {}).get('last_input_at'),
                    'context_target': context_target,
                    'context_generation_started_at': time.time(),
                    'context_binding_sha256': hashlib.sha256(context_binding.encode()).hexdigest(),
                    'owning_conductor': owning_conductor,
                    'requested_model': model, 'requested_effort': effort, 'actual_model': None,
                    'actual_effort': None, 'auth': auth, 'run_dir': str(run_dir),
                    'state': 'launching', 'owner': 'host', 'initialized': False,
                    'pending_message_uuids': [r['message_uuid'] for r in self.store.receipts(sid)
                                                if not r.get('completed') and not released_receipt(r)], 'launch': spec['safe'],
                    'last_activity_at': self.supervisor.clock(), 'setting_control_pending': True,
                    # A resume starts a fresh native generation; the old generation's retry and
                    # background markers are dead evidence and are never copied into it.
                    'background_tasks': {}, 'background_unsettled': False, 'native_wake_pending': False,
                    'background_level_known': False, 'native_session_state': 'unknown',
                    'native_state_required': (not bool(self.launch_builder) or
                        bool(spec['safe'].get('collector_envelope'))),
                    'pending_native_requests': {}, 'pending_control_requests': [],
                    'pending_recovery': (old or {}).get('pending_recovery'),
                    'pending_escalations': (old or {}).get('pending_escalations', []),
                    'recovery_attempt_failed': (old or {}).get('recovery_attempt_failed', False),
                    'prompt_sha256': hashlib.sha256(system_prompt.encode()).hexdigest(),
                    'native_transcript_path_expected': str(Path.home() / '.claude/projects' /
                         re.sub(r'[^a-zA-Z0-9]', '-', cwd) / (sid + '.jsonl')),
                    # Whether this generation's Glob honours .gitignore (its settings env says
                    # CLAUDE_CODE_GLOB_NO_IGNORE=false: launch.FORCED_PROFILE_ENV on a fresh launch; a resume
                    # keeps its stored profile, which may predate it). The root-search guard reads it.
                    'glob_honours_gitignore': ((spec.get('profile') or {}).get('env') or {}).get(
                        GLOB_IGNORE_ENV) == 'false'}
                self.store.put('sessions', sid, record)
                with self.store.batch():
                    self.store.mark_task_inputs_complete(sid, generation)
                    self.store.event(sid, generation, 'launch_intent', {'execution_id': 'cc-' + sid})
                process = await asyncio.create_subprocess_exec(*spec['argv'], cwd=cwd, env=spec['env'],
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, pass_fds=(lock_fd,), start_new_session=True)
                record.update(pid=process.pid, process_witness=witness(process.pid), state='initializing')
                session = NativeSession(self, record, process, lock_fd)
                self.sessions[sid] = session
                session.update()
                session.event('process_started', {'pid': process.pid, 'process_witness': record['process_witness']})
                session.start()
                record['root_search_hook'] = fresh_root_search_witness(record['generation'])
                record['initialize_request_id'] = session.control('initialize', {'hooks': INITIALIZE_HOOKS})['request_id']
                session.update()
                return record
            except BaseException as error:
                if process is None:
                    os.close(lock_fd)
                else:
                    # Do not kill/retry a possibly running CLI after durable state failure.
                    # If normal drains weren't started yet, keep both pipes drained.
                    if sid not in self.sessions:
                        record.update(pid=process.pid, process_witness=witness(process.pid), state='ownership_unknown')
                        session = NativeSession(self, record, process, lock_fd)
                        self.sessions[sid] = session
                        session.failed_storage(RuntimeError('launch journaling failed'))
                        session.start()
                    else:
                        self.sessions[sid].failed_storage(error)
                if (not isinstance(error, asyncio.CancelledError) and
                        not (self.store.get('sessions', sid) or {}).get('archived')):
                    prior = self.store.get('sessions', sid) or {'native_uuid': sid, 'generation': 0,
                        'execution_id': 'cc-' + sid, 'state': 'launch_failed', 'owner': 'gone', 'initialized': False,
                        'role': role, 'parent': parent, 'owning_conductor': owning_conductor,
                        'program': program, 'socket': socket_handle, 'labels': labels,
                        'last_activity_at': self.supervisor.clock()}  # so the list window can age it out
                    if process is None and prior.get('state') == 'launching' and not prior.get('process_witness'):
                        try:
                            probe = owner_lock(directory / 'owner.lock')
                        except RuntimeError:
                            prior.update(owner='unknown', state='ownership_unknown')
                        else:
                            os.close(probe)
                            prior.update(owner='gone', state='launch_failed', launch_failure=type(error).__name__)
                        self.store.put('sessions', sid, prior)
                    self.supervisor.incident(prior, 'launch_failure', 'alert_no_retry',
                        detail={'failure': type(error).__name__, 'automatic_respawn': False}, attention=True)
                raise

    async def resume_snapshot(self, execution_id):
        old = self.state(execution_id)
        refuse_archived(old)
        if not old.get('run_dir') or not (Path(old['run_dir']) / 'prompt.md').is_file():
            raise ValueError('its stored run directory is gone (pruned?), so this conversation cannot be resumed; '
                             'start a fresh session')
        return await self.launch(native_uuid=old['native_uuid'], cwd=old['cwd'],
            model=old['requested_model'], effort=old['requested_effort'],
            system_prompt=(Path(old['run_dir']) / 'prompt.md').read_bytes().decode('utf-8'),
            resume=True, role=old['role'], agent=old.get('agent'), program=old.get('program'),
            socket_handle=old.get('socket'), parent=old.get('parent'), owning_conductor=old.get('owning_conductor'),
            labels=old.get('labels'))

    # @beacon[
    #   id=opc@Host.empty_conversation_proved,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Allows same-UUID genesis only when the complete host journal proves no accepted message or native turn ever existed; never infer emptiness from missing native history.,
    # ]
    def empty_conversation_proved(self, sid, record):
        """Only the completely witnessed absence of any turn allows same-UUID genesis.

        Never use missing native files as proof: a deleted/lost worked transcript must fail.
        """
        if (self.store.receipts(sid) or record.get('protocol_attention') or
                record.get('infrastructure_error') or record.get('last_assistant_cursor') or
                record.get('last_result_cursor') or record.get('pending_escalations')):
            return False
        for row in self.store.db.execute('SELECT kind, detail FROM events WHERE sid=?', (sid,)):
            if row['kind'] in ('raw_reconciliation_gap', 'malformed_frame', 'unknown_frame', 'unsupported_frame'):
                return False
            detail = json.loads(row['detail'] or '{}')
            if row['kind'] == 'native_frame' and detail.get('type') in (
                    'user', 'assistant', 'result', 'stream_event', 'command_lifecycle'):
                return False
        return True

    # @beacon[
    #   id=opc@Host.reconnect,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Idempotent exact-conversation transport restoration under the shared reconnect_blockers rule; a tombstone refuses with its History key before role or path checks; a proved-gone owner's dead-generation markers and uncertain inputs are released with a logged incident, never replayed; a live owner is reused.,
    # ]
    async def reconnect(self, *, execution_id, generation):
        sid = native_id(execution_id)
        lock = self.reconnect_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            old = self.state(sid)
            refuse_archived(old)
            if old.get('role') != 'Manager':
                raise ValueError('pane reconnect is registered Manager-only')
            if type(generation) is not int or generation < 1 or generation > old['generation']:
                raise ValueError('invalid expected conversation generation')
            session = self.sessions.get(sid)
            if session and session.process.returncode is None:
                # Collection uses the same per-process Send gate. Wait until it either abandons
                # collection or queues EOF; never reuse a transport already closing its input.
                async with session.command_lock:
                    if session.record['state'] not in ('input_closing', 'exited_draining', 'exited'):
                        return self.state(sid)
                while session.record.get('owner') != 'gone':
                    if session.infrastructure_error:
                        raise RuntimeError('old transport drain is uncertain; reconnect withheld')
                    if (session.process.returncode is None and
                            session.record.get('state') not in ('input_closing', 'exited_draining', 'exited')):
                        return self.state(sid)  # writer revoked EOF after observing new native work
                    await asyncio.sleep(.05)
                old = self.state(sid)
            if session and session.record.get('owner') != 'gone':
                raise RuntimeError('its previous Claude process is still draining its output; wait a moment and Send again.')
            from .settlement import DEAD_GENERATION_HOLDS, reconnect_blockers
            recovery = (self.store.get('recoveries', sid, old['pending_recovery'])
                        if old.get('pending_recovery') else None)
            action = self.supervisor.actions.get(('cc-' + sid, (recovery or {}).get('recovery_id')))
            blockers = reconnect_blockers(old, policy=RECONNECT_POLICY,
                witness_alive=bool(old.get('process_witness') and witness(old['pid']) == old['process_witness']),
                recovery_status='claimed' if action is not None and not action.done() else (recovery or {}).get('status'))
            if blockers:
                raise RuntimeError(blockers[0])
            if generation != old['generation']:
                raise RuntimeError('conversation changed and is no longer live; select its current generation')
            # The old owner is proved gone (witness absent, drains settled; launch reacquires the
            # UUID lock). What the dead generation left behind is evidence, never a hold: its
            # uncertain inputs stay uncertain and are never replayed, but a new process can never
            # answer them, so they no longer gate the new generation's Send.
            released = {key: old.get(key) for key in DEAD_GENERATION_HOLDS if old.get(key)}
            uncertain = []
            for receipt in self.store.receipts(sid):
                if not receipt.get('completed') and not released_receipt(receipt):
                    receipt['released_by_reconnect'] = {'from_generation': old['generation'], 'at': time.time(),
                        'effects': 'uncertain; inspect the transcript before relying on or repeating it'}
                    self.store.put('receipts', sid, receipt, receipt['message_uuid'])
                    uncertain.append(receipt['message_uuid'])
            if recovery and recovery.get('status') == 'scheduled':
                # Dan's own next message replaces the automatic generic `continue`; two inputs
                # would race. The recovery stays in the journal with this disposition.
                recovery.update(status='cancelled', operator_disposition=(
                    "superseded: Dan's Send reconnected the conversation and carries his next message"))
                self.store.put('recoveries', sid, recovery, recovery['recovery_id'])
                stored = self.store.get('sessions', sid)
                stored['pending_recovery'] = None
                self.store.put('sessions', sid, stored)
                if session is not None:
                    session.record['pending_recovery'] = None
                released['pending_recovery'] = recovery['recovery_id']
            if released or uncertain:
                self.supervisor.incident(self.state(sid), 'dead_generation_holds_released', 'retain',
                    detail={'reason': 'reconnect: the previous process is proved gone, so these markers '
                                      'describe it and no longer gate the conversation',
                            'released': released, 'uncertain_message_uuids': uncertain,
                            'message_replayed': False})
            row = await self.resume_snapshot(sid)
            session = self.sessions[sid]
            session.event('conversation_reconnected', {'expected_generation': generation,
                'generation': row['generation'], 'message_replayed': False,
                'released_dead_generation_holds': sorted(released), 'uncertain_message_uuids': uncertain})
            return row

    # @beacon[
    #   id=opc@Host.recheck_orphans,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Reobserves orphan PID/start witnesses and inherited UUID locks, one record at a time (a bad record is that record's supervision failure, never a host-wide one); the orphans are found in the record cache and only they are copied; only proved absence frees reconnect, a missing session directory included, and prior evidence is retained.,
    # ]
    def recheck_orphans(self):
        # Found in the record cache, and each gets its own copy: recheck_orphan updates the record before writing it.
        orphans = [self.store.fresh(record, text) for _, record, text in self.store.cached_sessions()
                   if record.get('state') == 'ownership_unknown' and record.get('native_uuid') not in self.sessions]
        for record in orphans:
            sid = record.get('native_uuid')
            try:
                self.recheck_orphan(record)
            except Exception as error:
                self.supervisor.session_failure(sid, 'orphan_recheck', error)
            else:
                self.supervisor.session_passed(sid, 'orphan_recheck')

    def recheck_orphan(self, record):
        if not record.get('process_witness') or not record.get('pid'):
            return  # missing identity is not a proved-gone PID/start witness
        if witness(record['pid']) == record['process_witness']:
            return
        directory = self.root / record['native_uuid']
        fd = None
        if directory.is_dir():
            try:
                fd = owner_lock(directory / 'owner.lock')
            except RuntimeError:
                return
        try:
            before = {'owner': record.get('owner'), 'state': record.get('state'),
                'process_witness': record.get('process_witness')}
            record.update(owner='gone', state='exited', orphan_exit_rechecked=True)
            if fd is None:
                record['session_directory_missing'] = True  # no lock file left to hold; never resumable
            stale = clear_stale_native_markers(record)
            self.store.event(record['native_uuid'], record['generation'], 'orphan_owner_proved_gone',
                {'prior': before, 'lock_free': fd is not None, 'session_directory_missing': fd is None,
                    'stale_native_markers': stale, 'effects': 'unchanged; no message replay'})
            self.store.put('sessions', record['native_uuid'], record)
            self.changed.set()
        finally:
            if fd is not None:
                os.close(fd)

    # @beacon[
    #   id=opc@Host.lifecycle_tick,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Schedules independent durable observer-report publication plus every-live-process collection passes and the 60 s witness/lock orphan recheck; network publication and collection never block the Supervisor or account switching.,
    # ]
    async def lifecycle_tick(self):
        now = self.monotonic()
        self.publish_observer_reports()
        if now >= self.next_orphan_check:
            self.next_orphan_check = now + 60
            self.recheck_orphans()
        if now < self.next_collection:
            return
        self.next_collection = now + self.collection_cadence
        from .collection import collect_session
        # Every live process each pass: an eligible one is closed within one cadence of its settled threshold,
        # however many processes there are. Most checks return at once (busy, refused, not settled long enough);
        # a readback runs in its own task, and a session still being checked is skipped, never checked twice.
        for session in [s for s in self.sessions.values() if s.process.returncode is None]:
            running = self.collection_tasks.get(session.sid)
            if running is not None and not running.done():
                continue
            task = asyncio.create_task(collect_session(self, session))
            self.collection_tasks[session.sid] = task
            task.add_done_callback(lambda done, session=session: self.collection_finished(session, done))

    def publish_observer_reports(self):
        """Publication is background bookkeeping, never native work or a switch dependency."""
        for record in self.store.records('observer_recoveries'):
            if record.get('publication_enabled') is False or record.get('publication', {}).get('status') == 'confirmed':
                continue
            operation_id = record['operation_id']
            task = self.observer_publications.get(operation_id)
            if task is not None and not task.done():
                continue
            last = record.get('publication', {}).get('last_attempt_at') or 0
            # Reuse the notice queue's outage retry cadence, not an execution deadline.
            if self.supervisor.clock() < last + 30:
                continue
            task = asyncio.create_task(self.observer_recovery.publish(operation_id))
            self.observer_publications[operation_id] = task
            def finished(work, operation_id=operation_id):
                self.observer_publications.pop(operation_id, None)
                try:
                    work.result()
                except (Exception, asyncio.CancelledError):
                    pass  # publisher retains pending/uncertain identity and exact failure
            task.add_done_callback(finished)

    def collection_finished(self, session, task):
        if self.collection_tasks.get(session.sid) is task:
            del self.collection_tasks[session.sid]
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except (OSError, sqlite3.Error) as error:
            session.failed_storage(error)  # this session's own evidence could not be written
        except Exception as error:
            self.supervisor.session_failure(session.sid, 'collection', error)
        else:
            self.supervisor.session_passed(session.sid, 'collection')

    # @beacon[
    #   id=opc@Host.release_ended,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Drops an ended session's in-memory owner once its exit is fully journaled; from then on state, list, replay, raw, resolve, release, resume, reconnect and follow-up read its stored record, exactly as after a host restart.,
    # ]
    def release_ended(self, session):
        """Host.sessions holds live owners only. A session whose process exited, whose drains settled and whose exit
        is journaled (owner gone, no durability failure) is dropped; one with a durability failure stays until
        clear_infrastructure_error completes its exit. Its live-process supervision phases end with it."""
        if (self.sessions.get(session.sid) is not session or session.process.returncode is None
                or session.infrastructure_error or session.record.get('owner') != 'gone'):
            return
        del self.sessions[session.sid]
        for phase in ('silence', 'collection'):
            self.supervisor.session_passed(session.sid, phase)

    def stored_session(self, sid):
        """A read-only stand-in for an ended session (record, store, pending) for readers written against a live
        one, or None for an unknown id."""
        from types import SimpleNamespace
        record = self.store.get('sessions', sid)
        if record is None:
            return None
        return SimpleNamespace(sid=sid, store=self.store, record=record,
                               pending=set(record.get('pending_message_uuids') or []))

    def state(self, execution_id):
        sid = native_id(execution_id)
        if sid in self.sessions:
            session = self.sessions[sid]
            from .collection import collection_refusal
            refusal = collection_refusal(session)
            return {**session.record, 'settled_age_seconds': (max(0, self.monotonic() - session.settled_since)
                if session.settled_since is not None else None),
                'collection_threshold_seconds': self.collection_threshold,
                'collection_eligible': refusal is None, 'collection_refusal': refusal,
                'collection_check_in_progress': session.collection_in_progress,
                'reconnect_policy': RECONNECT_POLICY, **self.attention_fields(sid, session.record)}
        record = self.store.get('sessions', sid)
        if record is None:
            raise ValueError('unknown exact execution ID')
        # Readers (the pane) apply exactly this host's reconnect rule, never a guessed one.
        return {**record, 'reconnect_policy': RECONNECT_POLICY, **self.attention_fields(sid, record)}

    def root_search_health(self):
        """The root-search hook witness across live generations, by Claude Code version: after an update, one
        health read answers "is the guard working on this version?" (docs/root-search-guard.md)."""
        versions, flagged = {}, {}
        for sid, session in self.sessions.items():
            witness = session.record.get('root_search_hook') or {}
            if witness.get('generation') != session.generation:
                continue
            row = versions.setdefault(session.record.get('native_cli_version') or 'unknown',
                                      {'generations': 0, 'seen_answering': 0, 'results_checked': 0, 'flagged': 0})
            row['generations'] += 1
            row['seen_answering'] += bool(witness.get('seen_answering'))
            row['results_checked'] += witness.get('results_checked') or 0
            if witness.get('flags'):
                row['flagged'] += 1
                flagged['cc-' + sid] = sorted(witness['flags'])
        try:
            from .root_search import default_env
            extra_roots = default_env().extra_roots
        except Exception as error:
            extra_roots = f'unreadable: {type(error).__name__}'
        return {'matcher': INITIALIZE_HOOKS['PreToolUse'][0]['matcher'], 'by_claude_code_version': versions,
                'flagged': flagged, 'extra_roots': extra_roots}

    def attention_fields(self, sid, record):
        """Current supervision and read-only five-hour notice metadata of the record in hand (a live session's own
        record, or the stored one its caller read; never read again), never session settlement holds."""
        return {'supervision_attention': self.supervisor.attention_for(sid) or None,
                'quota_recovery_check': self.supervisor.quota_wake.for_record(record)}

    # @beacon[
    #   id=opc@Host.input_hold,
    #   slice_labels=opc-basic,opc-claude-seats,
    #   kind=ast,
    #   comment=The one operator-release rule: hold only while a process could still answer the input (unknown ownership, draining exit or own turn running). Uses the same native idle/closed predicate as evidence-gated stale-input reconciliation; names the way out.,
    # ]
    def input_hold(self, sid, receipt):
        """Why a process of this conversation could still answer this unfinished input, or None (releasable)."""
        session = self.sessions.get(sid)
        record = session.record if session is not None else (self.store.get('sessions', sid) or {})
        owner = record.get('owner')
        if owner == 'gone':
            return None  # proved gone: no process can answer it
        if session is None or owner == 'unknown' or record.get('state') == 'ownership_unknown':
            return ('ownership unknown: its old process may still answer it; wait for the orphan recheck (every '
                    '60 s) to prove that process gone')
        if owner == 'draining' or session.process.returncode is not None:
            return 'its process exited and its output is still draining (a buffered result may arrive); wait a moment'
        if receipt.get('withheld') or (receipt.get('generation') or 0) < session.generation:
            return None  # never written, or accepted by an earlier process this one cannot answer for
        if native_turn_over(record):
            return None  # its process is idle with its turn closed: no result is coming (it was lost)
        return (f"its process is still working on it (native state {record.get('native_session_state')!r}); wait "
                "for the turn to end, or interrupt it")

    # @beacon[
    #   id=opc@Host.reconcile_pending_inputs,
    #   slice_labels=opc-basic,opc-claude-seats,
    #   kind=ast,
    #   comment=Settles user inputs from named primary results of any origin; same-generation started or delivered-steer evidence before an idle closed result releases unmatched holds as stale uncertain (never a mere Send acknowledgement). Recovery-action receipts keep their user-origin-only rule. Journals each in one batch, also on reconstruct; never replays or uses time.,
    # ]
    def reconcile_pending_inputs(self, record, *, message_uuids=()):
        """Reconcile only evidence-backed input holds, live or reconstructed, without reclassifying results.

        A named result is completion, not task acceptance. An unnamed started input or model-delivered
        steer followed by a closed result on a now-idle turn is only stale/uncertain. An ordinary Send's
        echo acknowledges the line, not necessarily a model delivery; mere idle, acknowledgement or
        elapsed time proves neither: a just-sent input may not have started its native turn yet.
        Recovery-action receipts retain their existing user-origin-only result rule, including on
        reconstruct; neither a background result nor the stale rule changes recovery disposition.
        Receipt/result/event/pending changes share the stdout batch, or one local batch at startup.
        """
        sid = record['native_uuid']
        pending = set(record.get('pending_message_uuids') or [])
        named_now = {mid for mid in message_uuids if mid}
        candidates = pending | named_now
        if not candidates:
            return

        def native_seq(cursor, generation, typ):
            # Validate the evidence's store, session, generation and primary native-frame kind.
            try:
                seq = self.store.parse_cursor(cursor)
            except (ValueError, TypeError, AttributeError):
                return None
            row = self.store.db.execute('SELECT detail FROM events WHERE seq=? AND sid=? AND generation=? '
                                        "AND kind='native_frame'", (seq, sid, generation)).fetchone()
            detail = json.loads(row[0]) if row else {}
            return seq if detail.get('type') == typ and detail.get('parent_tool_use_id') is None else None

        with self.store.batch():
            for mid in sorted(candidates):
                receipt = self.store.get('receipts', sid, mid)
                if not receipt or (released_receipt(receipt) and mid not in named_now):
                    continue
                if receipt.get('completed') and mid not in pending:
                    continue
                generation = receipt.get('generation')
                named = next((r for r in receipt.get('results', [])
                    if isinstance(r, dict) and mid in (r.get('user_message_uuids') or [r.get('user_message_uuid')])
                    and (not receipt.get('recovery_id') or user_origin(r.get('origin')))
                    and isinstance(r.get('identity'), (list, tuple)) and len(r['identity']) == 4
                    and native_seq(r.get('cursor'), r['identity'][3], 'result') is not None), None)
                evidence = None
                if named is not None:
                    evidence = {'reason': 'named_primary_result', 'result_cursor': named['cursor'],
                                'origin': named.get('origin')}
                elif (not receipt.get('completed') and not receipt.get('recovery_id')
                      and generation == record.get('generation')
                      and record.get('primary_turn_open') is False and native_turn_over(record)
                      and not record.get('native_wake_pending')
                      and record.get('primary_turn_cursor') == record.get('last_result_cursor')):
                    result_cursor = record.get('last_result_cursor')
                    end = native_seq(result_cursor, generation, 'result')
                    delivered = ([(receipt.get('delivered_cursor'), 'user')]
                                 if receipt.get('kind') == 'steer' and receipt.get('delivered') else [])
                    delivered += [(step.get('cursor'), 'command_lifecycle')
                                  for step in receipt.get('lifecycle', []) if step.get('state') == 'started']
                    for cursor, typ in delivered:
                        start = native_seq(cursor, generation, typ)
                        if start is not None and end is not None and start < end:
                            evidence = {'reason': 'idle_closed_turn_after_delivery', 'delivery_cursor': cursor,
                                        'result_cursor': result_cursor,
                                        'native_state_cursor': record.get('native_state_cursor')}
                            break
                if evidence is None:
                    continue  # unknown is not stale proof, and a queued/not-yet-started input still matters
                kind = 'input_settled' if named is not None else 'input_stale'
                cursor = self.store.event(sid, record['generation'], kind,
                    {'message_uuid': mid, 'input_generation': generation, **evidence, 'semantic_replay': False})
                receipt.update(settlement_cursor=cursor, settlement_evidence=evidence)
                if named is not None:
                    receipt.update(status='completed', completed=True)
                else:
                    receipt.update(status='stale', released_as_stale=True, effects='uncertain')
                self.store.put('receipts', sid, receipt, mid)
                pending.discard(mid)
            session = self.sessions.get(sid)
            if session is not None:
                session.pending = pending
                session.update(pending_message_uuids=sorted(pending))
            else:
                record['pending_message_uuids'] = sorted(pending)
                self.store.put('sessions', sid, record)

    def live_work(self, sid):
        """What a still-running owner of this conversation may still be doing; empty once it is proved gone.
        resolve_recovery holds only on this."""
        session = self.sessions.get(sid)
        if session is None or session.record.get('owner') == 'gone':
            return []
        reasons = []
        for mid in sorted(session.pending):
            reason = self.input_hold(sid, self.store.get('receipts', sid, mid) or {})
            if reason:
                reasons.append(f'input {mid}: {reason}')
        if session.process.returncode is None:
            if session.record.get('background_unsettled'):
                reasons.append('background tasks unsettled')
            if session.record.get('native_retry_active'):
                reasons.append('native retry active')
        return reasons

    # @beacon[
    #   id=opc@Host.clear_infrastructure_error,
    #   slice_labels=opc-basic,opc-claude-seats,
    #   kind=ast,
    #   comment=Host verb clear_infrastructure_error: the operator's release of a durability failure once storage works again; a live session resyncs (NativeSession.clear_infrastructure_error), a stored record drops the marker; both journaled.,
    # ]
    def clear_infrastructure_error(self, execution_id, disposition):
        """The operator's release of a durability failure once storage works again (NativeSession.
        clear_infrastructure_error for a live session; a stored record just drops the marker, journaled)."""
        if not isinstance(disposition, str) or not disposition.strip():
            raise ValueError('explicit nonempty operator disposition required')
        sid = native_id(execution_id)
        session = self.sessions.get(sid)
        record = session.record if session is not None else self.store.get('sessions', sid)
        if record is None:
            raise ValueError('unknown exact execution ID')
        if not (record.get('infrastructure_error') or record.get('incident_unpersisted')
                or (session is not None and session.infrastructure_error)):
            return {**self.state(sid), 'cleared': False}
        if session is not None:
            session.clear_infrastructure_error(disposition)
        else:
            prior = {key: record.get(key) for key in ('infrastructure_error', 'infrastructure_error_at',
                                                       'incident_unpersisted')}
            cursor = self.store.event(sid, record['generation'], 'infrastructure_error_cleared',
                                      {**prior, 'disposition': disposition, 'owner': record.get('owner')})
            record.update(infrastructure_error=None, incident_unpersisted=None, infrastructure_error_at=None,
                          state_before_infrastructure_error=None,
                          retention_gap={**prior, 'cursor': cursor, 'disposition': disposition})
            self.store.put('sessions', sid, record)
            self.supervisor.incident(record, 'infrastructure_error_cleared', 'retain', cursor,
                                     detail={**prior, 'disposition': disposition, 'message_replayed': False})
        self.changed.set()
        return {**self.state(sid), 'cleared': True}

    def session(self, execution_id, generation):
        sid = native_id(execution_id)
        session = self.sessions.get(sid)
        if not session or type(generation) is not int or session.generation != generation:
            raise ValueError('no owned writer at requested generation')
        return session

    # @beacon[
    #   id=opc@ClaudeHost.begin_control,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Starts one caller-ID Manager control durably then returns pending; observer disconnect cannot cancel native reply/readback reconciliation.,
    # ]
    async def begin_control(self, *, execution_id, generation, request_id, operation, **params):
        from .controls import dispatch_control
        if not isinstance(operation, str) or operation not in ('set_model', 'apply_effort', 'interrupt'):
            raise ValueError('unsupported pane control operation')
        request_id = native_id(request_id)
        session = self.session(execution_id, generation)
        if session.record.get('role') != 'Manager':
            raise RuntimeError('pane controls are registered Manager-only')
        caller = {'request_id': request_id, 'operation': operation, 'generation': generation,
                  'params': params}
        async with session.control_start_lock:
            previous = self.store.get('controls', session.sid, request_id)
            if previous is not None:
                if previous.get('caller_request') != caller:
                    raise ValueError('control request UUID already bound to different operation')
                return previous
            task = asyncio.create_task(dispatch_control(session, operation,
                {**params, 'generation': generation, '_caller_request': caller}))
            session.control_tasks.add(task)
            def finished(work):
                session.control_tasks.discard(work)
                try:
                    work.result()
                except asyncio.CancelledError:
                    pass
                except Exception as error:
                    if isinstance(error, (OSError, sqlite3.Error)):
                        session.failed_storage(error)
            task.add_done_callback(finished)
            # Issuance has no await between durable native intent and enriched receipt.
            # Only observe that boundary; never wait for native reply or cancel its task.
            while True:
                previous = self.store.get('controls', session.sid, request_id)
                if previous is not None:
                    return previous
                if task.done():
                    return task.result()  # parameter/busy refusal before any write
                await asyncio.sleep(0)

    def tool_snapshot(self, execution_id, tool_use_id, generation, frame_cursor=None):
        # Indexed native frames, exact raw JSON input; no provider-ID-only identity.
        sid = native_id(execution_id)
        frames = self.store.db.execute("SELECT * FROM events WHERE sid=? AND generation=? "
            "AND kind='native_frame' AND stream='stdout' ORDER BY seq", (sid, generation))
        inputs, outputs = [], []
        seen = set()
        for row in frames:
            if row['length'] > PARSE_LIMIT:
                continue
            with self.store.path(sid, generation, 'stdout').open('rb') as f:
                f.seek(row['offset'])
                text = f.read(row['length']).decode('utf-8')
            event = json.loads(text)
            if event.get('parent_tool_use_id') is not None or event.get('isSidechain') is True:
                continue
            blocks = event.get('message', {}).get('content', [])
            if not isinstance(blocks, list) or not blocks:
                continue
            raw_blocks = member_raw(text, 'message', 'content')
            decoder = json.JSONDecoder()
            position = 1
            for index, block in enumerate(blocks):
                while raw_blocks[position].isspace() or raw_blocks[position] == ',':
                    position += 1
                start = position
                _, position = decoder.raw_decode(raw_blocks, position)
                block_text = raw_blocks[start:position]
                cursor = self.store.cursor(row['seq'])
                dedup = (event.get('uuid') or cursor, index, block.get('type'),
                         block.get('id') or block.get('tool_use_id'))
                if dedup in seen:
                    continue
                seen.add(dedup)
                identity = {'execution_id': 'cc-' + sid, 'generation': generation,
                    'event_uuid': event.get('uuid'), 'message_id': event.get('message', {}).get('id'),
                    'block_index': index, 'tool_use_id': tool_use_id, 'cursor': cursor}
                if block.get('type') == 'tool_use' and block.get('id') == tool_use_id:
                    if frame_cursor is None or cursor == frame_cursor:
                        inputs.append({**identity, 'name': block.get('name'),
                                       'input_raw': member_raw(block_text, 'input')})
                if block.get('type') == 'tool_result' and block.get('tool_use_id') == tool_use_id:
                    outputs.append({**identity, 'block_raw': block_text,
                                    'is_error': block.get('is_error', False),
                                    'provenance': 'native model-facing tool_result'})
        if len(inputs) > 1 and frame_cursor is None:
            raise ValueError('ambiguous tool ID; supply input frame_cursor')
        return {'input': inputs[0] if inputs else None, 'outputs': outputs,
                'available': bool(inputs or outputs), 'diagnostic_payload_is_model_facing': False}

    # @beacon[
    #   id=opc@Host.dispatch,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=One local verb branch per capability; health preserves its existing fields and row watermarks while adding cheap committed debt, loop lag, startup and disk facts; state/list and every control remain independent of those observations.,
    # ]
    async def dispatch(self, packet):
        method, params = packet.get('method'), packet.get('params', {})
        if not isinstance(params, dict):
            raise ValueError('params must be object')
        if method == 'health':
            from .collection import NATIVE_PROOF
            return {'protocol_version': 1, 'host_pid': os.getpid(), 'process_witness': witness(os.getpid()),
                    'state_root': str(self.root), 'socket_path': str(self.socket_path),
                     'implemented': ['launch', 'send', 'steer', 'receipt', 'state', 'list', 'program_activity',
                         'replay', 'subscribe',
                         'raw', 'raw_frames', 'tool_snapshot', 'control_receipt', 'permission_receipt', 'close_input', 'settle_for_restart',
                         'observer_inventory', 'observations', 'stop_task', 'retire_observer_owner', 'observer_retirement',
                         'observer_switch_inventory', 'prepare_observer_switch', 'finish_observer_switch', 'observer_recoveries',
                         'resume', 'reconnect', 'begin_control', 'interrupt', 'set_model', 'apply_effort', 'apply_flag_settings', 'get_settings',
                         'get_context_usage', 'context_status',
                        'incidents', 'incident', 'resolve_incident', 'recoveries', 'resolve_recovery',
                         'release_input', 'clear_infrastructure_error', 'rows', 'rows_changed', 'row_witnesses', 'row_diagnostics',
                           'quota_pause', 'quota_checks', 'quota_check_control', 'forget'],
                    'forget': self.forget_health(),
                    'five_hour_recovery_check': 1,
                    'row_schema': 1, 'rows_backfill': self.rows_health(),
                    'load': self.load_health(),
                    # `list` answers the last two weeks plus anything live or busy unless `all`/`window_days`.
                    'list_window_days': LIST_WINDOW_DAYS,
                    # Open escalations go on a Send's receipt for every role, never a refusal (oc_* scripts
                    # read this to tell a host started before that change from one after it).
                    'escalations_at_send': 'recorded',
                    # set_model/apply_effort accept a conductor/worker/helper with a `seat-switch:` authority
                    # (oc_seat resume / oc_steer --model/--variant); scripts refuse the flags on a host without it.
                    'seat_settings_switch': 1,
                    # launch accepts fork_from (a native fork of a seat's conversation; records carry forked_from,
                    # fork_base_call, fork_first_call); oc_seat fork --base cc-… refuses on a host without it.
                    'seat_fork': 1,
                    # launch accepts fork_self_binding: a Manager forks itself into a worker from inside its own
                    # turn (oc_seat quick --fork-self refuses on a host without it).
                    'manager_fork': 1,
                    'silence_observation_seconds': self.supervisor.silence_threshold,
                    'collection_threshold_seconds': self.collection_threshold,
                    'collection_cadence_seconds': self.collection_cadence,
                    'collection_scope': 'every live process each pass',
                    # Everything one native read produces is one store transaction, after the raw bytes' fsync.
                    'store_transactions': 'one per native read',
                    'collection_basis': 'fresh native idle and empty registered tasks, qualified execution envelope; never silence',
                    # Any Claude Code version: each process's own native proof, never a version pin.
                    'collection_gate': 'native_capabilities',
                    'collection_native_proof': list(NATIVE_PROOF),
                    # Current only: an unattributable tick failure, cleared by the next clean tick (the last one
                    # stays as history). One session's failing part is its own supervision_attention.
                    'supervision_error': self.supervision_error,
                    'supervision_error_detail': self.supervision_error_detail,
                    'last_supervision_error': self.last_supervision_error,
                    'supervision_attention': {'cc-' + sid: self.supervisor.attention_for(sid)
                                              for sid in sorted({key for key, _ in self.supervisor.attention})},
                    # Is the root-search hook really called on the running Claude Code version(s)?
                    'root_search_hook': self.root_search_health(),
                    'pane_activation': 'operator-only; host health is not whole-result or ergonomic proof',
                    # Dan's ruling D2 ([ap:0OP1FK]): every role measures context as the Manager does
                    # (the previous completed model call, OpenCode's own context_status basis). Exact
                    # tool-time occupancy itself stays unqualified, and says so.
                    'unattended_roles_enabled': True,
                    'unattended_context_basis': 'D2: measured input as of the previous completed model call '
                                                '(the Manager and OpenCode context_status basis)',
                    'native_tool_time_context_qualified': False,
                    'supervised_context_basis': 'measured input as of the previous completed model call',
                    # Seat scripts (scripts/oc_claude.py) require this interface: launch labels,
                    # first_launched_at and last_input_uuid on records.
                    'seat_interface': 1,
                    'pending_unit': {'seats': 'scripts/oc_claude.py core adapter (U1a); oc_seat wiring U1b+'}}
        if method == 'launch':
            return await self.launch(**params)
        if method == 'forget':
            return await self.forget(params)
        if method == 'quota_pause':
            return self.supervisor.quota_wake.arm(**params)
        if method == 'quota_checks':
            return self.store.records('quota_checks')
        if method == 'quota_check_control':
            return self.supervisor.quota_wake.control_episode(**params)
        if method == 'observer_inventory':
            if params:
                raise ValueError('observer_inventory takes no parameters')
            return self.observations.inventory()
        if method == 'retire_observer_owner':
            return await self.observations.retire(**params)
        if method == 'observer_switch_inventory':
            return self.observations.switch_inventory()
        if method == 'prepare_observer_switch':
            return self.observations.prepare_switch(**params)
        if method == 'finish_observer_switch':
            return self.observations.finish_switch(**params)
        if method == 'observer_recoveries':
            return self.observer_recovery.read(params['operation_id']) if params.get('operation_id') else self.store.records('observer_recoveries')
        if method == 'observer_retirement':
            return self.observations.retirement(**params)
        if method == 'observations':
            if set(params) - {'observation_id'}:
                raise ValueError('unexpected observations parameters')
            if params.get('observation_id'):
                row = self.observations.get(params['observation_id'])
                return self.observations.reconcile(row) if row else None
            return self.observations.records()
        if method == 'resume':
            return await self.resume_snapshot(**params)
        if method == 'reconnect':
            return await self.reconnect(**params)
        if method == 'state':
            record = self.state(**params)
            # The newest background result (a wake-up turn's last word, on no receipt), from its own row.
            record['last_synthetic_result'] = self.store.last_background_result(record['native_uuid'])
            return record
        if method == 'list':
            # Chosen on the cached records (a live session on its state, as before); only the rows answered are copied
            # and completed, so a narrow answer costs what it returns, not the whole store.
            stored = {record.get('native_uuid'): (record, text) for _, record, text in self.store.cached_sessions()}
            chosen = {key: record for key, (record, _) in stored.items()}
            chosen.update({sid: self.state(sid) for sid in self.sessions})

            def answer(record):
                key = record.get('native_uuid')
                if key not in stored or stored[key][0] is not record:
                    return record  # a live session's state is already a fresh row of its own
                row = self.store.fresh(*stored[key])
                return {**row, 'reconnect_policy': RECONNECT_POLICY, **self.attention_fields(key, row)}
            return list_view(list(chosen.values()), params, now=self.supervisor.clock(), answer=answer)
        if method == 'program_activity':
            # Wave-1 ruling R5, its own branch beside `list`'s: one pass over the records the host already holds
            # (`Store.cached_sessions`: no SQL, no decode, no copy). Nothing is capped, expired or refused.
            return program_activity(self.store.cached_sessions(), params, now=self.supervisor.clock())
        if method in ('send', 'steer'):
            session = self.session(params.pop('execution_id'), params.pop('generation'))
            if 'steer' in params:
                raise ValueError('a steer is its own method, never a send parameter')
            try:
                return await session.send(**params, steer=method == 'steer')
            except (OSError, sqlite3.Error) as error:
                session.failed_storage(error)
                raise
        if method == 'begin_control':
            return await self.begin_control(**params)
        if method in ('get_context_usage', 'context_status'):
            from .context import binding_matches, read_native_context, previous_call_report, unavailable
            values = dict(params)
            execution_id = values.pop('execution_id')
            if method == 'context_status':
                binding = values.pop('binding', None)
                record = self.state(execution_id)
                if not binding_matches(record, binding):
                    raise ValueError('context callback binding invalid for this launch generation')
                generation = record['generation']
            else:
                generation = values.pop('generation')
            if values:
                raise ValueError('unexpected context parameters; no caller model/target lookup')
            record = self.state(execution_id)
            session = self.sessions.get(native_id(execution_id))
            if type(generation) is not int or generation != record['generation']:
                return unavailable(record, 'stale', 'requested generation is not current')
            if session is None:
                call = record.get('last_completed_model_call') or {}
                if call.get('generation') == generation:
                    model = call.get('model')
                    return previous_call_report({**record, 'context_model': model}, call.get('usage'),
                        call.get('measured_at'), (record.get('context_windows') or {}).get(model),
                        call.get('source', 'stored completed model call'))
                return unavailable(record, 'unknown', 'no completed model call yet in this generation')
            return await read_native_context(session)
        if method == 'receipt':
            return self.store.get('receipts', native_id(params['execution_id']), native_id(params['message_uuid']))
        if method == 'control_receipt':
            sid = native_id(params['execution_id'])
            receipt = self.store.get('controls', sid, params['request_id'])
            if receipt and receipt.get('operation_class') == 'interrupt':
                from .controls import interrupt_settlement
                # An ended session answers from its stored record and receipts, as a live one from memory.
                session = self.sessions.get(sid) or self.stored_session(sid)
                if session is not None:
                    receipt['settlement'] = interrupt_settlement(session, params['request_id'])
            return receipt
        if method == 'permission_receipt':
            return self.store.permission(native_id(params['execution_id']), params['generation'], params['request_id'])
        if method in ('replay', 'subscribe'):
            raw_sid = params.pop('execution_id', None)
            sid = native_id(raw_sid) if raw_sid is not None else None
            while True:
                self.changed.clear()
                page = self.store.replay(sid=sid, **params)
                if page['events'] or method == 'replay':
                    return page
                await self.changed.wait()  # pending forever; disconnect never touches native process
        if method == 'raw':
            return self.store.raw(**params)
        if method == 'raw_frames':
            return self.store.raw_frames(**params)
        if method in ('rows', 'rows_changed', 'row_witnesses', 'row_diagnostics'):
            return getattr(self, method)(**params)  # no intervening await: one committed state
        if method == 'tool_snapshot':
            return self.tool_snapshot(**params)
        if method == 'close_input':
            session = self.session(params['execution_id'], params['generation'])
            async with session.command_lock:
                session.close_input()
            return {'status': 'input_close_queued', 'settlement': 'not_claimed'}
        if method == 'settle_for_restart':
            # The guarded restart's settle: native readback first; a stop only on the explicit
            # allow_stop AND the process-tree proof (collection.settle_for_restart). Never a blind kill.
            from .collection import settle_for_restart
            session = self.session(params['execution_id'], params['generation'])
            if set(params) - {'execution_id', 'generation', 'allow_stop'}:
                raise ValueError('unexpected settle_for_restart parameters')
            if type(params.get('allow_stop', False)) is not bool:
                raise ValueError('allow_stop must be a boolean')
            return await settle_for_restart(self, session, allow_stop=params.get('allow_stop', False))
        if method in ('interrupt', 'set_model', 'apply_effort', 'apply_flag_settings', 'get_settings', 'stop_task'):
            from .controls import dispatch_control
            session = self.session(params.pop('execution_id'), params.pop('generation'))
            return await dispatch_control(session, method, params)
        if method == 'incidents':
            execution_id = params.pop('execution_id', None)
            return self.store.incidents(sid=native_id(execution_id) if execution_id else None, **params)
        if method in ('incident', 'resolve_incident'):
            sid = native_id(params.pop('execution_id'))
            return (self.supervisor.resolve_incident(sid, **params) if method == 'resolve_incident'
                    else self.store.get('incidents', sid, params['incident_id']))
        if method == 'recoveries':
            return self.store.records('recoveries', native_id(params['execution_id']) if params.get('execution_id') else None)
        if method == 'resolve_recovery':
            return self.supervisor.resolve_recovery(native_id(params.pop('execution_id')), **params)
        if method == 'release_input':
            return self.supervisor.release_input(native_id(params.pop('execution_id')), **params)
        if method == 'clear_infrastructure_error':
            return self.clear_infrastructure_error(**params)
        raise ValueError('unknown method')

    async def connection(self, reader, writer):
        try:
            peer = writer.get_extra_info('socket')
            _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.getuid():
                return
            while True:
                packet = await read_packet(reader)
                try:
                    if packet.get('method') == 'subscribe':
                        work = asyncio.create_task(self.dispatch(packet))
                        disconnected = asyncio.create_task(reader.read(1))
                        try:
                            done, _ = await asyncio.wait((work, disconnected), return_when=asyncio.FIRST_COMPLETED)
                            if disconnected in done:
                                return
                            result = work.result()
                        finally:
                            work.cancel()
                            disconnected.cancel()
                            await asyncio.gather(work, disconnected, return_exceptions=True)
                    else:
                        result = await self.dispatch(packet)
                    response = {'ok': True, 'result': result}
                except Exception as error:
                    # Do NOT serialize errors/argv/env/prompt bodies from arbitrary exceptions.
                    response = {'ok': False, 'error': type(error).__name__,
                                'message': str(error) if isinstance(error, (ValueError, RuntimeError))
                                else 'infrastructure failure; inspect state/private records'}
                try:
                    await write_packet(writer, response)  # only this client waits on its own socket
                except ValueError:
                    await write_packet(writer, {'ok': False, 'error': 'oversize_response',
                        'message': 'response exceeds 64 MiB; read raw records in slices'})
        except (asyncio.IncompleteReadError, ConnectionError, ValueError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    # @beacon[
    #   id=opc@Host.serve,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=After reconstruction bind the private socket, resume forget and publish the one bound receipt, then own readiness, supervision, backfill and the one-second lag ticker; shutdown cancels and gathers each without waiting for deletion or using load as a gate.,
    # ]
    async def serve(self):
        private_dir(self.socket_path.parent)
        if len(os.fsencode(self.socket_path)) > 100:
            raise ValueError('Unix socket path too long')
        if self.socket_path.exists():
            # Probe rather than unlink a possibly live different-state host.
            probe = socket.socket(socket.AF_UNIX)
            try:
                probe.connect(str(self.socket_path))
            except ConnectionRefusedError:
                self.socket_path.unlink()
            else:
                raise RuntimeError('IPC endpoint already owned')
            finally:
                probe.close()
        self.server = await asyncio.start_unix_server(self.connection, path=str(self.socket_path))
        self.socket_path.chmod(0o600)
        self.resume_forgets()
        self.publish_start_receipt('bound')
        self.ready_task = asyncio.create_task(self.finalize_start_ready())
        self.supervisor_task = asyncio.create_task(self.supervisor.run())
        self.rows_backfill_task = asyncio.create_task(self.backfill_rows())
        self.loop_lag_task = asyncio.create_task(self.measure_loop_lag())
        try:
            async with self.server:
                await self.server.serve_forever()
        finally:
            if self.forget_task:
                self.forget_task.cancel()
                await asyncio.gather(self.forget_task, return_exceptions=True)
            self.ready_task.cancel()
            await asyncio.gather(self.ready_task, return_exceptions=True)
            self.supervisor_task.cancel()
            await asyncio.gather(self.supervisor_task, return_exceptions=True)
            self.loop_lag_task.cancel()
            await asyncio.gather(self.loop_lag_task, return_exceptions=True)
            # Alarm observers never keep shutdown alive; their durable episode/Send receipts resume observation.
            alarm_tasks = [task for task in self.supervisor.quota_wake.tasks.values() if not task.done()]
            for task in alarm_tasks:
                task.cancel()
            await asyncio.gather(*alarm_tasks, return_exceptions=True)
            publication_tasks = list(self.observer_publications.values())
            for task in publication_tasks:
                task.cancel()
            await asyncio.gather(*publication_tasks, return_exceptions=True)
            self.rows_backfill_task.cancel()
            await asyncio.gather(self.rows_backfill_task, return_exceptions=True)
            running = [task for task in self.collection_tasks.values() if not task.done()]
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
