"""Slow, native-proof-gated process collection; no model call or silence action.

Version-independent: a process is collected on any Claude Code version once its OWN native
readback proves every capability the collector relies on; a missing or malformed signal refuses
that one process (logged), never a whole version, and never closes it.
"""
import asyncio
import re

from .settlement import settlement_blockers

READBACK_FRESH_SECONDS = 30.0  # certificate validity only, never a native execution deadline

NATIVE_PROOF = (
    'launch envelope qualified (state events on, cron off, explicit tools without Monitor, no hooks)',
    "this generation's effective-settings readback: compaction, memory and fast mode false, no hooks, "
    'no auxiliary feature on, DISABLE_CRON and the session-state event variable reported as 1',
    'native session_state_changed pushes observed once the generation has run a turn',
    'no unrecognised native system subtype in this generation',
    'at the boundary: an initialize readback with this PID, a well-formed claude_code_version and '
    'session_state idle, then a fresh empty background task snapshot',
)
"""What the collector requires of each process, on any Claude Code version (host health, docs)."""

_VERSION = re.compile(r'\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?')


def well_formed_version(value):
    """A native ``claude_code_version`` the readback can be trusted to carry (e.g. ``2.1.300``)."""
    return isinstance(value, str) and _VERSION.fullmatch(value) is not None


# @beacon[
#   id=opc@collection_refusal,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Version-independent collector admission: the plain reason this one process may not be collected (a missing or malformed native capability signal), or None.,
# ]
def collection_refusal(session):
    """Why this process may not be collected, or None: its own native capability evidence.

    Never a version check. Every signal is read from this generation's own native output, so a
    Claude Code release that drops or renames one refuses exactly the processes that lack it.
    """
    record = session.record
    launch = record.get('launch') or {}
    if not isinstance(launch, dict):
        return 'launch record malformed'
    envelope = launch.get('collector_envelope') or {}
    if not isinstance(envelope, dict) or envelope.get('eligible') is not True:
        return 'launch envelope not collectable: ' + '; '.join(
            (envelope.get('reasons') if isinstance(envelope, dict) else None) or ['no qualified envelope'])
    settings = record.get('native_effective_settings')
    if not isinstance(settings, dict) or record.get('native_effective_settings_generation') != session.generation:
        return 'no native effective-settings readback for this generation'
    for key in ('autoCompactEnabled', 'idleCompaction', 'autoMemoryEnabled', 'fastMode'):
        if settings.get(key) is not False:
            return f'native effective setting {key} is not reported false'
    if settings.get('hooks'):
        return 'native effective settings carry hooks'
    for key, value in settings.items():
        feature = ''.join(character.lower() for character in key if character.isalnum())
        if any(part in feature for part in ('suggestion', 'progresssummar', 'sidequestion', 'observer', 'monitor', 'cron')):
            if value not in (None, False, 0, '', '0', 'false'):
                return f'native effective setting {key} enables an auxiliary feature'
    env = settings.get('env')
    if not isinstance(env, dict):
        return 'native effective settings report no env block'
    if env.get('DISABLE_CRON') != '1':
        return 'native effective env does not report DISABLE_CRON=1'
    if not any(env.get(name) == '1' for name in
               ('CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS', 'CLAUDE_CODE_SDK_READS_SESSION_STATE')):
        return 'native effective env does not report session-state events on'
    turned = any(record.get(key) is not None for key in
                 ('primary_turn_cursor', 'last_result_cursor', 'last_assistant_cursor'))
    if turned and record.get('native_state_push_cursor') is None:
        return 'ran a turn but never pushed session_state_changed (session-state events missing)'
    if record.get('unknown_system_subtypes'):
        return 'unrecognised native system subtype(s): ' + ', '.join(record['unknown_system_subtypes'])
    return None


def eligible(session):
    return collection_refusal(session) is None


UNKNOWN_LEVEL = 'native background task level is unknown'
"""The one hold a native readback can settle: the CLI never re-reported an (empty) task level."""


def start_readback(host, session, event_kind, **detail):
    """Issue one journaled native ``initialize`` readback on the owned stdin, under the Send gate.

    The caller holds ``session.command_lock`` and clears ``collection_in_progress`` and
    ``settlement_probe`` in its own ``finally`` (this may raise after setting them)."""
    import uuid
    session.collection_in_progress = True
    probe = {'generation': session.generation, 'started': host.monotonic(),
             'revision': session.work_revision, 'request_id': str(uuid.uuid4())}
    # Set the ID before control/update so this one observer request is not its own hold.
    session.settlement_probe = probe
    session.control('initialize', request_id=probe['request_id'], settlement_readback=True)
    session.event(event_kind, {'request_id': probe['request_id'], **detail,
        'generation': session.generation, 'pid': session.process.pid})
    return probe


# @beacon[
#   id=opc@observe_readback,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The one native readback observation the collector and the restart settle share: (snapshot|late|malformed|work|held, reason); a fresh task snapshot after a valid initialize answer is the only success.,
# ]
async def observe_readback(host, session, probe, *, tolerate_unknown_level, automatic=True):
    """Observe one readback until its fresh task snapshot, or name why it cannot settle.

    Returns ``('snapshot', None)`` once a fresh ``background_tasks_changed`` level followed a
    valid answer (the caller judges it, with no await in between), otherwise ``(kind, reason)``:
    ``late`` (no answer or no snapshot within the 30 s certificate), ``malformed`` (the answer is
    not this PID's well-formed idle/running readback), ``work`` (native work, exit or replacement
    during the readback) or ``held`` (a shared hold appeared). ``automatic=False`` uses the
    operator holds (a guarded restart) instead of the collector's."""
    revision = probe['revision']
    while True:
        if host.monotonic() - probe['started'] > READBACK_FRESH_SECONDS:
            return 'late', ('no fresh background task snapshot followed the readback'
                            if probe.get('response_frame') else 'no initialize readback answered in time')
        if (session.process.returncode is not None or
                host.sessions.get(session.sid) is not session or
                session.work_revision != revision):
            return 'work', 'native work, exit or replacement during the readback'
        if probe.get('response_frame') and not probe.get('valid'):
            return 'malformed', ('initialize readback missing or malformed (this PID, a well-formed '
                                 'claude_code_version, a known session_state, no pending requests)')
        holds = settlement_blockers(session.record, require_native=True,
                ignore_control=probe['request_id'], automatic=automatic)
        if holds and not (tolerate_unknown_level and holds == [UNKNOWN_LEVEL]):
            return 'held', 'held: ' + '; '.join(holds)
        if probe.get('snapshot_cursor'):
            return 'snapshot', None
        await asyncio.sleep(.05)  # readback observer cadence, no tool/reply cancellation


def snapshot_refusal(probe):
    return ('readback not idle with an empty task snapshot, or the process changed: '
            f"session_state {probe.get('session_state')!r}, "
            f"{len(probe.get('tasks') or [])} task(s) in the snapshot")


# @beacon[
#   id=opc@collect_settled_process,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Under the Send gate reinitializes an owned PID and requires its fresh idle empty-task snapshot plus every shared hold before EOF; unknown or late readback never closes.,
# ]
async def collect_session(host, session):
    """No reference counts: a push-observed age followed by native state/level readback.

    The two-frame native readback has no lease. We serialize companion admissions and recheck
    every observed native change up to EOF; the execution envelope excludes unenumerated wakes.
    Native readback lateness abandons only collection; its control stays journaled, not cancelled.
    """
    if session.command_lock.locked() or session.collection_in_progress:
        return False
    async with session.command_lock:
        refusal = collection_refusal(session)
        if refusal != session.record.get('collection_refusal'):
            # Logged once per change: a missing capability refuses this process, loudly.
            if refusal:
                session.event('collection_refused', {'reason': refusal, 'execution_action': 'none; left running'})
            session.update(collection_refusal=refusal)
        blockers = settlement_blockers(session.record, require_native=True)
        # The first initialize omits the task level on a never-tasked process (2.1.299, 2.1.300).
        # Seed it with a repeat readback, never close on this first observation.
        seed_only = blockers == ['native background task level is unknown']
        if refusal or (blockers and not seed_only):
            return False
        since = session.settled_since
        if not seed_only and (since is None or host.monotonic() - since < host.collection_threshold):
            return False
        age = host.monotonic() - since if since is not None else None
        if host.sessions.get(session.sid) is not session or session.process.returncode is not None:
            return False
        from .host import witness
        if not session.record.get('process_witness') or witness(session.process.pid) != session.record['process_witness']:
            return False
        try:
            probe = start_readback(host, session, 'collection_readback_requested', settled_age_seconds=age)
            revision = probe['revision']
            kind, reason = await observe_readback(host, session, probe, tolerate_unknown_level=seed_only)
            if kind == 'snapshot':
                blockers = settlement_blockers(session.record, require_native=True,
                    ignore_control=probe['request_id'])
                if (probe.get('valid') is True and probe.get('session_state') == 'idle' and
                        probe.get('tasks') == [] and not blockers and eligible(session) and
                        probe.get('snapshot_revision') == revision and
                        witness(session.process.pid) == session.record['process_witness']):
                    if seed_only:
                        session.event('settlement_baseline_observed', {
                            'request_id': probe['request_id'], 'snapshot_cursor': probe['snapshot_cursor'],
                            'execution_action': 'none; start monotonic settled span, do not close'})
                        return False
                    # No await between the final recheck and EOF admission. Input has been
                    # gated throughout the query; the ordinary writer/drains prove exit.
                    session.close_input(reason='collected', settled_age=age)
                    proof = {'settled_age_seconds': age,
                        'request_id': probe['request_id'], 'response_cursor': probe['response_cursor'],
                        'snapshot_cursor': probe['snapshot_cursor'], 'pid': session.process.pid}
                    session.update(collection_proof=proof)
                    session.event('collection_close_authorized', proof)
                    return True
                reason = snapshot_refusal(probe)
            session.event('collection_skipped', {'reason': reason,
                'request_id': probe['request_id'], 'execution_action': 'none'})
            return False
        finally:
            session.collection_in_progress = False
            session.settlement_probe = None
            session.update()


CONTEXT_HELPER = 'claude_context_mcp.py'
"""The one child a finished native process may still have: the bound context MCP helper."""


def _proc_children():
    """``{ppid: [pid, ...]}`` from /proc (Linux/WSL); a process gone mid-read is skipped."""
    import os
    children = {}
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        try:
            with open(f'/proc/{name}/stat', 'rb') as handle:
                stat = handle.read().decode('utf-8', 'replace')
        except OSError:
            continue
        # comm is parenthesised and may hold spaces or ')'; ppid is the 2nd field after the last ')'.
        fields = stat[stat.rfind(')') + 2:].split()
        if len(fields) >= 2 and fields[1].isdigit():
            children.setdefault(int(fields[1]), []).append(int(name))
    return children


def _proc_argv(pid):
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as handle:
            raw = handle.read()
    except OSError:
        return None
    return [part.decode('utf-8', 'replace') for part in raw.split(b'\0') if part]


def process_descendants(pid):
    """Every live descendant of ``pid`` as ``{'pid', 'argv'}`` rows (argv None once it vanished)."""
    children, rows, stack = _proc_children(), [], [pid]
    while stack:
        for child in children.get(stack.pop(), []):
            rows.append({'pid': child, 'argv': _proc_argv(child)})
            stack.append(child)
    return rows


def context_helper(argv):
    """``python …/claude_context_mcp.py`` exactly as the launch's mcp.json starts it, nothing else."""
    import os
    return (isinstance(argv, list) and len(argv) == 2 and
            os.path.basename(argv[0]).startswith('python') and
            os.path.basename(argv[1]) == CONTEXT_HELPER)


def process_tree_refusal(session, revision, *, descendants_fn=process_descendants):
    """Why the process-tree proof does not hold, or None (with the descendants it saw).

    The proof the Manager used by hand at Session 184: the session is natively idle, no background
    task is recorded, no other hold than the unknown level, no native work since the readback
    started, the exact PID witness is alive, and every descendant is the context helper."""
    from .host import witness
    record = session.record
    if record.get('native_session_state') != 'idle':
        return f"native session state is {record.get('native_session_state')!r}, not idle", []
    if record.get('background_tasks') or record.get('background_unsettled'):
        return 'background task(s) recorded', []
    holds = settlement_blockers(record, require_native=True, automatic=False,
                                ignore_control=(session.settlement_probe or {}).get('request_id'))
    if holds != [UNKNOWN_LEVEL]:
        return 'held: ' + '; '.join(holds or ['no hold left to prove']), []
    if session.work_revision != revision or session.process.returncode is not None:
        return 'native work or exit since the readback started', []
    if not record.get('process_witness') or witness(session.process.pid) != record['process_witness']:
        return 'the recorded PID/start witness is not verifiably alive', []
    seen = descendants_fn(session.process.pid)
    others = [row for row in seen if not context_helper(row.get('argv'))]
    if others:
        return ('child process(es) other than the context helper: ' + '; '.join(
            f"{row['pid']} {command_line(row.get('argv'))}" for row in others)), seen
    return None, seen


def command_line(argv, limit=400):
    """A child's command for a refusal reason. A long one keeps its head and its tail, where the script and its
    arguments are: a long interpreter or virtualenv path must never crop away the very name the operator needs."""
    text = ' '.join(argv or ['?'])
    return text if len(text) <= limit else text[:120] + ' … ' + text[-(limit - 123):]


# @beacon[
#   id=opc@settle_for_restart,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Guarded-restart settle for a session held only by an unknown task level: the collector's native readback first (settled = closable through close_input); only if it cannot answer, and allow_stop, the process-tree proof then one SIGTERM, reported by name. Never a blind kill.,
# ]
async def settle_for_restart(host, session, *, allow_stop=False, descendants_fn=process_descendants):
    """Settle one session whose only operator hold is "native background task level is unknown".

    Outcomes: ``settled`` (already, or the readback came back idle with an empty fresh level: the
    restart then closes it through ``close_input``), ``stopped`` (the readback could not settle it,
    ``allow_stop`` was given and the process-tree proof held: one SIGTERM to this owned process),
    ``held`` (the readback showed work or another hold), ``refused`` (not a candidate, or the
    readback could not settle it and the proof did not hold). Under the Send gate throughout."""
    from .host import witness
    async with session.command_lock:
        def outcome(name, reason=None, **extra):
            return {'outcome': name, 'reason': reason, 'execution_id': 'cc-' + session.sid,
                    'generation': session.generation, **extra}
        if host.sessions.get(session.sid) is not session or session.process.returncode is not None:
            return outcome('refused', 'the native process is gone or replaced')
        if not session.record.get('process_witness') or witness(session.process.pid) != session.record['process_witness']:
            return outcome('refused', 'the recorded PID/start witness is not verifiably alive')
        holds = settlement_blockers(session.record, require_native=True, automatic=False)
        if not holds:
            return outcome('settled', 'nothing holds it', readback=False)
        if holds != [UNKNOWN_LEVEL]:
            return outcome('refused', 'held: ' + '; '.join(holds))
        try:
            probe = start_readback(host, session, 'restart_readback_requested', allow_stop=bool(allow_stop))
            revision = probe['revision']
            kind, reason = await observe_readback(host, session, probe, tolerate_unknown_level=True,
                                                  automatic=False)
            if kind == 'snapshot':
                left = settlement_blockers(session.record, require_native=True, automatic=False,
                                           ignore_control=probe['request_id'])
                if (probe.get('valid') is True and probe.get('session_state') == 'idle' and
                        probe.get('tasks') == [] and not left and
                        probe.get('snapshot_revision') == revision and
                        witness(session.process.pid) == session.record['process_witness']):
                    proof = {'request_id': probe['request_id'], 'response_cursor': probe['response_cursor'],
                             'snapshot_cursor': probe['snapshot_cursor'], 'pid': session.process.pid}
                    session.event('restart_level_settled', {**proof,
                        'execution_action': 'none; the guarded restart closes it through close_input'})
                    return outcome('settled', 'native readback: idle with an empty task level', readback=True,
                                   proof=proof)
                reason = snapshot_refusal(probe) + (('; held: ' + '; '.join(left)) if left else '')
                session.event('restart_settle_held', {'request_id': probe['request_id'], 'reason': reason,
                                                      'execution_action': 'none'})
                return outcome('held', reason)
            if kind in ('work', 'held'):
                session.event('restart_settle_held', {'request_id': probe['request_id'], 'reason': reason,
                                                      'execution_action': 'none'})
                return outcome('held', reason)
            # late / malformed: the readback cannot settle it. Only the process-tree proof may.
            if not allow_stop:
                session.event('restart_settle_unanswered', {'request_id': probe['request_id'],
                    'reason': reason, 'execution_action': 'none; no stop authorized'})
                return outcome('refused', 'native readback could not settle it: ' + reason)
            refusal, seen = process_tree_refusal(session, revision, descendants_fn=descendants_fn)
            if refusal:
                session.event('restart_stop_refused', {'request_id': probe['request_id'],
                    'readback': reason, 'reason': refusal, 'execution_action': 'none; left running'})
                return outcome('refused', f'native readback could not settle it ({reason}) and the '
                               f'process-tree proof does not hold: {refusal}')
            proof = {'readback': reason, 'request_id': probe['request_id'], 'pid': session.process.pid,
                     'process_witness': session.record['process_witness'],
                     'native_session_state': 'idle', 'background_tasks': 0,
                     'descendants': [{'pid': row['pid'], 'argv': row.get('argv')} for row in seen]}
            # Input is gated from here: Send refuses a non-ready state; the drains prove the exit.
            session.event('restart_process_stop_authorized', {**proof,
                'execution_action': 'one SIGTERM to the owned native process'})
            session.update(state='input_closing', close_reason='restart_process_stop', restart_stop_proof=proof)
            session.process.terminate()
            return outcome('stopped', 'native readback could not settle it (' + reason + '); '
                           'process-tree proof held, one SIGTERM sent', proof=proof)
        finally:
            session.collection_in_progress = False
            session.settlement_probe = None
            session.update()
