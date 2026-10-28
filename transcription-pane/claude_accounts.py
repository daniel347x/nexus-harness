"""Pane-side Claude account switching: one fixed-label control over one durable switch operation.

The selector is **not** the pane's. ``scripts/oc_claude_accounts.py`` (through
``bash scripts/oc.sh claude_accounts status --json``) is the single authority for which label is
active, which native login folder each label uses, and the selector's monotonic ``epoch``. This
module only reads that answer and starts the guarded restart that applies another label:

* ``status`` reads the CLI status, forwards labels, the public identity (email/plan) and the epoch,
  and never a login folder path or a secret;
* ``switch`` spawns the **existing** queued guarded restart
  (``scripts/oc_claude_restart.py --_worker``) with ``--account <label> --close-idle`` and, when
   a registered Manager is observed, ``--resume <uuid>`` for the newest Manager (even halted/ended), so the same conversation continues
  under the new login. The wrapper retries every 15 s while busy work refuses the restart, so
  "Dan tapped the button" never has to be repeated;
* ``cancel`` atomically cancels the waiting worker through its durable restart gate; after the
  first old-process close/stop begins cancellation is refused, never decided from log timing;
* the one operation is recorded on disk (owner-only JSON under
  ``~/.local/state/opencode-companion/transcription-pane/``). A lost browser reply, a second tab
  or a pane-server restart observes the **same** operation instead of starting another.

Phases project the engine's canonical claude-restart/1 receipt, never log/exit/label-only
success. Selection, ready service, Manager and notice are distinct. Starting/recovering/unknown
remain reconcilable after reload; restored means failed switch with verified old service.
GET never advances recovery. The already-selected zero-spawn shortcut checks no service.

**CLI contract.** ``oc.sh claude_accounts status --json`` must print one JSON object on stdout:

    {"selected": {"label": "personal", "epoch": 4,
                  "identity": {"email": "dan@example", "plan": "max"}},
     "accounts": [{"label": "personal", "enrolled": true, "identity": {...}},
                  {"label": "work", "enrolled": true, "identity": {...}}]}

``epoch`` is a monotonically increasing integer that changes whenever the selector changes.
Missing/alias fields are tolerated (``active``/``current`` for ``selected``, ``name`` for
``label``), but a status without a determinable selected label is reported unavailable rather than
guessed. Only ``label``, ``epoch``, ``enrolled`` and the identity's ``email``/``plan``/``loggedIn``
reach a browser.

**Restart contract.** ``scripts/claude_headless/restart.py`` accepts ``--account <label>`` (the
lead's piece); ``scripts/oc_claude_restart.py`` passes unknown arguments through to
``claude_restart.sh`` unchanged. The user-facing command remains
``bash scripts/oc.sh claude_restart --account work --close-idle --resume <uuid>``. This module runs
the wrapper's own worker entry point so it owns the log and the exit code; if the wrapper's flags
change, ``_spawn`` is the one place to adjust.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

from claude_reader import ReaderError

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SCRIPTS = REPO / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from claude_headless.restart import RestartControl  # noqa: E402
STATUS_SCRIPT = SCRIPTS / 'oc_claude_accounts.py'
RESTART_WRAPPER = SCRIPTS / 'oc_claude_restart.py'
STATE_PATH = Path.home() / '.local/state/opencode-companion/transcription-pane/claude-account-switch.json'
LOG_PATH = Path.home() / '.local/state/opencode-companion/transcription-pane/claude-account-switch.log'

# The two labels the Manager App offers. The registry may know more; an unknown label is refused
# before any process is started, and an unenrolled one is refused with the enroll hint.
LABELS = ('personal', 'work')
SCHEMA_VERSION = 1
BACKEND = 'claude-accounts'
REQUEST_ID = re.compile(r'[A-Za-z0-9_-]{8,64}')
ATTEMPT = re.compile(r'^=== attempt [0-9]+ [^\n]* exit (-?[0-9]+)\s*$', re.M)
PHASES = ('waiting', 'switching', 'starting', 'recovering', 'active', 'restored', 'recovery_required',
          'refused', 'failed', 'unknown', 'cancelled')
TERMINAL = ('active', 'restored', 'recovery_required', 'refused', 'failed', 'cancelled')
# The wrapper's own retry window and cadence (oc_claude_restart.py defaults): an hour of waiting
# costs nothing, and a stuck switch lands as a plain "still busy" answer.
RESTART_TIMEOUT = 3600
RESTART_INTERVAL = 15
STATUS_TTL = 20.0        # an ordinary accounts read is reused this long
PENDING_TTL = 3.0        # ... and this long while a switch is being watched
STATUS_TIMEOUT = 20.0
LOG_TAIL = 64 * 1024


class AccountError(ReaderError):
    """A refused account read/switch, with the HTTP code the pane route answers."""


def _public_identity(value):
    """The identity fields a browser may see: who is signed in, never a token or a folder path."""
    if isinstance(value, str):
        # An opaque hash is not an identity to show; only an address-like string is.
        return {'email': value[:200]} if '@' in value[:200] else {}
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in ('email', 'account'):
        if isinstance(value.get(key), str) and 0 < len(value[key]) <= 200:
            out['email'] = value[key]
            break
    for key in ('plan', 'subscriptionType', 'subscription_type', 'planType'):
        if isinstance(value.get(key), str) and 0 < len(value[key]) <= 64:
            out['plan'] = value[key]
            break
    if isinstance(value.get('loggedIn'), bool):
        out['loggedIn'] = value['loggedIn']
    return out


def _label(value):
    if isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_-]{1,63}', value):
        return value
    return None


def _label_of(item):
    if isinstance(item, str):
        return _label(item)
    if not isinstance(item, dict):
        return None
    for key in ('label', 'name', 'account'):
        found = _label(item.get(key))
        if found:
            return found
    return None


def _epoch_of(value):
    if type(value) is int and 0 <= value <= 2 ** 53 - 1:
        return value
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,16}', value):
        return int(value)
    return None


# @beacon[
#   id=opc-pane@public_status,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Normalize the CLI's account status for a browser: labels, the active flag, the epoch and the identity's email/plan only; every login-folder path, folder hash and token is dropped.,
# ]
def public_status(payload):
    """Normalize the CLI's status into the pane's browser-safe reading, or None if unusable."""
    if not isinstance(payload, dict):
        return None
    selected = payload.get('selected') or payload.get('active') or payload.get('current') or {}
    if isinstance(selected, str):
        selected = {'label': selected}
    if not isinstance(selected, dict):
        selected = {}
    source = payload.get('accounts') or payload.get('labels') or []
    if isinstance(source, dict):
        source = [dict(value if isinstance(value, dict) else {}, label=key) for key, value in source.items()]
    if not isinstance(source, list):
        source = []
    accounts = []
    for item in source:
        label = _label_of(item)
        if label is None or any(row['label'] == label for row in accounts):
            continue
        fields = item if isinstance(item, dict) else {}
        auth = fields.get('auth') if isinstance(fields.get('auth'), dict) else {}
        details = {key: value for key, value in fields.items() if key != 'identity'}
        details.update({key: value for key, value in auth.items()
                        if key in ('email', 'subscriptionType', 'loggedIn')})
        identity = _public_identity(fields.get('identity'))
        if not identity:
            identity = _public_identity(details)
        enrolled = fields.get('enrolled')
        if enrolled is None and isinstance(auth.get('loggedIn'), bool):
            enrolled = auth['loggedIn']
        accounts.append({'label': label, 'enrolled': True if enrolled is None else bool(enrolled),
                         'identity': identity, 'active': fields.get('active') is True})
    label = _label_of(selected)
    if label is None:
        label = next((row['label'] for row in accounts if row['active']), None)
    if label is None:
        return None
    if label not in {row['label'] for row in accounts}:
        # The selector names a label the registry did not list: report it, never hide it.
        accounts.append({'label': label, 'enrolled': True,
                         'identity': _public_identity(selected.get('identity')), 'active': True})
    epoch = _epoch_of(selected.get('epoch'))
    if epoch is None:
        epoch = _epoch_of(payload.get('epoch'))
    identity = _public_identity(selected.get('identity')) or _public_identity(selected)
    matching = next((row for row in accounts if row['label'] == label), None)
    if matching and matching['identity']:
        identity = {**matching['identity'], **identity}   # the folder's plan fills in for the selector's email
    for row in accounts:
        row['active'] = row['label'] == label
        if row['active'] and not row['identity']:
            row['identity'] = dict(identity)
    accounts.sort(key=lambda row: (LABELS.index(row['label']) if row['label'] in LABELS else len(LABELS),
                                   row['label']))
    return {'selected': {'label': label, 'epoch': epoch, 'identity': identity}, 'accounts': accounts}


def _proc_start(pid):
    """The process start-time witness from /proc, or None when the process is gone/unreadable."""
    try:
        raw = Path(f'/proc/{int(pid)}/stat').read_text(encoding='utf-8', errors='replace')
    except (OSError, ValueError, TypeError):
        return None
    try:
        return raw.rsplit(')', 1)[1].split()[19]
    except IndexError:
        return None


def _alive(pid, witness):
    return bool(pid and witness) and _proc_start(pid) == witness


# @beacon[
#   id=opc-pane@AccountSwitch,
#   slice_labels=opc-pane,opc-claude-restart,
#   kind=ast,
#   comment=Query-only canonical operation projection with exact live-worker waiting and durable cancelled/stopping gate facts; target-active needs ready service and restoration/Manager/notice remain distinct. Switch busy and Cancel share this projection and existing worker/gate.,
# ]
class AccountSwitch:
    """The one durable Claude account switch and its atomic waiting-only cancellation."""

    def __init__(self, *, state_path=STATE_PATH, log_path=LOG_PATH, run_status=None, spawn=None,
                 clock=time.time, alive=_alive, reader=None, timeout=RESTART_TIMEOUT,
                 interval=RESTART_INTERVAL, read_operation=None, state_root=None):
        self.state_path = Path(state_path)
        self.log_path = Path(log_path)
        self.run_status = run_status or self._run_status
        self.spawn = spawn or self._spawn
        self.clock = clock
        self.alive = alive
        self.reader = reader
        self.timeout, self.interval = timeout, interval
        self.lock = threading.RLock()
        self.procs = {}
        self._snapshot_cache, self._snapshot_at = None, None
        self.state_root = Path(state_root) if state_root else Path.home() / '.local/state/opencode-companion/claude-headless'
        self.read_operation = read_operation or self._read_lifecycle

    # ---- the CLI's answer
    def _run_status(self):
        if not STATUS_SCRIPT.is_file():
            raise AccountError(503, 'unavailable', 'the claude_accounts command is not installed in this checkout')
        try:
            result = subprocess.run(['bash', str(SCRIPTS / 'oc.sh'), 'claude_accounts', 'status', '--json'],
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=STATUS_TIMEOUT, cwd=str(REPO))
        except (OSError, subprocess.SubprocessError) as error:
            raise AccountError(503, 'unavailable',
                               f'the account status could not be read: {type(error).__name__}') from None
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip().splitlines()
            raise AccountError(503, 'unavailable',
                               'the account status failed: ' + (detail[0][:300] if detail else f'exit {result.returncode}'))
        try:
            return json.loads(result.stdout)
        except ValueError:
            start = result.stdout.find('{')   # a shell banner before the JSON is not an answer
            if start > 0:
                try:
                    return json.loads(result.stdout[start:])
                except ValueError:
                    pass
            raise AccountError(503, 'unavailable', 'the account status did not print JSON') from None

    def _snapshot(self, force=False, pending=False):
        now = self.clock()
        with self.lock:
            ttl = PENDING_TTL if pending else STATUS_TTL
            if not force and self._snapshot_cache is not None and self._snapshot_at is not None and now - self._snapshot_at < ttl:
                return {'snapshot': self._snapshot_cache, 'error': None}
            try:
                snapshot, error = public_status(self.run_status()), None
                if snapshot is None:
                    raise AccountError(503, 'unavailable', 'the account status did not name the selected account')
            except AccountError as failure:
                snapshot, error = None, str(failure)
            except Exception as error_:  # noqa: BLE001 — an unreadable CLI is an unavailable selector, not a crash
                snapshot, error = None, f'{type(error_).__name__}: {error_}'
            self._snapshot_cache, self._snapshot_at = snapshot, now
        return {'snapshot': snapshot, 'error': error}

    def status(self, force=False):
        operation = self.operation()['operation']
        pending = bool(operation and operation.get('phase') not in TERMINAL)
        reading = self._snapshot(force=force, pending=pending)
        snapshot = reading['snapshot']
        return {
            'schemaVersion': SCHEMA_VERSION, 'backend': BACKEND,
            'status': 'ready' if snapshot else 'unavailable',
            'installed': STATUS_SCRIPT.is_file(),
            'available': snapshot is not None,
            'now': self.clock(),
            'labels': list(LABELS),
            'selected': snapshot['selected'] if snapshot else None,
            'accounts': snapshot['accounts'] if snapshot else [{'label': label, 'enrolled': None, 'identity': {},
                                                                'active': False} for label in LABELS],
            'operation': operation,
            'error': reading['error'],
        }

    # ---- the one operation
    def operation(self, operation_id=None):
        with self.lock:
            record = self._read_record(operation_id)
            if record is None:
                if operation_id is not None:
                    raise AccountError(404, 'notfound', 'no such switch operation; read the account status again')
                return {'schemaVersion': SCHEMA_VERSION, 'backend': BACKEND, 'status': 'none', 'operation': None}
            return {'schemaVersion': SCHEMA_VERSION, 'backend': BACKEND, 'status': 'operation',
                    'operation': self._public_operation(record)}

    def switch(self, account, operation_id, reader=None):
        if account not in LABELS:
            raise AccountError(400, 'schema', 'the account label must be personal or work')
        if not isinstance(operation_id, str) or not REQUEST_ID.fullmatch(operation_id):
            raise AccountError(400, 'schema', 'operationId must be 8-64 letters, digits, _ or -')
        with self.lock:
            prior = self._read_record(operation_id)
            if prior is not None:
                if prior['account'] != account:
                    raise AccountError(409, 'identity', 'that operationId already names another account')
                return {'schemaVersion': SCHEMA_VERSION, 'backend': BACKEND,
                        'operation': self._public_operation(prior)}, 200
            record = self._read_record()
            if record is not None:
                current = self._public_operation(record)
                if record.get('id') == operation_id:
                    return {'schemaVersion': SCHEMA_VERSION, 'backend': BACKEND, 'operation': current}, 200
                if current['phase'] not in TERMINAL:
                    raise AccountError(409, 'busy', f"a switch to {current['account']} is already in progress")
            reading = self._snapshot(force=True)
            snapshot = reading['snapshot']
            if snapshot is None:
                raise AccountError(503, 'unavailable', reading['error'] or 'the account status is unavailable')
            selected = snapshot['selected']['label']
            known = {row['label']: row for row in snapshot['accounts']}
            if account not in known:
                raise AccountError(409, 'unenrolled',
                                   f"'{account}' is not enrolled yet: run oc.sh claude_accounts enroll {account}")
            if known[account].get('enrolled') is False:
                raise AccountError(409, 'unenrolled', f"'{account}' is enrolled but not signed in")
            now = self.clock()
            if selected == account:
                done = {'id': operation_id, 'account': account, 'fromAccount': selected, 'phase': 'active',
                        'schemaVersion': SCHEMA_VERSION,
                        'alreadyActive': True, 'detail': f'{account} is already selected; service not checked',
                        'service': {'state': 'unchecked'}, 'manager': {'state': 'not_requested'},
                        'notice': {'status': 'not_scheduled'}, 'startedAt': now,
                        'updatedAt': now, 'finishedAt': now, 'durationSeconds': 0.0}
                self._save(done)
                return {'schemaVersion': SCHEMA_VERSION, 'backend': BACKEND,
                        'operation': {**done, 'canCancel': False}}, 200
            record = {
                'schemaVersion': SCHEMA_VERSION, 'id': operation_id, 'account': account,
                'fromAccount': selected, 'epochBefore': snapshot['selected']['epoch'],
                'managerUuid': self._manager_uuid(reader or self.reader), 'startedAt': now, 'updatedAt': now,
                'finishedAt': None, 'phase': None, 'detail': None, 'pid': None, 'procStart': None,
                'exitCode': None, 'timeout': self.timeout, 'interval': self.interval,
            }
            control = RestartControl(self.state_path.with_name(f'{self.state_path.stem}-{operation_id}.control.json'))
            control.initialize()
            record['controlPath'] = str(control.path)
            self._save(record)
            try:
                proc = self.spawn(record)
            except Exception as error:  # noqa: BLE001 — the record must say the spawn never happened
                record.update(phase='failed', finishedAt=self.clock(),
                              detail=f'the restart helper could not start: {error}')
                self._save(record)
                return {'schemaVersion': SCHEMA_VERSION, 'backend': BACKEND,
                        'operation': self._public_operation(record)}, 200
            record['pid'] = getattr(proc, 'pid', None)
            record['procStart'] = _proc_start(record['pid']) if record['pid'] else None
            record['updatedAt'] = self.clock()
            if record['pid']:
                self.procs[record['id']] = proc
            self._save(record)
            return {'schemaVersion': SCHEMA_VERSION, 'backend': BACKEND,
                    'operation': self._public_operation(record)}, 202

    # @beacon[
    #   id=opc-pane@AccountSwitch.cancel,
    #   slice_labels=opc-pane,
    #   kind=ast,
    #   comment=Cancel one exact durable operation only while its interprocess restart gate is waiting; terminal cancelled survives reload/replay and prevents in-flight attempts from ever closing/stopping or selecting. Refuse once stopping wins; never kill based on a log.,
    # ]
    def cancel(self, operation_id):
        """Cancel the queued action, not a host; a child already inspecting must still pass the gate."""
        if not isinstance(operation_id, str) or not REQUEST_ID.fullmatch(operation_id):
            raise AccountError(400, 'schema', 'operationId must be 8-64 letters, digits, _ or -')
        with self.lock:
            record = self._read_record(operation_id)
            if record is None:
                raise AccountError(404, 'notfound', 'no such switch operation; read the account status again')
            current = self._public_operation(record)
            if current['phase'] in TERMINAL:
                return {'schemaVersion': SCHEMA_VERSION, 'backend': BACKEND, 'operation': current}
            gate = record.get('controlPath')
            if not gate:
                raise AccountError(409, 'unavailable', 'this older operation has no durable cancellation gate; cancellation cannot be safely confirmed')
            try:
                accepted = RestartControl(gate).cancel()
            except (OSError, ValueError):
                raise AccountError(503, 'unavailable', 'the cancellation gate could not be read; no cancellation was confirmed') from None
            if not accepted:
                raise AccountError(409, 'too-late', 'stopping admission already won; cancellation is no longer available (not proof of host exit)')
            # The gate was durably written first. Even a crash before this receipt is recovered as
            # cancelled by _public_operation, and the worker can never cross the stopping boundary.
            record.update(phase='cancelled', finishedAt=self.clock(), updatedAt=self.clock(),
                          detail='switch cancelled; the account was not changed')
            self._save(record)
            return {'schemaVersion': SCHEMA_VERSION, 'backend': BACKEND,
                    'operation': self._public_operation(record)}

    def _manager_uuid(self, reader):
        """Newest host-registered Manager, even when quota-halted/ended.

        The role-filtered facade inventory is host-only and creation-ordered. A halted Manager
        must not disappear merely because it currently has no native-input capability.
        """
        if reader is None:
            return None
        try:
            rows = reader.sessions(role='Manager').get('sessions') or []
            candidates = [row for row in rows
                          if isinstance(row, dict) and isinstance(row.get('uuid'), str)
                           and row.get('source', 'host') == 'host']
        except Exception:  # noqa: BLE001 — a failed lookup must never block the switch itself
            return None
        return max(candidates, key=lambda row: row.get('createdAt') or 0)['uuid'] if candidates else None

    def _spawn(self, record):
        args = [sys.executable, '-X', 'utf8', str(RESTART_WRAPPER), '--_worker',
                '--timeout', str(self.timeout), '--interval', str(self.interval),
                '--account', record['account'], '--close-idle', '--operation-id', record['id']]
        if record.get('controlPath'):
            args += ['--_control', record['controlPath']]
        if record.get('managerUuid'):
            args += ['--resume', record['managerUuid']]
        self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # A cancelled child may finish printing after a new operation starts. Replace the inode,
        # rather than truncate it, so its old stdout cannot corrupt the new operation's receipts.
        fd, name = tempfile.mkstemp(prefix='.claude-switch-log-', dir=self.log_path.parent)
        os.replace(name, self.log_path)
        with os.fdopen(fd, 'wb') as log:
            return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, cwd=str(REPO), env=os.environ.copy())

    # ---- durable record
    def _read_record(self, operation_id=None):
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        record = data.get('operation') if isinstance(data, dict) else None
        if operation_id is not None and (not isinstance(record, dict) or record.get('id') != operation_id):
            history = data.get('operations') if isinstance(data, dict) else None
            record = history.get(operation_id) if isinstance(history, dict) else None
        if (not isinstance(record, dict) or record.get('schemaVersion') != SCHEMA_VERSION
                or _label(record.get('account')) is None
                or not isinstance(record.get('id'), str)):
            return None
        return record

    def _save(self, record, *, select=True):
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            previous = json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        operations = previous.get('operations')
        if not isinstance(operations, dict):
            operations = {}
        if isinstance(previous.get('operation'), dict):
            old = previous['operation']
            operations[old['id']] = old
        operations[record['id']] = record
        current = record if select or not isinstance(previous.get('operation'), dict) else previous['operation']
        if current.get('id') == record['id']:
            current = record
        body = json.dumps({'schemaVersion': SCHEMA_VERSION, 'operation': current, 'operations': operations},
                          ensure_ascii=False, indent=1).encode('utf-8')
        fd, temp = tempfile.mkstemp(prefix='.claude-account-switch-', dir=str(self.state_path.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(os.dup(fd), 'wb') as output:
                output.write(body)
                output.flush()
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, self.state_path)

    # ---- the phase Dan sees
    def _exit_code(self, record):
        if record.get('exitCode') is not None:
            return record['exitCode']
        proc = self.procs.get(record.get('id'))
        return None if proc is None else proc.poll()

    def _tail(self):
        try:
            with open(self.log_path, 'rb') as log:
                log.seek(0, os.SEEK_END)
                size = log.tell()
                log.seek(max(0, size - LOG_TAIL))
                return log.read().decode('utf-8', 'replace')
        except OSError:
            return ''

    @staticmethod
    def _last_attempt(text):
        matches = ATTEMPT.findall(text)
        return int(matches[-1]) if matches else None

    @staticmethod
    def _last_output(text):
        """The wrapper's last line after the final attempt header: its own refusal/failure reason."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            if line.startswith('=== attempt'):
                break
            return line  # retain exact owner/task/reason; a task may be after a long session label
        return None

    def _public_operation(self, record):
        """Query-only receipt + worker/gate projection; no PID/log/exit/selector-only success."""
        with self.lock:
            record = dict(record)
            if record.get('alreadyActive'):
                return self._project(record, record['phase'], record.get('detail') or '', self.clock())
            gate_phase = self._control_phase(record)
            receipt = self.read_operation(record['id'])
            if receipt is None:
                # Initial non-admission can have no journal row. Its MACHINE envelope is
                # still evidence, but a vanished wrapper or human prose never invents success.
                from oc_claude_restart import restart_envelope
                receipt = restart_envelope(self._tail(), record['id'])
            if receipt and receipt.get('schema') == 'claude-restart/1' and receipt.get('operation_id') == record['id']:
                from claude_headless.restart_state import public_value, commands
                receipt = {**public_value(receipt), 'commands': commands(record['id'], receipt.get('request') or {}, public=True)}
                phase, detail = self._receipt_phase(receipt, record)
                if gate_phase == 'cancelled':
                    phase, detail = 'cancelled', 'switch cancelled; the account was not changed'
                elif gate_phase == 'stopping' and phase not in TERMINAL:
                    phase, detail = 'switching', 'stopping admission won; host exit/service/Manager readiness not yet proved. ' + detail
                elif gate_phase == 'waiting' and record.get('pid') and self.alive(record['pid'], record.get('procStart')):
                    from oc_claude_restart import retryable
                    if retryable(receipt):
                        phase = 'waiting'
                        detail = 'waiting to switch to ' + record['account'] + ': ' + (receipt['retry'].get('reason') or detail)
                record.update(phase=phase, detail=detail, receipt=receipt,
                    service=receipt.get('service'), manager=receipt.get('manager'), notice=receipt.get('notice'),
                    recoverCommand=(receipt.get('commands') or {}).get('recover'), updatedAt=self.clock())
                if phase in TERMINAL:
                    record['finishedAt'] = record.get('finishedAt') or self.clock()
                self._save(record, select=False)
                return self._project(record, phase, detail, self.clock())
            if gate_phase == 'cancelled':
                record.update(phase='cancelled', detail='switch cancelled; the account was not changed',
                              finishedAt=self.clock(), updatedAt=self.clock())
                self._save(record, select=False)
                return self._project(record, 'cancelled', record['detail'], self.clock())
            exit_code = self._exit_code(record)
            if exit_code is not None:
                record['exitCode'] = exit_code
            tail = self._tail()
            attempt = self._last_attempt(tail)
            running = self.alive(record.get('pid'), record.get('procStart')) if record.get('pid') else False
            code = exit_code if exit_code is not None else attempt
            target = record['account']
            detail = ''
            if running:
                phase = 'switching' if gate_phase == 'stopping' else 'waiting'
            elif code == 3:
                phase = 'refused'
                detail = 'the helper reported refusal; exact effect evidence unavailable — query this operation ID'
            elif code == 2:
                phase = 'refused'
                detail = ('the restart command rejected the request (usage); nothing was signalled')
            else:
                phase = 'unknown'
                detail = 'operation evidence is pending/unknown; query this same ID, never submit another switch to recover a reply'
            if not detail:
                reason = self._last_output(tail) if phase == 'waiting' else None
                detail = {'waiting': (f'waiting to switch to {target}: {reason}' if reason else
                                      f'inspecting Claude owners and observation waits before switching to {target}'),
                           'switching': f'stopping admission won for {target}; host exit/service/Manager readiness not yet proved'}.get(phase, '')
            if phase in TERMINAL:
                record.update(phase=phase, detail=detail, finishedAt=self.clock(), updatedAt=self.clock())
                self._save(record, select=False)
            elif exit_code is not None:
                record['updatedAt'] = self.clock()
                self._save(record, select=False)
            return self._project(record, phase, detail, self.clock())

    def _read_lifecycle(self, operation_id):
        from claude_headless.restart_state import read_receipt
        try:
            return read_receipt(self.state_root, operation_id, public=True)
        except (OSError, ValueError, __import__('sqlite3').Error):
            return None  # unknown remains reconcilable; GET never finalizes or schedules anything

    @staticmethod
    def _receipt_phase(receipt, record):
        service, manager, notice = (receipt.get(k) or {} for k in ('service', 'manager', 'notice'))
        account = service.get('account') or {}
        requested, phase = receipt.get('requested_transition'), receipt.get('phase')
        extra = f"Manager {manager.get('state', 'unknown')}; notice {notice.get('delivery_status') or notice.get('status') or 'pending'}."
        if service.get('state') == 'ready' and receipt.get('direction') == 'restore_old':
            return 'restored', f"Switch failed; {account.get('label', 'old')} service restored. " + extra
        if (requested == 'succeeded' and service.get('state') == 'ready' and
                account.get('label') == record['account']):
            return 'active', record['account'] + ' host ready. ' + extra
        if phase == 'recovery_required':
            errors = receipt.get('errors') or []
            return 'recovery_required', (errors[-1].get('message', 'Recovery required') if errors else 'Recovery required') + '. ' + extra
        if service.get('state') == 'starting' or phase in ('starting', 'start_intended'):
            return 'starting', 'Replacement starting; not ready yet. ' + extra
        if requested in ('refused', 'cancelled'):
            return requested, ('; '.join(str(e.get('message', '')) for e in receipt.get('errors') or []) or requested) + '. ' + extra
        if receipt.get('direction') in ('restore_old', 'continue_destination') and requested != 'succeeded':
            return 'recovering', 'Recovering this same operation; service readiness not yet proved. ' + extra
        return ('waiting' if phase in ('waiting', 'settling') else 'switching'), extra

    @staticmethod
    def _control_phase(record):
        try:
            return RestartControl(record['controlPath']).phase() if record.get('controlPath') else None
        except (OSError, ValueError):
            return None  # missing/legacy gates never grant cancellation

    def _project(self, record, phase, detail, now):
        started = record.get('startedAt') if isinstance(record.get('startedAt'), (int, float)) else now
        return {'id': record['id'], 'account': record['account'], 'fromAccount': record.get('fromAccount'),
                'alreadyActive': bool(record.get('alreadyActive')), 'service': record.get('service') or {'state': 'unchecked'},
                'manager': record.get('manager') or {'state': 'unknown'}, 'notice': record.get('notice') or {'status': 'unknown'},
                'receipt': record.get('receipt'), 'recoverCommand': record.get('recoverCommand'),
                'phase': phase, 'detail': detail, 'startedAt': started,
                'canCancel': phase == 'waiting' and self._control_phase(record) == 'waiting',
                'updatedAt': now if phase in TERMINAL else record.get('updatedAt') or now,
                'finishedAt': record.get('finishedAt') if phase in TERMINAL else None,
                'durationSeconds': max(0.0, now - started),
                'retrySeconds': record.get('interval') or self.interval,
                'timeoutSeconds': record.get('timeout') or self.timeout}
