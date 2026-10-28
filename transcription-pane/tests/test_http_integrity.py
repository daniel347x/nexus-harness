"""Real ephemeral loopback HTTP pane handler -> offline OpenCode stub. Never live ports/auth."""
import importlib.util
import json
import pathlib
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("http_integrity_pane", pathlib.Path(__file__).resolve().parents[1] / "server.py")
pane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pane)


class HTTPIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        received = self.received

        class OfflineOpenCode(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((self.path, raw, self.headers.get("Authorization")))
                body = b'{"data":{"id":"offline-id"}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def start(handler):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def cleanup():
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            self.addCleanup(cleanup)
            return f"http://127.0.0.1:{server.server_port}"

        upstream = start(OfflineOpenCode)
        base = patch.object(pane, "OPENCODE_BASE", upstream)
        auth = patch.object(pane, "auth_header", return_value="Basic offline-fixture-only")
        base.start()
        auth.start()
        self.addCleanup(base.stop)
        self.addCleanup(auth.stop)
        self.base = start(pane.Handler)

    def post(self, path, data):
        request = urllib.request.Request(self.base + path, data=data,
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read())

    def test_real_http_full_bridge_and_legacy_proxy_ascii_and_unicode(self):
        inputs = ["head\n\n" + "x" * 25000 + "\nTAIL_25K",
                  "head\n\n" + "x" * 200000 + "\nTAIL_200K",
                  "汉字 👩🏽‍💻 e\u0301\n\n" * 20000 + "TAIL_Unicode"]
        for text in inputs:
            for queued in (False, True):
                with self.subTest(chars=len(text), queued=queued):
                    body = {"sessionId": "ses_offline", "targetMode": "manual", "text": text}
                    if queued:
                        body["delivery"] = "queue"
                    self.post("/api/send", json.dumps(body, ensure_ascii=False).encode("utf-8"))
                    route, raw, auth = self.received[-1]
                    self.assertEqual(route, "/api/session/ses_offline/prompt")
                    expected = {"text": text}
                    if queued:
                        expected.update(delivery="queue", resume=False)
                    self.assertEqual(json.loads(raw), expected)
                    self.assertEqual(auth, "Basic offline-fixture-only")
            raw = json.dumps({"text": " \n" + text + "\n "}, ensure_ascii=False).encode("utf-8")
            self.post("/oc/api/session/ses_offline/prompt", raw)
            self.assertEqual(self.received[-1][1], raw, "legacy proxy forwards exact raw bytes including whitespace")

    def test_legacy_proxy_refuses_non_loopback_host_and_cross_origin(self):
        port = self.base.rsplit(":", 1)[1]
        cases = [{"Host": "puget.example.ts.net"},
                 {"Origin": "https://puget.example.ts.net"},
                 {"Sec-Fetch-Site": "cross-site"},
                 {"Origin": "http://localhost:9899"}]
        for extra in cases:
            with self.subTest(extra=extra):
                before = len(self.received)
                request = urllib.request.Request(self.base + "/oc/api/session/ses_offline/prompt", data=b"{}",
                                                 headers={"Content-Type": "application/json", **extra}, method="POST")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(ctx.exception.code, 403)
                self.assertEqual(len(self.received), before, "nothing reaches OpenCode")
        request = urllib.request.Request(self.base + "/oc/api/session", method="OPTIONS")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 403)
        # Same-origin loopback (the desktop pane) still passes.
        self.assertEqual(self.post("/oc/api/session/ses_offline/prompt", b"{}"), {"data": {"id": "offline-id"}})
        self.assertTrue(port)


if __name__ == "__main__":
    unittest.main()
