"""The single process-close predicate and the single reconnect predicate.

Silence is never evidence of settlement. The host and the pane both import these, so they
cannot disagree about what blocks a close, a restart or a reconnect.
"""

RECONNECT_POLICY = 2
"""Version of :func:`reconnect_blockers` this code enforces. The host stamps it on every record it
returns, so a pane always applies the exact rule of the host that answered (an older host's
records carry none and get the legacy rule, policy 1)."""

DEAD_GENERATION_HOLDS = ('protocol_attention', 'settings_mismatch', 'setting_control_pending',
                         'infrastructure_error', 'incident_unpersisted')
"""Markers that describe what a proved-gone process did, never anything that can still happen.
A reconnect starts a new generation without them (settings are re-verified natively, a new
stream is parsed afresh) and logs their old values; resume has always ignored them."""


def native_turn_over(record):
    """Native idle with no open primary turn, queued turn or permission/dialog request.

    This is turn-state evidence, never an age or a substitute for an input's delivery/result evidence.
    """
    return (record.get('native_session_state') == 'idle' and record.get('primary_turn_open') is not True
            and not record.get('native_queued_turns') and not record.get('pending_native_requests'))


# @beacon[
#   id=opc@process_settlement_blockers,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Shared close/collector/restart holds; durable escalations are review-only and survive collection. Automatic collection retains the other marker holds; operator close/restart reports non-work markers. Silence is never settlement.,
# ]
def settlement_blockers(record, *, require_native=None, ignore_control=None, automatic=True):
    """Reasons a process must not be closed now.

    ``automatic=False`` is a deliberate operator close or guarded restart: the holds marked
    advisory below protect nothing such a close could lose (settings are re-verified by the next
    generation, escalations and protocol evidence stay in the durable feed, a financial-attention
    process is better closed than kept), so they are reported by :func:`settlement_advisories`
    instead of blocking. Escalations are durable review requests, not unfinished native work:
    they never block collection or reset its settled age, and remain pending after EOF. The
    automatic collector retains every other hold below.
    """
    reasons = []
    pending = record.get('pending_message_uuids') or []
    if pending:
        reasons.append(f'accepted input awaiting a result ({len(pending)} message UUID(s))')
    for key, explanation, advisory in (
        ('setting_control_pending', 'settings control pending (native applied readback unfinished)', True),
        ('settings_mismatch', 'effective settings unverified or mismatched', True),
        ('native_retry_active', 'native retry active', False),
        ('background_unsettled', 'background tasks unsettled', False),
        ('background_tasks', 'registered background task(s) present', False),
        ('native_wake_pending', 'a background task ended; its wake-up turn is due', True),
        ('pending_recovery', 'a scheduled recovery is pending', False),
        ('protocol_attention', 'protocol attention; work evidence is incomplete', True),
        ('pending_native_requests', 'native permission/dialog request awaiting delivery', False),
        ('native_queued_turns', 'native queued turn(s) pending', False),
        ('financial_route_attention', 'native financial route attention', True),
        ('stdout_eof', 'native reply channel EOF', False),
    ):
        if record.get(key) and (automatic or not advisory):
            reasons.append(explanation)
    controls = [rid for rid in record.get('pending_control_requests', []) if rid != ignore_control]
    if controls and automatic:
        reasons.append('native control reply pending')
    if record.get('infrastructure_error'):
        reasons.append(f"host-reported durability failure: {record['infrastructure_error']} (once storage works "
                       "again, the host's clear_infrastructure_error releases it)")
    if record.get('owner') in ('unknown', 'draining') or record.get('state') == 'ownership_unknown':
        reasons.append('native ownership uncertainty')
    if record.get('native_session_state') in ('running', 'requires_action'):
        reasons.append('native turn or required action in progress')
    # Pre-state-push generations retain their explicit operator-close compatibility. They are
    # NEVER collector eligible. New generations and all collector decisions require native proof.
    if require_native is None:
        require_native = record.get('native_state_required', False)
    if require_native:
        if record.get('native_session_state') != 'idle':
            reasons.append('native session state is not verified idle')
        if record.get('background_level_known') is not True and (automatic or not never_tasked(record)):
            reasons.append('native background task level is unknown')
        # An operator may also close a process the host blocked for auth/financial reasons:
        # closing it is the safe direction, and a blocked state is never cleared otherwise.
        closable = ('ready',) if automatic else ('ready', 'launch_blocked')
        if record.get('state') not in closable or record.get('owner') != 'host':
            reasons.append('native transport is not a ready owned process')
    return reasons


def never_tasked(record):
    """The host watched this generation's whole stream and saw no background or subagent work.

    Claude Code (2.1.299, 2.1.300) reports no background task level on a process that has never run a
    task (only the collector's readback probe seeds it), so a fresh Manager would hold every
    operator close and guarded restart forever. A level that is unknown only because nothing
    was ever started is not unsettled work: no task frame, no subagent frame, no level snapshot
    and no protocol doubt means there is nothing a close could lose. Any of those clears it.
    """
    return (not record.get('background_tasks') and not record.get('background_unsettled')
            and record.get('background_level_cursor') is None
            and record.get('last_subagent_cursor') is None
            and not record.get('protocol_attention'))


def settlement_advisories(record, *, ignore_control=None):
    """What an operator close/restart reports without blocking; escalations never block collection either."""
    full = settlement_blockers(record, ignore_control=ignore_control, require_native=False, automatic=True)
    held = set(settlement_blockers(record, ignore_control=ignore_control, require_native=False, automatic=False))
    advisories = [reason for reason in full if reason not in held]
    if record.get('pending_escalations'):
        advisories.append('pending escalation(s) awaiting operator disposition')
    return advisories


def owner_proved_gone(record):
    """The record's own proof that no process of this conversation can still run."""
    return record.get('owner') == 'gone' and record.get('state') in ('exited', 'launch_failed')


# @beacon[
#   id=opc@reconnect_blockers,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=The one reconnect rule for host and pane: plain-words reasons (each naming the way out) why Send cannot reconnect an ended Manager; dead-generation markers never block once the owner is proved gone.,
# ]
def reconnect_blockers(record, *, policy=None, witness_alive=False, recovery_status=None):
    """Plain-words reasons Send cannot reconnect this ended conversation, each naming the way
    out; an empty list means it can. One rule for the host's ``reconnect`` and the pane's offer.

    ``policy`` defaults to the record's own ``reconnect_policy`` stamp (absent = 1, the legacy
    rule of a host that predates this function). ``witness_alive``/``recovery_status`` are the
    host's extra facts (the pane cannot see /proc or the recovery journal; the host still
    refuses with its exact reason if one of them holds).
    """
    if policy is None:
        policy = record.get('reconnect_policy', 1)
    fresh = ' Start a fresh conversation with ＋ New Manager; this one stays in the list.'
    if record.get('role') != 'Manager':
        return ['only a registered Manager conversation can be reconnected from the pane.']
    if not owner_proved_gone(record):
        return ['its previous Claude process is not proved gone yet (owner '
                f"{record.get('owner')!r}, state {record.get('state')!r}); wait a moment and Send again."
                + (fresh if record.get('state') == 'ownership_unknown' else '')]
    if record.get('state') != 'launch_failed' and (not record.get('process_witness') or not record.get('pid')):
        return ['the identity of its previous Claude process is unknown, so a second process could run '
                'beside it.' + fresh]
    if witness_alive:
        return ['its previous Claude process is still running, so a second one would write to the same '
                'conversation; wait for it to exit, then Send again.']
    if policy < 2:
        legacy = [key for key in ('pending_message_uuids', 'pending_recovery', 'pending_escalations',
                                  'infrastructure_error', 'protocol_attention', 'settings_mismatch')
                  if record.get(key)]
        if legacy:
            return ['the running Claude host still uses its old reconnect rule and refuses while '
                    f"{', '.join(legacy)} is recorded on the ended process." + fresh +
                    ' The next guarded host restart installs the new rule, after which Send reconnects it.']
        return []
    if record.get('pending_recovery') and recovery_status in ('claimed', 'accepted'):
        return ['an automatic crash recovery is reconnecting this conversation right now; wait a moment, '
                'then Send again.']
    return []
