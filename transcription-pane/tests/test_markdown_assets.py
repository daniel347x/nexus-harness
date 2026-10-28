"""Local assets use the retained Handler without server/runtime configuration changes."""
import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class MarkdownAssetsTests(unittest.TestCase):
    def test_existing_http_handler_serves_exact_local_markdown_assets_no_store(self):
        with patch.object(server.Handler, 'log_message'), ThreadingHTTPServer(('127.0.0.1', 0), server.Handler) as httpd:
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                for relative in ('sessions.html', 'message-markdown.js', 'vendor/marked.umd.js', 'vendor/purify.min.js'):
                    with self.subTest(asset=relative):
                        conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
                        try:
                            conn.request('GET', '/' + relative)
                            response = conn.getresponse()
                            self.assertEqual(response.status, 200)
                            self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                            self.assertEqual(response.read(), (server.STATIC / relative).read_bytes())
                            self.assertIn('text/html' if relative.endswith('.html') else 'javascript', response.getheader('Content-Type'))
                        finally:
                            conn.close()
            finally:
                httpd.shutdown()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
