"""Shared factual restart journal projections/finalization; not a second transition authority.

The recipe lives ONLY in ObserverRecovery's row. Writers use its transaction admission;
queries use a narrow read-only connection (no Store initialization/migrations). Selection
is always independently read through accounts; this module never selects or starts anything.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import shlex
import sqlite3
import time
import uuid


def public_value(value):
    """Secret-free presentation, without private local paths or native/tool bodies."""
    private = {'config_dir', 'state_root', 'socket_path', 'control_path', 'run_dir', 'recipe',
               'stdout', 'stderr', 'system_prompt', 'original', 'submitted', 'input', 'input_raw',
               'block_raw', 'raw', 'tool_input', 'tool_result', 'command', 'argv', 'env'}
    if isinstance(value, dict):
        return {k: public_value(v) for k, v in value.items() if k not in private}
    if isinstance(value, list):
        return [public_value(v) for v in value]
    if isinstance(value, str):
        return re.sub(r'(?<!\w)/(?:[^\s;,\'"<>])+', '[private path]', value)
    return value


def transition_facts(transition):
    """Sanitized derived report facts; never accepted as a writable lifecycle recipe."""
    service = transition.get('service') or {}
    state = transition['requested_transition']
    return public_value({
        'outcome': 'complete' if state == 'succeeded' else state,
        'phase': transition['phase'], 'direction': transition['direction'],
        'requested_account': transition['request'].get('account'),
        'account': service.get('account'), 'service': service,
        'manager': transition.get('manager'), 'effects': list(transition['effects'].values()),
        'errors': transition['errors'], 'phases': transition.get('phases', {}),
        'failure': transition['errors'][0]['message'] if transition['errors'] else None})


def read_operations(root, operation_id=None):
    """Read-only even when the host is absent/unbound; never creates or upgrades a DB."""
    path = Path(root) / 'host.sqlite3'
    if not path.exists():
        return None if operation_id else []
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observer_recoveries'").fetchone():
            return None if operation_id else []
        if operation_id:
            row = db.execute('SELECT body FROM observer_recoveries WHERE sid=? AND rid=?', ('', operation_id)).fetchone()
            return json.loads(row[0]) if row else None
        return [json.loads(row[0]) for row in db.execute('SELECT body FROM observer_recoveries WHERE sid=?', ('',))]


def read_receipt(root, operation_id, *, public=False):
    """Canonical row + independent delivery episode, read-only for pane/status consumers."""
    record = read_operations(root, operation_id)
    if record is None:
        return None
    episode = None
    transition = record.get('transition')
    if transition:
        path = Path(root) / 'host.sqlite3'
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            value = db.execute('SELECT body FROM quota_checks WHERE sid=? AND rid=?',
                               ('', transition['recipe']['episode_id'])).fetchone()
            episode = json.loads(value[0]) if value else None
    return envelope(record, episode=episode, public=public)


def old_host_absent(transition, *, witness_fn=None):
    """Exact recorded PID/start/boot absence only; missing or unreadable proof is not absence."""
    old = (transition.get('recipe') or {}).get('old_host') or {}
    if not old.get('host_pid') or not old.get('process_witness'):
        return False
    if witness_fn is None:
        from .host import witness as witness_fn
    try:
        return witness_fn(old['host_pid']) != old['process_witness']
    except OSError:
        return False


def physical_pending(record, *, witness_fn=None):
    """Unfinished physical ownership, including gate-stopping before phase acknowledgement.

    The gate file is atomic; this pure read neither acquires/creates its lock nor interprets
    stopping as host exit. Ready service or a recorded pre-stop refusal/cancellation releases
    ownership without clearing sticky gates/effects. Without stop intent, exact old-host absence
    also releases it; a live/unknown old host with an unfinished stopping admission still owns R.
    """
    transition = record.get('transition') or {}
    if not transition or transition.get('service', {}).get('state') == 'ready':
        return False
    if transition.get('host_stop_intended'):
        return True
    if transition.get('requested_transition') in ('refused', 'cancelled'):
        return False
    if old_host_absent(transition, witness_fn=witness_fn):
        return False
    if transition.get('physical_active'):
        return True
    gate = transition['request'].get('control_path')
    if not gate:
        return False
    try:
        return json.loads(Path(gate).read_text(encoding='utf-8')).get('phase') == 'stopping'
    except (OSError, ValueError):
        return True  # missing gate evidence cannot prove that stopping admission never won


@contextmanager
def journal(root):
    from .store import Store
    from .observer_recovery import ObserverRecovery
    store = Store(root)
    try:
        yield ObserverRecovery(store)
    finally:
        store.db.close()


def commands(operation_id, request, *, public=False):
    prefix = 'bash scripts/claude_restart.sh'
    if not public:
        namespace = request.get('namespace') or {}
        for key in ('socket_path', 'state_root'):
            if namespace.get(key):
                prefix += ' --' + key.replace('_', '-') + ' ' + shlex.quote(namespace[key])
    return {key: prefix + ' --' + key + ' ' + shlex.quote(operation_id) for key in ('status', 'recover')}


def envelope(record, *, admitted=True, episode=None, public=False):
    if record is None:
        raise ValueError('unknown restart operation')
    transition = record.get('transition')
    if not transition:
        return {'schema': 'claude-restart/1', 'operation_id': record['operation_id'], 'admitted': False,
                'phase': 'legacy_evidence_incomplete', 'requested_transition': 'pending',
                'errors': [{'message': 'legacy evidence has no durable before-stop recipe'}],
                'retry': {'classification': 'evidence_reconciliation', 'safe_pre_stop_retry': False}}
    request = deepcopy(transition['request'])
    notice = episode or {}
    result = {'schema': 'claude-restart/1', 'operation_id': record['operation_id'], 'admitted': admitted,
              'revision': transition['revision'], 'request': request,
              **{k: deepcopy(transition.get(k)) for k in (
                  'phase', 'direction', 'requested_transition', 'service', 'manager', 'retry', 'errors')},
              'effects': public_value(list(transition['effects'].values())),
              'notice': {k: notice.get(k) for k in ('episode_id', 'message_uuid', 'status', 'recipient',
                                                  'generation', 'delivery_status', 'failure')},
              'publication': {**record['publication'], 'enabled': record.get('publication_enabled', True),
                              'index': record['pages']['master']['ap_id']},
              'commands': commands(record['operation_id'], request, public=public)}
    if not (transition.get('notice_authorized') or record['observations'] or request.get('notify')):
        result['notice']['status'] = 'not_requested'
    return public_value(result) if public else result


def lock_free(path, *, read_only=False):
    """Probe the EXISTING ownership lock; no deletion, new lock namespace or age policy."""
    import fcntl
    path = Path(path)
    if not path.parent.exists():
        return True
    import os
    if read_only and not path.exists():
        return True
    fd = os.open(path, os.O_RDONLY if read_only else os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    finally:
        os.close(fd)


def service_evidence(transition, health, selector, witness_fn, *, source):
    """Pure readiness projection shared by coordinator, bound host and read-only status.

    Matching health is necessary, not just a bound socket. Derived rows/supervision attention
    are advisory; a demonstrated failed required-service probe is not ready.
    """
    from .accounts import same_account
    namespace = transition['request']['namespace']
    expected = transition.get('start_account')
    health = health or {}
    witness = health.get('process_witness')
    if (not health.get('host_pid') or not witness or witness_fn(health['host_pid']) != witness
            or str(health.get('state_root')) != namespace['state_root']
            or str(health.get('socket_path')) != namespace['socket_path']):
        return None
    if not expected or not same_account(selector['account'], expected):
        return None
    bound = transition.get('candidate') or {}
    if bound.get('process_witness') and bound['process_witness'] != witness:
        return None
    if not bound.get('account') or not same_account(bound['account'], expected):
        return None
    old = transition['recipe']['old_host']
    if old.get('process_witness') == witness and transition.get('host_stop_intended'):
        return None
    if health.get('required_service_error'):
        return None
    token = bound.get('start_token')
    recorded_attempt = bool(token and (token == transition.get('start_token') or (
        bound.get('operation_id') == transition['operation_id'] and
        token in (transition['recipe'].get('start_tokens') or {}).values())))
    return {'state': 'ready', 'account': selector['account'], 'host_pid': health['host_pid'], 'error': None, 'reason': None,
            'process_witness': witness, 'evidence': {'source': source, 'health_confirmed': True,
                'selector_verified': True, 'namespace_verified': True,
                'start_binding': 'recorded_attempt' if recorded_attempt else 'externally_restored_service',
                'start_token': bound.get('start_token'), 'operation_id': bound.get('operation_id'),
                'supervision_error': health.get('supervision_error'),
                'supervision_attention': health.get('supervision_attention'),
                'rows_backfill': health.get('rows_backfill')}}


def manager_evidence(record, witness_fn):
    """Existing initialized/writable/settings policy, not a stricter applied-settings gate."""
    result = {k: record.get(k) for k in ('execution_id', 'generation', 'process_witness', 'settings_unverified')}
    result.update(state='starting', error=None)
    if (record.get('role') != 'Manager' or record.get('state') in ('launch_blocked', 'launch_failed')
            or record.get('settings_mismatch') or record.get('infrastructure_error')):
        result.update(state='failed', error='Manager auth/snapshot/settings/transport failure; inspect exact host state')
    elif (record.get('owner') == 'host' and record.get('initialized') and record.get('state') == 'ready'
            and not record.get('setting_control_pending') and not record.get('stdout_eof')
            and record.get('process_witness') and witness_fn(record.get('pid')) == record['process_witness']):
        result['state'] = 'ready'
    elif record.get('owner') == 'gone' or record.get('state') in ('exited', 'exited_draining'):
        result.update(state='failed', error='Manager transport ended before readiness')
    return result


# @beacon[
#   id=opc@finalize_restart_service,
#   slice_labels=opc-basic,opc-claude-restart,
#   kind=ast,
#   comment=One factual ready finalizer for coordinator/recover and bound-host endpoint probes; merges exact selector/service evidence and schedules the operation's retained outcome episode without selecting or resuming native work.,
# ]
def finalize_service(store, operation_id, health, selector, witness_fn, *, source):
    from .observer_recovery import ObserverRecovery
    owner = ObserverRecovery(store)
    with owner._mutation(operation_id):
        record = owner.read(operation_id)
        transition = record['transition']
        service = service_evidence(transition, health, selector, witness_fn, source=source)
        if service is None:
            raise ValueError('service readiness evidence does not match this operation/selection')
        failed = transition['direction'] == 'restore_old'
        outcome = 'failed' if failed else 'succeeded'
        post_stop_error = any(error.get('post_stop') or error.get('phase') in (
            'host_stop_intended', 'old_host_exited', 'applying_target', 'selector_commit_intended',
            'selector_observed', 'start_intended', 'starting', 'service_ready', 'followup_pending', 'recovery_required')
            for error in transition['errors'])
        authorized = bool(transition.get('notice_authorized') or record['observations'] or
                          transition['request'].get('notify') or
                          (transition.get('host_stop_intended') and (failed or post_stop_error)))
        manager = transition['manager']
        if failed and transition['request']['kind'] == 'account_switch' and manager['policy'] != 'current':
            manager = {**manager, 'policy': 'current', 'state': 'pending'}
        record = owner.transition_update(operation_id, fields={
            'service': service, 'phase': 'service_ready', 'physical_active': False,
            'requested_transition': outcome, 'notice_authorized': authorized, 'manager': manager,
            'retry': {'classification': 'none', 'safe_pre_stop_retry': False, 'reason': None}})
        for action in list(record['transition']['effects'].values()):
            if (action['kind'] == 'start_host' and action['target'].get('token') == transition.get('start_token')
                    and service['evidence'].get('start_binding') == 'recorded_attempt'):
                record = owner.transition_update(operation_id, action={
                    **{key: action[key] for key in ('action_id', 'kind', 'target', 'attempt')},
                    'status': 'observed_complete', 'receipt': {'service': service}})
        if authorized:
            key = transition['recipe']['episode_id']
            episode = store.get('quota_checks', '', key)
            if episode is None:
                raise ValueError('before-stop outcome episode is missing')
            if episode['status'] == 'pending_switch':
                episode.update(status='scheduled', due_at=time.time())
                store.put('quota_checks', '', episode, key)
        return record


def stage_episode(store, operation_id, episode_id):
    """Not due until verified service; exact operator/admission state is never reset."""
    if store.get('quota_checks', '', episode_id) is None:
        store.put('quota_checks', '', {'episode_id': episode_id, 'kind': 'observer_recovery',
            'operation_id': operation_id, 'detected_at': time.time(),
            'message_uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, episode_id)),
            'recipient': None, 'status': 'pending_switch', 'due_at': None}, episode_id)
