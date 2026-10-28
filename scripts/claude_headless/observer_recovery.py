"""Durable observer obligations, published through one Manager, never a leaf Send.

``stage``/``update`` only write the local store. Callers stage before stopping observers,
then schedule ``publish`` outside the account-switch critical path. Workflowy/ownership
outages leave a readable local queue, not a switch hold. ``workflowy`` can be an injected
async ``call(family, verb, params)`` client (or its context-manager factory); the resolver
accepts a program handle and returns a SID, ``(SID, number)``, or backend/session mapping.

Workflowy's point.capture supports an explicit AP-ID, but NOT a caller UUID/idempotency
token. We retain that AP-ID AND create intent before issuing capture, then reconcile the
same identity by live note read. An uncertain capture is never blindly reissued after a
cache miss: that could duplicate a page after a lost reply/cache reset. A request lost
*before* creation can consequently remain uncertain until its transport outcome is known.
Updates target the same page and are reconciled by exact content readback. No publication
claim implies delivery, accepted worker completion, or permission to resume watched work.
Confirmed process detachment without a native terminal retains the unknown native verdict.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import re
import time
import uuid


TABLE = 'observer_recoveries'
SCHEMA = 'observer-recovery/1'
MANAGER_DOCK = 'adee3c89'
CAPTURE_LIMITATION = ('Capture was issued without an idempotent transport token. Reconcile the retained AP-ID; '
                      'a missing cached read does not prove no page was created. No second capture is issued.')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _program(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    ap = re.fullmatch(r'\[?ap:([a-z0-9]+)\]?', value, re.I)
    return '[ap:' + ap[1].upper() + ']' if ap else value


def _group(row):
    chain = row.get('ownership_chain') or []
    role = (row.get('owner') or {}).get('role') or (chain[0].get('role') if chain else None)
    if isinstance(role, str) and role.lower() == 'manager':
        return 'manager', None
    programs = {_program(link.get('program')) for link in chain if link.get('program')}
    if row.get('program'):
        programs.add(_program(row['program']))
    programs.discard(None)
    if len(programs) > 1:
        return 'unresolved', None
    if programs:
        return 'program', next(iter(programs))
    if row.get('ownership_problem') or not chain or not (row.get('owner') or {}).get('session'):
        return 'unresolved', None
    return 'standalone', None


def _route(value):
    if value is None:
        return {'status': 'pending', 'problem': 'No current Conductor found; Manager must reconcile the current registry.'}
    if isinstance(value, (tuple, list)):
        value = {'session': value[0], 'number': value[1] if len(value) > 1 else None}
    elif isinstance(value, str):
        value = {'session': value}
    if not isinstance(value, dict):
        raise ValueError('current-Conductor resolver returned no backend/session identity')
    sid = value.get('session') or value.get('execution_id') or value.get('sid')
    if not isinstance(sid, str) or not sid:
        raise ValueError('current-Conductor resolver returned no session identity')
    backend = value.get('backend') or ('claude' if sid.startswith('cc-') else
                                     'opencode' if sid.startswith('ses_') else None)
    if not backend:
        raise ValueError('current-Conductor resolver returned no backend identity')
    return {'status': 'resolved', 'backend': backend, 'session': sid,
            **({'number': value['number']} if value.get('number') is not None else {})}


async def resolve_current_conductor(program):
    """Use the shared extant registry binding without blocking the host's event loop."""
    from oc_common import current_conductor
    # current_conductor includes synchronous backend inventory calls after its Workflowy read.
    return await asyncio.to_thread(lambda: asyncio.run(current_conductor(program)))


class PublicationError(RuntimeError):
    def __init__(self, reply):
        self.reply = reply
        self.error = reply.get('error') or reply
        super().__init__(_json(self.error))


def _reply(text):
    if isinstance(text, dict):
        result = text
    else:
        try:
            result, _ = json.JSONDecoder().raw_decode(text.lstrip())
        except (ValueError, AttributeError):
            raise RuntimeError('Workflowy mutation returned no structured acknowledgement') from None
    if not isinstance(result, dict):
        raise RuntimeError('Workflowy mutation returned no structured acknowledgement')
    if result.get('success') is False or result.get('error'):
        raise PublicationError(result)
    return result


async def read_page(workflowy, handle):
    """Read an exact LIVE semantic note; distinguish cache misses from transport failures."""
    text = await workflowy.call('node', 'read', {'node_handle': handle, 'max_lines': 0})
    if isinstance(text, str) and text.startswith('[name: not editable] '):
        name, separator, note = text.partition('\n\n')
        if not separator:
            raise RuntimeError('Workflowy note read omitted its document framing')
        return {'name': name[len('[name: not editable] '):], 'note': note}
    try:
        reply = _reply(text)
    except PublicationError as error:
        detail = error.error if isinstance(error.error, dict) else {}
        if detail.get('code') in ('node_not_found', 'handle_not_found', 'ap_id_not_found') or (
                detail.get('code') == 'operation_failed' and
                'did not resolve to a Workflowy node' in str(detail.get('message', ''))):
            return None
        raise
    # Structured injected clients may return an equivalent live semantic read.
    if isinstance(reply.get('note'), str) and isinstance(reply.get('name'), str):
        return reply
    raise RuntimeError('Workflowy read was not an exact live note (archives are not publication confirmation)')


async def create_page(workflowy, page, note):
    """Explicit retained AP-ID, not an invented caller-UUID/idempotency guarantee."""
    reply = _reply(await workflowy.call('anchor', 'point.capture', {
        'parent_handle': page['parent'], 'name': page['name'], 'content': note, 'ap_id': page['ap_id']}))
    roots = reply.get('root_node_ids') or []
    if reply.get('success') is not True or not (reply.get('uuid') or len(roots) == 1):
        raise RuntimeError('Workflowy capture acknowledgement did not confirm exactly one root')
    return reply


async def update_page(workflowy, page, note):
    reply = _reply(await workflowy.call('node', 'edit', {
        'node_handle': page['ap_id'], 'note': note, 'diff': 'none'}))
    if reply.get('success') is not True:
        raise RuntimeError('Workflowy update acknowledgement did not confirm success')
    return reply


@asynccontextmanager
async def _client(injected):
    if injected is None:
        from oc_common import Workflowy
        injected = Workflowy()
    elif callable(injected) and not hasattr(injected, 'call'):
        injected = injected()
    if hasattr(injected, '__aenter__'):
        async with injected as client:
            yield client
    else:
        yield injected


def _observation_text(row):
    from .restart_state import public_value
    row = public_value(row)  # exact native/tool bodies remain ONLY in the private local record
    owner = row.get('owner') or {}
    chain = row.get('ownership_chain') or []
    parent = chain[0].get('parent') if chain else None
    status = row.get('status', 'unknown')
    guidance = {
        'armed': 'No confirmed stop is recorded. Reconcile current task identity before treating this observer as armed.',
        'stop_intended': 'Stop intent is durable; issuance/outcome still requires reconciliation, not a claim of an armed wait.',
        'stop_uncertain': 'Stop outcome is uncertain. Inspect the retained exact receipt, terminal/readback and process witnesses.',
        'stop_failed': 'A stop failed. Retain any partial effects and reconcile the exact live observer before retrying.',
        'stopped': 'Observation ended. Inspect the actual watched result/socket, then re-arm only outstanding obligations.',
        'detached': 'Observer processes ended under the retained switch-retirement proof. The native task verdict remains '
                    'unknown without its own terminal receipt. Inspect actual watched results before re-arming outstanding obligations.',
        'raced_completion': 'Observer completion/failure raced switching. This is NOT accepted completion of its watched assignment.',
        'identity_changed': 'The recorded task incarnation changed. Reconcile its terminal evidence/current identity; do not stop a replacement.',
    }.get(status, 'Unknown observer state: reconcile the exact retained evidence before re-arming.')
    return '\n'.join([
        '### ' + str(row.get('observation_id', 'unknown observation')),
        f"Owner: {owner.get('backend', 'unknown')} / {owner.get('session', 'unknown')}; "
        f"generation {owner.get('generation')}; task {owner.get('task_id')}; run {owner.get('run_id')}.",
        f"Immediate parent: {parent or 'not recorded / root owner'}. Status: **{status}**.",
        'Purpose: ' + str(row.get('purpose') or 'not recorded'),
        guidance,
        'Watched work: UNTOUCHED by observer cleanup. Idle, waiter exit 0 and a stable PARTIAL are not worker success.',
        'Exact obligations, immediate ancestry, source and receipts:', '```json', _json(row), '```',
    ])


# @beacon[
#   id=opc@ObserverRecovery,
#   slice_labels=opc-basic,opc-claude-seats,opc-claude-restart,
#   kind=ast,
#   comment=Owns observer obligations and versioned restart recipes in one writer-serialized row; merges exact action/error evidence with stable publication identities so offline recovery and host readiness/publication cannot overwrite each other or resume leaves.,
# ]
class ObserverRecovery:
    """One durable lifecycle/observer index per operation, one report per affected program.

    Store contract: ``get/put('observer_recoveries', '', operation_id)``. Stage/update are
    synchronous and network-free; all network work belongs to the separately scheduled
    publish coroutine. Repeated stage/update merges observations by immutable observation_id,
    retaining earlier obligations when a later inventory no longer contains the stopped task.
    """
    def __init__(self, store, *, workflowy=None, resolve_conductor=None, clock=time.time):
        self.store, self.workflowy, self.clock = store, workflowy, clock
        self.resolve_conductor = resolve_conductor or resolve_current_conductor
        self._locks = {}

    def read(self, operation_id):
        return self.store.get(TABLE, '', operation_id)

    @contextmanager
    def _mutation(self, operation_id):
        # Acquire SQLite's writer lock BEFORE read/modify/write. The detached restart
        # finalizer and a replacement host publisher use separate connections/processes;
        # an in-process asyncio lock cannot protect their operation facts/page receipts.
        # There is never an await or network request inside this transaction.
        with self.store.batch():
            self.store.db.execute('UPDATE observer_recoveries SET body=body WHERE sid=? AND rid=?', ('', operation_id))
            yield

    def _new_page(self, operation_id, key, parent, name):
        # Long AP cores are supported (3+ alphanumerics). Include store identity so independent
        # fixture/host stores using the same operation name never share a publication identity.
        core = uuid.uuid5(uuid.NAMESPACE_URL, 'opc-observer-recovery:' +
                         _json([self.store.identity, operation_id, key])).hex.upper()
        return {'key': key, 'ap_id': '[ap:OR' + core + ']', 'parent': parent, 'name': name,
                'marker': 'observer-recovery-page/1 ' + json.dumps([operation_id, key]),
                'create_state': 'not_issued', 'publication': 'pending', 'confirmed_hash': None}

    def _save(self, record):
        # The index includes program publication states; calculate those before its hash.
        for key in [key for key in record['pages'] if key != 'master'] + ['master']:
            page = record['pages'][key]
            expected = _digest(self._document(record, page['key']))
            page['publication'] = 'confirmed' if page.get('confirmed_hash') == expected else 'pending'
        record['publication']['status'] = ('confirmed' if all(
            page['publication'] == 'confirmed' for page in record['pages'].values()) else 'pending')
        record['updated_at'] = self.clock()
        self.store.put(TABLE, '', record, record['operation_id'])
        return deepcopy(record)

    def stage(self, operation_id, observations, **facts):
        """Durably allocate ALL page identities before caller effects, without network work."""
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError('operation_id must be retained by the account-switch caller')
        with self._mutation(operation_id):
            return self._stage(operation_id, observations, **facts)

    def _stage(self, operation_id, observations, **facts):
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError('operation_id must be retained by the account-switch caller')
        record = self.read(operation_id)
        if record is None:
            master_parent = facts.pop('master_parent', MANAGER_DOCK)
            record = {'schema': SCHEMA, 'operation_id': operation_id, 'created_at': self.clock(),
                      'observations': [], 'facts': {}, 'pages': {}, 'routes': {},
                      'publication': {'status': 'pending'}}
            record['pages']['master'] = self._new_page(operation_id, 'master', master_parent,
                'Observer recovery index · ' + operation_id)
        else:
            facts.pop('master_parent', None)  # an existing page's identity/parent never migrates on replay
        by_id = {row['observation_id']: row for row in record['observations']}
        for row in observations:
            if not isinstance(row, dict) or not row.get('observation_id'):
                raise ValueError('each observation needs its immutable observation_id')
            previous = by_id.get(row['observation_id'], {})
            merged = {**previous, **deepcopy(row)}
            terminals = {json.dumps(item, sort_keys=True): item for item in
                         [*(previous.get('terminal_evidence') or []), *(row.get('terminal_evidence') or [])]}
            if terminals:
                merged['terminal_evidence'] = list(terminals.values())
            if previous.get('status') in ('stopped', 'detached', 'raced_completion') and merged.get('status') in (
                    'armed', 'stop_intended', 'stop_uncertain'):
                merged['status'] = previous['status']  # ended identities cannot silently become armed again
            by_id[row['observation_id']] = merged
        record['observations'] = list(by_id.values())
        if record.get('transition') and observations:
            record['publication_enabled'] = True
        record['facts'].update(deepcopy(facts))
        if record.get('transition'):
            from .restart_state import transition_facts
            record['facts'].update(transition_facts(record['transition']))
        for row in record['observations']:
            group, program = _group(row)
            if group == 'program' and program not in record['pages']:
                record['pages'][program] = self._new_page(operation_id, program, program,
                    'Observer recovery report · ' + operation_id)
                record['routes'][program] = {'status': 'pending',
                    'problem': 'Resolve the current Conductor at publication/recovery time; historical SID is provenance only.'}
        return self._save(record)

    def update(self, operation_id, observations=None, **facts):
        if self.read(operation_id) is None:
            raise ValueError('stage recovery identities before updating operation effects')
        return self.stage(operation_id, observations or [], **facts)

    def admit_transition(self, operation_id, request, recipe):
        """Commit the complete before-effect recipe; an ID binds immutable request semantics.

        Uses the same writer admission as publication. A lost acknowledgement is reconciled
        by reading this same row, never by allocating another operation or report identity.
        """
        with self._mutation(operation_id):
            record = self.read(operation_id)
            if record is not None and record.get('transition') is not None:
                if record['transition']['request'] != request:
                    raise ValueError('operation ID is already bound to a different restart request')
                return record
            if record is not None:
                raise ValueError('legacy recovery evidence has no before-stop transition recipe')
            self._stage(operation_id, [])
            record = self.read(operation_id)
            record['publication_enabled'] = False
            record['transition'] = {
                'schema': 'claude-transition/1', 'revision': 1, 'operation_id': operation_id,
                'created_at': self.clock(), 'updated_at': self.clock(),
                'request': deepcopy(request), 'recipe': deepcopy(recipe),
                'phase': 'waiting', 'direction': 'normal', 'physical_active': False,
                'requested_transition': 'pending', 'effects': {}, 'errors': [],
                'service': {'state': 'unchecked'},
                'manager': {'policy': request['manager']['policy'], 'state': (
                    'not_requested' if request['manager']['policy'] == 'none' else 'pending')},
                'retry': {'classification': 'none', 'safe_pre_stop_retry': False, 'reason': None}}
            return self._save(record)

    def transition_update(self, operation_id, *, fields=None, action=None, error=None):
        """Merge named lifecycle facts/action milestones, never an old whole-row snapshot.

        Action events are cumulative; observed completion survives a later uncertain reply.
        Physical readiness wins over a late coordinator phase write. Manager, episode controls,
        publication and original observer terminal evidence have independent owners.
        """
        from .restart_state import transition_facts
        with self._mutation(operation_id):
            record = self.read(operation_id)
            if record is None or not record.get('transition'):
                raise ValueError('operation has no durable before-stop recipe')
            transition = record['transition']
            for key, value in (fields or {}).items():
                if key in ('request', 'recipe', 'operation_id', 'schema', 'effects', 'errors'):
                    raise ValueError('immutable or cumulative transition field: ' + key)
                if transition.get('service', {}).get('state') == 'ready' and key in (
                        'phase', 'physical_active', 'service'):
                    if key == 'phase' and value in ('service_ready', 'followup_pending', 'settled'):
                        transition[key] = value
                    continue
                if isinstance(value, dict) and isinstance(transition.get(key), dict):
                    transition[key].update(deepcopy(value))
                else:
                    transition[key] = deepcopy(value)
            if action:
                action = deepcopy(action)
                key = action.pop('action_id')
                previous = transition['effects'].get(key)
                if previous is None:
                    previous = {k: action[k] for k in ('kind', 'target', 'attempt')}
                    previous.update(action_id=key, events=[])
                    transition['effects'][key] = previous
                elif any(previous[k] != action[k] for k in ('kind', 'target', 'attempt')):
                    raise ValueError('action identity conflicts with retained target incarnation')
                event = {k: v for k, v in action.items() if k not in ('kind', 'target', 'attempt')}
                if event not in previous['events']:
                    previous['events'].append(event)
            if error and error not in transition['errors']:
                transition['errors'].append(deepcopy(error))
            transition['revision'] += 1
            transition['updated_at'] = self.clock()
            record['facts'].update(transition_facts(transition))
            if (record['observations'] or transition.get('notice_authorized')):
                record['publication_enabled'] = True
            return self._save(record)

    def _patch_page(self, operation_id, key, **changes):
        with self._mutation(operation_id):
            record = self.read(operation_id)
            record['pages'][key].update(changes)
            return self._save(record)

    def _document(self, record, key):
        page = record['pages'][key]
        rows = record['observations']
        lines = [page['marker'], '', '# ' + page['name'], '',
                 'End observation mechanisms, but preserve and list their obligations. '
                 'Only the Manager coordinates recovery with CURRENT program Conductors; no autonomous leaf resumes.',
                 'Session idle / waiter exit 0 is not accepted worker completion. '
                 'Read actual results, errors and sockets before deciding what remains.', '',
                  '## Operation facts', '```json', _json(record['facts']), '```', '']
        if record.get('transition'):
            lines += ['Local status/recovery: `bash scripts/claude_restart.sh --status ' + record['operation_id'] +
                      '` / `bash scripts/claude_restart.sh --recover ' + record['operation_id'] + '`.',
                      'Selection, usable service, Manager transport, notice admission/delivery and publication '
                      'are separate evidence; this page does not resume any program.']
        if key != 'master':
            lines += ['Master index: ' + record['pages']['master']['ap_id'],
                      'Program: ' + key, '## Current Conductor route',
                      'Resolve this route again when recovering; do not address only the historical dispatching SID.',
                      '```json', _json(record['routes'][key]), '```', '## Program observation obligations']
            selected = [row for row in rows if _group(row) == ('program', key)]
            lines.extend(_observation_text(row) for row in selected)
        else:
            lines += ['Local queue: ' + ('observer_recoveries · operation_id=' + record['operation_id'] if
                record.get('transition') else self._local_queue(record['operation_id'])), '## Programs']
            for program, report in record['pages'].items():
                if program == 'master':
                    continue
                lines += [f"- {program}: {report['ap_id']} ({report['publication']}); route: "
                          + json.dumps(record['routes'][program], ensure_ascii=False, sort_keys=True)]
                if report.get('problem'):
                    lines.append('  Publication issue: ' + report['problem'])
            for category, title in (('manager', 'Manager waits'), ('standalone', 'Standalone waits'),
                                    ('unresolved', 'Unresolved ownership')):
                lines += ['', '## ' + title]
                selected = [row for row in rows if _group(row)[0] == category or
                            (category == 'unresolved' and row.get('ownership_problem'))]
                lines.extend(_observation_text(row) for row in selected)
                if not selected:
                    lines.append('None recorded.')
            lines += ['', '## Recovery coordination',
                      'Manager: inspect each program report, resolve its current extant Conductor on either backend, '
                      'and hand it the complete obligations for itself, direct workers and nested helpers. '
                      'Immediate parents and historical dispatching Conductors remain evidence, not routing authority.',
                      'Reconcile partial stop/failure/timeout/cancellation, auth/start failures and raced task terminals '
                      'against the exact retained receipts. Never call a stopped observer armed, or a watched job successful, '
                      'without its own evidence. Watched targets and productive commands were not stopped by this reporting module.']
        return '\n\n'.join(lines)

    def _local_queue(self, operation_id):
        root = getattr(self.store, 'root', None)
        path = str(Path(root) / 'host.sqlite3') if root is not None else 'host.sqlite3'
        return f'{path} · {TABLE} · sid="" · operation_id={operation_id}'

    async def _publish_page(self, workflowy, operation_id, key):
        record = self.read(operation_id)
        page = record['pages'][key]
        try:
            live = await read_page(workflowy, page['ap_id'])
            # A caller may update outcomes while this read/resolver yields. Never write an
            # older inventory back over those facts, and never use a stale create admission.
            record = self.read(operation_id)
            page = record['pages'][key]
            body = self._document(record, key)
            if live is not None:
                if live['note'].split('\n', 1)[0] != page['marker']:
                    self._patch_page(operation_id, key, problem='AP-ID exists with a different recovery marker; not overwritten.')
                    return
                self._patch_page(operation_id, key, create_state='confirmed')
                if live['note'] != body:
                    self._patch_page(operation_id, key, update_intended_hash=_digest(body),
                                     problem='Update issued; exact content readback pending.')
                    await update_page(workflowy, page, body)
            elif page['create_state'] != 'not_issued':
                self._patch_page(operation_id, key, problem=CAPTURE_LIMITATION)
                return
            else:
                self._patch_page(operation_id, key, create_state='issued', create_intended_hash=_digest(body),
                                 create_intended_at=self.clock(), problem='Capture issued; exact content readback pending.')
                await create_page(workflowy, page, body)
            live = await read_page(workflowy, page['ap_id'])
            if live is None or live['note'] != body:
                self._patch_page(operation_id, key, problem='Publication reply/readback did not confirm the exact submitted note.')
                return
            self._patch_page(operation_id, key, create_state='confirmed', confirmed_hash=_digest(body),
                             confirmed_at=self.clock(), problem=None)
        except asyncio.CancelledError:
            self._patch_page(operation_id, key, problem='Publication cancelled; admitted create/update remains uncertain until readback.')
            raise
        except Exception as error:
            self._patch_page(operation_id, key, problem=type(error).__name__ + ': ' + str(error))

    async def publish(self, operation_id):
        """Best-effort background publication; failures persist locally and never Send/resume.

        Re-resolve successors on EVERY call, including an already published report. Failed
        resolution does not block publishing: the report tells the Manager to reconcile it.
        Pending/uncertain creates reconcile only against the retained identity, never a new page.
        """
        async with self._locks.setdefault(operation_id, asyncio.Lock()):
            if self.read(operation_id) is None:
                raise ValueError('stage recovery identities before publication')
            if self.read(operation_id).get('publication_enabled') is False:
                return self.read(operation_id)
            try:
                keys = list(self.read(operation_id)['pages'])
                for key in keys:
                    if key == 'master':
                        continue
                    try:
                        value = self.resolve_conductor(key)
                        route = _route(await value if inspect.isawaitable(value) else value)
                    except Exception as error:
                        route = {'status': 'pending', 'problem': type(error).__name__ + ': ' + str(error)}
                    with self._mutation(operation_id):
                        record = self.read(operation_id)
                        record['routes'][key] = route
                        self._save(record)
                async with _client(self.workflowy) as workflowy:
                    # Program confirmations/issues precede the index so its links are honest.
                    for key in keys:
                        if key != 'master':
                            await self._publish_page(workflowy, operation_id, key)
                    await self._publish_page(workflowy, operation_id, 'master')
                with self._mutation(operation_id):
                    record = self.read(operation_id)
                    record['publication'].pop('problem', None)
                    record['publication']['last_attempt_at'] = self.clock()
                    return self._save(record)
            except asyncio.CancelledError:
                with self._mutation(operation_id):
                    record = self.read(operation_id)
                    record['publication']['problem'] = 'Publication cancelled; retained identities/intent require readback.'
                    self._save(record)
                raise
            except Exception as error:
                with self._mutation(operation_id):
                    record = self.read(operation_id)
                    record['publication'].update(problem=type(error).__name__ + ': ' + str(error),
                                                  last_attempt_at=self.clock())
                    return self._save(record)

    def notice_text(self, operation_id):
        """Latest as-of lifecycle/obligation snapshot BEFORE admission; never a direct Send.

        QuotaWake retains submitted bytes after possible admission, so later facts/page states
        cannot silently become a rewritten or second semantic outcome message.
        """
        record = self.read(operation_id)
        if record is None:
            raise ValueError('unknown observer recovery operation')
        master = record['pages']['master']
        transition = record.get('transition')
        label = 'AUTOMATED OBSERVER RECOVERY INDEX'
        lifecycle = []
        if transition:
            from .restart_state import public_value, commands, transition_facts
            request, service = transition['request'], transition.get('service') or {}
            if request['kind'] == 'account_switch':
                label = ('AUTOMATED ACCOUNT SWITCH FAILED — SERVICE RESTORED' if
                    transition['direction'] == 'restore_old' and service.get('state') == 'ready' else
                    'AUTOMATED ACCOUNT SWITCH COMPLETE' if transition['requested_transition'] == 'succeeded' else
                    'AUTOMATED ACCOUNT SWITCH FAILED')
            else:
                label = 'AUTOMATED RESTART RECOVERY'
            account = service.get('account') or (transition.get('selector_observed') or {}).get('account') or {}
            lifecycle = [
                f"Requested: {request['kind']} / {request.get('account') or 'same selection'}. "
                f"Requested outcome: {transition['requested_transition']}.",
                f"Actual observed account: {account.get('label', 'unknown')} (epoch {account.get('epoch', 'unknown')}). "
                f"Service: {service.get('state', 'unknown')}; old host exit proved: "
                f"{bool(transition.get('old_host_exit_observed'))}; replacement/old-service readiness: "
                f"{service.get('state') == 'ready'}.",
                'Original/later failed phases and errors: ' + _json(public_value(transition['errors'])),
                'Manager transport: ' + _json(public_value(transition['manager'])),
                f"Recovery direction/result: {transition['direction']} / {transition['phase']}. "
                'Remaining obstacle: ' + (public_value(transition['errors'][-1]['message']) if
                    transition['phase'] == 'recovery_required' and transition['errors'] else
                    'inspect current Manager/notice/publication fields; service readiness is not program completion'),
                'Real partial close/settle/observer receipts: ' + _json(public_value(list(transition['effects'].values()))),
                'Retained exact observer terminals: ' + _json(public_value([
                    {key: row.get(key) for key in ('observation_id', 'status', 'terminal_evidence', 'retirement')}
                    for row in record['observations']])),
                'Fresh quota: unavailable (not required for lifecycle recovery).',
                'Read-only status / supported same-ID recovery: ' + _json(commands(operation_id, request, public=True))]
        lines = [label + ' — not a new message from Dan.',
                 'Operation: ' + operation_id,
                 *lifecycle,
                 f"Master index: {master['ap_id']} (publication {master['publication']}; pending handles may not resolve yet).",
                 'Local durable queue fallback: ' + ('observer_recoveries · operation_id=' + operation_id if transition else
                                                      self._local_queue(operation_id)),
                 'Operation facts: ' + json.dumps(record['facts'], ensure_ascii=False, sort_keys=True),
                 'Manager: coordinate each program through its CURRENT Conductor on either backend. '
                 'Resolve again at recovery time; historical SIDs are not routing authority. '
                 'Do not autonomously resume every leaf.']
        for key, page in record['pages'].items():
            if key != 'master':
                lines.append(f"Program {key}: {page['ap_id']} (publication {page['publication']}); "
                             + json.dumps(record['routes'][key], ensure_ascii=False, sort_keys=True))
        for group, label in (('manager', 'Manager waits'), ('standalone', 'Standalone waits'),
                             ('unresolved', 'Unresolved ownership')):
            count = sum(_group(row)[0] == group or (group == 'unresolved' and bool(row.get('ownership_problem')))
                        for row in record['observations'])
            lines.append(f'{label}: {count}; see the master index/local record.')
        lines.append('Watched work was left untouched. Reconcile actual target results/errors/sockets and all exact '
                     'stop receipts, partial failures and completion/failure races before re-arming outstanding waits. '
                     'Session idle, waiter exit 0 and a stable PARTIAL do not establish worker success. '
                     'Publication pending is not confirmed delivery.')
        return '\n\n'.join(lines)
