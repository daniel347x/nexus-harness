"""Whole V2 conversation, raw-size authority, cursor/topology and byte-bound contracts."""
import copy
import json
import pathlib
import subprocess
import sys
import threading
import types
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import opencode_conversation as oc
from opencode_programs import Unavailable

SID = "ses_fixture0001"


def fixture():
    text = (HERE / "opencode-conversation-wire.json").read_text()
    fields = oc.raw_members(text)
    return json.loads(fields["session"]), list(oc.raw_members(fields["messages"]).values())


def node_snapshots(corpus):
    js = """require(process.argv[1]); const fs=require('fs');
    const rows=JSON.parse(fs.readFileSync(0,'utf8'));
    console.log(JSON.stringify(rows.map(raw=>{const v=opcToolText.toolSnapshotText(JSON.parse(raw),opcToolText.rawMembers(raw));
    return Object.fromEntries(['input','output','status','pending','hasInput','hasOutput','hasError','inputBytes','outputBytes'].map(k=>[k,v[k]]));})));"""
    path = HERE.parents[1] / "chrome-extension/codenomad-session-watcher/tool-record-text.js"
    result = subprocess.run(["node", "-e", js, str(path)], input=json.dumps(corpus), text=True, capture_output=True, timeout=15, check=True)
    return json.loads(result.stdout)


def message(n, kind="user", **extra):
    return {"id": "msg_" + str(n), "type": kind, "text": "row " + str(n), "time": {"created": 1792390000000 + n}, **extra}


def surrogate_cases(cut="\ud83d"):
    """The eight WO-1 faces, plus each recorded-detail encoding branch."""
    text = "before 汉😀 " + cut + " after\nliteral \\ud83d"
    states = {
        "i-output": {"status": "completed", "input": {"command": "ls"}, "output": text},
        "ii-input": {"status": "completed", "input": text, "output": "done"},
        "iii-content": {"status": "completed", "input": {}, "content": [{"type": "text", "text": text}]},
        "iv-error": {"status": "error", "input": {}, "error": text, "output": "partial"},
    }
    cases = {name: message(1, "assistant", text="", finish="tool-calls", content=[{
        "type": "tool", "id": "prt_cut", "name": "shell", "state": state}]) for name, state in states.items()}
    for name, typ in (("v-text", "text"), ("vi-reasoning", "reasoning"), ("unknown-part", "future-part")):
        cases[name] = message(1, "assistant", text="", finish="tool-calls", content=[{"type": typ, "text": text}])
    cases["vii-user"] = message(1, text=text)
    cases["viii-flag"] = message(1, "compaction", summary=text)
    cases["viii-unknown"] = message(1, "future-message", detail=text)
    cases["viii-shell"] = message(1, "shell", output={"output": text})
    cases["assistant-state"] = message(1, "assistant", text="", finish="tool-calls", content=[], error=text)
    return cases


class Wire:
    """Opaque returned-cursor API; no order on continuation, even for a short final page."""
    def __init__(self, messages=None):
        self.session, initial = fixture()
        self.messages = initial if messages is None else [json.dumps(m) if isinstance(m, dict) else m for m in messages]
        self.calls, self.active, self.fail, self.hook = [], {SID: {"type": "running"}}, None, None
        self.cursors, self.serial = {}, 0

    def request(self, path, timeout, *, raw=False):
        self.calls.append(path)
        if self.hook:
            self.hook(path)
        if self.fail and self.fail(path):
            raise Unavailable("offline")
        parsed = urlsplit(path)
        if parsed.path == "/api/session/active":
            data = json.dumps({"data": self.active}, separators=(",", ":")).encode()
        elif parsed.path.endswith("/message"):
            q = parse_qs(parsed.query)
            limit = int(q["limit"][0])
            if "cursor" in q:
                assert set(q) == {"cursor", "limit"}, "opaque continuation must not repeat order"
                order, start = self.cursors[q["cursor"][0]]
            else:
                order, start = q["order"][0], 0
            items = self.messages if order == "asc" else list(reversed(self.messages))
            page = items[start:start + limit]
            # Deliberately issue next on the last NONEMPTY page too. Only an
            # exhausted empty page proves exhaustion to this API double.
            nxt = None
            if page:
                self.serial += 1
                nxt = "opaque:" + str(self.serial)
                self.cursors[nxt] = order, start + len(page)
            data = ('{"data":[' + ",".join(page) + '],"cursor":{"next":' + json.dumps(nxt) + '}}').encode()
        elif "/message/" in parsed.path:
            mid = parsed.path.rsplit("/", 1)[1]
            m = next((m for m in self.messages if json.loads(m)["id"] == mid), None)
            if m is None:
                raise Unavailable("missing", code=404, status="missing")
            data = ('{"data":' + m + '}').encode()
        else:
            session = {**self.session, "id": parsed.path.rsplit("/", 1)[1]}
            # Wire fixtures must accept escaped UTF-16 independently of the reader's encoder.
            data = json.dumps({"data": session}, separators=(",", ":")).encode()
        return data if raw else json.loads(data)

    def set(self, index, **changes):
        obj = json.loads(self.messages[index]); obj.update(changes); self.messages[index] = json.dumps(obj)


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.wire = Wire()
        self.reader = oc.OpenCodeConversation(self.wire.request)
        self.page = self.reader.events(id=SID, fromSeq=0)
        self.rows = self.page["events"]

    def row(self, ref):
        return next(r for r in self.rows if r["uuid"] == ref)

    def test_frozen_handoff_envelope_matches_raw_wire(self):
        reader=oc.OpenCodeConversation(Wire().request)
        with patch.object(oc.uuid,"uuid4",return_value=types.SimpleNamespace(hex="1"*32)):
            current=reader.events(id=SID,fromSeq=0)
        frozen=json.loads((HERE/"opencode-conversation-events.json").read_text())
        self.assertIsInstance(current.pop("observedAt"), float)  # live observation clock, not frozen history
        self.assertEqual(current,frozen)

    def test_complete_envelope_and_every_source_slot(self):
        expected = ["msg_user_m"] + ["msg_work_p" + str(i) for i in range(7)] + [
            "msg_empty_user_m", "msg_synthetic_m", "msg_system_m", "msg_skill_m", "msg_shell_m", "msg_compact_m",
            "msg_agent_m", "msg_model_m", "msg_location_m", "msg_final_p0", "msg_idle_m", "msg_unknown_m",
            "msg_error_p0", "msg_error_state", "msg_interrupted_m", "msg_started_p0", "msg_started_p1", "msg_started_p2"]
        self.assertEqual([r["uuid"] for r in self.rows], expected)
        self.assertEqual([r["seq"] for r in self.rows], list(range(len(expected))))
        for r in self.rows:
            self.assertEqual(r["recordRef"], r["uuid"])
            self.assertEqual(type(r["toolResults"]), int)
            self.assertEqual(r["toolResults"], 0)
            self.assertTrue(oc.safe_ref(r["recordRef"]))
        self.assertEqual(self.row("msg_empty_user_m")["text"], "")
        self.assertEqual(self.page["backend"], "opencode")
        self.assertEqual(self.page["sessionRef"], SID)
        self.assertEqual(self.page["scopeRef"], "main")
        for k in ("branches", "traces", "subagentActivity", "summaryUpdates", "diagnostics", "eventUpdates"):
            self.assertEqual(self.page[k], [])
        for k in ("leafRef", "targetRef", "indexLimit"):
            self.assertIsNone(self.page[k])
        for k in ("contextReadout", "indexingContinuation", "targetInfo", "executionId", "generation", "liveState",
                  "incidents", "subagents", "indexRevision", "snapshotRevision", "sourceProgress"):
            self.assertNotIn(k, self.page)

    def test_reasoning_empty_encrypted_unfinished_and_no_provider_envelopes(self):
        self.assertEqual(self.row("msg_work_p0")["thinking"][0]["text"], "Recorded reasoning.")
        self.assertEqual(self.row("msg_work_p1")["thinking"][0]["availability"], "redacted")
        self.assertIsNone(self.row("msg_work_p1")["thinking"][0]["text"])
        self.assertEqual(self.row("msg_work_p2")["thinking"][0]["text"], "")
        live = self.row("msg_started_p0")
        self.assertTrue(live["partial"])
        self.assertEqual(live["thinking"], [{"blockIndex": 0, "availability": "stored", "streaming": True, "text": ""}])
        self.assertNotIn("TEST_SENTINEL", json.dumps(self.page))
        self.assertIn("ordinary input key", json.dumps(self.page))

    def test_per_block_metadata_time_and_call_child(self):
        row = self.row("msg_work_p4"); call = row["calls"][0]
        self.assertEqual(row["time"], 1792390003)
        self.assertEqual(self.row("msg_work_p3")["time"], 1792390002)
        self.assertEqual((row["model"], row["effort"], row["agent"]), ("block-model", "xhigh", "quick-worker"))
        self.assertEqual(call["childSessionId"], "ses_child000001")
        self.assertEqual(call["status"], "stored")  # metadata.status running is not call status
        self.assertEqual((call["messageId"], call["partIndex"], call["block"], call["id"]), ("msg_work", 4, 4, "prt_real"))
        self.assertEqual(call["inputBytes"], len('{ "n":1e+02, "zero":-0, "providerState":"ordinary input key" }'.encode()))
        self.assertEqual(self.row("msg_started_p2")["calls"][0]["status"], "pending-or-missing")
        self.assertIsNone(self.row("msg_started_p2")["calls"][0]["outputBytes"])

    def test_labelled_meta_shell_unknown_error_and_malformed_tool(self):
        for kind in ("synthetic", "system", "skill"):
            r = self.row("msg_" + kind + "_m")
            self.assertEqual(r["kind"], kind); self.assertTrue(r["isMeta"]); self.assertIn("Full " + kind + " text", r["text"])
        shell = self.row("msg_shell_m")
        self.assertEqual(shell["kind"], "shell"); self.assertEqual(shell["calls"], [])
        for text in ("printf hello", "hello\nall stored output", '"truncated": false', '"cursor": 23', 'exit: 0'):
            self.assertIn(text, shell["text"])
        self.assertIn("future-message", self.row("msg_unknown_m")["text"])
        self.assertIn("Unknown parts stay readable", self.row("msg_work_p5")["text"])
        self.assertEqual(self.row("msg_work_p6")["calls"], [])
        self.assertIn("no unique part id", self.row("msg_work_p6")["text"])
        self.assertIn('"attempt": 2', self.row("msg_error_state")["text"])
        self.assertIn("failed step", self.row("msg_error_state")["text"])
        for mid, kind in (("compact", "compaction"), ("agent", "agent-switched"), ("model", "model-switched"), ("location", "location-switched")):
            row = self.row("msg_" + mid + "_m")
            self.assertEqual(row["kind"], "task"); self.assertEqual(row["originKind"], kind)
            self.assertTrue(row["text"].startswith("description: OpenCode · " + kind))

    def test_receipt_idle_cost_and_last_step_tokens_not_summed(self):
        r = self.row("msg_idle_m")["receipt"]
        self.assertEqual(r["total_cost_usd"], .75)
        self.assertEqual(r["usage"], {"input_tokens": 99, "output_tokens": 8, "reasoning_tokens": 7,
                                     "cache_read_input_tokens": 6, "cache_creation_input_tokens": 5})
        self.assertFalse(r["is_error"]); self.assertNotIn("origin", r)
        self.assertIsNone(self.row("msg_interrupted_m")["receipt"]["is_error"])
        self.assertNotIn("msg_final_r", [r["uuid"] for r in self.rows])
        for steps, cost in (([{"type":"assistant","cost":0}], 0), ([{"type":"assistant"}], None),
                            ([{"type":"assistant","cost":1},{"type":"assistant"}], None)):
            self.assertEqual(oc.receipt(steps, "failed")["total_cost_usd"], cost)
        self.assertTrue(oc.receipt([], "failed")["is_error"])
        self.assertIsNone(oc.receipt([], "future-outcome")["is_error"])

    def test_tool_states_duplicates_and_unusual_values_do_not_drop_parts(self):
        parts = [{"type":"tool", "id":"p" + str(i), "state":{"status":status, "input":False, "content":[]}}
                 for i, status in enumerate(("streaming", "running", "completed", "error", "future", None))]
        parts.extend([{"type":"tool","id":"dup"}, {"type":"tool","id":"dup"}])
        source = oc.Message.read(json.dumps(message(1, "assistant", content=parts, text="")))
        rows = oc.build_rows(SID, [source])
        self.assertEqual(len(rows), 8)
        self.assertEqual([r["calls"][0]["status"] for r in rows[:4]], ["not-final-fragment", "pending-or-missing", "stored", "error"])
        self.assertIn("unknown", rows[4]["calls"][0]["status"])
        self.assertEqual([r["calls"] for r in rows[-2:]], [[], []])

    def test_empty_user_immediately_after_tool_is_never_a_folded_carrier(self):
        sources = [oc.Message.read(json.dumps(message(0, "assistant", content=[{
            "type":"tool","id":"p","name":"shell","state":{"status":"completed","input":{},"content":[]}}]))),
                   oc.Message.read(json.dumps(message(1, text="")))]
        rows=oc.build_rows(SID,sources)
        self.assertEqual(len(rows),2); self.assertEqual(len(rows[0]["calls"]),1)
        self.assertEqual(rows[1]["kind"],"user"); self.assertEqual(rows[1]["text"],"")
        self.assertEqual(rows[1]["toolResults"],0); self.assertIs(type(rows[1]["toolResults"]),int)

    def test_preview_refusal_does_not_change_raw_snapshot_size(self):
        for payload in ('{"a":1,"a":2}', "[" * 140 + "0" + "]" * 140, '{"big":900719925474099312345}'):
            state = '{"input":' + payload + ',"content":[]}'
            self.assertEqual(oc.input_preview(state)["status"], "unavailable")
            self.assertEqual(oc.snapshot_text(state)["inputBytes"], len(payload.encode()))

    def test_ref_digest_and_worst_case_cursor_length(self):
        mid = "unsafe identifier/" * 30
        rows = oc.build_rows(SID, [oc.Message.read(json.dumps(message(1, id=mid)))])
        self.assertTrue(oc.safe_ref(rows[0]["uuid"]))
        cursor = self.reader._cursor("ses_" + "z" * 64, "oc_" + "f" * 32, oc.SAFE_INTEGER, "d", oc.SAFE_INTEGER)
        self.assertTrue(oc.safe_ref(cursor)); self.assertLessEqual(len(cursor), 256)
        self.assertEqual(self.reader._decode("ses_" + "z" * 64, cursor, "oc_" + "f" * 32), (oc.SAFE_INTEGER, "d", oc.SAFE_INTEGER))


class RunningCallTests(unittest.TestCase):
    def assistant(self, n, parts, **extra):
        return oc.Message.read(json.dumps(message(n, "assistant", content=parts, **extra)))

    def part(self, tool_id, status="running", name="shell", command="pytest -q"):
        state = {"status": status, "input": ({"command": command} if command else {})}
        if status in ("completed", "error"):
            state["output"] = "stored output that is never reported"
        return {"type": "tool", "id": tool_id, "name": name, "state": state}

    def test_parallel_siblings_survive_the_last_one_finishing_and_only_visible_facts_are_reported(self):
        first, second = self.part("call_shell", command="long parallel shell"), self.part("call_read", name="read", command=None)
        messages = [self.assistant(0, [first, second])]
        calls = oc.running_calls(SID, messages, True)
        self.assertEqual([call["id"] for call in calls], ["call_shell", "call_read"])
        self.assertEqual({key: calls[0][key] for key in ("name", "status", "resultTime", "outputBytes", "command")},
                         {"name": "shell", "status": "pending-or-missing", "resultTime": None,
                          "outputBytes": None, "command": "long parallel shell"})
        self.assertNotIn("command", calls[1])
        self.assertNotIn("output", calls[0])
        self.assertEqual(set(calls[0]), set(oc.RUNNING_FIELDS) | {"command"})
        # The last sibling finishes first: the earlier long call is still the current turn's open call.
        second["state"] = {"status": "completed", "input": {}, "output": "done"}
        self.assertEqual([call["id"] for call in oc.running_calls(SID, [self.assistant(0, [first, second])], True)],
                         ["call_shell"])
        # A completed tool is never mapped (its output body is not even parsed).
        with patch.object(oc, "snapshot_text", wraps=oc.snapshot_text) as snapshot:
            self.assertEqual(len(oc.running_calls(SID, [self.assistant(0, [first, second])], True)), 1)
        self.assertEqual(snapshot.call_count, 1)
        self.assertEqual(oc.running_calls(SID, [self.assistant(0, [first], finish="tool-calls")], True), [])
        for status in ("completed", "error"):
            calls = oc.running_calls(SID, [self.assistant(0, [self.part("call_done", status)])], True)
            self.assertEqual(calls, [])

    def test_busy_must_be_true_and_the_latest_idle_starts_a_new_turn(self):
        old, idle = self.assistant(0, [self.part("call_old")]), oc.Message.read(json.dumps(message(1, "idle")))
        new = self.assistant(2, [self.part("call_new")])
        for busy in (False, None):
            self.assertEqual(oc.running_calls(SID, [old, idle, new], busy), [])
        self.assertEqual(oc.running_calls(SID, [old, idle], True), [])
        self.assertEqual([call["id"] for call in oc.running_calls(SID, [old, idle, new], True)], ["call_new"])
        self.assertEqual([call["id"] for call in oc.running_calls(
            SID, [old, idle, oc.Message.read(json.dumps(message(3, "user"))), new], True)], ["call_new"])

    def test_streaming_is_current_activity_and_long_commands_keep_wait_identity(self):
        streaming = self.part("call_stream", "streaming", command="")
        calls = oc.running_calls(SID, [self.assistant(0, [streaming])], True)
        self.assertEqual((calls[0]["status"], calls[0]["inputBytes"], calls[0]["outputBytes"]),
                         ("not-final-fragment", 2, None))       # "{}" is two stored UTF-8 bytes
        full_command = "x" * 2500 + "; bash /scripts/oc_seat.sh wait ses_longwait01 --max 540"
        command = self.part("call_long", command=full_command)
        calls = oc.running_calls(SID, [self.assistant(0, [command])], True)
        self.assertEqual(calls[0]["command"], full_command)

    def test_a_descending_page_is_read_chronologically(self):
        old, idle = self.assistant(0, [self.part("call_old")]), oc.Message.read(json.dumps(message(1, "idle")))
        new = self.assistant(2, [self.part("call_new")])
        page = [new, idle, old]                                    # the API's order=desc page
        self.assertEqual([call["id"] for call in oc.running_calls(SID, page, True)], ["call_old"])  # an ascending read would pick the closed turn
        self.assertEqual([call["id"] for call in oc.running_calls(SID, page, True, newest_first=True)], ["call_new"])


class EnvelopeRunningCallTests(unittest.TestCase):
    def test_the_events_envelope_carries_the_current_turn_and_follows_busy(self):
        reader = oc.OpenCodeConversation(Wire().request)
        page = reader.events(id=SID, fromSeq=0)
        self.assertTrue(page["busy"])
        self.assertEqual([call["id"] for call in page["runningCalls"]], ["prt_running"])
        self.assertEqual(page["runningCalls"][0]["command"], "work")
        self.assertEqual(page["runningCalls"], reader.cache[SID].running)
        reader = oc.OpenCodeConversation(Wire().request)
        with patch.object(reader, "_busy", return_value=None):
            page = reader.events(id=SID, fromSeq=0)
        self.assertIsNone(page["busy"])
        self.assertEqual(page["runningCalls"], [])
        # A malformed /api/session/active shape is unknown, never an idle claim.
        wire = Wire()
        wire.active = []
        page = oc.OpenCodeConversation(wire.request).events(id=SID, fromSeq=0)
        self.assertIsNone(page["busy"])
        self.assertEqual(page["runningCalls"], [])


class SnapshotTests(unittest.TestCase):
    def test_direct_snapshot_normalizes_recorded_files_without_changing_raw_contract(self):
        from test_tool_diff import opencode_fixture
        from tool_diff import opencode_diff
        f = opencode_fixture()
        for status in ('completed','pending','error'):
            with self.subTest(status=status):
                state = {**f['state'],'status':status}
                if status == 'error': state['error'] = 'failure'
                part = {'type':'tool','id':'prt_diff','name':'patch','state':state}
                wire = Wire([message(1,'assistant',text='',content=[part])])
                reader = oc.OpenCodeConversation(wire.request)
                page = reader.events(id=SID); call = page['events'][0]['calls'][0]
                snap = reader.snapshot(id=SID,callRef=call['callRef'],fileEpoch=page['fileEpoch'])
                self.assertEqual(snap['diff'],opencode_diff('patch',state))
                raw_state = oc.raw_members(oc.raw_members(wire.messages[0])['content'])[0]
                raw_state = oc.raw_members(raw_state)['state']
                raw = oc.snapshot_text(raw_state)
                self.assertEqual([snap[k] for k in ('input','inputBytes','outputBytes')],[raw[k] for k in ('input','inputBytes','outputBytes')])
                self.assertEqual(snap['output'],raw['output'])
                self.assertNotIn('diff',raw); self.assertNotIn('diff',call)
                if status == 'completed': self.assertEqual(snap['diff']['files'][0]['hunks'][1]['newStart'],900)
                else: self.assertFalse(snap['diff']['complete'])

    def test_actual_packaged_js_differential_corpus(self):
        corpus = (HERE / "opencode-snapshot-corpus.jsonl").read_text().splitlines()
        self.assertEqual([oc.snapshot_text(raw) for raw in corpus], node_snapshots(corpus))


class SurrogateTests(unittest.TestCase):
    def test_every_face_cold_tail_first_and_delta_preserves_dense_row_identities(self):
        for cut in ("\ud83d", "\ude00"):
            controls = surrogate_cases("\ud83d\ude00")
            for face, source in surrogate_cases(cut).items():
                with self.subTest(face=face, cut=ascii(cut)):
                    messages = [message(0), source, message(2)]
                    wire = Wire(messages)
                    reader = oc.OpenCodeConversation(wire.request)
                    control = oc.OpenCodeConversation(Wire([message(0), controls[face], message(2)]).request)
                    expected = control.events(id=SID)["events"]
                    tail = reader.events(id=SID)
                    first = reader.events(id=SID, fromSeq=0)
                    for page in (tail, first):
                        self.assertEqual([r["uuid"] for r in page["events"]], [r["uuid"] for r in expected])
                        self.assertEqual([r["seq"] for r in page["events"]], list(range(len(expected))))
                        self.assertTrue(all(r["status"] == "stored" for r in page["events"]))
                        self.assertEqual(json.loads(oc.encoded(page)), page)
                        for row in page["events"]:
                            self.assertEqual(row["textBytes"], len(row["text"].replace(cut, "\ufffd").encode()))
                    self.assertEqual(tail["events"], first["events"])
                    self.assertEqual(first["events"], reader.cache[SID].rows)
                    if face in ("i-output", "ii-input", "iii-content", "iv-error"):
                        state_wire = oc.raw_members(oc.raw_members(json.dumps(source))["content"])[0]
                        state_wire = oc.raw_members(state_wire)["state"]
                        snapshot = node_snapshots([state_wire])[0]
                        call = first["events"][1]["calls"][0]
                        self.assertEqual((call["inputBytes"], call["outputBytes"]),
                                         (snapshot["inputBytes"], snapshot["outputBytes"]))
                    wire.messages.append(json.dumps(message(3)))
                    delta = reader.events(id=SID, fileEpoch=tail["fileEpoch"], cursor=tail["deltaCursor"])
                    combined = first["events"] + delta["events"]
                    self.assertEqual([r["seq"] for r in combined], list(range(len(expected) + 1)))
                    self.assertEqual(len({r["uuid"] for r in combined}), len(combined))
                    self.assertEqual(combined, reader.cache[SID].rows)
                    self.assertEqual(delta["eventUpdates"], [])

    def test_cut_arriving_in_live_snapshot_keeps_delta_replacement_and_append(self):
        for face, source in surrogate_cases().items():
            with self.subTest(face=face):
                wire = Wire([message(0), surrogate_cases("😀")[face]])
                reader = oc.OpenCodeConversation(wire.request)
                tail = reader.events(id=SID)
                wire.messages[1] = json.dumps(source)
                wire.messages.append(json.dumps(message(2)))
                delta = reader.events(id=SID, fileEpoch=tail["fileEpoch"], cursor=tail["deltaCursor"])
                self.assertEqual(delta["fileEpoch"], tail["fileEpoch"])
                updates = {r["uuid"]: r for r in delta["eventUpdates"]}
                combined = [updates.get(r["uuid"], r) for r in tail["events"]] + delta["events"]
                self.assertEqual(combined, reader.cache[SID].rows)
                self.assertEqual([r["seq"] for r in combined], list(range(len(combined))))
                self.assertTrue(updates)

    def test_metadata_and_non_ref_message_id_keep_stored_strings(self):
        wire = Wire([message(0, id="msg_\ud83d", text="cut \ude00", agent="agent \ud83d",
                             model={"id": "model \ude00", "variant": "effort \ud83d"})])
        wire.session.update(title="title \ud83d", agent="agent \ude00", model={"id": "model \ud83d"})
        wire.session["location"]["directory"] = "/fixture/\ude00"
        reader = oc.OpenCodeConversation(wire.request)
        page = reader.events(id=SID)
        self.assertEqual(page["sessionInfo"]["title"], wire.session["title"])
        self.assertEqual(page["sessionInfo"]["directory"], wire.session["location"]["directory"])
        self.assertEqual(page["events"][0]["text"], "cut \ude00\n\nOpenCode · recorded facts\n" + oc.detail({
            "agent": "agent \ud83d", "model": {"id": "model \ude00", "variant": "effort \ud83d"}}))
        self.assertEqual(json.loads(oc.encoded(page)), page)
        self.assertTrue(oc.safe_ref(page["events"][0]["uuid"]))
        self.assertNotEqual(page["events"][0]["uuid"], oc.row_ref("msg_\\ud83d", "m"))


class PagingTests(unittest.TestCase):
    def setup_reader(self, messages, **limits):
        self.wire = Wire(messages)
        self.reader = oc.OpenCodeConversation(self.wire.request, **limits)
        return self.reader.events(id=SID)

    def error(self, status, **query):
        with self.assertRaises(oc.ReaderError) as e:
            self.reader.events(id=SID, **query)
        self.assertEqual(e.exception.status, status)
        return e.exception

    def test_cold_tail_and_all_older_rows_no_first_n_limit(self):
        page = self.setup_reader([message(i, "assistant", content=[{"type":"text","text":str(i)}, {"type":"reasoning","text":"r"}], finish="tool-calls") for i in range(437)])
        self.assertEqual([r["seq"] for r in page["events"]], list(range(774, 874)))
        self.assertFalse(page["hasMore"]); self.assertFalse(page["rowsAhead"])
        index = self.reader.cache[SID]
        rows, p = [], self.reader.events(id=SID, fromSeq=0)
        while True:
            self.assertLessEqual(len(p["events"]), 100); rows.extend(p["events"])
            if not p["hasMore"]: break
            p = self.reader.events(id=SID, fileEpoch=p["fileEpoch"], cursor=p["nextCursor"])
        self.assertEqual(rows, index.rows)
        self.assertEqual(len(set(r["uuid"] for r in rows)), 874)
        asc = [p for p in self.wire.calls if "order=asc" in p]
        self.assertGreaterEqual(len(asc), 2)  # older cached positional ranges were revalidated
        self.assertEqual(self.reader.events(id=SID, fromSeq=9999)["events"], [])

    def test_append_over_twenty_messages_follows_cursor_to_overlap(self):
        page = self.setup_reader([message(i) for i in range(60)])
        self.wire.messages.extend(json.dumps(message(i)) for i in range(60, 105))
        delta = self.reader.events(id=SID, fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        self.assertEqual([r["seq"] for r in delta["events"]], list(range(60, 105)))
        self.assertEqual(delta["fileEpoch"], page["fileEpoch"])
        self.assertEqual(delta["change"], "append")
        self.assertGreaterEqual(sum("cursor=" in p and "limit=20" in p for p in self.wire.calls), 2)

    def test_partial_final_same_identity_full_replacement_and_older_tool(self):
        tool = message(0, "assistant", content=[{"type":"tool","id":"prt_0","name":"shell","state":{"status":"running","input":{}}}])
        page = self.setup_reader([tool] + [message(i) for i in range(1, 45)])
        self.wire.set(0, content=[{"type":"tool","id":"prt_0","name":"shell","state":{"status":"completed","input":{},"content":[]}}], finish="tool-calls")
        delta = self.reader.events(id=SID, fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        self.assertEqual(delta["events"], [])
        self.assertEqual(len(delta["eventUpdates"]), 1)
        update = delta["eventUpdates"][0]
        self.assertEqual(update["uuid"], "msg_0_p0"); self.assertFalse(update["partial"])
        self.assertEqual(update["calls"][0]["outputBytes"], 0)
        self.assertTrue(any("/message/msg_0" in p for p in self.wire.calls))
        repeated = self.reader.events(id=SID, fileEpoch=page["fileEpoch"], cursor=delta["deltaCursor"])
        self.assertEqual(repeated["eventUpdates"], [])

    def test_empty_step_first_part_replaces_slot(self):
        page = self.setup_reader([message(0, "assistant", content=[])])
        self.wire.set(0, content=[{"type":"text","text":"new block"}])
        delta = self.reader.events(id=SID, fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        self.assertEqual(delta["fileEpoch"], page["fileEpoch"])
        self.assertEqual(delta["eventUpdates"][0]["uuid"], "msg_0_p0")
        self.assertEqual(delta["eventUpdates"][0]["text"], "new block")

    def test_missing_middle_in_observed_tail_changes_epoch_not_union(self):
        page = self.setup_reader([message(i) for i in range(30)])
        del self.wire.messages[22]
        self.error("changed-index", fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        fresh = self.reader.events(id=SID, fromSeq=0)
        self.assertNotEqual(fresh["fileEpoch"], page["fileEpoch"])
        self.assertNotIn("msg_22_m", [r["uuid"] for r in fresh["events"]])
        self.error("stale-epoch", fileEpoch=page["fileEpoch"], fromSeq=0)

    def test_unobserved_middle_is_revalidated_before_old_range_served(self):
        page = self.setup_reader([message(i) for i in range(80)])
        del self.wire.messages[10]
        tail = self.reader.events(id=SID, fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        self.assertEqual(tail["fileEpoch"], page["fileEpoch"])  # explicitly outside observation boundary
        self.error("changed-index", fromSeq=0, fileEpoch=page["fileEpoch"])
        self.assertNotIn("msg_10_m", [r["uuid"] for r in self.reader.events(id=SID, fromSeq=0)["events"]])

    def test_compaction_revalidates_deep_history(self):
        page = self.setup_reader([message(i) for i in range(80)])
        del self.wire.messages[10]
        self.wire.messages.append(json.dumps(message(80, "compaction", summary="summary", recent="recent")))
        self.error("changed-index", fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        self.assertTrue(any("order=asc" in p for p in self.wire.calls[5:]))

    def test_suffix_revert_reorder_and_part_slots_require_resync(self):
        for action in ("revert", "reorder", "insert", "remove", "state"):
            with self.subTest(action=action):
                page = self.setup_reader([message(0, "assistant", content=[{"type":"text","text":"x"},{"type":"tool","id":"p","state":{}}]), message(1)])
                if action == "revert": self.wire.messages.pop()
                if action == "reorder": self.wire.messages.reverse()
                if action in ("insert", "remove"):
                    m = json.loads(self.wire.messages[0]); parts=m["content"]
                    if action == "insert": parts.insert(0, {"type":"reasoning","text":"new"})
                    else: parts.pop()
                    self.wire.set(0, content=parts)
                if action == "state": self.wire.set(0, error="new observed state")
                self.error("changed-index", fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])

    def test_idle_replaces_provisional_legacy_receipt_via_epoch(self):
        page = self.setup_reader([message(0, "assistant", content=[{"type":"text","text":"answer"}], finish="stop", cost=0)])
        self.assertEqual([r["uuid"] for r in page["events"]], ["msg_0_p0", "msg_0_r"])
        self.wire.messages.append(json.dumps(message(1, "idle", outcome="succeeded")))
        self.error("changed-index", fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        fresh = self.reader.events(id=SID)
        self.assertEqual(sum(r["kind"] == "result" for r in fresh["events"]), 1)

    def test_empty_then_growth_restart_and_lru_eviction(self):
        page = self.setup_reader([], cache_sessions=1)
        self.assertEqual(page["events"], []); self.assertTrue(oc.safe_ref(page["deltaCursor"]))
        self.wire.messages.append(json.dumps(message(0)))
        growth = self.reader.events(id=SID, fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        self.assertEqual(len(growth["events"]), 1)
        self.reader.events(id="ses_other00001")
        self.error("stale-epoch", fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        new = oc.OpenCodeConversation(self.wire.request)
        with self.assertRaises(oc.ReaderError) as e: new.events(id=SID, fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        self.assertEqual(e.exception.status, "stale-epoch")
        self.assertEqual(new.events(id=SID, fromSeq=0)["events"][0]["uuid"], "msg_0_m")

    def test_aggregate_lru_accounts_wire_parsed_records_and_projection(self):
        self.setup_reader([message(0,text="x"*1000)])
        retained=self.reader.cache[SID].retained
        self.assertGreater(retained,self.reader.cache[SID].raw_bytes*2)
        reader=oc.OpenCodeConversation(self.wire.request,cache_limit=retained*3//2)
        page=reader.events(id=SID)
        reader.events(id="ses_other00001")
        self.assertNotIn(SID,reader.cache)
        with self.assertRaises(oc.ReaderError) as e:
            reader.events(id=SID,fileEpoch=page["fileEpoch"],fromSeq=0)
        self.assertEqual(e.exception.status,"stale-epoch")

    def test_schema_cursors_and_session_identity(self):
        page = self.setup_reader([])
        for query in ({"fromSeq":True}, {"fromSeq":-1}, {"fromSeq":oc.SAFE_INTEGER+1}, {"cursor":"bad"},
                      {"fileEpoch":"bad/epoch"}, {"fileEpoch":page["fileEpoch"],"cursor":"bad"},
                      {"fileEpoch":page["fileEpoch"],"cursor":page["deltaCursor"],"fromSeq":0}):
            with self.subTest(query=query): self.error("schema", **query)
        other = self.reader._cursor("ses_other00001", page["fileEpoch"], 0, "d", 0)
        self.error("schema", fileEpoch=page["fileEpoch"], cursor=other)

    def test_update_overflow_no_advance_and_position_resync_recovers(self):
        page = self.setup_reader([message(i, "assistant", content=[{"type":"text","text":""}]) for i in range(105)])
        for i in range(105): self.wire.set(i, content=[{"type":"text","text":"completed"}], finish="tool-calls")
        self.error("reload-required", fileEpoch=page["fileEpoch"], cursor=page["deltaCursor"])
        fresh = self.reader.events(id=SID, fromSeq=0)
        self.assertEqual(len(fresh["events"]), 100)
        resumed = self.reader.events(id=SID, fileEpoch=fresh["fileEpoch"], cursor=fresh["nextCursor"])
        self.assertEqual([r["seq"] for r in resumed["events"]], list(range(100,105)))
        self.assertEqual(resumed["eventUpdates"], [])

    def test_byte_update_overflow_and_whole_small_budget_traversal(self):
        page=self.setup_reader([message(i,"assistant",content=[{"type":"text","text":"a"*1000}]) for i in range(20)],response_limit=6000)
        for i in range(8): self.wire.set(i,content=[{"type":"text","text":"b"*1000}],finish="tool-calls")
        self.error("reload-required",fileEpoch=page["fileEpoch"],cursor=page["deltaCursor"])
        rows=[]; page=self.reader.events(id=SID,fromSeq=0)
        while True:
            self.assertLessEqual(len(oc.encoded(page)),6000)
            rows.extend(page["events"])
            if not page["hasMore"]: break
            page=self.reader.events(id=SID,fileEpoch=page["fileEpoch"],cursor=page["nextCursor"])
        self.assertEqual([r["seq"] for r in rows],list(range(20)))
        self.assertTrue(all(len(r["text"])==1000 for r in rows))

    def test_row_page_upstream_and_session_bounds_are_visible_not_cropped(self):
        page = self.setup_reader([message(0, text="汉" * 6000), message(1)], response_limit=6000)
        limited = self.reader.events(id=SID, fromSeq=0)
        self.assertEqual(limited["events"][0]["status"], "response-limit")
        self.assertEqual(limited["events"][0]["uuid"], "msg_0_m")
        self.assertIsNone(limited["events"][0]["text"])
        self.assertEqual(limited["events"][0]["toolResults"], 0)
        self.assertLessEqual(len(oc.encoded(limited)), 6000)
        self.setup_reader([message(i, text="x" * 1000) for i in range(10)], response_limit=6000)
        page = self.reader.events(id=SID, fromSeq=0)
        self.assertLess(len(page["events"]), 10); self.assertTrue(page["hasMore"])
        self.assertEqual(page["events"][0]["text"], "x" * 1000)
        for limit, value, status in (("upstream_limit", 200, "response-limit"), ("session_limit", 1000, "session-limit")):
            reader = oc.OpenCodeConversation(Wire([message(0, text="x" * 3000)]).request, **{limit:value})
            with self.assertRaises(oc.ReaderError) as e: reader.events(id=SID)
            self.assertEqual(e.exception.status, status); self.assertIn("not cropped", str(e.exception)); self.assertFalse(reader.cache)
            self.assertIn(str(value),str(e.exception))

    def test_failure_never_publishes_prefix_or_erases_last_complete_cache(self):
        page = self.setup_reader([message(i) for i in range(150)])
        before = self.reader.cache[SID]
        self.wire.fail = lambda p: "cursor=" in p
        self.error("read-failure", fromSeq=0)
        self.assertIs(self.reader.cache[SID], before)
        cold = oc.OpenCodeConversation(self.wire.request)
        with self.assertRaises(oc.ReaderError): cold.events(id=SID)
        self.assertFalse(cold.cache)
        self.wire.fail = lambda p: p == "/api/session/active"
        self.assertIsNone(self.reader.events(id=SID)["busy"])
        self.wire.fail = None; self.wire.active = {}
        self.assertFalse(self.reader.events(id=SID)["busy"])

    def test_concurrent_refreshes_publish_serially(self):
        self.setup_reader([message(0)])
        entered, release, second_entered = threading.Event(), threading.Event(), threading.Event()
        once = [False]
        def hook(path):
            if "order=desc" in path and not once[0]:
                once[0] = True; entered.set(); release.wait(3)
            elif "order=desc" in path:
                second_entered.set()
        self.wire.hook = hook
        results=[]
        a=threading.Thread(target=lambda: results.append(self.reader.events(id=SID)))
        b=threading.Thread(target=lambda: results.append(self.reader.events(id=SID)))
        a.start(); self.assertTrue(entered.wait(2)); b.start()
        try:
            self.assertFalse(second_entered.wait(.05), "overlapping acquisitions must not enter upstream together")
            self.wire.messages.append(json.dumps(message(1)))
        finally:
            release.set(); a.join(3); b.join(3)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.reader.cache[SID].rows), 2)


if __name__ == "__main__":
    unittest.main()
