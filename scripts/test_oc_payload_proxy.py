"""Tests for proxy/oc_payload_proxy.py: multi-provider routing and the 429 backoff (Manager-Session-110, [ap:F29OSR]).

Stdlib + pytest only. A fake upstream on localhost plays the provider; the proxy runs in-process with tiny backoff
timings, so the whole file takes a few seconds.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("oc_payload_proxy", ROOT / "proxy" / "oc_payload_proxy.py")
proxy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proxy)


class Upstream:
    """A fake provider: answers `script` statuses in order (repeating the last), then streams an SSE body on 200."""

    def __init__(self, script: list[int], retry_after: str | None = None):
        self.script = list(script); self.calls: list[str] = []; self.retry_after = retry_after
        up = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                up.calls.append(self.path)
                code = up.script[min(len(up.calls) - 1, len(up.script) - 1)]
                if code == 200:
                    self.send_response(200); self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
                    for part in (b"data: one\n\n", b"data: two\n\n"):
                        self.wfile.write(f"{len(part):X}\r\n".encode() + part + b"\r\n"); self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    msg = b'{"error":"rate limited"}'
                    self.send_response(code)
                    if up.retry_after is not None:
                        self.send_header("retry-after", up.retry_after)
                    self.send_header("Content-Length", str(len(msg))); self.end_headers(); self.wfile.write(msg)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.origin = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()


@pytest.fixture()
def run_proxy(monkeypatch):
    servers = []

    def start(origin: str, **retry):
        monkeypatch.setitem(proxy.ROUTES, "openrouter", origin)
        monkeypatch.setattr(proxy, "DEFAULT_UPSTREAM", origin)
        cfg = dict(proxy.RETRY_DEFAULTS, initial_s=0.05, max_wait_s=0.2, max_retry_after_s=0.3)
        cfg.update(retry)
        monkeypatch.setitem(proxy.FEATURES, "retry", cfg)
        monkeypatch.setitem(proxy.FEATURES, "log_summary", False)
        proxy.RETRY_STATE.clear()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), proxy.Relay)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for s in servers:
        s.shutdown()


def post(base: str, path: str, session: str = "ses_test"):
    req = urllib.request.Request(base + path, data=json.dumps({"model": "z-ai/glm-5.3-flashx"}).encode(),
                                 headers={"Content-Type": "application/json", "x-opencode-session-id": session}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_manager_sessions_get_the_1h_ttl_everything_else_keeps_cache_ttl():
    def body(system_first_line):
        return json.dumps({"model": "claude-opus-5-5", "max_tokens": 10,
                           "system": [{"type": "text", "text": system_first_line + "\nmore prompt",
                                       "cache_control": {"type": "ephemeral"}}],
                           "messages": [{"role": "user", "content": [{"type": "text", "text": "hi",
                                                                     "cache_control": {"type": "ephemeral"}}]}]}).encode()
    saved = dict(proxy.FEATURES)
    try:
        proxy.FEATURES.update({"cache_ttl": "5m", "manager_cache_ttl": "1h"})
        new, summary = proxy.transform_messages_body(body("# CONVERSATION CONTINUITY AGENT"))
        assert summary["manager"] is True and summary["ttl_rewritten"] == "1h"
        assert {b["ttl"] for b in summary["breakpoints"]} == {"1h"}
        assert all(b["cache_control"]["ttl"] == "1h" for b in json.loads(new)["system"])
        new, summary = proxy.transform_messages_body(body("You are a dispatched worker"))
        assert summary["manager"] is False and {b["ttl"] for b in summary["breakpoints"]} == {"5m"}
        proxy.FEATURES["manager_cache_ttl"] = "off"
        new, summary = proxy.transform_messages_body(body("# CONVERSATION CONTINUITY AGENT"))
        assert {b["ttl"] for b in summary["breakpoints"]} == {"5m"}
        # Conductors wait on workers for many minutes: their own 1h exception, keyed on conductor.md's opening.
        conductor = (proxy.HERE.parent / "agents" / "agent" / "conductor.md").read_text(encoding="utf-8")
        assert proxy.FEATURES["conductor_marker"] in conductor
        new, summary = proxy.transform_messages_body(body(proxy.FEATURES["conductor_marker"] + " (rest of prompt)"))
        assert summary["conductor"] is True and summary["manager"] is False
        assert {b["ttl"] for b in summary["breakpoints"]} == {"1h"}
        proxy.FEATURES["conductor_cache_ttl"] = "off"
        new, summary = proxy.transform_messages_body(body(proxy.FEATURES["conductor_marker"]))
        assert {b["ttl"] for b in summary["breakpoints"]} == {"5m"}
    finally:
        proxy.FEATURES.clear(); proxy.FEATURES.update(saved)


def test_route_mapping():
    assert proxy.route("/openrouter/api/v1/chat/completions")[0] == "openrouter"
    assert proxy.route("/openrouter/api/v1/chat/completions")[2] == "/api/v1/chat/completions"
    assert proxy.route("/openai/v1/responses")[1] == "https://api.openai.com"
    assert proxy.route("/v1/messages") == ("anthropic", proxy.DEFAULT_UPSTREAM, "/v1/messages")


def test_backoff_schedule_and_retry_after():
    cfg = proxy.RETRY_DEFAULTS
    waits = [proxy.backoff_s(n, cfg, None) for n in range(1, 11)]
    assert waits[:4] == [5, 10, 20, 40] and max(waits) == 300
    assert 1400 < sum(waits) < 1600                      # ≈ 25 minutes in all, well under the one-hour headerTimeout
    assert proxy.backoff_s(1, cfg, 120) == 120           # a longer retry-after wins
    assert proxy.backoff_s(1, cfg, 99999) == 900         # …capped
    assert proxy.parse_retry_after([("Retry-After", "7")]) == 7
    assert proxy.parse_retry_after([("retry-after-ms", "1500")]) == 1.5


def test_429s_are_retried_then_stream_relayed(run_proxy):
    up = Upstream([429, 429, 200], retry_after="0")
    try:
        base = run_proxy(up.origin)
        code, body = post(base, "/openrouter/api/v1/chat/completions")
        assert code == 200 and b"data: one" in body and b"data: two" in body
        assert up.calls == ["/api/v1/chat/completions"] * 3     # prefix stripped, three attempts
        st = json.loads(urllib.request.urlopen(base + "/_proxy/status?session=ses_test", timeout=5).read())
        s = st["sessions"]["ses_test"]
        assert s["phase"] == "recovered" and s["attempt"] == 3 and s["model"] == "z-ai/glm-5.3-flashx"
    finally:
        up.close()


def test_gives_up_after_max_retries_and_passes_the_429_on(run_proxy):
    up = Upstream([429])
    try:
        base = run_proxy(up.origin, max_retries=2)
        code, _ = post(base, "/openrouter/api/v1/chat/completions", session="ses_b")
        assert code == 429 and len(up.calls) == 3
        assert proxy.RETRY_STATE["ses_b"]["phase"] == "gave_up"
    finally:
        up.close()


def test_non_retryable_status_is_not_retried(run_proxy):
    up = Upstream([400])
    try:
        base = run_proxy(up.origin)
        code, _ = post(base, "/v1/messages", session="ses_c")     # unprefixed → default (Anthropic) route
        assert code == 400 and len(up.calls) == 1 and "ses_c" not in proxy.RETRY_STATE
    finally:
        up.close()


def test_oc_seat_wait_reporting_lines():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import oc_seat
    msgs = [{"type": "assistant"},
            {"type": "assistant", "retry": {"attempt": 3, "at": 1, "error": {"type": "api", "message": "x"}},
             "error": {"type": "api", "status": 429, "message": "rate limited upstream"}}]
    assert oc_seat.last_api_error(msgs) == "api 429 (after retry 3): rate limited upstream"
    assert oc_seat.last_api_error([{"type": "assistant"}]) is None
    line = oc_seat.fmt_retry_state({"phase": "backoff", "last_status": 429, "route": "openrouter",
                                    "model": "z-ai/glm-5.3-flashx", "attempt": 4, "max_retries": 10, "next_in_s": 80})
    assert "retry 4/10" in line and "not hung" in line


def test_client_hangup_stops_the_retries(run_proxy):
    up = Upstream([429])
    try:
        base = run_proxy(up.origin, initial_s=0.5, max_wait_s=0.5, max_retries=50)
        port = int(base.rsplit(":", 1)[1])
        body = b'{"model":"m"}'
        s = socket.create_connection(("127.0.0.1", port))
        s.sendall(b"POST /openrouter/x HTTP/1.1\r\nHost: x\r\nx-opencode-session-id: ses_d\r\nContent-Length: "
                  + str(len(body)).encode() + b"\r\n\r\n" + body)
        time.sleep(1.2)
        s.close()
        time.sleep(1.5)
        n = len(up.calls)
        time.sleep(1.5)
        assert len(up.calls) == n and n < 6
        assert proxy.RETRY_STATE["ses_d"]["phase"] == "abandoned"
    finally:
        up.close()


def test_effort_updates_folded_last_wins_and_untouched_without_them():
    f = proxy.transform_responses_body
    plain = json.dumps({"reasoning": {"effort": "medium"}, "input": [{"type": "message", "content": "configuration_update"}]}).encode()
    assert f(plain)[0] is plain                                   # no item (the word only in text): original bytes
    body = json.dumps({"reasoning": {"summary": "auto"}, "input": [
        {"type": "configuration_update", "reasoning": {"effort": "xhigh"}}, {"type": "message"},
        {"type": "configuration_update", "reasoning": {"effort": "medium"}}, {"type": "message"}]}).encode()
    out, summary = f(body)
    j = json.loads(out)
    assert j["reasoning"] == {"summary": "auto", "effort": "medium"}
    assert [i["type"] for i in j["input"]] == ["message", "message"]
    assert summary["effort_updates_folded"] == 2
