"""U04 correlated native controls: interrupt, set_model, effort and effective readback.

``NativeSession`` already journals every control request it sends and already fills the
``controls`` record when the native reply arrives (``NativeSession.project``). This module adds
the U04 *correlation* on top of that: one durable operation receipt per control, an explicit
authority record written before the bytes are queued, an unbounded wait for the native reply and
— for the settings setters — a native ``get_settings`` readback that alone may mark the requested
model/effort applied.

Policy this module implements:

* **No execution deadline.** :func:`await_control` polls the durable record and the host's
  ``changed`` event at a cadence; a missing reply stays pending and visible forever.
* **Setters are idle-only, and Manager-only unless a seat switch.** A conductor/worker/helper's
  model and effort are its launch argv (``launch.py``) until a script asks for an explicit switch
  with a ``seat-switch:…`` authority (``oc_seat resume`` / ``oc_steer`` ``--model``/``--variant``,
  :data:`SEAT_SWITCH_AUTHORITY`); a base never switches. A setter refuses while a turn is unsettled.
* **Interrupt and readback are allowed busy** and take no ``session.command_lock``; the lock is
  held only around issuing a setter, so issuing serializes with Send.
* **A receipt is not settlement.** An interrupt receipt names the native result/task evidence
  still required (:func:`interrupt_settlement`); it never completes the human input.
* **A different choice keeps Send blocked; an unverifiable one warns.** ``setting_control_pending``
  is persisted before a setter is queued. A readback whose native applied model/effort matches the
  request (or an explicit same-value no-op) clears it; one that positively names a different model
  or effort keeps it closed and discloses both: no secretly remapped Send. A readback that cannot
  verify the choice (an incomplete or reshaped reply, a failed get_settings, a value this code does
  not recognize) is NOT a difference: :func:`open_unverified` opens the gate with a loud, durable
  ``settings_unverified`` warning, so a Claude Code update can never close Send for good. A fresh
  launch/resume gate with no setter is verified against the launch-fixed
  ``requested_model``/``requested_effort`` (state ``awaiting_initial_readback`` until then). A
  setter still in flight, refused by the native, or lost with its caller stays closed until a readback.
* **Nothing is silently applied.** Every method returns its durable receipt, and every receipt
  stays reachable through the host's existing ``control_receipt`` (same ``controls`` index).
* **Readbacks verify only what they name.** Only the native ``applied.model`` / ``applied.effort``
  can verify a choice; a value missing from ``applied`` (an ``effective``-only reply) is
  unverifiable, never applied. An explicit native model name must match exactly or up to a
  release-date suffix; only ``haiku``/``sonnet``/``opus``/``fable`` resolve to a narrower
  ``claude-*`` name (:func:`compare_model`, :func:`compare_effort`).
* **The whole choice is verified.** A model operation may not silently alter effort (and vice
  versa): the dimension the request did not name is checked against the unchanged baseline. A
  mismatch sets ``settings_mismatch``, is recorded with both values, and is reported.
* **In-flight setters serialize.** A second setter refuses while the first has no native terminal
  reply (pending/replied/uncertain); a correction may supersede only after a native reply or a
  mismatch. A terminal refusal/unsupported/mismatch/unverified outcome raises one supervisor
  incident escalation per operation.

Host wiring (the lead owns it): route :data:`CONTROL_METHODS` in ``Host.dispatch`` to
:func:`dispatch_control`; refuse Send while ``settings_gate(session)['blocked']``; call
:func:`restore_setting_gate` (or ``await reconcile_setting_state(session)``) after launch/resume
so a durable unresolved operation cannot be dropped by the rebuilt session record; initialize
verification may call :func:`resolve_readback` with its verified readback.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

__all__ = [
    'CONTROL_METHODS', 'SETTING_METHODS', 'POLL_SECONDS', 'dispatch_control', 'read_settings',
    'settings_gate', 'restore_setting_gate', 'reconcile_setting_state', 'resolve_readback',
    'receipt', 'interrupt_settlement', 'model_matches', 'effort_matches', 'parse_readback',
    'escalate_control_incident', 'await_control', 'compare_model', 'compare_effort', 'open_unverified',
    'SEAT_SWITCH_AUTHORITY', 'SEAT_SWITCH_ROLES', 'setting_allowed',
]

CONTROL_METHODS = ('interrupt', 'set_model', 'apply_flag_settings', 'apply_effort', 'get_settings', 'stop_task')
"""The method names ``Host.dispatch`` routes here; anything else stays a loud error."""

SETTING_METHODS = ('set_model', 'apply_flag_settings', 'apply_effort')
"""The setters: Manager (or an explicit seat switch), idle-only, gated until a native readback verifies them."""

SEAT_SWITCH_AUTHORITY = 'seat-switch:'
"""Authority prefix with which a script switches a seat's model/effort (``oc_claude.switch_settings``)."""

SEAT_SWITCH_ROLES = ('conductor', 'worker', 'helper')
"""Roles a seat switch may change; a parked ``base`` (and an unknown role) never switches."""

POLL_SECONDS = 0.25
"""Cadence of the reply poll, never an operation deadline (a missing reply stays pending)."""

MODEL_ALIASES = ('haiku', 'sonnet', 'opus', 'fable')
"""The documented family aliases; an alias may resolve to a narrower claude-* name, nothing else."""

SETTLED_STATES = ('applied', 'no_op_applied', 'applied_unverified', 'superseded', 'replied', 'refused', 'unsupported')
"""Terminal receipt states a later cancellation must not downgrade."""

CLEARED_SETTING_STATES = ('applied', 'no_op_applied', 'applied_unverified', 'superseded')
"""States in which a setting operation no longer blocks Send. ``applied_unverified``: the readback could not
verify the choice but named nothing different, so Send is open with a loud warning (``settings_unverified``)."""

UNVERIFIABLE_READBACK_STATES = ('refused', 'unsupported')
"""A get_settings that itself failed (a native error reply, or a reply status this code does not know): the
choice cannot be verified, which is not evidence that it differs."""

OPENABLE_SETTING_STATES = ('replied', 'uncertain', 'unverified', 'unsupported')
"""Setter states an unverifiable readback may open (with a warning): nothing in them positively names a different
choice. ``pending`` (still in flight), ``mismatch`` and ``refused`` (the native said no) stay blocked."""

UNRESOLVED_SETTING_STATES = ('pending', 'replied', 'mismatch', 'refused', 'unsupported',
                             'unverified', 'uncertain')
"""Setter states that keep the Send gate closed until native readback evidence settles them."""

IN_FLIGHT_SETTING_STATES = ('pending', 'replied', 'uncertain')
"""A second setter refuses while one of these has no native terminal reply yet."""

ESCALATING_SETTING_STATES = ('mismatch', 'refused', 'unsupported', 'unverified')
"""Control outcomes that raise one supervisor incident escalation per operation."""

BLOCK_REASONS = {
    'pending': 'awaiting_native_reply',
    'replied': 'awaiting_readback',
    'mismatch': 'effective_choice_differs',
    'refused': 'native_refused',
    'unsupported': 'reply_or_readback_unsupported',
    'unverified': 'readback_unverified',
    'uncertain': 'uncertain_outcome',
}
"""Machine-readable gate reason per unresolved setter state."""

_EFFORT_KEYS = ('effort', 'effort_level', 'effortLevel')


# ---------------------------------------------------------------------------------------------
# Parameters and matching
# ---------------------------------------------------------------------------------------------

def _model_param(model):
    """The launch-time model rule, so the control path can never accept a name launch refuses."""
    from .launch import _validated_model
    return _validated_model(model)


def _validated_effort(effort):
    from .launch import EFFORT_LEVELS
    if not isinstance(effort, str) or effort not in EFFORT_LEVELS:
        raise ValueError(f'effort must be one of {", ".join(EFFORT_LEVELS)}: {effort!r}')
    return effort


def _effort_from_params(params):
    supplied = [key for key in _EFFORT_KEYS if key in params]
    if len(supplied) != 1:
        raise ValueError('exactly one of effort / effort_level / effortLevel is required')
    return _validated_effort(params.pop(supplied[0]))


def _reject_leftovers(params):
    if params:
        raise ValueError(f'unexpected control params: {sorted(params)}')


_DATED = re.compile(r'(.+?)[-@](\d{8})')
"""A model id with a release-date suffix (``claude-opus-5-5-20261001``): the same model as its base id."""


def _undated(model):
    match = _DATED.fullmatch(model)
    return match.group(1) if match else model


def compare_model(requested, applied):
    """``'match'`` · ``'differs'`` · ``'unknown'`` for a requested model against the native applied one.

    Equal names match, as does the same id with a release-date suffix on either side, and a documented
    family alias (``haiku``/``sonnet``/``opus``/``fable``) requested and resolved to a narrower
    ``claude-<alias>…`` name. A readback that names no usable model, or only the family alias of the
    requested id (less specific: the version cannot be told), is ``unknown``: unverifiable, not different.
    Anything else is ``differs``: a suffixed remap such as ``claude-haiku-4-5-remap`` or a paid
    ``…[1m]`` route never satisfies ``claude-haiku-4-5``.
    """
    if not isinstance(requested, str) or not requested.strip():
        return 'unknown'
    if not isinstance(applied, str) or not applied.strip():
        return 'unknown'
    requested, applied = requested.strip().lower(), applied.strip().lower()
    if requested == applied or _undated(requested) == _undated(applied):
        return 'match'
    tokens = [token for token in re.split(r'[^a-z0-9]+', applied) if token]
    if requested in MODEL_ALIASES:
        return 'match' if tokens and tokens[0] in (requested, 'claude') and requested in tokens else 'differs'
    if applied in MODEL_ALIASES:
        family = [token for token in re.split(r'[^a-z0-9]+', requested) if token]
        return 'unknown' if applied in family else 'differs'
    return 'differs'


def compare_effort(requested, applied):
    """``'match'`` · ``'differs'`` · ``'unknown'`` for effort: equal levels match; another known level
    differs; a missing or unrecognized value (a new spelling, a number, an object) is unverifiable."""
    from .launch import EFFORT_LEVELS
    if not isinstance(requested, str) or not requested.strip():
        return 'unknown'
    if not isinstance(applied, str) or not applied.strip():
        return 'unknown'
    requested, applied = requested.strip().lower(), applied.strip().lower()
    if requested == applied:
        return 'match'
    return 'differs' if applied in EFFORT_LEVELS else 'unknown'


def model_matches(requested, applied):
    """A requested model is verified by the native applied model (:func:`compare_model` says match)."""
    return compare_model(requested, applied) == 'match'


def effort_matches(requested, applied):
    """A requested effort is verified by the native applied effort (:func:`compare_effort` says match)."""
    return compare_effort(requested, applied) == 'match'


def _compare(field, expected, applied):
    return compare_model(expected, applied) if field == 'model' else compare_effort(expected, applied)


def _prior_effective(session, field, prior=None):
    """The last verified effective value for a dimension, before the current readback was merged."""
    if prior is None:
        prior = session.record.get('controls_effective')
    value = prior.get(field) if isinstance(prior, dict) else None
    if isinstance(value, str) and value:
        return value
    value = session.record.get('requested_' + field)
    return value if isinstance(value, str) and value else None


def _verify_dimensions(session, record, readback, prior=None):
    """Check the requested delta AND the unchanged baseline dimension; return (differing, unknown).

    A model operation must not silently alter effort (and vice versa): the dimension the request
    did not name is compared against the last verified effective value (or the launch choice when
    none exists yet). ``differing`` holds what the readback positively names differently (Send stays
    blocked); ``unknown`` what cannot be verified: no expectation at all, or a readback that names no
    usable value (Send opens with a loud warning, never applied silently).
    """
    requested = record.get('requested') or {}
    differing, unknown = {}, []
    for field in ('model', 'effort'):
        value = requested[field] if field in requested else _prior_effective(session, field, prior)
        verdict = 'unknown' if value is None else _compare(field, value, readback.get(field))
        if verdict == 'differs':
            differing[field] = value
        elif verdict == 'unknown':
            unknown.append(field)
    return differing, unknown


def _matches_effective(requested, previous):
    """True when the request already holds the last verified effective value (a no-op)."""
    if not isinstance(previous, dict):
        return False
    if 'model' in requested:
        return model_matches(requested['model'], previous.get('model'))
    if 'effort' in requested:
        return effort_matches(requested['effort'], previous.get('effort'))
    return False


def parse_readback(record):
    """Extract the native ``applied.model`` and ``applied.effort`` from a successful get_settings record.

    → ``{'model', 'effort', 'applied', 'effective', 'complete'}``, or None when the record is not a
    success. Only the native ``applied`` block can verify a choice: an ``effective``-only (or otherwise
    incomplete, nested or renamed) reply leaves that value None and ``complete`` False, so it is
    unverifiable — never applied, and never "different" either: a Claude Code update that reshapes the
    reply opens Send with a loud warning instead of closing it for good. A fallback/remap can still never
    be laundered through ``effective``.
    """
    if not isinstance(record, dict) or record.get('status') != 'success':
        return None
    response = record.get('response') if isinstance(record.get('response'), dict) else {}
    body = response.get('response') if isinstance(response.get('response'), dict) else {}
    applied = body.get('applied') if isinstance(body.get('applied'), dict) else {}
    effective = body.get('effective') if isinstance(body.get('effective'), dict) else {}
    model, effort = applied.get('model'), applied.get('effort')
    model = model if isinstance(model, str) and model else None
    effort = effort if isinstance(effort, str) and effort else None
    return {'model': model, 'effort': effort, 'applied': applied, 'effective': effective,
            'complete': model is not None and effort is not None}


def _readback_values(readback):
    return {'model': readback.get('model'), 'effort': readback.get('effort')}


# ---------------------------------------------------------------------------------------------
# Durable records and the Send gate
# ---------------------------------------------------------------------------------------------

def _record(session, request_id):
    return session.store.get('controls', session.sid, request_id)


def _record_update(session, request_id, **fields):
    record = _record(session, request_id)
    if record is None:
        raise RuntimeError(f'control {request_id} is not journaled')
    record.update(fields)
    session.store.put('controls', session.sid, record, request_id)
    return record


def _control_rows(session):
    """Every durable control row for this sid, or None when the index is unreadable (fail closed)."""
    store = session.store
    db = getattr(store, 'db', None)
    if db is not None:
        try:
            fetched = db.execute('SELECT body FROM controls WHERE sid=?', (session.sid,)).fetchall()
        except Exception:  # the durable index itself is unreadable: never claim the gate open
            return None
        rows = []
        for row in fetched:
            body = row[0]
            try:
                rows.append(json.loads(body) if isinstance(body, str) else dict(body))
            except (TypeError, ValueError):
                return None
        return rows
    lister = getattr(store, 'control_rows', None)
    if callable(lister):
        try:
            return [dict(row) for row in lister(session.sid)]
        except Exception:
            return None
    return []


def _block_reason(state):
    return BLOCK_REASONS.get(state, 'unresolved_setting_operation')


def _pending_setting_id(session):
    # A rebuilt (resumed) record may not carry the gate fields at all: restore from the durable index.
    restore_setting_gate(session)
    if session.record.get('setting_control_pending') is not True:
        return None
    return session.record.get('setting_control_request_id')


# @beacon[
#   id=opc@settings_gate,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The Send gate: blocked while any setting operation is pending, unverified, refused, uncertain or mismatched.,
# ]
def settings_gate(session):
    """Current Send gate for this session, from the durable record (restored if it was rebuilt).

    ``{'blocked': bool, 'state', 'reason', 'request_id', 'effective', 'disclosure'}``. A blocked
    gate is the lead's cue to refuse Send with the disclosed reason: the requested model/effort
    is not verified applied, and a differing native choice must never be sent silently.
    """
    restored = restore_setting_gate(session)
    record = session.record
    if restored.get('unreadable'):
        return {'blocked': True, 'state': 'unknown', 'reason': 'control_state_unreadable',
                'request_id': None, 'effective': record.get('controls_effective'),
                'disclosure': 'durable control state is unreadable; Send stays blocked'}
    blocked = record.get('setting_control_pending') is True
    return {'blocked': blocked,
            'state': record.get('setting_control_state') if blocked else 'idle',
            'reason': record.get('setting_control_block_reason') if blocked else None,
            'request_id': record.get('setting_control_request_id') if blocked else None,
            'effective': record.get('controls_effective'),
            'disclosure': record.get('setting_control_disclosure') if blocked else None}


def restore_setting_gate(session):
    """Re-attach a durable unresolved setter to a session record rebuilt by launch/resume.

    The operation lives in the durable ``controls`` index; a rebuilt session record does not
    carry the gate fields. This scans that index, repopulates the record's
    ``setting_control_pending``/``state``/``reason``/``disclosure``/``request_id`` fields, and
    clears them only when the durable row itself is in a verified (cleared) state. It never
    invents evidence. ``{'restored', 'unreadable', 'state', 'request_id'}`` is returned.
    """
    rows = _control_rows(session)
    if rows is None:
        return {'restored': False, 'unreadable': True, 'state': 'unknown', 'request_id': None}
    record = session.record
    unresolved = [row for row in rows if row.get('operation_class') == 'setting'
                  and row.get('state') in UNRESOLVED_SETTING_STATES and row.get('request_id')]
    if unresolved:
        latest = max(unresolved, key=lambda row: row.get('dispatched_at') or 0)
        if (record.get('setting_control_pending') is not True
                or record.get('setting_control_request_id') != latest['request_id']
                or record.get('setting_control_state') != latest['state']):
            session.update(setting_control_pending=True, setting_control_state=latest['state'],
                           setting_control_block_reason=_block_reason(latest['state']),
                           setting_control_disclosure=latest.get('disclosure')
                           or 'restored unresolved setting operation',
                           setting_control_request_id=latest['request_id'])
            return {'restored': True, 'unreadable': False, 'state': latest['state'],
                    'request_id': latest['request_id']}
        return {'restored': False, 'unreadable': False, 'state': latest['state'],
                'request_id': latest['request_id']}
    pending_id = record.get('setting_control_request_id')
    if record.get('setting_control_pending') is True and pending_id:
        row = _record(session, pending_id) or {}
        if row.get('state') in CLEARED_SETTING_STATES:
            # The durable row holds the verified (or superseded) outcome the rebuilt record lost.
            session.update(setting_control_pending=False, setting_control_block_reason=None,
                           setting_control_disclosure=None)
            return {'restored': True, 'unreadable': False, 'state': row['state'],
                    'request_id': pending_id}
        return {'restored': False, 'unreadable': False, 'state': record.get('setting_control_state'),
                'request_id': pending_id}
    if record.get('setting_control_pending') is True and not record.get('setting_control_block_reason'):
        # A fresh launch/resume closes the gate before any verified readback; label it precisely.
        session.update(setting_control_state=record.get('setting_control_state') or 'pending',
                       setting_control_block_reason='awaiting_initial_readback',
                       setting_control_disclosure='settings are unverified since launch/resume; a verified '
                       'native get_settings is required before Send')
        return {'restored': True, 'unreadable': False, 'state': 'pending', 'request_id': None}
    return {'restored': False, 'unreadable': False,
            'state': 'idle' if record.get('setting_control_pending') is not True
            else record.get('setting_control_state'),
            'request_id': pending_id}


def receipt(session, request_id):
    """The durable operation receipt for one control request (the ``control_receipt`` body)."""
    return _record(session, request_id)


# ---------------------------------------------------------------------------------------------
# Waiting and native evidence
# ---------------------------------------------------------------------------------------------

async def await_control(session, request_id, interval=None):
    """Wait without any execution deadline for the native reply to one control request.

    The native reply is written into the control record by ``NativeSession.project``; this wakes
    on the host's ``changed`` event where present and otherwise polls at ``interval``. Returns
    the replied record, or None when the native process exited while the request was still
    pending (the caller classifies that as uncertain — never as applied).
    """
    interval = POLL_SECONDS if interval is None else interval
    while True:
        record = _record(session, request_id)
        if record is None:
            raise RuntimeError(f'control {request_id} is not journaled')
        if record.get('status') != 'pending':
            return record
        process = getattr(session, 'process', None)
        if process is not None and getattr(process, 'returncode', None) is not None:
            return None
        host = getattr(session, 'host', None)
        changed = getattr(host, 'changed', None) if host is not None else None
        if changed is None:
            await asyncio.sleep(interval)
            continue
        changed.clear()
        record = _record(session, request_id)  # re-check: no lost wakeup between clear and wait
        if record is not None and record.get('status') != 'pending':
            return record
        try:
            await asyncio.wait_for(changed.wait(), interval)
        except asyncio.TimeoutError:
            pass


def interrupt_settlement(session, request_id, *, payload=None):
    """"A receipt is not settlement": native result/task evidence still required after an interrupt.

    Reports, per message UUID captured at dispatch, whether a non-background native result has
    arrived, and also requires that no background work is still unsettled: until every captured
    mid has a result and ``background_unsettled``/``background_tasks`` are clear, the interrupt is
    not settled, whatever the control receipt said.
    """
    record = _record(session, request_id) or {}
    if record.get('operation_class') != 'interrupt':
        raise ValueError('not an interrupt receipt')
    if payload is None:
        payload = record.get('receipt_payload')
    evidence, awaiting = [], []
    for mid in list(record.get('pending_message_uuids') or []):
        message = session.store.get('receipts', session.sid, mid) or {}
        row = {'message_uuid': mid, 'status': message.get('status'),
               'completed': bool(message.get('completed')),
               'results': len(message.get('results') or []),
               'task_cursors': len(message.get('task_cursors') or []),
               'denial_cursors': len(message.get('denial_cursors') or []),
               'tool_errors': len(message.get('tool_errors') or [])}
        evidence.append(row)
        if not row['completed'] or mid in session.pending:
            awaiting.append(mid)
    background = session.record.get('background_tasks')
    background_ids = sorted(background) if isinstance(background, dict) else []
    background_unsettled = bool(session.record.get('background_unsettled')) or bool(background_ids)
    return {'settled': bool(evidence) and not awaiting and not background_unsettled,
            'claim': 'the control receipt is not settlement; a native result, stopped tasks and no '
                     'unsettled background work are required',
            'evidence': evidence, 'awaiting_message_uuids': awaiting,
            'background_unsettled': background_unsettled, 'background_task_ids': background_ids,
            'still_queued': (payload or {}).get('still_queued'),
            'cancelled': (payload or {}).get('cancelled')}


# ---------------------------------------------------------------------------------------------
# Issue, reconcile and settle
# ---------------------------------------------------------------------------------------------

def _require_alive(session):
    record = session.record
    if record.get('infrastructure_error'):
        raise RuntimeError('durability unavailable: control withheld')
    if record.get('state') in ('exited', 'exited_draining', 'ownership_unknown', 'input_closing'):
        raise RuntimeError(f"native session state {record.get('state')!r} cannot accept controls")
    process = getattr(session, 'process', None)
    if process is not None and getattr(process, 'returncode', None) is not None:
        raise RuntimeError('native process has exited')


def _require_ready_idle(session):
    _require_alive(session)
    if session.record.get('state') != 'ready':
        raise RuntimeError('native session not initialized and Max-confirmed/ready')
    if session.pending:
        raise RuntimeError('busy: model/effort changes are idle-only; wait or explicitly interrupt')
    # The Manager's OWN turn is what a mid-turn switch could split. Background agents/tasks
    # keep the choices they started with, and a scheduled recovery re-checks the settings gate
    # before it sends, so neither blocks a change between turns any more.
    from .host import own_turn_busy
    busy = own_turn_busy(session.record) if isinstance(session.record, dict) else None
    if busy and session.record.get('native_state_required'):
        raise RuntimeError(f'busy: {busy}; model/effort changes wait for it')
    if session.record.get('native_retry_active'):
        raise RuntimeError('busy: a native retry is active; model/effort changes wait for it')


def _check_generation(session, generation):
    if generation is None:
        return
    if type(generation) is not int or generation != session.generation:
        raise ValueError(f'wrong generation: session is {session.generation}')


def _issue_control(session, *, operation, operation_class, native_subtype, native_request,
                   requested=None, authority=None, no_op=None, supersedes=None, pending_gate=False,
                   receipt_extra=None, caller_request=None):
    """Persist intent+authority, queue the native control, enrich its durable receipt.

    Durable order is authority event -> control journal -> operation fields, all before the
    stdin writer can run (the writer only acts at the next await), so the authority and the
    operation are on disk before anything reaches the native process.
    """
    if pending_gate:
        session.update(setting_control_pending=True, setting_control_state='pending',
                       setting_control_block_reason='awaiting_native_reply',
                       setting_control_disclosure=None, setting_control_request_id=None)
    cursor = session.event('control_authority', {
        'operation': operation, 'authority': authority, 'generation': session.generation,
        'requested': requested, 'supersedes': supersedes, 'pending_gate': bool(pending_gate)})
    try:
        body = (session.control(native_subtype, native_request,
                                request_id=caller_request['request_id']) if caller_request
                else session.control(native_subtype, native_request))
    except Exception:
        if caller_request and _record(session, caller_request['request_id']) is not None:
            _record_update(session, caller_request['request_id'], caller_request=caller_request,
                           operation=operation, operation_class=operation_class,
                           requested=requested, state='uncertain', applied=False,
                           blocks_send=bool(pending_gate),
                           disclosure='durable control journal exists but stdin intent failed; never repeat blindly')
        if pending_gate:
            # Nothing reached the native process; keep the gate closed but say so precisely.
            session.update(setting_control_state='uncertain',
                           setting_control_block_reason='uncertain_outcome',
                           setting_control_disclosure='the control was not queued (write unavailable); the '
                           'requested choice is unverified — reconcile before sending')
        raise
    request_id = body['request_id']
    _record_update(session, request_id, operation=operation, operation_class=operation_class,
                   native_subtype=native_subtype, requested=requested, no_op=no_op,
                   authority=authority, authority_cursor=cursor,
                   dispatched_generation=session.generation, dispatched_at=time.time(),
                   state='pending', applied=False, blocks_send=bool(pending_gate), supersedes=supersedes,
                   caller_request=caller_request,
                   **(receipt_extra or {}))
    if pending_gate:
        session.update(setting_control_request_id=request_id)
    return request_id


def _supersede_prior(session):
    """Mark the terminal-reply prior operation superseded; the newer readback is authoritative.

    Only reachable after :func:`_setting_operation` refused an in-flight (pending/replied/
    uncertain) prior: a second setter may correct only a native terminal reply or a mismatch.
    """
    record = session.record
    prior = record.get('setting_control_request_id') if record.get('setting_control_pending') is True else None
    if prior:
        _record_update(session, prior, state='superseded', blocks_send=False,
                       disclosure='superseded by a later setting request; the later verified readback is authoritative')
    return prior


def escalate_control_incident(session, *, category, request_id, state, disclosure, detail=None):
    """Durably escalate one control outcome through the host supervisor when this session has one.

    Uses the existing ``Supervisor.incident`` (handler ``escalate``, ``attention=True``), keyed per
    operation so repeats dedupe instead of multiplying incidents. Inert sessions without a
    supervisor are a no-op; a failing supervisor write must not hide the already-durable block.
    """
    supervisor = getattr(getattr(session, 'host', None), 'supervisor', None)
    incident = getattr(supervisor, 'incident', None)
    record = session.record
    if not callable(incident) or 'native_uuid' not in record or 'generation' not in record:
        return None
    body = {'request_id': request_id, 'state': state, 'disclosure': disclosure}
    if detail:
        body.update(detail)
    return incident(record, category, 'escalate', detail=body, attention=True,
                    key=f'control-{record["generation"]}-{state}-{request_id}')


def _block_setting(session, request_id, state, *, disclosure, settings_mismatch=None, **fields):
    record = _record(session, request_id) or {}
    if record.get('state') in CLEARED_SETTING_STATES:
        return record  # a later cancellation must never reopen a settled operation
    updates = dict(setting_control_pending=True, setting_control_state=state,
                   setting_control_block_reason=_block_reason(state),
                   setting_control_disclosure=disclosure, setting_control_request_id=request_id)
    if settings_mismatch is not None:
        updates['settings_mismatch'] = settings_mismatch
    session.update(**updates)
    record = _record_update(session, request_id, state=state, applied=False, blocks_send=True,
                            disclosure=disclosure, **fields)
    session.event('control_blocked', {'request_id': request_id, 'state': state, 'disclosure': disclosure})
    if state in ESCALATING_SETTING_STATES:
        escalate_control_incident(session, category=f'control_{state}', request_id=request_id,
                                  state=state, disclosure=disclosure,
                                  detail={'requested': record.get('requested'),
                                          'effective': record.get('effective')})
    return _record(session, request_id)


def _mark_uncertain(session, request_id, *, disclosure, **fields):
    record = _record(session, request_id) or {}
    if record.get('state') in SETTLED_STATES:
        return record
    _record_update(session, request_id, state='uncertain', caller_lost=True,
                   blocks_send=bool(record.get('blocks_send')), disclosure=disclosure, **fields)
    return _record(session, request_id)


def _merge_effective(session, readback, request_id, source):
    previous = session.record.get('controls_effective')
    effective = dict(previous) if isinstance(previous, dict) else {}
    for key, value in _readback_values(readback).items():
        if value is not None:
            effective[key] = value
    effective.update(request_id=request_id, source=source, generation=session.generation,
                     verified_at=time.time())
    return effective


def _verified_fields(readback):
    """The session fields a verified native readback establishes."""
    return {'actual_model': readback['model'], 'actual_effort': readback['effort'],
            'settings_mismatch': False, 'settings_unverified': False, 'settings_unverified_disclosure': None}


def _warn_incident(session, *, category, key, detail):
    """One durable, non-escalating incident (no attention: it never blocks a Send, a recovery or a seat's `done`)."""
    supervisor = getattr(getattr(session, 'host', None), 'supervisor', None)
    incident = getattr(supervisor, 'incident', None)
    record = session.record
    if not callable(incident) or 'native_uuid' not in record or 'generation' not in record:
        return None
    return incident(record, category, 'warn', detail=detail, key=key)


# @beacon[
#   id=opc@open_unverified,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Unverifiable is not different: a settings readback that names no usable model/effort (a reshaped reply, a failed get_settings) opens Send with a loud durable warning (settings_unverified, an event, a non-escalating incident); a positive difference still blocks.,
# ]
def open_unverified(session, request_id, readback, unknown, *, source, disclosure=None):
    """Open the Send gate on a choice that cannot be verified but that nothing names differently.

    A Claude Code update that renames or nests a get_settings field must never close Send for good:
    the setter row (if any) becomes ``applied_unverified``, the gate opens, and the session record
    carries ``settings_unverified`` with its disclosure (``oc_seat info``, the pane) beside an event and
    a non-escalating ``settings_unverified`` incident. A recorded ``settings_mismatch`` (positive
    evidence of a different model) is never cleared here: the gate stays as it is.
    """
    if session.record.get('settings_mismatch'):
        return {'disposition': 'mismatch_kept', 'request_id': request_id}
    values = _readback_values(readback or {})
    text = disclosure or (f'native readback {values} names no usable {" or ".join(unknown) or "value"}, '
                          'so the choice cannot be verified')
    warning = (f'settings unverified: {text}; nothing names a different model or effort, so Send is open '
               '(press Re-check settings to verify)')
    if request_id:
        row = _record(session, request_id) or {}
        if row.get('state') not in CLEARED_SETTING_STATES:
            _record_update(session, request_id, state='applied_unverified', applied=False, blocks_send=False,
                           disclosure=warning, readback=readback, unverified=sorted(unknown),
                           verified_generation=session.generation, verified_at=time.time())
    session.update(setting_control_pending=False, setting_control_state='applied_unverified',
                   setting_control_block_reason=None, setting_control_disclosure=None,
                   setting_control_request_id=request_id, settings_unverified=True,
                   settings_unverified_disclosure=warning)
    session.event('control_unverified_open', {'request_id': request_id, 'source': source,
                                              'unverified': sorted(unknown), **values})
    _warn_incident(session, category='settings_unverified',
                   key=f'settings-unverified-{session.record.get("generation")}-{request_id or source}',
                   detail={'request_id': request_id, 'source': source, 'unverified': sorted(unknown),
                           'disclosure': warning, **values})
    return {'disposition': 'applied_unverified', 'request_id': request_id, 'unverified': sorted(unknown),
            'disclosure': warning}


def _settle_applied(session, request_id, readback, *, source):
    record = _record(session, request_id) or {}
    state = 'no_op_applied' if record.get('no_op') else 'applied'
    effective = _merge_effective(session, readback, request_id, source)
    fields = _verified_fields(readback)
    if record.get('operation_class') == 'setting':
        # Same-UUID resume must use the current explicit effective choices, not the launch alias.
        try:
            fields['requested_model'] = _model_param(readback['model'])
        except ValueError:
            pass  # a spelling launch.py would refuse stays a readback fact, not a resume choice
        try:
            fields['requested_effort'] = _validated_effort(readback['effort'])
        except ValueError:
            pass
    # Durable receipt first, then the session record: a crash between them reconciles to open.
    _record_update(session, request_id, state=state, applied=True, blocks_send=False,
                   disclosure=None, effective=effective, readback=readback,
                   verified_generation=session.generation, verified_at=time.time())
    session.update(setting_control_pending=False, setting_control_state=state,
                   setting_control_block_reason=None, setting_control_disclosure=None,
                   setting_control_request_id=request_id, controls_effective=effective, **fields)
    session.event('control_applied', {'request_id': request_id, 'state': state,
                                      **_readback_values(readback)})
    return _record(session, request_id)


def _launch_baseline(session):
    """The launch-fixed choices a fresh/resumed session's first readback must confirm."""
    baseline = {}
    if isinstance(session.record.get('requested_model'), str) and session.record['requested_model']:
        baseline['model'] = session.record['requested_model']
    if isinstance(session.record.get('requested_effort'), str) and session.record['requested_effort']:
        baseline['effort'] = session.record['requested_effort']
    return baseline


def _settle_baseline(session, readback, baseline, effective, source):
    """Clear (or keep closed) a launch/resume gate whose only evidence is the initial readback: a match
    opens it, a positive difference keeps it closed, an unverifiable value opens it with a warning."""
    verdicts = {key: _compare(key, value, readback.get(key)) for key, value in baseline.items()}
    differing = {key: value for key, value in baseline.items() if verdicts[key] == 'differs'}
    unknown = [key for key in baseline if verdicts[key] == 'unknown']
    if not differing and unknown:
        session.update(controls_effective=effective)
        return {**open_unverified(session, None, readback, unknown, source=source),
                'requested': baseline, 'effective': effective}
    if not differing:
        session.update(setting_control_pending=False, setting_control_state='applied',
                       setting_control_block_reason=None, setting_control_disclosure=None,
                       setting_control_request_id=None, controls_effective=effective,
                       **_verified_fields(readback))
        session.event('control_baseline_verified', {'requested': baseline, 'source': source,
                                                    **_readback_values(readback)})
        return {'disposition': 'applied_baseline', 'requested': baseline, 'effective': effective}
    session.update(setting_control_pending=True, setting_control_state='mismatch',
                   setting_control_block_reason='effective_choice_differs',
                   setting_control_disclosure=(f'native effective {_readback_values(readback)} does not match '
                                               f'launch choices {differing}; Send stays blocked'),
                   settings_mismatch=True)
    session.event('control_baseline_mismatch', {'requested': baseline, 'differing': sorted(differing),
                                                **_readback_values(readback)})
    escalate_control_incident(session, category='control_mismatch', request_id=None, state='mismatch',
                              disclosure=f'native effective {_readback_values(readback)} does not match '
                                         f'launch choices {differing}',
                              detail={'requested': baseline, 'differing': sorted(differing)})
    return {'disposition': 'mismatch', 'requested': baseline, 'differing': sorted(differing),
            'effective': effective}


def resolve_readback(session, readback, *, request_id=None, source='readback'):
    """Record a verified native readback and settle the pending setter when it matches.

    This is the only path that clears the gate: a native get_settings (or the lead's initialize
    verification) whose applied/effective model/effort matches the pending request — or, when the
    gate is a fresh launch/resume with no setter at all, the launch-fixed requested model/effort.
    A readback that differs while no native setter reply was observed keeps the operation
    uncertain; one that differs after a reply is a disclosed mismatch. ``{'disposition', ...}``.
    """
    prior_effective = session.record.get('controls_effective')
    # Collection's envelope must not ignore a native/managed-policy override. This is the
    # complete get_settings effective snapshot, distinct from the two applied choices.
    session.update(native_effective_settings=readback.get('effective'),
                   native_effective_settings_generation=session.generation)
    effective = _merge_effective(session, readback, request_id, source)
    session.update(controls_effective=effective)
    pending_id = _pending_setting_id(session)
    if pending_id is None:
        baseline = _launch_baseline(session)
        if baseline:
            return _settle_baseline(session, readback, baseline, effective, source)
        return {'disposition': 'idle', 'request_id': request_id, 'effective': effective}
    record = _record(session, pending_id) or {}
    requested = record.get('requested') or {}
    differing, unknown = _verify_dimensions(session, record, readback, prior=prior_effective)
    if not differing and not unknown:
        settled = _settle_applied(session, pending_id, readback, source=f'readback:{source}')
        return {'disposition': settled['state'], 'request_id': pending_id,
                'requested': requested, 'effective': effective}
    if unknown and not differing:
        if session.record.get('setting_control_state') not in OPENABLE_SETTING_STATES:
            # Still in flight (or refused by the native): its own reply and readback settle it.
            return {'disposition': 'unverified_in_flight', 'request_id': pending_id, 'requested': requested,
                    'effective': effective}
        return {**open_unverified(session, pending_id, readback, unknown, source=f'readback:{source}'),
                'requested': requested, 'effective': effective}
    if session.record.get('setting_control_state') == 'pending':
        _block_setting(session, pending_id, 'uncertain', disclosure=(
            f'native readback {_readback_values(readback)} differs from requested {requested} and no '
            'native setter reply was observed; the setter may still be in flight'),
            readback=readback)
        return {'disposition': 'uncertain', 'request_id': pending_id, 'requested': requested,
                'effective': effective}
    _block_setting(session, pending_id, 'mismatch', disclosure=(
        f'native effective {_readback_values(readback)} does not match requested {requested}; '
        'Send stays blocked'), readback=readback, effective=_readback_values(readback),
        settings_mismatch=True, mismatch=True)
    return {'disposition': 'mismatch', 'request_id': pending_id, 'requested': requested,
            'effective': effective}


async def _readback_record(session, *, authority):
    """Issue one native get_settings; return ``(readback, failure)`` with exactly one non-None."""
    try:
        _require_alive(session)
    except RuntimeError as error:
        return None, {'state': 'uncertain', 'reason': str(error), 'request_id': None}
    request_id = _issue_control(session, operation='get_settings', operation_class='readback',
                                native_subtype='get_settings', native_request={}, authority=authority)
    try:
        record = await await_control(session, request_id)
    except asyncio.CancelledError:
        _mark_uncertain(session, request_id,
                        disclosure='caller cancelled or disconnected before the readback reply')
        session.event('control_caller_lost', {'request_id': request_id, 'operation': 'get_settings'})
        raise
    if record is None:
        reason = 'the native process exited before the readback reply'
        _mark_uncertain(session, request_id, disclosure=reason)
        return None, {'state': 'uncertain', 'reason': reason, 'request_id': request_id}
    status = record.get('status')
    response = record.get('response') if isinstance(record.get('response'), dict) else {}
    if status == 'error':
        reason = f"native get_settings error: {response.get('error') or 'no error detail'}"
        _record_update(session, request_id, state='refused', disclosure=reason)
        return None, {'state': 'refused', 'reason': reason, 'request_id': request_id}
    if status != 'success':
        reason = f'unrecognized native get_settings reply status {status!r}'
        _record_update(session, request_id, state='unsupported', disclosure=reason)
        return None, {'state': 'unsupported', 'reason': reason, 'request_id': request_id}
    readback = parse_readback(record)
    readback['request_id'] = request_id
    # An incomplete reply is still a readback: its missing values are unverifiable (open with a warning),
    # and a value it does name is still compared (a positive difference blocks).
    _record_update(session, request_id, state='applied' if readback['complete'] else 'unverified',
                   verified=readback['complete'], blocks_send=False,
                   readback=readback, verified_generation=session.generation,
                   readback_model=readback['model'], readback_effort=readback['effort'],
                   **({} if readback['complete'] else {'disclosure': (
                       'native get_settings reply does not name both applied.model and applied.effort; '
                       'the missing value is unverifiable (effective-only is never applied)')}))
    session.event('control_readback', {'request_id': request_id, 'complete': readback['complete'],
                                       **_readback_values(readback)})
    return readback, None


def _verify_setting(session, request_id, readback, failure):
    record = _record(session, request_id) or {}
    requested = record.get('requested') or {}
    if failure is not None and failure['state'] not in UNVERIFIABLE_READBACK_STATES:
        return _block_setting(session, request_id, failure['state'], disclosure=(
            f'requested {requested} is not verified: {failure["reason"]}'),
            readback_failure=failure, readback_request_id=failure.get('request_id'))
    differing, unknown = _verify_dimensions(session, record, readback) if failure is None else ({}, ['model', 'effort'])
    if unknown and not differing:
        disclosure = f'requested {requested} is not verified: {failure["reason"]}' if failure else None
        opened = open_unverified(session, request_id, readback, unknown, source='setter-readback',
                                 disclosure=disclosure)
        if opened['disposition'] == 'mismatch_kept':
            return _block_setting(session, request_id, 'unverified', disclosure=(
                f'requested {requested} cannot be verified, and an earlier readback named a different choice'),
                readback=readback)
        return _record(session, request_id)
    if differing:
        session.event('control_mismatch', {'request_id': request_id, 'requested': requested,
                                           'differing': differing,
                                           'effective': _readback_values(readback)})
        return _block_setting(session, request_id, 'mismatch', disclosure=(
            f'native effective {_readback_values(readback)} does not match requested {requested} '
            f'plus unchanged baseline {differing}; Send stays blocked'),
            readback=readback, effective=_readback_values(readback),
            settings_mismatch=True, mismatch=True)
    return _settle_applied(session, request_id, readback, source='setter-readback')


def setting_allowed(role, authority):
    """A setter may run for the Manager, or for a conductor/worker/helper when its caller names an explicit
    seat switch (an authority starting ``seat-switch:``, recorded before the control is queued)."""
    return role == 'Manager' or (role in SEAT_SWITCH_ROLES and isinstance(authority, str)
                                 and authority.startswith(SEAT_SWITCH_AUTHORITY))


# @beacon[
#   id=opc@_setting_operation,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=One model/effort setter: Manager, or a conductor/worker/helper with a seat-switch: authority (oc_seat resume / oc_steer --model/--variant); idle-only, gate persisted first, native reply then get_settings readback decides applied/mismatch/unverified.,
# ]
async def _setting_operation(session, method, requested, *, generation, authority, caller_request=None):
    role = session.record.get('role')
    if not setting_allowed(role, authority):
        raise RuntimeError(f'{method} is Manager-only unless a seat switch ({SEAT_SWITCH_AUTHORITY}… authority, roles '
                           f'{"/".join(SEAT_SWITCH_ROLES)}); {role!r} model/effort stay as launched')
    _check_generation(session, generation)
    async with session.command_lock:
        _require_ready_idle(session)
        restore_setting_gate(session)
        prior_state = session.record.get('setting_control_state')
        prior_id = session.record.get('setting_control_request_id')
        prior = _record(session, prior_id) if prior_id else None
        # Only a setter this very process may still answer serializes the next one. An uncertain
        # outcome, or a setter from an ended generation (its reply can never arrive), is
        # superseded by the new request, whose own readback is authoritative.
        if (session.record.get('setting_control_pending') is True and prior_id
                and prior_state in ('pending', 'replied')
                and (prior or {}).get('dispatched_generation', session.generation) == session.generation):
            raise RuntimeError(f'busy: a setting operation is {prior_state} without a native terminal '
                               'reply; wait a moment or press Re-check settings')
        supersedes = _supersede_prior(session)
        no_op = _matches_effective(requested, session.record.get('controls_effective'))
        if 'model' in requested:
            native_subtype = 'set_model'
            native_request = {'model': requested['model']}
        else:
            native_subtype = 'apply_flag_settings'
            native_request = {'settings': {'effortLevel': requested['effort']}}
        request_id = _issue_control(session, operation=method, operation_class='setting',
                                    native_subtype=native_subtype, native_request=native_request,
                                    requested=dict(requested), authority=authority, no_op=no_op,
                                    supersedes=supersedes, pending_gate=True, caller_request=caller_request)
    try:
        record = await await_control(session, request_id)
        if record is None:
            return _block_setting(session, request_id, 'uncertain', disclosure=(
                'the native process exited before the control reply; the requested choice must not be assumed'))
        status = record.get('status')
        response = record.get('response') if isinstance(record.get('response'), dict) else {}
        if status == 'error':
            return _block_setting(session, request_id, 'refused', disclosure=(
                f"native error reply: {response.get('error') or 'no error detail'}"),
                native_response=response)
        # Success, or a reply status this code does not know (not evidence that the setter failed):
        # the native readback decides.
        _record_update(session, request_id, state='replied',
                       **({} if status == 'success' else {'disclosure': f'unrecognized native control reply '
                                                          f'status {status!r}; verified by readback'}))
        session.update(setting_control_state='replied', setting_control_block_reason='awaiting_readback')
        readback, failure = await _readback_record(session, authority=f'verify:{method}')
        return _verify_setting(session, request_id, readback, failure)
    except asyncio.CancelledError:
        _block_setting(session, request_id, 'uncertain', disclosure=(
            'caller cancelled or disconnected before a verified readback; the native operation may '
            'still apply'), caller_lost=True)
        session.event('control_caller_lost', {'request_id': request_id, 'operation': method})
        raise


async def _interrupt(session, params, *, generation, authority, caller_request=None):
    if not isinstance(authority, str) or not authority.strip():
        raise ValueError('interrupt requires an explicit authority string, recorded before the control is sent')
    reason = params.pop('reason', None)
    cancel_queued = params.pop('cancel_queued', True)
    if not isinstance(cancel_queued, bool):
        raise ValueError('cancel_queued must be a boolean')
    _reject_leftovers(params)
    _check_generation(session, generation)
    _require_alive(session)
    restore_setting_gate(session)
    # The classifier needs the authorization before the native turn can end (host.result clears it);
    # set a string before the control is queued, then refine it to the exact request id.
    session.update(interrupt_authorized=authority.strip())
    try:
        request_id = _issue_control(session, operation='interrupt', operation_class='interrupt',
                                    native_subtype='interrupt', native_request={'cancel_queued': cancel_queued},
                                    requested={'cancel_queued': cancel_queued}, authority=authority.strip(),
                                    caller_request=caller_request,
                                    receipt_extra={'reason': reason, 'pending_message_uuids': sorted(session.pending)})
    except Exception:
        session.update(interrupt_authorized=None)  # nothing reached the CLI: no authorization stands
        raise
    session.update(interrupt_authorized=request_id)
    try:
        record = await await_control(session, request_id)
    except asyncio.CancelledError:
        _mark_uncertain(session, request_id, disclosure=(
            'caller cancelled or disconnected before the native interrupt receipt; the interrupt may '
            'still have been applied and is not settlement'))
        session.event('control_caller_lost', {'request_id': request_id, 'operation': 'interrupt'})
        raise
    if record is None:
        return _finalize_interrupt(session, request_id, 'uncertain', disclosure=(
            'the native process exited before the interrupt reply; read native result/task evidence'))
    status = record.get('status')
    response = record.get('response') if isinstance(record.get('response'), dict) else {}
    payload = response.get('response') if isinstance(response.get('response'), dict) else {}
    if status == 'success':
        return _finalize_interrupt(session, request_id, 'replied', payload=payload)
    if status == 'error':
        return _finalize_interrupt(session, request_id, 'refused', disclosure=(
            f"native error reply: {response.get('error') or 'no error detail'}"))
    return _finalize_interrupt(session, request_id, 'unsupported', disclosure=(
        f'unrecognized native control reply status {status!r}'))


def _finalize_interrupt(session, request_id, state, *, payload=None, disclosure=None):
    settlement = interrupt_settlement(session, request_id, payload=payload)
    _record_update(session, request_id, state=state, blocks_send=False, disclosure=disclosure,
                   receipt_payload=payload or {}, settlement=settlement)
    session.event('control_replied', {'request_id': request_id, 'operation': 'interrupt',
                                      'state': state, 'settled': settlement['settled']})
    return _record(session, request_id)


# ---------------------------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------------------------

# @beacon[
#   id=opc@dispatch_control,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Correlates interrupt/model/effort/readback controls and qualified exact-observation task stops with durable receipts and explicit authority; a native stop reply alone never proves cancellation or settlement.,
# ]
async def dispatch_control(session, method, params):
    """Dispatch one correlated native control and return its durable receipt.

    ``method`` is one of :data:`CONTROL_METHODS`; ``params`` may carry the optional
    ``generation`` (checked against the owned writer) and, for ``interrupt``, the required
    ``authority`` string recorded before the control is sent. Unknown methods or parameters
    fail loudly; nothing is silently applied or silently substituted.
    """
    if not isinstance(params, dict):
        raise ValueError('params must be object')
    if method not in CONTROL_METHODS:
        raise ValueError(f'unknown control method: {method}')
    params = dict(params)
    generation = params.pop('generation', None)
    authority = params.pop('authority', None)
    caller_request = params.pop('_caller_request', None)
    if method == 'stop_task':
        observation_id = params.pop('observation_id', None)
        _reject_leftovers(params)
        row = session.host.observations.get(observation_id)
        if not row or row['owner']['session'] != session.record['execution_id'] or row['owner']['generation'] != session.generation:
            raise ValueError('observation is not bound to this exact owner/generation')
        return await session.host.observations.stop(observation_id, authority=authority)
    if method == 'interrupt':
        return await _interrupt(session, params, generation=generation, authority=authority,
                                caller_request=caller_request)
    if method == 'set_model':
        model = _model_param(params.pop('model', None))
        _reject_leftovers(params)
        return await _setting_operation(session, method, {'model': model}, generation=generation,
                                        authority=authority, caller_request=caller_request)
    if method == 'apply_effort':
        effort = _effort_from_params(params)
        _reject_leftovers(params)
        return await _setting_operation(session, method, {'effort': effort}, generation=generation,
                                        authority=authority, caller_request=caller_request)
    if method == 'apply_flag_settings':
        settings = params.pop('settings', None)
        if not isinstance(settings, dict) or set(settings) != {'effortLevel'}:
            raise ValueError("apply_flag_settings is restricted to exactly {'effortLevel': <level>}")
        effort = _validated_effort(settings['effortLevel'])
        _reject_leftovers(params)
        return await _setting_operation(session, method, {'effort': effort}, generation=generation,
                                        authority=authority, caller_request=caller_request)
    _reject_leftovers(params)
    return await read_settings(session, generation=generation, authority=authority)


async def read_settings(session, *, generation=None, authority=None):
    """Native effective settings readback; allowed while a turn or another control is in flight.

    Records the verified effective model/effort and settles a matching pending setter (the
    recovery path after a lost caller or a restart). A readback that positively names a different
    choice keeps the gate closed; one that cannot verify it (an incomplete reply, a failed
    get_settings) opens it with a loud ``settings_unverified`` warning (:func:`open_unverified`).
    """
    _check_generation(session, generation)
    _require_alive(session)
    restore_setting_gate(session)
    readback, failure = await _readback_record(session, authority=authority or 'readback')
    if readback is not None:
        disposition = resolve_readback(session, readback, request_id=readback['request_id'],
                                       source=authority or 'readback')
        gate = settings_gate(session)
        stored = _record(session, readback['request_id']) or {}
        return {**stored, 'state': 'applied' if readback['complete'] else 'unverified',
                'applied': readback['complete'], 'operation': 'get_settings',
                'model': readback['model'], 'effort': readback['effort'],
                'effective': session.record.get('controls_effective'), 'readback': readback,
                'disposition': disposition, 'gate': gate, 'blocks_send': gate['blocked']}
    disposition = {'disposition': 'failed', **failure}
    if failure['state'] in UNVERIFIABLE_READBACK_STATES and session.record.get('setting_control_pending') is True:
        # get_settings itself failed or answered in a shape this code does not know: unverifiable, not
        # different. A launch/resume gate (no setter) or a setter nothing names differently opens with a
        # loud warning; an in-flight, mismatched or natively refused setter stays as it is.
        pending_id = _pending_setting_id(session)
        if pending_id is None or session.record.get('setting_control_state') in OPENABLE_SETTING_STATES:
            disposition = open_unverified(session, pending_id, None, ['model', 'effort'],
                                          source=authority or 'readback', disclosure=failure['reason'])
    gate = settings_gate(session)
    stored = _record(session, failure['request_id']) if failure.get('request_id') else {}
    return {**(stored or {}), 'state': failure['state'], 'applied': False, 'blocks_send': gate['blocked'],
            'disclosure': failure['reason'], 'disposition': disposition,
            'gate': gate, 'effective': session.record.get('controls_effective')}


async def reconcile_setting_state(session, *, authority='reconcile'):
    """Reconcile a durable unresolved setter with the live native session (restart/resume hook).

    Restores the gate from the durable index and, only when the native process is alive, takes
    one get_settings readback: a matching effective value settles the operation; a differing one
    keeps it blocked with the mismatch disclosed. Call it after initialize verification (it needs
    a live owned process); it never assumes a native choice.
    """
    restore_setting_gate(session)
    gate = settings_gate(session)
    if not gate['blocked']:
        return gate
    try:
        _require_alive(session)
    except RuntimeError as error:
        return {**gate, 'reconcile': f'not attempted: {error}'}
    readback, failure = await _readback_record(session, authority=authority)
    disposition = (resolve_readback(session, readback, request_id=readback['request_id'], source=authority)
                   if readback is not None else {'disposition': 'failed', **failure})
    return {**settings_gate(session), 'disposition': disposition}
