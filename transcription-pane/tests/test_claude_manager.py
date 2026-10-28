"""U06 real ephemeral HTTP with inert durable Manager intent/receipt transport."""
import copy
import http.client
import json
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

from test_claude_live import InertHost, EX, UUID
from claude_live import ClaudeFacade
from claude_reader import ClaudeReader
import server


class ManagerHost(InertHost):
    def __init__(self):
        super().__init__()
        self.record.update(initialized=True, owner='host', native_retry_active=False,
            background_unsettled=False, background_tasks={}, pending_escalations=[],
            pending_message_uuids=[], controls_effective={'model': 'claude-sonnet-5-5',
                'effort': 'high', 'generation': 1}, setting_control_pending=False)
        self.controls, self.writes = {}, []
        self.lose_reply = False

    def call(self, method, **params):
        if method == 'steer':
            if not getattr(self, 'steers', True):
                raise ValueError('unknown method')
            self.calls.append((method, params))
            if params['generation'] != self.record['generation']:
                raise ValueError('wrong generation')
            receipt = {'execution_id': EX, 'generation': params['generation'], 'message_uuid': params['mid'],
                'submitted': params['submitted'], 'original': params['original'], 'durable': True, 'kind': 'steer',
                'queued': False, 'delivered': False, 'acknowledged': False, 'started': False, 'completed': False,
                'status': 'accepted', 'results': []}
            self.receipts.setdefault(params['mid'], copy.deepcopy(receipt))
            self.writes.append(copy.deepcopy(params))
            return copy.deepcopy(self.receipts[params['mid']])
        if method not in ('send', 'begin_control', 'control_receipt'):
            return super().call(method, **params)
        self.calls.append((method, params))
        if method == 'control_receipt':
            return copy.deepcopy(self.controls.get(params['request_id']))
        if params['generation'] != self.record['generation']:
            raise ValueError('wrong generation')
        if method == 'send':
            mid = params['mid']
            old = self.receipts.get(mid)
            if old:
                if old['submitted'] != params['submitted'] or old['original'] != params['original']:
                    raise ValueError('UUID already bound to different text')
                return copy.deepcopy(old)
            if self.record['pending_message_uuids']:
                raise RuntimeError('busy: idle-only, wait or interrupt')
            receipt = {'execution_id': EX, 'generation': params['generation'], 'message_uuid': mid,
                'submitted': params['submitted'], 'original': params['original'], 'durable': True,
                'written': False, 'acknowledged': False, 'started': False, 'completed': False,
                'status': 'accepted', 'results': []}
            self.receipts[mid] = copy.deepcopy(receipt)
            self.writes.append(copy.deepcopy(params))
            self.record['pending_message_uuids'] = [mid]
            if self.lose_reply:
                raise OSError('inert lost POST response after durable intent')
            return receipt
        rid = params['request_id']
        old = self.controls.get(rid)
        if old:
            if old['caller_request'] != params:
                raise ValueError('control UUID already bound')
            return copy.deepcopy(old)
        if self.record['pending_message_uuids'] and params['operation'] != 'interrupt':
            raise RuntimeError('busy: idle-only settings')
        row = {'request_id': rid, 'generation': params['generation'], 'state': 'pending',
            'status': 'pending', 'applied': False, 'caller_request': copy.deepcopy(params)}
        self.controls[rid] = row
        self.writes.append(copy.deepcopy(params))
        return copy.deepcopy(row)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = ManagerHost()
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]
        _, inv = self.request('/api/claude/sessions')
        row = inv['sessions'][0]
        _, page = self.request('/api/claude/events?' + urlencode({'sessionRef': row['sessionRef'],
            'scopeRef': row['scopes'][0]['scopeRef']}))
        self.identity = {k: page['targetInfo'][k] for k in ('targetRef', 'executionId', 'generation')}

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()
        self.temp.cleanup()

    def request(self, route, method='GET', data=None, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        headers = dict(headers or {})
        if data is not None:
            raw = json.dumps(data, ensure_ascii=False).encode()
            headers.setdefault('Content-Type', 'application/json')
        connection.request(method, route, body=raw, headers=headers)
        response = connection.getresponse()
        body = json.loads(response.read())
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        code = response.status
        connection.close()
        return code, body

    def send_body(self, **fields):
        return {'schemaVersion': 1, 'backend': 'claude', **self.identity,
            'messageUuid': str(uuid.uuid4()), 'original': '  original 雪\r\n',
            'submitted': 'refined\n雪', **fields}

    def query(self, action, **fields):
        return self.request('/api/claude/' + action + '?' + urlencode({**self.identity, **fields}))

    def test_a_busy_manager_send_with_steer_goes_through_the_host_steer(self):
        self.host.record['pending_message_uuids'] = ['busy']
        self.assertEqual(self.request('/api/claude/send', 'POST', self.send_body())[0], 409)   # plain Send: busy
        body = self.send_body(steer=True)
        code, reply = self.request('/api/claude/send', 'POST', body)
        self.assertEqual(code, 200, reply)
        self.assertEqual(self.host.calls[-1][0], 'steer')
        self.assertEqual(reply['receipt']['kind'], 'steer')
        self.assertTrue(reply['receipt']['accepted'])
        self.assertEqual(self.request('/api/claude/send', 'POST', body)[0], 200)            # same UUID: same receipt
        self.assertEqual(len(self.host.writes), 1)
        self.assertEqual(self.request('/api/claude/send', 'POST', self.send_body(steer='yes'))[0], 400)
        self.host.steers = False
        code, reply = self.request('/api/claude/send', 'POST', self.send_body(steer=True))
        self.assertEqual(code, 409)
        self.assertIn('predates Send while the Manager is busy', reply['error'])

    def test_exact_long_unicode_durable_receipt_ack_states_duplicate_and_busy(self):
        original = '  \r\n🌈雪\x00\t' * 40000 + 'TAIL\r\n '
        body = self.send_body(original=original, submitted=original.strip())
        code, reply = self.request('/api/claude/send', 'POST', body)
        self.assertEqual(code, 200, reply)
        self.assertEqual(reply['receipt']['original'], original)
        self.assertEqual(reply['receipt']['submitted'], original.strip())
        self.assertTrue(reply['receipt']['accepted'])
        self.assertFalse(reply['receipt']['acknowledged'])
        self.assertFalse(reply['receipt']['completed'])
        self.assertEqual(self.host.writes[0]['submitted'], original.strip())
        self.assertEqual(self.request('/api/claude/send', 'POST', body)[0], 200)
        self.assertEqual(len(self.host.writes), 1)
        self.assertEqual(self.request('/api/claude/send', 'POST', {**body, 'submitted': 'changed'})[0], 409)
        self.assertEqual(self.request('/api/claude/send', 'POST', self.send_body())[0], 409)
        mid = body['messageUuid']
        self.host.receipts[mid].update(written=True, acknowledged=True, started=True)
        code, reply = self.query('receipt', messageUuid=mid)
        self.assertEqual(code, 200)
        self.assertTrue(reply['receipt']['acknowledged'])
        self.assertFalse(reply['receipt']['completed'])
        self.host.receipts[mid].update(status='uncertain')
        self.assertTrue(self.query('receipt', messageUuid=mid)[1]['receipt']['uncertain'])

    def test_lost_post_receipt_reconciliation_never_second_intent(self):
        self.host.lose_reply = True
        body = self.send_body()
        self.assertEqual(self.request('/api/claude/send', 'POST', body)[0], 502)
        code, reply = self.query('receipt', messageUuid=body['messageUuid'])
        self.assertEqual(code, 200)
        self.assertEqual(reply['receipt']['submitted'], body['submitted'])
        self.assertEqual(len(self.host.writes), 1)
        self.assertIsNone(self.query('receipt', messageUuid=str(uuid.uuid4()))[1]['receipt'])

    def test_worker_unmanaged_wrong_execution_and_generation_are_view_only(self):
        body = self.send_body()
        for role in ('worker', 'conductor', 'helper', 'base', 'manager', 'history'):
            self.host.record['role'] = role
            self.assertEqual(self.request('/api/claude/send', 'POST', body)[0], 409)
            self.assertEqual(self.query('state')[0], 409)
            for operation, fields in (('set_model', {'model': 'sonnet'}),
                    ('apply_effort', {'effort': 'low'}), ('interrupt', {})):
                control = {'schemaVersion': 1, 'backend': 'claude', **self.identity,
                    'requestId': str(uuid.uuid4()), 'operation': operation, **fields}
                self.assertEqual(self.request('/api/claude/control', 'POST', control)[0], 409)
        self.host.record['role'] = 'Manager'
        self.assertEqual(self.request('/api/claude/send', 'POST', {**body, 'targetRef': 'history'})[0], 409)
        self.assertEqual(self.request('/api/claude/send', 'POST', {**body, 'executionId': 'cc-'+str(uuid.uuid4())})[0], 409)
        self.host.record['generation'] = 2
        self.assertEqual(self.request('/api/claude/send', 'POST', body)[0], 409)
        self.assertEqual(self.query('state')[0], 409)
        self.assertEqual(len(self.host.writes), 0)

    def test_old_generation_receipts_recover_but_mutations_refuse(self):
        body = self.send_body()
        self.assertEqual(self.request('/api/claude/send', 'POST', body)[0], 200)
        self.host.record['generation'] = 2
        code, reply = self.query('receipt', messageUuid=body['messageUuid'])
        self.assertEqual(code, 200)
        self.assertEqual(reply['currentGeneration'], 2)
        self.assertEqual(reply['receipt']['generation'], 1)
        self.assertEqual(self.request('/api/claude/send', 'POST', body)[0], 409)
        self.assertEqual(self.query('receipt', messageUuid=body['messageUuid'], generation=2)[0], 409)
        # Pane restart loses refs, not host receipts: saved browser identity still reconciles
        # without reselecting, adopting a transcript, or sending again.
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host)
        self.httpd.claude_reader = self.reader
        self.assertEqual(self.query('receipt', messageUuid=body['messageUuid'])[0], 200)
        self.assertEqual(self.request('/api/claude/send', 'POST', body)[0], 409)
        self.host.record['role'] = 'worker'
        self.assertEqual(self.query('receipt', messageUuid=body['messageUuid'])[0], 409)
        self.host.record['role'] = 'Manager'
        _, inventory = self.request('/api/claude/sessions')
        row = inventory['sessions'][0]
        _, page = self.request('/api/claude/events?' + urlencode({'sessionRef':row['sessionRef'],
            'scopeRef':row['scopes'][0]['scopeRef']}))
        self.identity.update(targetRef=page['targetInfo']['targetRef'])
        self.assertEqual(self.query('receipt', messageUuid=body['messageUuid'])[0], 200)
        self.assertEqual(len(self.host.writes), 1)

    def test_post_accept_observation_failures_are_uncertain_never_prewrite_refusal(self):
        body = self.send_body()
        # Small injected response bound is enough to force the post-command branch; request fits.
        with patch.object(server, 'TOOL_PART_LIMIT', 600):
            code, result = self.request('/api/claude/send', 'POST', body)
        self.assertEqual(code, 502, result)
        self.assertEqual(len(self.host.writes), 1)
        self.assertEqual(self.query('receipt', messageUuid=body['messageUuid'])[0], 200)
        self.host.record['pending_message_uuids'] = []
        old = self.host.call
        def mismatched(method, **params):
            result = old(method, **params)
            if method == 'send':
                result['generation'] = 99
            return result
        self.host.call = mismatched
        code, result = self.request('/api/claude/send', 'POST', self.send_body())
        self.assertEqual(code, 502, result)
        self.assertEqual(len(self.host.writes), 2)

    def test_control_receipt_survives_reconstructed_pane_without_reselect(self):
        rid = str(uuid.uuid4())
        body = {'schemaVersion': 1, 'backend': 'claude', **self.identity,
            'requestId': rid, 'operation': 'apply_effort', 'effort': 'low'}
        self.assertEqual(self.request('/api/claude/control', 'POST', body)[0], 200)
        self.httpd.claude_reader = ClaudeFacade(ClaudeReader([{'id':'fixture', 'root':self.temp.name}]), self.host)
        self.assertEqual(self.query('control-receipt', requestId=rid)[0], 200)
        self.assertEqual(self.request('/api/claude/control', 'POST', body)[0], 409)
        self.assertEqual(len(self.host.writes), 1)

    def test_controls_pending_idempotency_effective_readback_and_no_poll_controls(self):
        rid = str(uuid.uuid4())
        body = {'schemaVersion': 1, 'backend': 'claude', **self.identity,
            'requestId': rid, 'operation': 'set_model', 'model': 'sonnet'}
        code, reply = self.request('/api/claude/control', 'POST', body)
        self.assertEqual(code, 200, reply)
        self.assertEqual(reply['controlReceipt']['state'], 'pending')
        self.assertEqual(self.request('/api/claude/control', 'POST', body)[0], 200)
        self.assertEqual(self.request('/api/claude/control', 'POST', {**body, 'model': 'opus'})[0], 409)
        self.assertEqual(len(self.host.writes), 1)
        self.host.controls[rid].update(state='applied', applied=True, effective={'model': 'claude-sonnet-5-5', 'effort': 'high'})
        for _ in range(4):
            self.assertEqual(self.query('state')[0], 200)
            self.assertTrue(self.query('control-receipt', requestId=rid)[1]['controlReceipt']['applied'])
        self.assertFalse(any(m == 'get_settings' for m, p in self.host.calls))
        self.host.record['pending_message_uuids'] = ['busy']
        self.assertEqual(self.request('/api/claude/control', 'POST', {**body, 'requestId': str(uuid.uuid4())})[0], 409)
        interrupt = {k:v for k,v in body.items() if k != 'model'}
        interrupt.update(operation='interrupt', requestId=str(uuid.uuid4()), reason='deliberate')
        self.assertEqual(self.request('/api/claude/control', 'POST', interrupt)[0], 200)

    def test_phone_interrupt_uses_native_control_authority_and_cancels_only_target_queue(self):
        self.host.record.update(primary_turn_open=True, native_session_state='running',
                                pending_message_uuids=['stuck-turn'])
        rid = str(uuid.uuid4())
        body = {'schemaVersion': 1, 'backend': 'claude', **self.identity,
                'requestId': rid, 'operation': 'interrupt', 'reason': 'Explicit phone pane operator Interrupt'}
        code, reply = self.request('/api/claude/control', 'POST', body)
        self.assertEqual(code, 200, reply)
        self.assertEqual(self.host.writes, [{'execution_id': EX, 'generation': self.identity['generation'],
            'request_id': rid, 'operation': 'interrupt', 'authority': 'explicit pane Manager control',
            'reason': body['reason'], 'cancel_queued': True}])
        self.host.controls[rid].update(state='replied', applied=False)
        self.assertEqual(self.query('control-receipt', requestId=rid)[1]['controlReceipt']['state'], 'replied')
        self.assertFalse(any(method in ('close_input', 'reconnect', 'launch', 'send', 'steer')
                             for method, _ in self.host.calls))
        self.assertEqual(len(self.host.writes), 1)

    def test_strict_origin_schema_method_ids_and_no_paths(self):
        body = self.send_body()
        for headers in ({'Origin': 'http://evil.invalid'}, {'Origin': 'http://localhost:'+str(self.port)},
                        {'Host': 'evil.invalid'}, {'Sec-Fetch-Site': 'cross-site'}, {'Content-Type': 'text/plain'}):
            self.assertEqual(self.request('/api/claude/send', 'POST', body, headers)[0], 403)
        for changes in ({'path': '/tmp/no'}, {'generation': True}, {'messageUuid': 'short'},
                        {'schemaVersion': True}, {'backend': 'opencode'}, {'original': None}, {'submitted': ''}):
            self.assertEqual(self.request('/api/claude/send', 'POST', {**body, **changes})[0], 400)
        raw = b'{"schemaVersion":1,"schemaVersion":1}'
        self.assertEqual(self.request('/api/claude/send', 'POST', raw=raw, headers={'Content-Type':'application/json'})[0], 400)
        self.assertEqual(self.request('/api/claude/send', 'GET')[0], 405)
        self.assertEqual(self.request('/api/claude/state', 'POST', body)[0], 405)
        self.assertEqual(self.query('state', path='/tmp/no')[0], 400)
        self.assertEqual(self.request('/api/claude/send', 'OPTIONS')[0], 403)
        for operation in ([], {}, None, 'launch', 'set_settings'):  # get_settings is the explicit Re-check (guard sweep)
            control = {'schemaVersion':1, 'backend':'claude', **self.identity,
                'requestId':str(uuid.uuid4()), 'operation':operation}
            self.assertEqual(self.request('/api/claude/control', 'POST', control)[0], 400)
        self.assertEqual(len(self.host.writes), 0)
