"""Host-role Manager selection before paging, through real facade and ephemeral HTTP."""
import http.client
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
from test_claude_lockout_batch_a import FleetHost, host_record


def record(number, **fields):
    return host_record(str(uuid.UUID(int=number)), **fields)


class PhoneInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.history = ClaudeReader([{'id': 'fixture', 'root': self.temp.name, 'native': True}])
        self.host = FleetHost([])
        self.reader = ClaudeFacade(self.history, self.host)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.httpd.claude_reader = self.reader
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.temp.cleanup()

    def install(self, records):
        self.host = FleetHost(records)
        self.reader.client = self.reader.command_client = self.host

    def request(self, **query):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
        connection.request('GET', '/api/claude/sessions?' + urlencode(query))
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def native_transcript(self, ident):
        folder = Path(self.temp.name) / 'project'
        folder.mkdir(exist_ok=True)
        (folder / (ident + '.jsonl')).write_text(json.dumps({'type': 'user', 'uuid': 'native-message',
            'sessionId': ident, 'cwd': '/fixture/work', 'timestamp': '2026-01-01T00:00:00Z',
            'message': {'role': 'user', 'content': 'Manager-Session-999 native history'}}) + '\n', encoding='utf-8')

    def test_exact_host_roles_only_no_display_name_worker_or_native_twins(self):
        manager = record(1, started_at=100)
        worker = record(2, role='worker', started_at=200, label='Manager-Session-999')
        worker['displayRole'] = 'Manager'
        worker['labels'] = {'title': 'Manager', 'role': 'Manager', 'seat_role': 'Manager'}
        self.install([worker, record(3, role='helper'), record(4, role='manager'), manager])
        self.native_transcript(manager['native_uuid'])  # host twin must also stay out
        self.native_transcript(str(uuid.UUID(int=99)))
        with patch.object(self.history, 'sessions', wraps=self.history.sessions) as history_scan:
            code, result = self.request(role='Manager')
            self.assertEqual(code, 200, result)
            history_scan.assert_not_called()
        self.assertEqual([r['executionId'] for r in result['sessions']], [manager['execution_id']])
        self.assertEqual([(r['source'], r['role']) for r in result['sessions']], [('host', 'Manager')])
        self.assertFalse(result['hasMore'])
        self.assertIsNone(result['nextCursor'])
        # The native files are genuinely discoverable; unfiltered behavior still includes them.
        query = {'storeRef': self.history.stores['fixture']['ref']}
        native_rows = []
        while True:
            code, native = self.request(**query)
            self.assertEqual(code, 200, native)
            native_rows.extend(native['sessions'])
            if not native['hasMore']:
                break
            query['cursor'] = native['nextCursor']
        self.assertEqual({r['uuid'] for r in native_rows}, {manager['native_uuid'], str(uuid.UUID(int=99))})

    def test_newest_sleeping_manager_behind_more_than_64_workers_is_on_first_page(self):
        workers = [record(n + 100, role='worker', started_at=500 + n) for n in range(80)]
        sleeping = record(1, state='collected', owner='gone', started_at=1000)
        live = record(2, started_at=100)
        self.install(workers + [live, sleeping])
        code, result = self.request(role='Manager')
        self.assertEqual(code, 200, result)
        self.assertEqual([r['executionId'] for r in result['sessions']],
                         [sleeping['execution_id'], live['execution_id']])
        self.assertEqual([r['lifecycle'] for r in result['sessions']], ['sleeping', 'live'])
        self.assertEqual([r['createdAt'] for r in result['sessions']], [1000, 100])
        self.assertFalse(result['hasMore'])
        self.assertFalse(self.host.writes)
        self.assertTrue(all(m in ('list', 'health') for m, _ in self.host.calls))

    def test_newest_created_order_across_live_sleeping_pending_and_ended(self):
        records = [record(1, started_at=100), record(2, state='collected', owner='gone', started_at=400),
                   record(3, state='exited', owner='gone', started_at=300),
                   record(4, state='initializing', initialized=False, started_at=200)]
        self.install(records)
        code, result = self.request(role='Manager')
        self.assertEqual(code, 200, result)
        self.assertEqual([r['createdAt'] for r in result['sessions']], [400, 300, 200, 100])
        self.assertEqual([r['lifecycle'] for r in result['sessions']], ['sleeping', 'ended', 'pending', 'live'])
        # Unfiltered live-first ordering stays independent of phone ordering.
        self.assertEqual(self.request()[1]['sessions'][0]['executionId'], records[0]['execution_id'])

    def test_host_failure_is_an_error_never_empty_success_or_native_fallback(self):
        self.native_transcript(str(uuid.UUID(int=99)))
        with patch.object(self.host, 'call', side_effect=OSError('fixture host unavailable')):
            with patch.object(self.history, 'sessions', wraps=self.history.sessions) as history_scan:
                code, result = self.request(role='Manager')
                self.assertEqual(code, 502, result)
                self.assertEqual(result['status'], 'read-failure')
                self.assertIn('host unavailable', result['error'])
                history_scan.assert_not_called()

    def test_more_than_64_managers_continuations_bound_to_role_and_independent_scans(self):
        self.install([record(n + 1, started_at=n + 1) for n in range(70)])
        first = self.request(role='Manager')[1]
        self.assertEqual(len(first['sessions']), 64)
        self.assertTrue(first['hasMore'])
        cursor = first['nextCursor']
        self.assertEqual(self.request(cursor=cursor)[0], 400)  # cannot silently become an all-role scan
        second = self.request(role='Manager')[1]              # independent snapshot, not a reset
        unfiltered = self.request()[1]
        self.assertEqual(self.request(role='Manager', cursor=unfiltered['nextCursor'])[0], 400)
        with patch.object(self.history, 'sessions', wraps=self.history.sessions) as history_scan:
            code, tail = self.request(role='Manager', cursor=cursor)
            self.assertEqual(code, 200, tail)
            history_scan.assert_not_called()
        combined = first['sessions'] + tail['sessions']
        self.assertEqual([r['createdAt'] for r in combined], list(range(70, 0, -1)))
        self.assertFalse(tail['hasMore'])
        self.assertIsNone(tail['nextCursor'])
        self.assertEqual(self.request(role='Manager', cursor=second['nextCursor'])[1]['sessions'], tail['sessions'])
        self.assertEqual(self.request(role='Manager', cursor='not-a-host-cursor')[0], 409)

    def test_role_schema_is_exact_nonempty_and_singleton(self):
        for query in ('role=worker', 'role=manager', 'role=', 'role=Manager&role=Manager'):
            with self.subTest(query=query):
                connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
                connection.request('GET', '/api/claude/sessions?' + query)
                response = connection.getresponse()
                result = json.loads(response.read())
                connection.close()
                self.assertEqual(response.status, 400, result)
                self.assertEqual(result['status'], 'schema')
        self.assertFalse(self.host.calls)


if __name__ == '__main__':
    unittest.main()
