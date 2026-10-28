"""Both inventory routes return full stored Conductor titles without a Workflowy lookup."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import server


FULL = 'PROGRAM · Millions of events: keep the Claude host and the Manager App fast as the stores grow — every program readable 雪'


class InventoryTitles(unittest.TestCase):
    def test_both_inventory_routes_preserve_full_stored_titles_without_workflowy(self):
        title = '[ap:AAAAAA]-001 · ' + FULL
        cc = {'schemaVersion': 1, 'backend': 'claude', 'sessions': [
            {'source': 'host', 'title': title, 'lineage': {'program': 'AAAAAA', 'kind': 'conductor', 'title': title}}]}
        oc = {'schemaVersion': 1, 'backend': 'opencode', 'status': 'complete', 'sessions': [
            {'program': 'AAAAAA', 'kind': 'conductor', 'title': title}]}
        empty = lambda **_: {}
        reader = SimpleNamespace(sessions=lambda **_: cc, events=empty, subagents=empty,
                                 context=empty, artifact=empty, snapshot=empty)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        httpd.claude_reader = reader
        httpd.opencode_programs = SimpleNamespace(programs=lambda **_: oc)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(server, 'GlimpseClient', side_effect=AssertionError('inventory must not read Workflowy')) as client:
                for route in ('/api/claude/sessions?details=0', '/api/opencode/programs?view=week'):
                    with self.subTest(route=route):
                        conn = http.client.HTTPConnection('127.0.0.1', httpd.server_address[1], timeout=3)
                        try:
                            conn.request('GET', route)
                            resp = conn.getresponse()
                            body = json.loads(resp.read())
                        finally:
                            conn.close()
                        self.assertEqual(resp.status, 200, body)
                        self.assertEqual(body['sessions'][0]['title'], title)
                        self.assertNotIn('programTitles', body)
                client.assert_not_called()
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
