"""Guard sweep, pane side: the pane offers reconnect exactly when the answering host would accept
it, says the host's reason in plain words when not, and a background subagent's permission
prompt never makes an idle Manager look busy. Inert host doubles only."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: F401  (puts scripts/ on sys.path the way production does)
from claude_live import ClaudeFacade, LiveIndex
from claude_reader import ClaudeReader, ReaderError, parse_record

UUID = '0f0f0f0f-1111-4111-8111-222222222222'
EX = 'cc-' + UUID


def dead(**fields):
    record = {'execution_id': EX, 'native_uuid': UUID, 'generation': 1, 'role': 'Manager', 'cwd': '/w',
              'state': 'exited', 'owner': 'gone', 'initialized': True, 'pid': 77,
              'process_witness': {'pid': 77, 'start_ticks': '1', 'boot_id': 'b'}}
    record.update(fields)
    return record


class Host:
    def __init__(self, record):
        self.record, self.calls = record, []

    def call(self, method, **params):
        self.calls.append(method)
        if method in ('state',):
            return dict(self.record)
        if method == 'list':
            return [dict(self.record)]
        if method == 'reconnect':
            return {**self.record, 'generation': 2, 'state': 'initializing', 'owner': 'host'}
        if method == 'replay':
            return {'events': [], 'cursor': params.get('cursor'), 'has_more': False, 'gap': False}
        if method == 'get_settings':
            self.readbacks = getattr(self, 'readbacks', 0) + 1
            return {'state': 'applied', 'applied': True, 'model': 'claude-opus-5-5', 'effort': 'high',
                    'blocks_send': False, 'gate': {'blocked': False}, 'generation': params['generation']}
        raise AssertionError(method)


class ReconnectAgreement(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def facade(self, record):
        host = Host(record)
        return ClaudeFacade(ClaudeReader([{'id': 'f', 'root': self.temp.name}]), host), host

    def test_a_stale_flag_on_a_dead_record_is_offered_only_when_the_host_releases_it(self):
        old_host = dead(protocol_attention=True)                       # no stamp: the running older host
        self.assertFalse(ClaudeFacade._reconnectable(old_host))
        reason = ClaudeFacade._reconnect_blockers(old_host)[0]
        self.assertIn('protocol_attention', reason)
        self.assertIn('New Manager', reason)
        new_host = dead(protocol_attention=True, settings_mismatch=True, reconnect_policy=2,
                        pending_escalations=['i'], pending_message_uuids=['m'])
        self.assertTrue(ClaudeFacade._reconnectable(new_host))
        self.assertEqual(ClaudeFacade._reconnect_blockers(new_host), [])

    def test_inventory_and_reconnect_carry_the_hosts_exact_reason(self):
        facade, host = self.facade(dead(protocol_attention=True))
        row = facade._inventory_record(dead(protocol_attention=True))
        self.assertFalse(row.get('capabilities', facade._capabilities(dead(protocol_attention=True)))['reconnect'])
        self.assertIn('old reconnect rule', row['reconnectBlocked'])
        ref = facade._ref('archive', (EX, 1))
        with self.assertRaises(ReaderError) as refused:
            facade.reconnect(ref, EX, 1)
        self.assertEqual(refused.exception.code, 409)
        self.assertIn('protocol_attention', str(refused.exception))
        self.assertNotIn('reconnect', host.calls)                      # refused before the host

    def test_a_policy_two_host_record_reconnects(self):
        facade, host = self.facade(dead(protocol_attention=True, reconnect_policy=2))
        ref = facade._ref('archive', (EX, 1))
        result = facade.reconnect(ref, EX, 1)
        self.assertEqual((result['previousGeneration'], result['generation']), (1, 2))
        self.assertIn('reconnect', host.calls)

    def test_a_live_unproved_owner_is_never_offered(self):
        self.assertFalse(ClaudeFacade._reconnectable(dead(state='ownership_unknown', owner='unknown', reconnect_policy=2)))


class RecheckSettings(unittest.TestCase):
    def test_an_explicit_recheck_is_one_read_only_native_readback(self):
        with tempfile.TemporaryDirectory() as temp:
            live = dead(state='ready', owner='host', setting_control_pending=True, settings_mismatch=True)
            host = Host(live)
            facade = ClaudeFacade(ClaudeReader([{'id': 'f', 'root': temp}]), host)
            ref = facade._ref('target', (EX, 1))
            result = facade.manager_operation('control', ref, EX, 1, requestId='12345678-1234-4234-8234-123456789abc',
                                              operation='get_settings')
            self.assertEqual(result['settingsReadback']['model'], 'claude-opus-5-5')
            self.assertEqual(host.readbacks, 1)
            self.assertNotIn('begin_control', host.calls)


class SubagentPermissionIsNotTheManagersTurn(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.facade = ClaudeFacade(ClaudeReader([{'id': 'f', 'root': self.temp.name}]), Host(dead()))

    def tearDown(self):
        self.temp.cleanup()

    def observe(self, idx, value, kind='native_frame', detail=None):
        text, root = parse_record((json.dumps(value) + '\n').encode())
        event = {'generation': 1, 'kind': kind, 'time': 1.0, 'detail': detail or {}}
        self.facade._observe_activity(idx, event, root.members(), text)

    def test_child_permission_prompts_leave_an_idle_manager_idle(self):
        idx = LiveIndex(EX)
        self.observe(idx, {'type': 'result', 'subtype': 'success', 'session_id': UUID})
        self.assertEqual(idx.activity[1]['state'], 'idle')
        self.observe(idx, {'type': 'control_request', 'request_id': 'r1', 'session_id': UUID,
                           'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash', 'agent_id': 'a1'}})
        self.assertEqual(idx.activity[1]['state'], 'idle')
        self.assertEqual(idx.permission_requests, {})
        # The Manager's own permission prompt still shows.
        self.observe(idx, {'type': 'control_request', 'request_id': 'r2', 'session_id': UUID,
                           'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash'}})
        self.assertEqual(idx.activity[1]['state'], 'permission')


if __name__ == '__main__':
    unittest.main()
