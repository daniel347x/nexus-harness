"""Pinned-native-layout policy fixtures; no native process, private history or inference."""
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from test_claude_reader import SESSION, uid, record
import test_claude_live as live_fixtures
from claude_reader import ClaudeReader, ReaderError
UUID = live_fixtures.UUID


def child_record(n, agent="a123456789abcdef0", **extra):
    row = record(n, n - 1 if n > 1 else None, version="2.1.299", isSidechain=True,
                 agentId=agent, **extra)
    row.pop("origin", None)
    return row


class SubagentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.main = self.root / "project" / (SESSION + ".jsonl")
        self.main.parent.mkdir()
        self.main.write_text(json.dumps(record(1, content="parent only")) + "\n")
        self.reader = ClaudeReader([{"id": "native", "root": str(self.root), "native": True}])
        self.parent = self.reader.sessions()["sessions"][0]

    def tearDown(self):
        for iterator, _ in self.reader.subagent_scans.values():
            iterator.close()
        self.temp.cleanup()

    def write_child(self, agent="a123456789abcdef0", rows=None):
        path = self.main.with_suffix("") / "subagents" / ("agent-" + agent + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in (rows or [child_record(1, agent)])))
        path.with_suffix(".meta.json").write_text(json.dumps({"agentType": "manager-quick", "description": "fixture job"}))
        return path

    def test_round5_subagent_context_is_its_own_last_call_with_the_learned_window(self):
        def call(n, agent, usage, at):
            return child_record(n, agent, kind="assistant", content=[{"type": "text", "text": "work"}], timestamp=at,
                                message={"role": "assistant", "model": "claude-opus-5-5", "stop_reason": "tool_use",
                                         "usage": usage, "content": [{"type": "text", "text": "work"}]})
        mine = {"input_tokens": 2, "cache_read_input_tokens": 300000, "cache_creation_input_tokens": 1000, "output_tokens": 9}
        older = {"input_tokens": 5, "cache_read_input_tokens": 900000, "cache_creation_input_tokens": 0, "output_tokens": 9}
        self.write_child(rows=[child_record(1, content="task"), call(2, "a123456789abcdef0", older, "2026-10-15T07:43:00.000Z"),
                               call(3, "a123456789abcdef0", mine, "2026-10-15T07:44:23.880Z")])
        self.reader.known_windows["claude-opus-5-5"] = 1000000
        child = self.reader.subagents(self.parent["sessionRef"])["sessions"][0]
        page = self.reader.events(sessionRef=child["sessionRef"], scopeRef=child["scopes"][0]["scopeRef"])
        readout = page["contextReadout"]
        # Its own newest sidechain call (never the parent's figure), with the learned window.
        self.assertEqual((readout["state"], readout["occupancy_tokens"], readout["window_tokens"], readout["source"]),
                         ("measured", 301002, 1000000, "subagent-transcript"))

    def test_discovery_after_initial_inventory_live_append_thinking_and_exact_isolated_tool(self):
        self.assertEqual(self.reader.subagents(self.parent["sessionRef"])["sessions"], [])
        path = self.write_child(rows=[child_record(1, content="Manager delegated task"),
            child_record(2, kind="assistant", content=[{"type": "thinking", "thinking": "Private thinking", "signature": "opaque"},
                {"type": "tool_use", "id": "same", "name": "Read", "input": {"path": "child"}}])])
        child = self.reader.subagents(self.parent["sessionRef"], parent_title="Manager-Session-181")["sessions"][0]
        self.assertEqual(child["title"], "Manager-Session-181 ▸ manager-quick · fixture job")
        self.assertFalse(child["capabilities"]["nativeInput"])
        address = {"sessionRef": child["sessionRef"], "scopeRef": child["scopes"][0]["scopeRef"]}
        page = self.reader.events(**address)
        self.assertIsNone(page["targetRef"])
        self.assertEqual(page["events"][0]["text"], "Manager delegated task")
        self.assertEqual(page["events"][1]["thinking"][0]["text"], "Private thinking")
        self.assertEqual(page["contextReadout"]["state"], "unavailable")  # no completed call of its own yet
        call = page["events"][1]["calls"][0]
        self.assertEqual(self.reader.snapshot(call["callRef"], page["fileEpoch"])["status"], "pending-or-missing")
        with path.open("a") as stream:
            stream.write(json.dumps(child_record(3, content=[{"type": "tool_result", "tool_use_id": "same", "content": "child result"}])) + "\n")
            stream.write(json.dumps(child_record(4, kind="assistant", content=[{"type": "text", "text": "child final"}])) + "\n")
        delta = self.reader.events(**address, cursor=page["deltaCursor"], fileEpoch=page["fileEpoch"])
        self.assertEqual(delta["events"][-1]["text"], "child final")
        self.assertEqual(self.reader.snapshot(call["callRef"], page["fileEpoch"])["output"], "child result")
        other = self.write_child("aOTHER", [child_record(1, "aOTHER", kind="assistant", content=[
            {"type": "tool_use", "id": "same", "name": "Read", "input": {"path": "other"}}])])
        other_ref = self.reader._ref("session", ("native", other))
        self.reader.inventory[("native", other)] = {"session": SESSION, "sequence": 0}
        other_index = self.reader._session(other_ref)
        with self.assertRaises(ReaderError):
            self.reader.events(sessionRef=other_ref, scopeRef=address["scopeRef"])
        forged = self.reader._ref("target", ("native", path, "main", uid(2), other_index.epoch))
        with self.assertRaises(ReaderError):
            self.reader.context(forged, 0)
        main = self.reader.events(sessionRef=self.parent["sessionRef"], scopeRef=self.parent["scopes"][0]["scopeRef"])
        self.assertEqual(self.reader.context(main["targetRef"], 0)["text"], "[USER]\nparent only")

    def test_every_child_pages_and_native_progress_is_not_a_second_conversation_leaf(self):
        for n in range(12):
            self.write_child("a" + str(n), [child_record(1, "a" + str(n))])
        first = self.reader.subagents(self.parent["sessionRef"])
        self.assertEqual(len(first["sessions"]), 8)
        last = self.reader.subagents(self.parent["sessionRef"], first["nextCursor"])
        self.assertEqual(len(last["sessions"]), 4)
        self.assertFalse(last["hasMore"])
        self.assertEqual(len({r["sessionRef"] for r in first["sessions"] + last["sessions"]}), 12)
        with self.assertRaises(ReaderError):
            self.reader.subagents(self.parent["sessionRef"], first["nextCursor"])
        path = self.write_child("a0", [child_record(1, "a0"),
            {**child_record(2, "a0"), "type": "progress", "data": {"type": "tool_heartbeat"}},
            child_record(3, "a0", kind="assistant", content=[{"type": "text", "text": "done"}], parentUuid=uid(1))])
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[1].pop("message")
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        found = self.reader.subagents(self.parent["sessionRef"])
        rows = list(found["sessions"])
        while found["hasMore"]:
            found = self.reader.subagents(self.parent["sessionRef"], found["nextCursor"])
            rows.extend(found["sessions"])
        child = next(r for r in rows if r["agentId"] == "a0")
        page = self.reader.events(sessionRef=child["sessionRef"], scopeRef=child["scopes"][0]["scopeRef"])
        self.assertEqual(page["status"], "complete")
        self.assertEqual([r["text"] for r in page["events"]], ["hello", "done"])

    def test_task_prompt_and_progress_rules_hold_on_any_claude_code_version(self):
        # 2.1.300 (installed) and future releases: the child's task prompt is its first block and
        # progress telemetry is never a second leaf, exactly as on 2.1.299. Shape, not release.
        for version in ("2.1.299", "2.1.300", "2.2.0", "3.0.0-beta.1"):
            with self.subTest(version=version):
                agent = "a" + version.replace(".", "").replace("-", "")
                rows = [child_record(1, agent, content="Manager delegated task"),
                        {**child_record(2, agent), "type": "progress", "data": {"type": "tool_heartbeat"}},
                        child_record(3, agent, kind="assistant", content=[{"type": "text", "text": "done"}], parentUuid=uid(1))]
                rows[1].pop("message")
                self.write_child(agent, [{**row, "version": version} for row in rows])
                found, kids = self.reader.subagents(self.parent["sessionRef"]), []
                kids += found["sessions"]
                while found["hasMore"]:
                    found = self.reader.subagents(self.parent["sessionRef"], found["nextCursor"])
                    kids += found["sessions"]
                child = next(r for r in kids if r["agentId"] == agent)
                page = self.reader.events(sessionRef=child["sessionRef"], scopeRef=child["scopes"][0]["scopeRef"])
                self.assertEqual(page["status"], "complete")
                self.assertEqual(page["diagnostics"], [])
                self.assertEqual([r["text"] for r in page["events"]], ["Manager delegated task", "done"])
                self.assertEqual([r["status"] for r in page["events"]], ["stored", "stored"])

    def test_contained_sidecar_is_optional_and_symlink_children_never_open(self):
        child = self.write_child()
        child.with_suffix(".meta.json").unlink()
        child.with_suffix(".meta.json").symlink_to(self.main)
        (child.parent / "agent-symlink.jsonl").symlink_to(self.main)
        rows = self.reader.subagents(self.parent["sessionRef"])["sessions"]
        self.assertEqual(len(rows), 1)
        self.assertIn("agent-a123456789abcdef0", rows[0]["title"])

    def test_actual_http_parent_label_no_child_send_or_mirror_and_main_progress_context_isolation(self):
        fixture = live_fixtures.LiveTests("runTest")
        fixture.setUp()
        try:
            fixture.host.record["title"] = "Manager-Session-181"
            main = Path(fixture.temp.name) / "project" / (UUID + ".jsonl")
            main.parent.mkdir()
            main.write_text(json.dumps({**record(1), "sessionId": UUID}) + "\n")
            child = main.with_suffix("") / "subagents" / "agent-a123456789abcdef0.jsonl"
            child.parent.mkdir(parents=True)
            child.write_text(json.dumps({**child_record(1, kind="assistant", content=[{"type": "text", "text": "child body"}]), "sessionId": UUID}) + "\n")
            child.with_suffix(".meta.json").write_text('{"agentType":"manager-quick","description":"fixture job"}')
            address = fixture.address()
            code, _, data = fixture.request('/api/claude/subagents?' + urlencode({"sessionRef": address["sessionRef"]}))
            self.assertEqual(code, 200, data)
            row = data["sessions"][0]
            self.assertEqual(row["title"], "Manager-Session-181 ▸ manager-quick · fixture job")
            page = fixture.page({"sessionRef": row["sessionRef"], "scopeRef": row["scopes"][0]["scopeRef"]})
            self.assertIsNone(page["targetRef"])
            code, _, refusal = fixture.request('/api/claude/send', 'POST', {
                "schemaVersion": 1, "backend": "claude", "targetRef": row["sessionRef"],
                "executionId": "cc-" + UUID, "generation": 1, "messageUuid": uid(98),
                "original": "never submit child", "submitted": "never submit child"})
            self.assertIn(code, (400, 409), refusal)
            fixture.host.user("parent prose")
            fixture.host.frame('assistant', [{"type": "tool_use", "id": "agent-call", "name": "Agent", "input": {"subagent_type": "manager-quick"}}], 'assistant-1')
            for subtype in ("task_started", "task_progress", "task_notification"):
                fixture.host.add({"type": "system", "subtype": subtype, "session_id": UUID,
                    "task_id": "a123456789abcdef0", "tool_use_id": "agent-call", "description": "fixture job",
                    "status": "completed" if subtype == "task_notification" else "running", "summary": "child progress"})
            main_page = fixture.page(address)
            tasks = [r for r in main_page["events"] if r["kind"] == "task"]
            self.assertEqual(len(tasks), 1)
            self.assertIn("completed", tasks[0]["text"])
            context = fixture.reader.context(main_page["targetRef"], 0)["text"]
            self.assertIn("parent prose", context)
            self.assertNotIn("child", context)
            for method in ("POST", "GET"):
                code, _, _ = fixture.request('/api/claude/subagents?sessionRef=' + row["sessionRef"] + '&path=/etc/passwd', method)
                self.assertIn(code, (400, 405))
            inventory = fixture.reader.sessions(cursor=fixture.reader.sessions()["nextCursor"])
            child_row = next(r for r in inventory["sessions"] if r.get("parentUuid"))
            self.assertEqual(child_row["role"], "subagent")
            self.assertNotIn("Native history boundary", child_row["title"])
            self.assertFalse(child_row["capabilities"]["nativeInput"])
        finally:
            fixture.tearDown()
