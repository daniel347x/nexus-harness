"""Start ownership/readiness with inert child programs and injected endpoint observations."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import claude_host
from claude_headless import accounts, restart
from claude_headless.host import owner_lock
from claude_headless.restart_state import lock_free, journal
from test_claude_restart_recovery import recovery, invoke, row, crash_after_stop, CoordinatorGone
from test_claude_restart_recovery import pre_stop_record
from test_claude_headless_restart import HOST_PID, NEW_PID, wit


@pytest.mark.parametrize('seam', ['before_spawn', 'before_receipt', 'after_receipt'])
def test_start_handoff_holds_existing_host_lock_across_spawn_and_lost_reply(recovery, monkeypatch, seam):
    """Run the REAL launcher but replace only Popen's argv with a non-Claude FD holder."""
    h = recovery
    root = Path(h.state_root)
    root.mkdir()
    spawned = []
    popen = subprocess.Popen
    release_r, release_w = os.pipe()
    ready_r, ready_w = os.pipe()
    script = ("import os,sys,pathlib; "
              "fd,release,ready=map(int,sys.argv[1:4]); "
              "pathlib.Path(sys.argv[4]).write_text('inert initializing') if sys.argv[5]=='yes' else None; "
              "os.write(ready,b'R'); os.read(release,1)")
    def spawn(argv, **kw):
        fd, = kw['pass_fds']
        assert not lock_free(root / 'host.lock')  # ownership already held BEFORE Popen
        if seam == 'before_spawn':
            raise OSError('injected failure before Popen')
        process = popen([sys.executable, '-c', script, str(fd), str(release_r), str(ready_w),
            str(root / 'inert-receipt'), 'yes' if seam == 'after_receipt' else 'no'],
            pass_fds=(fd, release_r, ready_w), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        spawned.append(process)
        assert os.read(ready_r, 1) == b'R'
        raise OSError('Popen succeeded before launcher acknowledgement')
    monkeypatch.setattr(claude_host.subprocess, 'Popen', spawn)
    monkeypatch.setattr(sys, 'argv', ['claude_host.py', '--state-root', str(root), '--socket-path',
        str(root / 'unused.sock'), '--operation-id', h.operation_id, '--start-token', 'stable-token', 'start'])
    try:
        assert claude_host.main() == 1
        if seam == 'before_spawn':
            assert lock_free(root / 'host.lock') and spawned == []
        else:
            assert not lock_free(root / 'host.lock') and len(spawned) == 1
            with pytest.raises(RuntimeError, match='lock held'):
                owner_lock(root / 'host.lock')
            assert (root / 'inert-receipt').exists() == (seam == 'after_receipt')
    finally:
        os.write(release_w, b'X')
        for process in spawned:
            assert process.wait(timeout=5) == 0
        for fd in (release_r, release_w, ready_r, ready_w):
            os.close(fd)
    assert lock_free(root / 'host.lock')


def test_committed_destination_start_failure_is_recoverable_not_old_fallback(recovery):
    h = recovery
    original = h.starter
    h.starter = lambda: (_ for _ in ()).throw(OSError('inert launch obstacle'))
    with pytest.raises(restart.RestartError):
        invoke(h)
    snapshot = accounts.selector_snapshot()
    assert snapshot['account']['label'] == 'work' and snapshot['account']['epoch'] == 8
    episode_id = row(h)['transition']['recipe']['episode_id']
    h.starter = original
    result = invoke(h, recover=True)
    again = invoke(h, recover=True)
    assert result['service']['state'] == 'ready' and result['service']['account']['label'] == 'work'
    assert again['replayed_completed_operation'] and h.starts == 1 and h.kills == [HOST_PID]
    assert accounts.selector_snapshot() == snapshot and row(h)['transition']['recipe']['episode_id'] == episode_id
    assert row(h)['transition']['errors']  # original start failure is not erased by later recovery


def test_viable_unbound_start_survives_old_timeout_and_queue_budget(recovery):
    h = recovery
    h.implemented = ['prepare_observer_switch']
    dispatch = h.client.handler
    def observer_path(method, params):
        if method == 'observer_switch_inventory':
            return {'blockers': [], 'candidates': []}
        if method == 'prepare_observer_switch':
            with journal(h.state_root) as owner:
                owner.stage(h.operation_id, [], requested_account='work')
            return {'outcome': 'prepared', 'candidates': []}
        if method == 'finish_observer_switch':
            return {}
        return dispatch(method, params)
    h.client.handler = observer_path
    h.new_health_refusals = 4
    phases = []
    def elapsed(seconds):
        h.clock_now += 1800  # beyond BOTH old120s and queue3600, without any real sleep
        phases.append(row(h)['transition']['phase'])
        assert h.starts == 1 and h.kills == [HOST_PID]
        assert accounts.selected_account()['epoch'] == 8
    h.sleep = elapsed
    result = invoke(h)
    assert h.clock_now > 3600 and phases and set(phases) == {'starting'}
    assert result['service']['state'] == 'ready' and h.starts == 1
    with journal(h.state_root) as owner:
        [episode] = owner.store.records('quota_checks')
        assert episode['status'] == 'scheduled'  # retained write uncertainty/outcome or observer notice only


@pytest.mark.parametrize('evidence', ['dead', 'pid_reused', 'missing_receipt_held', 'witness_unreadable', 'wrong_root', 'wrong_pid'])
def test_dead_reused_and_unknown_start_witnesses_do_not_compete(recovery, monkeypatch, evidence):
    h = recovery
    original = h.starter
    if evidence in ('dead', 'pid_reused'):
        def failed_start():
            answer = original()
            h.witnesses[NEW_PID] = None if evidence == 'dead' else wit(NEW_PID, 'reused')
            h.new_health_refusals = 999
            return {**answer, 'process_witness': wit(NEW_PID)}
        h.starter = failed_start
        with pytest.raises(restart.RestartError, match='proved absent'):
            invoke(h)
        assert h.starts == 1 and h.kills == [HOST_PID]
        h.new_host_pid = 6262
        h.recorded[6262] = wit(6262, 'next-start')
        def repaired():
            h.witnesses[6262] = h.recorded[6262]
            h.new_health_refusals = 0
            return original()
        h.starter = repaired
        # A definitively failed candidate can be replaced on explicit recover, after
        # absence/free-lock proof. The reused unrelated PID is never signalled.
        assert invoke(h, recover=True)['service']['state'] == 'ready'
        assert h.starts == 2
    elif evidence == 'missing_receipt_held':
        crash_after_stop(h)
        fd = owner_lock(Path(h.state_root) / 'host.lock')
        h.sleep = lambda seconds: (_ for _ in ()).throw(CoordinatorGone('observer stops, owner remains'))
        try:
            with pytest.raises(CoordinatorGone):
                invoke(h, recover=True)
            assert row(h)['transition']['phase'] == 'starting'
            assert h.starts == 0 and h.kills == [HOST_PID]
        finally:
            os.close(fd)
    elif evidence == 'witness_unreadable':
        crash_after_stop(h)
        old = h.witness
        def unavailable(pid):
            if pid == HOST_PID:
                raise OSError('witness unavailable, not absent')
            return old(pid)
        h.witness = unavailable
        with pytest.raises(restart.RestartError):
            invoke(h, recover=True)
        assert h.starts == 0
    else:
        dispatch = h.client.handler
        def wrong(method, params):
            value = dispatch(method, params)
            if method == 'health' and h.starts:
                value = {**value, **({'state_root': '/wrong-root'} if evidence == 'wrong_root' else
                                     {'host_pid': 9090, 'process_witness': wit(9090)})}
            return value
        h.client.handler = wrong
        with pytest.raises(restart.RestartError, match='unconfirmed/wrong'):
            invoke(h)
        assert h.starts == 1 and h.kills == [HOST_PID]


@pytest.mark.parametrize('problem', ['lost_ready_reply', 'advisory', 'required_service'])
def test_health_reply_loss_and_advisory_errors_preserve_usable_service(recovery, monkeypatch, problem):
    h = recovery
    if problem == 'advisory':
        h.new_supervision_error = 'inert supervisor error'
    if problem == 'required_service':
        dispatch = h.client.handler
        def unusable(method, params):
            if method == 'list' and h.starts:
                raise OSError('core service cannot answer')
            return dispatch(method, params)
        h.client.handler = unusable
        with pytest.raises(restart.RestartError):
            invoke(h)
        assert row(h)['transition']['service']['state'] == 'unconfirmed'
    else:
        if problem == 'lost_ready_reply':
            from claude_headless import restart_state
            original = restart_state.finalize_service
            def lost(*a, **kw):
                original(*a, **kw)
                raise ConnectionError('ready commit succeeded before reply lost')
            monkeypatch.setattr(restart_state, 'finalize_service', lost)
        result = invoke(h)
        assert result['service']['state'] == 'ready'
        assert invoke(h, recover=True)['replayed_completed_operation']
    assert h.starts == 1 and h.kills == [HOST_PID]


def test_bound_host_finalizes_through_ordinary_endpoint_after_coordinator_death(recovery, monkeypatch):
    import asyncio
    from claude_headless import host as host_module, host_start
    h = recovery
    starter = h.starter
    def launched():
        answer = starter()
        raise CoordinatorGone('launcher succeeded; coordinator disappears during reconstruction')
    h.starter = launched
    with pytest.raises(CoordinatorGone):
        invoke(h)
    transition = row(h)['transition']
    token = transition['start_token']
    receipt_phases, probes = [], []
    def retained(root, socket, **kw):
        receipt_phases.append(kw['phase'])
        return {'state_root': str(root), 'socket_path': str(socket), 'host_pid': NEW_PID,
            'process_witness': wit(NEW_PID), 'account': accounts.selector_snapshot()['account'],
            'operation_id': h.operation_id, 'start_token': token, 'phase': kw['phase']}
    monkeypatch.setattr(host_start, 'write_start_receipt', retained)
    monkeypatch.setattr(host_module, 'witness', h.witness)
    async def probe(method, **kw):
        probes.append(method)
        return h.client.call(method, **kw)
    host = host_module.Host(h.state_root, h.client.socket_path, operation_id=h.operation_id,
                            start_token=token, ready_probe=probe)
    try:
        assert receipt_phases == ['initializing'] and host.server is None and probes == []
        asyncio.run(host.finalize_start_ready())
        reopened = row(h)
        assert probes == ['health', 'list']  # not Host.dispatch inference or an early socket
        assert reopened['transition']['service']['state'] == 'ready'
        assert reopened['transition']['service']['evidence']['source'] == 'host_local_endpoint_probe'
        assert receipt_phases[-1] == 'ready' and h.starts == 1
    finally:
        host.store.db.close()
        os.close(host.host_lock)


def test_fresh_manager_failed_registered_launch_reconnects_same_intent_after_reopen(recovery, monkeypatch):
    import asyncio
    import uuid
    from claude_headless.host import Host
    from test_claude_headless_host import auth, builder, until
    from test_claude_headless_supervision import cleanup
    root = Path(recovery.state_root)
    identity = str(uuid.uuid4())
    async def scenario():
        host = Host(root, root / 'unused.sock', builder, auth)
        async def fail(*a, **kw):
            raise OSError('inert Popen failure after registered snapshot')
        with monkeypatch.context() as m:
            m.setattr(asyncio, 'create_subprocess_exec', fail)
            with pytest.raises(OSError):
                await host.launch(native_uuid=identity, cwd=str(root), model='claude-haiku-5-5', effort='low',
                                  system_prompt='fixture', role='Manager')
        record = host.state(identity)
        assert record['state'] == 'launch_failed' and record['owner'] == 'gone' and record['generation'] == 1
        before = {name: (Path(record['run_dir']) / name).read_bytes() for name in ('prompt.md', 'settings.json', 'mcp.json')}
        host.store.db.close()
        os.close(host.host_lock)
        host = Host(root, root / 'unused.sock', builder, auth)
        try:
            assert host.state(identity)['state'] == 'launch_failed'
            result = await host.reconnect(execution_id='cc-' + identity, generation=1)
            await until(lambda: host.state(identity).get('initialized') and not host.state(identity).get('setting_control_pending'))
            assert result['generation'] == 2 and list(host.sessions) == [identity]
            assert not host.store.receipts(identity)
            assert before == {name: (Path(result['run_dir']) / name).read_bytes() for name in before}
        finally:
            await cleanup(host)
    asyncio.run(scenario())


def test_inherited_host_lock_is_adopted_before_store_and_blocks_competing_reconstruction(recovery, monkeypatch):
    from claude_headless import host as host_module
    root = Path(recovery.state_root)
    root.mkdir()
    launcher = owner_lock(root / 'host.lock')
    child = os.dup(launcher)
    stores = []
    store_class = host_module.Store
    def opened(root):
        assert not lock_free(Path(root) / 'host.lock')
        stores.append(root)
        return store_class(root)
    monkeypatch.setattr(host_module, 'Store', opened)
    try:
        # The external launcher was admitted earlier; an inherited child must not retake
        # the physical shared gate and deadlock a recovering coordinator's exclusive pass.
        with accounts.transition_lock(root=root):
            host = host_module.Host(root, root / 'unused.sock', host_lock_fd=child)
            assert host.host_lock == child and len(stores) == 1
            os.close(launcher)
            launcher = None
            assert not lock_free(root / 'host.lock')
            with pytest.raises(RuntimeError, match='transition in progress'):
                host_module.Host(root, root / 'unused.sock')
        with pytest.raises(RuntimeError, match='owner lock held'):
            host_module.Host(root, root / 'unused.sock')
        assert len(stores) == 1  # no competing Store/reconstruction before singleton ownership
    finally:
        if launcher is not None:
            os.close(launcher)
        if 'host' in locals():
            host.store.db.close()
        os.close(child)
    assert lock_free(root / 'host.lock')


def test_external_start_reports_unfinished_operation_instead_of_bypassing_recovery(recovery, monkeypatch, capsys):
    h = recovery
    crash_after_stop(h)
    monkeypatch.setattr(sys, 'argv', ['claude_host.py', '--state-root', h.state_root,
                                     '--socket-path', h.client.socket_path, 'start'])
    monkeypatch.setattr(claude_host.subprocess, 'Popen', lambda *a, **kw: pytest.fail('must reconcile R, not spawn'))
    assert claude_host.main() == 1
    assert h.operation_id in capsys.readouterr().err and h.starts == 0


@pytest.mark.parametrize('command', ['start', 'serve'])
@pytest.mark.parametrize('outcome,old_absent', [
    ('refused', False), ('refused', True), ('cancelled', False), ('cancelled', True), ('interrupted', True)])
def test_finished_or_absent_pre_stop_operation_allows_ordinary_host_admission(recovery, monkeypatch, command, outcome, old_absent):
    from types import SimpleNamespace
    from claude_headless import host as host_module
    h = recovery
    gate = pre_stop_record(h, monkeypatch, outcome)
    original = row(h)
    if old_absent:
        h.witnesses.pop(HOST_PID)
    monkeypatch.setattr(host_module, 'witness', h.witness)
    monkeypatch.setattr(claude_host, 'witness', h.witness)
    spawned, served = [], []
    def spawn(argv, **kw):
        assert not lock_free(Path(h.state_root) / 'host.lock')
        assert kw['pass_fds'] and argv[-1] == 'serve'
        spawned.append(argv)
        return SimpleNamespace(pid=NEW_PID)
    async def serve(host):
        served.append(host)
        assert not lock_free(Path(h.state_root) / 'host.lock')
    monkeypatch.setattr(claude_host.subprocess, 'Popen', spawn)
    monkeypatch.setattr(host_module.Host, 'serve', serve)
    monkeypatch.setattr(sys, 'argv', ['claude_host.py', '--state-root', h.state_root,
                                     '--socket-path', h.client.socket_path, command])
    try:
        assert claude_host.main() == 0
        assert (len(spawned), len(served)) == ((1, 0) if command == 'start' else (0, 1))
        assert row(h) == original  # admission is not a finalizer or a history rewrite
        assert gate.phase() == ('cancelled' if outcome == 'cancelled' else 'stopping')
        assert not h.kills and h.starts == 0
    finally:
        for host in served:
            host.store.db.close()
            os.close(host.host_lock)
