"""Current Programs reads never walk native history or scan hidden host transcripts."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_live import ClaudeFacade
from claude_reader import ClaudeReader
import server
from test_claude_live import InertHost, UUID


class ProgramsPriority(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = ClaudeReader([{'id': 'fixture', 'root': self.temp.name}])
        self.host = InertHost()
        self.host.record['primary_turn_open'] = True
        self.reader = ClaudeFacade(self.history, self.host)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.http.claude_reader = self.reader
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(5)

    def get(self, path, **headers):
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=3)
        conn.request('GET', path, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read())
        conn.close()
        return response.status, data

    def test_summary_does_no_native_walk_or_host_ledger_and_keeps_live_identity(self):
        with patch.object(self.history, 'sessions', side_effect=AssertionError('native history walked')) as native, \
                patch.object(self.history, 'turn_ledger', side_effect=AssertionError('hidden transcript scanned')) as ledger:
            status, data = self.get('/api/claude/sessions?native=0&details=0')
        self.assertEqual(status, 200)
        self.assertFalse(data['hasMore'])
        self.assertEqual(data['sessions'][0]['uuid'], UUID)
        self.assertIs(data['sessions'][0]['liveState']['primary_turn_open'], True)
        self.assertNotIn('turnFacts', data['sessions'][0])
        native.assert_not_called()
        ledger.assert_not_called()
        self.assertEqual([name for name, _ in self.host.calls], ['list', 'health', 'list'])
        self.assertEqual([p['window_days'] for name, p in self.host.calls if name == 'list'], [7, 90])

    def test_host_outage_does_not_fall_back_to_an_expensive_history_scan(self):
        self.host.offline = True
        with patch.object(self.history, 'sessions', side_effect=AssertionError('native fallback')) as native:
            status, data = self.get('/api/claude/sessions?native=0&details=0')
        self.assertEqual((status, data['status']), (502, 'read-failure'))
        native.assert_not_called()

    def test_new_flags_and_turns_keep_schema_and_origin_policy(self):
        for query in ('native=2', 'native=0&native=1', 'details=oops', 'live=yes', 'native=0&storeRef=foreign'):
            self.assertEqual(self.get('/api/claude/sessions?' + query)[0], 400)
        self.assertEqual(self.get('/api/claude/turns?uuid=bad')[0], 400)
        self.assertEqual(self.get('/api/claude/turns?uuid=' + UUID, Origin='https://foreign.invalid')[0], 403)
        with patch.object(self.history, 'host_turn_ledger') as read:
            read.return_value.summary.return_value = {'status': 'complete', 'firstAt': 10, 'lastAt': 20}
            status, data = self.get('/api/claude/turns?uuid=' + UUID)
        self.assertEqual((status, data['uuid'], data['turnFacts']['lastAt']), (200, UUID, 20))
        read.assert_called_once_with(UUID, None)

    def test_activity_filters_terminal_history_before_paging_and_never_enters_native_history(self):
        ended = {**self.host.record, 'execution_id': 'cc-22222222-2222-4222-8222-222222222222',
                 'state': 'exited', 'owner': 'gone'}
        with patch.object(self.reader, '_host_list', return_value=[ended, self.host.record]), \
                patch.object(self.history, 'sessions', side_effect=AssertionError('native history')):
            status, data = self.get('/api/claude/sessions?live=1&details=0')
        self.assertEqual(status, 200)
        self.assertFalse(data['hasMore'])
        self.assertEqual([row['executionId'] for row in data['sessions']], [self.host.record['execution_id']])

    def test_active_work_and_ancestors_lead_newer_historical_sessions(self):
        def record(n, role='worker', **fields):
            return {'execution_id': 'cc-%08d-0000-4000-8000-000000000000' % n,
                    'native_uuid': '%08d-0000-4000-8000-000000000000' % n,
                    'generation': 1, 'role': role, 'state': 'ready', 'owner': 'owned',
                    'createdAt': n, 'labels': {}, **fields}
        conductor = record(1, 'conductor', program='[ap:ACTIVE]', labels={'kind': 'conductor', 'registry_n': 1})
        lead = record(2, program='[ap:ACTIVE]', labels={'kind': 'seat', 'conductor_id': conductor['execution_id']})
        helper = record(3, 'helper', primary_turn_open=True, last_activity_at=500,
                        program='[ap:ACTIVE]', labels={'kind': 'helper', 'parent_seat': lead['execution_id']})
        recent = record(4, primary_turn_open=True, last_activity_at=600)
        history = [record(n, state='exited', owner='gone') for n in range(100, 200)]
        with patch.object(self.reader, '_record_created', side_effect=lambda row: row['createdAt']):
            ordered = self.reader._order_records(history + [conductor, lead, helper, recent])
        self.assertEqual([r['execution_id'] for r in ordered[:4]],
                         [r['execution_id'] for r in (recent, helper, lead, conductor)])
        self.assertEqual(ordered[4]['createdAt'], 199)


if __name__ == '__main__':
    unittest.main()
