"""Batch B pane side: reconnect on Send for a collected/ended registered Manager.

The facade (`POST /api/claude/reconnect`) is identity-only, idempotent and never a Send:
a live generation is reused, at most one replacement generation is launched, ownership
uncertainty and every other role/authority refuse. Inert transport only: no production
socket, process, path or inference.
"""
import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
import uuid as uuidlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
from claude_reader import ClaudeReader, ReaderError
from claude_live import ClaudeFacade
from test_claude_lockout_batch_a import FleetHost, host_record, execution

SLEEP = 'aaaaaaaa-1111-4111-8111-111111111111'
LIVE = 'bbbbbbbb-2222-4222-8222-222222222222'
WORKER = 'cccccccc-3333-4333-8333-333333333333'
UNKNOWN = 'dddddddd-4444-4444-8444-444444444444'
CWD = '/fixture/work'


def collected_record(uuid_value, label='Manager-Session-181'):
    return host_record(uuid_value, state='collected', owner='gone', initialized=True,
                       started_at=900.0, label=label, close_reason='collected')


class ReconnectHost(FleetHost):
    """Inert host with the reconnect contract: a live owner is reused verbatim;
    otherwise exactly one replacement generation is launched, under a lock."""

    def __init__(self, records):
        super().__init__(records)
        self.launches = 0
        self.reconnect_calls = 0
        self.lose_next_reply = False
        self.gate = threading.Lock()

    def call(self, method, **params):
        if method != 'reconnect':
            return super().call(method, **params)
        with self.gate:
            self.calls.append((method, dict(params)))
            self.reconnect_calls += 1
            record = copy.deepcopy(self.records[params['execution_id']])
            if not isinstance(params.get('generation'), int) or params['generation'] > record['generation']:
                raise ValueError('invalid expected conversation generation')
            if record.get('state') == 'ownership_unknown' or record.get('owner') in ('unknown', 'draining'):
                raise RuntimeError('old owner is not proved gone; no second writer')
            if record.get('owner') not in ('host', 'owned'):
                # A stale expectation whose transport is already ended refuses; otherwise launch one.
                if params['generation'] != record['generation']:
                    raise RuntimeError('conversation changed and is no longer live; select its current generation')
                self.launches += 1
                record.update(generation=record['generation'] + 1, state='initializing', owner='host',
                              initialized=False, exit_code=None, close_reason=None)
                self.records[params['execution_id']] = record
            if self.lose_next_reply:
                self.lose_next_reply = False
                raise OSError('inert lost reconnect reply after the durable launch')
            return copy.deepcopy(record)


class ReconnectFacadeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = ReconnectHost([
            collected_record(SLEEP),
            host_record(LIVE, started_at=2000.0),
        ])
        self.facade = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host,
                                   clock=lambda: 5000.0)  # the fixture's own epoch, matching its 1000/2000 launch stamps
        self.sleep_row = next(r for r in self.facade.sessions()['sessions'] if r['executionId'] == execution(SLEEP))
        self.sleep_target = self._target(self.sleep_row)

    def tearDown(self):
        self.temp.cleanup()

    def _target(self, row):
        page = self.facade.events(sessionRef=row['sessionRef'],
                                  scopeRef=next(s for s in row['scopes'] if s['label'] == 'main')['scopeRef'])
        return page['targetRef']

    def test_collected_conversation_reads_sleeping_and_keeps_refine_authority(self):
        row = self.sleep_row
        self.assertEqual((row['lifecycle'], row['capabilities']['reconnect'],
                          row['capabilities']['nativeInput'], row['capabilities']['controls']),
                         ('sleeping', True, False, False))
        self.assertEqual(row['title'], 'Manager-Session-181')
        page = self.facade.events(targetRef=self.sleep_target)
        info = page['targetInfo']
        self.assertEqual((info['lifecycle'], info['archived'], info['authority']),
                         ('sleeping', True, 'archived'))
        self.assertEqual(self.facade.validate_target(self.sleep_target)['lifecycle'], 'sleeping')
        # Archived Refine context still works; a raw Send to the old generation refuses.
        with self.assertRaises(ReaderError) as caught:
            self.facade.manager_operation('send', self.sleep_target, execution(SLEEP), 1,
                                          messageUuid=str(uuidlib.uuid4()), original='wake', submitted='wake')
        self.assertEqual((caught.exception.code, caught.exception.status), (409, 'exit'))
        self.assertIn('This Manager is sleeping (reconnects on Send).', str(caught.exception))
        self.assertEqual(self.host.mutations(), [])

    def test_reconnect_launches_one_generation_then_the_new_ref_is_the_send_authority(self):
        result = self.facade.reconnect(self.sleep_target, execution(SLEEP), 1)
        self.assertEqual((result['previousGeneration'], result['generation']), (1, 2))
        self.assertEqual(result['executionId'], execution(SLEEP))
        self.assertEqual(result['capabilities']['nativeInput'], False)      # still initializing
        self.assertEqual(result['lifecycle'], 'pending')
        self.assertEqual(self.host.launches, 1)
        self.assertEqual(self.host.mutations(), [])                          # reconnect is never a Send
        # Readiness arrives: the same ref revalidates and one new-generation Send is accepted.
        self.host.update(SLEEP, state='ready', initialized=True, owner='host')
        valid = self.facade.validate_target(result['targetRef'])
        self.assertEqual((valid['generation'], valid['lifecycle']), (2, 'live'))
        receipt = self.facade.manager_operation('send', result['targetRef'], execution(SLEEP), 2,
                                               messageUuid=str(uuidlib.uuid4()), original='wake up', submitted='wake up')
        self.assertEqual(receipt['status'], 'accepted')
        self.assertEqual(receipt['receipt']['generation'], 2)
        self.assertEqual([m for m, _ in self.host.calls if m == 'reconnect'], ['reconnect'])
        self.assertEqual(len(self.host.writes), 1)

    def test_ten_concurrent_reconnects_launch_exactly_once(self):
        results, errors = [], []

        def work():
            try:
                results.append(self.facade.reconnect(self.sleep_target, execution(SLEEP), 1))
            except Exception as error:  # pragma: no cover - a failure here fails the assertions
                errors.append(error)

        threads = [threading.Thread(target=work) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 10)
        self.assertEqual({r['generation'] for r in results}, {2})
        self.assertEqual(self.host.launches, 1)
        self.assertEqual(self.host.reconnect_calls, 10)   # every call reaches the host; the host reuses
        # Every concurrent request kept the pane's ORIGINAL expected generation (never a
        # freshly read current one), which is what makes the host's reuse decision exact.
        self.assertEqual({p['generation'] for m, p in self.host.calls if m == 'reconnect'}, {1})
        self.assertEqual(self.host.mutations(), [])

    def test_pane_expected_generation_is_passed_through_for_a_live_newer_owner(self):
        # The pane still believes generation 1 (its ref was minted then); generation 2 is live.
        self.host.update(SLEEP, generation=2, state='ready', owner='host', initialized=True)
        result = self.facade.reconnect(self.sleep_target, execution(SLEEP), 1)
        self.assertEqual((result['previousGeneration'], result['generation']), (1, 2))
        self.assertEqual(self.host.launches, 0)           # the live newer owner was reused
        self.assertEqual([p['generation'] for m, p in self.host.calls if m == 'reconnect'], [1])

    def test_stale_expected_generation_refuses_when_the_newer_transport_is_ended(self):
        # Generation 2 already ran and ended: a stale generation-1 reconnect must not resume
        # the conversation behind the pane's back; the host's precise refusal is surfaced.
        self.host.update(SLEEP, generation=2, state='exited', owner='gone')
        with self.assertRaises(ReaderError) as caught:
            self.facade.reconnect(self.sleep_target, execution(SLEEP), 1)
        self.assertEqual((caught.exception.code, caught.exception.status), (409, 'refused'))
        self.assertIn('select its current generation', str(caught.exception))
        self.assertEqual(self.host.launches, 0)
        self.assertEqual(self.host.mutations(), [])

    def test_live_generation_is_reused_without_a_second_launch(self):
        self.host.update(SLEEP, state='ready', initialized=True, owner='host')
        result = self.facade.reconnect(self.sleep_target, execution(SLEEP), 1)
        self.assertEqual((result['previousGeneration'], result['generation']), (1, 1))
        self.assertEqual(self.host.launches, 0)

    def test_lost_reconnect_reply_is_idempotent_and_never_a_resend(self):
        self.host.lose_next_reply = True
        with self.assertRaises(ReaderError) as caught:
            self.facade.reconnect(self.sleep_target, execution(SLEEP), 1)
        self.assertEqual((caught.exception.code, caught.exception.status), (502, 'reply-lost'))
        self.assertIn('idempotent', str(caught.exception))
        self.assertEqual(self.host.launches, 1)
        retry = self.facade.reconnect(self.sleep_target, execution(SLEEP), 1)
        self.assertEqual(retry['generation'], 2)
        self.assertEqual(self.host.launches, 1)      # the retry reused, never relaunched
        self.assertEqual(self.host.mutations(), [])

    def test_native_origin_work_is_projected_for_the_pane_send_gate(self):
        # running/requires_action, queued turns and pending native requests are observations
        # the pane's busy label reads; the host remains the idle-only authority.
        self.host.update(SLEEP, state='ready', owner='host', initialized=True,
                         native_session_state='running', native_queued_turns=2,
                         pending_native_requests={'req-1': {'subtype': 'can_use_tool'}})
        state = self.facade.manager_operation('state', *self._identity_for(SLEEP))
        live = state['liveState']
        self.assertEqual(live['native_session_state'], 'running')
        self.assertEqual(live['native_queued_turns'], 2)
        self.assertEqual(live['pending_native_requests'], {'req-1': {'subtype': 'can_use_tool'}})
        row = next(r for r in self.facade.sessions()['sessions'] if r['executionId'] == execution(SLEEP))
        self.assertEqual((row['liveState']['native_session_state'], row['liveState']['native_queued_turns'],
                          row['liveState']['pending_native_requests']['req-1']['subtype']),
                         ('running', 2, 'can_use_tool'))

    def _identity_for(self, uuid_value):
        rows = {r['executionId']: r for r in self.facade.sessions()['sessions']}
        row = rows[execution(uuid_value)]
        page = self.facade.events(sessionRef=row['sessionRef'],
                                  scopeRef=next(s for s in row['scopes'] if s['label'] == 'main')['scopeRef'])
        return page['targetRef'], execution(uuid_value), row['generation']

    def test_unproved_ownership_other_roles_and_exact_identity_refuse(self):
        worker = host_record(WORKER, role='worker', state='collected', owner='gone', started_at=2100.0)
        self.host.records[execution(WORKER)] = worker
        unknown = collected_record(UNKNOWN)
        unknown.update(state='ownership_unknown', owner='unknown')
        self.host.records[execution(UNKNOWN)] = unknown
        rows = {r['executionId']: r for r in self.facade.sessions()['sessions']}
        self.assertEqual(rows[execution(WORKER)]['capabilities']['reconnect'], False)
        self.assertEqual((rows[execution(UNKNOWN)]['lifecycle'], rows[execution(UNKNOWN)]['capabilities']['reconnect']),
                         ('pending', False))
        after = self.facade.reconnect(self.sleep_target, execution(SLEEP), 1)
        refusals = [
            ('view-only', lambda: self.facade.reconnect(rows[execution(WORKER)]['targetRef'], execution(WORKER), 1)),
            ('ownership', lambda: self.facade.reconnect(rows[execution(UNKNOWN)]['targetRef'], execution(UNKNOWN), 1)),
            ('view-only', lambda: self.facade.reconnect('!not-a-ref!', execution(SLEEP), 1)),
            ('stale', lambda: self.facade.reconnect('X' * 32, execution(SLEEP), 1)),
            ('target-mismatch', lambda: self.facade.reconnect(self.sleep_target, execution(SLEEP), 5)),
            ('target-mismatch', lambda: self.facade.reconnect(self.sleep_target, execution(LIVE), 1)),
        ]
        for status, call in refusals:
            with self.assertRaises(ReaderError) as caught:
                call()
            self.assertEqual(caught.exception.status, status)
        # The exact-identity refusals never reached the host's launcher.
        self.assertEqual([m for m, _ in self.host.calls if m == 'reconnect'], ['reconnect'])
        self.assertEqual(self.host.launches, 1)
        self.assertEqual(after['generation'], 2)


class ReconnectWireTests(unittest.TestCase):
    """The exact HTTP contract the pane reads: identity-only POST, JSON-only, no launch args."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = ReconnectHost([collected_record(SLEEP), host_record(LIVE, started_at=2000.0)])
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host,
                                  clock=lambda: 5000.0)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]
        _, inventory = self.request('/api/claude/sessions')
        self.row = next(r for r in inventory['sessions'] if r['executionId'] == execution(SLEEP))
        _, page = self.request('/api/claude/events?' + urlencode(
            {'sessionRef': self.row['sessionRef'],
             'scopeRef': next(s for s in self.row['scopes'] if s['label'] == 'main')['scopeRef']}))
        self.target = page['targetRef']

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()
        self.temp.cleanup()

    def request(self, route, method='GET', data=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        headers = {}
        if data is not None:
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        connection.request(method, route, body=data, headers=headers)
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def identity(self, **fields):
        return {'schemaVersion': 1, 'backend': 'claude', 'targetRef': self.target,
                'executionId': execution(SLEEP), 'generation': 1, **fields}

    def test_reconnect_wire_is_identity_only_and_then_one_new_send(self):
        self.assertEqual(self.row['lifecycle'], 'sleeping')
        self.assertTrue(self.row['capabilities']['reconnect'])
        status, body = self.request('/api/claude/reconnect', 'POST', self.identity())
        self.assertEqual(status, 200, body)
        self.assertEqual((body['previousGeneration'], body['generation']), (1, 2))
        self.assertNotIn('receipt', body)
        self.assertNotIn('original', body)
        self.assertEqual(self.host.mutations(), [])                       # reconnect alone never sends
        self.assertEqual(self.host.launches, 1)
        # Readiness + the one new journaled Send ride the returned generation/ref.
        self.host.update(SLEEP, state='ready', initialized=True, owner='host')
        status, observed = self.request('/api/claude/state?' + urlencode(
            {'targetRef': body['targetRef'], 'executionId': execution(SLEEP), 'generation': '2'}))
        self.assertEqual(status, 200, observed)
        self.assertTrue(observed['capabilities']['nativeInput'])
        send = {'schemaVersion': 1, 'backend': 'claude', 'targetRef': body['targetRef'],
                'executionId': execution(SLEEP), 'generation': 2,
                'messageUuid': str(uuidlib.uuid4()), 'original': '  wake up 雪\r\n', 'submitted': 'wake up 雪'}
        status, receipt = self.request('/api/claude/send', 'POST', send)
        self.assertEqual(status, 200, receipt)
        self.assertEqual(receipt['receipt']['generation'], 2)
        self.assertEqual(len(self.host.writes), 1)
        self.assertEqual([m for m, _ in self.host.calls if m == 'reconnect'], ['reconnect'])

    def test_ten_concurrent_http_reconnects_return_one_generation(self):
        results, errors = [], []

        def work():
            try:
                results.append(self.request('/api/claude/reconnect', 'POST', self.identity()))
            except Exception as error:  # pragma: no cover
                errors.append(error)

        threads = [threading.Thread(target=work) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(errors, [])
        self.assertEqual([status for status, _ in results], [200] * 10)
        self.assertEqual({body['generation'] for _, body in results}, {2})
        self.assertEqual(self.host.launches, 1)
        self.assertEqual(self.host.mutations(), [])

    def test_reconnect_wire_refuses_get_launch_arguments_and_stale_identity(self):
        status, body = self.request('/api/claude/reconnect')
        self.assertEqual(status, 405, body)
        for extra in ({'messageUuid': str(uuidlib.uuid4()), 'original': 'x', 'submitted': 'x'},
                      {'cwd': '/other', 'model': 'claude-opus-5-5', 'uuid': str(uuidlib.uuid4())}):
            status, body = self.request('/api/claude/reconnect', 'POST', self.identity(**extra))
            self.assertEqual(status, 400, body)
            self.assertEqual(body['status'], 'schema')
        status, body = self.request('/api/claude/reconnect', 'POST', self.identity(generation=4))
        self.assertEqual(status, 409, body)
        self.assertEqual(body['status'], 'target-mismatch')
        status, body = self.request('/api/claude/reconnect', 'POST',
                                    {**self.identity(), 'executionId': execution(LIVE)})
        self.assertEqual(status, 409, body)
        self.assertEqual(self.host.mutations(), [])
        self.assertEqual(self.host.launches, 0)          # nothing refusable ever reached the launcher
        self.assertEqual(self.host.reconnect_calls, 0)


if __name__ == '__main__':
    unittest.main()
