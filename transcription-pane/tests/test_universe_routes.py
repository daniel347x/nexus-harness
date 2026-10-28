"""U2.2 route edges: the ★ pin store (/api/pins) behind the /api/claude/rename origin guard, and
the two inventory routes' page-local view= selector (week|month|all).

Every test injects its own scratch SessionPins, and the settings loader points at a scratch
config before `server` is imported, so the real pins file is never read or written. No live
host, OpenCode service or pane process is involved: the readers are recording stand-ins.
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

_SCRATCH = tempfile.mkdtemp(prefix='u2-2-routes-')
_PREVIOUS_CONFIG = os.environ.get('OC_SESSION_HISTORY_CONFIG')
# server builds its ONE UniverseEvidence (and its SessionPins) at import: hand it a scratch
# settings file so the singleton's pin store can never name the real one, then put the
# process environment back so no other module inherits this test's file.
os.environ['OC_SESSION_HISTORY_CONFIG'] = str(Path(_SCRATCH) / 'history.json')
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import server  # noqa: E402
    from claude_live import ClaudeFacade  # noqa: E402
    from oc_pins import SessionPins  # noqa: E402
finally:
    if _PREVIOUS_CONFIG is None:
        os.environ.pop('OC_SESSION_HISTORY_CONFIG', None)
    else:
        os.environ['OC_SESSION_HISTORY_CONFIG'] = _PREVIOUS_CONFIG


def tearDownModule():
    shutil.rmtree(_SCRATCH, ignore_errors=True)


EMPTY_SESSIONS = {"schemaVersion": 1, "backend": "claude", "sessions": [], "diagnostics": [],
                  "hasMore": False, "nextCursor": None, "status": "complete", "listWindowDays": None}


class RecordingFacade(ClaudeFacade):
    """isinstance(ClaudeFacade) inventory stand-in that records the filters it was handed."""

    def __init__(self):
        # Keep this an inert recorder, but initialize the facade's real per-thread
        # observation scope now used by the Handler. object() can never call IPC.
        super().__init__(types.SimpleNamespace(lock=threading.RLock()), host_client=object())
        self.store_ref = 'fixture-host-store'
        self.calls = []

    def sessions(self, **args):
        self.calls.append(args)
        return dict(EMPTY_SESSIONS)


class RecordingNative:
    """A native-only (non-facade) history reader: the view must never reach it."""

    def __init__(self):
        self.calls = []

    def sessions(self, **args):
        self.calls.append(args)
        return {"schemaVersion": 1, "backend": "claude", "sessions": [], "diagnostics": [],
                "hasMore": False, "nextCursor": None, "status": "complete"}

    # The route's dispatch table is built eagerly, so a reader stand-in names every method.
    def events(self, **args):
        raise AssertionError('not this route')

    def subagents(self, **args):
        raise AssertionError('not this route')

    def context(self, **args):
        raise AssertionError('not this route')

    def artifact(self, **args):
        raise AssertionError('not this route')

    def snapshot(self, **args):
        raise AssertionError('not this route')


class RecordingPrograms:
    """The OpenCode inventory stand-in: records programs(view=…) while the route is under test."""

    def __init__(self):
        self.calls = []

    def programs(self, **args):
        self.calls.append(args)
        return {"schemaVersion": 1, "backend": "opencode", "status": "complete", "sessions": []}


class Routes(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='u2-2-routes-store-')
        self.pins_path = Path(self.home) / 'state' / 'session-pins.json'
        self.pins = SessionPins(path=self.pins_path)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.session_pins = self.pins
        self.facade = RecordingFacade()
        self.httpd.claude_reader = self.facade
        self.programs = RecordingPrograms()
        self.httpd.opencode_programs = self.programs
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._close)

    def _close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        shutil.rmtree(self.home, ignore_errors=True)

    def request(self, route, method='GET', headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=15)
        connection.request(method, route, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        reply = json.loads(raw) if raw else None
        connection.close()
        return response.status, reply

    def post_pin(self, payload, headers=None):
        body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
        return self.request('/api/pins', 'POST', headers or {'Content-Type': 'application/json'}, body)


class Pins(Routes):
    SESSION = 'session:claude:cc-11111111-2222-4333-8444-555555555555'
    SUBAGENT = 'session:claude:cc-11111111-2222-4333-8444-555555555555/agent-a1bc_2-x'
    PROGRAM = 'program:UHZREM'
    OPENCODE = 'session:opencode:ses_abcdef123456'

    def test_a_missing_store_answers_an_empty_pin_set(self):
        status, reply = self.request('/api/pins')
        self.assertEqual((status, reply), (200, {'schemaVersion': 1, 'pins': {}}))
        self.assertFalse(self.pins_path.exists())        # a read never creates the store

    def test_session_program_and_subagent_pins_set_then_unset(self):
        keys = (self.SESSION, self.SUBAGENT, self.PROGRAM, self.OPENCODE)
        for key in keys:
            status, reply = self.post_pin({'schemaVersion': 1, 'key': key, 'pinned': True})
            self.assertEqual(status, 200, key)
            self.assertEqual(set(reply), {'schemaVersion', 'pins'})
            self.assertEqual(reply['schemaVersion'], 1)
            self.assertIn(key, reply['pins'])
            self.assertIs(type(reply['pins'][key]['pinned_at']), float)
            self.assertEqual(reply['pins'][key], self.pins.get(key))
        status, reply = self.request('/api/pins')
        self.assertEqual((status, sorted(reply['pins'])), (200, sorted(keys)))
        for key in keys:
            status, reply = self.post_pin({'schemaVersion': 1, 'key': key, 'pinned': False})
            self.assertEqual(status, 200, key)
            self.assertNotIn(key, reply['pins'])
        self.assertEqual(self.pins.all(), {})

    def test_a_malformed_key_is_a_400_and_writes_nothing(self):
        for key in ('', 'UHZREM', 'cc-11111111-2222-4333-8444-555555555555', 'program:UHZRE',
                    'program:uhzrem', 'session:claude:cc-NOT-A-UUID', 'session:opencode:ses_',
                    'session:claude:cc-11111111-2222-4333-8444-555555555555/agent-'):
            status, reply = self.post_pin({'schemaVersion': 1, 'key': key, 'pinned': True})
            self.assertEqual((status, reply['status']), (400, 'schema'), key)
        self.assertEqual(self.pins.all(), {})
        self.assertFalse(self.pins_path.exists())

    def test_a_malformed_write_schema_is_a_400(self):
        payloads = ({'schemaVersion': 2, 'key': self.PROGRAM, 'pinned': True},
                    {'schemaVersion': True, 'key': self.PROGRAM, 'pinned': True},   # bool is not the int 1
                    {'schemaVersion': 1, 'key': self.PROGRAM},                      # no pinned
                    {'schemaVersion': 1, 'key': self.PROGRAM, 'pinned': 'true'},    # not a Boolean
                    {'schemaVersion': 1, 'key': self.PROGRAM, 'pinned': 1},
                    {'schemaVersion': 1, 'key': self.PROGRAM, 'pinned': True, 'extra': 1},
                    {'key': self.PROGRAM, 'pinned': True},
                    ['schemaVersion', 1, self.PROGRAM, True])
        for payload in payloads:
            status, reply = self.post_pin(payload)
            self.assertEqual((status, reply['status']), (400, 'schema'), payload)
        self.assertEqual(self.pins.all(), {})
        # Not JSON at all is the bounded body's own 400.
        status, _ = self.request('/api/pins', 'POST', {'Content-Type': 'application/json'}, b'not json')
        self.assertEqual(status, 400)

    def test_only_a_same_origin_json_post_without_query_writes(self):
        payload = json.dumps({'schemaVersion': 1, 'key': self.PROGRAM, 'pinned': True}).encode()
        for route, method, headers, code in (
                ('/api/pins', 'GET', {'Origin': 'http://evil.example'}, 403),
                ('/api/pins', 'POST', {'Origin': 'http://evil.example', 'Content-Type': 'application/json'}, 403),
                ('/api/pins?x=1', 'POST', {'Content-Type': 'application/json'}, 403),
                ('/api/pins', 'POST', {'Content-Type': 'text/plain'}, 403),
                ('/api/pins', 'POST', {'Content-Type': 'text/html'}, 403),
                ('/api/pins?x=1', 'GET', {}, 400),
                ('/api/pins', 'PUT', {'Content-Type': 'application/json'}, 405),
                ('/api/pins', 'OPTIONS', {'Content-Type': 'application/json'}, 403),
                ('/api/pin', 'GET', {}, 404)):
            status, _ = self.request(route, method, headers, payload if method in ('POST', 'PUT') else None)
            self.assertEqual(status, code, (route, method, headers))
        self.assertEqual(self.pins.all(), {})

    def test_the_existing_64_kib_body_bound_still_refuses_an_oversized_write(self):
        body = json.dumps({'schemaVersion': 1, 'key': self.PROGRAM, 'pinned': True,
                           'pad': 'x' * (64 * 1024)}).encode()
        status, reply = self.post_pin(body)
        self.assertEqual((status, reply['status']), (413, 'body-limit'))
        self.assertEqual(self.pins.all(), {})

    def test_the_route_falls_back_to_the_one_server_store_when_none_is_injected(self):
        del self.httpd.session_pins
        scratch = SessionPins(path=Path(self.home) / 'fallback-pins.json')
        original = server._session_universe
        server._session_universe = types.SimpleNamespace(pins=scratch)
        try:
            status, reply = self.post_pin({'schemaVersion': 1, 'key': self.PROGRAM, 'pinned': True})
            self.assertEqual((status, list(reply['pins'])), (200, [self.PROGRAM]))
            self.assertEqual(list(scratch.all()), [self.PROGRAM])
        finally:
            server._session_universe = original


class ClaudeView(Routes):
    def test_the_page_local_view_reaches_the_facade_inventory(self):
        for view in ('week', 'month', 'all'):
            status, _ = self.request(f'/api/claude/sessions?details=0&native=0&view={view}')
            self.assertEqual(status, 200, view)
        self.assertEqual([call.get('view') for call in self.facade.calls], ['week', 'month', 'all'])

    def test_no_view_is_left_to_the_facades_default(self):
        status, _ = self.request('/api/claude/sessions?details=0')
        self.assertEqual(status, 200)
        self.assertNotIn('view', self.facade.calls[-1])
        # details is the route's own switch, never a facade argument.
        self.assertEqual(self.facade.calls[-1], {})

    def test_the_manager_picker_and_the_live_poll_ignore_the_view(self):
        status, _ = self.request('/api/claude/sessions?role=Manager&view=month')
        self.assertEqual(status, 200)
        self.assertNotIn('view', self.facade.calls[-1])
        self.assertEqual(self.facade.calls[-1].get('role'), 'Manager')
        status, _ = self.request('/api/claude/sessions?live=1&view=all')
        self.assertEqual(status, 200)
        self.assertNotIn('view', self.facade.calls[-1])
        self.assertIs(self.facade.calls[-1].get('live'), True)

    def test_an_unknown_empty_or_repeated_view_is_a_400(self):
        for query in ('view=day', 'view=', 'view=Week', 'view=week&view=month', 'view=week&x=1'):
            status, reply = self.request(f'/api/claude/sessions?{query}')
            self.assertEqual((status, reply['status']), (400, 'schema'), query)
        self.assertEqual(self.facade.calls, [])

    def test_a_native_only_reader_never_sees_the_view(self):
        native = RecordingNative()
        self.httpd.claude_reader = native
        status, _ = self.request('/api/claude/sessions?view=month')
        self.assertEqual(status, 200)
        self.assertEqual(len(native.calls), 1)
        self.assertNotIn('view', native.calls[-1])


class OpencodeView(Routes):
    def test_no_query_keeps_the_managers_all_answer(self):
        status, _ = self.request('/api/opencode/programs')
        self.assertEqual(status, 200)
        self.assertEqual(self.programs.calls[-1], {})   # the reader's own default, all

    def test_a_strict_single_view_is_forwarded(self):
        for view in ('week', 'month', 'all'):
            status, _ = self.request(f'/api/opencode/programs?view={view}')
            self.assertEqual(status, 200, view)
        self.assertEqual([call.get('view') for call in self.programs.calls], ['week', 'month', 'all'])

    def test_anything_else_is_still_a_400(self):
        for query in ('x=1', 'view=day', 'view=', 'view', 'view=week&view=month', 'view=week&x=1'):
            status, reply = self.request(f'/api/opencode/programs?{query}')
            self.assertEqual((status, reply['status']), (400, 'schema'), query)
        self.assertEqual(self.programs.calls, [])


if __name__ == '__main__':
    unittest.main()
