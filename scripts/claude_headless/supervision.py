"""Small native-error registry and durable observations. Silence has NO execution effects."""
import asyncio
from datetime import datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid

DEFAULT_RETRY_DELAY = 3600  # conservative one hour when no explicit reset/retry metadata exists
RECOVERY_TEXT = ('continue\nThe previous native turn ended or its process exited. Preserve uncertain '
    'effects: inspect native interrupted-tool/history receipts and actual postconditions before '
    'relying on or repeating any action. Do not replay the original input or assume rollback. '
    'Continue the authorized task only after reconciling that evidence.')


def user_origin(origin):
    return origin is None or origin == 'user' or isinstance(origin, dict) and origin.get('kind') in ('user', 'user-message')


USAGE_LIMIT_TYPES = ('five_hour', 'seven_day', 'seven_day_opus', 'seven_day_sonnet',
                     'seven_day_overage_included', 'overage')
"""Claude Code's own ``rate_limit_info.rateLimitType`` values (2.1.300 schema): the subscription usage windows. A newer
``five_hour…``/``seven_day…`` window counts too; anything else is not a lockout."""

QUOTA_HALT_INTERRUPT_ATTEMPTS = 3
"""How many host interrupts one lockout gets (each on a later retry frame) before it is left to the operator."""


# @beacon[
#   id=opc@usage_lockout,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The subscription lockout signature in one native rate_limit_info: status rejected, a usage-window rateLimitType, no overage in use or available, a reset still ahead. None for everything else, an ordinary capacity 429 included (it keeps its unbounded retry).,
# ]
def usage_lockout(info, now):
    """``{'rate_limit_type', 'resets_at', 'overage_status', 'overage_disabled_reason'}`` when ``info`` says the account
    is out of its usage window and nothing will pay for more until the reset, else None.

    Claude Code sends this ``rate_limit_event`` right before the 429 it then retries until ``resetsAt``. A capacity 429
    (an overloaded server, a short throttle) carries no rejected subscription window, so it never matches. A rejected
    window whose reset is already past is an old record, not a current lockout."""
    if not isinstance(info, dict) or info.get('status') != 'rejected':
        return None
    kind = info.get('rateLimitType')
    if not isinstance(kind, str) or not (kind in USAGE_LIMIT_TYPES or kind.startswith(('five_hour', 'seven_day'))):
        return None
    if info.get('isUsingOverage') is True or info.get('overageStatus') in ('allowed', 'allowed_warning'):
        return None
    resets = info.get('resetsAt')
    if type(resets) in (int, float) and math.isfinite(resets):
        resets = float(resets) / (1000 if resets > 1e12 else 1)
        if resets <= now:
            return None
    else:
        resets = None
    return {'rate_limit_type': kind, 'resets_at': resets, 'overage_status': info.get('overageStatus'),
            'overage_disabled_reason': info.get('overageDisabledReason')}


def quota_halt_active(record):
    """The usage-limit halt of this record's own generation (any status), or None. A new generation (resume,
    reconnect) starts without one."""
    halt = record.get('quota_halt')
    return halt if isinstance(halt, dict) and halt.get('generation') == record.get('generation') else None


# @beacon[
#   id=opc@clear_stale_native_markers,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Returns, then drops, the retry/background markers a proved-dead native owner can never clear; the caller journals the returned values as evidence.,
# ]
def clear_stale_native_markers(record: dict) -> dict:
    """Drop the marker values whose only clearing events can no longer arrive.

    Called only once a native exit is proved: ``process_exit`` plus settled drains, or a host
    reconstruction that observed the recorded PID/start witness absent and the UUID lock free.
    Returns the dropped values exactly as they were so the caller can journal them as evidence;
    an empty dict means there was nothing stale. Unfinished input, receipts and effects are
    deliberately untouched and still require review.
    """
    stale = {}
    if record.get('native_retry_active'):
        stale['native_retry_active'] = True
    if record.get('background_unsettled') or record.get('background_tasks'):
        stale['background_unsettled'] = record.get('background_unsettled') or False
        stale['background_tasks'] = record.get('background_tasks') or {}
    if record.get('native_wake_pending'):
        stale['native_wake_pending'] = True  # a queued wake-up turn a dead process can never start
    if not stale:
        return {}
    record['native_retry_active'] = False
    record['background_tasks'] = {}
    record['background_unsettled'] = False
    record['native_wake_pending'] = False
    return stale


# @beacon[
#   id=opc@classify_native_result,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Classifies terminal envelopes only; denials and tool errors remain evidence not task verdicts. An authorized interrupt's result is any aborted_* terminal reason (aborted_streaming when it lands mid-request or mid-retry).,
# ]
def classify_result(event, explicit_interrupt=False):
    subtype = str(event.get('subtype', ''))
    terminal = str(event.get('terminal_reason', ''))
    errors = event.get('errors')
    error = event.get('is_error') is True or subtype.startswith('error') or bool(errors)
    if not error:
        return {'category': 'turn_receipt', 'handler': 'retain', 'terminal_error': False,
                'task_acceptance': 'unjudged'}
    text = '\n'.join([str(event.get('result') or ''), json.dumps(errors or [])]).lower()
    if explicit_interrupt and (terminal in ('interrupted', 'aborted') or terminal.startswith('aborted_')
                               or 'interrupt' in text):
        category, handler = 'interrupted', 'record_only'
    elif re.search(r'not logged in|authentication|unauthorized|\b401\b|invalid api.?key|oauth|token expired', text):
        category, handler = 'auth', 'alert_no_retry'
    elif re.search(r'usage credits|extra usage|credit balance|billing|payment|spend limit|entitlement|not entitled|\b402\b|\b403\b', text):
        category, handler = 'financial_entitlement', 'alert_no_retry'
    elif re.search(r'context|prompt is too long|too many tokens', text) or 'context' in subtype:
        category, handler = 'context', 'continuation_needed'
    elif 'max_turns' in subtype or re.search(r'maximum number of turns|turn limit', text):
        category, handler = 'turn_limit', 'continuation_needed'
    elif 'budget' in subtype or re.search(r'budget|maximum cost', text):
        category, handler = 'budget', 'configuration_needed'
    elif re.search(r'unknown option|invalid (?:config|argument|setting)|configuration error', text):
        category, handler = 'invalid_config', 'configuration_needed'
    elif re.search(r'\b429\b|\b529\b|overloaded|hit your .*limit|quota|rate.?limit', text):
        category, handler = 'capacity_quota', 'wait_continue_once'
    elif re.search(r'\b500\b|request timed out|response stream was malformed', text):
        category, handler = 'native_api', 'escalate'
    else:
        category, handler = 'unknown', 'escalate'
    return {'category': category, 'handler': handler, 'terminal_error': True,
            'task_acceptance': 'unjudged'}


def retry_due(event, now):
    """Only explicit machine metadata; human reset text is retained, never guessed as a timestamp."""
    containers = [event]
    for key in ('rate_limit_info', 'retry', 'metadata'):
        if isinstance(event.get(key), dict):
            containers.append(event[key])
    candidates = []
    for value in containers:
        for key, divisor in (('retry_after_ms', 1000), ('retry_delay_ms', 1000), ('retry_after_seconds', 1)):
            seconds = value.get(key)
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds >= 0:
                candidates.append((now + seconds / divisor, key))
        for key in ('resets_at', 'reset_at', 'resetsAt', 'retry_at', 'retry_after'):
            stamp = value.get(key)
            try:
                if key == 'retry_after' and isinstance(stamp, (int, float, str)) and str(stamp).replace('.', '', 1).isdigit():
                    parsed = now + float(stamp)
                elif isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
                    if key == 'retry_after' and stamp < 0:
                        continue
                    parsed = float(stamp) / (1000 if stamp > 1e12 else 1)
                elif isinstance(stamp, str):
                    parsed = (parsedate_to_datetime(stamp).timestamp() if key == 'retry_after'
                              else datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp())
                else:
                    continue
                if math.isfinite(parsed):
                    candidates.append((max(now, parsed), key))
            except (ValueError, TypeError, OverflowError):
                pass
    return max(candidates) if candidates else (now + DEFAULT_RETRY_DELAY, 'conservative_one_hour')


INPUT_ESCALATIONS = ('recovery_failed', 'recovery_uncertain', 'startup_failure', 'ownership_unknown',
                     'reconciliation_uncertain', 'interrupted_missing_result', 'session_directory_missing')
"""Escalations that exist only because their inputs never got a result: releasing those inputs with a disposition
answers them. Auth, billing, model-mismatch and every other alert stay open for their own disposition."""

RECOVERY_BLOCKING_CATEGORIES = ('auth', 'financial_entitlement', 'recovery_failed', 'execution_model_mismatch')
"""Escalations that stop every automatic continue of their session, whatever input they name: no login, a billing or
entitlement problem (no API/overage spend), a recovery that already failed (no resend storm), a native model that
differs from the applied one (cost)."""


def recovery_escalations(store, record, recovery):
    """The pending escalations that concern this recovery: a blocking category, or an incident naming one of the
    recovery's own source inputs. Any other open escalation (an old unrelated error, a settings warning) never
    silently disables a later capacity or crash recovery; it stays listed for its own disposition."""
    sid = record.get('native_uuid') or recovery['execution_id'][3:]
    mids = set(recovery.get('source_message_uuids') or [])
    related = []
    for key in record.get('pending_escalations') or []:
        body = store.get('incidents', sid, key) or {}
        if (body.get('category') in RECOVERY_BLOCKING_CATEGORIES or key == recovery.get('recovery_id')
                or mids & set(body.get('message_uuids') or [])):
            related.append(key)
    return related


def escalation(record, category):
    target = ('Dan' if record.get('role') == 'Manager' or category in ('auth', 'financial_entitlement', 'quota_halt')
              else 'owning_conductor')
    return {'status': 'pending', 'target': target,
            'owning_conductor': record.get('owning_conductor'), 'parent': record.get('parent'),
            'owning_conductor_binding': 'explicit' if record.get('owning_conductor') else 'unbound; adapter must resolve program owner',
            'program': record.get('program'), 'socket': record.get('socket'),
            'delivery': 'local_incident_feed; adapter_delivery_pending'}


# @beacon[
#   id=opc@ClaudeSupervisor,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=One incident feed and journaled single continuations after terminal errors or proved exits; usage limits halt cleanly, with a separate five-hour Manager notification alarm (never a session recovery); silence only observes.,
# ]
class Supervisor:
    def __init__(self, host, clock=time.time, silence_threshold=600):
        if not isinstance(silence_threshold, (int, float)) or not math.isfinite(silence_threshold) or silence_threshold <= 0:
            raise ValueError('positive finite silence observation threshold required')
        self.host, self.store, self.clock = host, host.store, clock
        self.silence_threshold = silence_threshold
        self.actions = {}
        self.attention = {}  # (sid, phase) -> one session's failing part of the tick, cleared by its next clean pass
        self.quota_closers, self.quota_close_next = {}, {}  # sid -> close_halted task / next check (clock seconds)
        from .quota_wake import QuotaWake
        self.quota_wake = QuotaWake(host, clock)

    def incident(self, record, category, handler='retain', source_cursor=None, mids=None,
                 detail=None, attention=False, key=None):
        sid, gen = record['native_uuid'], record['generation']
        key = key or hashlib.sha256(json.dumps([gen, category, source_cursor, mids], sort_keys=True).encode()).hexdigest()
        existing = self.store.get('incidents', sid, key)
        if existing:
            return existing
        body = {'incident_id': key, 'execution_id': 'cc-' + sid, 'native_uuid': sid,
                'generation': gen, 'time': self.clock(), 'category': category, 'handler': handler,
                'source_cursor': source_cursor, 'message_uuids': mids or [], 'detail': detail or {},
                'task_acceptance': 'unjudged', 'escalation': escalation(record, category) if attention else None}
        # Store.event's host rows hook projects this incident in its append batch, including ended sessions.
        cursor = self.store.event(sid, gen, 'incident', body)
        body['cursor'] = cursor
        self.store.put('incidents', sid, body, key)
        if attention:
            ids = record.setdefault('pending_escalations', [])
            if key not in ids:
                ids.append(key)
            self.store.put('sessions', sid, record)
            session = self.host.sessions.get(sid)
            if session is not None and session.record is not record:
                session.update(pending_escalations=ids)
        self.host.changed.set()
        return body

    def activity(self, session):
        now = self.clock()
        onset = session.record.get('silence_onset')
        if onset is not None:
            self.incident(session.record, 'silence_recovered', source_cursor=session.record.get('silence_cursor'),
                          detail={'onset': onset, 'duration_seconds': max(0, now-onset), 'execution_action': 'none'})
        session.update(last_activity_at=now, silence_onset=None, silence_cursor=None)

    # @beacon[
    #   id=opc@Supervisor.result,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Classifies one native result and clears an ended retry; usage-limit results halt rather than continue (five-hour Manager checks are separate), while an ordinary capacity error schedules its one eligible continue.,
    # ]
    def result(self, session, event, cursor, mids, summary):
        interrupted = bool(session.record.get('interrupt_authorized'))
        classification = classify_result(event, interrupted)
        summary.update(classification)
        if user_origin(event.get('origin')):
            session.update(native_retry_active=False, interrupt_authorized=None)
        elif not classification['terminal_error'] or classification['category'] == 'interrupted':
            # An authorized interrupt ended the turn, whatever its origin (a wake-up turn's retry included): the
            # retry that turn was in is over, and the marker would otherwise hold Send, close and the setters.
            session.update(native_retry_active=False)
        if event.get('permission_denials'):
            self.incident(session.record, 'permission_denials', source_cursor=cursor, mids=mids,
                          detail={'permission_denials': event['permission_denials']})
        halt = quota_halt_active(session.record)
        if halt and halt.get('status') in ('interrupting', 'failed'):
            self.quota_halted(session, halt, cursor, mids)  # the turn ended (the host's interrupt, or anyone's)
        if not classification['terminal_error']:
            return
        category, handler = classification['category'], classification['handler']
        background = not user_origin(event.get('origin'))
        incident = self.incident(session.record, category, handler, cursor, mids, summary,
                                attention=handler not in ('record_only', 'wait_continue_once') or background)
        lockout = (usage_lockout(event.get('rate_limit_info') or session.record.get('last_rate_limit_info'),
                                 self.clock()) if handler == 'wait_continue_once' else None)
        if lockout:
            # A usage-limit turn halts; the separate five-hour Manager notice is not a continuation of it.
            self.quota_halt(session, lockout, cursor, mids=mids, interrupt=False)
            return
        if handler == 'wait_continue_once' and not background and mids and all(self.store.get('receipts', session.sid, m) for m in mids):
            metadata = dict(event)
            rate_info = session.record.get('last_rate_limit_info') or {}
            if rate_info.get('status') in ('rejected', 'limited', 'rate_limited', 'blocked'):
                metadata.setdefault('rate_limit_info', rate_info)
            retry = session.record.get('last_retry') or {}
            if retry.get('error_status') in (429, 529):
                metadata.setdefault('retry', retry)
            due, basis = retry_due(metadata, self.clock())
            self.schedule(session.record, incident, 'terminal_error', mids, due, basis)
        elif handler == 'wait_continue_once' and not background:
            self.incident(session.record, 'unattributed_terminal_error', 'escalate', cursor,
                          mids, {'reason': 'no matching accepted input; no automatic continue'}, True)

    # @beacon[
    #   id=opc@Supervisor.quota_halt,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=Records a usage-limit halt and alert, cancels generic recovery and interrupts the retry; five_hour also arms the independent reset+300s Manager check. No session continuation or native alarm; operator Send/close/resume/restart remain open.,
    # ]
    def quota_halt(self, session, lockout, cursor, *, mids=None, interrupt=True):
        """Halt this session for a usage-limit lockout. ``interrupt`` (the native retry is sleeping toward the reset):
        the host's authorized interrupt ends the turn, and ``result`` then marks it halted. Otherwise the turn already
        ended (a terminal error) and it is halted now. No continuation is scheduled for this session. A five-hour
        halt arms a separate Manager recovery-check notice, which coordinates current work rather than replaying it."""
        record = session.record
        self.quota_wake.arm(rate_limit_type=lockout['rate_limit_type'], resets_at=lockout.get('resets_at'), record=record)
        halt = quota_halt_active(record)
        status = (halt or {}).get('status')
        unfinished = status == 'failed' or status == 'interrupting'
        if halt and interrupt and status == 'interrupting' and halt.get('interrupt_state') is None:
            return halt  # its interrupt is still on its way
        if halt and (not unfinished or (interrupt and halt.get('attempts', 0) >= QUOTA_HALT_INTERRUPT_ATTEMPTS)):
            # Already halted in this generation (one incident is enough), or out of automatic attempts.
            if not (interrupt and status in ('halted', 'closing')):
                return halt
            unfinished = False  # a NEW turn of this generation hit the limit again (a wake-up): halt it too
        retry_halt = halt if halt and unfinished else None
        if retry_halt and status == 'interrupting':
            # Answered, yet the native retry carried on: count it as a failed attempt and try again.
            self.incident(record, 'quota_halt_interrupt_failed', 'escalate', cursor,
                detail={'failure': 'the interrupt was answered but the native retry continued',
                        'attempts': retry_halt.get('attempts', 0)},
                key=f"{session.sid}:quota_interrupt:{retry_halt['halt_id']}:{retry_halt.get('attempts', 0)}")
        resets = lockout.get('resets_at')
        when = (datetime.fromtimestamp(resets).astimezone().isoformat(timespec='minutes')
                if resets is not None else 'unknown')
        five_hour = str(lockout['rate_limit_type']).startswith('five_hour')
        next_action = ('the Manager coordinates five-hour recovery after reset + five minutes' if five_hour
                       else 'nothing resumes it automatically')
        authority = (f"usage-limit lockout: {lockout['rate_limit_type']}, resets {when} (host quota halt: the turn "
                     f'ends now; {next_action})')
        incident = (self.store.get('incidents', session.sid, retry_halt['incident_id'])
                    if retry_halt and retry_halt.get('incident_id') else None)
        if incident is None:
            incident = self.incident(record, 'quota_halt', 'alert_no_retry', cursor, mids, {
                **lockout, 'reason': authority, 'automatic_continuation': (
                    'none for this session; separate five-hour Manager check' if five_hour else 'none: no recovery, no timer at the reset'),
                'queued_inputs': 'cancelled with the turn (their texts stay in the receipts)' if interrupt else None,
                'way_back': 'a Send, oc_seat resume or a pane reconnect starts a new generation on the current login'},
                attention=True)
        halt = {'status': 'interrupting' if interrupt else 'halted', 'generation': session.generation,
                'halt_id': str(uuid.uuid4()), **lockout, 'authority': authority, 'detected_at': self.clock(),
                'cursor': cursor,
                'incident_id': incident['incident_id'],
                'attempts': (retry_halt or {}).get('attempts', 0) + (1 if interrupt else 0)}
        if not interrupt:
            halt.update(halted_at=self.clock(), result_cursor=cursor)
        session.update(quota_halt=halt)
        self.cancel_recovery(session, 'usage-limit lockout halt: no automatic continuation')
        session.event('quota_halt', {k: halt[k] for k in ('status', 'rate_limit_type', 'resets_at', 'authority',
                                                           'attempts')})
        if interrupt:
            task = asyncio.create_task(self.quota_interrupt(session, halt))
            session.control_tasks.add(task)
            task.add_done_callback(session.control_tasks.discard)
        return halt

    def quota_lockout(self, session, cursor):
        """Called on every native ``api_retry`` and ``rate_limit_event``: a 429 retry under a rejected usage window is a
        lockout (halt it); a capacity 429, or any other retry, keeps its native retry untouched."""
        record = session.record
        retry = record.get('last_retry') or {}
        if not record.get('native_retry_active') or not (retry.get('error_status') == 429
                                                          or retry.get('error') == 'rate_limit'):
            return None
        lockout = usage_lockout(record.get('last_rate_limit_info'), self.clock())
        return self.quota_halt(session, lockout, cursor) if lockout else None

    async def quota_interrupt(self, session, halt):
        """The host's own authorized interrupt for a quota halt (the documented control path, never a signal); a
        failure is recorded and the next retry frame tries again, up to QUOTA_HALT_INTERRUPT_ATTEMPTS times."""
        from .controls import dispatch_control
        try:
            receipt = await dispatch_control(session, 'interrupt', {'generation': halt['generation'],
                'authority': halt['authority'], 'cancel_queued': True, 'reason': 'usage-limit lockout'})
            state, failure = receipt.get('state'), receipt.get('disclosure')
        except Exception as error:
            receipt, state, failure = {}, 'failed', type(error).__name__ + ': ' + str(error)[:200]
        current = quota_halt_active(session.record)
        if not current or current.get('halt_id') != halt['halt_id']:
            return  # cleared by an operator Send, or a newer halt replaced this one
        current = {**current, 'interrupt_request_id': receipt.get('request_id'), 'interrupt_state': state}
        if current.get('status') != 'interrupting' or (state in ('replied', 'uncertain')
                                                        and session.process.returncode is None):
            session.update(quota_halt=current)  # the interrupted turn's result marks (or already marked) it halted
            return
        if session.process.returncode is not None:
            current.update(status='halted', halted_at=self.clock())  # the process ended: nothing sleeps any more
            session.update(quota_halt=current)
            return
        current.update(status='failed', failure=failure or state)
        session.update(quota_halt=current)
        self.incident(session.record, 'quota_halt_interrupt_failed', 'escalate', current.get('cursor'),
            detail={'failure': current['failure'], 'attempts': current['attempts'],
                    'next': ('the next native retry frame tries again' if current['attempts'] <
                             QUOTA_HALT_INTERRUPT_ATTEMPTS else 'interrupt it from the pane or the host by hand')},
            attention=current['attempts'] >= QUOTA_HALT_INTERRUPT_ATTEMPTS,
            key=f"{session.sid}:quota_interrupt:{current['halt_id']}:{current['attempts']}")

    def quota_halted(self, session, halt, cursor, mids):
        """The interrupted turn's result arrived: the session is halted (its process closes once provably idle)."""
        session.update(quota_halt={**halt, 'status': 'halted', 'halted_at': self.clock(), 'result_cursor': cursor,
                                   'result_message_uuids': mids or []})
        session.event('quota_halted', {'result_cursor': cursor, 'message_uuids': mids or []})

    def cancel_recovery(self, session, disposition):
        """A still-scheduled automatic continue of this session is cancelled (journaled), never sent."""
        rid = session.record.get('pending_recovery')
        recovery = self.store.get('recoveries', session.sid, rid) if rid else None
        if recovery and recovery.get('status') == 'scheduled' and ('cc-' + session.sid, rid) not in self.actions:
            recovery.update(status='cancelled', operator_disposition=disposition)
            self.store.put('recoveries', session.sid, recovery, rid)
            session.update(pending_recovery=None)

    def consider_quota_close(self, session, now):
        """A halted session's process holds the old login and nothing useful: close its input once it is provably
        idle (the operator-close rule, plus no wake-up due), so the guarded restart is never blocked by it and the next
        Send or resume starts a new generation. Checked every few seconds; never a signal, never a refusal."""
        halt = quota_halt_active(session.record)
        if (not halt or halt.get('status') != 'halted' or session.process.returncode is not None
                or session.record.get('state') not in ('ready', 'launch_blocked')):
            return
        sid = session.sid
        running = self.quota_closers.get(sid)
        if (running is not None and not running.done()) or now < self.quota_close_next.get(sid, 0):
            return
        self.quota_close_next[sid] = now + 5
        self.quota_closers[sid] = asyncio.create_task(self.close_halted(session))

    async def close_halted(self, session):
        from .collection import UNKNOWN_LEVEL, settle_for_restart
        from .settlement import settlement_blockers
        if session.command_lock.locked() or session.collection_in_progress:
            return 'busy'
        holds = settlement_blockers(session.record, automatic=False)
        if session.record.get('native_wake_pending'):
            holds.append('a background task ended; its wake-up turn is due')
        if holds == [UNKNOWN_LEVEL]:
            # The guarded restart's own readback (no stop): idle with an empty fresh task level settles it.
            outcome = await settle_for_restart(self.host, session, allow_stop=False)
            if outcome['outcome'] != 'settled':
                return outcome['outcome']
        elif holds:
            return 'held'
        async with session.command_lock:
            halt = quota_halt_active(session.record)
            if not halt or halt.get('status') != 'halted' or session.record.get('native_wake_pending'):
                return 'changed'
            try:
                session.close_input(reason='quota_halt')
            except RuntimeError:
                return 'held'
            session.update(quota_halt={**halt, 'status': 'closing', 'closing_at': self.clock()})
        return 'closing'

    def schedule(self, record, incident, reason, mids, due, basis):
        sid = record['native_uuid']
        if quota_halt_active(record):
            # A usage-limit halt is a stopped session: only the operator's Send, resume or reconnect runs it again.
            self.incident(record, 'recovery_withheld', 'retain', incident['cursor'], mids,
                          {'reason': 'usage-limit lockout halt: no automatic continuation', 'would_be': reason})
            return None
        if record.get('role') == 'Manager':
            # Dan's ruling (Manager-Session-208): a Manager is NEVER continued automatically after a crash, lockout or
            # restart. Only Dan knows what changed meanwhile; an automatic `continue` acts on stale context. His next
            # Send reconnects it.
            self.incident(record, 'recovery_withheld', 'escalate', incident['cursor'], mids,
                          {'reason': 'Managers are never continued automatically (Dan); his next Send reconnects it',
                           'would_be': reason}, True)
            return None
        # One automatic continuation per recovery lineage. A second failing recovery blocks loudly.
        source_receipts = [self.store.get('receipts', sid, mid) for mid in mids]
        if any(r and r.get('recovery_id') for r in source_receipts) or record.get('recovery_attempt_failed'):
            self.incident(record, 'recovery_failed', 'escalate', incident['cursor'], mids,
                          {'reason': 'automatic recovery already attempted; no respawn/resend storm'}, True)
            record['recovery_attempt_failed'] = True
            self.store.put('sessions', sid, record)
            return None
        rid = incident['incident_id']
        existing = self.store.get('recoveries', sid, rid)
        if existing:
            return existing
        if any(r['status'] not in ('completed', 'blocked', 'cancelled') for r in self.store.records('recoveries', sid)):
            return None
        recovery = {'recovery_id': rid, 'execution_id': 'cc-' + sid, 'generation': record['generation'],
            'reason': reason, 'incident_cursor': incident['cursor'], 'source_message_uuids': mids,
            'due_at': due, 'delay_basis': basis, 'action_uuid': str(uuid.uuid4()),
            'submitted': RECOVERY_TEXT, 'status': 'scheduled', 'original_replay': False,
            'effects': 'uncertain; inspect receipts/postconditions; never assume rollback'}
        self.store.put('recoveries', sid, recovery, rid)
        record['pending_recovery'] = rid
        self.store.put('sessions', sid, record)
        self.incident(record, 'recovery_scheduled', 'wait', incident['cursor'], mids,
                      {'recovery_id': rid, 'action_uuid': recovery['action_uuid'], 'due_at': due, 'delay_basis': basis})
        return recovery

    def exited(self, session, cursor):
        # Called only AFTER both drains; a buffered matching result may follow PID exit.
        stale = clear_stale_native_markers(session.record)
        if stale:
            session.update(native_retry_active=False, background_tasks={}, background_unsettled=False,
                           native_wake_pending=False)
            self.incident(session.record, 'native_markers_cleared', 'retain', cursor,
                detail={'reason': 'proved process exit; the retry/background clearing events can no '
                                 'longer arrive, so the stale markers are dropped, not blocking',
                        'cleared': stale})
        if session.pending and session.record.get('retention_gap'):
            # Output was lost while storage failed: a result may have answered these inputs unseen, so a
            # generic `continue` is not judged here. Inspect, then release or follow up.
            self.incident(session.record, 'reconciliation_uncertain', 'escalate', cursor, sorted(session.pending),
                {'reason': 'proved exit after a durability gap; a result may have been lost, no automatic continue',
                 'retention_gap': session.record['retention_gap']}, True)
            return
        halt = quota_halt_active(session.record)
        if halt and halt.get('status') != 'closed':
            session.update(quota_halt={**halt, 'status': 'closed', 'closed_at': self.clock(),
                                       'exit_code': session.process.returncode})
        if session.pending and halt:
            # Halted for a usage limit: its unanswered inputs wait for the operator, never for a crash recovery.
            self.incident(session.record, 'quota_halt_missing_result', 'escalate', cursor, sorted(session.pending),
                {'effects': 'uncertain; usage-limit halt: no automatic continue', 'authority': halt.get('authority')},
                True)
            return
        if session.pending:
            if session.record.get('interrupt_authorized'):
                self.incident(session.record, 'interrupted_missing_result', 'escalate', cursor,
                    sorted(session.pending), {'effects': 'uncertain; explicit interrupt never auto-continues'}, True)
                return
            incident = self.incident(session.record, 'crash_missing_result', 'resume_same_uuid', cursor,
                sorted(session.pending), {'exit_code': session.process.returncode, 'effects': 'uncertain'})
            if not session.record.get('initialized') or session.record.get('state_before_exit') == 'launch_blocked':
                self.incident(session.record, 'startup_failure', 'alert_no_retry', cursor,
                              sorted(session.pending), attention=True)
                return
            self.schedule(session.record, incident, 'crash', sorted(session.pending), self.clock(), 'proved_exit')
        elif stale.get('background_unsettled') or stale.get('background_tasks'):
            # A child task may outlive the CLI; that observation stays visible evidence, never a
            # lockout: the stale markers are already cleared above.
            self.incident(session.record, 'background_exit_unknown', 'retain', cursor,
                detail={'background_tasks': stale.get('background_tasks'),
                        'reason': 'proved process exit with background tasks unsettled; stale markers '
                                  'cleared, child exit/effects require review'})
        elif (session.process.returncode and not session.record.get('last_result_cursor')
                and session.record.get('close_reason') != 'restart_process_stop'):
            # A guarded restart's proved stop (collection.settle_for_restart) is no startup failure.
            self.incident(session.record, 'startup_failure', 'alert_no_retry', session.record.get('last_stderr_cursor') or cursor,
                          detail={'exit_code': session.process.returncode}, attention=True)

    # @beacon[
    #   id=opc@Supervisor.run,
    #   slice_labels=opc-basic,
    #   kind=ast,
    #   comment=The one-second observation loop. A failure inside one session's part of a tick stays that session's (Supervisor.session_failure); only an unattributable one sets the host's supervision_error, which the next clean tick clears. No session is ever marked failed_storage from here.,
    # ]
    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception as error:
                self.host_failure(error)
            else:
                self.host_passed()
            await asyncio.sleep(1)  # observation cadence, NEVER an execution deadline

    def host_failure(self, error):
        """A tick failure no single session explains (the recovery journal itself unreadable). It is a host-level
        observation in health, cleared by the next clean tick; it never marks a session: each session's own writes
        declare its own durability."""
        now, name = self.clock(), type(error).__name__
        detail = self.host.supervision_error_detail
        if not detail or detail.get('error') != name:
            detail = {'error': name, 'first_at': now, 'count': 0}
        detail.update(last_at=now, count=detail['count'] + 1)
        self.host.supervision_error, self.host.supervision_error_detail = name, detail

    def host_passed(self):
        if self.host.supervision_error is not None:
            self.host.last_supervision_error = {**(self.host.supervision_error_detail or {}), 'cleared_at': self.clock()}
            self.host.supervision_error = self.host.supervision_error_detail = None

    def session_failure(self, sid, phase, error):
        """One session's part of a tick failed (a malformed record, a missing directory, a bug): recorded for that
        session alone, in health, its state and one incident. Never a durability verdict on it or on any other
        session; its next clean pass clears it. Only the exception's type is kept, never its text."""
        now, name = self.clock(), type(error).__name__
        entry = self.attention.get((sid, phase))
        if entry and entry['error'] == name:
            entry.update(last_at=now, count=entry['count'] + 1)
            return
        self.attention[(sid, phase)] = {'phase': phase, 'error': name, 'first_at': now, 'last_at': now, 'count': 1}
        try:
            record = self.store.get('sessions', sid)
            if record:
                self.incident(record, 'supervision_failure', 'retain',
                    detail={'phase': phase, 'failure': name, 'execution_action': 'none; other sessions unaffected'},
                    key=f"{sid}:supervision:{phase}:{name}:{record.get('generation')}")
        except Exception:
            pass  # the in-memory entry (health, state) is the truthful surface then
        self.host.changed.set()

    def session_passed(self, sid, phase):
        if self.attention.pop((sid, phase), None) is not None:
            self.host.changed.set()

    def attention_for(self, sid):
        return [dict(entry) for (key, _), entry in self.attention.items() if key == sid]

    async def tick(self):
        await self.host.lifecycle_tick()
        now = self.clock()
        self.quota_wake.tick(now)
        for session in list(self.host.sessions.values()):
            try:
                self.observe_silence(session, now)
            except Exception as error:
                self.session_failure(session.sid, 'silence', error)
            else:
                self.session_passed(session.sid, 'silence')
            try:
                self.consider_quota_close(session, now)
            except Exception as error:
                self.session_failure(session.sid, 'quota_halt', error)
            else:
                self.session_passed(session.sid, 'quota_halt')
        for recovery in self.store.records('recoveries'):
            sid = str(recovery.get('execution_id') or '')[3:] or '?'
            try:
                self.consider_recovery(recovery, now)
            except Exception as error:
                self.session_failure(sid, 'recovery', error)
            else:
                self.session_passed(sid, 'recovery')

    def observe_silence(self, session, now):
        record = session.record
        observable = (session.pending or not record.get('initialized') or record.get('native_retry_active')
                      or record.get('background_unsettled') or record.get('setting_control_pending'))
        if (session.process.returncode is None and observable and not session.infrastructure_error
                and record.get('silence_onset') is None):
            last = record.get('last_activity_at', now)
            if now-last >= self.silence_threshold:
                incident = self.incident(record, 'long_silence', source_cursor=record.get('last_native_cursor'),
                    detail={'onset': last, 'duration_seconds': now-last, 'execution_action': 'none'})
                session.update(silence_onset=last, silence_cursor=incident['cursor'])

    def consider_recovery(self, recovery, now):
        key = (recovery['execution_id'], recovery['recovery_id'])
        if recovery['status'] != 'scheduled' or key in self.actions:
            return
        sid = recovery['execution_id'][3:]
        record = self.host.state(sid)
        if record.get('archived'):
            return  # forget owns the remaining rows; never schedule a tombstone's recovery
        if record.get('role') == 'Manager':
            # Never run, even if scheduled before Dan's ruling (Manager-Session-208): cancel it at once, journaled,
            # not only when it falls due (a pending recovery would otherwise hold close/restart until then).
            recovery.update(status='cancelled', operator_disposition='Managers are never continued automatically (Dan)')
            self.store.put('recoveries', sid, recovery, recovery['recovery_id'])
            if record.get('pending_recovery') == recovery['recovery_id']:
                live = self.host.sessions.get(sid)
                if live:
                    live.update(pending_recovery=None)
                else:
                    record['pending_recovery'] = None
                    self.store.put('sessions', sid, record)
            return
        if (record.get('native_retry_active') or record.get('background_unsettled') or quota_halt_active(record)
                or recovery_escalations(self.store, record, recovery) or record.get('infrastructure_error')):
            return
        session = self.host.sessions.get(sid)
        if recovery['reason'] == 'crash' or record.get('owner') == 'gone':
            if record.get('owner') != 'gone':
                return  # EOF/draining/live/unknown never proves a safe resume
        elif not session or session.pending or record.get('setting_control_pending'):
            return
        self.actions[key] = asyncio.create_task(self.execute(recovery))

    async def execute(self, recovery):
        sid, rid = recovery['execution_id'][3:], recovery['recovery_id']
        record = self.host.state(sid)
        if record.get('archived'):
            return
        # Durable claim precedes ANY launch/write: restart cannot repeat an uncertain action.
        recovery['status'] = 'claimed'
        self.store.put('recoveries', sid, recovery, rid)
        try:
            if record.get('owner') == 'gone':
                resumed = await self.host.resume_snapshot(sid)
                recovery['resumed_generation'] = resumed['generation']
                self.store.put('recoveries', sid, recovery, rid)
            session = self.host.sessions[sid]
            while not session.record.get('initialized') or session.record.get('setting_control_pending'):
                if session.process.returncode is not None or session.record.get('state') == 'launch_blocked':
                    raise RuntimeError('recovery initialization failed')
                await asyncio.sleep(.05)  # pending native initialize/readback has no deadline
            if session.record.get('native_retry_active') or session.record.get('background_unsettled'):
                raise RuntimeError('native work became unsettled; no recovery input sent')
            if session.record.get('settings_mismatch') or recovery_escalations(self.store, session.record, recovery):
                raise RuntimeError('settings mismatch or an escalation about this recovery; recovery input withheld')
            for mid in recovery['source_message_uuids']:
                receipt = self.store.get('receipts', sid, mid)
                if receipt and not receipt.get('completed'):
                    receipt['superseded_by_recovery'] = recovery['action_uuid']
                    receipt['effects'] = 'uncertain'
                    self.store.put('receipts', sid, receipt, mid)
                session.pending.discard(mid)
            session.update(pending_message_uuids=sorted(session.pending))
            receipt = await session.send(recovery['action_uuid'], recovery['submitted'], recovery['submitted'], recovery_id=rid)
            recovery.update(status='accepted', message_receipt=receipt['message_uuid'], generation=session.generation)
            self.store.put('recoveries', sid, recovery, rid)
            self.incident(session.record, 'recovery_continue', 'inspect_then_continue', receipt['intent_cursor'],
                          [receipt['message_uuid']], {'recovery_id': rid, 'original_replay': False})
        except Exception as error:
            if (self.store.get('sessions', sid) or {}).get('archived'):
                return  # forget may have committed while auth yielded; do not recreate its journal
            recovery.update(status='blocked', failure=type(error).__name__)
            self.store.put('recoveries', sid, recovery, rid)
            session = self.host.sessions.get(sid)
            if session is not None:
                session.update(recovery_attempt_failed=True)  # the live record, never a copy of it
                record = session.record
            else:
                record = self.store.get('sessions', sid)
                record['recovery_attempt_failed'] = True
                self.store.put('sessions', sid, record)
            self.incident(record, 'recovery_failed', 'escalate', recovery['incident_cursor'],
                          recovery['source_message_uuids'], {'failure': type(error).__name__}, True)

    def recovery_result(self, session, receipt, classification):
        rid = receipt.get('recovery_id')
        if not rid:
            return
        recovery = self.store.get('recoveries', session.sid, rid)
        if recovery:
            recovery['status'] = 'blocked' if classification['terminal_error'] else 'completed'
            recovery['result_cursor'] = receipt['results'][-1]['cursor']
            self.store.put('recoveries', session.sid, recovery, rid)
            session.update(pending_recovery=None, recovery_attempt_failed=classification['terminal_error'])

    def resolve_incident(self, sid, incident_id, disposition):
        if not isinstance(disposition, str) or not disposition.strip():
            raise ValueError('explicit nonempty operator disposition required')
        body = self.store.get('incidents', sid, incident_id)
        if body is None:
            raise ValueError('unknown incident')
        body['disposition'] = disposition
        if body.get('escalation'):
            body['escalation']['status'] = 'acknowledged'
        self.store.put('incidents', sid, body, incident_id)
        record = self.host.state(sid)
        record['pending_escalations'] = [i for i in record.get('pending_escalations', []) if i != incident_id]
        self.store.put('sessions', sid, record)
        if sid in self.host.sessions:
            self.host.sessions[sid].update(pending_escalations=record['pending_escalations'])
        self.incident(record, 'incident_disposition', source_cursor=body['cursor'],
                      detail={'incident_id': incident_id, 'disposition': disposition},
                      key=incident_id + ':disposition:' + hashlib.sha256(disposition.encode()).hexdigest())
        return body

    def resolve_recovery(self, sid, recovery_id, disposition):
        if not isinstance(disposition, str) or not disposition.strip():
            raise ValueError('explicit nonempty operator disposition required')
        recovery = self.store.get('recoveries', sid, recovery_id)
        if recovery is None or recovery['status'] not in ('scheduled', 'blocked', 'completed'):
            raise RuntimeError('claimed/accepted recovery is uncertain or active; never cancel/replay blindly')
        # Only work a live owner may still be doing holds it; a proved-gone owner, or inputs an earlier
        # generation accepted, never do (no process can still answer them).
        holds = self.host.live_work(sid)
        if holds:
            raise RuntimeError('native work still unsettled (' + '; '.join(holds) + '); inspect/wait or explicitly '
                               'interrupt first')
        session = self.host.sessions.get(sid)
        recovery['operator_disposition'] = disposition
        if recovery['status'] == 'scheduled':
            recovery['status'] = 'cancelled'
        self.store.put('recoveries', sid, recovery, recovery_id)
        record = self.host.state(sid)
        if record.get('pending_recovery') in (None, recovery_id):
            record.update(pending_recovery=None, recovery_attempt_failed=False)
            self.store.put('sessions', sid, record)
            if session:
                session.update(pending_recovery=None, recovery_attempt_failed=False)
        self.incident(record, 'recovery_disposition', source_cursor=recovery['incident_cursor'],
                      detail={'recovery_id': recovery_id, 'disposition': disposition})
        return recovery

    # @beacon[
    #   id=opc@Supervisor.release_input,
    #   slice_labels=opc-basic,opc-claude-seats,
    #   kind=ast,
    #   comment=The operator's general way out of a pending input no process can answer (owner proved gone, an earlier generation's input, a never-written one, or a live idle process whose result was lost): released with a disposition, never replayed, effects uncertain; the recovery covering it and the escalations only about it are dispositioned too. One a process may still answer is held, with the reason.,
    # ]
    def release_input(self, sid, disposition, message_uuids=None, recovery_id=None):
        """Release unfinished inputs: every unreleased one (default), the named ``message_uuids``, or the source
        inputs of ``recovery_id``. Returns what was released, what is held (and why), and what it dispositioned."""
        from .host import native_id, released_receipt
        if not isinstance(disposition, str) or not disposition.strip():
            raise ValueError('explicit nonempty operator disposition required')
        if message_uuids is not None and (not isinstance(message_uuids, list) or
                                          not all(isinstance(mid, str) for mid in message_uuids)):
            raise ValueError('message_uuids must be a list of message UUIDs')
        record = self.host.state(sid)
        rid = recovery_id or record.get('pending_recovery')
        recovery = self.store.get('recoveries', sid, rid) if rid else None
        if recovery_id and recovery is None:
            raise ValueError('unknown recovery')
        action = self.actions.get(('cc-' + sid, rid))
        if recovery and (recovery['status'] in ('claimed', 'accepted') or (action is not None and not action.done())):
            raise RuntimeError('an automatic recovery is sending its continue right now; wait a moment, then release')
        receipts = {r['message_uuid']: r for r in self.store.receipts(sid)}
        if message_uuids is not None:
            targets = [native_id(mid) for mid in message_uuids]
        elif recovery_id:
            targets = list(recovery.get('source_message_uuids') or [])
        else:
            targets = sorted(mid for mid, r in receipts.items() if not r.get('completed') and not released_receipt(r))
        answer = {'execution_id': 'cc-' + sid, 'released': [], 'held': [], 'completed': [], 'already_released': [],
                  'recovery': None, 'acknowledged_escalations': [], 'message_replayed': False}
        for mid in targets:
            receipt = receipts.get(mid)
            if receipt is None:
                raise ValueError(f'unknown message UUID {mid}')
            if receipt.get('completed'):
                answer['completed'].append(mid)
            elif released_receipt(receipt):
                answer['already_released'].append(mid)
            elif (reason := self.host.input_hold(sid, receipt)):
                answer['held'].append({'message_uuid': mid, 'reason': reason})
            else:
                receipt['released_by_operator'] = {'disposition': disposition, 'at': time.time(),
                    'from_generation': receipt.get('generation'), 'released_at_generation': record.get('generation'),
                    'effects': 'uncertain; inspect the transcript and postconditions before relying on or repeating it'}
                if receipt.get('status') != 'withheld':
                    receipt['status'] = 'uncertain'
                self.store.put('receipts', sid, receipt, mid)
                answer['released'].append(mid)
        released = set(answer['released'])
        session = self.host.sessions.get(sid)
        if released:
            if session is not None:
                session.pending -= released
                session.update(pending_message_uuids=sorted(session.pending))
            else:
                stored = self.store.get('sessions', sid)
                stored['pending_message_uuids'] = [m for m in stored.get('pending_message_uuids') or [] if m not in released]
                self.store.put('sessions', sid, stored)
            self.incident(self.host.state(sid), 'input_released', 'retain', mids=sorted(released),
                detail={'disposition': disposition, 'message_replayed': False,
                        'effects': 'uncertain; never replayed, never called completed'})
        # The recovery covering these inputs has nothing left to continue once none of its inputs is held.
        if recovery and recovery['status'] in ('scheduled', 'blocked', 'completed'):
            sources = set(recovery.get('source_message_uuids') or [])
            held = {row['message_uuid'] for row in answer['held']}
            settled = {mid for mid, r in receipts.items() if r.get('completed') or released_receipt(r)} | released
            if not (sources & held) and (recovery_id or sources <= settled):
                answer['recovery'] = self.resolve_recovery(sid, recovery['recovery_id'], 'inputs released: ' + disposition)
        # Escalations that exist only because these inputs never got a result are answered by this disposition.
        settled = {mid for mid, r in receipts.items() if r.get('completed') or released_receipt(r)} | released
        for iid in list(self.host.state(sid).get('pending_escalations') or []):
            body = self.store.get('incidents', sid, iid) or {}
            mids = set(body.get('message_uuids') or [])
            if body.get('category') in INPUT_ESCALATIONS and mids & released and mids <= settled:
                self.resolve_incident(sid, iid, disposition)
                answer['acknowledged_escalations'].append(iid)
        answer['pending_escalations'] = list(self.host.state(sid).get('pending_escalations') or [])
        return answer
