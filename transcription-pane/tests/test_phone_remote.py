"""Actual-wire Serve headers through the shared Handler guard; only inert host writes."""
import contextlib
from email.header import Header
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from claude_live import ClaudeFacade
from claude_reader import ClaudeReader
from test_claude_manager import ManagerHost
from test_claude_live import EX

ORIGIN = 'https://pane.example.ts.net'
AUTHORITY = 'pane.example.ts.net'
LOGIN = 'dan@example.test'  # Fixture only, never Dan's configuration.


class PhoneHost(ManagerHost):
    def call(self, method, **params):
        if method == 'get_settings':
            self.calls.append((method, params))
            return {'state': 'applied', 'applied': True, 'model': 'claude-opus-5-5', 'effort': 'high',
                    'blocks_send': False, 'gate': {'blocked': False}, 'generation': params['generation']}
        return super().call(method, **params)


class PhoneRemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / 'pane-remote.json'
        self.host = PhoneHost()
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.port = self.httpd.server_address[1]
        self.identity = {'targetRef': self.reader._ref('target', (EX, 1)), 'executionId': EX, 'generation': 1}
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.temp.cleanup()

    def enable(self, origin=ORIGIN, login=LOGIN):
        self.config.write_text(json.dumps({'origin': origin, 'login': login}), encoding='utf-8')
        policy = server.load_remote_policy(self.config)
        self.assertIsNotNone(policy)
        self.httpd.remote_origin_policy = policy
        return policy

    def headers(self, *, origin=ORIGIN, host=AUTHORITY, login=LOGIN, site=None):
        return ([("Host", host)] if host is not None else []) + (
            [("Origin", origin)] if origin is not None else []) + (
            [("Tailscale-User-Login", login)] if login is not None else []) + (
            [("Sec-Fetch-Site", site)] if site is not None else [])

    def request(self, route, headers, method='GET', data=None):
        # No header dict: duplicate fields must arrive as separate fields on the wire.
        raw = json.dumps(data).encode() if data is not None else None
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        connection.putrequest(method, route, skip_host=True)
        for name, value in headers:
            connection.putheader(name, value)
        if raw is not None:
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Content-Length', str(len(raw)))
        connection.endheaders(raw)
        response = connection.getresponse()
        payload = response.read()
        result = (response.status, dict(response.getheaders()),
                  json.loads(payload) if 'application/json' in response.getheader('Content-Type', '') else payload)
        connection.close()
        return result

    def operations(self):
        return [('/api/claude/sessions?role=Manager', 'GET', None),
                ('/api/claude/control', 'POST', {'schemaVersion': 1, 'backend': 'claude', **self.identity,
                    'requestId': str(uuid.uuid4()), 'operation': 'get_settings'}),
                ('/api/claude/send', 'POST', {'schemaVersion': 1, 'backend': 'claude', **self.identity,
                    'messageUuid': str(uuid.uuid4()), 'original': '  phone original 雪\n', 'submitted': 'phone text 雪'})]

    def assert_refused(self, headers):
        for route, method, body in self.operations():
            with self.subTest(route=route):
                before_calls, before_writes = len(self.host.calls), len(self.host.writes)
                code, response_headers, result = self.request(route, headers, method, body)
                self.assertEqual(code, 403, result)
                self.assertEqual(result['status'], 'origin')
                self.assertNotIn('Access-Control-Allow-Origin', response_headers)
                self.assertEqual(len(self.host.calls), before_calls, 'refusal reached the host')
                self.assertEqual(len(self.host.writes), before_writes, 'refusal mutated the host')

    def test_configured_origin_list_recheck_and_send_pass_for_each_allowed_fetch_site(self):
        self.enable()
        for site in (None, 'same-origin', 'none'):
            self.host.record['pending_message_uuids'] = []
            with self.subTest(site=site):
                for route, method, body in self.operations():
                    code, response_headers, result = self.request(route, self.headers(site=site), method, body)
                    self.assertEqual(code, 200, result)
                    self.assertNotIn('Access-Control-Allow-Origin', response_headers)
                    if method == 'GET':
                        self.assertEqual([(r['source'], r['role']) for r in result['sessions']], [('host', 'Manager')])
                    elif route.endswith('control'):
                        self.assertTrue(result['settingsReadback']['applied'])
                    else:
                        self.assertTrue(result['receipt']['accepted'])
        self.assertEqual(sum(m == 'get_settings' for m, _ in self.host.calls), 3)
        self.assertEqual(sum(m == 'send' for m, _ in self.host.calls), 3)
        self.assertEqual(len(self.host.writes), 3)

    def test_navigation_without_origin_and_shared_activity_caller_pass(self):
        self.enable()
        for site in (None, 'same-origin', 'none'):
            with self.subTest(site=site):
                headers = self.headers(origin=None, site=site)
                code, _, html = self.request('/sessions', headers)
                self.assertEqual(code, 200)
                self.assertIn(b'<html', html)
                self.assertEqual(self.request('/api/claude/sessions?role=Manager', headers)[0], 200)
                self.assertEqual(self.request('/api/pane-quiet', headers)[0], 200)

    def test_phone_page_and_trailing_slash_pass_the_configured_origin_policy(self):
        self.enable()
        for route in ('/m', '/m/', '/m.html'):
            for origin in (None, ORIGIN):
                with self.subTest(route=route, origin=origin):
                    code, headers, html = self.request(route, self.headers(origin=origin))
                    self.assertEqual(code, 200)
                    self.assertIn(b'id="m-actions"', html)
                    self.assertIn(b'src="/m.js"', html)
                    self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.host.writes, [])

    def test_glimpse_routes_files_and_reserved_pages_reuse_same_serve_policy(self):
        from test_glimpse_routes import FakeClient
        client = FakeClient()
        self.httpd.glimpse_client_factory = lambda: client
        self.httpd.nexus_checkout = Path(self.temp.name)
        assets = Path(self.temp.name) / 'workflowy-glimpse-cache'
        assets.mkdir()
        (assets / 'marked.min.js').write_bytes(b'/*! marked v15.0.12 */')
        pages = Path(self.temp.name) / 'static'
        pages.mkdir()
        for name in server.GLIMPSE_PAGES:
            (pages / name).write_bytes(b'B3 placeholder fixture, not a production page')
        routes = [("/api/glimpse/render", "POST", {"kind": "parcel", "value": {"node": "abcd1234"}}),
                  ("/api/glimpse/catalog", "GET", None), ("/api/glimpse/cache-refresh", "POST", {}),
                  ("/nexus-md/marked.min.js", "GET", None),
                  *[("/" + name, "GET", None) for name in server.GLIMPSE_PAGES]]
        with patch.object(server, 'STATIC', pages):
            for route, method, data in routes:
                with self.subTest(route=route, policy='off'):
                    before = len(client.calls)
                    self.assertEqual(self.request(route, self.headers(), method, data)[0], 403)
                    self.assertEqual(len(client.calls), before)
                    self.httpd.glimpse_refresh_attempt = None
                    self.assertEqual(self.request(route, [('Host', f'127.0.0.1:{self.port}')], method, data)[0], 200)
            self.enable()
            for route, method, data in routes:
                with self.subTest(route=route, policy='on'):
                    self.httpd.glimpse_refresh_attempt = None
                    code, headers, _ = self.request(route, self.headers(), method, data)
                    self.assertEqual(code, 200)
                    self.assertNotIn('Access-Control-Allow-Origin', headers)
                    for fields in (self.headers(login=None), self.headers(login='wrong'),
                                   self.headers(origin='https://foreign.invalid'), self.headers(site='cross-site')):
                        before = len(client.calls)
                        self.assertEqual(self.request(route, fields, method, data)[0], 403)
                        self.assertEqual(len(client.calls), before)
                    self.assertEqual(self.request(route, self.headers(), 'OPTIONS')[0], 403)
        self.assertEqual(self.host.writes, [])

    def test_phone_page_is_refused_with_remote_off_and_loopback_still_loads(self):
        for route in ('/m', '/m/', '/m.html'):
            with self.subTest(route=route):
                code, headers, result = self.request(route, self.headers(origin=None))
                self.assertEqual(code, 403)
                self.assertEqual(result['status'], 'origin')
                self.assertNotIn('Access-Control-Allow-Origin', headers)
                self.assertEqual(self.request(route, [('Host', f'127.0.0.1:{self.port}')])[0], 200)
        self.assertEqual(self.host.writes, [])

    def test_phone_program_page_has_the_same_origin_guard_as_the_chat_page(self):
        route = '/m-programs.html'
        self.assertEqual(self.request(route, self.headers())[0], 403)
        self.assertEqual(self.request(route, [('Host', f'127.0.0.1:{self.port}')])[0], 200)
        self.enable()
        code, headers, page = self.request(route, self.headers(origin=None, site='none'))
        self.assertEqual(code, 200)
        self.assertIn(b'data-view="programs"', page)
        self.assertIn(b'src="/sessions.js"', page)
        self.assertNotIn(b'id="history-target"', page)
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        for fields in (self.headers(login=None), self.headers(login='wrong'), self.headers(site='cross-site'),
                       self.headers(site='same-site'), self.headers(origin='https://foreign.invalid'),
                       self.headers() + [('Origin', ORIGIN)], self.headers() + [('Host', AUTHORITY)],
                       self.headers() + [('Tailscale-User-Login', LOGIN)]):
            self.assertEqual(self.request(route, fields)[0], 403)
        self.assertEqual(self.host.writes, [])

    def test_phone_reuses_existing_read_only_opencode_sidebar_routes_with_no_policy_change(self):
        calls = []

        class SidebarReads:
            def programs(self):
                calls.append(('programs', None))
                return {'schemaVersion': 1, 'backend': 'opencode', 'status': 'complete', 'sessions': []}

            def glance(self, id):
                calls.append(('glance', id))
                return {'schemaVersion': 1, 'backend': 'opencode', 'status': 'complete', 'id': id, 'messages': []}

            def turns(self, id):
                calls.append(('turns', id))
                return {'schemaVersion': 1, 'backend': 'opencode', 'status': 'complete', 'id': id, 'firstAt': None, 'lastAt': None}

        self.httpd.opencode_programs = SidebarReads()  # Inert; never contact the real OpenCode service.
        routes = ('/api/opencode/programs', '/api/opencode/glance?id=ses_fixture123', '/api/opencode/turns?id=ses_fixture123')
        for route in routes:
            self.assertEqual(self.request(route, self.headers())[0], 403)
        self.assertEqual(calls, [])
        self.enable()
        for route in routes:
            code, headers, data = self.request(route, self.headers())
            self.assertEqual(code, 200)
            self.assertEqual(data['backend'], 'opencode')
            self.assertNotIn('Access-Control-Allow-Origin', headers)
            self.assertEqual(self.request(route, self.headers(), 'POST')[0], 405)
            self.assertEqual(self.request(route, self.headers(), 'OPTIONS')[0], 403)
        before = list(calls)
        for route in routes:
            for fields in (self.headers(login=None), self.headers(login='wrong'), self.headers(site='cross-site'),
                           self.headers(site='same-site'), self.headers(origin='https://foreign.invalid')):
                self.assertEqual(self.request(route, fields)[0], 403)
        self.assertEqual(calls, before)
        self.assertEqual(self.host.writes, [])

    def test_phone_opencode_conversation_and_tool_record_reads_have_the_phone_policy(self):
        calls = []

        class Conversation:
            def events(self, **args):
                calls.append(('events', args))
                return {'schemaVersion': 1, 'backend': 'opencode', 'source': 'opencode', 'sessionRef': args['id'], 'events': []}

            def snapshot(self, **args):
                calls.append(('snapshot', args))
                return {'schemaVersion': 1, 'backend': 'opencode', 'callRef': args['callRef'], 'fileEpoch': args['fileEpoch'],
                        'input': '{}', 'output': 'ok 雪'}

        self.httpd.opencode_conversation = Conversation()  # Inert; never contact the real OpenCode service.
        routes = ('/api/opencode/events?id=ses_fixture123',
                  '/api/opencode/tool-record?' + urlencode({'id': 'ses_fixture123', 'callRef': 'occall_x', 'fileEpoch': 'oc_y'}))
        for route in routes:
            self.assertEqual(self.request(route, self.headers())[0], 403)  # remote access OFF
        self.assertEqual(calls, [])
        self.enable()
        for route in routes:
            code, headers, data = self.request(route, self.headers())
            self.assertEqual(code, 200, data)
            self.assertEqual(data['backend'], 'opencode')
            self.assertNotIn('Access-Control-Allow-Origin', headers)
            self.assertEqual(self.request(route, self.headers(), 'POST')[0], 405)
            self.assertEqual(self.request(route, self.headers(), 'OPTIONS')[0], 403)
        self.assertEqual(calls, [('events', {'id': 'ses_fixture123'}),
                                 ('snapshot', {'id': 'ses_fixture123', 'callRef': 'occall_x', 'fileEpoch': 'oc_y'})])
        for route in routes:
            for fields in (self.headers(login=None), self.headers(login='wrong'), self.headers(site='cross-site'),
                           self.headers(site='same-site'), self.headers(origin='https://foreign.invalid'),
                           self.headers(host='foreign.example.ts.net'), self.headers() + [('Tailscale-User-Login', LOGIN)]):
                code, _, data = self.request(route, fields)
                self.assertEqual((code, data['status']), (403, 'origin'))
        self.assertEqual(len(calls), 2)  # every refusal stopped before the reader
        self.assertEqual(self.host.writes, [])

    def test_phone_usage_route_has_the_phone_origin_policy_and_answers_from_its_cache(self):
        runs = []

        def claude():
            runs.append('claude')
            return {'claude': {'ok': True, 'plan': 'max', 'windows': [{'label': 'five-hour', 'kind': 'session', 'percent': 23, 'resets_at': '2026-10-19T03:00:00Z'}],
                               'reset_credits': [], 'reset_credits_error': None, 'observed_at': 1000.0, 'age_seconds': 0, 'stale': False},
                    'claude_frame_line': None}

        def chatgpt():
            runs.append('chatgpt')
            return {'chatgpt': {'ok': True, 'plan': 'pro', 'windows': [{'label': 'weekly', 'percent': 41, 'resets_at': 1792400000}],
                                'models': {'astra': {'available': True, 'available_at': None}}, 'account_key': 'a' * 64,
                                'observed_at': 1000.0, 'age_seconds': 0, 'stale': False,
                                'reset_credits': [{'expires_at': 1792700640.0, 'granted_at': 1790108640.0, 'title': 'Rate limit reset'}]}}

        # Synchronous start: the readings are in place before the response is written. Never the real tool.
        self.httpd.usage_readings = server.UsageReadings(run=claude, run_chatgpt=chatgpt, start=lambda work: work())
        route = '/api/usage'
        self.assertEqual(self.request(route, self.headers())[0], 403)
        self.assertEqual(runs, [])
        code, headers, data = self.request(route, [('Host', f'127.0.0.1:{self.port}')])
        self.assertEqual((code, data['backend'], data['status']), (200, 'usage', 'ready'))
        self.assertEqual(sorted(runs), ['chatgpt', 'claude'], 'one read per provider, never one reading for both')
        self.enable()
        for site in (None, 'same-origin', 'none'):
            code, headers, data = self.request(route, self.headers(site=site))
            self.assertEqual(code, 200, data)
            self.assertNotIn('Access-Control-Allow-Origin', headers)
            self.assertEqual(data['claude']['windows'][0]['percent'], 23)
            self.assertEqual(data['chatgpt']['models'], {'astra': {'available': True, 'available_at': None}})
            self.assertNotIn('account_key', data['chatgpt'])
            self.assertEqual(data['claude']['reset_credits'], [])
            self.assertEqual(data['chatgpt']['reset_credits'], [{'expires_at': 1792700640.0, 'granted_at': 1790108640.0, 'title': 'Rate limit reset'}])
        self.assertEqual(len(runs), 2, 'a fresh cached reading never re-reads the providers')
        self.assertEqual(self.request(route + '?refresh=1', self.headers())[0], 200)
        self.assertEqual(len(runs), 2, 'refresh=1 still honours the minimum gap')
        self.assertEqual(self.request(route + '?refresh=2', self.headers())[0], 400)
        self.assertEqual(self.request(route + '?x=1', self.headers())[0], 400)
        self.assertEqual(self.request(route, self.headers(), 'POST', {})[0], 405)
        self.assertEqual(self.request(route, self.headers(), 'OPTIONS')[0], 403)
        for fields in (self.headers(login=None), self.headers(login='wrong'), self.headers(site='cross-site'),
                       self.headers(site='same-site'), self.headers(origin='https://foreign.invalid'),
                       self.headers() + [('Tailscale-User-Login', LOGIN)]):
            self.assertEqual(self.request(route, fields)[0], 403)
        self.assertEqual(len(runs), 2)
        self.assertEqual(self.host.writes, [])

    def test_telemetry_route_has_the_phone_origin_policy_and_a_strict_query(self):
        calls = []

        class Telemetry:
            def program(self, core):
                calls.append(("program", core))
                return {"schemaVersion": 1, "backend": "telemetry", "status": "complete", "program": f"[ap:{core}]", "sessions": {}}

            def sessions(self, sids):
                calls.append(("sessions", tuple(sids)))
                return {"schemaVersion": 1, "backend": "telemetry", "status": "complete", "program": None, "sessions": {}}

        self.httpd.pane_telemetry = Telemetry()
        route = '/api/telemetry?program=Z362II'
        self.assertEqual(self.request(route, self.headers())[0], 403, 'remote access is off by default')
        code, headers, data = self.request(route, [('Host', f'127.0.0.1:{self.port}')])
        self.assertEqual((code, data['backend'], data['program']), (200, 'telemetry', '[ap:Z362II]'))
        self.enable()
        self.assertEqual(self.request(route, self.headers())[0], 200)
        cc = 'cc-0ebf3fde-a626-4829-a2e7-e7bd1029fb19'
        self.assertEqual(self.request('/api/telemetry?sid=ses_eac38000affeSFv3D0cwcrXZ0Y&sid=' + cc, self.headers())[0], 200)
        self.assertEqual(calls, [('program', 'Z362II')] * 2 + [('sessions', ('ses_eac38000affeSFv3D0cwcrXZ0Y', cc))])
        for bad in ('', '?program=z362ii', '?program=Z362II&program=ABCDEF', '?program=Z362II&sid=' + cc, '?sid=ses_x',
                    '?sid=' + cc + '&sid=' + cc, '?' + '&'.join('sid=ses_%010d' % n for n in range(41)), '?x=1',
                    '?program=[ap:Z362II]'):
            with self.subTest(query=bad):
                self.assertEqual(self.request('/api/telemetry' + bad, self.headers())[0], 400)
        self.assertEqual(len(calls), 3, 'a refused query reaches nothing')
        self.assertEqual(self.request(route, self.headers(), 'POST', {})[0], 405)
        self.assertEqual(self.request(route, self.headers(), 'OPTIONS')[0], 403)
        for fields in (self.headers(login=None), self.headers(login='wrong'), self.headers(site='cross-site'),
                       self.headers(origin='https://foreign.invalid')):
            self.assertEqual(self.request(route, fields)[0], 403)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.host.writes, [])

    def test_history_routes_have_the_phone_origin_policy(self):
        calls = []

        class Archive:
            def route(self, path, query):
                calls.append((path, query))
                return {'schemaVersion': 1, 'backend': 'history', 'status': 'empty', 'units': []}

        self.httpd.history_archive = Archive()  # Inert; never opens an archive.
        routes = ('/api/history/programs', '/api/history/sessions?unit=program:Z362II',
                  '/api/history/glance?key=claude:cc-0ebf3fde-a626-4829-a2e7-e7bd1029fb19',
                  '/api/history/events?key=opencode:ses_fixture123')
        for route in routes:
            self.assertEqual(self.request(route, self.headers())[0], 403)  # remote access OFF
        self.assertEqual(calls, [])
        self.enable()
        for route in routes:
            code, headers, data = self.request(route, self.headers())
            self.assertEqual((code, data['backend']), (200, 'history'), data)
            self.assertNotIn('Access-Control-Allow-Origin', headers)
            self.assertEqual(self.request(route, self.headers(), 'POST')[0], 405)
            self.assertEqual(self.request(route, self.headers(), 'OPTIONS')[0], 403)
        self.assertEqual(len(calls), 4)
        for route in routes:
            for fields in (self.headers(login=None), self.headers(login='wrong'), self.headers(site='cross-site'),
                           self.headers(site='same-site'), self.headers(origin='https://foreign.invalid'),
                           self.headers(host='foreign.example.ts.net'), self.headers() + [('Tailscale-User-Login', LOGIN)]):
                code, _, data = self.request(route, fields)
                self.assertEqual((code, data['status']), (403, 'origin'))
        self.assertEqual(len(calls), 4)  # every refusal stopped before the archive
        self.assertEqual(self.host.writes, [])

    def test_usage_readings_refresh_once_at_a_time_keep_the_last_good_reading_and_respect_the_gap(self):
        now, started, claude_results, gpt_results = [1000.0], [], [], []
        readings = server.UsageReadings(run=lambda: claude_results.pop(0)() if callable(claude_results[0]) else claude_results.pop(0),
                                        run_chatgpt=lambda: gpt_results.pop(0),
                                        clock=lambda: now[0], start=started.append)
        claude_good = {'ok': True, 'windows': []}
        gpt_stale = {'ok': False, 'stale': True, 'age_seconds': 600}
        first = readings.snapshot()
        self.assertEqual((first['status'], first['refreshing'], len(started)), ('loading', True, 2))
        readings.snapshot(force=True)
        self.assertEqual(len(started), 2, 'one refresh per provider at a time')
        claude_results.append({'claude': claude_good})
        gpt_results.append({'chatgpt': gpt_stale})
        started.pop(0)()  # Claude
        started.pop(0)()  # ChatGPT
        now[0] += 10
        ready = readings.snapshot()
        self.assertEqual((ready['status'], ready['ageSeconds'], ready['chatgptAgeSeconds'], ready['refreshing']),
                         ('ready', 10.0, 610.0, False))
        self.assertNotIn('account_key', json.dumps(ready['chatgpt']))
        self.assertEqual(started, [], 'a 10 s reading is fresh')
        now[0] += 25
        readings.snapshot(force=True)
        self.assertEqual(len(started), 2, 'a deliberate read refreshes a reading older than FORCE_SECONDS')
        # Claude's failure keeps its last good reading; GPT's own cache refreshes independently.
        def fail():
            raise RuntimeError('usage endpoint: HTTP 500')

        claude_results.append(fail)
        gpt_results.append({"chatgpt": gpt_stale})
        started.pop(0)()
        started.pop(0)()
        failed = readings.snapshot(force=True)
        self.assertEqual((failed['status'], failed['error'], failed['claude']['ok']),
                         ('ready', 'usage endpoint: HTTP 500', True))
        self.assertFalse(failed['refreshing'])
        self.assertEqual(started, [], 'a failure waits out MIN_GAP before the next attempt')
        now[0] += server.UsageReadings.MIN_GAP
        readings.snapshot(force=True)
        self.assertEqual(len(started), 2, 'the failed provider waits out MIN_GAP; GPT is due on its own')
        claude_results.append(['not', 'a', 'reading'])
        gpt_results.append({"chatgpt": gpt_stale})
        started.pop(0)()
        started.pop(0)()
        self.assertEqual(readings.snapshot()['error'], 'usage tool returned an unexpected reading')

    def test_phone_preview_uses_the_same_remote_policy_and_exact_shared_file(self):
        route = '/tool-preview-core.js'
        core = Path(__file__).resolve().parents[2] / 'chrome-extension/codenomad-session-watcher/tool-preview-core.js'
        self.assertEqual(self.request(route, self.headers())[0], 403)
        self.assertEqual(self.request(route, [('Host', f'127.0.0.1:{self.port}')])[2], core.read_bytes())
        self.enable()
        code, headers, script = self.request(route, self.headers(site='same-origin'))
        self.assertEqual(code, 200)
        self.assertEqual(script, core.read_bytes())
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        for request_headers in (self.headers(login=None), self.headers(login='wrong'),
                                self.headers(origin='https://foreign.invalid'), self.headers(site='cross-site'),
                                self.headers(site='same-site'), self.headers() + [('Host', AUTHORITY)],
                                self.headers() + [('Origin', ORIGIN)], self.headers() + [('Tailscale-User-Login', LOGIN)]):
            with self.subTest(headers=request_headers):
                self.assertEqual(self.request(route, request_headers)[0], 403)
        self.assertEqual(self.request(route, self.headers(), 'POST')[0], 405)
        self.assertEqual(self.request(route, self.headers(), 'OPTIONS')[0], 403)
        self.assertEqual(self.host.writes, [])

    def test_phone_tool_read_reuses_exact_reader_snapshot_without_touching_desktop_mailbox(self):
        self.enable()
        inp = {'command': 'echo 雪', 'n': 9007199254740993, 'flag': False}
        out = 'Stored tool output 雪\n<script>literal, not markup</script>'
        self.host.frame('assistant', [{'type': 'tool_use', 'id': 'phone-tool', 'name': 'Bash', 'input': inp}], 'tool-record')
        self.host.frame('user', [{'type': 'tool_result', 'tool_use_id': 'phone-tool', 'content': out}], 'result-record')
        code, _, inventory = self.request('/api/claude/sessions?role=Manager', self.headers())
        self.assertEqual(code, 200)
        row = inventory['sessions'][0]
        code, _, page = self.request('/api/claude/events?' + urlencode({'sessionRef': row['sessionRef'],
            'scopeRef': row['scopes'][0]['scopeRef']}), self.headers())
        self.assertEqual(code, 200)
        call = next(c for event in page['events'] for c in event['calls'])
        args = {'callRef': call['callRef'], 'fileEpoch': page['fileEpoch']}
        expected = self.reader.snapshot(**args)
        route = '/api/claude/tool-record?' + urlencode(args)
        mailbox = server.ToolMailbox()
        with patch.object(server, '_tools', mailbox):
            before = mailbox.snapshot()
            code, headers, snapshot = self.request(route, self.headers())
            self.assertEqual(code, 200)
            self.assertEqual(snapshot, expected)
            self.assertEqual(json.loads(snapshot['input']), inp)
            self.assertIn('9007199254740993', snapshot['input'])
            self.assertEqual(snapshot['output'], out)
            self.assertEqual(snapshot['diff'], {'files':[], 'source':'unavailable', 'complete':False,
                'reason':'Shell command; no structured edit diff recorded (it may have edited files).'})
            self.assertEqual((snapshot['inputBytes'],snapshot['outputBytes']),
                             (len(snapshot['input'].encode()),len(out.encode())))
            self.assertEqual(mailbox.snapshot(), before)
            self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.host.writes, [])
        self.assertFalse(any(method in ('send', 'begin_control', 'reconnect') for method, _ in self.host.calls))
        for fields in ({'callRef': call['callRef'], 'fileEpoch': 'foreign-epoch'},
                       {'callRef': 'unknown-call', 'fileEpoch': page['fileEpoch']},
                       {'fileEpoch': page['fileEpoch']}, {**args, 'arbitrary': 'field'}):
            with self.subTest(fields=fields):
                self.assertIn(self.request('/api/claude/tool-record?' + urlencode(fields), self.headers())[0], (400, 409))
        with patch.object(self.reader, 'snapshot', side_effect=AssertionError('refusal must precede reader')):
            for headers in (self.headers(login=None), self.headers(login='wrong'), self.headers(site='cross-site'),
                            self.headers(site='same-site'), self.headers(origin='https://foreign.invalid'),
                            self.headers() + [('Tailscale-User-Login', LOGIN)], self.headers() + [('Host', AUTHORITY)]):
                self.assertEqual(self.request(route, headers)[0], 403)
            self.httpd.remote_origin_policy = None
            self.assertEqual(self.request(route, self.headers())[0], 403)
        self.enable()
        self.assertEqual(self.request(route, self.headers(), 'POST')[0], 405)
        self.assertEqual(self.request(route, self.headers(), 'OPTIONS')[0], 403)

    def test_phone_tool_wire_allowance_matches_desktop_for_escaped_stored_text(self):
        self.enable()
        route = '/api/claude/tool-record?callRef=fixture&fileEpoch=fixture'
        snapshot = {'schemaVersion': 1, 'backend': 'claude', 'callRef': 'fixture', 'fileEpoch': 'fixture',
                    'input': '"' * (5 * 1024 * 1024), 'output': '', 'status': 'stored'}
        with patch.object(self.reader, 'snapshot', return_value=snapshot):
            code, _, result = self.request(route, self.headers())
            self.assertEqual(code, 200)
            self.assertEqual(result, snapshot)  # >8 MiB on the wire, <8 MiB of stored text.

    def test_off_by_default_absent_empty_and_invalid_config_refuse_without_upstream(self):
        # An ephemeral server with no injected attribute has the same OFF default.
        self.assertFalse(hasattr(self.httpd, 'remote_origin_policy'))
        self.assert_refused(self.headers())
        with patch.object(Path, 'home', return_value=Path(self.temp.name)):
            self.assertIsNone(server.load_remote_policy())  # absent default location
        for text in ('', '  \n', '{}'):
            self.config.write_text(text, encoding='utf-8')
            self.httpd.remote_origin_policy = server.load_remote_policy(self.config)
            self.assertIsNone(self.httpd.remote_origin_policy)
            self.assert_refused(self.headers())
        invalid = ['not json', '[]', 'null', '{"origin": "' + ORIGIN + '"}',
                   json.dumps({'login': LOGIN}), json.dumps({'origin': ORIGIN, 'login': ''}),
                   json.dumps({'origin': ORIGIN, 'login': 3}),
                   *[json.dumps({'origin': origin, 'login': LOGIN}) for origin in
                     ('http://pane.example.ts.net', ORIGIN + '/path', ORIGIN + '?q=1', ORIGIN + '#f',
                      'https://user@pane.example.ts.net', 'https://pane.example.ts.net:bad',
                      'https://', ' https://pane.example.ts.net')]]
        for text in invalid:
            with self.subTest(config=text):
                self.config.write_text(text, encoding='utf-8')
                diagnostic = io.StringIO()
                with contextlib.redirect_stderr(diagnostic):
                    self.httpd.remote_origin_policy = server.load_remote_policy(self.config)
                self.assertIsNone(self.httpd.remote_origin_policy)
                self.assertEqual(len(diagnostic.getvalue().splitlines()), 1)
                self.assertIn('pane remote access OFF:', diagnostic.getvalue())
                self.assert_refused(self.headers())
                self.assertEqual(self.request('/api/claude/sessions', [('Host', f'localhost:{self.port}')])[0], 200)
        with patch.object(Path, 'read_text', side_effect=PermissionError('fixture unreadable')):
            diagnostic = io.StringIO()
            with contextlib.redirect_stderr(diagnostic):
                self.httpd.remote_origin_policy = server.load_remote_policy(self.config)
            self.assertIn('fixture unreadable', diagnostic.getvalue())
        self.assert_refused(self.headers())

    def test_every_foreign_missing_and_duplicated_header_is_refused_without_upstream(self):
        self.enable()
        cases = {
            'missing Host': self.headers(host=None),
            'foreign Host': self.headers(host='foreign.example.ts.net'),
            'duplicate Host': self.headers() + [('Host', AUTHORITY)],
            'loopback then remote Host': [('Host', f'localhost:{self.port}')] + self.headers(),
            'foreign Origin': self.headers(origin='https://foreign.example.ts.net'),
            'http Origin': self.headers(origin='http://' + AUTHORITY),
            'duplicate Origin': self.headers() + [('Origin', ORIGIN)],
            'missing identity': self.headers(login=None),
            'wrong identity': self.headers(login='someone-else@example.test'),
            'identity case mismatch': self.headers(login=LOGIN.upper()),
            'identity is only a suffix': self.headers(login='prefix-' + LOGIN),
            'duplicate identity': self.headers() + [('Tailscale-User-Login', LOGIN)],
            'cross-site': self.headers(site='cross-site'),
            'same-site': self.headers(site='same-site'),
            'navigation cross-site': self.headers(origin=None, site='cross-site'),
            'forwarded headers alone': [('Host', 'foreign.invalid'), ('X-Forwarded-Host', AUTHORITY),
                                       ('X-Forwarded-Proto', 'https'), ('Tailscale-User-Login', LOGIN)],
        }
        for name, headers in cases.items():
            with self.subTest(case=name):
                self.assert_refused(headers)

    def test_loopback_spellings_keep_their_existing_decision_with_remote_on(self):
        self.enable()
        for hostname in ('127.0.0.1', 'localhost'):
            host = f'{hostname}:{self.port}'
            for origin in (None, 'http://' + host):
                for site in (None, 'same-origin', 'none'):
                    with self.subTest(host=host, origin=origin, site=site):
                        # Even duplicate/foreign identities do not alter the loopback branch.
                        headers = self.headers(host=host, origin=origin, login='foreign', site=site)
                        headers += [('Tailscale-User-Login', 'also foreign')]
                        self.assertEqual(self.request('/api/claude/sessions', headers)[0], 200)
            for headers in (self.headers(host=host, origin='https://' + host),
                            self.headers(host=host, origin=ORIGIN),
                            self.headers(host=host, origin=None, site='cross-site'),
                            self.headers(host=host, origin=None, site='same-site'),
                            self.headers(host=host, origin=None) + [('Host', host)],
                            self.headers(host=host, origin='http://' + host) + [('Origin', 'http://' + host)]):
                self.assert_refused(headers)

    def test_policy_canonicalization_exact_origin_port_and_mime_login(self):
        policy = self.enable('HTTPS://PANE.Example.TS.NET:443/')
        self.assertEqual((policy.origin, policy.authority), (ORIGIN, AUTHORITY))
        self.assertEqual(self.request('/api/claude/sessions?role=Manager', self.headers())[0], 200)
        self.assert_refused(self.headers(origin=ORIGIN + '/'))  # incoming Origin is exact serialized form
        self.assert_refused(self.headers(origin='https://PANE.example.ts.net'))
        self.enable(ORIGIN + ':8443/', login='dán@example.test')
        headers = self.headers(host=AUTHORITY + ':8443', origin=ORIGIN + ':8443',
                               login=Header('dán@example.test', 'utf-8').encode())
        self.assertEqual(self.request('/api/claude/sessions?role=Manager', headers)[0], 200)
        self.assert_refused(self.headers())
        # Loading is startup-only: changing the file does not change this server's injected policy.
        self.config.write_text('{}', encoding='utf-8')
        self.assertEqual(self.request('/api/claude/sessions?role=Manager', headers)[0], 200)

    def test_main_attaches_policy_once_without_changing_loopback_bind(self):
        policy = self.enable()
        with self.assertRaises(AttributeError):
            policy.login = 'not mutable'
        with patch.object(server, 'ThreadingHTTPServer') as server_factory:
            with patch.object(server, 'load_remote_policy', return_value=policy) as load:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(server.main(), 0)
                load.assert_called_once_with()
            server_factory.assert_called_once_with(('127.0.0.1', 9911), server.Handler)
            self.assertIs(server_factory.return_value.remote_origin_policy, policy)
            server_factory.return_value.serve_forever.assert_called_once_with()


class AccountUsageCacheTests(unittest.TestCase):
    def test_switch_invalidates_old_quota_and_drops_hash_before_browser(self):
        selected = {'label': 'personal', 'identity': 'a' * 24, 'epoch': 1}
        jobs = []
        def named(label):
            return {'claude': {'ok': True, 'windows': [{'percent': 99}], 'account': dict(selected)},
                    'claude_frame_line': 'account personal (login aaaaaaaa, epoch 1)'}
        def chatgpt():
            return {'chatgpt': {'ok': True, 'account_key': 'secret-account-hash'}}
        cache = server.UsageReadings(run_account=named, run_chatgpt=chatgpt, start=jobs.append,
                                     account_reader=lambda: dict(selected),
                                     label_reader=lambda label: {'label': label, 'identity': 'a' * 24, 'epoch': 1})
        cache.snapshot()
        self.assertEqual(len(jobs), 2, 'the active label and GPT read independently')
        jobs.pop(0)()  # the active label's one account cache
        jobs.pop(0)()  # ChatGPT
        first = cache.snapshot()
        self.assertEqual(first['claude']['account'], {'label': 'personal', 'epoch': 1})
        self.assertNotIn('account_key', first['chatgpt'])
        self.assertNotIn('aaaaaaaa', first['claudeFrameLine'])
        selected.update(label='work', identity='b' * 24, epoch=2)
        switched = cache.snapshot()
        self.assertNotIn('claude', switched, 'old-account 99% must disappear immediately')
        self.assertEqual(len(jobs), 1, 'the new account is not delayed by the previous account cache')
        jobs.pop(0)()
        self.assertEqual(cache.snapshot()['claude']['account']['label'], 'work')

    def test_in_flight_old_account_response_is_never_published_as_the_active_view(self):
        selected = {'label': 'personal', 'identity': 'a', 'epoch': 1}
        jobs = []
        def named(label):
            old = dict(selected)
            selected.update(label='work', identity='b', epoch=2)
            return {'claude': {'ok': True, 'windows': [{'percent': 99}], 'account': old}}
        cache = server.UsageReadings(run_account=named, run_chatgpt=lambda: {'chatgpt': {'ok': True}},
                                     start=jobs.append, account_reader=lambda: dict(selected),
                                     label_reader=lambda label: {'label': 'personal', 'identity': 'a', 'epoch': 1})
        cache.snapshot()
        jobs.pop(0)()  # the in-flight personal read, which flips the selection to work
        self.assertNotIn('claude', cache.snapshot(), 'the active view waits for the new login, never the old reading')
        self.assertEqual(cache.snapshot(account='personal')['claude']['windows'][0]['percent'], 99,
                         'the named personal view keeps its own isolated last-good reading')


if __name__ == '__main__':
    unittest.main()
