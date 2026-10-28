"""Cache-only primitives used by Ledger curation and Manager stamp checks (no live daemon)."""
import asyncio
import json

import pytest

import oc_common as C


class WF:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def call(self, family, verb, params):
        self.calls.append((family, verb, params))
        return json.dumps(self.reply)


def test_direct_children_have_exact_names_and_no_metric_or_browser_requests():
    wf = WF({"rows": [{"uuid": "root", "name": "[ap:ABC] Root #tag", "depth": 0},
                       {"uuid": "child", "name": "🟢 Child [literal] #tag", "depth": 1},
                       {"uuid": "deep", "name": "not direct", "depth": 2}]})
    name, rows = asyncio.run(C.cached_children(wf, "[ap:ABC]"))
    assert name == "[ap:ABC] Root #tag"
    assert rows == [{"uuid": "child", "name": "🟢 Child [literal] #tag", "depth": 1}]
    assert wf.calls == [("glimpse", "run", {"root_handle": "[ap:ABC]", "recursive": False,
                                            "descend_policy": "all", "allow_anchor_dock": True,
                                            "headings_only": False, "include_rows": True})]


@pytest.mark.parametrize("reply", [{}, {"error": "unreadable"}, {"success": False, "rows": []},
                                  {"rows": []}, {"rows": [{"uuid": "child", "depth": 1}]}])
def test_failed_inventory_is_not_an_empty_inventory(reply):
    with pytest.raises(RuntimeError, match="cache child inventory"):
        asyncio.run(C.cached_children(WF(reply), "root"))


def test_exact_cached_note_preserves_every_byte_and_uses_no_live_read():
    note = "## Curation\n\n- CF-01 · hold · literal &amp; [brackets]\r\n"
    wf = WF({"success": True, "_source": "api", "root": {"id": "root", "name": "Ledger", "note": note}})
    assert asyncio.run(C.cached_note(wf, "root")) == ("Ledger", note)
    assert wf.calls == [("node", "scry", {"root_handle": "root", "depth": 0, "size_limit": 1,
                                         "allow_anchor_dock": True})]


@pytest.mark.parametrize("reply", [{}, {"success": True, "root": None}, {"success": False, "root": {"id": "x"}},
                                  {"error": "unreadable"}])
def test_failed_note_never_becomes_an_empty_ledger(reply):
    with pytest.raises(RuntimeError, match="cache note read"):
        asyncio.run(C.cached_note(WF(reply), "root"))


def test_newest_stamp_uses_only_direct_uncompleted_child_names():
    wf = WF({"rows": [{"uuid": "root", "name": "Sessions", "depth": 0},
                       {"uuid": "one", "name": "Manager-Session-226", "depth": 1},
                       {"uuid": "two", "name": "[ap:XYZ] Manager-Session-227", "depth": 1},
                       {"uuid": "nested", "name": "Manager-Session-999", "depth": 2}]})
    assert asyncio.run(C.newest_manager_session(wf)) == 227
    assert len(wf.calls) == 1 and wf.calls[0][:2] == ("glimpse", "run")


def test_sub_session_guard_uses_cache_rows_and_keeps_its_existing_classification():
    wf = WF({"rows": [{"uuid": "root", "name": "Sub sessions", "depth": 0},
                       {"uuid": "a", "name": "🟢 Sub-session 227-a · page", "depth": 1},
                       {"uuid": "b", "name": "🔚 Sub-session 227-b · page", "depth": 1}]})
    assert asyncio.run(C.live_sub_sessions(wf)) == ["227-a"]
    assert len(wf.calls) == 1 and wf.calls[0][:2] == ("glimpse", "run")


class Response:
    def __init__(self, body="", status=200, headers=None):
        self.body, self.status, self.headers = body.encode(), status, headers or {}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self):
        return self.body


class Connection:
    def __init__(self, responses):
        self.responses, self.requests, self.closed = iter(responses), [], False

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, json.loads(body) if body else None, dict(headers or {})))

    def getresponse(self):
        reply = next(self.responses)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        self.closed = True


def http_client(monkeypatch, responses):
    import http.client
    import oc_view
    conn = Connection(responses)
    monkeypatch.setattr(oc_view, "_token", lambda: "fake-token")
    monkeypatch.setattr(C, "_mcp_url", lambda: "http://localhost:8766/mcp?test=1")
    monkeypatch.setattr(http.client, "HTTPConnection", lambda *a, **kw: conn)
    return C.WorkflowyHTTP(), conn


def initialized():
    return [Response(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}),
                     headers={"Mcp-Session-Id": "one-session"}), Response(status=202)]


@pytest.mark.parametrize("sse", [False, True])
def test_http_client_initializes_once_reuses_session_and_decodes_json_or_sse(monkeypatch, sse):
    message = {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "first"},
                                                                  {"type": "text", "text": "second"}]}}
    body = json.dumps(message)
    headers = {}
    if sse:
        body = ': ping\r\n\r\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\r\n\r\nevent: message\r\ndata: ' + body + '\r\n\r\n'
        headers = {"Content-Type": "text/event-stream; charset=utf-8"}
    wf, conn = http_client(monkeypatch, initialized() + [Response(body, headers=headers), Response(status=204)])

    async def run():
        async with wf:
            assert await wf.call("node", "scry", {"root_handle": "x"}) == "first\nsecond"
    asyncio.run(run())
    assert len(conn.requests) == 4 and conn.closed and wf.calls == 1
    assert conn.requests[0][2]["method"] == "initialize"
    assert conn.requests[1][2]["method"] == "notifications/initialized"
    request = conn.requests[2]
    assert request[:2] == ("POST", "/mcp?test=1")
    assert request[2]["params"] == {"name": "node", "arguments": {"verb": "scry", "params": {"root_handle": "x"}}}
    assert request[3]["Mcp-Session-Id"] == "one-session"
    assert request[3]["MCP-Protocol-Version"] == "2025-03-26"
    assert conn.requests[3][0] == "DELETE"


def test_http_structured_content_keeps_the_existing_text_interface(monkeypatch):
    result = {"jsonrpc": "2.0", "id": 2, "result": {"structuredContent": {"success": True, "root": {"id": "x"}}}}
    wf, _ = http_client(monkeypatch, initialized() + [Response(json.dumps(result)), Response(status=204)])

    async def run():
        async with wf:
            return await wf.call("node", "scry")
    assert json.loads(asyncio.run(run())) == result["result"]["structuredContent"]


@pytest.mark.parametrize("reply, error", [
    (OSError("reply lost"), "reply lost"),
    (Response(), "ended without"),
    (Response('{"jsonrpc":"2.0","id":99,"result":{}}'), "ended without"),
    (Response('{"jsonrpc":"2.0","id":2,"error":{"message":"bad"}}'), "bad"),
    (Response("unavailable", status=503), "HTTP 503"),
])
def test_http_client_never_retries_a_failed_or_lost_write_reply(monkeypatch, reply, error):
    wf, conn = http_client(monkeypatch, initialized() + [reply, Response(status=204)])

    async def run():
        async with wf:
            await wf.call("node", "append_note", {"node_handle": "x", "text": "one append"})
    with pytest.raises((RuntimeError, OSError), match=error):
        asyncio.run(run())
    assert sum(r[2] is not None and r[2]["method"] == "tools/call" for r in conn.requests) == 1
    assert conn.closed


def test_timed_out_read_keeps_framing_owned_until_a_later_write_can_use_the_connection(monkeypatch):
    import threading
    reply = lambda n, text: Response(json.dumps({"jsonrpc": "2.0", "id": n,
                                                "result": {"content": [{"type": "text", "text": text}]}}))
    wf, conn = http_client(monkeypatch, initialized() + [reply(2, "slow read"), reply(3, "write ack"), Response(status=204)])
    release = threading.Event()
    original = wf._post

    async def run():
        started = asyncio.Event()
        loop = asyncio.get_running_loop()
        def delayed(body):
            if body.get("method") == "tools/call" and body["params"]["arguments"]["verb"] == "scry":
                loop.call_soon_threadsafe(started.set)
                release.wait(2)
            return original(body)
        wf._post = delayed
        async with wf:
            read = asyncio.create_task(wf.call("node", "scry"))
            await started.wait()
            read.cancel()
            try:
                with pytest.raises(asyncio.CancelledError):
                    await read
                assert wf._lock.locked()
                write = asyncio.create_task(wf.call("node", "append_note"))
            finally:
                release.set()
            assert await write == "write ack"
    asyncio.run(run())
    assert len(conn.requests) == 5 and conn.closed
