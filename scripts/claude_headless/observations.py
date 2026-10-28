"""Typed observation obligations and the Claude exact-task stop adapter.

An observation is not its watched job. Recognition examines the WHOLE executed Bash input,
never its description or the host's cropped display summary. Records are backend-neutral;
only the execution adapter below is Claude-specific. A stop reply is not settlement.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import time
import uuid


REPO = Path(__file__).resolve().parents[2]
SCHEMA = 'observation/1'
TERMINALS = ('completed', 'failed', 'killed', 'stopped')


def identity(backend, session, generation, task, run):
    """A task incarnation, not a task description or a reusable task ID."""
    return hashlib.sha256(json.dumps([backend, session, generation, task, run]).encode()).hexdigest()


def _duration(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:\d+(?:\.\d+)?[smhd]?)+', value):
        return None
    parts = re.findall(r'(\d+(?:\.\d+)?)([smhd]?)', value)
    seconds = sum(float(n) * {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit] for n, unit in parts)
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _script(value, names):
    path = Path(value)
    # Absolute companion scripts or the ordinary launcher basename. A same-named script in
    # another folder is not our observer. Native execution still supplies the process witness.
    return path.name in names and (str(path) == path.name or path.parent == REPO / 'scripts')


def _argv_observation(argv):
    """Finite read-only effect grammar; unknown effects are returned, never discarded."""
    if not argv:
        return [], 'empty command'
    argv = list(argv)
    if argv[0] == 'env':
        argv.pop(0)
        while argv and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=[^\n]*', argv[0]):
            # Environment overrides can replace executable/module resolution. No need to guess.
            if argv[0].split('=', 1)[0] not in ('PYTHONDONTWRITEBYTECODE', 'TZ'):
                return [], 'environment changes executable or observer behavior'
            argv.pop(0)
    if argv and Path(argv[0]).name == 'timeout':
        argv.pop(0)
        if argv[:1] == ['--foreground']:
            argv.pop(0)
        if not argv or _duration(argv.pop(0)) is None:
            return [], 'unrecognised timeout wrapper'
    if not argv:
        return [], 'missing executable'
    name = Path(argv[0]).name
    if Path(argv[0]).is_absolute() and name in ('bash', 'sh', 'sleep', 'date', 'echo', 'printf', 'true',
                                                               'python', 'python3'):
        if Path(argv[0]).parent not in (Path('/bin'), Path('/usr/bin')):
            return [], 'executable is not the standard observer or companion script'
    if name in ('bash', 'sh'):
        if len(argv) == 3 and argv[1] in ('-c', '-lc'):
            result = recognize(argv[2])
            return result.get('obligations', []), None if result['detachable'] else result['reason']
        argv.pop(0)
    elif re.fullmatch(r'python(?:3(?:\.\d+)?)?', name):
        argv.pop(0)
        if argv[:1] in (['-u'], ['-B']):
            argv.pop(0)
    if not argv:
        return [], 'missing observer script'
    name = Path(argv[0]).name
    args = argv[1:]
    if name == 'sleep' and args and all(_duration(a) is not None for a in args):
        return [{'kind': 'timer', 'seconds': sum(_duration(a) for a in args)}], None
    if name == 'date' and (not args or args in (['-u'], ['--utc'], ['-Iseconds'], ['-u', '-Iseconds'])):
        return [], None
    if name in ('echo', 'printf'):
        return [], None  # stdout only; redirection/substitution were excluded lexically
    if argv == ['true'] or (name == 'cd' and len(args) == 1 and Path(args[0]).is_absolute()):
        return [], None
    if _script(argv[0], ('oc.sh',)):
        if args[:1] == ['timer']:
            return _argv_observation(['oc_timer.py', *args[1:]])
        if args[:1] in (['seat'], ['claude']):
            return _argv_observation(['oc_seat.py', *args[1:]])
        if args[:1] == ['usage'] and all(a in ('--json', '--refresh', '--account', '--provider',
                                                  'personal', 'work', 'claude', 'chatgpt') for a in args[1:]):
            return [{'kind': 'quota_poll', 'argv': args}], None
    if _script(argv[0], ('oc_timer.py',)) and args and _duration(args[0]) is not None:
        if len(args) == 1 or (len(args) == 3 and args[1] == '--label'):
            return [{'kind': 'timer', 'seconds': _duration(args[0]),
                     'purpose': args[2] if len(args) == 3 else None}], None
    if _script(argv[0], ('oc_seat.sh', 'oc_seat.py', 'oc_seat', 'oc_claude.py')):
        if args[:1] in (['info'], ['status']) and len(args) == 2 and re.fullmatch(r'(?:cc-|ses_)[\w-]+', args[1]):
            return [{'kind': 'session_poll', 'target': {'backend': 'claude' if args[1].startswith('cc-')
                                                       else 'opencode', 'session': args[1]}}], None
        if args[:1] == ['wait']:
            args = args[1:]
            target, maximum = None, None
            while args:
                arg = args.pop(0)
                if arg == '--max' and args:
                    maximum = _duration(args.pop(0))
                    if maximum is None:
                        return [], 'invalid wait deadline'
                elif arg == '--socket' and args and target is None:
                    target = {'socket': args.pop(0)}
                elif arg in ('--keep-others', '--force'):
                    continue
                elif target is None and re.fullmatch(r'(?:cc-|ses_)[\w-]+', arg):
                    target = {'backend': 'claude' if arg.startswith('cc-') else 'opencode', 'session': arg}
                else:
                    return [], 'unrecognised wait arguments'
            if target:
                return [{'kind': 'socket_doorbell' if 'socket' in target else 'session_doorbell',
                         'target': target, 'max_seconds': maximum}], None
    return [], 'executing command is not a proven observation: ' + name


# @beacon[
#   id=opc@recognize_observation,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Classifies the whole executed shell input into typed timer/session/socket/quota-poll obligations; rejects unknown or mixed effects including sleep-before-write and never uses descriptions or cropped summaries.,
# ]
def recognize(command):
    """Recognize practical native/legacy observers without blessing arbitrary shell programs."""
    def unknown(reason):
        return {'detachable': False, 'kind': 'unclassified', 'reason': reason, 'obligations': []}
    if not isinstance(command, str) or not command.strip():
        return unknown('full executed command unavailable')
    # Shell expansion can execute effects inside an otherwise innocent argv. Quotes are not
    # a reason to ignore this uncertainty (the native wrapper later evals the original text).
    if any(c in command for c in ('$', '`', '>', '<', '\\', '\x00')):
        return unknown('shell expansion, redirection or escaped syntax needs reconciliation')
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()')
        lexer.whitespace_split, lexer.commenters = True, ''
        tokens = list(lexer)
    except ValueError:
        return unknown('malformed shell input')
    # The useful read-only polling form; the complete body is still checked. No eval,
    # command substitutions, trap, arbitrary conditions or hidden shell functions.
    polling = tokens[:4] == ['while', 'true', ';', 'do'] and tokens[-2:] == [';', 'done']
    if polling:
        tokens = tokens[4:-2]
    groups, current = [], []
    for token in tokens:
        if token in (';', '&&', '||'):
            if not current:
                return unknown('empty shell command')
            groups.append(current)
            current = []
        elif token in ('&', '|', '(', ')'):
            return unknown('pipeline, detached shell or compound execution needs reconciliation')
        else:
            current.append(token)
    if current:
        groups.append(current)
    obligations = []
    for argv in groups:
        found, reason = _argv_observation(argv)
        if reason:
            return unknown(reason)
        obligations.extend(found)
    if not obligations:
        return unknown('no observation obligation in command')
    return {'detachable': True, 'kind': 'read_only_poll' if polling else
            obligations[0]['kind'] if len(obligations) == 1 else 'observation_sequence',
            'obligations': obligations, 'reason': None}


# @beacon[
#   id=opc@full_task_input,
#   slice_labels=opc-basic,
#   kind=ast,
#   comment=Resolve exact task/run and tool-use frame indexes with lazy old-generation coverage, then apply the original full scoped raw-input dedup and Bash-only proof; an unavailable index uses the original scan and reasons.,
# ]
def full_task_input(host, session, task):
    """Read only the exact indexed frames after one old-generation back-fill, with the original scan fallback.

    The index selects candidates, never proof: raw task/run equality, scoped input deduplication,
    first-frame cursor and Bash-only rules below stay identical to the generation-wide scan.
    """
    tool_id = task.get('tool_use_id')
    if not isinstance(tool_id, str) or not tool_id:
        # A native level can precede task_started or carry a null tool_use_id. Join
        # that same task/run's retained lifecycle, never its description or summary.
        candidates = set()
        lifecycle = host.store.task_input_rows(session.sid, session.generation, 'task',
                                              (task.get('task_id'), task.get('run_id')))
        if lifecycle is None:
            lifecycle = host.store.db.execute("SELECT * FROM events WHERE sid=? AND generation=? AND kind='native_frame' "
                "AND stream='stdout' AND (json_extract(detail,'$.type') IS NULL OR "
                "(json_extract(detail,'$.type')='system' AND json_extract(detail,'$.subtype') IN "
                "('task_started','task_updated','task_progress','task_notification'))) ORDER BY seq",
                (session.sid, session.generation))
        for row in lifecycle:
            with host.store.path(session.sid, session.generation, 'stdout').open('rb') as stream:
                stream.seek(row['offset'])
                event = json.loads(stream.read(row['length']))
            if (event.get('type') == 'system' and event.get('task_id') == task.get('task_id') and
                    event.get('run_id') == task.get('run_id') and isinstance(event.get('tool_use_id'), str)
                    and event['tool_use_id']):
                candidates.add(event['tool_use_id'])
        if len(candidates) != 1:
            return None, 'task has no unique exact task/run to tool-use identity'
        tool_id = next(iter(candidates))
    rows = host.store.task_input_rows(session.sid, session.generation, 'tool', tool_id)
    if rows is None:
        rows = host.store.db.execute("SELECT * FROM events WHERE sid=? AND generation=? AND kind='native_frame' "
            "AND stream='stdout' AND (json_extract(detail,'$.type') IS NULL OR "
            "json_extract(detail,'$.type')='assistant') ORDER BY seq", (session.sid, session.generation))
    found = {}
    for row in rows:
        with host.store.path(session.sid, session.generation, 'stdout').open('rb') as stream:
            stream.seek(row['offset'])
            event = json.loads(stream.read(row['length']))
        if event.get('type') != 'assistant':
            continue
        blocks = (event.get('message') or {}).get('content') or []
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict) or block.get('type') != 'tool_use' or block.get('id') != tool_id:
                continue
            source = {'tool_use_id': tool_id, 'name': block.get('name'), 'input': block.get('input'),
                      'scope': event.get('parent_tool_use_id'), 'sidechain': event.get('isSidechain') is True,
                      'cursor': host.store.cursor(row['seq'])}
            key = json.dumps({k: v for k, v in source.items() if k != 'cursor'}, sort_keys=True)
            found.setdefault(key, source)
    if len(found) != 1:
        return None, 'missing or ambiguous full scoped tool input'
    source = next(iter(found.values()))
    if source['name'] != 'Bash' or not isinstance(source['input'], dict):
        return None, 'task input is not native Bash (Agent work is not an observer)'
    return source, None


def process_tree(pid):
    """Owned descendants with PID/start witnesses; never targets of a watched doorbell."""
    from .host import witness
    result, pending = [], [pid]
    while pending:
        parent = pending.pop()
        try:
            children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
        except OSError:
            continue
        for text in children:
            child = int(text)
            proof = witness(child)
            if not proof:
                continue
            try:
                argv = Path(f'/proc/{child}/cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
            except (OSError, UnicodeError):
                continue
            result.append({'witness': proof, 'argv': argv, 'parent_pid': parent})
            pending.append(child)
    return result


def task_processes(session, command):
    """Bind native Bash eval arguments to full input; identical observers retain shared scope."""
    rows = process_tree(session.process.pid)
    shells = []
    for row in rows:
        argv = row['argv']
        if len(argv) != 3 or Path(argv[0]).name not in ('bash', 'sh') or argv[1] not in ('-c', '-lc'):
            continue
        try:
            tokens = shlex.split(argv[2])
        except ValueError:
            continue
        if any(tokens[i] == 'eval' and tokens[i + 1] == command for i in range(len(tokens) - 1)):
            shells.append({**row, 'shell_root': True})
    if not shells:
        return [], 'no owned native shell witness for full task command'
    selected = list(shells)
    known = {row['witness']['pid'] for row in shells}
    for _ in rows:
        additions = [r for r in rows if r['parent_pid'] in known and r['witness']['pid'] not in known]
        if not additions:
            break
        selected.extend(additions)
        known.update(r['witness']['pid'] for r in additions)
    return selected, None


def active_observer_effect(processes):
    """A quota *sleep* is disposable; a reader renewing a credential is actual work."""
    for process in processes:
        argv = process['argv']
        if any(Path(arg).name == 'oc_usage.py' for arg in argv) or any(
                Path(argv[i]).name == 'oc.sh' and argv[i + 1] == 'usage' for i in range(len(argv) - 1)):
            return 'active quota reader may be renewing credentials; let that reader settle (not its polling deadline)'
    return None


def ownership(record, sessions):
    """Immutable observed immediate links, leaving foreign/missing/cyclic ancestry explicit."""
    chain, seen, current = [], set(), record
    while current:
        sid = current.get('execution_id')
        if not sid or sid in seen:
            return chain, 'ownership cycle or missing identity'
        seen.add(sid)
        labels = current.get('labels') or {}
        parent = current.get('parent') or labels.get('parent_seat')
        conductor = current.get('owning_conductor') or labels.get('conductor_sid')
        chain.append({'backend': 'claude' if sid.startswith('cc-') else 'opencode', 'session': sid,
                      'role': current.get('role'), 'parent': parent, 'conductor': conductor,
                      'program': current.get('program') or labels.get('program')})
        if not parent:
            return chain, None
        current = sessions.get(parent)
        if current is None:
            chain.append({'backend': 'claude' if parent.startswith('cc-') else 'opencode', 'session': parent})
            return chain, 'immediate parent requires current registry/backend reconciliation'
    return chain, None


def watched_snapshot(host, target):
    """Use already-owned backend evidence only; a foreign/socket read never holds switching."""
    answer = {'target': target, 'execution_action': 'none; watched work left untouched',
              'assignment_completion': 'not inferred from idle, waiter exit or native input completion'}
    if target.get('backend') != 'claude' or not str(target.get('session') or '').startswith('cc-'):
        return {**answer, 'availability': 'unknown',
                'reason': 'resolve current socket/result or foreign backend state during Manager/Conductor reconciliation'}
    sid = target['session'][3:]
    session = host.sessions.get(sid)
    record = session.record if session else host.store.get('sessions', sid)
    if not record:
        return {**answer, 'availability': 'unknown', 'reason': 'watched Claude session is not owned by this host'}
    mid = record.get('last_input_uuid')
    receipt = host.store.get('receipts', sid, mid) if mid else None
    return {**answer, 'availability': 'observed', 'generation': record.get('generation'),
            'state': record.get('state'), 'owner': record.get('owner'),
            'native_state': record.get('native_session_state'), 'quota_halt': record.get('quota_halt'),
            'last_result_cursor': record.get('last_result_cursor'), 'last_terminal_reason': record.get('last_terminal_reason'),
            'native_receipt': {key: receipt.get(key) for key in ('message_uuid', 'generation', 'status', 'completed')}
                if receipt else None,
            'native_results': [{k: r.get(k) for k in ('subtype', 'is_error', 'terminal_reason', 'cursor')}
                               for r in (receipt or {}).get('results') or []]}


# @beacon[
#   id=opc@ObservationLedger,
#   slice_labels=opc-basic,opc-claude-seats,
#   kind=ast,
#   comment=Durable backend-neutral observation inventory and intent/outcome ledger; exact indexed raw-input/task and process witnesses retain the unchanged cancellation proof; ownership and switch inventories read cached session records without decoding all records; terminal/readback receipts survive lost replies and restart.,
# ]
class ObservationLedger:
    def __init__(self, host):
        self.host, self.store = host, host.store
        self.retirements = {}

    def get(self, observation_id):
        return self.store.get('observations', '', observation_id)

    def save(self, row, event):
        with self.store.batch():
            row['updated_at'] = time.time()
            row['last_event_cursor'] = self.store.event(row['owner']['session'].removeprefix('cc-'),
                row['owner']['generation'], event, {'observation_id': row['observation_id'],
                                                   'status': row['status']})
            self.store.put('observations', '', row, row['observation_id'])
        return row

    def inventory(self):
        rows = []
        sessions = {record['execution_id']: record for _, record, _ in self.store.cached_sessions()}
        sessions.update({s.record['execution_id']: s.record for s in self.host.sessions.values()})
        for session in self.host.sessions.values():
            for task_id, task in (session.record.get('background_tasks') or {}).items():
                run = task.get('run_id')
                oid = identity('claude', session.record['execution_id'], session.generation, task_id, run)
                previous = self.get(oid)
                if previous and (previous.get('stop_request_id') or previous.get('detachable')):
                    rows.append(self.reconcile(previous))
                    continue
                source, reason = full_task_input(self.host, session, {**task, 'task_id': task_id})
                command = (source['input'].get('command') if source else None)
                classification = recognize(command)
                if task.get('task_type') not in (None, 'local_bash'):
                    reason = 'native task is executing ' + str(task.get('task_type')) + ', not an observation'
                if not isinstance(run, str) or not run:
                    reason = 'task run identity is unknown'
                processes, process_reason = task_processes(session, command) if classification['detachable'] else ([], None)
                process_reason = process_reason or active_observer_effect(processes)
                chain, chain_problem = ownership(session.record, sessions)
                row = {'schema': SCHEMA, 'observation_id': oid,
                       'owner': {'backend': 'claude', 'session': session.record['execution_id'],
                                 'generation': session.generation, 'task_id': task_id, 'run_id': run,
                                 'tool_use_id': source['tool_use_id'] if source else task.get('tool_use_id')},
                       'owner_process': session.record.get('process_witness'), 'processes': processes,
                       'process_binding': 'shared_shell_population' if sum(bool(p.get('shell_root')) for p in processes) > 1
                                          else 'exact_shell' if processes else 'unavailable',
                       'native_task_type': task.get('task_type'),
                       'program': session.record.get('program'), 'ownership_chain': chain,
                       'ownership_problem': chain_problem, 'purpose': task.get('description'),
                       'source': source, 'command_sha256': hashlib.sha256(command.encode()).hexdigest()
                           if isinstance(command, str) else None,
                       **classification, 'status': 'armed', 'created_at': (previous or {}).get('created_at', time.time()),
                       'watched_work': 'untouched; observer exit/idle is not accepted worker completion'}
                if reason or process_reason:
                    row.update(detachable=False, reason=reason or process_reason)
                if classification['detachable']:
                    row['deadlines'] = [{**o, 'due_at': (task.get('started_at') + o['seconds'])
                        if isinstance(task.get('started_at'), (int, float)) and 'seconds' in o else None}
                        for o in classification['obligations']]
                row['watched_target_snapshots'] = [{'before': watched_snapshot(self.host, obligation['target'])}
                    for obligation in classification['obligations'] if obligation.get('target')]
                rows.append(self.save(row, 'observation_inventoried'))
            known = {r['observation_id'] for r in rows}
            rows.extend(r for r in self.pending_wake_observations(session) if r['observation_id'] not in known)
        return rows

    def pending_wake_observations(self, session):
        """A naturally ended observer's queued/in-flight notification, not arbitrary model work."""
        record = session.record
        cursor = record.get('native_wake_cursor')
        if not cursor or record.get('pending_message_uuids'):
            return []
        assistant = record.get('last_assistant_cursor')
        if assistant and self.store.parse_cursor(assistant) > self.store.parse_cursor(cursor):
            return []  # new model/tool work after that observer is genuine executing work
        return [row for row in self.store.records('observations') if row['owner']['session'] == record['execution_id']
                and row['owner']['generation'] == session.generation and row.get('detachable') and
                any(t['cursor'] == cursor for t in row.get('terminal_evidence') or [])]

    def task_event(self, session, event, cursor):
        """Retain terminals even if native projection already removed the task from its level."""
        task_id, run = event.get('task_id'), event.get('run_id')
        if not isinstance(task_id, str) or not isinstance(run, str):
            return
        row = self.get(identity('claude', session.record['execution_id'], session.generation, task_id, run))
        if not row or not row.get('detachable') or not row.get('source') or not row['owner'].get('tool_use_id'):
            # Terminal projection has not removed the old task yet. Capture its full input
            # now as well as at switch time, so a natural completion cannot erase the wait.
            self.inventory()
            row = self.get(identity('claude', session.record['execution_id'], session.generation, task_id, run))
            if not row:
                return
        status = (event.get('patch') or {}).get('status') or event.get('status')
        if status in TERMINALS:
            classification = recognize(((row.get('source') or {}).get('input') or {}).get('command'))
            if classification['detachable'] and row.get('native_task_type') in (None, 'local_bash'):
                row.update(detachable=True, reason=None,
                    terminal_process_witness_unavailable=not bool(row['processes']))
            row.setdefault('terminal_evidence', []).append({'status': status, 'cursor': cursor,
                'subtype': event.get('subtype'), 'run_id': run})
            self.save(row, 'observation_terminal_observed')

    def reconcile(self, row):
        from .host import native_id, witness
        owner = row['owner']
        session = self.host.sessions.get(native_id(owner['session']))
        record = session.record if session else self.store.get('sessions', native_id(owner['session'])) or {}
        if (not session and record.get('state') == 'ownership_unknown' and
                row.get('retirement', {}).get('signal_authorized') and record.get('process_witness') and
                witness(record.get('pid')) != record['process_witness']):
            self.host.recheck_orphan(record)  # exact witness + inherited lock proof, not the 60 s observer cadence
            record = self.store.get('sessions', native_id(owner['session'])) or record
        same_generation = record.get('generation') == owner['generation']
        level = record.get('background_tasks') or {}
        task = level.get(owner['task_id'])
        terminal = row.get('terminal_evidence') or []
        level_cursor = record.get('background_level_cursor')
        fresh = bool(terminal and level_cursor and self.store.parse_cursor(level_cursor) >
                     max(self.store.parse_cursor(e['cursor']) for e in terminal))
        absent = same_generation and record.get('background_level_known') is True and task is None and fresh
        gone = bool(row['processes'] or terminal) and all(witness(p['witness']['pid']) != p['witness'] for p in row['processes'])
        row['readback'] = {'generation': record.get('generation'), 'level_known': record.get('background_level_known'),
                           'level_cursor': record.get('background_level_cursor'), 'exact_task_absent': absent,
                           'original_processes_gone': gone, 'owner': record.get('owner')}
        for watched in row.get('watched_target_snapshots') or []:
            latest = watched_snapshot(self.host, watched['before']['target'])
            watched['latest'] = latest
            before = watched['before']
            if (latest.get('availability') == 'observed' and
                    any(latest.get(key) != before.get(key) for key in ('generation', 'last_result_cursor', 'native_receipt', 'quota_halt'))):
                races = row.setdefault('raced_target_events', [])
                if latest not in races:
                    races.append(latest)  # actual native result/quota evidence, never worker acceptance
        retired = (same_generation and record.get('owner') == 'gone' and record.get('state') == 'exited'
                   and row.get('retirement', {}).get('signal_authorized'))
        if gone and (retired or (terminal and absent)):
            statuses = {e['status'] for e in terminal}
            row['status'] = 'raced_completion' if statuses & {'completed', 'failed'} else 'stopped' if terminal else 'detached'
            row['outcome'] = {'task': sorted(statuses), 'observation_ended': True,
                              'native_terminal': 'observed' if terminal else 'unknown; exact mechanism proved exited',
                              'shell_exit_raced_freeze': (row.get('retirement', {}).get('shell_exit_before_shutdown') or {}).get(row['observation_id'], False),
                              'watched_work': 'untouched; reconcile actual result/socket before re-arming'}
        elif row.get('stop_request_id'):
            receipt = self.store.get('controls', native_id(owner['session']), row['stop_request_id'])
            row['stop_receipt'] = receipt
            if receipt and receipt.get('status') not in ('pending', 'success'):
                row['status'] = 'stop_failed'
            elif row['status'] not in ('stopped', 'raced_completion'):
                row['status'] = 'stop_uncertain'
        return self.save(row, 'observation_reconciled')

    async def stop(self, observation_id, *, authority):
        """Journal/revalidate one exact observer and issue at most one native stop for its run.

        Returns immediately after durable issuance. Disconnect/retry never cancels the native
        control or sends it again; `observations` readback reconciles terminal/level/process proof.
        This is not an arbitrary task-kill API and does not cancel watched sessions.
        """
        from .host import native_id, witness
        if not isinstance(authority, str) or not authority.strip():
            raise ValueError('explicit switch/operator authority required')
        row = self.get(observation_id)
        if not row:
            raise ValueError('unknown exact observation ID; inventory first')
        if row.get('stop_request_id') or row['status'] in ('stopped', 'detached', 'raced_completion'):
            return self.reconcile(row)
        if row.get('detachable') is not True:
            raise RuntimeError(row.get('reason') or 'not a proven observer')
        if row.get('process_binding') == 'shared_shell_population':
            raise RuntimeError('identical observers share process scope; use exact observer-only owner retirement for the switch')
        owner = row['owner']
        session = self.host.session(owner['session'], owner['generation'])
        async with session.command_lock:
            # Re-read after awaiting the admission lock: another caller may have issued it.
            row = self.get(observation_id)
            if row.get('stop_request_id'):
                return self.reconcile(row)
            task = (session.record.get('background_tasks') or {}).get(owner['task_id'])
            if not task or task.get('run_id') != owner['run_id']:
                row.update(status='identity_changed', reason='exact task/run/tool-use no longer current; no stop issued')
                return self.save(row, 'observation_stop_not_issued')
            if session.record.get('infrastructure_error') or session.record.get('owner') != 'host':
                raise RuntimeError('observer owner/durability is uncertain')
            if not row['owner_process'] or witness(session.process.pid) != row['owner_process']:
                raise RuntimeError('observer native PID/start witness changed')
            source, reason = full_task_input(self.host, session, {**task, 'task_id': owner['task_id']})
            if reason or source != row['source']:
                raise RuntimeError('full executed input changed or became ambiguous')
            processes, process_reason = task_processes(session, source['input']['command'])
            if process_reason or active_observer_effect(processes):
                raise RuntimeError(process_reason or active_observer_effect(processes))
            if not any(witness(p['witness']['pid']) == p['witness'] for p in row['processes']):
                row.update(status='stop_uncertain', reason='original shell already exited; await exact native terminal/level')
                return self.save(row, 'observation_stop_not_issued')
            request_id = str(uuid.uuid4())
            row.update(status='stop_intended', stop_request_id=request_id, authority=authority,
                       stop_intended_at=time.time())
            # Intent commits before control can enqueue bytes. If this process dies between
            # these writes, replay retains uncertainty and never blindly reissues the action.
            self.save(row, 'observation_stop_intended')
            try:
                receipt = session.control('stop_task', {'task_id': owner['task_id']}, request_id=request_id)
                receipt.update(operation_class='observation_stop', observation_id=observation_id,
                               qualified_owner=owner, authority=authority)
                self.store.put('controls', native_id(owner['session']), receipt, request_id)
                row.update(status='stop_uncertain', stop_receipt=receipt)
                return self.save(row, 'observation_stop_issued')
            except BaseException as error:
                row.update(status='stop_uncertain', stop_error=type(error).__name__)
                self.save(row, 'observation_stop_uncertain')
                raise

    def records(self):
        return [self.reconcile(row) for row in self.store.records('observations')]

    def retirement_refusal(self, session, rows):
        """An entire native owner is disposable ONLY when every executing task is an observer."""
        from .host import witness
        record = session.record
        if (record.get('owner') != 'host' or record.get('state') != 'ready'
                or not record.get('process_witness') or witness(session.process.pid) != record['process_witness']):
            return 'native owner/generation/PID-start witness is not ready and exactly owned'
        for key in ('pending_message_uuids', 'pending_recovery', 'native_retry_active', 'pending_native_requests',
                    'native_queued_turns', 'infrastructure_error', 'protocol_attention', 'stdout_eof',
                    'pending_control_requests'):
            if record.get(key):
                return 'genuine work or receipt ambiguity: ' + key
        pending_wake = self.pending_wake_observations(session)
        if record.get('primary_turn_open') is not False and not pending_wake:
            return 'the native owner has an open or unknown primary turn'
        if record.get('native_session_state') not in ('idle', 'running'):
            return 'native session state is unknown or requires action'
        if record.get('background_level_known') is not True:
            return 'native task registry level is unknown'
        tasks = record.get('background_tasks') or {}
        if not tasks and pending_wake and rows:
            wanted = {r['observation_id'] for r in pending_wake}
            if all(r['observation_id'] in wanted and r['owner']['generation'] == session.generation
                   and r['owner_process'] == record['process_witness'] for r in rows):
                return None  # only exact proven observer notifications remain in the native owner
            return 'pending observer notification has ambiguous owner/process evidence'
        if not tasks or len(rows) != len(tasks):
            return 'not every current task has an exact observation record'
        for row in rows:
            owner = row['owner']
            task = tasks.get(owner['task_id'])
            if not row.get('detachable'):
                return f"task {owner['task_id']} run {owner['run_id']}: {row.get('reason')}"
            if not task or task.get('run_id') != owner['run_id']:
                return f"task {owner['task_id']} incarnation changed"
            source, reason = full_task_input(self.host, session, {**task, 'task_id': owner['task_id']})
            if reason or source != row['source']:
                return f"task {owner['task_id']} full scoped input/tool-use evidence changed"
            if owner['generation'] != session.generation or row['owner_process'] != record['process_witness']:
                return 'observation owner generation/process witness changed'
        return None

    def switch_inventory(self):
        """Whole-host admission inventory with exact task/reason disclosure, not a bypass."""
        from .settlement import settlement_blockers
        # Reconcile already-authorized retirements after a host restart before treating an
        # expired old owner as an obstacle; the same inherited UUID-lock proof still applies.
        for row in self.store.records('observations'):
            if row.get('retirement', {}).get('signal_authorized'):
                self.reconcile(row)
        rows = self.inventory()
        candidates, blockers = [], []
        for session in self.host.sessions.values():
            holds = settlement_blockers(session.record, automatic=False)
            if not holds and not self.pending_wake_observations(session):
                continue
            own = [r for r in rows if r['owner']['session'] == session.record['execution_id'] and
                   r['owner']['generation'] == session.generation]
            refusal = self.retirement_refusal(session, own)
            if refusal:
                blockers.append({'execution_id': session.record['execution_id'], 'generation': session.generation,
                    'title': (session.record.get('labels') or {}).get('title'), 'reasons': holds,
                    'observer_reason': refusal, 'tasks': [{'task_id': r['owner']['task_id'],
                        'run_id': r['owner']['run_id'], 'kind': r['kind'], 'detachable': r['detachable'],
                        'purpose': r.get('purpose'), 'reason': r.get('reason')} for r in own]})
            else:
                candidates.append({'execution_id': session.record['execution_id'], 'generation': session.generation})
        # A stored unknown/draining owner is not absent merely because no writer object exists.
        for _, record, _ in self.store.cached_sessions():
            if record['native_uuid'] in self.host.sessions or record.get('owner') == 'gone':
                continue
            holds = settlement_blockers(record, automatic=False)
            if holds:
                blockers.append({'execution_id': record['execution_id'], 'generation': record['generation'],
                                 'reasons': holds, 'observer_reason': 'no owned native writer; inspect exact ownership'})
        return {'observations': rows, 'candidates': candidates, 'blockers': blockers}

    def prepare_switch(self, *, operation_id, requested_account, authority):
        """Fence new host inputs only after a complete admissible snapshot and durable index."""
        if not isinstance(operation_id, str) or not operation_id or not isinstance(authority, str) or not authority.strip():
            raise ValueError('exact switch operation and authority required')
        active = self.host.observer_switch
        if active and active != operation_id:
            raise RuntimeError('another exact account transition is already admitted: ' + active)
        inventory = self.switch_inventory()
        if inventory['blockers']:
            return {**inventory, 'outcome': 'held'}
        self.host.observer_recovery.stage(operation_id, inventory['observations'],
            requested_account=requested_account, authority=authority, outcome='preparing')
        self.host.observer_switch = operation_id
        return {**inventory, 'operation_id': operation_id, 'outcome': 'prepared'}

    def finish_switch(self, *, operation_id, outcome, **facts):
        if self.host.observer_switch == operation_id:
            self.host.observer_switch = None
        rows = [r for r in self.records() if r.get('retirement', {}).get('operation_id') == operation_id]
        return self.host.observer_recovery.update(operation_id, observations=rows, outcome=outcome, **facts)

    def retirement(self, operation_id, execution_id, generation):
        from .host import native_id, witness
        rows = [self.reconcile(row) for row in self.store.records('observations') if
                row['owner']['session'] == execution_id and row['owner']['generation'] == generation and
                row.get('retirement', {}).get('operation_id') == operation_id]
        session = self.host.sessions.get(native_id(execution_id))
        record = session.record if session else self.store.get('sessions', native_id(execution_id)) or {}
        owner_ended = (record.get('generation') == generation and record.get('state') == 'exited' and
                       record.get('owner') == 'gone' and record.get('process_witness') and
                       witness(record.get('pid')) != record['process_witness'])
        # A timer's natural completion is NOT retirement of its still-live queued wake.
        ended = owner_ended and bool(rows) and all(row['status'] in ('stopped', 'detached', 'raced_completion') for row in rows)
        return {'operation_id': operation_id, 'execution_id': execution_id, 'generation': generation,
                'outcome': 'ended' if ended else 'retiring' if (operation_id, execution_id, generation) in self.retirements
                else 'uncertain', 'observations': rows}

    # @beacon[
    #   id=opc@retire_observer_owner,
    #   slice_labels=opc-basic,opc-claude-seats,
    #   kind=ast,
    #   comment=Switch-only retirement of one exactly witnessed native owner containing ONLY proven observers: persist every obligation, freeze native callbacks, revalidate drained state/process scope, then native shutdown without inference; genuine work and watched jobs are never signalled.,
    # ]
    async def retire(self, *, operation_id, execution_id, generation, authority):
        """No model call and no global/process-group kill. Complete exact durable stop intent.

        Native stop_task is available above, but neither EOF nor interrupt is a suppression
        lease for a *naturally* completed task. For a switch the whole observer-only native
        owner must retire: freeze its callbacks before shutdown, keep every native terminal,
        prove original observer processes gone, and let normal drains/UUID-lock exit settle.
        """
        if not isinstance(operation_id, str) or not operation_id or not isinstance(authority, str) or not authority.strip():
            raise ValueError('exact switch operation and authority required')
        key = (operation_id, execution_id, generation)
        if key in self.retirements:
            return self.retirement(*key)
        existing = self.retirement(*key)
        if existing['observations']:
            if not all(r.get('retirement', {}).get('phase') in ('not_stopped', 'intended') and
                       not r.get('retirement', {}).get('signal_authorized') for r in existing['observations']):
                return existing  # never repeat an uncertain shutdown after a lost reply or reload
        session = self.host.session(execution_id, generation)
        async with session.command_lock:
            if key in self.retirements:
                return self.retirement(*key)  # another caller won while this one waited for admission
            rows = [r for r in self.inventory() if r['owner']['session'] == execution_id and r['owner']['generation'] == generation]
            reason = self.retirement_refusal(session, rows)
            if reason:
                return {**existing, 'outcome': 'held', 'reason': reason}
            intent = {'operation_id': operation_id, 'authority': authority, 'phase': 'intended',
                      'owner_process': session.record['process_witness'], 'intended_at': time.time(),
                      'last_assistant_cursor': session.record.get('last_assistant_cursor')}
            # One batch commits every affected obligation BEFORE the first process effect.
            with self.store.batch():
                for row in rows:
                    if row.get('retirement'):
                        row.setdefault('retirement_history', []).append(row['retirement'])
                    row.update(status='stop_intended', retirement=dict(intent))
                    self.save(row, 'observation_owner_retirement_intended')
                session.update(observation_transition=intent)
            work = asyncio.create_task(self._retire(session, rows, intent))
            self.retirements[key] = work
            session.control_tasks.add(work)
            def finished(task):
                self.retirements.pop(key, None)
                session.control_tasks.discard(task)
                try:
                    task.result()
                except (Exception, asyncio.CancelledError):
                    pass  # exact outcome is retained by _retire, not a claimed success
            work.add_done_callback(finished)
        return self.retirement(*key)

    async def _retire(self, session, rows, intent):
        from .host import witness
        from .collection import context_helper
        frozen, stopping = False, False
        proof = intent['owner_process']
        def update(phase, **facts):
            intent.update(phase=phase, **facts)
            with self.store.batch():
                for item in rows:
                    row = self.get(item['observation_id'])
                    row.update(retirement=dict(intent), status='stop_uncertain')
                    self.save(row, 'observation_owner_retirement_' + phase)
                session.update(observation_transition=dict(intent))
        async with session.command_lock:
            try:
                reason = self.retirement_refusal(session, rows)
                if reason:
                    raise RuntimeError(reason)
                if witness(session.process.pid) != proof:
                    raise RuntimeError('native owner witness changed before freeze')
                os.kill(session.process.pid, signal.SIGSTOP)
                frozen = True
                # Wait only for the exact kernel stop, not a timer deadline. Raw drains continue
                # in the host. A cancellation cannot leave an old-login native owner frozen.
                while _process_state(session.process.pid, proof) not in ('T', 't'):
                    if witness(session.process.pid) != proof:
                        raise RuntimeError('native owner exited before freeze confirmation')
                    await asyncio.sleep(.01)
                update('frozen', frozen_at=time.time())
                while not _stdout_drained(session):
                    if session.infrastructure_error:
                        raise RuntimeError('native output could not be retained at freeze')
                    await asyncio.sleep(.01)
                record = session.record
                if not session.stdout_at_boundary:
                    raise RuntimeError('partial native frame at freeze; inspect retained raw output')
                if record.get('last_assistant_cursor') != intent['last_assistant_cursor']:
                    raise RuntimeError('new model/tool work appeared before native freeze')
                for field in ('pending_message_uuids', 'pending_recovery', 'native_retry_active',
                              'pending_native_requests', 'native_queued_turns', 'infrastructure_error', 'protocol_attention'):
                    if record.get(field):
                        raise RuntimeError('work/receipt state changed before freeze: ' + field)
                expected = {row['owner']['task_id']: row['owner']['run_id'] for row in rows}
                if any(expected.get(task_id) != task.get('run_id') for task_id, task in
                       (record.get('background_tasks') or {}).items()):
                    raise RuntimeError('new or changed native task before freeze')
                current_processes = process_tree(session.process.pid)
                active_effect = active_observer_effect(current_processes)
                if active_effect:
                    raise RuntimeError(active_effect)
                observer_pids = {p['witness']['pid'] for row in rows for p in row['processes']
                                 if witness(p['witness']['pid']) == p['witness']}
                # Children newly spawned by a known observer script belong to that observer,
                # not its target. Every other native descendant must be the context MCP.
                for _ in current_processes:
                    observer_pids.update(p['witness']['pid'] for p in current_processes if p['parent_pid'] in observer_pids)
                others = [p for p in current_processes if p['witness']['pid'] not in observer_pids and
                          not context_helper(p['argv'])]
                if others:
                    raise RuntimeError('unattributed native child process: ' + ', '.join(str(p['witness']['pid']) for p in others))
                update('shutdown_authorized', signal_authorized=True,
                       descendants=current_processes, frozen_primary_turn_open=record.get('primary_turn_open'),
                       shell_exit_before_shutdown={row['observation_id']: not any(
                           witness(p['witness']['pid']) == p['witness'] for p in row['processes']) for row in rows},
                       raced_wake=bool(record.get('native_wake_pending') or record.get('primary_turn_open')))
                session.update(state='input_closing', close_reason='observer_switch_retirement')
                if witness(session.process.pid) != proof:
                    raise RuntimeError('native witness changed before exact shutdown')
                os.kill(session.process.pid, signal.SIGTERM)
                stopping = True
                os.kill(session.process.pid, signal.SIGCONT)
                frozen = False
                update('shutdown_issued', shutdown_issued_at=time.time())
                while session.record.get('owner') != 'gone':
                    await asyncio.sleep(.01)  # native exit AND both durable drains/lock release
                update('owner_exited', owner_exit_code=session.process.returncode)
                for row in rows:
                    self.reconcile(self.get(row['observation_id']))
            except BaseException as error:
                update('uncertain' if stopping else 'not_stopped', error=type(error).__name__ + ': ' + str(error))
                raise
            finally:
                if frozen and witness(session.process.pid) == proof:
                    # SIGTERM, if delivered, is already pending on the frozen owner. Always
                    # release the exact freeze even when signal delivery itself failed; do
                    # not hide a failed stop behind an indefinitely frozen old-login owner.
                    os.kill(session.process.pid, signal.SIGCONT)
                if not stopping:
                    session.update(observation_transition=None, **({'state': 'ready', 'close_reason': None}
                        if session.record.get('state') == 'input_closing' and session.process.returncode is None else {}))

    def recover_frozen_retirements(self):
        """Finish an admitted exact frozen-owner shutdown after host death, never a new kill.

        A reused PID, unknown effect, changed run/input or a non-frozen owner is not signalled.
        Missing native pipe receipts stay unknown; normal orphan/lock readback proves final exit.
        """
        from .host import native_id, witness
        from .collection import context_helper
        groups = {}
        for row in self.store.records('observations'):
            retirement = row.get('retirement') or {}
            if retirement.get('phase') in ('intended', 'frozen', 'shutdown_authorized', 'shutdown_issued', 'uncertain', 'recovery_shutdown_authorized'):
                groups.setdefault((row['owner']['session'], row['owner']['generation']), []).append(row)
        for (execution_id, generation), rows in groups.items():
            if native_id(execution_id) in self.host.sessions:
                continue
            record = self.store.get('sessions', native_id(execution_id)) or {}
            intent = dict(rows[0]['retirement'])
            proof = intent.get('owner_process')
            if (not proof or record.get('generation') != generation or witness(proof['pid']) != proof
                    or _process_state(proof['pid'], proof) not in ('T', 't')):
                continue
            reason = None
            expected = {r['owner']['task_id']: r['owner']['run_id'] for r in rows}
            if (record.get('last_assistant_cursor') != intent.get('last_assistant_cursor') or
                    any(record.get(field) for field in ('pending_message_uuids', 'pending_recovery',
                        'pending_native_requests', 'native_retry_active', 'native_queued_turns',
                        'infrastructure_error', 'protocol_attention')) or
                    any(expected.get(tid) != task.get('run_id') for tid, task in (record.get('background_tasks') or {}).items())):
                reason = 'frozen retirement work/receipt evidence changed; inspect exact old owner'
            if not all(row.get('detachable') and row.get('owner_process') == proof for row in rows):
                reason = 'frozen retirement does not carry a complete exact observer population'
            descendants = process_tree(proof['pid'])
            if active_observer_effect(descendants):
                reason = active_observer_effect(descendants)
            known = {p['witness']['pid'] for row in rows for p in row['processes'] if witness(p['witness']['pid']) == p['witness']}
            for _ in descendants:
                known.update(p['witness']['pid'] for p in descendants if p['parent_pid'] in known)
            if any(p['witness']['pid'] not in known and not context_helper(p['argv']) for p in descendants):
                reason = 'unattributed child under frozen retirement owner; inspect before stopping'
            if reason:
                for row in rows:
                    row['retirement']['recovery_problem'] = reason
                    row['retirement']['phase'] = 'recovery_held_thaw_intended'
                    self.save(row, 'observation_retirement_recovery_held')
                if witness(proof['pid']) == proof:
                    os.kill(proof['pid'], signal.SIGCONT)  # leave discovered genuine work alone, not frozen
                continue
            intent.update(signal_authorized=True, phase='recovery_shutdown_authorized',
                          recovery_at=time.time(), native_terminal_gap='host died; original pipe/drain receipts may be unavailable')
            with self.store.batch():
                for row in rows:
                    row.update(retirement=dict(intent), status='stop_uncertain')
                    self.save(row, 'observation_retirement_recovery_authorized')
            if witness(proof['pid']) == proof:
                try:
                    os.kill(proof['pid'], signal.SIGTERM)
                except ProcessLookupError:
                    pass  # normal exact exit won the race; orphan readback observes it
                except OSError as error:
                    for row in rows:
                        row['retirement']['recovery_problem'] = 'exact shutdown signal failed: ' + type(error).__name__
                        self.save(row, 'observation_retirement_recovery_signal_failed')
                finally:
                    if witness(proof['pid']) == proof:
                        os.kill(proof['pid'], signal.SIGCONT)


def _process_state(pid, proof):
    from .host import witness
    if witness(pid) != proof:
        return None
    text = Path(f'/proc/{pid}/stat').read_text()
    return text[text.rfind(')') + 2:].split()[0]


def _stdout_drained(session):
    """Frozen writer: both kernel pipe and asyncio reader must have reached the parser."""
    import array
    import fcntl
    import termios
    pipe = session.process._transport.get_pipe_transport(1)
    if pipe is None:
        return session.record.get('stdout_eof') is True
    remaining = array.array('i', [0])
    fcntl.ioctl(pipe.get_extra_info('pipe').fileno(), termios.FIONREAD, remaining, True)
    return remaining[0] == 0 and not session.process.stdout._buffer
