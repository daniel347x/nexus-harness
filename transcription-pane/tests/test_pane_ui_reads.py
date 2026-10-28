"""Display-only reads behind the Claude-mode pane UI: sidebar session names and record facts."""
import json
import tempfile
import unittest
from pathlib import Path

from test_claude_reader import SESSION, record
from claude_reader import ClaudeReader

HOST_UUID = "00000000-0000-4000-8000-0000000000aa"


class PaneUiReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "-fixture"
        self.project.mkdir()
        self.reader = ClaudeReader([{"id": "native", "root": str(self.root), "native": True}])

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, rows):
        path = self.project / (name + ".jsonl")
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def test_manager_session_label_from_first_human_message_with_suffix(self):
        self.write(SESSION, [
            record(1, content=[{"type": "text", "text": "<system-reminder>Manager-Session-1</system-reminder>"}]),
            record(2, 1, content="You are Manager-Session-212-a. Read your socket."),
            record(3, 2, content="later Manager-Session-999"),
        ])
        rows = self.reader.sessions()["sessions"]
        self.reader.label_sessions(rows)
        self.assertEqual(rows[0]["sessionLabel"], "Manager-Session-212-a")
        self.assertEqual(rows[0]["uuid"], SESSION)  # the UUID stays available

    def test_word_suffix_is_not_a_session_letter_and_no_label_invents_nothing(self):
        self.write(SESSION, [record(1, content="Manager-Session-41-and more"), record(2, 1, content="plain")])
        self.write("00000000-0000-4000-8000-000000000002",
                   [{**record(1, content="no number here"), "sessionId": "00000000-0000-4000-8000-000000000002"}])
        rows = self.reader.sessions()["sessions"]
        self.reader.label_sessions(rows)
        labels = {row["uuid"]: row.get("sessionLabel") for row in rows}
        self.assertEqual(labels[SESSION], "Manager-Session-41")
        self.assertIsNone(labels["00000000-0000-4000-8000-000000000002"])

    def test_tool_results_and_meta_are_not_the_launch_message(self):
        self.write(SESSION, [
            record(1, content=[{"type": "tool_result", "tool_use_id": "x", "content": "Manager-Session-5"}]),
            record(2, 1, content="Manager-Session-6 meta", isMeta=True),
            record(3, 2, content="Manager-Session-7"),
        ])
        rows = self.reader.sessions()["sessions"]
        self.reader.label_sessions(rows)
        self.assertEqual(rows[0]["sessionLabel"], "Manager-Session-7")

    def test_host_rows_resolve_their_native_transcript_and_subagents_are_skipped(self):
        self.write(HOST_UUID, [{**record(1, content="Manager-Session-300"), "sessionId": HOST_UUID}])
        host = {"source": "host", "uuid": HOST_UUID, "cwd": "/fixture", "sessionRef": "x" * 32}
        child = {"source": "native", "role": "subagent", "parentUuid": HOST_UUID, "uuid": HOST_UUID}
        missing = {"source": "host", "uuid": "00000000-0000-4000-8000-0000000000bb", "cwd": "/fixture"}
        self.reader.label_sessions([host, child, missing])
        self.assertEqual(host["sessionLabel"], "Manager-Session-300")
        self.assertNotIn("sessionLabel", child)
        self.assertNotIn("sessionLabel", missing)

    def test_events_carry_display_facts_for_tool_result_carriers_and_meta_records(self):
        self.write(SESSION, [
            record(1, content="question"),
            record(2, 1, "assistant", [{"type": "tool_use", "id": "c1", "name": "Read", "input": {"p": 1}}]),
            {k: v for k, v in record(3, 2, content=[{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}]).items()
             if k != "origin"},
            record(4, 3, content="injected", isMeta=True),
        ])
        session = self.reader.sessions()["sessions"][0]
        page = self.reader.events(sessionRef=session["sessionRef"], scopeRef=session["scopes"][0]["scopeRef"])
        events = {e["uuid"][-1]: e for e in page["events"]}
        self.assertEqual(events["3"]["toolResults"], 1)
        self.assertEqual(events["3"]["text"], "")
        self.assertEqual(events["4"]["isMeta"], True)
        self.assertEqual(events["1"]["toolResults"], 0)
        self.assertEqual(events["1"]["originKind"], "human")


if __name__ == "__main__":
    unittest.main()
