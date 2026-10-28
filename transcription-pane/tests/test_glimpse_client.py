"""Offline Streamable HTTP contract; the only HTTP destination is our ephemeral stub."""
import json
import pathlib
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "transcription-pane"), str(ROOT / "scripts")]
from glimpse_client import GlimpseClient, GlimpseError

# The daemon's real include_rows replies (S1's code, in-process MCP) and the pre-S1 daemon's refusal payloads.
ROWS_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "glimpse-rows"
ENVELOPES = json.loads((ROWS_FIXTURES / "envelopes.json").read_text(encoding="utf-8"))["records"]
PRE_S1 = json.loads((ROWS_FIXTURES / "pre_s1_forbidden.json").read_text(encoding="utf-8"))


def mcp_result(record):
    """A captured FastMCP reply as it travels: structuredContent, its text twin and isError."""
    result = {"content": [{"type": "text", "text": text} for text in record["text"]]}
    if record["structuredContent"] is not None:
        result["structuredContent"] = record["structuredContent"]
    if record["isError"]:
        result["isError"] = True
    return result


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append((body, dict(self.headers)))
        method = body["method"]
        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return
        mode = self.server.mode
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "stub", "version": "1"}}
            if mode == "init-error":
                return self.reply({"id": body["id"], "error": {"message": "initialize refused"}})
        else:
            if mode in ("lost", "always-lost") and (mode == "always-lost" or not self.server.lost):
                self.server.lost = True
                self.send_response(404)
                self.end_headers()
                return
            if mode == "timeout":
                time.sleep(.1)
            if mode == "rpc-error":
                return self.reply({"id": body["id"], "error": {"message": "bad secret-stub"}})
            result = {"content": [{"type": "text", "text": self.server.markdown}]}
            if mode == "tool-error":
                result["isError"] = True
                result["content"][0]["text"] = "failed secret-stub"
            if mode == "structured":
                result = {"structuredContent": {"result": self.server.markdown}}
            if mode == "catalog":
                result = {"structuredContent": {"groups": [{"id": "grp-1", "name": "One"}]}}
            if mode == "structured-error":
                result = {"structuredContent": {"success": False, "error": "failed secret-stub"}}
            if mode == "malformed-content":
                result = {"content": None}
            if self.server.result is not None:
                result = self.server.result
        message = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        if method == "tools/call" and mode.startswith("sse"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            data = (': heartbeat\n\nevent: message\ndata: {"jsonrpc":"2.0","method":"notice"}\n\n'
                    'data: {"id":999,"result":{}}\n\n'
                    'data: ' + json.dumps(message)[:1] + '\n'
                    'data: ' + json.dumps(message)[1:] + '\n\n')
            if mode == "sse-short":
                data = 'data: {"jsonrpc":"2.0","method":"notice"}\n\n'
            self.wfile.write(data.encode())
            self.wfile.flush()
            # Socket remains open beyond the client's timeout. Matching event must return first.
            if mode == "sse-open":
                time.sleep(.15)
            return
        return self.reply(message, malformed=mode == "malformed" and method == "tools/call",
                          short=mode == "short" and method == "tools/call")

    def reply(self, message, malformed=False, short=False):
        data = b'{"id":' if malformed else json.dumps(message).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "stub-session")
        self.send_header("Content-Length", str(len(data) + (20 if short else 0)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except BrokenPipeError:
            pass


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        self.stub.mode, self.stub.seen, self.stub.lost, self.stub.result = "json", [], False, None
        self.stub.markdown = ' \n# é\n<html> raw </html>\n"quoted"\n(empty)\n '
        self.thread = threading.Thread(target=self.stub.serve_forever, daemon=True)
        self.thread.start()
        self.client = GlimpseClient(url=f"http://127.0.0.1:{self.stub.server_port}/mcp", token="secret-stub", timeout=.05)

    def tearDown(self):
        self.stub.shutdown()
        self.stub.server_close()
        self.thread.join()

    def call(self):
        return self.client.call("glimpse", "run", {"render_spec": {"node": "abcd1234"}, "as_markdown": True})

    def test_json_envelopes_and_exact_markdown(self):
        self.assertEqual(self.call(), self.stub.markdown)
        seen = self.stub.seen
        self.assertEqual([x[0]["method"] for x in seen], ["initialize", "notifications/initialized", "tools/call"])
        self.assertNotIn("id", seen[1][0])
        self.assertEqual(seen[0][0]["params"]["protocolVersion"], "2025-11-25")
        self.assertEqual(seen[2][0]["params"], {"name": "glimpse", "arguments": {"verb": "run", "params": {"render_spec": {"node": "abcd1234"}, "as_markdown": True}}})
        headers = {k.lower(): v for k, v in seen[2][1].items()}
        self.assertEqual(headers["mcp-session-id"], "stub-session")
        self.assertEqual(headers["mcp-protocol-version"], "2025-06-18")
        self.assertEqual(headers["authorization"], "Bearer secret-stub")
        self.assertEqual(self.client.timeout, .05)
        self.assertEqual(GlimpseClient(url="http://unused", token="x").timeout, 30)

    def test_sse_multiline_unrelated_and_open_stream(self):
        for mode in ("sse", "sse-open"):
            with self.subTest(mode=mode):
                self.stub.mode = mode
                self.assertEqual(self.call(), self.stub.markdown)

    def test_structured_string_not_json_quoted(self):
        self.stub.mode = "structured"
        self.assertEqual(self.call(), self.stub.markdown)

    def test_structured_catalog(self):
        self.stub.mode = "catalog"
        self.assertEqual(self.client.call("slice_group", "list", {"detail": "full"}), {"groups": [{"id": "grp-1", "name": "One"}]})

    def test_errors_and_secret_redaction(self):
        for mode in ("init-error", "rpc-error", "tool-error", "structured-error", "malformed-content", "malformed", "short", "sse-short", "timeout"):
            with self.subTest(mode=mode):
                self.stub.mode = mode
                self.client._ready = False
                with self.assertRaises(GlimpseError) as caught:
                    self.call()
                self.assertNotIn("secret-stub", str(caught.exception))

    def test_session_loss_reinitializes_only_once(self):
        self.stub.mode = "lost"
        self.assertEqual(self.call(), self.stub.markdown)
        self.assertEqual(sum(b["method"] == "initialize" for b, h in self.stub.seen), 2)
        self.stub.mode = "always-lost"
        before = len(self.stub.seen)
        with self.assertRaisesRegex(GlimpseError, "one reinitialization"):
            self.call()
        self.assertEqual(sum(b["method"] == "tools/call" for b, h in self.stub.seen[before:]), 2)

    def test_cache_refresh_never_replayed(self):
        for mode in ("always-lost", "short", "timeout"):
            with self.subTest(mode=mode):
                self.stub.mode = mode
                before = len(self.stub.seen)
                with self.assertRaises(GlimpseError):
                    self.client.call("system", "cache.refresh", {})
                self.assertEqual(sum(b["method"] == "tools/call" for b, h in self.stub.seen[before:]), 1)

    # ---- include_rows (UnitSpec S §6.3): the envelope as each transport carries it, faults keep their code

    def rows_call(self, include_rows=True):
        params = {"render_spec": {"node": "abcd1234"}, "as_markdown": True}
        if include_rows is not None:
            params["include_rows"] = include_rows
        return self.client.call("glimpse", "run", params)

    def test_envelope_from_structured_content_or_its_text_twin(self):
        for name in ("anchor_dock", "node_parcel", "raw_root_created_asc_tap"):
            record = ENVELOPES[name]
            envelope = record["structuredContent"]
            for form in ("structured", "text-twin"):
                with self.subTest(name=name, form=form):
                    result = mcp_result(record)
                    if form == "text-twin":
                        del result["structuredContent"]
                    self.stub.result = result
                    self.assertEqual(self.rows_call(), envelope)

    def test_envelope_only_when_rows_were_asked(self):
        record = ENVELOPES["node_parcel"]
        self.stub.result = mcp_result(record)
        for flag in (None, False):
            with self.subTest(include_rows=flag):
                self.assertEqual(self.rows_call(flag), record["text"][0])  # the legacy text path, unchanged

    def test_json_looking_markdown_stays_literal(self):
        for text in ('{"markdown": "x"}', '{"markdown": "x", "rows": "no"}', '[1, 2]', '"quoted"', '{"rows": []}', "# plain"):
            with self.subTest(text=text):
                self.stub.result = {"content": [{"type": "text", "text": text}]}
                self.assertEqual(self.rows_call(), text)
        self.stub.result = None
        self.assertEqual(self.rows_call(), self.stub.markdown)

    def test_structured_fault_keeps_code_and_message(self):
        refusal = PRE_S1["forbidden_run"]
        for form in ("structured", "text-twin", "is-error", "is-error-structured"):
            with self.subTest(form=form):
                text = json.dumps(refusal, separators=(",", ":"))
                self.stub.result = {"content": [{"type": "text", "text": text}]}
                if form in ("structured", "is-error-structured"):
                    self.stub.result["structuredContent"] = refusal
                if form.startswith("is-error"):
                    self.stub.result["isError"] = True
                with self.assertRaises(GlimpseError) as caught:
                    self.rows_call()
                self.assertEqual(caught.exception.error, {"code": "forbidden_parameter", "message": refusal["error"]["message"]})
                self.assertEqual(str(caught.exception), refusal["error"]["message"])
                self.assertTrue(caught.exception.error["message"].startswith("forbidden parameter 'include_rows'; allowed parameters:"))

    def test_real_fault_transports_and_unchanged_messages(self):
        stale = ENVELOPES["stale_focus"]
        self.stub.result = mcp_result(stale)
        with self.assertRaises(GlimpseError) as caught:
            self.rows_call()
        self.assertEqual(caught.exception.error["code"], "glimpse_request_failed")
        # The fault's own message, once: never the dict's repr (which repeats it inside its legacy result).
        self.assertEqual(str(caught.exception), stale["structuredContent"]["error"]["message"])
        # Without include_rows the structured fault still raises, and keeps its code; a text-only JSON fault stays text.
        self.stub.result = mcp_result(ENVELOPES["unknown_parameter"])
        with self.assertRaises(GlimpseError) as caught:
            self.rows_call(None)
        self.assertEqual(caught.exception.error["code"], "forbidden_parameter")
        self.stub.result = {"content": [{"type": "text", "text": json.dumps(PRE_S1["forbidden_run"])}]}
        self.assertEqual(self.rows_call(None), json.dumps(PRE_S1["forbidden_run"]))
        # An isError reply's JSON text names its fault by the message, even without include_rows (no structured code then).
        self.stub.result["isError"] = True
        with self.assertRaises(GlimpseError) as caught:
            self.rows_call(None)
        self.assertEqual(str(caught.exception), PRE_S1["forbidden_run"]["error"]["message"])
        # String errors carry no structured code, and the bearer value is redacted inside a structured message.
        self.stub.result = {"structuredContent": {"success": False, "error": {"code": "x", "message": "bad secret-stub"}}}
        with self.assertRaises(GlimpseError) as caught:
            self.rows_call()
        self.assertEqual(caught.exception.error, {"code": "x", "message": "bad [redacted]"})
        self.assertNotIn("secret-stub", str(caught.exception))
        self.stub.result = None
        self.stub.mode = "structured-error"
        with self.assertRaises(GlimpseError) as caught:
            self.rows_call()
        self.assertIsNone(caught.exception.error)

    def test_missing_credentials_and_lookup_reuse(self):
        with patch("glimpse_client._token", return_value=None), patch("glimpse_client._mcp_url", return_value="http://unused"):
            with self.assertRaisesRegex(GlimpseError, "token missing"):
                GlimpseClient()
        with patch("glimpse_client._token", return_value="stub"), patch("glimpse_client._mcp_url", return_value="http://unused"):
            client = GlimpseClient()
            self.assertEqual(client.url, "http://unused")


if __name__ == "__main__":
    unittest.main()
