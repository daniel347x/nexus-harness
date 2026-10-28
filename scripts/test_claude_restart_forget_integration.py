"""Merged Host.serve owners: real endpoint/finalizer/deleter, isolated synthetic state only."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace

import pytest

from claude_headless import accounts
from claude_headless.client import Client
from claude_headless.host import Host, owner_lock
from claude_headless.observer_recovery import ObserverRecovery
from claude_headless.restart_state import stage_episode
from claude_headless.store import Store
from test_claude_forget import ended, seed
from test_claude_headless_host import auth, builder


# @beacon[
#   id=opc@test_restart_forget_serve_integration,
#   slice_labels=opc-claude-restart,
#   kind=ast,
#   comment=Real Host.serve starts both resumed forget and ordinary-endpoint readiness; paused list/files prove no early ready or deletion wait, both task cancellations, durable pending evidence and an empty receipt table, with no native launch or external publication.,
# ]
@pytest.mark.parametrize('finish_ready', [False, True], ids=['cancel-both-pending', 'ready-during-forget'])
def test_serve_preserves_readiness_and_forget_owners_together(tmp_path, monkeypatch, finish_ready):
    """Do not replace either owner: pause only the endpoint probe and executor file effect.

    Interleaving: reconstruct a tombstone + retained start, bind, real deletion batches,
    paused files and list probe, optionally prove ready, then cancel serve while deletion
    is unfinished. A reopened Store must retain both the operation and tombstone marker;
    the receipt table is observed directly to rule out new native input.
    """
    selected = {'label': 'personal', 'config_dir': None, 'identity': 'fixture-personal', 'epoch': 7}
    monkeypatch.setattr(accounts, 'selector_snapshot', lambda: {'present': True, 'account': dict(selected)})
    root, operation, token = tmp_path / 'state', 'restart-forget-integration', 'retained-start-token'
    episode_id = 'observer-switch:' + operation

    async def scenario():
        # Keep AF_UNIX short even when pytest's descriptive test directory is long.
        with tempfile.TemporaryDirectory(prefix='crr-i-') as sockets:
            path = Path(sockets) / 'h.sock'
            store = Store(root)
            try:
                tombstone = ended(SimpleNamespace(root=root, store=store),
                    archived={'at': 1, 'key': 'fixture-history', 'by': 'forget'}, forget_pending=True)
                sid = tombstone['native_uuid']
                seed(store, sid)
                owner = ObserverRecovery(store)
                owner.admit_transition(operation, {
                    'kind': 'restart', 'account': None, 'close_idle': True, 'notify': False,
                    'namespace': {'state_root': str(root), 'socket_path': str(path)},
                    'manager': {'policy': 'none'}}, {
                    'episode_id': episode_id,
                    'old_host': {'host_pid': 4242, 'process_witness': {'pid': 4242, 'start_ticks': 'old', 'boot_id': 'fixture'}}})
                stage_episode(store, operation, episode_id)
                owner.transition_update(operation, fields={
                    'phase': 'starting', 'physical_active': True, 'host_stop_intended': True,
                    'old_host_exit_observed': True, 'start_account': selected, 'start_token': token})
                episode = store.get('quota_checks', '', episode_id)
            finally:
                store.db.close()

            client = Client(path)
            list_waiting, release_ready = asyncio.Event(), asyncio.Event()
            files_waiting, release_files = threading.Event(), threading.Event()
            probes = []

            async def probe(method, **params):
                if method == 'list':
                    list_waiting.set()
                    await release_ready.wait()
                result = await client.acall(method, **params)
                probes.append(method)
                return result

            host = Host(root, path, builder, auth, operation_id=operation,
                        start_token=token, ready_probe=probe)
            remove = host.remove_forget_files

            def paused_files(record):
                files_waiting.set()
                if not release_files.wait(10):  # fixture cleanup budget, never product policy
                    raise RuntimeError('fixture did not release file deletion')
                remove(record)

            monkeypatch.setattr(host, 'remove_forget_files', paused_files)
            assert host.forget_task is None and host.ready_task is None and not path.exists()
            assert host.store.get('sessions', sid) == tombstone  # reconstruction did not touch it
            assert host.start_receipt['phase'] == 'initializing'
            serving = asyncio.create_task(host.serve())
            try:
                await asyncio.wait_for(list_waiting.wait(), 5)
                assert await asyncio.to_thread(files_waiting.wait, 5)
                assert host.forget_task is not None and not host.forget_task.done()
                assert host.ready_task is not None and not host.ready_task.done()
                assert probes == ['health'] and host.start_receipt['phase'] == 'bound'
                assert host.observer_recovery.read(operation)['transition']['service']['state'] == 'unchecked'
                assert host.store.get('sessions', sid)['forget_pending']
                health = await client.acall('health')
                assert health['forget']['status'] == 'running' and health['forget']['done'] == 0
                with pytest.raises(RuntimeError, match='owner lock held'):
                    owner_lock(root / 'host.lock')

                if finish_ready:
                    release_ready.set()
                    await asyncio.wait_for(asyncio.shield(host.ready_task), 5)
                    transition = host.observer_recovery.read(operation)['transition']
                    assert probes == ['health', 'list'] and host.start_receipt['phase'] == 'ready'
                    assert transition['service']['state'] == 'ready'
                    assert transition['service']['evidence']['source'] == 'host_local_endpoint_probe'
                    assert transition['service']['account'] == selected
                    assert host.store.get('sessions', sid)['forget_pending'] and not host.forget_task.done()

                serving.cancel()
                await asyncio.wait_for(asyncio.gather(serving, return_exceptions=True), 2)
                assert host.forget_task.cancelled() and host.forget_status == 'interrupted'
                assert host.ready_task.done()
                assert host.ready_task.cancelled() is (not finish_ready)
                assert host.store.get('sessions', sid)['forget_pending']
                assert host.store.get('quota_checks', '', episode_id) == episode
                transition = host.observer_recovery.read(operation)['transition']
                assert transition['service']['state'] == ('ready' if finish_ready else 'unchecked')
                assert host.sessions == {}  # no native launch
                assert host.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == 0
            finally:
                # Clean up exact tasks even in a deliberately broken source-sensitivity copy.
                serving.cancel()
                for task in (host.ready_task, host.forget_task):
                    if task is not None:
                        task.cancel()
                release_ready.set()
                release_files.set()
                await asyncio.gather(serving, *(task for task in (host.ready_task, host.forget_task)
                                               if task is not None), return_exceptions=True)
                host.store.db.close()
                os.close(host.host_lock)

            reopened = Store(root)
            try:
                assert reopened.get('sessions', sid)['forget_pending']
                assert reopened.get('quota_checks', '', episode_id) == episode
                assert ObserverRecovery(reopened).read(operation)['transition']['service']['state'] == (
                    'ready' if finish_ready else 'unchecked')
                receipt = json.loads((root / 'host-start-receipt.json').read_text())
                assert receipt['phase'] == ('ready' if finish_ready else 'bound')
            finally:
                reopened.db.close()

    asyncio.run(scenario())
