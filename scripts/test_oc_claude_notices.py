"""Exact-episode CLI adapter; no production host connection."""
import oc_claude_notices as cli


class Client:
    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        record = {'episode_id': params.get('episode', '123'), 'status': 'deferred_by_operator',
                  'due_at': None, 'message_uuid': 'retained-exact-uuid'}
        return [record] if method == 'quota_checks' else {'record': record, 'display': record}


def test_exact_control_and_display(capsys):
    client = Client()
    assert cli.main(['defer', '123'], client=client) == 0
    assert client.calls == [('quota_check_control', {'episode': '123', 'action': 'defer',
                            'authority': 'operator CLI: defer episode 123'})]
    assert 'NOT ARMED' in capsys.readouterr().out
    assert cli.main(['cancel', '123', '--authority', 'Dan requested', '--json'], client=client) == 0
    assert client.calls[-1][1]['authority'] == 'Dan requested'


def test_list_never_controls_or_sends(capsys):
    client = Client()
    assert cli.main(['list'], client=client) == 0
    assert client.calls == [('quota_checks', {})]
    assert 'NOT ARMED' in capsys.readouterr().out
