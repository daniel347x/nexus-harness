"""New Manager from the pane: one named fresh conversation through the host's own launch path.

Inert transport only: a recording fake host client, a fake launcher built on the operator
launcher's exact request builder, temporary name stores. No production socket, process, prompt
publication or inference.
"""
import argparse
import http.client
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import claude_launch
from claude_launch import DEFAULT_FIRST_MESSAGES, FirstMessages, SessionNames, launch_manager, next_session_name
from claude_reader import ClaudeReader, ReaderError
from claude_live import ClaudeFacade
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from claude_manager_headless import MANAGER_CWD, build_launch_params

NEW = '12345678-1234-4234-8234-123456789abc'
OLD = '87654321-4321-4321-8321-cba987654321'


class FakeHost:
    def __init__(self, *, down=0, registered=False):
        self.calls, self.down, self.registered = [], down, registered
        self.records = {}

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == 'health':
            if self.down:
                self.down -= 1
                raise ConnectionRefusedError('host socket refused')
            return {'protocol_version': 1}
        if method == 'launch':
            if self.registered or params['native_uuid'] in self.records:
                raise ReaderError(409, 'refused', 'UUID already registered; explicit resume required')
            record = {'execution_id': 'cc-' + params['native_uuid'], 'native_uuid': params['native_uuid'],
                      'generation': 1, 'role': params['role'], 'state': 'initializing', 'owner': 'host',
                      'cwd': params['cwd']}
            self.records[params['native_uuid']] = record
            return record
        if method == 'state':
            uuid = params['execution_id'][3:]
            return self.records.get(uuid) or {'execution_id': params['execution_id'], 'native_uuid': uuid,
                                              'generation': 1, 'role': 'Manager', 'state': 'ready'}
        raise AssertionError('unexpected host method ' + method)


def fake_run_launch(names, seen):
    """The launcher's exact launch request (role Manager, resume false, default CWD), no prompt I/O."""
    def run(client, args):
        seen.append({'name_at_launch': names.get(args.uuid), 'args': vars(args)})
        record = client.call('launch', **build_launch_params(
            'COMPOSED MANAGER PROMPT', native_uuid=args.uuid, cwd=args.cwd or MANAGER_CWD,
            model=args.model, effort=args.effort))
        return {'status': 'launch_requested', 'sent': False, 'warnings': [], 'record': record}
    return run


class NamesAndDefaults(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state' / 'session-names.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_names_are_private_durable_and_validated(self):
        names = SessionNames(self.path)
        self.assertIsNone(names.get(NEW))
        self.assertEqual(names.set(NEW, '  Manager-Session-183  '), 'Manager-Session-183')
        self.assertEqual(SessionNames(self.path).get(NEW), 'Manager-Session-183')
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        for bad in ('', ' ', 'x' * 81, 'tab\tname', 'line\nbreak'):
            with self.assertRaises(ValueError):
                names.set(OLD, bad)
        with self.assertRaises(ValueError):
            names.set('not-a-uuid', 'Manager-Session-9')

    def test_next_name_is_one_above_the_highest_known_session(self):
        config = Path(self.temp.name) / '.oc_config.json'
        config.write_text(json.dumps({'manager_session': '182'}))
        self.assertEqual(next_session_name([], [], config), 'Manager-Session-183')
        self.assertEqual(next_session_name(['Manager-Session-183'], [], config), 'Manager-Session-184')
        self.assertEqual(next_session_name([], ['Manager-Session-190-a'], config), 'Manager-Session-191')
        self.assertEqual(next_session_name([], [], Path(self.temp.name) / 'missing.json'), 'Manager-Session-1')

    def test_first_messages_default_to_dans_opener_save_privately_and_restore(self):
        store = FirstMessages(Path(self.temp.name) / 'state' / 'first-messages.json')
        got = store.get()
        self.assertEqual((got['manager'], got['subSession']), (DEFAULT_FIRST_MESSAGES['manager'], DEFAULT_FIRST_MESSAGES['subSession']))
        self.assertEqual(got['custom'], {'manager': False, 'subSession': False})
        self.assertTrue(got['manager'].startswith('Greetings! Please node.read the description of this Anchor Dock:'))
        self.assertTrue(got['manager'].endswith("then this is going to be Manager-Session-{N}."))
        self.assertIn('sub-session {N}-{letter} of Manager-Session-{N}. You are a sub-session: read [ap:M8T0EO] first', got['subSession'])
        self.assertIn('oc.sh session sub --parent {N} --letter {letter}', got['subSession'])
        self.assertTrue(got['subSession'].endswith('\n\nReason for this sub-session: '))
        saved = store.set({'manager': 'Hi {N}'})
        self.assertEqual((saved['manager'], saved['custom']['manager']), ('Hi {N}', True))
        self.assertEqual(saved['subSession'], DEFAULT_FIRST_MESSAGES['subSession'])
        self.assertEqual(FirstMessages(store.path).get()['manager'], 'Hi {N}')  # durable, read by every page
        self.assertEqual(stat.S_IMODE(os.stat(store.path).st_mode), 0o600)
        for restore in ('', '   ', DEFAULT_FIRST_MESSAGES['manager']):
            store.set({'manager': 'Hi {N}'})
            self.assertEqual(store.set({'manager': restore})['custom']['manager'], False, repr(restore[:10]))
        for bad in ({}, {'other': 'x'}, {'manager': 5}, {'manager': 'x' * (claude_launch.FIRST_MESSAGE_LIMIT + 1)}):
            with self.assertRaises(ValueError):
                store.set(bad)
        store.path.write_text('{not json')
        self.assertEqual(store.get()['manager'], DEFAULT_FIRST_MESSAGES['manager'])  # a damaged file reads as the defaults


class LaunchObserverBudgetTests(unittest.TestCase):
    def assert_observer_budget(self, budget=None):
        calls = []

        class RecordingTransport:
            def call(self, method, *, reader_deadline=None, **params):
                # Like PaneHostClient, a missing per-call deadline defaults to 12 s;
                # a transport.reader_deadline attribute deliberately has no effect.
                deadline = reader_deadline if reader_deadline is not None else claude_launch.time.monotonic() + 12
                calls.append((method, deadline, params))
                return {'inert': True}

        transport = RecordingTransport()
        with patch('claude_live.PaneHostClient', return_value=transport), \
                patch.object(claude_launch.time, 'monotonic', return_value=1000):
            client = claude_launch.PaneLaunchClient() if budget is None else claude_launch.PaneLaunchClient(budget)
            for method in ('health', 'launch', 'state', 'launch'):
                self.assertEqual(client.call(method, native_uuid=NEW), {'inert': True})
        launch_budget = 120.0 if budget is None else budget
        self.assertEqual(calls, [(method, 1000 + (launch_budget if method == 'launch' else 12),
                                 {'native_uuid': NEW}) for method in ('health', 'launch', 'state', 'launch')])
        self.assertNotIn('reader_deadline', transport.__dict__)

    def test_lb1_launch_uses_120_seconds_and_other_methods_use_12(self):
        self.assert_observer_budget()

    def test_lb1_launch_uses_its_custom_budget_per_call(self):
        self.assert_observer_budget(45.5)


class LaunchFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.names = SessionNames(Path(self.temp.name) / 'names.json')

    def tearDown(self):
        self.temp.cleanup()

    def launch(self, host, **fields):
        seen = []
        values = dict(native_uuid=NEW, name='Manager-Session-183', model='claude-opus-5-5', effort='high')
        values.update(fields)
        result = launch_manager(names=self.names, client=host, run_launch=fake_run_launch(self.names, seen),
                                start_host=fields.pop('start_host', None) or self.starter, sleep=lambda s: None,
                                **{k: v for k, v in values.items() if k != 'start_host'})
        return result, seen

    def starter(self):
        self.started = getattr(self, 'started', 0) + 1

    def test_one_fresh_named_manager_launch_never_touches_another_session(self):
        host = FakeHost()
        result, seen = self.launch(host)
        launches = [p for m, p in host.calls if m == 'launch']
        self.assertEqual(len(launches), 1)
        self.assertEqual(launches[0], build_launch_params('COMPOSED MANAGER PROMPT', native_uuid=NEW,
            cwd=MANAGER_CWD, model='claude-opus-5-5', effort='high'))
        self.assertEqual((launches[0]['role'], launches[0]['resume']), ('Manager', False))
        self.assertEqual(seen[0]['name_at_launch'], 'Manager-Session-183')  # named before the launch
        self.assertEqual(result['executionId'], 'cc-' + NEW)
        self.assertEqual((result['name'], result['sent'], result['hostStarted']), ('Manager-Session-183', False, False))
        self.assertTrue(all(m in ('health', 'launch') for m, _ in host.calls))
        self.assertFalse(any(p.get('native_uuid') == OLD or p.get('execution_id') == 'cc-' + OLD for _, p in host.calls))

    def test_a_repeated_request_answers_the_first_launch_never_a_second_process(self):
        host = FakeHost()
        self.launch(host)
        again, _ = self.launch(host)
        self.assertTrue(again['alreadyLaunched'])
        self.assertEqual(len(host.records), 1)

    def test_a_stopped_host_is_started_first(self):
        host = FakeHost(down=2)
        result, _ = self.launch(host)
        self.assertEqual(self.started, 1)
        self.assertTrue(result['hostStarted'])
        self.assertEqual([m for m, _ in host.calls].count('launch'), 1)

    def test_a_host_that_cannot_start_launches_nothing_and_says_so(self):
        host = FakeHost(down=1)

        def broken():
            raise RuntimeError('claude_host.py start failed (exit 1): endpoint owned')
        with self.assertRaises(ReaderError) as caught:
            self.launch(host, start_host=broken)
        self.assertEqual(caught.exception.code, 502)
        self.assertIn('endpoint owned', str(caught.exception))
        self.assertNotIn('launch', [m for m, _ in host.calls])

    def test_bad_names_models_and_uuids_refuse_before_any_host_call(self):
        for fields in ({'name': ''}, {'name': 'a\nb'}, {'model': 'gpt-5'}, {'model': 'claude-opus-5-5[1m]'},
                       {'effort': 'turbo'}, {'native_uuid': NEW.upper()}, {'native_uuid': 'abc'}):
            host = FakeHost()
            with self.assertRaises(ReaderError) as caught:
                self.launch(host, **fields)
            self.assertEqual(caught.exception.code, 400, fields)
            self.assertEqual(host.calls, [], fields)

    def test_one_launch_at_a_time(self):
        host = FakeHost()
        claude_launch._launch_lock.acquire()
        try:
            with self.assertRaises(ReaderError) as caught:
                self.launch(host)
            self.assertEqual(caught.exception.code, 409)
        finally:
            claude_launch._launch_lock.release()
        self.assertEqual(host.calls, [])


class LaunchRoutes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.names = SessionNames(Path(self.temp.name) / 'names.json')
        self.host = FakeHost()
        self.reader = ClaudeFacade(ClaudeReader([{'id': 'fixture', 'root': self.temp.name}]), self.host, names=self.names)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.httpd.claude_launch_client = self.host
        self.httpd.claude_host_starter = lambda: None
        self.seen = []
        self.httpd.claude_run_launch = fake_run_launch(self.names, self.seen)
        self.httpd.first_messages = FirstMessages(Path(self.temp.name) / 'first-messages.json')
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join()
        self.temp.cleanup()

    def request(self, route, method='GET', data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=15)
        extra = dict(headers or {})
        if data is not None:
            data = json.dumps(data).encode()
            extra.setdefault('Content-Type', 'application/json')
        connection.request(method, route, body=data, headers=extra)
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def body(self, **fields):
        return {'schemaVersion': 1, 'backend': 'claude', 'nativeUuid': NEW, 'name': 'Manager-Session-183',
                'model': 'claude-opus-5-5', 'effort': 'high', **fields}

    def test_defaults_and_a_named_launch_show_up_as_the_row_title(self):
        status, defaults = self.request('/api/claude/launch-defaults')
        self.assertEqual(status, 200, defaults)
        self.assertEqual((defaults['model'], defaults['effort']), ('claude-opus-5-5', 'xhigh'))
        self.assertTrue(defaults['name'].startswith('Manager-Session-'))
        status, body = self.request('/api/claude/launch-manager', 'POST', self.body())
        self.assertEqual(status, 200, body)
        self.assertEqual((body['uuid'], body['name'], body['sent']), (NEW, 'Manager-Session-183', False))
        # The route's new default never overrides an explicit choice in the launch request.
        self.assertEqual(next(args for method, args in self.host.calls if method == 'launch')['effort'], 'high')
        state = self.reader._call('state', execution_id='cc-' + NEW)
        self.assertEqual(self.reader._label(state), 'Manager-Session-183')
        self.assertEqual(self.reader._inventory_record(state)['sessionLabel'], 'Manager-Session-183')

    def test_the_route_accepts_no_paths_prompts_or_extra_fields(self):
        for body in (self.body(cwd='/tmp'), self.body(system_prompt='x'), self.body(schemaVersion=2),
                     {k: v for k, v in self.body().items() if k != 'name'}):
            status, reply = self.request('/api/claude/launch-manager', 'POST', body)
            self.assertEqual(status, 400, reply)
        status, _ = self.request('/api/claude/launch-manager', 'GET')
        self.assertEqual(status, 403)
        status, _ = self.request('/api/claude/launch-manager', 'POST', self.body(), {'Origin': 'http://evil.example'})
        self.assertEqual(status, 403)
        self.assertNotIn('launch', [m for m, _ in self.host.calls])

    def test_the_first_messages_route_reads_and_saves_one_shared_copy(self):
        status, got = self.request('/api/claude/first-messages')
        self.assertEqual(status, 200, got)
        self.assertEqual((got['schemaVersion'], got['backend'], got['manager']), (1, 'claude', DEFAULT_FIRST_MESSAGES['manager']))
        status, got = self.request('/api/claude/first-messages', 'POST', {'schemaVersion': 1, 'backend': 'claude', 'subSession': 'Sub {N}-{letter}'})
        self.assertEqual((status, got['subSession'], got['custom']['subSession']), (200, 'Sub {N}-{letter}', True), got)
        self.assertEqual(self.request('/api/claude/first-messages')[1]['subSession'], 'Sub {N}-{letter}')
        for body in ({'schemaVersion': 1, 'backend': 'claude'}, {'schemaVersion': 1, 'backend': 'claude', 'prompt': 'x'},
                     {'schemaVersion': 2, 'backend': 'claude', 'manager': 'x'}, {'schemaVersion': 1, 'backend': 'claude', 'manager': 3}):
            self.assertEqual(self.request('/api/claude/first-messages', 'POST', body)[0], 400, body)
        self.assertEqual(self.request('/api/claude/first-messages?x=1')[0], 403)
        self.assertEqual(self.request('/api/claude/first-messages', 'POST', {'schemaVersion': 1, 'backend': 'claude', 'manager': 'x'},
                                      {'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/claude/first-messages')[1]['manager'], DEFAULT_FIRST_MESSAGES['manager'])
        self.assertEqual(self.host.calls, [])  # config only: no host call, launch or Send


class ListingHost(FakeHost):
    """FakeHost plus the host inventory, so a fresh launch can be read back from the list."""
    def call(self, method, **params):
        if method == 'list':
            self.calls.append((method, params))
            return [dict(record) for record in self.records.values()]
        return super().call(method, **params)


class PhoneLaunchRoute(LaunchRoutes):
    """The phone's ＋ New Manager reaches the desktop's own routes under /m's remote-origin policy."""
    ORIGIN, AUTHORITY, LOGIN = 'https://pane.example.ts.net', 'pane.example.ts.net', 'dan@example.test'  # fixture only

    def setUp(self):
        super().setUp()
        self.host = ListingHost()
        self.reader.client = self.httpd.claude_launch_client = self.host

    def phone(self, **extra):
        return {'Host': self.AUTHORITY, 'Origin': self.ORIGIN, 'Tailscale-User-Login': self.LOGIN,
                'Sec-Fetch-Site': 'same-origin', **extra}

    def test_the_phone_origin_launches_through_the_desktop_route_and_lists_the_new_manager_before_any_message(self):
        # Remote OFF: the Serve origin is refused like every other guarded route, nothing launches.
        self.assertEqual(self.request('/api/claude/launch-defaults', headers=self.phone())[0], 403)
        self.assertEqual(self.request('/api/claude/launch-manager', 'POST', self.body(), self.phone())[0], 403)
        self.assertEqual(self.host.calls, [])
        config = Path(self.temp.name) / 'pane-remote.json'
        config.write_text(json.dumps({'origin': self.ORIGIN, 'login': self.LOGIN}), encoding='utf-8')
        self.httpd.remote_origin_policy = server.load_remote_policy(config)
        # Remote ON: the same policy as /m and its Send, wrong identity still refused.
        self.assertEqual(self.request('/api/claude/launch-manager', 'POST', self.body(),
                                      self.phone(**{'Tailscale-User-Login': 'someone@else.test'}))[0], 403)
        status, defaults = self.request('/api/claude/launch-defaults', headers=self.phone())
        self.assertEqual(status, 200, defaults)
        self.assertEqual((defaults['model'], defaults['effort']), ('claude-opus-5-5', 'xhigh'))
        self.assertTrue(defaults['name'].startswith('Manager-Session-'))
        status, body = self.request('/api/claude/launch-manager', 'POST', self.body(), self.phone())
        self.assertEqual(status, 200, body)
        self.assertEqual((body['executionId'], body['name'], body['sent']), ('cc-' + NEW, 'Manager-Session-183', False))
        self.assertEqual([m for m, _ in self.host.calls].count('launch'), 1)
        # The phone's own Manager list: the new conversation is there at once, named, with
        # the identity a phone Send needs, though it has no transcript or message yet.
        status, listed = self.request('/api/claude/sessions?role=Manager', headers=self.phone())
        self.assertEqual(status, 200, listed)
        row, = [r for r in listed['sessions'] if r['executionId'] == 'cc-' + NEW]
        self.assertEqual((row['source'], row['role'], row['uuid'], row['generation'], row['title']),
                         ('host', 'Manager', NEW, 1, 'Manager-Session-183'))
        self.assertTrue(row['targetRef'] and row['sessionRef'])
        self.assertEqual([s['label'] for s in row['scopes']], ['main'])


if __name__ == '__main__':
    unittest.main()
