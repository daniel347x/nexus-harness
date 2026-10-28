"""Guarded Claude host lifecycle — durable request, proved stop, same-ID recovery.

The one-command form of the guarded restart recipe in ``docs/claude-headless-manager.md``. It closes
nothing on a guess and signals nothing on a guess:

1. read the live session list; refuse if **any** session with a live or unproved owner is unsettled
   (accepted input awaiting a result, pending/mismatched settings, a native retry, unsettled
   background tasks, a scheduled recovery or a pending escalation) — a restart never drops unsettled
   work. A record whose native owner is **proved dead** (``exited`` or ``launch_failed``: nothing
   left to close, kill or resume) keeps its markers as reported history only, never as a hold.
   With ``--close-idle``, a session held **only** by "native background task level is unknown"
   (finished work the CLI never re-reported) is first settled by the host (``settle_for_restart``):
   the collector's native readback, idle with an empty level = closable in step 2; only if the
   readback cannot settle it, the host's process-tree proof (idle, no recorded task, no child but
   ``claude_context_mcp.py``) allows one SIGTERM to that process, reported by name. Never a blind kill;
2. without ``--close-idle``, refuse while **any** native owner with a live or unproved record
   remains; with it, close **only** settled ``ready`` sessions through the host's own
   ``close_input`` (which itself refuses unsettled input) and wait for every native exit;
3. re-read ``list``/``health``, verify the host PID/start witness, and send **one** explicit
   operator SIGTERM to that exact process — never a watchdog, never because of silence;
4. wait until the host's PID/start witness is observed gone;
5. start a fresh host through ``claude_host.py start`` and health-confirm the exact new PID/start
   witness;
6. optionally ``--resume <uuid>`` (the host's stored Manager snapshot) or ``--launch-manager
   --model … --effort …`` (a freshly composed Manager prompt) through the existing manager
   launcher. Neither sends a turn unless the Manager explicitly requests an account-switch
   outcome notice with ``--account LABEL --resume UUID --notify-manager``. That one labelled
   notice uses the host's existing five-hour check delivery path, including post-stop failures.

Every request prints its operation ID and a claude-restart/1 envelope. --operations/--status
are read-only; --recover ID continues the recorded recipe even without an old host. Verified
old selection after a post-stop precommit failure restores old service; a committed target
continues there. No elapsed startup deadline makes a live replacement dead. Exit 3 means no
host signal, NOT no partial idle/observer effects. Exit 1 may mean failed switch/old service
restored or healthy service/failed Manager. Pending notice is never delivered. No inference,
native login changes, original-turn replay or OpenCode restart.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

REFUSED_EXIT = 3
ERROR_EXIT = 1
DEFAULT_TIMEOUT = 120.0
DEFAULT_POLL_SECONDS = 0.25
START_TIMEOUT_SECONDS = 60.0
CANCELLED_EXIT = 4


# @beacon[
#   id=opc@claude_restart_control,
#   slice_labels=opc-basic,opc-pane,opc-claude-restart,
#   kind=ast,
#   comment=Per-operation durable cancellation gate shared by pane and queued restart: flock serializes waiting-to-cancelled against waiting-to-stopping before any old-process close/stop; a cancelled gate can never start stopping.,
# ]
class RestartControl:
    """A private local control file, never a browser-supplied path or a log-based decision.

    Only waiting -> cancelled or waiting -> stopping is allowed. The lock is held solely for
    this atomic decision/write, not across a restart or any host contact. A stale/missing file
    fails closed. Stopping is sticky across refused attempts (idle EOFs may already be sent).
    """

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _locked(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _read(self):
        value = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('phase') not in ('waiting', 'stopping', 'cancelled'):
            raise ValueError('invalid restart cancellation control')
        return value['phase']

    def _write(self, phase):
        fd, name = tempfile.mkstemp(prefix='.restart-control-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'phase': phase}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def initialize(self):
        with self._locked():
            if self.path.exists():
                raise ValueError('restart cancellation control already exists')
            self._write('waiting')

    def phase(self):
        with self._locked():
            return self._read()

    def cancel(self):
        with self._locked():
            phase = self._read()
            if phase == 'waiting':
                self._write('cancelled')
            return phase != 'stopping'

    def begin_stopping(self):
        with self._locked():
            if self._read() == 'cancelled':
                raise RestartRefused('account switch cancelled; nothing was stopped or selected')
            self._write('stopping')


class RestartRefused(RuntimeError):
    """No host signal; retain partial effects and classify all settlement paths alike."""

    def __init__(self, message, *, classification='permanent', safe_pre_stop_retry=False):
        super().__init__(message)
        self.classification = classification
        self.safe_pre_stop_retry = safe_pre_stop_retry

    @classmethod
    def settlement_hold(cls, message, holds):
        """Only the existing temporary settlement set authorizes a same-ID queue retry."""
        holds = list(holds)
        temporary = bool(holds) and all(reasons and all(
            reason.startswith('accepted input awaiting a result (') or reason in (
                'native turn or required action in progress', 'native session state is not verified idle',
                'native background task level is unknown') for reason in reasons) for reasons in holds)
        return cls(message, classification='settlement' if temporary else 'evidence_reconciliation',
                   safe_pre_stop_retry=temporary)


class RestartError(RuntimeError):
    """A lifecycle/follow-up error; actual selection and usable service remain separate facts."""


def socket_path_of(client) -> str:
    """The endpoint under inspection, for messages and for starting the same endpoint's host."""
    return str(getattr(client, "socket_path", "") or "")


# @beacon[
#   id=opc@claude_restart_settlement,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The host's own close predicate in operator mode: holds only on losable work of a live or unproved owner; settings/escalation/protocol/financial markers are reported, never blocking.,
# ]
def settlement_blockers(record: dict) -> list[str]:
    """Use the host's process-close predicate, not a second restart-specific rule.

    A guarded restart is a deliberate operator close: it holds only on work a close could lose
    (inputs awaiting a result, native turns, background tasks, retries, permission requests,
    queued turns, scheduled recovery, ownership doubt). Settings, escalations, protocol and
    financial markers are reported (:func:`settlement_advisories`) but never block it."""
    from .settlement import settlement_blockers as process_blockers
    return process_blockers(record, automatic=False)


def settlement_advisories(record: dict) -> list[str]:
    from .settlement import settlement_advisories as advisories
    return advisories(record)


def owner_proved_dead(record: dict) -> bool:
    """Proved-dead proof: the record's native owner cannot exist any more.

    ``exited`` is the host's own proved-exit state; ``launch_failed`` is written only when a launch
    failed with no subprocess and a free UUID lock (including a retained launch intent). Neither can be closed, killed
    or resumed by a host restart, so that record's retained markers are reported, never blocking.
    """
    return record.get("owner") == "gone" and record.get("state") in ("exited", "launch_failed")


def owner_hold(record: dict) -> str:
    return f"native owner {record.get('owner')!r} in state {record.get('state')!r}"


def partition_unsettled(rows):
    """Split host ``list`` rows into (blocking, historical) unsettled (record, reasons) pairs.

    A record with a live or unproved owner blocks on any marker. A record whose owner is proved
    dead keeps its markers as reported history: restarting the host cannot kill, drop or resume
    anything that a proved-dead owner still held.
    """
    blocking, historical = [], []
    for record in rows:
        reasons = settlement_blockers(record)
        if not reasons:
            continue
        (historical if owner_proved_dead(record) else blocking).append((record, reasons))
    return blocking, historical


UNKNOWN_LEVEL = "native background task level is unknown"


def session_name(record: dict) -> str:
    """The label plus the launch title (or kind/role) the pane and ``oc.sh`` show for it."""
    labels = record.get("labels") if isinstance(record.get("labels"), dict) else {}
    title = labels.get("title") or labels.get("kind") or record.get("socket") or ""
    return session_label(record) + (f" {title!r}" if title else "")


# @beacon[
#   id=opc@claude_restart_settle_unknown_levels,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Before refusing, asks the host to settle each live session held ONLY by an unknown task level (native readback; process-tree proof stop as the explicit fallback), awaits every stop's proved exit and re-reads the list. Never a blind kill.,
# ]
def settle_unknown_levels(client, unsettled, *, witness_fn, timeout, clock, sleep, interval, lifecycle=None):
    """Ask the host to settle each session whose only hold is the unknown task level.

    The host runs the collector's native readback (``settle_for_restart``): idle with an empty
    fresh level settles it (the ``--close-idle`` pass below then closes it through
    ``close_input``); only if the readback cannot settle it may the host stop it, after its own
    process-tree proof. Returns the outcome rows, each named; nothing else is touched here."""
    candidates = [record for record, reasons in unsettled
                  if reasons == [UNKNOWN_LEVEL] and not owner_proved_dead(record)]
    if not candidates:
        return []
    implemented = (client.call("health") or {}).get("implemented") or []
    if "settle_for_restart" not in implemented:
        return [{"session": session_name(record), "outcome": "unavailable",
                 "reason": "this host predates settle_for_restart (the next restart installs it)"}
                for record in candidates]
    outcomes, stopped = [], []
    for record in candidates:
        row = {"session": session_name(record)}
        if not pid_witness_alive(record, witness_fn):
            row.update(outcome="refused", reason="its recorded PID/start witness is not verifiably alive")
        else:
            call = lambda: client.call("settle_for_restart", execution_id=record.get("execution_id"),
                                       generation=record.get("generation"), allow_stop=True)
            answer = (lifecycle.effect('settle_native', native_identity(record), call) if lifecycle else call()) or {}
            row.update(outcome=answer.get("outcome") or "refused", reason=answer.get("reason"))
            if answer.get("proof"):
                row["proof"] = answer["proof"]
            if row["outcome"] == "stopped":
                stopped.append(record)
        outcomes.append(row)
    if stopped:
        wanted = {record.get("execution_id") for record in stopped}

        def stops_proved_gone():
            current = {row.get("execution_id"): row for row in client.call("list", all=True) or []}
            return all(execution_id in current and owner_proved_dead(current[execution_id])
                       and not pid_witness_alive(current[execution_id], witness_fn)
                       for execution_id in wanted)

        poll_until(
            stops_proved_gone, timeout=timeout, clock=clock, sleep=sleep, interval=interval,
            error=RestartRefused,
            message=("a process stopped on its process-tree proof did not reach a proved exit within "
                     f"{timeout:g}s; the host was not signalled - inspect it (never kill it): "
                     + "; ".join(session_name(record) for record in stopped)))
    return outcomes


def settle_note(outcomes) -> str:
    return "settle attempts: " + "; ".join(
        f"{row['session']}: {row['outcome']}" + (f" ({row['reason']})" if row.get("reason") else "")
        for row in outcomes)


def historical_note(historical) -> str:
    """One operator line naming reported, non-blocking markers on proved-dead records."""
    return ("dead historical records (reported, not blocking): " + "; ".join(
        f"{session_label(record)}: {', '.join(reasons)}" for record, reasons in historical))


def session_label(record: dict) -> str:
    sid = record.get("execution_id") or record.get("native_uuid") or "?"
    return f"{sid} (generation {record.get('generation')}, role {record.get('role')})"


def pid_witness_alive(record: dict, witness_fn) -> bool:
    """True while the recorded native PID is still the exact process it was at launch."""
    recorded = record.get("process_witness")
    if not recorded:
        return False
    try:
        return witness_fn(record.get("pid")) == recorded
    except OSError:
        return False


def session_summary(record: dict) -> dict:
    """A small, secret-free report row; full records may carry auth/provenance details."""
    return {key: record.get(key) for key in
            ("execution_id", "role", "generation", "state", "owner")}


def poll_until(predicate, *, timeout, clock, sleep, interval, error, message):
    """Bounded operator observation of the host — never an execution deadline on the host.

    Returns the first truthy predicate value. Raises ``error(message)`` when the timeout elapses;
    this function itself never signals, closes or writes anything.
    """
    deadline = clock() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if clock() >= deadline:
            raise error(message)
        sleep(interval)


def start_host_fresh(socket_path=None, state_root=None, *, operation_id=None, start_token=None,
                     python=None, host_script=None,
                     runner=subprocess.run, timeout=START_TIMEOUT_SECONDS) -> dict:
    """Start a fresh detached host through ``claude_host.py start`` and parse its start record.

    Returns start_requested only. The maintained launcher holds host.lock across Popen and
    passes it to serve; the engine reconciles the retained token/owner after a lost reply.
    """
    host_script = Path(host_script) if host_script is not None else (
        Path(__file__).resolve().parent.parent / "claude_host.py")
    argv = [python or sys.executable, str(host_script)]
    if socket_path:
        argv += ["--socket-path", str(socket_path)]
    if state_root:
        argv += ["--state-root", str(state_root)]
    if operation_id:
        argv += ['--operation-id', operation_id, '--start-token', start_token]
    argv.append("start")
    completed = runner(argv, capture_output=True, text=True, timeout=timeout)
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    if completed.returncode != 0 or not lines:
        detail = (completed.stderr or "").strip()
        raise RestartError(
            f"claude_host.py start failed (exit {completed.returncode}): {detail or 'no output'}")
    try:
        payload = json.loads(lines[-1])
    except ValueError as error:
        raise RestartError(f"claude_host.py start printed no start record: {lines[-1]!r}") from error
    if not payload.get("host_pid"):
        raise RestartError(f"claude_host.py start printed no host PID: {payload!r}")
    return payload


def _sigterm(pid):
    """Dan's explicit operator stop: one SIGTERM to the verified host PID; never a watchdog."""
    os.kill(pid, signal.SIGTERM)


def _posix_witness(pid):
    """Lazy POSIX import so this module stays importable (and testable) anywhere."""
    from .host import witness
    return witness(pid)


# @beacon[
#   id=opc@claude_restart_guarded,
#   slice_labels=opc-basic,opc-claude-restart,
#   kind=ast,
#   comment=One durable lifecycle entry for restarts/switches: checks unfinished ownership with the exact old-host witness under the existing transition lock, binds before effects and replays/recovers the same ID; retains preflight/settlement and selector-proved old-service restoration.,
# ]
def guarded_restart(client, *, close_idle=False, timeout=DEFAULT_TIMEOUT, state_root=None,
                    witness_fn=None, clock=time.monotonic, sleep=time.sleep,
                    interval=DEFAULT_POLL_SECONDS, kill_fn=None, starter=None, account=None,
                    notify_manager=None, control=None, operation_id=None, manager_policy=None,
                    recover=False) -> dict:
    from .accounts import transition_lock
    from .restart_state import journal, read_operations, physical_pending
    operation_id = operation_id or str(uuid.uuid4())
    root = Path(state_root) if state_root is not None else None
    inspection = None
    try:
        candidate_root = root or Path.home() / '.local/state/opencode-companion/claude-headless'
        unfinished = any(physical_pending(record, witness_fn=witness_fn or _posix_witness)
                         for record in read_operations(candidate_root))
        if unfinished and root is None:
            root = candidate_root
        if not recover and not unfinished and not (root and read_operations(root, operation_id)):
            try:
                inspection = client.call('health')
            except Exception as error:
                raise RestartRefused(f'no reachable host at {socket_path_of(client)!r}; '
                    f'use --operations/--recover for a recorded transition, not a blind second start ({error})') from error
            if root is not None and str(inspection.get('state_root')) != str(root):
                raise RestartRefused('--state-root differs from the inspected host state-root')
        if root is None:
            if recover:
                from .host import STATE_ROOT
                root = STATE_ROOT
            else:
                try:
                    root = Path(inspection['state_root'])
                except Exception as error:
                    raise RestartRefused(f'no reachable host at {socket_path_of(client)!r}; '
                        f'use --operations/--recover for a recorded transition, not a blind second start ({error})') from error
        with transition_lock(root=root):
            existing = read_operations(root, operation_id)
            for pending in read_operations(root):
                transition = pending.get('transition') or {}
                if pending['operation_id'] != operation_id and physical_pending(pending, witness_fn=witness_fn or _posix_witness):
                    problem = RestartRefused('unfinished physical transition ' + pending['operation_id'] +
                        '; inspect/recover that operation, not a new restart', classification='evidence_reconciliation')
                    problem.operation = pending
                    raise problem
            with journal(root) as owner:
                lifecycle = RestartLifecycle(owner, client, operation_id=operation_id, root=root,
                    close_idle=close_idle, account=account, notify_manager=notify_manager,
                    manager_policy=manager_policy, control=control, timeout=timeout,
                    witness_fn=witness_fn or _posix_witness, clock=clock, sleep=sleep,
                    interval=interval, kill_fn=kill_fn or _sigterm, starter=starter, inspection=inspection)
                return lifecycle.run(existing, recover=recover)
    except (ValueError, RuntimeError, OSError) as error:
        if isinstance(error, (RestartRefused, RestartError)):
            raise
        problem = RestartRefused(str(error), classification=(
            'transition_lock' if 'transition in progress' in str(error) else 'permanent'),
            safe_pre_stop_retry='transition in progress' in str(error))
        problem.operation_id = operation_id
        raise problem from error


def native_identity(record):
    return {key: record.get(key) for key in ('execution_id', 'native_uuid', 'generation', 'pid', 'process_witness')}


# @beacon[
#   id=opc@RestartLifecycle,
#   slice_labels=opc-basic,opc-claude-restart,
#   kind=ast,
#   comment=Canonical driver retains exact actions and selector/stop/start gaps; same-ID reattempts mark pending before effects so prior refusals cannot release an unfinished stop, absent-old-host pre-stop recovery records refusal and post-stop restoration needs selector proof; no blind replay.,
# ]
class RestartLifecycle:
    """Physical lifecycle driver; existing _guarded_restart remains the native stop proof owner.

    Injected witnesses/signals/starters are exclusively for inert tests. The row's immutable
    recipe, not this object's lifetime, supplies restart/recovery authority. Pre-stop recovery
    of an absent old host records refusal and leaves ordinary host-start admission available.
    """
    def __init__(self, owner, client, *, operation_id, root, close_idle, account, notify_manager,
                 manager_policy, control, timeout, witness_fn, clock, sleep, interval, kill_fn, starter,
                 inspection=None):
        self.owner, self.client, self.operation_id = owner, client, operation_id
        self.root, self.close_idle, self.label = Path(root), close_idle, account
        self.notify_manager, self.control = notify_manager, control
        self.policy = manager_policy or {'policy': 'none'}
        self.timeout, self.witness, self.clock, self.sleep = timeout, witness_fn, clock, sleep
        self.interval, self.kill, self.starter = interval, kill_fn, starter
        self.record = None
        self.report = {'socket_path': socket_path_of(client), 'close_idle': close_idle, 'phases': {},
                       'operation_id': operation_id}
        self.storage_errors = []
        self.inspection = inspection
        self.observed_service = None
        self.volatile_errors = []

    @property
    def transition(self):
        return self.record['transition']

    def refresh(self):
        try:
            self.record = self.owner.read(self.operation_id) or self.record
        except Exception as error:
            self.storage_errors.append(type(error).__name__ + ': ' + str(error))
        return self.record

    def patch(self, *, fields=None, action=None, error=None, essential=True):
        try:
            self.record = self.owner.transition_update(self.operation_id, fields=fields, action=action, error=error)
        except Exception as failure:
            # A commit may have succeeded before its acknowledgement failed. Read back the same
            # named evidence before deciding it was not durable. Never save this object's old row.
            try:
                current = self.owner.read(self.operation_id)
            except Exception:
                current = None
            found = current and current.get('transition')
            matches = bool(found)
            for key, value in (fields or {}).items():
                matches = matches and (all(found.get(key, {}).get(k) == v for k, v in value.items())
                    if isinstance(value, dict) else found.get(key) == value)
            if action:
                matches = matches and any(e.get('status') == action.get('status') for e in
                    found.get('effects', {}).get(action['action_id'], {}).get('events', []))
            if error:
                matches = matches and error in found.get('errors', [])
            if matches:
                self.record = current
                return
            if essential:
                raise
            self.storage_errors.append(type(failure).__name__ + ': ' + str(failure))
            if error:
                self.volatile_errors.append({**error, 'durable': False})
            # The already-durable recipe/start identities still authorize selector-aware
            # service restoration. Uncommitted observations stay explicitly local/pending.
            if fields:
                if self.record is None:
                    return
                self.record = deepcopy(self.record)
                for key, value in fields.items():
                    if isinstance(value, dict) and isinstance(self.transition.get(key), dict):
                        self.transition[key].update(value)
                    else:
                        self.transition[key] = value

    def fail(self, error, *, essential=False):
        self.patch(error={'phase': self.transition['phase'], 'type': type(error).__name__,
                          'message': str(error), 'post_stop': bool(self.transition.get('host_stop_intended'))}, essential=essential)

    def effect(self, kind, target, call, *, attempt=1, essential=True):
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
            [self.operation_id, kind, target, attempt], sort_keys=True)))
        action = {'action_id': key, 'kind': kind, 'target': target, 'attempt': attempt}
        previous = self.transition['effects'].get(key)
        if previous and kind in ('close_native', 'settle_native', 'retire_observer'):
            raise RestartRefused('exact ' + kind + ' incarnation already has an action intent; '
                'reconcile its native/retirement receipt before another effect', classification='evidence_reconciliation')
        self.patch(action={**action, 'status': 'intended'}, essential=essential)
        try:
            value = call()
        except BaseException as error:
            self.patch(action={**action, 'status': 'uncertain', 'error': type(error).__name__}, essential=False)
            raise
        self.patch(action={**action, 'status': 'issued', 'receipt': value}, essential=essential)
        if kind in ('apply_target', 'selector_commit') or (
                kind == 'prepare_observers' and (value or {}).get('outcome') == 'prepared') or (
                kind == 'settle_native' and (value or {}).get('outcome') == 'settled'):
            self.patch(action={**action, 'status': 'observed_complete', 'receipt': value}, essential=essential)
        return value

    def observe_effect(self, kind, target, receipt, *, attempt=1):
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
            [self.operation_id, kind, target, attempt], sort_keys=True)))
        if key in self.transition['effects']:
            self.patch(action={'action_id': key, 'kind': kind, 'target': target, 'attempt': attempt,
                'status': 'observed_complete', 'receipt': receipt}, essential=False)

    def observe_native_exits(self, rows):
        """Reconcile issued/lost native closes against their exact incarnation, not a later owner."""
        from .restart_state import lock_free
        for effect in list(self.transition['effects'].values()):
            if effect['kind'] not in ('close_native', 'settle_native'):
                continue
            target = effect['target']
            for row in rows:
                if (row.get('execution_id') != target.get('execution_id') or
                        row.get('generation') != target.get('generation') or not owner_proved_dead(row)):
                    continue
                try:
                    absent = not target.get('process_witness') or self.witness(target.get('pid')) != target['process_witness']
                except OSError:
                    continue
                native = row.get('native_uuid') or str(row.get('execution_id', '')).removeprefix('cc-')
                if absent and (not native or lock_free(self.root / native / 'owner.lock')):
                    self.observe_effect(effect['kind'], target, {'native_exit': {
                        **native_identity(row), 'state': row.get('state'), 'owner': row.get('owner'),
                        'witness_absent': True, 'uuid_lock_free': True}}, attempt=effect['attempt'])

    def before_effects(self):
        # The recipe is committed before the cancellation decision and before every possible
        # readback-stop/EOF/retirement. Gate-stopping is admission, never exit evidence.
        if self.control:
            self.control.begin_stopping()
        self.patch(fields={'phase': 'settling', 'physical_active': True})

    def request(self):
        return {'kind': 'account_switch' if self.label is not None else 'restart',
            'account': self.label, 'close_idle': bool(self.close_idle),
            'namespace': {'state_root': str(self.root), 'socket_path': socket_path_of(self.client)},
            'manager': self.policy, 'notify': bool(self.notify_manager),
            'control_path': str(self.control.path) if self.control else None}

    def admit(self, initial, rows):
        from . import accounts
        from .restart_state import stage_episode
        old = accounts.selector_snapshot()
        destination = accounts.resolve_account(self.label) if self.label else None
        if destination is not None:
            destination = {k: destination.get(k) for k in ('label', 'config_dir', 'identity')}
            if destination['config_dir']:
                plan = accounts.prepare_shared_directory(destination['config_dir'], check_only=True)
            else:
                plan = None
        else:
            plan = None
        recipe = {'old_selector': old, 'destination': destination,
            'target_epoch': old['account']['epoch'] + 1,
            'old_host': {k: initial.get(k) for k in ('host_pid', 'process_witness', 'state_root', 'socket_path')},
            'native_owners': [native_identity(row) for row in rows], 'shared_plan': plan,
            'start_tokens': {key: str(uuid.uuid4()) for key in ('normal', 'restore_old', 'continue_destination')},
            'fresh_manager_uuid': str(uuid.uuid4()) if self.policy['policy'] == 'fresh' else None,
            'episode_id': 'observer-switch:' + self.operation_id}
        # Episode link and immutable recipe commit together, before any effect. Optional
        # publication/quota/Workflowy/usage calls are not admission prerequisites.
        try:
            with self.owner._mutation(self.operation_id):
                self.record = self.owner.admit_transition(self.operation_id, self.request(), recipe)
                stage_episode(self.owner.store, self.operation_id, recipe['episode_id'])
        except Exception:
            current = self.owner.read(self.operation_id)
            episode = self.owner.store.get('quota_checks', '', recipe['episode_id'])
            if not current or not current.get('transition') or not episode:
                self.record = None  # an unacknowledged/rolled-back admission never authorizes effects
                raise
            if current['transition']['request'] != self.request():
                raise
            self.record = current

        if (destination is not None and self.close_idle and
                'prepare_observer_switch' in (initial.get('implemented') or [])):
            self.patch(fields={'manager': {'policy': 'current', 'state': 'pending'},
                               'notice_authorized': True})

    def run(self, existing, *, recover):
        from . import accounts
        from .restart_state import old_host_absent
        self.record = existing
        # A conflicting caller has no authority to mutate/recover the bound operation.
        # Keep this outside the admitted-operation exception/restoration handler.
        if existing and existing.get('transition') and not recover and self.transition['request'] != self.request():
            raise RestartRefused('operation ID is already bound to a different restart request')
        if existing and existing.get('transition') and recover and (
                self.transition['request']['namespace'] != self.request()['namespace']):
            raise RestartRefused('recovery namespace differs from the recorded operation')
        try:
            if existing:
                if not existing.get('transition'):
                    raise RestartRefused('legacy evidence incomplete: no before-stop recipe')
                request = self.transition['request']
                if recover:
                    if request['namespace'] != self.request()['namespace']:
                        raise RestartRefused('recovery namespace differs from the recorded operation')
                    self.label, self.close_idle = request['account'], request['close_idle']
                    self.policy = request['manager']
                    self.notify_manager = (self.policy.get('resume') if request['notify'] else None)
                    self.control = RestartControl(request['control_path']) if request.get('control_path') else None
                elif request != self.request():
                    raise RestartRefused('operation ID is already bound to a different restart request')
                self.report['phases'] = deepcopy(self.transition.get('phases', {}))
                if self.transition.get('service', {}).get('state') == 'ready':
                    self.report['replayed_completed_operation'] = True
                    return self.result()
                if self.transition.get('host_stop_intended'):
                    old = self.transition['recipe']['old_host']
                    if self.witness(old['host_pid']) != old['process_witness']:
                        self.continue_service(recovering=True)
                        return self.result()
                    # A lost signal reply is NOT authorization to signal a reused/different
                    # owner. The original stop proof below must run freshly on the same host.
                elif recover:
                    if old_host_absent(self.transition, witness_fn=self.witness):
                        raise RestartRefused('the recorded old host is absent without host-stop intent; '
                            'this operation did not stop it. Start service with claude_host.sh start; '
                            'the recorded pre-stop effects remain on this ID', classification='evidence_reconciliation')
                    if self.transition.get('requested_transition') not in ('refused', 'cancelled'):
                        self.fail(RuntimeError('coordinator interrupted before proved host exit'))
            elif recover:
                raise RestartRefused('unknown operation ID; no durable before-stop recipe')

            initial = self.inspection or self.client.call('health')
            if str(initial.get('state_root')) != str(self.root):
                raise RestartRefused('--state-root differs from the inspected host state-root')
            if initial.get('socket_path') and str(initial['socket_path']) != socket_path_of(self.client):
                raise RestartRefused('inspected host socket namespace differs from this operation endpoint')
            if self.record and (initial.get('host_pid') != self.transition['recipe']['old_host']['host_pid']
                    or initial.get('process_witness') != self.transition['recipe']['old_host']['process_witness']):
                raise RestartRefused('the recorded old host changed; recover needs exact ownership reconciliation')
            selected = self.transition['recipe']['destination'] if self.record else (
                accounts.resolve_account(self.label) if self.label else None)
            if selected and selected.get('config_dir'):
                accounts.prepare_shared_directory(selected['config_dir'], check_only=True)
            if self.record:
                truth, _ = accounts.selection_truth(self.transition['recipe']['old_selector'],
                    selected, self.operation_id, self.transition['recipe']['target_epoch'])
                if truth != 'old':
                    raise RestartRefused('selection changed before old-host stop; reconcile this operation')
                if not self.transition.get('host_stop_intended') and self.transition.get('requested_transition') in ('refused', 'cancelled'):
                    # This new attempt is not the preceding recorded refusal. Commit pending
                    # before any stopping gate/effect so a coordinator gap keeps exclusivity;
                    # all earlier errors, exact action receipts and sticky gates stay intact.
                    self.patch(fields={'requested_transition': 'pending',
                        'retry': {'classification': 'none', 'safe_pre_stop_retry': False, 'reason': None}})
            return _guarded_restart(self.client, close_idle=self.close_idle, timeout=self.timeout,
                state_root=self.root, witness_fn=self.witness, clock=self.clock, sleep=self.sleep,
                interval=self.interval, kill_fn=self.kill, starter=self.starter,
                account=selected, notify_manager=self.notify_manager, control=self.control,
                operation_id=self.operation_id, lifecycle=self, initial=initial)
        except BaseException as error:
            if self.record:
                self.fail(error)
                if self.transition.get('host_stop_intended'):
                    if isinstance(error, Exception):
                        try:
                            old = self.transition['recipe']['old_host']
                            truth, _ = accounts.selection_truth(self.transition['recipe']['old_selector'],
                                self.transition['recipe']['destination'], self.operation_id,
                                self.transition['recipe']['target_epoch'])
                            if (self.witness(old['host_pid']) != old['process_witness']
                                    and truth == 'old' and not self.transition.get('start_attempt')
                                    and self.transition['direction'] != 'restore_old'):
                                self.continue_service(recovering=True)
                        except Exception as recovery_error:
                            self.fail(recovery_error)
                            self.patch(fields={'phase': 'recovery_required', 'physical_active': True}, essential=False)
                    if isinstance(error, Exception):
                        if self.transition.get('service', {}).get('state') != 'ready':
                            self.patch(fields={'phase': 'recovery_required', 'physical_active': True,
                                'requested_transition': 'failed',
                                'retry': {'classification': 'evidence_reconciliation',
                                    'safe_pre_stop_retry': False, 'reason': 'continue the recorded operation, not a normal retry'}},
                                essential=False)
                        problem = error if isinstance(error, RestartError) else RestartError(str(error))
                        problem.report = self.result()
                        raise problem from error if problem is not error else None
                elif isinstance(error, RestartRefused):
                    self.patch(fields={'requested_transition': 'cancelled' if self.control and self.control.phase() == 'cancelled' else 'refused',
                        'retry': {'classification': error.classification,
                                  'safe_pre_stop_retry': error.safe_pre_stop_retry,
                                  'reason': str(error)}}, essential=False)
            if isinstance(error, Exception):
                if not isinstance(error, (RestartRefused, RestartError)):
                    error = RestartRefused(str(error))
                error.operation_id = self.operation_id
                if self.record:
                    error.report = self.result()
                raise error
            raise

    def native_absence(self):
        """All recorded/current native owners: exact witness absent, settled drain and free UUID lock."""
        proofs = self.transition.get('native_exit_proofs')
        if proofs is None:
            raise RestartError('old host exit lacks retained final native settlement/drain proofs')
        self.prove_native_absence([*proofs, *self.owner.store.sessions()])

    def prove_native_absence(self, rows):
        """The same fresh OS/UUID absence proof before host signal and on offline recovery."""
        from .restart_state import lock_free
        for row in rows:
            if not owner_proved_dead(row):
                raise RestartError('native owner/drain not proved settled: ' + session_label(row))
            recorded = row.get('process_witness')
            if recorded and self.witness(row.get('pid')) == recorded:
                raise RestartError('recorded native PID/start witness is still alive: ' + session_label(row))
            native = row.get('native_uuid') or str(row.get('execution_id', '')).removeprefix('cc-')
            if native and not lock_free(self.root / native / 'owner.lock'):
                raise RestartError('native UUID owner lock remains held: ' + session_label(row))

    def continue_service(self, *, recovering=False):
        from . import accounts
        from .restart_state import lock_free
        recipe = self.transition['recipe']
        old = recipe['old_host']
        if self.witness(old['host_pid']) == old['process_witness']:
            raise RestartError('recorded old host still alive; fresh stop proof is required')
        self.native_absence()
        self.observe_effect('host_signal', {'host_pid': old['host_pid'], 'process_witness': old['process_witness']},
            {'old_witness_absent': True, 'process_witness': old['process_witness'],
             'signal_issuance': 'see retained issued/uncertain events; absence does not prove a syscall'})
        self.report['phases']['host_exit_observed'] = True
        self.patch(fields={'phase': 'old_host_exited', 'old_host_exit_observed': True}, essential=False)
        truth, actual = accounts.selection_truth(recipe['old_selector'], recipe['destination'],
                                                self.operation_id, recipe['target_epoch'])
        if truth == 'other':
            raise RestartError('selector/native identity is changed or superseded; no automatic old fallback or overwrite')
        direction = self.transition['direction']
        if recipe['destination'] is not None and truth == 'old' and (recovering or direction == 'restore_old'):
            direction = 'restore_old'
            self.patch(fields={'direction': direction, 'requested_transition': 'failed'}, essential=False)
        elif truth == 'destination':
            direction = 'continue_destination'
            self.patch(fields={'direction': direction}, essential=False)
        if truth == 'old' and recipe['destination'] is not None and direction != 'restore_old':
            if not lock_free(self.root / 'host.lock'):
                raise RestartError('replacement ownership already present before selection; recover this operation to observe/restore it')
            try:
                endpoint = self.client.call('health')
            except (OSError, RuntimeError):
                endpoint = None
            if endpoint:
                raise RestartError('an endpoint already answers before selection; preserve it and reconcile the same operation')
            destination = recipe['destination']
            self.patch(fields={'phase': 'applying_target'})
            if destination.get('config_dir'):
                self.report['phases']['shared_stores'] = self.effect('apply_target', destination,
                    lambda: accounts.prepare_shared_directory(destination['config_dir']))
            self.patch(fields={'phase': 'selector_commit_intended'})
            try:
                self.effect('selector_commit', {**destination, 'epoch': recipe['target_epoch'],
                    'operation_id': self.operation_id}, lambda: accounts.commit_selection(destination,
                        expected_previous=recipe['old_selector'], operation_id=self.operation_id,
                        target_epoch=recipe['target_epoch']))
            except Exception as error:
                self.fail(error)
                truth, actual = accounts.selection_truth(recipe['old_selector'], destination,
                                                        self.operation_id, recipe['target_epoch'])
                if truth != 'destination':
                    raise
            truth, actual = accounts.selection_truth(recipe['old_selector'], destination,
                                                    self.operation_id, recipe['target_epoch'])
            if truth != 'destination':
                raise RestartError('selector readback did not verify the intended operation/epoch/identity')
            direction = 'continue_destination'
            self.patch(fields={'phase': 'selector_observed', 'direction': direction}, essential=False)
        if truth == 'destination':
            try:
                accounts.commit_selection(recipe['destination'], expected_previous=recipe['old_selector'],
                    operation_id=self.operation_id, target_epoch=recipe['target_epoch'])
                self.patch(fields={'selector_durability': 'confirmed'}, essential=False)
                self.observe_effect('selector_commit', {**recipe['destination'], 'epoch': recipe['target_epoch'],
                    'operation_id': self.operation_id}, {'selector': actual, 'durability': 'confirmed'})
            except Exception as error:
                # A persisted destination with interrupted fsync is NOT old. Keep the
                # durability uncertainty; do not kill/withhold an otherwise usable service.
                self.fail(error)
                self.patch(fields={'selector_durability': 'unconfirmed'}, essential=False)
        account = actual['account']
        self.report['account'] = account
        self.patch(fields={'start_account': account, 'selector_observed': actual}, essential=False)
        self.observe_or_start(direction)

    def observe_or_start(self, direction):
        from .restart_state import lock_free, service_evidence, finalize_service
        from .accounts import selector_snapshot
        recipe = self.transition['recipe']
        token = self.transition.get('start_token') or recipe['start_tokens'][direction]
        candidate = self.transition.get('candidate') or {}
        launched = False
        observing_since, next_diagnostic = self.clock(), self.clock()
        while True:
            receipt = None
            try:
                receipt = json.loads((self.root / 'host-start-receipt.json').read_text(encoding='utf-8'))
                if (receipt.get('state_root') != str(self.root) or
                        receipt.get('socket_path') != socket_path_of(self.client)):
                    receipt = None
            except FileNotFoundError:
                pass
            if receipt and receipt.get('process_witness'):
                live = self.witness(receipt.get('host_pid')) == receipt['process_witness']
                if live:
                    candidate = {k: receipt.get(k) for k in ('host_pid', 'process_witness', 'start_token', 'operation_id', 'account')}
                    self.patch(fields={'candidate': candidate}, essential=False)
                    if receipt.get('phase') == 'initialization_failed':
                        self.patch(fields={'service': {'state': 'failed', 'error': receipt.get('error'),
                            'host_pid': receipt['host_pid'], 'process_witness': receipt['process_witness']}}, essential=False)
                        raise RestartError('replacement reports terminal initialization failure; live ownership retained, recover this ID')
            try:
                health = self.client.call('health')
            except (OSError, RuntimeError):
                health = None
            if health:
                if not self.storage_errors:
                    self.refresh()
                evidence = service_evidence(self.transition, health, selector_snapshot(), self.witness,
                                            source='external_coordinator')
                if evidence:
                    try:
                        self.client.call('list', all=True, summary=True)
                    except Exception as error:
                        self.patch(fields={'service': {'state': 'unconfirmed', 'error': type(error).__name__}}, essential=False)
                        raise RestartError('replacement is alive but required list/store service is unavailable') from error
                    self.observed_service = evidence
                    self.report['phases']['host_started'] = {'pid': health['host_pid'],
                        'process_witness': health['process_witness'], 'state_root': str(self.root)}
                    self.patch(fields={'phases': self.report['phases']}, essential=False)
                    try:
                        self.record = finalize_service(self.owner.store, self.operation_id, health,
                            selector_snapshot(), self.witness, source='external_coordinator')
                    except Exception as error:
                        self.storage_errors.append(str(error))
                        self.patch(fields={'service': evidence, 'physical_active': False,
                            'phase': 'service_ready', 'requested_transition': 'failed' if direction == 'restore_old' else 'succeeded'}, essential=False)
                    return
                self.patch(fields={'service': {'state': 'unconfirmed', 'host_pid': health.get('host_pid'),
                    'process_witness': health.get('process_witness'),
                    'error': 'endpoint namespace/witness/selection/start binding is unconfirmed'}}, essential=False)
                raise RestartError('alive endpoint has unconfirmed/wrong namespace, witness or selection; not replaced')
            free = lock_free(self.root / 'host.lock')
            witness = candidate.get('process_witness')
            alive = bool(witness and self.witness(candidate.get('host_pid')) == witness)
            if not alive and free:
                if launched:
                    self.patch(fields={'service': {'state': 'failed', 'host_pid': candidate.get('host_pid'),
                        'process_witness': witness, 'evidence': {'candidate_absent': True, 'host_lock_free': True}}}, essential=False)
                    raise RestartError('replacement candidate is proved absent and host.lock is free; recover this ID after fixing startup')
                # A previous failed start is tried only on an explicit recover pass, after
                # the same absence proofs. No normal-command blind permanent-failure loop.
                self.native_absence()
                attempt = int(self.transition.get('start_attempt', 0)) + 1
                if attempt > 1:
                    token = str(uuid.uuid5(uuid.NAMESPACE_URL, recipe['start_tokens'][direction] + ':' + str(attempt)))
                self.patch(fields={'phase': 'start_intended', 'start_token': token,
                                   'start_attempt': attempt}, essential=False)
                call = self.starter or (lambda: start_host_fresh(socket_path_of(self.client), self.root,
                    operation_id=self.operation_id, start_token=token))
                launched = True
                try:
                    answer = self.effect('start_host', {'token': token, 'account': self.transition['start_account']},
                        call, attempt=attempt, essential=False) or {}
                    candidate = {'host_pid': answer.get('host_pid'),
                        'process_witness': answer.get('process_witness') or self.witness(answer.get('host_pid')),
                        'start_token': token, 'operation_id': self.operation_id,
                        'account': answer.get('account')}
                    self.patch(fields={'candidate': candidate}, essential=False)
                except Exception as error:
                    # Popen or the starter reply may already have succeeded. Reobserve lock,
                    # child receipt and ordinary endpoint BEFORE calling this a dead start.
                    if lock_free(self.root / 'host.lock'):
                        self.patch(fields={'service': {'state': 'failed', 'error': str(error),
                            'evidence': {'starter_failed': True, 'host_lock_free': True}}}, essential=False)
                        raise
                continue
            self.patch(fields={'phase': 'starting', 'service': {'state': 'starting',
                'account': self.transition['start_account'], 'host_pid': candidate.get('host_pid'),
                'process_witness': witness, 'error': None,
                'reason': 'candidate initializing/unbound' if alive else 'host.lock held; ownership unconfirmed'}}, essential=False)
            if self.clock() >= next_diagnostic:
                print(f'Operation {self.operation_id}: replacement starting, not ready yet '
                      f'({self.clock() - observing_since:g}s observed); no competing start.', file=sys.stderr, flush=True)
                next_diagnostic = self.clock() + 30  # diagnostics only, never an ownership/startup deadline
            self.sleep(self.interval)  # diagnostic polling only; no elapsed startup/TTL verdict

    def result(self):
        from .restart_state import envelope
        observed_direction = self.transition['direction']
        self.refresh()
        episode = self.owner.store.get('quota_checks', '', self.transition['recipe']['episode_id'])
        result = envelope(self.record, episode=episode)
        result.update(self.report)
        result['observer_recovery'] = {'operation_id': self.operation_id, 'state_root': str(self.root)}
        result['account'] = self.transition.get('service', {}).get('account') or self.report.get('account')
        if self.storage_errors:
            result['finalization_pending'] = self.storage_errors
            result['errors'] += self.volatile_errors
            if self.observed_service and result['service'].get('state') != 'ready':
                result['service'] = {**self.observed_service, 'durable': False}
                result['direction'] = observed_direction
                if observed_direction == 'restore_old':
                    result['requested_transition'] = 'failed'
                    result['outcome_durable'] = False
        return result


def _guarded_restart(client, *, close_idle=False, timeout=DEFAULT_TIMEOUT, state_root=None,
                     witness_fn=None, clock=time.monotonic, sleep=time.sleep,
                     interval=DEFAULT_POLL_SECONDS, kill_fn=None, starter=None, account=None,
                       notify_manager=None, control=None, operation_id=None, lifecycle=None, initial=None) -> dict:
    """Existing native settlement/old-host stop proof, integrated with the durable lifecycle.

    ``witness_fn``/``clock``/``sleep``/``kill_fn``/``starter`` are injection seams for the inert
    tests; production callers leave them alone. ``starter`` defaults to
    ``start_host_fresh(socket, state_root)`` on the inspected endpoint. ``control`` is the pane's
    optional cross-process gate, claimed after recipe commit and before the first stopping
    effect. Observer inventory/prepare holds use the ordinary temporary settlement classification,
    never a relaxed observer retirement proof. Every outcome retains cumulative partial receipts; replacement/recovery goes
    through the same phase-aware continuation, never a second stop engine.
    """
    witness_fn = witness_fn or _posix_witness
    kill_fn = kill_fn or _sigterm
    socket_path = socket_path_of(client)
    report = lifecycle.report

    try:
        rows = list(client.call("list", all=True) or [])
    except Exception as error:  # transport only; nothing has been read or touched yet
        raise RestartRefused(
            f"no reachable host at {socket_path!r}; refusing to start a second host blindly - "
            f"nothing was signalled. If the host is genuinely stopped, start it explicitly with "
            f"claude_host.sh start ({type(error).__name__}: {error})") from error

    try:
        recipient, target = None, None
        if notify_manager:
            recipient = 'cc-' + str(uuid.UUID(notify_manager.removeprefix('cc-')))
            target = next((row for row in rows if row.get('execution_id') == recipient), None)
            if account is None or target is None or target.get('role') != 'Manager':
                raise RestartRefused('--notify-manager needs an account and a registered --resume Manager')
        initial = initial or client.call('health')
        if lifecycle.record is None:
            lifecycle.admit(initial, rows)
        lifecycle.observe_native_exits(rows)
        unsettled, historical = partition_unsettled(rows)
        settled = []
        if unsettled and close_idle and (control is None or all(reasons == [UNKNOWN_LEVEL] for _, reasons in unsettled)):
            # Finished work never blocks a restart: a session held only by an unknown task level
            # is settled by the host's native readback (or, failing that, its process-tree proof).
            lifecycle.before_effects()
            settled = settle_unknown_levels(client, unsettled, witness_fn=witness_fn,
                                            timeout=timeout, clock=clock, sleep=sleep,
                                            interval=interval, lifecycle=lifecycle)
            if settled:
                report["phases"]["settled_unknown_level"] = settled
                stopped = [row["session"] for row in settled if row["outcome"] == "stopped"]
                if stopped:
                    report["phases"]["stopped_on_process_tree_proof"] = stopped
                rows = list(client.call("list", all=True) or [])
                unsettled, historical = partition_unsettled(rows)
        if account is not None and close_idle:
            capabilities = client.call('health') or {}
            if 'prepare_observer_switch' in (capabilities.get('implemented') or []):
                inventory = client.call('observer_switch_inventory')
                if inventory.get('blockers'):
                    raise RestartRefused.settlement_hold(observer_blocker_text(inventory['blockers']),
                        (row.get('reasons') or [] for row in inventory['blockers']))
                lifecycle.before_effects()
                report['observer_recovery'] = {'operation_id': operation_id, 'state_root': capabilities['state_root']}
                prepared = lifecycle.effect('prepare_observers', {'operation_id': operation_id},
                    lambda: client.call('prepare_observer_switch', operation_id=operation_id,
                        requested_account=account['label'], authority='explicit account switch ' + operation_id))
                if prepared.get('outcome') != 'prepared':
                    if prepared.get('outcome') == 'held':
                        raise RestartRefused.settlement_hold(observer_blocker_text(prepared.get('blockers') or []),
                            (row.get('reasons') or [] for row in prepared.get('blockers') or []))
                    raise RestartRefused(observer_blocker_text(prepared.get('blockers') or []))
                pending = prepared.get('candidates') or []
                for candidate in pending:
                    answer = lifecycle.effect('retire_observer', candidate,
                        lambda: client.call('retire_observer_owner', operation_id=operation_id,
                            **candidate, authority='explicit account switch ' + operation_id))
                    if answer.get('outcome') == 'held':
                        raise RestartRefused(f"{candidate['execution_id']}: {answer.get('reason')}")
                def observer_owners_ended():
                    statuses = [client.call('observer_retirement', operation_id=operation_id, **candidate)
                                for candidate in pending]
                    report['phases']['observer_retirements'] = statuses
                    for candidate, status in zip(pending, statuses):
                        if status.get('outcome') == 'ended':
                            lifecycle.observe_effect('retire_observer', candidate, {'retirement': status})
                    if any(row.get('outcome') == 'uncertain' for row in statuses):
                        raise RestartRefused('observer stop outcome is uncertain; exact receipts retained in recovery index ' + operation_id)
                    return all(row.get('outcome') == 'ended' for row in statuses)
                poll_until(observer_owners_ended, timeout=timeout, clock=clock, sleep=sleep, interval=interval,
                    error=RestartRefused, message='observer shutdown still pending; real stop receipts retained in recovery index ' + operation_id)
                rows = list(client.call('list', all=True) or [])
                unsettled, historical = partition_unsettled(rows)
        if unsettled:
            detail = ("refusing to restart: unsettled work must be reviewed first - " + "; ".join(
                f"{session_name(record)}: {', '.join(reasons)}" + task_detail(record) for record, reasons in unsettled))
            if settled:
                detail += ". " + settle_note(settled)
            elif not close_idle and any(reasons == [UNKNOWN_LEVEL] for _, reasons in unsettled):
                detail += (". With --close-idle the host first settles a session held only by an "
                           "unknown background task level (native readback, then the process-tree proof)")
            if historical:
                detail += ". " + historical_note(historical)
            raise RestartRefused.settlement_hold(detail, (reasons for _, reasons in unsettled))

        if initial.get("supervision_error"):
            # Reported, never a hold: a restart rebuilds supervision from the durable records, and the
            # failure stays in health/the incident feed. Refusing would leave only an off-doctrine kill.
            report["phases"]["supervision_error_reported"] = {
                "error": initial["supervision_error"], "detail": initial.get("supervision_error_detail")}
        if state_root is not None:
            observed = initial.get("state_root")
            if observed and str(Path(observed)) != str(Path(state_root)):
                raise RestartRefused(
                    f"--state-root {state_root} is not the inspected host's state root {observed}")
        report["phases"]["inspected"] = {
            "host_pid": initial.get("host_pid"),
            "state_root": initial.get("state_root"),
            "sessions": [session_summary(record) for record in rows],
        }

        holds = [record for record in rows if not owner_proved_dead(record)]
        if holds and not close_idle:
            detail = ("refusing to restart: native owner(s) still present - " + "; ".join(
                f"{session_label(record)}: {owner_hold(record)}" for record in holds) +
                ". Pass --close-idle ONLY if each session is idle and should end: it is closed "
                "through the host's own close_input and its exit is awaited, never killed.")
            if historical:
                detail += " " + historical_note(historical)
            raise RestartRefused(detail)
        closed = []
        for record in holds:
            state = record.get("state")
            if state in ("input_closing", "exited_draining"):
                continue  # already closing/draining; wait for the proved exit below
            if (state not in ("ready", "launch_blocked") or record.get("owner") == "gone"
                    or (state == "ready" and not record.get("initialized"))):
                raise RestartRefused(
                    f"refusing to close or signal {session_label(record)}: {state!r} is not an idle "
                    "ready session; review it before restarting")
            if not pid_witness_alive(record, witness_fn):
                raise RestartRefused(
                    f"refusing to close {session_label(record)}: its recorded PID/start witness is "
                    "not verifiably alive; review it before restarting")
            lifecycle.before_effects()
            lifecycle.effect('close_native', native_identity(record), lambda: client.call(
                'close_input', execution_id=record.get('execution_id'), generation=record.get('generation')))
            closed.append(session_label(record))
        report["phases"]["closed_idle"] = list(dict.fromkeys([*report['phases'].get('closed_idle', []), *closed]))

        if holds:
            def owners_proved_gone():
                current = client.call("list", all=True) or []
                return all(owner_proved_dead(record) and not pid_witness_alive(record, witness_fn)
                           for record in current)

            poll_until(
                owners_proved_gone, timeout=timeout, clock=clock, sleep=sleep, interval=interval,
                error=lambda message: RestartRefused(message, classification='settlement', safe_pre_stop_retry=True),
                message=(f"native owner(s) did not reach a proved exit within {timeout:g}s; nothing "
                         "was signalled - inspect receipts/state (a drain may still be settling; "
                         "never kill it)"))

        # Final guard: fresh reads immediately before the signal.
        final_rows = list(client.call("list", all=True) or [])
        lifecycle.observe_native_exits(final_rows)
        late_unsettled, late_historical = partition_unsettled(final_rows)
        if late_unsettled:
            detail = ('work became unsettled during restart; nothing was signalled - ' + '; '.join(
                f'{session_label(record)}: {", ".join(reasons)}'
                for record, reasons in late_unsettled))
            if late_historical:
                detail += ". " + historical_note(late_historical)
            raise RestartRefused(detail)
        advisories = [{"session": session_label(record), "markers": settlement_advisories(record)}
                      for record in final_rows if settlement_advisories(record)]
        if advisories:
            report["phases"]["reported_not_blocking"] = advisories
        if late_historical:
            report["phases"]["historical_records"] = [
                {"session": session_label(record), "markers": reasons}
                for record, reasons in late_historical]
        remaining = [record for record in final_rows if not owner_proved_dead(record)]
        still_alive = [record for record in final_rows if pid_witness_alive(record, witness_fn)]
        if remaining or still_alive:
            raise RestartRefused(
                "ownership changed while restarting; nothing was signalled - " + "; ".join(
                    f"{session_label(record)}: {owner_hold(record)}"
                    for record in (remaining or still_alive)))
        lifecycle.prove_native_absence(final_rows)  # unreadable witness is not observed absence
        final = client.call("health")
        if final.get("supervision_error"):
            report["phases"]["supervision_error_reported"] = {
                "error": final["supervision_error"], "detail": final.get("supervision_error_detail")}
        if (final.get("host_pid") != initial.get("host_pid")
                or final.get("process_witness") != initial.get("process_witness")):
            raise RestartRefused(
                "the host changed between inspection and the signal; refusing to signal an "
                "unverified process")
        host_pid = final.get("host_pid")
        if (not host_pid or not final.get("process_witness")
                or witness_fn(host_pid) != final.get("process_witness")):
            raise RestartRefused(
                "cannot verify the live host's PID/start witness; refusing to signal it")
        lifecycle.before_effects()
        observations = [row for row in lifecycle.owner.store.records('observations')
            if row.get('retirement', {}).get('operation_id') == operation_id]
        lifecycle.record = lifecycle.owner.update(operation_id, observations=observations)
        lifecycle.patch(fields={'phase': 'host_stop_intended', 'host_stop_intended': True,
            'native_exit_proofs': [{**native_identity(row), 'owner': row.get('owner'), 'state': row.get('state')}
                                   for row in final_rows], 'phases': report['phases']})
    except RestartRefused as error:
        error.report = report
        if lifecycle.record:
            lifecycle.patch(fields={'phases': report['phases']}, essential=False)
            finish_observer_recovery({**report, 'observer_recovery': {'operation_id': operation_id, 'state_root': str(state_root)}},
                                     outcome='refused', failure=str(error), client=client)
        raise
    except Exception as error:
        if lifecycle.record:
            lifecycle.patch(fields={'phases': report['phases']}, essential=False)
            finish_observer_recovery({**report, 'observer_recovery': {'operation_id': operation_id, 'state_root': str(state_root)}},
                outcome='refused', failure=f'{type(error).__name__}: {error}', client=client)
        raise RestartRefused(
            f"restart refused before any signal: {type(error).__name__}: {error}; nothing was "
            "signalled") from error
    except BaseException as error:
        if lifecycle.record:
            lifecycle.patch(fields={'phases': report['phases']}, essential=False)
            finish_observer_recovery({**report, 'observer_recovery': {'operation_id': operation_id, 'state_root': str(state_root)}},
                outcome='interrupted', failure=type(error).__name__, client=client)
        raise

    try:
        lifecycle.effect('host_signal', {'host_pid': host_pid, 'process_witness': final['process_witness']},
                         lambda: kill_fn(host_pid))
        report["phases"]["host_sigterm"] = {"pid": host_pid, "process_witness": final["process_witness"]}
        poll_until(
            lambda: witness_fn(host_pid) != final["process_witness"], timeout=timeout, clock=clock,
            sleep=sleep, interval=interval, error=RestartError,
            message=(f"host {host_pid} was sent SIGTERM but its PID/start witness was still present "
                     f"after {timeout:g}s; refusing to start a replacement - inspect it"))
        report["phases"]["host_exit_observed"] = True
        lifecycle.observe_effect('host_signal', {'host_pid': host_pid, 'process_witness': final['process_witness']},
            {'old_witness_absent': True, 'process_witness': final['process_witness']})
        lifecycle.patch(fields={'phases': report['phases']}, essential=False)
        lifecycle.continue_service()
    except Exception as error:
        problem = error if isinstance(error, RestartError) else RestartError(
            f"post-stop phase failed: {type(error).__name__}: {error}; recover operation {operation_id}")
        problem.report = report
        if problem is error:
            raise
        raise problem from error
    return lifecycle.result()


def task_detail(record):
    return ''.join(f"; task {task_id} run {task.get('run_id', '?')} ({task.get('task_type', 'unknown type')}): "
                   + str(task.get('description') or 'full task input needs inspection')
                   for task_id, task in (record.get('background_tasks') or {}).items())


def observer_blocker_text(blockers):
    return 'account switch held by: ' + '; '.join(
        f"{row['execution_id']} {row.get('title') or ''}: " + ', '.join(row.get('reasons') or []) +
        '; ' + str(row.get('observer_reason') or 'state changed during admission') +
        ''.join(f"; task {task['task_id']} run {task['run_id']} [{task['kind']}]: " +
                str(task.get('reason') or task.get('purpose') or 'observer') for task in row.get('tasks') or [])
        for row in blockers)


def finish_observer_recovery(report, *, outcome, failure=None, client=None):
    """Merge exact obligations; canonical readiness alone schedules new lifecycle episodes.

    Legacy reports remain readable. This function releases the old host's observer fence,
    but cannot turn a phase word or a failed/no-service result into ready/delivered.
    """
    recovery = report.get('observer_recovery')
    if not recovery:
        return
    from .store import Store
    from .observer_recovery import ObserverRecovery
    try:
        if client is not None and outcome != 'complete':
            try:
                client.call('finish_observer_switch', operation_id=recovery['operation_id'], outcome=outcome, failure=failure)
            except Exception:
                pass  # stopped host or lost reply: durable local store below is independent
        store = Store(recovery['state_root'])
        try:
            observations = [row for row in store.records('observations')
                if row.get('retirement', {}).get('operation_id') == recovery['operation_id']]
            manager = ObserverRecovery(store)
            record = manager.read(recovery['operation_id'])
            if record and record.get('transition'):
                manager.update(recovery['operation_id'], observations=observations)
                manager.transition_update(recovery['operation_id'], fields={'phases': report.get('phases') or {}})
                return
            manager.update(recovery['operation_id'], observations=observations, outcome=outcome,
                failure=failure, account=report.get('account'), phases=report.get('phases'))
            # One semantic notice per operation, with a stable caller UUID and current-Manager routing.
            # A failed/partial stop remains in the index too; no report waits for Workflowy.
            key = 'observer-switch:' + recovery['operation_id']
            notice = store.get('quota_checks', '', key)
            if notice is None:
                notice = {'episode_id': key, 'kind': 'observer_recovery', 'operation_id': recovery['operation_id'],
                    'detected_at': time.time(), 'message_uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                    'recipient': None, 'status': 'pending_switch', 'due_at': None}
            if outcome == 'complete' and notice['status'] == 'pending_switch':
                notice.update(status='scheduled', due_at=time.time())
            store.put('quota_checks', '', notice, key)
            recovery['notice_episode'] = key
        finally:
            store.db.close()
    except Exception as error:
        recovery['finalization_pending'] = type(error).__name__


# @beacon[
#   id=opc@account_switch_notice,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Compatibility adapter: canonical operations use their one retained lifecycle episode and shared factual finalizer; previously admitted legacy account-switch episodes keep their original exact UUID/receipt and delivery owner.,
# ]
def switch_notice(report, *, outcome, failure=None):
    """No new parallel notice: canonical lifecycle or its already-admitted legacy episode."""
    recovery = report.get('observer_recovery')
    if recovery:
        from .restart_state import read_operations
        record = read_operations(recovery['state_root'], recovery['operation_id'])
        if record and record.get('transition'):
            return report.get('notice') or {'status': 'pending_switch',
                'episode_id': record['transition']['recipe']['episode_id']}
    from .store import Store
    from .quota_wake import record_account_switch_notice
    notice = report['manager_notice']
    account, quota = None, None
    if outcome != 'pending':
        try:
            from .accounts import selected_account
            account = selected_account()
            from oc_usage import claude_usage
            quota = claude_usage(account=account)
        except Exception as error:
            quota = {'ok': False, 'error': f'quota read failed ({type(error).__name__})'}
    store = Store(notice['state_root'])
    try:
        episode = record_account_switch_notice(store, notice_id=notice['notice_id'],
            recipient=notice['recipient'], requested_account=notice['requested_account'],
            outcome=outcome, report=report, account=({k: account.get(k) for k in ('label', 'identity', 'epoch')}
                                                  if account else None),
            quota=({k: quota[k] for k in ('ok', 'windows', 'error') if k in quota} if quota else None), failure=failure)
        return {**notice, **{k: episode.get(k) for k in ('message_uuid', 'outcome', 'status')}}
    finally:
        store.db.close()


def run_followup(client, *, resume=None, launch_manager=False, model=None, effort=None,
                 fresh_uuid=None, strict=False) -> dict:
    """Ask the (fresh) host for one Manager resume or launch through the manager launcher.

    Exact resume uses Host.reconnect's serialized same-UUID snapshot path, not repeated raw
    launch. Fresh launch uses its durable preallocated UUID. Neither a request nor a later
    generation claims readiness; complete_followup observes that separately. Neither sends.
    """
    if resume and launch_manager:
        raise ValueError("--resume and --launch-manager are mutually exclusive")
    if resume:
        import claude_manager_headless as launcher
        eid = 'cc-' + launcher.exact_uuid(resume)
        record = client.call('state', execution_id=eid)
        launcher.stored_prompt(record)
        answer = client.call('reconnect', execution_id=eid, generation=record['generation'])
        return {'status': 'resume_requested', 'sent': False, 'record': answer}
    if launch_manager:
        if not model or not effort:
            raise ValueError("--launch-manager requires explicit --model and --effort")
        import claude_manager_headless as launcher
        if fresh_uuid:
            try:
                registered = client.call('state', execution_id='cc-' + fresh_uuid)
            except ValueError:
                registered = None
            if registered is not None:
                launcher.stored_prompt(registered)
                return {'status': 'launch_observed_or_reconnected', 'sent': False,
                    'record': client.call('reconnect', execution_id=registered['execution_id'],
                                          generation=registered['generation'])}
        return launcher.run_launch(client, argparse.Namespace(
            socket_path=None, uuid=fresh_uuid, cwd=None, model=model, effort=effort, strict=strict, agent=None,
            program=None, socket_handle=None, parent=None, workflowy_url=None))
    raise ValueError("the follow-up requires --resume <uuid> or --launch-manager")


def observed_resume(client, notice):
    """Observe exact native initialized/writable evidence, not merely a later generation."""
    from .restart_state import manager_evidence
    try:
        record = client.call('state', execution_id=notice['recipient'])
        generation = record.get('generation')
        if (isinstance(generation, int) and generation >= notice.get('previous_generation', 0)
                and manager_evidence(record, _posix_witness)['state'] == 'ready'):
            return {'status': 'resume_observed_after_lost_reply', 'sent': False,
                    'execution_id': notice['recipient'], 'generation': generation}
    except Exception:
        pass  # unresolved is not success; the failure notice asks for current-state reconciliation
    return None


def complete_followup(client, root, operation_id, *, sleep=time.sleep, witness_fn=None):
    """After releasing the physical lock: restore only the stored Manager policy, never a turn.

    Current-Manager notices own their reconnect. Direct exact/fresh policies observe one
    admitted generation and native readiness; recover touches no healthy service/selector.
    """
    from .restart_state import journal, manager_evidence, envelope, finalize_service
    from .accounts import selector_snapshot
    witness_fn = witness_fn or _posix_witness
    with journal(root) as owner:
        record = owner.read(operation_id)
        transition = record['transition']
        policy = transition['manager']['policy']
        if policy in ('none', 'current') or transition['manager']['state'] == 'ready':
            return envelope(record, episode=owner.store.get('quota_checks', '', transition['recipe']['episode_id']))
        request = transition['request']['manager']
        eid = ('cc-' + transition['recipe']['fresh_manager_uuid'] if policy == 'fresh' else
               'cc-' + str(uuid.UUID(request['resume'].removeprefix('cc-'))))
        try:
            try:
                state = client.call('state', execution_id=eid)
            except ValueError:
                if policy != 'fresh':
                    raise
                state = None  # exact unknown-ID response is definite non-admission, not lost IPC
            active = state and state.get('owner') == 'host' and state.get('state') not in ('exited', 'exited_draining')
            if not active:
                owner.transition_update(operation_id, fields={'manager': {
                    'state': 'requested', 'execution_id': eid, 'previous_generation': (state or {}).get('generation'),
                    'launch_intended': True}, 'phase': 'followup_pending'})
                try:
                    run_followup(client, resume=request.get('resume') if policy == 'exact' else None,
                        launch_manager=policy == 'fresh', model=request.get('model'), effort=request.get('effort'),
                        fresh_uuid=transition['recipe']['fresh_manager_uuid'], strict=request.get('strict', False))
                except Exception as lost_reply:
                    # Reconnect/launch can succeed before reply. Only observe that exact UUID;
                    # never allocate a second fresh persona or treat an initializing g3 as ready.
                    owner.transition_update(operation_id, error={'phase': 'followup_pending',
                        'type': type(lost_reply).__name__, 'message': str(lost_reply)})
                    state = client.call('state', execution_id=eid)
                    if (state.get('owner') != 'host' or state.get('state') in ('exited', 'launch_failed')):
                        raise
            while True:
                state = client.call('state', execution_id=eid)
                evidence = manager_evidence(state, witness_fn)
                owner.transition_update(operation_id, fields={'manager': evidence})
                if evidence['state'] == 'failed':
                    raise RestartError(evidence['error'])
                if evidence['state'] == 'ready':
                    owner.transition_update(operation_id, fields={'phase': 'settled'})
                    break
                sleep(DEFAULT_POLL_SECONDS)
        except Exception as error:
            observed = None
            try:
                observed = manager_evidence(client.call('state', execution_id=eid), witness_fn)
            except Exception:
                pass
            ready = observed and observed['state'] == 'ready'
            owner.transition_update(operation_id, fields={'manager': observed if ready else {
                'state': 'failed', 'execution_id': eid, 'error': str(error)},
                'phase': 'settled' if ready else 'followup_pending'},
                error={'phase': 'followup_pending', 'type': type(error).__name__, 'message': str(error)})
            # One narrow labelled post-stop failure episode, independent of direct follow-up.
            try:
                finalize_service(owner.store, operation_id, client.call('health'), selector_snapshot(), witness_fn,
                                 source='external_coordinator')
            except Exception:
                pass
        record = owner.read(operation_id)
        return envelope(record, episode=owner.store.get('quota_checks', '', transition['recipe']['episode_id']))


def open_client(args):
    """Open the local host client for the chosen endpoint; imported lazily (POSIX-only host)."""
    from .client import Client
    return Client(args.socket_path) if args.socket_path else Client()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude_restart.sh", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--socket-path", type=Path, default=None,
                        help="operator-only test override for the host endpoint; default is the host's own")
    parser.add_argument("--state-root", type=Path, default=None,
                        help="operator-only test override for the state root; must match the inspected host")
    parser.add_argument("--close-idle", action="store_true",
                        help="close settled, ready sessions through close_input before stopping the host "
                             "(a session held only by an unknown task level is first settled by the host's "
                             "native readback, or stopped on its process-tree proof); without it any "
                             "native owner refuses the restart")
    parser.add_argument('--strict', action='store_true',
                        help='reject residual OpenCode warnings in the composition preflight')
    parser.add_argument('--account', choices=('personal', 'work'), default=None,
                         help='select an enrolled native Max login only after the old host stops')
    parser.add_argument('--notify-manager', action='store_true',
                         help='with --account and --resume: one labelled switch outcome message wakes the Manager')
    parser.add_argument('--_control', type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument('--operation-id', '--_operation-id', dest='operation_id', default=None,
                        help='retained identity for this exact request (allocated when omitted)')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--status', metavar='ID', help='read-only operation/evidence; no live host required')
    mode.add_argument('--operations', action='store_true', help='list incomplete/recoverable IDs without an age filter')
    mode.add_argument('--recover', metavar='ID', help='continue this recorded recipe, not a new restart')
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"pre-stop settlement/exit observation seconds (default {DEFAULT_TIMEOUT:g}); "
                             "viable replacement startup has no elapsed failure deadline")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--resume", metavar="UUID", default=None,
                       help="after a successful restart, resume the host-owned exact UUID with its stored "
                             "Manager snapshot; nothing is sent unless --notify-manager is requested")
    group.add_argument("--launch-manager", action="store_true",
                       help="after a successful restart, compose and request one fresh Manager launch; "
                            "nothing is sent")
    parser.add_argument("--model", default=None,
                        help="with --launch-manager: explicit native model (e.g. claude-opus-5-5)")
    parser.add_argument("--effort", default=None,
                        help="with --launch-manager: explicit native effort (e.g. high)")
    return parser


# @beacon[
#   id=opc@claude_restart_main,
#   slice_labels=opc-basic,opc-claude-restart,
#   kind=ast,
#   comment=One command family for guarded restart/switch and same-ID recovery/status while absent or starting; emits typed claude-restart/1 facts on every outcome and restores recorded Manager policy only after releasing the physical transition lock.,
# ]
def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.status or args.operations or args.recover) and any((args.account, args.resume, args.launch_manager,
            args.notify_manager, args.close_idle, args.model, args.effort, args.operation_id, args._control, args.strict)):
        parser.error('status/operations/recover use the stored request; do not resupply transition/follow-up options')
    if args.notify_manager and not (args.account and args.resume):
        parser.error('--notify-manager requires --account and --resume')
    if (args.model or args.effort) and not args.launch_manager:
        parser.error("--model/--effort require --launch-manager")
    if args.launch_manager and not (args.model and args.effort):
        parser.error("--launch-manager requires explicit --model and --effort")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")

    from .restart_state import read_operations, read_receipt, envelope, lock_free, service_evidence
    from .host import STATE_ROOT
    root = args.state_root or STATE_ROOT
    if args.status or args.operations:
        try:
            if args.status:
                record = read_operations(root, args.status)
                result = read_receipt(root, args.status)
                if result is None:
                    raise ValueError('unknown restart operation')
                from .accounts import selector_snapshot
                try:
                    selector = selector_snapshot()
                    observation = {'selector': selector,
                        'host_lock_free': lock_free(Path(root) / 'host.lock', read_only=True)}
                    if record.get('transition'):
                        namespace = record['transition']['request']['namespace']
                        if args.socket_path and str(args.socket_path) != namespace['socket_path']:
                            raise ValueError('status socket override differs from recorded namespace')
                        args.socket_path = Path(namespace['socket_path'])
                        candidate = record['transition'].get('candidate') or {}
                        observation['candidate_witness'] = _posix_witness(candidate['host_pid']) if candidate.get('host_pid') else None
                        try:
                            reader = open_client(args)
                            health = reader.call('health')
                            reader.call('list', all=True, summary=True)
                            observation['service'] = service_evidence(record['transition'], health, selector,
                                _posix_witness, source='read_only_status')
                        except (OSError, RuntimeError):
                            observation['service'] = {'state': 'unbound_or_unavailable'}
                except Exception as error:
                    observation = {'error': type(error).__name__ + ': ' + str(error)}
                result['current_observation'] = observation
            else:
                receipts = [read_receipt(root, record['operation_id']) for record in read_operations(root)]
                result = {'schema': 'claude-restart/1', 'operations': [receipt for receipt in receipts
                    if receipt.get('service', {}).get('state') != 'ready' or
                    receipt.get('manager', {}).get('state') not in ('ready', 'not_requested') or
                    receipt.get('notice', {}).get('status') not in (None, 'not_requested', 'sent', 'deferred_by_operator', 'cancelled_by_operator') or
                    (receipt.get('publication', {}).get('enabled') and receipt['publication'].get('status') != 'confirmed')]}
            print(json.dumps(result, ensure_ascii=False))
            return 0  # query success is not operation success
        except Exception as error:
            print(f'{type(error).__name__}: {error}', file=sys.stderr)
            return ERROR_EXIT
    operation_id = args.recover or args.operation_id or str(uuid.uuid4())
    print('Operation ' + operation_id + '; use --status/--recover with this ID.', file=sys.stderr)
    policy = {'policy': 'fresh' if args.launch_manager else 'exact' if args.resume else 'none',
              'resume': args.resume, 'model': args.model, 'effort': args.effort, 'strict': args.strict}
    if args.recover:
        try:
            record = read_operations(root, operation_id)
            if not record or not record.get('transition'):
                raise ValueError('unknown/legacy operation: durable before-stop recipe unavailable')
            namespace = record['transition']['request']['namespace']
            if args.socket_path and str(args.socket_path) != namespace['socket_path']:
                raise ValueError('socket override differs from recorded recovery namespace')
            args.socket_path = Path(namespace['socket_path'])
        except Exception as error:
            print(json.dumps(unadmitted_receipt(operation_id, args, policy, error)))
            return ERROR_EXIT

    # Refuse bad follow-up choices BEFORE closing any idle session or signalling the host.
    if args.resume:
        import claude_manager_headless as launcher
        try:
            launcher.exact_uuid(args.resume)
        except ValueError as error:
            parser.error(str(error))
    if args.launch_manager:
        from .launch import _validated_model, _validated_effort
        try:
            _validated_model(args.model)
            _validated_effort(args.effort)
        except ValueError as error:
            parser.error(str(error))

    # Composition runs BEFORE host contact. Only --launch-manager composes a prompt, so only it (or an
    # explicit --strict) can be refused by a composition that fails outright (no frontmatter, or strict
    # warnings); doctrine drift is a loud warning, never a refusal. A plain restart or --resume (the
    # stored snapshot) composes nothing: its preflight only warns.
    prepared = None
    try:
        import claude_manager_headless as launcher
        if not args.recover:
            prepared = launcher.run_prepare(argparse.Namespace(out=None, strict=args.strict))
    except Exception as error:
        if args.launch_manager or args.strict:
            print(str(error), file=sys.stderr)  # preserve the composer's exact frontmatter/strict error
            print(json.dumps(unadmitted_receipt(operation_id, args, policy, error)))
            return REFUSED_EXIT
        print(f"WARNING: the Manager prompt does not compose ({error}); this restart composes no prompt, so it "
              "goes ahead. Fix it before the next fresh Manager launch.", file=sys.stderr)
        prepared = {"status": "not_composed", "error": str(error)}

    report, exit_code = None, 0
    try:
        client = open_client(args)
        report = guarded_restart(client, close_idle=args.close_idle, timeout=args.timeout,
                                 state_root=root if args.recover else args.state_root,
                                 **({'account': args.account} if args.account else {}),
                                 **({'notify_manager': args.resume} if args.notify_manager else {}),
                                 **({'control': RestartControl(args._control)} if args._control else {}),
                                 operation_id=operation_id, manager_policy=policy, recover=bool(args.recover))
        report['prompt_preflight'] = prepared
    except RestartRefused as error:
        report = getattr(error, 'report', None) or unadmitted_receipt(operation_id, args, policy, error)
        print(f'REFUSED - no host signal; retained partial effects matter: {error}', file=sys.stderr)
        exit_code = CANCELLED_EXIT if report.get('requested_transition') == 'cancelled' else REFUSED_EXIT
    except RestartError as error:
        report = getattr(error, 'report', None) or unadmitted_receipt(operation_id, args, policy, error)
        print(f'ERROR - recover the same operation {operation_id}: {error}', file=sys.stderr)
        exit_code = ERROR_EXIT
    except KeyboardInterrupt:
        try:
            report = envelope(read_operations(root, operation_id))
        except Exception:
            report = unadmitted_receipt(operation_id, args, policy, 'observer interrupted; admission unknown')
        print(json.dumps(report, ensure_ascii=False))
        return 130
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        report, exit_code = unadmitted_receipt(operation_id, args, policy, error), ERROR_EXIT
    if report.get('service', {}).get('state') == 'ready':
        try:
            actual_root = report['request']['namespace']['state_root']
            finalized = complete_followup(client, actual_root, operation_id)
            if report['service'].get('durable') is False and finalized.get('service', {}).get('state') != 'ready':
                # A read of the older durable row must not erase this invocation's exact
                # restoration/error observations merely because finalization storage failed.
                for key in ('service', 'direction', 'requested_transition', 'errors'):
                    finalized.pop(key, None)
            report.update(finalized)
            report['followup'] = {'status': report['manager']['state'], 'sent': False}
            if report['manager']['state'] == 'failed':
                report['followup_error'] = report['manager'].get('error')
                print('the host is ready, but the follow-up failed: ' + str(report['followup_error']), file=sys.stderr)
                exit_code = ERROR_EXIT
            if report['requested_transition'] == 'failed':
                exit_code = ERROR_EXIT
            if report.get('finalization_pending'):
                exit_code = ERROR_EXIT
        except Exception as error:
            report['finalization_pending'] = str(error)
            exit_code = ERROR_EXIT
    print(json.dumps(report, ensure_ascii=False))
    return exit_code


def unadmitted_receipt(operation_id, args, policy, error):
    """Machine truth for initial refusal/storage/transport failures before recipe admission."""
    from .restart_state import commands
    request = {'kind': 'account_switch' if args.account else 'restart', 'account': args.account,
        'close_idle': args.close_idle, 'manager': policy, 'notify': bool(args.notify_manager),
        'namespace': {'state_root': str(args.state_root) if args.state_root else None,
                      'socket_path': str(args.socket_path) if args.socket_path else None}}
    return {'schema': 'claude-restart/1', 'operation_id': operation_id, 'admitted': False,
        'request': request, 'phase': 'waiting', 'requested_transition': 'refused',
        'service': {'state': 'unchecked'}, 'manager': {'policy': policy['policy'], 'state': 'not_requested'},
        'notice': {'status': 'not_scheduled'}, 'publication': {'status': 'not_requested'}, 'effects': [],
        'errors': [{'type': type(error).__name__, 'message': str(error)}],
        'retry': {'classification': getattr(error, 'classification', 'permanent'),
                  'safe_pre_stop_retry': getattr(error, 'safe_pre_stop_retry', False), 'reason': str(error)},
        'commands': commands(operation_id, request)}


if __name__ == "__main__":
    raise SystemExit(main())
