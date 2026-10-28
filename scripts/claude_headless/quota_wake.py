"""Durable quota-coordination notices through one reconnect/exact-receipt delivery path."""
import asyncio
from datetime import datetime
import math
import time
import uuid


OPERATOR_STATUSES = ('deferred_by_operator', 'cancelled_by_operator')


def reset_epoch(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value) / (1000 if value > 1e12 else 1)


def episode_key(reset):
    # The same native window can report whole seconds in one field and milliseconds in another.
    return str(int(reset))


def when(value):
    try:
        stamp = (datetime.fromtimestamp(value) if isinstance(value, (int, float))
                 else datetime.fromisoformat(value.replace('Z', '+00:00')))
        return stamp.astimezone().isoformat(timespec='seconds')
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        return 'unknown'


# @beacon[
#   id=opc@record_account_switch_notice,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Persists an explicitly requested account-switch notice: pending before stop then immutable complete/failed with one retained message UUID; finalization preserves exact-episode operator controls and never decides to switch.,
# ]
def record_account_switch_notice(store, *, notice_id, recipient=None, requested_account=None,
                                 outcome='pending', report=None, account=None, quota=None,
                                 failure=None, clock=time.time):
    """Finalize without native input, even offline; keep any exact-episode operator stop intact."""
    key = 'account-switch:' + notice_id
    episode = store.get('quota_checks', '', key)
    if episode is None:
        if not recipient or not requested_account or outcome != 'pending':
            raise ValueError('an account-switch notice must be staged before the stop')
        episode = {'episode_id': key, 'kind': 'account_switch', 'detected_at': clock(),
                   'status': 'pending_switch', 'due_at': None, 'recipient': recipient,
                   'requested_recipient': recipient, 'requested_account': requested_account,
                   'message_uuid': str(uuid.uuid4())}
    elif episode.get('outcome') in ('complete', 'failed'):
        return episode  # final outcome and its exact input are immutable, even after a lost reply
    if outcome not in ('pending', 'complete', 'failed'):
        raise ValueError('invalid account-switch outcome')
    if outcome != 'pending':
        phases = (report or {}).get('phases') or {}
        controlled_status = episode.get('status') if episode.get('status') in OPERATOR_STATUSES else None
        episode.update(outcome=outcome, status='scheduled', due_at=clock(), finalized_at=clock(), switch_failure=failure,
                       account=account, quota=quota,
                       closed=phases.get('closed_idle') or [],
                       stopped=phases.get('stopped_on_process_tree_proof') or [],
                        host_exit_observed=bool(phases.get('host_exit_observed')),
                        host_started=phases.get('host_started'))
        if controlled_status:
            episode.update(status=controlled_status, due_at=None)
    store.put('quota_checks', '', episode, key)
    return episode


# @beacon[
#   id=opc@QuotaWake,
#   slice_labels=opc-basic,opc-claude-seats,opc-claude-restart,
#   kind=ast,
#   comment=One durable reset+300s or lifecycle-outcome delivery owner: current-Manager reconnect/readiness and exact controls precede immutable recipient/generation/text/UUID admission; any admitted receipt reconciles without replay or retarget, independently of physical recovery/publication.,
# ]
class QuotaWake:
    def __init__(self, host, clock):
        self.host, self.store, self.clock = host, host.store, clock
        self.tasks = {}
        self.admissions = {}  # only exact notice Send tasks, never a reconnect or watched/native task

    def save(self, episode, **changes):
        # Offline lifecycle finalization can race this host connection. Writer admission
        # precedes read/merge; publication/effect/controls are never overwritten by old rows.
        with self.store.batch():
            self.store.db.execute('UPDATE quota_checks SET body=body WHERE sid=? AND rid=?', ('', episode['episode_id']))
            previous = self.store.get('quota_checks', '', episode['episode_id'])
            retained = (previous or {}).get('submission')
            if retained:
                for field in ('recipient', 'generation', 'submitted', 'message_uuid'):
                    if field in changes and changes[field] != retained[field]:
                        raise ValueError('notice submission identity is immutable after possible Send admission')
            if previous and previous.get('status') in OPERATOR_STATUSES:
                if changes.get('status') and changes['status'] not in OPERATOR_STATUSES:
                    changes['delivery_status'] = changes.pop('status')
                changes['due_at'] = None
            latest = {**(previous if previous is not None else episode), **changes}
            episode.clear()
            episode.update(latest)
            if previous != episode:
                self.store.put('quota_checks', '', episode, episode['episode_id'])
                self.host.changed.set()
        return episode

    def lifecycle_manager(self, episode, **facts):
        if episode.get('kind') != 'observer_recovery':
            return
        from .observer_recovery import ObserverRecovery
        owner = ObserverRecovery(self.store)
        record = owner.read(episode['operation_id'])
        if record and record.get('transition'):
            owner.transition_update(episode['operation_id'], fields={'manager': {'policy': 'current', **facts}})

    def receipt(self, episode):
        retained = episode.get('submission') or episode
        recipient = retained.get('recipient')
        return self.store.get('receipts', recipient[3:], retained['message_uuid']) if recipient else None

    def display(self, episode):
        """One read-only alarm projection for control replies and halted session state."""
        receipt = self.receipt(episode)
        return {**{k: episode.get(k) for k in (
            'episode_id', 'resets_at', 'due_at', 'original_due_at', 'status', 'recipient', 'message_uuid',
            'failure', 'sent_at', 'delivery_status', 'operator_control', 'operator_defer', 'operator_cancel',
            'admission_cancel_requested', 'admission_cancel_confirmed')}, 'send_admitted': receipt is not None,
            'receipt_status': receipt.get('status') if receipt else None}

    # @beacon[
    #   id=opc@QuotaWake.control_episode,
    #   slice_labels=opc-claude-seats,
    #   kind=ast,
    #   comment=Synchronously journals an exact alarm's operator defer/cancel and authority with its original deadline; blocks pre-admission notice tasks without cancelling reconnect or an admitted Send and returns the retained record/display/exact receipt.,
    # ]
    def control_episode(self, episode: str, action: str, authority: str):
        """Stop this alarm, not its obligations or an already admitted Manager input.

        Runs on the host event loop, like arm/save. A Send can yield on its command lock before
        admission: cancel only that private notice task when no exact receipt exists. Reconnects
        finish normally and recheck the durable control before any Send.
        """
        if not isinstance(episode, str) or not episode:
            raise ValueError('exact episode id required')
        if action not in ('defer', 'cancel'):
            raise ValueError('episode action must be defer or cancel')
        if not isinstance(authority, str) or not authority.strip():
            raise ValueError('operator authority required')
        status = 'deferred_by_operator' if action == 'defer' else 'cancelled_by_operator'
        with self.store.batch():
            record = self.store.get('quota_checks', '', episode)
            if record is None:
                raise ValueError('unknown quota-check episode: ' + episode)
            self.reconcile_receipt(record)
            receipt = self.receipt(record)
            previous = record.get('operator_control') or {}
            original_due = record.get('original_due_at', record.get('due_at'))
            if original_due is None:
                original_due = (record.get('operator_defer') or {}).get('original_due_at')
            if previous.get('status') != status or previous.get('authority') != authority:
                control = {'action': action, 'status': status, 'authority': authority, 'at': self.clock(),
                           'original_due_at': original_due, 'previous_status': record['status'],
                           'message_uuid': record['message_uuid'], 'recipient': record.get('recipient'),
                           'send_admitted': receipt is not None}
                control['event_cursor'] = self.store.event('', 0, 'quota_episode_control', control,
                                                          arrival_time=control['at'])
                history = [*(record.get('operator_controls') or []), control]
                self.save(record, status=status, due_at=None, original_due_at=original_due,
                          delivery_status=record.get('delivery_status') or record['status'],
                          operator_control=control, operator_controls=history,
                          **{'operator_' + action: control})
        # Intent is committed before stopping any pre-admission Send task. Existing receipts always win.
        pending = [task for task in self.admissions.get(episode, ()) if not task.done()]
        if receipt is None and pending:
            for task in pending:
                task.cancel()
            self.save(record, admission_cancel_requested=True)
        return {'episode_id': episode, 'action': action, 'status': record['status'], 'due_at': record['due_at'],
                'record': record, 'display': self.display(record), 'receipt': receipt, 'message_replayed': False}

    def enabled(self, episode):
        return (episode is not None and episode.get('status') not in (*OPERATOR_STATUSES, 'sent')
                and episode.get('due_at') is not None and self.clock() >= episode['due_at'])

    def arm(self, *, rate_limit_type, resets_at, record=None, program=None, socket_handle=None, role=None):
        """Called only for an observed halt or a real refused launch, never a quota/preflight read."""
        if not isinstance(rate_limit_type, str) or not rate_limit_type.startswith('five_hour'):
            return None
        record = record or {}
        reset = reset_epoch(resets_at)
        key = episode_key(reset) if reset is not None else 'unknown:' + str(record.get('execution_id')) + ':' + str(record.get('generation'))
        episode = self.store.get('quota_checks', '', key)
        if episode is None:
            episode = {'episode_id': key, 'rate_limit_type': 'five_hour', 'resets_at': reset,
                'due_at': reset + 300 if reset is not None else None, 'detected_at': self.clock(),
                'original_due_at': reset + 300 if reset is not None else None,
                'status': 'scheduled' if reset is not None else 'delivery_problem',
                'message_uuid': str(uuid.uuid4()), 'recipient': None, 'affected': []}
            if reset is None:
                episode['failure'] = 'structured five-hour reset unavailable; no guessed alarm'
        affected = {'execution_id': record.get('execution_id'), 'generation': record.get('generation'),
            'program': record.get('program') or program, 'socket': record.get('socket') or socket_handle,
            'role': record.get('role') or role, 'source': 'quota_halt' if record else 'launch_guard'}
        if affected not in episode['affected']:
            episode['affected'].append(affected)
        return self.save(episode, affected=episode['affected'])

    def for_record(self, record):
        halt = record.get('quota_halt') or {}
        if not str(halt.get('rate_limit_type') or '').startswith('five_hour'):
            return None
        reset = reset_epoch(halt.get('resets_at'))
        key = episode_key(reset) if reset is not None else 'unknown:' + str(record.get('execution_id')) + ':' + str(record.get('generation'))
        episode = self.store.get('quota_checks', '', key)
        if episode is None:
            return None
        return {**self.display(episode), 'resets_at': reset}

    def current_manager(self):
        # Chosen on the record cache: a stored winner is answered as a fresh copy, a live one as its own record.
        stored = {r['native_uuid']: (r, text) for _, r, text in self.store.cached_sessions()
                  if r.get('role') == 'Manager'}
        records = {sid: r for sid, (r, _) in stored.items()}
        records.update({sid: s.record for sid, s in self.host.sessions.items() if s.record.get('role') == 'Manager'})
        def recency(r):
            return (r.get('last_input_at') or r.get('first_launched_at') or 0,
                    r.get('first_launched_at') or 0, r['native_uuid'])
        if not records:
            return None
        best = max(records.values(), key=recency)
        entry = stored.get(best['native_uuid'])
        return self.store.fresh(*entry) if entry is not None and entry[0] is best else best

    def tick(self, now):
        for episode in self.store.records('quota_checks'):
            key = episode['episode_id']
            if episode.get('status') in OPERATOR_STATUSES:
                self.reconcile_receipt(episode)  # late admitted delivery is still visible; never rearm
                continue
            if (episode['status'] == 'sent' or episode['due_at'] is None or now < episode['due_at']
                    or now < episode.get('next_attempt_at', 0)):
                continue
            task = self.tasks.get(key)
            if task is None or task.done():
                self.tasks[key] = asyncio.create_task(self.advance(key))

    def message(self, episode):
        if episode.get('submission'):
            return episode['submission']['submitted']
        if episode.get('kind') == 'observer_recovery':
            from .observer_recovery import ObserverRecovery
            return ObserverRecovery(self.store).notice_text(episode['operation_id'])
        if episode.get('kind') == 'account_switch':
            return self.account_switch_message(episode)
        affected = '\n'.join(' - ' + ' · '.join(str(r[k]) for k in ('program', 'execution_id', 'socket', 'role') if r.get(k))
                             + ' (' + r['source'] + ')' for r in episode['affected'])
        return ("AUTOMATED FIVE-HOUR RECOVERY CHECK — not a new message from Dan.\n\n"
            f"Claude Max's five-hour allowance halted work or paused a launch at {when(episode['detected_at'])}. "
            f"Its structured reset was {when(episode['resets_at'])}; the check was due at {when(episode['due_at'])} "
            f"(reset + five minutes). It is now {when(self.clock())}.\nAffected:\n{affected}\n\n"
            "Read [ap:T1TNT3] now. Dan's ruling [ap:TEUN9Y] supplies standing authority to recover work paused "
            "solely for this five-hour limit at existing program authority. This is a NEW request to inspect current "
            "work, not a replay of the interrupted turn. The host has not resumed Conductors or repeated tools.\n\n"
            "Reconcile current registries, ConductorState, successors and newer rulings; leave completed work and "
            "unrelated human HALTs alone. Steer each current quota-paused Conductor once, carrying [ap:T1TNT3], "
            "to reconcile EVERY in-flight assignment, resume the same interrupted pieces and re-arm waits. Recover "
            "Manager-direct unfinished work similarly. Workers may have finished, halted, failed, kept running or "
            "already woken a Conductor; an affected session may already have received a newer input. Check, do not "
            "duplicate workers or launch predecessors. Local tool results remain in conversation/host records: use "
            "successful receipts; inspect the specific live postcondition of any uncertain mutation before repeating "
            "it, including outstanding parallel calls. Keep the existing cast/authority and give a short status.")

    def account_switch_message(self, episode):
        complete = episode['outcome'] == 'complete'
        account = episode.get('account') or {}
        quota = episode.get('quota') or {}
        windows = '; '.join(f"{w.get('label', '?')} {w.get('percent', '?')}% used, resets {when(w.get('resets_at'))}"
                            for w in quota.get('windows') or []) if quota.get('ok') else ''
        quota_text = windows or ('unavailable: ' + (quota.get('error') or 'no readable windows'))
        closed = '\n'.join(' - ' + row for row in episode.get('closed') or []) or ' - none'
        stopped = '\n'.join(' - ' + row for row in episode.get('stopped') or []) or ' - none'
        label = 'COMPLETE' if complete else 'FAILED'
        return (f"AUTOMATED ACCOUNT SWITCH {label} — not a new message from Dan.\n\n"
            f"Requested at {when(episode['detected_at'])}; outcome recorded at {when(episode.get('finalized_at'))}.\n"
            f"Requested account: {episode['requested_account']}. Active account: {account.get('label') or 'unknown'} "
            f"(epoch {account.get('epoch', '?')}).\nQuota: {quota_text}.\n"
            f"Closed idle Claude sessions:\n{closed}\nStopped on the host's process-tree proof:\n{stopped}\n"
            f"Old host exit proved: {episode.get('host_exit_observed', False)}. "
            f"Replacement host health-confirmed: {bool(episode.get('host_started'))}.\n"
            + ("" if complete else f"Failure: {episode.get('switch_failure') or 'inspect the restart log'}.\n")
            + "\nRead [ap:XLM018] now. This is the outcome of the Manager's explicit switch under Dan's standing "
            "ruling [ap:4Y02WI], not mechanical quota failover or a replay of the interrupted turn. "
            + ("Reconcile current programs, successors, newer rulings and EVERY in-flight assignment; resume each "
               "current Conductor paused solely for this switch, carrying [ap:4Y02WI] and its switch pending_halt. "
               "Recover Manager-direct paused work and re-arm wanted doorbells. GPT workers are unaffected by this "
               "Claude restart; inspect their current state, do not duplicate them or repeat uncertain mutations "
               "without checking live postconditions. Leave completed "
               "work and unrelated human HALTs alone. End the quota-watch loop after the final switch, or carry on "
               "Dan's separately requested standing status reminders. Report account, quota and what resumed."
               if complete else
               "Do not claim the switch succeeded or resume programs blindly. Inspect actual account/host state "
               "and the restart receipt, then report the obstacle and use [ap:D8NOYW] if manual recovery is needed. "
               "The host has not independently resumed Conductors or replayed tools."))

    def reconcile_receipt(self, episode):
        """Read the retained submission's exact receipt FIRST; any admission forbids replay."""
        from .host import released_receipt
        if episode is None:
            return False
        episode.update(self.store.get('quota_checks', '', episode['episode_id']) or {})
        recipient = episode.get('recipient')
        receipt = self.receipt(episode)
        if receipt is None:
            return False  # no durable Send admission: safe to use this SAME UUID, never a fresh message
        binding = episode.get('submission')
        if binding and (receipt.get('generation') != binding['generation'] or receipt.get('submitted') != binding['submitted']):
            self.save(episode, status='delivery_problem', failure='exact admitted notice binding differs; reconcile retained UUID, never replay')
            return True
        if receipt.get('acknowledged') or receipt.get('started') or receipt.get('completed'):
            if (episode.get('delivery_status') if episode['status'] in OPERATOR_STATUSES else episode['status']) != 'sent':
                self.save(episode, status='sent', sent_at=self.clock(), failure=None)
        elif (released_receipt(receipt) or receipt.get('status') in ('uncertain', 'cancelled', 'withheld')
              or self.host.state(recipient).get('owner') == 'gone'):
            self.save(episode, status='delivery_problem', failure=(
                'Manager notice receipt is uncertain or undelivered; inspect its exact message UUID; never resent'))
        elif (episode.get('delivery_status') if episode['status'] in OPERATOR_STATUSES else episode['status']) != 'sending':
            self.save(episode, status='sending', failure=None)
        return True  # any existing receipt forbids semantic replay, including accepted-but-not-written

    async def advance(self, key):
        """One outcome snapshot, retained before possible Send; exact receipt wins every race.

        Reconnect/readiness stays with this owner. Only the selected current Manager may be
        restored before admission; no historical independent resume, leaf wake or text replay.
        """
        episode = self.store.get('quota_checks', '', key)
        try:
            if self.reconcile_receipt(episode) or not self.enabled(episode):
                return
            submission = episode.get('submission')
            intended = submission['recipient'] if submission else episode.get('requested_recipient')
            record = self.host.state(intended) if intended else self.current_manager()
            if record is None:
                self.lifecycle_manager(episode, state='pending', error='no registered current Manager')
                raise RuntimeError('no registered Manager; alarm retained until one is available')
            if record.get('role') != 'Manager':
                raise RuntimeError('notice recipient is not a registered Manager')
            sid, eid = record['native_uuid'], record['execution_id']
            if submission and (record['generation'] != submission['generation'] or record.get('owner') != 'host'):
                raise RuntimeError('retained notice generation unavailable; reconcile exact recipient/UUID, never retarget or replay')
            self.lifecycle_manager(episode, state='requested', execution_id=eid, generation=record['generation'])
            record = await self.host.reconnect(execution_id=eid, generation=record['generation'])
            episode = self.store.get('quota_checks', '', key)
            if self.reconcile_receipt(episode) or not self.enabled(episode):
                return
            session = self.host.sessions.get(sid)
            if session is None or record.get('state') == 'launch_blocked':
                raise RuntimeError('Manager transport unavailable; inspect its host state')
            from .restart_state import manager_evidence
            from .host import witness
            lifecycle = (self.store.get('observer_recoveries', '', episode['operation_id'])
                         if episode.get('kind') == 'observer_recovery' else None)
            readiness = (manager_evidence(record, witness) if (lifecycle or {}).get('transition') else
                {'state': 'ready' if record.get('initialized') and not record.get('setting_control_pending') else 'starting'})
            self.lifecycle_manager(episode, **readiness)
            if readiness['state'] == 'failed':
                raise RuntimeError(readiness['error'])
            if readiness['state'] != 'ready':
                self.save(episode, status='preparing', recipient=eid, failure=None)
                return  # readiness observed on later ticks, no sleeping native turn or wait task
            current = self.current_manager()
            if not intended and (current is None or current['native_uuid'] != sid):
                return  # Dan moved to another Manager during reconnect: select again before ANY Send
            episode = self.store.get('quota_checks', '', key)
            self.save(episode, status='preparing', recipient=eid, generation=session.generation, failure=None)
            async def admit():
                latest = self.store.get('quota_checks', '', key)
                if self.reconcile_receipt(latest) or not self.enabled(latest):
                    return
                current = self.current_manager()
                if not intended and (current is None or current['native_uuid'] != sid):
                    return
                submitted = self.message(latest)
                retained = latest.get('submission') or {'recipient': eid, 'generation': session.generation,
                    'submitted': submitted, 'message_uuid': latest['message_uuid'], 'possible_admission': True}
                self.save(latest, status='sending', submitted=submitted, recipient=eid,
                          generation=session.generation, submission=retained)
                # The native Send's only pre-admission await is its command lock. Operator control
                # can stop this private task while it waits there; never cancel reconnect/real work.
                await session.send(latest['message_uuid'], submitted, submitted, steer=True)
            admission = asyncio.create_task(admit())
            pending = self.admissions.setdefault(key, set())
            pending.add(admission)
            try:
                await admission
            except asyncio.CancelledError:
                latest = self.store.get('quota_checks', '', key)
                if latest is None or latest.get('status') not in OPERATOR_STATUSES:
                    raise
                if self.receipt(latest) is None:
                    self.save(latest, delivery_status='not_admitted', admission_cancel_confirmed=True)
            finally:
                pending.discard(admission)
                if not pending:
                    self.admissions.pop(key, None)
            self.reconcile_receipt(episode)
        except Exception as error:
            # If Send admitted anything, receipt reconciliation owns the outcome, never a second semantic Send.
            if not self.reconcile_receipt(episode):
                self.save(episode, status='delivery_problem', failure=str(error)[:300], next_attempt_at=self.clock() + 30)
