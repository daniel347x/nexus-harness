"""Round 8 "Open in CodeNomad": the pane server's one focus mailbox (codenomad_focus.FocusRequests: request, a
compare-and-claim with a visible-page head start, no-tab without claiming, outcomes only from the claimer, expiry
with an honest reason) and its routes (pane-only JSON post and poll; CodeNomad-origin text/plain poll, claim and
report with CORS to that exact origin; no preflight)."""
import http.client
import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server
from codenomad_focus import CLAIMED_SECONDS, NO_WATCHER_SECONDS, PENDING_SECONDS, VISIBLE_HEADSTART, FocusRequests

SID, DIR = 'ses_ebf922d9bffehFY24gBk453upB', '/mnt/e/__daniel347x/__Dan_Root/__repos/quick-worker'
NOW = 1_792_090_000.0


def ask(nonce='nonce-0001', **over):
    return {'sessionId': SID, 'directory': DIR, 'title': '[ap:UHZREM]-001 ▸ ⚡ quick helper · [ap:WB93JF]', 'nonce': nonce, **over}


class Mailbox(unittest.TestCase):
    def setUp(self):
        self.clock = [NOW]
        self.focus = FocusRequests(clock=lambda: self.clock[0])

    def state(self):
        return self.focus.snapshot()['request']

    def test_the_first_visible_claimer_wins_and_only_it_reports(self):
        self.assertEqual(self.focus.request(ask())['request']['state'], 'pending')
        self.assertEqual(self.focus.claim({'nonce': 'nonce-0001', 'watcherId': 'a', 'visible': True}), {'claimed': True})
        self.assertEqual(self.focus.claim({'nonce': 'nonce-0001', 'watcherId': 'a', 'visible': True}), {'claimed': True})
        self.assertFalse(self.focus.claim({'nonce': 'nonce-0001', 'watcherId': 'b', 'visible': True})['claimed'])
        self.assertFalse(self.focus.claim({'nonce': 'other-nonce', 'watcherId': 'a', 'visible': True})['claimed'])
        self.assertFalse(self.focus.report({'nonce': 'nonce-0001', 'watcherId': 'b', 'code': 'done'})['accepted'])
        self.assertTrue(self.focus.report({'nonce': 'nonce-0001', 'watcherId': 'a', 'code': 'done', 'message': 'shown'})['accepted'])
        r = self.state()
        self.assertEqual((r['state'], r['claimedBy'], r['outcome']), ('done', 'a', {'code': 'done', 'message': 'shown'}))
        self.assertFalse(self.focus.claim({'nonce': 'nonce-0001', 'watcherId': 'c', 'visible': True})['claimed'])

    def test_a_hidden_page_waits_for_a_visible_one_then_may_claim(self):
        self.focus.request(ask())
        self.assertEqual(self.focus.claim({'nonce': 'nonce-0001', 'watcherId': 'h', 'visible': False})['claimed'], False)
        self.clock[0] += VISIBLE_HEADSTART
        self.assertTrue(self.focus.claim({'nonce': 'nonce-0001', 'watcherId': 'h', 'visible': False})['claimed'])

    def test_no_tab_keeps_it_pending_until_a_page_with_the_tab_claims_or_it_expires_saying_which_folder(self):
        self.focus.request(ask())
        self.assertTrue(self.focus.report({'nonce': 'nonce-0001', 'watcherId': 'a', 'code': 'no-tab', 'folders': ['/home/dan']})['accepted'])
        r = self.state()
        self.assertEqual((r['state'], r['missingTab']['folders']), ('pending', ['/home/dan']))
        self.clock[0] += 20
        self.assertTrue(self.focus.claim({'nonce': 'nonce-0001', 'watcherId': 'a', 'visible': True})['claimed'])   # Dan opened it
        self.focus.request(ask('nonce-0002'))
        self.focus.report({'nonce': 'nonce-0002', 'watcherId': 'a', 'code': 'no-tab', 'folders': []})
        self.clock[0] += PENDING_SECONDS
        r = self.state()
        self.assertEqual((r['state'], r['outcome']['code']), ('expired', 'no-tab'))
        self.assertIn(DIR, r['outcome']['message'])

    def test_no_page_at_all_is_said_while_waiting_and_on_expiry_and_a_silent_claimer_fails(self):
        self.focus.request(ask())
        self.assertFalse(self.state()['noPage'])
        self.clock[0] += NO_WATCHER_SECONDS
        self.assertTrue(self.state()['noPage'])
        self.focus.snapshot('page-1')
        self.assertFalse(self.state()['noPage'])
        self.assertEqual(self.state()['pagesSeen'], 1)
        self.clock[0] += PENDING_SECONDS
        self.assertEqual(self.state()['outcome']['code'], 'no-page')
        self.focus.request(ask('nonce-0002'))
        self.focus.claim({'nonce': 'nonce-0002', 'watcherId': 'a', 'visible': True})
        self.clock[0] += CLAIMED_SECONDS
        self.assertEqual((self.state()['state'], self.state()['outcome']['code']), ('failed', 'silent'))

    def test_a_new_request_supersedes_and_fields_are_strict(self):
        self.focus.request(ask())
        self.focus.request(ask('nonce-0002'))
        self.assertEqual(self.state()['nonce'], 'nonce-0002')
        with self.assertRaises(ValueError):
            self.focus.request(ask('nonce-0002'))                       # a nonce is used once
        for bad in (ask('nonce-0003', sessionId='cc-44b3fc58-2503-4b76-8a39-f6b379756814'), ask('nonce-0003', sessionId='../credential'),
                    ask('nonce-0003', directory='relative'), ask('nonce-0003', directory='/x' * 600), ask('nonce-0003', extra=1),
                    ask('n'), ask('nonce-0003', parentId='bad'), ask('nonce-0003', title='x' * 401), 'not an object'):
            with self.assertRaises(ValueError):
                self.focus.request(bad)
        for bad in ({'nonce': 'nonce-0002', 'watcherId': 'bad id!', 'visible': True}, {'nonce': 'nonce-0002', 'watcherId': 'a', 'visible': 'yes'}):
            with self.assertRaises(ValueError):
                self.focus.claim(bad)
        for bad in ({'nonce': 'nonce-0002', 'watcherId': 'a', 'code': 'clicked'}, {'nonce': 'nonce-0002', 'watcherId': 'a', 'code': 'done', 'message': 'm' * 601},
                    {'nonce': 'nonce-0002', 'watcherId': 'a', 'code': 'no-tab', 'folders': ['/x'] * 33}):
            with self.assertRaises(ValueError):
                self.focus.report(bad)


class SessionFolder(unittest.TestCase):
    def test_rows_and_glances_carry_the_session_folder_the_button_matches_tabs_on(self):
        from opencode_programs import OpenCodePrograms, program_rows
        from test_pane_program_tree import FakeOpenCode, oc_session
        helper = oc_session(SID, '[ap:UHZREM]-001 ▸ ⚡ quick helper · [ap:WB93JF]', {'kind': 'helper', 'program': '[ap:UHZREM]'},
                            created=NOW - 60, location={'directory': DIR})
        odd = oc_session('ses_relative001', '[ap:UHZREM]-001 ▸ ⚡ quick helper · x', {'kind': 'helper', 'program': '[ap:UHZREM]'},
                         created=NOW - 60, location={'directory': 'relative/dir'})
        rows = {r['id']: r for r in program_rows([helper, odd], set(), NOW)}
        self.assertEqual(rows[SID]['directory'], DIR)
        self.assertNotIn('directory', rows['ses_relative001'])
        glance = OpenCodePrograms(request=FakeOpenCode([helper])).glance(SID)
        self.assertEqual((glance['directory'], glance['parentId']), (DIR, None))


class Routes(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.focus_requests = FocusRequests()
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()

    def request(self, route, method='GET', headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=15)
        data = json.dumps(body).encode() if body is not None else None
        connection.request(method, route, body=data, headers={**({'Content-Length': str(len(data))} if data else {}), **(headers or {})})
        response = connection.getresponse()
        reply = json.loads(response.read())
        connection.close()
        return response.status, reply, response.getheader('Access-Control-Allow-Origin')

    def test_the_pane_posts_and_polls_a_codenomad_page_polls_claims_and_reports(self):
        pane = {'Content-Type': 'application/json'}
        nomad = {'Origin': 'http://127.0.0.1:9899', 'Content-Type': 'text/plain'}
        status, reply, _ = self.request('/api/focus-request', 'POST', pane, ask())
        self.assertEqual((status, reply['request']['state']), (200, 'pending'))
        status, reply, cors = self.request('/api/focus-request?watcherId=page-1', headers={'Origin': 'http://127.0.0.1:9899'})
        self.assertEqual((status, reply['request']['sessionId'], cors), (200, SID, 'http://127.0.0.1:9899'))
        status, reply, cors = self.request('/api/focus-request/claim', 'POST', nomad, {'nonce': 'nonce-0001', 'watcherId': 'page-1', 'visible': True})
        self.assertEqual((status, reply, cors), (200, {'claimed': True}, 'http://127.0.0.1:9899'))
        status, reply, _ = self.request('/api/focus-request/report', 'POST', nomad, {'nonce': 'nonce-0001', 'watcherId': 'page-1', 'code': 'done', 'message': 'shown in CodeNomad'})
        self.assertEqual((status, reply), (200, {'accepted': True}))
        status, reply, cors = self.request('/api/focus-request')
        self.assertEqual((status, reply['request']['state'], cors), (200, 'done', None))

    def test_who_may_do_what(self):
        pane = {'Content-Type': 'application/json'}
        nomad = {'Origin': 'http://127.0.0.1:9899', 'Content-Type': 'text/plain'}
        for route, method, headers, body, code in (
                ('/api/focus-request', 'POST', {**pane, 'Origin': 'http://127.0.0.1:9899'}, ask(), 403),   # CodeNomad cannot ask
                ('/api/focus-request', 'POST', {**pane, 'Origin': 'http://evil.example'}, ask(), 403),
                ('/api/focus-request', 'POST', {'Content-Type': 'text/plain'}, ask(), 403),
                ('/api/focus-request', 'POST', pane, ask(sessionId='nope'), 400),
                ('/api/focus-request', 'GET', {'Origin': 'http://evil.example'}, None, 403),
                ('/api/focus-request?x=1', 'GET', {}, None, 400),
                ('/api/focus-request', 'GET', {'Origin': 'http://127.0.0.1:9899'}, None, 400),            # a page names itself
                ('/api/focus-request/claim', 'POST', {'Content-Type': 'text/plain'}, {'nonce': 'nonce-0001', 'watcherId': 'a', 'visible': True}, 403),
                ('/api/focus-request/claim', 'POST', {**nomad, 'Content-Type': 'application/json'}, {'nonce': 'nonce-0001', 'watcherId': 'a', 'visible': True}, 403),
                ('/api/focus-request/report', 'POST', nomad, {'nonce': 'nonce-0001', 'watcherId': 'a', 'code': 'sent'}, 400),
                ('/api/focus-request/claim', 'GET', nomad, None, 405),
                ('/api/focus-request', 'OPTIONS', {'Origin': 'http://127.0.0.1:9899'}, None, 403)):
            status, _, _ = self.request(route, method, headers, body)
            self.assertEqual(status, code, (route, method, headers))


if __name__ == '__main__':
    unittest.main()
