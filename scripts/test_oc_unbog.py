"""oc_unbog: the operator's by-hand way to see and clear what blocks the guarded Claude host restart."""
import oc_unbog as U


class FakeClient:
    def __init__(self, rows, close_ok=True, interrupt_error=None):
        self.rows, self.close_ok, self.interrupt_error, self.calls = rows, close_ok, interrupt_error, []

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == 'list':
            return self.rows
        if method == 'health':
            return {'host_pid': 4242}
        if method == 'interrupt':
            if self.interrupt_error:
                raise RuntimeError(self.interrupt_error)
            return {'request_id': 'r-1', 'state': 'replied', 'settlement': {'settled': False}}
        if method == 'close_input':
            if not self.close_ok:
                raise RuntimeError('cannot close input with unsettled work: native retry active')
            return {'status': 'input_close_queued'}
        raise AssertionError(method)


STUCK = {'execution_id': 'cc-1', 'native_uuid': '1', 'role': 'worker', 'generation': 3, 'state': 'ready',
         'owner': 'host', 'native_session_state': 'running', 'primary_turn_open': True,
         'pending_message_uuids': ['m'], 'native_retry_active': True}
MANAGER = {'execution_id': 'cc-2', 'native_uuid': '2', 'role': 'Manager', 'generation': 1, 'state': 'ready',
           'owner': 'host', 'native_session_state': 'idle', 'last_input_at': 1.0}


def test_show_prints_the_host_pid_each_blocker_and_the_managers(monkeypatch, capsys):
    monkeypatch.setattr(U, 'partition_unsettled', lambda rows: ([(STUCK, ['native retry active'])], []))
    blocking = U.show(FakeClient([STUCK, MANAGER]))
    out = capsys.readouterr().out
    assert len(blocking) == 1
    assert 'host pid 4242' in out and 'native retry active' in out
    assert 'Manager conversations' in out and '2  ' in out


def test_interrupt_uses_the_exact_generation_and_an_authority_and_skips_unknown_ids(capsys):
    client = FakeClient([STUCK])
    U.interrupt(client, ['cc-1', 'cc-missing'])
    sent = [p for m, p in client.calls if m == 'interrupt']
    assert len(sent) == 1 and sent[0]['execution_id'] == 'cc-1' and sent[0]['generation'] == 3
    assert sent[0]['authority'] and sent[0]['cancel_queued'] is True
    assert 'cc-missing: not in the host list' in capsys.readouterr().out


def test_interrupt_prints_the_hosts_refusal_and_never_retries(capsys):
    client = FakeClient([STUCK], interrupt_error='stale generation')
    U.interrupt(client, ['cc-1'])
    assert [m for m, _ in client.calls].count('interrupt') == 1
    assert 'host refused' in capsys.readouterr().out


def test_stop_idle_prefers_the_hosts_own_close_and_refuses_a_busy_session(capsys):
    assert U.stop_idle(FakeClient([STUCK]), 'cc-1') == 0          # the host closed it: no signal at all
    assert 'no signal sent' in capsys.readouterr().out
    assert U.stop_idle(FakeClient([STUCK], close_ok=False), 'cc-1') == 3   # not idle: nothing signalled
    assert 'NOT idle' in capsys.readouterr().out
    assert U.stop_idle(FakeClient([]), 'cc-x') == 1


def test_usage_without_a_verb_exits_2(capsys):
    assert U.main([]) == 2
    assert 'oc.sh unbog show' in capsys.readouterr().out
