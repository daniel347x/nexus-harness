"""Observer recovery publications: isolated SQLite + Workflowy wire fixtures, never production."""
import asyncio
from copy import deepcopy
import json
import threading
import types
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from claude_headless.observer_recovery import (CAPTURE_LIMITATION, MANAGER_DOCK, ObserverRecovery,
                                             read_page, resolve_current_conductor)
from claude_headless.store import Store


def test_separate_store_writers_do_not_lose_outcome_or_publication_facts(tmp_path):
    root = tmp_path / 'state'
    initial = Store(root)
    ObserverRecovery(initial).stage('switch-1', [], outcome='preparing')
    initial.db.close()
    read_locked, release, second_started = threading.Event(), threading.Event(), threading.Event()
    def first_writer():
        store = Store(root)
        coordinator = ObserverRecovery(store)
        save = coordinator._save
        def paused(record):
            read_locked.set()
            assert release.wait(3)
            return save(record)
        coordinator._save = paused
        try:
            coordinator.update('switch-1', outcome='complete', verified_host='new-host')
        finally:
            store.db.close()
    def second_writer():
        second_started.set()
        store = Store(root)
        try:
            ObserverRecovery(store)._patch_page('switch-1', 'master', problem='readback pending')
        finally:
            store.db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first_writer)
        assert read_locked.wait(3)
        two = pool.submit(second_writer)
        assert second_started.wait(3)
        release.set()
        one.result(3)
        two.result(3)
    final = Store(root)
    record = ObserverRecovery(final).read('switch-1')
    assert record['facts']['outcome'] == 'complete'
    assert record['facts']['verified_host'] == 'new-host'
    assert record['pages']['master']['problem'] == 'readback pending'
    final.db.close()


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / 'state')
    # The primary owns the Store migration; keep this narrow fixture independently runnable
    # while that nonoverlapping file is being integrated in the shared checkout.
    store.db.execute('CREATE TABLE IF NOT EXISTS observer_recoveries '
                     '(sid TEXT, rid TEXT, body TEXT NOT NULL, PRIMARY KEY(sid,rid))')
    store.db.commit()
    yield store
    store.db.close()


def observation(oid='one', *, program='[ap:PROG1]', session='cc-helper', parent='ses_worker',
                role='helper', status='stopped', target=None, problem=None):
    backend = 'claude' if session.startswith('cc-') else 'opencode'
    chain = [{'backend': backend, 'session': session, 'role': role, 'parent': parent,
              'conductor': 'cc-historical', 'program': program}]
    if parent:
        chain += [{'backend': 'opencode', 'session': parent, 'role': 'worker', 'parent': 'cc-historical',
                   'conductor': 'cc-historical', 'program': program},
                  {'backend': 'claude', 'session': 'cc-historical', 'role': 'conductor', 'parent': None,
                   'conductor': 'cc-historical', 'program': program}]
    return {'schema': 'observation/1', 'observation_id': oid,
            'owner': {'backend': backend, 'session': session, 'generation': 7, 'task_id': 'task-' + oid,
                      'run_id': 'run-' + oid, 'tool_use_id': 'tool-' + oid},
            'program': program, 'ownership_chain': chain, 'ownership_problem': problem,
            'kind': 'session_doorbell' if target else 'timer', 'purpose': 'quota backoff or assignment doorbell',
            'obligations': [{'kind': 'session_doorbell', 'target': target, 'max_seconds': 540}] if target else
                           [{'kind': 'timer', 'seconds': 7190}],
            'deadlines': [{'due_at': 1234567890}],
            'source': {'input': {'command': 'sleep 7190; date -u'}, 'cursor': 'store:12', 'scope': None},
            'owner_process': {'pid': 123, 'start': 456}, 'processes': [{'witness': {'pid': 789, 'start': 321}}],
            'status': status, 'stop_request_id': 'stop-' + oid,
            'terminal_evidence': [{'status': 'stopped', 'cursor': 'store:22'}] if status == 'stopped' else [],
            'readback': {'exact_task_absent': status == 'stopped'},
            'watched_work': 'untouched; observer exit/idle is not accepted worker completion'}


class WorkflowyFixture:
    """Wire-level call interface with explicit effects/lost replies and exact note reads."""
    def __init__(self, store=None, operation='switch-1'):
        self.store, self.operation = store, operation
        self.pages, self.calls = {}, []
        self.capture_failure = None
        self.edit_failure = None
        self.read_failure = False
        self.hook = None

    async def call(self, family, verb, params):
        self.calls.append((family, verb, deepcopy(params)))
        if self.hook:
            await self.hook(family, verb, params)
        if verb == 'read':
            if self.read_failure:
                raise ConnectionError('Workflowy unavailable')
            page = self.pages.get(params['node_handle'])
            if page is None:
                return json.dumps({'success': False, 'error': {'code': 'operation_failed',
                    'message': 'workflowy_read_node: handle did not resolve to a Workflowy node'}})
            return '[name: not editable] ' + page['name'] + '\n\n' + page['note']
        if verb == 'point.capture':
            handle = params['ap_id']
            if self.store:
                record = self.store.get('observer_recoveries', '', self.operation)
                assert next(page for page in record['pages'].values() if page['ap_id'] == handle)['create_state'] == 'issued'
            if self.capture_failure == 'before':
                raise TimeoutError('capture transport lost before effect (caller cannot know)')
            if handle in self.pages:
                return json.dumps({'success': False, 'error': {'code': 'ap_id_collision'}})
            uid = str(uuid.uuid5(uuid.NAMESPACE_URL, handle))
            self.pages[handle] = {'name': handle + ' ' + params['name'], 'note': params['content'],
                                  'parent': params['parent_handle'], 'uuid': uid}
            if self.capture_failure == 'after':
                raise TimeoutError('capture reply lost after effect')
            return json.dumps({'success': True, 'uuid': uid, 'root_node_ids': [uid], 'ap_id': handle})
        if verb == 'edit':
            self.pages[params['node_handle']]['note'] = params['note']
            if self.edit_failure:
                raise TimeoutError('edit reply lost after effect')
            return json.dumps({'success': True, 'node_id': self.pages[params['node_handle']]['uuid']})
        pytest.fail('Unexpected effect: ' + family + '.' + verb)

    def count(self, verb):
        return sum(call[1] == verb for call in self.calls)


def service(store, workflowy=None, resolver=lambda program: ('ses_current', 4)):
    return ObserverRecovery(store, workflowy=workflowy, resolve_conductor=resolver, clock=lambda: 100)


def test_stage_is_network_free_stable_and_committed_before_effects(store):
    def forbidden(*args):
        pytest.fail('stage must not resolve or publish')
    recovery = service(store, forbidden, forbidden)
    row = observation(status='armed')
    record = recovery.stage('switch-1', [row], outcome='preparing')
    assert record['publication']['status'] == 'pending'
    assert record['pages']['master']['parent'] == MANAGER_DOCK
    assert record['pages']['[ap:PROG1]']['parent'] == '[ap:PROG1]'
    assert all(page['create_state'] == 'not_issued' for page in record['pages'].values())
    persisted = Store(store.root)
    try:
        assert persisted.get('observer_recoveries', '', 'switch-1') == record
        reloaded = service(persisted, forbidden, forbidden)
        assert reloaded.stage('switch-1', [row], master_parent='ignored')['pages'] == record['pages']
    finally:
        persisted.db.close()


def test_update_retains_stopped_obligations_and_honest_partial_outcomes(store):
    recovery = service(store)
    initial = recovery.stage('switch-1', [observation(), observation('two', status='stop_failed')])
    recovery.update('switch-1', observations=[{'observation_id': 'one', 'status': 'armed'}],
                    outcome='timeout', error='destination authentication failed', replacement_started=False)
    record = recovery.read('switch-1')
    assert record['pages'].keys() == initial['pages'].keys()
    assert [row['status'] for row in record['observations']] == ['stopped', 'stop_failed']
    assert record['observations'][0]['stop_request_id'] == 'stop-one'
    assert record['observations'][0]['terminal_evidence']
    assert record['facts']['outcome'] == 'timeout' and not record['facts']['replacement_started']
    recovery.update('switch-1', observations=[], outcome='cancelled')
    assert len(recovery.read('switch-1')['observations']) == 2


def test_full_reports_nested_both_backend_targets_manager_standalone_unknown_and_races(store):
    async def scenario():
        wf = WorkflowyFixture(store)
        recovery = service(store, wf)
        rows = [observation(target={'backend': 'opencode', 'session': 'ses_watched'}),
                observation('own', role='conductor', session='cc-historical', parent=None),
                observation('second', program='[ap:PROG2]', session='ses_helper',
                            target={'backend': 'claude', 'session': 'cc-watched'}, status='stop_uncertain'),
                observation('manager', program=None, session='cc-manager', parent=None, role='Manager'),
                observation('standalone', program=None, session='ses_direct', parent=None, role='worker'),
                observation('unknown', program=None, parent=None, problem='parent conflict'),
                observation('nested-unknown', problem='missing intermediate parent'),
                observation('race', status='raced_completion', target={'socket': '[ap:WORKSOCKET]'})]
        rows[-1]['terminal_evidence'] = [{'status': 'failed', 'cursor': 'store:23', 'exit_code': 1}]
        recovery.stage('switch-1', rows, outcome='partial_failure', stop_failed=['second'],
                       host_started=False, failure='replacement startup failed')
        record = await recovery.publish('switch-1')
        assert record['publication']['status'] == 'confirmed'
        assert len(wf.pages) == 3 and wf.count('point.capture') == 3
        master = wf.pages[record['pages']['master']['ap_id']]['note']
        for label in ('## Manager waits', '## Standalone waits', '## Unresolved ownership', 'missing intermediate parent'):
            assert label in master
        program = wf.pages[record['pages']['[ap:PROG1]']['ap_id']]['note']
        for text in ('cc-helper', 'ses_worker', 'cc-historical', 'own', '7190', 'raced_completion',
                     'store:23', 'WORKSOCKET', 'stable PARTIAL', 'UNTOUCHED', 'replacement startup failed'):
            assert text in program
        assert 'ses_current' in program and record['routes']['[ap:PROG1]']['backend'] == 'opencode'
        assert all(family in ('node', 'anchor') for family, _, _ in wf.calls)
        assert not {'send', 'resume', 'steer', 'stop_task'} & {verb for _, verb, _ in wf.calls}
        assert recovery.read('switch-1')['observations'] == rows  # reporting never mutates target/owner facts
    asyncio.run(scenario())


def test_lost_create_reply_reconciles_same_identity_across_reload_without_duplicate(store):
    async def scenario():
        wf = WorkflowyFixture(store)
        wf.capture_failure = 'after'
        recovery = service(store, wf)
        staged = recovery.stage('switch-1', [observation()])
        first = await recovery.publish('switch-1')
        assert first['publication']['status'] == 'pending'
        assert all(page['create_state'] == 'issued' for page in first['pages'].values())
        assert len(wf.pages) == 2
        wf.capture_failure = None
        reloaded = service(store, wf)
        result = await reloaded.publish('switch-1')
        assert result['publication']['status'] == 'confirmed'
        assert wf.count('point.capture') == 2 and len(wf.pages) == 2
        assert [page['ap_id'] for page in result['pages'].values()] == [page['ap_id'] for page in staged['pages'].values()]
    asyncio.run(scenario())


def test_request_lost_before_create_remains_uncertain_not_an_invented_idempotent_retry(store):
    async def scenario():
        wf = WorkflowyFixture(store)
        wf.capture_failure = 'before'
        recovery = service(store, wf)
        recovery.stage('switch-1', [observation()])
        await recovery.publish('switch-1')
        assert not wf.pages and wf.count('point.capture') == 2
        wf.capture_failure = None
        result = await service(store, wf).publish('switch-1')
        assert result['publication']['status'] == 'pending'
        assert all(page['problem'] == CAPTURE_LIMITATION for page in result['pages'].values())
        assert not wf.pages and wf.count('point.capture') == 2
    asyncio.run(scenario())


def test_workflowy_outage_does_not_admit_create_or_lose_local_queue(store):
    async def scenario():
        wf = WorkflowyFixture(store)
        wf.read_failure = True
        recovery = service(store, wf)
        recovery.stage('switch-1', [observation()], outcome='complete', account='work')
        result = await recovery.publish('switch-1')
        assert result['publication']['status'] == 'pending' and not wf.pages
        assert all(page['create_state'] == 'not_issued' for page in result['pages'].values())
        notice = recovery.notice_text('switch-1')
        assert 'Local durable queue fallback:' in notice and str(store.root) in notice
        assert 'observer_recoveries' in notice and 'pending handles may not resolve' in notice
        wf.read_failure = False
        assert (await recovery.publish('switch-1'))['publication']['status'] == 'confirmed'
    asyncio.run(scenario())


@pytest.mark.parametrize('result', [None, ('cc-successor', 5), {'backend': 'opencode', 'session': 'ses_successor'}])
def test_current_conductor_resolved_at_each_publish_not_historical_sid(store, result):
    async def scenario():
        wf = WorkflowyFixture(store)
        current = ['ses_first']
        seen = []
        async def resolve(program):
            seen.append(program)
            return current[0]
        recovery = service(store, wf, resolve)
        recovery.stage('switch-1', [observation()])
        first = await recovery.publish('switch-1')
        current[0] = result
        second = await service(store, wf, resolve).publish('switch-1')
        assert first['pages'].keys() == second['pages'].keys()
        assert seen == ['[ap:PROG1]', '[ap:PROG1]'] and wf.count('point.capture') == 2
        route = second['routes']['[ap:PROG1]']
        if result is None:
            assert route['status'] == 'pending'
            assert 'current registry' in route['problem']
        else:
            assert route['session'].endswith('successor') and route['session'] != 'cc-historical'
        assert second['publication']['status'] == 'confirmed'
    asyncio.run(scenario())


def test_resolver_failure_publishes_route_pending_without_blocking_reports(store):
    async def scenario():
        def unavailable(program):
            raise ConnectionError('Workflowy/OpenCode registry temporarily unavailable')
        wf = WorkflowyFixture(store)
        recovery = service(store, wf, unavailable)
        recovery.stage('switch-1', [observation()])
        result = await recovery.publish('switch-1')
        assert result['routes']['[ap:PROG1]']['status'] == 'pending'
        assert result['publication']['status'] == 'confirmed'
        assert 'registry temporarily unavailable' in recovery.notice_text('switch-1')
    asyncio.run(scenario())


def test_lost_update_reply_and_pending_flags_reconcile_on_same_page(store):
    async def scenario():
        wf = WorkflowyFixture(store)
        recovery = service(store, wf)
        recovery.stage('switch-1', [observation(status='stop_uncertain')])
        await recovery.publish('switch-1')
        updated = recovery.update('switch-1', [observation()], outcome='complete')
        assert updated['publication']['status'] == 'pending'
        assert all(page['publication'] == 'pending' for page in updated['pages'].values())
        wf.edit_failure = True
        failed = await recovery.publish('switch-1')
        assert failed['publication']['status'] == 'pending'
        captures = wf.count('point.capture')
        wf.edit_failure = False
        confirmed = await service(store, wf).publish('switch-1')
        assert confirmed['publication']['status'] == 'confirmed'
        assert wf.count('point.capture') == captures == 2
    asyncio.run(scenario())


def test_update_during_network_yield_preserves_newer_facts_and_obligations(store):
    async def scenario():
        wf = WorkflowyFixture(store)
        recovery = service(store, wf)
        recovery.stage('switch-1', [observation(status='stop_uncertain')], outcome='stopping')
        changed = False
        async def hook(family, verb, params):
            nonlocal changed
            if verb == 'read' and not changed:
                changed = True
                recovery.update('switch-1', [observation(), observation('late', program='[ap:LATE]')],
                                outcome='auth_failed', replacement_started=False)
        wf.hook = hook
        first = await recovery.publish('switch-1')
        assert first['facts']['outcome'] == 'auth_failed'
        assert len(first['observations']) == 2 and first['pages']['[ap:LATE]']['publication'] == 'pending'
        assert first['publication']['status'] == 'pending'
        assert (await recovery.publish('switch-1'))['publication']['status'] == 'confirmed'
        assert len(wf.pages) == 3
    asyncio.run(scenario())


def test_cancel_during_capture_retains_admitted_identity_then_reconciles_effect(store):
    async def scenario():
        wf = WorkflowyFixture(store)
        recovery = service(store, wf)
        staged = recovery.stage('switch-1', [observation()])
        entered, resume = asyncio.Event(), asyncio.Event()
        async def hook(family, verb, params):
            if verb == 'point.capture':
                # Simulate remote effect admitted before local task cancellation.
                wf.pages[params['ap_id']] = {'name': params['ap_id'] + ' ' + params['name'],
                                            'note': params['content'], 'uuid': str(uuid.uuid4())}
                entered.set()
                await resume.wait()
        wf.hook = hook
        task = asyncio.create_task(recovery.publish('switch-1'))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        record = recovery.read('switch-1')
        assert record['publication']['status'] == 'pending'
        assert record['pages']['[ap:PROG1]']['create_state'] == 'issued'
        wf.hook = None
        result = await service(store, wf).publish('switch-1')
        assert result['publication']['status'] == 'confirmed'
        assert wf.count('point.capture') == 2 and len(wf.pages) == 2
        assert result['pages']['master']['ap_id'] == staged['pages']['master']['ap_id']
    asyncio.run(scenario())


def test_two_publisher_instances_admit_each_capture_only_once(store):
    async def scenario():
        wf = WorkflowyFixture(store)
        first, second = service(store, wf), service(store, wf)
        first.stage('switch-1', [observation()])
        entered, resume = asyncio.Event(), asyncio.Event()
        async def hook(family, verb, params):
            if verb == 'point.capture' and not entered.is_set():
                entered.set()
                await resume.wait()
        wf.hook = hook
        task = asyncio.create_task(first.publish('switch-1'))
        await entered.wait()
        await second.publish('switch-1')
        resume.set()
        await task
        await first.publish('switch-1')
        assert wf.count('point.capture') == 2 and len(wf.pages) == 2
        assert first.read('switch-1')['publication']['status'] == 'confirmed'
    asyncio.run(scenario())


def test_identity_collision_never_overwrites_another_page_or_allocates_replacement(store):
    async def scenario():
        wf = WorkflowyFixture(store)
        recovery = service(store, wf)
        staged = recovery.stage('switch-1', [observation()])
        handle = staged['pages']['[ap:PROG1]']['ap_id']
        wf.pages[handle] = {'name': 'Unrelated work', 'note': 'Not this report', 'uuid': str(uuid.uuid4())}
        result = await recovery.publish('switch-1')
        assert result['publication']['status'] == 'pending'
        assert wf.pages[handle]['note'] == 'Not this report'
        assert 'different recovery marker' in result['pages']['[ap:PROG1]']['problem']
        assert wf.count('point.capture') == 1
        assert result['pages']['[ap:PROG1]']['ap_id'] == handle
    asyncio.run(scenario())


def test_program_alias_normalization_and_conflicting_ownership_is_explicit(store):
    recovery = service(store)
    first, second = observation(), observation('two', program='ap:prog1')
    conflict = observation('conflict')
    conflict['ownership_chain'][-1]['program'] = '[ap:OTHER]'
    record = recovery.stage('switch-1', [first, second, conflict])
    assert len(record['pages']) == 2
    text = recovery._document(record, 'master')
    assert '## Unresolved ownership' in text and 'conflict' in text and '[ap:OTHER]' in text


def test_notice_is_one_manager_index_with_all_routes_and_no_success_inference(store):
    recovery = service(store)
    recovery.stage('switch-1', [observation(), observation('manager', program=None, role='Manager', parent=None),
                               observation('standalone', program=None, role='worker', parent=None),
                               observation('unknown', program=None, parent=None, problem='unknown ownership')],
                   outcome='cancelled', partial_stops=True)
    text = recovery.notice_text('switch-1')
    for expected in ('Master index:', 'Program [ap:PROG1]:', 'CURRENT Conductor on either backend',
                     'Manager waits: 1', 'Standalone waits: 1', 'Unresolved ownership: 1',
                     'waiter exit 0', 'partial failures', 'not confirmed delivery', 'cancelled'):
        assert expected in text
    assert text == recovery.notice_text('switch-1')


def test_detached_process_proof_retains_unknown_native_verdict_and_never_becomes_armed(store):
    recovery = service(store)
    row = observation(status='detached')
    row['terminal_evidence'] = []
    row['retirement'] = {'operation_id': 'switch-1', 'phase': 'owner_exited', 'signal_authorized': True}
    recovery.stage('switch-1', [row])
    record = recovery.update('switch-1', [{'observation_id': 'one', 'status': 'armed'}])
    assert record['observations'][0]['status'] == 'detached'
    text = recovery._document(record, '[ap:PROG1]')
    assert 'native task verdict remains unknown' in text
    assert 'owner_exited' in text and 'signal_authorized' in text
    assert not record['observations'][0]['terminal_evidence']


def test_program_inherited_from_opencode_immediate_parent_when_owner_has_no_program(store):
    recovery = service(store)
    row = observation()
    row['program'] = None
    row['ownership_chain'][0]['program'] = None
    record = recovery.stage('switch-1', [row])
    assert record['pages']['[ap:PROG1]']['parent'] == '[ap:PROG1]'
    assert 'ses_worker' in recovery._document(record, '[ap:PROG1]')
    # Missing foreign-parent evidence is left explicit, never silently assigned by a historical SID.
    unresolved = observation('unresolved', program=None, problem='OpenCode parent requires registry reconciliation')
    record = recovery.update('switch-1', [unresolved])
    assert len(record['pages']) == 2
    assert 'OpenCode parent requires registry reconciliation' in recovery._document(record, 'master')


def test_default_resolver_runs_existing_backend_inventory_off_main_loop(monkeypatch):
    main_thread = threading.get_ident()
    async def current(program):
        assert threading.get_ident() != main_thread
        assert program == '[ap:PROG1]'
        return ('ses_successor', 3)
    monkeypatch.setitem(__import__('sys').modules, 'oc_common', types.SimpleNamespace(current_conductor=current))
    assert asyncio.run(resolve_current_conductor('[ap:PROG1]')) == ('ses_successor', 3)


@pytest.mark.parametrize('reply', [
    '⚠️⚠️⚠️ ARCHIVED NODE\n[name: not editable] Archived\n\nold report',
    {'success': False, 'error': {'code': 'operation_failed', 'message': 'temporary auth/transport error'}},
    {'success': True, 'text': 'not an exact note'},
])
def test_read_failures_and_archives_are_never_mislabelled_as_live_missing_or_confirmation(reply):
    async def scenario():
        async def call(*args):
            return reply
        with pytest.raises(RuntimeError):
            await read_page(types.SimpleNamespace(call=call), '[ap:SAMEID]')
    asyncio.run(scenario())


def test_lifecycle_recovery_and_publication_writers_merge_without_losing_facts(tmp_path, monkeypatch):
    from test_claude_restart_recovery import recovery as fixture, invoke, crash_after_stop
    from test_claude_headless_restart import Harness
    from test_claude_accounts import native
    from claude_headless import accounts
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    native(home)
    native(home, home / '.claude-work', 'work@example.test')
    accounts._write(accounts.registry_path(), {'emails': {'personal': 'personal@example.test', 'work': 'work@example.test'}})
    monkeypatch.setattr(accounts, 'resolve_account', lambda label: {
        **accounts._folder_identity(home / '.claude-work', accounts._read(accounts.registry_path())), 'label': label})
    h = Harness([], state_root=str(tmp_path / 'lifecycle'))
    h.operation_id = 'lifecycle-publication'
    crash_after_stop(h)
    async def scenario():
        store = Store(h.state_root)
        wf = WorkflowyFixture(store, h.operation_id)
        owner = ObserverRecovery(store, workflowy=wf, resolve_conductor=lambda program: 'ses_current')
        original = owner.stage(h.operation_id, [observation()])
        identities = {key: page['ap_id'] for key, page in original['pages'].items()}
        ran = []
        async def while_network_yields(family, verb, params):
            if verb == 'read' and not ran:
                ran.append(True)
                other = Store(h.state_root)
                try:
                    ObserverRecovery(other).transition_update(h.operation_id,
                        error={'phase': 'applying_target', 'type': 'OSError', 'message': 'original failure retained'})
                finally:
                    other.db.close()
                result = invoke(h, recover=True)
                assert result['direction'] == 'restore_old' and result['service']['state'] == 'ready'
        wf.hook = while_network_yields
        wf.capture_failure = 'after'
        first = await owner.publish(h.operation_id)
        assert first['publication']['status'] == 'pending' and first['transition']['service']['state'] == 'ready'
        wf.capture_failure = None
        second = await owner.publish(h.operation_id)
        assert second['publication']['status'] == 'confirmed' and wf.count('point.capture') == len(identities)
        assert {key: page['ap_id'] for key, page in second['pages'].items()} == identities
        assert second['observations'][0]['terminal_evidence'] == original['observations'][0]['terminal_evidence']
        assert any(error['message'] == 'original failure retained' for error in second['transition']['errors'])
        assert second['facts']['direction'] == 'restore_old' and second['facts']['service']['state'] == 'ready'
        assert h.starts == 1
        store.db.close()
    asyncio.run(scenario())
