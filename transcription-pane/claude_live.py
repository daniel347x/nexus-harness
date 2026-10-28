"""Durable host projection and guarded Manager bridge beside native history.

Only summaries and immutable raw cursors survive a request. Browser handles never
contain host cursors, paths or execution IDs. Only registered Manager authority can drive a turn;
an ended registered Manager keeps a read-only archived authority for Refine and revalidation.
"""
from __future__ import annotations

import base64
import hashlib
import inspect
import json
import math
import os
import re
import secrets
import socket
import struct
import threading
import time
import uuid
from functools import wraps
from itertools import islice
from bisect import bisect_right
from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

import sys
from tool_diff import claude_diff, recorded_value

_SCRIPTS = str(Path(__file__).resolve().parent.parent / 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
# The host's own reconnect rule (stdlib only, never the host process): pane and host agree.
from claude_headless.settlement import reconnect_blockers as host_reconnect_blockers  # noqa: E402
from claude_headless.rows import (MemoryRows, RowProjector, frame_prose, join_status,
                                  permitted, project_through, read_frame, scope_for)  # noqa: E402
from claude_headless.context import input_tokens as context_input_tokens, timestamp_seconds  # noqa: E402
from claude_headless.host import list_program, list_recency  # noqa: E402
from oc_backend import seat_busy  # noqa: E402
from universe import UniverseEvidence, session_key  # noqa: E402

from claude_reader import (BODY_LIMIT, PAGE_LIMIT, PAGE_EVENTS, SOURCE_LIMIT,
                           HANDLE_LIMIT, INDEX_FILES,
                           INDEX_SUMMARY_BYTES, ReaderError, envelope, digest, check_from_seq,
                           projection, result_projection, scalar, utf8_tail, open_contained,
                           thinking_projection, HistoryBlocks, check_history_mode)

# One viewer request drains durable replay continuations for at most this long before
# handing the honest partial index back; _settle passes its own absolute observer
# deadline instead. The envelope's continuation cursor resumes the rest.
DRAIN_BUDGET_SECONDS = 2.0

# The pane's native window scan is a bounded reverse read of the generation's raw stream
# for the last result frame; the reported window is the CLI's own modelUsage value.
HOST_CONTEXT_TAIL_BYTES = 16 * 1024 * 1024
HOST_CONTEXT_CHUNK_BYTES = 256 * 1024
HOST_WINDOW_CACHE_FILES = 8

# One reconnect may run the host's native launch preflight (auth status/version) before
# it answers; this only lengthens this read's own observation, never a host deadline.
RECONNECT_OBSERVER_BUDGET = 60.0

# Host rows per sidebar inventory page (each row is a cheap summary; no history read).
HOST_INVENTORY_PAGE = 64

# Replay diagnostic that is a receipt observation, not a damaged row.
RESULT_RECEIPT_DIAGNOSTIC = 'result-receipt; task acceptance unjudged'

# Refine context reads whose sent prose is remembered (per frozen leaf) so the desktop's pinned
# re-check after inference can tell a message that grew from one that was rewritten.
CONTEXT_WITNESS_LIMIT = 64


def canonical_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


# @beacon[
#   id=opc-pane@host_row_fields,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Pure host-summary display fields shared by live inventory and History: the existing label/title fallback and cwd guard, registry/display roles, lineage, Manager Session, socket and exact execution identity; no refs, live state, capabilities, transcript reads or new validation.,
# ]
def host_row_fields(record) -> dict:
    """Project the existing record-derived sidebar fields from a host list summary.

    Live augmentation belongs to ClaudeFacade. sessionLabel is retained where the
    inventory summary already sets it; sessions does not add it to its public rows.
    """
    execution = record.get('execution_id')
    fields = {'uuid': record.get('native_uuid', execution[3:] if isinstance(execution, str) else None),
              'title': ClaudeFacade._label(record), 'cwd': record.get('cwd'),
              'role': record.get('role', 'unknown'), 'executionId': execution,
              'generation': record.get('generation')}
    labels = record.get('labels') if isinstance(record.get('labels'), dict) else {}
    fields['displayRole'], fields['roleSource'] = ClaudeFacade._display_role(record), 'host'
    lineage = ClaudeFacade._lineage(record)
    if lineage:
        fields['lineage'] = lineage
    if isinstance(record.get('socket'), str) and len(record['socket']) <= 128:
        fields['socket'] = record['socket']
    if isinstance(labels.get('manager_session'), str) and re.fullmatch(r'[0-9]{1,6}', labels['manager_session']):
        fields['managerSession'] = labels['manager_session']
    if isinstance(record.get('label'), str) and record['label'].strip():
        fields['sessionLabel'] = fields['title']
    elif (fields['title'] == record.get('native_uuid') and isinstance(labels.get('title'), str)
            and labels['title'].strip() and len(labels['title'].encode()) <= 4096):
        fields['title'] = labels['title'].strip()
    if not isinstance(fields['cwd'], str) or len(fields['cwd'].encode()) > 4096:
        fields['cwd'] = 'unknown/label-limit'
    return fields


# @beacon[
#   id=opc-pane@PaneHostClient,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Read-only fixed local host IPC with a per-call observer deadline and a fresh socket; raw-frame batches and program_activity stay read-only and an observer disconnect never changes native work.,
# ]
class PaneHostClient:
    """Read-only IPC on fresh sockets with per-call budgets; disconnect never changes native work."""
    METHODS = frozenset(('list', 'state', 'replay', 'raw', 'raw_frames', 'receipt', 'control_receipt', 'incident',
                         'health', 'program_activity', 'rows', 'rows_changed', 'row_witnesses', 'row_diagnostics'))

    def call(self, method, *, reader_deadline=None, **params):
        if method not in self.METHODS:
            raise ValueError('read-only pane host client')
        import os
        path = '/tmp/opencode/cc-' + str(os.getuid()) + '/host.sock'
        data = json.dumps({'method': method, 'params': params}).encode()
        # A call owns its budget, not this shared transport. It never reaches the host
        # as a parameter and disconnects only this observer, never the CLI.
        deadline = reader_deadline if reader_deadline is not None else time.monotonic() + 12
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(12)
            connection.connect(path)
            connection.sendall(struct.pack('!Q', len(data)) + data)
            def exact(length):
                body = bytearray()
                while len(body) < length:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('host reader deadline')
                    connection.settimeout(remaining)
                    chunk = connection.recv(min(65536, length - len(body)))
                    if not chunk:
                        raise OSError('host reader disconnected')
                    body.extend(chunk)
                return body
            size = struct.unpack('!Q', exact(8))[0]
            if size > 64 * 1024 * 1024:
                raise ReaderError(413, 'response-limit', 'host IPC response exceeds transport limit')
            reply = json.loads(exact(size))
            if not reply.get('ok'):
                message = str(reply.get('message', 'host read refused'))
                if reply.get('error') in ('ValueError', 'RuntimeError'):
                    raise ReaderError(409, 'refused', message)
                status = 'gap' if 'cursor' in message or 'gap' in message else 'read-failure'
                raise ReaderError(409 if status == 'gap' else 502, status, message)
            return reply['result']


# @beacon[
#   id=opc-pane@PaneManagerClient,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Fixed local send/begin-control transport; caller IDs reconcile a lost observer reply without semantic retry or native cancellation.,
# ]
class PaneManagerClient(PaneHostClient):
    METHODS = frozenset(('send', 'steer', 'begin_control', 'reconnect', 'get_settings', 'resolve_incident'))


@dataclass
class LiveIndex:
    execution: str
    epoch: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    cursor: str | None = None
    revision: int = 0
    rows: list = field(default_factory=list)
    active: dict = field(default_factory=dict)
    identities: dict = field(default_factory=dict)
    results: dict = field(default_factory=dict)
    calls_by_id: dict = field(default_factory=dict)
    by_origin: dict = field(default_factory=dict)
    dirty: dict = field(default_factory=dict)
    content_witnesses: set = field(default_factory=set)
    incidents: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    summary_bytes: int = 0
    indexed_bytes: int = 0
    status: str = 'indexing'
    activity: dict = field(default_factory=dict)
    permission_requests: dict = field(default_factory=dict)
    # (generation, native task_id) -> the primary Agent call ID that owns it: a local_agent's
    # own lifecycle, and the background tasks its subagent started (owned_by_subagent).
    agent_tasks: dict = field(default_factory=dict)
    child_tasks: dict = field(default_factory=dict)
    # (generation, Agent call ID) -> display label/state of that subagent, in start order.
    agent_info: dict = field(default_factory=dict)
    # Row origins sent before their transcript record (thinking level) had landed; bounded.
    facts_pending: OrderedDict = field(default_factory=OrderedDict)
    projector: RowProjector | None = None


# @beacon[
#   id=opc-pane@PaneReplaySource,
#   slice_labels=opc-pane,opc-rows,
#   kind=ast,
#   comment=Read-only replay/raw IPC in the Store-shaped interface used by the shared row driver and frame reader; keeps 500-event paging and existing read failures.,
# ]
class PaneReplaySource:
    def __init__(self, facade):
        self.facade = facade

    def replay(self, cursor=None, sid=None, limit=100):
        return self.facade._call('replay', execution_id='cc-' + sid, cursor=cursor, limit=limit)

    def raw(self, **params):
        return self.facade._call('raw', **params)


class RowsUnavailable(Exception):
    """One execution's rows failed; its reader chooses the unchanged non-rows path."""
    def __init__(self, execution, message):
        super().__init__(message)
        self.execution = execution


class HistoryRequestGone(Exception):
    """The HTTP viewer disappeared before observation started; no native work is involved."""


@dataclass
class _SharedHistoryRead:
    future: Future = field(default_factory=Future)
    checks: list = field(default_factory=list)


@dataclass
class RowsIndex:
    execution: str
    epoch: str
    revision: int
    counts: dict
    head: dict
    status: str = 'complete'
    summary_bytes: int = 0
    activity: dict = field(default_factory=dict)
    permission_requests: dict = field(default_factory=dict)
    facts_pending: OrderedDict = field(default_factory=OrderedDict)


class StoredScope:
    """Request-local positional view; only addressed pages are read, never retained across reads."""
    def __init__(self, facade, idx, scope, count=None):
        self.facade, self.idx, self.scope = facade, idx, scope
        self.count = idx.counts.get(scope, 0) if count is None else count
        self.known = {r['seq']: r for r in idx.head['rows'] if r['scope'] == scope}

    def __len__(self):
        return self.count

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, end, step = key.indices(self.count)
            if start == 0 and step == 1:
                view = StoredScope(self.facade, self.idx, self.scope, end)
                view.known = self.known
                return view
            return [self[n] for n in range(start, end, step)]
        number = key + self.count if key < 0 else key
        if not 0 <= number < self.count:
            raise IndexError(number)
        if number not in self.known:
            start = number // PAGE_EVENTS * PAGE_EVENTS
            page = self.facade._row_reply(self.idx, 'rows', scope=self.scope,
                from_seq=start, limit=min(PAGE_EVENTS, self.count - start))
            self.known.update((r['seq'], r) for r in page['rows'])
        if number not in self.known:
            raise RowsUnavailable(self.idx.execution, 'missing stored row position')
        return self.known[number]


class _OperationState(threading.local):
    """One thread's nested operation; no observer can reset another's state."""
    def __init__(self):
        self.deadline, self.read_depth = None, 0
        self.row_reads, self.rows_failed, self.row_origins = {}, set(), {}
        self.rows_unreadable = set()
        self.frames, self.host_methods = {}, None
        self.start_check = None


def _operation_field(name):
    # Preserve the existing offline inspection/deadline seams, backed only by TLS.
    return property(lambda self: getattr(self._operation, name),
                    lambda self, value: setattr(self._operation, name, value))


# @beacon[
#   id=opc-pane@reader_operation,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Nested reads share only their own thread-local budget and staging; queued observation checks its viewer at the history start, only real rows failures retry, and deadlines never discard retained indexes or publish unfinished reads.,
# ]
def reader_operation(fn=None, *, lane='history'):
    """Own one observer budget on the history, control, or lock-free observation lane."""
    if fn is None:
        return lambda operation: reader_operation(operation, lane=lane)
    @wraps(fn)
    def bounded(self, *args, **kwargs):
        lock = self.history_lock if lane == 'history' else self.control_lock if lane == 'control' else nullcontext()
        with lock:
            previous = getattr(self, '_deadline', None)
            depth = self._read_depth
            if depth == 0 and lane != 'control':
                check = self._operation.start_check
                if check is not None and not check():
                    raise HistoryRequestGone()
            self._read_depth = depth + 1
            if depth == 0:
                self._row_reads, self._rows_failed = {}, set()
                self._row_origins = {}
                self._rows_unreadable = set()
                self._frames, self._host_methods = {}, None
            if previous is None:
                self._deadline = time.monotonic() + 12
            try:
                try:
                    value = fn(self, *args, **kwargs)
                    if time.monotonic() > self._deadline:
                        raise ReaderError(502, 'read-failure', 'pane host read deadline; native execution untouched')
                except (RowsUnavailable, TimeoutError, ReaderError) as error:
                    if depth != 0:
                        raise
                    cause = error
                    while cause is not None:
                        if (isinstance(cause, TimeoutError) or
                                (isinstance(cause, ReaderError) and cause.code == 502 and
                                 cause.status == 'read-failure' and
                                 str(cause) == 'pane host read deadline; native execution untouched')):
                            # _row_set/_row_reply may wrap _call's chained TimeoutError in
                            # RowsUnavailable. Time says nothing about projection integrity:
                            # publish nothing, keep every retained index and try rows next poll.
                            raise ReaderError(502, 'read-failure',
                                'pane host read deadline; native execution untouched') from error
                        cause = cause.__cause__
                    if not isinstance(error, RowsUnavailable):
                        raise
                    self._discard_rows(error.execution)
                    # Re-arm the existing observer budget for replay, never shorten an
                    # outer budget (the offline comparison tool supplies infinity).
                    self._deadline = max(self._deadline, time.monotonic() + 12)
                    value = fn(self, *args, **kwargs)
                    if time.monotonic() > self._deadline:
                        raise ReaderError(502, 'read-failure', 'pane host read deadline; native execution untouched')
                if depth == 0:
                    self._publish_rows(value, fn.__name__)
                return value
            finally:
                if depth == 0:
                    self._row_reads.clear()
                    self._rows_failed.clear()
                    self._row_origins.clear()
                    self._rows_unreadable.clear()
                    self._frames.clear()
                    self._host_methods = None
                self._deadline = previous
                self._read_depth = depth
    return bounded


# @beacon[
#   id=opc-pane@control_operation,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Manager authority and commands serialize on their own reentrant control lane; the one operation wrapper supplies thread-local budgets without ever waiting for the history lane.,
# ]
def control_operation(fn):
    """Use the existing operation owner, but never a history reader's lock or deadline."""
    return reader_operation(fn, lane='control')


# @beacon[
#   id=opc-pane@shared_history_read,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Only identical in-flight events addresses share one history operation; one leader publishes, every caller gets its own answer or the same failure, and a queued shared read starts only for a still-connected observer. Nothing is cached after completion.,
# ]
def shared_history_read(fn):
    """Coalesce before the history lock; never hold maps while waiting or copying a page."""
    signature = inspect.signature(fn)

    @wraps(fn)
    def shared(self, *args, **kwargs):
        if self._read_depth:
            return fn(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        key = (fn.__name__, tuple((name, value) for name, value in bound.arguments.items() if name != 'self'))
        check = self._operation.start_check
        with self.maps_lock:
            entry = self._shared_reads.get(key)
            leader = entry is None
            if leader:
                entry = self._shared_reads[key] = _SharedHistoryRead()
            entry.checks.append(check)
        if not leader:
            value = entry.future.result()
            if check is not None and not check():
                raise HistoryRequestGone()
            return deepcopy(value)

        def any_observer():
            # A departed leader must not discard the same page a live waiter still
            # requested. These are only nonblocking socket peeks, outside maps.
            with self.maps_lock:
                checks = tuple(entry.checks)
            return any(current is None or current() for current in checks)

        self._operation.start_check = any_observer
        try:
            value = fn(self, *args, **kwargs)
            entry.future.set_result(deepcopy(value))
            return value
        except BaseException as error:
            entry.future.set_exception(error)
            raise
        finally:
            self._operation.start_check = check
            with self.maps_lock:
                self._shared_reads.pop(key, None)

    return shared


# @beacon[
#   id=opc-pane@ClaudeFacade,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=History (including the Inspector) and Manager controls have independent lanes and operation-local observers; inventory and target metadata hold neither lane across host reads, and short maps sections never cover IPC. Exact-generation Send/receipts and archived Refine authority stay unchanged.,
# ]
class ClaudeFacade:
    _deadline = _operation_field('deadline')
    _read_depth = _operation_field('read_depth')
    _row_reads = _operation_field('row_reads')
    _rows_failed = _operation_field('rows_failed')
    _row_origins = _operation_field('row_origins')
    _rows_unreadable = _operation_field('rows_unreadable')
    _frames = _operation_field('frames')
    _host_methods = _operation_field('host_methods')

    def __init__(self, history, host_client=None, *, command_client=None,
                 summary_limit=INDEX_SUMMARY_BYTES, names=None, universe_state=None, spawn=None, clock=time.time):
        self.history = history
        self._operation = _OperationState()
        # Display-only names chosen at New Manager launch (claude_launch.SessionNames), or None.
        self.names = names
        self.client = host_client or PaneHostClient()
        self.command_client = command_client or host_client or PaneManagerClient()
        # Inventory and slow evidence use independent transports, never another
        # reader's deadline or the facade lock that Send/target validation uses.
        self._source_owner = self.client
        self.inventory_client = PaneHostClient() if host_client is None else self.client
        self.idle_client = PaneHostClient() if host_client is None else self.client
        self.universe = universe_state if universe_state is not None else UniverseEvidence()
        self.clock = clock
        self.spawn_idle = spawn or (lambda work: threading.Thread(target=work, daemon=True,
                                                                 name='claude-universe-idle').start())
        self.idle_lock = threading.Lock()
        self.idle_scanning, self.idle_scanned_at, self.idle_records = False, None, ()
        self.activity_unsupported = False
        # Lock order: control -> history (native validate_target only) -> maps.
        # History never takes control; maps never covers IPC or a ClaudeReader call.
        self.history_lock = history.lock
        self.control_lock, self.maps_lock = threading.RLock(), threading.RLock()
        self.lock = self.control_lock  # compatibility seam: the Send/target lane, not history
        self.handles, self.reverse, self.indexes = OrderedDict(), {}, OrderedDict()
        self.window_cache = OrderedDict()
        self.store_ref = secrets.token_urlsafe(24)
        self.summary_limit = summary_limit
        # Inventory snapshots by token: a cursor names its snapshot, never hashes every record.
        self.inventories = OrderedDict()
        # Open incident bodies by (execution, incident id) for the Manager panel; bounded.
        self.incident_cache = OrderedDict()
        # Refine's sent prose per frozen leaf identity: {row origin: {(chars, sha256 of the prose read)}}.
        self.context_witness = OrderedDict()
        self._shared_reads = {}

    def __getattr__(self, name):
        # Compatibility for callers/tests using the original history inspection seams.
        return getattr(self.history, name)

    @contextmanager
    def history_request(self, start_check):
        """Bind the handler's connected-viewer check to this thread, never to controls."""
        previous = self._operation.start_check
        self._operation.start_check = start_check
        try:
            yield
        finally:
            self._operation.start_check = previous

    def _ref(self, kind, identity):
        with self.maps_lock:
            key = (kind, identity)
            ref = self.reverse.get(key)
            if ref not in self.handles:
                ref = secrets.token_urlsafe(24)
                self.reverse[key] = ref
                self.handles[ref] = key
            self.handles.move_to_end(ref)
            while len(self.handles) > HANDLE_LIMIT:
                old, key = self.handles.popitem(last=False)
                self.reverse.pop(key, None)
            return ref

    def _has_live(self, ref):
        with self.maps_lock:
            return ref in self.handles

    def _cached_index(self, execution):
        with self.maps_lock:
            return self.indexes.get(execution)

    def _get_live(self, ref, kind):
        with self.maps_lock:
            value = self.handles.get(ref)
            if value is None:
                raise ReaderError(409, 'stale', 'host handle expired; reselect')
            if value[0] != kind:
                raise ReaderError(400, 'schema', 'wrong host handle kind')
            self.handles.move_to_end(ref)  # Actively observed targets remain live in the bounded LRU.
            return value[1]

    def _call(self, method, **params):
        try:
            deadline = getattr(self, '_deadline', None)
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError('pane observer deadline')
            value = self._client_call(self.client, method, deadline=deadline, **params)
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError('pane observer deadline')
            if method in ('state', 'list') and self.names is not None:
                self._attach_names(value)
            return value
        except ReaderError:
            raise
        except (OSError, RuntimeError, ValueError, AttributeError) as error:
            raise ReaderError(502, 'read-failure', 'host unavailable; retained history is independent') from error

    @staticmethod
    def _client_call(client, method, *, deadline=None, **params):
        """Pass the observer budget only to our transport, never into an injected host's params."""
        if getattr(client.call, '__func__', None) is PaneHostClient.call:
            return client.call(method, reader_deadline=deadline, **params)
        return client.call(method, **params)

    def _attach_names(self, value):
        """Copy the launch name onto host records as `label` (display only, never identity)."""
        try:
            names = self.names.all()
        except Exception:  # noqa: BLE001 - a name is cosmetic; never fail a read for it
            return
        for record in (value if isinstance(value, list) else [value]):
            if isinstance(record, dict) and not record.get('label') and names.get(record.get('native_uuid')):
                record['label'] = names[record['native_uuid']]

    @classmethod
    def _capabilities(cls, record=None):
        manager = bool(record and record.get('role') == 'Manager' and record.get('initialized') is True
                       and record.get('owner') in ('host', 'owned')
                       and record.get('state') == 'ready')
        # `reconnect` is Send's reconnect-on-demand authority: a registered Manager whose
        # conversation has no live process (collected sleeping, or proved ended) can be
        # woken by one Send; a writable Manager is already live. History, other roles and
        # unproved ownership never carry it.
        return {'history': True, 'live': True, 'context': True, 'inspect': True,
                'nativeInput': manager, 'controls': manager, 'subagents': True,
                'reconnect': bool(record and (manager or cls._reconnectable(record)))}

    def _live_state(self, record):
        # Observations, not guessed busy/idle. Reserve the activity evidence first; task
        # summaries keep bounded background tool inputs/start times, never expose prompts or
        # turn a dict registry into a detail-limit stub.
        # Never requested settings as applied; unknown value shapes keep their handling.
        keys = ('state', 'primary_turn_open', 'pending_message_uuids', 'background_tasks',
                'owner', 'initialized', 'native_retry_active', 'last_retry', 'background_unsettled', 'pending_recovery',
                'pending_escalations', 'silence', 'silence_observation', 'last_activity_at',
                'protocol_attention', 'infrastructure_error', 'incident_unpersisted',
                'setting_control_pending', 'settings_mismatch', 'controls_effective',
                'settings_unverified', 'settings_unverified_disclosure',
                'actual_model', 'actual_effort', 'model', 'effort', 'exit_code', 'stdout_eof',
                'setting_control_state', 'setting_control_block_reason', 'setting_control_disclosure',
                'setting_control_request_id', 'close_reason', 'settled_age_seconds',
                'collected_settled_age_seconds', 'native_session_state', 'native_state_required',
                'native_queued_turns', 'pending_native_requests', 'collection_threshold_seconds',
                'quota_halt', 'last_quota_halt', 'quota_recovery_check')
        result, used = {}, 0
        for key in keys:
            if key not in record:
                continue
            value = record[key]
            projected = key == 'background_tasks' and isinstance(value, dict)
            if projected:
                fields = ('task_id', 'run_id', 'task_type', 'status', 'description', 'tool_use_id', 'subagent_type',
                          'tool_input', 'started_at')
                value = {task_id: {field: (detail[:200] if field == 'description' and isinstance(detail, str) else detail)
                                  for field, detail in task.items() if field in fields} if isinstance(task, dict) else task
                         for task_id, task in value.items()}
                for task in value.values():
                    if not isinstance(task, dict):
                        continue
                    if 'tool_input' in task:
                        tool_input = task['tool_input']
                        if isinstance(tool_input, dict):
                            task['tool_input'] = {field: tool_input[field][:limit]
                                if isinstance(tool_input.get(field), str) else ''
                                for field, limit in (('name', None), ('command', 1024), ('description', 256))}
                        else:
                            del task['tool_input']
                    if 'started_at' in task and type(task['started_at']) not in (int, float):
                        del task['started_at']
                if len(json.dumps(value, ensure_ascii=False).encode()) > 16 * 1024:
                    value = {task_id: {field: detail for field, detail in task.items()
                                      if field in ('task_id', 'run_id', 'task_type')} if isinstance(task, dict) else task
                             for task_id, task in value.items()}
            size = len(json.dumps(value, ensure_ascii=False).encode())
            if not projected and (size > 16 * 1024 or used + size > 32 * 1024):
                result[key] = {'status': 'detail-limit', 'message': 'Host observation exceeds viewer metadata bound; not inferred.'}
            else:
                result[key] = value
                used += size
        execution = record.get('execution_id')
        idx = self._row_reads.get(execution) or self._cached_index(execution)
        result['activity'] = self._activity(record, idx)
        return result

    @staticmethod
    def _activity(record, idx=None):
        states = ('idle', 'thinking', 'writing', 'tool', 'retrying', 'permission', 'unknown')
        activity = {'state': 'unknown', 'thinkingTokens': None, 'tool': None,
                    'attempt': None, 'lastEventAt': None}
        observed = record.get('activity')
        if not isinstance(observed, dict) and idx and idx.status == 'complete':
            observed = idx.activity.get(record.get('generation'))
        if isinstance(observed, dict):
            activity.update({k: observed.get(k) for k in activity})
        if activity['state'] not in states:
            activity['state'] = 'unknown'
        for key in ('thinkingTokens', 'attempt'):
            if type(activity[key]) is not int or not 0 <= activity[key] <= 2**53 - 1:
                activity[key] = None
        if not isinstance(activity['tool'], str) or len(activity['tool'].encode()) > 256:
            activity['tool'] = None
        stamp = activity['lastEventAt']
        if type(stamp) not in (float, int) or not math.isfinite(stamp) or stamp < 0:
            activity['lastEventAt'] = None
        if record.get('native_retry_active'):
            activity.update(state='retrying', tool=None)
            retry = record.get('last_retry') or {}
            retry = retry if isinstance(retry, dict) else {}
            attempt = retry.get('attempt')
            activity['attempt'] = attempt if type(attempt) is int and 0 <= attempt <= 2**53 - 1 else None
        # A fresh initialized, verified and explicitly settled host is idle even
        # before its first assistant stream. Missing fields never mean settled.
        if (not isinstance(record.get('activity'), dict) and activity['state'] == 'unknown' and
                (idx is None or idx.status == 'complete') and
                record.get('state') == 'ready' and record.get('initialized') is True and
                record.get('owner') in ('host', 'owned') and record.get('pending_message_uuids') == [] and
                record.get('setting_control_state') == 'applied' and
                not any(record.get(k) for k in ('setting_control_pending', 'settings_mismatch',
                    'native_retry_active', 'background_unsettled', 'pending_recovery',
                    'pending_escalations', 'protocol_attention', 'infrastructure_error', 'incident_unpersisted')) and
                not (idx and any(g == record.get('generation') for g, _ in idx.permission_requests.copy()))):
            activity['state'] = 'idle'
        if activity['lastEventAt'] is None:
            stamp = record.get('last_activity_at')
            if type(stamp) in (int, float) and math.isfinite(stamp) and stamp >= 0:
                activity['lastEventAt'] = stamp
        if (record.get('owner') in ('unknown', 'gone', 'draining') or
                record.get('state') in ('ownership_unknown', 'infrastructure_error', 'exited', 'exited_draining') or
                record.get('infrastructure_error') or record.get('incident_unpersisted')):
            activity.update(state='unknown', tool=None, attempt=None)
        elif activity['state'] == 'idle' and (record.get('background_unsettled') or
                record.get('pending_recovery') or record.get('pending_message_uuids')):
            activity['state'] = 'unknown'  # main result is not background settlement
        if activity['state'] != 'tool':
            activity['tool'] = None
        if activity['state'] != 'retrying':
            activity['attempt'] = None
        return activity

    # @beacon[
    #   id=opc-pane@ClaudeFacade._observe_activity,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Current-generation durable arrival-order activity; thinking counts are native telemetry and silence never drives execution.,
    # ]
    def _observe_activity(self, idx, event, fields=None, text=None):
        """Arrival-order observations only; neither silence nor this viewer drives work."""
        gen = event['generation']
        a = idx.activity.setdefault(gen, {'state': 'unknown', 'thinkingTokens': None,
            'tool': None, 'attempt': None, 'lastEventAt': None})
        detail = event.get('detail') or {}
        quiet = event['kind'] == 'incident' and detail.get('category') in (
            'silence', 'long_silence', 'silence_recovered')
        stamp = event.get('time')
        if not quiet and type(stamp) in (int, float) and math.isfinite(stamp) and stamp >= 0:
            a['lastEventAt'] = stamp
        if fields is None:
            if quiet:
                return  # observer's silence report is not a new native event
            if event['kind'] == 'stdin_written' and detail.get('intent_kind') == 'permission':
                key = (gen, detail.get('request_id'))
                if key in idx.permission_requests:
                    tool = idx.permission_requests.pop(key)
                    waiting = any(g == gen for g, _ in idx.permission_requests)
                    a.update(state='permission' if waiting else 'tool' if tool else 'unknown',
                             tool=tool if not waiting else None, attempt=None)
            elif event['kind'] in ('process_exit', 'raw_reconciliation_gap', 'unsupported_frame',
                                  'malformed_frame', 'unknown_frame', 'infrastructure_error'):
                a.update(state='unknown', tool=None, attempt=None)
            elif event['kind'] == 'incident':
                category = event.get('detail', {}).get('category')
                if category == 'native_retry':
                    a.update(state='retrying', tool=None)
                elif category not in ('silence', 'silence_recovered', 'permission_request', 'permission_allow'):
                    a.update(state='unknown', tool=None)
            return
        if scope_for(fields) != 'main':
            return
        def value(key):
            return json.loads(text[fields[key].start:fields[key].end]) if key in fields else None
        typ = scalar(fields, 'type', 'string')
        state, tool = None, None
        if typ == 'system':
            sub = scalar(fields, 'subtype', 'string')
            if sub == 'thinking_tokens':
                count = value('estimated_tokens')
                a['thinkingTokens'] = count if type(count) is int and 0 <= count <= 2**53 - 1 else None
                state = 'thinking'
            elif sub == 'status':
                status = value('status')
                state = {'thinking': 'thinking', 'writing': 'writing', 'idle': 'idle',
                    'requesting': 'unknown', 'compacting': 'unknown', 'retrying': 'retrying',
                    'waiting_for_permission': 'permission'}.get(status, 'unknown') if isinstance(status, str) else 'unknown'
            elif sub == 'api_retry':
                state = 'retrying'
                attempt = value('attempt')
                a['attempt'] = attempt if type(attempt) is int and 0 <= attempt <= 2**53 - 1 else None
        elif typ == 'command_lifecycle':
            state = {'queued': 'unknown', 'started': 'unknown', 'completed': 'idle',
                     'failed': 'unknown', 'cancelled': 'unknown'}.get(value('state'))
            if value('state') in ('queued', 'started'):
                a['thinkingTokens'] = None
        elif typ == 'control_request':
            request = fields.get('request')
            if request and scalar(request.members(), 'agent_id', 'string'):
                return  # a background subagent's own tool permission is not the Manager's turn
            if request and scalar(request.members(), 'subtype', 'string') in ('can_use_tool', 'elicitation'):
                req = request.members()
                rid = scalar(fields, 'request_id', 'string')
                tool_id = scalar(req, 'tool_use_id', 'string')
                name = scalar(req, 'tool_name', 'string')
                # Restore only a tool whose native start preceded this exact
                # permission request. An allow decision alone is not execution.
                prior = a.get('tool') if (a['state'] == 'tool' and
                    ((tool_id and tool_id == a.get('toolUseId')) or
                     (not tool_id and name and name == a.get('tool')))) else None
                if isinstance(rid, str) and len(rid) <= 256:
                    idx.permission_requests[(gen, rid)] = prior
                state = 'permission'
        elif typ == 'control_response':
            response = fields.get('response')
            if response:
                rid = scalar(response.members(), 'request_id', 'string')
                if (gen, rid) in idx.permission_requests:
                    idx.permission_requests.pop((gen, rid), None)
                    state = 'unknown'  # approval is not tool execution evidence
        elif typ == 'stream_event':
            stream = fields['event'].members()
            sub = scalar(stream, 'type', 'string')
            if sub == 'message_start':
                state = 'unknown'
                a['thinkingTokens'] = None
                a['toolUseId'] = None
            elif sub in ('content_block_start', 'content_block_delta'):
                block = stream['content_block' if sub == 'content_block_start' else 'delta'].members()
                btyp = scalar(block, 'type', 'string')
                state = {'thinking': 'thinking', 'redacted_thinking': 'thinking', 'thinking_delta': 'thinking',
                         'text': 'writing', 'text_delta': 'writing', 'tool_use': 'tool',
                         'input_json_delta': 'tool'}.get(btyp)
                tool = scalar(block, 'name', 'string') if btyp == 'tool_use' else a.get('tool')
                if btyp == 'tool_use':
                    a['toolUseId'] = scalar(block, 'id', 'string')
        elif typ == 'assistant':
            content = fields['message'].members().get('content')
            if content and content.kind == 'array' and content.children:
                b = content.children[-1].members()
                state = {'thinking': 'thinking', 'redacted_thinking': 'thinking', 'text': 'writing',
                         'tool_use': 'tool'}.get(scalar(b, 'type', 'string'))
                tool = scalar(b, 'name', 'string')
                if scalar(b, 'type', 'string') == 'tool_use':
                    a['toolUseId'] = scalar(b, 'id', 'string')
        elif typ == 'result':
            # Every primary result closes the Manager's own turn (2.1.299 alternates primary
            # system/init and result strictly; the host's primary_turn_open uses the same rule).
            # A task-notification result closes a notification-started turn: idle too, or that
            # turn's last 'writing' would stay on screen until Dan's next Send.
            state = 'idle'  # terminal receipt, never task acceptance
            for key in [key for key in idx.permission_requests if key[0] == gen]:
                idx.permission_requests.pop(key)
        if state:
            a.update(state=state, tool=tool if state == 'tool' else None,
                     attempt=a.get('attempt') if state == 'retrying' else None)

    # @beacon[
    #   id=opc-pane@ClaudeFacade._display_role,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Sidebar role badge and grouping from the host registry role refined by the oc_* seat labels (quick worker, helper); a name mentioning a Manager Session never makes a session a Manager. Display only.,
    # ]
    @staticmethod
    def _display_role(record):
        """The session's real role for the sidebar badge and grouping (display only, never authority).

        The host registry's role, refined by the labels the oc_* scripts stamp on a seat: a quick
        seat is a quick worker, a helper is a helper. A name or first message that mentions a
        Manager Session never makes a session a Manager."""
        labels = record.get('labels') if isinstance(record.get('labels'), dict) else {}
        role = str(record.get('role') or '').lower()
        if role == 'manager':
            return 'manager'
        if role == 'conductor' or labels.get('kind') == 'conductor':
            return 'conductor'
        if role == 'helper' or labels.get('kind') == 'helper':
            return 'helper'
        if role == 'worker':
            if labels.get('quick') is True:
                return 'quick-worker'
            return 'lead-seat' if ClaudeFacade._seat_role(labels) == 'lead' else 'worker'
        return 'base' if role == 'base' else 'unknown'

    @staticmethod
    def _seat_role(labels):
        """A fresh seat's role: seat_role when stamped; before it was (efdd0f6), a fresh lead was kind seat and a
        fresh worker kind worker."""
        if labels.get('seat_role') in ('lead', 'worker'):
            return labels['seat_role']
        if labels.get('fresh') is True and labels.get('kind') in ('seat', 'worker'):
            return 'lead' if labels['kind'] == 'seat' else 'worker'
        return None

    # @beacon[
    #   id=opc-pane@ClaudeFacade._lineage,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=The sidebar tree's edge data for one host record, from the immutable launch labels only (program core, Conductor number, Conductor id, helper's lead, seat role, quick/full, Manager Session); a freestanding quick worker or helper (labels quick, no program) gets the same edges without a program. Malformed values drop out, the title prefix is only the fallback for a missing number. Display only.,
    # ]
    @staticmethod
    def _lineage(record):
        """Where a host session sits in the sidebar's trees (display only): its program's tree, the freestanding
        quick-worker forest (a quick worker or helper started with no program: no `program` key), or None for any
        other session.

        Every field comes from labels the oc_* scripts stamp at launch (immutable); a value of the wrong shape is
        dropped, never guessed. The `[ap:CORE]-NNN` title prefix is read only when no number was stamped."""
        labels = record.get('labels') if isinstance(record.get('labels'), dict) else {}
        program = record.get('program') if isinstance(record.get('program'), str) else ''
        found = re.fullmatch(r'\[ap:([A-Z0-9]{6})\]', program.strip())
        core = found.group(1) if found else labels.get('program_core')
        role = str(record.get('role') or '').lower()
        if not isinstance(core, str) or not re.fullmatch(r'[A-Z0-9]{6}', core):
            # Freestanding only when no program was given at all: a malformed one still drops out.
            if program.strip() or labels.get('program_core') is not None or labels.get('quick') is not True \
                    or role not in ('worker', 'helper'):
                return None
            core = None
        kind = labels.get('kind') if labels.get('kind') in ('conductor', 'seat', 'worker', 'helper') else None
        kind = 'conductor' if role == 'conductor' else 'helper' if role == 'helper' else kind or 'seat'
        lineage = {'program': core, 'kind': kind} if core else {'kind': kind}

        def number(value):
            return value if type(value) is int and 1 <= value <= 999 else None

        n = number(labels.get('registry_n') if kind == 'conductor' else labels.get('conductor_n'))
        title = labels.get('title') if isinstance(labels.get('title'), str) and len(labels['title']) <= 4096 else None
        if n is None and title and core:
            prefix = re.match(r'\s*\[ap:' + core + r'\]-(\d{3})(?!\d)', title)
            if prefix and number(int(prefix.group(1))):
                n, lineage['numberSource'] = int(prefix.group(1)), 'title'
        if n is not None:
            lineage['conductorN'] = n
        ident = r'(?:cc-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|ses_[A-Za-z0-9]{8,64})'
        for key, values in (('conductorId', (labels.get('conductor_id'), record.get('owning_conductor'))),
                            ('parentSeat', (labels.get('parent_seat'), record.get('parent')))):
            value = next((v for v in values if isinstance(v, str) and re.fullmatch(ident, v)), None)
            if value and kind != 'conductor':
                lineage[key] = value
        if kind != 'conductor' and ClaudeFacade._seat_role(labels):
            lineage['seatRole'] = ClaudeFacade._seat_role(labels)
        for key, label in (('quick', 'quick'), ('fresh', 'fresh'), ('quickProgram', 'quick_program')):
            if isinstance(labels.get(label), bool):
                lineage[key] = labels[label]
        if labels.get('entry_action') in ('seeded', 'resume'):
            lineage['entryAction'] = labels['entry_action']
        if isinstance(labels.get('manager_session'), str) and re.fullmatch(r'[0-9]{1,6}', labels['manager_session']):
            lineage['managerSession'] = labels['manager_session']
        if title:
            lineage['title'] = title.strip()
        return lineage

    def _inventory_record(self, record, successor=None, fields=None):
        """Snapshot the shared display fields plus the facade's existing live augmentation."""
        summary = {k: record[k] for k in ('execution_id', 'native_uuid', 'generation', 'role', 'cwd', 'createdAt') if k in record}
        summary.update(host_row_fields(record) if fields is None else fields)
        summary.update(self._live_state(record))
        summary['lifecycle'] = self._lifecycle(record)
        last_context = self._row_context(record)
        if last_context:
            summary['lastContext'] = last_context  # the Manager gauges' figure (display only)
        if record.get('role') == 'Manager' and self._ended(record):
            summary['successor'] = successor
            blocked = self._reconnect_blockers(record)
            summary['reconnectBlocked'] = blocked[0] if blocked else None
            summary['reconnectBlockers'] = blocked  # decided on the full record, not this summary
        return summary

    def _source_client(self, *, idle=False):
        """Preserve an injected/replaced adapter; only default IPC owns separate transports."""
        if self.client is not self._source_owner:
            return self.client
        return self.idle_client if idle else self.inventory_client

    def _inventory_call(self, method, **params):
        """Use the same fixed reader transport without holding the control/facade lock."""
        try:
            client = self._source_client()
            value = self._client_call(client, method, deadline=time.monotonic() + 12, **params)
            if method == 'list':
                if not isinstance(value, list):
                    raise ReaderError(502, 'read-failure', 'host inventory has an unexpected shape')
                if self.names is not None:
                    self._attach_names(value)
            return value
        except ReaderError:
            raise
        except (OSError, RuntimeError, ValueError, AttributeError) as error:
            raise ReaderError(502, 'read-failure', 'host unavailable; retained history is independent') from error

    def _universe_rows(self, records):
        """Only normalize evidence; the existing ended/busy and program rules remain owners."""
        rows = []
        for record in records:
            key = session_key(record.get('execution_id'))
            if key is None:
                continue
            labels = record.get('labels') if isinstance(record.get('labels'), dict) else {}
            rows.append({'key': key, 'program': list_program(record),
                         'parent_key': session_key(record.get('parent') or labels.get('parent_seat')),
                         'manager': record.get('role') == 'Manager',
                         'last_at': (list_recency(record) or 0) * 1000,
                         'live': not self._ended(record) or seat_busy(record)})
        return rows

    def _start_idle_scan(self, supported):
        """Populate own cold evidence once, then refresh it at the settings cadence.

        Both paths are outside the facade/control lock. A cold old host's slow
        window must be usable on the first list, not an empty sidebar until the
        next poll merely because program_activity is absent.
        """
        now = self.clock()
        with self.idle_lock:
            if self.idle_scanning or (self.idle_scanned_at is not None and
                    now - self.idle_scanned_at < self.universe.settings.universe_idle_scan_minutes * 60):
                return
            self.idle_scanning = True
            initial = self.idle_scanned_at is None
        if initial:
            self._scan_idle(supported)
        else:
            self.spawn_idle(lambda: self._scan_idle(supported))

    def _scan_idle(self, supported):
        """Read cheap program activity, or the pre-wave-1 slow window plus its members."""
        now_ms = self.clock() * 1000
        since = now_ms - self.universe.settings.idle_unsealed_days * 86_400_000
        try:
            client = self._source_client(idle=True)
            deadline = time.monotonic() + 12
            activity = None
            if supported and not self.activity_unsupported:
                try:
                    reply = self._client_call(client, 'program_activity', deadline=deadline, since=since)
                    activity = {p['core']: p['last_at'] for p in reply['programs']}
                    now_ms = reply['as_of']
                except (ValueError, ReaderError) as error:
                    # A stale advertised method is still an older service, not
                    # an empty sidebar. Only unknown-method falls back here.
                    if 'unknown method' not in str(error).lower():
                        raise
                    self.activity_unsupported = True
            if activity is None:
                records = self._client_call(client, 'list', deadline=deadline, summary=True,
                    window_days=self.universe.settings.idle_unsealed_days)
                activity = {}
                for record in records:
                    core = list_program(record)
                    if core and record.get('role') != 'Manager':
                        activity[core] = max(activity.get(core, 0), (list_recency(record) or 0) * 1000)
                with self.idle_lock:
                    self.idle_records = tuple(records)
            self.universe.publish_idle('claude', activity, now_ms)
        except (OSError, RuntimeError, ValueError, ReaderError, KeyError, TypeError, AttributeError):
            pass  # Keep last good evidence; neither absence nor a refresh failure blocks work.
        finally:
            with self.idle_lock:
                self.idle_scanned_at, self.idle_scanning = self.clock(), False

    def _host_list(self, live=False, *, view='week', role=None, health=None):
        """Window and at-any-age selectors are separate calls, merged by execution id.

        Feature detection is program_activity in health. Pre-wave-1 hosts get no
        programs/live assumptions: the slow 90-day answer supplies whole-unit
        members, while pinned ids still work. No week/month inventory asks all.
        The optional health receipt belongs to this inventory only, never shared
        across operations. All-view also reads it for the quiet caption; live
        activity keeps its existing list-only lane and the normal list cadence refreshes load.
        """
        if live or role == 'Manager':
            return self._inventory_call('list', summary=True, **({'live': True} if live else {}))
        settings = self.universe.settings
        window = settings.view_wide_window_days if view == 'month' else settings.view_window_days
        records = self._inventory_call('list', summary=True, **({'all': True} if view == 'all' else {'window_days': window}))
        try:
            observed = self._inventory_call('health')
            if isinstance(observed, dict) and health is not None:
                health.update(observed)
            supported = 'program_activity' in observed.get('implemented', ()) and not self.activity_unsupported
        except (ReaderError, AttributeError, TypeError):
            supported = False
        if view == 'all':
            self.universe.publish('claude', self._universe_rows(records))
            return records
        self._start_idle_scan(supported)
        supported = supported and not self.activity_unsupported
        by_id = {r['execution_id']: r for r in records}
        pins = self.universe.pins.all()
        ids = {key[len('session:claude:'):].split('/agent-', 1)[0] for key in pins
               if key.startswith('session:claude:')}
        selected, _ = self.universe.project(view, self.clock() * 1000, claude=self._universe_rows(records))
        cores = {unit[len('program:'):] for unit in selected if unit.startswith('program:')}
        cores.update(key[len('program:'):] for key in pins if key.startswith('program:'))
        requested = set()
        # Pins may reveal another program/root. Follow only those exact edges;
        # the selectors ignore windows, hence never combine them with window_days.
        while ids or (supported and cores - requested):
            params = {}
            if ids:
                params['execution_ids'] = sorted(ids)
            if supported and cores - requested:
                params['programs'] = sorted(cores - requested)
                requested.update(cores)
            extra = self._inventory_call('list', summary=True, **params)
            by_id.update((r['execution_id'], r) for r in extra)
            evidence = self._universe_rows(by_id.values())
            selected, assignment = self.universe.project(view, self.clock() * 1000, claude=evidence)
            cores.update(unit[len('program:'):] for unit in selected if unit.startswith('program:'))
            ids = {r['parent_key'].split(':', 1)[1] for r in evidence
                   if (r.get('parent_key') or '').startswith('claude:') and
                   r['parent_key'].split(':', 1)[1] not in by_id and
                   assignment.get(r['key']) in selected}
            # An absent source ancestor cannot make a selector retry forever.
            ids.difference_update(params.get('execution_ids', ()))
        if not supported:
            with self.idle_lock:
                cached = self.idle_records
            by_id.update((r['execution_id'], r) for r in cached if list_program(r) in cores)
        evidence = self._universe_rows(by_id.values())
        selected, assignment = self.universe.project(view, self.clock() * 1000, claude=evidence)
        self.universe.publish('claude', evidence)
        return [r for r in by_id.values() if assignment.get(session_key(r.get('execution_id'))) in selected]

    # @beacon[
    #   id=opc-pane@ClaudeFacade.sessions,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Page-local inventory over the ONE whole-unit universe, with source/health reads outside both control and history lanes; the first non-live page carries only this read's optional host-load facts, reusing health rather than adding a poll. Cursors, selectors, Manager/live semantics, host_row_fields and deliberate native history remain unchanged.,
    # ]
    def sessions(self, cursor=None, storeRef=None, workspace=None, role=None, live=False, view='week'):
        """Read inventory sources independently, then issue the existing snapshot handles."""
        if type(live) is not bool:
            raise ReaderError(400, 'schema', 'live must be Boolean')
        if live and storeRef not in (None, self.store_ref):
            raise ReaderError(400, 'schema', 'live inventory requires the host store')
        if role not in (None, 'Manager'):
            raise ReaderError(400, 'schema', 'unsupported inventory role')
        if not live and role is None and view not in ('week', 'month', 'all'):
            raise ReaderError(400, 'schema', 'view must be week, month or all')
        prefetched, inventory_health = None, {}
        if not cursor:
            try:
                prefetched = self._host_list(live=live, view=view, role=role, health=inventory_health)
            except ReaderError as error:
                prefetched = error
        reply = self._sessions(cursor, storeRef, workspace, role, live, view, prefetched, inventory_health)
        if not cursor and not live:
            load = inventory_health.get('load')
            reply['hostLoad'] = {key: load.get(key) for key in ('rows_behind_sets', 'loop_lag_ms_max_60s')} if isinstance(load, dict) else None
        return reply

    @reader_operation(lane=None)
    def _sessions(self, cursor, storeRef, workspace, role, live, view, prefetched, inventory_health=None):
        """Page current-work-first host/native inventory, or only Managers newest-created first.

        Manager mode never scans native rows and propagates host failures. Cursors bind
        the selected mode without changing the unfiltered cursor representation. Host-store
        reads stop at host summaries and propagate outages, never falling back to transcripts.
        Live mode excludes terminal records before ordering/paging, not after a history walk.
        host_row_fields supplies every record-derived display field; refs and live
        augmentation stay here and in _inventory_record.
        Inventory and mirror host reads hold neither lane; only snapshot map access is locked.
        View windows come from the one settings loader; the Manager picker's cosmetic host
        window still tolerates an older host or health outage without blocking rows.
        """
        if type(live) is not bool:
            raise ReaderError(400, 'schema', 'live must be Boolean')
        if live:
            if storeRef not in (None, self.store_ref):
                raise ReaderError(400, 'schema', 'live inventory requires the host store')
            storeRef = self.store_ref
        if role not in (None, 'Manager'):
            raise ReaderError(400, 'schema', 'unsupported inventory role')
        filters = (storeRef, workspace) if role is None else (storeRef, workspace, role)
        if live:
            filters += ('live',)
        elif role is None:
            filters += ('view', view)
        list_window_days = (self.universe.settings.view_window_days if view == 'week' else
                            self.universe.settings.view_wide_window_days if view == 'month' else None) if not live and role is None else None
        with self.maps_lock:
            if role is not None and cursor and cursor not in self.handles:
                raise ReaderError(409, 'stale', 'unknown Manager inventory cursor; reload it')
            if cursor in self.handles:
                stage, native_cursor, position, snapshot, cursor_filters = self._get_live(cursor, 'inventory')
                if cursor_filters != filters:
                    raise ReaderError(400, 'schema', 'inventory cursor filter mismatch')
                records = self.inventories.get(snapshot)
                if records is None:
                    raise ReaderError(409, 'stale', 'the session list was read again meanwhile; reload it')
            else:
                stage, native_cursor, position, records = 'host', cursor, 0, []
                snapshot = secrets.token_urlsafe(12)
        if not cursor:
            # Finish this immutable snapshot before publishing its cursor. Another tab's
            # scan neither resets it nor sees a partly projected inventory.
            try:
                if isinstance(prefetched, ReaderError):
                    raise prefetched
                records = prefetched
                if live:
                    records = [r for r in records if not self._ended(r)]
                if role is not None:
                    try:
                        health = self._inventory_call('health')
                        if isinstance(health, dict) and inventory_health is not None:
                            inventory_health.update(health)
                        window = health.get('list_window_days') if isinstance(health, dict) else None
                        if type(window) in (int, float) and math.isfinite(window) and window > 0:
                            list_window_days = window
                    except ReaderError:
                        pass
                    records = [r for r in records if r.get('role') == role]
                # These native display seams may read files, never the host. They use
                # only the history lane, not the maps/control lock.
                with self.history_lock:
                    self._learn_windows(records)
                    if role is None:
                        records = self._order_records(records)
                    else:
                        for record in records:
                            record['createdAt'] = self._record_created(record)
                        records.sort(key=lambda r: r['createdAt'], reverse=True)
                    successors = self._successors(records)
                    records = [self._inventory_record(r, successors.get(r.get('execution_id')), host_row_fields(r))
                               for r in records]
            except ReaderError as error:
                if role is not None or storeRef == self.store_ref:
                    raise  # A host outage is not an empty or native Manager list.
                native = self.history.sessions(storeRef=storeRef, workspace=workspace)
                for row in native['sessions']:
                    row.update(role=row.get('role', 'history'), executionId=None, generation=None,
                               state='unmanaged-history')
                native['diagnostics'].append({'source': 'host', 'status': error.status,
                                              'message': str(error)})
                native['listWindowDays'] = None
                return native
        if stage == 'host' and position < len(records):
            rows = []
            end = min(position + HOST_INVENTORY_PAGE, len(records))
            for record in records[position:end]:
                execution, generation = record.get('execution_id'), record.get('generation')
                if (not isinstance(execution, str) or not execution.startswith('cc-') or
                        type(generation) is not int):
                    continue
                if storeRef not in (None, self.store_ref) or (workspace is not None and record.get('cwd') != workspace):
                    continue
                idx = self._cached_index(execution)
                row = {'storeRef': self.store_ref,
                    'sessionRef': self._ref('session', (execution, generation)),
                    'targetRef': self._ref('archive' if self._ended(record) else 'target', (execution, generation)),
                    'uuid': record['uuid'], 'title': record['title'],
                    'cwd': record['cwd'], 'status': record.get('state', 'unknown'),
                    'fileEpoch': idx.epoch if idx else None,
                    'scopes': [{'label': 'main', 'scopeRef': self._ref('scope', (execution, generation, 'main'))}],
                    'source': 'host', 'role': record['role'],
                    'executionId': record['executionId'], 'generation': record['generation'],
                    'createdAt': record.get('createdAt') or None,
                    'state': record.get('state', 'unknown'), 'liveState': self._live_state(record),
                    'capabilities': self._capabilities(record),
                    'lifecycle': record.get('lifecycle', self._lifecycle(record)),
                    'historySeam': {'policy': 'independent-provenance',
                        'message': 'Prior native history is an explicit separate source boundary; only UUID+scope+exact content witnessed mirrors are hidden.'}}
                for key in ('displayRole', 'roleSource', 'managerSession', 'lineage', 'socket'):
                    if key in record:
                        row[key] = record[key]
                if 'successor' in record:
                    row['successor'] = record['successor']
                if record.get('reconnectBlocked'):
                    row['reconnectBlocked'] = record['reconnectBlocked']
                if record.get('lastContext'):
                    row['lastContext'] = record['lastContext']
                rows.append(row)
            more = end < len(records) or (role is None and storeRef != self.store_ref)
            next_stage = 'host' if end < len(records) else 'native'
            return envelope(sessions=rows, diagnostics=[], hasMore=more,
                nextCursor=self._inventory_cursor((next_stage, None, end), snapshot, records,
                                                  filters) if more else None,
                status='discovering' if more else 'complete', listWindowDays=list_window_days)
        if role is not None or storeRef == self.store_ref:
            return envelope(sessions=[], diagnostics=[], hasMore=False, nextCursor=None, status='complete',
                            listWindowDays=list_window_days)
        native = self.history.sessions(cursor=native_cursor, storeRef=storeRef, workspace=workspace)
        visible = []
        owners = {r.get('native_uuid'): r for r in records}
        for row in native['sessions']:
            owner = owners.get(row['uuid'])
            row.update(role=row.get('role', 'history'), executionId=None, generation=None,
                       state='unmanaged-history', capabilities={**row['capabilities'], 'controls': False})
            if row.get('parentUuid'):
                if owner:
                    with self.history_lock:
                        _, child_path = self.history._get(row['sessionRef'], 'session')
                        row.update(self.history._agent_labels(self.history._get(row['sessionRef'], 'session')[0],
                            child_path, owner.get('title') or row['parentUuid']))
                visible.append(row)
                continue  # A child is neither its parent's native mirror nor its Send target.
            if owner:
                execution = owner['execution_id']
                try:
                    stored = self._rows_available(execution)
                    idx = self._row_set(execution) if stored else self._cached_index(execution)
                    if stored:
                        witnesses = {tuple(w) for w in self._row_reply(idx, 'row_witnesses')['witnesses']}
                except RowsUnavailable:
                    self._discard_rows(execution)
                    stored, idx = False, self._cached_index(execution)
                with self.history_lock:
                    if not stored:
                        witnesses = idx.content_witnesses if isinstance(idx, LiveIndex) else set()
                    index = self.history._session(row['sessionRef'])
                    conversations = [r for r in index.records if r.scope == 'main' and r.kind in ('user', 'assistant')]
                    exact_mirror = bool(idx and idx.status == 'complete' and index.status == 'complete' and conversations and
                        all(not r.reason and (r.kind, r.scope, r.uuid, r.content_hash) in witnesses
                            for r in conversations))
                    if exact_mirror:
                        # Same-size writes can share a filesystem timestamp tick. Verify
                        # the bounded current prefix, not cached witnesses alone.
                        if index.offset > self.history.scan_bytes:
                            exact_mirror = False
                        else:
                            with open_contained(index.root, index.path) as source:
                                current = source.read(index.offset + 1)
                            exact_mirror = (len(current) == index.offset and
                                hashlib.sha256(current).digest() == index.prefix_hash.digest())
                            if not exact_mirror:
                                self.history._reset(index)
                if exact_mirror:
                    continue
                row['title'] = 'Native history boundary · ' + row['title']
                if owner.get('displayRole'):
                    row['displayRole'], row['roleSource'] = owner['displayRole'], 'host-twin'
                row['hostTwin'] = True
                row['historySeam'] = {'executionId': owner['execution_id'],
                    'policy': 'independent-provenance', 'overlap': 'unmerged',
                    'message': 'Separate native history of this host UUID; not the live target.'}
            visible.append(row)
        native['sessions'] = visible
        if not cursor:
            native['listWindowDays'] = list_window_days
        if native['hasMore']:
            native['nextCursor'] = self._inventory_cursor(('native', native['nextCursor'], 0), snapshot,
                                                          records, filters)
        return native

    def _inventory_cursor(self, position, snapshot, records, filters):
        """A continuation over one inventory snapshot, held by token: hashing every record into
        each cursor's identity made a long list quadratic. A few concurrent readers fit.

        Filters bind the page-local view for ordinary inventory, and keep the Manager/live
        modes separate without making their cursors depend on that selector."""
        with self.maps_lock:
            self.inventories[snapshot] = records
            self.inventories.move_to_end(snapshot)
            while len(self.inventories) > 8:
                self.inventories.popitem(last=False)
            return self._ref('inventory', (*position, snapshot, filters))

    @reader_operation
    def subagents(self, sessionRef, cursor=None):
        with self.history_lock:
            if not self._has_live(sessionRef):
                return self.history.subagents(sessionRef, cursor)
            execution, generation = self._get_live(sessionRef, 'session')
            record = self._state(execution, generation)
            sid, path = self.history.transcript_path(record.get('native_uuid'), record.get('cwd'))
            if sid is None:
                return envelope(sessions=[], diagnostics=[], hasMore=False, nextCursor=None, status='no-transcript-yet')
            parent = self.history._ref('session', (sid, path))
            return self.history.subagents(parent, cursor, parent_title=record.get('title') or record.get('native_uuid'))

    def _index_live(self, execution, epoch=None):
        with self.maps_lock:
            idx = self.indexes.get(execution)
            if idx is None or isinstance(idx, RowsIndex):
                if epoch:
                    raise ReaderError(409, 'stale-epoch', 'host index evicted; reselect')
                idx = LiveIndex(execution)
                self.indexes[execution] = idx
            if epoch and epoch != idx.epoch:
                raise ReaderError(409, 'stale-epoch', 'host epoch changed; reselect')
            self.indexes.move_to_end(execution)
            while len(self.indexes) > INDEX_FILES:
                self.indexes.popitem(last=False)
            return idx

    def _discard_rows(self, execution):
        self._rows_failed.add(execution)
        self._row_reads.pop(execution, None)
        for key in [key for key in self._row_origins if key[0] == execution]:
            self._row_origins.pop(key)

    # @beacon[
    #   id=opc-pane@ClaudeFacade._publish_rows,
    #   slice_labels=opc-pane,opc-rows,
    #   kind=ast,
    #   comment=Publishes the successful outer read's own rows metadata under the short maps lock; only events may replace retained replay, while secondary/inventory probes never overwrite its progress and no IPC happens here.,
    # ]
    def _publish_rows(self, result, operation):
        if not isinstance(result, dict) or result.get('rowSource') != 'host':
            return
        with self.maps_lock:
            execution = result.get('executionId')
            if execution is None:
                call = self.handles.get(result.get('callRef'))
                execution = call[1][0] if call and call[0] == 'call' else None
            idx = self._row_reads.get(execution)
            if idx is None or idx.epoch != result.get('fileEpoch'):
                return
            if operation != 'events' and isinstance(self.indexes.get(execution), LiveIndex):
                return
            self.indexes[execution] = idx
            self.indexes.move_to_end(execution)
            while len(self.indexes) > INDEX_FILES:
                self.indexes.popitem(last=False)

    # @beacon[
    #   id=opc-pane@ClaudeFacade._rows_available,
    #   slice_labels=opc-pane,opc-rows,
    #   kind=ast,
    #   comment=Per-execution per-read capability and complete-row-set gate; failed rows leave retained replay untouched and other executions enabled, never a TTL decision.,
    # ]
    def _rows_available(self, execution):
        return self._row_set(execution) is not None

    # @beacon[
    #   id=opc-pane@ClaudeFacade._row_set,
    #   slice_labels=opc-pane,opc-rows,
    #   kind=ast,
    #   comment=One operation-local health capability read stages fresh complete metadata per execution; an explicit unreadable status records only that conversation's quiet notice and keeps the raw path, while failed/unfinished probes never publish over replay progress.,
    # ]
    def _row_set(self, execution):
        """Stage complete rows or the explicit per-execution damage fact; fallback stays unchanged."""
        if execution in self._rows_failed:
            return None
        reads = self._row_reads
        if execution not in reads:
            reads[execution] = None
            try:
                if self._host_methods is None:
                    self._host_methods = frozenset(self._call('health').get('implemented', []))
                methods = ('rows', 'rows_changed', 'row_witnesses', 'row_diagnostics')
                if not all(m in self._host_methods for m in methods):
                    return None
                head = self._call('rows', execution_id=execution, limit=PAGE_EVENTS)
                if head.get('status') == 'unreadable':
                    self._rows_unreadable.add(execution)
                if head.get('status') != 'complete' or not head.get('epoch') or 'agent_info' not in head:
                    return None
                self._row_maps(head)
                idx = RowsIndex(execution, head['epoch'], head['as_of'], dict(head['counts']), head)
                old = self._cached_index(execution)
                if isinstance(old, RowsIndex) and old.epoch == idx.epoch:
                    idx.facts_pending = old.facts_pending.copy()
                    idx.activity, idx.permission_requests = old.activity.copy(), old.permission_requests.copy()
                reads[execution] = idx
                self._row_origins.update(((execution, r['origin']), r) for r in head['rows'])
            except ReaderError as error:
                raise RowsUnavailable(execution, str(error)) from error
        return reads[execution]

    def _row_reply(self, idx, method, **params):
        try:
            page = self._call(method, execution_id=idx.execution, **params)
        except ReaderError as error:
            raise RowsUnavailable(idx.execution, str(error)) from error
        if page.get('status') == 'unreadable':
            self._rows_unreadable.add(idx.execution)
        if (page.get('status') != 'complete' or page.get('epoch') != idx.epoch or
                (method == 'rows' and 'agent_info' not in page)):
            raise RowsUnavailable(idx.execution, 'stored rows changed availability')
        idx.revision = min(idx.revision, page['as_of'])
        self._row_maps(page)
        self._row_origins.update(((idx.execution, r['origin']), r) for r in page.get('rows', []))
        return page

    @staticmethod
    def _row_maps(page):
        # JSON transports integer block keys as strings (not a stream interpretation).
        for row in page.get('rows', []):
            for key in ('stream_types', 'final_blocks', 'thinking', 'text_bytes'):
                row[key] = {int(n): value for n, value in row[key].items()}

    def _row_origin(self, idx, origin, *, reuse=False):
        key = (idx.execution, origin)
        # Activity can reuse all rows this read already holds. Explicit inspection
        # keeps its addressed by-origin witness read, as before.
        if not reuse or key not in self._row_origins:
            page = self._row_reply(idx, 'rows', by_origin=origin, limit=1)
            self._row_origins[key] = page['rows'][0] if page['rows'] else None
        return self._row_origins[key]

    def _rows_changes(self, idx, scope, revision):
        changed, after = revision, -1
        while True:
            page = self._row_reply(idx, 'rows_changed', scope=scope,
                since_changed=changed, after_seq=after, limit=500)
            yield from (r for r in page['rows'] if r['changed'] > revision)
            if not page['has_more']:
                break
            point = tuple(page['next'])
            if point <= (changed, after):
                raise RowsUnavailable(idx.execution, 'stored changes made no progress')
            changed, after = point

    # @beacon[
    #   id=opc-pane@ClaudeFacade._rows_page,
    #   slice_labels=opc-pane,opc-rows,
    #   kind=ast,
    #   comment=Selects a lazy stored scope and one diagnostic/incident page for the ONE events renderer; positional new rows and fully paged changed rows share its budgets and minimum as_of watermark.,
    # ]
    def _rows_page(self, idx, scope, diagnostic_start, incident_start):
        rows = StoredScope(self, idx, scope)
        diagnostics = self._row_reply(idx, 'row_diagnostics', offset=diagnostic_start, limit=100)
        incidents = idx.head if incident_start == 0 else self._row_reply(idx, 'rows',
            scope=scope, from_seq=idx.counts.get(scope, 0), limit=1, incident_offset=incident_start)
        return rows, diagnostics, incidents

    def _rows_diagnostics(self, idx):
        values, offset = [], 0
        while True:
            page = self._row_reply(idx, 'row_diagnostics', offset=offset, limit=500)
            values.extend(page['diagnostics'])
            if not page['has_more']:
                return values
            next_offset = page['next_offset']
            if next_offset is None or next_offset <= offset:
                raise RowsUnavailable(idx.execution, 'stored diagnostics made no progress')
            offset = next_offset

    def _rows_activity_tail(self, idx, state):
        """Observe only the newest main row's generation, with the existing drain budget.

        The shared projector writes a throwaway MemoryRows; none of its rows are served.
        There is deliberately no retained tail cursor (CF-01).
        """
        newest = idx.head['rows'][-1] if idx.head['rows'] else None
        generation = state.get('generation')
        idx.activity.pop(generation, None)
        idx.permission_requests = {k: v for k, v in idx.permission_requests.items() if k[0] != generation}
        if newest is None or newest['generation'] != generation:
            return
        identity, seq = newest['origin'].rsplit(':', 1)
        tail = LiveIndex(idx.execution, cursor=identity + ':' + str(int(seq) - 1))
        backend = MemoryRows(tail,
            receipt=lambda mid: self._call('receipt', execution_id=idx.execution, message_uuid=mid),
            observe=lambda event, fields=None, text=None: self._observe_activity(idx, event, fields, text))
        end = time.monotonic() + DRAIN_BUDGET_SECONDS
        def stop(event):
            return time.monotonic() >= end or (event is not None and event['generation'] != generation)
        try:
            complete = project_through(PaneReplaySource(self), idx.execution[3:], RowProjector(backend), stop=stop)
        except ReaderError:
            complete = False  # activity is auxiliary; it never forces a history drain
        if not complete:
            idx.activity.pop(generation, None)
            idx.permission_requests = {k: v for k, v in idx.permission_requests.items() if k[0] != generation}

    def _state(self, execution, generation):
        state = self._call('state', execution_id=execution)
        if not state or state.get('execution_id') != execution:
            raise ReaderError(409, 'stale', 'registered host identity unavailable')
        if state.get('generation') != generation:
            raise ReaderError(409, 'stale-generation', 'host generation changed; reselect')
        return state

    ENDED_STATES = frozenset(('exited', 'exited_draining', 'ownership_unknown', 'launch_failed', 'collected'))

    @classmethod
    def _ended(cls, state):
        """Terminal host states: stored target authority then requires reselection."""
        return (state.get('state') in cls.ENDED_STATES or state.get('owner') in ('gone', 'draining')
                or state.get('exit_code') is not None)

    @classmethod
    def _sleeping(cls, record):
        """A collected conversation: the host closed a settled process on purpose.

        Nothing is lost (resume restores the conversation exactly), so the pane shows
        `sleeping (reconnects on Send)` instead of `ended`. Only the host's durable
        `collected` close reason distinguishes it from a crash or a manual close."""
        return (cls._ended(record) and record.get('role') == 'Manager'
                and record.get('state') != 'ownership_unknown'
                and (record.get('close_reason') or record.get('state')) == 'collected')

    # @beacon[
    #   id=opc-pane@ClaudeFacade._reconnect_blockers,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=The pane offers reconnect exactly when the host that answered would accept it: the host's own reconnect_blockers under that host's stamped policy, plain-words reasons with the way out.,
    # ]
    @classmethod
    def _reconnect_blockers(cls, record):
        """Plain-words reasons Send cannot reconnect this ended Manager ([] = it can).

        The very function the host's ``reconnect`` enforces, under the policy stamped on the
        record by the host that produced it, so the pane never offers what that host refuses."""
        if record.get('role') != 'Manager' or not cls._ended(record):
            return ['only an ended registered Manager conversation is reconnected by Send.']
        if isinstance(record.get('reconnectBlockers'), list):
            return list(record['reconnectBlockers'])  # an inventory summary carries the full record's verdict
        execution, uuid_value = record.get('execution_id'), record.get('native_uuid')
        if not (isinstance(execution, str) and isinstance(uuid_value, str)
                and execution == 'cc-' + uuid_value and canonical_uuid(uuid_value)):
            return ['its host identity is incomplete.']
        if record.get('state') == 'collected' and record.get('owner') == 'gone':
            record = {**record, 'state': 'exited'}  # a collected transport is a proved, settled exit
        return host_reconnect_blockers(record)

    @classmethod
    def _reconnectable(cls, record):
        """A registered Manager conversation a Send may wake (the host's own rule)."""
        return not cls._reconnect_blockers(record)

    @staticmethod
    def _label(record, execution=None):
        """The optional launch label when the host recorded one; the native UUID otherwise.

        A host record carries no name by default, so the honest title is the session UUID;
        an explicit `label`/`title` field, when present, is preferred."""
        for key in ('label', 'title'):
            value = record.get(key)
            if isinstance(value, str) and value.strip() and len(value.encode()) <= 4096:
                return value.strip()
        uuid = record.get('native_uuid')
        if not isinstance(uuid, str) or not uuid:
            uuid = execution[3:] if isinstance(execution, str) and execution.startswith('cc-') else execution
        return uuid if isinstance(uuid, str) and uuid else 'unknown'

    @classmethod
    def _lifecycle(cls, record):
        """Live (ready, writable Manager) / sleeping (collected) / ended (terminal) / pending.

        Derived from the same observations as the capabilities, never a guessed busy flag."""
        if cls._ended(record) and record.get('state') != 'ownership_unknown':
            return 'sleeping' if cls._sleeping(record) else 'ended'
        return 'live' if cls._capabilities(record)['nativeInput'] else 'pending'

    @classmethod
    def _record_recency(cls, record):
        """Best available launch/resume stamp: ordering and successor choice, never authority."""
        cutoff = cls._host_generation_cutoff(record)
        if cutoff is not None:
            return cutoff
        stamp = record.get('last_activity_at')
        if type(stamp) in (int, float) and math.isfinite(stamp) and stamp > 0:
            return float(stamp)
        return 0.0

    # @beacon[
    #   id=opc-pane@ClaudeFacade._row_context,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=The list row's slim lastContext (the /m strip's and the sidebar's Manager gauges): this generation's host-recorded last completed call (input + cache) over its recorded or already-learned window, from the inventory record in hand; never a transcript or stream read, so a closed Manager's figure is only what the host still holds.,
    # ]
    def _row_context(self, record):
        """{tokens, window, at} from the record's own last completed model call, else None."""
        call = record.get('last_completed_model_call')
        if not isinstance(call, dict) or call.get('generation') != record.get('generation'):
            return None
        used = context_input_tokens(call.get('usage'))
        if used is None:
            return None
        model = call.get('model')
        windows = record.get('context_windows') if isinstance(record.get('context_windows'), dict) else {}
        window = windows.get(model)
        if not (type(window) is int and window > 0):
            known = getattr(self.history, 'known_windows', None)
            window = known.get(model) if isinstance(known, dict) else None
        return {'tokens': used, 'window': window if type(window) is int and window > 0 else None,
                'at': timestamp_seconds(call.get('measured_at'))}

    def _learn_windows(self, records):
        """Remember each model's CLI-reported context window from host records (display only)."""
        known = getattr(self.history, 'known_windows', None)
        if not isinstance(known, dict):
            return
        for record in records:
            windows = record.get('context_windows') if isinstance(record, dict) else None
            if isinstance(windows, dict):
                for model, window in windows.items():
                    if isinstance(model, str) and len(model) <= 128 and type(window) is int and window > 0:
                        known[model] = window

    def _record_created(self, record):
        """When the session was created (display order only): its own transcript's first record (a
        fork's own launch message, never the base records it copied), else its generation start,
        else its last activity; 0 when nothing is known."""
        try:
            stamp = self.history.created_for_uuid(record.get('native_uuid'), record.get('cwd'))
        except Exception:  # noqa: BLE001 - an order stamp is cosmetic; never fail the list
            stamp = None
        if stamp is None:
            stamp = self._host_generation_cutoff(record)
        if stamp is None:
            stamp = record.get('last_activity_at')
        return float(stamp) if type(stamp) in (int, float) and math.isfinite(stamp) and stamp > 0 else 0.0

    # @beacon[
    #   id=opc-pane@ClaudeFacade._order_records,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Sidebar summary order: writable Managers then busy/waiting work and its launch ancestors before historical rows; newest activity/creation first within each priority. Creation remains the session's own transcript launch stamp, never copied fork history or its name.,
    # ]
    def _order_records(self, records):
        """Current work and its launch ancestors before history; Managers keep their first-page place.

        Each record gains `createdAt` (epoch seconds, display order only) for the pane."""
        for record in records:
            if isinstance(record, dict):
                record['createdAt'] = self._record_created(record)
        by_id = {r.get('execution_id'): r for r in records}
        active, ancestors = set(), set()
        for r in records:
            tasks = r.get('background_tasks')
            if not self._ended(r) and (r.get('primary_turn_open') is True or
                    r.get('pending_message_uuids') or isinstance(tasks, dict) and tasks and not tasks.get('status')):
                active.add(r.get('execution_id'))
        for execution in active:
            seen, current = set(), by_id.get(execution)
            while current and current.get('execution_id') not in seen:
                seen.add(current.get('execution_id'))
                lineage = self._lineage(current) or {}
                parent = lineage.get('parentSeat') or lineage.get('conductorId')
                if parent not in by_id:
                    # Older launches stamp the program/Conductor number but not its id.
                    parent = next((r.get('execution_id') for r in records
                        if str(r.get('role')).lower() == 'conductor' and lineage.get('program') and
                        (self._lineage(r) or {}).get('program') == lineage['program'] and
                        (self._lineage(r) or {}).get('conductorN') == lineage.get('conductorN')), None)
                if parent not in by_id or parent in seen:
                    break
                ancestors.add(parent)
                current = by_id[parent]
        def order(r):
            execution = r.get('execution_id')
            priority = 0 if self._capabilities(r)['nativeInput'] else 1 if execution in active else 2 if execution in ancestors else 3
            recent = r.get('last_activity_at') if priority in (1, 2) else None
            if type(recent) not in (int, float) or not math.isfinite(recent):
                recent = r.get('createdAt') or 0.0
            return priority, -recent
        return sorted(records, key=order)

    # @beacon[
    #   id=opc-pane@ClaudeFacade._successors,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Ended Manager -> newest ready Manager in the same cwd as naming metadata for the pane row; it grants that row nothing and is never silently targeted.,
    # ]
    def _successors(self, records):
        """Ended Manager execution_id -> its newest ready same-cwd Manager, as metadata.

        The successor names the replacement row; it grants that row nothing and never
        silently targets it. A candidate must itself be a ready, writable Manager."""
        managers = [r for r in records if r.get('role') == 'Manager' and self._capabilities(r)['nativeInput']]
        found = {}
        for record in records:
            if record.get('role') != 'Manager' or not self._ended(record):
                continue
            best = None
            for candidate in managers:
                if (candidate.get('native_uuid') == record.get('native_uuid') or
                        candidate.get('cwd') != record.get('cwd')):
                    continue
                if best is None or self._record_recency(candidate) > self._record_recency(best):
                    best = candidate
            successor = None
            if best is not None:
                execution, generation = best.get('execution_id'), best.get('generation')
                title = self._label(best, execution)
                successor = {'executionId': execution, 'generation': generation, 'uuid': best.get('native_uuid'),
                             'title': title, 'name': title, 'cwd': best.get('cwd'),
                             'sessionRef': self._ref('session', (execution, generation)),
                             # A usable row link even when that workspace's Manager is
                             # beyond the currently mounted inventory page. These are
                             # observation refs; the parent still validates before Send.
                             'source': 'host', 'role': 'Manager', 'state': 'ready', 'lifecycle': 'live',
                             'capabilities': self._capabilities(best),
                             'targetRef': self._ref('target', (execution, generation)),
                             'scopes': [{'label': 'main',
                                         'scopeRef': self._ref('scope', (execution, generation, 'main'))}]}
            found[record.get('execution_id')] = successor
        return found

    def _successor_for(self, state):
        """One bounded inventory read; successor metadata never breaks a live page."""
        if state.get('role') != 'Manager':
            return None
        try:
            records = self._host_list(role='Manager')
        except ReaderError:
            return None
        return self._successors(records).get(state.get('execution_id'))

    def _ended_message(self, state):
        if self._lifecycle(state) not in ('ended', 'sleeping') or state.get('role') != 'Manager':
            return 'host session exited or ownership is unknown; reselect'
        successor = self._successor_for(state)
        replacement = ('The live Manager is ' + successor['title'] + '.' if successor else
                       'No live Manager in this workspace is ready yet; the list refreshes automatically.')
        if self._sleeping(state):
            return 'This Manager is sleeping (reconnects on Send). ' + replacement
        return 'This Manager has ended. ' + replacement + ' Send to it reconnects the conversation.'

    def _get_target(self, ref):
        """Resolve a session-authority ref: a live Send/observation target or an ended
        session's archived (read-only Refine/revalidation) authority."""
        with self.maps_lock:
            value = self.handles.get(ref)
            if value is None:
                raise ReaderError(409, 'stale', 'host handle expired; reselect')
            if value[0] not in ('target', 'archive'):
                raise ReaderError(400, 'schema', 'wrong host handle kind')
            self.handles.move_to_end(ref)
            return value[0], value[1]

    @staticmethod
    def _authority(archived):
        return 'archived' if archived else 'live'

    # @beacon[
    #   id=opc-pane@ClaudeFacade._authority_state,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Splits live target authority (a live owner is required) from an ended session's archived read-only Refine/revalidation authority; both stay bound to exact execution+generation.,
    # ]
    def _authority_state(self, execution, generation, archived=False):
        """State behind a target: live authority needs a live owner; archived authority is
        the ended session's read-only history, still bound to its exact generation."""
        state = self._state(execution, generation)
        if not archived and self._ended(state):
            raise ReaderError(409, 'exit', self._ended_message(state))
        return state

    def _target_state(self, execution, generation):
        """Stable host target authority: exact execution/generation and a live owner.
        The selection-time leaf is deliberately not part of it; transcript growth and
        per-block partial->final swaps keep a target valid, an ended session does not."""
        return self._authority_state(execution, generation)

    # @beacon[
    #   id=opc-pane@ClaudeFacade._settle,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Catches a strict context read's durable index up inside the observer budget; refuses specifically instead of publishing an incomplete frozen read.,
    # ]
    def _settle(self, idx):
        """Catch a strict context read's durable index up inside this reader's observer
        budget. Target/Send authority never depends on this: a stored host target is
        echoed at any honest index status, while a Refine context read must be complete
        to freeze a whole-history path. A too-deep backlog refuses specifically."""
        deadline = getattr(self, '_deadline', None) or (time.monotonic() + 12)
        self._refresh(idx, deadline)
        while idx.status == 'indexing' and time.monotonic() < deadline - 0.5:
            self._refresh(idx, deadline)
        if idx.status == 'indexing':
            raise ReaderError(409, 'indexing', 'host durable index is still catching up; retry in a moment')

    # @beacon[
    #   id=opc-pane@ClaudeFacade._prefetch,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Optional operation-local whole-frame prefetch under its thread's deadline: health opts in once per outer read, remaining drains without a history cutoff, and skips/refusals/incomplete bytes use the same witnessed per-frame reader.,
    # ]
    def _prefetch(self, cursors, cache):
        """Hint this operation's renderer with whole frames; exact raw/hash witnesses still decide every body."""
        if 'raw_frames' not in (self._host_methods or ()):
            return
        frames = self._frames
        pending = list(dict.fromkeys(c for c in cursors if c not in cache and c not in frames))
        while pending:
            try:
                page = self._call('raw_frames', cursors=pending, budget=4*1024*1024)
            except ReaderError as error:
                if isinstance(error.__cause__, TimeoutError):
                    raise  # not a capability refusal: the existing observer budget was spent
                self._host_methods = self._host_methods - {'raw_frames'}
                return  # an older/refusing host uses exactly the ordinary raw path
            if not isinstance(page, dict) or not isinstance(page.get('frames'), dict):
                return
            for cursor in pending:
                frame = page['frames'].get(cursor)
                if not isinstance(frame, dict) or type(frame.get('total')) is not int or not 0 <= frame['total'] <= SOURCE_LIMIT:
                    continue
                try:
                    raw = base64.b64decode(frame['base64'], validate=True)
                except (KeyError, TypeError, ValueError):
                    continue
                if len(raw) == frame['total']:
                    frames[cursor] = raw
            remaining = page.get('remaining')
            if not isinstance(remaining, list):
                return
            remaining = {c for c in remaining if isinstance(c, str)}
            rest = [c for c in pending if c in remaining and c not in frames]
            if len(rest) >= len(pending):
                return  # a non-progressing hint cannot replace raw's existing gap/error checks
            pending = rest

    def _prefetched(self, rows, cache, *, thinking=False, eligible=None, row_key=None):
        """Incremental lookahead for body hints, not another renderer or a limit on rows returned.

        A window is at most the existing page's row count; whole context/changed-row walks
        keep taking windows. Eligibility affects only the hint, never the yielded rows.
        """
        source = iter(rows)
        while True:
            if 'raw_frames' not in (self._host_methods or ()):
                yield from source
                return
            batch = list(islice(source, PAGE_EVENTS))
            if not batch:
                return
            selected = map(row_key, batch) if row_key else batch
            self._prefetch((cursor for row in selected if eligible is None or eligible(row)
                            for cursor in self._row_frames(row, ownership=False, thinking=thinking)), cache)
            yield from batch

    def _raw(self, cursor, cache):
        """Parse through read_frame's existing in-hand/raw paths; release hinted bytes after this parse."""
        if cursor in cache:
            return cache[cursor]
        frames = self._frames
        try:
            value = read_frame(cursor, PaneReplaySource(self), frames)
        finally:
            if frames is not None:
                frames.pop(cursor, None)
        # Request-local only, bounded combined bodies. Repeated 40 blocks reuse one frame.
        total = len(value[0].encode())
        if sum(len(v[0].encode()) for v in cache.values()) + total <= SOURCE_LIMIT:
            cache[cursor] = value
        return value

    def _validated_raw(self, cursor, expected_hash, cache):
        value = self._raw(cursor, cache)
        if expected_hash is None or digest(value[0].encode()) != expected_hash:
            raise ReaderError(409, 'stale-epoch', 'indexed durable raw witness changed; no substitute body')
        return value

    def _turn_facts(self, idx, row, turns):
        """A Claude block's label facts: the model its own frames name and, from the session's
        native transcript record with the same UUID (and message id), the thinking level that
        call was sent with. Each is None unless recorded for exactly this message."""
        model = None if row.get('model_conflict') else row.get('model')
        level = None
        entry = turns.entries.get(row['uuid']) if turns is not None and row['uuid'] and row['scope'] == 'main' else None
        if model and entry is not None:
            message_id, recorded, effort = entry
            if recorded == model and (message_id is None or row.get('message_id') in (None, message_id)):
                level = effort
        elif model and turns is not None and row['uuid'] and row['scope'] == 'main' and row['status'] != 'partial':
            # The transcript line can trail the host frame: re-send this row once its record lands.
            idx.facts_pending[row['origin']] = None
            while len(idx.facts_pending) > 256:
                idx.facts_pending.popitem(last=False)
        return {'model': model, 'effort': level}

    def _index_limit(self, idx):
        """Host indexes never refuse a conversation for its event or retained-row count."""
        return None

    # @beacon[
    #   id=opc-pane@ClaudeFacade._refresh,
    #   slice_labels=opc-pane,opc-rows,
    #   kind=ast,
    #   comment=History-lane durable replay drains 500-event continuations within its own budget; the retained cursor survives partial reads and only short maps sections evict other indexes by aggregate summary bytes.,
    # ]
    def _refresh(self, idx, until=None):
        """History-lane durable replay: consume continuations (500-event pages)
        until the indexed tail is reached ('complete'), the time budget is spent
        ('indexing', resumable from the retained cursor). `until` is _settle's absolute observer
        deadline; without one, a request drains for at most DRAIN_BUDGET_SECONDS."""
        deadline = until if until is not None else time.monotonic() + DRAIN_BUDGET_SECONDS
        if idx.projector is None:
            backend = MemoryRows(idx,
                receipt=lambda uuid: self._call('receipt', execution_id=idx.execution, message_uuid=uuid),
                observe=lambda event, fields=None, text=None: self._observe_activity(idx, event, fields, text))
            idx.projector = RowProjector(backend)

        def stop(event):
            now = time.monotonic()
            if (now >= deadline if event is None else now > deadline):
                idx.status = 'indexing'
                return True
            return False

        complete = project_through(PaneReplaySource(self), idx.execution[3:], idx.projector, stop=stop)
        if not complete:
            idx.status = 'indexing'
            return
        idx.status = 'complete'
        with self.maps_lock:
            while len(self.indexes) > 1 and sum(i.summary_bytes for i in self.indexes.values()) > self.summary_limit:
                old = next(iter(self.indexes))
                if old == idx.execution:
                    break
                self.indexes.pop(old)

    @staticmethod
    def _row_frames(row, *, ownership=True, thinking=False, call=None):
        """The one frame lister: ownership witnesses (default), rendered prose/thinking, or one snapshot's call.

        Body hints exclude tool input/result witnesses the event renderer never opens;
        snapshot hints include only the selected input's final/partial frames and its joined output.
        """
        if call is not None:
            frames = set(call['partial']) if call['input_path'] == 'stream' else {call.get('frame')}
            _, output = join_status(call)
            if output:
                frames.add(output['frame'])
        elif ownership:
            frames = {row['origin'], row.get('frame')}
            frames.update(b.get('frame') for b in row['final_blocks'].values())
            frames.update(part[0] for part in row['text_parts'])
            frames.update(c.get('frame') for c in row['calls'])
        else:
            frames = set()
            if row['kind'] in ('task', 'result') or (0 < row['prose_bytes'] <= BODY_LIMIT and row['frame'] and not row['stream_types']):
                frames.add(row.get('frame'))
            elif 0 < row['prose_bytes'] <= BODY_LIMIT:
                frames.update(b['frame'] for b in row['final_blocks'].values() if b['type'] == 'text')
                frames.update(part[0] for part in row['text_parts'])
        if thinking:
            prose_bytes = row['prose_bytes'] if row['prose_bytes'] <= BODY_LIMIT else 0
            remaining = max(0, BODY_LIMIT - prose_bytes)
            for number, summary in sorted(row['thinking'].items()):
                if summary['type'] == 'redacted_thinking' or not 0 < summary['bytes'] <= remaining:
                    continue
                final = row['final_blocks'].get(number)
                frames.update([final['frame']] if final else (part[0] for part in summary['parts']))
                remaining -= summary['bytes']
        frames.discard(None)
        return frames

    def _prose(self, row, cache):
        if row['prose_bytes'] == 0:
            return ''
        if row['prose_bytes'] > BODY_LIMIT:
            raise ReaderError(413, 'body-limit', 'prose exceeds 8 MiB')
        if row['frame'] and not row['stream_types']:
            _, root = self._validated_raw(row['frame'], row['frame_hash'], cache)
            fields = root.members()
            return frame_prose(fields, fields['message'].members()['content'], row)
        pieces, used = {}, 0
        for number, block in row['final_blocks'].items():
            if block['type'] != 'text':
                continue
            _, root = self._validated_raw(block['frame'], block['frame_hash'], cache)
            fields = root.members()
            if scalar(fields, 'isMeta', default=False):
                continue
            content = fields['message'].members()['content']
            value = scalar(content.children[block['source_block']].members(), 'text', 'string')
            if permitted(value):
                pieces[number] = [value]
                used += len(value.encode())
        for cursor, kind, expected_hash, number in row['text_parts']:
            _, root = self._validated_raw(cursor, expected_hash, cache)
            event = root.members()['event'].members()
            part = event['delta' if kind == 'delta' else 'content_block'].members()
            value = scalar(part, 'text', 'string')
            pieces.setdefault(number, []).append(value)
            used += len(value.encode())
            if used > BODY_LIMIT:
                raise ReaderError(413, 'body-limit', 'partial prose exceeds 8 MiB')
        value = '\n\n'.join(v for n in sorted(pieces) if permitted(v := ''.join(pieces[n])))
        if len(value.encode()) > BODY_LIMIT:
            raise ReaderError(413, 'body-limit', 'combined prose exceeds 8 MiB')
        return value if permitted(value) else ''

    # @beacon[
    #   id=opc-pane@ClaudeFacade._frozen_changes,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Refine's off-by-N tolerance for a live Manager: growth after the freeze never refuses (new rows past the boundary, rows that sent nothing because they were in flight/tools/excluded, a sent message gaining blocks); only a sent message whose prose no longer begins with what Refine read refuses changed-context. With no witness (a pin not minted by a context read) any change inside the boundary refuses as before.,
    # ]
    def _frozen_changes(self, changed, stored, boundary, revision, sent, cache):
        """Check rows changed since a frozen Refine read: refuse only an earlier message rewritten.

        `sent` maps a row origin to the (chars, sha256) of the prose that read took from it;
        None means no witness, so any body change inside the boundary still refuses."""
        for row in changed:
            if row['body_changed'] <= revision or (stored and row['seq'] >= boundary):
                continue  # unchanged, or appended after the read
            if sent is None:
                raise ReaderError(409, 'changed-context', 'frozen host prose changed; reselect')
            witnesses = sent.get(row['origin'])
            if not witnesses:
                continue  # it contributed nothing: in flight at the read, a tool/internal row or outside the budget
            now = self._prose(row, cache)
            for chars, expected in witnesses:
                if len(now) < chars or hashlib.sha256(now[:chars].encode()).hexdigest() != expected:
                    raise ReaderError(409, 'changed-context',
                                      'an earlier message Refine read was rewritten, not appended; Refine again')

    def _remember_context(self, key, sent):
        with self.maps_lock:
            previous = self.context_witness.get(key, {})
        # Proportional prose bookkeeping stays on the history lane, never under maps.
        merged = {origin: set(witnesses) for origin, witnesses in previous.items()}
        for origin, witnesses in sent.items():
            merged.setdefault(origin, set()).update(witnesses)
        with self.maps_lock:
            self.context_witness[key] = merged
            self.context_witness.move_to_end(key)
            while len(self.context_witness) > CONTEXT_WITNESS_LIMIT:
                self.context_witness.popitem(last=False)

    def _thinking(self, row, cache, remaining):
        items = []
        for number, summary in sorted(row['thinking'].items()):
            streaming = not summary['stopped'] and number not in row['final_blocks'] and not row.get('stream_stopped')
            base = {'blockIndex': number, 'text': '', 'textBytes': summary['bytes'],
                    'availability': 'omitted', 'streaming': streaming}
            if summary['type'] == 'redacted_thinking':
                base['availability'] = 'redacted'
            elif summary['bytes'] > remaining:
                base['availability'] = 'body-limit'
            elif summary['bytes']:
                final = row['final_blocks'].get(number)
                if final:
                    _, root = self._validated_raw(final['frame'], final['frame_hash'], cache)
                    block = root.members()['message'].members()['content'].children[final['source_block']].members()
                    base, _ = thinking_projection(block, number, remaining, streaming=streaming)
                else:
                    pieces = []
                    for cursor, kind, expected_hash in summary['parts']:
                        _, root = self._validated_raw(cursor, expected_hash, cache)
                        event = root.members()['event'].members()
                        block = event['delta' if kind == 'delta' else 'content_block'].members()
                        pieces.append(scalar(block, 'thinking', 'string', default=''))
                    base.update(text=''.join(pieces), availability='stored')
                remaining -= summary['bytes']
            items.append(base)
        return items

    def _record_ref(self, idx, row):
        return self._ref('record', (idx.execution, idx.epoch, row['origin']))

    def _calls(self, idx, row, times=False):
        calls = []
        for call in row['calls']:
            status_call, output = join_status(call)
            cref = self._ref('call', (idx.execution, idx.epoch, row['origin'], row['generation'],
                                    row['scope'], call['block'], call['id'], call['name']))
            calls.append({key: call[key] for key in ('block', 'id', 'name', 'inputBytes', 'previewData')})
            calls[-1].update(callRef=cref, outputBytes=output['outputBytes'] if output else None,
                             status=status_call)
            if times:
                # As the call's snapshot states them (display only: how long it took, on its row); the result's row
                # comes from the rows this read already holds when it can.
                calls[-1].update(time=row.get('time'), resultTime=self._result_time(idx, output, reuse=True) if output else None)
        return calls

    def _event(self, idx, row, cache, turns=None):
        receipt = None
        if row['kind'] == 'task':
            text, root = self._validated_raw(row['frame'], row['frame_hash'], cache)
            fields = root.members()
            parts = ['Native task observation (not task acceptance or parent context)']
            for key in ('task_id', 'description', 'subagent_type', 'status', 'last_tool_name', 'summary', 'tool_name'):
                value = scalar(fields, key, 'string')
                if value:
                    parts.append(key+': '+(value if len(value) <= 4096 else value[:4096]+' [display label shortened]'))
            prose = '\n'.join(parts)
            size, status = len(prose.encode()), row['status']
        elif row['kind'] == 'result':
            text, root = self._validated_raw(row['frame'], row['frame_hash'], cache)
            fields = root.members()
            receipt = {k: json.loads(text[v.start:v.end]) for k, v in fields.items() if k in (
                'subtype', 'is_error', 'terminal_reason', 'errors', 'permission_denials',
                'origin', 'result_index', 'user_message_uuid', 'user_message_uuids', 'queued_turn_count')}
            receipt['task_acceptance'] = 'unjudged'
            # Fixed numeric telemetry only; never transport arbitrary new receipt
            # objects or double the assistant's prose to render a compact marker.
            cost_span = fields.get('total_cost_usd')
            if cost_span and cost_span.kind == 'number' and cost_span.end - cost_span.start <= 128:
                cost = json.loads(projection(text, cost_span))
                if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
                    receipt['total_cost_usd'] = cost
            usage = fields.get('usage')
            if usage and usage.kind == 'object':
                counters = {}
                for key in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'):
                    value = usage.members().get(key)
                    if value and value.kind == 'number' and value.end - value.start <= 32:
                        n = json.loads(projection(text, value))
                        if type(n) is int and 0 <= n <= 2**53 - 1:
                            counters[key] = n
                if counters:
                    receipt['usage'] = counters
            prose = scalar(fields, 'result', 'string', default='')
            size, status = len(prose.encode()), row['status']
        else:
          try:
            prose = self._prose(row, cache)
            size = len(prose.encode())
            status = 'body-limit' if size > BODY_LIMIT else row['status']
          except ReaderError as error:
            if error.code != 413:
                raise
            prose, size, status = None, None, error.status
        thinking = self._thinking(row, cache, max(0, BODY_LIMIT - (size or 0)))
        facts = self._turn_facts(idx, row, turns) if row['kind'] == 'assistant' else {}
        return {**facts, 'recordRef': self._record_ref(idx, row), 'uuid': row['uuid'],
                'kind': row['kind'], 'scope': row['scope'], 'status': status,
                'text': prose if size is None or size <= BODY_LIMIT else None,
                'textBytes': size, 'calls': self._calls(idx, row, times=True), 'orphanResults': [],
                'thinking': thinking, 'partial': row['status'] == 'partial',
                'replays': row.get('replays', 0), 'source': 'host', 'time': row.get('time'),
                'generation': row['generation'], 'provenance': 'host-durable-feed',
                'replayGenerations': row.get('replay_generations', []),
                **({'receipt': receipt} if receipt is not None else {})}

    @staticmethod
    def _host_raw_bytes(state):
        """The retained raw stream size for one host record: a stat only, never content.

        The durable raw files are the host's own private state; the viewer reports their
        total so a slow first index explains itself. A generation directory that exists
        with no bytes yet is a known zero; an uninspectable run_dir is unknown (None).
        """
        run_dir = state.get("run_dir") if isinstance(state, dict) else None
        uuid = state.get("native_uuid") if isinstance(state, dict) else None
        if not isinstance(run_dir, str) or not run_dir.startswith("/") or not isinstance(uuid, str):
            return None
        base = Path(run_dir).parent
        if base.name != uuid or not base.is_dir():
            return None
        total = 0
        try:
            for generation in base.glob("g*/"):
                for name in ("stdout.bin", "stdin.bin", "stderr.bin"):
                    candidate = generation / name
                    if candidate.is_file():
                        total += candidate.stat().st_size
        except OSError:
            return None
        return total

    # Bounded LRU of native window scans: (uuid, run_dir) -> (stdout signature, {model: window}).

    @staticmethod
    def _host_generation_cutoff(state):
        """The exact start of a host session's current generation.

        The record's own launch stamp when present; otherwise launch.json's mtime, written
        just before the process starts and never rewritten. Never the run directory's
        st_ctime (it moves when a new stream file is created) and never a previous
        generation's transcript call: a resumed generation must not borrow the call it
        resumed from. An unknown cutoff returns None and the caller refuses to guess.
        """
        stamp = state.get("context_generation_started_at") if isinstance(state, dict) else None
        if type(stamp) in (int, float) and math.isfinite(stamp) and stamp > 0:
            return float(stamp)
        run_dir = state.get("run_dir") if isinstance(state, dict) else None
        if not isinstance(run_dir, str) or not run_dir.startswith("/"):
            return None
        try:
            created = os.stat(Path(run_dir) / "launch.json").st_mtime
        except OSError:
            return None
        return float(created) if created > 0 else None

    @staticmethod
    def _scan_result_windows(path, size):
        """Bounded reverse scan of one raw stream for its latest result frame's windows."""
        floor = max(0, size - HOST_CONTEXT_TAIL_BYTES)
        base, buffer = size, b""
        try:
            with path.open("rb") as stream:
                while True:
                    candidates = buffer.split(b"\n")[:-1]
                    for index in range(len(candidates) - 1, (-1 if base == 0 else 0) - 1, -1):
                        line = candidates[index]
                        if b'"modelUsage"' not in line:
                            continue
                        try:
                            frame = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        if not isinstance(frame, dict) or frame.get("type") != "result":
                            continue
                        usage = frame.get("modelUsage")
                        if not isinstance(usage, dict):
                            continue
                        windows = {}
                        for model, detail in usage.items():
                            window = detail.get("contextWindow") if isinstance(detail, dict) else None
                            if isinstance(model, str) and type(window) is int and window > 0:
                                windows[model] = window
                        if windows:
                            return windows
                    if base <= floor:
                        return None
                    new_base = max(floor, base - HOST_CONTEXT_CHUNK_BYTES)
                    stream.seek(new_base)
                    chunk = stream.read(base - new_base)
                    if not chunk:
                        return None
                    buffer = chunk + buffer
                    base = new_base
        except OSError:
            return None

    def _host_context_windows(self, state):
        """The CLI's own reported context window per model, from the raw stream's result tail.

        `modelUsage[model].contextWindow` is the actual window; a bounded reverse scan of
        the current generation's stdout is read-only and stat-cached. Unknown (None) beats
        an assumed 1M — the pane still shows measured tokens without a window. This
        facade's cache uses short maps sections; the file scan stays outside them.
        """
        run_dir = state.get("run_dir") if isinstance(state, dict) else None
        uuid = state.get("native_uuid") if isinstance(state, dict) else None
        if not isinstance(run_dir, str) or not run_dir.startswith("/") or not isinstance(uuid, str):
            return None
        path = Path(run_dir) / "stdout.bin"
        try:
            st = os.stat(path)
        except OSError:
            return None
        signature = (st.st_ino, st.st_size, st.st_mtime_ns)
        key = (uuid, run_dir)
        with self.maps_lock:
            cached = self.window_cache.get(key)
            if cached is not None and cached[0] == signature:
                self.window_cache.move_to_end(key)
                return cached[1]
        windows = self._scan_result_windows(path, st.st_size)
        with self.maps_lock:
            self.window_cache[key] = (signature, windows)
            self.window_cache.move_to_end(key)
            while len(self.window_cache) > HOST_WINDOW_CACHE_FILES:
                self.window_cache.popitem(last=False)
        return windows

    # @beacon[
    #   id=opc-pane@ClaudeFacade.host_context_readout,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Host context for the pane: matching-generation host call first, else a bounded cutoff-checked native transcript read with the CLI's own modelUsage window; rides the first events page.,
    # ]
    def _host_context_readout(self, state, generation):
        """The last completed model call for a host session: matching-generation snapshot first.

        A host-recorded call for THIS generation is exact, so it wins outright. Otherwise the
        native transcript is read through the shared bounded witness scan, cut off at the
        exact generation start; a call from a previous generation is never borrowed, and an
        unknown cutoff refuses rather than guesses. The window prefers the host-recorded
        `context_windows`, else the CLI's own modelUsage value from the raw stream's result
        tail. The figure arrives with the first page, independent of the durable-feed index.
        """
        record = {key: state.get(key) for key in ("execution_id", "generation", "role", "context_target")}
        record["native_uuid"] = state.get("native_uuid")
        windows = state.get("context_windows") if isinstance(state.get("context_windows"), dict) else None
        try:
            call = state.get("last_completed_model_call")
            if not windows or (isinstance(call, dict) and call.get("model") not in windows):
                observed = self._host_context_windows(state)
                windows = {**(windows or {}), **(observed or {})} or None
            if (isinstance(call, dict) and call.get("generation") == generation
                    and isinstance(call.get("usage"), dict) and call["usage"]):
                return self.history.context_report(
                    call["usage"], call.get("measured_at"), record=record,
                    source=call.get("source") or "host completed model call", windows=windows,
                    model=call.get("model"), message_id=call.get("message_id"))
            cutoff = self._host_generation_cutoff(state)
            if cutoff is None:
                return self.history._context_unavailable(
                    record, "native-transcript",
                    "no matching-generation host call and the generation cutoff is unknown")
            return self.history.context_readout_for_uuid(
                state.get("native_uuid"), cwd=state.get("cwd"), record=record,
                windows=windows, not_before=cutoff)
        except Exception as error:  # noqa: BLE001 — the context line never breaks the live page
            return self.history._context_unavailable(record, "native-transcript", "context read failed: " + str(error)[:120])

    # @beacon[
    #   id=opc-pane@ClaudeFacade.events,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Identical in-flight pages share this history-lane witnessed renderer and operation-local raw batches; explicit unreadable rows carry one per-conversation notice on the unchanged raw path, while frozen paths and Refine checks never gate Manager selection or Send.,
    # ]
    @shared_history_read
    @reader_operation
    def events(self, *, sessionRef=None, scopeRef=None, leafRef=None, fileEpoch=None,
               targetRef=None, cursor=None, fromSeq=None):
        """Render one shared addressed history page/delta, carrying explicit row damage on the raw fallback."""
        ref = targetRef or sessionRef
        if not self._has_live(ref):
            return self.history.events(sessionRef=sessionRef, scopeRef=scopeRef, leafRef=leafRef,
                                       fileEpoch=fileEpoch, targetRef=targetRef, cursor=cursor, fromSeq=fromSeq)
        check_from_seq(fromSeq, targetRef, leafRef, cursor)
        with self.history_lock:
            stale_epoch = False
            if targetRef:
                # A host targetRef is the session's authority — exact execution, generation
                # and live role/capabilities — never the selection-time leaf. Transcript
                # growth, per-block partial->final swaps and a replaced leaf record keep it
                # valid; only a generation change or an ended session require reselection.
                # An optional leafRef(+fileEpoch) pins a frozen context path (Refine),
                # validated against the durable index without re-reading prose.
                if any((sessionRef, scopeRef)):
                    raise ReaderError(400, 'schema', 'mixed addressing forms')
                kind, (execution, generation) = self._get_target(targetRef)
                archived = kind == 'archive'
                scope = 'main'
                state = self._authority_state(execution, generation, archived)
                stored = self._rows_available(execution)
                idx = self._row_set(execution) if stored else self._index_live(execution)
                # Drain within the normal refresh budget; a still-cold
                # index is honest metadata, never a loss of session Send authority.
                if stored:
                    pass
                elif leafRef:
                    # A context pin is different: unprocessed final replacements can
                    # change its old prefix. Catch up before claiming it unchanged.
                    self._settle(idx)
                else:
                    self._refresh(idx)
                epoch = idx.epoch
                boundary, revision = None, None
                if fileEpoch is not None and fileEpoch != epoch:
                    if not stored:
                        raise ReaderError(409, 'stale-epoch', 'host epoch changed; reselect')
                    stale_epoch = True
                if leafRef:
                    # Frozen-context pin: the path witness is validated against the durable
                    # complete index; unlike session authority it must observe every
                    # pending replacement before claiming the frozen read unchanged.
                    ex, ep, sc, count, pinned = self._get_live(leafRef, 'leaf')
                    if (ex, sc) != (execution, scope) or (ep != epoch and not stored):
                        raise ReaderError(409, 'stale-epoch', 'host frozen path identity changed; reselect')
                    if ep != epoch:
                        stale_epoch = True
                    else:
                        boundary, revision = count, pinned
            else:
                execution, generation = self._get_live(sessionRef, 'session')
                ex, gen, scope = self._get_live(scopeRef, 'scope')
                if (ex, gen) != (execution, generation):
                    raise ReaderError(409, 'ambiguous', 'host scope/session mismatch')
                epoch, boundary, revision = fileEpoch, None, None
                state = self._state(execution, generation)
                stored = self._rows_available(execution)
                idx = self._row_set(execution) if stored else self._index_live(execution, epoch)
                if stored:
                    if epoch is not None and epoch != idx.epoch:
                        stale_epoch = True
                else:
                    self._refresh(idx)
            rows = StoredScope(self, idx, scope) if stored else [r for r in idx.rows if r['scope'] == scope]
            # fromSeq: a first page that starts at that row position (seq) with fresh cursors; the
            # viewer resyncs the rows already on screen after a pane-server restart voided its handles.
            mode, old_revision = 'forward', idx.revision
            diagnostic_start, incident_start = 0, 0
            if cursor:
                value = self._get_live(cursor, 'events')
                ex, ep, sc, gen, start, mode, old_revision = value[:7]
                diagnostic_start, incident_start = value[7:9] if len(value) >= 9 else (0, 0)
                if (ex, sc, gen) != (execution, scope, generation) or (ep != idx.epoch and not stored):
                    raise ReaderError(409, 'stale-epoch', 'host cursor identity changed')
                if ep != idx.epoch:
                    # Establish that rows can serve this read before refusing a mode
                    # switch. Never compare a fallback revision with a rows revision;
                    # a downstream failure must still get to try the original cursor
                    # against the retained fallback index.
                    stale_epoch = True
                    start, old_revision = min(start, len(rows)), idx.revision
                    diagnostic_start, incident_start = 0, 0
            if leafRef:
                ex, ep, sc, count, rev = self._get_live(leafRef, 'leaf')
                if (ex, sc) != (execution, scope) or (ep != idx.epoch and not stored):
                    raise ReaderError(409, 'stale-epoch', 'host leaf identity changed')
                if ep != idx.epoch:
                    stale_epoch = True
                elif mode != 'delta':
                    rows = rows[:count]
            if boundary is not None and mode != 'delta':
                rows = rows[:boundary]
                changed = self._rows_changes(idx, scope, revision) if stored else rows
                # The live turn may have moved on since Refine read this path; only a message
                # that read sent being rewritten refuses (its witness, kept by the context read).
                frozen = (execution, epoch, scope, boundary, revision)
                with self.maps_lock:
                    sent = self.context_witness.get(frozen)
                frozen_cache = {}
                changed = self._prefetched(changed, frozen_cache, eligible=lambda r:
                    r['body_changed'] > revision and (not stored or r['seq'] < boundary) and bool(sent and sent.get(r['origin'])))
                self._frozen_changes(changed, stored, boundary, revision, sent, frozen_cache)
            tail_open = stored and fromSeq is None and not cursor and not leafRef
            if not cursor:
                start = max(0, len(rows) - PAGE_EVENTS) if tail_open else min(fromSeq or 0, len(rows))
            if stored:
                selected_count = len(rows)
                rows, diagnostic_page, incident_page = self._rows_page(idx, scope, diagnostic_start, incident_start)
                rows = rows[:selected_count]
                self._rows_activity_tail(idx, state)
            cache, events, used, end = {}, [], 0, start
            # Thinking levels come from the session's own native transcript records; an
            # unreadable or missing transcript only leaves the levels out (display only).
            try:
                turns = self.history.host_turn_ledger(state.get('native_uuid'), state.get('cwd')) if scope == 'main' else None
            except (ReaderError, OSError):
                turns = None
            numbers = range(len(rows) - 1, start - 1, -1) if tail_open else range(start, len(rows))
            self._prefetch((c for n in numbers[:PAGE_EVENTS]
                            for c in self._row_frames(rows[n], ownership=False, thinking=True)), cache)
            for number in numbers:
                row = self._event(idx, rows[number], cache, turns)
                size = len(json.dumps(row, ensure_ascii=False).encode())
                if size > PAGE_LIMIT - 64 * 1024:
                    row = self._limit_event(row, size)
                    size = len(json.dumps(row).encode())
                if events and (len(events) >= PAGE_EVENTS or used + size > PAGE_LIMIT - 64 * 1024):
                    break
                # Its position in this scope's rows (stable within the epoch): the viewer keeps
                # its resident window one contiguous run and can never splice a silent gap.
                row['seq'] = number
                events.append(row)
                used += size
                end = number + 1
            if tail_open:
                events.reverse()
                start, end = (events[0]['seq'] if events else len(rows)), len(rows)
            updates, summaries = [], []
            # A forward continuation also refreshes the durable index. Its earlier
            # rows may still be streaming (even when opening completed history).
            # Advancing the cursor revision without their replacements permanently
            # strands the browser on that first partial body.
            if cursor:
                changed = ((r['seq'], r) for r in self._rows_changes(idx, scope, old_revision)
                           if r['seq'] < min(start, len(rows))) if stored else enumerate(rows[:start])
                changed = sorted(changed, key=lambda pair: pair[0]) if stored else changed
                changed = self._prefetched(changed, cache, thinking=True, row_key=lambda pair: pair[1],
                    eligible=lambda r: r['changed'] > old_revision and r['body_changed'] > old_revision)
                for number, row in changed:
                    if row['changed'] <= old_revision:
                        continue
                    value = self._event(idx, row, cache, turns) if row['body_changed'] > old_revision else None
                    if value is not None:
                        body_size = len(json.dumps(value, ensure_ascii=False).encode())
                        if body_size > PAGE_LIMIT - 64 * 1024:
                            value = self._limit_event(value, body_size)
                        value['seq'] = number
                    summary = [{'recordRef': self._record_ref(idx, row), **call} for call in self._calls(idx, row, times=True)]
                    size = len(json.dumps([value, summary], ensure_ascii=False).encode())
                    if len(updates) >= PAGE_EVENTS or used + size > PAGE_LIMIT - 64 * 1024:
                        # Do not publish a cursor which silently loses earlier replacements.
                        raise ReaderError(409, 'reload-required', 'changed live rows exceed one page; reload')
                    if value is not None:
                        updates.append(value)
                    used += size
                    summaries.extend(summary)
                used = self._late_facts(idx, rows, start, scope, turns, cache, updates, used)
            activity, agents =self._subagent_activity(idx, rows, start, end, mode, old_revision, bool(cursor), cache,
                max(0, min(1024 * 1024, PAGE_LIMIT - 128 * 1024 - used))) if scope == 'main' else ([], [])
            pinned_revision = revision if (targetRef and boundary is not None and mode != 'delta') else idx.revision
            identity = (execution, idx.epoch, scope, len(rows), pinned_revision)
            if stored:
                diagnostics, incidents = diagnostic_page['diagnostics'], incident_page['incidents']
                diagnostic_end, incident_end = diagnostic_start + len(diagnostics), incident_start + len(incidents)
                metadata_more = diagnostic_page['has_more'] or incident_end < incident_page['incident_count']
            else:
                diagnostic_end = min(diagnostic_start + 100, len(idx.diagnostics))
                incident_end = min(incident_start + 100, len(idx.incidents))
                diagnostics, incidents = idx.diagnostics[diagnostic_start:diagnostic_end], idx.incidents[incident_start:incident_end]
                metadata_more = diagnostic_end < len(idx.diagnostics) or incident_end < len(idx.incidents)
            more = end < len(rows) or idx.status == 'indexing' or metadata_more
            leaf = self._ref('leaf', identity)
            # A requested target is echoed: its authority is the live session (or the ended
            # session's archived read-only authority), never this page's completion. A fresh
            # selection mints session authority too: replay diagnostics and index progress
            # gate Refine context (the context read re-checks both) but never Send or
            # observation authority. Selection during initialization therefore already has
            # an observation target the pane can keep revalidating to writable.
            if targetRef:
                target = targetRef
            elif scope == 'main':
                target = self._ref('archive' if self._ended(state) else 'target', (execution, generation))
            else:
                target = None
            result = envelope(sessionRef=self._ref('session', (execution, generation)),
                scopeRef=self._ref('scope', (execution, generation, scope)), leafRef=leaf,
                fileEpoch=idx.epoch, targetRef=target, events=events, branches=[],
                traces=sorted(s for s in idx.counts if s != 'main') if stored else sorted({r['scope'] for r in idx.rows if r['scope'] != 'main'}),
                diagnostics=[{k: v for k, v in d.items() if k != 'revision'} for d in diagnostics],
                incidents=incidents, liveState=self._live_state(state),
                source='host', role=state.get('role', 'unknown'), executionId=execution, generation=generation,
                indexRevision=idx.revision, snapshotRevision=pinned_revision, status=idx.status,
                indexLimit=None if stored else self._index_limit(idx), hasMore=more, rowSource='host' if stored else 'fallback',
                rowsUnreadable=execution in self._rows_unreadable,
                rowsAhead=end < len(rows) or idx.status == 'indexing',
                nextCursor=self._ref('events', (execution, idx.epoch, scope, generation, end, 'forward', idx.revision, diagnostic_end, incident_end)) if more else None,
                deltaCursor=self._ref('events', (execution, idx.epoch, scope, generation, end, 'delta', idx.revision, diagnostic_end, incident_end)),
                change='append' if mode == 'delta' else 'snapshot', eventUpdates=updates, summaryUpdates=summaries,
                indexingContinuation=self._ref('events', (execution, idx.epoch, scope, generation, end,
                    'forward', idx.revision, diagnostic_end, incident_end))
                    if idx.status == 'indexing' and end == len(rows) else None)
            # The context line rides every page: the figure arrives with the first page
            # (bounded transcript tail read), never after the whole durable feed replays.
            result['subagentActivity'] = activity
            result['subagents'] = agents
            result['contextReadout'] = self._host_context_readout(state, generation)
            result['sourceProgress'] = {'indexedBytes': self._host_raw_bytes(state) if stored else idx.indexed_bytes,
                'indexedRecords': sum(idx.counts.values()) if stored else len(idx.rows),
                'sourceBytes': self._host_raw_bytes(state), 'status': idx.status, 'kind': 'host-raw'}
            if target:
                result['targetInfo'] = self.target_info(result, state)
            if stale_epoch:
                raise ReaderError(409, 'stale-epoch', 'host mode/epoch changed; reselect')
            return result

    def _late_facts(self, idx, rows, start, scope, turns, cache, updates, used):
        """Re-send earlier rows whose transcript record (their thinking level) landed after they were sent.

        A display-only replacement with optional body prefetch: no row revision moves,
        so frozen Refine paths are untouched."""
        if turns is None or not idx.facts_pending:
            return used
        sent = {u['recordRef'] for u in updates}
        def candidates():
            positions = None
            for origin in list(idx.facts_pending):
                row = self._row_origin(idx, origin) if isinstance(idx, RowsIndex) else idx.by_origin.get(origin)
                if row is None or row['scope'] != scope or row['uuid'] not in turns.entries:
                    if row is None or row['scope'] != scope:
                        idx.facts_pending.pop(origin, None)
                    continue
                if isinstance(idx, RowsIndex):
                    number = row['seq'] if row['seq'] < len(rows) else None
                else:
                    if positions is None:
                        positions = {id(r): n for n, r in enumerate(rows)}
                    number = positions.get(id(row))
                if number is None or number >= start or self._record_ref(idx, row) in sent:
                    idx.facts_pending.pop(origin, None)
                    continue
                yield origin, number, row
        for origin, number, row in self._prefetched(candidates(), cache, thinking=True, row_key=lambda item: item[2]):
            try:
                value = self._event(idx, row, cache, turns)
            except ReaderError:
                idx.facts_pending.pop(origin, None)  # its body is not readable now: the label waits for a reload
                continue
            value['seq'] = number
            size = len(json.dumps(value, ensure_ascii=False).encode())
            if len(updates) >= PAGE_EVENTS:
                break  # still pending: the next poll carries it
            if used + size > PAGE_LIMIT - 64 * 1024:
                continue
            idx.facts_pending.pop(origin, None)
            updates.append(value)
            used += size
        return used

    ACTIVITY_LIMIT = 400

    # @beacon[
    #   id=opc-pane@ClaudeFacade._subagent_activity,
    #   slice_labels=opc-pane,opc-rows,
    #   kind=ast,
    #   comment=Display-only child calls/text/tasks through the same renderer and optional raw-body batches, anchored within the selected main path; anchors reuse fetched rows and each missing origin is looked up at most once, including truncated views.,
    # ]
    def _subagent_activity(self, idx, rows, start, end, mode, old_revision, continued, cache, budget=1024 * 1024):
        """Child rows interleaved with this page's main rows, as compact display entries.

        The window is the stretch of arrival order this page covers (after main row
        start-1, before main row end, or to the tail); a delta page sends only entries
        changed since its cursor, wherever they sit, so a running call's status updates
        in place. ``after`` names the preceding primary turn (Dan's message or an
        assistant row) so the browser groups per subagent between primary turns.
        Its stored origin is clamped to the latest anchor in this selected main
        path at or before it; a truncated path never names an anchor outside it.
        Eligible child body frames use the same optional prefetch as main rows."""
        stored = isinstance(idx, RowsIndex)
        has_agents = not stored or any(s.startswith('agent:') for s in idx.counts)
        def boundary(number):
            if not stored or number in rows.known:
                return rows[number]
            page = self._row_reply(idx, 'rows', scope=rows.scope, from_seq=number, limit=1)
            if not page['rows']:
                raise RowsUnavailable(idx.execution, 'stored activity boundary unavailable')
            return page['rows'][0]
        low = boundary(start - 1)['ordinal'] if has_agents and 0 < start <= len(rows) else -1
        high = boundary(end)['ordinal'] if has_agents and end < len(rows) else None
        # Index only the supplied response path, not the full index's anchors.
        # The stored after_origin, not the child's arrival, remains the witness.
        anchors = [] if stored else [r for r in rows if r['kind'] == 'assistant' or (r['kind'] == 'user' and r.get('genuine'))]
        anchor_ordinals = [r['ordinal'] for r in anchors]
        entries = []
        children = self._rows_activity(idx, low, high, mode, old_revision, continued) if stored else idx.rows
        children = self._prefetched(children, cache, eligible=lambda r:
            r['scope'].startswith('agent:') and r['kind'] in ('task', 'assistant') and
            (high is None or r['ordinal'] < high) and
            (r['changed'] > old_revision if mode == 'delta' or (continued and r['ordinal'] <= low) else r['ordinal'] > low))
        for row in children:
            if not row['scope'].startswith('agent:'):
                continue
            ordinal = row['ordinal']
            if high is not None and ordinal >= high:
                break
            if mode == 'delta' or (continued and ordinal <= low):
                if row['changed'] <= old_revision:
                    continue
            elif ordinal <= low:
                continue
            if stored:
                stored_anchor = self._row_origin(idx, row['after_origin'], reuse=True) if row.get('after_origin') else None
                after_origin = None
                # The same Q1 clamp as the bisect below, including a frozen prefix.
                if stored_anchor:
                    for number in range(min(stored_anchor['seq'], len(rows) - 1), -1, -1):
                        anchor = stored_anchor if number == stored_anchor['seq'] else rows[number]
                        if anchor['kind'] == 'assistant' or (anchor['kind'] == 'user' and anchor.get('genuine')):
                            after_origin = anchor['origin']
                            break
            else:
                stored_anchor = idx.by_origin.get(row.get('after_origin'))
                at = bisect_right(anchor_ordinals, stored_anchor['ordinal']) - 1 if stored_anchor else -1
                after_origin = anchors[at]['origin'] if at >= 0 else None
            for entry in self._activity_entries(idx, row, cache):
                entry['after'] = self._ref('record', (idx.execution, idx.epoch, after_origin)) if after_origin else None
                entries.append(entry)
        # Bounded like any page: the newest entries win (they are where the work is now).
        kept, used = [], 0
        for entry in reversed(entries):
            size = len(json.dumps(entry, ensure_ascii=False).encode())
            if len(kept) >= self.ACTIVITY_LIMIT or used + size > budget:
                break
            kept.append(entry)
            used += size
        entries = list(reversed(kept))
        agents = []
        registry = [((a['generation'], a['call']), a) for a in idx.head['agent_info']] if stored else idx.agent_info.items()
        for (gen, call), info in registry:
            agents.append({'agent': call, 'generation': gen, **{k: info.get(k) for k in (
                'index', 'agentId', 'description', 'subagentType', 'state', 'started', 'finished')}})
        return entries, agents

    def _rows_activity(self, idx, low, high, mode, revision, continued):
        found = {}
        if not any(s.startswith('agent:') for s in idx.counts):
            return []
        if mode != 'delta':
            after = low
            while True:
                page = self._row_reply(idx, 'rows', agent_window=[after, high], limit=500)
                found.update((r['origin'], r) for r in page['rows'])
                if not page['has_more']:
                    break
                next_after = page['next_after_ordinal']
                if next_after <= after:
                    raise RowsUnavailable(idx.execution, 'stored agent window made no progress')
                after = next_after
        if mode == 'delta' or continued:
            for scope in idx.counts:
                if scope.startswith('agent:'):
                    for row in self._rows_changes(idx, scope, revision):
                        if (high is None or row['ordinal'] < high) and (mode == 'delta' or row['ordinal'] <= low):
                            found[row['origin']] = row
        return sorted(found.values(), key=lambda r: r['ordinal'])

    def _activity_entries(self, idx, row, cache):
        agent = row['scope'][6:]
        base = {'agent': agent, 'time': row.get('time'), 'ordinal': row['ordinal'],
                'generation': row['generation']}
        record = self._record_ref(idx, row)
        if row['kind'] == 'task':
            text, root = self._validated_raw(row['frame'], row['frame_hash'], cache)
            fields = root.members()
            value = lambda key: (scalar(fields, key, 'string') or '')[:400] or None
            return [{**base, 'key': record, 'type': 'task', 'status': row['status'],
                     'taskType': value('task_type'), 'taskStatus': value('status'),
                     'description': value('description'), 'summary': value('summary')}]
        if row['kind'] != 'assistant':
            return []
        out = []
        if row['prose_bytes']:
            try:
                prose = self._prose(row, cache)
            except ReaderError:
                prose = None
            if prose:
                out.append({**base, 'key': record + ':text', 'type': 'text',
                            'text': prose[:400], 'textBytes': len(prose.encode())})
        for call in self._calls(idx, row):
            preview = call.get('previewData')
            if preview is not None and len(json.dumps(preview, ensure_ascii=False).encode()) > 8 * 1024:
                preview = {'schemaVersion': 1, 'status': 'unavailable', 'reason': 'activity preview limit'}
            out.append({**base, 'key': record + ':' + str(call['block']) + ':' + call['id'], 'type': 'call',
                        'name': call['name'], 'id': call['id'], 'block': call['block'], 'callRef': call['callRef'],
                        'status': call['status'], 'previewData': preview,
                        'inputBytes': call.get('inputBytes'), 'outputBytes': call.get('outputBytes')})
        return out

    @staticmethod
    def _limit_event(row, size):
        result = {**row, 'text': None, 'calls': [], 'status': 'response-limit', 'bodyBytes': size,
                'thinking': [{**b, 'text': '', 'availability': 'body-limit' if b['availability'] == 'stored'
                              else b['availability']} for b in row.get('thinking', [])]}
        if len(json.dumps(result, ensure_ascii=False).encode()) > PAGE_LIMIT - 64 * 1024:
            result = {'recordRef': row['recordRef'], 'kind': row['kind'], 'status': 'response-limit',
                      'text': None, 'textBytes': row.get('textBytes'), 'bodyBytes': size,
                      'calls': [], 'thinking': [], 'thinkingStatus': 'response-limit',
                      'thinkingBlocks': len(row.get('thinking', [])), 'partial': row['partial'],
                      'source': 'host', 'generation': row['generation']}
        return result

    def summary_updates(self, args, result):
        # Host summaries are projected atomically with the page. Native server seam remains.
        return result.get('source') == 'host'

    @reader_operation(lane=None)
    def target_info(self, result, state=None):
        ref = result['targetRef']
        if not self._has_live(ref):
            return None
        kind, (execution, generation) = self._get_target(ref)
        archived = kind == 'archive'
        state = state if state is not None else self._authority_state(execution, generation, archived)
        captured = result.get('targetInfo')
        if (result.get('rowSource') == 'host' and isinstance(captured, dict) and
                captured.get('targetRef') == ref and captured.get('fileEpoch') == result.get('fileEpoch')):
            # Handler's authority recheck consumes this page's frozen row witness. Re-reading
            # the row set here would read the cold page twice and mix two envelope watermarks.
            return {**captured, 'liveState': self._live_state(state), 'capabilities': self._capabilities(state)}
        stored = result.get('rowSource') == 'host' and self._rows_available(execution)
        idx = self._row_set(execution) if stored else None
        fields = self._target_fields(ref, execution, generation, state, archived)
        with self.maps_lock:
            leaf = self.handles.get(result.get('leafRef'))
        # StoredScope is operation-local and may fetch its last row. The retained raw
        # index instead needs the history lane, only AFTER every host metadata read.
        with nullcontext() if stored else self.history_lock:
            idx = idx if stored else self._index_live(execution)
            if result.get('fileEpoch') != idx.epoch:
                raise ReaderError(409, 'stale-epoch', 'host target epoch changed; reselect')
            rows = StoredScope(self, idx, 'main') if stored else [r for r in idx.rows if r['scope'] == 'main']
            boundary, revision = len(rows), idx.revision
            if leaf and leaf[0] == 'leaf' and leaf[1][:3] == (execution, idx.epoch, 'main') and leaf[1][3] <= len(rows):
                boundary, revision = leaf[1][3], leaf[1][4]
            leaf_uuid, status, epoch = canonical_leaf(rows[:boundary]), idx.status, idx.epoch
        return envelope(**{**fields,
            'sessionRef': result['sessionRef'], 'scopeRef': result['scopeRef'], 'leafRef': result['leafRef'],
            'fileEpoch': epoch, 'leafUuid': leaf_uuid, 'status': status,
            'contextIdentity': self._ref('context', (execution, generation, idx.epoch, boundary, revision))})

    def _target_fields(self, ref, execution, generation, state, archived):
        """A host target's session authority, from its registry state alone (no history field)."""
        cwd = state.get('cwd')
        cwd = cwd if isinstance(cwd, str) and len(cwd.encode()) <= 4096 else 'unknown'
        return dict(targetRef=ref, storeRef=self.store_ref,
            sessionRef=self._ref('session', (execution, generation)),
            scopeRef=self._ref('scope', (execution, generation, 'main')), scope='main',
            uuid=execution[3:], title=self._label(state, execution), cwd=cwd,
            source='host', role=state.get('role', 'unknown'), executionId=execution,
            generation=generation, state=state.get('state', 'unknown'), liveState=self._live_state(state),
            capabilities=self._capabilities(state), bindingRef=None,
            authority=self._authority(archived), archived=archived, lifecycle=self._lifecycle(state),
            successor=self._successor_for(state) if archived else None,
            reconnectBlocked=((self._reconnect_blockers(state) or [None])[0]
                              if state.get('role') == 'Manager' and self._ended(state) else None))

    # @beacon[
    #   id=opc-pane@ClaudeFacade.target_authority,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Control-lane selection authority from exact host state, independent of the history lock; a targetRef keeps its kind, sessionRef+scopeRef mints the freshly proved kind, and Refine's separate history pin stays untouched.,
    # ]
    @control_operation
    def target_authority(self, targetRef=None, sessionRef=None, scopeRef=None):
        """Control-lane host authority, without taking the history lock or rendering a page.

        The same exact execution, generation, role, capabilities and lifecycle that a history
        read's targetInfo carries, from one host `state` read; leafRef, fileEpoch, leafUuid and
        contextIdentity are null. Send and selection therefore never wait on a page of history,
        while Refine's frozen pin is still minted and checked by the history reads (context, and
        events with leafRef+fileEpoch). A handle the facade does not own is a native history
        target: its own history read validates it, as before."""
        if targetRef is not None and (sessionRef is not None or scopeRef is not None):
            raise ReaderError(400, 'schema', 'mixed addressing forms')
        if targetRef is None and (sessionRef is None or scopeRef is None):
            raise ReaderError(400, 'schema', 'targetRef, or sessionRef with scopeRef, required')
        if not self._has_live(targetRef if targetRef is not None else sessionRef):
            raise ReaderError(409, 'history-target', 'not a host session handle; its history read validates it')
        with self.control_lock:
            if targetRef is not None:
                kind, (execution, generation) = self._get_target(targetRef)
                archived = kind == 'archive'
                state = self._authority_state(execution, generation, archived)
            else:
                execution, generation = self._get_live(sessionRef, 'session')
                ex, gen, scope = self._get_live(scopeRef, 'scope')
                if (ex, gen) != (execution, generation):
                    raise ReaderError(409, 'ambiguous', 'host scope/session mismatch')
                if scope != 'main':
                    raise ReaderError(400, 'schema', 'only the main conversation can be targeted')
                state = self._state(execution, generation)
                archived = self._ended(state)
                targetRef = self._ref('archive' if archived else 'target', (execution, generation))
            info = envelope(**self._target_fields(targetRef, execution, generation, state, archived),
                            leafRef=None, fileEpoch=None, leafUuid=None, contextIdentity=None, status='authority')
            return envelope(targetRef=targetRef, sessionRef=info['sessionRef'], scopeRef=info['scopeRef'],
                            status='authority', source='host', role=info['role'], executionId=execution,
                            generation=generation, liveState=info['liveState'], targetInfo=info)

    def _manager_identity(self, targetRef, executionId, generation, *, recovery=False, mutation=False):
        if (not isinstance(targetRef, str) or not 24 <= len(targetRef) <= 64 or
                any(not (c.isascii() and (c.isalnum() or c in '_-')) for c in targetRef)):
            raise ReaderError(409, 'view-only', 'registered host Manager target required; history is view-only')
        if recovery and not self._has_live(targetRef):
            # A pane restart invalidates opaque view refs, not the independent host's receipts.
            # Read-only reconciliation may use exact registry identity; it never adopts a writer.
            if (not isinstance(executionId, str) or not executionId.startswith('cc-') or
                    not canonical_uuid(executionId[3:])):
                raise ReaderError(409, 'target-mismatch', 'exact registered execution required')
            execution, gen, archived = executionId, generation, False
        elif self._has_live(targetRef):
            kind, (execution, gen) = self._get_target(targetRef)
            archived = kind == 'archive'
        else:
            raise ReaderError(409, 'view-only', 'registered host Manager target required; history is view-only')
        if execution != executionId or type(generation) is not int or generation < 1:
            raise ReaderError(409, 'target-mismatch', 'exact registered execution and generation required')
        state = self._call('state', execution_id=execution)
        if (state.get('execution_id') != execution or state.get('native_uuid') != execution[3:]
                or not canonical_uuid(execution[3:]) or state.get('role') != 'Manager'):
            raise ReaderError(409, 'view-only', 'only explicitly host-registered Manager sessions permit this operation')
        current = state.get('generation')
        if (type(current) is not int or (not recovery and (gen != generation or current != generation))
                or (recovery and not 1 <= generation <= current)):
            raise ReaderError(409, 'generation', 'generation changed; reselect before mutation')
        if mutation and archived:
            # Archived authority is the ended session's read-only history: Refine and
            # revalidation only, never Send or control.
            raise ReaderError(409, 'exit', self._ended_message(state))
        capabilities = self._capabilities(state)
        if not recovery and not archived:
            # Live authority, not a captured leaf: the exact session must still be owned
            # and unexited, and mutations additionally need the current Manager capability.
            if self._ended(state):
                raise ReaderError(409, 'exit', self._ended_message(state))
            if mutation and not (capabilities['nativeInput'] and capabilities['controls']):
                raise ReaderError(409, 'authority', 'Manager native input authority is unavailable; reselect')
        return state, envelope(targetRef=targetRef, source='host', role='Manager',
            executionId=execution, generation=generation, currentGeneration=current,
            uuid=state['native_uuid'], capabilities=capabilities,
            authority=self._authority(archived), archived=archived, lifecycle=self._lifecycle(state))

    def _command(self, method, **params):
        try:
            return self._client_call(self.command_client, method, deadline=self._deadline, **params)
        except ReaderError:
            raise
        except (RuntimeError, ValueError) as error:
            raise ReaderError(409, 'refused', str(error)) from error
        except (OSError, AttributeError) as error:
            raise ReaderError(502, 'uncertain', 'host reply unavailable; query the same receipt, never resend') from error

    INCIDENTS_SHOWN = 20

    def _open_incidents(self, state):
        """The open escalations of this Manager, summarized for the panel (category, time, what happened)."""
        shown = []
        for incident in (state.get('pending_escalations') or [])[-self.INCIDENTS_SHOWN:]:
            if not isinstance(incident, str):
                continue
            key = (state.get('execution_id'), incident)
            with self.maps_lock:
                body = self.incident_cache.get(key)
            if body is None:
                try:
                    body = self._call('incident', execution_id=state.get('execution_id'), incident_id=incident)
                except ReaderError:
                    body = None  # a host that cannot answer still lists the id
                if isinstance(body, dict):
                    with self.maps_lock:
                        self.incident_cache[key] = body
                        while len(self.incident_cache) > 256:
                            self.incident_cache.popitem(last=False)
            body = body if isinstance(body, dict) else {}
            detail = body.get('detail') if isinstance(body.get('detail'), dict) else {}
            what = next((detail[k] for k in ('disclosure', 'terminal_error', 'message', 'reason', 'error')
                         if isinstance(detail.get(k), str) and detail[k].strip()), None)
            if what is None and detail:
                what = json.dumps(detail, ensure_ascii=False)
            shown.append({'incidentId': incident, 'category': body.get('category') or 'unknown',
                          'time': body.get('time') if type(body.get('time')) in (int, float) else None,
                          'summary': what[:300] if isinstance(what, str) else None})
        return shown

    # @beacon[
    #   id=opc-pane@ClaudeFacade.manager_operation,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Control-lane Send/state/control/receipts and incident disposition never wait for history; exact execution+generation and UUID/text receipt binding still bracket the one native intent, with nested validation sharing only this operation's observer.,
    # ]
    @control_operation
    def manager_operation(self, action, targetRef, executionId, generation, **fields):
        """Keep identity, receipt reconciliation and one intent together on the control lane."""
        recovery = action in ('receipt', 'control-receipt')
        state, result = self._manager_identity(targetRef, executionId, generation, recovery=recovery,
                                               mutation=action in ('send', 'control'))
        if action == 'state':
            return {**result, 'liveState': self._live_state(state), 'openIncidents': self._open_incidents(state)}
        if action == 'resolve-incident':
            # Dan's disposition of one open incident (an escalation addressed to him) through the
            # host's own resolve verb: recorded on the incident, never a Send or a native control.
            incident, disposition = fields['incidentId'], fields['disposition']
            if incident not in (state.get('pending_escalations') or []):
                raise ReaderError(409, 'refused', 'that incident is no longer open for this Manager')
            if not disposition.strip() or len(disposition) > 2000:
                raise ReaderError(400, 'schema', 'a nonempty disposition of at most 2000 characters is required')
            body = self._command('resolve_incident', execution_id=executionId, incident_id=incident,
                                 disposition=disposition.strip())
            with self.maps_lock:
                self.incident_cache.pop((executionId, incident), None)
            return {**result, 'status': 'resolved', 'incidentId': incident,
                    'disposition': body.get('disposition') if isinstance(body, dict) else None}
        if action == 'send':
            mid = fields['messageUuid']
            if not canonical_uuid(mid) or not isinstance(fields['original'], str) or not isinstance(fields['submitted'], str) or not fields['submitted']:
                raise ReaderError(400, 'schema', 'canonical message UUID and exact original/nonempty submitted text required')
            value = self._call('receipt', execution_id=executionId, message_uuid=mid)
            if value is not None:
                if value.get('original') != fields['original'] or value.get('submitted') != fields['submitted']:
                    raise ReaderError(409, 'refused', 'message UUID already bound to different original/submission')
            else:
                self.validate_target(targetRef)
                # A steer goes to the busy Manager now: Claude Code queues it and hands it to the
                # model at its next tool step. Same receipt journal, never a resend.
                method = 'steer' if fields.get('steer') is True else 'send'
                try:
                    value = self._command(method, execution_id=executionId, generation=generation,
                        mid=mid, original=fields['original'], submitted=fields['submitted'])
                except ReaderError as error:
                    if method == 'steer' and 'unknown method' in str(error):
                        raise ReaderError(409, 'refused', 'this Claude host predates Send while the Manager '
                            'is busy (its guarded restart enables it); nothing was sent') from error
                    raise
        elif action == 'receipt':
            mid = fields['messageUuid']
            if not canonical_uuid(mid):
                raise ReaderError(400, 'schema', 'canonical message UUID required')
            value = self._call('receipt', execution_id=executionId, message_uuid=mid)
        elif action == 'control':
            rid = fields['requestId']
            if not canonical_uuid(rid):
                raise ReaderError(400, 'schema', 'canonical request UUID required')
            self.validate_target(targetRef)
            operation = fields['operation']
            if operation == 'get_settings':
                # Read-only native readback (allowed busy): a matching applied model/effort clears
                # a stale settings gate; nothing is set, sent or repeated.
                value = self._command('get_settings', execution_id=executionId, generation=generation)
                return {**result, 'settingsReadback': {k: value.get(k) for k in (
                    'state', 'applied', 'model', 'effort', 'blocks_send', 'disclosure', 'gate', 'effective')
                    if isinstance(value, dict) and k in value}}
            params = ({'model': fields['model']} if operation == 'set_model' else
                      {'effort': fields['effort']} if operation == 'apply_effort' else
                      {'reason': fields.get('reason'), 'cancel_queued': True})
            value = self._command('begin_control', execution_id=executionId, generation=generation,
                request_id=rid, operation=operation, authority='explicit pane Manager control', **params)
        elif action == 'control-receipt':
            rid = fields['requestId']
            if not canonical_uuid(rid):
                raise ReaderError(400, 'schema', 'canonical request UUID required')
            value = self._call('control_receipt', execution_id=executionId, request_id=rid)
        else:
            raise ReaderError(400, 'schema', 'unknown Manager operation')
        if value is not None and (value.get('generation') != generation or
                (action in ('send', 'receipt') and value.get('execution_id') != executionId)):
            raise ReaderError(502 if action in ('send', 'control') else 409, 'receipt-mismatch',
                'receipt does not match exact execution/generation; query only, never resend')
        if action in ('send', 'receipt'):
            if value is not None:
                value = {**value, 'accepted': value.get('durable') is True,
                         'uncertain': value.get('status') == 'uncertain' or
                         (not value.get('completed') and bool(state.get('infrastructure_error') or
                          state.get('state') == 'ownership_unknown'))}
            return {**result, 'status': 'accepted' if action == 'send' else 'receipt', 'receipt': value}
        return {**result, 'controlReceipt': value}

    # @beacon[
    #   id=opc-pane@ClaudeFacade.reconnect,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=One control intent reconnects an ended registered Manager by exact execution and expected old generation; only its own per-call observer budget is extended, ownership stays proved settled and at most one replacement precedes the pane's new Send.,
    # ]
    @control_operation
    def reconnect(self, targetRef, executionId, generation):
        """Reconnect on the control lane with only this operation's extended observer budget.

        The pane's Send path calls this first, then rebinds to the returned generation
        and delivers one new journaled Send. Identity is exact (execution + expected old
        generation, passed through to the host unchanged); the host reuses a live
        generation for that expectation and launches at most one replacement, only when
        the old owner is proved gone and its lock is free — a stale expectation whose
        transport is already ended refuses instead of resuming behind the pane's back.
        Ownership uncertainty, other roles and unregistered history all refuse;
        reconnect is never a Send and never repeats an uncertain one."""
        if (not isinstance(targetRef, str) or not 24 <= len(targetRef) <= 64 or
                any(not (c.isascii() and (c.isalnum() or c in '_-')) for c in targetRef)):
            raise ReaderError(409, 'view-only', 'registered host Manager target required; history is view-only')
        if not self._has_live(targetRef):
            raise ReaderError(409, 'stale', 'host handle expired; reselect before reconnect')
        kind, (execution, expected) = self._get_target(targetRef)
        if execution != executionId or type(generation) is not int or generation < 1 or expected != generation:
            raise ReaderError(409, 'target-mismatch', 'exact registered execution and expected generation required')
        # One reconnect may run the host's launch preflight before it answers: lengthen
        # only this reader's observation window, never a host deadline.
        deadline = getattr(self, '_deadline', None)
        if deadline is not None:
            self._deadline = deadline = max(deadline, time.monotonic() + RECONNECT_OBSERVER_BUDGET)
        state = self._call('state', execution_id=execution)
        if (state.get('execution_id') != execution or state.get('native_uuid') != execution[3:]
                or not canonical_uuid(execution[3:]) or state.get('role') != 'Manager'):
            raise ReaderError(409, 'view-only', 'only explicitly host-registered Manager sessions reconnect')
        current = state.get('generation')
        if type(current) is not int or current < generation:
            raise ReaderError(409, 'generation', 'generation changed; reselect before reconnect')
        if self._ended(state):
            blocked = self._reconnect_blockers(state)
            if blocked:
                unproved = state.get('state') == 'ownership_unknown' or state.get('owner') in ('unknown', 'draining')
                raise ReaderError(409, 'ownership' if unproved else 'refused', blocked[0])
        elif not self._ownership_settled(state):
            raise ReaderError(409, 'ownership', 'ownership is not proved settled; reconnect withheld')
        try:
            # The pane's ORIGINAL expected generation is what the host decides on: a live
            # newer owner may be reused for it, while a stale generation whose transport is
            # ended refuses rather than resuming a conversation the pane no longer knows.
            record = self._client_call(self.command_client, 'reconnect', deadline=deadline,
                                       execution_id=execution, generation=generation)
        except ReaderError:
            raise
        except (RuntimeError, ValueError) as error:
            raise ReaderError(409, 'refused', str(error)) from error
        except (OSError, AttributeError) as error:
            # A lost reconnect reply is not an uncertain Send: reconnect is idempotent.
            raise ReaderError(502, 'reply-lost',
                'host reconnect reply unavailable; reconnect is idempotent — re-read state or Send again') from error
        if (not isinstance(record, dict) or record.get('execution_id') != execution
                or record.get('native_uuid') != execution[3:] or record.get('role') != 'Manager'):
            raise ReaderError(502, 'reply-mismatch', 'host reconnect answered a different session; re-read state')
        current = record.get('generation')
        if type(current) is not int or current < generation:
            raise ReaderError(502, 'reply-mismatch', 'host reconnect answered an impossible generation; re-read state')
        return envelope(targetRef=self._ref('archive' if self._ended(record) else 'target', (execution, current)),
            executionId=execution, previousGeneration=generation, generation=current,
            uuid=record['native_uuid'], role='Manager', state=record.get('state', 'unknown'),
            liveState=self._live_state(record), lifecycle=self._lifecycle(record),
            capabilities=self._capabilities(record))

    @classmethod
    def _ownership_settled(cls, record):
        """The old owner question is answered and no storage/protocol incident withholds it."""
        return (record.get('state') != 'ownership_unknown' and record.get('owner') not in ('unknown', 'draining')
                and not record.get('infrastructure_error') and not record.get('incident_unpersisted'))

    @control_operation
    def validate_target(self, targetRef):
        if not self._has_live(targetRef):
            return self.history.validate_target(targetRef)
        with self.control_lock:
            # Authority only: exact execution/generation, live owner (or the ended session's
            # archived read-only authority), no leaf/path freeze. Refine's frozen history is
            # validated by its own context read and pinned path.
            kind, (execution, generation) = self._get_target(targetRef)
            archived = kind == 'archive'
            state = self._authority_state(execution, generation, archived)
            # This is session authority, not a path read: use the observed epoch without
            # probing rows (or draining history) on a Send/control validation path.
            idx = self._cached_index(execution) or self._index_live(execution)
            return envelope(targetRef=targetRef, fileEpoch=idx.epoch, indexRevision=idx.revision,
                            executionId=execution, generation=generation, status='valid',
                            authority=self._authority(archived), archived=archived,
                            lifecycle=self._lifecycle(state),
                            capabilities=self._capabilities(state))

    # @beacon[
    #   id=opc-pane@ClaudeFacade.context,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=History-lane exact-generation Refine with operation-local raw batches, whole or budgeted prose and Read Aloud's before cut; frozen witnesses still tolerate live growth but refuse a rewrite, independently of Manager Send authority.,
    # ]
    @reader_operation
    def context(self, targetRef, targetRevision, budgetBytes=50 * 1024, historyMode='budgeted', before=None):
        """Freeze whole/budgeted prose on the history lane, batching eligible witnesses after Read Aloud's cut."""
        mode = check_history_mode(historyMode)
        if not self._has_live(targetRef):
            return self.history.context(targetRef, targetRevision, budgetBytes, mode, before=before)
        if ((mode == 'budgeted' and (type(budgetBytes) is not int or not 5 * 1024 <= budgetBytes <= 400 * 1024))
                or type(targetRevision) is not int or not 0 <= targetRevision <= 2**53 - 1):
            raise ReaderError(400, 'schema', 'invalid context revision/budget')
        with self.history_lock:
            kind, (execution, generation) = self._get_target(targetRef)
            archived = kind == 'archive'
            self._authority_state(execution, generation, archived)
            stored = self._rows_available(execution)
            idx = self._row_set(execution) if stored else self._index_live(execution)
            if not stored:
                self._settle(idx)
            if idx.status != 'complete':
                raise ReaderError(409, 'incomplete-context', 'host context requires a complete replay')
            # Freeze this read's own main path. The provider sees exactly this history:
            # growth beyond it stays out, a live turn moving on while it is read is fine
            # (_frozen_changes), and only a message it read being rewritten refuses.
            rows = StoredScope(self, idx, 'main') if stored else [r for r in idx.rows if r['scope'] == 'main']
            diagnostics = self._rows_diagnostics(idx) if stored else idx.diagnostics
            boundary, revision = len(rows), idx.revision
            history, exclusions, cache, sent = HistoryBlocks(mode, budgetBytes), [], {}, {}
            # A replay diagnostic (an unknown block, an overlapping stream, a skipped event)
            # excludes the rows its frame touched and is listed, never the whole conversation.
            damaged = {d['cursor']: d['status'] for d in diagnostics if d['status'] != RESULT_RECEIPT_DIAGNOSTIC}
            touched = set()
            found = before is None
            def earlier():
                nonlocal found
                for row in reversed(rows):
                    if not found:  # Read Aloud: cut before any raw-body prefetch
                        found = row['uuid'] == before
                        continue
                    yield row
            eligible = lambda r: r['genuine'] and r['status'] == 'stored' and r['prose_bytes'] <= BODY_LIMIT and not (
                damaged and any(c in damaged for c in self._row_frames(r)))
            for row in self._prefetched(earlier(), cache, eligible=eligible):
                hit = next((damaged[c] for c in self._row_frames(row) if c in damaged), None) if damaged else None
                if hit is not None:
                    touched.update(c for c in self._row_frames(row) if c in damaged)
                    exclusions.append({'uuid': row['uuid'], 'reason': 'replay diagnostic: ' + str(hit)[:160]})
                    continue
                if not row['genuine'] or row['status'] != 'stored':
                    exclusions.append({'uuid': row['uuid'], 'reason': 'partial/internal/recovery/tool/unknown'})
                    continue
                if row['prose_bytes'] > BODY_LIMIT:
                    exclusions.append({'uuid': row['uuid'], 'reason': 'body-limit'})
                    continue
                prose = self._prose(row, cache)
                if not prose or len(prose.encode()) > BODY_LIMIT:
                    continue
                label = '[USER]' if row['kind'] == 'user' else '[ASSISTANT]'
                status = history.add(row['uuid'] or row['origin'], label, prose)
                if status in ('fits', 'truncated'):
                    sent[row['origin']] = {(len(prose), hashlib.sha256(prose.encode()).hexdigest())}
                if status != 'fits':
                    if status == 'truncated':
                        history.users += row['kind'] == 'user'
                    break
                history.users += row['kind'] == 'user'
            if not found:
                raise ReaderError(409, 'missing', "that message is not on this conversation's main path")
            # Diagnostics no context row carries (an event skipped whole) are listed first.
            exclusions[:0] = [{'uuid': None, 'reason': 'replay diagnostic, not in context: ' + str(status)[:160]}
                              for cursor, status in damaged.items() if cursor not in touched][:20]
            self._authority_state(execution, generation, archived)
            changed = self._rows_changes(idx, 'main', revision) if stored else rows
            changed = self._prefetched(changed, cache, eligible=lambda r:
                r['body_changed'] > revision and (not stored or r['seq'] < boundary) and bool(sent.get(r['origin'])))
            self._frozen_changes(changed, stored, boundary, revision, sent, cache)
            result, boundaries = history.finish()
            frozen = (execution, idx.epoch, 'main', boundary, revision)
            self._remember_context(frozen, sent)
            leaf = self._ref('leaf', frozen)
            return envelope(targetRef=targetRef, targetRevision=targetRevision, storeRef=self.store_ref,
                sessionRef=self._ref('session', (execution, generation)), scopeRef=self._ref('scope', (execution, generation, 'main')),
                leafRef=leaf, fileEpoch=idx.epoch, leafUuid=canonical_leaf(rows),
                indexRevision=idx.revision, executionId=execution, generation=generation, source='host',
                rowSource='host' if stored else 'fallback',
                authority=self._authority(archived), archived=archived,
                contextIdentity=self._ref('context', (execution, generation, idx.epoch, boundary, revision)),
                text=result, bytes=len(result.encode()), turns=len(boundaries), users=history.users,
                historyMode=mode, historyBoundaries=boundaries,
                readAt=int(time.time() * 1000), exclusions=exclusions[:100], status='stored')

    def _call_row(self, callRef, fileEpoch):
        """Try the rows origin before a mode refusal, so failed inspection can retain replay's call."""
        execution, epoch, origin, generation, scope, number, callid, name = self._get_live(callRef, 'call')
        if fileEpoch != epoch:
            raise ReaderError(409, 'stale-epoch', 'host call epoch mismatch')
        stored = self._rows_available(execution)
        idx = self._row_set(execution) if stored else self._index_live(execution, epoch)
        row = self._row_origin(idx, origin) if stored else None
        if idx.epoch != epoch:
            raise ReaderError(409, 'stale-epoch', 'host call mode/epoch changed; reselect')
        if not stored:
            row = idx.by_origin.get(origin)
        matches = [(row, c) for c in row['calls'] if (c['block'], c['id'], c['name']) == (number, callid, name)] if row and row['generation'] == generation and row['scope'] == scope else []
        if len(matches) != 1:
            raise ReaderError(409, 'ambiguous', 'host call witness unavailable')
        return idx, matches[0][0], matches[0][1]

    @reader_operation
    def witness(self, callRef, fileEpoch):
        if not self._has_live(callRef):
            return None
        idx, row, call = self._call_row(callRef, fileEpoch)
        # Reopen immutable origin, not a mutable final hash in mailbox identity.
        text, _ = self._validated_raw(row['origin'], row['origin_hash'], {})
        leaf = self._ref('call-leaf', (idx.execution, idx.epoch, row['origin'], row['generation'], row['scope']))
        identity = {'storeRef': self.store_ref, 'sessionRef': self._ref('session', (idx.execution, row['generation'])),
            'scopeRef': self._ref('scope', (idx.execution, row['generation'], row['scope'])),
            'leafRef': leaf, 'leafUuid': row['origin_uuid'], 'recordUuid': row['origin_uuid'],
            'recordRef': self._record_ref(idx, row), 'sessionUuid': idx.execution[3:],
            'scope': row['scope'],
            'recordHash': digest(text.encode()), 'blockIndex': call['block'], 'callId': call['id'],
            'callRef': callRef, 'fileEpoch': fileEpoch}
        return envelope(identity=identity, callRef=callRef, fileEpoch=fileEpoch,
                        rowSource='host' if isinstance(idx, RowsIndex) else 'fallback',
                        toolName=call['name'], source='host', sessionTitle=idx.execution,
                        workspaceName='host-owned', provenance='host-durable-feed')

    @reader_operation
    def snapshot(self, callRef, fileEpoch):
        """Read one selected call's exact input/output through the existing parser, with optional frame hints."""
        if not self._has_live(callRef):
            return self.history.snapshot(callRef, fileEpoch)
        with self.history_lock:
            idx, row, call = self._call_row(callRef, fileEpoch)
            if not isinstance(idx, RowsIndex):
                self._refresh(idx)
                idx, row, call = self._call_row(callRef, fileEpoch)
            cache = {}
            self._prefetch(self._row_frames(row, call=call), cache)
            input_value = None
            if call['input_path'] == 'stream':
                pieces = []
                for cursor in call['partial']:
                    _, root = self._validated_raw(cursor, call['partial_hashes'][cursor], cache)
                    pieces.append(scalar(root.members()['event'].members()['delta'].members(), 'partial_json', 'string'))
                    if sum(len(p.encode()) for p in pieces) > BODY_LIMIT:
                        raise ReaderError(413, 'body-limit', 'stream input exceeds 8 MiB; not cropped')
                inp = ''.join(pieces) if pieces else None
            else:
                text, root = self._validated_raw(call['frame'], call.get('frame_hash', row['frame_hash']), cache)
                block = root.members()['message'].members()['content'].children[call.get('source_block', call['block'])].members()
                inp = projection(text, block.get('input'))
                input_value = recorded_value(text, block.get('input'))
            status, output = join_status(call)
            out, result_time, diff_metadata = None, None, None
            if output:
                text, root = self._validated_raw(output['frame'], output['frame_hash'], cache)
                diff_metadata = recorded_value(text, root.members().get('tool_use_result'))
                block = root.members()['message'].members()['content'].children[output['block']].members()
                out = result_projection(text, block.get('content'))
                result_time = self._result_time(idx, output)
            counts = [len(v.encode()) if v is not None else None for v in (inp, out)]
            if sum(v or 0 for v in counts) > BODY_LIMIT:
                raise ReaderError(413, 'body-limit', 'combined stored host snapshot exceeds 8 MiB; not cropped')
            state = self._call('state', execution_id=idx.execution)
            blocked = (state.get('generation') != row['generation'] or state.get('owner') in ('gone', 'unknown') or
                       state.get('state') in ('ownership_unknown', 'infrastructure_error') or
                       bool(state.get('infrastructure_error')))
            return envelope(callRef=callRef, fileEpoch=fileEpoch, input=inp, output=out,
                diff=claude_diff(call['name'], input_value, diff_metadata, status),
                rowSource='host' if isinstance(idx, RowsIndex) else 'fallback',
                inputBytes=counts[0], outputBytes=counts[1], status=status,
                inputAvailability='stored' if inp is not None else 'missing',
                outputAvailability='stored' if out is not None else status, caption='stored', artifactRef=None,
                inputFinal=call['input_path'] == 'final', live=True,
                terminal=status in ('stored', 'error', 'ambiguous') or blocked,
                liveState=self._live_state(state), time=row.get('time'), resultTime=result_time,
                provenance='host-durable-feed', outputProvenance='native model-facing tool_result; diagnostic payload is not output')

    def _result_time(self, idx, output, reuse=False):
        """The time of the row a tool result arrived in (display only: how long the call took); unknown → None."""
        try:
            row = self._row_origin(idx, output['frame'], reuse=reuse) if isinstance(idx, RowsIndex) else idx.by_origin.get(output['frame'])
        except (ReaderError, RowsUnavailable, KeyError, TypeError):
            return None
        stamp = row.get('time') if isinstance(row, dict) else None
        return stamp if type(stamp) in (int, float) else None

    @reader_operation
    def artifact(self, artifactRef, callRef, fileEpoch):
        if self._has_live(callRef):
            raise ReaderError(404, 'missing', 'host offload stub is the recorded result; no verified artifact reference')
        return self.history.artifact(artifactRef, callRef, fileEpoch)


def canonical_leaf(rows):
    from claude_reader import UUID
    uid = rows[-1]['uuid'] if rows else None
    return uid if isinstance(uid, str) and UUID.fullmatch(uid) else None
