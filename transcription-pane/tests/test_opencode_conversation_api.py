"""Authenticated ephemeral upstream -> production Handler -> one mailbox -> unchanged inspector."""
import copy
import importlib.util
import json
import pathlib
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlencode

from test_opencode_conversation import SID, Wire, fixture, message, node_snapshots, surrogate_cases
import opencode_conversation as oc
import opencode_programs
import oc_client

spec = importlib.util.spec_from_file_location("opencode_api_pane", pathlib.Path(__file__).resolve().parents[1] / "server.py")
pane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pane)


def browser_decode(body):
    """Decode actual HTTP bytes as a browser does, not Python's permissive JSON bytes input."""
    js = "const fs=require('fs'); const page=JSON.parse(new TextDecoder().decode(fs.readFileSync(0))); console.log(JSON.stringify(page));"
    result = subprocess.run(["node", "-e", js], input=body, capture_output=True, timeout=15, check=True)
    return json.loads(result.stdout)


class ApiTests(unittest.TestCase):
    def test_recorded_diff_exact_equality_direct_phone_and_mailbox_preserves_raw_wire(self):
        from test_tool_diff import opencode_fixture
        from tool_diff import opencode_diff
        f = opencode_fixture()
        self.wire = Wire([message(1,'assistant',text='',content=[{'type':'tool','id':'prt_diff','name':'patch','state':f['state']}])])
        self.server.opencode_conversation = oc.OpenCodeConversation()
        page = self.page(); call = page['events'][0]['calls'][0]
        code, direct, _, _ = self.tool_record(page,call)
        self.assertEqual(code,200)
        self.assertEqual(direct['diff'],opencode_diff('patch',f['state']))
        code, ack, _, _ = self.http('/api/opencode/inspect',method='POST',data=self.click(page))
        self.assertEqual(code,200)
        code, held, raw, _ = self.http('/api/tool-record?'+urlencode({k:ack[k] for k in ('epoch','revision')}),origin=False)
        self.assertEqual(code,200)
        self.assertEqual(held['diff'],direct['diff'])
        self.assertEqual(held['diff']['files'][0]['hunks'][0]['oldStart'],124)
        part_wire = pane.wire_members(raw.decode())['part']
        original = pane.wire_members(pane.wire_members(self.wire.messages[0])['content'])[0]
        self.assertEqual(part_wire,original)
        state_wire = pane.wire_members(part_wire)['state']
        expected = node_snapshots([state_wire])[0]
        self.assertEqual([direct[k] for k in ('input','output','inputBytes','outputBytes')],
                         [expected[k] for k in ('input','output','inputBytes','outputBytes')])
        self.assertEqual(len(self.upstream_calls),len([c for c in self.upstream_calls if c[0]=='GET']))

    def setUp(self):
        self.wire = Wire()
        self.upstream_calls = []
        owner = self
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                owner.upstream_calls.append((self.command, self.path, self.headers.get("Authorization")))
                if self.headers.get("Authorization") != "Basic fixture-auth":
                    self.send_error(401); return
                try:
                    body = owner.wire.request(self.path, 10, raw=True)
                    code = 200
                except opencode_programs.Unavailable as e:
                    code, body = e.code, b'{"error":"fixture unavailable"}'
                self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        class QuietHandler(pane.Handler):
            def log_message(self, *args): pass
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.server.opencode_conversation = oc.OpenCodeConversation()
        for server in (self.upstream, self.server):
            thread=threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.origin = "http://127.0.0.1:" + str(self.server.server_address[1])
        upstream = "http://127.0.0.1:" + str(self.upstream.server_address[1])
        for target, name, value in ((oc_client,"OPENCODE_BASE",upstream), (oc_client,"auth_header",lambda: "Basic fixture-auth"),
                                    (pane,"OPENCODE_BASE",upstream), (pane,"auth_header",lambda: "Basic fixture-auth"),
                                    (pane,"_tools",pane.ToolMailbox())):
            p=patch.object(target, name, value); p.start(); self.addCleanup(p.stop)

    def http(self, path, *, method="GET", data=None, origin=True, content_type="application/json"):
        body = None if data is None else data if isinstance(data, bytes) else json.dumps(data).encode()
        headers = {"Content-Type": content_type}
        if origin: headers["Origin"] = self.origin if origin is True else origin
        req = urllib.request.Request(self.origin + path, data=body, method=method, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as e:
            response=e
        with response:
            body=response.read(); headers=response.headers; code=response.code
        return code, json.loads(body), body, headers

    def page(self):
        code, page, _, headers = self.http("/api/opencode/events?" + urlencode({"id":SID,"fromSeq":0}))
        self.assertEqual(code, 200); self.assertEqual(headers["Cache-Control"], "no-store")
        return page

    def click(self, page, **extra):
        row = next(r for r in page["events"] if r["calls"])
        call = row["calls"][0]
        return {"schemaVersion":1, "backend":"opencode", "id":SID, "messageId":call["messageId"],
                "partIndex":call["partIndex"], "callRef":call["callRef"], "fileEpoch":page["fileEpoch"],
                "selectorId":"opencode-page", "sequence":1, "observedAt":int(time.time()*1000)-10, **extra}

    def test_production_events_inspect_unchanged_read_record_preserves_wire_state(self):
        page = self.page(); click = self.click(page)
        code, ack, _, _ = self.http("/api/opencode/inspect", method="POST", data=click)
        self.assertEqual(code, 200); self.assertTrue(ack["ok"]); self.assertEqual(ack["backend"], "opencode")
        selected = ack["selection"]
        self.assertEqual(selected["origin"], "pane"); self.assertEqual(selected["instanceId"], "opencode-local")
        self.assertEqual(selected["association"], {"sessionCreated":1792390000000, "directory":"/fixture/workspace",
                                                  "messageCreated":1792390002000, "toolName":"subagent", "callId":None})
        self.assertNotIn("state", selected); self.assertNotIn("input", selected)
        code, mail, _, _ = self.http("/api/tool-selection", origin=False)
        self.assertEqual(mail, {k:ack[k] for k in ("epoch","revision","selection")})
        code, part, raw, _ = self.http("/api/tool-record?" + urlencode({k:ack[k] for k in ("epoch","revision")}), origin=False)
        self.assertEqual(code, 200)
        self.assertEqual(part["part"]["id"], "prt_real")
        part_wire = pane.wire_members(raw.decode())["part"]
        state_wire = pane.wire_members(part_wire)["state"]
        _, source = fixture()
        original = pane.wire_members(pane.wire_members(source[1])["content"])[4]
        self.assertEqual(state_wire, pane.wire_members(original)["state"])
        self.assertNotIn("providerState", pane.wire_members(part_wire))
        self.assertNotIn("providerResultState", pane.wire_members(part_wire))
        self.assertIn("ordinary input key", state_wire)
        self.assertIn("1e+02", state_wire); self.assertIn("-0", state_wire)
        self.assertNotIn(b"TEST_SENTINEL", raw)
        call = next(r["calls"][0] for r in page["events"] if r["calls"])
        snap = node_snapshots([state_wire])[0]  # the actual inspector helper on these exact HTTP state bytes
        self.assertEqual((call["inputBytes"], call["outputBytes"]), (snap["inputBytes"], snap["outputBytes"]))
        self.assertTrue(all(method == "GET" and auth == "Basic fixture-auth" for method, _, auth in self.upstream_calls))
        self.assertFalse(any(any(word in path for word in ("/prompt", "/control", "/credential")) for _, path, _ in self.upstream_calls))
        self.assertNotIn("fixture-auth", json.dumps(page) + json.dumps(ack) + raw.decode())

    def test_all_surrogate_faces_tail_first_and_delta_over_production_http(self):
        for cut in ("\ud83d", "\ude00"):
            controls = surrogate_cases("😀")
            for face, source in surrogate_cases(cut).items():
                with self.subTest(face=face, cut=ascii(cut)):
                    self.wire = Wire([message(0), source, message(2)])
                    self.server.opencode_conversation = oc.OpenCodeConversation()
                    control = oc.OpenCodeConversation(Wire([message(0), controls[face], message(2)]).request)
                    expected = control.events(id=SID)["events"]
                    pages = []
                    for query in ({"id": SID}, {"id": SID, "fromSeq": 0}):
                        code, page, raw, headers = self.http("/api/opencode/events?" + urlencode(query))
                        self.assertEqual(code, 200, face)
                        self.assertEqual(headers["Cache-Control"], "no-store")
                        self.assertEqual(raw, oc.encoded(page))
                        self.assertEqual([r["uuid"] for r in page["events"]], [r["uuid"] for r in expected])
                        self.assertEqual([r["seq"] for r in page["events"]], list(range(len(expected))))
                        self.assertTrue(all(r["status"] == "stored" for r in page["events"]))
                        pages.append(page)
                    self.assertEqual(browser_decode(raw), page)  # form (a): exactly one original UTF-16 cut
                    if face in ("v-text", "vii-user"):
                        self.assertEqual(page["events"][1]["text"], source["text"] if face == "vii-user" else source["content"][0]["text"])
                    elif face == "vi-reasoning":
                        self.assertEqual(page["events"][1]["thinking"][0]["text"], source["content"][0]["text"])
                    self.assertEqual(pages[0]["events"], pages[1]["events"])
                    self.wire.messages.append(json.dumps(message(3)))
                    code, delta, raw, _ = self.http("/api/opencode/events?" + urlencode({
                        "id": SID, "fileEpoch": pages[0]["fileEpoch"], "cursor": pages[0]["deltaCursor"]}))
                    self.assertEqual(code, 200, face)
                    self.assertEqual(raw, oc.encoded(delta))
                    combined = page["events"] + delta["events"]
                    self.assertEqual([r["seq"] for r in combined], list(range(len(expected) + 1)))
                    self.assertEqual(len({r["uuid"] for r in combined}), len(combined))
                    self.assertEqual(combined, self.server.opencode_conversation.cache[SID].rows)
                    self.assertEqual(delta["eventUpdates"], [])

    def test_live_cut_arrival_delta_keeps_following_over_http(self):
        for face in ("i-output", "ii-input", "iii-content", "iv-error", "v-text", "vi-reasoning", "vii-user"):
            with self.subTest(face=face):
                self.wire = Wire([message(0), surrogate_cases("😀")[face]])
                self.server.opencode_conversation = oc.OpenCodeConversation()
                tail = self.page()
                self.wire.messages[1] = json.dumps(surrogate_cases()[face])
                self.wire.messages.append(json.dumps(message(2)))
                code, delta, raw, _ = self.http("/api/opencode/events?" + urlencode({
                    "id": SID, "fileEpoch": tail["fileEpoch"], "cursor": tail["deltaCursor"]}))
                self.assertEqual(code, 200, face)
                self.assertEqual(delta["fileEpoch"], tail["fileEpoch"])
                updates = {r["uuid"]: r for r in delta["eventUpdates"]}
                combined = [updates.get(r["uuid"], r) for r in tail["events"]] + delta["events"]
                self.assertEqual(combined, self.server.opencode_conversation.cache[SID].rows)
                self.assertEqual([r["seq"] for r in combined], list(range(len(combined))))
                self.assertTrue(updates)
                self.assertEqual(browser_decode(raw), delta)

    def test_surrogate_tool_inspect_preserves_exact_state_wire(self):
        state_wire = '{ "status":"completed", "input":{ "n":1e+02, "zero":-0 }, "output":"before \\ud83d after" }'
        part_wire = '{"id":"prt_cut","type":"tool","name":"shell","providerState":"hidden","state":' + state_wire + '}'
        source = json.dumps(message(1, "assistant", text="", finish="tool-calls"))[:-1] + ',"content":[' + part_wire + ']}'
        self.wire = Wire([message(0), source])
        page = self.page()
        code, ack, _, _ = self.http("/api/opencode/inspect", method="POST", data=self.click(page))
        self.assertEqual(code, 200); self.assertTrue(ack["ok"])
        code, record, raw, _ = self.http("/api/tool-record?" + urlencode({k: ack[k] for k in ("epoch", "revision")}), origin=False)
        self.assertEqual(code, 200)
        sent_part = pane.wire_members(raw.decode())["part"]
        sent_state = pane.wire_members(sent_part)["state"]
        self.assertEqual(sent_state.encode(), state_wire.encode())
        self.assertNotIn("providerState", pane.wire_members(sent_part))
        self.assertEqual(record["part"]["state"]["output"], "before \ud83d after")
        self.assertEqual(browser_decode(raw), record)
        snapshot = node_snapshots([state_wire])[0]
        call = next(r["calls"][0] for r in page["events"] if r["calls"])
        self.assertEqual((call["inputBytes"], call["outputBytes"]), (snapshot["inputBytes"], snapshot["outputBytes"]))
        self.assertEqual(call["outputBytes"], 16)

    def test_surrogate_title_inspect_ack_mailbox_record_and_other_serializers(self):
        # Start with a valid page: at the baseline the failure must be the Inspect
        # selection's shared _json, not the test double or events' earlier failure.
        page = self.page()
        self.wire.session["title"] = "title 汉😀 \ud83d after"
        code, ack, raw, _ = self.http("/api/opencode/inspect", method="POST", data=self.click(page))
        self.assertEqual(code, 200)
        self.assertEqual(ack["selection"]["sessionTitle"], self.wire.session["title"])
        self.assertEqual(browser_decode(raw), ack)
        for path in ("/api/tool-selection", "/api/tool-record?" + urlencode({k: ack[k] for k in ("epoch", "revision")})):
            code, obj, raw, _ = self.http(path, origin=False)
            self.assertEqual(code, 200)
            self.assertEqual(obj["selection"], ack["selection"])
            self.assertEqual(browser_decode(raw), obj)
        self.assertEqual(self.page()["sessionInfo"]["title"], self.wire.session["title"])
        # The new _json seam is restricted to pane OpenCode selections. Compare
        # actual bytes with the previous serializer for Claude and CodeNomad.
        handler = object.__new__(pane.Handler)
        for selected in ({"backend": "claude", "title": "Claude 汉😀"},
                         {"origin": "http://127.0.0.1:9899", "title": "CodeNomad 汉😀"}):
            obj = {"epoch": "fixture", "revision": 1, "selection": selected}
            with patch.object(handler, "_send") as send:
                handler._json(200, obj)
                self.assertEqual(send.call_args.args, (200, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()))
            record = pane.WireRecord({**obj, "part": {"state": {"output": "汉😀"}}}, '{ "state": { "output": "汉😀" } }')
            before = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))[:-1] + ',"part":' + record.wire + '}'
            with patch.object(handler, "_send") as send:
                handler._json(200, record)
                self.assertEqual(send.call_args.args, (200, before.encode()))

    def test_non_ascii_fitted_pages_use_exact_route_bytes(self):
        text = "汉😀" * 100 + "\ud83d"
        self.wire = Wire([message(i, text=text) for i in range(20)])
        self.server.opencode_conversation = oc.OpenCodeConversation(response_limit=9000)
        rows, query = [], {"id": SID, "fromSeq": 0}
        while True:
            code, page, raw, _ = self.http("/api/opencode/events?" + urlencode(query))
            self.assertEqual(code, 200)
            self.assertEqual(raw, oc.encoded(page))
            self.assertLessEqual(len(raw), 9000)
            self.assertIn("汉😀".encode(), raw)
            self.assertIn(b"\\ud83d", raw)
            self.assertEqual(browser_decode(raw), page)
            self.assertTrue(all(r["status"] == "stored" and r["text"] == text for r in page["events"]))
            rows.extend(page["events"])
            if not page["hasMore"]: break
            query = {"id": SID, "fileEpoch": page["fileEpoch"], "cursor": page["nextCursor"]}
        self.assertEqual([r["seq"] for r in rows], list(range(20)))
        self.assertEqual(len({r["uuid"] for r in rows}), 20)

    def test_surrogate_selection_metadata_keeps_original_association(self):
        self.wire.session["location"]["directory"] = "/fixture/\ude00"
        self.wire.session.update(agent="agent \ud83d", model={"id": "model \ude00"})
        source = json.loads(self.wire.messages[1])
        source["content"][4]["name"] = "tool \ud83d"
        self.wire.messages[1] = json.dumps(source)
        page = self.page()
        self.assertEqual(page["sessionInfo"]["directory"], "/fixture/\ude00")
        self.assertEqual(page["sessionInfo"]["agent"], "agent \ud83d")
        self.assertEqual(page["sessionInfo"]["model"], "model \ude00")
        code, ack, raw, _ = self.http("/api/opencode/inspect", method="POST", data=self.click(page))
        self.assertEqual(code, 200)
        self.assertEqual(ack["selection"]["workspaceName"], "/fixture/\ude00")
        self.assertEqual(ack["selection"]["association"]["directory"], "/fixture/\ude00")
        self.assertEqual(ack["selection"]["toolName"], "tool \ud83d")
        self.assertEqual(ack["selection"]["association"]["toolName"], "tool \ud83d")
        self.assertEqual(browser_decode(raw), ack)
        code, record, raw, _ = self.http("/api/tool-record?" + urlencode({k: ack[k] for k in ("epoch", "revision")}), origin=False)
        self.assertEqual(code, 200)  # unchanged association algorithm compares original strings, not U+FFFD copies
        self.assertEqual(record["part"]["name"], "tool \ud83d")
        self.assertEqual(browser_decode(raw), record)

    def test_default_transport_parsed_callers_unchanged_and_one_fetch_raw(self):
        path = "/api/session/" + SID
        before=len(self.upstream_calls)
        parsed=opencode_programs.default_request(path,10)
        raw=opencode_programs.default_request(path,10,raw=True)
        self.assertEqual(parsed,json.loads(raw))
        self.assertEqual(len(self.upstream_calls)-before,2)
        self.assertIsInstance(raw,bytes)

    def test_route_methods_origins_strict_queries_and_json_fields(self):
        for path, method, expected in (("/api/opencode/events?id="+SID,"POST",405), ("/api/opencode/inspect","GET",405),
                                        ("/api/opencode/inspect","OPTIONS",403), ("/api/opencode/control","POST",400)):
            with self.subTest(path=path,method=method):
                self.assertEqual(self.http(path,method=method,data={} if method=="POST" else None)[0],expected)
        for query in ("", "id=", "id="+SID+"&id="+SID, "id="+SID+"&bogus=1", "id="+SID+"&fromSeq=-1",
                      "id="+SID+"&fromSeq=9007199254740992", "id="+SID+"&fileEpoch=", "id=../bad"):
            with self.subTest(query=query):
                code, obj, _, _=self.http("/api/opencode/events?"+query)
                self.assertEqual((code,obj["status"]),(400,"schema"))
        self.assertEqual(self.http("/api/opencode/events?id="+SID,origin="http://evil.invalid")[0],403)
        page=self.page(); click=self.click(page)
        for changes in ({"association":{}},{"input":"forbidden"},{"partIndex":True},{"messageId":"bad/ref"},
                        {"selectorId":"x"*257},{"schemaVersion":True},{"sequence":0},{"observedAt":True},{"fileEpoch":"bad/ref"}):
            with self.subTest(changes=changes):
                code,obj,_,_=self.http("/api/opencode/inspect",method="POST",data={**click,**changes})
                self.assertEqual((code,obj["status"]),(400,"schema"))
        self.assertEqual(self.http("/api/opencode/inspect",method="POST",data=click,content_type="text/plain")[0],403)
        self.assertEqual(self.http("/api/opencode/inspect?x=1",method="POST",data=click)[0],403)
        self.assertEqual(self.http("/api/opencode/inspect",method="POST",data=b"x"*32769)[0],413)

    def test_unknown_non_tool_stays_in_events_but_cannot_inspect_and_bad_witness(self):
        page=self.page(); click=self.click(page)
        for changes in ({"partIndex":3},{"partIndex":6},{"partIndex":99},{"messageId":"msg_missing"},{"callRef":"wrong"}):
            code,obj,_,_=self.http("/api/opencode/inspect",method="POST",data={**click,**changes})
            self.assertEqual((code,obj["status"]),(404,"missing"))
        self.assertIn("msg_work_p5",[r["uuid"] for r in page["events"]])
        m=json.loads(self.wire.messages[1]); del m["time"]["created"]; self.wire.messages[1]=json.dumps(m)
        code,obj,_,_=self.http("/api/opencode/inspect",method="POST",data=click)
        self.assertEqual((code,obj["status"]),(502,"read-failure"))
        self.assertIsNone(pane._tools.selection)

    def test_unlisted_child_restart_and_visible_upstream_failures(self):
        code,page,_,_=self.http("/api/opencode/events?id=ses_child000001")
        self.assertEqual(code,200); self.assertEqual(page["sessionInfo"]["id"],"ses_child000001")
        self.server.opencode_conversation=oc.OpenCodeConversation()
        code,obj,_,_=self.http("/api/opencode/events?"+urlencode({"id":"ses_child000001","fileEpoch":page["fileEpoch"],"cursor":page["deltaCursor"]}))
        self.assertEqual((code,obj["status"]),(409,"stale-epoch"))
        self.wire.fail=lambda p: "/message" in p
        code,obj,_,_=self.http("/api/opencode/events?id="+SID)
        self.assertEqual((code,obj["status"]),(502,"read-failure")); self.assertNotIn("events",obj)
        self.wire.fail=None
        with patch.object(opencode_programs,"READ_LIMIT",200):
            code,obj,_,_=self.http("/api/opencode/events?id="+SID)
        self.assertEqual((code,obj["status"]),(413,"response-limit")); self.assertIn("not cropped",obj["error"])

    def tool_record(self, page, call, **changes):
        query = {"id": SID, "callRef": call["callRef"], "fileEpoch": page["fileEpoch"], **changes}
        return self.http("/api/opencode/tool-record?" + urlencode(query))

    def test_phone_tool_record_is_the_rows_exact_snapshot_without_the_mailbox(self):
        page = self.page()
        call = next(r["calls"][0] for r in page["events"] if r["calls"])
        code, snap, raw, headers = self.tool_record(page, call)
        self.assertEqual(code, 200, snap); self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(raw, oc.encoded(snap)); self.assertEqual(browser_decode(raw), snap)
        _, source = fixture()
        state_wire = pane.wire_members(pane.wire_members(pane.wire_members(source[1])["content"])[4])["state"]
        node = node_snapshots([state_wire])[0]  # the desktop inspector's own reading of the same bytes
        self.assertEqual([snap[k] for k in ("input", "output", "inputBytes", "outputBytes")],
                         [node[k] for k in ("input", "output", "inputBytes", "outputBytes")])
        self.assertEqual([snap[k] for k in ("callRef", "fileEpoch", "status", "inputBytes", "outputBytes")],
                         [call["callRef"], page["fileEpoch"], call["status"], call["inputBytes"], call["outputBytes"]])
        self.assertEqual([snap[k] for k in ("backend", "toolName", "inputAvailability", "outputAvailability", "live", "terminal", "time", "resultTime")],
                         ["opencode", "subagent", "stored", "stored", True, True, 1792390003.0, 1792390005.0])
        # The collapsed row carries the same two times (its total time); a running call has no result time.
        self.assertEqual((call["time"], call["resultTime"]), (snap["time"], snap["resultTime"]))
        self.assertNotIn(b"TEST_SENTINEL", raw)
        running = next(c for r in page["events"] for c in r["calls"] if c["status"] == "pending-or-missing")
        code, live, _, _ = self.tool_record(page, running)
        self.assertEqual(code, 200)
        self.assertEqual([live[k] for k in ("status", "output", "outputAvailability", "live", "terminal", "resultTime", "inputFinal")],
                         ["pending-or-missing", None, "pending-or-missing", True, False, None, True])
        self.assertIsNone(running["resultTime"])
        self.assertEqual(json.loads(live["input"]), {"command": "work"})
        self.assertIsNone(pane._tools.selection)  # the desktop's held inspector is untouched
        self.assertTrue(all(method == "GET" for method, _, _ in self.upstream_calls))

    def test_phone_tool_record_keeps_lone_surrogates_and_refuses_stale_wrong_or_foreign_reads(self):
        for face in ("i-output", "ii-input", "iii-content", "iv-error"):
            with self.subTest(face=face):
                self.wire = Wire([message(0), surrogate_cases()[face]])
                self.server.opencode_conversation = oc.OpenCodeConversation()
                page = self.page(); call = next(r["calls"][0] for r in page["events"] if r["calls"])
                code, snap, raw, _ = self.tool_record(page, call)
                self.assertEqual(code, 200, snap)
                self.assertEqual(browser_decode(raw), snap)
                part = oc.raw_members(oc.raw_members(self.wire.messages[1])["content"])[0]
                node = node_snapshots([oc.raw_members(part)["state"]])[0]
                self.assertEqual([snap[k] for k in ("input", "output", "inputBytes", "outputBytes")],
                                 [node[k] for k in ("input", "output", "inputBytes", "outputBytes")])
                self.assertEqual((snap["inputBytes"], snap["outputBytes"]), (call["inputBytes"], call["outputBytes"]))
        for changes, expected in (({"fileEpoch": "oc_" + "0" * 32}, (409, "stale-epoch")), ({"callRef": "occall_" + "0" * 64}, (404, "missing")),
                                  ({"id": "../bad"}, (400, "schema")), ({"callRef": "bad/ref"}, (400, "schema"))):
            with self.subTest(changes=changes):
                code, obj, _, _ = self.tool_record(page, call, **changes)
                self.assertEqual((code, obj["status"]), expected)
        good = urlencode({"id": SID, "callRef": call["callRef"], "fileEpoch": page["fileEpoch"]})
        for query in ("", "id=" + SID, good + "&bogus=1", good + "&id=" + SID, good.replace("callRef=", "callRef=&x=")):
            with self.subTest(query=query):
                code, obj, _, _ = self.http("/api/opencode/tool-record?" + query)
                self.assertEqual((code, obj["status"]), (400, "schema"))
        self.assertEqual(self.http("/api/opencode/tool-record?" + good, method="POST", data={})[0], 405)
        before = len(self.upstream_calls)
        for origin in ("http://evil.invalid", "http://localhost:1"):
            code, obj, _, _ = self.http("/api/opencode/tool-record?" + good, origin=origin)
            self.assertEqual((code, obj["status"]), (403, "origin"))
        self.assertEqual(len(self.upstream_calls), before)  # refused before any OpenCode read

    def test_missing_session_invalid_envelope_and_busy_failure_are_distinct(self):
        def missing(path):
            if path=="/api/session/"+SID:
                raise opencode_programs.Unavailable("missing",code=404,status="missing")
        self.wire.hook=missing
        code,obj,_,_=self.http("/api/opencode/events?id="+SID)
        self.assertEqual((code,obj["status"]),(404,"missing"))
        self.wire.hook=None
        with patch.object(self.wire,"request",return_value=b'{"unexpected":[]}'):
            code,obj,_,_=self.http("/api/opencode/events?id="+SID)
        self.assertEqual((code,obj["status"]),(502,"read-failure")); self.assertNotIn("events",obj)
        self.wire.fail=lambda path: path=="/api/session/active"
        page=self.page()
        self.assertIsNone(page["busy"]); self.assertGreater(len(page["events"]),0)
        self.assertEqual(page["runningCalls"], [])
        self.wire.fail=None; self.wire.active=[]
        page=self.page()
        self.assertIsNone(page["busy"]); self.assertEqual(page["runningCalls"], [])   # malformed: unknown, not idle


if __name__ == "__main__": unittest.main()
