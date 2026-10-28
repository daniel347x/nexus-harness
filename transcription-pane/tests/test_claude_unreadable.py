"""S1: one explicit row-set damage fact, unchanged raw fallback, no cross-conversation state."""
import copy
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from test_claude_live import EX
from test_claude_slow_host import SlowClock, seeded
from test_claude_rows_pane import CheckedClient, normalized
import claude_live as live
from claude_reader import ClaudeReader
import server


class UnreadableRowsTests(unittest.TestCase):
    def facade(self):
        host = seeded(SlowClock(), rows=4, per_call=0)
        host.record['pending_escalations'] = []
        reader = live.ClaudeFacade(ClaudeReader([]), CheckedClient(host))
        row = reader.sessions()['sessions'][0]
        host.calls.clear()
        return host, reader, row

    def test_explicit_unreadable_rows_keep_the_exact_old_raw_path_and_clear_when_rebuilt(self):
        outputs = []
        for damaged in (False, True):
            host, reader, row = self.facade()
            host.status = 'unreadable' if damaged else 'complete'
            host.enabled = damaged  # ordinary baseline is the old host's raw path
            page = reader.events(targetRef=row['targetRef'])
            self.assertEqual(page.get('rowsUnreadable', False), damaged)
            self.assertEqual(page['rowSource'], 'fallback')
            self.assertEqual([event['text'] for event in page['events']], ['Dan', 'answer 0', 'answer 1', 'answer 2'])
            self.assertTrue(any(method == 'raw' for method, _ in host.calls))
            plain = normalized(page)
            plain.pop('rowsUnreadable', None)
            outputs.append(plain)
            if damaged:
                host.status = 'complete'
                repaired = reader.events(targetRef=row['targetRef'])
                self.assertEqual(repaired['rowSource'], 'host')
                self.assertFalse(repaired.get('rowsUnreadable', False))
        self.assertEqual(*outputs, 'the damage notice must not change raw replay or its witnesses')

    def test_noncomplete_and_older_hosts_do_not_claim_damage(self):
        for status in ('building', 'queued', 'absent'):
            with self.subTest(status=status):
                host, reader, row = self.facade()
                host.status = status
                page = reader.events(targetRef=row['targetRef'])
                self.assertEqual(page['rowSource'], 'fallback')
                self.assertFalse(page.get('rowsUnreadable', False))
                self.assertEqual(len(page['events']), 4)
        host, reader, row = self.facade()
        host.enabled = False
        page = reader.events(targetRef=row['targetRef'])
        self.assertFalse(page.get('rowsUnreadable', False))
        self.assertFalse(any(method == 'rows' for method, _ in host.calls))

    def test_damage_found_after_the_head_survives_the_existing_rows_retry(self):
        host, reader, row = self.facade()

        def mark_after_head(method, params):
            if method == 'rows':
                host.status = 'unreadable'

        host.hook = mark_after_head
        page = reader.events(targetRef=row['targetRef'])
        self.assertEqual(page['rowSource'], 'fallback')
        self.assertTrue(page.get('rowsUnreadable', False))
        self.assertEqual(len(page['events']), 4)

    def test_only_the_damaged_execution_carries_the_fact_and_no_ttl_is_kept(self):
        host, reader, row = self.facade()
        other = 'cc-22222222-2222-4222-8222-222222222222'
        record = {**copy.deepcopy(host.record), 'execution_id': other, 'native_uuid': other[3:]}
        original = host.call

        def call(method, **params):
            if params.get('execution_id') == other:
                if method == 'state':
                    return copy.deepcopy(record)
                result = original(method, **params)
                if method in ('rows', 'rows_changed', 'row_witnesses', 'row_diagnostics'):
                    result.update(status='complete', epoch='healthy-other', counts={'main': 0})
                if method == 'rows':
                    result.update(rows=[], incidents=[], incident_count=0)
                return result
            return original(method, **params)

        host.call = call
        host.status = 'unreadable'
        damaged = reader.events(targetRef=row['targetRef'])
        self.assertTrue(damaged.get('rowsUnreadable', False))
        # This test isolates the status carrier from the separate display-only activity tail.
        reader._rows_activity_tail = lambda *args: None
        other_ref = reader._ref('target', (other, 1))
        healthy = reader.events(targetRef=other_ref)
        self.assertEqual(healthy['executionId'], other)
        self.assertFalse(healthy.get('rowsUnreadable', False))
        self.assertEqual(healthy['events'], [])
        host.calls.clear()
        again = reader.events(targetRef=row['targetRef'])
        self.assertTrue(again.get('rowsUnreadable', False))
        self.assertTrue(any(method == 'rows' for method, _ in host.calls), 'each operation reads status anew, never a TTL')

    def test_real_handler_carries_the_damage_fact_with_nonempty_raw_events(self):
        host, reader, row = self.facade()
        host.status = 'unreadable'
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        httpd.claude_reader = reader
        serving = threading.Thread(target=httpd.serve_forever, daemon=True)
        serving.start()
        try:
            connection = http.client.HTTPConnection(*httpd.server_address, timeout=3)
            connection.request('GET', '/api/claude/events?targetRef=' + row['targetRef'])
            answer = connection.getresponse()
            page = json.loads(answer.read())
            connection.close()
            self.assertEqual(answer.status, 200)
            self.assertTrue(page.get('rowsUnreadable', False))
            self.assertEqual((page['executionId'], page['rowSource'], len(page['events'])), (EX, 'fallback', 4))
        finally:
            httpd.shutdown()
            httpd.server_close()
            serving.join(2)


if __name__ == '__main__':
    unittest.main()
