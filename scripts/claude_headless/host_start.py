"""Small private launch/ownership receipt; never a selector or session cache."""
import os
from pathlib import Path
import time
from contextlib import contextmanager


@contextmanager
def start_admission(root, operation_id=None, *, inherited=False):
    """External starts honor existing transition ownership; admitted driver children don't relock it.

    The driver already holds the exclusive transition lock across its maintained starter.
    A plain start/serve uses the existing shared gate and reports any durable unfinished R,
    not a second physical transition after its coordinator died. No new lock/lease is added.
    """
    if operation_id or inherited:
        yield
        return
    from .accounts import transition_lock
    from .restart_state import read_operations, physical_pending
    with transition_lock(root=root, shared=True):
        pending = [row for row in read_operations(root) if physical_pending(row)]
        if pending:
            raise RuntimeError('unfinished physical transition ' + pending[0]['operation_id'] +
                '; use claude_restart.sh --status/--recover with that ID')
        yield


def write_start_receipt(root, socket_path, *, operation_id=None, start_token=None,
                        phase='initializing', account=None, error=None):
    from .accounts import _write, selector_snapshot
    from .host import witness
    value = {'schema': 'claude-host-start/1', 'operation_id': operation_id, 'start_token': start_token,
             'state_root': str(root), 'socket_path': str(socket_path), 'host_pid': os.getpid(),
             'process_witness': witness(os.getpid()), 'account': (
                 selector_snapshot()['account'] if account is None else account),
             'phase': phase, 'observed_at': time.time(), 'error': error}
    _write(Path(root) / 'host-start-receipt.json', value)
    return value
