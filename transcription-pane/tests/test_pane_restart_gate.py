"""The restart gate and the incidents panel, offline: PaneActivity, GET /api/pane-quiet,
POST /api/pane-activity, POST /api/claude/resolve-incident, the state read's openIncidents and
settings-unverified observations, and tools/pane_quiet.py.

Real `server.Handler` on an ephemeral loopback port with an inert host; no production socket,
process, network or restart.
"""
import contextlib
import copy
import http.client
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
import claude_live
import pane_quiet
from claude_live import ClaudeFacade
from claude_reader import ClaudeReader
from test_claude_live import EX
from test_claude_manager import ManagerHost


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def lease(id_='lease-0001', kind='send', active=True, **fields):
    return {'schemaVersion': 1, 'id': id_, 'kind': kind, 'active': active, **fields}


class PaneActivityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.activity = server.PaneActivity(clock=self.clock)

    def test_quiet_snapshot(self):
        self.assertEqual(self.activity.snapshot(), {'quiet': True, 'busy': []})

    def test_begin_end_count_requests_per_path(self):
        self.activity.begin('/api/claude/send')
        self.activity.begin('/api/claude/send')
        self.activity.begin('/api/claude/control')
        self.assertEqual(self.activity.snapshot(), {'quiet': False, 'busy': [
            '/api/claude/control request in flight', '/api/claude/send request in flight ×2']})
        self.activity.end('/api/claude/send')
        self.activity.end('/api/claude/control')
        self.assertEqual(self.activity.snapshot()['busy'], ['/api/claude/send request in flight'])
        self.activity.end('/api/claude/send')
        self.assertEqual(self.activity.snapshot(), {'quiet': True, 'busy': []})
        self.activity.end('/api/claude/send')            # an unmatched end never goes negative
        self.activity.begin('/api/claude/send')
        self.assertEqual(self.activity.snapshot()['busy'], ['/api/claude/send request in flight'])

    def test_lease_active_true_then_false(self):
        answer = self.activity.lease(lease(kind='refine'))
        self.assertEqual(answer, {'quiet': False, 'busy': ['refine in progress in the pane']})
        self.activity.lease(lease('lease-0002', kind='send'))
        self.assertEqual(self.activity.snapshot()['busy'],
                         ['refine in progress in the pane', 'send in progress in the pane'])
        self.activity.lease(lease(kind='refine', active=False))
        self.activity.lease(lease('lease-0002', active=False))
        self.assertEqual(self.activity.snapshot(), {'quiet': True, 'busy': []})
        self.activity.lease(lease('never-leased', active=False))   # releasing an unknown id is harmless
        self.assertTrue(self.activity.snapshot()['quiet'])

    def test_requests_list_before_leases(self):
        self.activity.lease(lease(kind='control'))
        self.activity.begin('/api/send')
        self.assertEqual(self.activity.snapshot()['busy'],
                         ['/api/send request in flight', 'control in progress in the pane'])

    def test_every_kind_is_accepted(self):
        for index, kind in enumerate(('send', 'refine', 'control', 'launch', 'resolve')):
            self.activity.lease(lease(f'lease-kind-{index}', kind=kind))
        self.assertEqual(len(self.activity.snapshot()['busy']), 5)

    def test_schema_refusals(self):
        bad = [
            None, [], 'lease',
            {k: v for k, v in lease().items() if k != 'active'},          # missing field
            lease(extra=1),                                              # unknown field
            lease(schemaVersion=2), lease(schemaVersion=True), lease(schemaVersion='1'),
            lease(id_='short'), lease(id_='x' * 65), lease(id_='has space ok'), lease(id_='semi;colon1'),
            lease(id_=12345678),
            lease(kind='restart'), lease(kind='Send'), lease(kind=None),
            lease(active=1), lease(active='true'), lease(active=None),
        ]
        for body in bad:
            with self.subTest(body=body):
                with self.assertRaises(server.ToolError) as caught:
                    self.activity.lease(body)
                self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.activity.snapshot(), {'quiet': True, 'busy': []})
        # The id bounds themselves are accepted.
        self.activity.lease(lease(id_='a' * 8))
        self.activity.lease(lease(id_='A-_9' * 16))
        self.assertEqual(len(self.activity.snapshot()['busy']), 2)

    def test_lease_expires_after_lease_seconds(self):
        self.activity.lease(lease())
        self.clock.now += server.PaneActivity.LEASE_SECONDS - 1
        self.assertFalse(self.activity.snapshot()['quiet'])
        self.clock.now += 1
        self.assertEqual(self.activity.snapshot(), {'quiet': True, 'busy': []})

    def test_renewal_extends_the_lease(self):
        self.activity.lease(lease())
        self.clock.now += 100
        self.activity.lease(lease())                     # the page renews while the work runs
        self.clock.now += server.PaneActivity.LEASE_SECONDS - 1
        self.assertFalse(self.activity.snapshot()['quiet'])
        self.clock.now += 1
        self.assertTrue(self.activity.snapshot()['quiet'])

    def test_leases_are_bounded(self):
        for index in range(70):
            self.activity.lease(lease(f'lease-{index:04d}'))
        self.assertEqual(len(self.activity.leases), 64)


class HttpFixture(unittest.TestCase):
    """Real Handler on an ephemeral loopback port, inert Manager host, fresh activity per test."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = self.make_host()
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host)
        self.activity = patch.object(server, '_activity', server.PaneActivity())
        self.activity.start()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]
        _, inventory = self.request('/api/claude/sessions')
        row = inventory['sessions'][0]
        _, page = self.request('/api/claude/events?' + urlencode({'sessionRef': row['sessionRef'],
                                                                  'scopeRef': row['scopes'][0]['scopeRef']}))
        self.identity = {k: page['targetInfo'][k] for k in ('targetRef', 'executionId', 'generation')}

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()
        self.activity.stop()
        self.temp.cleanup()

    def make_host(self):
        return ManagerHost()

    def request(self, route, method='GET', data=None, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        headers = dict(headers or {})
        if data is not None:
            raw = json.dumps(data, ensure_ascii=False).encode()
            headers.setdefault('Content-Type', 'application/json')
        connection.request(method, route, body=raw, headers=headers)
        response = connection.getresponse()
        body = json.loads(response.read())
        code = response.status
        connection.close()
        return code, body

    def quiet(self):
        code, body = self.request('/api/pane-quiet')
        self.assertEqual(code, 200, body)
        return body


class PaneQuietWireTests(HttpFixture):
    def test_quiet_when_nothing_runs(self):
        self.assertEqual(self.quiet(), {'quiet': True, 'busy': []})

    def test_page_lease_makes_it_busy_and_release_quiet_again(self):
        code, body = self.request('/api/pane-activity', 'POST', lease(kind='refine'))
        self.assertEqual(code, 200, body)
        self.assertEqual(body, {'quiet': False, 'busy': ['refine in progress in the pane']})
        self.assertEqual(self.quiet(), {'quiet': False, 'busy': ['refine in progress in the pane']})
        code, body = self.request('/api/pane-activity', 'POST', lease(kind='refine', active=False))
        self.assertEqual(code, 200, body)
        self.assertEqual(self.quiet(), {'quiet': True, 'busy': []})

    def test_lease_schema_refusal_is_400(self):
        for body in (lease(kind='restart'), lease(active='yes'), lease(extra=True), lease(id_='bad')):
            code, reply = self.request('/api/pane-activity', 'POST', body)
            self.assertEqual(code, 400, reply)
        code, _ = self.request('/api/pane-activity', 'POST', raw=b'not json',
                               headers={'Content-Type': 'application/json'})
        self.assertEqual(code, 400)
        self.assertTrue(self.quiet()['quiet'])

    def test_cross_origin_and_non_json_are_refused(self):
        for headers in ({'Origin': 'http://evil.invalid'}, {'Origin': 'http://localhost:9911'},
                        {'Sec-Fetch-Site': 'cross-site'}, {'Host': 'evil.invalid'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/pane-quiet', headers=headers)[0], 403)
                self.assertEqual(self.request('/api/pane-activity', 'POST', lease(), headers=headers)[0], 403)
        for content_type in ('application/x-www-form-urlencoded', 'multipart/form-data', 'application/jsonx'):
            self.assertEqual(self.request('/api/pane-activity', 'POST', lease(),
                                          headers={'Content-Type': content_type})[0], 403)
        # The page's sendBeacon posts text/plain: accepted same-origin, refused cross-origin.
        self.assertEqual(self.request('/api/pane-activity', 'POST', lease(),
                                      headers={'Content-Type': 'text/plain;charset=UTF-8',
                                               'Origin': 'http://evil.invalid'})[0], 403)
        self.assertTrue(self.quiet()['quiet'])
        same = {'Origin': 'http://127.0.0.1:' + str(self.port)}
        self.assertEqual(self.request('/api/pane-quiet', headers=same)[0], 200)
        code, body = self.request('/api/pane-activity', 'POST', lease(kind='resolve'),
                                  headers={**same, 'Content-Type': 'text/plain;charset=UTF-8'})
        self.assertEqual((code, body['busy']), (200, ['resolve in progress in the pane']))
        self.assertEqual(self.request('/api/pane-activity', 'OPTIONS')[0], 403)

    def test_wrong_method_and_query(self):
        self.assertEqual(self.request('/api/pane-quiet', 'POST', lease())[0], 405)
        self.assertEqual(self.request('/api/pane-activity')[0], 405)
        self.assertEqual(self.request('/api/pane-quiet?x=1')[0], 400)

    def test_send_in_flight_shows_in_busy(self):
        entered, release = threading.Event(), threading.Event()

        def blocking(action, targetRef, executionId, generation, **fields):
            entered.set()
            release.wait(10)
            return {'schemaVersion': 1, 'backend': 'claude', 'status': 'accepted'}

        self.reader.manager_operation = blocking
        body = {'schemaVersion': 1, 'backend': 'claude', **self.identity,
                'messageUuid': str(uuid.uuid4()), 'original': 'x', 'submitted': 'x'}
        result = {}
        worker = threading.Thread(target=lambda: result.update(reply=self.request('/api/claude/send', 'POST', body)))
        worker.start()
        try:
            self.assertTrue(entered.wait(10))
            self.assertEqual(self.quiet(), {'quiet': False, 'busy': ['/api/claude/send request in flight']})
        finally:
            release.set()
            worker.join(15)
        self.assertEqual(result['reply'][0], 200, result['reply'])
        self.assertEqual(self.quiet(), {'quiet': True, 'busy': []})

    def test_refused_mutation_still_ends_its_count(self):
        # A mutation the route refuses (wrong schema) counted and then uncounted itself.
        self.assertEqual(self.request('/api/claude/send', 'POST', {'schemaVersion': 1})[0], 400)
        self.assertEqual(self.quiet(), {'quiet': True, 'busy': []})


class IncidentHost(ManagerHost):
    """ManagerHost plus the host's incident read and resolve verb (inert)."""

    def __init__(self):
        super().__init__()
        self.record['pending_escalations'] = ['inc-a', 'inc-b', 'inc-c']
        self.incidents = {
            'inc-a': {'category': 'capacity_error', 'time': 1760000000.5,
                      'detail': {'disclosure': 'quota reached; recovery scheduled', 'code': 529}},
            'inc-b': {'category': 'protocol_attention', 'time': 1760000100,
                      'detail': {'other': 'value'}},
        }
        self.resolutions = []

    def call(self, method, **params):
        if method == 'incident':
            self.calls.append((method, params))
            if params['incident_id'] == 'inc-c':
                raise RuntimeError('incident read unavailable')
            return copy.deepcopy(self.incidents.get(params['incident_id']))
        if method == 'resolve_incident':
            self.calls.append((method, params))
            self.resolutions.append(dict(params))
            self.record['pending_escalations'] = [i for i in self.record['pending_escalations']
                                                  if i != params['incident_id']]
            return {'incident_id': params['incident_id'], 'disposition': params['disposition'], 'resolved': True}
        return super().call(method, **params)


class ResolveIncidentTests(HttpFixture):
    def make_host(self):
        return IncidentHost()

    def resolve(self, **fields):
        body = {'schemaVersion': 1, 'backend': 'claude', **self.identity,
                'incidentId': 'inc-a', 'disposition': 'Checked by Dan in the pane', **fields}
        return self.request('/api/claude/resolve-incident', 'POST', body)

    def state(self):
        code, body = self.request('/api/claude/state?' + urlencode(self.identity))
        self.assertEqual(code, 200, body)
        return body

    def test_open_incident_reaches_the_command_client_and_answers_resolved(self):
        code, body = self.resolve(disposition='  looked at it, fine  ')
        self.assertEqual(code, 200, body)
        self.assertEqual(body['status'], 'resolved')
        self.assertEqual(body['incidentId'], 'inc-a')
        self.assertEqual(body['disposition'], 'looked at it, fine')
        self.assertEqual(self.host.resolutions, [{'execution_id': EX, 'incident_id': 'inc-a',
                                                  'disposition': 'looked at it, fine'}])
        self.assertEqual(self.host.writes, [])          # never a Send or a native control
        # Resolved, it is no longer open: a second resolve refuses.
        code, body = self.resolve()
        self.assertEqual(code, 409, body)
        self.assertEqual(len(self.host.resolutions), 1)
        self.assertTrue(self.quiet()['quiet'])

    def test_incident_not_open_answers_409(self):
        code, body = self.resolve(incidentId='inc-zzz')
        self.assertEqual(code, 409, body)
        self.assertIn('no longer open', body['error'])
        self.assertEqual(self.host.resolutions, [])

    def test_empty_whitespace_and_oversized_disposition_answer_400(self):
        for disposition in ('', '   ', 'x' * 2001, 'x' * 5000):
            with self.subTest(size=len(disposition)):
                code, body = self.resolve(disposition=disposition)
                self.assertEqual(code, 400, body)
        for fields in ({'incidentId': ''}, {'incidentId': 7}, {'disposition': None}, {'extra': 1}):
            with self.subTest(fields=fields):
                self.assertEqual(self.resolve(**fields)[0], 400)
        self.assertEqual(self.resolve(disposition='x' * 2000)[0], 200)     # the bound itself is accepted
        self.assertEqual(len(self.host.resolutions), 1)

    def test_wrong_method_is_405_and_cross_origin_403(self):
        code, body = self.request('/api/claude/resolve-incident?' + urlencode(self.identity))
        self.assertEqual(code, 405, body)
        body = {'schemaVersion': 1, 'backend': 'claude', **self.identity, 'incidentId': 'inc-a', 'disposition': 'x'}
        self.assertEqual(self.request('/api/claude/resolve-incident', 'POST', body,
                                      headers={'Origin': 'http://evil.invalid'})[0], 403)
        self.assertEqual(self.host.resolutions, [])

    def test_state_carries_open_incidents(self):
        body = self.state()
        self.assertEqual(body['openIncidents'], [
            {'incidentId': 'inc-a', 'category': 'capacity_error', 'time': 1760000000.5,
             'summary': 'quota reached; recovery scheduled'},
            {'incidentId': 'inc-b', 'category': 'protocol_attention', 'time': 1760000100,
             'summary': json.dumps({'other': 'value'}, ensure_ascii=False)},
            {'incidentId': 'inc-c', 'category': 'unknown', 'time': None, 'summary': None},   # unreadable: listed anyway
        ])
        reads = [p['incident_id'] for m, p in self.host.calls if m == 'incident']
        self.assertEqual(sorted(set(reads)), ['inc-a', 'inc-b', 'inc-c'])
        self.state()
        reads_after = [p['incident_id'] for m, p in self.host.calls if m == 'incident']
        self.assertEqual(reads_after.count('inc-a'), 1)  # a readable body is cached
        self.assertNotIn('settings_unverified', body['liveState'])

    def test_state_carries_settings_unverified_observation(self):
        self.host.record.update(settings_unverified=True,
                                settings_unverified_disclosure='get_settings readback timed out')
        live = self.state()['liveState']
        self.assertIs(live['settings_unverified'], True)
        self.assertEqual(live['settings_unverified_disclosure'], 'get_settings readback timed out')


class PaneQuietToolTests(unittest.TestCase):
    def manager(self, **fields):
        return {'role': 'Manager', 'state': 'ready', 'owner': 'host', 'label': 'Manager-Session-7',
                'native_uuid': '12345678-aaaa-4bbb-8ccc-dddddddddddd', 'pending_message_uuids': [],
                'primary_turn_open': False, **fields}

    def busy_for(self, records):
        with patch.object(claude_live.PaneHostClient, 'call', lambda self, method, **params: copy.deepcopy(records)):
            return pane_quiet.manager_busy()

    def test_open_turn_is_busy(self):
        self.assertEqual(self.busy_for([self.manager(primary_turn_open=True)]),
                         (['Manager Manager-Session-7: its reply is still running'], None))

    def test_unanswered_send_is_busy(self):
        self.assertEqual(self.busy_for([self.manager(pending_message_uuids=['m-1'])]),
                         (['Manager Manager-Session-7: a Send is still unanswered'], None))

    def test_closed_turn_with_background_tasks_is_quiet(self):
        record = self.manager(background_tasks={'t-1': {'status': 'running'}}, background_unsettled=True,
                              native_session_state='running')
        self.assertEqual(self.busy_for([record]), ([], None))

    def test_non_manager_and_ended_manager_are_ignored(self):
        records = [self.manager(role='worker', primary_turn_open=True),
                   self.manager(role='helper', pending_message_uuids=['m']),
                   self.manager(state='exited', owner='gone', primary_turn_open=True),
                   self.manager(state='collected', owner='gone', pending_message_uuids=['m']),
                   self.manager(owner='unknown', primary_turn_open=True),
                   'not a record', None]
        self.assertEqual(self.busy_for(records), ([], None))
        self.assertEqual(self.busy_for({'not': 'a list'}), ([], None))

    def test_legacy_record_without_turn_witness_and_name_fallbacks(self):
        legacy = self.manager(label=None, labels={'title': 'Titled'}, native_session_state='requires_action')
        del legacy['primary_turn_open']
        bare = self.manager(label=None, native_session_state='running')
        del bare['primary_turn_open']
        reasons, note = self.busy_for([legacy, bare])
        self.assertIsNone(note)
        self.assertEqual(reasons, ['Manager Titled: it is working (this host has no turn witness)',
                                   'Manager 12345678: it is working (this host has no turn witness)'])

    def test_host_that_raises_gives_no_reasons_and_a_note(self):
        def raises(self, method, **params):
            raise OSError('no socket')
        with patch.object(claude_live.PaneHostClient, 'call', raises):
            reasons, note = pane_quiet.manager_busy()
        self.assertEqual(reasons, [])
        self.assertIn('not answering', note)
        self.assertIn('OSError', note)

    def test_host_is_asked_for_a_summary_list(self):
        seen = []
        with patch.object(claude_live.PaneHostClient, 'call',
                          lambda self, method, **params: seen.append((method, params)) or []):
            pane_quiet.manager_busy()
        self.assertEqual(seen, [('list', {'summary': True})])

    def test_pane_busy_404_predates(self):
        error = urllib.error.HTTPError(pane_quiet.PANE + '/api/pane-quiet', 404, 'Not Found', {}, None)
        with patch.object(pane_quiet.urllib.request, 'urlopen', side_effect=error):
            reasons, note = pane_quiet.pane_busy()
        self.assertEqual(reasons, [])
        self.assertIn('predates', note)

    def test_pane_busy_other_http_error_is_a_reason(self):
        error = urllib.error.HTTPError(pane_quiet.PANE + '/api/pane-quiet', 500, 'Boom', {}, None)
        with patch.object(pane_quiet.urllib.request, 'urlopen', side_effect=error):
            self.assertEqual(pane_quiet.pane_busy(), (['the pane server answered HTTP 500 to the quiet check'], None))

    def test_pane_busy_not_running_is_a_note(self):
        with patch.object(pane_quiet.urllib.request, 'urlopen', side_effect=urllib.error.URLError('refused')):
            reasons, note = pane_quiet.pane_busy()
        self.assertEqual(reasons, [])
        self.assertIn('not answering', note)

    def test_pane_busy_passes_the_busy_list_through(self):
        payload = json.dumps({'quiet': False, 'busy': ['/api/claude/send request in flight',
                                                       'refine in progress in the pane']}).encode()
        opened = []

        def urlopen(url, timeout=None):
            opened.append(url)
            return contextlib.nullcontext(io.BytesIO(payload))
        with patch.object(pane_quiet.urllib.request, 'urlopen', urlopen):
            self.assertEqual(pane_quiet.pane_busy(),
                             (['/api/claude/send request in flight', 'refine in progress in the pane'], None))
        self.assertEqual(opened, ['http://127.0.0.1:9911/api/pane-quiet'])

    def test_wait_limit_zero_returns_3_when_busy(self):
        out = io.StringIO()
        with patch.object(pane_quiet, 'pane_busy', return_value=(['send in progress in the pane'], None)), \
                patch.object(pane_quiet, 'manager_busy', return_value=([], None)), \
                patch.object(pane_quiet.time, 'sleep', lambda seconds: None), contextlib.redirect_stdout(out):
            self.assertEqual(pane_quiet.wait(0), 3)
        self.assertIn('send in progress in the pane', out.getvalue())
        self.assertIn('nothing was restarted', out.getvalue())

    def test_wait_needs_two_quiet_checks(self):
        checks = []

        def pane():
            checks.append(1)
            return [], None
        out = io.StringIO()
        with patch.object(pane_quiet, 'pane_busy', pane), \
                patch.object(pane_quiet, 'manager_busy', return_value=([], 'the Claude host is not answering')), \
                patch.object(pane_quiet.time, 'sleep', lambda seconds: None), contextlib.redirect_stdout(out):
            self.assertEqual(pane_quiet.wait(0), 0)
        self.assertEqual(len(checks), 2)
        self.assertEqual(out.getvalue().count('the Claude host is not answering'), 1)   # each note once
        self.assertIn('quiet after', out.getvalue())


if __name__ == '__main__':
    unittest.main()
