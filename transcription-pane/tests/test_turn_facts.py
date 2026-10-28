"""Model and thinking-level labels: only what each native record states, never a default or a guess."""
import http.client
import json
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_reader as cr
import server
from claude_live import ClaudeFacade
from test_claude_live import InertHost, UUID

SESSION = "00000000-0000-4000-8000-000000000001"


def uid(n):
    return str(uuid.UUID(int=n))


def said(n, model="claude-opus-5-5", effort="high", per_turn="same", parent=None, **extra):
    """One native assistant record as Claude Code 2.1.296+ writes it."""
    value = {"sessionId": SESSION, "uuid": uid(n), "parentUuid": uid(parent) if parent else None,
             "type": "assistant", "version": "synthetic-v1", "isSidechain": False, "cwd": "/fixture",
             "requestedModel": model,
             "message": {"role": "assistant", "id": "msg_" + str(n), "model": model,
                         "content": [{"type": "text", "text": "answer " + str(n)}]}, **extra}
    if effort is not None:
        value["effort"] = effort
    if per_turn is not None:
        value["perTurnEffort"] = effort if per_turn == "same" else per_turn
    return value


def human(n, parent=None):
    return {"sessionId": SESSION, "uuid": uid(n), "parentUuid": uid(parent) if parent else None, "type": "user",
            "version": "synthetic-v1", "isSidechain": False, "cwd": "/fixture", "origin": {"kind": "human"},
            "message": {"role": "user", "content": "question " + str(n)}}


def lines(rows):
    return "".join(json.dumps(r) + "\n" for r in rows)


class CallFactsTests(unittest.TestCase):
    def test_a_record_states_its_model_and_one_certain_level_or_nothing(self):
        self.assertEqual(cr.call_facts("claude-opus-5-5", "high", "high"), (True, "claude-opus-5-5", "high"))
        self.assertEqual(cr.call_facts("claude-opus-5-5", "high", None), (True, "claude-opus-5-5", "high"))
        self.assertEqual(cr.call_facts("claude-opus-5-5", None, "low"), (True, "claude-opus-5-5", "low"))
        # Two different levels on one record are no certain level.
        self.assertEqual(cr.call_facts("claude-opus-5-5", "high", "xhigh"), (True, "claude-opus-5-5", None))
        self.assertEqual(cr.call_facts("claude-opus-5-5", None, None), (True, "claude-opus-5-5", None))
        self.assertEqual(cr.call_facts("claude-opus-5-5", cr._INVALID, "high"), (True, "claude-opus-5-5", None))
        self.assertEqual(cr.call_facts("claude-opus-5-5", "HIGH; drop", None), (True, "claude-opus-5-5", None))
        # A level never stands without its model; synthetic and API-error records state no call.
        self.assertEqual(cr.call_facts(None, "high", "high"), (True, None, None))
        self.assertEqual(cr.call_facts("bad model id", "high", "high"), (True, None, None))
        self.assertEqual(cr.call_facts("<synthetic>", None, "low"), (False, None, None))
        self.assertEqual(cr.call_facts("claude-opus-5-5", "high", "high", api_error=True), (False, None, None))


class NativeFactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "project" / (SESSION + ".jsonl")
        self.path.parent.mkdir()
        self.reader = cr.ClaudeReader([{"id": "test", "root": str(self.root), "native": False}])

    def tearDown(self):
        self.temp.cleanup()

    def events(self):
        for _ in range(20):
            rows = self.reader.sessions()["sessions"]
            if rows and rows[0]["status"] == "complete":
                main = next(s for s in rows[0]["scopes"] if s["label"] == "main")
                return self.reader.events(sessionRef=rows[0]["sessionRef"], scopeRef=main["scopeRef"])
        self.fail("index did not finish")

    def test_each_block_carries_its_own_record_facts_across_a_mid_conversation_switch(self):
        self.path.write_text(lines([human(1), said(2, parent=1), human(3, 2),
                                    said(4, "claude-fable-5-1", "medium", parent=3),
                                    said(5, "claude-opus-5-5", None, None, parent=4),      # no level recorded
                                    said(6, "claude-opus-5-5", "high", "xhigh", parent=5),  # two levels: none certain
                                    said(7, "<synthetic>", None, None, parent=6)]))
        page = self.events()
        facts = {e["uuid"]: (e.get("model"), e.get("effort")) for e in page["events"] if e["kind"] == "assistant"}
        self.assertEqual(facts, {uid(2): ("claude-opus-5-5", "high"), uid(4): ("claude-fable-5-1", "medium"),
                                 uid(5): ("claude-opus-5-5", None), uid(6): ("claude-opus-5-5", None),
                                 uid(7): (None, None)})
        self.assertTrue(all("model" not in e for e in page["events"] if e["kind"] == "user"))

    def test_the_ledger_aggregates_only_fully_read_own_turns(self):
        self.path.write_text(lines([human(1), said(2, parent=1), said(3, "claude-fable-5-1", "medium", parent=2),
                                    said(4, "claude-opus-5-5", "low", isSidechain=True, parent=3),  # not its own turn
                                    said(5, "<synthetic>", None, None, parent=3)]))
        ledger, used = self.reader.turn_ledger("test", self.path)
        self.assertEqual(used, self.path.stat().st_size)
        self.assertEqual(ledger.summary(), {"status": "complete", "records": 2,
                                            "models": ["claude-opus-5-5", "claude-fable-5-1"],
                                            "levels": ["high", "medium"], "modelsComplete": True, "levelsComplete": True})
        self.assertEqual(ledger.entries[uid(3)], ("msg_3", "claude-fable-5-1", "medium"))
        self.assertNotIn(uid(4), ledger.entries)
        self.assertEqual(self.reader.turn_ledger("test", self.path)[1], 0)  # unchanged file: no read
        # An incomplete final record waits for its newline; until then the aggregate is not complete.
        with self.path.open("a") as stream:
            stream.write(json.dumps(said(6, "claude-sonnet-5-5", None, None, parent=3))[:40])
        summary = self.reader.turn_ledger("test", self.path)[0].summary()
        self.assertEqual((summary["status"], summary["modelsComplete"]), ("scanning", False))
        self.path.write_text(lines([human(1), said(2, parent=1), said(3, "claude-fable-5-1", "medium", parent=2),
                                    said(4, "claude-opus-5-5", "low", isSidechain=True, parent=3),
                                    said(5, "<synthetic>", None, None, parent=3),
                                    said(6, "claude-sonnet-5-5", None, None, parent=3)]))
        summary = self.reader.turn_ledger("test", self.path)[0].summary()
        # A turn without a recorded level: the models stay certain, the level list does not.
        self.assertEqual((summary["models"], summary["modelsComplete"], summary["levelsComplete"]),
                         (["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5"], True, False))
        # A rewrite of what was already read starts over rather than trusting stale facts.
        self.path.write_text(lines([human(1), said(2, "claude-haiku-5-5", "low", parent=1)]) + " " * 2000 + "\n")
        summary = self.reader.turn_ledger("test", self.path)[0].summary()
        self.assertEqual((summary["models"], summary["levels"]), (["claude-haiku-5-5"], ["low"]))

    def test_the_ledger_records_the_first_and_last_own_turn_times(self):
        stamp = lambda minute: {"timestamp": f"2026-10-15T23:{minute:02d}:00.000Z"}
        result = {**human(3, 2), **stamp(5), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}
        rows = [{"type": "queue-operation", "operation": "enqueue", **stamp(0)},  # not a turn
                {**human(1), **stamp(2)}, said(2, parent=1, **stamp(3)), result,
                said(4, isSidechain=True, parent=3, **stamp(40)),                # another agent's turn
                {"type": "api-request", **stamp(50)}]                           # not a turn
        self.path.write_text(lines(rows))
        summary = self.reader.turn_ledger("test", self.path)[0].summary()
        first, last = cr.stamp_seconds("2026-10-15T23:02:00Z"), cr.stamp_seconds("2026-10-15T23:05:00Z")
        self.assertEqual((summary["firstAt"], summary["lastAt"]), (first, last))
        # A record still being written: the first stands, the last is not known yet.
        with self.path.open("a") as stream:
            stream.write(json.dumps(said(6, parent=3, **stamp(7)))[:40])
        summary = self.reader.turn_ledger("test", self.path)[0].summary()
        self.assertEqual((summary.get("firstAt"), "lastAt" in summary), (first, False))
        # An unreadable record after the last known turn, or a turn with no zoned stamp: that end is unknown.
        self.path.write_text(lines(rows) + '{"type":"user","broken\n')
        summary = self.reader.turn_ledger("test", self.path)[0].summary()
        self.assertEqual((summary.get("firstAt"), "lastAt" in summary, summary["modelsComplete"]), (first, False, True))
        self.path.write_text(lines([{**human(1), "timestamp": "2026-10-15T23:02:00"}] + rows[2:4]))
        summary = self.reader.turn_ledger("test", self.path)[0].summary()
        self.assertEqual(("firstAt" in summary, summary.get("lastAt")), (False, last))
        # A newer turn moves the last; the route rows carry both.
        self.path.write_text(lines(rows + [said(7, parent=3, **stamp(9))]))
        self.assertEqual(self.reader.turn_ledger("test", self.path)[0].summary()["lastAt"],
                         cr.stamp_seconds("2026-10-15T23:09:00Z"))

    def test_a_native_fork_counts_only_its_own_work_after_its_own_launch_message(self):
        stamp = lambda minute: {"timestamp": f"2026-10-21T20:{minute:02d}:00.000Z"}
        base = "11111111-1111-4111-8111-111111111111"
        launch = f"You are a FORK (session cc-{SESSION}) of cc-{base}. Everything above this message is cc-{base}'s conversation."
        copied = [{**human(1), **stamp(6)}, said(2, "claude-opus-5-5", "low", parent=1, **stamp(7))]
        mine = [{**human(3, 2), **stamp(8), "message": {"role": "user", "content": launch}},
                said(4, "claude-fable-5-1", "xhigh", parent=3, **stamp(20)), {**human(5, 4), **stamp(21)}]
        self.path.write_text(lines(copied + mine))
        summary = self.reader.turn_ledger("test", self.path)[0].summary()
        self.assertEqual((summary["firstAt"], summary["lastAt"]),
                         (cr.stamp_seconds("2026-10-21T20:08:00Z"), cr.stamp_seconds("2026-10-21T20:21:00Z")))
        self.assertEqual((summary["models"], summary["levels"], summary["records"], summary["modelsComplete"]),
                         (["claude-fable-5-1"], ["xhigh"], 1, True))
        # The copied records keep their own per-message facts: a message's label is not a count.
        self.assertEqual(self.reader.turn_ledger("test", self.path)[0].entries[uid(2)], ("msg_2", "claude-opus-5-5", "low"))
        for _ in range(20):
            rows = self.reader.sessions()["sessions"]
            if rows and rows[0]["status"] == "complete":
                break
        self.reader.attach_turn_facts(rows)
        facts = rows[0]["turnFacts"]
        self.assertEqual((facts["firstAt"], facts["models"], facts["levels"]),
                         (cr.stamp_seconds("2026-10-21T20:08:00Z"), ["claude-fable-5-1"], ["xhigh"]))

    def test_the_forks_own_launch_queue_operation_fixes_the_boundary_before_the_copy_is_read(self):
        stamp = lambda minute, second=0: {"timestamp": f"2026-10-21T20:{minute:02d}:{second:02d}.000Z"}
        base = "22222222-2222-4222-8222-222222222222"
        launch = f"You are a FORK (session cc-{SESSION}) of cc-{base}. Everything above this message is cc-{base}'s conversation."
        queued = {"type": "queue-operation", "operation": "enqueue", "content": launch, **stamp(6, 3)}
        # Equal launch stamps cannot separate a copied record; the exact user-message boundary can.
        copied = [{**human(1), **stamp(4)}, said(2, "claude-opus-5-5", "low", parent=1, **stamp(6, 3))]
        mine = [{**human(3, 2), **stamp(6, 4), "message": {"role": "user", "content": launch}},
                said(4, "claude-fable-5-1", "xhigh", parent=3, **stamp(6, 40))]
        self.path.write_text(lines([queued] + copied + mine))
        ledger, used = self.reader.turn_ledger("test", self.path, budget=len(json.dumps(queued).encode()) + 1)
        summary = ledger.summary()
        # Only the launch's queue-operation is read yet: the earlier copy is not mistaken for the fork's own.
        self.assertEqual((summary["status"], summary["records"], summary["models"], "firstAt" in summary),
                         ("scanning", 0, [], False))
        summary = self.reader.turn_ledger("test", self.path)[0].summary()
        self.assertEqual((summary["firstAt"], summary["lastAt"]),
                         (cr.stamp_seconds("2026-10-21T20:06:04Z"), cr.stamp_seconds("2026-10-21T20:06:40Z")))
        self.assertEqual((summary["models"], summary["levels"], summary["records"]),
                         (["claude-fable-5-1"], ["xhigh"], 1))

    def test_a_fork_of_a_fork_keeps_only_its_own_boundary(self):
        stamp = lambda minute: {"timestamp": f"2026-10-21T20:{minute:02d}:00.000Z"}
        ancestor, older = "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444"
        rows = [{**human(1), **stamp(1)},
                {**human(2, 1), **stamp(2), "message": {"role": "user", "content":
                 f"You are a FORK (session cc-{ancestor}) of cc-{older}. Everything above this message is cc-{older}'s conversation."}},
                said(3, "claude-opus-5-5", "low", parent=2, **stamp(3)),
                {**human(4, 3), **stamp(4), "message": {"role": "user", "content":
                 f"You are a FORK (session cc-{SESSION}) of cc-{ancestor}. Everything above this message is cc-{ancestor}'s conversation."}},
                said(5, "claude-fable-5-1", "xhigh", parent=4, **stamp(5))]
        self.path.write_text(lines(rows))
        summary = self.reader.turn_ledger("test", self.path)[0].summary()
        self.assertEqual((summary["firstAt"], summary["models"], summary["levels"], summary["records"]),
                         (cr.stamp_seconds("2026-10-21T20:04:00Z"), ["claude-fable-5-1"], ["xhigh"], 1))

    def test_transcript_created_takes_the_forks_own_launch_message_over_the_copied_records(self):
        base = "55555555-5555-4555-8555-555555555555"
        copied = {**human(1), "timestamp": "2026-10-21T17:51:49.255Z"}
        launch = {**human(2, 1), "timestamp": "2026-10-21T20:06:03.462Z", "message": {"role": "user", "content":
                  f"You are a FORK (session cc-{SESSION}) of cc-{base}. Everything above this message is cc-{base}'s conversation."}}
        self.path.write_text(lines([copied, launch]))
        self.assertEqual(self.reader.transcript_created("test", self.path), cr.stamp_seconds("2026-10-21T20:06:03.462Z"))
        # A transcript without its own FORK prompt keeps today's answer: its first timestamped record.
        plain = self.root / "project" / "plain-history.jsonl"
        plain.write_text(lines([{**copied, "timestamp": "2026-10-21T17:51:49.255Z"},
                                {**launch, "sessionId": SESSION, "timestamp": "2026-10-21T20:06:03.462Z"}]))
        self.assertEqual(self.reader.transcript_created("test", plain), cr.stamp_seconds("2026-10-21T17:51:49.255Z"))

    def test_a_subagent_transcript_counts_its_own_records_and_rows_carry_turn_facts(self):
        self.path.write_text(lines([human(1), said(2, parent=1)]))
        child = self.path.with_suffix("") / "subagents" / "agent-a1.jsonl"
        child.parent.mkdir(parents=True)
        child.write_text(lines([said(10, "claude-opus-5-5", "xhigh", isSidechain=True, agentId="a1")]))
        rows = []
        for _ in range(20):
            rows = self.reader.sessions()["sessions"]
            if len(rows) == 2 and all(r["status"] == "complete" for r in rows):
                break
        self.reader.attach_turn_facts(rows)
        by_agent = {r.get("agentId"): r["turnFacts"] for r in rows}
        self.assertEqual((by_agent[None]["models"], by_agent[None]["levels"]), (["claude-opus-5-5"], ["high"]))
        self.assertEqual((by_agent["a1"]["models"], by_agent["a1"]["levels"], by_agent["a1"]["records"]),
                         (["claude-opus-5-5"], ["xhigh"], 1))
        # The shared byte budget is honest: an unread transcript says so instead of guessing.
        fresh = cr.ClaudeReader([{"id": "test", "root": str(self.root), "native": False}])
        for _ in range(20):
            rows = fresh.sessions()["sessions"]
            if len(rows) == 2:
                break
        fresh.attach_turn_facts(rows, budget=0)
        self.assertTrue(all(r["turnFacts"]["status"] == "scanning" and not r["turnFacts"]["modelsComplete"] for r in rows))


class HostFactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = InertHost()
        self.facade = ClaudeFacade(cr.ClaudeReader([{"id": "fixture", "root": self.temp.name}]), self.host)
        (Path(self.temp.name) / "-fixture").mkdir()
        self.transcript = Path(self.temp.name) / "-fixture" / (UUID + ".jsonl")
        self.transcript.write_text("")

    def tearDown(self):
        self.temp.cleanup()

    def assistant(self, n, model, message_id=None):
        """The host feed's final assistant frame for record uid(n): the stream names the model only."""
        self.host.add({"type": "assistant", "uuid": uid(n), "session_id": UUID, "parent_tool_use_id": None,
                       "message": {"role": "assistant", "id": message_id or "msg_" + str(n), "model": model,
                                   "content": [{"type": "text", "text": "answer " + str(n)}]}})

    def record(self, n, model, effort):
        value = said(n, model, effort, sessionId=UUID)
        value["sessionId"] = UUID
        with self.transcript.open("a") as stream:
            stream.write(json.dumps(value) + "\n")

    def first(self):
        row = self.facade.sessions()["sessions"][0]
        return self.facade.events(sessionRef=row["sessionRef"], scopeRef=row["scopes"][0]["scopeRef"])

    def test_host_blocks_take_the_stream_model_and_the_transcript_level_of_the_same_record(self):
        self.host.user("hello")
        self.assistant(2, "claude-opus-5-5"); self.record(2, "claude-opus-5-5", "high")
        self.assistant(3, "claude-fable-5-1"); self.record(3, "claude-fable-5-1", "medium")   # mid-conversation switch
        self.assistant(4, "claude-opus-5-5", message_id="msg_other"); self.record(4, "claude-opus-5-5", "xhigh")
        self.assistant(5, "claude-opus-5-5"); self.record(5, "claude-fable-5-1", "low")       # the record disagrees
        page = self.first()
        facts = {e["uuid"]: (e["model"], e["effort"]) for e in page["events"] if e["kind"] == "assistant"}
        self.assertEqual(facts, {uid(2): ("claude-opus-5-5", "high"), uid(3): ("claude-fable-5-1", "medium"),
                                 uid(4): ("claude-opus-5-5", None), uid(5): ("claude-opus-5-5", None)})
        self.assertTrue(all("model" not in e for e in page["events"] if e["kind"] == "user"))

    def test_a_level_whose_record_lands_after_the_frame_follows_as_a_display_update(self):
        self.host.user("hello")
        self.assistant(2, "claude-opus-5-5")
        page = self.first()
        event = next(e for e in page["events"] if e["kind"] == "assistant")
        self.assertEqual((event["model"], event["effort"]), ("claude-opus-5-5", None))  # model now, level not yet
        self.record(2, "claude-opus-5-5", "xhigh")
        delta = self.facade.events(sessionRef=page["sessionRef"], scopeRef=page["scopeRef"], cursor=page["deltaCursor"])
        update = next(e for e in delta["eventUpdates"] if e["uuid"] == uid(2))
        self.assertEqual((update["model"], update["effort"], update["recordRef"]), ("claude-opus-5-5", "xhigh", event["recordRef"]))
        again = self.facade.events(sessionRef=page["sessionRef"], scopeRef=page["scopeRef"], cursor=delta["deltaCursor"])
        self.assertFalse(any(e["uuid"] == uid(2) for e in again["eventUpdates"]))  # sent once

    def test_the_inventory_route_attaches_each_sessions_recorded_models_and_levels(self):
        self.record(2, "claude-opus-5-5", "high"); self.record(3, "claude-fable-5-1", "medium")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.claude_reader = self.facade
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server, "_tools", server.ToolMailbox()):
                connection = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=15)
                connection.request("GET", "/api/claude/sessions")
                body = json.loads(connection.getresponse().read())
                connection.close()
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()
        row = next(r for r in body["sessions"] if r["source"] == "host")
        self.assertEqual(row["turnFacts"], {"status": "complete", "records": 2,
                                            "models": ["claude-opus-5-5", "claude-fable-5-1"],
                                            "levels": ["high", "medium"], "modelsComplete": True, "levelsComplete": True})

    def test_a_host_fork_row_counts_only_the_forks_own_work(self):
        base = "99999999-9999-4999-8999-999999999999"
        launch = f"You are a FORK (session cc-{UUID}) of cc-{base}. Everything above this message is cc-{base}'s conversation."
        rows = [{"type": "queue-operation", "operation": "enqueue", "content": launch, "timestamp": "2026-10-21T20:06:03.442Z"},
                {**said(1, "claude-opus-5-5", "low"), "sessionId": UUID, "timestamp": "2026-10-21T17:51:51.627Z"},
                {**human(2, 1), "sessionId": UUID, "timestamp": "2026-10-21T20:06:03.462Z",
                 "message": {"role": "user", "content": launch}},
                {**said(3, "claude-fable-5-1", "xhigh"), "sessionId": UUID, "timestamp": "2026-10-21T20:06:04.000Z"}]
        with self.transcript.open("a") as stream:
            for record in rows:
                stream.write(json.dumps(record) + "\n")
        row = self.facade.sessions()["sessions"][0]
        self.facade.history.attach_turn_facts([row])
        self.assertEqual((row["turnFacts"]["models"], row["turnFacts"]["levels"], row["turnFacts"]["records"]),
                         (["claude-fable-5-1"], ["xhigh"], 1))                 # the copied base call is not the fork's
        self.assertEqual(row["turnFacts"]["firstAt"], cr.stamp_seconds("2026-10-21T20:06:03.462Z"))
        # The creation order stamp is the fork's own launch, never the base record above it.
        self.assertEqual(row["createdAt"], cr.stamp_seconds("2026-10-21T20:06:03.442Z"))


if __name__ == "__main__":
    unittest.main()
