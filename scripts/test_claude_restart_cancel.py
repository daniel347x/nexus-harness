"""Offline cancellation races: synthetic gates, fake host clients and injected wrapper subprocesses."""
import multiprocessing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from claude_headless import restart
import oc_claude_restart
from test_claude_headless_restart import Harness, rec, HOST_PID


def gate_at(tmp_path):
    control = restart.RestartControl(tmp_path / 'control.json')
    control.initialize()
    return control


def invoke(harness, control):
    return restart.guarded_restart(harness.client, close_idle=True, control=control,
        state_root=harness.state_root, witness_fn=harness.witness, clock=harness.clock,
        sleep=harness.sleep, kill_fn=harness.kill, starter=harness.starter)


def test_cancelled_gate_blocks_host_stop_and_all_idle_eofs(tmp_path):
    gate = gate_at(tmp_path)
    gate.cancel()
    h = Harness([rec(8001, state='ready', owner='host')], state_root=str(tmp_path / 'host'))
    with pytest.raises(restart.RestartRefused, match='cancelled'):
        invoke(h, gate)
    assert 'close_input' not in h.client.methods
    assert h.kills == [] and h.starts == 0


def test_cancel_during_final_health_read_still_prevents_signal(tmp_path):
    gate = gate_at(tmp_path)
    h = Harness([], state_root=str(tmp_path / 'host'))
    original = h.client.handler
    def handler(method, params):
        value = original(method, params)
        if method == 'health' and h.old_health_reads == 2:
            assert gate.cancel()
        return value
    h.client.handler = handler
    # The inspected namespace/witness read is retained; the second read is the fresh final guard.
    with pytest.raises(restart.RestartRefused, match='cancelled'):
        invoke(h, gate)
    assert h.kills == [] and h.starts == 0


def test_stopping_wins_before_first_idle_close_not_after_log_receipt(tmp_path):
    gate = gate_at(tmp_path)
    def close(row, h):
        assert gate.phase() == 'stopping'
        assert not gate.cancel()
        row.update(state='exited', owner='gone')
        h.witnesses.pop(row['pid'])
    h = Harness([rec(8001, state='ready', owner='host')], close_effect=close, state_root=str(tmp_path / 'host'))
    invoke(h, gate)
    assert h.kills == [HOST_PID] and h.starts == 1
    assert not gate.cancel()


def test_busy_work_leaves_gate_waiting_and_cancelable(tmp_path):
    gate = gate_at(tmp_path)
    h = Harness([rec(8001, state='ready', owner='host', native_retry_active=True)], state_root=str(tmp_path / 'host'))
    with pytest.raises(restart.RestartRefused):
        invoke(h, gate)
    assert gate.phase() == 'waiting'
    assert gate.cancel()
    assert h.kills == []


def _race(path, barrier, queue, action):
    control = restart.RestartControl(path)
    barrier.wait(timeout=5)
    try:
        answer = control.cancel() if action == 'cancel' else control.begin_stopping()
        queue.put((action, answer))
    except restart.RestartRefused:
        queue.put((action, 'refused'))


def test_real_interprocess_lock_has_exactly_one_winner(tmp_path):
    ctx = multiprocessing.get_context('fork')
    for i in range(8):
        root = tmp_path / str(i)
        gate = gate_at(root)
        barrier, queue = ctx.Barrier(2), ctx.Queue()
        processes = [ctx.Process(target=_race, args=(gate.path, barrier, queue, action)) for action in ('cancel', 'stop')]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=8)
            assert process.exitcode == 0
        results = dict(queue.get(timeout=2) for _ in range(2))
        assert ((gate.phase() == 'cancelled' and results == {'cancel': True, 'stop': 'refused'})
                or (gate.phase() == 'stopping' and results == {'cancel': False, 'stop': None}))
        queue.close()


def test_wrapper_cancelled_before_attempt_never_invokes_restart_or_popup(tmp_path):
    gate = gate_at(tmp_path)
    gate.cancel()
    with patch.object(oc_claude_restart.subprocess, 'run') as run, patch.object(oc_claude_restart, 'popup') as popup:
        assert oc_claude_restart.worker('synthetic.sh', ['--account', 'work'], 30, 1, control=gate) == restart.CANCELLED_EXIT
    run.assert_not_called()
    popup.assert_not_called()


def test_wrapper_cancel_during_inflight_refusal_stops_retry_cleanly(tmp_path):
    gate = gate_at(tmp_path)
    seen = []
    def run(args, **kwargs):
        seen.append(args)
        assert args[-2:] == ['--_control', str(gate.path)]
        assert gate.cancel()
        return SimpleNamespace(returncode=3, stdout='', stderr='REFUSED synthetic busy')
    with patch.object(oc_claude_restart.subprocess, 'run', run), patch.object(oc_claude_restart.time, 'sleep'), \
            patch.object(oc_claude_restart, 'popup') as popup:
        assert oc_claude_restart.worker('synthetic.sh', ['--account', 'work'], 30, 1, control=gate) == restart.CANCELLED_EXIT
    assert len(seen) == 1
    popup.assert_not_called()
